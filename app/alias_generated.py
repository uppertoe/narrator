"""Auto-generated alias map — DO NOT EDIT BY HAND.

Produced by scripts/gen_aliases.py: each canonical drug name is spoken by
Australian macOS voices and transcribed by the *actual* on-device model
(onnx-community/moonshine-tiny-ONNX). Mishears are recorded here so the
server can map them back to canonical drug names before parsing.

Voices: Karen. Re-generate with:

    uv run python scripts/gen_aliases.py

Hand-curated overrides live in app.correct.MANUAL_ALIASES and win on conflict.
"""
from __future__ import annotations

# normalised spoken mishear -> canonical drug  (count = times the model produced it)
GENERATED_ALIASES: dict[str, str] = {
    'happy nephrain': 'adrenaline',  # ×1
    'happy nephren': 'adrenaline',  # ×1
    'calcium chl': 'calcium chloride',  # ×4
    'yellow cmchl': 'calcium chloride',  # ×1
    'glock of pyronium': 'glycopyrrolate',  # ×2
    'glyco parrelate': 'glycopyrrolate',  # ×1
    'like a piranium': 'glycopyrrolate',  # ×1
    'like i parrelate': 'glycopyrrolate',  # ×1
    'like o par relates': 'glycopyrrolate',  # ×1
    'nor a drain line': 'noradrenaline',  # ×1
    'nor a penifrain': 'noradrenaline',  # ×1
    'nor a pin of rain': 'noradrenaline',  # ×1
    'fennel f r': 'phenylephrine',  # ×1
    'finna lefferin': 'phenylephrine',  # ×1
    'potassium chloride': 'potassium',  # ×4
    'remy fentanyl': 'remifentanil',  # ×1
    'rocky ronium': 'rocuronium',  # ×1
    'sucks amethonium': 'suxamethonium',  # ×2
    'sucks in neal ch': 'suxamethonium',  # ×1
    'sucks in neutral': 'suxamethonium',  # ×2
    'sucks in neutral line': 'suxamethonium',  # ×1
    'sucks in the chill': 'suxamethonium',  # ×1
}
