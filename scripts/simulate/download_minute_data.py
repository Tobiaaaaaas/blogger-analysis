"""
Download minute-level index data from EastMoney.
Uses subprocess + curl (only curl bypasses EastMoney TLS fingerprint detection).
Six investable indices: 上证50, 沪深300, 创业板指, 科创50, 中证500, 中证1000

Usage:
  python scripts/simulate/download_minute_data.py              # default: 5min
  python scripts/simulate/download_minute_data.py --period 1   # 1min
  python scripts/simulate/download_minute_data.py --period 5   # 5min
"""
import json, os, sys, time, argparse, math, subprocess, tempfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

INDICES = {
    "上证50":   {"code": "1.000016", "name": "SH50"},
    "沪深300":  {"code": "1.000300", "name": "CSI300"},
    "创业板指": {"code": "0.399006", "name": "CYB"},
    "科创50":   {"code": "1.000688", "name": "KC50"},
    "中证500":  {"code": "1.000905", "name": "CSI500"},
    "中证1000": {"code": "1.000852", "name": "CSI1000"},
}

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
UT = "fa5fd1943c7b386f172d6893dbbf196b"

FIELDS1_KL = "f1,f2,f3,f4,f5,f6"
FIELDS2_KL = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
FIELDS1_TR = "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13"
FIELDS2_TR = "f51,f52,f53,f54,f55,f56,f57,f58"


def curl_get(url, timeout=60):
    """Call API via curl subprocess (bypasses EastMoney TLS blocking)."""
    result = subprocess.run(
        ["curl", "-s", "--max-time", str(timeout),
         "-H", f"User-Agent: {UA}",
         "-H", "Referer: https://quote.eastmoney.com/",
         url],
        capture_output=True, text=True, timeout=timeout + 5
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def check_api_available():
    """Quick health check — returns True if push2his API is reachable."""
    url = (f"https://push2his.eastmoney.com/api/qt/stock/trends2/get?"
           f"fields1=f1&fields2=f51,f52&iscr=0&ndays=1&secid=1.000016&ut={UT}")
    data = curl_get(url, timeout=10)
    return data is not None and data.get("rc") == 0


def fetch_5min_kline(code, start_date="2026-01-01", end_date="2026-08-07"):
    """Fetch 5-min K-line via kline/get API."""
    url = (f"https://push2his.eastmoney.com/api/qt/stock/kline/get?"
           f"secid={code}&fields1={FIELDS1_KL}&fields2={FIELDS2_KL}&klt=5&fqt=1"
           f"&beg={start_date.replace('-', '')}&end={end_date.replace('-', '')}"
           f"&lmt=100000&ut={UT}")

    data = curl_get(url, timeout=60)
    if data is None or data.get("rc") != 0:
        return None

    klines = data.get("data", {}).get("klines", [])
    if not klines:
        return None

    rows = []
    for line in klines:
        parts = line.split(",")
        rows.append({
            "time": parts[0], "open": float(parts[1]), "close": float(parts[2]),
            "high": float(parts[3]), "low": float(parts[4]),
            "volume": float(parts[5]), "amount": float(parts[6]),
        })
    return rows


def fetch_1min_trends(code, ndays=5):
    """Fetch 1-min K-line via trends2/get API.

    Returns list of {time, open, high, low, close, volume, amount}.
    trends2 cols: time, open, close, high, low, volume, amount, avg_price
    We reorder to: time, open, high, low, close, volume, amount
    """
    url = (f"https://push2his.eastmoney.com/api/qt/stock/trends2/get?"
           f"fields1={FIELDS1_TR}&fields2={FIELDS2_TR}&iscr=0"
           f"&ndays={ndays}&secid={code}&ut={UT}")

    data = curl_get(url, timeout=60)
    if data is None or data.get("rc") != 0:
        return None

    trends = data.get("data", {}).get("trends", [])
    if not trends:
        return None

    rows = []
    for line in trends:
        parts = line.split(",")
        if len(parts) < 7:
            continue
        rows.append({
            "time": parts[0],
            "open": float(parts[1]),
            "high": float(parts[3]),
            "low": float(parts[4]),
            "close": float(parts[2]),
            "volume": float(parts[5]),
            "amount": float(parts[6]),
        })
    return rows


def wait_for_api(timeout_minutes=120, poll_seconds=30):
    """Block until push2his API becomes available."""
    print(f"Waiting for EastMoney API (timeout={timeout_minutes}min)...")
    deadline = time.time() + timeout_minutes * 60
    while time.time() < deadline:
        if check_api_available():
            print("  API available!")
            return True
        print(f"  [{time.strftime('%H:%M:%S')}] blocked, retry in {poll_seconds}s...")
        time.sleep(poll_seconds)
    print("  TIMEOUT: API did not become available")
    return False


def main():
    parser = argparse.ArgumentParser(description="Download minute-level index data")
    parser.add_argument("--period", "-p", type=int, default=5, choices=[1, 5],
                        help="K-line period: 1 or 5 (default: 5)")
    parser.add_argument("--start", default="2026-01-01")
    parser.add_argument("--end", default="2026-08-07")
    parser.add_argument("--wait", type=int, default=0,
                        help="Wait up to N minutes for API to become available")
    parser.add_argument("--ndays", type=int, default=200,
                        help="ndays param for trends2 API (default: 200, try 5 if fails)")
    args = parser.parse_args()

    period = args.period
    subdir = "1min" if period == 1 else "5min"
    output_dir = os.path.join(PROJECT_ROOT, "data", "minute", subdir)
    os.makedirs(output_dir, exist_ok=True)

    # Wait for API if requested
    if args.wait > 0:
        if not wait_for_api(timeout_minutes=args.wait):
            sys.exit(1)

    print(f"{'='*60}")
    print(f"  EastMoney {period}min Downloader (curl-based)")
    print(f"  Output: {output_dir}")
    print(f"{'='*60}")

    total_rows = 0
    suffix = f"_{period}min"

    for i, (name, info) in enumerate(INDICES.items()):
        code = info["code"]
        csv_path = os.path.join(output_dir, f"{info['name']}{suffix}.csv")

        print(f"\n[{i+1}/6] {name} ({code})")

        if i > 0:
            delay = 10 if period == 1 else 3
            print(f"  Delay {delay}s...")
            time.sleep(delay)

        if period == 1:
            print(f"  Fetching 1min trends (ndays={args.ndays})...")
            rows = None
            for ndays in [args.ndays, 60, 30, 10, 5]:
                if ndays != args.ndays:
                    print(f"    Retry with ndays={ndays}...")
                    time.sleep(5)
                rows = fetch_1min_trends(code, ndays=ndays)
                if rows is not None:
                    break
        else:
            print(f"  Fetching 5min kline ({args.start}~{args.end})...")
            rows = fetch_5min_kline(code, args.start, args.end)

        if rows is None:
            print(f"  FAILED — API blocked or rejected")
            continue

        # Save CSV
        import csv
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["time", "open", "high", "low", "close", "volume", "amount"])
            w.writeheader()
            w.writerows(rows)

        dates = sorted(set(r["time"][:10] for r in rows))
        print(f"  OK: {len(rows):>6} rows, {len(dates):>3} dates: {dates[0]} ~ {dates[-1]}")
        print(f"  Saved: {csv_path}")
        total_rows += len(rows)

    # Summary
    print(f"\n{'='*60}")
    print(f"  Download complete — {total_rows:,} total rows")
    print(f"{'='*60}")
    for name, info in INDICES.items():
        csv_path = os.path.join(output_dir, f"{info['name']}{suffix}.csv")
        if os.path.exists(csv_path):
            with open(csv_path) as f:
                line_count = sum(1 for _ in f) - 1  # minus header
            print(f"  {name}: {line_count:>6} rows")
        else:
            print(f"  {name}: MISSING")


if __name__ == "__main__":
    main()
