"""Fast VAD-only probe: does NonRealTimeVAD split two commands across a gap?"""
from __future__ import annotations
import base64, io, subprocess, sys, tempfile, wave
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "static" / "_vadtest.html"; RATE = 16000
sys.path.insert(0, str(ROOT))
from scripts.eval_speech import PAGE_HTML, say_wav, _pcm, _wav_bytes  # reuse


def clip(cmds, gap_ms, tmp):
    sil = b"\x00\x00" * int(RATE * gap_ms / 1000)
    parts = []
    for i, c in enumerate(cmds):
        p = tmp / f"c{i}.wav"; say_wav(c, p); parts.append(_pcm(p))
    return _wav_bytes(sil.join(parts))


def main():
    from playwright.sync_api import sync_playwright
    PAGE.write_text(PAGE_HTML); tmp = Path(tempfile.mkdtemp())
    try:
        with sync_playwright() as p:
            br = p.chromium.launch(); pg = br.new_page()
            pg.goto("http://127.0.0.1:8055/static/_vadtest.html", wait_until="load")
            pg.wait_for_function("() => window.__ready === true", timeout=20000)
            for redemption in (3, 5, 8):
                opts = {"baseAssetPath": "/static/vad/", "onnxWASMBasePath": "/static/vad/",
                        "positiveSpeechThreshold": 0.6, "negativeSpeechThreshold": 0.45,
                        "minSpeechFrames": 3, "redemptionFrames": redemption, "preSpeechPadFrames": 3}
                print(f"\nredemptionFrames={redemption}:")
                for gap in (0, 600, 1200, 2500):
                    b64 = base64.b64encode(clip(["propofol twenty", "rocuronium fifty"], gap, tmp)).decode()
                    segs = pg.evaluate("async ([b,o]) => await window.__segment(b,o)", [b64, opts])
                    bounds = ", ".join(f"{int(s['startMs'])}-{int(s['endMs'])}ms" for s in segs)
                    print(f"   gap {gap:>4}ms -> {len(segs)} seg(s): {bounds}")
            br.close()
    finally:
        PAGE.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
