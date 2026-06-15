# Narrator

Voice-driven anaesthetic medication chart — **prototype**.

> Documentation assistance only. **Not** medication ordering or clinical
> decision support. The drug list and dose ranges are an unvalidated prototype
> safety net and must be clinically reviewed before any real use.

The LLM/parser turns messy speech into *candidate* events; deterministic code
(`validate.py` + `state.py`) decides what is auto-accepted vs held for human
confirmation. The clinician always has the final say, and every change is kept
in an immutable audit trail.

## Status — Phases 0–2

- ✅ Case setup (weight etc.), SQLite persistence, full data model + audit trail
- ✅ Drug dictionary, synonyms, allowed units, dose-range plausibility checks
- ✅ Per-case medication **state machine** (derived by replaying events)
- ✅ Deterministic **validator** (unknown drug, bad unit, inactive-infusion
  change, out-of-range dose, inferred unit, high-risk drug → confirm)
- ✅ **Text-box utterance entry** → parse → validate → timeline
- ✅ **Claude structured extraction** (`extract.py`) with forced JSON schema,
  same `Candidate` shape; **falls back to the naive parser** when offline
- ✅ Anaesthetic **chart (server-rendered SVG)**: chart left, editable list right
- ✅ Add / edit / delete events (HTMX), pending review queue
- ✅ Exports: CSV, JSON (incl. audit), printable report (browser → Save as PDF)

### Not yet (later phases)

- Phase 3: browser VAD → WebSocket → `faster-whisper` audio capture
- Phase 4: paeds weight-based flagging polish, multi-user, VPS deployment

## Configuration

| Env var | Default | Effect |
|---|---|---|
| `ANTHROPIC_API_KEY` | _(unset)_ | If set, utterances are parsed by Claude; otherwise the naive parser is used. |
| `NARRATOR_MODEL` | `claude-haiku-4-5` | Extraction model. Set to `claude-sonnet-4-6` / `claude-opus-4-8` for higher accuracy. |

The deterministic validator (`validate.py`) is the safety source of truth
regardless of which parser/model produced the candidate.

## Run

Uses [uv](https://docs.astral.sh/uv/).

```bash
uv sync                                   # create .venv + install from uv.lock
uv run uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000 — start a case, then type utterances such as:

- `noradrenaline up to 0.2 micrograms per kilo per minute`
- `10 microg adrenaline now`
- `norad down to 0.1`  (unit inferred from the running infusion → flagged)
- `stop the norad`
- `bypass on`

## Test

```bash
uv run pytest
```

## Layout

```
app/
  main.py      FastAPI routes, CRUD + audit, exports
  parse.py     naive utterance parser (Phase-1 placeholder for Claude)
  validate.py  deterministic safety validator
  state.py     per-case medication state machine
  drugs.py     drug dictionary / units / ranges  ← edit this with clinical input
  chart.py     server-rendered SVG anaesthetic chart
  models.py    SQLModel tables (Case, Event, EventRevision)
  templates/   Jinja2 + HTMX partials
static/app.css Pico.css overrides + two-pane layout
tests/         validator + state-machine unit tests
```
