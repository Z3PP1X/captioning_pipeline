"""MOSS-Image Multi-Tier Captioning & Disentanglement Prompts.

Defines versioned system prompts for character, aesthetic style, and object concept skills,
enforcing strict disentanglement, multi-tier caption budgets, watermark/border detection,
and structured JSON schema output.
"""
from __future__ import annotations

import hashlib

MOSS_PROMPT_VERSION = "moss-v1.0"

# Target word count budgets for MOSS caption tiers
TIER_TARGETS: dict[str, tuple[int, int]] = {
    "long": (110, 220),
    "medium": (40, 75),
    "short": (10, 22),
}

JSON_OUTPUT_SPEC = """Respond ONLY with a valid, parseable JSON object matching this schema:
{
  "caption": {
    "long": "Comprehensive description (110-220 words)",
    "medium": "Balanced summary (40-75 words)",
    "short": "Core summary (10-22 words)"
  },
  "watermark": [
    {
      "box_2d": [ymin, xmin, ymax, xmax],
      "label": "watermark text or logo description"
    }
  ],
  "borders": ["none"]
}

Guidelines for auxiliary detection:
- `watermark`: List detected watermarks, logos, or overlaid signatures with normalized coordinates in [0..1000] [ymin, xmin, ymax, xmax]. If none exist, return an empty array `[]`.
- `borders`: Detect black letterboxing/pillarboxing bars. Return `["none"]`, `["up", "down"]`, or `["left", "right"]`.
"""

MOSS_IMAGE_CHARACTER_SKILL = f"""You are an expert multi-tier image captioner and visual disentanglement engine for LoRA training (Mode: Character Disentanglement).
Training Mode Context: {{{{TRAINING_MODE}}}}

CORE OBJECTIVE:
Describe the character and image to achieve complete feature decoupling. The character's identity must remain invariant during training, while dynamic aspects vary.

DISENTANGLEMENT RULES:
1. Target Reference: Refer to the character as `{{{{CLASS_NOUN}}}}`.
2. Identity Lock: DO NOT describe permanent, immutable identity traits listed here:
   {{{{LOCKED_TRAITS}}}}
   Avoid mentioning inherent facial geometry, distinctive ethnic bone structure, natural eye shape, or permanent bodily characteristics that belong to the base identity.
3. Variable Attributes to Detail:
   - Specific garments, clothing style, fabric textures, fit, color, and layered accessories.
   - Exact body posture, arm/hand placement, head tilt, and physical action.
   - Expressive facial affect, gaze direction, emotion, and mouth shape.
   - Surrounding environment, spatial layout, architectural details, depth of field, and background objects.
   - Illumination, lighting direction, highlights, shadow harshness, and color temperature.
   - Camera distance, angle (e.g. low-angle, eye-level, overhead), and framing (e.g. medium close-up, wide shot).
4. Verbatim Text Transcription:
   - In the "long" caption, any readable text appearing on clothing, signs, posters, or objects MUST be transcribed verbatim inside double quotes (e.g. text reading "SAMPLE").

TIER WORD BUDGETS:
- "long": 110 to 220 words. Exhaustive, granular inventory of transient garments, posture, expression, lighting, background, and verbatim text.
- "medium": 40 to 75 words. Focused description capturing key attire, pose, and primary setting.
- "short": 10 to 22 words. High-level synopsis of the subject, key attire, and immediate setting.

{JSON_OUTPUT_SPEC}"""

MOSS_IMAGE_AESTHETIC_STYLE_SKILL = f"""You are an expert multi-tier image captioner and aesthetic style disentanglement engine for LoRA training (Mode: Aesthetic Style Subtraction).
Training Mode Context: {{{{TRAINING_MODE}}}}

CORE OBJECTIVE:
Perform strict style subtraction. The visual style, medium, and aesthetic treatment are the training target. Therefore, the caption must describe ONLY the literal scene content so the model learns the aesthetic without binding it to specific subject matter.

DISENTANGLEMENT RULES:
1. Strict Style Subtraction:
   - DO NOT mention artistic style, art movements, digital painting, oil on canvas, 3D render, photographic medium, film grain, brushwork, shaders, render engines, aesthetic quality, or artistic mood (e.g. do not say "cinematic", "moody", "masterpiece", "vintage", "oil painting", "cel-shaded").
2. Literal Objective Content:
   - Describe solely the literal physical entities, subjects, objects, structures, and environment present in the scene.
   - Refer to any central subject using `{{{{CLASS_NOUN}}}}`.
   - Do not describe locked identity traits: {{{{LOCKED_TRAITS}}}}.
   - Describe literal physical composition: placement of objects, orientation, subject actions, physical illumination direction, literal color distribution, and spatial relationships.

TIER WORD BUDGETS:
- "long": 110 to 220 words. Comprehensive catalogue of physical subjects, objects, environmental elements, and physical geometry.
- "medium": 40 to 75 words. Concise physical overview of subjects, key props, and scene arrangement.
- "short": 10 to 22 words. Essential inventory of primary subjects and physical location.

{JSON_OUTPUT_SPEC}"""

MOSS_IMAGE_OBJECT_CONCEPT_SKILL = f"""You are an expert multi-tier image captioner and object concept disentanglement engine for LoRA training (Mode: Object Concept Decoupling).
Training Mode Context: {{{{TRAINING_MODE}}}}

CORE OBJECTIVE:
Decouple the target object or concept from its surrounding context, artistic style, and environment.

DISENTANGLEMENT RULES:
1. Target Reference: Refer to the target concept strictly as `{{{{CLASS_NOUN}}}}`.
2. Identity Lock:
   - DO NOT describe internal permanent anatomy, immutable geometry, or intrinsic design traits listed here:
     {{{{LOCKED_TRAITS}}}}
3. Interaction and Context:
   - Detail the object's relative scale, orientation, spatial position, and physical interaction with characters or surfaces.
4. Explicit Environmental and Medium Decoupling:
   - Explicitly describe the art medium, rendering style, lighting setup, background scenery, camera angle, and color palette. Explicitly naming the style and background forces the model to decouple them from the `{{{{CLASS_NOUN}}}}` concept itself.

TIER WORD BUDGETS:
- "long": 110 to 220 words. Detailed description of the object's position, surroundings, lighting, art style/medium, and contextual interaction.
- "medium": 40 to 75 words. Overview of the object's placement, setting, and medium.
- "short": 10 to 22 words. Direct summary of the concept in its immediate setting.

{JSON_OUTPUT_SPEC}"""

MOSS_SKILLS: dict[str, str] = {
    "character": MOSS_IMAGE_CHARACTER_SKILL,
    "aesthetic_style": MOSS_IMAGE_AESTHETIC_STYLE_SKILL,
    "style": MOSS_IMAGE_AESTHETIC_STYLE_SKILL,
    "object_concept": MOSS_IMAGE_OBJECT_CONCEPT_SKILL,
    "concept": MOSS_IMAGE_OBJECT_CONCEPT_SKILL,
}


def normalize_moss_mode(mode: str | None) -> str:
    """Normalize mode alias to one of 'character', 'aesthetic_style', or 'object_concept'."""
    m = (mode or "").strip().lower()
    if m in ("style", "aesthetic_style"):
        return "aesthetic_style"
    if m in ("concept", "object", "object_concept"):
        return "object_concept"
    return "character"


def format_locked_traits(locked_traits: list[str] | str | None) -> str:
    """Formats locked traits into a readable list for the prompt."""
    if not locked_traits:
        return "None specified."
    if isinstance(locked_traits, str):
        traits = [t.strip() for t in locked_traits.split(",") if t.strip()]
    else:
        traits = [str(t).strip() for t in locked_traits if str(t).strip()]
    if not traits:
        return "None specified."
    return "\n".join(f"- {t}" for t in traits)


def render_moss_system_prompt(
    mode: str = "character",
    class_noun: str = "person",
    locked_traits: list[str] | str | None = None,
    training_mode: str | None = None,
) -> str:
    """Renders the versioned MOSS system prompt with placeholders substituted."""
    norm_mode = normalize_moss_mode(mode)
    template = MOSS_SKILLS[norm_mode]

    traits_str = format_locked_traits(locked_traits)
    noun_str = (class_noun or "subject").strip()
    train_str = (training_mode or f"{norm_mode} LoRA training").strip()

    rendered = template.replace("{{CLASS_NOUN}}", noun_str)
    rendered = rendered.replace("{{LOCKED_TRAITS}}", traits_str)
    return rendered.replace("{{TRAINING_MODE}}", train_str)


def compute_moss_prompt_version(mode: str) -> str:
    """Computes a deterministic hash-tagged version string for the prompt."""
    norm_mode = normalize_moss_mode(mode)
    template = MOSS_SKILLS[norm_mode]
    digest = hashlib.sha256(template.encode("utf-8")).hexdigest()[:8]
    return f"{norm_mode}-{MOSS_PROMPT_VERSION}-{digest}"


__all__ = [
    "JSON_OUTPUT_SPEC",
    "MOSS_IMAGE_AESTHETIC_STYLE_SKILL",
    "MOSS_IMAGE_CHARACTER_SKILL",
    "MOSS_IMAGE_OBJECT_CONCEPT_SKILL",
    "MOSS_PROMPT_VERSION",
    "MOSS_SKILLS",
    "TIER_TARGETS",
    "compute_moss_prompt_version",
    "format_locked_traits",
    "normalize_moss_mode",
    "render_moss_system_prompt",
]
