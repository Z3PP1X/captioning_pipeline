"""OpenRouter API client for VLM batch captioning with 512px token saving and krea2 linter."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
from pathlib import Path
from typing import Any, Callable

import cv2
import httpx
import numpy as np

from pipeline.config import Settings, get_settings

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

# Target word range per mode (must match the ranges stated in load_system_prompt)
MODE_WORD_RANGES = {"concept": (120, 180), "character": (80, 140), "style": (75, 130)}
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
    def _read_skill_reference(mode: str) -> str:
        """Loads skill reference files from disk to provide full role and taxonomy context to the OpenRouter model."""
        skill_roots = [
            Path("/mnt/c/Users/Patri/.gemini/config/skills"),
            Path("C:/Users/Patri/.gemini/config/skills"),
            Path.home() / ".gemini" / "config" / "skills",
        ]
        base_dir = next((p for p in skill_roots if p.is_dir()), None)
        if not base_dir:
            return ""

        snippets: list[str] = []
        char_concept_dir = base_dir / "krea2-character-concept-captioner"
        taxonomy_file = base_dir / "shibari-bondage-taxonomy" / "SKILL.md"

        try:
            if mode == "character":
                guide = char_concept_dir / "references" / "character_captioning_guide.md"
                if guide.is_file():
                    snippets.append(guide.read_text(encoding="utf-8")[:2500])
            elif mode == "style":
                guide = char_concept_dir / "references" / "concept_captioning_guide.md"
                if guide.is_file():
                    snippets.append(guide.read_text(encoding="utf-8")[:2000])
            else:
                guide = char_concept_dir / "references" / "concept_captioning_guide.md"
                if guide.is_file():
                    snippets.append(guide.read_text(encoding="utf-8")[:2200])
                if taxonomy_file.is_file():
                    snippets.append(taxonomy_file.read_text(encoding="utf-8")[:2800])
        except Exception:
            pass

        if not snippets:
            return ""
        return "\n\n--- SKILL & TAXONOMY REFERENCE CONTEXT ---\n" + "\n\n".join(snippets)

    @staticmethod
    def load_system_prompt(mode: str = "concept", trigger_word: str = "restrained_elegance", class_noun: str = "woman") -> str:
        """Returns the expert VLM system prompt with loaded skill context and strict equipment/tie priority."""
        trigger = trigger_word.strip() or "restrained_elegance"
        noun = class_noun.strip() or "woman"
        skill_context = OpenRouterCaptioner._read_skill_reference(mode)

        if mode == "character":
            base_prompt = (
                f"You are an expert visual director annotating a character training dataset for the Krea 2 foundation model.\n"
                f"Describe this image adhering to these strict rules:\n"
                f"1. Refer to the human subject at Token 0 as: '{trigger} Photograph of a {noun}...'\n"
                f"2. DO NOT describe permanent, immutable facial geometry (never mention eye shape, facial bone structure, nose shape, or ethnicity).\n"
                f"3. PRIMARY FOCUS: Thoroughly describe all ties, restraints, equipment, hardware, clothing, fabrics, and accessories.\n"
                f"4. SECONDARY (Keep Brief): Briefly note posture, expression, background, and lighting in 1-2 short clauses.\n"
                f"DO NOT include quality buzzwords ('masterpiece', '8k', 'photorealistic'). Output a single cohesive paragraph between 80 and 140 words (strictly under 300 tokens)."
            )
        elif mode == "style":
            base_prompt = (
                f"You are an objective visual annotator creating dataset descriptions for a style model.\n"
                f"Describe this image following the Content Inversion rule:\n"
                f"1. Begin with: '{trigger} Photograph of...'\n"
                f"2. Describe all objects, equipment, ties, people, clothing, and actions in plain, objective detail.\n"
                f"3. DO NOT describe the artistic style, color grading, film grain, vintage look, lighting aesthetics, or artistic technique.\n"
                f"Avoid quality praise. Output a single cohesive paragraph between 75 and 130 words (strictly under 300 tokens)."
            )
        else:  # Concept / Restraint default
            base_prompt = (
                f"You are an expert visual director and restraint taxonomist annotating a concept dataset for the Krea 2 foundation model (MMDiT architecture with Qwen3-VL text encoder).\n"
                f"Describe this scene in a single cohesive, photographic paragraph adhering to these Krea 2 training principles:\n"
                f"1. Token 0 Anchor: Start Line 1 with '{trigger} Photograph of...'\n"
                f"2. Subject Decoupling: State the adult subject's demographic, build, and skin tone to avoid baking identity into the concept trigger.\n"
                f"3. Posture & Anatomical Tension: Describe body stance, limb angles, joint positions, and muscle tension as they interact with the restraints.\n"
                f"4. Ties, Bondage, Hardware & Materials Focus: Thoroughly describe the knots, rigging patterns (e.g. takate-kote box tie, hishime harness, frog tie, suspension lines), cordage/chain materials (e.g. 6mm unbleached Japanese hemp rope, welded steel chains, chrome shackles, leather cuffs/collars, spreader bars, steel cages), and visible skin indentation/compression lines.\n"
                f"5. Spatial Setting & Optics: Ground the scene with the immediate environment, floor reflections, chiaroscuro directional lighting, and shallow depth of field to ensure Qwen3-VL learns cohesive 3D geometry and realistic surface textures without token bleeding.\n"
                f"6. Formatting: Avoid Danbooru tags and bracket weights. DO NOT include quality buzzwords ('masterpiece', '8k', 'photorealistic'). Output flowing natural prose between 120 and 180 words (strictly under 300 tokens)."
            )

        return base_prompt + skill_context

    @staticmethod
    def build_request_payload(
        base64_jpeg: str,
        system_prompt: str,
        model: str = "google/gemma-4-31b-it",
        max_tokens: int = DEFAULT_CAPTION_MAX_TOKENS,
        temperature: float = 0.80,
        top_p: float = 0.90,
        reasoning_effort: str | None = None,
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
                        {
                            "type": "text",
                            "text": (
                                "Describe this image for Krea 2 LoRA dataset training following the system instructions and skill context. "
                                "Focus on the specific ties, bondage mechanics, and equipment while maintaining cohesive scene context (posture, tension, setting, chiaroscuro lighting). "
                                "Output a single flowing paragraph between 120 and 180 words, strictly under 300 tokens."
                            ),
                        },
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_jpeg}"}},
                    ],
                },
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": top_p,
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
    ) -> tuple[str, list[str]]:
        """Cleans caption and audits against krea2-character-concept-captioner linter standards.
        
        Guarantees that `trigger` is placed at Token 0, and `video_hash` (if provided) is
        placed immediately adjacent at Token 1 for LoRA scene identification and disambiguation.
        """
        cleaned = raw_caption.strip()
        warnings: list[str] = []

        # 1. Strip WebUI-style bracket weights
        cleaned = BRACKET_WEIGHT_REGEX.sub("", cleaned).strip()

        # 2. Strip banned Danbooru sludge tokens
        for s in BANNED_SLUDGE:
            pattern = re.compile(re.escape(s), re.IGNORECASE)
            if pattern.search(cleaned):
                cleaned = pattern.sub("", cleaned)
                warnings.append(f"Stripped sludge token: '{s}'")

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
        lo, hi = MODE_WORD_RANGES.get(mode, MODE_WORD_RANGES["concept"])
        if n_words < lo:
            warnings.append(f"Caption is short ({n_words} words, expected {lo}-{hi})")
        elif n_words > hi:
            warnings.append(f"Caption is long ({n_words} words, expected {lo}-{hi})")
        if looks_truncated(cleaned):
            warnings.append("Caption does not end with a complete sentence")

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
        temperature: float = 0.80,
        top_p: float = 0.90,
        reasoning_effort: str | None = None,
        max_dim: int = 512,
        video_hash: str | None = None,
    ) -> tuple[str, list[str]]:

        """Sends single frame to OpenRouter and returns sanitized caption + validation warnings."""
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
        )

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
        min_words = MODE_WORD_RANGES.get(mode, MODE_WORD_RANGES["concept"])[0]

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
                choice = data["choices"][0]
                raw_text = choice["message"].get("content") or ""
                hit_limit = choice.get("finish_reason") == "length"

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
                    # Complete but short after retries: accept, sanitize_and_validate_caption warns

                return self.sanitize_and_validate_caption(
                    raw_text, mode=mode, trigger=trigger, video_hash=video_hash
                )

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
