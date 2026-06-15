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

# Manual timestamped events (minute precision) to exercise label clustering.
DAY = "2026-06-15"
SEED_EVENTS = [
    ("infusion_start", "noradrenaline", "10:00", None, None, 0.1, "microgram/kg/min"),
    ("infusion_rate_change", "noradrenaline", "10:08", None, None, 0.2, "microgram/kg/min"),
    ("infusion_rate_change", "noradrenaline", "10:09", None, None, 0.1, "microgram/kg/min"),
    ("bolus", "adrenaline", "10:00", 10, "microgram", None, None),
    ("bolus", "adrenaline", "10:01", 20, "microgram", None, None),
    ("bolus", "adrenaline", "10:02", 10, "microgram", None, None),
    ("infusion_start", "propofol", "10:00", None, None, 15, "mg/kg/hr"),
    ("infusion_rate_change", "propofol", "10:05", None, None, 12, "mg/kg/hr"),
    ("infusion_rate_change", "propofol", "10:06", None, None, 10, "mg/kg/hr"),
    ("bolus", "metaraminol", "10:03", 0.5, "milligram", None, None),
    ("bolus", "metaraminol", "10:04", 0.5, "milligram", None, None),
    ("phase", None, "10:05", None, None, None, None),
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
    for kind, drug, hm, dv, du, rv, ru in SEED_EVENTS:
        c.post(f"/case/{cid}/events", data={
            "kind": kind, "timestamp": f"{DAY}T{hm}", "drug": drug or "",
            "dose_value": dv if dv is not None else "", "dose_unit": du or "",
            "rate_value": rv if rv is not None else "", "rate_unit": ru or "",
            "phase_label": "Bypass on" if kind == "phase" else "",
        })
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
