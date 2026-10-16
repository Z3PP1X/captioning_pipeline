"""Pre-downloads ONNX CLIP models, tokenizer, and OpenCV Face models into models dir.

`--optional` also fetches the opt-in models (about 300 MB): DINOv2-S (`selection_embedder: dinov2`), SigLIP
(Prompt Director "more like this frame") and TOPIQ-NR (`iqa_model: topiq_nr`). Without them the app downloads them
on first use, or falls back (SigLIP -> CLIP).
"""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path
from huggingface_hub import hf_hub_download

YUNET_URL = "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
SFACE_URL = "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx"


# repo, file - keep in sync with pipeline.embedders / pipeline.rag.image_embedder / pipeline.quality
OPTIONAL_MODELS = [
    ("onnx-community/dinov2-small", "onnx/model_quantized.onnx"),
    ("Xenova/siglip-base-patch16-224", "onnx/vision_model_quantized.onnx"),
    ("nocturna/topiq-nr-koniq-onnx", "model.onnx"),
]


def download_models(target_dir: str = "/app/models", optional: bool = False) -> None:
    t_path = Path(target_dir)
    t_path.mkdir(parents=True, exist_ok=True)

    # 1. HuggingFace CLIP models
    clip_repo = "Xenova/clip-vit-base-patch32"
    clip_files = [
        "onnx/vision_model_quantized.onnx",
        "onnx/text_model_quantized.onnx",
        "tokenizer.json",
    ]
    print(f"Downloading ONNX CLIP models from {clip_repo} to {target_dir}...")
    for f in clip_files:
        path = hf_hub_download(clip_repo, f, cache_dir=str(t_path))
        print(f"Cached CLIP {f} -> {path}")

    # 2. OpenCV Face Models (YuNet + SFace)
    face_dir = t_path / "face"
    face_dir.mkdir(parents=True, exist_ok=True)

    yunet_dest = face_dir / "face_detection_yunet_2023mar.onnx"
    if not yunet_dest.is_file():
        print(f"Downloading YuNet face detector to {yunet_dest}...")
        urllib.request.urlretrieve(YUNET_URL, yunet_dest)
        print("Cached YuNet model successfully.")
    else:
        print(f"YuNet model already present at {yunet_dest}")

    sface_dest = face_dir / "face_recognition_sface_2021dec.onnx"
    if not sface_dest.is_file():
        print(f"Downloading SFace face recognizer to {sface_dest}...")
        urllib.request.urlretrieve(SFACE_URL, sface_dest)
        print("Cached SFace model successfully.")
    else:
        print(f"SFace model already present at {sface_dest}")

    if optional:
        for repo, filename in OPTIONAL_MODELS:
            path = hf_hub_download(repo, filename, cache_dir=str(t_path))
            print(f"Cached optional {repo}/{filename} -> {path}")

    print("All models successfully cached.")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    out_dir = args[0] if args else "/app/models"
    download_models(out_dir, optional="--optional" in sys.argv[1:])
