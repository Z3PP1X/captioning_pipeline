"""Database seeder for default system_settings and builtin Krea 2 Master Skills."""
from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

from backend.app.config import get_app_settings

if TYPE_CHECKING:
    from backend.app.db.session import DatabaseManager


DEFAULT_SYSTEM_SETTINGS: list[tuple[str, Any, bool, str]] = [
    (
        "openrouter_api_key",
        "",
        True,
        "OpenRouter API Key (stored in SQLite WAL, never returned raw in GET responses)",
    ),
    (
        "primary_model",
        "google/gemini-2.5-pro",
        False,
        "Tier 1 Primary Vision-Language Model on OpenRouter",
    ),
    (
        "fallback_model_1",
        "qwen/qwen-2.5-vl-72b-instruct",
        False,
        "Tier 2 Fallback 1 Vision-Language Model when Tier 1 refuses or truncates",
    ),
    (
        "fallback_model_2",
        "meta-llama/llama-3.2-90b-vision-instruct",
        False,
        "Tier 3 Fallback 2 Vision-Language Model when Tier 1 & Tier 2 fail",
    ),
    (
        "max_tokens",
        600,
        False,
        "Maximum completion tokens per VLM caption call",
    ),
    (
        "temperature",
        0.4,
        False,
        "Sampling temperature in [0.0, 2.0] for caption consistency",
    ),
    (
        "top_p",
        0.90,
        False,
        "Nucleus sampling top_p parameter",
    ),
    (
        "max_image_dim",
        512,
        False,
        "Maximum longest edge (px) for vision payload downscaling",
    ),
    (
        "trigger_word",
        "ohwx_subject",
        False,
        "Default LoRA triggerword injected into training captions",
    ),
    (
        "trigger_position",
        "prefix",
        False,
        "Triggerword placement rule ('prefix', 'suffix', or 'natural_integration')",
    ),
    (
        "custom_rejection_regexes",
        [],
        False,
        "Optional list of user-defined refusal/banned regex patterns for RejectionDetector",
    ),
    (
        "quality_weights",
        {"sharpness": 0.45, "exposure": 0.25, "entropy": 0.15, "contrast": 0.15},
        False,
        "Mathematical quality scoring weights Q in [0, 1]",
    ),
    (
        "rag_dense_backend",
        "tfidf_svd_128",
        False,
        "Default Tier-2 RAG dense retrieval backend ('tfidf_svd_128' or 'openrouter_embedding')",
    ),
    (
        "rag_embedding_model",
        "openai/text-embedding-3-small",
        False,
        "OpenRouter Embeddings model when 'openrouter_embedding' backend is selected",
    ),
]


def seed_database(db: DatabaseManager) -> None:
    """Idempotently seed default `system_settings` and builtin Krea 2 Master Skills."""
    from backend.app.modules.captioning.skills import BUILTIN_SKILLS

    app_settings = get_app_settings()
    now = time.time()

    with db.write() as conn:
        for key, default_val, is_secret, desc in DEFAULT_SYSTEM_SETTINGS:
            if key == "openrouter_api_key" and not default_val and app_settings.openrouter_api_key:
                default_val = app_settings.openrouter_api_key
            conn.execute(
                """
                INSERT INTO system_settings (key, value, is_secret, description, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(key) DO NOTHING
                """,
                (key, json.dumps(default_val), 1 if is_secret else 0, desc, now),
            )

        for skill in BUILTIN_SKILLS:
            conn.execute(
                """
                INSERT INTO skills (
                    id, slug, name, version, training_type,
                    system_prompt_template, user_prompt_template, glossary_template,
                    min_words, max_words, hard_min_words, hard_max_words,
                    discouraged_regexes, require_class_noun, is_builtin,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                (
                    skill.id,
                    skill.slug,
                    skill.name,
                    skill.version,
                    skill.training_type,
                    skill.system_prompt_template,
                    skill.user_prompt_template,
                    skill.glossary_template,
                    skill.min_words,
                    skill.max_words,
                    skill.hard_min_words,
                    skill.hard_max_words,
                    json.dumps(list(skill.discouraged_regexes)),
                    1 if skill.require_class_noun else 0,
                    now,
                    now,
                ),
            )
