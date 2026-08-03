"""
Markdown to PDF converter using Python markdown + Chrome headless.
Usage:
  python scripts/md_to_pdf.py                          # all reports
  python scripts/md_to_pdf.py --blogger 顺应周期        # single
"""

import os
import sys
import argparse
import subprocess
import tempfile
import markdown

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
BLOGGERS_DIR = os.path.join(PROJECT_ROOT, "reports", "bloggers")
STRATEGY_DIR = os.path.join(PROJECT_ROOT, "reports", "strategy")
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

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
table { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 12px; }
th { background: #2c3e50; color: white; padding: 6px 8px; text-align: center; font-weight: 600; }
td { border: 1px solid #ddd; padding: 5px 8px; text-align: center; }
tr:nth-child(even) { background: #f8f9fa; }
tr:nth-child(odd) { background: white; }
strong { color: #c0392b; }
hr { border: none; border-top: 1px solid #eee; margin: 30px 0; }
code { background: #f4f4f4; padding: 1px 4px; border-radius: 3px; font-size: 12px; }
pre { background: #f4f4f4; padding: 10px; border-radius: 4px; font-size: 11px; line-height: 1.4; overflow-x: auto; }
a { color: #3498db; text-decoration: none; }
"""


def convert_one(md_path, pdf_path):
    with open(md_path, encoding="utf-8") as f:
        md_content = f.read()

    html_body = markdown.markdown(md_content, extensions=["tables", "fenced_code"])
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

    with tempfile.NamedTemporaryFile(suffix=".html", mode="w", encoding="utf-8", delete=False) as tmp:
        tmp.write(html)
        tmp_path = tmp.name

    try:
        subprocess.run([
            CHROME, "--headless", "--disable-gpu", "--no-sandbox",
            f"--print-to-pdf={os.path.abspath(pdf_path)}",
            f"--virtual-time-budget=5000",
            f"file://{tmp_path}"
        ], check=True, capture_output=True, timeout=30)
    finally:
        os.unlink(tmp_path)

    size_kb = os.path.getsize(pdf_path) // 1024
    print(f"  ✓ {os.path.basename(pdf_path)} ({size_kb}K)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--blogger", help="Convert single blogger only")
    args = parser.parse_args()

    # 1. Blogger reports
    bloggers_pdf = os.path.join(BLOGGERS_DIR, "pdf")
    os.makedirs(bloggers_pdf, exist_ok=True)
    md_files = sorted(f for f in os.listdir(BLOGGERS_DIR) if f.endswith("_analysis.md"))
    if args.blogger:
        md_files = [f"{args.blogger}_analysis.md"]

    for md_file in md_files:
        md_path = os.path.join(BLOGGERS_DIR, md_file)
        pdf_path = os.path.join(bloggers_pdf, md_file.replace(".md", ".pdf"))
        print(f"Converting bloggers/{md_file}...")
        convert_one(md_path, pdf_path)

    # 2. Strategy docs
    if not args.blogger:
        strategy_pdf = os.path.join(STRATEGY_DIR, "pdf")
        os.makedirs(strategy_pdf, exist_ok=True)
        for md_file in sorted(f for f in os.listdir(STRATEGY_DIR) if f.endswith(".md")):
            md_path = os.path.join(STRATEGY_DIR, md_file)
            pdf_path = os.path.join(strategy_pdf, md_file.replace(".md", ".pdf"))
            print(f"Converting strategy/{md_file}...")
            convert_one(md_path, pdf_path)

    print(f"\nDone. PDFs in {bloggers_pdf}/ and {os.path.join(STRATEGY_DIR, 'pdf')}/")


if __name__ == "__main__":
    main()
