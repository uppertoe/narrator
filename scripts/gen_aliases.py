"""Generate the phonetic alias map by round-tripping drug names through TTS→ASR.

Why: hand-guessing how Moonshine mishears Australian-accented drug names doesn't
scale. Instead, speak each drug name with macOS Australian voices and transcribe
it with the *actual* on-device model the app uses
(onnx-community/moonshine-tiny-ONNX, wasm). Wherever the model mishears, record
mishear -> canonical. The result is model- and accent-specific, reproducible, and
reviewable as a plain diff.

How it runs the real model: Moonshine runs in-browser via transformers.js, so we
drive it through the app's own ``static/asr-local.js`` in a headless browser
(Playwright) — the exact inference path production uses. A tiny throwaway page
(``static/_gen.html``) just imports that module and exposes a decode+transcribe
helper.

Prereqs: the dev server must be serving /static on 127.0.0.1:8055 (so the page
and the vendored model load same-origin), macOS ``say`` with an en_AU voice, and
Playwright chromium (``uv run playwright install chromium``).

    uv run python scripts/gen_aliases.py

Writes app/alias_generated.py (consumed by app/correct.py; manual aliases win).
"""
from __future__ import annotations

import base64
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import jellyfish  # noqa: E402

from app.correct import MANUAL_ALIASES, STOPWORDS, _norm  # noqa: E402
from app.drugs import DRUGS  # noqa: E402

_SYNONYMS = {d.canonical: list(d.synonyms) for d in DRUGS}

BASE_URL = "http://127.0.0.1:8055"
GEN_PAGE = ROOT / "static" / "_gen.html"
OUT_FILE = ROOT / "app" / "alias_generated.py"

# Prosody sweep per drug: (words-per-minute, pitch-baseline). Varying speed AND
# pitch widens acoustic coverage so we catch more of the mishear distribution
# (rushed vs. deliberate, higher vs. lower voice). pbas=None uses the voice
# default. Voices are discovered at runtime (any en_AU); Karen is the fallback —
# install Lee/Matilda via System Settings ▸ Spoken Content for more variety.
PROSODY = (
    (175, None),   # natural baseline
    (150, 40),     # slow + low pitch
    (150, 70),     # slow + high pitch
    (220, 40),     # fast + low pitch
    (220, 70),     # fast + high pitch
    (255, 55),     # very fast + mid pitch (rushed dictation)
)
MIN_SYNONYM_LEN = 4  # skip 2–3 char abbreviations ("nor", "roc") — too noisy

GEN_PAGE_HTML = """<!doctype html>
<meta charset="utf-8">
<title>alias generator</title>
<body>
<script type="module">
  import "/static/asr-local.js";
  window.__warmup = () => window.NarratorLocalASR.warmup();
  // Decode a base64 WAV to 16 kHz mono Float32 and transcribe with Moonshine.
  window.__transcribe = async (b64) => {
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    const ctx = new OfflineAudioContext(1, 16000, 16000);
    const audio = await ctx.decodeAudioData(bytes.buffer);
    return await window.NarratorLocalASR.transcribe(audio.getChannelData(0));
  };
</script>
</body>
"""


def au_voices() -> list[str]:
    """Installed Australian (en_AU) macOS voices, or ['Karen'] as a fallback."""
    try:
        out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, check=True).stdout
    except Exception:  # noqa: BLE001
        return ["Karen"]
    voices = [line.split()[0] for line in out.splitlines() if "en_AU" in line]
    return voices or ["Karen"]


def phrases() -> list[tuple[str, str]]:
    """(spoken_text, canonical_drug) for each canonical name + longer synonyms."""
    items: list[tuple[str, str]] = []
    for d in DRUGS:
        spoken = [d.canonical] + [s for s in d.synonyms if len(s) >= MIN_SYNONYM_LEN]
        for s in dict.fromkeys(spoken):  # dedupe, keep order
            items.append((s, d.canonical))
    return items


def say_wav(text: str, voice: str, rate: int, pitch: int | None, path: Path) -> None:
    spoken = f"[[pbas {pitch}]] {text}" if pitch is not None else text
    subprocess.run(
        ["say", "-v", voice, "-r", str(rate),
         "--file-format=WAVE", "--data-format=LEI16@16000",
         "-o", str(path), spoken],
        check=True, capture_output=True,
    )


# Phonetic-plausibility gate. A single bare word spoken with no context makes the
# model emit its nearest *common English word* ("sent", "doug", "like"), which we
# must never alias to a drug. Two defences: (1) require the mishear to actually
# SOUND like the drug (combined Metaphone/Jaro–Winkler ≥ COMBINED, with a raw-JW
# floor to kill Metaphone-only collisions like "final"/phenylephrine); and (2)
# keep MULTI-WORD mishears only — those are the model fragmenting a long name
# ("nor a drain line"), which the single-token phonetic layer in correct.py can't
# reassemble. Genuine single-token mishears that truly sound like a drug are left
# to that phonetic layer; the dangerous common-word collisions all live there too.
_COMBINED = 0.80
_RAW_FLOOR = 0.62


def phonetic_ok(key: str, canonical: str) -> bool:
    k = key.replace(" ", "")
    raw = combined = 0.0
    for cand in [canonical, *_SYNONYMS.get(canonical, [])]:
        c = _norm(cand).replace(" ", "")
        if not c:
            continue
        r = jellyfish.jaro_winkler_similarity(k, c)
        raw = max(raw, r)
        combined = max(combined, r)
        mk, mc = jellyfish.metaphone(k), jellyfish.metaphone(c)
        if mk and mc:
            combined = max(combined, jellyfish.jaro_winkler_similarity(mk, mc))
    return combined >= _COMBINED and raw >= _RAW_FLOOR


def keep_alias(key: str, canonical: str) -> bool:
    """A mishear becomes a generated alias only if it's a safe, plausible match."""
    if not key or len(key) < 4 or any(ch.isdigit() for ch in key):
        return False
    if " " not in key:                          # single tokens → phonetic layer's job
        return False
    if all(w in STOPWORDS for w in key.split()):
        return False
    if key == _norm(canonical):                 # heard correctly
        return False
    return phonetic_ok(key, canonical)


def main() -> int:
    voices = au_voices()
    plan = phrases()
    print(f"voices: {', '.join(voices)}   prosody variants: {len(PROSODY)}")
    print(f"phrases: {len(plan)}   total clips: {len(plan) * len(voices) * len(PROSODY)}\n")

    canon_set = {_norm(d.canonical) for d in DRUGS}
    # key -> {canonical -> count}; lets us spot ambiguous mishears.
    hits: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    GEN_PAGE.write_text(GEN_PAGE_HTML)
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{BASE_URL}/static/_gen.html", wait_until="load")
            page.wait_for_function("() => window.NarratorLocalASR && window.NarratorLocalASR.available", timeout=30000)
            print("warming up model…")
            ok = page.evaluate("async () => await window.__warmup()")
            if not ok:
                print("ERROR: model warmup failed", *errors, sep="\n")
                return 1
            backend = page.evaluate("() => window.NarratorLocalASR.backend")
            print(f"model ready (backend: {backend})\n")

            tmp = Path(tempfile.mkdtemp())
            for idx, (text, canonical) in enumerate(plan, 1):
                seen: set[str] = set()
                for voice in voices:
                    for rate, pitch in PROSODY:
                        wav = tmp / "clip.wav"
                        say_wav(text, voice, rate, pitch, wav)
                        b64 = base64.b64encode(wav.read_bytes()).decode()
                        raw = page.evaluate("async (b) => await window.__transcribe(b)", b64) or ""
                        key = _norm(raw)
                        if keep_alias(key, canonical):
                            hits[key][canonical] += 1
                            seen.add(key)
                tag = (" → " + ", ".join(sorted(seen))) if seen else " (clean)"
                print(f"[{idx:>3}/{len(plan)}] {text:<20} ({canonical}){tag}")
            browser.close()
    finally:
        GEN_PAGE.unlink(missing_ok=True)

    # Resolve to a clean map: drop ambiguous keys (heard for >1 drug) and any key
    # that is itself a real drug name. Manual aliases override later, in correct.py.
    generated: dict[str, tuple[str, int]] = {}
    dropped_ambiguous: list[str] = []
    for key, byc in hits.items():
        if len(byc) > 1:
            dropped_ambiguous.append(f"{key!r} → {dict(byc)}")
            continue
        canonical, count = next(iter(byc.items()))
        if key in canon_set and key != _norm(canonical):
            continue
        generated[key] = (canonical, count)

    write_output(generated, voices)
    print(f"\nwrote {len(generated)} aliases → {OUT_FILE.relative_to(ROOT)}")
    overlap = sorted(set(generated) & set(MANUAL_ALIASES))
    if overlap:
        print(f"  ({len(overlap)} also hand-curated; manual wins): {', '.join(overlap)}")
    if dropped_ambiguous:
        print(f"  dropped {len(dropped_ambiguous)} ambiguous mishears:")
        for line in dropped_ambiguous:
            print(f"    {line}")
    return 0


def write_output(generated: dict[str, tuple[str, int]], voices: list[str]) -> None:
    lines = [
        '"""Auto-generated alias map — DO NOT EDIT BY HAND.',
        "",
        "Produced by scripts/gen_aliases.py: each canonical drug name is spoken by",
        "Australian macOS voices and transcribed by the *actual* on-device model",
        "(onnx-community/moonshine-tiny-ONNX). Mishears are recorded here so the",
        "server can map them back to canonical drug names before parsing.",
        "",
        f"Voices: {', '.join(voices)}. Re-generate with:",
        "",
        "    uv run python scripts/gen_aliases.py",
        "",
        "Hand-curated overrides live in app.correct.MANUAL_ALIASES and win on conflict.",
        '"""',
        "from __future__ import annotations",
        "",
        "# normalised spoken mishear -> canonical drug  (count = times the model produced it)",
        "GENERATED_ALIASES: dict[str, str] = {",
    ]
    for key in sorted(generated, key=lambda k: (generated[k][0], k)):
        canonical, count = generated[key]
        lines.append(f"    {key!r}: {canonical!r},  # ×{count}")
    lines.append("}")
    OUT_FILE.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
