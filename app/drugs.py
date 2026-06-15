"""Drug dictionary: synonyms, default units, and plausible per-kg bands.

PROTOTYPE — clinician-reviewed starting point, NOT a validated formulary.

These numbers are NOT safety limits (this is a record, not an order). They drive
exactly two things:
  1. the default *unit* assumed when none is spoken, and
  2. per-kg vs. absolute disambiguation using the patient's weight.

Each drug carries, per route, a native unit and a plausible *per-kg* band. The
two auto-candidates are always the per-kg form and the absolute form of that
unit (e.g. microgram/kg/min ↔ microgram/min; microgram ↔ microgram/kg). The
resolver (see resolve.py) tests the spoken value `V` (per-kg form) and `V / weight`
(absolute form) against the band; a unique hit wins, a tie/miss asks once.

Tuned for paediatric cardiac. Bands feed disambiguation only — precision isn't
critical; the per-kg/absolute boundary and the default unit are what matter.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DrugRule:
    canonical: str
    synonyms: tuple[str, ...] = ()
    bolus_unit: str | None = None          # native absolute bolus unit
    bolus_band: tuple[float, float] | None = None      # plausible per-kg dose band
    infusion_unit: str | None = None       # native per-kg infusion unit (e.g. microgram/kg/min)
    infusion_band: tuple[float, float] | None = None   # plausible per-kg rate band


# --- The starter formulary (reviewed; paediatric cardiac) ------------------
DRUGS: list[DrugRule] = [
    DrugRule("adrenaline", ("epinephrine", "adren", "adr"),
             bolus_unit="microgram", bolus_band=(1, 10),
             infusion_unit="microgram/kg/min", infusion_band=(0.01, 1.0)),
    DrugRule("noradrenaline", ("norad", "norepinephrine", "nor"),
             bolus_unit="microgram", bolus_band=(0.05, 1),
             infusion_unit="microgram/kg/min", infusion_band=(0.01, 1.0)),
    DrugRule("metaraminol", ("metaram",),
             bolus_unit="microgram", bolus_band=(1, 10),
             infusion_unit="microgram/kg/min", infusion_band=(0.1, 1.0)),
    DrugRule("phenylephrine", ("phenyl",),
             bolus_unit="microgram", bolus_band=(1, 10),
             infusion_unit="microgram/kg/min", infusion_band=(0.1, 0.5)),
    DrugRule("dopamine", ("dopa",),
             infusion_unit="microgram/kg/min", infusion_band=(2, 20)),
    DrugRule("dobutamine", ("dobut",),
             infusion_unit="microgram/kg/min", infusion_band=(2, 20)),
    DrugRule("milrinone", ("milrin",),
             bolus_unit="microgram", bolus_band=(25, 75),   # loading dose
             infusion_unit="microgram/kg/min", infusion_band=(0.25, 0.75)),
    DrugRule("propofol", ("prop", "propafol"),
             bolus_unit="milligram", bolus_band=(1, 4),
             infusion_unit="mg/kg/hr", infusion_band=(1.5, 18)),
    DrugRule("ketamine", ("ket",),
             bolus_unit="milligram", bolus_band=(1, 2),
             infusion_unit="mg/kg/hr", infusion_band=(0.1, 3)),
    DrugRule("fentanyl", ("fent",),
             bolus_unit="microgram", bolus_band=(1, 20),
             infusion_unit="microgram/kg/hr", infusion_band=(1, 5)),
    DrugRule("alfentanil", ("alf",),
             bolus_unit="microgram", bolus_band=(10, 50),
             infusion_unit="microgram/kg/min", infusion_band=(0.5, 5)),
    DrugRule("remifentanil", ("remi", "remifent"),
             bolus_unit="microgram", bolus_band=(0.5, 2),
             infusion_unit="microgram/kg/min", infusion_band=(0.05, 1.0)),
    DrugRule("morphine", (),
             bolus_unit="milligram", bolus_band=(0.05, 0.2)),
    DrugRule("rocuronium", ("roc",),
             bolus_unit="milligram", bolus_band=(0.6, 1.2)),
    DrugRule("suxamethonium", ("sux", "succinylcholine", "scoline"),
             bolus_unit="milligram", bolus_band=(1, 2)),
    DrugRule("sugammadex", ("sugamm",),
             bolus_unit="milligram", bolus_band=(2, 16)),
    DrugRule("atropine", (),
             bolus_unit="microgram", bolus_band=(10, 20)),
    DrugRule("glycopyrrolate", ("glyco", "glycopyrronium"),
             bolus_unit="microgram", bolus_band=(4, 10)),
    DrugRule("heparin", (),
             bolus_unit="unit", bolus_band=(100, 400)),
    DrugRule("protamine", (),
             bolus_unit="milligram", bolus_band=(1, 5)),
    DrugRule("calcium chloride", ("calcium", "cacl", "calcium chl"),
             bolus_unit="mmol", bolus_band=(0.1, 0.2)),
    DrugRule("midazolam", ("midaz", "versed"),
             bolus_unit="milligram", bolus_band=(0.05, 0.1)),
    DrugRule("insulin", (),
             infusion_unit="unit/kg/hr", infusion_band=(0.02, 0.1)),
    DrugRule("potassium", ("kcl", "potassium chloride"),
             bolus_unit="mmol", bolus_band=(0.1, 0.5)),
]


# --- Lookups (built once) --------------------------------------------------
BY_CANONICAL: dict[str, DrugRule] = {d.canonical: d for d in DRUGS}

SYNONYMS: dict[str, str] = {}
for _d in DRUGS:
    SYNONYMS[_d.canonical.lower()] = _d.canonical
    for _s in _d.synonyms:
        SYNONYMS[_s.lower()] = _d.canonical

APPROVED_DRUGS: set[str] = set(BY_CANONICAL)


def resolve_drug(name: str | None) -> str | None:
    """Map a spoken/typed name or synonym to a canonical drug name."""
    if not name:
        return None
    return SYNONYMS.get(name.strip().lower())


def rule_for(drug: str | None) -> DrugRule | None:
    if not drug:
        return None
    return BY_CANONICAL.get(drug)


# --- Unit forms & bands ----------------------------------------------------
def _abs_infusion(perkg_unit: str) -> str:
    """microgram/kg/min → microgram/min; mg/kg/hr → mg/hr; unit/kg/hr → unit/hr."""
    return perkg_unit.replace("/kg", "", 1)


def forms_and_band(drug: str | None, scope: str):
    """Return (per_kg_form, absolute_form, (low, high)) for a drug+scope, or None.

    scope is "bolus" or "infusion".
    """
    r = rule_for(drug)
    if r is None:
        return None
    if scope == "bolus":
        if not r.bolus_unit:
            return None
        return (f"{r.bolus_unit}/kg", r.bolus_unit, r.bolus_band)
    if not r.infusion_unit:
        return None
    return (r.infusion_unit, _abs_infusion(r.infusion_unit), r.infusion_band)


def candidate_units(drug: str | None, scope: str) -> list[str]:
    """The per-kg and absolute unit options to offer for disambiguation."""
    fb = forms_and_band(drug, scope)
    if fb is None:
        return []
    perkg, absf, _ = fb
    return [perkg, absf]


def looks_like_rate(drug: str | None, value: float | None) -> bool:
    """True if a bare number reads as an infusion rate rather than a bolus dose.

    Uses the per-kg bands as a magnitude heuristic on the raw number (good for
    the common per-kg-dosed small patient; keyword/context override the rest).
    """
    r = rule_for(drug)
    if not r or value is None or r.infusion_unit is None:
        return False
    lo, hi = r.infusion_band
    if not (lo <= value <= hi):
        return False
    if not r.bolus_unit:
        return True
    blo, bhi = r.bolus_band
    return value < blo or not (blo <= value <= bhi)
