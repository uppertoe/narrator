"""Speech-to-text behind a swappable interface — PHASE 3.

Default backend is faster-whisper (CTranslate2) on CPU, biased toward the drug
vocabulary via an initial prompt. The model is chosen with NARRATOR_WHISPER_MODEL
(default base.en — small enough for a cheap CPU VPS; use small.en for accuracy).
If the model can't load, falls back to a NullASR so the app still runs.

Audio arrives as raw bytes (whatever the browser MediaRecorder produced, e.g.
webm/opus); faster-whisper decodes it via PyAV.
"""
from __future__ import annotations

import io
import os
from functools import lru_cache

from app.drugs import DRUGS

WHISPER_MODEL = os.environ.get("NARRATOR_WHISPER_MODEL", "base.en")

_UNIT_WORDS = [
    "micrograms", "microgram", "milligrams", "milligram", "millilitres",
    "units", "millimoles", "per kilo per minute", "micrograms per kilo per minute",
    "milligrams per kilo per hour", "per hour", "per minute",
]
_COMMAND_WORDS = [
    "bolus", "infusion", "up to", "down to", "started", "stopped", "stop",
    "given", "now", "bypass on", "bypass off", "cross clamp", "point",
]


def _build_prompt() -> str:
    drugs = [d.canonical for d in DRUGS]
    syns = [s for d in DRUGS for s in d.synonyms]
    terms = ", ".join(drugs + syns + _UNIT_WORDS + _COMMAND_WORDS)
    return f"Paediatric anaesthetic medication log. Vocabulary: {terms}."


INITIAL_PROMPT = _build_prompt()


class ASR:
    def transcribe(self, audio: bytes) -> str:  # pragma: no cover - interface
        raise NotImplementedError


class NullASR(ASR):
    """Used when no model is available; the app still runs (typed entry works)."""
    available = False

    def transcribe(self, audio: bytes) -> str:
        return ""


class FasterWhisperASR(ASR):
    available = True

    def __init__(self, model_size: str = WHISPER_MODEL):
        from faster_whisper import WhisperModel
        self.model = WhisperModel(model_size, device="cpu", compute_type="int8")

    def transcribe(self, audio: bytes) -> str:
        segments, _info = self.model.transcribe(
            io.BytesIO(audio),
            language="en",
            initial_prompt=INITIAL_PROMPT,
            vad_filter=True,
            beam_size=1,
        )
        return " ".join(s.text.strip() for s in segments).strip()


@lru_cache(maxsize=1)
def get_asr() -> ASR:
    try:
        return FasterWhisperASR()
    except Exception:  # noqa: BLE001 - any load failure → graceful fallback
        return NullASR()
