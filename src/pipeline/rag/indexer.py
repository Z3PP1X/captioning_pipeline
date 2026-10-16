"""RAG Indexer: Parses captions into 4 domains, tags taxonomy, and embeds vectors.

Caption source (first non-empty wins):
1. the ``captions`` table in state.db (sources 'vlm' and 'imported', never 'stub'), see ``scan_table``
2. ``data/curated_2500/{train,val}/*.txt`` written by ``scripts/curate_dataset.py``
3. ``<finished_dir>/merged/*.txt`` written by the regular Export step

``update_index`` (R6) embeds only new or changed captions; ``split_for_key`` (R10) is the held-out split.

Embeddings (``rag_embeddings.npz``): ``ids`` + ``embeddings`` = one CLIP text vector per whole caption (CLIP
truncates at 77 tokens, i.e. roughly the first 55 words). R1 adds per-domain vectors in the same file:
``seg_ids`` (caption id), ``seg_domains`` (index into ``DOMAIN_KEYS``) and ``seg_embeddings``, one row per
chunk (at most ``DOMAIN_CHUNK_WORDS`` words, well under 77 tokens) of each domain segment. Indexes without
these arrays still load (the retriever falls back to whole-caption scoring); an incremental update backfills
them. R3 image embeddings of the captions' images live in ``rag_image_embeddings.npz`` (see
``image_index_path``; built when the user setting ``rag_image_index`` is on).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np

from pipeline.classifier import ClipClassifier
from pipeline.config import DEFAULT_USER_SETTINGS, Settings, get_settings
from pipeline.rag.image_embedder import AutoImageEmbedder, default_image_embedder, load_image_bgr
from pipeline.rag.taxonomy import TAXONOMY_CATEGORIES

logger = logging.getLogger(__name__)

SENTENCE_SPLIT_REGEX = re.compile(r"(?<=[.!?])\s+")

FTS_TABLE = "rag_captions_fts"

# R1 per-domain embeddings: the taxonomy categories, in the order of ``seg_domains`` values
DOMAIN_KEYS = ("equipment", "surroundings", "position", "camera_lighting")
DOMAIN_TEXT_COLUMNS = ("equipment_text", "surroundings_text", "position_text", "lighting_text")
# ~1.3 CLIP BPE tokens per word plus punctuation: 40 words stay below the 77-token context
DOMAIN_CHUNK_WORDS = 40
IMAGE_BATCH = 16


def chunk_text(text: str, max_words: int = DOMAIN_CHUNK_WORDS) -> list[str]:
    """Whole sentences packed greedily into chunks of at most ``max_words`` words (longer sentences are cut)."""
    chunks: list[str] = []
    current: list[str] = []
    for sentence in SENTENCE_SPLIT_REGEX.split((text or "").strip()):
        words = sentence.split()
        if not words:
            continue
        if current and len(current) + len(words) > max_words:
            chunks.append(" ".join(current))
            current = []
        while len(words) > max_words:
            chunks.append(" ".join(words[:max_words]))
            words = words[max_words:]
        current.extend(words)
    if current:
        chunks.append(" ".join(current))
    return chunks


def domain_segments(record: dict[str, Any], max_words: int = DOMAIN_CHUNK_WORDS) -> list[tuple[int, str]]:
    """(domain index, chunk text) rows of a caption record's 4 domain texts."""
    return [
        (d, chunk)
        for d, column in enumerate(DOMAIN_TEXT_COLUMNS)
        for chunk in chunk_text(record.get(column) or "", max_words)
    ]


def image_index_path(settings: Settings) -> Path:
    """R3 image embeddings of the indexed captions' images (``ids``, ``embeddings``, ``valid``, ``paths``, ``embedder``)."""
    return settings.cache_dir / "rag_image_embeddings.npz"


def read_user_setting(settings: Settings, key: str) -> Any:
    """A user setting from state.db (``settings.user_settings``), else its default; never raises."""
    default = DEFAULT_USER_SETTINGS.get(key)
    if not settings.db_path.is_file():
        return default
    try:
        with sqlite3.connect(settings.db_path, timeout=10) as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='settings'").fetchone():
                return default
            row = conn.execute("SELECT value FROM settings WHERE key='user_settings'").fetchone()
        stored = json.loads(row[0]) if row else {}
    except (sqlite3.Error, ValueError, TypeError):
        return default
    return stored.get(key, default) if isinstance(stored, dict) else default


def load_image_index(path: Path) -> dict[str, Any] | None:
    """The image index file as a dict (None when missing or unreadable)."""
    if not path.is_file():
        return None
    try:
        with np.load(path) as npz:
            data = {
                "ids": npz["ids"].astype(np.int64),
                "embeddings": npz["embeddings"].astype(np.float32),
                "valid": npz["valid"].astype(bool),
                "paths": [str(p) for p in npz["paths"]],
                "embedder": str(npz["embedder"]),
                "model": str(npz["model"]),
                "built_at": str(npz["built_at"]) if "built_at" in npz.files else "",
            }
    except Exception as err:  # half-written / foreign file: treated as missing
        logger.warning("Cannot read RAG image index %s: %s", path, err)
        return None
    return data


def keyword_pattern(keywords: Iterable[str]) -> re.Pattern[str]:
    """Whole-word regex for a keyword list (``light`` no longer hits "slightly", ``room`` not "broom").

    A trailing plural ``s``/``es`` is allowed and spaces/hyphens are interchangeable ("all fours" = "all-fours").
    """
    alts: list[str] = []
    for kw in sorted({k.strip().lower() for k in keywords if k.strip()}, key=len, reverse=True):
        alts.append(re.sub(r"(?:\\ |\\-)+", r"[\\s-]+", re.escape(kw)))
    if not alts:
        return re.compile(r"(?!x)x")  # matches nothing
    return re.compile(r"(?<!\w)(?:" + "|".join(alts) + r")(?:e?s)?(?!\w)", re.IGNORECASE)


_DOMAIN_WORDS = {
    "equipment": ("restrain", "restrained", "restraint", "bound", "rope", "hemp", "jute", "cordage", "chain",
                  "chained", "cuff", "handcuff", "collar", "gag", "harness", "tie", "tied", "rigging", "rigged",
                  "shackle", "spreader"),
    "surroundings": ("set in", "setting", "scene is set", "basement", "cellar", "brick", "concrete", "wood",
                     "wooden", "wall", "floor", "dungeon", "studio", "void", "loft", "room"),
    "position": ("stand", "standing", "stands", "kneel", "kneeling", "seiza", "tabletop", "all-fours",
                 "quadruped", "arched", "lordosis", "prone", "supine", "posture", "stance", "torso", "arms"),
    "camera_lighting": ("light", "lighting", "lit", "spotlight", "backlight", "backlit", "chiaroscuro", "shadow",
                        "shadows", "lens", "f/2.8", "depth of field", "bokeh", "specular", "reflections",
                        "highlights", "optics"),
}
_DOMAIN_PATTERNS = {key: keyword_pattern(words) for key, words in _DOMAIN_WORDS.items()}
_TAG_PATTERNS: dict[str, list[tuple[str, re.Pattern[str]]]] = {}


def _taxonomy_patterns(category_key: str) -> list[tuple[str, re.Pattern[str]]]:
    if category_key not in _TAG_PATTERNS:
        cat_data = TAXONOMY_CATEGORIES.get(category_key, {})
        _TAG_PATTERNS[category_key] = [
            (item["id"], keyword_pattern(item.get("keywords", [])))
            for items in cat_data.get("groups", {}).values()
            for item in items
        ]
    return _TAG_PATTERNS[category_key]


def extract_source_id(stem: str) -> str:
    """Source video of a file stem without a captions-table video id ('vid_a_0001' -> 'vid_a')."""
    if "_frame_" in stem:
        return stem.split("_frame_")[0]
    parts = stem.rsplit("_", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[0]
    return stem


def fts5_available() -> bool:
    """True when the stdlib sqlite was compiled with FTS5 (BM25 lexical search)."""
    try:
        with sqlite3.connect(":memory:") as conn:
            conn.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        return True
    except sqlite3.Error:
        return False


def build_fts(conn: sqlite3.Connection, built_at: str) -> bool:
    """(Re)build the external-content FTS5 index over ``rag_captions``; False if FTS5 is unavailable.

    Records the index build it belongs to (``rag_meta.fts_built_at``) so readers can detect a stale table.
    """
    try:
        conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} USING fts5("
            "full_caption, equipment_text, surroundings_text, position_text, lighting_text, "
            "content='rag_captions', content_rowid='id', tokenize='porter unicode61')"
        )
        conn.execute(f"INSERT INTO {FTS_TABLE}({FTS_TABLE}) VALUES('rebuild')")
    except sqlite3.OperationalError as err:
        logger.info("FTS5 unavailable, BM25 lexical search disabled: %s", err)
        return False
    conn.execute("CREATE TABLE IF NOT EXISTS rag_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("INSERT OR REPLACE INTO rag_meta (key, value) VALUES ('fts_built_at', ?)", (built_at,))
    return True

ProgressCallback = Callable[[float, str], None]

# Primary caption source: the `captions` table in state.db (Phase G). Only real captions are indexed;
# 'stub' rows are tag placeholders the caption job replaces and must never become exemplars.
TABLE_SOURCE = "captions_table"
INDEXED_CAPTION_SOURCES = ("vlm", "imported")

# R10 deterministic held-out split: sha1(key) -> bucket 0..9999, buckets below VAL_FRACTION * 10000 are 'val'.
# key = "frame:<frame_id>" for table rows, the file stem for finished/merged files. The curated_2500 folders
# keep their own train/val split.
VAL_FRACTION = 0.10

_TABLE_WHERE = (
    f"c.source IN ({', '.join(repr(s) for s in INDEXED_CAPTION_SOURCES)}) AND TRIM(c.text) != '' "
    "AND (p.state IS NULL OR p.state != 'reject')"
)
_TABLE_FROM = (
    "FROM captions c "
    "LEFT JOIN frames f ON f.id = c.frame_id "
    "LEFT JOIN videos v ON v.id = c.video_id "
    "LEFT JOIN exports e ON e.frame_id = c.frame_id "
    "LEFT JOIN picks p ON p.frame_id = c.frame_id"
)


def split_for_key(key: str, val_fraction: float | None = None) -> str:
    """'val' for a stable ~10% of keys, else 'train' (same key -> same split on every rebuild and machine)."""
    frac = VAL_FRACTION if val_fraction is None else val_fraction
    bucket = int(hashlib.sha1(key.encode("utf-8")).hexdigest()[:8], 16) % 10000
    return "val" if bucket < round(frac * 10000) else "train"


def content_hash(text: str) -> str:
    """Short hash of the caption text; an incremental update re-embeds a row only when it changes."""
    return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()[:16]


def rag_index_paths(settings: Settings) -> tuple[Path, Path]:
    """Location of the RAG metadata DB and the embedding matrix."""
    return settings.cache_dir / "rag_index.sqlite", settings.cache_dir / "rag_embeddings.npz"


def _state_db_query(settings: Settings, sql: str) -> list[sqlite3.Row]:
    """Read-only query against state.db; [] when the DB or the captions table does not exist yet."""
    db_path = settings.db_path
    if not db_path.is_file():
        return []
    try:
        with sqlite3.connect(db_path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='captions'").fetchone():
                return []
            return conn.execute(sql).fetchall()
    except sqlite3.Error as err:
        logger.warning("Cannot read captions table from %s: %s", db_path, err)
        return []


def count_table_captions(settings: Settings) -> int:
    """Number of indexable captions (vlm/imported, frame not rejected) in the captions table."""
    rows = _state_db_query(settings, f"SELECT COUNT(*) AS n {_TABLE_FROM} WHERE {_TABLE_WHERE}")
    return int(rows[0]["n"]) if rows else 0


def resolve_caption_sources(settings: Settings, dataset_dir: Path | None = None) -> dict[str, Any]:
    """Pick the caption source: the captions table, else the curated train/val split, else finished/merged.

    ``dataset_dir`` forces a curated-style folder (train/ + val/) and skips the table.
    Each split is ``(name, folder)``; a ``None`` name means "hash split per file" (see ``split_for_key``).
    """
    if dataset_dir is None:
        table_count = count_table_captions(settings)
        if table_count:
            return {"source": TABLE_SOURCE, "path": str(settings.db_path), "splits": [], "caption_files": table_count}

    curated = dataset_dir or settings.curated_dir
    curated_splits: list[tuple[str | None, Path]] = [(split, curated / split) for split in ("train", "val")]
    curated_count = sum(len(list(d.glob("*.txt"))) for _, d in curated_splits if d.is_dir())
    if curated_count or dataset_dir is not None:
        return {"source": "curated", "path": str(curated), "splits": curated_splits, "caption_files": curated_count}

    merged = settings.finished_dir / "merged"
    merged_count = len(list(merged.glob("*.txt"))) if merged.is_dir() else 0
    if merged_count:
        return {"source": "finished_merged", "path": str(merged), "splits": [(None, merged)], "caption_files": merged_count}

    return {
        "source": None,
        "path": None,
        "splits": [],
        "caption_files": 0,
        "searched": [
            f"{settings.db_path} (captions table)", str(curated / "train"), str(curated / "val"), str(merged),
        ],
    }


def record_key(record: dict[str, Any]) -> str:
    """Identity of an indexed caption across rebuilds: its frame for table rows, else its file stem."""
    frame_id = record.get("frame_id")
    return f"frame:{frame_id}" if frame_id is not None else f"stem:{record['stem']}"


_RECORD_COLUMNS = (
    "split", "stem", "img_rel_path", "txt_rel_path", "full_caption",
    "equipment_text", "surroundings_text", "position_text", "lighting_text",
    "equipment_tags", "surroundings_tags", "position_tags", "camera_lighting_tags",
    "word_count", "indexed_at", "frame_id", "source_id", "content_hash", "caption_source",
)
_JSON_COLUMNS = {"equipment_tags", "surroundings_tags", "position_tags", "camera_lighting_tags"}
# Columns an incremental update refreshes in place (no re-embedding) when only the metadata changed
_META_COLUMNS = ("split", "stem", "img_rel_path", "txt_rel_path", "source_id", "caption_source")


def _record_values(record: dict[str, Any], now: float) -> tuple:
    out = []
    for col in _RECORD_COLUMNS:
        if col == "indexed_at":
            out.append(now)
        elif col in _JSON_COLUMNS:
            out.append(json.dumps(record[col]))
        else:
            out.append(record.get(col))
    return tuple(out)


_UNSET: Any = object()


class RagCaptionIndexer:
    """Indexes captions (captions table, else curated train/val or finished/merged files) into domain chunks + embeddings."""

    def __init__(
        self,
        settings: Settings | None = None,
        classifier: ClipClassifier | None = None,
        image_embedder: Any = _UNSET,
        image_index: bool | None = None,
    ):
        """``image_embedder``: R3 embedder (default: SigLIP, else CLIP for the real classifier; None = off).
        ``image_index``: build image embeddings (default: user setting ``rag_image_index``)."""
        self.settings = settings or get_settings()
        self.classifier = classifier or ClipClassifier(self.settings)
        self.db_path, self.npz_path = rag_index_paths(self.settings)
        self.image_path = image_index_path(self.settings)
        self.image_embedder = (
            default_image_embedder(self.settings, self.classifier) if image_embedder is _UNSET else image_embedder
        )
        self.image_index = image_index
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
                    indexed_at REAL NOT NULL,
                    frame_id INTEGER,
                    source_id TEXT,
                    content_hash TEXT,
                    caption_source TEXT
                )
                """
            )
            # Indexes built before the captions-table source lack the identity columns
            cols = {r[1] for r in conn.execute("PRAGMA table_info(rag_captions)")}
            for col, decl in (("frame_id", "INTEGER"), ("source_id", "TEXT"), ("content_hash", "TEXT"),
                              ("caption_source", "TEXT")):
                if col not in cols:
                    conn.execute(f"ALTER TABLE rag_captions ADD COLUMN {col} {decl}")
            conn.execute("CREATE INDEX IF NOT EXISTS ix_rag_split ON rag_captions(split)")
            conn.execute("CREATE INDEX IF NOT EXISTS ix_rag_stem ON rag_captions(stem)")
            conn.execute("CREATE INDEX IF NOT EXISTS ix_rag_frame ON rag_captions(frame_id)")
            conn.execute("CREATE TABLE IF NOT EXISTS rag_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")

    @staticmethod
    def segment_caption(caption: str) -> dict[str, str]:
        """Segments a Krea 2 dense caption into the 4 primary descriptive domains."""
        sentences = [s.strip() for s in SENTENCE_SPLIT_REGEX.split(caption.strip()) if s.strip()]

        equipment_sents: list[str] = []
        surroundings_sents: list[str] = []
        position_sents: list[str] = []
        lighting_sents: list[str] = []

        for s in sentences:
            is_lighting = bool(_DOMAIN_PATTERNS["camera_lighting"].search(s))
            is_surround = bool(_DOMAIN_PATTERNS["surroundings"].search(s))
            is_equip = bool(_DOMAIN_PATTERNS["equipment"].search(s))
            is_pos = bool(_DOMAIN_PATTERNS["position"].search(s))

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
        """Taxonomy tags of a category whose keywords occur in ``text`` as whole words (case-insensitive)."""
        return [tag_id for tag_id, pattern in _taxonomy_patterns(category_key) if pattern.search(text)]

    def _rel_path(self, path: Path) -> str:
        """Path relative to the project root when possible (portable), else absolute."""
        try:
            return str(path.relative_to(self.settings.project_root))
        except ValueError:
            return str(path)

    def _make_record(
        self,
        caption: str,
        *,
        split: str,
        stem: str,
        img_file: Path | None,
        txt_file: Path | None,
        frame_id: int | None = None,
        source_id: str | None = None,
        caption_source: str = "file",
    ) -> dict[str, Any]:
        segments = self.segment_caption(caption)
        return {
            "split": split,
            "stem": stem,
            "img_rel_path": self._rel_path(img_file) if img_file is not None else None,
            "txt_rel_path": self._rel_path(txt_file) if txt_file is not None else None,
            "full_caption": caption,
            "equipment_text": segments["equipment"],
            "surroundings_text": segments["surroundings"],
            "position_text": segments["position"],
            "lighting_text": segments["camera_lighting"],
            "equipment_tags": self.match_taxonomy_tags(segments["equipment"] or caption, "equipment"),
            "surroundings_tags": self.match_taxonomy_tags(segments["surroundings"] or caption, "surroundings"),
            "position_tags": self.match_taxonomy_tags(segments["position"] or caption, "position"),
            "camera_lighting_tags": self.match_taxonomy_tags(segments["camera_lighting"] or caption, "camera_lighting"),
            "word_count": len(caption.split()),
            "frame_id": frame_id,
            "source_id": source_id or extract_source_id(stem),
            "content_hash": content_hash(caption),
            "caption_source": caption_source,
        }

    def scan_table(self) -> list[dict[str, Any]]:
        """Records from the captions table (vlm + imported, never stubs; frames rejected later are skipped).

        Exemplar image: the frame's exported PNG (``exports.merged_path``, else ``per_video_path``), else the
        native/preview frame from the cache. The stem is the export file stem (``<slug>_cNN_<ms>``), else
        ``<slug>_f<frame_id>``; ``source_id`` is the video id (used by ``max_per_source`` and the eval).
        """
        rows = _state_db_query(
            self.settings,
            "SELECT c.frame_id, c.video_id, c.text, c.source, v.slug AS video_slug, "
            "e.merged_path, e.per_video_path, f.full_path, f.preview_path "
            f"{_TABLE_FROM} WHERE {_TABLE_WHERE} ORDER BY c.frame_id",
        )
        records: list[dict[str, Any]] = []
        seen_stems: set[str] = set()
        for r in rows:
            caption = (r["text"] or "").strip()
            if not caption:
                continue
            frame_id = int(r["frame_id"])
            export_path = r["merged_path"] or r["per_video_path"]
            stem = Path(export_path).stem if export_path else f"{r['video_slug'] or r['video_id'] or 'frame'}_f{frame_id}"
            if stem in seen_stems:
                stem = f"{stem}_f{frame_id}"
            seen_stems.add(stem)
            img_file = next(
                (Path(p) for p in (r["merged_path"], r["per_video_path"], r["full_path"], r["preview_path"])
                 if p and Path(p).is_file()),
                None,
            )
            txt_file = Path(export_path).with_suffix(".txt") if export_path else None
            records.append(self._make_record(
                caption,
                split=split_for_key(f"frame:{frame_id}"),
                stem=stem,
                img_file=img_file,
                txt_file=txt_file if txt_file is not None and txt_file.is_file() else None,
                frame_id=frame_id,
                source_id=r["video_id"] or None,
                caption_source=r["source"],
            ))
        return records

    def scan_dataset(self, dataset_dir: Path | None = None) -> list[dict[str, Any]]:
        """Scans the resolved caption source (captions table, curated train/val or merged export) into records."""
        sources = resolve_caption_sources(self.settings, dataset_dir)
        return self._collect(sources)

    def _collect(self, sources: dict[str, Any]) -> list[dict[str, Any]]:
        if sources["source"] == TABLE_SOURCE:
            return self.scan_table()

        records: list[dict[str, Any]] = []
        seen_stems: set[str] = set()
        for split, split_dir in sources["splits"]:
            if not split_dir.is_dir():
                continue

            for txt_file in sorted(split_dir.glob("*.txt")):
                stem = txt_file.stem
                if stem in seen_stems:
                    continue
                img_file = txt_file.with_suffix(".png")

                try:
                    caption = txt_file.read_text(encoding="utf-8").strip()
                except Exception as err:
                    logger.warning("Failed to read %s: %s", txt_file, err)
                    continue

                if not caption:
                    continue
                seen_stems.add(stem)
                records.append(self._make_record(
                    caption,
                    split=split or split_for_key(stem),
                    stem=stem,
                    img_file=img_file if img_file.is_file() else None,
                    txt_file=txt_file,
                ))

        return records

    def _meta(self) -> dict[str, str]:
        if not self.db_path.exists():
            return {}
        with sqlite3.connect(self.db_path) as conn:
            return {k: v for k, v in conn.execute("SELECT key, value FROM rag_meta")}

    def _embed(
        self,
        texts: list[str],
        batch_size: int,
        report: ProgressCallback,
        lo: float,
        hi: float,
        label: str = "captions",
        dim: int = 0,
    ) -> np.ndarray:
        """CLIP text embeddings in batches (CLIP truncates each text to 77 tokens); (0, dim) for no texts."""
        total = len(texts)
        if total == 0:
            return np.zeros((0, dim), dtype=np.float32)
        parts: list[np.ndarray] = []
        for i in range(0, total, batch_size):
            parts.append(self.classifier.embed_texts(texts[i:i + batch_size]))
            done = min(total, i + batch_size)
            report(lo + (hi - lo) * done / total, f"Embedding {label} {done}/{total}")
        return np.concatenate(parts, axis=0).astype(np.float32)

    def _embed_segments(
        self, owned: list[tuple[int, dict[str, Any]]], batch_size: int, report: ProgressCallback,
        lo: float, hi: float, dim: int,
    ) -> tuple[list[int], np.ndarray, np.ndarray]:
        """R1: (owner per row, domain index per row, embeddings) of the records' domain chunks."""
        owners: list[int] = []
        domains: list[int] = []
        texts: list[str] = []
        for owner, record in owned:
            for d, text in domain_segments(record):
                owners.append(owner)
                domains.append(d)
                texts.append(text)
        embeddings = self._embed(texts, batch_size, report, lo, hi, label="domain segments", dim=dim)
        return owners, np.asarray(domains, dtype=np.int8), embeddings

    def _write_npz_tmp(
        self,
        ids: np.ndarray,
        embeddings: np.ndarray,
        segments: tuple[np.ndarray, np.ndarray, np.ndarray],
    ) -> Path:
        """Write the matrices next to the live file; ``os.replace`` it into place after the SQLite commit."""
        seg_ids, seg_domains, seg_embeddings = segments
        tmp_path = self.npz_path.with_name(self.npz_path.stem + ".tmp.npz")
        np.savez_compressed(
            tmp_path,
            ids=np.asarray(ids, dtype=np.int64),
            embeddings=np.asarray(embeddings, dtype=np.float32),
            seg_ids=np.asarray(seg_ids, dtype=np.int64),
            seg_domains=np.asarray(seg_domains, dtype=np.int8),
            seg_embeddings=np.asarray(seg_embeddings, dtype=np.float32),
            domain_chunk_words=np.int64(DOMAIN_CHUNK_WORDS),
        )
        return tmp_path

    # ------------------------------------------------------------------ R3 image embeddings
    def image_index_enabled(self) -> bool:
        """Image embeddings are built when an embedder exists and ``rag_image_index`` (or the override) is on."""
        if self.image_embedder is None:
            return False
        flag = self.image_index if self.image_index is not None else read_user_setting(self.settings, "rag_image_index")
        return bool(flag)

    def _image_file(self, record: dict[str, Any]) -> Path | None:
        rel = record.get("img_rel_path")
        return self.settings.project_root / rel if rel else None  # absolute paths stay absolute

    def _has_image(self, record: dict[str, Any]) -> bool:
        path = self._image_file(record)
        return bool(path and path.is_file())

    def _resolve_image_embedder(self, keep_name: str | None = None) -> Any:
        """The embedder to use: for an incremental update the index's own embedder when it still loads."""
        emb = self.image_embedder
        if isinstance(emb, AutoImageEmbedder):
            if keep_name:
                try:
                    return emb.for_name(keep_name)
                except Exception as err:
                    logger.warning("Image embedder %s of the RAG image index unavailable: %s", keep_name, err)
            return emb.resolve()
        return emb

    def _embed_images(
        self, owned: list[tuple[int, dict[str, Any]]], embedder: Any, report: ProgressCallback, lo: float, hi: float,
    ) -> tuple[list[int], np.ndarray, np.ndarray, list[str]]:
        """(owners, embeddings, valid mask, image paths); missing/undecodable images get valid=False, a zero row."""
        total = len(owned)
        rows: list[np.ndarray | None] = [None] * total
        for start in range(0, total, IMAGE_BATCH):
            batch_idx: list[int] = []
            images: list[np.ndarray] = []
            for j in range(start, min(total, start + IMAGE_BATCH)):
                path = self._image_file(owned[j][1])
                img = load_image_bgr(path) if path is not None else None
                if img is not None:
                    batch_idx.append(j)
                    images.append(img)
            if images:
                for j, vec in zip(batch_idx, embedder.embed_images(images)):
                    rows[j] = np.asarray(vec, dtype=np.float32)
            done = min(total, start + IMAGE_BATCH)
            report(lo + (hi - lo) * done / max(1, total), f"Embedding images {done}/{total}")
        dim = next((len(v) for v in rows if v is not None), 0)
        embeddings = np.zeros((total, dim), dtype=np.float32)
        valid = np.zeros(total, dtype=bool)
        for j, vec in enumerate(rows):
            if vec is not None:
                embeddings[j] = vec
                valid[j] = True
        return [o for o, _ in owned], embeddings, valid, [r.get("img_rel_path") or "" for _, r in owned]

    def _write_image_tmp(
        self, ids: np.ndarray, embeddings: np.ndarray, valid: np.ndarray, paths: list[str],
        embedder: str, model: str, built_at: str,
    ) -> Path:
        tmp_path = self.image_path.with_name(self.image_path.stem + ".tmp.npz")
        np.savez_compressed(
            tmp_path,
            ids=np.asarray(ids, dtype=np.int64),
            embeddings=np.asarray(embeddings, dtype=np.float32),
            valid=np.asarray(valid, dtype=bool),
            paths=np.asarray(paths, dtype=str),
            embedder=np.asarray(embedder),
            model=np.asarray(model),
            built_at=np.asarray(built_at),
        )
        return tmp_path

    def _image_stage(
        self, owned: list[tuple[int, dict[str, Any]]], report: ProgressCallback, lo: float, hi: float,
        keep_name: str | None = None,
    ) -> tuple[dict[str, Any], tuple | None]:
        """Embed the images of ``owned``; returns (summary, (owners, emb, valid, paths, name, model) or None)."""
        info: dict[str, Any] = {"enabled": True, "embedder": keep_name, "count": 0, "missing": 0, "error": None}
        if not owned:
            return info, ([], np.zeros((0, 0), np.float32), np.zeros(0, bool), [], keep_name or "", "")
        try:
            if any(self._has_image(r) for _, r in owned):
                embedder = self._resolve_image_embedder(keep_name)
                name, model = embedder.name, embedder.model_id
                owners, emb, valid, paths = self._embed_images(owned, embedder, report, lo, hi)
            else:  # nothing to embed: do not load (or download) a model for it
                name = keep_name or getattr(self.image_embedder, "name", "")
                model = ""
                owners, paths = [o for o, _ in owned], [r.get("img_rel_path") or "" for _, r in owned]
                emb, valid = np.zeros((len(owned), 0), np.float32), np.zeros(len(owned), bool)
        except Exception as err:  # the text index must not fail over the image index
            logger.exception("RAG image embeddings failed")
            info["error"] = f"{type(err).__name__}: {err}"
            return info, None
        info.update(embedder=name, count=int(valid.sum()), missing=int((~valid).sum()))
        return info, (owners, emb, valid, paths, name, model)

    @staticmethod
    def _insert(cursor: sqlite3.Cursor, records: list[dict[str, Any]], now: float) -> list[int]:
        sql = (
            f"INSERT INTO rag_captions ({', '.join(_RECORD_COLUMNS)}) "
            f"VALUES ({', '.join('?' for _ in _RECORD_COLUMNS)})"
        )
        ids: list[int] = []
        for r in records:
            cursor.execute(sql, _record_values(r, now))
            ids.append(int(cursor.lastrowid))
        return ids

    def build_index(
        self,
        limit: int | None = None,
        batch_size: int = 64,
        progress_cb: ProgressCallback | None = None,
        incremental: bool = False,
    ) -> dict[str, Any]:
        """Extracts domains, computes CLIP text embeddings (whole caption + R1 domain chunks) and R3 image
        embeddings, and replaces SQLite + NPZ atomically.

        ``incremental=True`` delegates to ``update_index`` (only new/changed captions are embedded).
        An existing index is left untouched when no captions are found.
        """
        if incremental:
            return self.update_index(batch_size=batch_size, progress_cb=progress_cb)
        start_t = time.time()

        def report(frac: float, step: str) -> None:
            if progress_cb:
                progress_cb(frac, step)

        sources = resolve_caption_sources(self.settings)
        report(0.02, f"Scanning captions ({sources['source'] or 'no source found'})")
        records = self._collect(sources)
        if limit and limit > 0:
            records = records[:limit]

        total = len(records)
        if total == 0:
            return {
                "mode": "full",
                "indexed": 0,
                "count": 0,
                "source": sources["source"],
                "source_path": sources["path"],
                "searched": sources.get("searched", []),
                "duration": round(time.time() - start_t, 2),
            }

        image_enabled = self.image_index_enabled()
        embeddings_matrix = self._embed(
            [r["full_caption"] for r in records], batch_size, report, 0.05, 0.4 if image_enabled else 0.5
        )
        # R1: every domain chunk separately (each under CLIP's 77-token context)
        seg_owners, seg_domains, seg_embeddings = self._embed_segments(
            list(enumerate(records)), batch_size, report, 0.4 if image_enabled else 0.5,
            0.7 if image_enabled else 0.9, dim=embeddings_matrix.shape[1],
        )
        # R3: image embeddings (failures are reported, the text index is still written)
        image_info: dict[str, Any] = {"enabled": image_enabled}
        image_data = None
        if image_enabled:
            image_info, image_data = self._image_stage(list(enumerate(records)), report, 0.7, 0.9)

        # Persist to SQLite in one transaction (readers keep seeing the old rows until commit)
        report(0.92, "Writing index")
        now = time.time()
        img_tmp: Path | None = None
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM rag_captions")
            record_ids = self._insert(conn.cursor(), records, now)
            meta = {
                "source": sources["source"] or "",
                "source_path": sources["path"] or "",
                "built_at": str(now),
                "updated_at": str(now),
                "count": str(total),
                "embedding_model": getattr(self.classifier, "repo", "unknown"),
            }
            conn.executemany("INSERT OR REPLACE INTO rag_meta (key, value) VALUES (?, ?)", list(meta.items()))
            # BM25 lexical index in the same transaction (skipped when sqlite lacks FTS5)
            fts_ready = build_fts(conn, meta["built_at"])
            # NPZ goes to a temp file first: a failed write rolls the SQLite transaction back
            id_arr = np.array(record_ids, dtype=np.int64)
            seg_ids = id_arr[np.asarray(seg_owners, dtype=np.int64)] if seg_owners else np.zeros(0, np.int64)
            tmp_path = self._write_npz_tmp(id_arr, embeddings_matrix, (seg_ids, seg_domains, seg_embeddings))
            if image_data is not None:
                owners, img_emb, valid, paths, name, model = image_data
                img_tmp = self._write_image_tmp(
                    id_arr[np.asarray(owners, dtype=np.int64)], img_emb, valid, paths, name, model, meta["built_at"]
                )
            conn.commit()
        os.replace(tmp_path, self.npz_path)  # readers never see a partial matrix
        if img_tmp is not None:
            os.replace(img_tmp, self.image_path)
        else:  # disabled or failed: an image index of the previous build would only hold stale ids
            self.image_path.unlink(missing_ok=True)
        report(1.0, f"Indexed {total} captions")

        return {
            "mode": "full",
            "indexed": total,
            "count": total,
            "source": sources["source"],
            "source_path": sources["path"],
            "embeddings_shape": list(embeddings_matrix.shape),
            "domain_segments": int(len(seg_ids)),
            "image_index": image_info,
            "fts": fts_ready,
            "db_path": str(self.db_path),
            "npz_path": str(self.npz_path),
            "duration": round(time.time() - start_t, 2),
        }

    def _incremental_blocker(self, sources: dict[str, Any]) -> str | None:
        """Why an incremental update is not possible (then a full build runs instead); None when it is."""
        if not self.db_path.exists() or not self.npz_path.exists():
            return "no index yet"
        meta = self._meta()
        if not meta.get("built_at"):
            return "index has no build metadata"
        if (meta.get("source") or None) != sources["source"]:
            return f"caption source changed ({meta.get('source') or 'none'} -> {sources['source'] or 'none'})"
        if meta.get("embedding_model") != getattr(self.classifier, "repo", "unknown"):
            return "embedding model changed"
        with sqlite3.connect(self.db_path) as conn:
            if conn.execute("SELECT 1 FROM rag_captions WHERE content_hash IS NULL LIMIT 1").fetchone():
                return "index predates incremental updates"
        return None

    def update_index(self, batch_size: int = 64, progress_cb: ProgressCallback | None = None) -> dict[str, Any]:
        """R6 incremental update: embed only captions that are new or whose text changed, drop vanished ones.

        Captions are matched by ``record_key`` (frame id, else file stem) and ``content_hash``. Metadata-only
        changes (stem, image path, split) are updated in place. The SQLite rows, FTS5 index and the NPZ matrix
        are swapped in one transaction + atomic rename, like a full build. Falls back to ``build_index`` when
        there is no compatible index (missing, other source, other embedding model, pre-R6 rows).
        R1 domain vectors are embedded for new/changed rows and backfilled for rows that lack them (an index
        built before R1); R3 image vectors for new rows, rows whose image path changed and rows missing from
        the image index (all of them when the image embedder changed).
        """
        start_t = time.time()

        def report(frac: float, step: str) -> None:
            if progress_cb:
                progress_cb(frac, step)

        sources = resolve_caption_sources(self.settings)
        blocker = self._incremental_blocker(sources) if sources["source"] else None
        if blocker:
            logger.info("RAG incremental update runs a full build instead: %s", blocker)
            result = self.build_index(batch_size=batch_size, progress_cb=progress_cb)
            result["full_reason"] = blocker
            return result

        report(0.02, f"Scanning captions ({sources['source'] or 'no source found'}, incremental)")
        records = self._collect(sources) if sources["source"] else []
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            existing_rows = conn.execute(
                f"SELECT id, content_hash, {', '.join(_META_COLUMNS)}, frame_id FROM rag_captions"
            ).fetchall()
        existing = {record_key(dict(r)): dict(r) for r in existing_rows}

        base = {
            "mode": "incremental",
            "source": sources["source"],
            "source_path": sources["path"],
            "db_path": str(self.db_path),
            "npz_path": str(self.npz_path),
        }
        if not records:  # like a full build: an empty source never wipes the index
            return {**base, "indexed": 0, "added": 0, "updated": 0, "removed": 0, "count": len(existing),
                    "searched": sources.get("searched", []), "duration": round(time.time() - start_t, 2)}

        current = {record_key(r): r for r in records}
        added = [r for k, r in current.items() if k not in existing]
        changed = [r for k, r in current.items() if k in existing and existing[k]["content_hash"] != r["content_hash"]]
        relabelled = [
            (existing[k]["id"], r) for k, r in current.items()
            if k in existing and existing[k]["content_hash"] == r["content_hash"]
            and any(existing[k][c] != r[c] for c in _META_COLUMNS)
        ]
        removed_ids = [row["id"] for k, row in existing.items() if k not in current]
        stale_ids = removed_ids + [existing[record_key(r)]["id"] for r in changed]
        to_embed = added + changed
        summary = {"added": len(added), "updated": len(changed), "relabelled": len(relabelled), "removed": len(removed_ids)}

        kept = {
            existing[k]["id"]: r for k, r in current.items()
            if k in existing and existing[k]["content_hash"] == r["content_hash"]
        }
        with np.load(self.npz_path) as npz:
            old_ids = npz["ids"].astype(np.int64)
            old_embeddings = npz["embeddings"].astype(np.float32)
            has_segments = "seg_ids" in npz.files
            old_seg_ids = npz["seg_ids"].astype(np.int64) if has_segments else np.zeros(0, np.int64)
            old_seg_domains = npz["seg_domains"].astype(np.int8) if has_segments else np.zeros(0, np.int8)
            old_seg_emb = (
                npz["seg_embeddings"].astype(np.float32) if has_segments
                else np.zeros((0, old_embeddings.shape[1]), np.float32)
            )
        dim = old_embeddings.shape[1] if old_embeddings.ndim == 2 else 0
        # R1: kept rows without domain vectors (index built before R1) are backfilled
        seg_known = set(old_seg_ids.tolist())
        seg_backfill = [(row_id, r) for row_id, r in kept.items() if row_id not in seg_known]

        # R3: rows whose image is new, moved, or not yet in the image index
        image_enabled = self.image_index_enabled()
        old_img = load_image_index(self.image_path) if image_enabled else None
        old_img_pos = {int(i): p for p, i in enumerate(old_img["ids"])} if old_img else {}
        img_need = [
            (row_id, r) for row_id, r in kept.items()
            if row_id not in old_img_pos or old_img["paths"][old_img_pos[row_id]] != (r.get("img_rel_path") or "")
        ] if image_enabled else []
        if (old_img and not isinstance(self.image_embedder, AutoImageEmbedder)
                and getattr(self.image_embedder, "name", None) != old_img["embedder"]):
            img_need = list(kept.items())  # another embedder: its vectors cannot mix with the old ones

        if not to_embed and not stale_ids and not relabelled and not seg_backfill and not img_need:
            report(1.0, "RAG index already up to date")
            return {**base, **summary, "indexed": 0, "count": len(existing), "duration": round(time.time() - start_t, 2)}

        # Owners: existing row id (>= 1) or -(j + 1) for to_embed[j] (its id is known after the insert)
        new_owned = [(-(j + 1), r) for j, r in enumerate(to_embed)]
        new_embeddings = self._embed(
            [r["full_caption"] for r in to_embed], batch_size, report, 0.05, 0.4, dim=dim
        )
        seg_owners, seg_domains, seg_embeddings = self._embed_segments(
            seg_backfill + new_owned, batch_size, report, 0.4, 0.7, dim=dim
        )
        image_info: dict[str, Any] = {"enabled": image_enabled}
        image_data = None
        if image_enabled:
            keep_name = old_img["embedder"] if old_img else None
            image_info, image_data = self._image_stage(img_need + new_owned, report, 0.7, 0.88, keep_name=keep_name)
            if image_data is not None and old_img and image_data[4] != old_img["embedder"] and image_data[4]:
                # the index's embedder is gone (or replaced): re-embed every image with the current one
                logger.info("RAG image embedder changed (%s -> %s): re-embedding all images",
                            old_img["embedder"], image_data[4])
                if len(img_need) < len(kept):
                    image_info, image_data = self._image_stage(
                        list(kept.items()) + new_owned, report, 0.7, 0.88, keep_name=image_data[4]
                    )
                old_img = None

        report(0.9, "Writing index")
        now = time.time()
        img_tmp: Path | None = None
        # Stale rows are deleted before the re-insert (stem is UNIQUE)
        stale_set = set(stale_ids)
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany("DELETE FROM rag_captions WHERE id=?", [(i,) for i in stale_ids])
            for row_id, r in relabelled:
                conn.execute(
                    f"UPDATE rag_captions SET {', '.join(f'{c}=?' for c in _META_COLUMNS)} WHERE id=?",
                    (*(r[c] for c in _META_COLUMNS), row_id),
                )
            new_ids = self._insert(conn.cursor(), to_embed, now)

            def owner_ids(owners: list[int]) -> np.ndarray:
                return np.array([o if o > 0 else new_ids[-o - 1] for o in owners], dtype=np.int64)

            stale_arr = np.array(sorted(stale_set), dtype=np.int64)
            keep = ~np.isin(old_ids, stale_arr)
            ids = np.concatenate([old_ids[keep], np.array(new_ids, dtype=np.int64)])
            embeddings = np.concatenate([old_embeddings[keep], new_embeddings], axis=0)
            seg_keep = ~np.isin(old_seg_ids, stale_arr)
            all_seg_ids = np.concatenate([old_seg_ids[seg_keep], owner_ids(seg_owners)])
            all_seg_domains = np.concatenate([old_seg_domains[seg_keep], seg_domains])
            all_seg_emb = np.concatenate([old_seg_emb[seg_keep], seg_embeddings], axis=0)
            count = int(conn.execute("SELECT COUNT(*) FROM rag_captions").fetchone()[0])
            updates = {"built_at": str(now), "updated_at": str(now), "count": str(count)}
            conn.executemany("INSERT OR REPLACE INTO rag_meta (key, value) VALUES (?, ?)", list(updates.items()))
            fts_ready = build_fts(conn, updates["built_at"])
            tmp_path = self._write_npz_tmp(ids, embeddings.astype(np.float32), (all_seg_ids, all_seg_domains, all_seg_emb))
            if image_data is not None:
                owners, img_emb, valid, paths, name, model = image_data
                new_img_ids = owner_ids(owners)
                if old_img:  # keep the old rows of live captions that were not re-embedded
                    redone = set(new_img_ids.tolist())
                    live = set(kept)
                    keep_pos = [p for p, i in enumerate(old_img["ids"]) if int(i) in live and int(i) not in redone]
                    img_dim = old_img["embeddings"].shape[1] if img_emb.shape[1] == 0 else img_emb.shape[1]
                    if old_img["embeddings"].shape[1] in (0, img_dim):
                        img_emb = img_emb if img_emb.shape[1] else np.zeros((len(owners), img_dim), np.float32)
                        old_emb = old_img["embeddings"][keep_pos]
                        if old_emb.shape[1] != img_dim:  # no valid old vector yet
                            old_emb = np.zeros((len(keep_pos), img_dim), np.float32)
                        new_img_ids = np.concatenate([old_img["ids"][keep_pos], new_img_ids])
                        img_emb = np.concatenate([old_emb, img_emb], axis=0)
                        valid = np.concatenate([old_img["valid"][keep_pos], valid])
                        paths = [old_img["paths"][p] for p in keep_pos] + paths
                        model = model or old_img["model"]
                        name = name or old_img["embedder"]
                img_tmp = self._write_image_tmp(new_img_ids, img_emb, valid, paths, name, model, updates["built_at"])
                image_info.update(embedder=name, count=int(np.asarray(valid).sum()),
                                  missing=int((~np.asarray(valid, dtype=bool)).sum()))
            conn.commit()
        os.replace(tmp_path, self.npz_path)
        if img_tmp is not None:
            os.replace(img_tmp, self.image_path)
        report(1.0, f"Indexed {len(to_embed)} new/changed captions ({count} total)")

        return {
            **base,
            **summary,
            "indexed": len(to_embed),
            "count": count,
            "embeddings_shape": list(embeddings.shape),
            "domain_segments": int(len(all_seg_ids)),
            "domain_backfilled": len(seg_backfill),
            "image_index": image_info,
            "fts": fts_ready,
            "duration": round(time.time() - start_t, 2),
        }
