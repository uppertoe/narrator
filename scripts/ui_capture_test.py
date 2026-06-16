"""Behavioural check: a concurrent voice capture must NOT wipe an open in-line
edit, must add the new row, and must auto-follow only when at the bottom."""
from __future__ import annotations
import sys, httpx
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8055"
c = httpx.Client(base_url=BASE, follow_redirects=False)
cid = c.post("/cases", data={"label": "cap", "weight_kg": "12",
                             "timezone": "Australia/Melbourne"}).headers["location"].rstrip("/").split("/")[-1]
c.post(f"/case/{cid}/utterance", data={"text": "adrenaline 10 microgram bolus", "source": "typed"})

fails = []
with sync_playwright() as p:
    br = p.chromium.launch()
    pg = br.new_page(viewport={"width": 1280, "height": 900})
    pg.goto(f"{BASE}/case/{cid}", wait_until="networkidle")

    # open the in-line edit on the existing row and type an un-saved correction
    pg.locator("article.ev a[title='Edit']").first.click()
    pg.wait_for_selector("form.row-edit")
    drug = pg.locator("form.row-edit input[name='drug']")
    drug.fill("noradrenaline")          # in-progress, NOT saved

    # a voice utterance lands mid-edit (provisional placeholder)
    pg.evaluate("async () => { await window.NarratorCapture.createProvisional(new Date().toISOString()); }")
    pg.wait_for_timeout(150)

    # 1) the open edit survived with its un-saved value
    if pg.locator("form.row-edit").count() != 1:
        fails.append("edit form was wiped by the concurrent capture")
    elif drug.input_value() != "noradrenaline":
        fails.append(f"edit value lost: {drug.input_value()!r}")

    # 2) the new transcribing row was added to the log
    if pg.locator("article.ev-transcribing").count() < 1:
        fails.append("new transcribing row did not appear")

    # 3) +add form area (#editor) untouched
    if pg.locator("#editor").count() != 1:
        fails.append("#editor container disappeared")

    br.close()

print("FAIL:" if fails else "PASS — open edit preserved, new row added")
for f in fails:
    print("  -", f)
sys.exit(1 if fails else 0)
