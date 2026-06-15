"""Per-case medication state machine.

The case state is *derived* by replaying accepted events in chronological
order. It is the deterministic context the parser and validator rely on to
interpret shorthand like "norad down to 0.1" or "stop the norad".
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.models import Event, EventKind, EventStatus


@dataclass
class InfusionState:
    drug: str
    rate_value: float | None
    rate_unit: str | None
    status: str  # "running" | "stopped"
    last_changed: object = None  # datetime


@dataclass
class BolusRecord:
    drug: str
    dose_value: float | None
    dose_unit: str | None
    timestamp: object = None


@dataclass
class CaseState:
    active_infusions: dict[str, InfusionState] = field(default_factory=dict)
    recent_boluses: list[BolusRecord] = field(default_factory=list)
    recent_drugs: list[str] = field(default_factory=list)
    last_drug: str | None = None

    def note_drug(self, drug: str | None) -> None:
        if not drug:
            return
        self.last_drug = drug
        if drug in self.recent_drugs:
            self.recent_drugs.remove(drug)
        self.recent_drugs.insert(0, drug)
        del self.recent_drugs[8:]

    def running_infusion(self, drug: str) -> InfusionState | None:
        inf = self.active_infusions.get(drug)
        if inf and inf.status == "running":
            return inf
        return None


def build_state(events: list[Event]) -> CaseState:
    """Replay accepted events (in timestamp order) into a CaseState."""
    state = CaseState()
    ordered = sorted(
        (e for e in events if e.status == EventStatus.accepted),
        key=lambda e: e.timestamp,
    )
    for e in ordered:
        state.note_drug(e.drug)
        if e.kind == EventKind.bolus and e.drug:
            state.recent_boluses.insert(
                0, BolusRecord(e.drug, e.dose_value, e.dose_unit, e.timestamp)
            )
            del state.recent_boluses[12:]
        elif e.kind in (EventKind.infusion_start, EventKind.infusion_rate_change) and e.drug:
            state.active_infusions[e.drug] = InfusionState(
                drug=e.drug,
                rate_value=e.rate_value,
                rate_unit=e.rate_unit,
                status="running",
                last_changed=e.timestamp,
            )
        elif e.kind == EventKind.infusion_stop and e.drug:
            inf = state.active_infusions.get(e.drug)
            if inf:
                inf.status = "stopped"
                inf.last_changed = e.timestamp
    return state
