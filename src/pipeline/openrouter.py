"""OpenRouter API client for VLM batch captioning with 512px token saving and krea2 linter.

Prompts are versioned in `pipeline.prompts` (no runtime reads outside the repo). Every reply passes a
validation gate (`validate_caption_gate`); a rejected caption raises `CaptionValidationError`, which the
caption job treats like a refusal and sends to the next cascade layer. Token usage and cost (OpenRouter
`usage: {include: true}`) are accumulated into the caller's `usage` dict.
"""
from __future__ import annotations

import asyncio
import base64
import re
from typing import Any

import cv2
import httpx
import numpy as np

from pipeline.config import Settings, get_settings
from pipeline.prompts import (
    MODE_WORD_RANGES,
    get_profile,
    normalize_mode,
    primary_class_noun,
    prompt_version,
    render_system_prompt,
    render_user_prompt,
)

# Captions want consistency, not creativity (review §7.2: 0.3-0.5)
DEFAULT_CAPTION_TEMPERATURE = 0.4
DEFAULT_CAPTION_TOP_P = 0.90

# Banned Danbooru buzzwords and quality filler
BANNED_SLUDGE = [
    "masterpiece", "best quality", "high quality", "8k", "4k", "photorealistic",
    "hyperrealistic", "ultra detailed", "ultra-detailed", "extremely detailed",
    "award winning", "trending on artstation", "unreal engine"
]
BRACKET_WEIGHT_REGEX = re.compile(r"(\([^\)]+:\d+(\.\d+)?\)|\[[^\]]+:\d+(\.\d+)?\]|\(\([^\)]+\)\))")

# Visible-output budget. 180 words is ~250 tokens; the old hard cap of 300 left no headroom.
DEFAULT_CAPTION_MAX_TOKENS = 600
# With reasoning on, OpenRouter's max_tokens covers reasoning + output ("high" ~ 80% reasoning)
MIN_MAX_TOKENS_WITH_REASONING = 1200
MAX_CAPTION_MAX_TOKENS = 4000
# Extra attempts when a caption comes back cut off or far too short
TRUNCATION_RETRIES = 2

# Target word range per mode: MODE_WORD_RANGES comes from pipeline.prompts (the prompts state the same ranges)
TERMINAL_PUNCTUATION = ('.', '!', '?', '"', "'", ')', '…', '»', '”', '’')


def normalize_reasoning_effort(value: Any) -> str | None:
    """None/''/'none'/'off'/False -> None (reasoning disabled); otherwise the effort string."""
    if value is None or value is False:
        return None
    text = str(value).strip().lower()
    return None if text in ("", "none", "off", "false", "0") else text


def looks_truncated(text: str) -> bool:
    """A caption that does not end a sentence was almost certainly cut off by the token limit."""
    stripped = text.rstrip()
    return bool(stripped) and not stripped.endswith(TERMINAL_PUNCTUATION)


class CaptionTruncatedError(RuntimeError):
    """The model kept returning a cut-off caption (finish_reason 'length' or no closing sentence)."""


class CaptionValidationError(RuntimeError):
    """The caption failed the validation gate (word range, preamble/refusal residue, trigger, adult rule)."""

    def __init__(self, message: str, reasons: list[str] | None = None, text: str = "", model: str = ""):
        super().__init__(message)
        self.reasons = list(reasons or [])
        self.text = text
        self.model = model


class ModelRefusalError(Exception):
    """Raised when the vision-language model refuses to caption the image due to safety or content filter."""
    def __init__(self, message: str, raw_response: str = "", model: str = ""):
        super().__init__(message)
        self.raw_response = raw_response
        self.model = model


# Refusal indicators across providers
REFUSAL_PHRASES = [
    "cannot fulfill", "cannot assist", "cannot generate", "cannot describe", "cannot comply",
    "unable to fulfill", "unable to assist", "unable to generate", "unable to describe", "unable to comply",
    "sorry, but i cannot", "sorry, but i am unable", "sorry, i cannot", "sorry, i am unable",
    "i'm sorry, but i cannot", "i'm sorry, i cannot", "i am sorry, but i cannot", "i am sorry, i cannot",
    "must decline", "i must decline", "i decline to", "as an ai", "as a responsible ai",
    "content policy", "safety policy", "violates our policy", "violates our policies",
    "violates safety guidelines", "safety guidelines", "against our guidelines", "against my safety guidelines",
    "not permitted to describe", "not allowed to describe", "not allowed to generate",
    "i cannot provide a description", "i am unable to provide a description",
    "explicit sexual content", "not appropriate for me to describe", "inappropriate content"
]


def is_refusal_response(raw_text: str, choice_data: dict | None = None) -> bool:
    """Checks whether the response contains an explicit refusal or safety rejection."""
    if choice_data:
        finish_reason = str(choice_data.get("finish_reason") or "").lower()
        if finish_reason in ("content_filter", "safety", "refusal"):
            return True
        msg = choice_data.get("message") or {}
        if msg.get("refusal"):
            return True

    text_lower = raw_text.lower().strip()
    if not text_lower:
        return True

    for phrase in REFUSAL_PHRASES:
        if phrase in text_lower:
            return True

    # If the response is extremely brief and apologetic / negative
    words = text_lower.split()
    if len(words) < 15:
        if any(w in text_lower for w in ("sorry", "cannot", "unable", "refuse", "policy")):
            return True

    return False


# ----------------------------------------------------------------------------- Validation gate
# Chatty openers: the caption must start with the trigger, not with talk about the task
PREAMBLE_REGEX = re.compile(
    r"^(?:here(?:'s|’s| is| are)\b|sure\b|certainly\b|of course\b|okay\b|ok\b|absolutely\b|"
    r"(?:this|the) (?:image|photo|photograph|picture|frame|scene) (?:shows|depicts|features|captures|presents)\b|"
    r"in this (?:image|photo|photograph|picture)\b|caption\s*:|description\s*:)",
    re.IGNORECASE,
)
# Refusal residue / meta talk anywhere in an otherwise complete reply
RESIDUE_REGEX = re.compile(
    r"\bI (?:can(?:'|’)t|cannot|can not|won(?:'|’)t|am unable|(?:'|’)m unable|am not able|apologi[sz]e)\b|"
    r"\bI(?:'|’)m sorry\b|\bI am sorry\b|\bas an ai\b|\blanguage model\b|"
    r"\b(?:this|the) (?:image|photo|photograph|picture) (?:shows|depicts)\b|\bhere is (?:a|the) (?:caption|description)\b",
    re.IGNORECASE,
)
# Adult-only rule: minor-coded descriptors reject the caption (plain "girl" is rewritten to "adult woman")
MINOR_CODED_REGEX = re.compile(
    r"\b(?:teen(?:age|ager)?s?|pre-?teens?|school ?girls?|(?:young|little) girls?|child(?:ren)?|"
    r"juveniles?|underage|barely legal)\b",
    re.IGNORECASE,
)
GIRL_REGEX = re.compile(r"\bgirl(s)?\b", re.IGNORECASE)

# Errors that move a frame to the next cascade layer (L1 -> L2 -> L3) instead of failing it at once
CASCADE_ERRORS: tuple[type[BaseException], ...] = (ModelRefusalError, CaptionTruncatedError, CaptionValidationError)


def _strip_lead_tokens(text: str, trigger: str, video_hash: str | None) -> str:
    """Drop leading quotes/markdown and a leading trigger/hash so the preamble check sees the model's own opening."""
    words = text.strip().lstrip("\"'*#>`- ").split()
    for token in (trigger, video_hash):
        if token and words and words[0].strip(",.:;\"'").lower() == token.lower():
            words = words[1:]
    return " ".join(words).lstrip("\"'*#>`-:, ")


def validate_caption_gate(
    raw_text: str,
    cleaned: str,
    mode: str = "concept",
    trigger: str = "restrained_elegance",
    video_hash: str | None = None,
) -> list[str]:
    """Hard checks before a caption is accepted. Returns the reasons it is rejected (empty list = accepted)."""
    reasons: list[str] = []
    profile = get_profile(mode)
    words = cleaned.split()

    if not words or words[0].strip(",.:;").lower() != (trigger or "").strip().lower():
        reasons.append(f"trigger '{trigger}' is not at token 0")

    lo, hi = profile.hard_word_limits
    n = len(words)
    if n < lo:
        reasons.append(f"too short ({n} words, minimum {lo} for {profile.mode})")
    elif n > hi:
        reasons.append(f"too long ({n} words, maximum {hi} for {profile.mode})")

    opening = _strip_lead_tokens(raw_text, trigger, video_hash)
    if PREAMBLE_REGEX.match(opening):
        reasons.append(f"preamble instead of a caption: '{' '.join(opening.split()[:6])}'")
    residue = RESIDUE_REGEX.search(raw_text)
    if residue:
        reasons.append(f"refusal or meta residue: '{residue.group(0)}'")

    if looks_truncated(cleaned):
        reasons.append("does not end with a complete sentence")

    minor = MINOR_CODED_REGEX.search(raw_text)
    if minor:
        reasons.append(f"minor-coded descriptor '{minor.group(0)}' (adults-only rule)")
    return reasons


def empty_usage() -> dict[str, Any]:
    return {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0, "calls": 0, "cost_reported": False}


def accumulate_usage(usage: dict[str, Any] | None, body: dict[str, Any] | None) -> None:
    """Adds one OpenRouter response's `usage` (tokens + `cost` in USD credits) to the running totals."""
    if usage is None:
        return
    for key, val in empty_usage().items():
        usage.setdefault(key, val)
    usage["calls"] += 1
    u = (body or {}).get("usage") or {}
    try:
        usage["prompt_tokens"] += int(u.get("prompt_tokens") or 0)
        usage["completion_tokens"] += int(u.get("completion_tokens") or 0)
        if u.get("cost") is not None:
            usage["cost_usd"] += float(u["cost"])
            usage["cost_reported"] = True
    except (TypeError, ValueError):
        pass


def tag_hint_text(tags: list[str] | None) -> str:
    """User-message suffix with human-verified image tags (learn-as-you-tag). Empty when there are none."""
    names = [t.strip() for t in (tags or []) if t and t.strip()]
    if not names:
        return ""
    return (
        "\n\nVerified visible elements (confirmed by a human; describe them naturally where they appear, "
        "do not list them): " + ", ".join(dict.fromkeys(names)) + "."
    )


class OpenRouterCaptioner:
    """Dispatches 512px token-saving image payloads to OpenRouter (deepseek/deepseek-v4.1-flash & x-ai/grok-4.7)."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    @staticmethod
    def prepare_512px_payload(img_bgr: np.ndarray, max_dim: int = 512) -> str:
        """Downscales frame so max(w, h) <= max_dim (preserving aspect ratio) and returns base64 JPEG."""
        h, w = img_bgr.shape[:2]
        long_edge = max(h, w)
        if long_edge > max_dim:
            scale = max_dim / float(long_edge)
            new_w = max(16, int(round(w * scale)))
            new_h = max(16, int(round(h * scale)))
            small = cv2.resize(img_bgr, (new_w, new_h), interpolation=cv2.INTER_AREA)
        else:
            small = img_bgr

        success, encoded = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not success:
            raise RuntimeError("Failed to encode frame to JPEG")
        return base64.b64encode(encoded).decode("utf-8")

    @staticmethod
    def load_system_prompt(mode: str = "concept", trigger_word: str = "restrained_elegance", class_noun: str = "woman") -> str:
        """Renders the versioned system prompt for the mode (`pipeline/prompts/<mode>.md`, see `prompt_version`)."""
        return render_system_prompt(mode, trigger_word, class_noun)

    @staticmethod
    def load_user_prompt(mode: str = "concept", trigger_word: str = "restrained_elegance", class_noun: str = "woman") -> str:
        """Mode-specific user message whose word range matches the system prompt."""
        return render_user_prompt(mode, trigger_word, class_noun)

    @staticmethod
    def build_request_payload(
        base64_jpeg: str,
        system_prompt: str,
        model: str = "google/gemma-4-31b-it",
        max_tokens: int = DEFAULT_CAPTION_MAX_TOKENS,
        temperature: float = DEFAULT_CAPTION_TEMPERATURE,
        top_p: float = DEFAULT_CAPTION_TOP_P,
        reasoning_effort: str | None = None,
        user_text: str | None = None,
    ) -> dict[str, Any]:
        """Builds the OpenRouter payload. Enabling reasoning raises max_tokens so the visible caption fits."""
        reasoning_effort = normalize_reasoning_effort(reasoning_effort)
        if reasoning_effort:
            max_tokens = max(max_tokens, MIN_MAX_TOKENS_WITH_REASONING)
        max_tokens = max(64, min(int(max_tokens), MAX_CAPTION_MAX_TOKENS))
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text or render_user_prompt("concept")},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_jpeg}"}},
                    ],
                },
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": top_p,
            # OpenRouter returns token counts and the billed cost in `usage` when asked to
            "usage": {"include": True},
        }
        if reasoning_effort:
            payload["reasoning"] = {
                "effort": reasoning_effort,
                "exclude": True,
            }
        return payload

    @staticmethod
    def sanitize_and_validate_caption(
        raw_caption: str,
        mode: str = "concept",
        trigger: str = "restrained_elegance",
        video_hash: str | None = None,
        class_noun: str | None = None,
    ) -> tuple[str, list[str]]:
        """Cleans caption and audits against krea2-character-concept-captioner linter standards.

        Guarantees that `trigger` is placed at Token 0, and `video_hash` (if provided) is
        placed immediately adjacent at Token 1 for LoRA scene identification and disambiguation.
        Per-mode trigger profile checks (`pipeline.prompts.TRIGGER_PROFILES`) only add warnings.
        """
        cleaned = raw_caption.strip()
        warnings: list[str] = []
        profile = get_profile(mode)

        # 1. Strip WebUI-style bracket weights
        cleaned = BRACKET_WEIGHT_REGEX.sub("", cleaned).strip()

        # 2. Strip banned Danbooru sludge tokens
        for s in BANNED_SLUDGE:
            pattern = re.compile(re.escape(s), re.IGNORECASE)
            if pattern.search(cleaned):
                cleaned = pattern.sub("", cleaned)
                warnings.append(f"Stripped sludge token: '{s}'")

        # Adults only: a bare "girl" becomes "adult woman" (minor-coded phrases are rejected by the gate)
        if GIRL_REGEX.search(cleaned):
            cleaned = GIRL_REGEX.sub(lambda m: "adult women" if m.group(1) else "adult woman", cleaned)
            warnings.append("Replaced 'girl' with 'adult woman'")

        # Normalize redundant spaces/commas
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        cleaned = re.sub(r",\s*,+", ",", cleaned).strip(", ")

        # 3. Ensure Token 0 trigger and Token 1 video_hash
        words = cleaned.split()
        if not words:
            prefix = f"{trigger} {video_hash}".strip() if video_hash else trigger
            cleaned = f"{prefix} Photograph of a scene."
        else:
            first = words[0].strip(",.:;")
            if first.lower() != trigger.lower():
                to_prepend = [trigger]
                if video_hash:
                    to_prepend.append(video_hash)
                cleaned = f"{' '.join(to_prepend)} {cleaned}"
                warnings.append(f"Prepend trigger '{trigger}' and hash '{video_hash}' to Token 0 position")
            elif video_hash:
                # Trigger is already at Token 0. Ensure video_hash is at Token 1.
                second = words[1].strip(",.:;") if len(words) > 1 else ""
                if second.lower() != video_hash.lower():
                    # Insert video_hash immediately after trigger
                    rest = " ".join(words[1:])
                    cleaned = f"{words[0]} {video_hash} {rest}".strip()
                    warnings.append(f"Inserted video title hash '{video_hash}' adjacent to trigger '{trigger}'")

        # 4. Length audit against the mode's requested word range
        n_words = len(cleaned.split())
        lo, hi = profile.word_range
        if n_words < lo:
            warnings.append(f"Caption is short ({n_words} words, expected {lo}-{hi})")
        elif n_words > hi:
            warnings.append(f"Caption is long ({n_words} words, expected {lo}-{hi})")
        if looks_truncated(cleaned):
            warnings.append("Caption does not end with a complete sentence")

        # 5. Trigger profile: the trigger is used exactly once; character captions name the class noun early
        trig_re = re.compile(r"(?<![\w-])" + re.escape(trigger) + r"(?![\w-])", re.IGNORECASE) if trigger else None
        if trig_re and len(trig_re.findall(cleaned)) > 1:
            warnings.append(f"Trigger '{trigger}' appears more than once")
        if profile.class_noun_in_opening and class_noun:
            noun = primary_class_noun(class_noun)
            opening = " ".join(cleaned.split()[:12]).lower()
            if not re.search(r"\b" + re.escape(noun.lower()) + r"s?\b", opening):
                warnings.append(f"Class noun '{noun}' missing from the opening (expected '{trigger} ... {noun}')")
        for pattern in profile.discouraged:
            hit = re.search(pattern, cleaned, re.IGNORECASE)
            if hit:
                warnings.append(f"Discouraged in {profile.mode} mode: '{hit.group(0)}'")

        return cleaned, warnings

    async def caption_single_frame(
        self,
        client: httpx.AsyncClient,
        img_bgr: np.ndarray,
        api_key: str,
        model: str = "google/gemma-4-31b-it",
        mode: str = "concept",
        trigger: str = "restrained_elegance",
        class_noun: str = "woman",
        max_tokens: int = DEFAULT_CAPTION_MAX_TOKENS,
        temperature: float = DEFAULT_CAPTION_TEMPERATURE,
        top_p: float = DEFAULT_CAPTION_TOP_P,
        reasoning_effort: str | None = None,
        max_dim: int = 512,
        video_hash: str | None = None,
        usage: dict[str, Any] | None = None,
        tag_hints: list[str] | None = None,
    ) -> tuple[str, list[str]]:
        """Sends single frame to OpenRouter and returns sanitized caption + validation warnings.

        `tag_hints` are image tags a human confirmed on this frame; the model is asked to name them if visible.

        Raises ModelRefusalError / CaptionTruncatedError / CaptionValidationError (the cascade errors)
        when the reply is unusable. Every billed response is added to `usage` (see accumulate_usage).
        """
        mode = normalize_mode(mode)
        base64_img = self.prepare_512px_payload(img_bgr, max_dim=max_dim)
        sys_prompt = self.load_system_prompt(mode, trigger, class_noun)
        payload = self.build_request_payload(
            base64_img,
            sys_prompt,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            reasoning_effort=reasoning_effort,
            user_text=self.load_user_prompt(mode, trigger, class_noun) + tag_hint_text(tag_hints),
        )
        if usage is not None:
            usage.setdefault("prompt_version", prompt_version(mode))

        headers = {
            "Authorization": f"Bearer {api_key.strip()}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:8000",
            "X-Title": "Captioning Pipeline Local Extractor",
        }

        # Send with retries on HTTP 429/5xx/timeouts and separate retries for cut-off captions
        max_retries = 3
        backoff = 2.0
        retry_attempts = 0
        truncation_retries = 0
        min_words = MODE_WORD_RANGES[mode][0]

        while True:
            try:
                resp = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    json=payload,
                    headers=headers,
                    timeout=60.0,
                )
            except (httpx.TimeoutException, httpx.NetworkError) as ex:
                retry_attempts += 1
                if retry_attempts >= max_retries:
                    raise RuntimeError(f"OpenRouter network/timeout error after {max_retries} attempts: {ex}") from ex
                await asyncio.sleep(backoff)
                backoff *= 2.0
                continue

            if resp.status_code == 200:
                data = resp.json()
                accumulate_usage(usage, data)
                choice = data["choices"][0]
                raw_text = choice["message"].get("content") or ""
                hit_limit = choice.get("finish_reason") == "length"
                if usage is not None:
                    usage["finish_reason"] = choice.get("finish_reason")

                # Refusals are short and complete; detect them before the too-short retry re-bills them.
                # (Empty content with finish_reason 'length' is a reasoning-starved reply, not a refusal.)
                if not hit_limit and is_refusal_response(raw_text, choice_data=choice):
                    raise ModelRefusalError(
                        f"Model '{model}' refused image description: {raw_text[:120]}",
                        raw_response=raw_text,
                        model=model,
                    )

                # A caption cut off by the token limit (or a fragment far below the requested length) is
                # never accepted: retry without reasoning and with a larger budget, then give up loudly.
                too_short = bool(raw_text.strip()) and len(raw_text.split()) < min_words // 2
                if hit_limit or (raw_text.strip() and (looks_truncated(raw_text) or too_short)):
                    if truncation_retries < TRUNCATION_RETRIES:
                        truncation_retries += 1
                        payload.pop("reasoning", None)
                        payload["max_tokens"] = min(MAX_CAPTION_MAX_TOKENS, int(payload["max_tokens"]) * 2)
                        continue
                    if hit_limit or looks_truncated(raw_text):
                        raise CaptionTruncatedError(
                            f"Model '{model}' returned a cut-off caption after {truncation_retries + 1} attempts "
                            f"(finish_reason={choice.get('finish_reason')}, {len(raw_text.split())} words)"
                        )
                    # Complete but still short after retries: the validation gate below decides

                cleaned, warnings = self.sanitize_and_validate_caption(
                    raw_text, mode=mode, trigger=trigger, video_hash=video_hash, class_noun=class_noun
                )
                reasons = validate_caption_gate(raw_text, cleaned, mode=mode, trigger=trigger, video_hash=video_hash)
                if reasons:
                    raise CaptionValidationError(
                        f"Model '{model}' caption rejected: {'; '.join(reasons)}",
                        reasons=reasons,
                        text=cleaned,
                        model=model,
                    )
                return cleaned, warnings

            elif resp.status_code in (400, 403):
                err_text = resp.text
                if any(k in err_text.lower() for k in ("moderation", "safety", "policy", "refused", "blocked", "filter")):
                    raise ModelRefusalError(
                        f"HTTP {resp.status_code} content filter refusal from {model}: {err_text}",
                        raw_response=err_text,
                        model=model,
                    )
                raise RuntimeError(f"OpenRouter API Error {resp.status_code}: {err_text}")
            elif resp.status_code in (429, 500, 502, 503, 504):
                retry_attempts += 1
                if retry_attempts >= max_retries:
                    raise RuntimeError(f"OpenRouter Transient Error ({resp.status_code}) after {max_retries} attempts: {resp.text}")
                await asyncio.sleep(backoff)
                backoff *= 2.0
            else:
                raise RuntimeError(f"OpenRouter API Error {resp.status_code}: {resp.text}")
