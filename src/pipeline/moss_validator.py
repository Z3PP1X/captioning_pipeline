"""Deterministic Refusal-, Leakage-, and Hierarchy-Validator for MOSS-Image captions.

Provides:
- Refusal detection via regex and JSON/schema validation.
- Trait leakage scanning using word-boundary regexes against locked traits.
- Hierarchy validation (long > medium > short >= 5) and trigger phrase injection.
- Deterministic auto-sanitization of leaked traits.
"""
from __future__ import annotations

import json
import re
from typing import Any

REFUSAL_PATTERN = (
    r"(i\s+can'?t\s+assist|i\s+cannot\s+fulfill|i\s+am\s+unable\s+to|as\s+an\s+ai|"
    r"i'?m\s+sorry,\s+but\s+i\s+can'?t|cannot\s+generate|violates\s+our\s+safety)"
)
REFUSAL_REGEX = re.compile(REFUSAL_PATTERN, re.IGNORECASE)

NOUN_PLACEHOLDER_REGEX = re.compile(r"\{\{?\s*class_noun\s*\}?\}", re.IGNORECASE)


def extract_json_object(raw_text: str) -> dict[str, Any]:
    """Extracts and parses JSON object from raw response text, handling markdown fences."""
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except Exception:  # noqa: S110
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = text[start : end + 1]
        data = json.loads(candidate)
        if isinstance(data, dict):
            return data

    raise ValueError("Could not parse valid JSON object from model response")


def _validate_caption_payload(data: dict[str, Any]) -> tuple[bool, str | None]:
    """Validates presence and minimum lengths of caption tiers in parsed JSON."""
    for val in data.values():
        if isinstance(val, str) and REFUSAL_REGEX.search(val):
            return True, f"Refusal pattern matched in payload value: '{val[:60]}'"

    caption_obj = data.get("caption")
    if not isinstance(caption_obj, dict):
        return True, "Missing 'caption' dictionary in response"

    long_tier = str(caption_obj.get("long") or "").strip()
    if not long_tier:
        return True, "Missing or empty 'caption.long'"

    short_tier = str(caption_obj.get("short") or "").strip()
    short_words = short_tier.split()
    if len(short_words) < 5:
        return True, f"'caption.short' is too short ({len(short_words)} words, minimum 5)"

    return False, None


def detect_refusal(raw_input: str | dict[str, Any]) -> tuple[bool, str | None]:
    """Detects refusal or invalid structure from raw text or parsed JSON.

    Returns (is_refusal, refusal_reason).
    """
    if isinstance(raw_input, str):
        match = REFUSAL_REGEX.search(raw_input)
        if match:
            return True, f"Refusal pattern matched: '{match.group(0)}'"

        try:
            data = extract_json_object(raw_input)
        except Exception as ex:
            return True, f"JSON parse failure: {ex}"
    else:
        data = raw_input

    return _validate_caption_payload(data)


def _collect_caption_texts(captions: str | dict[str, Any]) -> list[str]:
    """Flattens caption inputs (strings, tier dicts, or wrapper dicts) into text fragments."""
    if isinstance(captions, str):
        return [captions]
    if not isinstance(captions, dict):
        return [str(captions)]
    cap_dict = captions.get("caption", captions)
    if isinstance(cap_dict, dict):
        return [str(cap_dict.get(k) or "") for k in ("long", "medium", "short") if cap_dict.get(k)]
    return [str(captions)]


def scan_trait_leakage(
    captions: str | dict[str, Any],
    locked_traits: list[str],
) -> list[str]:
    """Scans captions against locked traits using word-boundary regexes.

    Returns a list of detected leaked trait strings.
    """
    if not locked_traits:
        return []

    combined_text = " ".join(_collect_caption_texts(captions))
    detected: list[str] = []

    for trait in locked_traits:
        t_clean = trait.strip()
        if t_clean and re.search(rf"\b{re.escape(t_clean)}\b", combined_text, re.IGNORECASE):
            detected.append(t_clean)

    return detected


def _inject_trigger_into_text(text: str, trigger_phrase: str) -> str:
    """Prepends trigger phrase if not already present."""
    stripped = text.strip()
    if not stripped:
        return trigger_phrase
    if not trigger_phrase:
        return stripped

    clean_trigger = trigger_phrase.strip().rstrip(",").strip()
    check_lower = stripped.lower()
    prefix_lower = clean_trigger.lower()

    if check_lower.startswith(prefix_lower):
        remainder = stripped[len(clean_trigger) :].lstrip()
        if remainder.startswith(","):
            return stripped
        return f"{clean_trigger}, {remainder.lstrip()}"

    return f"{clean_trigger}, {stripped}"


def _replace_class_noun(text: str, class_noun: str | None) -> str:
    """Replaces any lingering {{CLASS_NOUN}} placeholders."""
    replacement = (class_noun or "subject").strip()
    return NOUN_PLACEHOLDER_REGEX.sub(replacement, text)


def validate_and_inject_triggers(
    caption_data: dict[str, Any],
    trigger_phrase: str | None = None,
    class_noun: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Validates word count hierarchy (long > medium > short >= 5) and injects trigger phrase.

    Returns (updated_caption_data, warnings_list).
    """
    warnings: list[str] = []
    updated = json.loads(json.dumps(caption_data))

    cap_obj = updated.get("caption")
    if not isinstance(cap_obj, dict):
        warnings.append("Missing caption object")
        return updated, warnings

    for tier in ("long", "medium", "short"):
        val = str(cap_obj.get(tier) or "")
        val = _replace_class_noun(val, class_noun)
        if trigger_phrase and trigger_phrase.strip():
            val = _inject_trigger_into_text(val, trigger_phrase)
        cap_obj[tier] = val

    long_words = len(cap_obj.get("long", "").split())
    med_words = len(cap_obj.get("medium", "").split())
    short_words = len(cap_obj.get("short", "").split())

    if short_words < 5:
        warnings.append(f"Hierarchy violation: short tier has {short_words} words (expected >= 5)")
    if med_words <= short_words:
        warnings.append(f"Hierarchy violation: medium tier ({med_words}) <= short tier ({short_words})")
    if long_words <= med_words:
        warnings.append(f"Hierarchy violation: long tier ({long_words}) <= medium tier ({med_words})")

    return updated, warnings


def _clean_residual_punctuation(text: str) -> str:
    """Cleans up orphan commas, duplicate spaces, and punctuation artifacts."""
    s = re.sub(r"\s+", " ", text).strip()
    s = re.sub(r",\s*,+", ",", s)
    s = re.sub(r"\s+([,.;!?])", r"\1", s)
    s = re.sub(r",\s*\.", ".", s)
    s = re.sub(r"\b(with|having|featuring|sporting)\s*([,.;!?])", r"\2", s, flags=re.IGNORECASE)
    s = re.sub(r"\b(and|or)\s*([,.;!?])", r"\2", s, flags=re.IGNORECASE)
    s = re.sub(r",\s*and\s*([,.;!?])", r"\1", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip()
    return s.strip(" ,")


def _prune_sentence_or_trait(sentence: str, trait: str) -> str:
    """Removes a trait or prune the descriptor phrase from a single sentence."""
    t_pattern = rf"\b{re.escape(trait)}\b"
    if not re.search(t_pattern, sentence, re.IGNORECASE):
        return sentence

    phrase_pattern = (
        rf"(?:,\s*)?(?:\b(?:with|having|featuring|sporting|showing)\b\s*)?(?:\b(?:a|an|the)\b\s*)?"
        rf"{re.escape(trait)}"
        rf"(?:\s+and\s+|,|\.)?"
    )
    pruned = re.sub(phrase_pattern, " ", sentence, flags=re.IGNORECASE)
    pruned = re.sub(t_pattern, "", pruned, flags=re.IGNORECASE)
    cleaned = _clean_residual_punctuation(pruned)

    # If the sentence collapsed into a trivial stub or lost all substance
    words = [w for w in cleaned.split() if w.strip(".,;:!?")]
    if len(words) < 3:
        return ""
    if not cleaned.endswith((".", "!", "?")):
        cleaned += "."
    return cleaned


def _prune_sentence_traits(sentence: str, traits: list[str]) -> str:
    """Applies pruning of all traits against a single sentence."""
    current = sentence
    for trait in traits:
        current = _prune_sentence_or_trait(current, trait)
        if not current:
            return ""
    return current


def _fallback_word_prune(text: str, traits: list[str]) -> str:
    """Fallback pruning when sentence pruning removed all sentences."""
    fallback = text
    for trait in traits:
        fallback = re.sub(rf"\b{re.escape(trait)}\b", "", fallback, flags=re.IGNORECASE)
    fallback = _clean_residual_punctuation(fallback)
    return fallback if fallback.endswith((".", "!", "?")) else f"{fallback}."


def sanitize_leaks(text: str, leaked_traits: list[str]) -> str:
    """Cleanses leaked traits from text using deterministic sentence and phrase pruning."""
    if not text or not leaked_traits:
        return text

    split_sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    raw_sentences = split_sentences or [text.strip()]

    sanitized = [_prune_sentence_traits(s, leaked_traits) for s in raw_sentences]
    valid_sentences = [s for s in sanitized if s]

    if not valid_sentences:
        return _fallback_word_prune(text, leaked_traits)

    result = " ".join(valid_sentences)
    return _clean_residual_punctuation(result)


__all__ = [
    "REFUSAL_PATTERN",
    "REFUSAL_REGEX",
    "detect_refusal",
    "extract_json_object",
    "sanitize_leaks",
    "scan_trait_leakage",
    "validate_and_inject_triggers",
]
