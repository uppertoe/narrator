"""Weight-sanity unit resolver."""
from app.resolve import resolve_unit


def test_explicit_unit_wins():
    r = resolve_unit("adrenaline", "infusion", 0.1, 18.4,
                     explicit_unit="microgram/min")
    assert r.unit == "microgram/min"
    assert r.source == "explicit"
    assert r.inferred is False


def test_convention_beats_weight():
    r = resolve_unit("adrenaline", "infusion", 0.5, 18.4,
                     convention="microgram/kg/min")
    assert r.unit == "microgram/kg/min"
    assert r.source == "convention"
    assert r.inferred is False


def test_running_infusion_unit_inherited():
    r = resolve_unit("adrenaline", "infusion", 0.5, 18.4,
                     running_unit="microgram/kg/min")
    assert r.unit == "microgram/kg/min"
    assert r.source == "state"
    assert r.inferred is True


def test_weight_picks_per_kg_when_absolute_implausible():
    # 0.1 mcg/kg/min plausible; 0.1 mcg/min = 0.005 mcg/kg/min below band.
    r = resolve_unit("adrenaline", "infusion", 0.1, 18.4)
    assert r.unit == "microgram/kg/min"
    assert r.source == "weight"


def test_weight_picks_absolute_for_large_bolus():
    # adrenaline 100: per-kg 100 implausible (band 1–10); absolute 100/18.4≈5.4 ok.
    r = resolve_unit("adrenaline", "bolus", 100, 18.4)
    assert r.unit == "microgram"
    assert r.source == "weight"


def test_genuine_ambiguity_asks():
    # 0.5 plausible as 0.5/kg/min AND as 0.5/min (0.027/kg/min) → ask.
    r = resolve_unit("adrenaline", "infusion", 0.5, 18.4)
    assert r.unit is None
    assert r.source == "ambiguous"
    assert r.candidates == ["microgram/kg/min", "microgram/min"]


def test_no_weight_defaults_to_per_kg():
    r = resolve_unit("adrenaline", "infusion", 0.5, None)
    assert r.unit == "microgram/kg/min"
    assert r.source == "default"
