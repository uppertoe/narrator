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


# Order matters: most specific unit phrases first. Unit bases below also accept
# the spoken shorthand clinicians actually dictate — "mics"/"mic" for micrograms,
# "kig" for kilo — which is what the ASR transcribes and what kept "per kilo" from
# being recognised. `_NL` (no preceding letter) blocks mid-word matches like the
# "mics" inside "dynamics" while still allowing digit-glued forms ("20mics").
_NL = r"(?<![a-z])"
_KG = r"(?:kg|kig|kilo(?:gram)?s?)"
_UG = r"(?:micro\s*g(?:ram)?s?|mcg|mics?|µg)"   # incl. "mics"/"mic"
_MG = r"m(?:illi)?g(?:ram)?s?"
_ML = r"m(?:illi)?l(?:itre|iter)?s?"
_PER = r"(?:/|per)"

_RATE_UNIT_PATTERNS = [
    (rf"{_NL}{_UG}\s*{_PER}\s*{_KG}\s*{_PER}\s*min", "microgram/kg/min"),
    (rf"{_NL}{_UG}\s*{_PER}\s*{_KG}\s*{_PER}\s*h(?:ou)?r", "microgram/kg/hr"),
    (rf"{_NL}{_MG}\s*{_PER}\s*{_KG}\s*{_PER}\s*h(?:ou)?r", "mg/kg/hr"),
    (rf"{_NL}{_ML}\s*{_PER}\s*h(?:ou)?r", "mL/hr"),
    (rf"\bunits?\s*{_PER}\s*{_KG}\s*{_PER}\s*h(?:ou)?r", "unit/kg/hr"),
    (rf"\bunits?\s*{_PER}\s*h(?:ou)?r", "unit/hr"),
    (rf"{_NL}{_MG}\s*{_PER}\s*h(?:ou)?r", "mg/hr"),
    # Absolute (non-per-kg) forms — listed AFTER the /kg variants so those win.
    (rf"{_NL}{_UG}\s*{_PER}\s*min", "microgram/min"),
    (rf"{_NL}{_UG}\s*{_PER}\s*h(?:ou)?r", "microgram/hr"),
]

_DOSE_UNIT_PATTERNS = [
    # Per-kg dose forms first so "mics per kilo" → microgram/kg (not microgram).
    (rf"{_NL}{_UG}\s*{_PER}\s*{_KG}", "microgram/kg"),
    (rf"{_NL}{_MG}\s*{_PER}\s*{_KG}", "milligram/kg"),
    (rf"\bunits?\s*{_PER}\s*{_KG}", "unit/kg"),
    (rf"\bmmol\s*{_PER}\s*{_KG}", "mmol/kg"),
    (rf"{_NL}{_UG}\b", "microgram"),
    (rf"{_NL}{_MG}\b", "milligram"),
    (r"\bgrams?\b", "gram"),
    (r"\bunits?\b", "unit"),
    (r"\bmmol\b", "mmol"),
    (rf"{_NL}{_ML}\b", "mL"),
]


def _find_unit(text: str, patterns) -> str | None:
    for pat, unit in patterns:
        if re.search(pat, text):
            return unit
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


def _find_all_drugs(low: str) -> list[tuple[int, int, str]]:
    """Every drug mention as (start, end, canonical), de-duplicated with longest
    match winning on overlap — so one utterance can carry several commands."""
    matches: list[tuple[int, int, str]] = []
    for syn in SYNONYMS:
        for m in re.finditer(rf"\b{re.escape(syn)}\b", low):
            matches.append((m.start(), m.end(), resolve_drug(syn)))
    matches.sort(key=lambda x: (x[0], -(x[1] - x[0])))   # earliest, then longest
    picked: list[tuple[int, int, str]] = []
    last_end = -1
    for start, end, canon in matches:
        if start >= last_end:        # drop overlaps (e.g. "calcium" in "calcium chloride")
            picked.append((start, end, canon))
            last_end = end
    return picked


def _dose_before_drug(low: str, drugs: list[tuple[int, int, str]]) -> bool:
    """Does this utterance dictate the dose BEFORE the drug ("20 mics propofol")
    rather than after ("propofol 20")? Decided by where the first number falls
    relative to the first drug. The convention is consistent within an utterance,
    so this orients the whole split."""
    m = re.search(r"\d", low)
    return bool(m and m.start() < drugs[0][0])


def _split_by_drugs(raw: str, drugs: list[tuple[int, int, str]],
                    dose_before: bool) -> list[tuple[str, str]]:
    """One segment per drug, keeping each dose with its own drug. For dose-after,
    a segment runs from each drug to the next ("propofol 20 | rocuronium 50"); for
    dose-before, it runs from the previous drug to this one ("20 mics propofol |
    30 mics adrenaline"). The first/last segment absorbs any leading/trailing text."""
    n = len(drugs)
    out: list[tuple[str, str]] = []
    for i, (start, end, canon) in enumerate(drugs):
        if dose_before:
            seg_start = 0 if i == 0 else drugs[i - 1][1]      # previous drug's end
            seg_end = len(raw) if i == n - 1 else end          # this drug's end
        else:
            seg_start = 0 if i == 0 else start                 # this drug's start
            seg_end = len(raw) if i == n - 1 else drugs[i + 1][0]
        out.append((canon, raw[seg_start:seg_end].strip()))
    return out


def _parse_command(seg: str, drug: str, state: CaseState) -> Candidate:
    """Parse a single drug-command segment (drug already identified)."""
    low = seg.lower()
    number = _extract_number(low)
    rate_unit = _find_unit(low, _RATE_UNIT_PATTERNS)
    dose_unit = _find_unit(low, _DOSE_UNIT_PATTERNS)

    if re.search(r"\b(stop|off|cease|ceased|discontinue)\b", low):
        return Candidate(kind=EventKind.infusion_stop, drug=drug,
                         source_text=seg, confidence=0.8)

    running = state.running_infusion(drug)
    has_rate_kw = bool(re.search(
        r"\b(up|down|to|rate|running|infusion|start|started|wean|titrat\w*|increase|reduce)\b",
        low))
    has_bolus_kw = bool(re.search(
        r"\b(bolus|give|gave|given|push|shot|slug|stat)\b", low))
    # The kind is "locked" when an explicit unit or a bolus/rate word fixed it;
    # otherwise it's an inference and we'll offer a one-tap ↔ flip.
    kind_locked = bool(rate_unit or dose_unit or has_rate_kw or has_bolus_kw)

    # Resolution matrix (see README). Bolus vs. rate-change:
    #   explicit rate unit → rate; explicit dose unit, no rate language → bolus;
    #   rate language → rate; bare number → rate/bolus by magnitude.
    rate_intent = bool(rate_unit) or has_rate_kw
    if dose_unit and not rate_unit and not has_rate_kw:
        rate_intent = False
    if not rate_intent and not dose_unit and looks_like_rate(drug, number):
        rate_intent = True
    if has_bolus_kw and not has_rate_kw and not rate_unit:
        rate_intent = False   # an explicit bolus word beats the magnitude guess

    if rate_intent:
        kind = EventKind.infusion_rate_change if running else EventKind.infusion_start
        return Candidate(kind=kind, drug=drug, rate_value=number, rate_unit=rate_unit,
                         source_text=seg, confidence=0.7, kind_locked=kind_locked)
    return Candidate(kind=EventKind.bolus, drug=drug, dose_value=number,
                     dose_unit=dose_unit, source_text=seg, confidence=0.6,
                     kind_locked=kind_locked)


def parse_utterance(text: str, state: CaseState) -> list[Candidate]:
    """Parse one utterance into zero or more candidate events.

    Handles multiple commands in a single utterance (run-on, e.g.
    "propofol 20 rocuronium 50") by splitting on drug boundaries — one event per
    drug. A drugless utterance is a phase milestone or an unidentified row."""
    raw = text.strip()
    low = raw.lower()
    if not low:
        return []

    drugs = _find_all_drugs(low)
    if not drugs:
        phase = _find_phase(low)
        if phase:
            return [Candidate(kind=EventKind.phase, phase_label=phase,
                              source_text=raw, confidence=0.7)]
        c = Candidate(kind=EventKind.bolus, source_text=raw, confidence=0.2)
        c.requires_confirmation = True
        c.ambiguity_reason = "Could not identify a drug"
        return [c]

    dose_before = _dose_before_drug(low, drugs)
    return [_parse_command(seg, drug, state)
            for drug, seg in _split_by_drugs(raw, drugs, dose_before)]
