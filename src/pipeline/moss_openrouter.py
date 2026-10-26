"""Asynchronous OpenRouter Client with 3-Tier Fallback Cascade for MOSS-Image.

Cascade hierarchy:
  1. Primary: qwen/qwen3-vl-72b-instruct
  2. Fallback 1: google/gemini-2.5-pro
  3. Fallback 2: mistralai/pixtral-large-2411

Handles structured JSON output (response_format={"type": "json_object"}),
refusal and schema validation, automatic cascading on 429/5xx/refusals,
trait leakage scanning, trigger phrase injection, and auto-sanitization.
"""
from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any

import cv2
import httpx
import numpy as np
from pydantic import BaseModel, Field

from pipeline.config import Settings, get_settings
from pipeline.moss_validator import (
    detect_refusal,
    extract_json_object,
    sanitize_leaks,
    scan_trait_leakage,
    validate_and_inject_triggers,
)
from pipeline.prompts.moss_prompts import (
    compute_moss_prompt_version,
    normalize_moss_mode,
    render_moss_system_prompt,
)

logger = logging.getLogger("moss.openrouter")

PRIMARY_MODEL = "qwen/qwen3-vl-72b-instruct"
FALLBACK_1_MODEL = "google/gemini-2.5-pro"
FALLBACK_2_MODEL = "mistralai/pixtral-large-2411"

DEFAULT_CASCADE_TIERS: list[tuple[str, str]] = [
    ("primary", PRIMARY_MODEL),
    ("fallback_1", FALLBACK_1_MODEL),
    ("fallback_2", FALLBACK_2_MODEL),
]


class MossCascadeExhaustedError(RuntimeError):
    """Raised when all cascade tiers (primary, fallback 1, fallback 2) fail or refuse."""

    def __init__(self, message: str, failures: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.failures = failures or []


class MossCaptionTiers(BaseModel):
    long: str
    medium: str
    short: str


class MossWatermarkItem(BaseModel):
    box_2d: list[int] = Field(default_factory=list)
    label: str = ""


class MossResult(BaseModel):
    caption: MossCaptionTiers
    watermark: list[MossWatermarkItem] = Field(default_factory=list)
    borders: list[str] = Field(default_factory=lambda: ["none"])
    model_used: str  # "primary", "fallback_1", "fallback_2"
    model_name: str
    cascade_tier: str
    leaked_traits: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    prompt_version: str = ""
    raw_response: dict[str, Any] = Field(default_factory=dict)


class MossCaptionOptions(BaseModel):
    mode: str = "character"
    class_noun: str = "woman"
    locked_traits: list[str] = Field(default_factory=list)
    trigger_phrase: str | None = None
    training_mode: str | None = None
    auto_sanitize: bool = False
    primary_model: str | None = None
    fallback_1: str | None = None
    fallback_2: str | None = None
    timeout: float = 60.0
    max_tokens: int = 1500
    temperature: float = 0.4


def _str_to_base64(val: str) -> str:
    """Converts a string file path or data URI to raw base64."""
    path = Path(val)
    if path.is_file():
        return base64.b64encode(path.read_bytes()).decode("utf-8")
    if val.startswith("data:image"):
        return val.split(",", 1)[-1].strip()
    if len(val) > 64 and " " not in val:
        return val.strip()
    raise ValueError("String is neither an existing file path nor a valid base64 payload")


def prepare_image_base64(image_input: str | bytes | Path | np.ndarray) -> str:
    """Converts numpy array, file path, bytes, or base64 string into base64 JPEG format."""
    if isinstance(image_input, np.ndarray):
        ok, enc = cv2.imencode(".jpg", image_input, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if not ok:
            raise ValueError("Failed to encode numpy image array to JPEG")
        return base64.b64encode(enc).decode("utf-8")

    if isinstance(image_input, Path):
        return base64.b64encode(image_input.read_bytes()).decode("utf-8")

    if isinstance(image_input, str):
        return _str_to_base64(image_input)

    if isinstance(image_input, bytes):
        return base64.b64encode(image_input).decode("utf-8")

    raise ValueError(f"Unsupported image input type: {type(image_input)}")


def build_cascade_models(options: MossCaptionOptions) -> list[tuple[str, str]]:
    """Builds the 3-tier cascade list based on options or defaults."""
    primary = options.primary_model or PRIMARY_MODEL
    fb1 = options.fallback_1 or FALLBACK_1_MODEL
    fb2 = options.fallback_2 or FALLBACK_2_MODEL
    return [
        ("primary", primary),
        ("fallback_1", fb1),
        ("fallback_2", fb2),
    ]


def _build_payload(base64_img: str, sys_prompt: str, model: str, options: MossCaptionOptions) -> dict[str, Any]:
    """Constructs OpenRouter chat completion payload with JSON object enforcement."""
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Describe this image across all 3 tiers according to your instructions. "
                            "Output strictly valid JSON matching the schema."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{base64_img}"},
                    },
                ],
            },
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": options.max_tokens,
        "temperature": options.temperature,
    }


def _process_response_payload(
    parsed: dict[str, Any],
    tier_info: tuple[str, str],
    options: MossCaptionOptions,
) -> MossResult:
    """Validates traits, injects triggers, applies auto-sanitization and returns MossResult."""
    tier_name, model_name = tier_info
    leaked = scan_trait_leakage(parsed, options.locked_traits)
    norm_mode = normalize_moss_mode(options.mode)
    prompt_ver = compute_moss_prompt_version(norm_mode)

    updated_data, warnings = validate_and_inject_triggers(
        parsed,
        trigger_phrase=options.trigger_phrase,
        class_noun=options.class_noun,
    )

    cap = updated_data.get("caption", {})
    long_t = str(cap.get("long") or "")
    med_t = str(cap.get("medium") or "")
    short_t = str(cap.get("short") or "")

    if options.auto_sanitize and leaked:
        long_t = sanitize_leaks(long_t, leaked)
        med_t = sanitize_leaks(med_t, leaked)
        short_t = sanitize_leaks(short_t, leaked)
        warnings.append(f"Auto-sanitized leaked traits: {', '.join(leaked)}")

    raw_wm = updated_data.get("watermark") or []
    watermark_items = [
        MossWatermarkItem(
            box_2d=item.get("box_2d", []),
            label=str(item.get("label", "")),
        )
        for item in raw_wm
        if isinstance(item, dict)
    ]

    raw_borders = updated_data.get("borders") or ["none"]
    borders = [str(b) for b in raw_borders] if isinstance(raw_borders, list) else ["none"]

    return MossResult(
        caption=MossCaptionTiers(long=long_t, medium=med_t, short=short_t),
        watermark=watermark_items,
        borders=borders,
        model_used=tier_name,
        model_name=model_name,
        cascade_tier=tier_name,
        leaked_traits=leaked,
        warnings=warnings,
        prompt_version=prompt_ver,
        raw_response=parsed,
    )


class MossOpenRouterClient:
    """Asynchronous client with 3-tier fallback cascade for MOSS-Image captioning."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    async def _post_chat(
        self,
        client: httpx.AsyncClient,
        payload: dict[str, Any],
        api_key: str,
        timeout_seconds: float,
    ) -> tuple[int, str]:
        """Dispatches an async POST request to OpenRouter API."""
        headers = {
            "Authorization": f"Bearer {api_key.strip()}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:8000",
            "X-Title": "MOSS-Image Disentanglement Pipeline",
        }
        resp = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            json=payload,
            headers=headers,
            timeout=timeout_seconds,
        )
        return resp.status_code, resp.text

    async def _attempt_tier(
        self,
        client: httpx.AsyncClient,
        base64_img: str,
        sys_prompt: str,
        tier_info: tuple[str, str],
        options: MossCaptionOptions,
        api_key: str,
    ) -> tuple[MossResult | None, str | None]:
        """Attempts a single tier in the cascade; returns (result, error_reason)."""
        tier_name, model_name = tier_info
        payload = _build_payload(base64_img, sys_prompt, model_name, options)

        try:
            status, text = await self._post_chat(client, payload, api_key, options.timeout)
        except Exception as ex:
            msg = f"Tier '{tier_name}' ({model_name}) transport error: {ex}"
            logger.warning(msg)
            return None, msg

        if status != 200:
            msg = f"Tier '{tier_name}' ({model_name}) HTTP {status}: {text[:140]}"
            logger.warning(msg)
            return None, msg

        try:
            raw_json = extract_json_object(text)
            choice = (raw_json.get("choices") or [{}])[0]
            content = choice.get("message", {}).get("content") or ""
        except Exception as ex:
            msg = f"Tier '{tier_name}' ({model_name}) unparseable outer JSON: {ex}"
            logger.warning(msg)
            return None, msg

        is_ref, ref_reason = detect_refusal(content)
        if is_ref:
            msg = f"Tier '{tier_name}' ({model_name}) refused/invalid: {ref_reason}"
            logger.warning(msg)
            return None, msg

        parsed = extract_json_object(content)
        result = _process_response_payload(parsed, tier_info, options)
        return result, None

    async def caption_image(
        self,
        client: httpx.AsyncClient,
        image_input: str | bytes | Path | np.ndarray,
        api_key: str | None = None,
        options: MossCaptionOptions | None = None,
    ) -> MossResult:
        """Executes MOSS captioning across the 3-tier cascade until successful."""
        opts = options or MossCaptionOptions()
        key = (api_key or self.settings.openrouter_api_key or "").strip()
        if not key:
            raise ValueError("OpenRouter API key is required for MOSS captioning")

        base64_img = prepare_image_base64(image_input)
        sys_prompt = render_moss_system_prompt(
            mode=opts.mode,
            class_noun=opts.class_noun,
            locked_traits=opts.locked_traits,
            training_mode=opts.training_mode,
        )

        cascade = build_cascade_models(opts)
        failures: list[dict[str, Any]] = []

        for tier_info in cascade:
            tier_name, model_name = tier_info
            result, err = await self._attempt_tier(client, base64_img, sys_prompt, tier_info, opts, key)
            if result is not None:
                return result
            failures.append({"tier": tier_name, "model": model_name, "error": err})

        err_summary = "; ".join(f"[{f['tier']} - {f['model']}]: {f['error']}" for f in failures)
        raise MossCascadeExhaustedError(
            f"All {len(cascade)} cascade tiers failed to generate valid MOSS caption: {err_summary}",
            failures=failures,
        )


__all__ = [
    "DEFAULT_CASCADE_TIERS",
    "FALLBACK_1_MODEL",
    "FALLBACK_2_MODEL",
    "PRIMARY_MODEL",
    "MossCaptionOptions",
    "MossCaptionTiers",
    "MossCascadeExhaustedError",
    "MossOpenRouterClient",
    "MossResult",
    "MossWatermarkItem",
    "build_cascade_models",
    "prepare_image_base64",
]
