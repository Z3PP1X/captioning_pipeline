"""RAG Indexer: Parses 2,500 curated captions into 4 domains, tags taxonomy, and embeds vectors."""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

import numpy as np

from pipeline.classifier import ClipClassifier
from pipeline.config import Settings, get_settings
from pipeline.rag.taxonomy import TAXONOMY_CATEGORIES

logger = logging.getLogger(__name__)

SENTENCE_SPLIT_REGEX = re.compile(r"(?<=[.!?])\s+")


class RagCaptionIndexer:
    """Indexes captions from curated_2500 train and val sets into categorized domain chunks and embeddings."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.classifier = ClipClassifier(self.settings)
        self.db_path = self.settings.cache_dir / "rag_index.sqlite"
        self.npz_path = self.settings.cache_dir / "rag_embeddings.npz"
        self._init_db()

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rag_captions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    split TEXT NOT NULL,
                    stem TEXT NOT NULL UNIQUE,
                    img_rel_path TEXT,
                    txt_rel_path TEXT,
                    full_caption TEXT NOT NULL,
                    equipment_text TEXT NOT NULL,
                    surroundings_text TEXT NOT NULL,
                    position_text TEXT NOT NULL,
                    lighting_text TEXT NOT NULL,
                    equipment_tags TEXT NOT NULL,
                    surroundings_tags TEXT NOT NULL,
                    position_tags TEXT NOT NULL,
                    camera_lighting_tags TEXT NOT NULL,
                    word_count INTEGER NOT NULL,
                    indexed_at REAL NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS ix_rag_split ON rag_captions(split)")
            conn.execute("CREATE INDEX IF NOT EXISTS ix_rag_stem ON rag_captions(stem)")

    @staticmethod
    def segment_caption(caption: str) -> dict[str, str]:
        """Segments a Krea 2 dense caption into the 4 primary descriptive domains."""
        sentences = [s.strip() for s in SENTENCE_SPLIT_REGEX.split(caption.strip()) if s.strip()]

        equipment_sents: list[str] = []
        surroundings_sents: list[str] = []
        position_sents: list[str] = []
        lighting_sents: list[str] = []

        equip_words = {"restrain", "restrained", "bound", "rope", "hemp", "jute", "cordage", "chain", "chains",
                       "cuff", "cuffs", "collar", "gag", "harness", "tie", "ties", "rigging", "shackles", "spreader"}
        surround_words = {"set in", "setting", "scene is set", "basement", "cellar", "brick", "concrete", "wood",
                          "wooden", "wall", "floor", "dungeon", "studio", "void", "loft", "room"}
        position_words = {"stand", "standing", "stands", "kneel", "kneeling", "seiza", "tabletop", "all-fours",
                          "quadruped", "arched", "lordosis", "prone", "supine", "posture", "stance", "torso", "arms"}
        lighting_words = {"light", "lighting", "chiaroscuro", "shadow", "shadows", "spotlight", "lens", "f/2.8",
                          "depth of field", "bokeh", "specular", "reflections", "highlights", "optics"}

        for s in sentences:
            s_lower = s.lower()
            tokens = set(re.findall(r"\b\w+\b", s_lower))

            is_lighting = any(w in s_lower for w in lighting_words)
            is_surround = any(w in s_lower for w in surround_words)
            is_equip = any(w in s_lower for w in equip_words)
            is_pos = any(w in s_lower for w in position_words)

            if is_lighting:
                lighting_sents.append(s)
            elif is_surround:
                surroundings_sents.append(s)
            elif is_equip:
                equipment_sents.append(s)
            elif is_pos:
                position_sents.append(s)
            else:
                # Fallback based on sentence position in 8-domain structure
                if len(equipment_sents) == 0:
                    equipment_sents.append(s)
                else:
                    position_sents.append(s)

        return {
            "equipment": " ".join(equipment_sents),
            "surroundings": " ".join(surroundings_sents),
            "position": " ".join(position_sents),
            "camera_lighting": " ".join(lighting_sents),
        }

    @staticmethod
    def match_taxonomy_tags(text: str, category_key: str) -> list[str]:
        """Matches lowercase text against canonical taxonomy keywords for a specific category."""
        matched: list[str] = []
        text_lower = text.lower()
        cat_data = TAXONOMY_CATEGORIES.get(category_key, {})
        for _, items in cat_data.get("groups", {}).items():
            for item in items:
                tag_id = item["id"]
                for kw in item.get("keywords", []):
                    if kw.lower() in text_lower:
                        matched.append(tag_id)
                        break
        return matched

    def scan_dataset(self, dataset_dir: Path | None = None) -> list[dict[str, Any]]:
        """Scans curated_2500 train and val directories and prepares raw records."""
        base_dir = dataset_dir or self.settings.curated_dir
        records: list[dict[str, Any]] = []

        for split in ("train", "val"):
            split_dir = base_dir / split
            if not split_dir.is_dir():
                continue

            for txt_file in sorted(split_dir.glob("*.txt")):
                stem = txt_file.stem
                img_file = txt_file.with_suffix(".png")

                try:
                    caption = txt_file.read_text(encoding="utf-8").strip()
                except Exception as err:
                    logger.warning("Failed to read %s: %s", txt_file, err)
                    continue

                if not caption:
                    continue

                segments = self.segment_caption(caption)
                equip_tags = self.match_taxonomy_tags(segments["equipment"] or caption, "equipment")
                surround_tags = self.match_taxonomy_tags(segments["surroundings"] or caption, "surroundings")
                pos_tags = self.match_taxonomy_tags(segments["position"] or caption, "position")
                light_tags = self.match_taxonomy_tags(segments["camera_lighting"] or caption, "camera_lighting")

                img_rel = str(img_file.relative_to(self.settings.project_root)) if img_file.is_file() else None
                txt_rel = str(txt_file.relative_to(self.settings.project_root))

                records.append({
                    "split": split,
                    "stem": stem,
                    "img_rel_path": img_rel,
                    "txt_rel_path": txt_rel,
                    "full_caption": caption,
                    "equipment_text": segments["equipment"],
                    "surroundings_text": segments["surroundings"],
                    "position_text": segments["position"],
                    "lighting_text": segments["camera_lighting"],
                    "equipment_tags": equip_tags,
                    "surroundings_tags": surround_tags,
                    "position_tags": pos_tags,
                    "camera_lighting_tags": light_tags,
                    "word_count": len(caption.split()),
                })

        return records

    def build_index(self, limit: int | None = None, batch_size: int = 64) -> dict[str, Any]:
        """Extracts domains, computes CLIP text embeddings, and updates SQLite + NPZ."""
        start_t = time.time()
        records = self.scan_dataset()
        if limit and limit > 0:
            records = records[:limit]

        total = len(records)
        if total == 0:
            return {"indexed": 0, "duration": round(time.time() - start_t, 2)}

        # 1. Compute CLIP embeddings in batches
        all_captions = [r["full_caption"] for r in records]
        embeddings_list: list[np.ndarray] = []

        for i in range(0, total, batch_size):
            batch_texts = all_captions[i:i + batch_size]
            emb_chunk = self.classifier.embed_texts(batch_texts)
            embeddings_list.append(emb_chunk)

        embeddings_matrix = np.concatenate(embeddings_list, axis=0) if embeddings_list else np.zeros((0, 512), dtype=np.float32)

        # 2. Persist to SQLite
        now = time.time()
        record_ids: list[int] = []

        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM rag_captions")
            cursor = conn.cursor()
            for r in records:
                cursor.execute(
                    """
                    INSERT INTO rag_captions (
                        split, stem, img_rel_path, txt_rel_path, full_caption,
                        equipment_text, surroundings_text, position_text, lighting_text,
                        equipment_tags, surroundings_tags, position_tags, camera_lighting_tags,
                        word_count, indexed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        r["split"],
                        r["stem"],
                        r["img_rel_path"],
                        r["txt_rel_path"],
                        r["full_caption"],
                        r["equipment_text"],
                        r["surroundings_text"],
                        r["position_text"],
                        r["lighting_text"],
                        json.dumps(r["equipment_tags"]),
                        json.dumps(r["surroundings_tags"]),
                        json.dumps(r["position_tags"]),
                        json.dumps(r["camera_lighting_tags"]),
                        r["word_count"],
                        now,
                    ),
                )
                record_ids.append(cursor.lastrowid)
            conn.commit()

        # 3. Save NPZ vector file
        np.savez_compressed(
            self.npz_path,
            ids=np.array(record_ids, dtype=np.int64),
            embeddings=embeddings_matrix.astype(np.float32),
        )

        duration = round(time.time() - start_t, 2)
        return {
            "indexed": total,
            "embeddings_shape": list(embeddings_matrix.shape),
            "db_path": str(self.db_path),
            "npz_path": str(self.npz_path),
            "duration": duration,
        }
