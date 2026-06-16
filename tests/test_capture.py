"""Two-tier capture: provisional placeholder → resolve in place (in-memory DB)."""
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, SQLModel, create_engine

from app.main import capture_payload, process_utterance
from app.models import Case, Event, EventKind, EventStatus


def mem_session() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _case(s: Session, weight=10.0) -> Case:
    c = Case(weight_kg=weight)
    s.add(c); s.commit(); s.refresh(c)
    return c


def _placeholder(s: Session, case: Case, when: datetime) -> Event:
    ev = Event(case_id=case.id, kind=EventKind.bolus,
               status=EventStatus.transcribing, timestamp=when)
    s.add(ev); s.commit(); s.refresh(ev)
    return ev


def _age(ev: Event, when: datetime) -> float:
    return abs((ev.timestamp.replace(tzinfo=timezone.utc) - when).total_seconds())


def test_resolve_fills_placeholder_and_keeps_timestamp():
    s = mem_session(); case = _case(s)
    when = datetime.now(timezone.utc) - timedelta(seconds=90)
    ev = _placeholder(s, case, when)

    process_utterance(s, case, "propofol twenty", source="asr", into_event=ev)
    s.refresh(ev)
    assert ev.drug == "propofol"
    assert ev.dose_value == 20            # spoken number normalised
    assert ev.status != EventStatus.transcribing
    assert _age(ev, when) < 1             # capture time preserved


def test_empty_transcript_keeps_editable_placeholder():
    s = mem_session(); case = _case(s)
    when = datetime.now(timezone.utc) - timedelta(seconds=30)
    ev = _placeholder(s, case, when)

    process_utterance(s, case, "", source="asr", into_event=ev)
    s.refresh(ev)
    assert ev.status == EventStatus.pending
    assert ev.requires_confirmation
    assert ev.drug is None
    assert "no speech" in (ev.source_text or "").lower()
    assert _age(ev, when) < 1             # timestamp survives a failed transcription


def test_capture_payload_shape():
    s = mem_session(); case = _case(s)
    ev = _placeholder(s, case, datetime.now(timezone.utc))
    p = capture_payload(s, case, focus_id=ev.id, transcript="propofol 20")
    assert {"rows", "chart_labels", "chart_plot", "chart_live_x",
            "focus_id", "transcript"} <= set(p)
    assert p["focus_id"] == ev.id
    assert p["rows"][0]["id"] == ev.id
    assert "transcribing" in p["rows"][0]["html"]   # one row HTML per event
    assert p["transcript"] == "propofol 20"


def test_unrecognised_drug_surfaces_what_was_heard():
    s = mem_session(); case = _case(s)
    when = datetime.now(timezone.utc)
    ev = _placeholder(s, case, when)

    process_utterance(s, case, "the patient looks stable", source="asr", into_event=ev)
    s.refresh(ev)
    assert ev.requires_confirmation              # flagged, stays in the log
    assert ev.source_text == "the patient looks stable"  # raw shown for correction
