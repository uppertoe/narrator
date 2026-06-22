"""Dose-vocabulary mishear correction (from the TTS->ASR generator findings)."""
from app.dose_correct import correct_dose_phrases


def test_marks_to_mics_number_anchored():
    assert correct_dose_phrases("20 marks per kilo") == "20 mics per kilo"
    assert correct_dose_phrases("30 marks adrenaline") == "30 mics adrenaline"
    assert correct_dose_phrases("1 mite per kilo") == "1 mics per kilo"


def test_marks_before_per_phrase():
    assert correct_dose_phrases("marks per kilo per minute") == "mics per kilo per minute"


def test_per_kilo_mishears():
    assert correct_dose_phrases("10 mics for kilo") == "10 mics per kilo"
    assert correct_dose_phrases("10 mics pakilo") == "10 mics per kilo"


def test_peculiar_only_after_unit_token():
    assert correct_dose_phrases("20 mics peculiar") == "20 mics per kilo"
    # real word out of context is left alone
    assert correct_dose_phrases("the patient looks peculiar") == "the patient looks peculiar"


def test_per_hour_nonsense_tokens():
    assert correct_dose_phrases("10 mics per kilo thorella") == "10 mics per kilo per hour"


def test_ambiguous_words_not_touched_out_of_context():
    # "marks" not next to a number or a per-phrase stays as-is
    assert correct_dose_phrases("he marks the chart") == "he marks the chart"
    assert correct_dose_phrases("the marks were high") == "the marks were high"
