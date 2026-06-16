"""Spoken-number → digit normalisation for ASR transcripts.

Whisper usually emits digits, but a grammar-constrained engine (the planned
upgrade) emits number *words* ("propofol twenty", "nought point oh five"). This
turns spoken numbers into digit tokens so the existing parser — which prefers
digits — works for either engine. Conservative: only contiguous runs of genuine
number words are touched; everything else (drug names, units, "to"/"point" used
as connectors) is left alone.
"""
from __future__ import annotations

import re

_ONES = {
    "zero": 0, "oh": 0, "o": 0, "nought": 0, "naught": 0,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}
_SCALES = {"hundred": 100, "thousand": 1000}
_NUM_WORDS = set(_ONES) | set(_TENS) | set(_SCALES)


def _int_from_words(words: list[str]) -> int | None:
    """Parse a run of integer words ('one hundred and twenty' → 120)."""
    total = current = 0
    seen = False
    for w in words:
        if w == "and":
            continue
        if w in _TENS:
            current += _TENS[w]; seen = True
        elif w in _ONES:
            current += _ONES[w]; seen = True
        elif w == "hundred":
            current = (current or 1) * 100; seen = True
        elif w == "thousand":
            total += (current or 1) * 1000; current = 0; seen = True
        else:
            return None
    return (total + current) if seen else None


def _value_from_run(words: list[str]) -> str | None:
    """A number run, possibly with a decimal: returns a digit string or None.

    Decimal digits after 'point' are read individually ('point one five' → .15,
    'point oh five' → .05), matching how doses are dictated."""
    if "point" in words:
        p = words.index("point")
        int_words, frac_words = words[:p], words[p + 1:]
        int_part = _int_from_words(int_words) if int_words else 0
        if int_part is None:
            return None
        digits = ""
        for w in frac_words:
            if w in _ONES and _ONES[w] < 10:
                digits += str(_ONES[w])
            else:
                return None
        if not digits:
            return None
        return f"{int_part}.{digits}"
    val = _int_from_words(words)
    return None if val is None else str(val)


# A run = number words / point / connective 'and' (kept only between numbers).
_TOKEN = re.compile(r"[a-zA-Z]+|\S")


def normalize_numbers(text: str) -> str:
    """Replace spoken-number runs in ``text`` with digit strings."""
    tokens = text.split()
    out: list[str] = []
    i = 0
    while i < len(tokens):
        # how far does a number run extend from i?
        j = i
        while j < len(tokens):
            t = tokens[j].lower().strip(".,")
            is_num = t in _NUM_WORDS or t == "point"
            is_and = (t == "and" and j > i and j + 1 < len(tokens)
                      and tokens[j + 1].lower().strip(".,") in _NUM_WORDS)
            if is_num or is_and:
                j += 1
            else:
                break
        if j > i:
            run = [tokens[k].lower().strip(".,") for k in range(i, j)]
            # a lone "point"/"and" isn't a number
            if run not in (["point"], ["and"]):
                value = _value_from_run([w for w in run if w != "and"])
                if value is not None:
                    # preserve trailing punctuation of the last token
                    trailing = tokens[j - 1][len(tokens[j - 1].rstrip(".,")):]
                    out.append(value + trailing)
                    i = j
                    continue
        out.append(tokens[i])
        i += 1
    return " ".join(out)
