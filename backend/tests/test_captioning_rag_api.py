"""Comprehensive Unit & Integration Tests for Phase 3 (DB, Module C, Module D, and FastAPI Endpoints)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
import zipfile

import cv2
from fastapi.testclient import TestClient
import numpy as np
import pytest

from backend.app.db.session import DatabaseManager, reset_default_db
from backend.app.main import create_app
from backend.app.modules.captioning.cascade import OpenRouterCascadeEngine, prepare_512px_payload
from backend.app.modules.captioning.rejection_guard import RejectionDetector
from backend.app.modules.captioning.skills import (
    SKILL_KREA2_CHARACTER_V1,
    SKILL_KREA2_CONCEPT_V1,
    SKILL_KREA2_STYLE_V1,
    apply_trigger_word,
    render_skill_prompts,
    verify_trigger_placement,
)
from backend.app.modules.export_rag.exporter import DatasetExporter
from backend.app.modules.export_rag.prompt_studio import LoRAPromptStudio
from backend.app.modules.export_rag.rag_engine import LocalRagEngine, cosine_similarity_vec


def _create_synthetic_images(folder: Path, count: int = 4) -> list[Path]:
    """Create sharp synthetic test images with rich gradients and textures so they pass CV quality gates."""
    folder.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    rng = np.random.default_rng(42)
    for i in range(count):
        h, w = 540, 720
        y_grid, x_grid = np.mgrid[0:h, 0:w]
        base = ((x_grid * 0.25 + y_grid * 0.25 + i * 35) % 200 + 30).astype(np.uint8)
        img = np.stack(
            [
                base,
                np.clip(base.astype(np.int32) + (i * 15 - 20), 20, 235).astype(np.uint8),
                np.clip(255 - base.astype(np.int32), 20, 235).astype(np.uint8),
            ],
            axis=-1,
        )
        # Draw high-frequency geometric patterns in center for strong Laplacian + Tenengrad sharpness
        for k in range(8):
            pt1 = (120 + k * 55, 100 + (i * 20))
            pt2 = (160 + k * 55, 420 - (k * 15))
            color = (int((k * 47 + i * 31) % 230 + 20), 220, 60)
            cv2.rectangle(img, pt1, pt2, color, thickness=2)
            cv2.circle(img, (360, 270), 30 + k * 18, (240, 180, 40), thickness=2)
        noise = rng.integers(-6, 7, size=img.shape, dtype=np.int16)
        img = np.clip(img.astype(np.int16) + noise, 15, 240).astype(np.uint8)
        p = folder / f"sample_{i + 1:02d}.png"
        cv2.imwrite(str(p), img)
        paths.append(p)
    return paths


# ============================================================================
# 1. Database Schema, WAL, FTS5 & Seeder Tests
# ============================================================================
def test_database_wal_fts5_and_seed(tmp_path: Path) -> None:
    db_file = tmp_path / "test_state.db"
    db = DatabaseManager(db_path=db_file, auto_seed=True)

    wal_ok, fts_ok = db.check_wal_and_fts5()
    assert wal_ok is True
    assert fts_ok is True

    settings = db.get_all_settings(redact_secrets=True)
    assert settings["primary_model"] == "google/gemini-2.5-pro"
    assert settings["fallback_model_1"] == "qwen/qwen-2.5-vl-72b-instruct"
    assert settings["fallback_model_2"] == "meta-llama/llama-3.2-90b-vision-instruct"
    assert settings["max_tokens"] == 600
    assert settings["temperature"] == 0.4
    assert settings["trigger_word"] == "ohwx_subject"
    assert settings["trigger_position"] == "prefix"
    assert "openrouter_api_key" not in settings
    assert "openrouter_api_key_set" in settings

    # Verify API key persistence and masking
    db.set_setting("openrouter_api_key", "sk-or-v1-1234567890abcdef9a2f", is_secret=True)
    assert db.resolve_openrouter_api_key() == "sk-or-v1-1234567890abcdef9a2f"
    redacted = db.get_all_settings(redact_secrets=True)
    assert redacted["openrouter_api_key_set"] is True
    assert redacted["openrouter_api_key_masked"] == "sk-or-v1-...9a2f"

    skills = db.list_skills()
    skill_ids = {s["id"] for s in skills}
    assert {"krea2_character_v1", "krea2_style_v1", "krea2_concept_v1"}.issubset(skill_ids)


# ============================================================================
# 2. Skills & Triggerword Positioning Tests
# ============================================================================
def test_triggerword_positioning_and_skill_rendering() -> None:
    raw = "Photograph of an athletic adult woman standing in a sunlit loft wearing a tailored black linen blazer."

    # Prefix (Token 0)
    prefixed, _ = apply_trigger_word(raw, "ohwx_sara", position="prefix")
    assert prefixed.startswith("ohwx_sara Photograph of")
    ok_p, err_p = verify_trigger_placement(prefixed, "ohwx_sara", "prefix")
    assert ok_p is True and err_p is None

    # Suffix
    suffixed, _ = apply_trigger_word(raw, "noir_krea_style", position="suffix")
    assert suffixed.endswith(", noir_krea_style.")
    ok_s, err_s = verify_trigger_placement(suffixed, "noir_krea_style", "suffix")
    assert ok_s is True and err_s is None

    # Natural integration
    nat, _ = apply_trigger_word(raw, "restrained_elegance", position="natural_integration")
    assert "restrained_elegance" in nat
    ok_n, err_n = verify_trigger_placement(nat, "restrained_elegance", "natural_integration")
    assert ok_n is True and err_n is None

    # Skill rendering checks
    char_sys, char_usr = render_skill_prompts(
        SKILL_KREA2_CHARACTER_V1,
        trigger_word="ohwx_sara",
        trigger_position="prefix",
        class_noun="woman",
        tag_hints=["chiaroscuro", "85mm lens"],
    )
    assert "ohwx_sara" in char_sys
    assert "IDENTITY DECOUPLING" in char_sys
    assert "chiaroscuro, 85mm lens" in char_usr

    style_sys, _ = render_skill_prompts(
        SKILL_KREA2_STYLE_V1,
        trigger_word="analog_cinestill",
        trigger_position="suffix",
    )
    assert "STYLE DECOUPLING" in style_sys
    assert "analog_cinestill" in style_sys


# ============================================================================
# 3. Deterministic RejectionDetector Tests
# ============================================================================
def test_rejection_detector_comprehensive() -> None:
    detector = RejectionDetector(min_chars=15, min_words=10, custom_regexes=[r"\bforbidden_custom_word\b"])

    # 1. Empty / Null / Too short
    assert detector.evaluate(None).rejection_type == "too_short"
    assert detector.evaluate("   ").rejection_type == "too_short"
    assert detector.evaluate("Short text.").rejection_type == "too_short"

    # 2. Refusal regexes in first 200 chars (including smart quotes)
    refusals = [
        "I'm sorry, but I can't fulfill this request as it violates safety guidelines.",
        "I’m unable to assist with describing this image due to content policies.",
        "As an AI language model, I cannot describe this photograph.",
        "I apologize, but I am unable to provide a caption for this content.",
        "Sorry but I cannot assist with this image.",
        "This request goes against my safety guidelines.",
    ]
    for r_text in refusals:
        res = detector.evaluate(r_text)
        assert res.accepted is False, f"Expected refusal rejection for: {r_text}"
        assert res.rejection_type == "refusal_regex"

    # 3. Provider metadata safety filter
    res_meta = detector.evaluate(
        "Some text that was cut by safety filter",
        finish_reason="content_filter",
    )
    assert res_meta.accepted is False
    assert res_meta.rejection_type == "provider_safety_filter"

    # 4. Meta-talk / missing image / trailing question
    meta_samples = [
        "Please provide an image so that I can write a detailed description for your dataset.",
        "I don't see an image attached to your message. Could you upload it again?",
        "What would you like me to focus on in this photograph of the studio?",
    ]
    for m_text in meta_samples:
        res_m = detector.evaluate(m_text)
        assert res_m.accepted is False
        assert res_m.rejection_type == "meta_preamble"

    # 5. Truncation detection (finish_reason == 'length' or no terminal punctuation)
    res_trunc1 = detector.evaluate(
        "ohwx_subject Photograph of an adult woman standing in a studio with soft directional lighting and",
        finish_reason="stop",
    )
    assert res_trunc1.accepted is False
    assert res_trunc1.rejection_type == "truncated_length"

    res_trunc2 = detector.evaluate(
        "ohwx_subject Photograph of an adult woman standing in a studio with soft directional lighting.",
        finish_reason="length",
    )
    assert res_trunc2.accepted is False
    assert res_trunc2.rejection_type == "truncated_length"

    # 6. Minor-coded descriptor rejection
    res_minor = detector.evaluate(
        "ohwx_subject Photograph of a teenage schoolgirl standing in a sunlit corridor wearing a uniform."
    )
    assert res_minor.accepted is False
    assert res_minor.rejection_type == "minor_coded_rule"

    # 7. Valid caption with benign preamble stripping, sludge removal, and trigger injection
    valid_raw = (
        "Here is a description: Photograph of a tall girl in her twenties standing upright in an industrial "
        "brick loft, wearing a structured black leather jacket with polished chrome buckles, masterpiece, 8k, "
        "(dramatic lighting:1.3), illuminated by high-contrast chiaroscuro side lighting."
    )
    res_ok = detector.evaluate(
        valid_raw,
        trigger_word="ohwx_sara",
        trigger_position="prefix",
        class_noun="woman",
        require_class_noun=True,
    )
    assert res_ok.accepted is True
    assert res_ok.cleaned_caption.startswith("ohwx_sara")
    assert "masterpiece" not in res_ok.cleaned_caption.lower()
    assert "8k" not in res_ok.cleaned_caption.lower()
    assert "(dramatic lighting:1.3)" not in res_ok.cleaned_caption
    assert "adult woman" in res_ok.cleaned_caption


# ============================================================================
# 4. OpenRouter 3-Tier Cascade Escalation Tests
# ============================================================================
def test_openrouter_3_tier_cascade_escalation(tmp_path: Path) -> None:
    db = DatabaseManager(db_path=tmp_path / "cascade.db", auto_seed=True)
    img_dir = tmp_path / "imgs"
    _create_synthetic_images(img_dir, count=3)

    ds = db.create_dataset(
        name="Cascade Test Dataset",
        source_type="image_folder",
        source_path=str(img_dir),
        trigger_word="ohwx_test",
        trigger_position="prefix",
    )
    from backend.app.modules.ingestion.extractor import ingest_image_folder

    items, clusters, stats = ingest_image_folder(img_dir, thumbnails_dir=tmp_path / "thumbs")
    media_ids = db.replace_dataset_media_and_clusters(ds["id"], items, clusters, stats)
    assert len(media_ids) == 3

    # Test Case 1: Primary refuses -> Fallback 1 truncates -> Fallback 2 succeeds
    call_log: list[tuple[str, str]] = []

    def responder_escalate_to_tier3(model: str, layer: str, _payload: dict[str, Any]) -> dict[str, Any]:
        call_log.append((layer, model))
        if layer == "primary":
            return {
                "choices": [
                    {
                        "message": {"content": "I'm sorry, but I cannot fulfill this request due to safety policy."},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 15, "cost": 0.001},
            }
        if layer == "fallback_1":
            return {
                "choices": [
                    {
                        "message": {
                            "content": "ohwx_test Photograph of an adult woman standing in a studio with directional light and"
                        },
                        "finish_reason": "length",
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "cost": 0.001},
            }
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            "ohwx_test Photograph of an athletic adult woman standing in a minimalist concrete "
                            "studio, wearing a dark tailored coat with silver clasps, illuminated by sharp "
                            "directional chiaroscuro key lighting from the upper left."
                        )
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 120, "completion_tokens": 45, "cost": 0.002},
        }

    engine = OpenRouterCascadeEngine(db=db, mock_responder=responder_escalate_to_tier3)
    res1 = asyncio.run(
        engine.caption_media_item(
            media_ids[0],
            dataset_id=ds["id"],
            min_words_override=12,
        )
    )
    assert res1.accepted is True
    assert res1.cascade_layer == "fallback_2"
    assert len(call_log) == 3
    assert [c[0] for c in call_log] == ["primary", "fallback_1", "fallback_2"]

    rej_info = db.list_rejection_logs(ds["id"], media_id=media_ids[0])
    assert len(rej_info["logs"]) == 2
    logged_types = {r["rejection_type"] for r in rej_info["logs"]}
    assert logged_types == {"refusal_regex", "truncated_length"}

    # Test Case 2: All 3 tiers fail -> item marked `needs_manual_caption`
    def responder_all_fail(model: str, layer: str, _payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "choices": [
                {
                    "message": {"content": f"As an AI assistant ({model}), I cannot describe this image."},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 80, "completion_tokens": 12, "cost": 0.0005},
        }

    engine_fail = OpenRouterCascadeEngine(db=db, mock_responder=responder_all_fail)
    res2 = asyncio.run(
        engine_fail.caption_media_item(
            media_ids[1],
            dataset_id=ds["id"],
            min_words_override=12,
        )
    )
    assert res2.accepted is False
    assert res2.caption_status == "needs_manual_caption"

    item2 = db.get_media_item(media_ids[1])
    assert item2 is not None
    assert item2["caption_status"] == "needs_manual_caption"
    assert item2["active_caption"] is None  # Refusal string was NEVER saved as active_caption!


# ============================================================================
# 5. Dataset Exporter & Local FTS5 + TF-IDF/SVD RAG + Prompt Studio Tests
# ============================================================================
def test_exporter_and_local_rag_pipeline(tmp_path: Path) -> None:
    db = DatabaseManager(db_path=tmp_path / "rag_export.db", auto_seed=True)
    img_dir = tmp_path / "raw_images"
    _create_synthetic_images(img_dir, count=4)

    ds = db.create_dataset(
        name="Krea2 Noir Dataset",
        source_type="image_folder",
        source_path=str(img_dir),
        trigger_word="ohwx_noir",
        trigger_position="prefix",
    )
    from backend.app.modules.ingestion.extractor import ingest_image_folder

    items, clusters, stats = ingest_image_folder(
        img_dir,
        thumbnails_dir=tmp_path / "thumbs",
        dedup_hamming_threshold=0,
    )
    media_ids = db.replace_dataset_media_and_clusters(ds["id"], items, clusters, stats)
    db.batch_update_triage(ds["id"], media_ids, curation_state="keep")

    sample_captions = [
        (
            "ohwx_noir Photograph of an adult woman kneeling in traditional seiza on a raw concrete floor, "
            "bound in 6mm unbleached Japanese hemp rope in a tight takate-kote box tie. High-contrast "
            "chiaroscuro overhead spotlight sculpts deep shadows across the industrial brick basement."
        ),
        (
            "ohwx_noir Photograph of an athletic adult woman standing upright in a minimalist black studio void "
            "with a high-gloss reflective floor, wearing a heavy leather posture collar and polished chrome "
            "handcuffs linked by welded steel chains, captured with an 85mm prime portrait lens at f/2.8."
        ),
        (
            "ohwx_noir Photograph of a slender adult woman in a tabletop all-fours posture on a weathered "
            "wooden loft floor, wearing a diamond hishime hemp harness with locking steel carabiners under "
            "warm tungsten directional side lighting."
        ),
        (
            "ohwx_noir Photograph of a poised adult woman seated on a velvet bench inside a stone cellar, "
            "dressed in a tailored emerald silk gown with soft window rim lighting and shallow bokeh depth of field."
        ),
    ]
    for mid, cap in zip(media_ids, sample_captions):
        db.record_caption(
            media_id=mid,
            dataset_id=ds["id"],
            caption_text=cap,
            source="vlm",
            caption_status="vlm_accepted",
            model_used="google/gemini-2.5-pro",
            cascade_layer="primary",
            trigger_word="ohwx_noir",
            trigger_position="prefix",
        )
    db.batch_modify_tags(
        media_ids[:2],
        add_tags=[{"name": "Chiaroscuro", "slug": "chiaroscuro", "category": "lighting"}],
    )

    # 1. Test DatasetExporter
    export_root = tmp_path / "exports"
    exporter = DatasetExporter(db=db, export_root=export_root)
    exp_res = exporter.export_dataset(
        ds["id"],
        image_format="png",
        max_edge=512,
        snap_multiple_16=True,
        create_zip=True,
    )
    assert exp_res["exported_count"] == len(media_ids)
    exp_dir = Path(exp_res["export_dir"])
    assert (exp_dir / "metadata.jsonl").is_file()
    assert (exp_dir / "dataset_card.json").is_file()

    # Check exported image dimensions are snapped to multiples of 16 and <= 512
    png_files = sorted(exp_dir.glob("*.png"))
    txt_files = sorted(exp_dir.glob("*.txt"))
    assert len(png_files) == len(media_ids)
    assert len(txt_files) == len(media_ids)
    sample_img = cv2.imread(str(png_files[0]))
    assert sample_img is not None
    assert max(sample_img.shape[:2]) <= 512
    assert sample_img.shape[0] % 16 == 0 and sample_img.shape[1] % 16 == 0
    assert txt_files[0].read_text(encoding="utf-8").startswith("ohwx_noir ")

    # Check zip archive
    zip_path = Path(exp_res["zip_path"])
    assert zip_path.is_file()
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        assert any(n.endswith("metadata.jsonl") for n in names)
        assert any(n.endswith("dataset_card.json") for n in names)

    # 2. Test LocalRagEngine (FTS5 BM25 + TF-IDF/TruncatedSVD LSA)
    rag = LocalRagEngine(db=db)
    idx_res = asyncio.run(rag.build_index(dataset_id=ds["id"], dense_backend="tfidf_svd_128"))
    assert idx_res["indexed_documents"] == len(media_ids)
    assert idx_res["fts5_ready"] is True

    hits = asyncio.run(
        rag.retrieve(
            query="Japanese hemp rope takate-kote box tie in concrete basement",
            dataset_id=ds["id"],
            tag_filters={"lighting": ["chiaroscuro"]},
            top_k=2,
            retrieval_mode="hybrid",
        )
    )
    assert len(hits) == 2
    assert "takate-kote" in hits[0]["full_caption"]
    assert hits[0]["bm25_score"] > 0

    # Verify cosine similarity function directly
    v1 = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    v2 = np.array([1.0, 1.0, 0.0], dtype=np.float32)
    assert abs(cosine_similarity_vec(v1, v2) - (1.0 / np.sqrt(2.0))) < 1e-5

    # 3. Test LoRAPromptStudio (combining character + style triggerwords)
    studio = LoRAPromptStudio(db=db, rag_engine=rag)
    gen_res = asyncio.run(
        studio.generate(
            query="kneeling on concrete floor in hemp rope box tie with chiaroscuro spotlight",
            selected_tags={"lighting": ["chiaroscuro"]},
            dataset_id=ds["id"],
            trigger_word="ohwx_sara",
            style_trigger="ohwx_noir",
            character="Sara",
            model="local_template",
            top_k=3,
        )
    )
    assert gen_res["grounded"] is True
    for arch_key in ("a", "b", "c"):
        arch = gen_res["archetypes"][arch_key]
        assert arch["prompt"].startswith("ohwx_sara")
        assert "ohwx_noir" in arch["prompt"]
        assert arch["token_count"] > 10


# ============================================================================
# 6. FastAPI End-to-End REST + SSE + WebSocket Tests via TestClient
# ============================================================================
def test_fastapi_endpoints_e2e(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    appdata = tmp_path / "appdata"
    input_dir = tmp_path / "input"
    export_dir = tmp_path / "exports"
    sample_folder = input_dir / "studio_set"
    _create_synthetic_images(sample_folder, count=3)

    monkeypatch.setenv("PIPELINE_APPDATA_DIR", str(appdata))
    monkeypatch.setenv("PIPELINE_INPUT_DIR", str(input_dir))
    monkeypatch.setenv("PIPELINE_EXPORT_DIR", str(export_dir))

    from backend.app.config import get_app_settings

    get_app_settings.cache_clear()
    db_path = appdata / "db" / "state.db"
    app = create_app(db_path=db_path)

    with TestClient(app) as client:
        # 1. Health check
        r_health = client.get("/api/v1/health")
        assert r_health.status_code == 200
        h_body = r_health.json()
        assert h_body["status"] == "ok"
        assert h_body["sqlite_wal"] is True
        assert h_body["fts5"] is True

        # 2. Settings GET & PUT
        r_set = client.put(
            "/api/v1/settings",
            json={
                "openrouter_api_key": "sk-or-v1-secretkey9988",
                "primary_model": "google/gemini-2.5-pro",
                "temperature": 0.35,
                "max_tokens": 700,
            },
        )
        assert r_set.status_code == 200
        assert r_set.json()["settings"]["openrouter_api_key_set"] is True
        assert r_set.json()["settings"]["temperature"] == 0.35

        # Empty API key should preserve existing key
        r_set_empty = client.put("/api/v1/settings", json={"openrouter_api_key": ""})
        assert r_set_empty.status_code == 200
        assert "openrouter_api_key" in r_set_empty.json()["ignored_keys"]
        assert r_set_empty.json()["settings"]["openrouter_api_key_set"] is True

        # 3. Browse filesystem
        r_browse = client.get("/api/v1/fs/browse")
        assert r_browse.status_code == 200
        assert "studio_set" in r_browse.json()["directories"]

        # 4. Create dataset & auto-ingest
        r_ds = client.post(
            "/api/v1/datasets",
            json={
                "name": "Studio Character Set",
                "source_type": "image_folder",
                "source_path": str(sample_folder),
                "trigger_word": "ohwx_lena",
                "trigger_position": "prefix",
                "active_skill_id": "krea2_character_v1",
                "target_count": 10,
                "extraction_config": {"dedup_hamming_threshold": 0},
            },
        )
        assert r_ds.status_code == 200
        ds_id = r_ds.json()["dataset"]["id"]
        assert r_ds.json()["dataset"]["total_items"] == 3

        # 5. List items, batch-triage, autotag, stratification, batch-tag
        r_items = client.get(f"/api/v1/datasets/{ds_id}/items")
        assert r_items.status_code == 200
        items_list = r_items.json()["items"]
        assert len(items_list) == 3
        item_ids = [it["id"] for it in items_list]

        r_triage = client.post(
            f"/api/v1/datasets/{ds_id}/items/batch-triage",
            json={"item_ids": item_ids, "curation_state": "keep"},
        )
        assert r_triage.status_code == 200
        assert r_triage.json()["updated"] == 3

        r_autotag = client.post(
            f"/api/v1/datasets/{ds_id}/autotag",
            json={"plugin_id": "rule_cv_heuristics", "min_confidence": 0.5},
        )
        assert r_autotag.status_code == 200
        assert r_autotag.json()["items_tagged"] == 3

        r_strat = client.get(f"/api/v1/datasets/{ds_id}/stratification")
        assert r_strat.status_code == 200
        assert "aspect_ratio_buckets" in r_strat.json()

        r_btag = client.post(
            f"/api/v1/datasets/{ds_id}/items/batch-tag",
            json={
                "item_ids": item_ids[:2],
                "add_tags": [{"name": "Rope Harness", "category": "equipment"}],
            },
        )
        assert r_btag.status_code == 200

        # 6. Skills & Manual Caption Update
        r_skills = client.get("/api/v1/skills")
        assert r_skills.status_code == 200
        assert len(r_skills.json()["skills"]) >= 3

        for idx, mid in enumerate(item_ids):
            r_cap = client.put(
                f"/api/v1/items/{mid}/caption",
                json={
                    "caption_text": (
                        f"Photograph of an adult woman in studio pose {idx + 1}, wearing a structured "
                        f"rope harness over dark linen clothing under directional key lighting."
                    ),
                    "enforce_trigger": True,
                },
            )
            assert r_cap.status_code == 200
            assert r_cap.json()["caption_text"].startswith("ohwx_lena ")

        # 7. Export & ZIP Download
        r_exp = client.post(
            f"/api/v1/datasets/{ds_id}/export",
            json={"image_format": "png", "max_edge": 512, "snap_multiple_16": True, "create_zip": True},
        )
        assert r_exp.status_code == 200
        assert r_exp.json()["exported_count"] == 3

        r_dl = client.get(f"/api/v1/datasets/{ds_id}/export/download")
        assert r_dl.status_code == 200
        assert r_dl.headers["content-type"] == "application/zip"
        assert len(r_dl.content) > 100

        # 8. RAG Index, Retrieve, and Generate
        r_ridx = client.post("/api/v1/rag/index", json={"dataset_id": ds_id})
        assert r_ridx.status_code == 200
        assert r_ridx.json()["indexed_documents"] == 3

        r_rret = client.post(
            "/api/v1/rag/retrieve",
            json={"query": "rope harness directional key lighting", "dataset_id": ds_id, "top_k": 2},
        )
        assert r_rret.status_code == 200
        assert len(r_rret.json()["results"]) == 2

        r_rgen = client.post(
            "/api/v1/rag/generate",
            json={
                "query": "studio pose with structured rope harness",
                "dataset_id": ds_id,
                "trigger_word": "ohwx_lena",
                "model": "local_template",
                "top_k": 2,
            },
        )
        assert r_rgen.status_code == 200
        assert r_rgen.json()["grounded"] is True
        assert "a" in r_rgen.json()["archetypes"]

        # 8b. MOSS 3-Tier Prompt Expander
        r_expand = client.post(
            "/api/v1/rag/expand-prompt",
            json={
                "idea": "sitting quietly on a wooden bench under soft lighting",
                "dataset_id": ds_id,
                "trigger_word": "ohwx_lena",
                "class_noun": "woman",
                "mode": "CHARACTER",
                "model": "local_template",
                "top_k": 2,
            },
        )
        assert r_expand.status_code == 200
        data_expand = r_expand.json()
        assert "long" in data_expand
        assert "medium" in data_expand
        assert "short" in data_expand
        assert "ohwx_lena" in data_expand["short"]
        assert len(data_expand["exemplars"]) == 2

        # 9. SSE & WebSocket Telemetry
        r_sse = client.get("/api/v1/events/stream?max_events=1")
        assert r_sse.status_code == 200
        assert "data:" in r_sse.text

        with client.websocket_connect("/api/v1/ws") as ws:
            msg = json.loads(ws.receive_text())
            assert msg["type"] == "connected"
            ws.send_text("ping")
            pong = json.loads(ws.receive_text())
            assert pong["type"] == "pong"

    reset_default_db(None)
    get_app_settings.cache_clear()
