"""Screenshot the two-tier capture flow states (desktop + mobile)."""
from __future__ import annotations
import pathlib, httpx
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8055"
OUT = pathlib.Path("/tmp/narrator_flow"); OUT.mkdir(exist_ok=True)

c = httpx.Client(base_url=BASE, follow_redirects=False)
loc = c.post("/cases", data={"label": "Flow demo", "weight_kg": "12",
                             "timezone": "Australia/Melbourne"}).headers["location"]
cid = loc.rstrip("/").split("/")[-1]

# a couple of clean accepted events (chart + list content)
c.post(f"/case/{cid}/utterance", data={"text": "noradrenaline up to 0.1 mcg/kg/min", "source": "typed"})
c.post(f"/case/{cid}/utterance", data={"text": "10 microg adrenaline now", "source": "typed"})
# a flagged row that surfaces "what we heard" (unrecognised) — editable in-line
c.post(f"/case/{cid}/utterance", data={"text": "give some of that thing", "source": "asr"})
# a live provisional placeholder (left unresolved → shows "transcribing…")
c.post(f"/case/{cid}/utterance/provisional", data={})

with sync_playwright() as p:
    br = p.chromium.launch()
    for name, w, h in [("desktop", 1280, 900), ("mobile", 390, 844)]:
        pg = br.new_page(viewport={"width": w, "height": h})
        pg.goto(f"{BASE}/case/{cid}", wait_until="networkidle")
        pg.screenshot(path=str(OUT / f"{name}_timeline.png"), full_page=True)
        # open an inline edit on the flagged row (the one with a ⚠ / source quote)
        edit = pg.locator("article.ev-pending a[title='Edit'], article.ev-pending a[title='Enter by hand']").first
        if edit.count():
            edit.click()
            pg.wait_for_selector("form.row-edit", timeout=3000)
            pg.screenshot(path=str(OUT / f"{name}_inline_edit.png"), full_page=True)
        pg.close()
    br.close()
print("case", cid, "→", OUT)
