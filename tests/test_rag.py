"""Unit tests for Krea 2 RAG Prompt Tool."""
from __future__ import annotations

import pytest
from pipeline.config import get_settings
from pipeline.rag.linter import estimate_token_count, lint_prompt
from pipeline.rag.retriever import RagRetriever
from pipeline.rag.taxonomy import CURATED_PRESETS, TAXONOMY_CATEGORIES


def test_taxonomy_structure():
    assert "equipment" in TAXONOMY_CATEGORIES
    assert "surroundings" in TAXONOMY_CATEGORIES
    assert "position" in TAXONOMY_CATEGORIES
    assert "camera_lighting" in TAXONOMY_CATEGORIES
    assert len(CURATED_PRESETS) >= 4


def test_prompt_linter_strips_sludge():
    raw = "masterpiece, best quality, 8k, photograph of a woman in chains"
    res = lint_prompt(raw, archetype="tags", trigger="restrained_elegance")
    assert "masterpiece" not in res["clean_prompt"].lower()
    assert "8k" not in res["clean_prompt"].lower()
    assert "best quality" not in res["clean_prompt"].lower()
    assert res["clean_prompt"].startswith("restrained_elegance,")


def test_prompt_linter_adult_guardrail():
    raw = "photograph of a young girl sitting in rope harness"
    res = lint_prompt(raw, archetype="short", trigger="restrained_elegance")
    assert "girl" not in res["clean_prompt"].lower()
    assert "adult woman" in res["clean_prompt"].lower()


def test_prompt_linter_bracket_weights():
    raw = "restrained_elegance Photograph of (woman:1.4) kneeling in [hemp rope:0.8]"
    res = lint_prompt(raw, archetype="short", trigger="restrained_elegance")
    assert "(woman:1.4)" not in res["clean_prompt"]
    assert "[hemp rope:0.8]" not in res["clean_prompt"]


def test_token_counter():
    text = "restrained_elegance Photograph of an adult woman kneeling in traditional seiza."
    count = estimate_token_count(text)
    assert 10 <= count <= 25


def test_characters_taxonomy_and_linter():
    from pipeline.rag.taxonomy import CHARACTERS
    assert "sara" in CHARACTERS
    assert "lene" in CHARACTERS
    assert "21 year old" in CHARACTERS["sara"]["description"]
    assert "petite" in CHARACTERS["lene"]["description"]

    # Test Sara linter
    sara_prompt = "restrained_elegance photograph of sara, a 21 year old woman, with brown hair and dark brown eyes bound in hemp rope"
    res_sara = lint_prompt(sara_prompt, archetype="short", trigger="restrained_elegance", character="sara")
    assert "sara" in res_sara["clean_prompt"].lower()
    assert "21 year old woman" in res_sara["clean_prompt"].lower()

    # Test Lene linter
    lene_prompt = "restrained_elegance, lene, 21yo petite woman, dark brown hair, dark brown eyes, takate kote"
    res_lene = lint_prompt(lene_prompt, archetype="tags", trigger="restrained_elegance", character="lene")
    assert "lene" in res_lene["clean_prompt"].lower()
    assert "petite" in res_lene["clean_prompt"].lower()


def test_eval_query_text_and_source_ids():
    from pipeline.rag.evaluation import query_text
    from pipeline.rag.indexer import extract_source_id, record_key

    assert query_text("a b c d e", 3) == "a b c" and query_text("a b", 0) == "a b"
    assert extract_source_id("vid_a_0001") == "vid_a" and extract_source_id("clip_frame_12") == "clip"
    assert record_key({"frame_id": 7, "stem": "x"}) == "frame:7" and record_key({"frame_id": None, "stem": "x"}) == "stem:x"


def test_rag_auto_reindex_setting_defaults_on_and_validates():
    from pipeline.config import DEFAULT_USER_SETTINGS, validate_user_settings

    assert DEFAULT_USER_SETTINGS["rag_auto_reindex"] is True
    clean, errors, _ = validate_user_settings({"rag_auto_reindex": False})
    assert clean == {"rag_auto_reindex": False} and not errors
    assert validate_user_settings({"rag_auto_reindex": "yes"})[1]


def test_rag_image_index_setting_defaults_on_and_validates():
    from pipeline.config import DEFAULT_USER_SETTINGS, validate_user_settings

    assert DEFAULT_USER_SETTINGS["rag_image_index"] is True
    clean, errors, ignored = validate_user_settings({"rag_image_index": False})
    assert clean == {"rag_image_index": False} and not errors and not ignored
    assert validate_user_settings({"rag_image_index": 1})[1]


def test_dense_mode_defaults():
    from pipeline.rag.retriever import DEFAULT_DENSE_MODE, DENSE_MODES

    assert DENSE_MODES == ("whole", "domain") and DEFAULT_DENSE_MODE == "domain"
