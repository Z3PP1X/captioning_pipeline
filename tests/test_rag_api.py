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
from pipeline.rag.generator import exemplar_subsets, variation_temperature
from pipeline.rag.history import RagHistory
from pipeline.rag.indexer import FTS_TABLE, RagCaptionIndexer, resolve_caption_sources
from pipeline.rag.linter import lint_prompt
from pipeline.rag.retriever import competition_ranks, fts_query

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
    monkeypatch.setattr(api, "rag_history", RagHistory(rag_settings.cache_dir / "state.db"))
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
    assert res.json() == {"results": [], "index_ready": False, "dense_mode": "whole"}

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
    async def fake_openrouter(self, api_key, model, system_prompt, user_prompt, temperature=0.7):
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
    assert set(data) == {
        "model", "trigger", "character", "grounded", "notices", "archetypes", "rag_context",
        "history_id", "batch_id", "variations",
    }
    assert isinstance(data["history_id"], int) and len(data["variations"]) == 1
    assert data["variations"][0]["id"] == data["history_id"]
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


# ----------------------------------------------------------------------------- Phase H: R2 lexical matching
def test_tag_matching_uses_word_boundaries():
    seg = RagCaptionIndexer.segment_caption
    match = RagCaptionIndexer.match_taxonomy_tags
    # "slightly" must not count as a lighting sentence, "room" not as "broom"
    segments = seg("An adult woman leans slightly forward. A broom rests nearby. Chiaroscuro spotlight from above.")
    assert "slightly" not in segments["camera_lighting"]
    assert "broom" not in segments["surroundings"]
    assert "Chiaroscuro" in segments["camera_lighting"]
    # Plurals and hyphen/space variants still match; substrings inside other words do not
    assert "tabletop_all_fours" in match("She is on all-fours.", "position")
    assert "tabletop_all_fours" in match("She is on all fours.", "position")
    assert "steel_chains" in match("Heavy steel chains.", "equipment")
    assert match("The bridle leather harness is tied.", "equipment")
    assert "leather_collar" not in match("A collared shirt.", "equipment")


def test_fts_query_terms():
    assert fts_query("The woman, in a LEATHER collar!") == '"woman" OR "leather" OR "collar"'
    assert fts_query("a the of") == ""
    assert list(competition_ranks(np.array([0.9, 0.5, 0.9, 0.1]))) == [1, 3, 1, 4]


def test_bm25_built_with_index_and_fused_with_rrf(curated: Settings):
    clf = FakeClassifier()
    res = RagCaptionIndexer(curated, classifier=clf).build_index()
    assert res["fts"] is True
    retriever = RagRetriever(curated, classifier=clf)
    hits = retriever.retrieve({}, "leather collar window", top_k=2, diversity=1.0)
    assert retriever.lexical_ready is True
    assert hits[0]["stem"] == "vid_c_0001"
    assert 0.0 < hits[0]["score"] <= 1.0 and hits[0]["score"] >= hits[1]["score"]
    # Linear fusion is still available for comparison (no BM25)
    assert retriever.retrieve({}, "leather collar window", top_k=1, fusion="linear")[0]["stem"] == "vid_c_0001"


def test_fts_built_lazily_for_index_that_predates_it(curated: Settings):
    import sqlite3

    clf = FakeClassifier()
    indexer = RagCaptionIndexer(curated, classifier=clf)
    indexer.build_index()
    with sqlite3.connect(indexer.db_path) as conn:  # simulate an index built before Phase H
        conn.execute(f"DROP TABLE {FTS_TABLE}")
        conn.execute("DELETE FROM rag_meta WHERE key='fts_built_at'")
    retriever = RagRetriever(curated, classifier=clf)
    assert retriever.retrieve({}, "chrome handcuffs", top_k=1)[0]["stem"] == "vid_b_0001"
    assert retriever.lexical_ready is True
    with sqlite3.connect(indexer.db_path) as conn:
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (FTS_TABLE,)).fetchone()


def test_retrieval_degrades_without_fts5(curated: Settings, monkeypatch: pytest.MonkeyPatch):
    import pipeline.rag.indexer as indexer_mod
    import pipeline.rag.retriever as retriever_mod

    monkeypatch.setattr(indexer_mod, "build_fts", lambda conn, built_at: False)
    monkeypatch.setattr(retriever_mod, "build_fts", lambda conn, built_at: False)
    clf = FakeClassifier()
    assert RagCaptionIndexer(curated, classifier=clf).build_index()["fts"] is False
    retriever = RagRetriever(curated, classifier=clf)
    hits = retriever.retrieve({"equipment": ["hemp_rope"]}, "leather collar", top_k=2)
    assert len(hits) == 2 and retriever.lexical_ready is False


# ----------------------------------------------------------------------------- Phase H: R7 warm + query cache
class CountingClassifier(FakeClassifier):
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        self.calls.append(list(texts))
        return super().embed_texts(texts)


def test_query_embeddings_are_lru_cached(curated: Settings):
    RagCaptionIndexer(curated, classifier=FakeClassifier()).build_index()
    clf = CountingClassifier()
    retriever = RagRetriever(curated, classifier=clf)
    retriever.retrieve({}, "Hemp rope  kneeling", top_k=2)
    retriever.retrieve({}, "hemp rope kneeling", top_k=2)  # same text after normalisation
    assert len(clf.calls) == 1 and retriever.query_cache_hits == 1
    retriever.invalidate()  # an index reload keeps the query cache
    retriever.retrieve({}, "hemp rope kneeling", top_k=2)
    assert len(clf.calls) == 1


def test_status_warms_retriever_once_index_is_ready(rag_api: TestClient, curated: Settings):
    assert api.rag_retriever._warm_thread is None
    rag_api.get("/api/rag/status")
    assert api.rag_retriever._warm_thread is None  # nothing to warm without an index
    rag_api.post("/api/rag/index", json={})
    status = _wait_for_build(rag_api)
    assert status["retrieval"]["fusion"] == "rrf" and status["retrieval"]["token_counter"] in {"qwen", "heuristic"}
    thread = api.rag_retriever._warm_thread
    assert thread is not None
    thread.join(timeout=5)
    assert api.rag_retriever.warm_async() is False  # only once
    assert rag_api.get("/api/rag/status").json()["retrieval"]["bm25"] is True


# ----------------------------------------------------------------------------- Phase H: Generate N + history
def _fake_llm(monkeypatch: pytest.MonkeyPatch, fail_above: float | None = None) -> list[dict]:
    calls: list[dict] = []

    async def fake(self, api_key, model, system_prompt, user_prompt, temperature=0.7):
        calls.append({"temperature": temperature, "user_prompt": user_prompt})
        if fail_above is not None and temperature > fail_above:
            raise api.RagUpstreamError("OpenRouter error 429: rate limited")
        return (
            '{"archetype_a": "photograph, adult woman, hemp rope",'
            f' "archetype_b": "Photograph of an adult woman kneeling at {temperature}.",'
            ' "archetype_c": "An adult woman kneels on concrete."}'
        )

    monkeypatch.setattr(api.RagPromptGenerator, "_call_openrouter", fake)
    return calls


def test_variation_helpers():
    assert variation_temperature(0, 5) == 0.7 and variation_temperature(4, 5) == 0.95
    assert exemplar_subsets(3, 4, 2) == [[0, 1, 2], [0, 1, 2]]
    subsets = exemplar_subsets(8, 4, 3)
    assert subsets[0] == [0, 1, 2, 3] and all(len(s) == 4 and s == sorted(s) for s in subsets)
    assert subsets[1] != subsets[0]


def test_generate_n_returns_variations_and_stores_history(rag_api: TestClient, curated: Settings, monkeypatch):
    rag_api.post("/api/rag/index", json={})
    assert _wait_for_build(rag_api)["ready"] is True
    calls = _fake_llm(monkeypatch)
    res = rag_api.post(
        "/api/rag/generate",
        json={"selected_tags": {"equipment": ["hemp_rope"]}, "api_key": "sk-test", "trigger": "tok", "n": 3, "top_k": 2},
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert len(calls) == 3 and sorted(c["temperature"] for c in calls) == [0.7, 0.825, 0.95]
    variations = data["variations"]
    assert [v["batch_index"] for v in variations] == [0, 1, 2]
    assert {v["batch_id"] for v in variations} == {data["batch_id"]}
    assert all(isinstance(v["id"], int) and v["result"]["grounded"] for v in variations)
    assert variations[0]["inputs"]["n"] == 3 and "api_key" not in variations[0]["inputs"]
    assert data["archetypes"] == variations[0]["result"]["archetypes"]

    history = rag_api.get("/api/rag/history").json()
    assert history["total"] == 3
    assert [e["id"] for e in history["items"]] == sorted((v["id"] for v in variations), reverse=True)

    for bad in (0, 11):
        assert rag_api.post("/api/rag/generate", json={"api_key": "sk-test", "n": bad}).status_code == 422


def test_generate_n_partial_and_total_failure(rag_api: TestClient, monkeypatch: pytest.MonkeyPatch):
    _fake_llm(monkeypatch, fail_above=0.8)
    data = rag_api.post("/api/rag/generate", json={"api_key": "sk-test", "n": 3}).json()
    assert len(data["variations"]) == 1
    assert any("2 of 3 variations failed" in n for n in data["notices"])

    _fake_llm(monkeypatch, fail_above=0.0)
    res = rag_api.post("/api/rag/generate", json={"api_key": "sk-test", "n": 2})
    assert res.status_code == 502 and "429" in res.json()["detail"]


def test_history_favorite_edit_delete(rag_api: TestClient, monkeypatch: pytest.MonkeyPatch):
    _fake_llm(monkeypatch)
    first = rag_api.post("/api/rag/generate", json={"api_key": "sk-test", "trigger": "tok"}).json()["history_id"]
    second = rag_api.post("/api/rag/generate", json={"api_key": "sk-test", "trigger": "tok"}).json()["history_id"]

    starred = rag_api.patch(f"/api/rag/history/{first}", json={"favorite": True})
    assert starred.status_code == 200 and starred.json()["favorite"] is True
    favs = rag_api.get("/api/rag/history?favorites=1").json()
    assert favs["total"] == 1 and favs["items"][0]["id"] == first
    assert rag_api.get("/api/rag/history?limit=1").json()["items"][0]["id"] == second

    edited = rag_api.patch(f"/api/rag/history/{first}", json={"prompts": {"b": "masterpiece, photograph of a woman kneeling"}})
    arch_b = edited.json()["result"]["archetypes"]["b"]
    assert arch_b["edited"] is True and arch_b["prompt"].startswith("tok") and "masterpiece" not in arch_b["prompt"]
    assert "adult woman" in arch_b["prompt"] and arch_b["warnings"]
    assert edited.json()["favorite"] is True

    assert rag_api.delete(f"/api/rag/history/{second}").json() == {"deleted": second}
    assert rag_api.delete(f"/api/rag/history/{second}").status_code == 404
    assert rag_api.patch("/api/rag/history/999999", json={"favorite": True}).status_code == 404
    assert rag_api.get("/api/rag/history").json()["total"] == 1
    assert rag_api.get("/api/rag/history?limit=0").status_code == 422


def test_history_prunes_old_non_favorites(tmp_path: Path):
    hist = RagHistory(tmp_path / "state.db", keep=2)
    fav = hist.add({"n": 1}, {"archetypes": {}})["id"]
    hist.update(fav, favorite=True)  # favourites survive pruning
    ids = [hist.add({"n": 1}, {"archetypes": {}})["id"] for _ in range(4)]
    remaining = {e["id"] for e in hist.list(limit=100)["items"]}
    assert remaining == {fav, ids[2], ids[3]}


def test_lint_endpoint(rag_api: TestClient):
    res = rag_api.post(
        "/api/rag/lint",
        json={"prompt": "masterpiece, (woman:1.4) kneeling", "archetype": "a", "trigger": "tok"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["archetype"] == "tags" and body["clean_prompt"].startswith("tok,")
    assert "masterpiece" not in body["clean_prompt"] and body["warnings"] and body["is_valid"] is True
    assert {"token_count", "token_counter"} <= set(body)
    assert rag_api.post("/api/rag/lint", json={"prompt": "x", "archetype": "z"}).status_code == 422


# ----------------------------------------------------------------------------- Phase H: captions table source, R6, R10
from pipeline.captions import import_caption, record_caption  # noqa: E402
from pipeline.db import DB  # noqa: E402
from pipeline.rag import auto_reindex  # noqa: E402
from pipeline.rag import indexer as indexer_mod  # noqa: E402
from pipeline.rag.evaluation import evaluate_retrieval  # noqa: E402
from pipeline.rag.indexer import TABLE_SOURCE, split_for_key  # noqa: E402

TABLE_CAPTIONS = {
    "vid_rope": [
        f"An adult woman kneels on a concrete floor, her arms bound in hemp rope in a box tie, take {i}. "
        "Chiaroscuro spotlight from the left." for i in range(10)
    ],
    "vid_chain": [
        f"An adult woman stands in a studio void with steel chains and chrome handcuffs, take {i}. "
        "Soft light and shallow depth of field." for i in range(10)
    ],
    "vid_collar": [
        f"An adult woman lies prone on a wooden floor in a loft wearing a leather collar, take {i}. "
        "Window light and bokeh." for i in range(10)
    ],
}


def _seed_table(settings: Settings, captions_by_video: dict[str, list[str]], export: bool = True) -> tuple[DB, dict]:
    """state.db with videos/chunks/frames/picks/exports + captions rows (vlm) in temp dirs; returns frame ids."""
    settings.ensure_dirs()
    db = DB(settings.db_path)
    merged = settings.finished_dir / "merged"
    merged.mkdir(parents=True, exist_ok=True)
    frame_ids: dict[str, list[int]] = {}
    with db.write() as conn:
        for vid, texts in captions_by_video.items():
            conn.execute(
                "INSERT INTO videos (id, path, name, slug, status) VALUES (?, ?, ?, ?, 'exported')",
                (vid, f"/nonexistent/{vid}.mp4", f"{vid}.mp4", vid),
            )
            cid = conn.execute(
                "INSERT INTO chunks (video_id, position, start_s, end_s) VALUES (?, 0, 0, 60)", (vid,)
            ).lastrowid
            frames_dir = settings.cache_dir / "candidates" / vid
            frames_dir.mkdir(parents=True, exist_ok=True)
            for i, text in enumerate(texts):
                full = frames_dir / f"full_{i}.jpg"
                full.write_bytes(b"\xff\xd8\xff\xe0fixture")
                fid = conn.execute(
                    "INSERT INTO frames (video_id, chunk_id, t, full_path) VALUES (?, ?, ?, ?)",
                    (vid, cid, 1.0 + i, str(full)),
                ).lastrowid
                conn.execute("INSERT INTO picks (frame_id, chunk_id, role, state) VALUES (?, ?, 'pick', 'keep')", (fid, cid))
                record_caption(conn, fid, vid, text, model="m", layer="layer1", mode="concept")
                if export:
                    png = merged / f"{vid}_c01_{int((1.0 + i) * 1000):07d}.png"
                    png.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
                    conn.execute(
                        "INSERT INTO exports (frame_id, per_video_path, merged_path, exported_at) VALUES (?, ?, ?, ?)",
                        (fid, None, str(png), time.time()),
                    )
                frame_ids.setdefault(vid, []).append(fid)
    return db, frame_ids


def _rows(settings: Settings) -> list[dict]:
    import sqlite3

    db_path, _ = indexer_mod.rag_index_paths(settings)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM rag_captions ORDER BY id")]


def test_captions_table_is_primary_source_without_stubs_or_rejects(rag_settings: Settings):
    db, fids = _seed_table(rag_settings, {"vid_a": [CAPTIONS["vid_a_0001"], CAPTIONS["vid_a_0002"]]}, export=False)
    _write_pairs(rag_settings.curated_dir / "train", {"c_0001": CAPTIONS["vid_b_0001"]})  # fallback is ignored
    with db.write() as conn:
        # a second video: one imported caption (with export), one stub, one caption on a rejected frame
        conn.execute("INSERT INTO videos (id, path, name, slug) VALUES ('vid_b', '/x/b.mp4', 'b.mp4', 'vid_b')")
        cid = conn.execute("INSERT INTO chunks (video_id, start_s, end_s) VALUES ('vid_b', 0, 9)").lastrowid
        ids = [conn.execute("INSERT INTO frames (video_id, chunk_id, t) VALUES ('vid_b', ?, ?)", (cid, t)).lastrowid
               for t in (1.0, 2.0, 3.0)]
        conn.execute("INSERT INTO picks (frame_id, chunk_id, role, state) VALUES (?, ?, 'pick', 'reject')", (ids[2], cid))
        import_caption(conn, ids[0], "vid_b", CAPTIONS["vid_b_0001"], source="imported")
        import_caption(conn, ids[1], "vid_b", "restrained_elegance, rope", source="stub")
        record_caption(conn, ids[2], "vid_b", CAPTIONS["vid_c_0001"])
        png = rag_settings.finished_dir / "merged" / "vid_b_c01_0001000.png"
        png.parent.mkdir(parents=True, exist_ok=True)
        png.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
        conn.execute("INSERT INTO exports (frame_id, merged_path) VALUES (?, ?)", (ids[0], str(png)))

    sources = resolve_caption_sources(rag_settings)
    assert sources["source"] == TABLE_SOURCE and sources["caption_files"] == 3
    res = RagCaptionIndexer(rag_settings, classifier=FakeClassifier()).build_index()
    assert res["indexed"] == 3 and res["source"] == TABLE_SOURCE

    rows = {r["frame_id"]: r for r in _rows(rag_settings)}
    assert set(rows) == {*fids["vid_a"], ids[0]}
    assert rows[ids[0]]["caption_source"] == "imported" and rows[ids[0]]["source_id"] == "vid_b"
    assert rows[ids[0]]["stem"] == "vid_b_c01_0001000"
    assert rows[ids[0]]["img_rel_path"] == "data/finished/merged/vid_b_c01_0001000.png"  # export, project-relative
    first = rows[fids["vid_a"][0]]
    assert first["stem"] == f"vid_a_f{fids['vid_a'][0]}" and first["img_rel_path"].endswith("full_0.jpg")
    assert first["split"] in {"train", "val"} and first["split"] == split_for_key(f"frame:{fids['vid_a'][0]}")
    assert all(r["content_hash"] for r in rows.values())

    hits = RagRetriever(rag_settings, classifier=FakeClassifier()).retrieve({}, "hemp rope box tie", top_k=1)
    assert hits[0]["frame_id"] in fids["vid_a"] and hits[0]["source_id"] == "vid_a"


def test_empty_captions_table_falls_back_to_files(curated: Settings):
    DB(curated.db_path)  # schema with an empty captions table
    assert resolve_caption_sources(curated)["source"] == "curated"
    with DB(curated.db_path).write() as conn:  # stubs alone do not count
        conn.execute("INSERT INTO videos (id, path, name, slug) VALUES ('v', '/x', 'x', 'v')")
        fid = conn.execute("INSERT INTO frames (video_id, t) VALUES ('v', 1.0)").lastrowid
        import_caption(conn, fid, "v", "restrained_elegance, rope", source="stub")
    assert resolve_caption_sources(curated)["source"] == "curated"
    assert "captions table" in resolve_caption_sources(Settings(
        video_dir=curated.video_dir, cache_dir=curated.cache_dir / "other", finished_dir=curated.finished_dir / "x" / "y"
    ))["searched"][0]


def test_preview_serves_exported_png_and_cache_jpeg(rag_api: TestClient, rag_settings: Settings):
    db, fids = _seed_table(rag_settings, {"vid_a": [CAPTIONS["vid_a_0001"]]}, export=True)
    with db.write() as conn:  # second frame without export -> preview falls back to the cached frame
        cid = conn.execute("SELECT id FROM chunks WHERE video_id='vid_a'").fetchone()[0]
        full = rag_settings.cache_dir / "candidates" / "vid_a" / "full_x.jpg"
        full.write_bytes(b"\xff\xd8\xff\xe0fixture")
        fid = conn.execute(
            "INSERT INTO frames (video_id, chunk_id, t, full_path) VALUES ('vid_a', ?, 9.0, ?)", (cid, str(full))
        ).lastrowid
        record_caption(conn, fid, "vid_a", CAPTIONS["vid_b_0001"])
    rag_api.post("/api/rag/index", json={})
    assert _wait_for_build(rag_api)["index"]["source"] == TABLE_SOURCE
    by_frame = {r["frame_id"]: r["id"] for r in _rows(rag_settings)}
    png = rag_api.get(f"/api/rag/preview/{by_frame[fids['vid_a'][0]]}")
    assert png.status_code == 200 and png.headers["content-type"] == "image/png"
    jpg = rag_api.get(f"/api/rag/preview/{by_frame[fid]}")
    assert jpg.status_code == 200 and jpg.headers["content-type"] == "image/jpeg"


def test_incremental_update_embeds_only_new_and_changed(rag_settings: Settings):
    db, fids = _seed_table(rag_settings, {"vid_a": [CAPTIONS["vid_a_0001"], CAPTIONS["vid_a_0002"]],
                                          "vid_b": [CAPTIONS["vid_b_0001"]]})
    RagCaptionIndexer(rag_settings, classifier=FakeClassifier()).build_index()
    retriever = RagRetriever(rag_settings, classifier=FakeClassifier())
    assert retriever.count() == 3
    old_rows = {r["frame_id"]: r for r in _rows(rag_settings)}

    with db.write() as conn:
        cid = conn.execute("SELECT id FROM chunks WHERE video_id='vid_b'").fetchone()[0]
        new_fid = conn.execute("INSERT INTO frames (video_id, chunk_id, t) VALUES ('vid_b', ?, 7.0)", (cid,)).lastrowid
        record_caption(conn, new_fid, "vid_b", CAPTIONS["vid_c_0001"])  # new
        record_caption(conn, fids["vid_a"][1], "vid_a", "An adult woman in a leather collar, re-captioned.")  # changed
        conn.execute("DELETE FROM frames WHERE id=?", (fids["vid_b"][0],))  # removed (caption cascades)

    clf = CountingClassifier()
    time.sleep(0.01)
    res = RagCaptionIndexer(rag_settings, classifier=clf).build_index(incremental=True)
    assert res["mode"] == "incremental", res
    assert (res["added"], res["updated"], res["removed"], res["indexed"], res["count"]) == (1, 1, 1, 2, 3)
    new_texts = [CAPTIONS["vid_c_0001"], "An adult woman in a leather collar, re-captioned."]
    embedded = [t for call in clf.calls for t in call]
    # whole captions + their R1 domain chunks, nothing of the untouched rows
    assert set(new_texts) <= set(embedded)
    assert set(embedded) - set(new_texts) <= {c for t in new_texts for c in _domain_chunks(t)}
    rows = {r["frame_id"]: r for r in _rows(rag_settings)}
    assert set(rows) == {fids["vid_a"][0], fids["vid_a"][1], new_fid}
    assert rows[fids["vid_a"][0]]["id"] == old_rows[fids["vid_a"][0]]["id"]  # untouched row keeps its id

    # The running retriever hot-reloads (npz mtime changed) and the embeddings stay aligned with the rows
    assert retriever.count() == 3
    hits = retriever.retrieve({}, "leather collar wooden floor loft window", top_k=1, diversity=1.0)
    assert hits[0]["frame_id"] in {new_fid, fids["vid_a"][1]}
    captions, embeddings = retriever.load()
    expected = FakeClassifier().embed_texts([c["full_caption"] for c in captions])
    assert np.allclose(embeddings, expected, atol=1e-6)
    assert retriever.retrieve({}, "chrome handcuffs", top_k=3)  # FTS rebuilt for the new rows

    # Nothing new -> no embedding calls, index untouched
    clf2 = CountingClassifier()
    noop = RagCaptionIndexer(rag_settings, classifier=clf2).build_index(incremental=True)
    assert noop["indexed"] == 0 and noop["count"] == 3 and clf2.calls == []


def test_incremental_falls_back_to_full_build(curated: Settings):
    clf = FakeClassifier()
    first = RagCaptionIndexer(curated, classifier=clf).build_index(incremental=True)
    assert first["mode"] == "full" and first["full_reason"] == "no index yet" and first["source"] == "curated"
    _seed_table(curated, {"vid_a": [CAPTIONS["vid_a_0001"]]})
    switched = RagCaptionIndexer(curated, classifier=clf).build_index(incremental=True)
    assert switched["mode"] == "full" and "source changed" in switched["full_reason"]
    assert switched["source"] == TABLE_SOURCE and switched["count"] == 1

    class OtherModel(FakeClassifier):
        repo = "fake/other"

    assert RagCaptionIndexer(curated, classifier=OtherModel()).build_index(incremental=True)["full_reason"] == (
        "embedding model changed"
    )


def test_split_is_deterministic_and_about_ten_percent():
    keys = [f"frame:{i}" for i in range(4000)]
    splits = [split_for_key(k) for k in keys]
    assert splits == [split_for_key(k) for k in keys]
    assert set(splits) == {"train", "val"}
    assert 0.08 < splits.count("val") / len(splits) < 0.12
    assert split_for_key("frame:1", val_fraction=0.0) == "train" and split_for_key("frame:1", val_fraction=1.0) == "val"


def test_index_job_queues_incremental_behind_running_build(rag_settings: Settings):
    import threading

    _seed_table(rag_settings, {"vid_a": [CAPTIONS["vid_a_0001"]]})
    gate = threading.Event()

    class SlowClassifier(FakeClassifier):
        def embed_texts(self, texts):
            gate.wait(5)
            return super().embed_texts(texts)

    finished: list[dict] = []
    job = RagIndexJob(rag_settings, classifier=SlowClassifier(), on_finished=finished.append)
    assert job.start() is True
    assert job.request_incremental() is True and job.request_incremental() is True
    assert job.build_state()["pending"] is True
    gate.set()
    deadline = time.time() + 10
    while time.time() < deadline and (len(finished) < 2 or job.running):
        time.sleep(0.02)
    state = job.build_state()
    assert [r["mode"] for r in finished] == ["full", "incremental"]  # queued requests collapse into one run
    assert state["state"] == "done" and state["mode"] == "incremental" and state["pending"] is False


def test_auto_reindex_hook_registry():
    calls: list[dict] = []

    class Manager:
        pass

    manager, other = Manager(), Manager()
    auto_reindex.register(manager, lambda **kw: calls.append(kw))
    assert auto_reindex.notify_caption_job_finished(manager, job_id=1, video_id="v", captioned=2) is True
    assert calls == [{"job_id": 1, "video_id": "v", "captioned": 2}]
    assert auto_reindex.notify_caption_job_finished(other, job_id=2, video_id="v") is False

    def boom(**_kw):
        raise RuntimeError("index broken")

    auto_reindex.register(manager, boom)
    assert auto_reindex.notify_caption_job_finished(manager, job_id=3, video_id="v") is False  # never raises
    auto_reindex.unregister(manager)
    assert auto_reindex.notify_caption_job_finished(manager, job_id=4, video_id="v") is False


def test_caption_job_success_triggers_incremental_reindex(rag_settings: Settings, monkeypatch: pytest.MonkeyPatch):
    """End to end: a (mocked) caption job writes a captions row, the hook indexes just that caption."""
    import cv2

    from pipeline.jobs import JobManager

    db, fids = _seed_table(rag_settings, {"vid_a": [CAPTIONS["vid_a_0001"]]})
    clf = FakeClassifier()
    RagCaptionIndexer(rag_settings, classifier=clf).build_index()
    with db.write() as conn:  # one more approved frame without a caption
        cid = conn.execute("SELECT id FROM chunks WHERE video_id='vid_a'").fetchone()[0]
        png = rag_settings.cache_dir / "candidates" / "vid_a" / "full_new.png"
        cv2.imwrite(str(png), np.full((64, 96, 3), 60, dtype=np.uint8))
        new_fid = conn.execute(
            "INSERT INTO frames (video_id, chunk_id, t, metrics, zs_scores, full_path) VALUES ('vid_a', ?, 5.0, '{}', '{}', ?)",
            (cid, str(png)),
        ).lastrowid
        conn.execute("INSERT INTO picks (frame_id, chunk_id, role, state) VALUES (?, ?, 'pick', 'keep')", (new_fid, cid))

    jm = JobManager(db, rag_settings)
    held: list = []
    jm.caption_executor.submit = lambda fn, *args: held.append((fn, args))
    jm.broadcast_event = lambda kind, data: None

    async def fake_caption(**_kwargs):
        return CAPTIONS["vid_c_0001"], []

    jm.captioner.caption_single_frame = fake_caption
    retriever = RagRetriever(rag_settings, classifier=clf)
    index_job = RagIndexJob(rag_settings, classifier=clf, on_finished=lambda _r: retriever.invalidate())
    seen: list[dict] = []

    def handler(**kw):
        seen.append(kw)
        index_job.request_incremental()

    auto_reindex.register(jm, handler)
    try:
        job_id = jm.submit_openrouter_caption_job("vid_a", {"api_key": "k", "trigger": "restrained_elegance"})
        for fn, args in held:
            fn(*args)
    finally:
        auto_reindex.unregister(jm)
        jm.executor.shutdown(wait=False)
    # the seeded frame's fixture JPEG cannot be rendered, so only the new frame counts as captioned
    assert seen == [{"job_id": job_id, "video_id": "vid_a", "captioned": 1}]
    deadline = time.time() + 10
    while time.time() < deadline and index_job.build_state()["state"] in {"idle", "running"}:
        time.sleep(0.02)
    result = index_job.build_state()["result"]
    assert result["mode"] == "incremental" and result["added"] == 1 and result["count"] == 2
    assert {r["frame_id"] for r in _rows(rag_settings)} == {fids["vid_a"][0], new_fid}


def test_api_hook_respects_setting_and_incremental_route(rag_api: TestClient, rag_settings: Settings,
                                                         monkeypatch: pytest.MonkeyPatch):
    db, _ = _seed_table(rag_settings, {"vid_a": [CAPTIONS["vid_a_0001"]]})
    monkeypatch.setattr(api, "db", db)
    assert rag_api.get("/api/rag/status").json()["auto_reindex"] is True

    db.set_setting("user_settings", {"rag_auto_reindex": False})
    api._rag_after_caption_job(job_id=1, video_id="vid_a", captioned=1)
    assert api.rag_index_job.build_state()["state"] == "idle"
    assert rag_api.get("/api/rag/status").json()["auto_reindex"] is False

    db.set_setting("user_settings", {"rag_auto_reindex": True})
    api._rag_after_caption_job(job_id=1, video_id="vid_a", captioned=1)
    status = _wait_for_build(rag_api)
    assert status["build"]["state"] == "done" and status["build"]["mode"] == "incremental"
    assert status["build"]["result"]["full_reason"] == "no index yet" and status["indexed"] == 1

    with db.write() as conn:
        fid = conn.execute("INSERT INTO frames (video_id, t) VALUES ('vid_a', 8.0)").lastrowid
        record_caption(conn, fid, "vid_a", CAPTIONS["vid_b_0001"])
    assert rag_api.post("/api/rag/index", json={"incremental": True}).status_code == 200
    status = _wait_for_build(rag_api)
    assert status["build"]["result"]["mode"] == "incremental" and status["build"]["result"]["added"] == 1
    assert status["indexed"] == 2 and status["index"]["source"] == TABLE_SOURCE
    hits = rag_api.post("/api/rag/retrieve", json={"user_description": "chrome handcuffs", "top_k": 1}).json()
    assert hits["results"][0]["frame_id"] == fid


def test_eval_metrics_on_val_split(rag_settings: Settings, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(indexer_mod, "VAL_FRACTION", 0.3)
    _seed_table(rag_settings, TABLE_CAPTIONS)
    RagCaptionIndexer(rag_settings, classifier=FakeClassifier()).build_index()
    retriever = RagRetriever(rag_settings, classifier=FakeClassifier())
    res = evaluate_retrieval(retriever, k=4)
    assert res["queries"] == res["split"]["val"] > 0 and res["split"]["val"] + res["split"]["candidates"] == 30
    sib, tags = res["siblings"], res["tags"]
    assert sib["queries"] == res["queries"]
    # Distinct vocabulary per video: siblings are found far above chance
    assert sib["recall_at_k"] == 1.0 and sib["hit_at_k"] == 1.0 and sib["mrr"] == 1.0
    assert sib["chance_recall_at_k"] < 0.5
    assert tags["queries"] > 0 and 0.0 < tags["precision_at_k"] <= 1.0 and 0.0 < tags["recall_at_k"] <= 1.0
    again = evaluate_retrieval(retriever, k=4)
    assert {**again, "duration": 0} == {**res, "duration": 0}  # deterministic
    assert evaluate_retrieval(retriever, k=2, max_queries=1)["queries"] == 1
    assert evaluate_retrieval(retriever, k=4, fusion="linear")["siblings"]["recall_at_k"] is not None


def test_eval_route(rag_api: TestClient, rag_settings: Settings, monkeypatch: pytest.MonkeyPatch):
    assert rag_api.get("/api/rag/eval").status_code == 404
    monkeypatch.setattr(indexer_mod, "VAL_FRACTION", 0.3)
    _seed_table(rag_settings, TABLE_CAPTIONS)
    rag_api.post("/api/rag/index", json={})
    assert _wait_for_build(rag_api)["ready"] is True
    body = rag_api.get("/api/rag/eval?k=3&fusion=linear").json()
    assert body["k"] == 3 and body["fusion"] == "linear" and body["index"]["count"] == 30
    assert {"siblings", "tags", "split", "queries", "stage", "query_words"} <= set(body)
    assert rag_api.get("/api/rag/eval?fusion=nope").status_code == 422


# ----------------------------------------------------------------------------- R1 per-domain embeddings
from pipeline.rag.image_embedder import (  # noqa: E402
    AutoImageEmbedder,
    ClipImageEmbedder,
    default_image_embedder,
    siglip_preprocess_bgr,
)
from pipeline.rag.indexer import (  # noqa: E402
    DOMAIN_CHUNK_WORDS,
    chunk_text,
    domain_segments,
    image_index_path,
    load_image_index,
)


def _domain_chunks(caption: str) -> list[str]:
    segments = RagCaptionIndexer.segment_caption(caption)
    record = {col: segments[key] for col, key in zip(
        ("equipment_text", "surroundings_text", "position_text", "lighting_text"),
        ("equipment", "surroundings", "position", "camera_lighting"),
    )}
    return [chunk for _, chunk in domain_segments(record)]


class TruncatingClassifier(FakeClassifier):
    """Bag of words over the first ``max_words`` words only, like CLIP's 77-token context."""

    repo = "fake/truncating"

    def __init__(self, max_words: int = 12) -> None:
        self.max_words = max_words
        self.calls: list[list[str]] = []

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        self.calls.append(list(texts))
        return super().embed_texts([" ".join(t.split()[: self.max_words]) for t in texts])


# Same generic opening; the distinguishing equipment/surroundings/lighting only come after ~20 words
LONG_OPENING = ("An adult woman with long dark hair kneels upright with her knees apart and her gaze lowered "
                "toward the floor in front of her, calm and focused.")
LONG_CAPTIONS = {
    f"{vid}_{i:04d}": f"{LONG_OPENING} {detail}"
    for vid, detail in (
        ("vid_jute", "Natural jute rope forms a box tie across her chest. The scene is set in a concrete basement. "
                     "Hard spotlight light creates chiaroscuro shadows."),
        ("vid_chain", "Heavy steel chains run to chrome handcuffs on her wrists. The scene is set in a brick loft. "
                      "Soft window light with gentle reflections."),
        ("vid_collar", "A black leather collar is buckled at her neck. The scene is set in a white studio void. "
                       "Softbox lighting gives clean highlights."),
    )
    for i in range(3)
}


def test_chunk_text_and_domain_segments():
    assert chunk_text("") == []
    assert chunk_text("One two. Three four.", max_words=3) == ["One two.", "Three four."]
    assert chunk_text("One two. Three.", max_words=3) == ["One two. Three."]
    long_sentence = " ".join(f"w{i}" for i in range(95)) + "."
    chunks = chunk_text(long_sentence)
    assert [len(c.split()) for c in chunks] == [DOMAIN_CHUNK_WORDS, DOMAIN_CHUNK_WORDS, 15]
    rows = domain_segments({"equipment_text": "Hemp rope.", "surroundings_text": "", "position_text": "Kneels.",
                            "lighting_text": long_sentence})
    assert [d for d, _ in rows] == [0, 2, 3, 3, 3]
    assert all(len(text.split()) <= DOMAIN_CHUNK_WORDS for _, text in rows)


def test_domain_vectors_stored_and_beat_truncated_whole_caption(rag_settings: Settings):
    _write_pairs(rag_settings.curated_dir / "train", LONG_CAPTIONS)
    clf = TruncatingClassifier()
    res = RagCaptionIndexer(rag_settings, classifier=clf).build_index()
    assert res["indexed"] == 9 and res["domain_segments"] >= 9 * 3
    _, npz_path = indexer_mod.rag_index_paths(rag_settings)
    with np.load(npz_path) as npz:
        assert {"ids", "embeddings", "seg_ids", "seg_domains", "seg_embeddings"} <= set(npz.files)
        assert set(npz["seg_ids"].tolist()) == set(npz["ids"].tolist())
        assert npz["seg_embeddings"].shape == (res["domain_segments"], npz["embeddings"].shape[1])
        assert set(npz["seg_domains"].tolist()) <= {0, 1, 2, 3}

    retriever = RagRetriever(rag_settings, classifier=clf)
    retriever.load()
    assert retriever.domain_ready and retriever.effective_dense_mode(None) == "domain"
    assert retriever.effective_dense_mode("whole") == "whole"
    with pytest.raises(ValueError):
        retriever.effective_dense_mode("nope")
    query = "Heavy steel chains run to chrome handcuffs on her wrists."
    # whole-caption vectors only saw the shared opening: all 9 captions tie
    _, _, whole, _ = retriever.score({}, query, fusion="linear", dense_mode="whole")
    assert np.ptp(whole) < 1e-6
    captions, _, domain, _ = retriever.score({}, query, fusion="linear", dense_mode="domain")
    top3 = {captions[i]["source_id"] for i in np.argsort(-domain)[:3]}
    assert top3 == {"vid_chain"}
    # selected tags are scored against their own domain (and weighted up)
    hits = retriever.retrieve({"equipment": ["posture_collar"]}, "", top_k=3, diversity=1.0, dense_mode="domain")
    assert {h["source_id"] for h in hits} == {"vid_collar"}


def test_domain_query_parts_split_description_and_weight_tag_domains():
    parts, weights = RagRetriever.domain_query_parts(
        {"equipment": ["hemp_rope"]}, "Kneeling in a concrete basement. Hard spotlight from above."
    )
    assert set(parts) == {0, 1, 3} and "hemp" in parts[0].lower()
    assert weights[0] > weights[1] == weights[3] == 1.0


def _strip_domain_vectors(npz_path: Path) -> None:
    with np.load(npz_path) as npz:
        ids, emb = npz["ids"], npz["embeddings"]
    np.savez_compressed(npz_path, ids=ids, embeddings=emb)


def test_old_index_without_domain_vectors_falls_back_then_incremental_backfills(rag_api: TestClient,
                                                                              rag_settings: Settings):
    _write_pairs(rag_settings.curated_dir / "train", LONG_CAPTIONS)
    RagCaptionIndexer(rag_settings, classifier=TruncatingClassifier()).build_index()
    _, npz_path = indexer_mod.rag_index_paths(rag_settings)
    time.sleep(0.01)
    _strip_domain_vectors(npz_path)  # an index written before R1

    retriever = RagRetriever(rag_settings, classifier=TruncatingClassifier())
    assert retriever.has_domain_embeddings() is False
    assert retriever.effective_dense_mode("domain") == "whole"
    assert len(retriever.retrieve({}, "steel chains", top_k=2, dense_mode="domain")) == 2
    api.rag_retriever = retriever  # rag_api restores the original after the test
    status = rag_api.get("/api/rag/status").json()["retrieval"]
    assert status["dense_mode"] == "whole" and status["domain_embeddings"] is False
    assert status["dense_modes"] == ["whole", "domain"]
    body = rag_api.post("/api/rag/retrieve", json={"user_description": "chains", "dense_mode": "domain"}).json()
    assert body["dense_mode"] == "whole" and body["results"]
    assert rag_api.post("/api/rag/retrieve", json={"dense_mode": "bogus"}).status_code == 422

    clf = TruncatingClassifier()
    time.sleep(0.01)
    res = RagCaptionIndexer(rag_settings, classifier=clf).build_index(incremental=True)
    assert res["mode"] == "incremental" and res["indexed"] == 0 and res["domain_backfilled"] == 9
    embedded = {t for call in clf.calls for t in call}
    assert not embedded & set(LONG_CAPTIONS.values())  # whole captions are not re-embedded
    assert embedded == {c for t in LONG_CAPTIONS.values() for c in _domain_chunks(t)}
    assert retriever.has_domain_embeddings() and retriever.effective_dense_mode(None) == "domain"
    assert rag_api.get("/api/rag/status").json()["retrieval"]["dense_mode"] == "domain"
    noop = RagCaptionIndexer(rag_settings, classifier=clf).build_index(incremental=True)
    assert noop["indexed"] == 0 and "domain_backfilled" not in noop  # nothing left to do


def test_eval_compares_dense_modes(rag_api: TestClient, rag_settings: Settings, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(indexer_mod, "VAL_FRACTION", 0.3)
    _seed_table(rag_settings, TABLE_CAPTIONS)
    clf = TruncatingClassifier(max_words=3)  # "An adult woman": the whole-caption vectors all tie
    api.rag_retriever = RagRetriever(rag_settings, classifier=clf)
    RagCaptionIndexer(rag_settings, classifier=clf).build_index()
    whole = evaluate_retrieval(api.rag_retriever, k=4, fusion="linear", query_words=0, dense_mode="whole")
    domain = evaluate_retrieval(api.rag_retriever, k=4, fusion="linear", query_words=0, dense_mode="domain")
    assert whole["dense_mode"] == "whole" and domain["dense_mode"] == "domain"
    assert domain["siblings"]["recall_at_k"] > whole["siblings"]["recall_at_k"]
    body = rag_api.get("/api/rag/eval?fusion=linear&query_words=0&dense_mode=whole").json()
    assert body["dense_mode"] == "whole" and body["siblings"] == whole["siblings"]
    assert rag_api.get("/api/rag/eval").json()["dense_mode"] == "domain"
    assert rag_api.get("/api/rag/eval?dense_mode=x").status_code == 422


# ----------------------------------------------------------------------------- R3 image embeddings + /similar
COLORS = {"vid_rope": (40, 40, 200), "vid_chain": (40, 200, 40), "vid_collar": (200, 40, 40)}


class FakeImageEmbedder:
    """Mean colour + brightness of the image (normalised); counts the images it embeds."""

    def __init__(self, name: str = "siglip") -> None:
        self.name = name
        self.model_id = f"fake/{name}"
        self.embedded = 0

    def embed_images(self, frames_bgr: list[np.ndarray]) -> np.ndarray:
        self.embedded += len(frames_bgr)
        out = np.array([[*f.reshape(-1, 3).mean(axis=0), 30.0] for f in frames_bgr], dtype=np.float32)
        return out / np.linalg.norm(out, axis=1, keepdims=True)


def _paint_images(settings: Settings, db: DB, seed: int = 0) -> None:
    """Replace the fixture bytes of every frame / export image with a decodable image in the video's colour."""
    import cv2

    import sqlite3

    rng = np.random.default_rng(seed)

    with sqlite3.connect(settings.db_path) as conn:
        rows = conn.execute(
            "SELECT f.video_id, f.full_path, e.merged_path FROM frames f LEFT JOIN exports e ON e.frame_id = f.id"
        ).fetchall()
    for video_id, full_path, merged_path in rows:
        color = np.array(COLORS.get(video_id, (128, 128, 128)), dtype=np.int16)
        for p in (full_path, merged_path):
            if p:
                jitter = rng.integers(-10, 10, size=3)
                img = np.clip(np.full((24, 32, 3), color + jitter), 0, 255).astype(np.uint8)
                cv2.imwrite(str(p), img)


def _image_api(monkeypatch: pytest.MonkeyPatch, rag_settings: Settings, db: DB, embedder) -> TestClient:
    clf = FakeClassifier()
    retriever = RagRetriever(rag_settings, classifier=clf)
    job = RagIndexJob(rag_settings, classifier=clf, image_embedder=embedder,
                      on_finished=lambda _r: retriever.invalidate())
    monkeypatch.setattr(api, "settings", rag_settings)
    monkeypatch.setattr(api, "db", db)
    monkeypatch.setattr(api, "rag_retriever", retriever)
    monkeypatch.setattr(api, "rag_index_job", job)
    monkeypatch.setattr(api, "_openrouter_key", lambda explicit: "")
    return TestClient(api.app)


def test_similar_endpoint_contract(monkeypatch: pytest.MonkeyPatch, rag_settings: Settings):
    db, fids = _seed_table(rag_settings, {vid: texts[:4] for vid, texts in TABLE_CAPTIONS.items()})
    _paint_images(rag_settings, db)
    with db.write() as conn:  # a frame without any image on disk
        cid = conn.execute("SELECT id FROM chunks WHERE video_id='vid_rope'").fetchone()[0]
        no_image = conn.execute(
            "INSERT INTO frames (video_id, chunk_id, t, full_path) VALUES ('vid_rope', ?, 99.0, '/nonexistent/x.jpg')",
            (cid,),
        ).lastrowid
    embedder = FakeImageEmbedder("siglip")
    client = _image_api(monkeypatch, rag_settings, db, embedder)

    assert client.get("/api/rag/similar?frame_id=999999").status_code == 404
    assert client.get(f"/api/rag/similar?frame_id={no_image}").status_code == 404
    query_frame = fids["vid_chain"][0]
    empty = client.get(f"/api/rag/similar?frame_id={query_frame}")
    assert empty.status_code == 200
    assert empty.json() == {"results": [], "index_ready": False, "embedder": "siglip"}
    assert client.get("/api/rag/status").json()["retrieval"]["image_index"]["ready"] is False

    client.post("/api/rag/index", json={})
    status = _wait_for_build(client)
    assert status["build"]["result"]["image_index"]["count"] == 12
    image_status = status["retrieval"]["image_index"]
    assert image_status["ready"] is True and image_status["count"] == 12 and image_status["embedder"] == "siglip"
    assert image_status["enabled"] is True

    body = client.get(f"/api/rag/similar?frame_id={query_frame}&top_k=3").json()
    assert body["index_ready"] is True and body["embedder"] == "siglip" and len(body["results"]) == 3
    assert all(r["source_id"] == "vid_chain" for r in body["results"])
    assert query_frame not in {r["frame_id"] for r in body["results"]}  # the frame itself is skipped
    retrieve_item = client.post("/api/rag/retrieve", json={"user_description": "chains", "top_k": 1}).json()["results"][0]
    assert set(body["results"][0]) == set(retrieve_item)
    assert 0.0 <= body["results"][0]["score"] <= 1.0
    assert len(client.get(f"/api/rag/similar?frame_id={query_frame}").json()["results"]) == 8  # default top_k
    assert client.get(f"/api/rag/similar?frame_id={query_frame}&top_k=0").status_code == 422
    assert client.get("/api/rag/similar").status_code == 422


def test_similar_embedder_mismatch_is_503(monkeypatch: pytest.MonkeyPatch, rag_settings: Settings):
    db, fids = _seed_table(rag_settings, {"vid_rope": TABLE_CAPTIONS["vid_rope"][:2]})
    _paint_images(rag_settings, db)
    RagCaptionIndexer(rag_settings, classifier=FakeClassifier(), image_embedder=FakeImageEmbedder("clip")).build_index()
    client = _image_api(monkeypatch, rag_settings, db, FakeImageEmbedder("siglip"))
    res = client.get(f"/api/rag/similar?frame_id={fids['vid_rope'][0]}")
    assert res.status_code == 503 and "Rebuild" in res.json()["detail"]


def test_incremental_maintains_domain_and_image_arrays(rag_settings: Settings):
    db, fids = _seed_table(rag_settings, {"vid_rope": TABLE_CAPTIONS["vid_rope"][:3],
                                          "vid_chain": TABLE_CAPTIONS["vid_chain"][:2]})
    _paint_images(rag_settings, db)
    embedder = FakeImageEmbedder()
    first = RagCaptionIndexer(rag_settings, classifier=FakeClassifier(), image_embedder=embedder).build_index()
    assert first["image_index"] == {"enabled": True, "embedder": "siglip", "count": 5, "missing": 0, "error": None}
    assert embedder.embedded == 5

    with db.write() as conn:
        cid = conn.execute("SELECT id FROM chunks WHERE video_id='vid_chain'").fetchone()[0]
        img = rag_settings.cache_dir / "candidates" / "vid_chain" / "full_new.jpg"
        new_fid = conn.execute(
            "INSERT INTO frames (video_id, chunk_id, t, full_path) VALUES ('vid_chain', ?, 50.0, ?)", (cid, str(img))
        ).lastrowid
        record_caption(conn, new_fid, "vid_chain", TABLE_CAPTIONS["vid_chain"][5])  # new
        record_caption(conn, fids["vid_rope"][1], "vid_rope", "An adult woman in a leather collar, re-captioned.")
        conn.execute("DELETE FROM frames WHERE id=?", (fids["vid_rope"][2],))  # removed
    _paint_images(rag_settings, db, seed=1)

    embedder.embedded = 0
    time.sleep(0.01)
    res = RagCaptionIndexer(rag_settings, classifier=FakeClassifier(), image_embedder=embedder).build_index(
        incremental=True
    )
    assert res["mode"] == "incremental" and (res["added"], res["updated"], res["removed"]) == (1, 1, 1)
    assert embedder.embedded == 2  # only the new and the re-captioned row
    assert res["image_index"]["count"] == 5 and res["domain_backfilled"] == 0

    rows = {r["id"]: r for r in _rows(rag_settings)}
    _, npz_path = indexer_mod.rag_index_paths(rag_settings)
    with np.load(npz_path) as npz:
        assert set(npz["ids"].tolist()) == set(rows) == set(npz["seg_ids"].tolist())
        assert len(npz["seg_ids"]) == res["domain_segments"]
    image = load_image_index(image_index_path(rag_settings))
    assert set(image["ids"].tolist()) == set(rows) and image["valid"].all()
    assert image["embedder"] == "siglip" and image["model"] == "fake/siglip"

    retriever = RagRetriever(rag_settings, classifier=FakeClassifier())
    by_frame = {r["frame_id"]: r["id"] for r in rows.values()}
    query = FakeImageEmbedder().embed_images([np.full((4, 4, 3), COLORS["vid_chain"], np.uint8)])[0]
    hits = retriever.similar(query, top_k=3)
    assert {h["source_id"] for h in hits} == {"vid_chain"} and by_frame[new_fid] in {h["id"] for h in hits}

    # nothing changed -> no text or image embedding at all
    embedder.embedded = 0
    clf = CountingClassifier()
    noop = RagCaptionIndexer(rag_settings, classifier=clf, image_embedder=embedder).build_index(incremental=True)
    assert noop["indexed"] == 0 and clf.calls == [] and embedder.embedded == 0

    # another embedder -> every image is re-embedded with it
    other = FakeImageEmbedder("clip")
    switched = RagCaptionIndexer(rag_settings, classifier=FakeClassifier(), image_embedder=other).build_index(
        incremental=True
    )
    assert other.embedded == 5 and switched["image_index"]["embedder"] == "clip"
    assert load_image_index(image_index_path(rag_settings))["embedder"] == "clip"


def test_image_index_setting_and_failures_never_break_the_text_index(rag_settings: Settings):
    db, _ = _seed_table(rag_settings, {"vid_rope": TABLE_CAPTIONS["vid_rope"][:2]})
    _paint_images(rag_settings, db)
    path = image_index_path(rag_settings)

    db.set_setting("user_settings", {"rag_image_index": False})
    embedder = FakeImageEmbedder()
    res = RagCaptionIndexer(rag_settings, classifier=FakeClassifier(), image_embedder=embedder).build_index()
    assert res["indexed"] == 2 and res["image_index"] == {"enabled": False} and not path.exists()
    assert embedder.embedded == 0
    db.set_setting("user_settings", {"rag_image_index": True})

    class Broken(FakeImageEmbedder):
        def embed_images(self, frames_bgr):
            raise RuntimeError("model file corrupt")

    res = RagCaptionIndexer(rag_settings, classifier=FakeClassifier(), image_embedder=Broken()).build_index()
    assert res["indexed"] == 2 and "model file corrupt" in res["image_index"]["error"] and not path.exists()
    assert RagRetriever(rag_settings, classifier=FakeClassifier()).image_index_status()["ready"] is False

    # the default embedder of a stand-in classifier without embed_images is "none" (no model, no download)
    res = RagCaptionIndexer(rag_settings, classifier=FakeClassifier()).build_index()
    assert res["image_index"] == {"enabled": False}


def test_image_embedder_selection_and_offline_fallback(rag_settings: Settings, monkeypatch: pytest.MonkeyPatch):
    from pipeline.classifier import ClipClassifier
    from pipeline.rag import image_embedder as ie

    class WithImages(FakeClassifier):
        def embed_images(self, frames):
            return np.ones((len(frames), 4), np.float32)

    assert default_image_embedder(rag_settings, FakeClassifier()) is None
    assert isinstance(default_image_embedder(rag_settings, WithImages()), ClipImageEmbedder)
    monkeypatch.setenv("CP_MODELS_DIR", str(rag_settings.cache_dir / "models"))
    real = ClipClassifier(rag_settings)  # constructing it loads nothing
    assert isinstance(default_image_embedder(rag_settings, real), AutoImageEmbedder)
    monkeypatch.setenv("CP_RAG_IMAGE_EMBEDDER", "off")
    assert default_image_embedder(rag_settings, real) is None
    monkeypatch.delenv("CP_RAG_IMAGE_EMBEDDER")

    def offline(self):
        raise OSError("offline: no SigLIP in the local cache")

    monkeypatch.setattr(ie.SiglipImageEmbedder, "_resolve_model_path", offline)
    auto = AutoImageEmbedder(rag_settings, WithImages())
    assert auto.name == "siglip"  # preference before the first use
    assert auto.resolve() is auto.clip and auto.name == "clip" and "offline" in auto.fallback_reason
    assert auto.embed_images([np.zeros((8, 8, 3), np.uint8)]).shape == (1, 4)
    assert ie.embedder_for_index(auto, "clip") is auto.clip
    with pytest.raises(OSError):
        ie.embedder_for_index(auto, "siglip")
    assert AutoImageEmbedder(rag_settings, WithImages(), prefer="clip").resolve().name == "clip"

    x = siglip_preprocess_bgr(np.full((30, 50, 3), 255, np.uint8))
    assert x.shape == (3, 224, 224) and np.allclose(x, 1.0)
