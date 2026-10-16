"""Versioned VLM caption prompts (Phase G).

One template per mode lives next to this module (`concept.md`, `character.md`, `style.md`); each holds the
system prompt, then a line `=== USER ===`, then the user message. `glossary.md` is a compact shared
vocabulary (it replaces the ~7.5k character skill dump that used to be read from `~/.gemini` at runtime).
Placeholders: `{trigger}`, `{noun}`, `{lo}`, `{hi}`, `{glossary}`.

Every caption records `prompt_version(mode)`: `<mode>-<PROMPT_VERSION>-<sha8 of the template files>`.
Bump `PROMPT_VERSION` when the intent changes; the hash changes on any edit anyway.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PROMPT_VERSION = "g1"
PROMPT_DIR = Path(__file__).resolve().parent
MODES = ("concept", "character", "style")
DEFAULT_MODE = "concept"
DEFAULT_TRIGGER = "restrained_elegance"
DEFAULT_CLASS_NOUN = "woman"
USER_MARKER = "=== USER ==="


@dataclass(frozen=True)
class TriggerProfile:
    """How a mode places the trigger and class noun, and what its captions must not talk about."""

    mode: str
    # Target word range stated in the prompt (warnings outside it)
    word_range: tuple[int, int]
    # Opening words the caption must start with (after sanitizing: trigger, then the video title hash)
    lead: str
    # Character mode: the class noun is the subject reference right after the trigger ("<trigger> ... a woman")
    class_noun_in_opening: bool
    # Phrases this mode should avoid (identity traits for characters, style words for styles) -> warnings
    discouraged: tuple[str, ...] = ()

    @property
    def hard_word_limits(self) -> tuple[int, int]:
        """Outside these the caption is rejected by the validation gate (and retried on the next layer)."""
        lo, hi = self.word_range
        return lo // 2, int(hi * 1.5)


TRIGGER_PROFILES: dict[str, TriggerProfile] = {
    "concept": TriggerProfile(
        mode="concept",
        word_range=(120, 180),
        lead="{trigger} Photograph of",
        class_noun_in_opening=False,
    ),
    "character": TriggerProfile(
        mode="character",
        word_range=(80, 140),
        lead="{trigger} Photograph of a {noun}",
        class_noun_in_opening=True,
        discouraged=(
            r"\beye shape\b", r"\b(almond|hooded|monolid|deep-set)[- ]eyes?\b", r"\bnose shape\b",
            r"\b(button|aquiline|roman) nose\b", r"\bcheekbones?\b", r"\bjaw ?line\b", r"\bbone structure\b",
            r"\bethnicity\b", r"\b(asian|caucasian|latina|latino|african|european|hispanic)\b",
        ),
    ),
    "style": TriggerProfile(
        mode="style",
        word_range=(75, 130),
        lead="{trigger} Photograph of",
        class_noun_in_opening=False,
        discouraged=(
            r"\bfilm grain\b", r"\bcolou?r grading\b", r"\bvintage\b", r"\bcinematic\b", r"\baesthetic\b",
            r"\bartistic\b", r"\bmoody\b", r"\bretro\b",
        ),
    ),
}

MODE_WORD_RANGES: dict[str, tuple[int, int]] = {m: p.word_range for m, p in TRIGGER_PROFILES.items()}


def normalize_mode(mode: str | None) -> str:
    text = str(mode or "").strip().lower()
    return text if text in MODES else DEFAULT_MODE


def get_profile(mode: str | None) -> TriggerProfile:
    return TRIGGER_PROFILES[normalize_mode(mode)]


def primary_class_noun(class_noun: str | None) -> str:
    """'woman, ropes' -> 'woman': the first entry is the subject reference, the rest are hints."""
    first = re.split(r"[,;/]", str(class_noun or ""))[0].strip()
    return first or DEFAULT_CLASS_NOUN


@lru_cache(maxsize=None)
def _read(name: str) -> str:
    return (PROMPT_DIR / name).read_text(encoding="utf-8")


@lru_cache(maxsize=None)
def _templates(mode: str) -> tuple[str, str]:
    raw = _read(f"{mode}.md")
    system, _, user = raw.partition(USER_MARKER)
    return system.strip(), user.strip()


@lru_cache(maxsize=None)
def prompt_version(mode: str | None) -> str:
    m = normalize_mode(mode)
    digest = hashlib.sha256((_read(f"{m}.md") + "\n" + _read("glossary.md")).encode("utf-8")).hexdigest()[:8]
    return f"{m}-{PROMPT_VERSION}-{digest}"


def _values(mode: str, trigger: str | None, class_noun: str | None) -> dict[str, str]:
    lo, hi = TRIGGER_PROFILES[mode].word_range
    return {
        "trigger": (trigger or "").strip() or DEFAULT_TRIGGER,
        "noun": primary_class_noun(class_noun),
        "lo": str(lo),
        "hi": str(hi),
        "glossary": _read("glossary.md").strip(),
    }


def render_system_prompt(mode: str | None, trigger: str | None = None, class_noun: str | None = None) -> str:
    m = normalize_mode(mode)
    return _templates(m)[0].format_map(_values(m, trigger, class_noun))


def render_user_prompt(mode: str | None, trigger: str | None = None, class_noun: str | None = None) -> str:
    m = normalize_mode(mode)
    return _templates(m)[1].format_map(_values(m, trigger, class_noun))


def expected_lead(mode: str | None, trigger: str | None = None, class_noun: str | None = None) -> str:
    m = normalize_mode(mode)
    return TRIGGER_PROFILES[m].lead.format_map(_values(m, trigger, class_noun))


__all__ = [
    "DEFAULT_CLASS_NOUN",
    "DEFAULT_MODE",
    "DEFAULT_TRIGGER",
    "MODES",
    "MODE_WORD_RANGES",
    "PROMPT_VERSION",
    "TRIGGER_PROFILES",
    "TriggerProfile",
    "expected_lead",
    "get_profile",
    "normalize_mode",
    "primary_class_noun",
    "prompt_version",
    "render_system_prompt",
    "render_user_prompt",
]
