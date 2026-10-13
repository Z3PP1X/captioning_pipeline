"""Krea 2 prompt linter and validator enforcing clean natural language, adult 25+ subjects, and token budgets."""
from __future__ import annotations

import re
from typing import Any

BANNED_SLUDGE = [
    "masterpiece", "best quality", "high quality", "8k", "4k", "photorealistic",
    "hyperrealistic", "ultra detailed", "ultra-detailed", "extremely detailed",
    "award winning", "trending on artstation", "unreal engine", "octane render",
    "sharp focus", "canon eos", "nikon d850" # camera models if generic
]

BANNED_MINOR_TERMS = [
    "girl", "teen", "schoolgirl", "young girl", "juvenile", "barely legal",
    "underage", "little girl", "petite young"
]

BRACKET_WEIGHT_REGEX = re.compile(r"(\([^\)]+:\d+(\.\d+)?\)|\[[^\]]+:\d+(\.\d+)?\]|\(\([^\)]+\)\))")
PREAMBLE_REGEX = re.compile(r"^(here is (a |the )?(prompt|description)?:|in this (image|photo|photograph),?|prompt:)\s*", re.IGNORECASE)


def estimate_token_count(text: str) -> int:
    """Estimates Qwen3-VL token count using words and punctuation heuristic (~1.25 tokens/word)."""
    words = text.strip().split()
    if not words:
        return 0
    punctuation_count = len(re.findall(r"[,;.:!?-]", text))
    return int(len(words) * 1.15 + punctuation_count * 0.25)


def lint_prompt(
    raw_prompt: str,
    archetype: str = "short",
    trigger: str = "restrained_elegance",
    require_adult: bool = True,
    character: str | None = None,
) -> dict[str, Any]:
    """Audits and sanitizes a prompt against Krea 2 standards and specified archetype budgets."""
    cleaned = raw_prompt.strip()
    warnings: list[str] = []

    # 1. Strip preamble like 'Here is a prompt: ...'
    cleaned = PREAMBLE_REGEX.sub("", cleaned).strip()

    # 2. Strip bracket weights
    if BRACKET_WEIGHT_REGEX.search(cleaned):
        cleaned = BRACKET_WEIGHT_REGEX.sub("", cleaned).strip()
        warnings.append("Stripped WebUI-style bracket weights")

    # 3. Strip banned sludge
    for sludge in BANNED_SLUDGE:
        pattern = re.compile(r"\b" + re.escape(sludge) + r"\b", re.IGNORECASE)
        if pattern.search(cleaned):
            cleaned = pattern.sub("", cleaned)
            warnings.append(f"Stripped quality sludge token: '{sludge}'")

    # Normalize whitespace & punctuation
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r",\s*,+", ",", cleaned).strip(", ")

    # 4. Adult Guardrail Check
    lower_text = cleaned.lower()
    for minor_term in BANNED_MINOR_TERMS:
        pattern = re.compile(r"\b" + re.escape(minor_term) + r"\b", re.IGNORECASE)
        if pattern.search(lower_text):
            cleaned = pattern.sub("adult woman", cleaned)
            warnings.append(f"Replaced prohibited term '{minor_term}' with 'adult woman'")

    # Recognize 21+ or twenties/thirties as adult indicators
    has_adult_indicator = any(x in cleaned.lower() for x in ["adult", "21 year old", "21-year-old", "21yo", "twenties", "thirties"])
    if require_adult and not has_adult_indicator and "woman" in cleaned.lower():
        cleaned = re.sub(r"\bwoman\b", "adult woman", cleaned, count=1, flags=re.IGNORECASE)
        warnings.append("Added 'adult' qualifier to female subject")

    # Character presence check
    if character and character.strip():
        char_key = character.strip().lower()
        if char_key not in cleaned.lower():
            warnings.append(f"Selected character '{character}' was not detected in generated prompt")

    # 5. Token 0 Trigger Verification
    if trigger and trigger.strip() and trigger != "--no-trigger":
        trig = trigger.strip()
        words = cleaned.split()
        if not words:
            cleaned = f"{trig} Photograph of a scene."
            warnings.append(f"Set prompt to default with trigger '{trig}'")
        else:
            first = words[0].strip(",.:;")
            if first.lower() != trig.lower():
                if archetype == "tags":
                    cleaned = f"{trig}, {cleaned}"
                else:
                    cleaned = f"{trig} {cleaned}"
                warnings.append(f"Prepended trigger token '{trig}' to Token 0 position")

    # 6. Archetype token budget audit
    tok_est = estimate_token_count(cleaned)
    if archetype == "tags":
        if tok_est > 70:
            warnings.append(f"Tag prompt is slightly long (~{tok_est} tokens, target 25-50)")
    elif archetype == "short":
        if tok_est < 35:
            warnings.append(f"Short anchor prompt is brief (~{tok_est} tokens)")
        elif tok_est > 150:
            warnings.append(f"Short anchor prompt exceeds budget (~{tok_est} tokens, target 50-120)")
    elif archetype == "dense":
        if tok_est < 120:
            warnings.append(f"Dense narrative prompt is short (~{tok_est} tokens, target 180-300)")
        elif tok_est > 350:
            warnings.append(f"Dense narrative prompt exceeds ceiling (~{tok_est} tokens, target 180-300)")

    return {
        "clean_prompt": cleaned,
        "warnings": warnings,
        "token_count": tok_est,
        "is_valid": len([w for w in warnings if "Prohibited" in w]) == 0,
    }
