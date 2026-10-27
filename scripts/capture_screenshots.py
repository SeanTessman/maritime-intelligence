"""Capture REAL screenshots of the running dashboard with Playwright (headless Chromium).

Usage: start the app (`python tasks.py app`), then
    python scripts/capture_screenshots.py [--base http://127.0.0.1:8501] [--pages Home ...]
Screenshots are written to reports/figures/screenshot_<page>.png.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PAGES = {
    "home": "",
    "vessel_explorer": "Vessel_Explorer",
    "geospatial": "Geospatial_Intelligence",
    "anomaly_detection": "Anomaly_Detection",
    "network_analysis": "Network_Analysis",
    "investigation_workspace": "Investigation_Workspace",
    "analytical_reports": "Analytical_Reports",
    "system_health": "System_Health",
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8501")
    ap.add_argument("--pages", nargs="*", default=list(PAGES))
    ap.add_argument("--wait-ms", type=int, default=20000)
    ap.add_argument("--height", type=int, default=2200)
    args = ap.parse_args()
    out = ROOT / "reports" / "figures"
    out.mkdir(parents=True, exist_ok=True)
    exe = "/opt/pw-browsers/chromium" if os.path.exists("/opt/pw-browsers/chromium") else None
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=exe,
            args=["--use-gl=swiftshader", "--enable-webgl", "--ignore-gpu-blocklist"],
        )
        page = browser.new_page(
            viewport={"width": 1500, "height": args.height}, device_scale_factor=1
        )
        for name in args.pages:
            # Pages that list detections are captured on the SYNTHETIC demo data so portfolio
            # screenshots never present a real, named vessel as an outlier.
            query = "?synthetic=1" if name == "anomaly_detection" else ""
            page.goto(f"{args.base}/{PAGES[name]}{query}", wait_until="load", timeout=120_000)
            page.wait_for_timeout(args.wait_ms)
            errors = page.locator("[data-testid='stException']").count()
            path = out / f"screenshot_{name}.png"
            page.screenshot(path=str(path), full_page=True)
            print(f"{name}: {path} (exceptions on page: {errors})")
        browser.close()


if __name__ == "__main__":
    main()
