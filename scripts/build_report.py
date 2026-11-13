"""Render docs/report/REPORT.md to docs/report/GigBid_RnD_Report.pdf (A4, print CSS) with headless Chrome.

    python scripts/build_report.py [--chrome /path/to/chrome]

The Markdown is the source of truth; every number in it cites a file under evals/results/. Mermaid blocks are not
rendered in the PDF (the architecture figure is an image); everything after '<!-- APPENDIX -->' starts on a new page
and does not count toward the 10-page limit.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "docs/report/REPORT.md"
OUT = ROOT / "docs/report/GigBid_RnD_Report.pdf"

CSS = """
@page { size: A4; margin: 16mm 15mm 16mm 15mm; }
body { font-family: "Inter", "Segoe UI", "Helvetica Neue", Arial, sans-serif; font-size: 9.6pt; line-height: 1.38;
       color: #15181e; }
h1 { font-size: 17pt; margin: 0 0 2mm; }
h2 { font-size: 12pt; margin: 5mm 0 1.5mm; border-bottom: 0.4pt solid #c9ced6; padding-bottom: 0.8mm;
     break-after: avoid; }
h3 { font-size: 10.2pt; margin: 3mm 0 1mm; break-after: avoid; }
p, li { margin: 0 0 1.6mm; }
ul, ol { margin: 0 0 1.6mm 5mm; padding-left: 3mm; }
table { border-collapse: collapse; width: 100%; margin: 1.5mm 0 2.5mm; font-size: 8.4pt; break-inside: avoid; }
th, td { border: 0.4pt solid #c9ced6; padding: 0.8mm 1.4mm; text-align: left; vertical-align: top; }
th { background: #eef1f5; }
code { font-family: "JetBrains Mono", Menlo, Consolas, monospace; font-size: 8.2pt; background: #f3f4f6;
       padding: 0 0.6mm; border-radius: 1mm; }
pre { background: #f3f4f6; padding: 2mm; font-size: 7.8pt; line-height: 1.3; white-space: pre-wrap;
      break-inside: avoid; }
pre code { background: none; padding: 0; }
img { max-width: 100%; }
.meta { color: #5c6370; margin-bottom: 4mm; }
.appendix { break-before: page; }
blockquote { margin: 1.5mm 0; padding: 1mm 3mm; border-left: 1mm solid #c9ced6; color: #3b414c; }
"""


def find_chrome(explicit: str | None) -> str:
    for c in [explicit, "google-chrome", "chromium", "chromium-browser", "chrome"]:
        if c and shutil.which(c):
            return shutil.which(c)
    raise SystemExit("Chrome/Chromium not found; pass --chrome")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chrome")
    a = ap.parse_args()
    text = SRC.read_text()
    text = re.sub(r"```mermaid.*?```", "", text, flags=re.S)
    main_part, _, appendix = text.partition("<!-- APPENDIX -->")
    md = markdown.Markdown(extensions=["tables", "fenced_code", "sane_lists"])
    body = md.convert(main_part)
    if appendix:
        md.reset()
        body += '<div class="appendix">' + md.convert(appendix) + "</div>"
    html = f'<!doctype html><html><head><meta charset="utf-8"><style>{CSS}</style></head><body>{body}</body></html>'
    page = SRC.parent / ".report_build.html"  # next to REPORT.md so relative image paths resolve
    page.write_text(html)
    try:
        subprocess.run([find_chrome(a.chrome), "--headless=new", "--disable-gpu", "--no-sandbox",
                        "--no-pdf-header-footer", f"--print-to-pdf={OUT}", page.as_uri()],
                       check=True, capture_output=True, timeout=120)
    finally:
        page.unlink(missing_ok=True)
    pages = len(re.findall(rb"/Type\s*/Page[^s]", OUT.read_bytes()))
    print(f"wrote {OUT.relative_to(ROOT)} ({pages} pages incl. appendix)")


if __name__ == "__main__":
    main()
