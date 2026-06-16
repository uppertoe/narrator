"""Database models.

One `Event` table holds the tagged union of medication + phase events that
share the timeline. `EventRevision` is the immutable audit trail: every edit or
delete writes a before/after JSON snapshot here.
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EventKind(str, enum.Enum):
    bolus = "bolus"
    infusion_start = "infusion_start"
    infusion_rate_change = "infusion_rate_change"
    infusion_stop = "infusion_stop"
    phase = "phase"  # procedural milestone, e.g. bypass on/off, cross-clamp


class EventStatus(str, enum.Enum):
    transcribing = "transcribing"  # provisional placeholder: timestamp locked, awaiting ASR
    noise = "noise"           # captured sound held no command (no drug, no number)
    accepted = "accepted"
    pending = "pending"       # awaiting human confirm/correct
    corrected = "corrected"
    rejected = "rejected"


class CreatedBy(str, enum.Enum):
    model = "model"
    clinician = "clinician"
    imported = "imported"


class Case(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    patient_label: str | None = None        # de-identified label only
    weight_kg: float | None = None
    anaesthetist: str | None = None
    timezone: str = "Australia/Melbourne"   # IANA tz for display/entry
    started_at: datetime = Field(default_factory=utcnow)
    created_at: datetime = Field(default_factory=utcnow)


class Event(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    case_id: int = Field(foreign_key="case.id", index=True)

    # Canonical action time (server receipt time unless edited by a human).
    timestamp: datetime = Field(default_factory=utcnow, index=True)
    kind: EventKind

    drug: str | None = None                 # canonical drug name (or None for phase)
    dose_value: float | None = None
    dose_unit: str | None = None
    rate_value: float | None = None
    rate_unit: str | None = None
    route: str | None = "IV"
    phase_label: str | None = None          # for kind == phase

    # Provenance / safety
    source_text: str | None = None
    confidence: float | None = None
    status: EventStatus = EventStatus.accepted
    created_by: CreatedBy = CreatedBy.clinician
    requires_confirmation: bool = False
    ambiguity_reason: str | None = None
    inferred_unit: bool = False
    kind_guessed: bool = False     # bolus/rate decided without an explicit cue → offer ↔ flip
    note: str | None = None

    created_at: datetime = Field(default_factory=utcnow)


class CaseConvention(SQLModel, table=True):
    """A unit convention the case has learned, e.g. adrenaline infusion is
    microgram/kg/min. Set when a unit is spoken explicitly or chosen at a
    disambiguation prompt; thereafter applied to that drug+scope automatically."""
    id: int | None = Field(default=None, primary_key=True)
    case_id: int = Field(foreign_key="case.id", index=True)
    drug: str
    scope: str          # "bolus" | "infusion"
    unit: str
    created_at: datetime = Field(default_factory=utcnow)


class EventRevision(SQLModel, table=True):
    """Immutable audit record. Append-only; never updated or deleted."""
    id: int | None = Field(default=None, primary_key=True)
    event_id: int = Field(foreign_key="event.id", index=True)
    previous_json: str | None = None        # None for a create
    new_json: str | None = None             # None for a delete
    changed_by: str = "clinician"
    changed_at: datetime = Field(default_factory=utcnow)
    reason: str | None = None
