"""Speech-to-text behind a swappable interface — PHASE 3.

Default backend is faster-whisper (CTranslate2) on CPU, biased toward the drug
vocabulary via hotwords. Recognition is the *async accuracy tier* (events are
already in the log with a locked timestamp before transcription returns), and the
phonetic corrector cleans drug names afterwards — so base.en is the default: fast
and cheap enough for a single-core no-AVX2 VPS. NARRATOR_WHISPER_MODEL=small.en
trades latency for accuracy on a capable box (the offline image bakes whatever
the default is, so switching it for a container means re-baking). If the model
can't load, falls back to NullASR so the app still runs. This whole class sits
behind one swappable resolver — the planned hard grammar-constrained engine drops
in here without touching callers.

Audio arrives as raw bytes (whatever the browser MediaRecorder produced, e.g.
webm/opus); faster-whisper decodes it via PyAV.
"""
from __future__ import annotations

import io
import os
import threading
from functools import lru_cache

from app.drugs import DRUGS

WHISPER_MODEL = os.environ.get("NARRATOR_WHISPER_MODEL", "base.en")
WHISPER_BEAM = int(os.environ.get("NARRATOR_WHISPER_BEAM", "5"))

# Benchmark (scripts/asr_bench.py) finding: biasing the decoder toward the drug
# names via `hotwords` beats a long vocabulary `initial_prompt`, and base.en +
# beam 5 + hotwords matched small.en for drug recall at ~3x the speed.
_PHASE_TERMS = ["bypass", "cross-clamp", "cross clamp"]


def _build_hotwords() -> str:
    drugs = [d.canonical for d in DRUGS]
    syns = [s for d in DRUGS for s in d.synonyms]
    return ", ".join(drugs + syns + _PHASE_TERMS)


HOTWORDS = _build_hotwords()

# Kept for the benchmark/optional use; the live path uses hotwords instead.
INITIAL_PROMPT = (
    "Paediatric anaesthetic medication log. Vocabulary: " + HOTWORDS + "."
)


class ASR:
    def transcribe(self, audio: bytes) -> str:  # pragma: no cover - interface
        raise NotImplementedError


class NullASR(ASR):
    """Used when no model is available; the app still runs (typed entry works)."""
    available = False

    def transcribe(self, audio: bytes) -> str:
        return ""


# Transcription is CPU-bound and the VPS is a single core. Running utterances
# concurrently (FastAPI serves the sync route from a threadpool) just thrashes the
# one core and inflates memory, so several quick orders all surface minutes later,
# together. Serialise: one transcription at a time → each order surfaces as soon
# as it's done (~Ns, 2N, 3N), and peak memory stays at one working set.
_TRANSCRIBE_LOCK = threading.Lock()


class FasterWhisperASR(ASR):
    available = True

    def __init__(self, model_size: str = WHISPER_MODEL):
        from faster_whisper import WhisperModel
        self.model = WhisperModel(model_size, device="cpu", compute_type="int8")

    def transcribe(self, audio: bytes) -> str:
        with _TRANSCRIBE_LOCK:
            segments, _info = self.model.transcribe(
                io.BytesIO(audio),
                language="en",
                hotwords=HOTWORDS,     # bias toward drug names (see asr_bench.py)
                vad_filter=True,
                beam_size=WHISPER_BEAM,
            )
            return " ".join(s.text.strip() for s in segments).strip()


@lru_cache(maxsize=1)
def get_asr() -> ASR:
    try:
        return FasterWhisperASR()
    except Exception:  # noqa: BLE001 - any load failure → graceful fallback
        return NullASR()
