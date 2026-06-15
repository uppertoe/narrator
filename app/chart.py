"""Server-rendered anaesthetic chart (SVG).

Fixed time scale (PX_PER_MIN px/min), ≥60-min window that grows as the case
progresses. The drug-name lane labels are a FROZEN left column; only the time
plot scrolls horizontally (auto-pinned to the live edge). The axis snaps to
clean 5-minute clock boundaries in the case timezone.

`render_chart(case, events)` → {"labels", "plot", "height"} for the live board
(frozen labels + scrolling plot). `render_chart_combined(...)` → one SVG with the
label gutter inline, for the printable report.
"""
from __future__ import annotations

from datetime import timedelta, timezone
from html import escape
from math import ceil
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.models import Case, Event, EventKind, EventStatus

LEFT = 132           # frozen label gutter width
LEFTPAD = 12         # small pad before the first time tick in the plot
RIGHT = 28
TOP = 44             # time-axis band
LANE_H = 52
PX_PER_MIN = 14
MIN_MINUTES = 60
TICK_MIN = 5

_UNIT_ABBR = {
    "microgram": "mcg", "microgram/kg": "mcg/kg", "milligram": "mg",
    "milligram/kg": "mg/kg", "gram": "g", "unit": "u", "unit/kg": "u/kg",
    "mmol": "mmol", "mmol/kg": "mmol/kg", "mL": "mL",
    "microgram/kg/min": "mcg/kg/min", "microgram/min": "mcg/min",
    "microgram/kg/hr": "mcg/kg/hr", "microgram/hr": "mcg/hr",
    "mg/kg/hr": "mg/kg/hr", "mg/hr": "mg/hr", "mL/hr": "mL/hr",
    "unit/kg/hr": "u/kg/hr", "unit/hr": "u/hr",
}


def _abbr(u: str | None) -> str:
    return _UNIT_ABBR.get(u or "", u or "")


def _zone(tzname: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(tzname or "Australia/Melbourne")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("Australia/Melbourne")


def _num(v: float | None) -> str:
    if v is None:
        return "?"
    return str(int(v)) if v == int(v) else f"{v:g}"


def _baseline(idx: int) -> float:
    return TOP + idx * LANE_H + LANE_H * 0.55


def _geometry(case: Case, events: list[Event]):
    zi = _zone(case.timezone)
    shown = [e for e in events if e.status == EventStatus.accepted]
    meds = [e for e in shown if e.kind != EventKind.phase]
    phases = [e for e in shown if e.kind == EventKind.phase]

    def aware(dt):
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt

    t0 = aware(case.started_at)
    times = [aware(e.timestamp) for e in shown]
    earliest = min([t0, *times]) if times else t0
    last = max(times) if times else t0

    local0 = earliest.astimezone(zi)
    local0 = local0.replace(minute=local0.minute - local0.minute % TICK_MIN,
                            second=0, microsecond=0)
    origin = local0.astimezone(timezone.utc)
    span_min = max(0.0, (last - origin).total_seconds() / 60)
    plot_min = max(MIN_MINUTES, ceil(span_min / TICK_MIN) * TICK_MIN + TICK_MIN)

    lane_order: list[str] = []
    for e in sorted(meds, key=lambda e: e.timestamp):
        if e.drug and e.drug not in lane_order:
            lane_order.append(e.drug)
    height = TOP + max(1, len(lane_order)) * LANE_H + 16
    return zi, meds, phases, origin, plot_min, lane_order, height, span_min


def _plot_body(parts, *, meds, phases, lane_order, origin, plot_min, height,
               zi, x_at_min, x_of, lane_x1):
    bottom = height - 10
    # time axis
    for i in range(0, plot_min + 1, TICK_MIN):
        gx = x_at_min(i)
        label = (origin + timedelta(minutes=i)).astimezone(zi).strftime("%H:%M")
        parts.append(f'<line x1="{gx:.1f}" y1="{TOP}" x2="{gx:.1f}" y2="{bottom}" '
                     f'stroke="#eee" stroke-width="1"/>')
        parts.append(f'<text x="{gx:.1f}" y="{TOP - 14}" font-size="11" fill="#666" '
                     f'text-anchor="middle">{label}</text>')

    for idx, drug in enumerate(lane_order):
        base = _baseline(idx)
        parts.append(f'<line x1="{x_at_min(0):.1f}" y1="{base:.1f}" x2="{lane_x1:.1f}" '
                     f'y2="{base:.1f}" stroke="#f3f3f3" stroke-width="1"/>')
        de = sorted([e for e in meds if e.drug == drug], key=lambda e: e.timestamp)
        rate_points = [e for e in de if e.kind in (
            EventKind.infusion_start, EventKind.infusion_rate_change)]
        stops = [e for e in de if e.kind == EventKind.infusion_stop]
        if rate_points:
            x_start = x_of(rate_points[0].timestamp)
            x_end = x_of(stops[-1].timestamp) if stops else lane_x1
            parts.append(f'<line x1="{x_start:.1f}" y1="{base:.1f}" x2="{x_end:.1f}" '
                         f'y2="{base:.1f}" stroke="#2563eb" stroke-width="2.5"/>')
            for e in rate_points:
                ex = x_of(e.timestamp)
                lab = f'{_num(e.rate_value)} {_abbr(e.rate_unit)}'.strip()
                parts.append(f'<circle cx="{ex:.1f}" cy="{base:.1f}" r="4" fill="#2563eb"/>')
                parts.append(f'<text x="{ex:.1f}" y="{base - 9:.1f}" font-size="11" '
                             f'fill="#1d4ed8" text-anchor="middle">{escape(lab)}</text>')
            for e in stops:
                ex = x_of(e.timestamp)
                parts.append(f'<text x="{ex:.1f}" y="{base + 5:.1f}" font-size="13" '
                             f'fill="#b91c1c" text-anchor="middle">✕</text>')
        for e in de:
            if e.kind != EventKind.bolus:
                continue
            ex = x_of(e.timestamp)
            lab = f'{_num(e.dose_value)} {_abbr(e.dose_unit)}'.strip()
            parts.append(f'<text x="{ex:.1f}" y="{base + 5:.1f}" font-size="13" '
                         f'fill="#7c3aed" text-anchor="middle">▼</text>')
            parts.append(f'<text x="{ex:.1f}" y="{base - 9:.1f}" font-size="11" '
                         f'fill="#6d28d9" text-anchor="middle">{escape(lab)}</text>')

    for e in phases:
        ex = x_of(e.timestamp)
        parts.append(f'<line x1="{ex:.1f}" y1="{TOP}" x2="{ex:.1f}" y2="{bottom}" '
                     f'stroke="#0f766e" stroke-width="1.5" stroke-dasharray="4 3"/>')
        parts.append(f'<text x="{ex + 3:.1f}" y="{TOP + 10}" font-size="10" '
                     f'fill="#0f766e">{escape(e.phase_label or "")}</text>')


def _lane_labels(lane_order: list[str], height: int) -> str:
    parts = [f'<svg width="{LEFT}" height="{height}" viewBox="0 0 {LEFT} {height}" '
             f'font-family="system-ui, sans-serif" class="anaes-labels">',
             f'<rect x="0" y="0" width="{LEFT}" height="{height}" fill="#fff"/>',
             f'<line x1="{LEFT - 0.5}" y1="0" x2="{LEFT - 0.5}" y2="{height}" '
             f'stroke="#e5e5e5" stroke-width="1"/>']
    for idx, drug in enumerate(lane_order):
        base = _baseline(idx)
        parts.append(f'<text x="{LEFT - 8}" y="{base + 4:.1f}" font-size="12" '
                     f'fill="#222" text-anchor="end">{escape(drug)}</text>')
    if not lane_order:
        parts.append(f'<text x="8" y="{TOP + 24}" font-size="12" fill="#999">drugs</text>')
    parts.append("</svg>")
    return "".join(parts)


def render_chart(case: Case, events: list[Event]) -> dict:
    zi, meds, phases, origin, plot_min, lane_order, height, span_min = _geometry(case, events)
    plot_w = LEFTPAD + plot_min * PX_PER_MIN + RIGHT

    def x_at_min(m: float) -> float:
        return LEFTPAD + m * PX_PER_MIN

    def x_of(dt) -> float:
        d = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
        return x_at_min((d - origin).total_seconds() / 60)

    parts = [f'<svg width="{plot_w}" height="{height}" viewBox="0 0 {plot_w} {height}" '
             f'font-family="system-ui, sans-serif" class="anaes-chart">',
             f'<rect x="0" y="0" width="{plot_w}" height="{height}" fill="#fff"/>']
    if lane_order or phases:
        _plot_body(parts, meds=meds, phases=phases, lane_order=lane_order,
                   origin=origin, plot_min=plot_min, height=height, zi=zi,
                   x_at_min=x_at_min, x_of=x_of, lane_x1=plot_w - RIGHT)
    else:
        parts.append(f'<text x="{plot_w / 2}" y="{height / 2}" font-size="13" '
                     f'fill="#999" text-anchor="middle">No events yet</text>')
    parts.append("</svg>")
    return {"labels": _lane_labels(lane_order, height),
            "plot": "".join(parts), "height": height,
            "live_x": round(LEFTPAD + span_min * PX_PER_MIN)}


def render_chart_combined(case: Case, events: list[Event]) -> str:
    """Single SVG with the label gutter inline — for the printable report."""
    zi, meds, phases, origin, plot_min, lane_order, height, _span = _geometry(case, events)
    width = LEFT + plot_min * PX_PER_MIN + RIGHT

    def x_at_min(m: float) -> float:
        return LEFT + m * PX_PER_MIN

    def x_of(dt) -> float:
        d = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
        return x_at_min((d - origin).total_seconds() / 60)

    parts = [f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
             f'font-family="system-ui, sans-serif" class="anaes-chart">',
             f'<rect x="0" y="0" width="{width}" height="{height}" fill="#fff"/>']
    if lane_order or phases:
        _plot_body(parts, meds=meds, phases=phases, lane_order=lane_order,
                   origin=origin, plot_min=plot_min, height=height, zi=zi,
                   x_at_min=x_at_min, x_of=x_of, lane_x1=width - RIGHT)
        for idx, drug in enumerate(lane_order):
            parts.append(f'<text x="{LEFT - 8}" y="{_baseline(idx) + 4:.1f}" font-size="12" '
                         f'fill="#222" text-anchor="end">{escape(drug)}</text>')
    else:
        parts.append(f'<text x="{width / 2}" y="{height / 2}" font-size="13" '
                     f'fill="#999" text-anchor="middle">No events yet</text>')
    parts.append("</svg>")
    return "".join(parts)
