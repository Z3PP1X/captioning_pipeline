#!/usr/bin/env bash
# Sourced or executed: Python wrapper for ast_vector_lint
""":"
if command -v python3 >/dev/null 2>&1; then
    exec python3 "$0" "$@"
elif [ -n "$UV_PROJECT_ENVIRONMENT" ] && [ -x "$UV_PROJECT_ENVIRONMENT/bin/python" ]; then
    exec "$UV_PROJECT_ENVIRONMENT/bin/python" "$0" "$@"
else
    exec python "$0" "$@"
fi
"""
from __future__ import annotations

import ast
import os
import sys
from collections.abc import Sequence
from pathlib import Path

# Parameter names or type names that indicate image / frame / pixel array processing
VISION_PARAM_NAMES = {
    "img",
    "image",
    "frame",
    "gray",
    "pixels",
    "pixel_array",
    "bgr",
    "rgb",
    "arr",
    "ndarray",
    "hashes",
}

VISION_TYPE_SUBSTRINGS = (
    "ndarray",
    "ArrayLike",
    "Mat",
)


def is_vision_param(arg: ast.arg) -> bool:
    """Check if function parameter name or type annotation indicates an array/image."""
    name_lower = arg.arg.lower()
    if name_lower in VISION_PARAM_NAMES or any(name_lower.startswith(p) for p in ("img_", "frame_", "image_")):
        return True

    if arg.annotation:
        ann_str = ast.unparse(arg.annotation)
        if any(sub in ann_str for sub in VISION_TYPE_SUBSTRINGS):
            return True

    return False


def is_vision_function(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Determine whether a function accepts ndarray/image/frame parameters."""
    all_args = list(func_node.args.args) + list(func_node.args.posonlyargs) + list(func_node.args.kwonlyargs)
    return any(is_vision_param(arg) for arg in all_args)


class VisionLoopVisitor(ast.NodeVisitor):
    def __init__(self, source_lines: list[str], filename: str) -> None:
        self.source_lines = source_lines
        self.filename = filename
        self.violations: list[str] = []
        self._current_func: ast.FunctionDef | ast.AsyncFunctionDef | None = None
        self._loop_depth = 0

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        prev_func = self._current_func
        prev_depth = self._loop_depth
        self._current_func = node
        self._loop_depth = 0
        self.generic_visit(node)
        self._current_func = prev_func
        self._loop_depth = prev_depth

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        prev_func = self._current_func
        prev_depth = self._loop_depth
        self._current_func = node
        self._loop_depth = 0
        self.generic_visit(node)
        self._current_func = prev_func
        self._loop_depth = prev_depth

    def visit_For(self, node: ast.For) -> None:
        self._check_loop(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._check_loop(node)

    def _check_loop(self, node: ast.For | ast.AsyncFor) -> None:
        if self._current_func and is_vision_function(self._current_func):
            self._loop_depth += 1
            if self._loop_depth >= 2:
                # Check for noqa suppression on the loop line
                line_idx = node.lineno - 1
                if 0 <= line_idx < len(self.source_lines):
                    line_text = self.source_lines[line_idx]
                    if "# noqa: VEC001" not in line_text and "# noqa" not in line_text:
                        col = node.col_offset + 1
                        msg = (
                            f"{self.filename}:{node.lineno}:{col}: VEC001 "
                            "Nested Python loop detected in vision module. "
                            "Use vectorized NumPy/OpenCV operations instead."
                        )
                        self.violations.append(msg)
            self.generic_visit(node)
            self._loop_depth -= 1
        else:
            self.generic_visit(node)


def lint_source(source: str, filename: str) -> list[str]:
    """Lint python source code string for nested vision loops."""
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as e:
        return [f"{filename}:{e.lineno}:{e.offset}: VEC999 Syntax error parsing file: {e}"]

    lines = source.splitlines()
    visitor = VisionLoopVisitor(lines, filename)
    visitor.visit(tree)
    return visitor.violations


def lint_file(file_path: Path) -> list[str]:
    """Lint a single python file."""
    try:
        source = file_path.read_text(encoding="utf-8")
    except Exception as e:
        return [f"{file_path}:0:0: VEC998 Failed to read file: {e}"]
    return lint_source(source, str(file_path))


def run_self_tests() -> int:
    """Verify linter logic against positive, negative, and suppression test cases."""
    print("Running ast_vector_lint self-tests...")

    # Case 1: Nested loop on ndarray parameter without noqa -> VIOLATION
    bad_code = """
import numpy as np

def bad_pixel_loop(img: np.ndarray) -> float:
    h, w = img.shape[:2]
    total = 0.0
    for y in range(h):
        for x in range(w):
            total += float(img[y, x])
    return total
"""
    violations = lint_source(bad_code, "test_bad.py")
    assert len(violations) == 1, f"Expected 1 violation, got {len(violations)}: {violations}"
    assert "VEC001" in violations[0]
    print("  [PASS] Detection of unvectorized nested loop on ndarray")

    # Case 2: Nested loop with # noqa: VEC001 -> SUPPRESSED
    suppressed_code = """
import numpy as np

def suppressed_loop(frame: np.ndarray) -> None:
    for y in range(4):
        for x in range(4):  # noqa: VEC001 tile coordinates justified
            pass
"""
    violations = lint_source(suppressed_code, "test_suppressed.py")
    assert len(violations) == 0, f"Expected 0 violations with noqa, got: {violations}"
    print("  [PASS] noqa: VEC001 suppression works")

    # Case 3: Fully vectorized code -> CLEAN
    good_code = """
import numpy as np
import cv2

def vectorized_metric(gray: np.ndarray) -> float:
    lap = cv2.Laplacian(gray, cv2.CV_32F)
    return float(lap.var())
"""
    violations = lint_source(good_code, "test_good.py")
    assert len(violations) == 0, f"Expected 0 violations for vectorized code, got: {violations}"
    print("  [PASS] Clean pass on vectorized NumPy/OpenCV operations")

    # Case 4: Nested loop on non-vision function -> IGNORED
    non_vision_code = """
def process_nested_data(items: list[list[int]]) -> list[int]:
    out = []
    for sub in items:
        for x in sub:
            out.append(x * 2)
    return out
"""
    violations = lint_source(non_vision_code, "test_non_vision.py")
    assert len(violations) == 0, f"Expected 0 violations for non-vision function, got: {violations}"
    print("  [PASS] Non-vision functions safely ignored")

    print("All ast_vector_lint self-tests PASSED successfully.")
    return 0


def _show_help() -> int:
    print("Usage: ast_vector_lint.py [OPTIONS] [PATHS...]")
    print("")
    print("Scans Python files for nested loops (depth >= 2) within vision/image functions.")
    print("")
    print("Options:")
    print("  --test         Run internal self-tests and exit")
    print("  -h, --help     Show this help message and exit")
    return 0


def _resolve_target_paths(args: Sequence[str]) -> list[Path]:
    target_paths = [Path(arg) for arg in args if not arg.startswith("-")]
    if target_paths:
        return target_paths

    repo_root = Path(__file__).resolve().parent.parent
    defaults = [
        repo_root / "src" / "pipeline",
        repo_root / "backend" / "app" / "vision",
    ]
    return [p for p in defaults if p.exists()]


def _collect_dir_files(directory: Path) -> list[Path]:
    matched: list[Path] = []
    ignored = (".venv", "node_modules", ".git", "__pycache__")
    for root, _, files in os.walk(directory):
        if any(ign in root for ign in ignored):
            continue
        matched.extend(Path(root) / f for f in files if f.endswith(".py"))
    return matched


def _collect_py_files(paths: Sequence[Path]) -> list[Path]:
    all_files: list[Path] = []
    for path in paths:
        if path.is_file() and path.suffix == ".py":
            all_files.append(path)
        elif path.is_dir():
            all_files.extend(_collect_dir_files(path))
    return all_files


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])

    if "--test" in args:
        return run_self_tests()

    if "--help" in args or "-h" in args:
        return _show_help()

    target_paths = _resolve_target_paths(args)
    if not target_paths:
        print("No target paths found to lint.")
        return 0

    files_to_check = _collect_py_files(target_paths)
    all_violations: list[str] = []
    for file_path in files_to_check:
        all_violations.extend(lint_file(file_path))

    if all_violations:
        count = len(all_violations)
        total_files = len(files_to_check)
        print(f"ast_vector_lint: Found {count} violation(s) across {total_files} file(s):", file=sys.stderr)
        for v in all_violations:
            print(f"  {v}", file=sys.stderr)
        return 1

    print(f"ast_vector_lint: {len(files_to_check)} file(s) checked. No unvectorized vision loops found. [PASS]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
