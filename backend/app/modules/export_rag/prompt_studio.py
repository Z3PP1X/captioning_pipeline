"""LoRA Prompt Studio & Krea 2 Prompt Linter (Module D.2).

Combines retrieved dataset exemplars (from `LocalRagEngine`), compatible Character + Style/Concept
triggerwords, and user scene descriptions to synthesize 3 grounded Krea 2 / Flux / SDXL prompt archetypes:
- Archetype A (`a`): Concise comma-separated tag prompt (~25-50 tokens)
- Archetype B (`b`): Short anchor prose (~50-120 tokens)
- Archetype C (`c`): Dense visual narrative (~180-280 tokens)

Supports OpenRouter LLM generation as well as a deterministic local grounded synthesizer fallback
when offline or no API key is configured.
"""
from __future__ import annotations

import json
import re
from typing import Any, Awaitable, Callable

import httpx

from backend.app.config import get_app_settings
from backend.app.db.session import DatabaseManager, get_db
from backend.app.modules.captioning.rejection_guard import (
    BANNED_SLUDGE,
    BARE_GIRL_REGEX,
    BRACKET_WEIGHT_REGEX,
    MINOR_CODED_REGEX,
    STRIPPABLE_PREAMBLE_PREFIX_REGEX,
)
from backend.app.modules.export_rag.rag_engine import LocalRagEngine

PROMPT_STUDIO_SYSTEM_PROMPT = """You are an expert photographic director and visual prompt architect for Krea 2 (MMDiT architecture powered by Qwen3-VL text encoder) and LoRA inference.

Your mission is to synthesize the user's scene request, active Character/Style/Concept triggerwords ({triggers_display}), and the provided REAL DATASET CAPTIONS into three optimal Krea 2 inference prompt archetypes:

### MANDATORY RULES:
1. Adult Subjects Only (21+): Explicitly designate human subjects as adults (e.g., '21 year old adult woman', 'athletic adult woman in her twenties'). NEVER use minor-coded descriptors.
2. Triggerword Integration: Include {triggers_display} accurately (begin with the primary trigger at Token 0, and weave any secondary style/character trigger naturally).
3. Grounding: Draw sensory vocabulary, clothing/restraint details, spatial staging, and lighting optics directly from the retrieved dataset exemplars.
4. ZERO Fluff: Never include Danbooru quality sludge ('masterpiece', '8k', 'photorealistic', 'ultra detailed', 'trending on artstation') or bracket weights ('(x:1.4)').
5. Output STRICT JSON format matching the schema below without markdown fences or preamble:
{
  "archetype_a": "string (~25-50 tokens comma-separated tags)",
  "archetype_b": "string (~50-120 tokens 1-2 punchy anchor sentences)",
  "archetype_c": "string (~180-280 tokens dense visual narrative)"
}
"""


def estimate_token_count(text: str) -> int:
    """Estimate Qwen3-VL / CLIP BPE token count via word + punctuation heuristic (~1.15 tokens/word)."""
    cleaned = (text or "").strip()
    if not cleaned:
        return 0
    words = cleaned.split()
    punct = len(re.findall(r"[,;.:!?-]", cleaned))
    return int(round(len(words) * 1.15 + punct * 0.25))


def lint_prompt(
    raw_prompt: str,
    *,
    archetype: str = "short",
    trigger_word: str = "ohwx_subject",
    style_trigger: str | None = None,
    character: str | None = None,
) -> dict[str, Any]:
    """Sanitize and audit a generated prompt against Krea 2 standards and archetype token budgets."""
    cleaned = (raw_prompt or "").strip()
    warnings: list[str] = []

    m_pre = STRIPPABLE_PREAMBLE_PREFIX_REGEX.match(cleaned)
    if m_pre and m_pre.group(0).strip():
        cleaned = cleaned[m_pre.end() :].strip()
        warnings.append("Stripped leading preamble")

    if BRACKET_WEIGHT_REGEX.search(cleaned):
        cleaned = BRACKET_WEIGHT_REGEX.sub("", cleaned).strip()
        warnings.append("Stripped WebUI bracket weights")

    for sludge in BANNED_SLUDGE:
        pat = re.compile(r"\b" + re.escape(sludge) + r"\b", re.IGNORECASE)
        if pat.search(cleaned):
            cleaned = pat.sub("", cleaned)
            warnings.append(f"Stripped quality sludge token: '{sludge}'")

    if MINOR_CODED_REGEX.search(cleaned):
        cleaned = MINOR_CODED_REGEX.sub("adult woman", cleaned)
        warnings.append("Replaced prohibited minor-coded term with 'adult woman'")

    if BARE_GIRL_REGEX.search(cleaned):
        cleaned = BARE_GIRL_REGEX.sub(
            lambda m: "adult women" if m.group(1) else "adult woman",
            cleaned,
        )
        warnings.append("Replaced bare 'girl' with 'adult woman'")

    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r",\s*,+", ",", cleaned).strip(", ")

    # Ensure primary trigger_word at Token 0
    trig = (trigger_word or "").strip()
    if trig and trig != "--no-trigger":
        words = cleaned.split()
        first = words[0].strip(",.:;\"'") if words else ""
        if first.lower() != trig.lower():
            sep = ", " if archetype == "tags" else " "
            cleaned = f"{trig}{sep}{cleaned}".strip()
            warnings.append(f"Prepended primary trigger '{trig}' at Token 0")

    # Ensure secondary style_trigger is present if requested
    s_trig = (style_trigger or "").strip()
    if s_trig and s_trig.lower() != trig.lower():
        if s_trig.lower() not in cleaned.lower():
            if archetype == "tags":
                cleaned = f"{cleaned.rstrip('.')}, {s_trig}"
            else:
                cleaned = f"{cleaned.rstrip('.')} in {s_trig} aesthetic."
            warnings.append(f"Appended secondary style trigger '{s_trig}'")

    if character and character.strip():
        if character.strip().lower() not in cleaned.lower():
            warnings.append(f"Character '{character}' not explicitly named in prompt")

    tok_count = estimate_token_count(cleaned)
    return {
        "prompt": cleaned,
        "token_count": tok_count,
        "warnings": warnings,
        "is_valid": True,
    }


class LoRAPromptStudio:
    """Grounded LoRA Prompt Generator combining dataset RAG exemplars and multi-LoRA triggers."""

    def __init__(
        self,
        db: DatabaseManager | None = None,
        rag_engine: LocalRagEngine | None = None,
        *,
        mock_llm_fn: Callable[[str, str, str], dict[str, str] | Awaitable[dict[str, str]]] | None = None,
    ) -> None:
        self.db = db or get_db()
        self.rag_engine = rag_engine or LocalRagEngine(self.db)
        self.app_settings = get_app_settings()
        self.mock_llm_fn = mock_llm_fn

    @staticmethod
    def _synthesize_grounded_local(
        query: str,
        selected_tags: dict[str, list[str]],
        exemplars: list[dict[str, Any]],
        primary_trigger: str,
        style_trigger: str | None = None,
        character: str | None = None,
    ) -> dict[str, str]:
        """Deterministic local prompt synthesis grounded in retrieved dataset domain segments."""
        subj = f"{character}, a 25-year-old adult woman" if character else "a 25-year-old athletic adult woman"
        user_idea = (query or "").strip().rstrip(".") or "posed with poised composure in a studio frame"

        # Extract domain details from top retrieved dataset exemplars
        equip_clauses: list[str] = []
        surr_clauses: list[str] = []
        pos_clauses: list[str] = []
        light_clauses: list[str] = []
        matched_tag_tokens: list[str] = []

        for ex in exemplars[:3]:
            if ex.get("equipment_text"):
                equip_clauses.append(str(ex["equipment_text"]).strip())
            if ex.get("surroundings_text"):
                surr_clauses.append(str(ex["surroundings_text"]).strip())
            if ex.get("position_text"):
                pos_clauses.append(str(ex["position_text"]).strip())
            if ex.get("lighting_text"):
                light_clauses.append(str(ex["lighting_text"]).strip())
            for mt in ex.get("matched_tags") or []:
                if mt not in matched_tag_tokens:
                    matched_tag_tokens.append(str(mt))

        for _, t_list in (selected_tags or {}).items():
            for t in t_list or []:
                if t and str(t) not in matched_tag_tokens:
                    matched_tag_tokens.append(str(t))

        equip_snippet = (
            equip_clauses[0]
            if equip_clauses
            else "wearing tailored structured attire with tactile fabric folds and polished metallic hardware accents."
        )
        pos_snippet = (
            pos_clauses[0]
            if pos_clauses
            else "She holds a balanced upright posture with defined shoulder tension and deliberate gaze."
        )
        surr_snippet = (
            surr_clauses[0]
            if surr_clauses
            else "The scene is staged inside an architectural interior with textured surfaces and subtle floor reflections."
        )
        light_snippet = (
            light_clauses[0]
            if light_clauses
            else "Illuminated by directional key lighting that sculpts high-contrast chiaroscuro shadows, captured with an 85mm prime portrait lens at f/2.8 with shallow depth of field."
        )

        style_clause = f", {style_trigger}" if style_trigger else ""
        style_prose = f" Rendered in {style_trigger} visual style." if style_trigger else ""
        tag_str = ", ".join(matched_tag_tokens[:8]) if matched_tag_tokens else "chiaroscuro lighting, 85mm lens, shallow depth of field"

        # Archetype A: Concise comma-separated tag prompt (~25-50 tokens)
        arch_a = (
            f"{primary_trigger}{style_clause}, photograph, {subj}, {user_idea}, "
            f"{tag_str}, tactile surface textures, directional key light, 85mm portrait optics"
        )

        # Archetype B: Short anchor prose (~50-120 tokens)
        arch_b = (
            f"{primary_trigger} Photograph of {subj}, {user_idea}. "
            f"{pos_snippet} {light_snippet}{style_prose}"
        )

        # Archetype C: Dense visual narrative (~180-280 tokens)
        arch_c = (
            f"{primary_trigger} Photograph of {subj}, {user_idea}. "
            f"{equip_snippet} {pos_snippet} {surr_snippet} {light_snippet} "
            f"Fine surface micro-contrast, natural skin texture, and precise optical depth ground the three-dimensional composition.{style_prose}"
        )

        return {
            "archetype_a": arch_a,
            "archetype_b": arch_b,
            "archetype_c": arch_c,
        }

    async def generate(
        self,
        *,
        query: str,
        selected_tags: dict[str, list[str]] | None = None,
        dataset_id: str | None = None,
        trigger_word: str = "ohwx_subject",
        style_trigger: str | None = None,
        character: str | None = None,
        model: str = "google/gemini-2.5-pro",
        top_k: int = 4,
        n_variations: int = 1,
        api_key_override: str | None = None,
    ) -> dict[str, Any]:
        """Retrieve top-K dataset exemplars and synthesize grounded Krea 2 prompts (Archetypes A, B, C)."""
        tags_dict = selected_tags or {}
        exemplars = await self.rag_engine.retrieve(
            query=query,
            dataset_id=dataset_id,
            tag_filters=tags_dict,
            top_k=max(1, int(top_k)),
        )

        prim_trig = (trigger_word or "").strip() or "ohwx_subject"
        sec_trig = (style_trigger or "").strip() or None
        triggers_display = f"'{prim_trig}'" + (f" and '{sec_trig}'" if sec_trig else "")

        raw_archetypes: dict[str, str] | None = None
        notices: list[str] = []

        if self.mock_llm_fn is not None:
            res = self.mock_llm_fn(query, prim_trig, model)
            if hasattr(res, "__await__"):
                res = await res  # type: ignore[misc]
            raw_archetypes = dict(res)
        else:
            api_key = self.db.resolve_openrouter_api_key(api_key_override)
            if api_key and model not in ("local", "local_template"):
                sys_prompt = PROMPT_STUDIO_SYSTEM_PROMPT.replace("{triggers_display}", triggers_display)
                ex_text = "\n\n".join(
                    f"--- Exemplar {i} ({ex.get('stem')}) ---\n{ex.get('full_caption')}"
                    for i, ex in enumerate(exemplars, 1)
                ) or "No dataset exemplars indexed yet."
                user_prompt = (
                    f"User scene description: {query}\n"
                    f"Selected tags: {json.dumps(tags_dict)}\n"
                    f"Primary trigger: {prim_trig}\n"
                    f"Secondary style trigger: {sec_trig or 'None'}\n"
                    f"Character: {character or 'None'}\n\n"
                    f"Retrieved Dataset Exemplars:\n{ex_text}\n\n"
                    f"Return strict JSON with keys 'archetype_a', 'archetype_b', 'archetype_c'."
                )
                try:
                    url = f"{self.app_settings.openrouter_base_url}/chat/completions"
                    headers = {
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    }
                    payload = {
                        "model": model,
                        "messages": [
                            {"role": "system", "content": sys_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "temperature": 0.65,
                        "max_tokens": 900,
                        "response_format": {"type": "json_object"},
                    }
                    async with httpx.AsyncClient(timeout=45.0) as client:
                        resp = await client.post(url, json=payload, headers=headers)
                    if resp.status_code == 200:
                        content = resp.json()["choices"][0]["message"].get("content") or ""
                        cleaned_json = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
                        parsed = json.loads(cleaned_json)
                        if isinstance(parsed, dict) and "archetype_a" in parsed:
                            raw_archetypes = {
                                "archetype_a": str(parsed.get("archetype_a") or ""),
                                "archetype_b": str(parsed.get("archetype_b") or ""),
                                "archetype_c": str(parsed.get("archetype_c") or ""),
                            }
                except Exception as exc:
                    notices.append(f"OpenRouter synthesis unavailable ({exc}); used local grounded synthesizer.")

        if raw_archetypes is None:
            raw_archetypes = self._synthesize_grounded_local(
                query=query,
                selected_tags=tags_dict,
                exemplars=exemplars,
                primary_trigger=prim_trig,
                style_trigger=sec_trig,
                character=character,
            )

        lint_a = lint_prompt(
            raw_archetypes.get("archetype_a", ""),
            archetype="tags",
            trigger_word=prim_trig,
            style_trigger=sec_trig,
            character=character,
        )
        lint_b = lint_prompt(
            raw_archetypes.get("archetype_b", ""),
            archetype="short",
            trigger_word=prim_trig,
            style_trigger=sec_trig,
            character=character,
        )
        lint_c = lint_prompt(
            raw_archetypes.get("archetype_c", ""),
            archetype="dense",
            trigger_word=prim_trig,
            style_trigger=sec_trig,
            character=character,
        )

        return {
            "model": model,
            "trigger_word": prim_trig,
            "style_trigger": sec_trig,
            "character": character,
            "grounded": len(exemplars) > 0,
            "notices": notices,
            "archetypes": {
                "a": {
                    "name": "Archetype A (Tags)",
                    "prompt": lint_a["prompt"],
                    "token_count": lint_a["token_count"],
                    "warnings": lint_a["warnings"],
                },
                "b": {
                    "name": "Archetype B (Short Anchor)",
                    "prompt": lint_b["prompt"],
                    "token_count": lint_b["token_count"],
                    "warnings": lint_b["warnings"],
                },
                "c": {
                    "name": "Archetype C (Dense Narrative)",
                    "prompt": lint_c["prompt"],
                    "token_count": lint_c["token_count"],
                    "warnings": lint_c["warnings"],
                },
            },
            "rag_context": exemplars,
        }
