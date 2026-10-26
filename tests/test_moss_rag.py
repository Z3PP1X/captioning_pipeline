"""Unit tests for SQLite FTS5 RAG and MOSS Prompt-Expander Engine."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from pipeline.api import app
from pipeline.captions import record_caption
from pipeline.db import DB
from pipeline.moss_validator import scan_trait_leakage
from pipeline.rag.moss_rag import (
    compute_cosine_similarities,
    ensure_moss_tables,
    expand_moss_prompt,
    index_moss_caption,
    retrieve_moss_exemplars,
    sanitize_fts5_query,
)


@pytest.fixture
def temp_db(tmp_path: Path) -> DB:
    """Creates a temporary isolated SQLite database with full schema."""
    db_file = tmp_path / "test_moss_pipeline.sqlite"
    database = DB(db_file)
    ensure_moss_tables(database)
    return database


def test_fts5_table_creation_and_schema(temp_db: DB) -> None:
    """Verifies that FTS5 virtual table and metadata tables are created properly."""
    with temp_db.conn() as conn:
        tables = [
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual')"
            ).fetchall()
        ]
        assert "moss_captions_fts" in tables
        assert "moss_captions_meta" in tables

        # Verify FTS5 columns
        fts_cols = [row["name"] for row in conn.execute("PRAGMA table_info(moss_captions_fts)").fetchall()]
        assert "frame_id" in fts_cols
        assert "video_id" in fts_cols
        assert "trigger" in fts_cols
        assert "mode" in fts_cols
        assert "long" in fts_cols
        assert "medium" in fts_cols
        assert "short" in fts_cols
        assert "tags" in fts_cols


def test_bm25_indexing_and_querying(temp_db: DB) -> None:
    """Verifies indexing and BM25 search ranking on moss_captions_fts."""
    index_moss_caption(
        db=temp_db,
        frame_id=1,
        video_id="video_alpha",
        trigger="ohwx",
        mode="CHARACTER",
        long="A woman in a velvet crimson evening dress walks through a marble ballroom holding champagne.",
        medium="A woman in a crimson dress walking through a ballroom.",
        short="woman in crimson dress",
        tags=["woman", "dress", "ballroom", "crimson"],
    )
    index_moss_caption(
        db=temp_db,
        frame_id=2,
        video_id="video_beta",
        trigger="ohwx",
        mode="CHARACTER",
        long="A cybernetic warrior wearing matte titanium armor stands in a futuristic rainy alleyway.",
        medium="A warrior in titanium armor in a rainy alleyway.",
        short="warrior in titanium armor",
        tags=["warrior", "armor", "cybernetic", "alleyway"],
    )

    # Re-indexing frame 1 should replace without duplication
    index_moss_caption(
        db=temp_db,
        frame_id=1,
        video_id="video_alpha",
        trigger="ohwx",
        mode="CHARACTER",
        long="A woman in an elegant scarlet evening gown walks through a grand marble ballroom.",
        medium="A woman in a scarlet gown walking in a ballroom.",
        short="woman in scarlet gown",
        tags=["woman", "gown", "ballroom", "scarlet"],
    )

    with temp_db.conn() as conn:
        count = conn.execute("SELECT count(*) FROM moss_captions_fts WHERE frame_id = 1").fetchone()[0]
        assert count == 1

    # Query for ballroom
    results = retrieve_moss_exemplars(temp_db, query_text="ballroom gown", top_k=5)
    assert len(results) >= 1
    assert results[0]["frame_id"] == 1
    assert "ballroom" in results[0]["long"]
    assert results[0]["score"] > 0


def test_query_sanitization_and_special_chars() -> None:
    """Verifies that punctuation, special characters, and quotes are sanitized safely."""
    raw = 'woman, smiling! (summer vibes) c++ / "fast:code" *test*'
    sanitized = sanitize_fts5_query(raw)
    assert '"woman"' in sanitized
    assert '"smiling"' in sanitized
    assert '"summer"' in sanitized
    assert '"vibes"' in sanitized
    assert " OR " in sanitized
    assert "*" not in sanitized
    assert "(" not in sanitized
    assert ")" not in sanitized

    # Empty and whitespace inputs
    assert sanitize_fts5_query("") == ""
    assert sanitize_fts5_query("   !?,.  ") == ""


def test_exemplar_retrieval_fuzzy_and_fallbacks(temp_db: DB) -> None:
    """Tests exemplar retrieval with filters, missing terms, and empty query fallbacks."""
    index_moss_caption(
        db=temp_db,
        frame_id=10,
        video_id="vid_gardens",
        trigger="ohwx",
        mode="CHARACTER",
        long="A woman sits in a sunlit botanical garden reading poetry surrounded by blooming orchids.",
        medium="A woman reading in a sunlit botanical garden.",
        short="woman reading in garden",
        tags=["garden", "reading", "orchids"],
    )
    index_moss_caption(
        db=temp_db,
        frame_id=11,
        video_id="vid_city",
        trigger="ohwx",
        mode="CHARACTER",
        long="A woman drinks latte at a bustling outdoor cafe terrace on a rainy avenue.",
        medium="A woman drinking latte at an outdoor cafe.",
        short="woman at cafe terrace",
        tags=["cafe", "latte", "terrace"],
    )

    # Specific video_id filter
    results_garden = retrieve_moss_exemplars(
        temp_db, query_text="woman", video_id="vid_gardens", top_k=5
    )
    assert len(results_garden) == 1
    assert results_garden[0]["frame_id"] == 10

    # Fallback to recent exemplars on empty or unmatched query
    fallback_results = retrieve_moss_exemplars(temp_db, query_text="", top_k=2)
    assert len(fallback_results) == 2


def test_cosine_similarity_retrieval(temp_db: DB) -> None:
    """Tests pure NumPy cosine similarity ranking and embedding fallback."""
    vec1 = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    vec2 = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    vec3 = np.array([0.707, 0.707, 0.0], dtype=np.float32)

    index_moss_caption(
        db=temp_db,
        frame_id=101,
        video_id="vec_test",
        trigger="t1",
        mode="CHARACTER",
        long="Vector fixture 1 description.",
        medium="Vector fixture 1.",
        short="Fixture 1",
        tags=["v1"],
        embedding=vec1,
    )
    index_moss_caption(
        db=temp_db,
        frame_id=102,
        video_id="vec_test",
        trigger="t1",
        mode="CHARACTER",
        long="Vector fixture 2 description.",
        medium="Vector fixture 2.",
        short="Fixture 2",
        tags=["v2"],
        embedding=vec2,
    )

    # Pure vectorized cosine test
    sims = compute_cosine_similarities(vec1, np.stack([vec1, vec2, vec3]))
    assert np.isclose(sims[0], 1.0, atol=1e-4)
    assert np.isclose(sims[1], 0.0, atol=1e-4)
    assert np.isclose(sims[2], 0.707, atol=1e-3)

    # Retrieval using query embedding fallback
    retrieved = retrieve_moss_exemplars(
        temp_db,
        query_text="unmatched_query_xyz",
        query_embedding=vec1,
        top_k=2,
    )
    assert len(retrieved) >= 1
    assert retrieved[0]["frame_id"] == 101


def test_deterministic_prompt_expansion_character(temp_db: DB) -> None:
    """Verifies deterministic 3-tier expansion, word count budgets, trigger injection, and locked traits absence."""
    index_moss_caption(
        db=temp_db,
        frame_id=201,
        video_id="v_park",
        trigger="ohwx",
        mode="CHARACTER",
        long="A woman sitting on a wooden park bench reading an antique novel. The surrounding garden has flowering rose bushes and quiet gravel pathways. Gentle diffused sunlight filters through leaves, casting soft dappled shadows. Captured from eye-level perspective with shallow depth of field.",
        medium="A woman sitting on a park bench reading an antique novel. Surrounding gardens feature rose bushes under gentle diffused sunlight.",
        short="woman sitting on park bench reading",
        tags=["park", "bench", "reading"],
    )

    locked_traits = ["silver hair", "freckles", "green eyes"]
    user_idea = "a woman with silver hair and freckles drinking tea on a terrace"

    expanded = expand_moss_prompt(
        db=temp_db,
        idea=user_idea,
        video_id_or_dataset="v_park",
        mode="CHARACTER",
        trigger_phrase="ohwx",
        class_noun="woman",
        locked_traits=locked_traits,
        use_llm=False,
    )

    prompt = expanded["prompt"]
    assert "long" in prompt
    assert "medium" in prompt
    assert "short" in prompt

    # Verify trigger phrase injection
    assert prompt["short"].startswith("ohwx,")
    assert prompt["medium"].startswith("ohwx,")
    assert prompt["long"].startswith("ohwx,")

    # Verify class noun utilization
    assert "woman" in prompt["short"].lower()
    assert "woman" in prompt["medium"].lower()
    assert "woman" in prompt["long"].lower()

    # Verify locked traits are completely absent (0% leakage)
    leaks = scan_trait_leakage(prompt, locked_traits)
    assert leaks == []

    # Verify word count budgets and hierarchy
    short_words = len(prompt["short"].split())
    med_words = len(prompt["medium"].split())
    long_words = len(prompt["long"].split())

    assert 10 <= short_words <= 22
    assert 40 <= med_words <= 75
    assert 110 <= long_words <= 220
    assert long_words > med_words > short_words >= 5

    # Verify exemplars in output
    assert len(expanded["exemplars"]) >= 1
    assert expanded["expansion_type"] == "deterministic"
    assert expanded["mode"] == "CHARACTER"
    assert expanded["trigger_phrase"] == "ohwx"


def test_deterministic_prompt_expansion_aesthetic_and_concept(temp_db: DB) -> None:
    """Verifies prompt expansion for style subtraction and object concept decoupling."""
    # 1. Aesthetic Style Mode
    style_res = expand_moss_prompt(
        db=temp_db,
        idea="a concrete staircase with glass railings and shadow patterns",
        video_id_or_dataset=None,
        mode="AESTHETIC_STYLE",
        trigger_phrase="archstyle",
        class_noun="structure",
        use_llm=False,
    )
    s_prompt = style_res["prompt"]
    assert s_prompt["short"].startswith("archstyle,")
    assert "cinematic" not in s_prompt["long"].lower()
    assert "masterpiece" not in s_prompt["long"].lower()
    assert len(s_prompt["long"].split()) >= 110

    # 2. Object Concept Mode
    concept_res = expand_moss_prompt(
        db=temp_db,
        idea="a vintage mechanical wristwatch resting on a mahogany desk",
        video_id_or_dataset=None,
        mode="OBJECT_CONCEPT",
        trigger_phrase="vntgwatch",
        class_noun="wristwatch",
        use_llm=False,
    )
    c_prompt = concept_res["prompt"]
    assert c_prompt["short"].startswith("vntgwatch,")
    assert "wristwatch" in c_prompt["short"].lower()
    assert len(c_prompt["short"].split()) >= 10
    assert len(c_prompt["medium"].split()) >= 40
    assert len(c_prompt["long"].split()) >= 110


def test_auto_indexing_from_record_caption(temp_db: DB) -> None:
    """Verifies that record_caption automatically populates moss_captions_fts."""
    with temp_db.write() as conn:
        conn.execute("INSERT INTO videos (id, path, name, slug) VALUES ('vid_auto', '/tmp/vid.mp4', 'Vid', 'vid')")
        conn.execute("INSERT INTO frames (id, video_id, t) VALUES (305, 'vid_auto', 1.0)")
        record_caption(
            conn=conn,
            frame_id=305,
            video_id="vid_auto",
            text="A woman in an athletic jacket sprinting across an open stadium track.",
            caption_long="A woman in an athletic jacket sprinting across an open stadium track under afternoon skies.",
            caption_medium="A woman sprinting across an open stadium track.",
            caption_short="woman sprinting on track",
            trigger="athletics",
            mode="CHARACTER",
        )

    results = retrieve_moss_exemplars(temp_db, query_text="sprinting stadium", top_k=2)
    assert len(results) >= 1
    assert results[0]["frame_id"] == 305
    assert "stadium" in results[0]["long"]


def test_api_expand_prompt_endpoint(monkeypatch: pytest.MonkeyPatch, temp_db: DB) -> None:
    """Verifies POST /api/rag/expand-prompt, POST /api/rag/index-moss, and GET /api/rag/exemplars."""
    # Monkeypatch the global api.db to use our temporary fixture
    monkeypatch.setattr("pipeline.api.db", temp_db)
    client = TestClient(app)

    # 1. Index captions via POST /api/rag/index-moss
    index_payload = {
        "captions": [
            {
                "frame_id": 501,
                "video_id": "api_test_video",
                "trigger": "ohwx",
                "mode": "CHARACTER",
                "long": "A woman wearing a linen summer dress strolling along a pebble beach at sunset.",
                "medium": "A woman in a summer dress walking on a beach at sunset.",
                "short": "woman on pebble beach",
                "tags": ["beach", "sunset", "dress"],
            }
        ]
    }
    resp_index = client.post("/api/rag/index-moss", json=index_payload)
    assert resp_index.status_code == 200
    assert resp_index.json()["indexed_count"] == 1

    # 2. Retrieve exemplars via GET /api/rag/exemplars
    resp_ex = client.get("/api/rag/exemplars?query=beach+sunset&top_k=3")
    assert resp_ex.status_code == 200
    data_ex = resp_ex.json()
    assert data_ex["count"] >= 1
    assert data_ex["exemplars"][0]["frame_id"] == 501

    # 3. Expand prompt via POST /api/rag/expand-prompt
    expand_payload = {
        "idea": "a woman with green eyes watching ocean waves at dusk",
        "dataset_id": "api_test_video",
        "mode": "CHARACTER",
        "trigger_phrase": "ohwx",
        "class_noun": "woman",
        "locked_traits": ["green eyes"],
        "use_llm": False,
    }
    resp_expand = client.post("/api/rag/expand-prompt", json=expand_payload)
    assert resp_expand.status_code == 200
    res = resp_expand.json()

    assert "prompt" in res
    assert res["prompt"]["short"].startswith("ohwx,")
    assert "green eyes" not in res["prompt"]["long"].lower()
    assert "green eyes" not in res["prompt"]["medium"].lower()
    assert "green eyes" not in res["prompt"]["short"].lower()
    assert len(res["exemplars"]) >= 1
    assert res["expansion_type"] == "deterministic"
