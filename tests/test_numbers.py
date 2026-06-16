"""Spoken-number → digit normalisation."""
from app.numbers import normalize_numbers


def test_integers():
    assert normalize_numbers("propofol twenty") == "propofol 20"
    assert normalize_numbers("give fifty of rocuronium") == "give 50 of rocuronium"
    assert normalize_numbers("one hundred and twenty micrograms") == "120 micrograms"
    assert normalize_numbers("two hundred") == "200"


def test_decimals():
    assert normalize_numbers("adrenaline to point one") == "adrenaline to 0.1"
    assert normalize_numbers("metaraminol point five") == "metaraminol 0.5"
    assert normalize_numbers("noradrenaline nought point oh five") == "noradrenaline 0.05"
    assert normalize_numbers("up to point two") == "up to 0.2"


def test_leaves_non_numbers_alone():
    assert normalize_numbers("propofol and adrenaline") == "propofol and adrenaline"
    assert normalize_numbers("propofol 20") == "propofol 20"
    assert normalize_numbers("stop the noradrenaline") == "stop the noradrenaline"


def test_connectors_not_eaten():
    # "to"/"point" as connectors shouldn't be swallowed when not part of a number
    assert normalize_numbers("titrate to effect") == "titrate to effect"
