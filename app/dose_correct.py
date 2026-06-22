"""Correct ASR mishears of the spoken DOSE vocabulary (units / per-kg / per-time).

Built from scripts/gen_dose_aliases.py — a TTS->ASR round-trip through the
production model (base.en + hotwords). It showed base.en reliably mishears the
unit shorthand, most notably:
  * "mics"/"mic"  -> "marks"/"mark" (very consistent), also "mugs", "mite", "mike"
  * "per kilo"    -> "for kilo", "pakilo"/"pikilo"
  * "per hour"    -> nonsense tokens ("thorella", "threla", "porella", "choralea")
  * "per kilo"    -> "peculiar" (seen in real case-1 dictation)

We map those back to canonical unit phrases so the parser can read them. Ambiguous
*real* words ("marks", "mite", "peculiar") are corrected ONLY in dose context —
adjacent to a number or a per-phrase, or right after a unit token — so ordinary
speech ("the patient looks peculiar") is untouched. Nonsense tokens that never
occur in normal speech are mapped directly.

Runs after number normalisation, so the numbers it anchors on are digits.
"""
from __future__ import annotations

import re

# microgram shorthand that the model mishears (see generator output).
_UG_WORDS = r"(?:marks?|mugs?|mites?|mikes?|mud)"

_RULES: list[tuple[re.Pattern[str], str]] = [
    # <number> <ug-mishear>            -> <number> mics   ("20 marks" / "20-mite")
    (re.compile(rf"\b(\d[\d.]*)\s+{_UG_WORDS}\b", re.I), r"\1 mics"),
    # <ug-mishear> per ...             -> mics per ...     ("marks per kilo")
    (re.compile(rf"\b{_UG_WORDS}\b(?=\s+per\b)", re.I), "mics"),
    (re.compile(r"\bmaggi\b", re.I), "mcg"),
    # per-kilo
    (re.compile(r"\bfor\s+kilo(grams?)?\b", re.I), r"per kilo\1"),
    (re.compile(r"\bfor\s+kig\b", re.I), "per kig"),
    (re.compile(r"\bp[ai]kilo\b", re.I), "per kilo"),
    # "peculiar" only right after a microgram/milligram token (real case-1 mishear)
    (re.compile(r"\b(mics?|mcg|mg)\s+peculiar\b", re.I), r"\1 per kilo"),
    # per-hour nonsense tokens (do not occur in normal speech)
    (re.compile(r"\b(?:thorella|thorela|tharala|tharela|threla|porella|choralea)\b", re.I), "per hour"),
    (re.compile(r"\banella\b", re.I), "an hour"),
]


def correct_dose_phrases(text: str) -> str:
    """Map dose-vocabulary mishears back to canonical unit phrases."""
    for pat, repl in _RULES:
        text = pat.sub(repl, text)
    return text
