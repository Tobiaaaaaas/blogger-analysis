"""
Markdown to PDF converter using Python markdown + Playwright (Chromium).
Better emoji support than pandoc+xelatex.

Usage:
  python scripts/md_to_pdf.py                          # all reports
  python scripts/md_to_pdf.py --blogger 顺应周期        # single
"""

import os
import sys
import argparse
import markdown
from playwright.sync_api import sync_playwright

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
REPORTS_DIR = os.path.join(PROJECT_ROOT, "reports")
PDF_DIR = os.path.join(REPORTS_DIR, "pdf")
os.makedirs(PDF_DIR, exist_ok=True)

CSS = """
body {
    font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Noto Sans SC",
                 "Microsoft YaHei", sans-serif;
    max-width: 800px;
    margin: 0 auto;
    padding: 20px 40px;
    color: #1a1a1a;
    line-height: 1.8;
    font-size: 14px;
}
h1 { font-size: 24px; border-bottom: 2px solid #e74c3c; padding-bottom: 8px; color: #c0392b; }
h2 { font-size: 19px; border-bottom: 1px solid #ddd; padding-bottom: 4px; margin-top: 28px; color: #2c3e50; }
h3 { font-size: 16px; margin-top: 20px; color: #34495e; }
h4 { font-size: 14px; color: #555; }
blockquote {
    border-left: 3px solid #3498db;
    padding: 6px 14px;
    margin: 10px 0;
    background: #f0f7fb;
    color: #555;
    font-size: 13px;
}
table {
    border-collapse: collapse;
    width: 100%;
    margin: 12px 0;
    font-size: 12px;
}
th {
    background: #2c3e50;
    color: white;
    padding: 6px 8px;
    text-align: center;
    font-weight: 600;
}
td {
    border: 1px solid #ddd;
    padding: 5px 8px;
    text-align: center;
}
tr:nth-child(even) { background: #f8f9fa; }
tr:nth-child(odd) { background: white; }
strong { color: #c0392b; }
hr { border: none; border-top: 1px solid #eee; margin: 30px 0; }
code {
    background: #f4f4f4;
    padding: 1px 4px;
    border-radius: 3px;
    font-size: 12px;
}
a { color: #3498db; text-decoration: none; }
"""


def convert_one(md_path, pdf_path):
    with open(md_path, encoding="utf-8") as f:
        md_content = f.read()

    # Convert MD to HTML
    html_body = markdown.markdown(md_content, extensions=["tables", "fenced_code", "codehilite"])

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<style>{CSS}</style>
</head>
<body>
{html_body}
</body>
</html>"""

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(html, wait_until="networkidle")
        page.pdf(
            path=pdf_path,
            format="A4",
            margin={"top": "15mm", "bottom": "15mm", "left": "15mm", "right": "15mm"},
            print_background=True,
        )
        browser.close()

    size_kb = os.path.getsize(pdf_path) // 1024
    print(f"  ✓ {os.path.basename(pdf_path)} ({size_kb}K)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--blogger", help="Convert single blogger only")
    args = parser.parse_args()

    md_files = sorted(f for f in os.listdir(REPORTS_DIR) if f.endswith("_analysis.md"))
    if args.blogger:
        md_files = [f"{args.blogger}_analysis.md"]

    for md_file in md_files:
        md_path = os.path.join(REPORTS_DIR, md_file)
        pdf_name = md_file.replace(".md", ".pdf")
        pdf_path = os.path.join(PDF_DIR, pdf_name)
        print(f"Converting {md_file}...")
        convert_one(md_path, pdf_path)

    print(f"\nDone. PDFs in {PDF_DIR}/")


if __name__ == "__main__":
    main()
