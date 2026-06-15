"""Server-rendered anaesthetic chart (SVG).

Fixed time scale: time runs left→right at PX_PER_MIN pixels/minute, anchored at
the case start. The window is at least 60 minutes and grows as the case
progresses; the SVG is its true pixel width and the page scrolls horizontally
(auto-scrolled to the live edge after each update). Labels are in the case's
timezone.

One fixed horizontal track per drug. An infusion is a line from start to stop
along its track; rate changes are labelled dots (no vertical stepping). Boluses
are ▼ markers. Procedural milestones are vertical dashed lines. The same SVG is
embedded in the printable report.
"""
from __future__ import annotations

from datetime import timedelta, timezone
from html import escape
from math import ceil
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.models import Case, Event, EventKind, EventStatus

# Geometry
LEFT = 150           # label gutter
RIGHT = 28
TOP = 46             # time axis band
LANE_H = 56
PX_PER_MIN = 14      # ~60 min ≈ 840px across; wider cases scroll
MIN_MINUTES = 60
TICK_MIN = 5         # gridline/label spacing in minutes

_UNIT_ABBR = {
    "microgram": "mcg", "microgram/kg": "mcg/kg", "milligram": "mg",
    "milligram/kg": "mg/kg", "gram": "g", "unit": "u", "unit/kg": "u/kg",
    "mmol": "mmol", "mmol/kg": "mmol/kg", "mL": "mL",
    "microgram/kg/min": "mcg/kg/min", "microgram/min": "mcg/min",
    "microgram/kg/hr": "mcg/kg/hr", "microgram/hr": "mcg/hr",
    "mg/kg/hr": "mg/kg/hr", "mg/hr": "mg/hr", "mL/hr": "mL/hr",
    "unit/kg/hr": "u/kg/hr", "unit/hr": "u/hr",
}


def _abbr(unit: str | None) -> str:
    return _UNIT_ABBR.get(unit or "", unit or "")


def _zone(tzname: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(tzname or "Australia/Melbourne")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("Australia/Melbourne")


def _num(v: float | None) -> str:
    if v is None:
        return "?"
    if v == int(v):
        return str(int(v))
    return f"{v:g}"


def render_chart_svg(case: Case, events: list[Event]) -> str:
    zi = _zone(case.timezone)
    shown = [e for e in events if e.status == EventStatus.accepted]
    meds = [e for e in shown if e.kind != EventKind.phase]
    phases = [e for e in shown if e.kind == EventKind.phase]

    t0 = case.started_at
    if t0.tzinfo is None:
        t0 = t0.replace(tzinfo=timezone.utc)
    norm = [e.timestamp.replace(tzinfo=timezone.utc) if e.timestamp.tzinfo is None
            else e.timestamp for e in shown]
    earliest = min([t0, *norm]) if norm else t0
    last = max(norm) if norm else t0

    # Anchor the axis to a clean clock boundary in local time (e.g. 19:10, not
    # 19:13), and round the right edge to a whole tick too.
    local0 = earliest.astimezone(zi)
    local0 = local0.replace(minute=local0.minute - local0.minute % TICK_MIN,
                            second=0, microsecond=0)
    origin = local0.astimezone(timezone.utc)
    span_min = max(0.0, (last - origin).total_seconds() / 60)
    plot_min = max(MIN_MINUTES, ceil(span_min / TICK_MIN) * TICK_MIN + TICK_MIN)
    plot_w = plot_min * PX_PER_MIN
    width = LEFT + plot_w + RIGHT

    def x_at_min(m: float) -> float:
        return LEFT + m * PX_PER_MIN

    def x_of(dt) -> float:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return x_at_min((dt - origin).total_seconds() / 60)

    lane_order: list[str] = []
    for e in sorted(meds, key=lambda e: e.timestamp):
        if e.drug and e.drug not in lane_order:
            lane_order.append(e.drug)

    height = TOP + max(1, len(lane_order)) * LANE_H + 20
    parts: list[str] = [
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'role="img" aria-label="Anaesthetic chart" '
        f'font-family="system-ui, sans-serif" class="anaes-chart">',
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#fff"/>',
    ]

    # Time axis: gridline + label every TICK_MIN minutes
    for i in range(0, plot_min + 1, TICK_MIN):
        gx = x_at_min(i)
        label = (origin + timedelta(minutes=i)).astimezone(zi).strftime("%H:%M")
        parts.append(
            f'<line x1="{gx:.1f}" y1="{TOP}" x2="{gx:.1f}" y2="{height - 12}" '
            f'stroke="#eee" stroke-width="1"/>')
        parts.append(
            f'<text x="{gx:.1f}" y="{TOP - 16}" font-size="11" fill="#666" '
            f'text-anchor="middle">{label}</text>')

    # Lanes
    for idx, drug in enumerate(lane_order):
        base = TOP + idx * LANE_H + LANE_H * 0.55
        parts.append(
            f'<line x1="{LEFT}" y1="{base:.1f}" x2="{width - RIGHT}" '
            f'y2="{base:.1f}" stroke="#f3f3f3" stroke-width="1"/>')
        parts.append(
            f'<text x="{LEFT - 10}" y="{base + 4:.1f}" font-size="12" '
            f'fill="#222" text-anchor="end">{escape(drug)}</text>')

        de = sorted([e for e in meds if e.drug == drug], key=lambda e: e.timestamp)
        rate_points = [e for e in de if e.kind in (
            EventKind.infusion_start, EventKind.infusion_rate_change)]
        stops = [e for e in de if e.kind == EventKind.infusion_stop]
        if rate_points:
            x_start = x_of(rate_points[0].timestamp)
            x_end = x_of(stops[-1].timestamp) if stops else (width - RIGHT)
            parts.append(
                f'<line x1="{x_start:.1f}" y1="{base:.1f}" x2="{x_end:.1f}" '
                f'y2="{base:.1f}" stroke="#2563eb" stroke-width="2.5"/>')
            for e in rate_points:
                ex = x_of(e.timestamp)
                label = f'{_num(e.rate_value)} {_abbr(e.rate_unit)}'.strip()
                parts.append(f'<circle cx="{ex:.1f}" cy="{base:.1f}" r="4" fill="#2563eb"/>')
                parts.append(
                    f'<text x="{ex:.1f}" y="{base - 9:.1f}" font-size="11" '
                    f'fill="#1d4ed8" text-anchor="middle">{escape(label)}</text>')
            for e in stops:
                ex = x_of(e.timestamp)
                parts.append(
                    f'<text x="{ex:.1f}" y="{base + 5:.1f}" font-size="13" '
                    f'fill="#b91c1c" text-anchor="middle">✕</text>')

        for e in de:
            if e.kind != EventKind.bolus:
                continue
            ex = x_of(e.timestamp)
            label = f'{_num(e.dose_value)} {_abbr(e.dose_unit)}'.strip()
            parts.append(
                f'<text x="{ex:.1f}" y="{base + 5:.1f}" font-size="13" '
                f'fill="#7c3aed" text-anchor="middle">▼</text>')
            parts.append(
                f'<text x="{ex:.1f}" y="{base - 9:.1f}" font-size="11" '
                f'fill="#6d28d9" text-anchor="middle">{escape(label)}</text>')

    for e in phases:
        ex = x_of(e.timestamp)
        parts.append(
            f'<line x1="{ex:.1f}" y1="{TOP}" x2="{ex:.1f}" y2="{height - 12}" '
            f'stroke="#0f766e" stroke-width="1.5" stroke-dasharray="4 3"/>')
        parts.append(
            f'<text x="{ex + 3:.1f}" y="{TOP + 10}" font-size="10" '
            f'fill="#0f766e">{escape(e.phase_label or "")}</text>')

    if not lane_order and not phases:
        parts.append(
            f'<text x="{width / 2}" y="{height / 2}" font-size="13" '
            f'fill="#999" text-anchor="middle">No events yet</text>')

    parts.append("</svg>")
    return "".join(parts)
