"""Unit tests for MOSS-Image Prompts and Deterministic Validator."""
from __future__ import annotations

import json

from pipeline.moss_validator import (
    detect_refusal,
    extract_json_object,
    sanitize_leaks,
    scan_trait_leakage,
    validate_and_inject_triggers,
)
from pipeline.prompts.moss_prompts import (
    TIER_TARGETS,
    compute_moss_prompt_version,
    normalize_moss_mode,
    render_moss_system_prompt,
)


def test_prompt_templates_rendering():
    """Validates prompt template rendering, placeholders, and word budgets."""
    assert normalize_moss_mode("style") == "aesthetic_style"
    assert normalize_moss_mode("concept") == "object_concept"
    assert normalize_moss_mode("character") == "character"

    rendered_char = render_moss_system_prompt(
        mode="character",
        class_noun="cyberpunk warrior",
        locked_traits=["neon blue mohawk", "bionic left eye"],
        training_mode="character LoRA",
    )
    assert "cyberpunk warrior" in rendered_char
    assert "- neon blue mohawk" in rendered_char
    assert "- bionic left eye" in rendered_char
    assert "character LoRA" in rendered_char
    assert "watermark" in rendered_char
    assert "borders" in rendered_char
    assert "110 to 220 words" in rendered_char

    rendered_style = render_moss_system_prompt(
        mode="aesthetic_style",
        class_noun="knight",
        locked_traits="silver armor, red cape",
        training_mode="oil painting style",
    )
    assert "knight" in rendered_style
    assert "- silver armor" in rendered_style
    assert "oil painting style" in rendered_style
    assert "Strict Style Subtraction" in rendered_style

    rendered_obj = render_moss_system_prompt(
        mode="object_concept",
        class_noun="magic crystal sphere",
        locked_traits=["inner swirl"],
    )
    assert "magic crystal sphere" in rendered_obj
    assert "- inner swirl" in rendered_obj
    assert "Explicit Environmental and Medium Decoupling" in rendered_obj

    version_str = compute_moss_prompt_version("character")
    assert version_str.startswith("character-moss-v1.0-")
    assert len(version_str) > 20


def test_tier_targets_constants():
    assert TIER_TARGETS["long"] == (110, 220)
    assert TIER_TARGETS["medium"] == (40, 75)
    assert TIER_TARGETS["short"] == (10, 22)


def test_refusal_patterns_detection():
    """Checks refusal regexes on diverse refusal responses."""
    refusals = [
        "I can't assist with this request.",
        "I cannot fulfill your request due to safety policies.",
        "I am unable to generate the caption.",
        "As an AI, I am not permitted to view this image.",
        "I'm sorry, but I can't analyze this image.",
        "I cannot generate a description for this content.",
        "This request violates our safety policies.",
    ]
    for ref in refusals:
        is_ref, reason = detect_refusal(ref)
        assert is_ref is True, f"Failed to detect refusal: {ref}"
        assert reason is not None
        assert "Refusal pattern" in reason


def test_refusal_json_schema_validation():
    """Checks JSON parseability and schema validation in detect_refusal."""
    # Invalid JSON
    is_ref, reason = detect_refusal("Not a valid json response at all")
    assert is_ref is True
    assert "JSON parse failure" in reason

    # Missing caption object
    invalid_schema = json.dumps({"watermark": [], "borders": ["none"]})
    is_ref, reason = detect_refusal(invalid_schema)
    assert is_ref is True
    assert "Missing 'caption' dictionary" in reason

    # Missing long tier
    missing_long = json.dumps({"caption": {"medium": "med text", "short": "one two three four five"}})
    is_ref, reason = detect_refusal(missing_long)
    assert is_ref is True
    assert "Missing or empty 'caption.long'" in reason

    # Short tier has fewer than 5 words
    short_too_brief = json.dumps({
        "caption": {
            "long": "This is a sufficiently long description that satisfies the initial long check.",
            "medium": "Medium description of the scene.",
            "short": "Too brief.",
        }
    })
    is_ref, reason = detect_refusal(short_too_brief)
    assert is_ref is True
    assert "too short" in reason

    # Valid JSON payload
    valid_payload = json.dumps({
        "caption": {
            "long": "A detailed multi-tier caption describing an elaborate outdoor environment with diverse props.",
            "medium": "An outdoor environment with props and clear lighting.",
            "short": "Outdoor setting with several props.",
        },
        "watermark": [],
        "borders": ["none"],
    })
    is_ref, reason = detect_refusal(valid_payload)
    assert is_ref is False
    assert reason is None


def test_extract_json_object_with_markdown_fences():
    fenced = """```json
{
  "caption": {
    "long": "Long test caption.",
    "medium": "Medium caption.",
    "short": "Short caption with five words."
  }
}
```"""
    data = extract_json_object(fenced)
    assert "caption" in data
    assert data["caption"]["short"] == "Short caption with five words."


def test_scan_trait_leakage():
    """Checks word-boundary regex detection for locked traits across tiers."""
    locked_traits = ["emerald eyes", "scar", "dragon tattoo"]

    # Trait appears in long tier
    payload_long = {
        "caption": {
            "long": "The person has emerald eyes and wears a gray woolen coat in the rain.",
            "medium": "A person standing in the rain wearing a woolen coat.",
            "short": "Person wearing a coat in the rain.",
        }
    }
    leaked = scan_trait_leakage(payload_long, locked_traits)
    assert leaked == ["emerald eyes"]

    # Trait appears in short tier
    payload_short = {
        "caption": {
            "long": "The person wears a gray woolen coat in the rain.",
            "medium": "A person standing in the rain.",
            "short": "Person with a dragon tattoo outside.",
        }
    }
    leaked = scan_trait_leakage(payload_short, locked_traits)
    assert leaked == ["dragon tattoo"]

    # Word-boundary check: "scar" should not match "scarecrow" or "oscar"
    payload_no_leak = {
        "caption": {
            "long": "Standing near a scarecrow holding an Oscar award in the park.",
            "medium": "A person standing in a park near a scarecrow.",
            "short": "Person in a city park.",
        }
    }
    assert scan_trait_leakage(payload_no_leak, locked_traits) == []

    # Clean caption with no leaks
    clean_payload = {
        "caption": {
            "long": "A person in a simple jacket walking down a cobblestone alley.",
            "medium": "Person walking down an alley.",
            "short": "Person in an alleyway walking.",
        }
    }
    assert scan_trait_leakage(clean_payload, locked_traits) == []


def test_validate_and_inject_triggers():
    """Checks word count hierarchy validation and trigger phrase injection."""
    caption_payload = {
        "caption": {
            "long": "A " * 30,  # 30 words
            "medium": "B " * 15,  # 15 words
            "short": "C " * 6,  # 6 words
        }
    }
    updated, warnings = validate_and_inject_triggers(
        caption_payload,
        trigger_phrase="c1_eva",
        class_noun="woman",
    )
    assert len(warnings) == 0
    assert updated["caption"]["long"].startswith("c1_eva, ")
    assert updated["caption"]["medium"].startswith("c1_eva, ")
    assert updated["caption"]["short"].startswith("c1_eva, ")

    # Pre-existing trigger should not be duplicated
    updated_again, _ = validate_and_inject_triggers(
        updated,
        trigger_phrase="c1_eva",
        class_noun="woman",
    )
    assert not updated_again["caption"]["long"].startswith("c1_eva, c1_eva")

    # Lingering {{CLASS_NOUN}} placeholder replacement
    placeholder_payload = {
        "caption": {
            "long": "Photograph of {{CLASS_NOUN}} walking in a forest. " + ("tree " * 25),
            "medium": "Photograph of {{class_noun}} in a forest. " + ("tree " * 10),
            "short": "A {{CLASS_NOUN}} in a forest.",
        }
    }
    replaced, _ = validate_and_inject_triggers(
        placeholder_payload,
        trigger_phrase="c1_eva",
        class_noun="heroic knight",
    )
    assert "heroic knight" in replaced["caption"]["long"]
    assert "{{CLASS_NOUN}}" not in replaced["caption"]["long"]

    # Hierarchy violation (medium >= long)
    bad_hierarchy = {
        "caption": {
            "long": "word " * 10,
            "medium": "word " * 15,
            "short": "word " * 6,
        }
    }
    _, warnings = validate_and_inject_triggers(bad_hierarchy)
    assert any("long tier" in w for w in warnings)


def test_sanitize_leaks_phrase_and_sentence():
    """Validates deterministic phrase and sentence-level trait leakage sanitization."""
    text = (
        "A woman with emerald eyes and wearing a dark jacket stands on the platform. "
        "Her eyes are bright emerald eyes. "
        "The background features an empty subway platform."
    )
    traits = ["emerald eyes"]

    sanitized = sanitize_leaks(text, traits)
    assert "emerald eyes" not in sanitized.lower()
    assert "subway platform" in sanitized
    assert "dark jacket" in sanitized
    # No trailing commas or orphan punctuation
    assert ", ." not in sanitized
    assert "  " not in sanitized

    # Multiple traits
    multi_text = "Subject with silver hair, carrying a sword, with red glasses in the city."
    multi_traits = ["silver hair", "red glasses"]
    clean_multi = sanitize_leaks(multi_text, multi_traits)
    assert "silver hair" not in clean_multi
    assert "red glasses" not in clean_multi
    assert "carrying a sword" in clean_multi
