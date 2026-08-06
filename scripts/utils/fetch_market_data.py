"""
获取 A 股大盘数据（上证指数、沪深300、创业板指）
独立脚本，供 Skill 和其他分析流程调用

用法:
  python scripts/fetch_market_data.py                     # 默认2026年全年
  python scripts/fetch_market_data.py --start 20250101    # 指定起始日期
"""

import json
import os
import argparse
from datetime import datetime
import pandas as pd
import akshare as ak

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(PROJECT_ROOT, "data", "market")
os.makedirs(DATA_DIR, exist_ok=True)
OUTPUT_FILE = os.path.join(DATA_DIR, "market_data.json")


def fetch_index(symbol, name, start_date, end_date):
    """获取单个指数的日线数据"""
    print(f"  获取{name} ({symbol})...")
    try:
        df = ak.stock_zh_index_daily_em(symbol=symbol, start_date=start_date, end_date=end_date)
        df = df.rename(columns={
            "date": "日期", "open": "开盘", "close": "收盘",
            "high": "最高", "low": "最低", "volume": "成交量",
        })
        # 保留成交量字段（上证有），其他指数可能没有则删
        keep_cols = ["日期", "开盘", "收盘", "最高", "最低"]
        if "成交量" in df.columns:
            keep_cols.append("成交量")
        df = df[keep_cols]
        print(f"    {len(df)} 条记录")
        return df
    except Exception as e:
        print(f"    ❌ 失败: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description="获取A股大盘数据")
    parser.add_argument("--start", default=None, help="起始日期 YYYYMMDD")
    parser.add_argument("--end", default=None, help="结束日期 YYYYMMDD")
    args = parser.parse_args()

    # 默认获取 2026 年全年 + 前后缓冲
    start = args.start or "20260101"
    end = args.end or datetime.now().strftime("%Y%m%d")

    print(f"获取 {start} ~ {end} 大盘数据...")

    indices = [
        ("sh000001", "上证指数"),
        ("sh000300", "沪深300"),
        ("sz399006", "创业板指"),
    ]

    result = {}
    for symbol, name in indices:
        df = fetch_index(symbol, name, start, end)
        if df is not None:
            result[name] = df.to_dict(orient="records")

    if not result:
        print("❌ 所有指数获取失败")
        return

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    total_days = max((len(v) for v in result.values()), default=0)
    print(f"\n✅ 已保存: {OUTPUT_FILE} ({', '.join(result.keys())}, 各{total_days}天)")


if __name__ == "__main__":
    main()
