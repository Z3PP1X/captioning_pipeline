"""Thread-safe SQLite WAL connection pool, serialized write lock manager, and domain repository methods."""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid
from typing import Any, Iterator

from backend.app.config import AppSettings, get_app_settings
from backend.app.models.domain import ClusterRecord, MediaItemRecord, Tag

SCHEMA_SQL_PATH = Path(__file__).resolve().parent / "schema.sql"


def slugify_text(text: str) -> str:
    """Create a URL/filesystem-safe slug from arbitrary text."""
    slug = re.sub(r"[^a-z0-9]+", "_", (text or "").strip().lower()).strip("_")
    return slug or "item"


def mask_api_key(key: str | None) -> str:
    """Mask an API key for safe display, e.g. 'sk-or-v1-...9a2f'."""
    if not key or not key.strip():
        return ""
    s = key.strip()
    if len(s) <= 10:
        return "***"
    return f"{s[:9]}...{s[-4:]}"


class DatabaseManager:
    """Thread-safe SQLite WAL manager with serialized writes and rich domain query helpers."""

    def __init__(self, db_path: Path | str | None = None, auto_seed: bool = True) -> None:
        settings = get_app_settings()
        self.db_path = Path(db_path) if db_path is not None else settings.resolved_db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.RLock()
        self._init_schema()
        if auto_seed:
            from backend.app.db.seed import seed_database
            seed_database(self)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.execute("PRAGMA temp_store = MEMORY;")
        conn.execute("PRAGMA cache_size = -64000;")
        return conn

    def _init_schema(self) -> None:
        ddl = SCHEMA_SQL_PATH.read_text(encoding="utf-8")
        with self._write_lock:
            conn = self._connect()
            try:
                conn.executescript(ddl)
                existing_cols = {
                    str(r["name"])
                    for r in conn.execute("PRAGMA table_info(media_items);").fetchall()
                }
                for col in ("caption_medium", "caption_short"):
                    if col not in existing_cols:
                        conn.execute(f"ALTER TABLE media_items ADD COLUMN {col} TEXT;")
                conn.commit()
            finally:
                conn.close()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        with self._write_lock:
            conn = self._connect()
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def all(self, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> list[dict[str, Any]]:
        with self.read() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
            return [dict(r) for r in rows]

    def one(self, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> dict[str, Any] | None:
        with self.read() as conn:
            row = conn.execute(sql, tuple(params)).fetchone()
            return dict(row) if row is not None else None

    def scalar(self, sql: str, params: tuple[Any, ...] | list[Any] = (), default: Any = None) -> Any:
        with self.read() as conn:
            row = conn.execute(sql, tuple(params)).fetchone()
            return row[0] if row is not None and row[0] is not None else default

    def check_wal_and_fts5(self) -> tuple[bool, bool]:
        """Return (wal_active, fts5_available)."""
        try:
            with self.read() as conn:
                mode = str(conn.execute("PRAGMA journal_mode;").fetchone()[0]).lower()
                fts_ok = (
                    conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE name='rag_fts'"
                    ).fetchone()
                    is not None
                )
                return (mode == "wal", fts_ok)
        except Exception:
            return (False, False)

    # ------------------------------------------------------------------
    # System Settings
    # ------------------------------------------------------------------
    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value FROM system_settings WHERE key = ?", (key,))
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except Exception:
            return row["value"]

    def set_setting(
        self,
        key: str,
        value: Any,
        *,
        is_secret: bool | None = None,
        description: str | None = None,
    ) -> None:
        now = time.time()
        serialized = json.dumps(value)
        with self.write() as conn:
            existing = conn.execute(
                "SELECT is_secret, description FROM system_settings WHERE key = ?",
                (key,),
            ).fetchone()
            sec_flag = (
                int(bool(is_secret))
                if is_secret is not None
                else (int(existing["is_secret"]) if existing else int(key == "openrouter_api_key"))
            )
            desc_val = description if description is not None else (existing["description"] if existing else "")
            conn.execute(
                """
                INSERT INTO system_settings (key, value, is_secret, description, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    is_secret = excluded.is_secret,
                    description = excluded.description,
                    updated_at = excluded.updated_at
                """,
                (key, serialized, sec_flag, desc_val, now),
            )

    def resolve_openrouter_api_key(self, override_key: str | None = None) -> str:
        """Resolve OpenRouter API key: override -> system_settings -> env."""
        if override_key and override_key.strip():
            return override_key.strip()
        stored = self.get_setting("openrouter_api_key", "")
        if isinstance(stored, str) and stored.strip():
            return stored.strip()
        return get_app_settings().openrouter_api_key

    def get_all_settings(self, redact_secrets: bool = True) -> dict[str, Any]:
        rows = self.all("SELECT key, value, is_secret FROM system_settings ORDER BY key ASC")
        out: dict[str, Any] = {}
        for r in rows:
            k = r["key"]
            try:
                val = json.loads(r["value"])
            except Exception:
                val = r["value"]
            if redact_secrets and int(r["is_secret"]) == 1:
                raw_str = str(val or "").strip() or get_app_settings().openrouter_api_key
                out[f"{k}_set"] = bool(raw_str)
                out[f"{k}_masked"] = mask_api_key(raw_str)
            else:
                out[k] = val
        if redact_secrets and "openrouter_api_key_set" not in out:
            raw_env = get_app_settings().openrouter_api_key
            out["openrouter_api_key_set"] = bool(raw_env)
            out["openrouter_api_key_masked"] = mask_api_key(raw_env)
        return out

    # ------------------------------------------------------------------
    # Datasets
    # ------------------------------------------------------------------
    def create_dataset(
        self,
        *,
        name: str,
        source_type: str,
        source_path: str,
        trigger_word: str = "ohwx_subject",
        trigger_position: str = "prefix",
        active_skill_id: str | None = "krea2_concept_v1",
        target_count: int = 150,
        dedup_hamming_threshold: int = 6,
        video_metadata: dict[str, Any] | None = None,
        dataset_id: str | None = None,
    ) -> dict[str, Any]:
        now = time.time()
        base_slug = slugify_text(name)
        ds_id = dataset_id or f"ds_{uuid.uuid4().hex[:10]}"
        slug = base_slug
        with self.write() as conn:
            suffix = 1
            while conn.execute("SELECT 1 FROM datasets WHERE slug = ?", (slug,)).fetchone():
                suffix += 1
                slug = f"{base_slug}_{suffix}"

            # Check active_skill_id exists
            if active_skill_id:
                skill_exists = conn.execute(
                    "SELECT 1 FROM skills WHERE id = ?", (active_skill_id,)
                ).fetchone()
                if not skill_exists:
                    active_skill_id = None

            conn.execute(
                """
                INSERT INTO datasets (
                    id, name, slug, source_type, source_path, status,
                    trigger_word, trigger_position, active_skill_id,
                    target_count, dedup_hamming_threshold, video_metadata,
                    stats_cache, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'created', ?, ?, ?, ?, ?, ?, '{}', ?, ?)
                """,
                (
                    ds_id,
                    name.strip() or slug,
                    slug,
                    source_type,
                    source_path,
                    trigger_word.strip() or "ohwx_subject",
                    trigger_position if trigger_position in ("prefix", "suffix", "natural_integration") else "prefix",
                    active_skill_id,
                    max(1, int(target_count)),
                    max(0, int(dedup_hamming_threshold)),
                    json.dumps(video_metadata or {}),
                    now,
                    now,
                ),
            )
        return self.get_dataset(ds_id) or {}

    def get_dataset(self, dataset_id: str) -> dict[str, Any] | None:
        row = self.one("SELECT * FROM datasets WHERE id = ? OR slug = ?", (dataset_id, dataset_id))
        if row is None:
            return None
        return self._enrich_dataset_row(row)

    def _enrich_dataset_row(self, row: dict[str, Any]) -> dict[str, Any]:
        ds_id = row["id"]
        counts = self.one(
            """
            SELECT
                COUNT(*) AS total_items,
                SUM(CASE WHEN curation_state IN ('keep', 'star') THEN 1 ELSE 0 END) AS kept_items,
                SUM(CASE WHEN curation_state = 'reject' OR curation_state = 'auto_rejected' THEN 1 ELSE 0 END) AS rejected_items,
                SUM(CASE WHEN curation_state = 'duplicate_hidden' THEN 1 ELSE 0 END) AS duplicate_items,
                SUM(CASE WHEN caption_status IN ('vlm_accepted', 'manual', 'approved') THEN 1 ELSE 0 END) AS captioned_items,
                SUM(CASE WHEN caption_status = 'needs_manual_caption' THEN 1 ELSE 0 END) AS needs_manual_items
            FROM media_items
            WHERE dataset_id = ?
            """,
            (ds_id,),
        ) or {}
        try:
            vid_meta = json.loads(row.get("video_metadata") or "{}")
        except Exception:
            vid_meta = {}
        try:
            stats_cache = json.loads(row.get("stats_cache") or "{}")
        except Exception:
            stats_cache = {}
        out = dict(row)
        out["video_metadata"] = vid_meta
        out["stats_cache"] = stats_cache
        out["total_items"] = int(counts.get("total_items") or 0)
        out["kept_items"] = int(counts.get("kept_items") or 0)
        out["rejected_items"] = int(counts.get("rejected_items") or 0)
        out["duplicate_items"] = int(counts.get("duplicate_items") or 0)
        out["captioned_items"] = int(counts.get("captioned_items") or 0)
        out["needs_manual_items"] = int(counts.get("needs_manual_items") or 0)
        return out

    def list_datasets(self, status: str | None = None, search: str | None = None) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status and status.strip():
            clauses.append("status = ?")
            params.append(status.strip())
        if search and search.strip():
            clauses.append("(name LIKE ? OR slug LIKE ?)")
            q = f"%{search.strip()}%"
            params.extend([q, q])
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.all(f"SELECT * FROM datasets {where_sql} ORDER BY created_at DESC", params)
        return [self._enrich_dataset_row(r) for r in rows]

    def update_dataset(self, dataset_id: str, **fields: Any) -> dict[str, Any] | None:
        if not fields:
            return self.get_dataset(dataset_id)
        allowed = {
            "name",
            "status",
            "trigger_word",
            "trigger_position",
            "active_skill_id",
            "target_count",
            "dedup_hamming_threshold",
            "video_metadata",
            "stats_cache",
        }
        updates: list[str] = []
        params: list[Any] = []
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k in ("video_metadata", "stats_cache") and isinstance(v, dict):
                v = json.dumps(v)
            updates.append(f"{k} = ?")
            params.append(v)
        if not updates:
            return self.get_dataset(dataset_id)
        updates.append("updated_at = ?")
        params.append(time.time())
        params.append(dataset_id)
        with self.write() as conn:
            conn.execute(f"UPDATE datasets SET {', '.join(updates)} WHERE id = ?", tuple(params))
        return self.get_dataset(dataset_id)

    # ------------------------------------------------------------------
    # Clusters & Media Items
    # ------------------------------------------------------------------
    def replace_dataset_media_and_clusters(
        self,
        dataset_id: str,
        items: list[MediaItemRecord],
        clusters: list[ClusterRecord],
        stats: dict[str, Any] | None = None,
        video_metadata: dict[str, Any] | None = None,
    ) -> list[int]:
        """Atomically persist ingested media items and their perceptual hash clusters."""
        now = time.time()
        inserted_media_ids: list[int] = []
        with self.write() as conn:
            conn.execute("DELETE FROM clusters WHERE dataset_id = ?", (dataset_id,))
            conn.execute("DELETE FROM media_items WHERE dataset_id = ?", (dataset_id,))

            # Map local cluster_id -> DB cluster.id
            cluster_db_map: dict[int, int] = {}
            for cl in clusters:
                cur = conn.execute(
                    """
                    INSERT INTO clusters (
                        dataset_id, representative_media_id, centroid_phash, centroid_dhash,
                        member_count, max_quality_score, scene_index, created_at
                    ) VALUES (?, NULL, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        dataset_id,
                        cl.centroid_phash,
                        cl.centroid_dhash,
                        cl.member_count,
                        cl.max_quality_score,
                        cl.scene_index,
                        now,
                    ),
                )
                cluster_db_map[cl.cluster_id] = int(cur.lastrowid)

            # Map local item.item_id -> DB media_items.id
            item_db_map: dict[int, int] = {}
            for item in items:
                db_cluster_id = cluster_db_map.get(item.cluster_id) if item.cluster_id is not None else None
                has_existing_cap = bool(item.existing_caption and item.existing_caption.strip())
                cap_text = item.existing_caption.strip() if has_existing_cap and item.existing_caption else None
                cap_status = "imported" if has_existing_cap else "uncaptioned"
                # Note schema CHECK for caption_status: ('uncaptioned', 'queued', 'vlm_accepted', 'needs_manual_caption', 'manual', 'approved')
                if has_existing_cap:
                    cap_status = "approved"

                q = item.quality
                cur = conn.execute(
                    """
                    INSERT INTO media_items (
                        dataset_id, cluster_id, is_cluster_representative,
                        source_filename, source_path, extracted_path, thumbnail_path,
                        timestamp_s, scene_index, frame_extract_type,
                        width, height, aspect_ratio,
                        phash, dhash, hsv_hist,
                        sharpness_laplacian, sharpness_tenengrad, sharpness_score,
                        exposure_mean_luma, exposure_clipping_frac, exposure_score,
                        entropy_shannon, entropy_score, contrast_rms, quality_score,
                        curation_state, reject_reason, crop_geometry, is_outlier,
                        active_caption, caption_status, active_caption_model, active_caption_layer,
                        created_at, updated_at
                    ) VALUES (
                        ?, ?, ?,
                        ?, ?, ?, ?,
                        ?, ?, ?,
                        ?, ?, ?,
                        ?, ?, ?,
                        ?, ?, ?,
                        ?, ?, ?,
                        ?, ?, ?, ?,
                        ?, ?, NULL, 0,
                        ?, ?, ?, ?,
                        ?, ?
                    )
                    """,
                    (
                        dataset_id,
                        db_cluster_id,
                        1 if item.is_cluster_representative else 0,
                        item.source_filename,
                        item.source_path,
                        item.extracted_path,
                        item.thumbnail_path,
                        item.timestamp_s,
                        item.scene_index,
                        item.frame_extract_type,
                        item.width,
                        item.height,
                        item.aspect_ratio,
                        item.phash,
                        item.dhash,
                        item.hsv_hist,
                        q.sharpness_laplacian,
                        q.sharpness_tenengrad,
                        q.sharpness_score,
                        q.exposure_mean_luma,
                        q.exposure_clipping_frac,
                        q.exposure_score,
                        q.entropy_shannon,
                        q.entropy_score,
                        q.contrast_rms,
                        q.quality_score,
                        item.curation_state,
                        q.primary_reject_reason,
                        cap_text,
                        cap_status,
                        "imported" if has_existing_cap else None,
                        "cached" if has_existing_cap else None,
                        now,
                        now,
                    ),
                )
                db_media_id = int(cur.lastrowid)
                inserted_media_ids.append(db_media_id)
                item_db_map[item.item_id] = db_media_id

                if cap_text:
                    conn.execute(
                        """
                        INSERT INTO caption_history (
                            media_id, dataset_id, caption_text, source,
                            cascade_layer, word_count, is_active, created_at
                        ) VALUES (?, ?, ?, 'imported', 'cached', ?, 1, ?)
                        """,
                        (db_media_id, dataset_id, cap_text, len(cap_text.split()), now),
                    )

            # Back-populate representative_media_id on clusters
            for cl in clusters:
                db_cl_id = cluster_db_map.get(cl.cluster_id)
                rep_db_id = item_db_map.get(cl.representative_item_id)
                if db_cl_id is not None and rep_db_id is not None:
                    conn.execute(
                        "UPDATE clusters SET representative_media_id = ? WHERE id = ?",
                        (rep_db_id, db_cl_id),
                    )

            # Update dataset status & metadata
            ds_updates = ["status = 'curating'", "updated_at = ?"]
            ds_params: list[Any] = [now]
            if stats is not None:
                ds_updates.append("stats_cache = ?")
                ds_params.append(json.dumps(stats))
            if video_metadata is not None:
                ds_updates.append("video_metadata = ?")
                ds_params.append(json.dumps(video_metadata))
            ds_params.append(dataset_id)
            conn.execute(
                f"UPDATE datasets SET {', '.join(ds_updates)} WHERE id = ?",
                tuple(ds_params),
            )

        return inserted_media_ids

    def list_media_items(
        self,
        dataset_id: str,
        *,
        curation_state: str | None = None,
        cluster_id: int | None = None,
        min_quality: float = 0.0,
        tag_slug: str | None = None,
        caption_status: str | None = None,
        include_duplicates: bool = True,
        limit: int = 500,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        clauses = ["m.dataset_id = ?"]
        params: list[Any] = [dataset_id]

        if curation_state and curation_state.strip():
            states = [s.strip() for s in curation_state.split(",") if s.strip()]
            if len(states) == 1:
                clauses.append("m.curation_state = ?")
                params.append(states[0])
            elif states:
                placeholders = ", ".join("?" for _ in states)
                clauses.append(f"m.curation_state IN ({placeholders})")
                params.extend(states)
        elif not include_duplicates:
            clauses.append("m.curation_state != 'duplicate_hidden'")

        if cluster_id is not None:
            clauses.append("m.cluster_id = ?")
            params.append(int(cluster_id))

        if min_quality > 0.0:
            clauses.append("m.quality_score >= ?")
            params.append(float(min_quality))

        if caption_status and caption_status.strip():
            clauses.append("m.caption_status = ?")
            params.append(caption_status.strip())

        if tag_slug and tag_slug.strip():
            clauses.append(
                """
                EXISTS (
                    SELECT 1 FROM media_tags mt
                    JOIN tags t ON t.id = mt.tag_id
                    WHERE mt.media_id = m.id AND mt.status != 'rejected' AND t.slug = ?
                )
                """
            )
            params.append(tag_slug.strip())

        where_sql = " AND ".join(clauses)
        total = int(
            self.scalar(f"SELECT COUNT(*) FROM media_items m WHERE {where_sql}", params, default=0)
        )

        rows = self.all(
            f"""
            SELECT m.*, COALESCE(c.member_count, 1) AS cluster_size
            FROM media_items m
            LEFT JOIN clusters c ON c.id = m.cluster_id
            WHERE {where_sql}
            ORDER BY COALESCE(m.timestamp_s, m.id) ASC, m.id ASC
            LIMIT ? OFFSET ?
            """,
            [*params, int(limit), int(offset)],
        )

        media_ids = [int(r["id"]) for r in rows]
        tags_map = self.get_tags_for_media_items(media_ids)

        enriched: list[dict[str, Any]] = []
        for r in rows:
            mid = int(r["id"])
            item_dict = dict(r)
            item_dict.pop("hsv_hist", None)  # binary blob not needed in JSON list
            try:
                item_dict["crop_geometry"] = (
                    json.loads(r["crop_geometry"]) if r.get("crop_geometry") else None
                )
            except Exception:
                item_dict["crop_geometry"] = None
            item_dict["thumbnail_url"] = f"/api/v1/media/{mid}/thumbnail"
            item_dict["full_url"] = f"/api/v1/media/{mid}/full"
            item_dict["tags"] = [t.model_dump() for t in tags_map.get(mid, [])]
            enriched.append(item_dict)

        return enriched, total

    def get_media_item(self, media_id: int) -> dict[str, Any] | None:
        row = self.one("SELECT * FROM media_items WHERE id = ?", (int(media_id),))
        if row is None:
            return None
        tags_map = self.get_tags_for_media_items([int(media_id)])
        out = dict(row)
        try:
            out["crop_geometry"] = json.loads(row["crop_geometry"]) if row.get("crop_geometry") else None
        except Exception:
            out["crop_geometry"] = None
        out["thumbnail_url"] = f"/api/v1/media/{media_id}/thumbnail"
        out["full_url"] = f"/api/v1/media/{media_id}/full"
        out["tags"] = [t.model_dump() for t in tags_map.get(int(media_id), [])]
        return out

    def batch_update_triage(
        self,
        dataset_id: str,
        item_ids: list[int],
        curation_state: str | None = None,
        crop_geometry: dict[str, float] | None = None,
    ) -> int:
        if not item_ids:
            return 0
        now = time.time()
        updates = ["updated_at = ?"]
        params: list[Any] = [now]
        if curation_state is not None:
            updates.append("curation_state = ?")
            params.append(curation_state)
        if crop_geometry is not None:
            updates.append("crop_geometry = ?")
            params.append(json.dumps(crop_geometry))

        placeholders = ", ".join("?" for _ in item_ids)
        params.append(dataset_id)
        params.extend(int(i) for i in item_ids)
        with self.write() as conn:
            cur = conn.execute(
                f"UPDATE media_items SET {', '.join(updates)} WHERE dataset_id = ? AND id IN ({placeholders})",
                tuple(params),
            )
            return int(cur.rowcount)

    def swap_cluster_representative(
        self,
        dataset_id: str,
        cluster_id: int,
        new_representative_id: int,
    ) -> dict[str, Any]:
        now = time.time()
        with self.write() as conn:
            cl = conn.execute(
                "SELECT * FROM clusters WHERE id = ? AND dataset_id = ?",
                (int(cluster_id), dataset_id),
            ).fetchone()
            if cl is None:
                raise ValueError(f"Cluster {cluster_id} not found in dataset {dataset_id}")
            old_rep_id = cl["representative_media_id"]

            member = conn.execute(
                "SELECT id FROM media_items WHERE id = ? AND dataset_id = ? AND cluster_id = ?",
                (int(new_representative_id), dataset_id, int(cluster_id)),
            ).fetchone()
            if member is None:
                raise ValueError(
                    f"Media item {new_representative_id} is not a member of cluster {cluster_id}"
                )

            # Demote current members in cluster
            conn.execute(
                """
                UPDATE media_items
                SET is_cluster_representative = 0,
                    curation_state = CASE WHEN curation_state IN ('keep', 'star') THEN 'duplicate_hidden' ELSE curation_state END,
                    updated_at = ?
                WHERE cluster_id = ? AND dataset_id = ? AND id != ?
                """,
                (now, int(cluster_id), dataset_id, int(new_representative_id)),
            )
            # Promote new representative
            conn.execute(
                """
                UPDATE media_items
                SET is_cluster_representative = 1,
                    curation_state = 'keep',
                    updated_at = ?
                WHERE id = ? AND dataset_id = ?
                """,
                (now, int(new_representative_id), dataset_id),
            )
            conn.execute(
                "UPDATE clusters SET representative_media_id = ? WHERE id = ?",
                (int(new_representative_id), int(cluster_id)),
            )

        return {
            "cluster_id": int(cluster_id),
            "representative_media_id": int(new_representative_id),
            "demoted_media_id": int(old_rep_id) if old_rep_id is not None else None,
        }

    # ------------------------------------------------------------------
    # Tags & Media Tags
    # ------------------------------------------------------------------
    def get_tags_for_media_items(self, media_ids: list[int]) -> dict[int, list[Tag]]:
        if not media_ids:
            return {}
        placeholders = ", ".join("?" for _ in media_ids)
        rows = self.all(
            f"""
            SELECT mt.media_id, mt.source, mt.plugin_id, mt.confidence, mt.bbox,
                   t.id AS tag_id, t.name, t.slug, t.category
            FROM media_tags mt
            JOIN tags t ON t.id = mt.tag_id
            WHERE mt.media_id IN ({placeholders}) AND mt.status != 'rejected'
            ORDER BY mt.confidence DESC, t.name ASC
            """,
            [int(i) for i in media_ids],
        )
        out: dict[int, list[Tag]] = {int(i): [] for i in media_ids}
        for r in rows:
            bbox_val = None
            if r.get("bbox"):
                try:
                    bbox_val = json.loads(r["bbox"])
                except Exception:
                    bbox_val = None
            out.setdefault(int(r["media_id"]), []).append(
                Tag(
                    name=r["name"],
                    slug=r["slug"],
                    source=r["source"],
                    plugin_id=r["plugin_id"] or "manual",
                    category=r["category"],
                    confidence=float(r["confidence"]),
                    bbox=bbox_val,
                )
            )
        return out

    def assign_tags_to_media_batch(
        self,
        assignments: dict[int, list[Tag]],
        min_confidence: float = 0.0,
    ) -> int:
        """Persist Tag lists per media_id into `tags` and `media_tags`. Returns count of tag links written."""
        if not assignments:
            return 0
        now = time.time()
        total_written = 0
        with self.write() as conn:
            slug_to_id: dict[str, int] = {}
            for _, tag_list in assignments.items():
                for tag in tag_list:
                    if tag.confidence < min_confidence:
                        continue
                    slug = slugify_text(tag.slug or tag.name)
                    if slug not in slug_to_id:
                        conn.execute(
                            """
                            INSERT INTO tags (name, slug, category, created_at)
                            VALUES (?, ?, ?, ?)
                            ON CONFLICT(slug) DO UPDATE SET name = excluded.name
                            """,
                            (tag.name, slug, tag.category, now),
                        )
                        row = conn.execute("SELECT id FROM tags WHERE slug = ?", (slug,)).fetchone()
                        if row:
                            slug_to_id[slug] = int(row["id"])

            for media_id, tag_list in assignments.items():
                for tag in tag_list:
                    if tag.confidence < min_confidence:
                        continue
                    slug = slugify_text(tag.slug or tag.name)
                    tag_id = slug_to_id.get(slug)
                    if tag_id is None:
                        continue
                    conn.execute(
                        """
                        INSERT INTO media_tags (
                            media_id, tag_id, source, plugin_id, confidence, status, bbox, updated_at
                        ) VALUES (?, ?, ?, ?, ?, 'confirmed', ?, ?)
                        ON CONFLICT(media_id, tag_id, source) DO UPDATE SET
                            plugin_id = excluded.plugin_id,
                            confidence = excluded.confidence,
                            status = 'confirmed',
                            bbox = excluded.bbox,
                            updated_at = excluded.updated_at
                        """,
                        (
                            int(media_id),
                            tag_id,
                            tag.source,
                            tag.plugin_id,
                            float(tag.confidence),
                            json.dumps(tag.bbox) if tag.bbox else None,
                            now,
                        ),
                    )
                    total_written += 1
        return total_written

    def batch_modify_tags(
        self,
        item_ids: list[int],
        add_tags: list[dict[str, Any]] | None = None,
        remove_tag_ids: list[int] | None = None,
        remove_tag_slugs: list[str] | None = None,
    ) -> int:
        if not item_ids:
            return 0
        now = time.time()
        with self.write() as conn:
            if add_tags:
                for t_spec in add_tags:
                    name = str(t_spec.get("name") or "").strip()
                    if not name:
                        continue
                    slug = slugify_text(str(t_spec.get("slug") or name))
                    cat = str(t_spec.get("category") or "style_element")
                    if cat not in (
                        "pose",
                        "lighting",
                        "outfit",
                        "expression",
                        "style_element",
                        "shot_scale",
                        "environment",
                        "equipment",
                    ):
                        cat = "style_element"
                    conn.execute(
                        """
                        INSERT INTO tags (name, slug, category, created_at)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(slug) DO UPDATE SET name = excluded.name
                        """,
                        (name, slug, cat, now),
                    )
                    t_row = conn.execute("SELECT id FROM tags WHERE slug = ?", (slug,)).fetchone()
                    if not t_row:
                        continue
                    tag_id = int(t_row["id"])
                    for mid in item_ids:
                        conn.execute(
                            """
                            INSERT INTO media_tags (
                                media_id, tag_id, source, plugin_id, confidence, status, updated_at
                            ) VALUES (?, ?, 'manual', 'manual', 1.0, 'confirmed', ?)
                            ON CONFLICT(media_id, tag_id, source) DO UPDATE SET
                                status = 'confirmed',
                                confidence = 1.0,
                                updated_at = excluded.updated_at
                            """,
                            (int(mid), tag_id, now),
                        )

            if remove_tag_ids:
                m_place = ", ".join("?" for _ in item_ids)
                t_place = ", ".join("?" for _ in remove_tag_ids)
                conn.execute(
                    f"DELETE FROM media_tags WHERE media_id IN ({m_place}) AND tag_id IN ({t_place})",
                    tuple([*(int(i) for i in item_ids), *(int(t) for t in remove_tag_ids)]),
                )

            if remove_tag_slugs:
                m_place = ", ".join("?" for _ in item_ids)
                s_place = ", ".join("?" for _ in remove_tag_slugs)
                conn.execute(
                    f"""
                    DELETE FROM media_tags
                    WHERE media_id IN ({m_place})
                      AND tag_id IN (SELECT id FROM tags WHERE slug IN ({s_place}))
                    """,
                    tuple([*(int(i) for i in item_ids), *(str(s) for s in remove_tag_slugs)]),
                )
        return len(item_ids)

    # ------------------------------------------------------------------
    # Skills
    # ------------------------------------------------------------------
    def list_skills(self) -> list[dict[str, Any]]:
        rows = self.all("SELECT * FROM skills ORDER BY is_builtin DESC, slug ASC, version DESC")
        for r in rows:
            try:
                r["discouraged_regexes"] = json.loads(r.get("discouraged_regexes") or "[]")
            except Exception:
                r["discouraged_regexes"] = []
        return rows

    def get_skill(self, skill_id_or_slug: str) -> dict[str, Any] | None:
        row = self.one(
            "SELECT * FROM skills WHERE id = ? OR slug = ? ORDER BY version DESC LIMIT 1",
            (skill_id_or_slug, skill_id_or_slug),
        )
        if row is None:
            return None
        try:
            row["discouraged_regexes"] = json.loads(row.get("discouraged_regexes") or "[]")
        except Exception:
            row["discouraged_regexes"] = []
        return row

    def create_or_version_skill(
        self,
        *,
        slug: str,
        name: str,
        training_type: str,
        system_prompt_template: str,
        user_prompt_template: str,
        glossary_template: str = "",
        min_words: int = 80,
        max_words: int = 160,
        hard_min_words: int | None = None,
        hard_max_words: int | None = None,
        discouraged_regexes: list[str] | None = None,
        require_class_noun: bool = False,
        is_builtin: bool = False,
        skill_id: str | None = None,
    ) -> dict[str, Any]:
        now = time.time()
        clean_slug = slugify_text(slug or name)
        t_type = training_type if training_type in ("character", "concept", "style", "general") else "concept"
        h_min = hard_min_words if hard_min_words is not None else max(15, int(min_words) // 2)
        h_max = hard_max_words if hard_max_words is not None else int(int(max_words) * 1.5)

        with self.write() as conn:
            max_ver_row = conn.execute(
                "SELECT MAX(version) AS max_v FROM skills WHERE slug = ?", (clean_slug,)
            ).fetchone()
            next_version = int(max_ver_row["max_v"] or 0) + 1
            sid = skill_id or f"{clean_slug}_v{next_version}"
            conn.execute(
                """
                INSERT INTO skills (
                    id, slug, name, version, training_type,
                    system_prompt_template, user_prompt_template, glossary_template,
                    min_words, max_words, hard_min_words, hard_max_words,
                    discouraged_regexes, require_class_noun, is_builtin,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    system_prompt_template = excluded.system_prompt_template,
                    user_prompt_template = excluded.user_prompt_template,
                    glossary_template = excluded.glossary_template,
                    min_words = excluded.min_words,
                    max_words = excluded.max_words,
                    hard_min_words = excluded.hard_min_words,
                    hard_max_words = excluded.hard_max_words,
                    discouraged_regexes = excluded.discouraged_regexes,
                    require_class_noun = excluded.require_class_noun,
                    updated_at = excluded.updated_at
                """,
                (
                    sid,
                    clean_slug,
                    name,
                    next_version,
                    t_type,
                    system_prompt_template,
                    user_prompt_template,
                    glossary_template,
                    int(min_words),
                    int(max_words),
                    int(h_min),
                    int(h_max),
                    json.dumps(discouraged_regexes or []),
                    1 if require_class_noun else 0,
                    1 if is_builtin else 0,
                    now,
                    now,
                ),
            )
        return self.get_skill(sid) or {}

    # ------------------------------------------------------------------
    # Captions & Rejection Logs
    # ------------------------------------------------------------------
    def record_caption(
        self,
        *,
        media_id: int,
        dataset_id: str,
        caption_text: str,
        caption_medium: str | None = None,
        caption_short: str | None = None,
        source: str = "vlm",
        caption_status: str = "vlm_accepted",
        skill_id: str | None = None,
        skill_version: int | None = None,
        model_used: str | None = None,
        cascade_layer: str = "primary",
        trigger_word: str | None = None,
        trigger_position: str | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost_usd: float = 0.0,
        warnings: list[str] | None = None,
        job_id: int | None = None,
    ) -> dict[str, Any]:
        now = time.time()
        clean_text = caption_text.strip()
        word_list = clean_text.split()
        words = len(word_list)
        clean_medium = (
            caption_medium.strip()
            if caption_medium is not None
            else (" ".join(word_list[:45]) if word_list else "")
        )
        clean_short = (
            caption_short.strip()
            if caption_short is not None
            else (" ".join(word_list[:14]) if word_list else "")
        )
        with self.write() as conn:
            conn.execute(
                "UPDATE caption_history SET is_active = 0 WHERE media_id = ?",
                (int(media_id),),
            )
            conn.execute(
                """
                INSERT INTO caption_history (
                    media_id, dataset_id, job_id, caption_text, source,
                    skill_id, skill_version, model_used, cascade_layer,
                    trigger_word, trigger_position, word_count,
                    prompt_tokens, completion_tokens, cost_usd,
                    warnings, is_active, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    int(media_id),
                    dataset_id,
                    job_id,
                    clean_text,
                    source,
                    skill_id,
                    skill_version,
                    model_used,
                    cascade_layer,
                    trigger_word,
                    trigger_position,
                    words,
                    int(prompt_tokens),
                    int(completion_tokens),
                    float(cost_usd),
                    json.dumps(warnings or []),
                    now,
                ),
            )
            conn.execute(
                """
                UPDATE media_items
                SET active_caption = ?,
                    caption_medium = ?,
                    caption_short = ?,
                    caption_status = ?,
                    active_caption_model = ?,
                    active_caption_layer = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    clean_text,
                    clean_medium,
                    clean_short,
                    caption_status,
                    model_used,
                    cascade_layer,
                    now,
                    int(media_id),
                ),
            )
        return {
            "media_id": int(media_id),
            "caption_text": clean_text,
            "active_caption": clean_text,
            "caption_medium": clean_medium,
            "caption_short": clean_short,
            "caption_status": caption_status,
            "word_count": words,
            "warnings": warnings or [],
        }

    def approve_media_item(self, media_id: int) -> dict[str, Any] | None:
        now = time.time()
        with self.write() as conn:
            conn.execute(
                "UPDATE media_items SET caption_status = 'approved', updated_at = ? WHERE id = ?",
                (now, int(media_id)),
            )
        return self.get_media_item(media_id)

    def set_media_smart_crop(
        self,
        media_id: int,
        crop_geometry: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        now = time.time()
        serialized = json.dumps(crop_geometry) if crop_geometry is not None else None
        with self.write() as conn:
            conn.execute(
                "UPDATE media_items SET crop_geometry = ?, updated_at = ? WHERE id = ?",
                (serialized, now, int(media_id)),
            )
        return self.get_media_item(media_id)

    def mark_needs_manual_caption(self, media_id: int, last_model: str | None = None) -> None:
        now = time.time()
        with self.write() as conn:
            conn.execute(
                """
                UPDATE media_items
                SET caption_status = 'needs_manual_caption',
                    active_caption_model = ?,
                    active_caption_layer = 'fallback_2',
                    updated_at = ?
                WHERE id = ?
                """,
                (last_model, now, int(media_id)),
            )

    def record_rejection_log(
        self,
        *,
        media_id: int,
        dataset_id: str,
        cascade_layer: str,
        model_attempted: str,
        rejection_type: str,
        rejection_reasons: list[str],
        raw_response_snippet: str = "",
        finish_reason: str | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost_usd: float = 0.0,
        job_id: int | None = None,
    ) -> int:
        now = time.time()
        valid_types = {
            "refusal_regex",
            "provider_safety_filter",
            "too_short",
            "truncated_length",
            "meta_preamble",
            "minor_coded_rule",
            "http_error",
        }
        r_type = rejection_type if rejection_type in valid_types else "refusal_regex"
        layer = cascade_layer if cascade_layer in ("primary", "fallback_1", "fallback_2") else "primary"
        with self.write() as conn:
            cur = conn.execute(
                """
                INSERT INTO rejection_logs (
                    media_id, dataset_id, job_id, cascade_layer, model_attempted,
                    rejection_type, rejection_reasons, raw_response_snippet,
                    finish_reason, prompt_tokens, completion_tokens, cost_usd, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(media_id),
                    dataset_id,
                    job_id,
                    layer,
                    model_attempted,
                    r_type,
                    json.dumps(rejection_reasons),
                    (raw_response_snippet or "")[:500],
                    finish_reason,
                    int(prompt_tokens),
                    int(completion_tokens),
                    float(cost_usd),
                    now,
                ),
            )
            return int(cur.lastrowid)

    def list_rejection_logs(
        self,
        dataset_id: str,
        media_id: int | None = None,
        layer: str | None = None,
    ) -> dict[str, Any]:
        clauses = ["dataset_id = ?"]
        params: list[Any] = [dataset_id]
        if media_id is not None:
            clauses.append("media_id = ?")
            params.append(int(media_id))
        if layer and layer.strip():
            clauses.append("cascade_layer = ?")
            params.append(layer.strip())
        where_sql = " AND ".join(clauses)
        rows = self.all(
            f"SELECT * FROM rejection_logs WHERE {where_sql} ORDER BY created_at DESC, id DESC",
            params,
        )
        for r in rows:
            try:
                r["rejection_reasons"] = json.loads(r.get("rejection_reasons") or "[]")
            except Exception:
                r["rejection_reasons"] = [str(r.get("rejection_reasons") or "")]
        needs_manual = int(
            self.scalar(
                "SELECT COUNT(*) FROM media_items WHERE dataset_id = ? AND caption_status = 'needs_manual_caption'",
                (dataset_id,),
                default=0,
            )
        )
        return {
            "needs_manual_count": needs_manual,
            "logs": rows,
        }


_DEFAULT_DB_LOCK = threading.Lock()
_DEFAULT_DB_INSTANCE: DatabaseManager | None = None


def get_db(db_path: Path | str | None = None) -> DatabaseManager:
    global _DEFAULT_DB_INSTANCE
    if db_path is not None:
        return DatabaseManager(db_path=db_path)
    with _DEFAULT_DB_LOCK:
        if _DEFAULT_DB_INSTANCE is None:
            _DEFAULT_DB_INSTANCE = DatabaseManager()
        return _DEFAULT_DB_INSTANCE


def reset_default_db(db: DatabaseManager | None = None) -> None:
    global _DEFAULT_DB_INSTANCE
    with _DEFAULT_DB_LOCK:
        _DEFAULT_DB_INSTANCE = db
