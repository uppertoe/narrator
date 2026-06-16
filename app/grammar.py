"""Build a GBNF grammar from the formulary, for grammar-constrained ASR.

This is the engine-independent half of the planned hard-constraint upgrade: it
emits a GBNF grammar (the dialect whisper.cpp / llama.cpp accept) whose drug slot
is locked to the formulary, so the recogniser cannot invent a drug name —
"ripe fall" can't be produced, only "propofol".

Two important design points discovered while planning this:

* whisper.cpp matches the grammar against the *raw* decoded text, which is
  case-sensitive. We therefore emit each drug with an optional capitalised first
  letter, and keep everything else lower-case (whisper-cli is run with settings
  that avoid mid-sentence capitalisation).
* A purely closed grammar + always-on VAD would COERCE non-command chatter into a
  fake drug event. So ``allow_freeform`` adds an escape branch: speech that isn't
  a command can be transcribed freely (and then falls through to the
  "unrecognised" row) instead of being forced into the formulary.

The grammar is intentionally a starting point to be tuned empirically against the
binary (scripts/grammar_bench.py); it is not wired into the live resolver yet.
"""
from __future__ import annotations

from app.drugs import DRUGS

# Unit spellings whisper is likely to emit (the parser normalises afterwards).
_UNITS = [
    "micrograms", "microgram", "mcg", "milligrams", "milligram", "mg",
    "units", "unit", "millimoles", "mmol", "mls", "ml", "mil",
]
_RATE_KW = ["up to", "down to", "to", "at", "running at", "rate of", "rate"]
_BOLUS_KW = ["give", "giving", "gave", "bolus", "push", "stat"]
_NUM_WORDS = [
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "fifteen", "twenty", "thirty", "forty",
    "fifty", "sixty", "seventy", "eighty", "ninety", "hundred", "point", "and",
    "nought", "oh", "half",
]


def _lit(s: str) -> str:
    """A GBNF string literal that also accepts a capitalised first letter."""
    s = s.strip()
    if s and s[0].isalpha():
        return f'("{s[0].upper()}" | "{s[0]}") "{s[1:]}"'
    return f'"{s}"'


def _alt(options: list[str]) -> str:
    # longest first so multi-word forms win the match
    uniq = sorted(set(o.lower() for o in options), key=len, reverse=True)
    return " | ".join(_lit(o) for o in uniq)


def drug_terms() -> list[str]:
    """All spoken drug forms (canonical + synonyms) for the grammar's drug slot."""
    terms: list[str] = []
    for d in DRUGS:
        terms.append(d.canonical)
        terms.extend(d.synonyms)
    return terms


def build_gbnf(*, allow_freeform: bool = True) -> str:
    drug_rule = _alt(drug_terms())
    unit_rule = _alt(_UNITS)
    rate_kw = _alt(_RATE_KW)
    bolus_kw = _alt(_BOLUS_KW)
    numword = _alt(_NUM_WORDS)

    clause_alts = ["bolus", "rate", "stop", "phase"]
    if allow_freeform:
        clause_alts.append("freeform")

    lines = [
        f'root    ::= ws clause ws "."?',
        f'clause  ::= {" | ".join(clause_alts)}',
        f'ws      ::= " "*',
        f'drug    ::= {drug_rule}',
        f'unit    ::= {unit_rule}',
        f'digits  ::= [0-9]+ ("." [0-9]+)?',
        f'numword ::= {numword} (ws {numword})*',
        f'num     ::= digits | numword',
        f'ratekw  ::= {rate_kw}',
        f'boluskw ::= {bolus_kw}',
        # "give 20 of propofol", "propofol 20 mg", "20 of propofol"
        f'bolus   ::= (boluskw ws)? (num ws (unit ws)? ("of" ws)?)? drug (ws num)? (ws unit)?',
        # "noradrenaline up to 0.1", "adrenaline to 0.2 mcg/kg/min"
        f'rate    ::= drug ws ratekw ws num (ws unit)?',
        f'stop    ::= ("stop" | "cease" | "off") (ws "the")? ws drug | drug ws "off"',
        f'phase   ::= "bypass" ws ("on" | "off") | "cross" "-"? "clamp" (ws ("on" | "off" | "release"))?',
    ]
    if allow_freeform:
        # escape hatch: non-command speech is transcribed freely (won't be coerced
        # into a fake drug event). Kept deliberately low-priority via grammar order.
        lines.append(r'freeform ::= [a-zA-Z0-9 ,.\x27-]+')
    return "\n".join(lines) + "\n"
