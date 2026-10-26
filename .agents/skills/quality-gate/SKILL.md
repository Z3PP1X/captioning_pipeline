---
name: quality-gate
description: Automated Complexity, Big-O & Code Quality Gatekeeper for Python and Angular/TypeScript. Enforces McCabe CC <= 10, Cognitive Complexity <= 15, Radon MI Rank A, strict static typing (mypy and TypeScript), empirical Big-O regression (<= O(N log N) deduplication, O(P) image metrics, < 300MB peak RAM), AST-based vision loop linting (VEC001), Angular OnPush change detection, and Knip dead-code elimination. Run with `make quality-gate` or `scripts/verify_quality.sh`.
---

# Automated Complexity, Big-O & Code Quality Gatekeeper

This skill acts as the **Principal Quality, Performance & Static Analysis Architect** for the repository. Every piece of code written or refactored by any agent or engineer MUST pass these quality gates before a task or refactoring is considered complete.

## 1. Hard Quality & Complexity Thresholds

### A. Static Code Complexity (Backend & Frontend)
1. **Cyclomatic Complexity (McCabe):** Maximum $CC \le 10$ per function or method (`ruff C901`, `radon cc`, `eslint complexity`).
2. **Cognitive Complexity:** Maximum $C_{\text{cog}} \le 15$ per function or method (`complexipy --max-complexity-allowed 15`, `eslint-plugin-sonarjs/cognitive-complexity`).
3. **Maintainability Index:** Every Python module must achieve Radon Rank `A` ($MI > 19$, target $> 65$). Overall average complexity ranked `A` / `B` (`xenon --max-absolute B --max-modules A --max-average A`).
4. **Function Length & Signatures:** Maximum 60-75 lines per function, maximum 6 positional arguments (use dataclasses or Pydantic models for larger parameter sets).
5. **Type Safety:** 100% strict type annotations in Python (`mypy --strict`) and TypeScript (`"strict": true`, no implicit `any`).
6. **Angular Change Detection:** All Angular components must declare `ChangeDetectionStrategy.OnPush` with Signals (`@angular-eslint/prefer-on-push-component-change-detection`).
7. **Dead Code Elimination:** No orphaned components, unused exports, or dead dependencies (`knip`).

### B. Algorithmic & Asymptotical Complexity (Vision & Backend)
1. **Asymptotic Time Complexity ($\mathcal{O}$-Notation):**
   - **Image Quality Metrics:** Laplace variance, Sobel/Tenengrad gradient, luminance histogram, and Shannon entropy over pixel count $P$: Strictly $\mathcal{O}(P)$ with native NumPy/OpenCV vectorization. **Zero** nested Python loops over pixel coordinates (`ast_vector_lint.py: VEC001`).
   - **Frame Deduplication & Clustering:** Over $N$ frames: Strictly $\le \mathcal{O}(N \log N)$ (e.g. Multi-Index Hashing + Disjoint Set Union). 5,000 frames must cluster in $< 150\,\text{ms}$ (benchmarked with `pytest-benchmark`).
2. **Memory Complexity:**
   - Video frame extraction and scoring must stream in batches ($\mathcal{O}(B)$ memory for batch size $B$, never accumulating raw uncompressed frames in RAM). Hard memory ceiling of $< 300\,\text{MB}$ peak RAM (`@pytest.mark.limit_memory("300 MB")`).

---

## 2. Command Reference

All checks are centrally orchestrable via `make` or `scripts/verify_quality.sh`:

```bash
# Run full combined Quality Gate (Linters + Big-O + Unit Tests)
make quality-gate
# or: bash scripts/verify_quality.sh --gate

# Run static complexity and dead-code linters only
make lint-complexity
# or: bash scripts/verify_quality.sh --lint

# Run empirical Big-O regression, memory bounds and benchmark suite
make test-bigo
# or: bash scripts/verify_quality.sh --bigo

# Run Python and Angular unit tests
make test
# or: bash scripts/verify_quality.sh --unit
```

Individual tools:
```bash
# AST vision loop linter
python3 scripts/ast_vector_lint.py src/pipeline

# Python static complexity & style
ruff check src/pipeline
complexipy --max-complexity-allowed 15 src/pipeline
xenon --max-absolute B --max-modules A --max-average A src/pipeline
mypy --strict src/pipeline

# Frontend ESLint & Knip
npm run lint
npx knip
```

---

## 3. Self-Healing & Refactoring Playbook

When any quality gate fails, apply the following deterministic remediation:

1. **Cyclomatic / Cognitive Complexity Failure (`C901`, `complexipy`, `xenon`, `sonarjs`):**
   - Split large functions into smaller, single-purpose pure functions.
   - Replace complex nested `if / elif / else` chains with lookup dictionaries, strategy patterns, or match expressions.
   - Extract state mutations into distinct subroutines.

2. **Performance & Loop Violations (`PERF`, `VEC001`, `test-bigo`):**
   - Eliminate Python nested loops inside image/video processing routines.
   - Use OpenCV vectorized kernels (`cv2.Laplacian`, `cv2.Sobel`, `cv2.resize`) or NumPy broadcasting/reduction (`np.bincount`, `np.packbits`, `np.dot`).
   - For all-pairs comparisons, use Multi-Index Hashing (MIH) or spatial index trees rather than $\mathcal{O}(N^2)$ brute-force comparisons.
   - For legitimate macro-tile slicing that cannot be avoided, annotate explicitly:
     ```python
     for x in range(4):  # noqa: VEC001 4x4 macro-tile sharpness slicing
     ```

3. **Memory Ceiling Violation (`pytest-memray`):**
   - Ensure frame decoders and scoring loops use generator streams (`yield from frame_generator()`) rather than returning full lists of arrays.
   - Allow Python GC to collect previous batch frames before allocating the next batch.

4. **Angular OnPush / Rendering Performance:**
   - Add `changeDetection: ChangeDetectionStrategy.OnPush` to `@Component({...})`.
   - Use Angular `signal()`, `computed()`, and `effect()` to drive reactive UI rendering without triggering global zone re-renders.
