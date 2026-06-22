"""TTS -> ASR generator for DOSE-phrase mishears.

Discovers how the production ASR (faster-whisper base.en + hotwords) actually
mangles the spoken dose vocabulary — magnitude units, per-kilo, per-time — so the
planned dose corrector is built from real data, not guesses. (Case 1 showed
"per kilo" -> "peculiar" and "mic" -> "mite"; this finds the full set.)

    uv run python scripts/gen_dose_aliases.py

Runs locally: faster-whisper is fast on this Mac, and the mishears are
model-specific (base.en) — which is what matters, not the hardware. Voices are AU
(macOS `say`); install Lee/Matilda for more variety.
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.asr import WHISPER_BEAM, WHISPER_MODEL, get_asr  # noqa: E402

# (words-per-minute, pitch-baseline) — vary prosody to surface more mishears.
PROSODY = [(175, None), (150, 40), (150, 70), (225, 55), (250, None)]

# Spoken dose components in isolation — the cleanest signal of each one's mishear.
COMPONENTS = [
    "micrograms", "microgram", "mics", "mic", "mcg",
    "milligrams", "milligram", "units", "unit", "millimoles", "millilitres",
    "per kilo", "per kilogram", "per kig",
    "per minute", "a minute", "per hour", "an hour",
    "per kilo per minute", "per kilo per hour",
    "mics per kilo", "mics per kilo per minute",
    "micrograms per kilo per minute", "milligrams per kilo per hour",
]

# Realistic full orders (drug + number + unit phrase) — catches contextual mangling
# and ordering, mirroring the case-1 failures.
CARRIERS = [
    "adrenaline one mic per kilo",
    "propofol twenty mics per kilo",
    "adrenaline ten mics per kilo per minute",
    "noradrenaline nought point one mics per kilo per minute",
    "propofol two milligrams per kilo",
    "thirty mics adrenaline",
    "dopamine five mics per kilo per minute",
    "fentanyl fifty mics",
]


def au_voices() -> list[str]:
    try:
        out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, check=True).stdout
    except Exception:  # noqa: BLE001
        return ["Karen"]
    return [ln.split()[0] for ln in out.splitlines() if "en_AU" in ln] or ["Karen"]


def say_wav(text: str, voice: str, rate: int, pitch: int | None, path: Path) -> None:
    spoken = f"[[pbas {pitch}]] {text}" if pitch is not None else text
    subprocess.run(["say", "-v", voice, "-r", str(rate),
                    "--file-format=WAVE", "--data-format=LEI16@16000",
                    "-o", str(path), spoken], check=True, capture_output=True)


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", s.lower()).strip()


def main() -> int:
    voices = au_voices()
    asr = get_asr()
    tmp = Path(tempfile.mkdtemp())
    print(f"ASR: {WHISPER_MODEL} beam {WHISPER_BEAM} | voices: {', '.join(voices)} "
          f"| prosody: {len(PROSODY)}\n")

    def heard_for(phrase: str) -> Counter:
        c: Counter = Counter()
        for v in voices:
            for rate, pitch in PROSODY:
                w = tmp / "c.wav"
                say_wav(phrase, v, rate, pitch, w)
                c[norm(asr.transcribe(w.read_bytes()))] += 1
        return c

    suggestions: dict[str, str] = {}
    print("=== COMPONENTS: said -> heard (counts) ===")
    for said in COMPONENTS:
        c = heard_for(said)
        print(f"  {said!r:34} -> " + ", ".join(f"{h!r}×{n}" for h, n in c.most_common()))
        for h, _ in c.items():
            if h and h != norm(said) and h not in suggestions:
                suggestions[h] = said

    print("\n=== CARRIERS: said -> heard ===")
    for said in CARRIERS:
        c = heard_for(said)
        print(f"  {said!r}")
        for h, n in c.most_common(3):
            print(f"        {h!r} ×{n}")

    print("\n=== candidate dose mishears (heard -> canonical) for review ===")
    for h in sorted(suggestions):
        print(f"  {h!r:34} -> {suggestions[h]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
