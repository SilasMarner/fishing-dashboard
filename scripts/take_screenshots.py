#!/usr/bin/env python3
"""Take screenshots of all Fish Logger pages for README docs."""

import time
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = "http://localhost:9879"
OUT  = Path(__file__).resolve().parent.parent / "docs" / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)

LOCATIONS = [
    "freeport_tx",
    "padre_island_tx",
    "pensacola_fl",
    "sargent_tx",
    "matagorda_tx",
]

def shot(page, url, name, wait_ms=2000):
    page.goto(url)
    page.wait_for_load_state("networkidle")
    time.sleep(wait_ms / 1000)
    path = OUT / f"{name}.png"
    page.screenshot(path=str(path), full_page=True)
    print(f"  {path.name}")

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1280, "height": 900},
                                  color_scheme="dark")
        page = ctx.new_page()

        print("=== Log form (embed) ===")
        shot(page, f"{BASE}/embed?location=freeport_tx", "embed_log_form")

        print("=== History pages ===")
        for loc in LOCATIONS:
            shot(page, f"{BASE}/history?location={loc}", f"history_{loc}")

        print("=== Analysis pages ===")
        for loc in LOCATIONS:
            shot(page, f"{BASE}/analysis?location={loc}", f"analysis_{loc}")

        print("=== Dashboard (log form) per location ===")
        for loc in LOCATIONS:
            shot(page, f"{BASE}/?location={loc}", f"dashboard_{loc}")

        browser.close()
    print(f"\nAll screenshots saved to {OUT}")

if __name__ == "__main__":
    main()
