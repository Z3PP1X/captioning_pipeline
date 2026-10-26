"""Deterministic Algorithmic Rejection-Guard (`RejectionDetector`) for VLM Caption Validation (Module C.4).

Implements a zero-ML multi-stage validation pipeline:
1. Provider Metadata Check (`finish_reason` in `content_filter`/`safety`/`refusal` or `message.refusal`).
2. Empty / Short / Truncation Check (`None`, whitespace-only, `< min_chars=15`, `< hard_min_words`,
   `finish_reason == "length"`, or missing terminal sentence punctuation).
3. First-200-Char Quote-Normalized Refusal Regex Matching (including exact required pattern +
   extended provider safety/policy phrases + user custom regexes).
4. Meta-Talk & Hallucination Check (detects missing-image complaints like "Please provide an image",
   "I don't see an image", conversational questions ending in '?', mid-paragraph AI residue, and
   strips benign leading labels like "Caption:" or "Here is a description:").
5. Adult-Only Rule & Danbooru Sludge Sanitization (strips bracket weights and quality buzzwords,
   rewrites bare "girl" -> "adult woman", and rejects minor-coded terms).
6. Triggerword Positioning Verification (`prefix`, `suffix`, `natural_integration`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from backend.app.modules.captioning.skills import (
    TriggerPosition,
    apply_trigger_word,
    primary_class_noun,
    verify_trigger_placement,
)

TERMINAL_PUNCTUATION = (".", "!", "?", '"', "'", ")", "…", "»", "”", "’")

# Required core refusal regex pattern from specification
REQUIRED_REFUSAL_REGEX = re.compile(
    r"\b(i\s*can'?t|i\s*cannot|i\s*am\s*unable|i'm\s*unable|i\s*apologize|sorry\s*but\s*i|"
    r"as\s*an\s*ai|against\s*my\s*safety|cannot\s*fulfill|can'?t\s*assist|unable\s*to\s*provide|"
    r"cannot\s*describe)\b",
    re.IGNORECASE,
)

# Extended provider refusal patterns (first 200 chars & full text)
EXTENDED_REFUSAL_HEAD_REGEX = re.compile(
    r"\b(?:"
    r"i\s+(?:can(?:'t|not)|am\s+unable|must\s+decline|won'?t|will\s+not|decline\s+to)|"
    r"sorry,?\s+(?:but\s+)?i\s+(?:can(?:'t|not)|am\s+unable|won'?t)|"
    r"i(?:'m|\s+am)\s+sorry,?\s+(?:but\s+)?i|"
    r"unable\s+to\s+(?:fulfill|assist|generate|describe|comply|provide)|"
    r"cannot\s+(?:fulfill|assist|generate|describe|comply|provide)|"
    r"violates?\s+(?:our\s+|my\s+)?(?:safety|content)\s+(?:policy|policies|guidelines)|"
    r"against\s+(?:our|my)\s+(?:safety\s+)?(?:policy|policies|guidelines)|"
    r"as\s+an?\s+(?:ai|helpful|responsible)\s+(?:assistant|language\s+model|model)|"
    r"not\s+(?:appropriate|permitted|allowed)\s+(?:for\s+me\s+)?to\s+(?:describe|generate|assist)|"
    r"explicit\s+sexual\s+content|inappropriate\s+content"
    r")\b",
    re.IGNORECASE,
)

# Meta-talk / hallucination / missing-image patterns
META_TALK_NO_IMAGE_REGEX = re.compile(
    r"\b(?:"
    r"please\s+(?:provide|upload|share|attach)\s+(?:an?\s+|the\s+)?(?:image|photo|picture|file)|"
    r"i\s+(?:don'?t|do\s+not|cannot|can'?t)\s+see\s+(?:an?\s+|any\s+|the\s+)?(?:image|photo|picture)|"
    r"no\s+(?:image|photo|picture)\s+(?:was\s+)?(?:provided|attached|uploaded|found)|"
    r"could\s+you\s+(?:please\s+)?(?:provide|upload|share|clarify)|"
    r"what\s+would\s+you\s+like\s+me\s+to\s+(?:describe|do)|"
    r"how\s+can\s+i\s+(?:help|assist)\s+you\s+today"
    r")\b",
    re.IGNORECASE,
)

# Strippable benign conversational preamble prefixes (e.g. "Here is a description: ...")
STRIPPABLE_PREAMBLE_PREFIX_REGEX = re.compile(
    r"^(?:"
    r"(?:sure|certainly|of\s+course|okay|absolutely)[!.,]?\s*"
    r"(?:here(?:'s|\s+is)\s+(?:a|the)\s+(?:detailed\s+)?(?:caption|description)(?:\s+of\s+the\s+(?:image|photo|photograph))?\s*[:\-]\s*)?|"
    r"here(?:'s|\s+is)\s+(?:a|the)\s+(?:detailed\s+)?(?:caption|description)(?:\s+of\s+the\s+(?:image|photo|photograph))?\s*[:\-]\s*|"
    r"(?:caption|description)\s*[:\-]\s*"
    r")+",
    re.IGNORECASE,
)

# Conversational meta-opening that talks *about* the image instead of starting with the caption
META_OPENING_REGEX = re.compile(
    r"^(?:(?:this|the)\s+(?:image|photo|photograph|picture|frame|scene)\s+(?:shows|depicts|features|captures|presents|illustrates)\b|"
    r"in\s+this\s+(?:image|photo|photograph|picture|frame|scene)\b)",
    re.IGNORECASE,
)

# Mid-paragraph AI / refusal residue
MID_TEXT_RESIDUE_REGEX = re.compile(
    r"\b(?:as\s+an\s+ai\b|language\s+model\b|i\s+apologi[sz]e\b|i\s+cannot\s+fulfill\b|"
    r"against\s+my\s+safety\s+guidelines\b)",
    re.IGNORECASE,
)

# Minor-coded terms (strictly prohibited -> rejected)
MINOR_CODED_REGEX = re.compile(
    r"\b(?:teen(?:age|ager)?s?|pre-?teens?|school\s*girls?|(?:young|little)\s+girls?|child(?:ren)?|"
    r"juveniles?|underage|barely\s+legal)\b",
    re.IGNORECASE,
)
BARE_GIRL_REGEX = re.compile(r"\bgirl(s)?\b", re.IGNORECASE)

# Danbooru quality sludge & WebUI bracket weights
BANNED_SLUDGE = (
    "masterpiece",
    "best quality",
    "high quality",
    "8k",
    "4k",
    "photorealistic",
    "hyperrealistic",
    "ultra detailed",
    "ultra-detailed",
    "extremely detailed",
    "award winning",
    "trending on artstation",
    "unreal engine",
    "octane render",
)
BRACKET_WEIGHT_REGEX = re.compile(
    r"(\([^\)]+:\d+(\.\d+)?\)|\[[^\]]+:\d+(\.\d+)?\]|\(\([^\)]+\)\))"
)


def normalize_quotes_and_whitespace(text: str) -> str:
    """Normalize smart apostrophes/quotes and collapse whitespace for deterministic regex matching."""
    out = (
        (text or "")
        .replace("’", "'")
        .replace("‘", "'")
        .replace("“", '"')
        .replace("”", '"')
        .replace("`", "'")
    )
    out = re.sub(r"[,;]+", " ", out)
    return re.sub(r"\s+", " ", out).strip()


def looks_truncated(text: str) -> bool:
    """Return True if non-empty text does not end with terminal sentence punctuation."""
    stripped = (text or "").rstrip()
    if not stripped:
        return True
    return not stripped.endswith(TERMINAL_PUNCTUATION)


@dataclass(slots=True)
class RejectionAuditResult:
    """Structured result of `RejectionDetector.evaluate`."""

    accepted: bool
    cleaned_caption: str
    rejection_type: str | None = None
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "cleaned_caption": self.cleaned_caption,
            "rejection_type": self.rejection_type,
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
        }


class RejectionDetector:
    """Deterministic algorithmic Rejection-Guard for VLM caption validation."""

    def __init__(
        self,
        *,
        min_chars: int = 15,
        min_words: int = 12,
        max_words: int = 350,
        custom_regexes: list[str] | None = None,
        strip_benign_preamble: bool = True,
        reject_meta_opening: bool = False,
    ) -> None:
        self.min_chars = max(1, int(min_chars))
        self.min_words = max(1, int(min_words))
        self.max_words = max(self.min_words + 10, int(max_words))
        self.strip_benign_preamble = strip_benign_preamble
        self.reject_meta_opening = reject_meta_opening

        self.custom_patterns: list[re.Pattern[str]] = []
        for pat_str in custom_regexes or []:
            if pat_str and pat_str.strip():
                try:
                    self.custom_patterns.append(re.compile(pat_str.strip(), re.IGNORECASE))
                except re.error:
                    pass

    def evaluate(
        self,
        raw_text: str | None,
        *,
        finish_reason: str | None = None,
        choice_metadata: dict[str, Any] | None = None,
        trigger_word: str | None = None,
        trigger_position: TriggerPosition = "prefix",
        class_noun: str | None = None,
        require_class_noun: bool = False,
        discouraged_regexes: list[str] | tuple[str, ...] = (),
        min_words_override: int | None = None,
        max_words_override: int | None = None,
        video_hash: str | None = None,
    ) -> RejectionAuditResult:
        """Audit a raw VLM caption response and return a `RejectionAuditResult`."""
        reasons: list[str] = []
        warnings: list[str] = []

        # ------------------------------------------------------------------
        # 1. Provider Metadata Safety / Content-Filter Check
        # ------------------------------------------------------------------
        f_reason = str(
            finish_reason
            or (choice_metadata or {}).get("finish_reason")
            or ""
        ).strip().lower()

        if f_reason in ("content_filter", "safety", "refusal"):
            return RejectionAuditResult(
                accepted=False,
                cleaned_caption="",
                rejection_type="provider_safety_filter",
                reasons=[f"Provider finish_reason='{f_reason}' indicates safety/content filter refusal"],
            )

        if choice_metadata and isinstance(choice_metadata.get("message"), dict):
            provider_refusal = choice_metadata["message"].get("refusal")
            if provider_refusal:
                return RejectionAuditResult(
                    accepted=False,
                    cleaned_caption="",
                    rejection_type="provider_safety_filter",
                    reasons=[f"Provider message.refusal: {str(provider_refusal)[:160]}"],
                )

        # ------------------------------------------------------------------
        # 2. Null / Empty / Minimum Character Check (< 15 chars)
        # ------------------------------------------------------------------
        if raw_text is None:
            return RejectionAuditResult(
                accepted=False,
                cleaned_caption="",
                rejection_type="too_short",
                reasons=["Response is null (None)"],
            )

        stripped_raw = raw_text.strip()
        if not stripped_raw:
            r_type = "truncated_length" if f_reason == "length" else "too_short"
            return RejectionAuditResult(
                accepted=False,
                cleaned_caption="",
                rejection_type=r_type,
                reasons=["Response is empty or whitespace-only"],
            )

        # ------------------------------------------------------------------
        # 3. First-200-Char Refusal Pattern Matching (before length check so
        #    short refusals like "I'm sorry, I can't assist." are classified
        #    as `refusal_regex`!)
        # ------------------------------------------------------------------
        head_raw = stripped_raw[:200]
        head_norm = normalize_quotes_and_whitespace(head_raw)

        req_hit = REQUIRED_REFUSAL_REGEX.search(head_norm) or REQUIRED_REFUSAL_REGEX.search(head_raw)
        if req_hit:
            return RejectionAuditResult(
                accepted=False,
                cleaned_caption="",
                rejection_type="refusal_regex",
                reasons=[f"Matched refusal pattern in opening 200 chars: '{req_hit.group(0)}'"],
            )

        ext_hit = EXTENDED_REFUSAL_HEAD_REGEX.search(head_norm) or EXTENDED_REFUSAL_HEAD_REGEX.search(head_raw)
        if ext_hit:
            return RejectionAuditResult(
                accepted=False,
                cleaned_caption="",
                rejection_type="refusal_regex",
                reasons=[f"Matched provider refusal pattern: '{ext_hit.group(0)}'"],
            )

        for custom_re in self.custom_patterns:
            c_hit = custom_re.search( stripped_raw )
            if c_hit:
                return RejectionAuditResult(
                    accepted=False,
                    cleaned_caption="",
                    rejection_type="refusal_regex",
                    reasons=[f"Matched custom rejection regex '{custom_re.pattern}': '{c_hit.group(0)}'"],
                )

        # ------------------------------------------------------------------
        # 4. Meta-Talk / Missing-Image / Question Detection
        # ------------------------------------------------------------------
        meta_no_img = META_TALK_NO_IMAGE_REGEX.search(normalize_quotes_and_whitespace(stripped_raw))
        if meta_no_img:
            return RejectionAuditResult(
                accepted=False,
                cleaned_caption="",
                rejection_type="meta_preamble",
                reasons=[f"Model emitted meta-talk instead of image description: '{meta_no_img.group(0)}'"],
            )

        residue_hit = MID_TEXT_RESIDUE_REGEX.search(normalize_quotes_and_whitespace(stripped_raw))
        if residue_hit:
            return RejectionAuditResult(
                accepted=False,
                cleaned_caption="",
                rejection_type="refusal_regex",
                reasons=[f"Mid-caption AI/refusal residue detected: '{residue_hit.group(0)}'"],
            )

        # Strip surrounding quotes or markdown fences if model wrapped the caption
        working = stripped_raw.strip("\"'` ")
        if self.strip_benign_preamble:
            m_pre = STRIPPABLE_PREAMBLE_PREFIX_REGEX.match(working)
            if m_pre and m_pre.group(0).strip():
                working = working[m_pre.end():].lstrip("\"'` ")
                warnings.append(f"Stripped conversational preamble: '{m_pre.group(0).strip()}'")

        # Check if model asked a question back to the user (ends with '?')
        if working.rstrip().endswith("?"):
            return RejectionAuditResult(
                accepted=False,
                cleaned_caption="",
                rejection_type="meta_preamble",
                reasons=["Model response ends with a question ('?') instead of a declarative visual description"],
            )

        if self.reject_meta_opening and META_OPENING_REGEX.match(working):
            return RejectionAuditResult(
                accepted=False,
                cleaned_caption="",
                rejection_type="meta_preamble",
                reasons=[f"Conversational meta-opening detected: '{' '.join(working.split()[:6])}'"],
            )
        elif META_OPENING_REGEX.match(working):
            warnings.append("Caption begins with conversational phrasing ('This image shows...')")

        # ------------------------------------------------------------------
        # 5. Adult-Only Guardrail (Minor-Coded Check)
        # ------------------------------------------------------------------
        minor_hit = MINOR_CODED_REGEX.search(working)
        if minor_hit:
            return RejectionAuditResult(
                accepted=False,
                cleaned_caption="",
                rejection_type="minor_coded_rule",
                reasons=[f"Prohibited minor-coded descriptor '{minor_hit.group(0)}' (adults-only 21+ rule)"],
            )

        # ------------------------------------------------------------------
        # 6. Truncation & Minimum Length Check
        # ------------------------------------------------------------------
        if len(working) < self.min_chars:
            return RejectionAuditResult(
                accepted=False,
                cleaned_caption=working,
                rejection_type="too_short",
                reasons=[f"Caption length ({len(working)} chars) is below minimum ({self.min_chars} chars)"],
            )

        if f_reason == "length":
            return RejectionAuditResult(
                accepted=False,
                cleaned_caption=working,
                rejection_type="truncated_length",
                reasons=["Provider finish_reason='length' indicates token limit truncation"],
            )

        if looks_truncated(working):
            return RejectionAuditResult(
                accepted=False,
                cleaned_caption=working,
                rejection_type="truncated_length",
                reasons=["Caption is truncated (missing terminal sentence punctuation '.', '!', or '.')"],
            )

        # ------------------------------------------------------------------
        # 7. Sludge & Bracket Weight Sanitization + Trigger Placement
        # ------------------------------------------------------------------
        if BRACKET_WEIGHT_REGEX.search(working):
            working = BRACKET_WEIGHT_REGEX.sub("", working).strip()
            warnings.append("Stripped WebUI bracket weights")

        for sludge in BANNED_SLUDGE:
            pat = re.compile(r"\b" + re.escape(sludge) + r"\b", re.IGNORECASE)
            if pat.search(working):
                working = pat.sub("", working)
                warnings.append(f"Stripped banned quality sludge token: '{sludge}'")

        if BARE_GIRL_REGEX.search(working):
            working = BARE_GIRL_REGEX.sub(
                lambda m: "adult women" if m.group(1) else "adult woman",
                working,
            )
            warnings.append("Replaced bare 'girl' with 'adult woman'")

        working = re.sub(r"\s+", " ", working).strip()
        working = re.sub(r",\s*,+", ",", working).strip(", ")

        if trigger_word and trigger_word.strip():
            working, trig_warnings = apply_trigger_word(
                working,
                trigger_word=trigger_word,
                position=trigger_position,
                video_hash=video_hash,
            )
            warnings.extend(trig_warnings)
            trig_ok, trig_err = verify_trigger_placement(working, trigger_word, trigger_position)
            if not trig_ok and trig_err:
                reasons.append(trig_err)

        # ------------------------------------------------------------------
        # 8. Word Count Bounds & Skill Linter Warnings
        # ------------------------------------------------------------------
        words = working.split()
        n_words = len(words)
        eff_min_words = min_words_override if min_words_override is not None else self.min_words
        eff_max_words = max_words_override if max_words_override is not None else self.max_words

        if n_words < eff_min_words:
            return RejectionAuditResult(
                accepted=False,
                cleaned_caption=working,
                rejection_type="too_short",
                reasons=[f"Caption has {n_words} words, below minimum {eff_min_words} words"],
                warnings=warnings,
            )

        if n_words > eff_max_words:
            warnings.append(f"Caption word count ({n_words}) exceeds target ceiling ({eff_max_words})")

        if require_class_noun and class_noun:
            noun = primary_class_noun(class_noun)
            opening_window = " ".join(words[:14]).lower()
            if not re.search(r"\b" + re.escape(noun.lower()) + r"s?\b", opening_window):
                warnings.append(f"Class noun '{noun}' not found in opening clause")

        for disc_pat in discouraged_regexes:
            try:
                hit = re.search(disc_pat, working, re.IGNORECASE)
                if hit:
                    warnings.append(f"Discouraged trait/style phrase detected: '{hit.group(0)}'")
            except re.error:
                pass

        if reasons:
            return RejectionAuditResult(
                accepted=False,
                cleaned_caption=working,
                rejection_type="meta_preamble",
                reasons=reasons,
                warnings=warnings,
            )

        return RejectionAuditResult(
            accepted=True,
            cleaned_caption=working,
            rejection_type=None,
            reasons=[],
            warnings=warnings,
        )
