# syntax=docker/dockerfile:1

# --- build stage: install deps with uv + bake the ASR model into the image ---
FROM python:3.13-slim AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy HF_HOME=/opt/models
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app

# Production deps only (faster-whisper, fastapi, uvicorn, …); cached on lockfile.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY app ./app
COPY static ./static

# Pre-download the default server Whisper model so the runtime needs no network
# and can run on a read-only filesystem. Call the venv python directly — `uv run`
# would re-sync and pull dev deps (e.g. playwright, ~136MB) back into the image.
RUN .venv/bin/python -c "from faster_whisper import WhisperModel; WhisperModel('base.en', device='cpu', compute_type='int8')"

# (On-device/transformers.js model vendoring removed: recognition is now fully
# server-side, so no in-browser model is shipped.)

# --- runtime stage ---
FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH="/app/.venv/bin:$PATH" \
    HF_HOME=/opt/models \
    HF_HUB_OFFLINE=1 \
    NARRATOR_DATABASE_URL=sqlite:////data/narrator.db \
    NARRATOR_WHISPER_MODEL=base.en
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY --from=build /opt/models /opt/models
COPY app ./app
# static = CSS/JS + vendored VAD assets (no ASR model; recognition is server-side).
COPY --from=build /app/static ./static

# Pre-create the data dir owned by the nonroot uid. An empty named volume mounted
# here inherits this ownership, so the SQLite file is writable without root.
RUN mkdir -p /data && chown 65532:65532 /data

USER 65532:65532
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --retries=3 --start-period=25s \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
