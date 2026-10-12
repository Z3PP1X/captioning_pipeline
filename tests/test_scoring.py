"""Unit tests for the 7-dimension ScoringEngine and 750 -> 300 reduction."""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from pipeline.autochunk import AutoChunker
from pipeline.config import Settings
from pipeline.db import DB
from pipeline.scoring import ScoringEngine


def test_autochunker_max_15_constraint():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        db = DB(tmp_path / "test.db")
        settings = Settings(
            video_dir=tmp_path / "videos",
            cache_dir=tmp_path / "cache",
            finished_dir=tmp_path / "finished",
        )
        settings.ensure_dirs()

        with db.write() as conn:
            conn.execute(
                """
                INSERT INTO videos (id, path, name, slug, status, duration)
                VALUES ('v_test', '/fake/video.mp4', 'Test Video.mp4', 'test_video', 'new', 3600.0)
                """
            )

        # Mock indexer returning 30 cuts (which would produce 31 scenes)
        mock_indexer = MagicMock()
        mock_indexer.get_or_build_filmstrip.return_value = {
            "cuts": [float(i * 100) for i in range(1, 31)],
            "thumbnails": [],
        }

        chunker = AutoChunker(db, settings, indexer=mock_indexer)
        chunks = chunker.generate_balanced_chunks("v_test", target_frames=300)

        # Must be strictly <= 15 chunks
        assert len(chunks) <= 15
        assert len(chunks) > 0

        # Total budget percentage must sum to 100.0%
        tot_budget = sum(c["budget_pct"] for c in chunks)
        assert abs(tot_budget - 100.0) < 0.2


def test_scoring_engine_dimension_evaluation():
    mock_classifier = MagicMock()
    # 512-dim mock embeddings
    mock_classifier.embed_texts.side_effect = lambda texts: np.tile(
        np.array([1.0] + [0.0] * 511, dtype=np.float32), (len(texts), 1)
    )

    engine = ScoringEngine(classifier=mock_classifier)

    # Synthetic candidate
    dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)
    dummy_img[100:300, 100:300] = 180  # add some contrast
    emb = np.array([1.0] + [0.0] * 511, dtype=np.float32)

    candidates = [{"img_bgr": dummy_img, "chunk_id": 1, "t": 10.5}]
    scored = engine.score_candidates_batch(candidates, np.array([emb]))

    assert len(scored) == 1
    sc = scored[0]["scores"]
    assert "sharpness" in sc
    assert "quality" in sc
    assert "multiple_persons" in sc
    assert "one_person" in sc
    assert "tools" in sc
    assert "equipment" in sc
    assert "face_score" in sc
    assert "face_visible_50pct" in sc
    assert "composite" in sc
    assert 0.0 <= sc["composite"] <= 1.0


def test_reduction_750_to_300_budget_weights():
    mock_classifier = MagicMock()
    mock_classifier.embed_texts.side_effect = lambda texts: np.tile(
        np.array([1.0] + [0.0] * 511, dtype=np.float32), (len(texts), 1)
    )
    engine = ScoringEngine(classifier=mock_classifier)

    # 3 chunks with custom budget weights: 50%, 30%, 20%
    chunks = [
        {"id": 1, "budget_pct": 50.0},
        {"id": 2, "budget_pct": 30.0},
        {"id": 3, "budget_pct": 20.0},
    ]

    # Generate 600 synthetic candidates across the 3 chunks
    candidates = []
    np.random.seed(42)
    for cid, count in [(1, 300), (2, 200), (3, 100)]:
        for i in range(count):
            emb = np.random.randn(512).astype(np.float32)
            emb /= np.linalg.norm(emb)
            candidates.append({
                "id": f"c_{cid}_{i}",
                "chunk_id": cid,
                "t": float(i * 2.0),
                "embedding": emb,
                "composite": float(np.random.uniform(0.4, 0.95)),
                "scores": {
                    "multiple_persons": float(np.random.uniform(0.2, 0.8)),
                    "one_person": float(np.random.uniform(0.1, 0.7)),
                    "tools": float(np.random.uniform(0.1, 0.6)),
                    "equipment": float(np.random.uniform(0.1, 0.7)),
                    "face_score": float(np.random.uniform(0.2, 0.9)),
                    "face_visible_50pct": np.random.rand() > 0.4,
                },
            })

    picks, runners, stats = engine.reduce_750_to_300(candidates, chunks, target_total=300)

    # Total picks should be exactly 300
    assert len(picks) == 300
    assert len(runners) == len(candidates) - 300

    # Verify chunk distribution matches budget weights:
    # Chunk 1 (50%): ~150 frames
    # Chunk 2 (30%): ~90 frames
    # Chunk 3 (20%): ~60 frames
    c1_picks = [p for p in picks if p["chunk_id"] == 1]
    c2_picks = [p for p in picks if p["chunk_id"] == 2]
    c3_picks = [p for p in picks if p["chunk_id"] == 3]

    assert abs(len(c1_picks) - 150) <= 2
    assert abs(len(c2_picks) - 90) <= 2
    assert abs(len(c3_picks) - 60) <= 2

    # Stats dictionary check
    assert "multiple_pct" in stats
    assert "one_person_pct" in stats
    assert "tools_equipment_pct" in stats
    assert "face_visible_pct" in stats
