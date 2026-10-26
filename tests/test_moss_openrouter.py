"""Unit tests for MOSS OpenRouter cascade, fallback mechanism, and API endpoints."""
from __future__ import annotations

import json
from typing import Any

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from pipeline.api import app, db
from pipeline.moss_openrouter import (
    FALLBACK_1_MODEL,
    FALLBACK_2_MODEL,
    PRIMARY_MODEL,
    MossCaptionOptions,
    MossCascadeExhaustedError,
    MossOpenRouterClient,
)


def _make_valid_response(
    long_desc: str = (
        "A stylish cyberpunk warrior wearing a holographic trench coat with intricate glowing patterns stands "
        "alert in a rain-drenched futuristic city street surrounded by towering skyscrapers and neon billboards. "
        "Her boots are mud-spattered and her hands rest on a high-tech utility belt. Ambient purple and blue lights "
        "reflect off wet pavement while soft mist rises from nearby steam vents under a gloomy evening sky."
    ),
    med_desc: str = (
        "A stylish cyberpunk warrior wearing a holographic trench coat stands in a rain-drenched futuristic city street "
        "surrounded by neon lights and towering skyscrapers with wet reflections."
    ),
    short_desc: str = "A cyberpunk warrior in a futuristic neon city street.",
) -> str:
    payload = {
        "caption": {
            "long": long_desc,
            "medium": med_desc,
            "short": short_desc,
        },
        "watermark": [{"box_2d": [10, 10, 50, 100], "label": "watermark logo"}],
        "borders": ["none"],
    }
    return json.dumps(payload)


def _mock_openrouter(monkeypatch, sequence: list[tuple[int, str]]):
    """Mocks httpx.AsyncClient.post with a sequence of (status_code, response_text)."""
    calls: list[dict[str, Any]] = []

    class MockResponse:
        def __init__(self, status_code: int, text: str):
            self.status_code = status_code
            self.text = text

        def json(self):
            return json.loads(self.text)

    async def fake_post(self, url, json_payload=None, headers=None, timeout=None, **kwargs):
        payload_data = json_payload if json_payload is not None else kwargs.get("json")
        calls.append({"url": url, "payload": payload_data, "headers": headers})
        idx = min(len(calls) - 1, len(sequence) - 1)
        status, text = sequence[idx]
        if status == 200:
            outer = {"choices": [{"message": {"content": text}}]}
            return MockResponse(200, json.dumps(outer))
        return MockResponse(status, text)

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    return calls


@pytest.fixture(autouse=True)
def setup_test_environment(monkeypatch):
    """Prevents /mnt/c directory creation and state reconciliation issues in test runner."""
    monkeypatch.setattr("pipeline.config.Settings.ensure_dirs", lambda self: None)
    monkeypatch.setattr("pipeline.reconcile.reconcile_captions_and_state", lambda db, s: {})


@pytest.mark.anyio
async def test_primary_model_success(monkeypatch):
    """Primary tier succeeds on first try."""
    valid_content = _make_valid_response()
    calls = _mock_openrouter(monkeypatch, [(200, valid_content)])

    client = MossOpenRouterClient()
    dummy_img = np.zeros((64, 64, 3), dtype=np.uint8)

    async with httpx.AsyncClient() as http_client:
        res = await client.caption_image(
            client=http_client,
            image_input=dummy_img,
            api_key="sk-test-key",
            options=MossCaptionOptions(trigger_phrase="c1_eva", class_noun="woman"),
        )

    assert len(calls) == 1
    assert calls[0]["payload"]["model"] == PRIMARY_MODEL
    assert res.model_used == "primary"
    assert res.model_name == PRIMARY_MODEL
    assert res.cascade_tier == "primary"
    assert res.caption.long.startswith("c1_eva, ")
    assert len(res.watermark) == 1
    assert res.watermark[0].box_2d == [10, 10, 50, 100]


@pytest.mark.anyio
async def test_refusal_cascades_to_fallback_1(monkeypatch):
    """Model 1 refuses; client cascades immediately to Fallback 1 (Gemini 2.5 Pro)."""
    valid_content = _make_valid_response()
    sequence = [
        (200, "I cannot fulfill this request due to safety guidelines."),
        (200, valid_content),
    ]
    calls = _mock_openrouter(monkeypatch, sequence)

    client = MossOpenRouterClient()
    dummy_img = np.zeros((64, 64, 3), dtype=np.uint8)

    async with httpx.AsyncClient() as http_client:
        res = await client.caption_image(
            client=http_client,
            image_input=dummy_img,
            api_key="sk-test-key",
        )

    assert len(calls) == 2
    assert calls[0]["payload"]["model"] == PRIMARY_MODEL
    assert calls[1]["payload"]["model"] == FALLBACK_1_MODEL
    assert res.model_used == "fallback_1"
    assert res.model_name == FALLBACK_1_MODEL
    assert res.cascade_tier == "fallback_1"


@pytest.mark.anyio
async def test_error_and_refusal_cascade_to_fallback_2(monkeypatch):
    """Primary returns 429 rate limit, Fallback 1 refuses, Fallback 2 (Pixtral) succeeds."""
    valid_content = _make_valid_response()
    sequence = [
        (429, "Rate limit exceeded"),
        (200, "As an AI, I am unable to assist with this image."),
        (200, valid_content),
    ]
    calls = _mock_openrouter(monkeypatch, sequence)

    client = MossOpenRouterClient()
    dummy_img = np.zeros((64, 64, 3), dtype=np.uint8)

    async with httpx.AsyncClient() as http_client:
        res = await client.caption_image(
            client=http_client,
            image_input=dummy_img,
            api_key="sk-test-key",
        )

    assert len(calls) == 3
    assert calls[0]["payload"]["model"] == PRIMARY_MODEL
    assert calls[1]["payload"]["model"] == FALLBACK_1_MODEL
    assert calls[2]["payload"]["model"] == FALLBACK_2_MODEL
    assert res.model_used == "fallback_2"
    assert res.model_name == FALLBACK_2_MODEL
    assert res.cascade_tier == "fallback_2"


@pytest.mark.anyio
async def test_all_tiers_exhausted_raises(monkeypatch):
    """When all three tiers fail, MossCascadeExhaustedError is raised."""
    sequence = [
        (500, "Internal Server Error"),
        (429, "Rate Limit Exceeded"),
        (200, "I can't assist with this request."),
    ]
    _mock_openrouter(monkeypatch, sequence)

    client = MossOpenRouterClient()
    dummy_img = np.zeros((64, 64, 3), dtype=np.uint8)

    with pytest.raises(MossCascadeExhaustedError) as exc_info:
        async with httpx.AsyncClient() as http_client:
            await client.caption_image(
                client=http_client,
                image_input=dummy_img,
                api_key="sk-test-key",
            )

    assert len(exc_info.value.failures) == 3
    assert "All 3 cascade tiers failed" in str(exc_info.value)


@pytest.mark.anyio
async def test_trait_leakage_and_auto_sanitization(monkeypatch):
    """Checks trait leakage detection and auto-sanitization across tiers."""
    leaked_long = (
        "A warrior with neon mohawk and scar standing firmly in the street surrounded by buildings. "
        "The warrior looks forward while lights gleam along the pavement."
    )
    leaked_med = "A warrior with neon mohawk in the city street."
    leaked_short = "Warrior with scar standing outside."

    leaked_content = json.dumps({
        "caption": {
            "long": leaked_long,
            "medium": leaked_med,
            "short": leaked_short,
        },
        "watermark": [],
        "borders": ["none"],
    })
    _mock_openrouter(monkeypatch, [(200, leaked_content)])

    client = MossOpenRouterClient()
    dummy_img = np.zeros((64, 64, 3), dtype=np.uint8)

    # 1. Without auto-sanitize: leaks should be reported in leaked_traits
    async with httpx.AsyncClient() as http_client:
        res_no_clean = await client.caption_image(
            client=http_client,
            image_input=dummy_img,
            api_key="sk-test-key",
            options=MossCaptionOptions(
                locked_traits=["neon mohawk", "scar"],
                auto_sanitize=False,
            ),
        )
    assert "neon mohawk" in res_no_clean.leaked_traits
    assert "scar" in res_no_clean.leaked_traits

    # 2. With auto_sanitize=True: leaks should be cleansed from caption texts
    async with httpx.AsyncClient() as http_client:
        res_cleaned = await client.caption_image(
            client=http_client,
            image_input=dummy_img,
            api_key="sk-test-key",
            options=MossCaptionOptions(
                locked_traits=["neon mohawk", "scar"],
                auto_sanitize=True,
            ),
        )
    assert "neon mohawk" not in res_cleaned.caption.long
    assert "scar" not in res_cleaned.caption.long
    assert "neon mohawk" not in res_cleaned.caption.medium
    assert "scar" not in res_cleaned.caption.short
    assert any("Auto-sanitized" in w for w in res_cleaned.warnings)


def test_api_validate_caption_endpoint():
    """Tests POST /api/moss/validate-caption endpoint."""
    with TestClient(app) as test_client:
        # 1. Refusal payload
        res = test_client.post(
            "/api/moss/validate-caption",
            json={"caption": "I am sorry, but I cannot fulfill this request."},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["is_refusal"] is True
        assert data["is_valid"] is False

        # 2. Valid multi-tier payload with trigger injection check
        valid_cap = {
            "long": "A " * 35,
            "medium": "B " * 15,
            "short": "C " * 6,
        }
        res_valid = test_client.post(
            "/api/moss/validate-caption",
            json={
                "caption": valid_cap,
                "locked_traits": ["laser rifle"],
                "trigger_phrase": "c1_eva",
                "class_noun": "hero",
            },
        )
        assert res_valid.status_code == 200
        data_valid = res_valid.json()
        assert data_valid["is_valid"] is True
        assert data_valid["caption"]["long"].startswith("c1_eva, ")
        assert data_valid["word_counts"]["short"] == 7  # 6 + trigger word


def test_api_sanitize_leaks_endpoint():
    """Tests POST /api/moss/sanitize-leaks and /api/datasets/{id}/sanitize-leaks."""
    with TestClient(app) as test_client:
        # Direct text & caption dict sanitization
        body = {
            "text": "The subject with blue eyes wearing a jacket.",
            "caption": {
                "long": "Person with blue eyes in a city.",
                "medium": "Person with blue eyes.",
                "short": "Subject with blue eyes.",
            },
            "locked_traits": ["blue eyes"],
        }
        res = test_client.post("/api/moss/sanitize-leaks", json=body)
        assert res.status_code == 200
        data = res.json()
        assert "blue eyes" not in data["sanitized_text"]
        assert "blue eyes" not in data["sanitized_caption"]["long"]
        assert "blue eyes" not in data["sanitized_caption"]["medium"]
        assert "blue eyes" not in data["sanitized_caption"]["short"]

        # Dataset route alias
        res_ds = test_client.post("/api/datasets/test_ds_1/sanitize-leaks", json=body)
        assert res_ds.status_code == 200
        data_ds = res_ds.json()
        assert "blue eyes" not in data_ds["sanitized_text"]


def test_api_caption_frame_endpoint(monkeypatch, tmp_path):
    """Tests POST /api/moss/caption/frame/{frame_id} with mock OpenRouter and DB fixture."""
    # Create dummy image on disk
    dummy_img_path = tmp_path / "test_frame.jpg"
    dummy_img = np.zeros((64, 64, 3), dtype=np.uint8)
    import cv2
    cv2.imwrite(str(dummy_img_path), dummy_img)

    # Insert frame into test DB
    vid_id = "test_moss_vid"
    with db.write() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO videos (id, path, name, slug, duration) VALUES (?, ?, ?, ?, ?)",
            (vid_id, "/dummy/test.mp4", "Test Vid", "test_vid", 10.0),
        )
        cursor = conn.execute(
            "INSERT INTO frames (video_id, preview_path, full_path, t) VALUES (?, ?, ?, ?)",
            (vid_id, str(dummy_img_path), str(dummy_img_path), 0.0),
        )
        frame_id = cursor.lastrowid

    valid_content = _make_valid_response()
    _mock_openrouter(monkeypatch, [(200, valid_content)])

    with TestClient(app) as test_client:
        res = test_client.post(
            f"/api/moss/caption/frame/{frame_id}",
            json={
                "mode": "character",
                "class_noun": "woman",
                "locked_traits": ["neon hair"],
                "trigger_phrase": "c1_eva",
                "api_key": "sk-dummy-key",
                "save_to_db": True,
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["frame_id"] == frame_id
        assert data["model_used"] == "primary"
        assert data["caption"]["long"].startswith("c1_eva, ")

        # Verify saved in captions table
        saved = db.one("SELECT * FROM captions WHERE frame_id=?", (frame_id,))
        assert saved is not None
        assert saved["source"] == "moss"
        assert saved["model"] == PRIMARY_MODEL
