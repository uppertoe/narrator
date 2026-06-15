"""Extractor mapping + offline fallback (no network)."""
from app.extract import ExtractedEvent, _to_candidate, extract_candidates
from app.models import EventKind
from app.state import CaseState


def test_non_medication_maps_to_none():
    assert _to_candidate(ExtractedEvent(event_type="non_medication"), "small talk") is None


def test_drug_synonym_canonicalised():
    c = _to_candidate(ExtractedEvent(
        event_type="bolus", drug="norad", dose=10, dose_unit="microgram"), "norad 10")
    assert c is not None
    assert c.drug == "noradrenaline"   # resolved from synonym
    assert c.kind == EventKind.bolus


def test_phase_event_mapping():
    c = _to_candidate(ExtractedEvent(
        event_type="phase", phase_label="Bypass on"), "bypass on")
    assert c.kind == EventKind.phase
    assert c.phase_label == "Bypass on"
    assert c.drug is None


def test_offline_fallback_uses_naive_parser():
    # With no ANTHROPIC_API_KEY in the test env, extract_candidates falls back
    # to the naive parser and still yields a usable candidate.
    cands = extract_candidates("10 microg adrenaline now", CaseState(), 18.4)
    assert cands
    assert cands[0].kind == EventKind.bolus
    assert cands[0].drug == "adrenaline"
