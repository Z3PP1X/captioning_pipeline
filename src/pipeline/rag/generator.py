"""RAG Prompt Generator synthesizing user clicks and retrieved context with OpenRouter Gemma or local model."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from pipeline.config import Settings, get_settings
from pipeline.rag.linter import lint_prompt
from pipeline.rag.taxonomy import CHARACTERS, TAXONOMY_CATEGORIES

logger = logging.getLogger(__name__)

GEMMA_SYSTEM_INSTRUCTION = """You are an expert photographic director and visual prompt architect for Krea 2 (MMDiT architecture powered by Qwen3-VL text encoder) and the fine-art LoRA '{trigger}'.

Your mission is to synthesize the user's selected scene elements and custom description, strictly grounded in the provided REAL DATASET CAPTIONS, into three optimal Krea 2 prompt archetypes:

### MANDATORY RULES:
1. Adult Subjects Only (25+): Explicitly designate subjects as adults (e.g. 'athletic adult woman in her thirties', 'fair-skinned adult woman'). NEVER use minor-coded descriptors.
2. Token 0 Trigger: Begin every prompt with '{trigger}' at Token 0.
3. Grounding: Draw sensory vocabulary, rigging terms, tactile hardware details, and chiaroscuro staging directly from the retrieved dataset context.
4. ZERO Fluff: Never include Danbooru quality sludge ('masterpiece', '8k', 'photorealistic', 'ultra detailed', 'trending on artstation') or bracket weights ('(x:1.4)').
5. Output STRICT JSON format matching the schema below without any markdown fences, preamble, or conversational commentary.

### PROMPT ARCHETYPES:
- ARCHETYPE A (Tags): Concise comma-separated tokens (~25-50 tokens).
  Formula: {trigger}, photograph, [subject & demographics], [model position], [bondage discipline], [gear & materials], [rigging location/knot], [environment], [lighting], [optics]
- ARCHETYPE B (Short Anchor): 1-2 punchy, dense sentences (~50-120 tokens).
  Formula: {trigger} Photograph of [adult subject] [position] upon [environment]. Bound in [gear] with [rigging] causing [skin indentation dynamics]. Staged in [lighting] and captured with [optics].
- ARCHETYPE C (Dense Visual Narrative): Flowing connected narrative covering all 8 domains (~180-280 tokens).
  Detail anatomical geometry, muscle tension, fiber cordage fuzz or specular metal reflections, skin compression marks, environmental textures, chiaroscuro directional key light, and 85mm f/2.8 optics.

### OUTPUT JSON SCHEMA:
{
  "archetype_a": "string",
  "archetype_b": "string",
  "archetype_c": "string"
}
"""


class RagPromptGenerator:
    """Generates Krea 2 prompts using Gemma models via OpenRouter or local Ollama."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    @staticmethod
    def _format_context_summary(retrieved_items: list[dict[str, Any]]) -> str:
        """Formats top retrieved real captions into readable grounding context for the LLM."""
        if not retrieved_items:
            return "No specific dataset exemplars retrieved."
        lines: list[str] = []
        for i, item in enumerate(retrieved_items[:4], 1):
            stem = item.get("stem", f"item_{i}")
            cap = item.get("full_caption", "")
            lines.append(f"--- Dataset Exemplar {i} ({stem}) ---\n{cap}")
        return "\n\n".join(lines)

    @staticmethod
    def _format_user_selections(
        selected_tags: dict[str, list[str]],
        user_description: str,
        character: str | None = None,
    ) -> str:
        """Formats user-clicked categories, description, and character into clear requirements."""
        lines: list[str] = []
        if character and character.lower() in CHARACTERS:
            c = CHARACTERS[character.lower()]
            lines.append(f"- Character: {c['name']} ('{c['description']}')")
        if user_description.strip():
            lines.append(f"- User Description / Idea: {user_description.strip()}")

        for cat_key, tag_ids in selected_tags.items():
            if not tag_ids:
                continue
            cat_info = TAXONOMY_CATEGORIES.get(cat_key, {})
            title = cat_info.get("title", cat_key)
            labels: list[str] = []
            for _, items in cat_info.get("groups", {}).items():
                for item in items:
                    if item["id"] in tag_ids:
                        labels.append(item["label"])
            if labels:
                lines.append(f"- Selected {title}: {', '.join(labels)}")

        return "\n".join(lines) if lines else "- Default artistic scene"

    async def generate(
        self,
        retrieved_items: list[dict[str, Any]],
        selected_tags: dict[str, list[str]],
        user_description: str = "",
        character: str | None = None,
        model: str = "google/gemma-4-31b-it",
        trigger: str = "restrained_elegance",
        api_key: str | None = None,
        local_url: str | None = None,
    ) -> dict[str, Any]:
        """Synthesizes RAG context, character specification, and user clicks into Archetypes A, B, and C."""
        effective_trigger = trigger.strip() or "restrained_elegance"
        system_prompt = GEMMA_SYSTEM_INSTRUCTION.replace("{trigger}", effective_trigger)

        char_prompt_chunk = ""
        if character and character.lower() in CHARACTERS:
            c = CHARACTERS[character.lower()]
            char_prompt_chunk = (
                f"\n\n### MANDATORY CHARACTER SPECIFICATION:\n"
                f"The subject MUST be {c['name']}. You MUST depict her according to her exact profile: '{c['description']}'.\n"
                f"- In ARCHETYPE A (Tags): Must begin with '{effective_trigger}, {c['name'].lower()}, 21yo woman, {', '.join(c['tag_tokens'][2:])}' as the subject tokens right after the trigger.\n"
                f"- In ARCHETYPE B (Short Anchor): Must name and describe '{c['name']}, a 21 year old {'petite ' if 'petite' in c['description'] else ''}woman with {c['tag_tokens'][2]} and {c['tag_tokens'][3]}' as the subject.\n"
                f"- In ARCHETYPE C (Dense Narrative): Must feature {c['name']} ('{c['description']}') throughout the narrative, describing how the restraints press against her skin, highlighting her dark brown eyes and hair.\n"
            )

        user_content = (
            f"Please generate the 3 Krea 2 prompts for '{effective_trigger}' based on these user requirements and grounded in the retrieved dataset context:{char_prompt_chunk}\n\n"
            f"### USER REQUIREMENTS:\n{self._format_user_selections(selected_tags, user_description, character)}\n\n"
            f"### RETRIEVED REAL DATASET EXEMPLARS:\n{self._format_context_summary(retrieved_items)}\n\n"
            f"Remember: Output strictly JSON with 'archetype_a', 'archetype_b', 'archetype_c'. No preamble."
        )

        raw_json_str = ""

        # Dispatch to local Ollama or OpenRouter
        if model == "local" or (local_url and local_url.strip()):
            url = (local_url.strip() or "http://localhost:11434").rstrip("/")
            raw_json_str = await self._call_ollama(url, system_prompt, user_content)
        else:
            key = (api_key or self.settings.openrouter_api_key or "").strip()
            if not key:
                raise ValueError("OpenRouter API key is missing. Configure it in .env or settings.")
            raw_json_str = await self._call_openrouter(key, model, system_prompt, user_content)

        # Parse JSON
        parsed_data = self._clean_and_parse_json(raw_json_str)

        # Lint and validate all three archetypes
        arch_a_lint = lint_prompt(parsed_data.get("archetype_a", ""), archetype="tags", trigger=effective_trigger, character=character)
        arch_b_lint = lint_prompt(parsed_data.get("archetype_b", ""), archetype="short", trigger=effective_trigger, character=character)
        arch_c_lint = lint_prompt(parsed_data.get("archetype_c", ""), archetype="dense", trigger=effective_trigger, character=character)

        return {
            "model": model,
            "trigger": effective_trigger,
            "character": character,
            "archetypes": {
                "a": {
                    "name": "Archetype A (Tags)",
                    "prompt": arch_a_lint["clean_prompt"],
                    "token_count": arch_a_lint["token_count"],
                    "warnings": arch_a_lint["warnings"],
                    "is_valid": arch_a_lint["is_valid"],
                },
                "b": {
                    "name": "Archetype B (Short Anchor)",
                    "prompt": arch_b_lint["clean_prompt"],
                    "token_count": arch_b_lint["token_count"],
                    "warnings": arch_b_lint["warnings"],
                    "is_valid": arch_b_lint["is_valid"],
                },
                "c": {
                    "name": "Archetype C (Dense Narrative)",
                    "prompt": arch_c_lint["clean_prompt"],
                    "token_count": arch_c_lint["token_count"],
                    "warnings": arch_c_lint["warnings"],
                    "is_valid": arch_c_lint["is_valid"],
                },
            },
            "rag_context": [
                {
                    "id": item.get("id"),
                    "stem": item.get("stem"),
                    "score": item.get("score"),
                    "matched_tags": item.get("matched_tags", []),
                    "img_rel_path": item.get("img_rel_path"),
                    "full_caption": item.get("full_caption"),
                }
                for item in retrieved_items
            ],
        }

    async def _call_openrouter(self, api_key: str, model: str, system_prompt: str, user_prompt: str) -> str:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:8000",
            "X-Title": "Krea 2 RAG Prompt Generator",
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.70,
            "max_tokens": 800,
            "response_format": {"type": "json_object"},
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload)
            if resp.status_code != 200:
                raise RuntimeError(f"OpenRouter error {resp.status_code}: {resp.text}")
            data = resp.json()
            return data["choices"][0]["message"].get("content") or ""

    async def _call_ollama(self, base_url: str, system_prompt: str, user_prompt: str) -> str:
        payload = {
            "model": "gemma2:2b",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "format": "json",
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(f"{base_url}/api/chat", json=payload)
            if resp.status_code != 200:
                raise RuntimeError(f"Ollama error {resp.status_code}: {resp.text}")
            data = resp.json()
            return data.get("message", {}).get("content", "")

    @staticmethod
    def _clean_and_parse_json(text: str) -> dict[str, str]:
        """Cleans and extracts JSON dict from LLM response."""
        cleaned = text.strip()
        # Remove markdown code block if present
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

        # Regex fallback for JSON object
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if match:
            try:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass

        # Return fallback with raw text
        return {
            "archetype_a": "restrained_elegance, photograph, scene.",
            "archetype_b": f"restrained_elegance Photograph of a scene. {cleaned[:100]}",
            "archetype_c": cleaned,
        }
