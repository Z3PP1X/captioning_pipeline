"""RAG index build, retrieval and /api/rag/* endpoints against temp fixtures (no user data, no network)."""
from __future__ import annotations

import hashlib
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

import pipeline.api as api
from pipeline.config import Settings
from pipeline.rag import RagIndexJob, RagRetriever
from pipeline.rag.indexer import RagCaptionIndexer, resolve_caption_sources
from pipeline.rag.linter import lint_prompt

CAPTIONS = {
    "vid_a_0001": "An adult woman kneels on a concrete floor. Her arms are bound behind her in hemp rope in a box tie. Chiaroscuro spotlight from the left.",
    "vid_a_0002": "An adult woman kneels on a concrete floor. Her arms are bound behind her in hemp rope in a box tie. Chiaroscuro spotlight from the right.",
    "vid_b_0001": "An adult woman stands in a studio void with steel chains and chrome handcuffs. Soft light and shallow depth of field.",
    "vid_c_0001": "An adult woman lies prone on a wooden floor in a loft with a leather collar. Window light and bokeh.",
}


class FakeClassifier:
    """Deterministic bag-of-words embedder standing in for the CLIP text model."""

    repo = "fake/bag-of-words"

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), 64), dtype=np.float32)
        for i, text in enumerate(texts):
            for word in text.lower().replace(".", " ").replace(",", " ").split():
                out[i, int(hashlib.md5(word.encode()).hexdigest(), 16) % 64] += 1.0
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return out / norms


def _write_pairs(folder: Path, items: dict[str, str]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for stem, caption in items.items():
        (folder / f"{stem}.txt").write_text(caption, encoding="utf-8")
        (folder / f"{stem}.png").write_bytes(b"\x89PNG\r\n\x1a\nfixture")


@pytest.fixture()
def rag_settings(tmp_path: Path) -> Settings:
    return Settings(
        video_dir=tmp_path / "videos",
        cache_dir=tmp_path / "cache",
        finished_dir=tmp_path / "proj" / "data" / "finished",
    )


@pytest.fixture()
def curated(rag_settings: Settings) -> Settings:
    items = list(CAPTIONS.items())
    _write_pairs(rag_settings.curated_dir / "train", dict(items[:3]))
    _write_pairs(rag_settings.curated_dir / "val", dict(items[3:]))
    return rag_settings


@pytest.fixture()
def rag_api(monkeypatch: pytest.MonkeyPatch, rag_settings: Settings):
    """Point the API's RAG singletons at the temp settings; no lifespan (no library scan)."""
    clf = FakeClassifier()
    retriever = RagRetriever(rag_settings, classifier=clf)
    job = RagIndexJob(rag_settings, classifier=clf, on_finished=lambda _r: retriever.invalidate())
    monkeypatch.setattr(api, "settings", rag_settings)
    monkeypatch.setattr(api, "rag_retriever", retriever)
    monkeypatch.setattr(api, "rag_index_job", job)
    monkeypatch.setattr(api, "_openrouter_key", lambda explicit: (explicit or "").strip())
    return TestClient(api.app)


def _wait_for_build(client: TestClient, timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get("/api/rag/status").json()
        if status["build"]["state"] != "running":
            return status
        time.sleep(0.05)
    raise AssertionError("RAG index build did not finish")


# ----------------------------------------------------------------------------- sources + index
def test_sources_prefer_curated_then_merged(rag_settings: Settings):
    assert resolve_caption_sources(rag_settings)["source"] is None

    _write_pairs(rag_settings.finished_dir / "merged", {"m_0001": CAPTIONS["vid_b_0001"]})
    merged = resolve_caption_sources(rag_settings)
    assert merged["source"] == "finished_merged" and merged["caption_files"] == 1

    _write_pairs(rag_settings.curated_dir / "train", {"c_0001": CAPTIONS["vid_a_0001"]})
    assert resolve_caption_sources(rag_settings)["source"] == "curated"


def test_build_and_retrieve_with_mmr_and_reload(curated: Settings):
    clf = FakeClassifier()
    res = RagCaptionIndexer(curated, classifier=clf).build_index()
    assert res["indexed"] == 4 and res["source"] == "curated"

    retriever = RagRetriever(curated, classifier=clf)
    query = {"equipment": ["hemp_rope", "takate_kote"]}
    pure = retriever.retrieve(query, "", top_k=2, diversity=1.0)
    assert {h["stem"] for h in pure} == {"vid_a_0001", "vid_a_0002"}  # two near-identical frames

    hits = retriever.retrieve(query, "", top_k=2, diversity=0.3)
    assert len(hits) == 2
    assert {"hemp_rope", "takate_kote"} & set(hits[0]["matched_tags"])
    # MMR swaps the near-duplicate for a different exemplar
    assert not {h["stem"] for h in hits} >= {"vid_a_0001", "vid_a_0002"}
    assert {"id", "stem", "score", "matched_tags", "img_rel_path", "full_caption"} <= set(hits[0])

    # Rebuild with fewer captions -> the same retriever instance reloads without restart
    (curated.curated_dir / "val" / "vid_c_0001.txt").unlink()
    time.sleep(0.01)
    RagCaptionIndexer(curated, classifier=clf).build_index()
    assert retriever.count() == 3


def test_identical_captions_are_not_returned_twice(rag_settings: Settings):
    _write_pairs(rag_settings.finished_dir / "merged", {
        "dup_0001": CAPTIONS["vid_a_0001"],
        "dup_0002": CAPTIONS["vid_a_0001"],
        "other_0001": CAPTIONS["vid_b_0001"],
    })
    clf = FakeClassifier()
    assert RagCaptionIndexer(rag_settings, classifier=clf).build_index()["source"] == "finished_merged"
    hits = RagRetriever(rag_settings, classifier=clf).retrieve({"equipment": ["hemp_rope"]}, "", top_k=2)
    assert [h["stem"] for h in hits][1] == "other_0001"
    # Pool exhausted -> duplicates back-fill instead of returning fewer results
    assert len(RagRetriever(rag_settings, classifier=clf).retrieve({}, "rope", top_k=3)) == 3


def test_empty_source_keeps_existing_index(curated: Settings, rag_settings: Settings):
    clf = FakeClassifier()
    RagCaptionIndexer(curated, classifier=clf).build_index()
    for txt in curated.curated_dir.rglob("*.txt"):
        txt.unlink()
    assert RagCaptionIndexer(curated, classifier=clf).build_index()["indexed"] == 0
    assert RagRetriever(curated, classifier=clf).count() == 4


# ----------------------------------------------------------------------------- API
def test_status_and_retrieve_without_index(rag_api: TestClient):
    status = rag_api.get("/api/rag/status").json()
    assert status["ready"] is False and status["indexed"] == 0
    assert status["sources"]["source"] is None and status["sources"]["searched"]
    assert status["build"]["state"] == "idle"

    res = rag_api.post("/api/rag/retrieve", json={"selected_tags": {}, "user_description": "rope"})
    assert res.status_code == 200
    assert res.json() == {"results": [], "index_ready": False}

    assert rag_api.get("/api/rag/preview/1").status_code == 404


def test_build_without_captions_reports_error(rag_api: TestClient):
    assert rag_api.post("/api/rag/index", json={}).status_code == 200
    status = _wait_for_build(rag_api)
    assert status["build"]["state"] == "error"
    assert "No caption .txt files found" in status["build"]["error"]
    assert status["ready"] is False


def test_build_index_via_api_then_retrieve_and_preview(rag_api: TestClient, curated: Settings):
    started = rag_api.post("/api/rag/index", json={})
    assert started.status_code == 200
    status = _wait_for_build(rag_api)
    assert status["build"]["state"] == "done", status["build"]
    assert status["ready"] is True and status["indexed"] == 4
    assert status["index"]["source"] == "curated"

    res = rag_api.post("/api/rag/retrieve", json={"selected_tags": {"surroundings": ["studio_void"]}, "top_k": 2})
    body = res.json()
    assert body["index_ready"] is True and len(body["results"]) == 2

    preview = rag_api.get(f"/api/rag/preview/{body['results'][0]['id']}")
    assert preview.status_code == 200 and preview.headers["content-type"] == "image/png"


def test_generate_shape_matches_frontend_model(rag_api: TestClient, monkeypatch: pytest.MonkeyPatch):
    async def fake_openrouter(self, api_key, model, system_prompt, user_prompt):
        assert api_key == "sk-test"
        return (
            '{"archetype_a": "photograph, adult woman, hemp rope",'
            ' "archetype_b": "Photograph of an adult woman kneeling.",'
            ' "archetype_c": "masterpiece, an adult woman kneels on concrete."}'
        )

    monkeypatch.setattr(api.RagPromptGenerator, "_call_openrouter", fake_openrouter)
    res = rag_api.post(
        "/api/rag/generate",
        json={"selected_tags": {"equipment": ["hemp_rope"]}, "api_key": "sk-test", "trigger": "tok"},
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert set(data) == {"model", "trigger", "character", "grounded", "notices", "archetypes", "rag_context"}
    assert data["grounded"] is False and data["rag_context"] == [] and data["notices"]
    for key in ("a", "b", "c"):
        arch = data["archetypes"][key]
        assert set(arch) == {"name", "prompt", "token_count", "warnings", "is_valid"}
        assert arch["prompt"].startswith("tok")
    assert "masterpiece" not in data["archetypes"]["c"]["prompt"]


def test_generate_errors_are_actionable(rag_api: TestClient, monkeypatch: pytest.MonkeyPatch):
    res = rag_api.post("/api/rag/generate", json={"selected_tags": {}})
    assert res.status_code == 400 and "API key" in res.json()["detail"]

    async def failing(self, *args, **kwargs):
        raise api.RagUpstreamError("OpenRouter error 401: bad key")

    monkeypatch.setattr(api.RagPromptGenerator, "_call_openrouter", failing)
    res = rag_api.post("/api/rag/generate", json={"selected_tags": {}, "api_key": "sk-x"})
    assert res.status_code == 502 and "401" in res.json()["detail"]


def test_unknown_api_route_is_json_404(rag_api: TestClient):
    res = rag_api.get("/api/rag/does-not-exist")
    assert res.status_code == 404
    assert res.headers["content-type"].startswith("application/json")


def test_linter_flags_prohibited_terms_as_invalid():
    assert lint_prompt("photograph of a teen in rope", archetype="short")["is_valid"] is False
    assert lint_prompt("photograph of an adult woman in rope", archetype="short")["is_valid"] is True


def test_max_per_source_capping(curated: Settings):
    clf = FakeClassifier()
    RagCaptionIndexer(curated, classifier=clf).build_index()
    retriever = RagRetriever(curated, classifier=clf)
    query = {"equipment": ["hemp_rope"]}
    # With max_per_source=1, even if vid_a has multiple matching frames, only 1 should be picked
    hits = retriever.retrieve(query, "", top_k=2, diversity=0.5, max_per_source=1)
    vid_a_hits = [h for h in hits if "vid_a" in h["stem"]]
    assert len(vid_a_hits) <= 1
