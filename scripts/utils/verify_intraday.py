#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""盘中观点验证：用 30 分钟线核对 Direction 信号的当日实际走势

数据来源：data/market/intraday/<指数名>_30min.json（fetch_market_intraday.py 生成）
信号来源：data/direction_signals/<博主名>.json（spec=today 等盘中观点）

用法:
  单点模式:
    python scripts/utils/verify_intraday.py --date 2026-03-05 --index 上证指数 \
        --direction 多 --window 下午
  信号批量模式:
    python scripts/utils/verify_intraday.py --from-signals <博主名> [--spec today] \
        [--window auto] [--out reports/<博主>_intraday_verify.md]

窗口: 全天 | 上午 | 下午 | 尾盘 | 发布后(--pub HH:MM)
  - 全天: 当日全部 8 根（10:00~15:00），首根 open≈9:30 开盘价
  - 上午: 10:00~11:30 | 下午: 13:30~15:00 | 尾盘: 14:30~15:00
  - 发布后: pub 时间起至 15:00 的 bar
收益率 = 窗口末根 close / 窗口首根 open - 1
"""
import argparse
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
INTRADAY_DIR = os.path.join(PROJECT_ROOT, "data", "market", "intraday")
SIGNALS_DIR = os.path.join(PROJECT_ROOT, "data", "direction_signals")

WINDOWS = ("全天", "上午", "下午", "尾盘", "发布后")

# 新浪 30 分钟 bar 的时刻序列（每日 8 根）
DIRECTION_ALIASES = {
    "多": 1, "看多": 1, "涨": 1, "上": 1, "1": 1,
    "空": -1, "看空": -1, "跌": -1, "下": -1, "-1": -1,
}


def load_bars(index_name):
    """加载指定指数的 30 分钟线 bars（按 time 排序的 dict 列表）"""
    path = os.path.join(INTRADAY_DIR, f"{index_name}_30min.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return (json.load(f) or {}).get("bars", [])


def select_bars(bars, date, window, pub_time=None):
    """取指定交易日窗口内的 bar 列表（无该日数据返回 None）"""
    day_bars = [b for b in bars if b["time"][:10] == date]
    if not day_bars:
        return None
    hhmm = lambda b: b["time"][11:16]
    if window == "全天":
        return day_bars
    if window == "上午":
        return [b for b in day_bars if hhmm(b) <= "11:30"]
    if window == "下午":
        return [b for b in day_bars if hhmm(b) >= "13:00"]
    if window == "尾盘":
        return [b for b in day_bars if hhmm(b) >= "14:00"]
    if window == "发布后":
        if not pub_time:
            raise ValueError("发布后窗口需要 --pub HH:MM")
        return [b for b in day_bars if hhmm(b) >= pub_time]
    raise ValueError(f"未知窗口 {window}（可选 {WINDOWS}）")


def window_stats(sel):
    """窗口 OHLC + 收益率"""
    o = sel[0]["open"]
    c = sel[-1]["close"]
    hi = max(b["high"] for b in sel)
    lo = min(b["low"] for b in sel)
    return {
        "n": len(sel),
        "open": o,
        "close": c,
        "high": hi,
        "low": lo,
        "ret": c / o - 1,
    }


def verdict(d, ret):
    """按信号方向 d(1/-1) 与收益率判定方向对错"""
    if ret > 0.0001:
        actual = 1
    elif ret < -0.0001:
        actual = -1
    else:
        actual = 0
    if actual == d:
        return "对"
    if actual == 0:
        return "平"
    return "错"


def auto_window(summary, pub):
    """批量模式窗口自动推导：summary 关键词优先，否则按 pub 时间"""
    summary = summary or ""
    if "下午" in summary:
        return "下午", None
    if "尾盘" in summary:
        return "尾盘", None
    if "上午" in summary or "早盘" in summary:
        return "上午", None
    hm = pub.split(" ")[1][:5] if " " in pub and len(pub) > 10 else ""
    if hm >= "15:00":
        return None, None       # 盘后发布，当天预测已失效
    if hm >= "09:30":
        return "发布后", hm     # 盘中发布 → 发布后至收盘
    return "全天", None         # 盘前发布 → 全天


def verify_one(bars, date, d, window, pub_time=None):
    """核对单个观点，返回统计 dict 或错误信息 dict"""
    sel = select_bars(bars, date, window, pub_time)
    if sel is None:
        return {"error": "非交易日或无数据"}
    st = window_stats(sel)
    st.update({"date": date, "window": window, "d": d, "verdict": verdict(d, st["ret"])})
    return st


def fmt_pct(x):
    return f"{x * 100:+.2f}%"


def print_single(st, date, idx, direction):
    if "error" in st:
        print(f"❌ {date} {idx}: {st['error']}")
        return
    print(f"\n=== {date} {idx} · {direction} · 窗口[{st['window']}] ===")
    print(f"  窗口 {st['n']} 根 30 分钟 bar")
    print(f"  开 {st['open']:.2f} | 收 {st['close']:.2f} | 高 {st['high']:.2f} | 低 {st['low']:.2f}")
    print(f"  收益率 {fmt_pct(st['ret'])}  → 方向判定: {st['verdict']}")


def mode_single(args):
    d = DIRECTION_ALIASES.get(str(args.direction).strip())
    if d is None:
        print(f"错误: 无法识别方向「{args.direction}」（用 多/空/涨/跌 或 1/-1）")
        sys.exit(2)
    bars = load_bars(args.index)
    if bars is None:
        print(f"错误: 无 30 分钟数据文件 data/market/intraday/{args.index}_30min.json\n"
              f"  先运行 python scripts/utils/fetch_market_intraday.py")
        sys.exit(2)
    st = verify_one(bars, args.date, d, args.window, args.pub)
    print_single(st, args.date, args.index, args.direction)


def mode_batch(args):
    sig_file = os.path.join(SIGNALS_DIR, f"{args.blogger}.json")
    if not os.path.exists(sig_file):
        print(f"错误: 信号文件不存在 {sig_file}")
        sys.exit(2)
    with open(sig_file, encoding="utf-8") as f:
        data = json.load(f)
    signals = data.get("signals", [])

    # 过滤 spec
    if args.spec != "all":
        signals = [s for s in signals if s.get("spec") == args.spec]
    if not signals:
        print(f"⚠️ {args.blogger} 无 spec={args.spec} 的信号")
        return

    rows = []
    skipped = {"非交易日": 0, "无数据": 0, "盘后": 0, "无intraday文件": 0}
    for s in signals:
        idx = s.get("idx", "上证指数")
        pub = s.get("pub", "")
        date = pub[:10]
        d = s.get("d", 1)
        summary = s.get("summary", "")

        bars = load_bars(idx)
        if bars is None:
            skipped["无intraday文件"] += 1
            continue

        if args.window == "auto":
            window, pub_time = auto_window(summary, pub)
            if window is None:
                skipped["盘后"] += 1
                continue
        else:
            window = args.window
            pub_time = s.get("pub", "").split(" ")[1][:5] if window == "发布后" else None

        st = verify_one(bars, date, d, window, pub_time)
        if "error" in st:
            skipped["非交易日"] += 1
            continue
        st["idx"] = idx
        st["pub"] = pub
        st["summary"] = summary[:30]
        rows.append(st)

    # 输出
    print(f"\n=== {args.blogger} 盘中验证（spec={args.spec}，窗口={args.window}）===")
    print(f"信号 {len(signals)} 条 → 可验证 {len(rows)} 条（跳过: {skipped}）\n")
    if not rows:
        print("无可验证信号")
        return
    print(f"{'日期':<11}{'方向':>3}{'窗口':<5}{'收益率':>9} {'判定':<4}  摘要")
    print("-" * 70)
    for r in rows:
        print(f"{r['date']:<11}{r['d']:>3}{r['window']:<5}{fmt_pct(r['ret']):>9} {r['verdict']:<4}  {r['summary']}")

    ok = sum(1 for r in rows if r["verdict"] == "对")
    wrong = sum(1 for r in rows if r["verdict"] == "错")
    flat = sum(1 for r in rows if r["verdict"] == "平")
    print("-" * 70)
    print(f"对 {ok} / 错 {wrong} / 平 {flat} | 方向准确率(不计平) {ok / max(ok + wrong, 1):.0%}")

    if args.out:
        lines = [
            f"# {args.blogger} 盘中验证（spec={args.spec}，窗口={args.window}）",
            "",
            f"> 信号 {len(signals)} 条 → 可验证 {len(rows)} 条（跳过: {skipped}）",
            f"> 对 {ok} / 错 {wrong} / 平 {flat} | 方向准确率(不计平) {ok / max(ok + wrong, 1):.0%}",
            "",
            "| 日期 | 方向 | 窗口 | 收益率 | 判定 | 摘要 |",
            "|------|-----|------|--------|------|------|",
        ]
        for r in rows:
            lines.append(f"| {r['date']} | {r['d']} | {r['window']} | {fmt_pct(r['ret'])} | {r['verdict']} | {r['summary']} |")
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print(f"\n已保存: {args.out}")


def main():
    parser = argparse.ArgumentParser(description="盘中观点验证（30 分钟线）")
    # 单点模式参数
    parser.add_argument("--date", default=None, help="交易日 YYYY-MM-DD")
    parser.add_argument("--index", default="上证指数", help="指数名（默认上证指数）")
    parser.add_argument("--direction", default=None, help="方向 多/空/涨/跌 或 1/-1")
    parser.add_argument("--window", default=None, help=f"窗口 {WINDOWS}")
    parser.add_argument("--pub", default=None, help="发布后窗口的发布时间 HH:MM")
    # 批量模式参数
    parser.add_argument("--from-signals", dest="blogger", default=None, help="批量验证博主信号")
    parser.add_argument("--spec", default="today", help="批量过滤的 spec（默认 today；all=不过滤）")
    parser.add_argument("--out", default=None, help="批量结果保存路径（reports/*.md）")
    args = parser.parse_args()

    if args.blogger:
        args.window = args.window or "auto"
        mode_batch(args)
    elif args.date and args.direction:
        if not args.window:
            parser.error("单点模式需要 --window")
        mode_single(args)
    else:
        parser.error("需要单点模式(--date --direction --window) 或批量模式(--from-signals)")


if __name__ == "__main__":
    main()
