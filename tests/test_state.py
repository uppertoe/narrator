"""Case state machine: replaying events into derived infusion/bolus state."""
from datetime import datetime, timedelta, timezone

from app.models import Event, EventKind, EventStatus
from app.state import build_state

T0 = datetime(2026, 6, 15, 10, 0, tzinfo=timezone.utc)


def ev(minutes, **kw):
    kw.setdefault("status", EventStatus.accepted)
    return Event(case_id=1, timestamp=T0 + timedelta(minutes=minutes), **kw)


def test_infusion_lifecycle():
    events = [
        ev(0, kind=EventKind.infusion_start, drug="noradrenaline",
           rate_value=0.1, rate_unit="microgram/kg/min"),
        ev(1, kind=EventKind.bolus, drug="adrenaline",
           dose_value=10, dose_unit="microgram"),
        ev(3, kind=EventKind.infusion_rate_change, drug="noradrenaline",
           rate_value=0.2, rate_unit="microgram/kg/min"),
    ]
    s = build_state(events)
    assert s.running_infusion("noradrenaline").rate_value == 0.2
    assert s.recent_boluses[0].drug == "adrenaline"
    assert s.last_drug == "noradrenaline"  # last by timestamp


def test_infusion_stop_clears_running():
    events = [
        ev(0, kind=EventKind.infusion_start, drug="noradrenaline",
           rate_value=0.1, rate_unit="microgram/kg/min"),
        ev(5, kind=EventKind.infusion_stop, drug="noradrenaline"),
    ]
    s = build_state(events)
    assert s.running_infusion("noradrenaline") is None
    assert s.active_infusions["noradrenaline"].status == "stopped"


def test_rejected_events_ignored():
    events = [
        ev(0, kind=EventKind.infusion_start, drug="propofol",
           rate_value=100, rate_unit="microgram/kg/min",
           status=EventStatus.rejected),
    ]
    s = build_state(events)
    assert "propofol" not in s.active_infusions
