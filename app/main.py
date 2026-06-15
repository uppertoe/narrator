"""FastAPI app: case board, utterance entry, CRUD with audit, exports.

Phase 0 + 1: no audio, no LLM. Utterances are typed into a text box and run
through the naive parser → deterministic validator → state machine. Clean
candidates are accepted onto the timeline; flagged ones land in the pending
review column.
"""
from __future__ import annotations

import csv
import io
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, FastAPI, Form, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select
from starlette.concurrency import run_in_threadpool

from app.asr import get_asr
from app.chart import render_chart_svg
from app.db import engine, get_session, init_db
from app.drugs import candidate_units, forms_and_band
from app.models import (
    Case, CaseConvention, CreatedBy, Event, EventKind, EventRevision, EventStatus,
)
from app.extract import extract_candidates
from app.parse import Candidate
from app.resolve import resolve_unit
from app.state import build_state
from app.validate import scope_of, validate_candidate

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Narrator", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="app/templates")

DEFAULT_TZ = "Australia/Melbourne"
TIMEZONES = [
    "Australia/Melbourne", "Australia/Sydney", "Australia/Brisbane",
    "Australia/Adelaide", "Australia/Perth", "Australia/Darwin",
    "Australia/Hobart", "Pacific/Auckland", "UTC",
]


def _local(dt: datetime | None, tzname: str | None) -> datetime | None:
    """Render a stored (UTC) timestamp in the case's timezone."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        zi = ZoneInfo(tzname or DEFAULT_TZ)
    except (ZoneInfoNotFoundError, ValueError):
        zi = ZoneInfo(DEFAULT_TZ)
    return dt.astimezone(zi)


templates.env.filters["hm"] = lambda dt, tz=None: _local(dt, tz).strftime("%H:%M:%S") if dt else ""
templates.env.filters["hmm"] = lambda dt, tz=None: _local(dt, tz).strftime("%H:%M") if dt else ""
templates.env.filters["dtlocal"] = lambda dt, tz=None: _local(dt, tz).strftime("%Y-%m-%d %H:%M") if dt else ""
templates.env.filters["dtinput"] = lambda dt, tz=None: _local(dt, tz).strftime("%Y-%m-%dT%H:%M") if dt else ""
templates.env.globals["TIMEZONES"] = TIMEZONES

from app.drugs import APPROVED_DRUGS  # noqa: E402

templates.env.globals["DRUG_NAMES"] = sorted(APPROVED_DRUGS)
templates.env.globals["DOSE_UNITS"] = [
    "microgram", "microgram/kg", "milligram", "milligram/kg", "gram",
    "unit", "unit/kg", "mmol", "mmol/kg", "mL"]
templates.env.globals["RATE_UNITS"] = [
    "microgram/kg/min", "microgram/min", "microgram/kg/hr", "microgram/hr",
    "mg/kg/hr", "mg/hr", "mL/hr", "unit/kg/hr", "unit/hr"]
# Per-kg vs absolute unit options offered at a disambiguation prompt.
templates.env.globals["unit_choices"] = lambda drug, kind: candidate_units(
    drug, "bolus" if kind == "bolus" else "infusion")


# --- Helpers ---------------------------------------------------------------
def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def event_to_dict(e: Event) -> dict:
    return {
        "id": e.id, "case_id": e.case_id,
        "timestamp": e.timestamp.isoformat() if e.timestamp else None,
        "kind": e.kind.value, "drug": e.drug,
        "dose_value": e.dose_value, "dose_unit": e.dose_unit,
        "rate_value": e.rate_value, "rate_unit": e.rate_unit,
        "route": e.route, "phase_label": e.phase_label,
        "source_text": e.source_text, "confidence": e.confidence,
        "status": e.status.value, "created_by": e.created_by.value,
        "requires_confirmation": e.requires_confirmation,
        "ambiguity_reason": e.ambiguity_reason, "inferred_unit": e.inferred_unit,
        "kind_guessed": e.kind_guessed, "note": e.note,
    }


def record_revision(session: Session, event: Event, *, previous: dict | None,
                    new: dict | None, by: str = "clinician",
                    reason: str | None = None) -> None:
    session.add(EventRevision(
        event_id=event.id,
        previous_json=json.dumps(previous) if previous is not None else None,
        new_json=json.dumps(new) if new is not None else None,
        changed_by=by, reason=reason,
    ))


def get_case(session: Session, case_id: int) -> Case | None:
    return session.get(Case, case_id)


def case_events(session: Session, case_id: int,
                include_rejected: bool = False) -> list[Event]:
    stmt = select(Event).where(Event.case_id == case_id)
    if not include_rejected:
        stmt = stmt.where(Event.status != EventStatus.rejected)
    return list(session.exec(stmt.order_by(Event.timestamp)))


def board_context(session: Session, case: Case, notice: str | None = None) -> dict:
    events = case_events(session, case.id)
    pending = [e for e in events if e.status == EventStatus.pending]
    accepted = sorted(
        [e for e in events if e.status == EventStatus.accepted],
        key=lambda e: e.timestamp,
    )
    return {
        "case": case, "pending": pending, "accepted": accepted,
        "chart_svg": render_chart_svg(case, events), "notice": notice,
        "kinds": [k.value for k in EventKind],
    }


def board_response(request: Request, session: Session, case: Case,
                   notice: str | None = None) -> HTMLResponse:
    ctx = board_context(session, case, notice)
    return templates.TemplateResponse(request, "partials/board.html",
                                      {"request": request, **ctx})


def render_board_str(session: Session, case: Case, notice: str | None = None) -> str:
    """Render the board partial to a string (for WebSocket push)."""
    return templates.get_template("partials/board.html").render(
        board_context(session, case, notice))


def load_conventions(session: Session, case_id: int) -> dict[tuple[str, str], str]:
    rows = session.exec(select(CaseConvention).where(CaseConvention.case_id == case_id))
    return {(r.drug, r.scope): r.unit for r in rows}


def upsert_convention(session: Session, case_id: int, drug: str, scope: str,
                      unit: str) -> bool:
    """Record/learn a unit convention. Returns True if it changed."""
    row = session.exec(select(CaseConvention).where(
        CaseConvention.case_id == case_id, CaseConvention.drug == drug,
        CaseConvention.scope == scope)).first()
    if row:
        if row.unit == unit:
            return False
        row.unit = unit
        session.add(row)
        return True
    session.add(CaseConvention(case_id=case_id, drug=drug, scope=scope, unit=unit))
    return True


def backfill_unit(session: Session, case_id: int, drug: str, scope: str,
                  unit: str) -> int:
    """Relabel earlier *inferred* events of this drug+scope to the learned unit.

    Only touches guesses (inferred_unit), never explicitly-set units. The recorded
    number is unchanged — we only correct the assumed unit. Returns the count."""
    n = 0
    stmt = select(Event).where(
        Event.case_id == case_id, Event.drug == drug,
        Event.status == EventStatus.accepted,
        Event.inferred_unit == True)  # noqa: E712
    for ev in session.exec(stmt):
        if scope_of(ev.kind) != scope:
            continue
        current = ev.dose_unit if scope == "bolus" else ev.rate_unit
        if current == unit:
            continue
        prev = event_to_dict(ev)
        if scope == "bolus":
            ev.dose_unit = unit
        else:
            ev.rate_unit = unit
        ev.inferred_unit = False
        session.add(ev)
        record_revision(session, ev, previous=prev, new=event_to_dict(ev),
                        reason="unit convention backfill")
        n += 1
    return n


def apply_convention_to_pending(session: Session, case_id: int, drug: str,
                                scope: str, unit: str,
                                exclude_id: int | None = None) -> int:
    """Snap queued pending entries (held only for this unit question) into place.

    Lets the question be non-blocking: log as many doses as you like while it's
    open, then answering once clears every same-drug entry waiting on the unit."""
    n = 0
    stmt = select(Event).where(
        Event.case_id == case_id, Event.drug == drug,
        Event.status == EventStatus.pending)
    for ev in session.exec(stmt):
        if ev.id == exclude_id or scope_of(ev.kind) != scope:
            continue
        value = ev.dose_value if scope == "bolus" else ev.rate_value
        current = ev.dose_unit if scope == "bolus" else ev.rate_unit
        if value is None or current is not None:
            continue  # pending for some other reason (missing number) — leave it
        prev = event_to_dict(ev)
        if scope == "bolus":
            ev.dose_unit = unit
        else:
            ev.rate_unit = unit
        ev.inferred_unit = False
        ev.requires_confirmation = False
        ev.ambiguity_reason = None
        ev.status = EventStatus.accepted
        session.add(ev)
        record_revision(session, ev, previous=prev, new=event_to_dict(ev),
                        reason="unit convention applied to pending")
        n += 1
    return n


def _learn_notice(drug: str, unit: str, backfilled: int, pending_done: int) -> str | None:
    parts = []
    if backfilled:
        parts.append(f"corrected {backfilled} earlier")
    if pending_done:
        parts.append(f"resolved {pending_done} pending")
    if not parts:
        return None
    return f"{drug} = {unit} ({', '.join(parts)})"


def candidate_to_event(case: Case, c: Candidate) -> Event:
    status = EventStatus.pending if c.requires_confirmation else EventStatus.accepted
    return Event(
        case_id=case.id, timestamp=utcnow(), kind=c.kind, drug=c.drug,
        dose_value=c.dose_value, dose_unit=c.dose_unit,
        rate_value=c.rate_value, rate_unit=c.rate_unit, route=c.route,
        phase_label=c.phase_label, source_text=c.source_text,
        confidence=c.confidence, status=status, created_by=CreatedBy.model,
        requires_confirmation=c.requires_confirmation,
        ambiguity_reason=c.ambiguity_reason, inferred_unit=c.inferred_unit,
        kind_guessed=c.kind_guessed,
    )


# --- Pages -----------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def index(request: Request, session: Session = Depends(get_session)):
    cases = list(session.exec(select(Case).order_by(Case.created_at.desc())))
    return templates.TemplateResponse(request, "index.html", {"request": request, "cases": cases})


@app.post("/cases")
def create_case(
    weight_kg: float | None = Form(None),
    patient_label: str | None = Form(None),
    anaesthetist: str | None = Form(None),
    timezone: str = Form(DEFAULT_TZ),
    session: Session = Depends(get_session),
):
    case = Case(weight_kg=weight_kg, patient_label=patient_label,
                anaesthetist=anaesthetist, timezone=timezone or DEFAULT_TZ,
                started_at=utcnow())
    session.add(case)
    session.commit()
    session.refresh(case)
    return RedirectResponse(f"/case/{case.id}", status_code=303)


@app.get("/case/{case_id}", response_class=HTMLResponse)
def case_view(case_id: int, request: Request, session: Session = Depends(get_session)):
    case = get_case(session, case_id)
    if not case:
        return RedirectResponse("/", status_code=303)
    events = case_events(session, case_id)
    pending = [e for e in events if e.status == EventStatus.pending]
    accepted = sorted([e for e in events if e.status == EventStatus.accepted],
                      key=lambda e: e.timestamp)
    return templates.TemplateResponse(request, "case.html", {
        "request": request, "case": case, "pending": pending,
        "accepted": accepted, "chart_svg": render_chart_svg(case, events),
        "kinds": [k.value for k in EventKind],
    })


# --- Utterance entry (the Phase 1 text box) --------------------------------
def process_utterance(session: Session, case: Case, text: str) -> str | None:
    """Parse an utterance into validated events; learn/backfill conventions.

    Shared by the typed-text route and the audio WebSocket. Returns a subtle
    notice if a convention correction happened."""
    state = build_state(case_events(session, case.id))
    conventions = load_conventions(session, case.id)
    notice = None
    for cand in extract_candidates(text, state, case.weight_kg):
        validate_candidate(cand, state, case.weight_kg, conventions)
        ev = candidate_to_event(case, cand)
        session.add(ev)
        session.commit()
        session.refresh(ev)
        record_revision(session, ev, previous=None, new=event_to_dict(ev),
                        by="model", reason="parsed from utterance")
        # A spoken unit teaches the case convention and corrects earlier guesses.
        scope = scope_of(ev.kind)
        unit = (ev.dose_unit if scope == "bolus" else ev.rate_unit) if scope else None
        if cand.unit_source == "explicit" and scope and unit and ev.drug:
            if upsert_convention(session, case.id, ev.drug, scope, unit):
                bf = backfill_unit(session, case.id, ev.drug, scope, unit)
                pend = apply_convention_to_pending(
                    session, case.id, ev.drug, scope, unit, exclude_id=ev.id)
                notice = _learn_notice(ev.drug, unit, bf, pend) or notice
            conventions[(ev.drug, scope)] = unit
    session.commit()
    return notice


@app.post("/case/{case_id}/utterance", response_class=HTMLResponse)
def add_utterance(case_id: int, request: Request, text: str = Form(...),
                  session: Session = Depends(get_session)):
    case = get_case(session, case_id)
    notice = process_utterance(session, case, text)
    return board_response(request, session, case, notice=notice)


# --- Pending queue actions -------------------------------------------------
@app.post("/case/{case_id}/events/{event_id}/accept", response_class=HTMLResponse)
def accept_event(case_id: int, event_id: int, request: Request,
                 session: Session = Depends(get_session)):
    case = get_case(session, case_id)
    ev = session.get(Event, event_id)
    if ev:
        prev = event_to_dict(ev)
        ev.status = EventStatus.accepted
        ev.requires_confirmation = False
        session.add(ev)
        record_revision(session, ev, previous=prev, new=event_to_dict(ev),
                        reason="confirmed from pending")
        session.commit()
    return board_response(request, session, case)


@app.post("/case/{case_id}/events/{event_id}/reject", response_class=HTMLResponse)
def reject_event(case_id: int, event_id: int, request: Request,
                 session: Session = Depends(get_session)):
    case = get_case(session, case_id)
    ev = session.get(Event, event_id)
    if ev:
        prev = event_to_dict(ev)
        ev.status = EventStatus.rejected
        session.add(ev)
        record_revision(session, ev, previous=prev, new=event_to_dict(ev),
                        reason="rejected")
        session.commit()
    return board_response(request, session, case)


@app.post("/case/{case_id}/events/{event_id}/resolve-unit", response_class=HTMLResponse)
def resolve_unit_event(case_id: int, event_id: int, request: Request,
                       unit: str = Form(...), session: Session = Depends(get_session)):
    case = get_case(session, case_id)
    ev = session.get(Event, event_id)
    notice = None
    if ev:
        scope = scope_of(ev.kind)
        prev = event_to_dict(ev)
        if scope == "bolus":
            ev.dose_unit = unit
        elif scope == "infusion":
            ev.rate_unit = unit
        ev.inferred_unit = False
        ev.requires_confirmation = False
        ev.ambiguity_reason = None
        ev.status = EventStatus.accepted
        session.add(ev)
        record_revision(session, ev, previous=prev, new=event_to_dict(ev),
                        reason="unit chosen at prompt")
        if scope and ev.drug:
            upsert_convention(session, case_id, ev.drug, scope, unit)
            bf = backfill_unit(session, case_id, ev.drug, scope, unit)
            pend = apply_convention_to_pending(
                session, case_id, ev.drug, scope, unit, exclude_id=ev.id)
            notice = _learn_notice(ev.drug, unit, bf, pend)
        session.commit()
    return board_response(request, session, case, notice=notice)


def _default_unit(drug: str, scope: str) -> str | None:
    fb = forms_and_band(drug, scope)
    if not fb:
        return None
    perkg, absf, _ = fb
    return absf if scope == "bolus" else perkg   # bolus→absolute, infusion→per-kg


@app.post("/case/{case_id}/events/{event_id}/flip-kind", response_class=HTMLResponse)
def flip_kind(case_id: int, event_id: int, request: Request,
              session: Session = Depends(get_session)):
    """Flip a guessed event between bolus and infusion rate-change (same number)."""
    case = get_case(session, case_id)
    ev = session.get(Event, event_id)
    if ev and ev.drug and scope_of(ev.kind):
        state = build_state(case_events(session, case_id))
        conventions = load_conventions(session, case_id)
        prev = event_to_dict(ev)
        if ev.kind == EventKind.bolus:
            value = ev.dose_value
            running = state.running_infusion(ev.drug)
            res = resolve_unit(ev.drug, "infusion", value, case.weight_kg,
                               convention=conventions.get((ev.drug, "infusion")),
                               running_unit=running.rate_unit if running else None)
            ev.kind = (EventKind.infusion_rate_change if running
                       else EventKind.infusion_start)
            ev.rate_value = value
            ev.rate_unit = res.unit or _default_unit(ev.drug, "infusion")
            ev.dose_value = ev.dose_unit = None
            ev.inferred_unit = res.unit is None or res.inferred
        else:
            value = ev.rate_value
            res = resolve_unit(ev.drug, "bolus", value, case.weight_kg,
                               convention=conventions.get((ev.drug, "bolus")))
            ev.kind = EventKind.bolus
            ev.dose_value = value
            ev.dose_unit = res.unit or _default_unit(ev.drug, "bolus")
            ev.rate_value = ev.rate_unit = None
            ev.inferred_unit = res.unit is None or res.inferred
        ev.kind_guessed = True  # still flippable
        session.add(ev)
        record_revision(session, ev, previous=prev, new=event_to_dict(ev),
                        reason="kind flipped (bolus↔rate)")
        session.commit()
    return board_response(request, session, case)


# --- Manual add / edit / delete --------------------------------------------
@app.get("/case/{case_id}/add", response_class=HTMLResponse)
def add_form(case_id: int, request: Request, session: Session = Depends(get_session)):
    case = get_case(session, case_id)
    return templates.TemplateResponse(request, "partials/edit_form.html", {
        "request": request, "case": case, "event": None,
        "kinds": [k.value for k in EventKind],
    })


@app.get("/case/{case_id}/events/{event_id}/edit", response_class=HTMLResponse)
def edit_form(case_id: int, event_id: int, request: Request,
              session: Session = Depends(get_session)):
    case = get_case(session, case_id)
    ev = session.get(Event, event_id)
    return templates.TemplateResponse(request, "partials/edit_form.html", {
        "request": request, "case": case, "event": ev,
        "kinds": [k.value for k in EventKind],
    })


def _parse_dt(value: str | None, tzname: str | None) -> datetime:
    """Parse a datetime-local value (naive, in the case tz) into UTC."""
    if not value:
        return utcnow()
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return utcnow()
    try:
        zi = ZoneInfo(tzname or DEFAULT_TZ)
    except (ZoneInfoNotFoundError, ValueError):
        zi = ZoneInfo(DEFAULT_TZ)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=zi)
    return dt.astimezone(timezone.utc)


def _f(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


@app.post("/case/{case_id}/events", response_class=HTMLResponse)
def create_event(
    case_id: int, request: Request,
    kind: str = Form(...), timestamp: str = Form(None), drug: str = Form(None),
    dose_value: str = Form(None), dose_unit: str = Form(None),
    rate_value: str = Form(None), rate_unit: str = Form(None),
    route: str = Form("IV"), phase_label: str = Form(None), note: str = Form(None),
    session: Session = Depends(get_session),
):
    case = get_case(session, case_id)
    ev = Event(
        case_id=case_id, timestamp=_parse_dt(timestamp, case.timezone), kind=EventKind(kind),
        drug=drug or None, dose_value=_f(dose_value), dose_unit=dose_unit or None,
        rate_value=_f(rate_value), rate_unit=rate_unit or None, route=route or None,
        phase_label=phase_label or None, note=note or None,
        status=EventStatus.accepted, created_by=CreatedBy.clinician,
    )
    session.add(ev)
    session.commit()
    session.refresh(ev)
    record_revision(session, ev, previous=None, new=event_to_dict(ev),
                    reason="manual add")
    session.commit()
    return board_response(request, session, case)


@app.put("/case/{case_id}/events/{event_id}", response_class=HTMLResponse)
def update_event(
    case_id: int, event_id: int, request: Request,
    kind: str = Form(...), timestamp: str = Form(None), drug: str = Form(None),
    dose_value: str = Form(None), dose_unit: str = Form(None),
    rate_value: str = Form(None), rate_unit: str = Form(None),
    route: str = Form("IV"), phase_label: str = Form(None), note: str = Form(None),
    session: Session = Depends(get_session),
):
    case = get_case(session, case_id)
    ev = session.get(Event, event_id)
    if ev:
        prev = event_to_dict(ev)
        ev.kind = EventKind(kind)
        ev.timestamp = _parse_dt(timestamp, case.timezone)
        ev.drug = drug or None
        ev.dose_value = _f(dose_value)
        ev.dose_unit = dose_unit or None
        ev.rate_value = _f(rate_value)
        ev.rate_unit = rate_unit or None
        ev.route = route or None
        ev.phase_label = phase_label or None
        ev.note = note or None
        ev.status = EventStatus.corrected if ev.status == EventStatus.pending \
            else EventStatus.accepted
        ev.requires_confirmation = False
        session.add(ev)
        record_revision(session, ev, previous=prev, new=event_to_dict(ev),
                        reason="manual edit")
        session.commit()
    return board_response(request, session, case)


@app.delete("/case/{case_id}/events/{event_id}", response_class=HTMLResponse)
def delete_event(case_id: int, event_id: int, request: Request,
                 session: Session = Depends(get_session)):
    case = get_case(session, case_id)
    ev = session.get(Event, event_id)
    if ev:
        prev = event_to_dict(ev)
        ev.status = EventStatus.rejected  # soft-delete keeps the audit trail intact
        session.add(ev)
        record_revision(session, ev, previous=prev, new=None, reason="deleted")
        session.commit()
    return board_response(request, session, case)


# --- Exports ---------------------------------------------------------------
@app.get("/case/{case_id}/export.json")
def export_json(case_id: int, session: Session = Depends(get_session)):
    case = get_case(session, case_id)
    events = case_events(session, case_id, include_rejected=True)
    revisions = list(session.exec(
        select(EventRevision).join(Event).where(Event.case_id == case_id)))
    payload = {
        "case": {
            "id": case.id, "patient_label": case.patient_label,
            "weight_kg": case.weight_kg, "anaesthetist": case.anaesthetist,
            "started_at": case.started_at.isoformat(),
        },
        "events": [event_to_dict(e) for e in events],
        "audit": [{
            "event_id": r.event_id, "previous": json.loads(r.previous_json) if r.previous_json else None,
            "new": json.loads(r.new_json) if r.new_json else None,
            "changed_by": r.changed_by, "changed_at": r.changed_at.isoformat(),
            "reason": r.reason,
        } for r in revisions],
    }
    return JSONResponse(payload)


@app.get("/case/{case_id}/export.csv")
def export_csv(case_id: int, session: Session = Depends(get_session)):
    events = sorted(case_events(session, case_id), key=lambda e: e.timestamp)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["timestamp", "kind", "drug", "dose_value", "dose_unit",
                "rate_value", "rate_unit", "route", "phase_label", "status",
                "source_text"])
    for e in events:
        w.writerow([e.timestamp.isoformat(), e.kind.value, e.drug or "",
                    e.dose_value or "", e.dose_unit or "", e.rate_value or "",
                    e.rate_unit or "", e.route or "", e.phase_label or "",
                    e.status.value, e.source_text or ""])
    return Response(buf.getvalue(), media_type="text/csv", headers={
        "Content-Disposition": f"attachment; filename=case_{case_id}.csv"})


# --- Audio (WebSocket) -----------------------------------------------------
def _handle_audio_chunk(case_id: int, data: bytes) -> dict:
    """Transcribe one utterance and run it through the pipeline (own session)."""
    text = get_asr().transcribe(data)
    if not text.strip():
        return {"transcript": "", "board": None, "notice": None}
    with Session(engine) as session:
        case = session.get(Case, case_id)
        if not case:
            return {"transcript": text, "board": None, "notice": None}
        notice = process_utterance(session, case, text)
        return {"transcript": text,
                "board": render_board_str(session, case, notice),
                "notice": notice}


@app.websocket("/ws/case/{case_id}")
async def ws_audio(websocket: WebSocket, case_id: int):
    """Receive VAD-segmented audio utterances; push back transcript + board."""
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_bytes()
            # Transcription + DB work is blocking → run off the event loop.
            result = await run_in_threadpool(_handle_audio_chunk, case_id, data)
            await websocket.send_json(result)
    except WebSocketDisconnect:
        return


@app.get("/case/{case_id}/report", response_class=HTMLResponse)
def report(case_id: int, request: Request, session: Session = Depends(get_session)):
    """Printable report. Use the browser's Print → Save as PDF.

    Phase 4 can swap this for server-side PDF (e.g. WeasyPrint) reusing the
    same SVG, but browser-print keeps the prototype dependency-free.
    """
    case = get_case(session, case_id)
    events = sorted(case_events(session, case_id), key=lambda e: e.timestamp)
    return templates.TemplateResponse(request, "report.html", {
        "request": request, "case": case, "events": events,
        "chart_svg": render_chart_svg(case, case_events(session, case_id)),
    })
