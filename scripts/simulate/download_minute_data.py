#!/usr/bin/env python3
"""
Download minute-level index data from EastMoney for NAV simulation.

Self-contained script. Zero Python dependencies beyond stdlib + system curl.
EastMoney blocks requests/urllib by TLS fingerprint — only curl gets through.

Supports two API paths:
  A) kline/get + klt=1  — ideal: supports beg/end date params (full year range)
  B) trends2/get         — fallback: only ndays param (most-recent-N-days)

Usage:
  python scripts/simulate/download_minute_data.py --period 1          # 1min (2026 full year)
  python scripts/simulate/download_minute_data.py --period 5          # 5min
  python scripts/simulate/download_minute_data.py --period 1 --test   # test: 1 index only
  python scripts/simulate/download_minute_data.py --period 1 --start 2026-06-01  # custom range

Output:
  data/minute/1min/{SH50,CSI300,CSI500,CSI1000,CYB,KC50}_1min.csv
  data/minute/5min/{SH50,CSI300,CSI500,CSI1000,CYB,KC50}_5min.csv

CSV format: time,open,high,low,close,volume,amount
"""
import argparse
import csv
import json
import os
import subprocess
import sys
import time
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Index definitions ──────────────────────────────────────────────
INDICES = [
    ("上证50",   "1.000016", "SH50"),
    ("沪深300",  "1.000300", "CSI300"),
    ("创业板指", "0.399006", "CYB"),
    ("科创50",   "1.000688", "KC50"),
    ("中证500",  "1.000905", "CSI500"),
    ("中证1000", "1.000852", "CSI1000"),
]

# ── API constants ──────────────────────────────────────────────────
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
UT = "fa5fd1943c7b386f172d6893dbbf196b"
HOST = "https://push2his.eastmoney.com"

# kline/get API
KL_F1 = "f1,f2,f3,f4,f5,f6"
KL_F2 = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"

# trends2/get API
TR_F1 = "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13"
TR_F2 = "f51,f52,f53,f54,f55,f56,f57,f58"


# ── Core: curl subprocess ──────────────────────────────────────────
def curl_api(url, timeout=60):
    """Call EastMoney API via curl subprocess. Returns parsed JSON or None."""
    try:
        result = subprocess.run(
            ["curl", "-s", "--max-time", str(timeout),
             "-H", f"User-Agent: {UA}",
             "-H", "Referer: https://quote.eastmoney.com/",
             url],
            capture_output=True, text=True, timeout=timeout + 10,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception):
        return None


# ── API methods ────────────────────────────────────────────────────
def fetch_via_kline(code, start_date, end_date, klt):
    """Fetch via kline/get API (supports date range, works for klt=5, UNTESTED for klt=1).

    Returns list of {time, open, high, low, close, volume, amount} or None.
    """
    s = start_date.replace("-", "")
    e = end_date.replace("-", "")
    url = (f"{HOST}/api/qt/stock/kline/get?"
           f"secid={code}&fields1={KL_F1}&fields2={KL_F2}&klt={klt}&fqt=1"
           f"&beg={s}&end={e}&lmt=100000&ut={UT}")

    data = curl_api(url, timeout=60)
    if data is None or data.get("rc") != 0:
        return None

    klines = data.get("data", {}).get("klines", [])
    if not klines:
        return None

    rows = []
    for line in klines:
        p = line.split(",")
        if len(p) < 7:
            continue
        rows.append({
            "time": p[0], "open": float(p[1]), "high": float(p[3]),
            "low": float(p[4]), "close": float(p[2]),
            "volume": float(p[5]), "amount": float(p[6]),
        })
    return rows


def fetch_via_trends(code, ndays):
    """Fetch via trends2/get API (no date range, returns most-recent-N trading days).

    trends2 columns: time, open, close, high, low, volume, amount, avg_price
    We remap to:      time, open, high,  low, close, volume, amount
    """
    url = (f"{HOST}/api/qt/stock/trends2/get?"
           f"fields1={TR_F1}&fields2={TR_F2}&iscr=0"
           f"&ndays={ndays}&secid={code}&ut={UT}")

    data = curl_api(url, timeout=60)
    if data is None or data.get("rc") != 0:
        return None

    trends = data.get("data", {}).get("trends", [])
    if not trends:
        return None

    rows = []
    for line in trends:
        p = line.split(",")
        if len(p) < 7:
            continue
        rows.append({
            "time": p[0], "open": float(p[1]), "high": float(p[3]),
            "low": float(p[4]), "close": float(p[2]),
            "volume": float(p[5]), "amount": float(p[6]),
        })
    return rows


# ── Save ───────────────────────────────────────────────────────────
def save_csv(filepath, rows):
    """Save rows to CSV, return (row_count, date_range_str)."""
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["time", "open", "high", "low", "close", "volume", "amount"])
        w.writeheader()
        w.writerows(rows)

    dates = sorted(set(r["time"][:10] for r in rows))
    return len(rows), f"{dates[0]} ~ {dates[-1]}" if dates else "no data"


# ── Main download logic ────────────────────────────────────────────
def download_one(name, code, prefix, period, start_date, end_date):
    """Download one index. Returns (success, method, rows_count, date_range)."""
    klt = str(period)
    suffix = f"_{period}min"
    filepath = os.path.join(PROJECT_ROOT, "data", "minute",
                            "1min" if period == 1 else "5min",
                            f"{prefix}{suffix}.csv")

    # ── Strategy A: kline/get (supports date range) ──
    print(f"  [A] kline/get klt={klt} ({start_date} ~ {end_date})...", end=" ", flush=True)
    rows = fetch_via_kline(code, start_date, end_date, klt)
    if rows:
        n, dr = save_csv(filepath, rows)
        print(f"OK: {n:,} rows, {dr}")
        return True, "kline", n, dr
    print("FAIL")

    # ── Strategy B: trends2/get (most-recent-N-days) ──
    for ndays in [200, 100, 60, 30, 10, 5]:
        print(f"  [B] trends2/get ndays={ndays}...", end=" ", flush=True)
        rows = fetch_via_trends(code, ndays)
        if rows:
            n, dr = save_csv(filepath, rows)
            print(f"OK: {n:,} rows, {dr}")
            return True, f"trends ndays={ndays}", n, dr
        print("FAIL")
        if ndays > 5:
            time.sleep(2)

    print(f"  ALL METHODS FAILED for {name}")
    return False, None, 0, ""


def main():
    today = datetime.now().strftime("%Y-%m-%d")
    parser = argparse.ArgumentParser(
        description="Download minute-level index data from EastMoney (curl-based)")
    parser.add_argument("--period", "-p", type=int, default=5, choices=[1, 5],
                        help="K-line period: 1 or 5 (default: 5)")
    parser.add_argument("--start", default="2026-01-01",
                        help="Start date for kline/get (default: 2026-01-01)")
    parser.add_argument("--end", default=today,
                        help=f"End date (default: {today})")
    parser.add_argument("--test", action="store_true",
                        help="Test mode: download first index only")
    args = parser.parse_args()

    period = args.period
    subdir = "1min" if period == 1 else "5min"
    out_dir = os.path.join(PROJECT_ROOT, "data", "minute", subdir)
    os.makedirs(out_dir, exist_ok=True)

    indices = INDICES[:1] if args.test else INDICES

    print("=" * 65)
    print(f"  EastMoney {period}min Downloader")
    print(f"  {len(indices)} index(es) | kline range: {args.start} ~ {args.end}")
    print(f"  Output: {out_dir}/")
    print("=" * 65)
    print()

    results = []
    for i, (name, code, prefix) in enumerate(indices):
        print(f"[{i+1}/{len(indices)}] {name} ({code})")
        ok, method, count, dr = download_one(
            name, code, prefix, period, args.start, args.end)
        results.append((name, prefix, ok, method, count, dr))
        print()

        # Rate limit between indices
        if i < len(indices) - 1:
            time.sleep(3)

    # ── Summary ────────────────────────────────────────────────────
    print("=" * 65)
    print(f"  SUMMARY — {period}min")
    print("=" * 65)
    total = 0
    for name, prefix, ok, method, count, dr in results:
        status = f"[{method}]" if ok else "[FAILED]"
        print(f"  {name:8s}  {status:20s}  {count:>8,} rows  {dr}")
        if ok:
            total += count
    print(f"  {'─'*60}")
    print(f"  TOTAL: {total:,} rows across {sum(1 for r in results if r[2])}/{len(results)} indices")

    if not any(r[2] for r in results):
        print()
        print("  ⚠️  ALL downloads failed. Likely causes:")
        print("     1. EastMoney API is blocking your IP (wait 1-2 hours and retry)")
        print("     2. curl is not installed (required: brew install curl or apt install curl)")
        print("     3. Network connectivity issues")
        sys.exit(1)


if __name__ == "__main__":
    main()
