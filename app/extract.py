"""Claude structured-extraction parser — PHASE 2.

Replaces the naive `parse_utterance` with a Claude call that returns the SAME
`Candidate` shape, so the validator / state machine / UI are unchanged. The LLM
proposes candidates; `validate.py` remains the deterministic source of truth.

Model defaults to Haiku 4.5 (fast/cheap, fine for per-utterance parsing); override
with NARRATOR_MODEL. If no ANTHROPIC_API_KEY is set, falls back to the naive
parser so the app still runs offline.
"""
from __future__ import annotations

import json
import os

from pydantic import BaseModel

from app.drugs import APPROVED_DRUGS, resolve_drug
from app.models import EventKind
from app.parse import Candidate, parse_utterance
from app.state import CaseState

MODEL = os.environ.get("NARRATOR_MODEL", "claude-haiku-4-5")


def _use_llm() -> bool:
    """LLM extraction adds a per-utterance Claude round-trip (~1.5–3.5s). The
    deterministic parser now covers the common cases, so this is opt-out:
    NARRATOR_USE_LLM=0 disables it even when a key is present (the key can stay
    for other uses). Default: on when a key is set."""
    flag = os.environ.get("NARRATOR_USE_LLM")
    if flag is not None:
        return flag.strip().lower() in ("1", "true", "yes", "on")
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


USE_LLM = _use_llm()

_DOSE_UNITS = ["microgram", "microgram/kg", "milligram", "milligram/kg",
               "unit", "unit/kg", "mmol", "mmol/kg", "mL"]
_RATE_UNITS = ["microgram/kg/min", "microgram/min", "microgram/kg/hr",
               "microgram/hr", "mg/kg/hr", "mg/hr", "mL/hr",
               "unit/kg/hr", "unit/hr"]

_KIND_MAP = {
    "bolus": EventKind.bolus,
    "infusion_start": EventKind.infusion_start,
    "infusion_rate_change": EventKind.infusion_rate_change,
    "infusion_stop": EventKind.infusion_stop,
    "phase": EventKind.phase,
}


# --- Structured-output schema (enforced by the SDK) ------------------------
class ExtractedEvent(BaseModel):
    event_type: str          # bolus|infusion_start|infusion_rate_change|infusion_stop|phase|non_medication
    drug: str | None = None
    dose: float | None = None
    dose_unit: str | None = None
    rate: float | None = None
    rate_unit: str | None = None
    route: str | None = "IV"
    phase_label: str | None = None
    confidence: float = 0.5
    requires_confirmation: bool = False
    inferred_unit: bool = False
    ambiguity_reason: str | None = None


class Extraction(BaseModel):
    events: list[ExtractedEvent]


SYSTEM_PROMPT = f"""\
You extract anaesthetic medication events from a single spoken utterance to \
build a clinical RECORD (not an order). Record what was said — NEVER withhold or \
flag anything for safety, plausibility, or dose magnitude. The only reason to \
flag is genuine ambiguity about *what command* was meant.

Return JSON matching the schema. Rules:
- Use ONLY these canonical drug names: {sorted(APPROVED_DRUGS)}. Map shorthand \
(norad→noradrenaline, roc→rocuronium, etc.) to the canonical name. NEVER invent \
a drug that is not in the list — if unsure, set drug to null.
- dose_unit must be one of {_DOSE_UNITS}; rate_unit one of {_RATE_UNITS}.
- event_type: bolus, infusion_start, infusion_rate_change, infusion_stop, \
phase, or non_medication.
- Bolus vs rate change: rate language ("to/up/down/wean/titrate") or a \
rate-magnitude number is a rate change (start if no infusion is running); an \
explicit dose unit or a bolus-magnitude number is a bolus. Use the provided \
active_infusions / recent context to decide.
- "phase" is a procedural milestone (bypass on/off, cross-clamp); put it in \
phase_label, leave drug null.
- "non_medication" for chatter; prefer returning an empty events list.
- Convert spoken numbers to digits ("point two" → 0.2).
- Set inferred_unit=true WHENEVER the unit was not explicitly spoken (a \
downstream engine fills the sensible default from patient weight and the case's \
learned conventions, so a guessed unit must be marked inferred; only a unit the \
speaker actually said should have inferred_unit=false). Prefer per-kg forms \
(microgram/kg/min) for weight-based infusions.
- Set requires_confirmation=true + ambiguity_reason ONLY for true ambiguity: \
which drug, bolus-vs-rate you genuinely can't resolve, or a missing essential \
number you cannot infer ("turn it down" with no amount). Do NOT flag large or \
unusual doses — just record them.
- An utterance may contain zero, one, or several events."""


def _context_payload(text: str, state: CaseState, weight: float | None) -> str:
    return json.dumps({
        "utterance": text,
        "patient_weight_kg": weight,
        "active_infusions": {
            d: {"rate": inf.rate_value, "unit": inf.rate_unit, "status": inf.status}
            for d, inf in state.active_infusions.items()
        },
        "recent_boluses": [
            {"drug": b.drug, "dose": b.dose_value, "unit": b.dose_unit}
            for b in state.recent_boluses[:5]
        ],
        "recent_drugs": state.recent_drugs[:8],
        "last_drug": state.last_drug,
    })


def _to_candidate(e: ExtractedEvent, raw: str) -> Candidate | None:
    if e.event_type == "non_medication":
        return None
    kind = _KIND_MAP.get(e.event_type)
    if kind is None:
        return None
    return Candidate(
        kind=kind,
        drug=resolve_drug(e.drug) or e.drug,
        dose_value=e.dose,
        dose_unit=e.dose_unit,
        rate_value=e.rate,
        rate_unit=e.rate_unit,
        route=e.route or "IV",
        phase_label=e.phase_label,
        source_text=raw,
        confidence=e.confidence,
        inferred_unit=e.inferred_unit,
        requires_confirmation=e.requires_confirmation,
        ambiguity_reason=e.ambiguity_reason,
        # An explicitly-spoken unit pins the kind; otherwise it's an inference.
        kind_locked=bool((e.dose_unit or e.rate_unit) and not e.inferred_unit),
    )


def extract_candidates(text: str, state: CaseState,
                       weight: float | None) -> list[Candidate]:
    """Parse an utterance into candidates via Claude, or the naive fallback."""
    if not USE_LLM:
        return parse_utterance(text, state)
    try:
        import anthropic
        client = anthropic.Anthropic()
        response = client.messages.parse(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _context_payload(text, state, weight)}],
            output_format=Extraction,
        )
        if response.stop_reason == "refusal" or response.parsed_output is None:
            raise RuntimeError("extraction refused or empty")
        cands = [c for e in response.parsed_output.events
                 if (c := _to_candidate(e, text)) is not None]
        return cands
    except Exception as exc:  # noqa: BLE001 — never lose an utterance to an API error
        # Fall back to the naive parser; its output is still validated downstream.
        fallback = parse_utterance(text, state)
        for c in fallback:
            c.requires_confirmation = True
            c.ambiguity_reason = (c.ambiguity_reason
                                  or f"LLM extraction unavailable ({type(exc).__name__}); naive parse — review")
        return fallback
