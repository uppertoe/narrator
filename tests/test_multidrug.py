"""Multiple commands in one utterance (run-on) → one event per drug."""
from app.parse import parse_utterance
from app.state import build_state


def _parse(text):
    return parse_utterance(text, build_state([]))


def _pairs(cands):
    return [(c.drug, c.dose_value if c.dose_value is not None else c.rate_value)
            for c in cands]


def test_two_drugs_run_on():
    cs = _parse("propofol 20 rocuronium 50")
    assert _pairs(cs) == [("propofol", 20), ("rocuronium", 50)]


def test_three_drugs_run_on_keeps_order_and_pairing():
    cs = _parse("fentanyl 50 propofol 20 rocuronium 50")
    assert _pairs(cs) == [("fentanyl", 50), ("propofol", 20), ("rocuronium", 50)]


def test_single_command_unchanged():
    assert _pairs(_parse("propofol 20")) == [("propofol", 20)]
    assert len(_parse("noradrenaline up to 0.1")) == 1


def test_leading_text_stays_with_first_drug():
    # "give 20 of propofol" — number before the (only) drug
    assert _pairs(_parse("give 20 of propofol")) == [("propofol", 20)]


def test_mixed_stop_and_bolus_in_one_utterance():
    cs = _parse("stop the noradrenaline propofol 20")
    assert [c.drug for c in cs] == ["noradrenaline", "propofol"]
    assert cs[0].kind.value == "infusion_stop"
    assert cs[1].kind.value == "bolus" and cs[1].dose_value == 20


def test_drugless_utterance_not_split():
    assert len(_parse("the patient looks stable")) == 1   # single unidentified row
    assert _parse("bypass on")[0].kind.value == "phase"
