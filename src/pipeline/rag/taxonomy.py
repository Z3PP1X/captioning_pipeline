"""Domain taxonomy, vocabulary definitions, and tagging rules for Krea 2 RAG Prompt Tool."""
from __future__ import annotations

from typing import Any

# Canonical taxonomy structure with UI display metadata and matching keywords
TAXONOMY_CATEGORIES: dict[str, dict[str, Any]] = {
    "equipment": {
        "title": "Bondage Equipment & Ties",
        "icon": "🪢",
        "description": "Tactile cordage, metallic chains, hardware, harnesses, collars and gags",
        "groups": {
            "Rope & Kinbaku": [
                {
                    "id": "hemp_rope",
                    "label": "6mm Japanese Hemp Rope",
                    "keywords": ["hemp rope", "japanese hemp", "unbleached", "asanawa", "fiber rope", "cordage"],
                },
                {
                    "id": "jute_cordage",
                    "label": "Raw Jute Cordage",
                    "keywords": ["jute", "raw jute", "oiled jute", "jute cordage"],
                },
                {
                    "id": "takate_kote",
                    "label": "Takate-Kote (Box Tie)",
                    "keywords": ["takate-kote", "box tie", "gote", "arms bound behind", "elbows pulled", "lumbar spine"],
                },
                {
                    "id": "hishime_harness",
                    "label": "Hishime (Diamond Chest Harness)",
                    "keywords": ["hishime", "diamond harness", "chest harness", "diamond pattern", "diamond grid"],
                },
                {
                    "id": "kikko_harness",
                    "label": "Kikko (Tortoise Shell Harness)",
                    "keywords": ["kikko", "tortoise shell", "hexagonal pattern", "torso harness"],
                },
                {
                    "id": "frog_tie",
                    "label": "Frog Tie",
                    "keywords": ["frog tie", "knees bent outward", "ankles bound to thighs"],
                },
            ],
            "Metallic Hardware & Chains": [
                {
                    "id": "steel_chains",
                    "label": "Welded Steel Link Chains",
                    "keywords": ["chains", "steel chains", "welded steel", "chain links", "heavy-gauge"],
                },
                {
                    "id": "chrome_handcuffs",
                    "label": "Polished Chrome Handcuffs",
                    "keywords": ["handcuffs", "chrome handcuffs", "rigid handcuffs", "double-locking"],
                },
                {
                    "id": "shackles",
                    "label": "Stainless Steel Shackles",
                    "keywords": ["shackles", "steel shackles", "hinged cuffs", "wrist shackles", "ankle shackles"],
                },
                {
                    "id": "spreader_bar",
                    "label": "Spreader Bar",
                    "keywords": ["spreader bar", "rigid bar", "limbs spread wide", "steel bar"],
                },
                {
                    "id": "carabiners_rings",
                    "label": "Steel O-Rings & Carabiners",
                    "keywords": ["o-ring", "d-ring", "carabiner", "locking carabiner", "swivel hitch", "hardware"],
                },
            ],
            "Leather & Posture Gear": [
                {
                    "id": "posture_collar",
                    "label": "Leather Posture Collar",
                    "keywords": ["posture collar", "leather collar", "thick black leather", "wide collar", "collar"],
                },
                {
                    "id": "leather_cuffs",
                    "label": "Leather Wrist & Ankle Cuffs",
                    "keywords": ["leather cuffs", "leather straps", "buckle-locking", "wrist cuffs", "ankle cuffs"],
                },
                {
                    "id": "ball_gag",
                    "label": "Leather Strapped Ball Gag",
                    "keywords": ["ball gag", "gag", "posture gag", "leather straps forcing her jaw", "jaw open"],
                },
                {
                    "id": "leather_harness",
                    "label": "Heavy Leather Body Harness",
                    "keywords": ["leather harness", "leather straps", "bridle leather", "roller buckles"],
                },
            ],
            "Suspension & Predicament": [
                {
                    "id": "partial_suspension",
                    "label": "Partial Suspension (Tsuri)",
                    "keywords": ["partial suspension", "suspended aloft", "tiptoes brushing", "suspension line", "taut vertical rigging"],
                },
                {
                    "id": "tether_leash",
                    "label": "Ceiling Leash / Anchor Tether",
                    "keywords": ["tether", "leash", "ceiling anchor", "overhead hardpoint", "rigging line"],
                },
            ],
        },
    },
    "surroundings": {
        "title": "Surroundings & Architecture",
        "icon": "🏚️",
        "description": "Spatial staging, backgrounds, surfaces, and environmental physics",
        "groups": {
            "Environment & Setting": [
                {
                    "id": "industrial_basement",
                    "label": "Gritty Industrial Basement",
                    "keywords": ["industrial basement", "industrial", "basement", "warehouse", "gritty industrial"],
                },
                {
                    "id": "cellar_dungeon",
                    "label": "Stone Cellar / Dungeon",
                    "keywords": ["cellar", "dungeon", "stone wall", "vaulted", "crypt", "cold stone"],
                },
                {
                    "id": "brick_wall",
                    "label": "Textured Brick Wall",
                    "keywords": ["brick wall", "textured white brick", "exposed brick", "brick"],
                },
                {
                    "id": "weathered_wood",
                    "label": "Weathered Wooden Door / Beams",
                    "keywords": ["weathered wooden door", "wooden door", "wood beams", "rustic wood", "timber"],
                },
                {
                    "id": "concrete_floor",
                    "label": "Raw Concrete Floor",
                    "keywords": ["concrete floor", "raw concrete", "concrete background", "distressed concrete"],
                },
                {
                    "id": "studio_void",
                    "label": "Minimalist Studio Void",
                    "keywords": ["studio void", "pitch-black background", "black void", "dark studio", "minimalist background"],
                },
                {
                    "id": "reflective_floor",
                    "label": "High-Gloss Reflective Black Floor",
                    "keywords": ["reflective floor", "high-gloss black", "mirror reflections", "specular reflections on the floor", "glossy floor"],
                },
                {
                    "id": "rustic_loft",
                    "label": "Sunlit Rustic Loft",
                    "keywords": ["loft", "rustic loft", "bamboo", "hardwood floor", "apartment"],
                },
            ],
        },
    },
    "position": {
        "title": "Model Position & Posture",
        "icon": "🧘",
        "description": "Anatomical geometry, limb angles, muscle tension, and physical stance",
        "groups": {
            "Postures & Stances": [
                {
                    "id": "seiza",
                    "label": "Traditional Seiza (Kneeling)",
                    "keywords": ["seiza", "sitting back on heels", "kneeling in traditional seiza", "shins pressed flat"],
                },
                {
                    "id": "high_kneeling",
                    "label": "High Kneeling (Torso Vertical)",
                    "keywords": ["high kneeling", "kneeling upright", "vertical torso", "pelvis thrust forward"],
                },
                {
                    "id": "tabletop_all_fours",
                    "label": "Tabletop / All-Fours (Lordosis Arch)",
                    "keywords": ["tabletop", "all fours", "all-fours", "quadruped", "arched lumbar spine", "lordosis", "pelvis thrust upward"],
                },
                {
                    "id": "standing_upright",
                    "label": "Standing Upright / Leaning",
                    "keywords": ["standing upright", "stands upright", "standing in profile", "leaning against", "erect posture"],
                },
                {
                    "id": "standing_tiptoes",
                    "label": "Standing Balanced on Tiptoes",
                    "keywords": ["tiptoes", "balanced on tiptoes", "arched foot arches", "stretched upright"],
                },
                {
                    "id": "prone",
                    "label": "Prone (Lying Face-Down)",
                    "keywords": ["prone", "lying flat face-down", "chest touching floor", "face-down"],
                },
                {
                    "id": "supine",
                    "label": "Supine (Lying Face-Up)",
                    "keywords": ["supine", "lying on back", "face-up", "ribcage lifted"],
                },
                {
                    "id": "suspended_pose",
                    "label": "Suspended / Semi-Airborne",
                    "keywords": ["suspended", "aloft", "semi-airborne", "weight supported", "inverted suspension"],
                },
            ],
        },
    },
    "camera_lighting": {
        "title": "Camera & Lighting Optics",
        "icon": "💡",
        "description": "Photographic optics, directional lighting, chiaroscuro, and depth of field",
        "groups": {
            "Lighting Dynamics": [
                {
                    "id": "chiaroscuro",
                    "label": "High-Contrast Chiaroscuro",
                    "keywords": ["chiaroscuro", "high-contrast chiaroscuro", "deep shadows", "sculpted shadows", "dramatic lighting"],
                },
                {
                    "id": "directional_spotlight",
                    "label": "Directional Overhead Key Spotlight",
                    "keywords": ["directional key light", "key spotlight", "overhead spotlight", "sharp directional", "spotlight"],
                },
                {
                    "id": "rim_lighting",
                    "label": "Sharp Rim / Edge Lighting",
                    "keywords": ["rim lighting", "edge light", "silhouette rim", "backlit highlights"],
                },
                {
                    "id": "specular_highlights",
                    "label": "Specular Metallic Hardware Highlights",
                    "keywords": ["specular reflections", "metallic reflections", "subtle reflections on the metal", "matte texture", "gleam"],
                },
            ],
            "Optics & Lens": [
                {
                    "id": "lens_85mm",
                    "label": "85mm Prime Portrait Lens (f/2.8)",
                    "keywords": ["85mm", "85mm lens", "portrait lens", "85mm prime"],
                },
                {
                    "id": "lens_50mm",
                    "label": "50mm Prime Lens",
                    "keywords": ["50mm", "50mm lens", "50mm prime"],
                },
                {
                    "id": "shallow_dof",
                    "label": "Shallow Depth of Field (Sharp Hardware Focus)",
                    "keywords": ["shallow depth of field", "sharp focus", "blurred background", "bokeh", "focusing sharply"],
                },
            ],
        },
    },
}

# Curated Presets for one-click staging
CURATED_PRESETS: list[dict[str, Any]] = [
    {
        "id": "cellar_hemp_takate",
        "title": "Gritty Cellar Takate-Kote",
        "description": "Natural fiber rope box tie in an industrial basement with brick and chiaroscuro key light",
        "tags": {
            "equipment": ["hemp_rope", "takate_kote"],
            "surroundings": ["industrial_basement", "brick_wall"],
            "position": ["high_kneeling"],
            "camera_lighting": ["chiaroscuro", "directional_spotlight", "lens_85mm", "shallow_dof"],
        },
        "suggested_description": "Athletic adult woman with blonde hair bound in a tight box tie with visible rope indentation lines",
    },
    {
        "id": "void_chrome_chains",
        "title": "Studio Void Chrome Chains & Collar",
        "description": "High-fashion chrome handcuffs and leather posture collar on black mirror reflective floor",
        "tags": {
            "equipment": ["steel_chains", "chrome_handcuffs", "posture_collar", "carabiners_rings"],
            "surroundings": ["studio_void", "reflective_floor"],
            "position": ["tabletop_all_fours"],
            "camera_lighting": ["chiaroscuro", "specular_highlights", "lens_50mm", "shallow_dof"],
        },
        "suggested_description": "Slender adult woman in tabletop pose with arched spine, wrists chained behind back to leather posture collar",
    },
    {
        "id": "cellar_posture_gag",
        "title": "Basement Collar & Ball Gag Staging",
        "description": "Leather posture collar and strapped ball gag with heavy chiaroscuro and wooden door",
        "tags": {
            "equipment": ["posture_collar", "ball_gag", "leather_cuffs"],
            "surroundings": ["industrial_basement", "weathered_wood", "brick_wall"],
            "position": ["standing_upright"],
            "camera_lighting": ["chiaroscuro", "directional_spotlight", "lens_85mm", "shallow_dof"],
        },
        "suggested_description": "Fair-skinned adult woman standing upright restrained by a posture collar and strapped ball gag creating neck tension",
    },
    {
        "id": "hishime_partial_suspension",
        "title": "Hishime Partial Suspension",
        "description": "Intricate diamond chest harness on tiptoes with vertical overhead rigging line",
        "tags": {
            "equipment": ["hemp_rope", "hishime_harness", "partial_suspension", "carabiners_rings"],
            "surroundings": ["studio_void", "reflective_floor"],
            "position": ["standing_tiptoes"],
            "camera_lighting": ["chiaroscuro", "directional_spotlight", "lens_85mm", "shallow_dof"],
        },
        "suggested_description": "Curvy adult woman balanced on tiptoes bound in an unbleached hemp diamond hishime chest harness with taut vertical line",
    },
]

# Supported character identities and visual trait definitions
CHARACTERS: dict[str, dict[str, Any]] = {
    "sara": {
        "id": "sara",
        "name": "Sara",
        "age": 21,
        "avatar": "👩",
        "summary": "21yo woman, brown hair, dark brown eyes",
        "description": "sara, a 21 year old woman, with brown hair and dark brown eyes",
        "tag_tokens": ["sara", "21 year old woman", "brown hair", "dark brown eyes"],
    },
    "lene": {
        "id": "lene",
        "name": "Lene",
        "age": 21,
        "avatar": "👧",
        "summary": "21yo petite woman, dark brown hair, dark brown eyes",
        "description": "lene, a 21 year old petite woman, with dark brown hair and dark brown eyes",
        "tag_tokens": ["lene", "21 year old petite woman", "dark brown hair", "dark brown eyes"],
    },
}
