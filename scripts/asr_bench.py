"""ASR tuning benchmark (dev tool, macOS).

Generates representative anaesthetic utterances with `say`, transcribes them
under several faster-whisper configs, and scores drug-term recall (the hard part
for this domain). Clean TTS audio is optimistic vs. theatre noise, but it's a
fair relative comparison of model size / beam / biasing.

    uv run python scripts/asr_bench.py
"""
from __future__ import annotations

import io
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.drugs import DRUGS, resolve_drug  # noqa: E402
from app.asr import INITIAL_PROMPT  # noqa: E402

# (utterance, expected canonical drug | None for a phase phrase + keyword)
PHRASES = [
    ("noradrenaline up to point two micrograms per kilo per minute", "noradrenaline"),
    ("ten micrograms of adrenaline now", "adrenaline"),
    ("propofol fifteen milligrams per kilo per hour", "propofol"),
    ("give twenty of rocuronium", "rocuronium"),
    ("metaraminol point five milligrams", "metaraminol"),
    ("milrinone point five micrograms per kilo per minute", "milrinone"),
    ("fentanyl fifty micrograms", "fentanyl"),
    ("dobutamine five micrograms per kilo per minute", "dobutamine"),
    ("sugammadex two hundred milligrams", "sugammadex"),
    ("glycopyrrolate two hundred micrograms", "glycopyrrolate"),
    ("phenylephrine fifty micrograms", "phenylephrine"),
    ("dexamethasone is not on the list start the noradrenaline", "noradrenaline"),
]

HOTWORDS = ", ".join([d.canonical for d in DRUGS]
                     + [s for d in DRUGS for s in d.synonyms])

CONFIGS = [
    {"label": "base.en beam1 hotwords", "model": "base.en", "beam": 1, "hotwords": HOTWORDS},
    {"label": "base.en beam5 hotwords", "model": "base.en", "beam": 5, "hotwords": HOTWORDS},
    {"label": "small.en beam5 hotwords", "model": "small.en", "beam": 5, "hotwords": HOTWORDS},
    {"label": "small.en beam5 prompt+hot", "model": "small.en", "beam": 5,
     "prompt": INITIAL_PROMPT, "hotwords": HOTWORDS},
]

CACHE = Path("/tmp/narrator_asr_bench")


def gen_audio() -> list[bytes]:
    CACHE.mkdir(exist_ok=True)
    out = []
    for i, (text, _) in enumerate(PHRASES):
        f = CACHE / f"p{i}.aiff"
        if not f.exists():
            subprocess.run(["say", "-o", str(f), text], check=True)
        out.append(f.read_bytes())
    return out


def drug_hit(transcript: str, expected: str) -> bool:
    low = transcript.lower()
    if expected in low:
        return True
    # also accept any token that resolves to the expected canonical drug
    import re
    for tok in re.findall(r"[a-z]+", low):
        if resolve_drug(tok) == expected:
            return True
    return False


def run():
    from faster_whisper import WhisperModel
    audios = gen_audio()
    models: dict[str, WhisperModel] = {}
    print(f"{len(PHRASES)} phrases\n")
    for cfg in CONFIGS:
        m = cfg["model"]
        if m not in models:
            models[m] = WhisperModel(m, device="cpu", compute_type="int8")
        model = models[m]
        hits, total, misses = 0, 0, []
        t0 = time.perf_counter()
        for (text, expected), audio in zip(PHRASES, audios):
            kw = {"language": "en", "beam_size": cfg["beam"]}
            if cfg.get("prompt"):
                kw["initial_prompt"] = cfg["prompt"]
            if cfg.get("hotwords"):
                kw["hotwords"] = cfg["hotwords"]
            segs, _ = model.transcribe(io.BytesIO(audio), **kw)
            tr = " ".join(s.text.strip() for s in segs).strip()
            total += 1
            ok = drug_hit(tr, expected) if expected else (
                "bypass" in tr.lower() or "clamp" in tr.lower())
            hits += 1 if ok else 0
            if not ok:
                misses.append(f"      {expected or 'phase'}: {tr!r}")
        dt = time.perf_counter() - t0
        print(f"{cfg['label']:26} {hits}/{total} drug-recall  ({dt/total:.2f}s/clip)")
        for mline in misses:
            print(mline)
    print()


if __name__ == "__main__":
    run()
