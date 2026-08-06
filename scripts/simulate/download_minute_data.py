"""
Download minute-level index data from EastMoney.
Uses urllib (NOT requests) — EastMoney blocks requests library by TLS fingerprint.
Six investable indices: 上证50, 沪深300, 创业板指, 科创50, 中证500, 中证1000

Period: 2026-01-01 onwards
Supports 1-min and 5-min K-line via --period param.

Usage:
  python scripts/simulate/download_minute_data.py              # default: 5min
  python scripts/simulate/download_minute_data.py --period 1   # 1min
  python scripts/simulate/download_minute_data.py --period 5   # 5min
"""
import json, os, sys, time, argparse, math, ssl
import urllib.request
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Index codes for EastMoney
# Format: 1.000016 for Shanghai, 0.399006 for Shenzhen
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


def _api_get(url, params, timeout=60):
    """Call EastMoney API via urllib (bypasses requests TLS fingerprint block)."""
    query = "&".join(f"{k}={v}" for k, v in params.items())
    full_url = f"{url}?{query}"
    req = urllib.request.Request(full_url, headers={
        "User-Agent": UA,
        "Referer": "https://quote.eastmoney.com/",
    })
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return json.loads(resp.read().decode())


def fetch_5min_kline(code, start_date="2026-01-01", end_date="2026-08-06"):
    """Fetch 5-min K-line via kline/get API."""
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": code,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "5",
        "fqt": "1",
        "beg": start_date.replace("-", ""),
        "end": end_date.replace("-", ""),
        "lmt": "100000",
        "ut": "fa5fd1943c7b386f172d6893dbbf196b",
    }

    data = _api_get(url, params)
    if data.get("rc") != 0:
        return None, data.get("msg", "unknown error")

    klines = data.get("data", {}).get("klines", [])
    if not klines:
        return None, "no data"

    rows = []
    for line in klines:
        parts = line.split(",")
        rows.append({
            "time": parts[0],
            "open": float(parts[1]),
            "close": float(parts[2]),
            "high": float(parts[3]),
            "low": float(parts[4]),
            "volume": float(parts[5]),
            "amount": float(parts[6]),
        })

    return pd.DataFrame(rows), None


def fetch_1min_trends(code, ndays=5):
    """Fetch 1-min K-line via trends2/get API.

    Returns intraday 1-min bars:
    time, open, close, high, low, volume, amount, _

    ndays: how many recent trading days to return (max ~200).
    """
    url = "https://push2his.eastmoney.com/api/qt/stock/trends2/get"
    params = {
        "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        "iscr": "0",
        "ndays": str(ndays),
        "secid": code,
        "ut": "fa5fd1943c7b386f172d6893dbbf196b",
    }

    data = _api_get(url, params)
    if data.get("rc") != 0:
        return None, data.get("msg", "unknown error")

    trends = data.get("data", {}).get("trends", [])
    if not trends:
        return None, "no data"

    rows = []
    for line in trends:
        parts = line.split(",")
        if len(parts) < 7:
            continue
        rows.append({
            "time": parts[0],
            "open": float(parts[1]),
            "close": float(parts[2]),
            "high": float(parts[3]),
            "low": float(parts[4]),
            "volume": float(parts[5]),
            "amount": float(parts[6]),
        })

    return pd.DataFrame(rows), None


def download_with_retry(fetch_fn, code, idx_name, max_retries=5):
    """Download with exponential backoff retry."""
    last_error = None
    for attempt in range(max_retries):
        try:
            df, error = fetch_fn()
            if df is not None and len(df) > 0:
                return df
            last_error = error or "empty data"
        except Exception as e:
            last_error = str(e)

        if attempt < max_retries - 1:
            delay = int(5 * math.pow(2, attempt))  # 5, 10, 20, 40, 80s
            print(f"  Retry {attempt+1}/{max_retries} in {delay}s... ({last_error[:80]})")
            time.sleep(delay)

    print(f"  FAILED after {max_retries} attempts: {last_error[:100]}")
    return None


def main():
    parser = argparse.ArgumentParser(description="Download minute-level index data from EastMoney")
    parser.add_argument("--period", "-p", type=int, default=5, choices=[1, 5],
                        help="K-line period: 1 (1min) or 5 (5min), default: 5")
    parser.add_argument("--start", default="2026-01-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default="2026-08-06", help="End date (YYYY-MM-DD)")
    args = parser.parse_args()

    period = args.period
    subdir = "1min" if period == 1 else "5min"
    OUTPUT_DIR = os.path.join(PROJECT_ROOT, "data", "minute", subdir)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    suffix = f"_{period}min"

    print("=" * 60)
    print(f"  EastMoney {period}min K-line Downloader")
    print(f"  Range: {args.start} ~ {args.end}")
    print(f"  Output: {OUTPUT_DIR}")
    print(f"  Indices: {', '.join(INDICES.keys())}")
    print("=" * 60)

    total_rows = 0

    for i, (idx_name, info) in enumerate(INDICES.items()):
        code = info["code"]
        csv_path = os.path.join(OUTPUT_DIR, f"{info['name']}{suffix}.csv")

        print(f"\n[{i+1}/6] {idx_name} ({code})", end="", flush=True)

        if i > 0:
            time.sleep(3)  # Rate limit between indices

        if period == 1:
            # 1min: trends2/get API, ndays=200 to cover full 2026
            print(" — 1min trends2...")
            df = download_with_retry(
                lambda: fetch_1min_trends(code, ndays=200),
                code, idx_name
            )

            if df is not None:
                dates = sorted(df["time"].str[:10].unique())
                print(f"  {len(df):>6} rows, {len(dates):>3} dates: {dates[0]} ~ {dates[-1]}")
                df.to_csv(csv_path, index=False, encoding="utf-8")
                total_rows += len(df)
        else:
            # 5min: kline/get API
            print(" — 5min kline...")
            df = download_with_retry(
                lambda: fetch_5min_kline(code, args.start, args.end),
                code, idx_name
            )

            if df is not None:
                dates = sorted(df["time"].str[:10].unique())
                print(f"  {len(df):>6} rows, {len(dates):>3} dates: {dates[0]} ~ {dates[-1]}")
                df.to_csv(csv_path, index=False, encoding="utf-8")
                total_rows += len(df)

    # Summary
    print(f"\n{'=' * 60}")
    print(f"  Download complete — {total_rows:,} total rows")
    print(f"{'=' * 60}")
    for name, info in INDICES.items():
        csv_path = os.path.join(OUTPUT_DIR, f"{info['name']}{suffix}.csv")
        if os.path.exists(csv_path):
            df = pd.read_csv(csv_path)
            dates = sorted(df["time"].str[:10].unique())
            print(f"  {name}: {len(df):>6} rows, {len(dates):>3} dates, "
                  f"{dates[0] if dates else '?'} ~ {dates[-1] if dates else '?'}")
        else:
            print(f"  {name}: MISSING")


if __name__ == "__main__":
    main()
