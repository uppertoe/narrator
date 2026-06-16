"""Phonetic drug-name correction for ASR transcripts."""
from app.correct import correct_transcript


def test_known_aliases_mapped():
    assert correct_transcript("Ripe fall 20") == "propofol 20"
    assert correct_transcript("A trendal in three") == "adrenaline three"
    assert correct_transcript("give 20 of rock uranium") == "give 20 of rocuronium"


def test_phonetic_near_miss():
    # not in the alias map — matched by sound
    assert "metaraminol" in correct_transcript("metaramanol half a milligram")


def test_real_drug_names_preserved():
    assert correct_transcript("noradrenaline up to 0.2") == "noradrenaline up to 0.2"
    assert "adrenaline" in correct_transcript("10 micrograms of adrenaline now")


def test_synonyms_normalised():
    assert "noradrenaline" in correct_transcript("stop the norad")


def test_benign_words_not_drug_ified():
    out = correct_transcript("she is stable and the patient looks well now")
    drugs = {"propofol", "adrenaline", "noradrenaline", "rocuronium", "atropine",
             "fentanyl", "metaraminol", "midazolam", "heparin"}
    assert not (set(out.split()) & drugs), out


def test_generated_multiword_aliases():
    from app.correct import GENERATED_ALIASES
    # a 4-word mishear exercises multi-word window sizing (regression: window was
    # capped at 3 so longer aliases never matched)
    assert correct_transcript("nor a drain line up to 0.2") == "noradrenaline up to 0.2"
    assert correct_transcript("give 50 of sucks amethonium") == "give 50 of suxamethonium"
    # every generated alias must resolve to its canonical drug, around numbers
    for mishear, drug in GENERATED_ALIASES.items():
        assert drug in correct_transcript(f"{mishear} 10"), mishear


def test_numbers_preserved():
    assert "0.2" in correct_transcript("noradrenaline 0.2")
    assert correct_transcript("propofol 20").endswith("20")
