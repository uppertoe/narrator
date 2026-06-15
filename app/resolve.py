"""Unit resolution: explicit → convention → running infusion → weight-sanity.

Decides which unit to record when one wasn't unambiguously spoken, using the
patient's weight to choose between the per-kg and absolute forms. Returns a
`Resolution` whose `source` tells the caller what happened — in particular,
"explicit" means the unit was actually spoken (and should become the case
convention), while "ambiguous" means we should ask the user once.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.drugs import forms_and_band

# Sources that mean "we guessed" (eligible for later backfill) vs. established.
INFERRED_SOURCES = {"state", "weight", "default"}


@dataclass
class Resolution:
    unit: str | None
    source: str                       # explicit|convention|state|weight|default|ambiguous|none
    candidates: list[str] = field(default_factory=list)

    @property
    def inferred(self) -> bool:
        return self.source in INFERRED_SOURCES


def resolve_unit(drug: str | None, scope: str, value: float | None,
                 weight: float | None, *, explicit_unit: str | None = None,
                 convention: str | None = None,
                 running_unit: str | None = None) -> Resolution:
    if explicit_unit:
        return Resolution(explicit_unit, "explicit")
    if convention:
        return Resolution(convention, "convention")
    if running_unit:
        return Resolution(running_unit, "state")

    fb = forms_and_band(drug, scope)
    if fb is None:
        return Resolution(None, "none")
    perkg, absf, (lo, hi) = fb

    # No number or no weight: can't weight-test → assume the per-kg default.
    if value is None or not weight:
        return Resolution(perkg, "default", [perkg, absf])

    plausible: list[str] = []
    if lo <= value <= hi:
        plausible.append(perkg)
    if lo <= value / weight <= hi:
        plausible.append(absf)

    if len(plausible) == 1:
        return Resolution(plausible[0], "weight")
    # zero or both plausible → genuinely ambiguous: ask once.
    return Resolution(None, "ambiguous", [perkg, absf])
