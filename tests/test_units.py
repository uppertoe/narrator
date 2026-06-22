"""Spoken unit shorthand, esp. per-kilo ("mics per kilo" → microgram/kg)."""
from app.correct import correct_transcript
from app.numbers import normalize_numbers
from app.parse import parse_utterance
from app.state import build_state


def _run(raw):
    text = normalize_numbers(correct_transcript(raw))
    cs = parse_utterance(text, build_state([]))
    return [(c.drug, c.dose_value if c.dose_value is not None else c.rate_value,
             c.dose_unit or c.rate_unit) for c in cs]


def test_mics_recognised_as_micrograms():
    assert _run("adrenaline 100 mics") == [("adrenaline", 100, "microgram")]
    assert _run("noradrenaline 20 mics") == [("noradrenaline", 20, "microgram")]


def test_mics_per_kilo_is_per_kg():
    assert _run("propofol two mics per kilo") == [("propofol", 2, "microgram/kg")]


def test_run_on_per_kilo_keeps_units_per_drug():
    assert _run("propofol two mics per kilo, adrenaline ten mics per kilo") == [
        ("propofol", 2, "microgram/kg"), ("adrenaline", 10, "microgram/kg")]


def test_per_kig_slang_and_rate():
    assert _run("adrenaline 0.1 mics per kig per minute") == [
        ("adrenaline", 0.1, "microgram/kg/min")]


def test_existing_unit_forms_unchanged():
    assert _run("propofol 0.2 mcg/kg/min") == [("propofol", 0.2, "microgram/kg/min")]
    assert _run("give 10 mg of propofol") == [("propofol", 10, "milligram")]


def test_no_false_unit_match_midword():
    # "dynamics" must not yield a microgram unit
    assert _run("the cardiac dynamics are stable") == [(None, None, None)]
