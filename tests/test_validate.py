"""Parser + validator behaviour on the spec's worked examples."""
from app.parse import parse_utterance
from app.state import CaseState, InfusionState
from app.models import EventKind
from app.validate import validate_candidate


def parse_one(text, state=None, weight=18.4, conventions=None):
    state = state or CaseState()
    cands = parse_utterance(text, state)
    assert cands, f"no candidate parsed from {text!r}"
    c = cands[0]
    validate_candidate(c, state, weight, conventions or {})
    return c


def norad_running():
    s = CaseState()
    s.active_infusions["noradrenaline"] = InfusionState(
        "noradrenaline", 0.1, "microgram/kg/min", "running")
    s.note_drug("noradrenaline")
    return s


def adrenaline_running():
    s = CaseState()
    s.active_infusions["adrenaline"] = InfusionState(
        "adrenaline", 0.05, "microgram/kg/min", "running")
    s.note_drug("adrenaline")
    return s


def test_clean_bolus_is_accepted():
    c = parse_one("10 microg adrenaline now")
    assert c.kind == EventKind.bolus
    assert c.drug == "adrenaline"
    assert c.dose_value == 10
    assert c.dose_unit == "microgram"
    assert c.requires_confirmation is False


def test_bare_dose_assumes_default_unit():
    # "adrenaline 100" — no unit, no infusion. Bolus-magnitude → bolus, and the
    # unit is auto-assumed (adrenaline's default bolus unit = microgram).
    c = parse_one("adrenaline 100")
    assert c.kind == EventKind.bolus
    assert c.dose_value == 100
    assert c.dose_unit == "microgram"
    assert c.inferred_unit is True
    assert c.requires_confirmation is False


def test_bare_low_value_reads_as_rate():
    # "adrenaline 0.1" — rate-magnitude, no infusion; absolute interp (0.1/18.4 ≈
    # 0.005) falls below the band, so per-kg is the unique fit → no question.
    c = parse_one("adrenaline 0.1")
    assert c.kind == EventKind.infusion_start
    assert c.rate_value == 0.1
    assert c.rate_unit == "microgram/kg/min"
    assert c.inferred_unit is True
    assert c.requires_confirmation is False


def test_ambiguous_unit_prompts_for_choice():
    # At 18.4 kg, adrenaline 0.5 is plausible as 0.5 mcg/kg/min OR 0.5 mcg/min
    # (= 0.027 mcg/kg/min). Genuinely ambiguous → hold for a one-time unit choice.
    c = parse_one("adrenaline 0.5")
    assert c.kind == EventKind.infusion_start
    assert c.rate_unit is None
    assert c.requires_confirmation is True
    assert c.unit_source == "ambiguous"


def test_convention_overrides_weight():
    # Once the case has learned a unit, it wins over the weight heuristic.
    conv = {("adrenaline", "bolus"): "microgram"}
    c = parse_one("adrenaline 10", conventions=conv)
    assert c.kind == EventKind.bolus
    assert c.dose_value == 10
    assert c.dose_unit == "microgram"
    assert c.inferred_unit is False     # established convention, not a guess
    assert c.requires_confirmation is False


def test_infusion_rate_change_explicit_unit():
    c = parse_one(
        "noradrenaline up to 0.2 micrograms per kilo per minute", norad_running())
    assert c.kind == EventKind.infusion_rate_change
    assert c.drug == "noradrenaline"
    assert c.rate_value == 0.2
    assert c.rate_unit == "microgram/kg/min"
    assert c.inferred_unit is False


def test_infusion_unit_inferred_from_state():
    c = parse_one("norad down to 0.1", norad_running())
    assert c.kind == EventKind.infusion_rate_change
    assert c.rate_value == 0.1
    assert c.rate_unit == "microgram/kg/min"
    assert c.inferred_unit is True
    # inferred-from-running-infusion is high-confidence → accepted, not held
    assert c.requires_confirmation is False


def test_context_to_value_is_rate_change_when_infusion_running():
    c = parse_one("adrenaline to 0.2", adrenaline_running())
    assert c.kind == EventKind.infusion_rate_change
    assert c.drug == "adrenaline"
    assert c.rate_value == 0.2
    assert c.rate_unit == "microgram/kg/min"
    assert c.inferred_unit is True
    assert c.requires_confirmation is False


def test_context_bare_dose_is_bolus_when_infusion_running():
    c = parse_one("adrenaline 100 micrograms", adrenaline_running())
    assert c.kind == EventKind.bolus
    assert c.drug == "adrenaline"
    assert c.dose_value == 100
    assert c.dose_unit == "microgram"


def test_stop_infusion():
    c = parse_one("stop the norad", norad_running())
    assert c.kind == EventKind.infusion_stop
    assert c.drug == "noradrenaline"


def test_unknown_drug_flagged():
    c = parse_one("give 10 of frobinol")
    assert c.requires_confirmation is True
    assert c.ambiguity_reason is not None


def test_out_of_range_dose_is_recorded_not_gated():
    # It's a record, not an order: an unusual dose is recorded exactly as said,
    # never withheld for plausibility.
    c = parse_one("adrenaline 10 mg now")
    assert c.kind == EventKind.bolus
    assert c.dose_value == 10
    assert c.dose_unit == "milligram"
    assert c.requires_confirmation is False


def test_missing_amount_is_disambiguation():
    # "turn the norad down" — a rate change with no amount we can infer.
    c = parse_one("turn the norad down", norad_running())
    assert c.kind in (EventKind.infusion_rate_change, EventKind.infusion_start)
    assert c.requires_confirmation is True
    assert c.ambiguity_reason is not None


def propofol_running():
    s = CaseState()
    s.active_infusions["propofol"] = InfusionState(
        "propofol", 15, "mg/kg/hr", "running")
    s.note_drug("propofol")
    return s


def test_kind_guessed_for_bare_number_while_infusion_running():
    # "propofol 10" with a propofol infusion running: bolus-vs-rate is a guess
    # (defaults to rate by magnitude) → flaggable for a one-tap flip.
    c = parse_one("propofol 10", propofol_running())
    assert c.kind == EventKind.infusion_rate_change
    assert c.kind_guessed is True


def test_bolus_keyword_beats_magnitude_and_locks_kind():
    c = parse_one("give 10 of propofol", propofol_running())
    assert c.kind == EventKind.bolus
    assert c.kind_guessed is False     # an explicit bolus word pins the kind


def test_explicit_rate_unit_locks_kind():
    c = parse_one("propofol 12 mg per kilo per hour", propofol_running())
    assert c.kind == EventKind.infusion_rate_change
    assert c.rate_unit == "mg/kg/hr"
    assert c.kind_guessed is False


def test_phase_event():
    c = parse_one("bypass on")
    assert c.kind == EventKind.phase
    assert c.phase_label == "Bypass on"
