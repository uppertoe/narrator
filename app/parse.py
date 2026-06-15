"""Naive utterance parser — PHASE 1 PLACEHOLDER.

This is a deterministic keyword/regex parser used only so the safety core
(validator + state machine) can be exercised by typing utterances into a text
box, with no ASR or LLM in the loop. It is intentionally dumb. Phase 2 replaces
`parse_utterance` with a Claude structured-extraction call returning the same
`Candidate` shape; nothing downstream needs to change.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.drugs import SYNONYMS, looks_like_rate, resolve_drug
from app.models import EventKind
from app.state import CaseState


@dataclass
class Candidate:
    """A parsed candidate event, before deterministic validation."""
    kind: EventKind
    drug: str | None = None
    dose_value: float | None = None
    dose_unit: str | None = None
    rate_value: float | None = None
    rate_unit: str | None = None
    route: str | None = "IV"
    phase_label: str | None = None
    source_text: str = ""
    confidence: float = 0.5
    inferred_unit: bool = False
    requires_confirmation: bool = False
    ambiguity_reason: str | None = None
    unit_source: str = ""          # set by validate: explicit|convention|state|weight|...
    kind_locked: bool = False      # kind fixed by an explicit unit or keyword
    kind_guessed: bool = False     # set by validate when kind was an inference (offer ↔ flip)
    flags: list[str] = field(default_factory=list)


_NUM_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "fifty": 50,
    "hundred": 100, "thousand": 1000,
}


def _extract_number(text: str) -> float | None:
    """Find the first numeric value, as digits or simple words incl. 'point'."""
    m = re.search(r"\d+\.\d+|\.\d+|\d+", text)
    digit = float(m.group()) if m else None

    # word form: "point two", "one point five", "ten"
    toks = re.findall(r"[a-z]+", text.lower())
    word_val: float | None = None
    i = 0
    while i < len(toks):
        t = toks[i]
        if t == "point" and i + 1 < len(toks) and toks[i + 1] in _NUM_WORDS:
            word_val = float(f"0.{_NUM_WORDS[toks[i + 1]]}")
            if i > 0 and toks[i - 1] in _NUM_WORDS:
                word_val = _NUM_WORDS[toks[i - 1]] + word_val / (
                    10 ** len(str(_NUM_WORDS[toks[i + 1]]))
                )
            break
        if t in _NUM_WORDS and (i + 1 >= len(toks) or toks[i + 1] != "point"):
            word_val = float(_NUM_WORDS[t])
        i += 1

    # Prefer an explicit digit unless a word-decimal was spoken.
    if word_val is not None and (digit is None or "point" in text.lower()):
        return word_val
    return digit


# Order matters: most specific unit phrases first. "_KG" accepts kg/kilo/kilogram.
_KG = r"(?:kg|kilo(?:gram)?s?)"
_RATE_UNIT_PATTERNS = [
    (rf"micro\s*g(?:ram)?s?\s*(?:/|per)\s*{_KG}\s*(?:/|per)\s*min", "microgram/kg/min"),
    (rf"mcg\s*(?:/|per)\s*{_KG}\s*(?:/|per)\s*min", "microgram/kg/min"),
    (rf"micro\s*g(?:ram)?s?\s*(?:/|per)\s*{_KG}\s*(?:/|per)\s*h(?:ou)?r", "microgram/kg/hr"),
    (rf"m(?:illi)?g(?:ram)?s?\s*(?:/|per)\s*{_KG}\s*(?:/|per)\s*h(?:ou)?r", "mg/kg/hr"),
    (r"m(?:illi)?l(?:itre|iter)?s?\s*(?:/|per)\s*h(?:ou)?r", "mL/hr"),
    (rf"units?\s*(?:/|per)\s*{_KG}\s*(?:/|per)\s*h(?:ou)?r", "unit/kg/hr"),
    (r"units?\s*(?:/|per)\s*h(?:ou)?r", "unit/hr"),
    (r"m(?:illi)?g(?:ram)?s?\s*(?:/|per)\s*h(?:ou)?r", "mg/hr"),
    # Absolute (non-per-kg) forms — listed AFTER the /kg variants so those win.
    (r"micro\s*g(?:ram)?s?\s*(?:/|per)\s*min", "microgram/min"),
    (r"mcg\s*(?:/|per)\s*min", "microgram/min"),
    (r"micro\s*g(?:ram)?s?\s*(?:/|per)\s*h(?:ou)?r", "microgram/hr"),
    (r"mcg\s*(?:/|per)\s*h(?:ou)?r", "microgram/hr"),
]

_DOSE_UNIT_PATTERNS = [
    # Per-kg dose forms first so "milligrams per kilo" → milligram/kg (not milligram).
    (rf"micro\s*g(?:ram)?s?\s*(?:/|per)\s*{_KG}", "microgram/kg"),
    (rf"mcg\s*(?:/|per)\s*{_KG}", "microgram/kg"),
    (rf"m(?:illi)?g(?:ram)?s?\s*(?:/|per)\s*{_KG}", "milligram/kg"),
    (rf"units?\s*(?:/|per)\s*{_KG}", "unit/kg"),
    (rf"mmol\s*(?:/|per)\s*{_KG}", "mmol/kg"),
    (r"micro\s*g(?:ram)?s?\b", "microgram"),
    (r"mcg\b", "microgram"),
    (r"m(?:illi)?g(?:ram)?s?\b", "milligram"),
    (r"\bgrams?\b", "gram"),
    (r"\bunits?\b", "unit"),
    (r"\bmmol\b", "mmol"),
    (r"m(?:illi)?l(?:itre|iter)?s?\b", "mL"),
]


def _find_unit(text: str, patterns) -> str | None:
    for pat, unit in patterns:
        if re.search(pat, text):
            return unit
    return None


def _find_drug(text: str) -> str | None:
    low = text.lower()
    # Longest synonym first so multi-word names win.
    for syn in sorted(SYNONYMS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(syn)}\b", low):
            return resolve_drug(syn)
    return None


def _find_phase(text: str) -> str | None:
    low = text.lower()
    if "bypass" in low:
        return "Bypass off" if "off" in low else "Bypass on"
    if "cross clamp" in low or "cross-clamp" in low or "x clamp" in low or "xclamp" in low:
        return "Cross-clamp off" if ("off" in low or "release" in low) else "Cross-clamp on"
    if "clamp" in low:
        return "Cross-clamp off" if ("off" in low or "release" in low) else "Cross-clamp on"
    return None


def parse_utterance(text: str, state: CaseState) -> list[Candidate]:
    """Best-effort parse of a single utterance into zero or more candidates."""
    raw = text.strip()
    low = raw.lower()
    if not low:
        return []

    # 1) Phase / procedural milestone
    phase = _find_phase(low)
    if phase and _find_drug(low) is None:
        return [Candidate(kind=EventKind.phase, phase_label=phase,
                          source_text=raw, confidence=0.7)]

    drug = _find_drug(low)
    number = _extract_number(low)
    rate_unit = _find_unit(low, _RATE_UNIT_PATTERNS)
    dose_unit = _find_unit(low, _DOSE_UNIT_PATTERNS)

    # 2) Stop / off
    if re.search(r"\b(stop|off|cease|ceased|discontinue)\b", low) and drug:
        return [Candidate(kind=EventKind.infusion_stop, drug=drug,
                          source_text=raw, confidence=0.8)]

    # If no drug at all, we can't make a medication event.
    if drug is None:
        c = Candidate(kind=EventKind.bolus, source_text=raw, confidence=0.2)
        c.requires_confirmation = True
        c.ambiguity_reason = "Could not identify a drug"
        return [c]

    running = state.running_infusion(drug)
    has_rate_kw = bool(re.search(
        r"\b(up|down|to|rate|running|infusion|start|started|wean|titrat\w*|increase|reduce)\b",
        low))
    has_bolus_kw = bool(re.search(
        r"\b(bolus|give|gave|given|push|shot|slug|stat)\b", low))
    # The kind is "locked" when an explicit unit or a bolus/rate word fixed it;
    # otherwise it's an inference and we'll offer a one-tap ↔ flip.
    kind_locked = bool(rate_unit or dose_unit or has_rate_kw or has_bolus_kw)

    # See the resolution matrix in the README. Bolus vs. rate-change:
    #   - explicit rate unit ("0.2 mcg/kg/min")          → rate
    #   - explicit dose unit, no rate language ("10 mg")  → bolus
    #   - rate language ("to", "up/down", "wean", …)      → rate
    #   - bare number that reads as a rate (0.2)          → rate  (magnitude)
    #   - bare number that reads as a bolus (10)          → bolus (magnitude)
    # Missing units are filled with sensible defaults later (validate._fill_defaults).
    rate_intent = bool(rate_unit) or has_rate_kw
    if dose_unit and not rate_unit and not has_rate_kw:
        rate_intent = False
    if not rate_intent and not dose_unit and looks_like_rate(drug, number):
        rate_intent = True
    if has_bolus_kw and not has_rate_kw and not rate_unit:
        rate_intent = False   # an explicit bolus word beats the magnitude guess

    # 3) Infusion rate change / start (unit, if unspoken, is resolved in validate)
    if rate_intent:
        kind = EventKind.infusion_rate_change if running else EventKind.infusion_start
        return [Candidate(
            kind=kind, drug=drug, rate_value=number, rate_unit=rate_unit,
            source_text=raw, confidence=0.7, kind_locked=kind_locked,
        )]

    # 4) Default: bolus
    return [Candidate(
        kind=EventKind.bolus, drug=drug, dose_value=number, dose_unit=dose_unit,
        source_text=raw, confidence=0.6, kind_locked=kind_locked,
    )]
