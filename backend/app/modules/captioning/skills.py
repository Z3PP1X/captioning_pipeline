"""Master Skill Templates (Krea 2 Character, Concept, Style) and Triggerword Positioning Engine (Module C.2 & C.3)."""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Literal

TriggerPosition = Literal["prefix", "suffix", "natural_integration"]
TrainingType = Literal["character", "concept", "style", "general"]

KREA2_GLOSSARY = (
    "Vocabulary reference (use the precise term only for what is actually visible; never invent gear):\n"
    "- Rope: 6mm unbleached Japanese hemp rope, raw jute cordage, cotton rope; wraps, cinches, frictions, hitches, knots, stem lines, rope ends.\n"
    "- Ties: takate-kote (box tie), hishime / diamond chest harness, kikko (tortoise shell) harness, hip harness, futomomo (thigh tie), frog tie, ebi (shrimp) tie, hogtie, suspension uplines.\n"
    "- Hardware: welded steel chains, chrome handcuffs, stainless steel shackles, spreader bar, steel O-rings and D-rings, locking carabiners, suspension ring, bamboo pole, steel frame, cage.\n"
    "- Leather and gear: posture collar, wrist and ankle cuffs, harness straps, buckles, ball gag, blindfold.\n"
    "- Body interaction: rope indentation and compression lines on the skin, limb angles, joint positions, weight bearing, balance, muscle tension.\n"
    "- Setting and optics: floor and wall materials, props, light direction and quality (hard side light, soft window light), shadow fall-off, depth of field, camera angle and framing."
)


@dataclass(frozen=True, slots=True)
class SkillDefinition:
    """Versioned Master Skill template for VLM dataset captioning."""

    id: str
    slug: str
    name: str
    version: int
    training_type: TrainingType
    system_prompt_template: str
    user_prompt_template: str
    glossary_template: str = KREA2_GLOSSARY
    min_words: int = 80
    max_words: int = 150
    hard_min_words: int = 35
    hard_max_words: int = 260
    discouraged_regexes: tuple[str, ...] = field(default_factory=tuple)
    require_class_noun: bool = False
    is_builtin: bool = True


SKILL_KREA2_CHARACTER_V1 = SkillDefinition(
    id="krea2_character_v1",
    slug="krea2_character",
    name="Krea 2 Character Training",
    version=1,
    training_type="character",
    system_prompt_template=(
        "You are an expert visual director annotating a character training dataset for the Krea 2 foundation model.\n"
        "Describe this image adhering to these strict rules:\n"
        "1. Trigger Binding ({trigger_instruction}): The trigger '{trigger}' represents the character's permanent identity. Write it exactly once and never use it for any other person in the image.\n"
        "2. Adults Only: The subject and everyone else shown are consenting adults (21+). Never use minor-coded words such as girl, teen, or schoolgirl.\n"
        "3. IDENTITY DECOUPLING (CRITICAL): DO NOT describe permanent, immutable facial geometry or physiognomic traits (never mention eye shape, eye color, facial bone structure, nose shape, cheekbones, jawline, or ethnicity). Those permanent features must bind exclusively to '{trigger}'.\n"
        "4. PRIMARY FOCUS (Mutable Variable Elements): Exhaustively describe all variable elements that change between images: clothing, fabrics, ties, restraints, equipment, hardware, hairstyle, pose, body stance, facial expression, camera angle, framing, lighting direction, and background setting.\n"
        "5. SECONDARY: If other people are visible, describe them briefly as other adults without the trigger.\n"
        "6. Formatting: DO NOT include quality buzzwords ('masterpiece', '8k', 'photorealistic'). Output only the caption: no preamble ('Here is...'), no commentary about the image or the task ('This image shows...'), no lists, markdown, or quotes. Output a single cohesive paragraph between {lo} and {hi} words and end with a complete sentence.\n\n"
        "{glossary}"
    ),
    user_prompt_template=(
        "Caption this image for a Krea 2 character LoRA dataset following the system instructions. "
        "Describe what changes between images (clothing, pose, camera angle, lighting, background, facial expression, hairstyle, restraints/props) "
        "and nothing about fixed facial geometry. {trigger_user_hint} "
        "Write one cohesive paragraph of {lo} to {hi} words."
    ),
    glossary_template=KREA2_GLOSSARY,
    min_words=80,
    max_words=140,
    hard_min_words=35,
    hard_max_words=220,
    discouraged_regexes=(
        r"\beye shape\b",
        r"\b(almond|hooded|monolid|deep-set)[- ]eyes?\b",
        r"\bnose shape\b",
        r"\b(button|aquiline|roman) nose\b",
        r"\bcheekbones?\b",
        r"\bjaw ?line\b",
        r"\bbone structure\b",
        r"\bethnicity\b",
        r"\b(asian|caucasian|latina|latino|african|european|hispanic)\b",
    ),
    require_class_noun=True,
    is_builtin=True,
)


SKILL_KREA2_STYLE_V1 = SkillDefinition(
    id="krea2_style_v1",
    slug="krea2_style",
    name="Krea 2 Style Training (Content Inversion)",
    version=1,
    training_type="style",
    system_prompt_template=(
        "You are an objective visual annotator creating dataset descriptions for a Krea 2 style / aesthetic LoRA model.\n"
        "Describe this image following the Content Inversion rule:\n"
        "1. Trigger Binding ({trigger_instruction}): The trigger '{trigger}' stands for the overall visual style and aesthetic; write it exactly once.\n"
        "2. Adults Only: All people shown are consenting adults (21+). Refer to them as adults; never use minor-coded words such as girl, teen, or schoolgirl.\n"
        "3. Neutral Content Description: Describe all subjects, objects, people, clothing, pose, actions, and spatial scene composition in plain, neutral, objective detail.\n"
        "4. STYLE DECOUPLING (CRITICAL): Strictly separate content from style. DO NOT describe the artistic style, color grading, color palette cast, film grain, vintage look, lens flare aesthetics, brushstrokes, or artistic rendering technique so that the visual aesthetic binds entirely to '{trigger}'.\n"
        "5. Formatting: Avoid quality praise ('masterpiece', '8k', 'photorealistic'). Output only the caption: no preamble ('Here is...'), no commentary about the image or the task ('This image shows...'), no lists, markdown, or quotes. Output a single cohesive paragraph between {lo} and {hi} words and end with a complete sentence.\n\n"
        "{glossary}"
    ),
    user_prompt_template=(
        "Caption this image for a Krea 2 style LoRA dataset following the system instructions. "
        "Describe the subject and scene neutrally and objectively (people, clothing, pose, objects, setting) while leaving the visual style unnamed so it binds to '{trigger}'. "
        "{trigger_user_hint} Write one cohesive paragraph of {lo} to {hi} words."
    ),
    glossary_template=KREA2_GLOSSARY,
    min_words=75,
    max_words=130,
    hard_min_words=35,
    hard_max_words=210,
    discouraged_regexes=(
        r"\bfilm grain\b",
        r"\bcolou?r grading\b",
        r"\bvintage\b",
        r"\bcinematic\b",
        r"\baesthetic\b",
        r"\bartistic\b",
        r"\bmoody\b",
        r"\bretro\b",
    ),
    require_class_noun=False,
    is_builtin=True,
)


SKILL_KREA2_CONCEPT_V1 = SkillDefinition(
    id="krea2_concept_v1",
    slug="krea2_concept",
    name="Krea 2 Concept Training",
    version=1,
    training_type="concept",
    system_prompt_template=(
        "You are an expert visual director and restraint taxonomist annotating a concept dataset for the Krea 2 foundation model (MMDiT architecture with Qwen3-VL text encoder).\n"
        "Describe this scene in a single cohesive, photographic paragraph adhering to these Krea 2 training principles:\n"
        "1. Trigger Binding ({trigger_instruction}): The trigger '{trigger}' names the physical/visual concept, not a specific person; write it exactly once.\n"
        "2. Adults Only: All people shown are consenting adults (21+). Refer to them as adults (for example 'an adult woman'); never use minor-coded words such as girl, teen, or schoolgirl.\n"
        "3. Subject Decoupling: State the adult subject's apparent age range, build, hair, and skin tone explicitly so that personal identity is not baked into the concept trigger.\n"
        "4. Posture & Anatomical Tension: Describe body stance, limb angles, joint positions, and muscle tension as they interact with the scene.\n"
        "5. Materials, Hardware & Interaction Focus: Thoroughly describe any visible knots, rigging patterns, cordage, chains, cuffs, collars, fabrics, and visible skin compression or surface interaction.\n"
        "6. Spatial Setting & Optics: Ground the scene with the immediate environment, floor reflections, directional lighting, camera angle, and depth of field so that the model learns cohesive 3D geometry and realistic surface textures.\n"
        "7. Formatting: Avoid Danbooru tags and bracket weights. DO NOT include quality buzzwords ('masterpiece', '8k', 'photorealistic'). Output only the caption: no preamble ('Here is...'), no commentary about the image or the task ('This image shows...'), no headings, lists, markdown, or quotes. Write flowing natural prose between {lo} and {hi} words and end with a complete sentence.\n\n"
        "{glossary}"
    ),
    user_prompt_template=(
        "Caption this image for a Krea 2 concept LoRA dataset following the system instructions. "
        "Explicitly describe the adult subject's physical build and hair so identity is decoupled from '{trigger}', and focus on posture, materials, hardware, spatial setting, and lighting. "
        "{trigger_user_hint} Write one flowing paragraph of {lo} to {hi} words."
    ),
    glossary_template=KREA2_GLOSSARY,
    min_words=120,
    max_words=180,
    hard_min_words=45,
    hard_max_words=270,
    discouraged_regexes=(),
    require_class_noun=False,
    is_builtin=True,
)


BUILTIN_SKILLS: tuple[SkillDefinition, ...] = (
    SKILL_KREA2_CHARACTER_V1,
    SKILL_KREA2_STYLE_V1,
    SKILL_KREA2_CONCEPT_V1,
)

BUILTIN_SKILLS_BY_ID: dict[str, SkillDefinition] = {s.id: s for s in BUILTIN_SKILLS}
BUILTIN_SKILLS_BY_TYPE: dict[str, SkillDefinition] = {
    "character": SKILL_KREA2_CHARACTER_V1,
    "style": SKILL_KREA2_STYLE_V1,
    "concept": SKILL_KREA2_CONCEPT_V1,
}


def primary_class_noun(class_noun: str | None) -> str:
    """Extract the primary subject class noun ('woman, ropes' -> 'woman')."""
    first = re.split(r"[,;/]", str(class_noun or ""))[0].strip()
    return first or "woman"


def _trigger_instructions(
    trigger: str,
    noun: str,
    training_type: str,
    position: TriggerPosition,
) -> tuple[str, str]:
    """Return (system_trigger_instruction, user_trigger_hint) for the chosen trigger_position."""
    if position == "suffix":
        sys_inst = f"Place the trigger word '{trigger}' at the very end of the caption as a closing tag before the final period"
        usr_hint = f"End the paragraph with ', {trigger}.'"
        return sys_inst, usr_hint
    if position == "natural_integration":
        sys_inst = f"Weave the trigger word '{trigger}' naturally into the opening sentence of the description"
        usr_hint = f"Integrate '{trigger}' naturally once within the first sentence."
        return sys_inst, usr_hint
    # Default: prefix (Token 0)
    if training_type == "character":
        sys_inst = f"Start the caption at Token 0 with '{trigger} Photograph of a {noun}...'"
        usr_hint = f"Begin at Token 0 with '{trigger} Photograph of a {noun}'."
    else:
        sys_inst = f"Start the caption at Token 0 with '{trigger} Photograph of...'"
        usr_hint = f"Begin at Token 0 with '{trigger} Photograph of'."
    return sys_inst, usr_hint


def render_skill_prompts(
    skill: SkillDefinition | dict[str, Any],
    *,
    trigger_word: str = "ohwx_subject",
    trigger_position: TriggerPosition = "prefix",
    class_noun: str = "woman",
    tag_hints: list[str] | None = None,
) -> tuple[str, str]:
    """Render `(system_prompt, user_prompt)` for a SkillDefinition or DB skill row."""
    if isinstance(skill, SkillDefinition):
        sys_tmpl = skill.system_prompt_template
        usr_tmpl = skill.user_prompt_template
        glossary = skill.glossary_template
        lo = skill.min_words
        hi = skill.max_words
        t_type = skill.training_type
    else:
        sys_tmpl = str(skill.get("system_prompt_template") or SKILL_KREA2_CONCEPT_V1.system_prompt_template)
        usr_tmpl = str(skill.get("user_prompt_template") or SKILL_KREA2_CONCEPT_V1.user_prompt_template)
        glossary = str(skill.get("glossary_template") or KREA2_GLOSSARY)
        lo = int(skill.get("min_words") or 80)
        hi = int(skill.get("max_words") or 160)
        t_type = str(skill.get("training_type") or "concept")

    trig = (trigger_word or "").strip() or "ohwx_subject"
    noun = primary_class_noun(class_noun)
    pos: TriggerPosition = (
        trigger_position
        if trigger_position in ("prefix", "suffix", "natural_integration")
        else "prefix"
    )
    sys_inst, usr_hint = _trigger_instructions(trig, noun, t_type, pos)

    values = {
        "trigger": trig,
        "noun": noun,
        "lo": str(lo),
        "hi": str(hi),
        "glossary": glossary.strip(),
        "trigger_instruction": sys_inst,
        "trigger_user_hint": usr_hint,
    }

    # Safe format substitution supporting custom user templates that may omit some keys
    class _SafeDict(dict[str, str]):
        def __missing__(self, key: str) -> str:
            return "{" + key + "}"

    safe_vals = _SafeDict(values)
    system_prompt = sys_tmpl.format_map(safe_vals).strip()
    user_prompt = usr_tmpl.format_map(safe_vals).strip()

    clean_hints = [t.strip() for t in (tag_hints or []) if t and t.strip()]
    if clean_hints:
        unique_hints = ", ".join(dict.fromkeys(clean_hints))
        user_prompt += (
            f"\n\nVerified visible elements (confirmed by a human; describe them naturally where they appear, "
            f"do not list them): {unique_hints}."
        )

    return system_prompt, user_prompt


def apply_trigger_word(
    caption: str,
    trigger_word: str | None,
    position: TriggerPosition = "prefix",
    video_hash: str | None = None,
) -> tuple[str, list[str]]:
    """Enforce and verify trigger_word placement according to `position` ('prefix', 'suffix', 'natural_integration').

    Returns `(updated_caption, warnings)`.
    """
    cleaned = re.sub(r"\s+", " ", (caption or "").strip())
    warnings: list[str] = []
    trig = (trigger_word or "").strip()
    if not trig or trig == "--no-trigger":
        return cleaned, warnings

    if not cleaned:
        return f"{trig} Photograph of a scene.", ["Empty caption replaced with trigger stub"]

    trig_pattern = re.compile(r"(?<![\w-])" + re.escape(trig) + r"(?![\w-])", re.IGNORECASE)
    matches = list(trig_pattern.finditer(cleaned))

    if position == "prefix":
        words = cleaned.split()
        first_token = words[0].strip(",.:;\"'") if words else ""
        if first_token.lower() != trig.lower():
            # Remove stray occurrences of trigger_word elsewhere so it appears once at Token 0
            if matches:
                cleaned = trig_pattern.sub("", cleaned)
                cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,;:-")
            prefix_parts = [trig]
            if video_hash and video_hash.strip():
                prefix_parts.append(video_hash.strip())
            cleaned = f"{' '.join(prefix_parts)} {cleaned}".strip()
            warnings.append(f"Prepended trigger '{trig}' to prefix (Token 0)")
        elif video_hash and video_hash.strip():
            vh = video_hash.strip()
            second_token = words[1].strip(",.:;\"'") if len(words) > 1 else ""
            if second_token.lower() != vh.lower():
                rest = " ".join(words[1:])
                cleaned = f"{words[0]} {vh} {rest}".strip()
                warnings.append(f"Inserted video hash '{vh}' at Token 1")

    elif position == "suffix":
        # Ensure trigger is at the end before terminal punctuation
        stripped_end = cleaned.rstrip(".!?\"' ")
        if not stripped_end.lower().endswith(trig.lower()):
            if matches:
                cleaned_no_trig = trig_pattern.sub("", cleaned)
                stripped_end = re.sub(r"\s+", " ", cleaned_no_trig).strip(" .,;:-")
            cleaned = f"{stripped_end}, {trig}."
            warnings.append(f"Appended trigger '{trig}' as suffix")
        elif not cleaned.endswith((".", "!", "?")):
            cleaned = f"{cleaned}."

    elif position == "natural_integration":
        if not matches:
            # Weave into opening clause
            if cleaned.lower().startswith("photograph of "):
                cleaned = f"Photograph of {trig}, {cleaned[14:].lstrip()}"
            elif cleaned.lower().startswith("a photograph of "):
                cleaned = f"A photograph of {trig}, {cleaned[16:].lstrip()}"
            else:
                cleaned = f"{trig}, {cleaned}"
            warnings.append(f"Integrated missing trigger '{trig}' into opening clause")
        elif len(matches) > 1:
            warnings.append(f"Trigger '{trig}' appears {len(matches)} times (expected 1)")

    # Clean up any double spaces or double commas introduced during normalization
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r",\s*,+", ",", cleaned)
    return cleaned, warnings


def verify_trigger_placement(
    caption: str,
    trigger_word: str | None,
    position: TriggerPosition = "prefix",
) -> tuple[bool, str | None]:
    """Verify whether `trigger_word` is present in `caption` at the configured `position`."""
    trig = (trigger_word or "").strip()
    if not trig or trig == "--no-trigger":
        return True, None
    text = (caption or "").strip()
    if not text:
        return False, "Caption is empty"

    trig_re = re.compile(r"(?<![\w-])" + re.escape(trig) + r"(?![\w-])", re.IGNORECASE)
    if not trig_re.search(text):
        return False, f"Trigger '{trig}' is missing from caption"

    if position == "prefix":
        first_word = text.split()[0].strip(",.:;\"'")
        if first_word.lower() != trig.lower():
            return False, f"Trigger '{trig}' is not at prefix (Token 0); found '{first_word}'"
    elif position == "suffix":
        body = text.rstrip(".!?\"' ")
        if not body.lower().endswith(trig.lower()):
            return False, f"Trigger '{trig}' is not at suffix position"

    return True, None
