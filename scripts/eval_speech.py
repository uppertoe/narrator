"""Synthetic end-to-end eval for the voice pipeline, focused on the hard cases:
run-on sentences and commands close together in time.

Pipeline under test (the real one):
    TTS clip -> vad-web NonRealTimeVAD (production params) -> snippets
             -> faster-whisper -> phonetic corrector -> number normaliser
             -> parser  =>  events

For each scenario we report how many snippets the VAD produced and which expected
commands were recovered. A gap-sensitivity sweep shows where two adjacent
commands start splitting into separate snippets (i.e. separate timestamps).

Honest caveat: macOS `say` is clean speech with no theatre noise, so absolute
numbers are optimistic — this is for RELATIVE comparison, regression, and seeing
structural failures (e.g. run-on snippets yielding only one event).

    uv run python scripts/eval_speech.py        # needs the dev server on :8055

Reuses the running server only to serve /static/vad to a headless browser.
"""
from __future__ import annotations

import base64
import io
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.asr import get_asr  # noqa: E402
from app.correct import correct_transcript  # noqa: E402
from app.extract import extract_candidates  # noqa: E402
from app.numbers import normalize_numbers  # noqa: E402
from app.state import build_state  # noqa: E402

BASE_URL = "http://127.0.0.1:8055"
PAGE = ROOT / "static" / "_vadtest.html"
WEIGHT_KG = 12.0
RATE = 16000

# Production VAD params (keep in sync with static/audio.js).
VAD_OPTS = {
    "baseAssetPath": "/static/vad/",
    "onnxWASMBasePath": "/static/vad/",
    "positiveSpeechThreshold": 0.6,
    "negativeSpeechThreshold": 0.45,
    "minSpeechFrames": 3,
    "redemptionFrames": 5,
    "preSpeechPadFrames": 3,
}

# Each command: (spoken, canonical_drug, value). value matches dose OR rate.
SCENARIOS = [
    ("single bolus", "single", [("propofol twenty milligrams", "propofol", 20)]),
    ("single rate", "single", [("noradrenaline up to point one", "noradrenaline", 0.1)]),
    ("single stop", "single", [("stop the adrenaline", "adrenaline", None)]),
    ("run-on, 2 drugs", "runon",
     [("propofol twenty rocuronium fifty", "propofol", 20), (None, "rocuronium", 50)]),
    ("run-on, 3 drugs", "runon",
     [("fentanyl fifty propofol twenty rocuronium fifty", "fentanyl", 50),
      (None, "propofol", 20), (None, "rocuronium", 50)]),
    ("spaced 600ms", "spaced:600",
     [("propofol twenty", "propofol", 20), ("rocuronium fifty", "rocuronium", 50)]),
]

PAGE_HTML = """<!doctype html><meta charset="utf-8"><body>
<script src="/static/vad/ort.wasm.min.js"></script>
<script src="/static/vad/bundle.min.js"></script>
<script>
  ort.env.wasm.wasmPaths = "/static/vad/";
  ort.env.wasm.numThreads = 1;
  function f32ToWavB64(f32){
    const n=f32.length, dv=new DataView(new ArrayBuffer(44+n*2));
    const s=(o,t)=>{for(let i=0;i<t.length;i++)dv.setUint8(o+i,t.charCodeAt(i));};
    s(0,"RIFF");dv.setUint32(4,36+n*2,true);s(8,"WAVE");s(12,"fmt ");
    dv.setUint32(16,16,true);dv.setUint16(20,1,true);dv.setUint16(22,1,true);
    dv.setUint32(24,16000,true);dv.setUint32(28,32000,true);dv.setUint16(32,2,true);
    dv.setUint16(34,16,true);s(36,"data");dv.setUint32(40,n*2,true);
    let o=44;for(let i=0;i<n;i++){const x=Math.max(-1,Math.min(1,f32[i]));dv.setInt16(o,x<0?x*0x8000:x*0x7fff,true);o+=2;}
    let bin="";const b=new Uint8Array(dv.buffer);for(let i=0;i<b.length;i++)bin+=String.fromCharCode(b[i]);
    return btoa(bin);
  }
  async function decode(b64){
    const bin=atob(b64),buf=new Uint8Array(bin.length);
    for(let i=0;i<bin.length;i++)buf[i]=bin.charCodeAt(i);
    const ctx=new OfflineAudioContext(1,1,16000);
    const a=await ctx.decodeAudioData(buf.buffer);return a.getChannelData(0);
  }
  window.__segment = async (clipB64, opts) => {
    const f32 = await decode(clipB64);
    const v = await vad.NonRealTimeVAD.new(opts);
    const out = [];
    for await (const s of v.run(f32, 16000)) out.push({startMs:s.start, endMs:s.end, wav:f32ToWavB64(s.audio)});
    return out;
  };
  window.__ready = true;
</script></body>"""


def say_wav(text: str, path: Path, rate: int = 190) -> None:
    subprocess.run(["say", "-v", "Karen", "-r", str(rate),
                    "--file-format=WAVE", "--data-format=LEI16@16000",
                    "-o", str(path), text], check=True, capture_output=True)


def _pcm(path: Path) -> bytes:
    with wave.open(str(path)) as w:
        return w.readframes(w.getnframes())


def _wav_bytes(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE)
        w.writeframes(pcm)
    return buf.getvalue()


def make_clip(commands: list[str], gap_ms: int, tmp: Path) -> bytes:
    """One run-on say() if gap_ms<0, else per-command clips joined by silence."""
    if gap_ms < 0:  # run-on: a single utterance
        p = tmp / "ro.wav"; say_wav(" ".join(commands), p)
        return _wav_bytes(_pcm(p))
    silence = b"\x00\x00" * int(RATE * gap_ms / 1000)
    parts = []
    for i, c in enumerate(commands):
        p = tmp / f"c{i}.wav"; say_wav(c, p)
        parts.append(_pcm(p))
    return _wav_bytes((silence).join(parts))


def run_pipeline(wav: bytes) -> list:
    text = normalize_numbers(correct_transcript(get_asr().transcribe(wav)))
    return extract_candidates(text, build_state([]), WEIGHT_KG), text


def matches(cands, drug, value) -> bool:
    for c in cands:
        if c.drug != drug:
            continue
        if value is None:
            return True
        got = c.dose_value if c.dose_value is not None else c.rate_value
        if got is not None and abs(got - value) < max(0.01, value * 0.02):
            return True
    return False


def main() -> int:
    from playwright.sync_api import sync_playwright
    PAGE.write_text(PAGE_HTML)
    tmp = Path(tempfile.mkdtemp())
    try:
        with sync_playwright() as p:
            br = p.chromium.launch(); pg = br.new_page()
            pg.goto(f"{BASE_URL}/static/_vadtest.html", wait_until="load")
            pg.wait_for_function("() => window.__ready === true", timeout=20000)

            def segment(wav: bytes, opts=VAD_OPTS):
                b64 = base64.b64encode(wav).decode()
                return pg.evaluate("async ([b,o]) => await window.__segment(b,o)", [b64, opts])

            print(f"VAD: redemptionFrames={VAD_OPTS['redemptionFrames']} "
                  f"minSpeechFrames={VAD_OPTS['minSpeechFrames']} | ASR: base.en\n")

            for name, kind, cmds in SCENARIOS:
                texts = [c[0] for c in cmds if c[0]]
                gap = -1 if kind == "runon" else (int(kind.split(":")[1]) if ":" in kind else 0)
                segs = segment(make_clip(texts, gap, tmp))
                events = []
                heard = []
                for s in segs:
                    cands, text = run_pipeline(base64.b64decode(s["wav"]))
                    events += cands; heard.append(text)
                print(f"● {name}")
                print(f"    VAD snippets: {len(segs)} | events: {len(events)} | heard: {heard}")
                for _, drug, val in cmds:
                    ok = matches(events, drug, val)
                    print(f"    {'✓' if ok else '✗ MISSED'} {drug} {val if val is not None else ''}")
                print()

            # gap sensitivity: where do two adjacent commands split apart?
            print("Gap sensitivity — 'propofol twenty' | 'rocuronium fifty':")
            for gap in (150, 300, 450, 600, 900, 1200):
                clip = make_clip(["propofol twenty", "rocuronium fifty"], gap, tmp)
                n = len(segment(clip))
                print(f"    gap {gap:>4}ms -> {n} snippet(s)")
            br.close()
    finally:
        PAGE.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
