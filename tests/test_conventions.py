"""Convention learning + backfill (in-memory DB, no HTTP)."""
from sqlmodel import Session, SQLModel, create_engine, select

from app.main import (
    apply_convention_to_pending, backfill_unit, load_conventions, upsert_convention,
)
from app.models import Case, Event, EventKind, EventRevision, EventStatus


def mem_session() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_upsert_learn_and_backfill():
    s = mem_session()
    case = Case(weight_kg=18.4)
    s.add(case)
    s.commit()
    s.refresh(case)

    # An earlier dopamine infusion whose unit we *guessed* (inferred) as per-kg.
    ev = Event(case_id=case.id, kind=EventKind.infusion_start, drug="dopamine",
               rate_value=10, rate_unit="microgram/kg/min", inferred_unit=True,
               status=EventStatus.accepted)
    s.add(ev)
    s.commit()
    s.refresh(ev)

    # The case now learns dopamine runs in absolute microgram/min.
    assert upsert_convention(s, case.id, "dopamine", "infusion", "microgram/min") is True
    n = backfill_unit(s, case.id, "dopamine", "infusion", "microgram/min")
    s.commit()

    assert n == 1
    s.refresh(ev)
    assert ev.rate_unit == "microgram/min"   # earlier entry corrected
    assert ev.inferred_unit is False         # now established
    assert load_conventions(s, case.id) == {("dopamine", "infusion"): "microgram/min"}
    # idempotent
    assert upsert_convention(s, case.id, "dopamine", "infusion", "microgram/min") is False
    # audit trail captured the correction
    revs = list(s.exec(select(EventRevision).where(EventRevision.event_id == ev.id)))
    assert any(r.reason == "unit convention backfill" for r in revs)


def test_backfill_leaves_explicit_units_untouched():
    s = mem_session()
    case = Case(weight_kg=18.4)
    s.add(case)
    s.commit()
    s.refresh(case)
    # An explicitly-set unit (not inferred) must NOT be rewritten by backfill.
    ev = Event(case_id=case.id, kind=EventKind.infusion_start, drug="dopamine",
               rate_value=10, rate_unit="microgram/kg/min", inferred_unit=False,
               status=EventStatus.accepted)
    s.add(ev)
    s.commit()
    s.refresh(ev)
    n = backfill_unit(s, case.id, "dopamine", "infusion", "microgram/min")
    assert n == 0
    s.refresh(ev)
    assert ev.rate_unit == "microgram/kg/min"


def test_answer_resolves_other_queued_pending():
    # Non-blocking: two same-drug ambiguous entries queued; answering once clears
    # the rest. (A third pending for a different reason stays pending.)
    s = mem_session()
    case = Case(weight_kg=18.4)
    s.add(case)
    s.commit()
    s.refresh(case)

    def pending_adr(rate):
        e = Event(case_id=case.id, kind=EventKind.infusion_rate_change,
                  drug="adrenaline", rate_value=rate, rate_unit=None,
                  inferred_unit=True, requires_confirmation=True,
                  ambiguity_reason="Unit unclear — per-kg or absolute?",
                  status=EventStatus.pending)
        s.add(e)
        s.commit()
        s.refresh(e)
        return e

    a, b = pending_adr(0.5), pending_adr(0.3)
    # a different pending, missing the *number* — must NOT be auto-resolved
    no_value = Event(case_id=case.id, kind=EventKind.infusion_rate_change,
                     drug="adrenaline", rate_value=None, rate_unit="microgram/kg/min",
                     requires_confirmation=True, ambiguity_reason="Rate not specified",
                     status=EventStatus.pending)
    s.add(no_value)
    s.commit()
    s.refresh(no_value)

    # User answers `a`; convention applies to the other unit-ambiguous pending `b`.
    n = apply_convention_to_pending(s, case.id, "adrenaline", "infusion",
                                    "microgram/min", exclude_id=a.id)
    s.commit()
    assert n == 1
    s.refresh(b)
    s.refresh(no_value)
    assert b.status == EventStatus.accepted and b.rate_unit == "microgram/min"
    assert no_value.status == EventStatus.pending   # still needs its number
