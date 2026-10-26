"""Module C: OpenRouter 3-Tier Cascade Captioning Engine, Skills, and Rejection-Guard."""
from backend.app.modules.captioning.cascade import (
    CascadeItemResult,
    OpenRouterCascadeEngine,
    OpenRouterService,
    prepare_512px_payload,
)
from backend.app.modules.captioning.rejection_guard import (
    REQUIRED_REFUSAL_REGEX,
    RejectionAuditResult,
    RejectionDetector,
    looks_truncated,
    normalize_quotes_and_whitespace,
)
from backend.app.modules.captioning.skills import (
    BUILTIN_SKILLS,
    SKILL_KREA2_CHARACTER_V1,
    SKILL_KREA2_CONCEPT_V1,
    SKILL_KREA2_STYLE_V1,
    SkillDefinition,
    TriggerPosition,
    apply_trigger_word,
    render_skill_prompts,
    verify_trigger_placement,
)

__all__ = [
    "BUILTIN_SKILLS",
    "CascadeItemResult",
    "OpenRouterCascadeEngine",
    "OpenRouterService",
    "REQUIRED_REFUSAL_REGEX",
    "RejectionAuditResult",
    "RejectionDetector",
    "SKILL_KREA2_CHARACTER_V1",
    "SKILL_KREA2_CONCEPT_V1",
    "SKILL_KREA2_STYLE_V1",
    "SkillDefinition",
    "TriggerPosition",
    "apply_trigger_word",
    "looks_truncated",
    "normalize_quotes_and_whitespace",
    "prepare_512px_payload",
    "render_skill_prompts",
    "verify_trigger_placement",
]
