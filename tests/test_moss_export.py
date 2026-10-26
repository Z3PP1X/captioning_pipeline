"""Tests for MOSS-Image database schema, models, and multi-line exporter."""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

from pipeline.captions import get_moss_caption, record_moss_caption
from pipeline.db import DB
from pipeline.export_moss import CAPTION_SEPARATOR, export_moss_dataset
from pipeline.moss_models import (
    MossCaptionRecord,
    MossDatasetConfig,
    TargetArchitecture,
    TrainingMode,
)


def test_moss_db_schema_and_caption_storage(tmp_path: Path) -> None:
    db = DB(tmp_path / "test.db")

    # Insert test video
    with db.write() as conn:
        conn.execute(
            """
            INSERT INTO videos (id, path, name, slug, target_architecture, training_mode, trigger_phrase, class_noun)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("v1", "/tmp/v1.mp4", "Video 1", "video_1", "MOSS_IMAGE", "CHARACTER", "ohwx woman", "woman"),
        )
        conn.execute(
            """
            INSERT INTO chunks (id, video_id, position, start_s, end_s)
            VALUES (1, 'v1', 0, 0.0, 5.0)
            """
        )
        conn.execute(
            """
            INSERT INTO frames (id, video_id, chunk_id, t)
            VALUES (101, 'v1', 1, 1.5)
            """
        )

    # Test record_moss_caption using dict
    with db.write() as conn:
        record_moss_caption(
            conn,
            frame_id=101,
            video_id="v1",
            caption_long="A detailed view of a woman with braided silver hair and green eyes.",
            caption_medium="A portrait of a woman with silver hair.",
            caption_short="A woman looking at the camera.",
            trigger_phrase="ohwx woman",
            watermark=[{"box_2d": [900, 850, 950, 950], "label": "watermark"}],
            borders=["up", "down"],
            suggested_crop={"crop_box": [0, 50, 1024, 900], "target_bucket": [1024, 1024]},
            applied_crop={"crop_box": [0, 50, 1024, 900], "target_bucket": [1024, 1024]},
            leaked_traits=["silver hair"],
            review_status="APPROVED",
            model_used="google/gemini-2.5-flash",
        )

    row = get_moss_caption(db, 101)
    assert row is not None
    assert row["caption_long"] == "A detailed view of a woman with braided silver hair and green eyes."
    assert row["caption_medium"] == "A portrait of a woman with silver hair."
    assert row["caption_short"] == "A woman looking at the camera."
    assert row["review_status"] == "APPROVED"
    assert row["model_used"] == "google/gemini-2.5-flash"
    assert isinstance(row["watermark"], list)
    assert isinstance(row["borders"], list)
    assert isinstance(row["suggested_crop"], dict)
    assert row["leaked_traits"] == ["silver hair"]

    # Test get/set video moss config
    cfg = db.get_video_moss_config("v1")
    assert cfg["target_architecture"] == "MOSS_IMAGE"
    assert cfg["training_mode"] == "CHARACTER"
    assert cfg["trigger_phrase"] == "ohwx woman"

    new_cfg = MossDatasetConfig(
        target_architecture=TargetArchitecture.MOSS_IMAGE,
        training_mode=TrainingMode.AESTHETIC_STYLE,
        trigger_phrase="artstyle_test",
        class_noun="style",
        locked_traits=["lineart", "watercolor"],
    )
    db.set_video_moss_config("v1", new_cfg)
    cfg2 = db.get_video_moss_config("v1")
    assert cfg2["training_mode"] == "AESTHETIC_STYLE"
    assert cfg2["locked_traits"] == ["lineart", "watercolor"]


def test_moss_caption_record_model(tmp_path: Path) -> None:
    db = DB(tmp_path / "test2.db")

    with db.write() as conn:
        conn.execute("INSERT INTO videos (id, path, name, slug) VALUES ('v2', '/tmp/v2.mp4', 'V2', 'v2')")
        conn.execute("INSERT INTO frames (id, video_id, t) VALUES (201, 'v2', 2.0)")

    rec = MossCaptionRecord(
        frame_id=201,
        video_id="v2",
        trigger_phrase="my_obj",
        caption_long="A detailed metallic robot.",
        caption_medium="A robot standing.",
        caption_short="A robot.",
        review_status="PENDING",
        model_used="gemini",
    )

    with db.write() as conn:
        record_moss_caption(conn, rec)

    retrieved = get_moss_caption(db, 201)
    assert retrieved is not None
    assert retrieved["caption_long"] == "A detailed metallic robot."
    assert retrieved["trigger"] == "my_obj"


def test_export_moss_dataset(tmp_path: Path) -> None:
    db_path = tmp_path / "export_test.db"
    db = DB(db_path)

    # Create dummy image on disk
    img_dir = tmp_path / "images"
    img_dir.mkdir()
    sample_img_path = img_dir / "frame_000101.png"
    dummy_img = np.full((1080, 1920, 3), 100, dtype=np.uint8)
    cv2.imwrite(str(sample_img_path), dummy_img)

    with db.write() as conn:
        conn.execute(
            """
            INSERT INTO videos (id, path, name, slug, trigger_phrase)
            VALUES ('v1', '/path/v1.mp4', 'Video1', 'video1', 'ohwx person')
            """
        )
        conn.execute(
            """
            INSERT INTO frames (id, video_id, t, full_path)
            VALUES (101, 'v1', 3.5, ?)
            """,
            (str(sample_img_path),),
        )
        conn.execute("INSERT INTO image_tags (id, name, slug, created_at, updated_at) VALUES (1, 'portrait', 'portrait', 0.0, 0.0)")
        conn.execute("INSERT INTO frame_tags (frame_id, tag_id, status, updated_at) VALUES (101, 1, 'confirmed', 0.0)")

        record_moss_caption(
            conn,
            frame_id=101,
            video_id="v1",
            caption_long="standing outside in autumn park with warm lighting.",
            caption_medium="standing in a park.",
            caption_short="a person in a park.",
            trigger_phrase="ohwx person",
            watermark=[{"box_2d": [900, 800, 950, 900], "label": "watermark"}],
            borders=[],
            suggested_crop={"crop_box": [0, 0, 1344, 768], "target_bucket": [1344, 768]},
            applied_crop={"crop_box": [0, 0, 1344, 768], "target_bucket": [1344, 768]},
            model_used="google/gemini-2.5-flash",
        )

    export_out = tmp_path / "moss_export"
    res = export_moss_dataset(db, "v1", export_out, target_format="png")

    assert res["exported_count"] == 1
    assert export_out.is_dir()

    # Verify generated files
    img_file = export_out / "frame_000101.png"
    txt_file = export_out / "frame_000101.txt"
    json_file = export_out / "frame_000101_caption.json"
    toml_file = export_out / "moss_dataset_config.toml"

    assert img_file.is_file()
    assert txt_file.is_file()
    assert json_file.is_file()
    assert toml_file.is_file()

    # Verify exported image dimensions match target bucket (1344x768)
    exported_img = cv2.imread(str(img_file))
    assert exported_img.shape == (768, 1344, 3)

    # Verify multi-line caption structure
    txt_content = txt_file.read_text(encoding="utf-8")
    lines = [line.strip() for line in txt_content.split(CAPTION_SEPARATOR)]
    assert len(lines) == 3
    assert lines[0] == "ohwx person, standing outside in autumn park with warm lighting."
    assert lines[1] == "ohwx person, standing in a park."
    assert lines[2] == "ohwx person, a person in a park."

    # Verify JSON sidecar structure
    sidecar = json.loads(json_file.read_text(encoding="utf-8"))
    assert sidecar["frame_id"] == 101
    assert sidecar["video_id"] == "v1"
    assert sidecar["trigger_phrase"] == "ohwx person"
    assert sidecar["captions"]["long"] == lines[0]
    assert sidecar["captions"]["medium"] == lines[1]
    assert sidecar["captions"]["short"] == lines[2]
    assert sidecar["tags"] == ["portrait"]
    assert sidecar["timestamp"] == 3.5
    assert sidecar["model_used"] == "google/gemini-2.5-flash"
    assert sidecar["crop"]["crop_box"] == [0, 0, 1344, 768]

    # Verify TOML config content
    toml_content = toml_file.read_text(encoding="utf-8")
    assert "enable_multiline_caption = true" in toml_content
    assert 'caption_separator = "---===---"' in toml_content
    assert 'target_architecture = "MOSS_IMAGE"' in toml_content
