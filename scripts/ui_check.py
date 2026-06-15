"""UI/UX audit via Playwright — desktop + mobile screenshots + overflow report.

    uv run python scripts/ui_check.py
Screenshots land in /tmp/narrator_ui/. Requires the app running on :8055.
"""
from __future__ import annotations

import pathlib

import httpx
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8055"
OUT = pathlib.Path("/tmp/narrator_ui")
OUT.mkdir(exist_ok=True)

SEED = [
    "start noradrenaline at 0.1 micrograms per kilo per minute",
    "10 micrograms of adrenaline now",
    "propofol 15 mg per kilo per hour",
    "propofol 10",                 # kind-guessed → flip control
    "metaraminol 0.5 milligrams",
    "bypass on",
    "adrenaline 0.5",              # ambiguous unit → pending unit-choice card
]

AUDIT_JS = """() => {
  const vw = window.innerWidth;
  const offenders = [];
  document.querySelectorAll('body *').forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.right > vw + 1) {
      let p = el.parentElement, scroller = false;
      while (p) {
        const ov = getComputedStyle(p).overflowX;
        if (ov === 'auto' || ov === 'scroll') { scroller = true; break; }
        p = p.parentElement;
      }
      if (!scroller) offenders.push(
        (el.tagName.toLowerCase()) + (el.className ? '.' + String(el.className).split(' ').join('.') : '')
        + ' →' + Math.round(r.right));
    }
  });
  return {vw, docW: document.documentElement.scrollWidth,
          overflow: document.documentElement.scrollWidth > vw + 1,
          offenders: [...new Set(offenders)].slice(0, 12)};
}"""


def seed() -> str:
    c = httpx.Client(base_url=BASE, timeout=10)
    cid = c.post("/cases", data={"weight_kg": "18.4", "patient_label": "UI demo",
                                 "timezone": "Australia/Melbourne"},
                 follow_redirects=False).headers["location"].split("/")[-1]
    for t in SEED:
        c.post(f"/case/{cid}/utterance", data={"text": t})
    return cid


def run():
    cid = seed()
    print(f"seeded case {cid}\n")
    with sync_playwright() as p:
        chromium = p.chromium.launch()
        targets = [("index", "/"), ("case", f"/case/{cid}"), ("report", f"/case/{cid}/report")]
        for label, dev in [("desktop", {"viewport": {"width": 1366, "height": 900}}),
                           ("mobile", p.devices["iPhone 12"])]:
            ctx = chromium.new_context(**dev)
            page = ctx.new_page()
            for name, path in targets:
                page.goto(BASE + path, wait_until="networkidle")
                page.wait_for_timeout(400)
                shot = OUT / f"{name}_{label}.png"
                page.screenshot(path=str(shot), full_page=True)
                a = page.evaluate(AUDIT_JS)
                flag = "OVERFLOW" if a["overflow"] else "ok"
                print(f"{name:7} {label:7} {flag:9} vw={a['vw']} docW={a['docW']}")
                for o in a["offenders"]:
                    print(f"        ⤷ {o}")
            ctx.close()
        chromium.close()
    print(f"\nscreenshots in {OUT}")


if __name__ == "__main__":
    run()
