"""
Download minute-level index data from EastMoney.
Bypasses system proxy since EastMoney blocks proxy requests.
Six investable indices: 上证50, 沪深300, 创业板指, 科创50, 中证500, 中证1000
Period: 2026-01-01 onwards, 5-min K-line
"""
import json, os, sys, time

# Force no proxy BEFORE any other imports
import requests as _requests
_session_patch = _requests.Session
class NoProxySession(_session_patch):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.trust_env = False
        self.proxies = {'http': None, 'https': None}
_requests.Session = NoProxySession

import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "data", "minute", "5min")

# Index codes for EastMoney
# EastMoney symbol format: 1.000300 for CSI300, 0.399006 for CYB, etc.
INDICES = {
    "上证50":   {"code": "1.000016", "name": "SH50"},
    "沪深300":  {"code": "1.000300", "name": "CSI300"},
    "创业板指": {"code": "0.399006", "name": "CYB"},
    "科创50":   {"code": "1.000688", "name": "KC50"},
    "中证500":  {"code": "1.000905", "name": "CSI500"},
    "中证1000": {"code": "1.000852", "name": "CSI1000"},
}

def fetch_minute_data(code, start_date="2026-01-01", end_date="2026-08-05"):
    """Fetch 5-min K-line from EastMoney push2 API.
    API: https://push2his.eastmoney.com/api/qt/stock/kline/get
    """
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": code,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "5",         # 5-minute
        "fqt": "1",         # 前复权
        "beg": start_date.replace("-", ""),
        "end": end_date.replace("-", ""),
        "lmt": "100000",    # max records per request
        "ut": "fa5fd1943c7b386f172d6893dbbf196b",
    }

    s = _requests.Session()  # Use our NoProxySession
    r = s.get(url, params=params, timeout=60)
    data = r.json()

    if data.get("rc") != 0:
        print(f"  ERROR: API returned rc={data.get('rc')}, msg={data.get('msg')}")
        return None

    klines = data.get("data", {}).get("klines", [])
    if not klines:
        print(f"  No data returned for {code}")
        return None

    rows = []
    for line in klines:
        parts = line.split(",")
        rows.append({
            "time": parts[0],           # 2026-01-02 09:35:00
            "open": float(parts[1]),
            "close": float(parts[2]),
            "high": float(parts[3]),
            "low": float(parts[4]),
            "volume": float(parts[5]),
            "amount": float(parts[6]),
        })

    df = pd.DataFrame(rows)
    return df


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    all_data = {}

    for idx_name, info in INDICES.items():
        code = info["code"]
        print(f"\nDownloading {idx_name} ({code})...")
        time.sleep(0.5)  # Rate limit

        for attempt in range(3):
            df = fetch_minute_data(code)
            if df is not None:
                break
            print(f"  Retry {attempt+1}/3...")
            time.sleep(2)

        if df is None:
            print(f"  FAILED to download {idx_name}")
            continue

        print(f"  {len(df)} rows, {df['time'].min()} ~ {df['time'].max()}")

        # Convert to dict for JSON storage
        records = df.to_dict(orient="records")
        all_data[idx_name] = records

        # Also save individual CSV for convenience
        csv_path = os.path.join(OUTPUT_DIR, f"{info['name']}_5min.csv")
        df.to_csv(csv_path, index=False, encoding="utf-8")
        print(f"  Saved CSV: {csv_path}")

    # Save combined JSON
    json_path = os.path.join(OUTPUT_DIR, "minute_data.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False)
    print(f"\nCombined JSON saved: {json_path}")
    print(f"File size: {os.path.getsize(json_path):,} bytes")

    # Summary
    print(f"\n=== Summary ===")
    for name in all_data:
        df = pd.DataFrame(all_data[name])
        print(f"  {name}: {len(df)} rows, {df['time'].min()} ~ {df['time'].max()}")

    total_rows = sum(len(v) for v in all_data.values())
    print(f"  Total: {total_rows} rows across {len(all_data)} indices")


if __name__ == "__main__":
    main()
