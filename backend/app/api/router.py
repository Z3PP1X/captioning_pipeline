"""Master API Router implementing all Step 1..5 REST, SSE, and WebSocket endpoints (Section 5)."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
from typing import Any

import cv2
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response, StreamingResponse
import numpy as np

from backend.app.api.events import EventBus, get_event_bus
from backend.app.config import AppSettings, get_app_settings
from backend.app.db.session import DatabaseManager, get_db
from backend.app.models.api_schemas import (
    AutotagRequest,
    BatchTagRequest,
    BatchTriageRequest,
    DatasetCaptionJobRequest,
    DatasetClusterRequest,
    DatasetCreateRequest,
    DatasetExportRequest,
    DatasetIngestRequest,
    ManualCaptionUpdateRequest,
    OpenRouterTestRequest,
    PromptExpandRequest,
    RagGenerateRequest,
    RagIndexRequest,
    RagRetrieveRequest,
    SettingsUpdateRequest,
    SkillCreateRequest,
    StratifyRequest,
    SwapRepresentativeRequest,
)
from backend.app.models.domain import (
    ExtractionConfig,
    MediaItemRecord,
    QualityScoreResult,
    QualityWeights,
    Tag,
)
from backend.app.modules.captioning.cascade import OpenRouterCascadeEngine
from backend.app.modules.captioning.skills import apply_trigger_word
from backend.app.modules.curation.rule_tagger import RuleBasedHeuristicPlugin
from backend.app.modules.curation.stratifier import RuleBasedStratifier
from backend.app.modules.export_rag.exporter import DatasetExporter
from backend.app.modules.export_rag.prompt_studio import LoRAPromptStudio
from backend.app.modules.export_rag.rag_engine import LocalRagEngine
from backend.app.modules.ingestion.deduplicator import cluster_media_items
from backend.app.modules.ingestion.extractor import (
    SUPPORTED_IMAGE_EXTENSIONS,
    extract_video_pipeline,
    generate_webp_thumbnail,
    ingest_image_folder,
)

SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"}

router = APIRouter()


def _resolve_source_path(raw_path: str, app_settings: AppSettings) -> Path:
    p = Path(raw_path)
    if p.exists():
        return p.resolve()
    rel_in_input = app_settings.input_dir / raw_path.lstrip("/")
    if rel_in_input.exists():
        return rel_in_input.resolve()
    rel_in_proj = app_settings.project_root / raw_path.lstrip("/")
    if rel_in_proj.exists():
        return rel_in_proj.resolve()
    return p


def _run_dataset_ingestion(
    db: DatabaseManager,
    app_settings: AppSettings,
    event_bus: EventBus,
    dataset: dict[str, Any],
    ext_cfg: ExtractionConfig,
    job_id: int,
) -> dict[str, Any]:
    ds_id = dataset["id"]
    src_type = dataset["source_type"]
    src_path = _resolve_source_path(dataset["source_path"], app_settings)
    if not src_path.exists():
        db.update_dataset(ds_id, status="error")
        raise HTTPException(status_code=400, detail=f"Source path does not exist: {src_path}")

    db.update_dataset(ds_id, status="ingesting")
    event_bus.publish(
        "job_started",
        {"job_id": job_id, "dataset_id": ds_id, "step": f"Ingesting {src_path.name}"},
    )

    raw_weights = db.get_setting("quality_weights", {})
    weights = QualityWeights(**raw_weights) if isinstance(raw_weights, dict) else QualityWeights()

    thumb_dir = app_settings.thumbnails_dir / ds_id
    frames_dir = app_settings.frames_dir / ds_id

    if src_type == "video" or src_path.is_file():
        items, clusters, vid_meta, stats = extract_video_pipeline(
            src_path,
            output_frames_dir=frames_dir,
            thumbnails_dir=thumb_dir,
            config=ext_cfg,
            weights=weights,
        )
        vid_meta_dict = vid_meta.to_dict()
    else:
        items, clusters, stats = ingest_image_folder(
            src_path,
            thumbnails_dir=thumb_dir,
            thumbnail_dim=ext_cfg.thumbnail_dim,
            weights=weights,
            dedup_hamming_threshold=ext_cfg.dedup_hamming_threshold,
            dedup_dhash_threshold=ext_cfg.dedup_dhash_threshold,
            dedup_hsv_min_cosine=ext_cfg.dedup_hsv_min_cosine,
        )
        vid_meta_dict = {}

    db.replace_dataset_media_and_clusters(
        ds_id,
        items=items,
        clusters=clusters,
        stats=stats,
        video_metadata=vid_meta_dict,
    )
    event_bus.publish(
        "job_finished",
        {
            "job_id": job_id,
            "dataset_id": ds_id,
            "progress": 1.0,
            "step": f"Ingested {len(items)} items into {len(clusters)} clusters",
        },
    )
    return stats


# ============================================================================
# 5.1 System & Configuration Endpoints
# ============================================================================
@router.get("/health")
def get_health(
    db: DatabaseManager = Depends(get_db),
    app_settings: AppSettings = Depends(get_app_settings),
) -> dict[str, Any]:
    wal_ok, fts_ok = db.check_wal_and_fts5()
    ffmpeg_ok = shutil.which("ffmpeg") is not None
    return {
        "status": "ok",
        "sqlite_wal": wal_ok,
        "ffmpeg": ffmpeg_ok,
        "fts5": fts_ok,
        "mounts": {
            "input": app_settings.input_dir.exists(),
            "exports": app_settings.export_dir.exists(),
            "appdata": app_settings.appdata_dir.exists(),
        },
    }


@router.get("/settings")
def get_settings_endpoint(db: DatabaseManager = Depends(get_db)) -> dict[str, Any]:
    return {"settings": db.get_all_settings(redact_secrets=True)}


@router.put("/settings")
def update_settings_endpoint(
    payload: SettingsUpdateRequest,
    db: DatabaseManager = Depends(get_db),
) -> dict[str, Any]:
    ignored_keys: list[str] = []
    dump = payload.model_dump(exclude_unset=True)
    for key, val in dump.items():
        if val is None:
            continue
        if key == "openrouter_api_key":
            # Empty API key preserves existing key
            if not str(val).strip():
                ignored_keys.append("openrouter_api_key")
                continue
            db.set_setting("openrouter_api_key", str(val).strip(), is_secret=True)
        else:
            db.set_setting(key, val, is_secret=False)

    return {
        "settings": db.get_all_settings(redact_secrets=True),
        "ignored_keys": ignored_keys,
    }


@router.post("/settings/test-openrouter")
async def test_openrouter_endpoint(
    payload: OpenRouterTestRequest,
    db: DatabaseManager = Depends(get_db),
) -> dict[str, Any]:
    engine = OpenRouterCascadeEngine(db=db)
    return await engine.test_api_key(api_key_override=payload.api_key)


# ============================================================================
# 5.2 Step 1: Ingestion, Extraction & Deduplication
# ============================================================================
@router.get("/fs/browse")
def browse_filesystem(
    subpath: str = Query(default=""),
    app_settings: AppSettings = Depends(get_app_settings),
) -> dict[str, Any]:
    base_dir = app_settings.input_dir.resolve()
    base_dir.mkdir(parents=True, exist_ok=True)
    clean_sub = subpath.strip().lstrip("/")
    target = (base_dir / clean_sub).resolve() if clean_sub else base_dir
    if not str(target).startswith(str(base_dir)):
        raise HTTPException(status_code=400, detail="Path traversal outside input root is not permitted")
    if not target.exists() or not target.is_dir():
        raise HTTPException(status_code=404, detail=f"Directory not found: {subpath}")

    directories: list[str] = []
    videos: list[str] = []
    image_counts = 0

    for entry in sorted(target.iterdir()):
        if entry.name.startswith("."):
            continue
        if entry.is_dir():
            directories.append(entry.name)
        elif entry.is_file():
            suf = entry.suffix.lower()
            if suf in SUPPORTED_IMAGE_EXTENSIONS:
                image_counts += 1
            elif suf in SUPPORTED_VIDEO_EXTENSIONS:
                videos.append(entry.name)

    return {
        "current_path": str(target),
        "directories": directories,
        "videos": videos,
        "image_counts": image_counts,
    }


@router.get("/datasets")
def list_datasets_endpoint(
    status: str | None = Query(default=None),
    search: str | None = Query(default=None),
    db: DatabaseManager = Depends(get_db),
) -> dict[str, Any]:
    return {"datasets": db.list_datasets(status=status, search=search)}


@router.get("/datasets/{dataset_id}")
def get_dataset_endpoint(
    dataset_id: str,
    db: DatabaseManager = Depends(get_db),
) -> dict[str, Any]:
    ds = db.get_dataset(dataset_id)
    if ds is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' not found")
    return {"dataset": ds}


@router.post("/datasets")
def create_dataset_endpoint(
    payload: DatasetCreateRequest,
    db: DatabaseManager = Depends(get_db),
    app_settings: AppSettings = Depends(get_app_settings),
    event_bus: EventBus = Depends(get_event_bus),
) -> dict[str, Any]:
    ds = db.create_dataset(
        name=payload.name,
        source_type=payload.source_type,
        source_path=payload.source_path,
        trigger_word=payload.trigger_word,
        trigger_position=payload.trigger_position,
        active_skill_id=payload.active_skill_id,
        target_count=payload.target_count,
        dedup_hamming_threshold=payload.extraction_config.dedup_hamming_threshold,
    )
    job_id = event_bus.next_job_id()
    ingest_stats: dict[str, Any] = {}
    if payload.auto_ingest:
        src_p = _resolve_source_path(payload.source_path, app_settings)
        if src_p.exists():
            ingest_stats = _run_dataset_ingestion(
                db=db,
                app_settings=app_settings,
                event_bus=event_bus,
                dataset=ds,
                ext_cfg=payload.extraction_config,
                job_id=job_id,
            )
            ds = db.get_dataset(ds["id"]) or ds

    return {
        "dataset": ds,
        "job_id": job_id,
        "status": ds.get("status", "queued"),
        "ingest_stats": ingest_stats,
    }


@router.post("/datasets/{dataset_id}/ingest")
def ingest_dataset_endpoint(
    dataset_id: str,
    payload: DatasetIngestRequest,
    db: DatabaseManager = Depends(get_db),
    app_settings: AppSettings = Depends(get_app_settings),
    event_bus: EventBus = Depends(get_event_bus),
) -> dict[str, Any]:
    ds = db.get_dataset(dataset_id)
    if ds is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' not found")
    job_id = event_bus.next_job_id()
    cfg = ExtractionConfig(
        mode=payload.mode,
        scene_threshold=payload.scene_threshold,
        interval_s=payload.interval_s,
        window_peak=payload.window_peak,
        target_candidates=payload.target_candidates,
        dedup_hamming_threshold=payload.dedup_hamming_threshold,
    )
    stats = _run_dataset_ingestion(db, app_settings, event_bus, ds, cfg, job_id)
    return {
        "job_id": job_id,
        "status": "completed",
        "stats": stats,
        "dataset": db.get_dataset(ds["id"]),
    }


@router.post("/datasets/{dataset_id}/cluster")
def recluster_dataset_endpoint(
    dataset_id: str,
    payload: DatasetClusterRequest,
    db: DatabaseManager = Depends(get_db),
) -> dict[str, Any]:
    ds = db.get_dataset(dataset_id)
    if ds is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' not found")
    ds_id = ds["id"]

    rows = db.all("SELECT * FROM media_items WHERE dataset_id = ? ORDER BY id ASC", (ds_id,))
    if not rows:
        return {
            "clusters_count": 0,
            "duplicates_collapsed": 0,
            "representatives_kept": 0,
        }

    records: list[MediaItemRecord] = []
    for r in rows:
        passed = r["curation_state"] != "auto_rejected"
        q_res = QualityScoreResult(
            sharpness_laplacian=float(r.get("sharpness_laplacian") or 0.0),
            sharpness_laplacian_global=float(r.get("sharpness_laplacian") or 0.0),
            sharpness_tenengrad=float(r.get("sharpness_tenengrad") or 0.0),
            sharpness_score=float(r.get("sharpness_score") or 0.0),
            noise_sigma=0.0,
            exposure_mean_luma=float(r.get("exposure_mean_luma") or 128.0),
            shadow_clipping_frac=0.0,
            highlight_clipping_frac=0.0,
            exposure_clipping_frac=float(r.get("exposure_clipping_frac") or 0.0),
            exposure_score=float(r.get("exposure_score") or 0.0),
            entropy_shannon=float(r.get("entropy_shannon") or 0.0),
            entropy_score=float(r.get("entropy_score") or 0.0),
            contrast_rms=float(r.get("contrast_rms") or 0.0),
            contrast_score=0.5,
            border_black_frac=0.0,
            quality_score=float(r.get("quality_score") or 0.0),
            passed_gates=passed,
            reject_reasons=[r["reject_reason"]] if r.get("reject_reason") else [],
            primary_reject_reason=r.get("reject_reason"),
            curation_state=r["curation_state"],
        )
        records.append(
            MediaItemRecord(
                item_id=int(r["id"]),
                source_filename=r["source_filename"],
                source_path=r["source_path"],
                extracted_path=r.get("extracted_path"),
                thumbnail_path=r.get("thumbnail_path"),
                timestamp_s=r.get("timestamp_s"),
                scene_index=int(r.get("scene_index") or 0),
                frame_extract_type=r.get("frame_extract_type") or "image",
                width=int(r.get("width") or 0),
                height=int(r.get("height") or 0),
                aspect_ratio=float(r.get("aspect_ratio") or 1.0),
                phash=str(r.get("phash") or "0" * 16),
                dhash=str(r.get("dhash") or "0" * 16),
                hsv_hist=r.get("hsv_hist") or b"",
                quality=q_res,
                curation_state=r["curation_state"],
            )
        )

    clusters, assignments, stats = cluster_media_items(
        records,
        phash_threshold=payload.hamming_threshold,
        dhash_threshold=payload.dhash_threshold,
        hsv_min_cosine=payload.hsv_min_cosine,
        temporal_window_pass=(ds["source_type"] == "video"),
    )

    with db.write() as conn:
        conn.execute("DELETE FROM clusters WHERE dataset_id = ?", (ds_id,))
        cl_map: dict[int, int] = {}
        for cl in clusters:
            cur = conn.execute(
                """
                INSERT INTO clusters (
                    dataset_id, representative_media_id, centroid_phash, centroid_dhash,
                    member_count, max_quality_score, scene_index, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, strftime('%s','now'))
                """,
                (
                    ds_id,
                    cl.representative_item_id,
                    cl.centroid_phash,
                    cl.centroid_dhash,
                    cl.member_count,
                    cl.max_quality_score,
                    cl.scene_index,
                ),
            )
            cl_map[cl.cluster_id] = int(cur.lastrowid)

        for assign in assignments:
            db_cl_id = cl_map.get(assign.cluster_id)
            if payload.auto_select_representatives:
                conn.execute(
                    """
                    UPDATE media_items
                    SET cluster_id = ?,
                        is_cluster_representative = ?,
                        curation_state = ?
                    WHERE id = ? AND dataset_id = ?
                    """,
                    (
                        db_cl_id,
                        1 if assign.is_cluster_representative else 0,
                        assign.curation_state,
                        assign.item_id,
                        ds_id,
                    ),
                )
            else:
                conn.execute(
                    "UPDATE media_items SET cluster_id = ?, is_cluster_representative = ? WHERE id = ? AND dataset_id = ?",
                    (db_cl_id, 1 if assign.is_cluster_representative else 0, assign.item_id, ds_id),
                )

    db.update_dataset(ds_id, dedup_hamming_threshold=payload.hamming_threshold)
    return {
        "clusters_count": stats.get("clusters_count", len(clusters)),
        "duplicates_collapsed": stats.get("duplicates_collapsed", 0),
        "representatives_kept": stats.get("representatives_kept", len(clusters)),
    }


# ============================================================================
# 5.3 Step 2: HITL Curation, Triage & Stratification
# ============================================================================
@router.get("/datasets/{dataset_id}/items")
def list_dataset_items_endpoint(
    dataset_id: str,
    curation_state: str | None = Query(default=None),
    cluster_id: int | None = Query(default=None),
    min_quality: float = Query(default=0.0),
    tag_slug: str | None = Query(default=None),
    caption_status: str | None = Query(default=None),
    include_duplicates: bool = Query(default=True),
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    db: DatabaseManager = Depends(get_db),
) -> dict[str, Any]:
    ds = db.get_dataset(dataset_id)
    if ds is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' not found")
    items, total = db.list_media_items(
        ds["id"],
        curation_state=curation_state,
        cluster_id=cluster_id,
        min_quality=min_quality,
        tag_slug=tag_slug,
        caption_status=caption_status,
        include_duplicates=include_duplicates,
        limit=limit,
        offset=offset,
    )
    return {"items": items, "total": total}


@router.post("/datasets/{dataset_id}/items/batch-triage")
def batch_triage_endpoint(
    dataset_id: str,
    payload: BatchTriageRequest,
    db: DatabaseManager = Depends(get_db),
) -> dict[str, Any]:
    ds = db.get_dataset(dataset_id)
    if ds is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' not found")
    updated = db.batch_update_triage(
        ds["id"],
        item_ids=payload.item_ids,
        curation_state=payload.curation_state,
        crop_geometry=payload.crop_geometry,
    )
    return {"updated": updated, "curation_state": payload.curation_state}


@router.post("/datasets/{dataset_id}/clusters/{cluster_id}/representative")
def swap_cluster_representative_endpoint(
    dataset_id: str,
    cluster_id: int,
    payload: SwapRepresentativeRequest,
    db: DatabaseManager = Depends(get_db),
) -> dict[str, Any]:
    ds = db.get_dataset(dataset_id)
    if ds is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' not found")
    try:
        return db.swap_cluster_representative(
            ds["id"],
            cluster_id=cluster_id,
            new_representative_id=payload.new_representative_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/datasets/{dataset_id}/stratification")
def get_stratification_endpoint(
    dataset_id: str,
    db: DatabaseManager = Depends(get_db),
) -> dict[str, Any]:
    ds = db.get_dataset(dataset_id)
    if ds is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' not found")
    items, _ = db.list_media_items(ds["id"], include_duplicates=False, limit=10000)
    tags_map = db.get_tags_for_media_items([int(i["id"]) for i in items])

    stratifier = RuleBasedStratifier()
    dist = stratifier.compute_distribution(items, tags_map)
    gaps = stratifier.detect_outliers_and_gaps(dist, target_size=int(ds.get("target_count") or 150))
    return {**dist, "recommendations": gaps.get("recommendations", [])}


@router.post("/datasets/{dataset_id}/stratify")
def apply_stratification_endpoint(
    dataset_id: str,
    payload: StratifyRequest,
    db: DatabaseManager = Depends(get_db),
) -> dict[str, Any]:
    ds = db.get_dataset(dataset_id)
    if ds is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' not found")
    ds_id = ds["id"]
    items, _ = db.list_media_items(ds_id, include_duplicates=False, limit=10000)
    tags_map = db.get_tags_for_media_items([int(i["id"]) for i in items])

    stratifier = RuleBasedStratifier()
    res = stratifier.stratify_selection(
        items,
        tags_by_item=tags_map,
        target_count=payload.target_count,
        diversity_weight=payload.diversity_weight,
    )
    keep_ids = res.get("recommended_keep_ids", [])
    demote_ids = res.get("recommended_demote_ids", [])
    outlier_ids = res.get("outlier_ids", [])

    if keep_ids:
        db.batch_update_triage(ds_id, keep_ids, curation_state="keep")
    if demote_ids:
        db.batch_update_triage(ds_id, demote_ids, curation_state="candidate")
    if outlier_ids:
        with db.write() as conn:
            placeholders = ", ".join("?" for _ in outlier_ids)
            conn.execute(
                f"UPDATE media_items SET is_outlier = 1 WHERE dataset_id = ? AND id IN ({placeholders})",
                tuple([ds_id, *(int(i) for i in outlier_ids)]),
            )

    db.update_dataset(ds_id, target_count=payload.target_count)
    return {
        "kept_count": len(keep_ids),
        "promoted_ids": keep_ids,
        "demoted_ids": demote_ids,
        "outlier_ids": outlier_ids,
        "distribution": res.get("distribution", {}),
    }


@router.post("/datasets/{dataset_id}/autotag")
def autotag_dataset_endpoint(
    dataset_id: str,
    payload: AutotagRequest,
    db: DatabaseManager = Depends(get_db),
) -> dict[str, Any]:
    ds = db.get_dataset(dataset_id)
    if ds is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' not found")
    items, _ = db.list_media_items(ds["id"], include_duplicates=False, limit=10000)

    plugin = RuleBasedHeuristicPlugin()
    assignments: dict[int, list[Tag]] = {}
    items_tagged = 0

    for item in items:
        mid = int(item["id"])
        img_bgr = None
        for key in ("extracted_path", "source_path", "thumbnail_path"):
            p_str = item.get(key)
            if p_str and Path(p_str).is_file():
                raw = np.fromfile(str(p_str), dtype=np.uint8)
                if raw.size > 0:
                    img_bgr = cv2.imdecode(raw, cv2.IMREAD_COLOR)
                    if img_bgr is not None and img_bgr.size > 0:
                        break
        if img_bgr is None:
            continue
        tags = plugin.analyze_item(img_bgr, metadata=item)
        filtered = [t for t in tags if t.confidence >= payload.min_confidence]
        if filtered:
            assignments[mid] = filtered
            items_tagged += 1

    tags_assigned = db.assign_tags_to_media_batch(assignments, min_confidence=payload.min_confidence)
    return {
        "items_tagged": items_tagged,
        "tags_assigned": tags_assigned,
        "plugin_id": plugin.plugin_id,
    }


@router.post("/datasets/{dataset_id}/items/batch-tag")
def batch_tag_endpoint(
    dataset_id: str,
    payload: BatchTagRequest,
    db: DatabaseManager = Depends(get_db),
) -> dict[str, Any]:
    ds = db.get_dataset(dataset_id)
    if ds is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' not found")
    updated = db.batch_modify_tags(
        item_ids=payload.item_ids,
        add_tags=payload.add_tags,
        remove_tag_ids=payload.remove_tag_ids,
        remove_tag_slugs=payload.remove_tag_slugs,
    )
    return {"updated_items": updated}


# ============================================================================
# 5.4 Step 3: OpenRouter Captioning, Skills & Rejection-Guard
# ============================================================================
@router.get("/skills")
def list_skills_endpoint(db: DatabaseManager = Depends(get_db)) -> dict[str, Any]:
    return {"skills": db.list_skills()}


@router.post("/skills")
def create_skill_endpoint(
    payload: SkillCreateRequest,
    db: DatabaseManager = Depends(get_db),
) -> dict[str, Any]:
    skill = db.create_or_version_skill(
        slug=payload.slug,
        name=payload.name,
        training_type=payload.training_type,
        system_prompt_template=payload.system_prompt_template,
        user_prompt_template=payload.user_prompt_template,
        glossary_template=payload.glossary_template,
        min_words=payload.min_words,
        max_words=payload.max_words,
        discouraged_regexes=payload.discouraged_regexes,
        require_class_noun=payload.require_class_noun,
        is_builtin=False,
    )
    return {"skill": skill}


@router.post("/datasets/{dataset_id}/caption")
async def caption_dataset_endpoint(
    dataset_id: str,
    payload: DatasetCaptionJobRequest,
    db: DatabaseManager = Depends(get_db),
    event_bus: EventBus = Depends(get_event_bus),
) -> dict[str, Any]:
    ds = db.get_dataset(dataset_id)
    if ds is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' not found")
    ds_id = ds["id"]
    job_id = event_bus.next_job_id()

    engine = OpenRouterCascadeEngine(db=db)
    summary = await engine.caption_dataset(
        ds_id,
        skill_id=payload.skill_id,
        trigger_word=payload.trigger_word,
        trigger_position=payload.trigger_position,
        class_noun=payload.class_noun,
        primary_model=payload.primary_model,
        fallback_model_1=payload.fallback_model_1,
        fallback_model_2=payload.fallback_model_2,
        max_tokens=payload.max_tokens,
        temperature=payload.temperature,
        concurrency=payload.concurrency,
        only_uncaptioned=payload.only_uncaptioned,
        job_id=job_id,
        event_cb=event_bus.publish,
    )
    return {
        "job_id": job_id,
        "status": "completed",
        "items_to_caption": summary["total"],
        "summary": summary,
    }


@router.put("/items/{media_id}/caption")
def update_manual_caption_endpoint(
    media_id: int,
    payload: ManualCaptionUpdateRequest,
    db: DatabaseManager = Depends(get_db),
) -> dict[str, Any]:
    item = db.get_media_item(media_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Media item {media_id} not found")
    ds = db.get_dataset(item["dataset_id"]) or {}

    trig = (
        payload.trigger_word
        if payload.trigger_word is not None
        else ds.get("trigger_word") or "ohwx_subject"
    )
    pos_raw = (
        payload.trigger_position
        or ds.get("trigger_position")
        or "prefix"
    )
    pos = pos_raw if pos_raw in ("prefix", "suffix", "natural_integration") else "prefix"

    text = payload.caption_text.strip()
    warnings: list[str] = []
    if payload.enforce_trigger and trig:
        text, warnings = apply_trigger_word(text, trigger_word=trig, position=pos)

    return db.record_caption(
        media_id=media_id,
        dataset_id=item["dataset_id"],
        caption_text=text,
        source="manual",
        caption_status="manual",
        model_used="manual",
        cascade_layer="manual",
        trigger_word=trig,
        trigger_position=pos,
        warnings=warnings,
    )


@router.get("/datasets/{dataset_id}/rejections")
def list_dataset_rejections_endpoint(
    dataset_id: str,
    media_id: int | None = Query(default=None),
    layer: str | None = Query(default=None),
    db: DatabaseManager = Depends(get_db),
) -> dict[str, Any]:
    ds = db.get_dataset(dataset_id)
    if ds is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' not found")
    return db.list_rejection_logs(ds["id"], media_id=media_id, layer=layer)


# ============================================================================
# 5.5 Step 4: Dataset Export
# ============================================================================
@router.post("/datasets/{dataset_id}/export")
def export_dataset_endpoint(
    dataset_id: str,
    payload: DatasetExportRequest,
    db: DatabaseManager = Depends(get_db),
) -> dict[str, Any]:
    ds = db.get_dataset(dataset_id)
    if ds is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' not found")
    exporter = DatasetExporter(db=db)
    return exporter.export_dataset(
        ds["id"],
        image_format=payload.image_format,
        max_edge=payload.max_edge,
        snap_multiple_16=payload.snap_multiple_16,
        trigger_word=payload.trigger_word,
        trigger_position=payload.trigger_position,
        create_zip=payload.create_zip,
    )


@router.get("/datasets/{dataset_id}/export/download")
def download_dataset_export_zip(
    dataset_id: str,
    db: DatabaseManager = Depends(get_db),
) -> FileResponse:
    exporter = DatasetExporter(db=db)
    try:
        zip_path = exporter.get_export_zip_path(dataset_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(
        path=str(zip_path),
        media_type="application/zip",
        filename=zip_path.name,
    )


# ============================================================================
# 5.6 Step 5: Local LoRA-RAG & Prompt Studio
# ============================================================================
@router.post("/rag/index")
async def build_rag_index_endpoint(
    payload: RagIndexRequest,
    db: DatabaseManager = Depends(get_db),
) -> dict[str, Any]:
    engine = LocalRagEngine(db=db)
    return await engine.build_index(
        dataset_id=payload.dataset_id,
        dense_backend=payload.dense_backend,
        embedding_model=payload.embedding_model,
    )


@router.post("/rag/retrieve")
async def retrieve_rag_endpoint(
    payload: RagRetrieveRequest,
    db: DatabaseManager = Depends(get_db),
) -> dict[str, Any]:
    engine = LocalRagEngine(db=db)
    results = await engine.retrieve(
        query=payload.query,
        dataset_id=payload.dataset_id,
        trigger_word=payload.trigger_word,
        training_type=payload.training_type,
        tag_filters=payload.tag_filters,
        top_k=payload.top_k,
        diversity=payload.diversity,
        retrieval_mode=payload.retrieval_mode,
        dense_backend=payload.dense_backend,
    )
    return {"results": results}


@router.post("/rag/generate")
async def generate_rag_prompts_endpoint(
    payload: RagGenerateRequest,
    db: DatabaseManager = Depends(get_db),
) -> dict[str, Any]:
    studio = LoRAPromptStudio(db=db)
    return await studio.generate(
        query=payload.query,
        selected_tags=payload.selected_tags,
        dataset_id=payload.dataset_id,
        trigger_word=payload.trigger_word,
        style_trigger=payload.style_trigger,
        character=payload.character,
        model=payload.model,
        top_k=payload.top_k,
        n_variations=payload.n_variations,
    )


@router.post("/rag/expand-prompt")
async def expand_prompt_endpoint(
    payload: PromptExpandRequest,
    db: DatabaseManager = Depends(get_db),
) -> dict[str, Any]:
    studio = LoRAPromptStudio(db=db)
    res = await studio.generate(
        query=payload.idea,
        selected_tags={},
        dataset_id=payload.dataset_id,
        trigger_word=payload.trigger_word,
        style_trigger=payload.style_trigger,
        character=payload.class_noun,
        model=payload.model,
        top_k=payload.top_k,
        n_variations=1,
    )
    archs = res.get("archetypes", {})
    arch_a = archs.get("a", {}).get("prompt", "")
    arch_b = archs.get("b", {}).get("prompt", "")
    arch_c = archs.get("c", {}).get("prompt", "")

    return {
        "long": arch_c or f"{payload.trigger_word} Detailed visual composition showing {payload.idea}.",
        "medium": arch_b or f"{payload.trigger_word} Photograph of {payload.class_noun}, {payload.idea}.",
        "short": arch_a or f"{payload.trigger_word} {payload.class_noun}, {payload.idea}.",
        "exemplars": res.get("rag_context", []),
    }



# ============================================================================
# Media Preview Serving Endpoints
# ============================================================================
@router.get("/media/{media_id}/thumbnail")
def get_media_thumbnail_endpoint(
    media_id: int,
    db: DatabaseManager = Depends(get_db),
    app_settings: AppSettings = Depends(get_app_settings),
) -> Response:
    item = db.get_media_item(media_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Media item {media_id} not found")

    t_path = item.get("thumbnail_path")
    if t_path and Path(t_path).is_file():
        return FileResponse(str(t_path), media_type="image/webp")

    for key in ("extracted_path", "source_path"):
        p_str = item.get(key)
        if p_str and Path(p_str).is_file():
            raw = np.fromfile(str(p_str), dtype=np.uint8)
            img = cv2.imdecode(raw, cv2.IMREAD_COLOR)
            if img is not None:
                out_file = app_settings.thumbnails_dir / f"thumb_{media_id}.webp"
                generate_webp_thumbnail(img, out_file, max_dim=384)
                return FileResponse(str(out_file), media_type="image/webp")

    raise HTTPException(status_code=404, detail="Media thumbnail file not found on disk")


@router.get("/media/{media_id}/full")
def get_media_full_endpoint(
    media_id: int,
    db: DatabaseManager = Depends(get_db),
) -> FileResponse:
    item = db.get_media_item(media_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Media item {media_id} not found")
    for key in ("extracted_path", "source_path", "thumbnail_path"):
        p_str = item.get(key)
        if p_str and Path(p_str).is_file():
            return FileResponse(str(p_str))
    raise HTTPException(status_code=404, detail="Media source file not found on disk")


# ============================================================================
# 5.7 Real-Time Telemetry Stream (SSE & WebSocket)
# ============================================================================
@router.get("/events/stream")
async def sse_events_stream(
    max_events: int | None = Query(default=None),
    event_bus: EventBus = Depends(get_event_bus),
) -> StreamingResponse:
    return StreamingResponse(
        event_bus.sse_stream(heartbeat_interval=15.0, max_events=max_events),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.websocket("/ws")
async def websocket_telemetry_endpoint(
    websocket: WebSocket,
    event_bus: EventBus = Depends(get_event_bus),
) -> None:
    await websocket.accept()
    await websocket.send_text(
        json.dumps({"type": "connected", "data": {"status": "ws_connected"}})
    )
    try:
        while True:
            msg = await websocket.receive_text()
            if msg.strip().lower() == "ping":
                await websocket.send_text(json.dumps({"type": "pong", "data": {}}))
    except WebSocketDisconnect:
        return
