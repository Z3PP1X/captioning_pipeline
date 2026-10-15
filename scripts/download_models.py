"""Pre-downloads ONNX CLIP models and tokenizer into cache/models dir."""
from __future__ import annotations

import sys
from huggingface_hub import hf_hub_download


def download_models(target_dir: str = "/app/models") -> None:
    repo = "Xenova/clip-vit-base-patch32"
    files = [
        "onnx/vision_model_quantized.onnx",
        "onnx/text_model_quantized.onnx",
        "tokenizer.json",
    ]
    print(f"Downloading ONNX models from {repo} to {target_dir}...")
    for f in files:
        path = hf_hub_download(repo, f, cache_dir=target_dir)
        print(f"Cached {f} -> {path}")
    print("All ONNX models successfully cached.")


if __name__ == "__main__":
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "/app/models"
    download_models(out_dir)
