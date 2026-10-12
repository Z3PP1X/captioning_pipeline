"""Unit tests for AutoChunker, LocalTagger, and AutoProcessor."""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import cv2
import numpy as np
import pytest

from pipeline.autochunk import AutoChunker
from pipeline.autoprocess import AutoProcessor
from pipeline.config import Settings
from pipeline.db import DB
from pipeline.tagger import LocalTagger


def test_autochunker_balanced_partition():
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
                VALUES ('v_test', '/fake/video.mp4', 'Test Video.mp4', 'test_video', 'new', 600.0)
                """
            )

        # Mock indexer to return 5 cuts
        mock_indexer = MagicMock()
        mock_indexer.get_or_build_filmstrip.return_value = {
            "cuts": [60.0, 150.0, 240.0, 360.0, 480.0],
            "thumbnails": [],
        }

        chunker = AutoChunker(db, settings, indexer=mock_indexer)
        chunks = chunker.generate_balanced_chunks("v_test", target_frames=120)

        assert len(chunks) >= 3
        total_k = sum(c["k"] for c in chunks)
        # Should be roughly within target range (100-200)
        assert 80 <= total_k <= 200
        assert all(c["rule"] == "person" for c in chunks)


def test_local_tagger_cosine_matching():
    # Test tagging with synthetic embeddings
    mock_classifier = MagicMock()
    # Return 512-dim mock embeddings for tag prompts
    mock_classifier.embed_texts.side_effect = lambda texts: np.tile(
        np.array([1.0] + [0.0] * 511, dtype=np.float32), (len(texts), 1)
    )

    tagger = LocalTagger(classifier=mock_classifier)
    test_vec = np.array([1.0] + [0.0] * 511, dtype=np.float32)

    tags = tagger.tag_frame(test_vec, top_k_per_category=1, threshold=0.1)
    assert len(tags) > 0
