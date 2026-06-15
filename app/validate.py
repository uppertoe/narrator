"""Resolution + light validation.

A clinical **record**, not an order: nothing is withheld for safety or
plausibility. We resolve the unit (using weight + learned case conventions),
then only hold an event for review when it's genuinely ambiguous — which drug,
or a missing essential number, or a per-kg/absolute unit we truly can't decide.
"""
from __future__ import annotations

from app.models import EventKind
from app.parse import Candidate
from app.resolve import resolve_unit
from app.state import CaseState

_INFUSION_KINDS = (EventKind.infusion_start, EventKind.infusion_rate_change)


def scope_of(kind: EventKind) -> str | None:
    if kind == EventKind.bolus:
        return "bolus"
    if kind in _INFUSION_KINDS:
        return "infusion"
    return None


def _flag(cand: Candidate, reason: str) -> None:
    cand.flags.append(reason)
    cand.requires_confirmation = True
    if not cand.ambiguity_reason:
        cand.ambiguity_reason = reason


def validate_candidate(cand: Candidate, state: CaseState,
                       weight_kg: float | None,
                       conventions: dict[tuple[str, str], str] | None = None) -> Candidate:
    conventions = conventions or {}

    # Phase / procedural events: just need a label.
    if cand.kind == EventKind.phase:
        if not cand.phase_label:
            _flag(cand, "Procedural event with no label")
        return cand

    from app.drugs import resolve_drug, rule_for  # local import avoids a cycle
    canonical = resolve_drug(cand.drug)
    if canonical is None:
        _flag(cand, f"Unknown or unrecognised drug: {cand.drug!r}")
        return cand
    cand.drug = canonical

    scope = scope_of(cand.kind)
    if scope is not None:
        is_bolus = scope == "bolus"
        value = cand.dose_value if is_bolus else cand.rate_value
        spoken = cand.dose_unit if is_bolus else cand.rate_unit
        # Treat a unit as "explicit" only if it was actually spoken (not a guess).
        explicit = spoken if (spoken and not cand.inferred_unit) else None
        running = state.running_infusion(canonical) if not is_bolus else None
        res = resolve_unit(
            canonical, scope, value, weight_kg,
            explicit_unit=explicit,
            convention=conventions.get((canonical, scope)),
            running_unit=running.rate_unit if running else None,
        )
        cand.unit_source = res.source
        cand.inferred_unit = res.inferred
        if res.unit:
            if is_bolus:
                cand.dose_unit = res.unit
            else:
                cand.rate_unit = res.unit
        elif res.source == "ambiguous":
            _flag(cand, "Unit unclear — per-kg or absolute?")

        # Disambiguation: an essential number we couldn't infer.
        if is_bolus and cand.dose_value is None:
            _flag(cand, "Dose not specified")
        if not is_bolus and cand.rate_value is None:
            _flag(cand, "Rate not specified")

        # If bolus-vs-rate wasn't pinned by a unit/keyword and the drug has both
        # routes, the kind is a guess → offer a one-tap ↔ flip on the row.
        rule = rule_for(canonical)
        cand.kind_guessed = (not cand.kind_locked
                             and bool(rule and rule.bolus_unit and rule.infusion_unit))

    return cand
