"""Photograph the dashboard for the README, panel by panel.

The README described a dashboard nobody outside this machine had seen. The results page
is about 5,000 px tall, so one full-page capture is illegible at the width GitHub renders
it; each panel is shot on its own instead, at twice the pixel density so the numbers stay
readable.

    python -m uvicorn app.main:app --port 8077      # from backend/, in another shell
    python scripts/shoot_dashboard.py               # writes docs/img/

**The shipment is the form's own default** - Gebze to Düsseldorf, 24 t - so the images
carry no customer data and anyone can reproduce them by pressing the button.

Running this is also the cheapest end-to-end check there is. It has already caught three
things the API could not: CARTO stamping "API KEY REQUIRED" across a basemap inside a
200 response, terminals printed as `trieste → koln` beside a map spelling them properly,
and decimals written with a dot on a page that separates thousands with one.

Needs `playwright` and a Chrome install; neither is a runtime dependency, which is why
this is a script rather than a test.
"""

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO / "docs" / "img"
DEFAULT_URL = "http://127.0.0.1:8077/"

# Selectors read off the live DOM rather than guessed from the markup - several panels
# are built by JavaScript and carry no id in index.html.
SHOTS = [
    ("01-ozet.png", "#ceo-cta-slot", "yönetici özeti"),
    ("02-gostergeler.png", "#kpi-row", "KPI satırı"),
    ("03-iso14083.png", "#conformance-card", "ISO 14083 öz değerlendirme"),
    ("04-bos-donus.png", "#empty-running-card", "Eurostat boş dönüş"),
    ("05-deniz-faktoru.png", "#sea-factor-card", "EU MRV gemi faktörü"),
    ("06-mesafe.png", "#sea-distance-card", "Pub 151 + OSM mesafe"),
    ("07-harita.png", ".panel-grid", "harita + alternatifler"),
    ("08-sure.png", "main .card:has(h2:text('Kapıdan kapıya süre'))", "kapıdan kapıya süre"),
    ("09-risk.png", "main .card:has(h2:text('Risk ve maliyet'))", "risk ve maliyet"),
    ("10-duyarlilik.png", "main .card:has(h2:text('Faktör esası duyarlılığı'))", "duyarlılık"),
    ("11-bacaklar.png", "main .card:has(h2:text('Bacak dökümü'))", "bacak dökümü"),
]


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    args.out.mkdir(parents=True, exist_ok=True)
    missing, failures = [], []

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        page = browser.new_page(viewport={"width": 1500, "height": 1000},
                                device_scale_factor=2)
        page.on("pageerror", lambda e: failures.append(str(e)))
        # A tile or font that 404s is exactly the kind of fault a screenshot run should
        # report, since the picture will show the hole without explaining it.
        page.on("response",
                lambda r: failures.append(f"{r.status} {r.url}") if r.status >= 400 else None)

        page.goto(args.url, wait_until="networkidle", timeout=60000)
        page.click("text=Rotala ve hesapla")
        # A cold route makes seven OSRM calls against a rate-limited public demo.
        page.wait_for_selector("#sea-distance-card", state="visible", timeout=180000)
        page.wait_for_timeout(9000)

        for name, selector, label in SHOTS:
            node = page.query_selector(selector)
            if node is None or not node.is_visible():
                missing.append(label)
                print(f"  {label:28} ATLANDI - {selector}")
                continue
            node.scroll_into_view_if_needed()
            page.wait_for_timeout(800)
            node.screenshot(path=str(args.out / name))
            box = node.bounding_box()
            print(f"  {label:28} {name:22} {int(box['width'])}x{int(box['height'])}")

        browser.close()

    print(f"\n{len(SHOTS) - len(missing)}/{len(SHOTS)} panel -> {args.out}")
    if failures:
        print("\nAG HATALARI (goruntulerde delik birakabilir):", file=sys.stderr)
        for line in sorted(set(failures)):
            print(f"  {line}", file=sys.stderr)
    return 1 if missing or failures else 0


if __name__ == "__main__":
    sys.exit(main())
