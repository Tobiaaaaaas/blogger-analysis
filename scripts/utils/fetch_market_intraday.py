#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""获取 A 股指数 30 分钟线（新浪免费源）→ data/market/intraday/<指数名>_30min.json

为什么不用 akshare：
  东财接口对 Python requests/urllib 做 TLS 指纹封锁（连 `stock_zh_index_daily_em` 日线都
  RemoteDisconnected），即使 curl 绕过，东财分钟 K 线也是服务器端滚动 ~32 交易日缓冲，
  beg/end/lmt 参数无法回溯。新浪 CN_MarketData.getKLineData 无封锁，scale=30 & datalen=5000
  可回溯到 2024 年，覆盖全 2026。BaoStock 指数无分钟线；tushare 需积分凭据。

用法:
  python scripts/utils/fetch_market_intraday.py                # 7 指数全量
  python scripts/utils/fetch_market_intraday.py --test         # 只拉第 1 个指数
  python scripts/utils/fetch_market_intraday.py --out <目录>    # 自定义输出目录

增量合并：按 time 去重、新行覆盖旧行（对齐 fetch_market_data.py 的写回策略），可每天重跑续覆盖。
覆盖 QC：对照 data/market/market_data.json 上证指数 2026 交易日历，输出缺失交易日。
"""
import argparse
import json
import os
import sys
import time

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
DATA_DIR = os.path.join(PROJECT_ROOT, "data", "market")
INTRADAY_DIR = os.path.join(DATA_DIR, "intraday")
MARKET_FILE = os.path.join(DATA_DIR, "market_data.json")
DEFAULT_SINCE = "2026-01-01"

# 7 大指数（与 fetch_market_data.py / SKILL.md 一致）
INDICES = [
    ("sh000001", "上证指数"),
    ("sh000300", "沪深300"),
    ("sz399006", "创业板指"),
    ("sh000016", "上证50"),
    ("sh000905", "中证500"),
    ("sh000852", "中证1000"),
    ("sh000688", "科创50"),
]

SINA_URL = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            "CN_MarketData.getKLineData")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36")
SCALE = "30"
DATALEN = 5000  # 30 分钟 × 5000 根 ≈ 625 交易日（2024 → 今），覆盖全 2026


def fetch_intraday(symbol, retries=3):
    """拉取单个指数 30 分钟线，返回 [{time,open,high,low,close,volume}] 或 None"""
    url = f"{SINA_URL}?symbol={symbol}&scale={SCALE}&ma=no&datalen={DATALEN}"
    headers = {"User-Agent": UA, "Referer": "https://finance.sina.com.cn/"}
    for i in range(retries):
        try:
            r = requests.get(url, headers=headers, timeout=20)
            rows = r.json()
            if not isinstance(rows, list) or not rows:
                raise ValueError("空响应")
            out = []
            for row in rows:
                day = str(row.get("day", "")).strip()
                if len(day) < 16:
                    continue
                out.append({
                    "time": day[:16],  # YYYY-MM-DD HH:MM
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row.get("volume", 0) or 0),
                })
            if not out:
                raise ValueError("无可解析的 bar")
            return out
        except Exception as e:
            print(f"    尝试{i + 1}/{retries} 失败: {str(e)[:100]}")
            time.sleep(2 + i * 2)
    return None


def merge_bars(existing, new_rows):
    """按 time 去重合并，新行覆盖旧行，返回按 time 排序的 bars"""
    merged = {}
    for b in (existing or []):
        merged[b["time"]] = b
    for b in new_rows:
        merged[b["time"]] = b
    return [merged[t] for t in sorted(merged)]


def coverage_gap(present_dates, market_file, since):
    """对照日线交易日历，返回 2026 缺失交易日列表（文件缺失/解析失败返回 None）"""
    if not os.path.exists(market_file):
        return None
    try:
        with open(market_file, encoding="utf-8") as f:
            mkt = json.load(f)
        trading = sorted(r["日期"] for r in mkt.get("上证指数", []) if r["日期"] >= since)
    except Exception:
        return None
    present = set(present_dates)
    return [d for d in trading if d not in present]


def main():
    parser = argparse.ArgumentParser(description="获取 A 股指数 30 分钟线（新浪）")
    parser.add_argument("--test", action="store_true", help="只拉第 1 个指数")
    parser.add_argument("--out", default=INTRADAY_DIR, help=f"输出目录（默认 {INTRADAY_DIR}）")
    parser.add_argument("--since", default=DEFAULT_SINCE, help=f"覆盖 QC 起点（默认 {DEFAULT_SINCE}）")
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    indices = INDICES[:1] if args.test else INDICES

    print("=" * 62)
    print(f"  新浪 30 分钟线下载 | {len(indices)} 指数 | scale={SCALE} datalen={DATALEN}")
    print(f"  输出: {args.out}/")
    print("=" * 62)

    results = []
    for i, (symbol, name) in enumerate(indices):
        print(f"[{i + 1}/{len(indices)}] {name} ({symbol})")
        out_file = os.path.join(args.out, f"{name}_30min.json")

        existing = []
        if os.path.exists(out_file):
            try:
                with open(out_file, encoding="utf-8") as f:
                    existing = (json.load(f) or {}).get("bars", [])
                print(f"  已有旧数据 {len(existing)} 根，增量合并")
            except Exception as e:
                print(f"  旧文件读取失败（将重建）: {str(e)[:80]}")

        rows = fetch_intraday(symbol)
        if rows is None:
            print(f"  ❌ 抓取失败（保留旧数据）")
            results.append((name, False, len(existing)))
            if i < len(indices) - 1:
                time.sleep(3)
            continue

        bars = merge_bars(existing, rows)
        dates = sorted(set(b["time"][:10] for b in bars))
        time_range = {"earliest": dates[0], "latest": dates[-1]} if dates else {}
        result = {
            "index": name,
            "symbol": symbol,
            "period": SCALE,
            "scrape_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "time_range": time_range,
            "bars": bars,
        }
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        # 2026 覆盖 QC（以上证交易日历为基准）
        gap = coverage_gap(dates, MARKET_FILE, args.since) if not args.test else None
        gap_txt = f" | 2026 缺失交易日 {len(gap)} 个" if gap is not None else ""
        print(f"  ✅ {len(bars)} 根 / {len(dates)} 天 | {dates[0]} ~ {dates[-1]}{gap_txt}")
        results.append((name, True, len(bars)))
        if i < len(indices) - 1:
            time.sleep(3)

    print("-" * 62)
    failed = [r for r in results if not r[1]]
    ok = [r for r in results if r[1]]
    print(f"成功 {len(ok)}/{len(results)} 指数 | 失败: {', '.join(r[0] for r in failed) or '无'}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
