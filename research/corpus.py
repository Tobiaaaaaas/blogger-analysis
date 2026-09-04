# -*- coding: utf-8 -*-
"""research/corpus.py — 【语料构建·一次性】把 30 位板块成员的 data/direction_signals
规范化成 research/signals/ 下的可复用标准语料（后续 poll/backtest/其它研究都读它）。

只读 data/direction_signals（30 位板块成员的既有 DeepSeek 方向信号），不重跑 DeepSeek；
每信号归一化补上：board（板块归属）、target/target_txt（解析出的绝对目标日期/说明）。
不动 data/direction_signals 原文件。输出 deterministic，无随机。

字段语义（与 eval/run_direction.endpoint_of 对齐，见 trading_cal.endpoint_of）：
  target    = 验证终点日（scored：spec→交易日历解析出绝对日期；long/unscored → None=无期限）
  target_txt= 展示用（超短行=目标日 MM-DD 或词；波段行=周段/具体日期或"周期不明确"）
  board     = short（spec∈today/t1）/ swing（其余 scored + unscored long）
  剔除      = spec=today 且发布日非交易日（无真实可交易目标日，run_direction 同判 报错）

用法：python -m research.corpus   （无参数；需已存在 data/direction_signals 与行情缓存）
"""
import json
import os
import sys
from datetime import date

from . import config
from . import trading_cal as tc

BOARD_WORD = config.BOARD_WORD
SPEC_TEXT = config.SPEC_TEXT


def _period_word(spec):
    if spec in SPEC_TEXT:
        return SPEC_TEXT[spec]
    if spec.startswith("d:"):
        return spec[2:]
    return spec


def normalize_signal(blogger, raw):
    """raw dict → 归一化信号 dict；返回 (norm_or_None, drop_reason)。
    drop：spec=today 但发布日非交易日（无真实目标）→ 剔除并在 manifest 计数。"""
    pub = raw.get("pub", "")
    spec = raw.get("spec", "")
    cat = raw.get("cat", "")
    pubd = pub[:10]
    # 非交易日"今天" → 无 A 股真实目标（run_direction calc ② 报错同判）
    if spec == "today" and not tc.is_trading_day(pubd):
        return None, f"non_trading_today {pubd}"
    target = tc.endpoint_of(pubd, spec)          # date | None
    board = config.board_of_spec(spec)
    # 目标展示文案
    if spec == "long" or target is None:
        target_txt = "周期不明确"
    elif board == "short":
        target_txt = target.strftime("%m-%d")     # 超短行头 = 目标日（今天/明天 → MM-DD）
    elif spec.startswith("d:"):
        target_txt = target.strftime("%m-%d")      # 明确日期
    elif spec in ("week", "nweek", "nweek_first", "month", "nmonth"):
        target_txt = f"{_period_word(spec)}（至 {target:%m-%d}）"
    else:                                          # tN 等
        target_txt = f"{_period_word(spec)}（目标 {target:%m-%d}）"
    return {
        "blogger": blogger,
        "pub": pub,
        "d": raw.get("d"),
        "s": raw.get("s"),                        # scored 才有；unscored=long 无
        "idx": raw.get("idx"),
        "spec": spec,
        "cat": cat,
        "summary": raw.get("summary", ""),
        "board": board,
        "target": target.isoformat() if target else None,
        "target_txt": target_txt,
    }, None


def build():
    config.ensure_dirs()
    members = sorted(set(config.PANELS["short"]) | set(config.PANELS["swing"]))
    manifest = {
        "version": 1,
        "created": __import__("datetime").datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z"),
        "source": "data/direction_signals/{昵称}.json（DeepSeek 既有信号，未重跑）",
        "members": len(members),
        "by_blogger": {},
        "totals": {"signals": 0, "short": 0, "swing": 0, "scored": 0, "unscored": 0},
        "dropped": {},       # 剔除原因 → 计数
        "spec_dist": {},
    }
    n_all = 0
    for blogger in members:
        src = os.path.join(config.DATA_SIGNALS_DIR, f"{blogger}.json")
        if not os.path.exists(src):
            print(f"  ⚠️ 缺 {src}，跳过")
            manifest["by_blogger"][blogger] = {"signals": 0, "error": "no source file"}
            continue
        raw_list = json.load(open(src, encoding="utf-8")).get("signals", [])
        out = []
        for raw in raw_list:
            n_all += 1
            norm, drop = normalize_signal(blogger, raw)
            if drop:
                manifest["dropped"][drop] = manifest["dropped"].get(drop, 0) + 1
                continue
            out.append(norm)
            m = manifest["totals"]
            m["signals"] += 1
            m[norm["board"]] += 1
            m["scored" if norm["cat"] == "scored" else "unscored"] += 1
            manifest["spec_dist"][norm["spec"]] = manifest["spec_dist"].get(norm["spec"], 0) + 1
        out.sort(key=lambda s: s["pub"])
        with open(os.path.join(config.SIGNALS_OUT_DIR, f"{blogger}.json"), "w", encoding="utf-8") as f:
            json.dump({"blogger": blogger, "signals": out}, f, ensure_ascii=False, indent=1)
        manifest["by_blogger"][blogger] = {"signals": len(out)}
        print(f"  {blogger}: {len(raw_list)} raw → {len(out)} 归一化"
              + (f"（剔 {len(raw_list) - len(out)}）" if len(out) != len(raw_list) else ""))
    manifest["totals"]["raw_signals_seen"] = n_all
    # 覆盖区间（每板块/全部）
    dates = []
    for b in members:
        fp = os.path.join(config.SIGNALS_OUT_DIR, f"{b}.json")
        for s in json.load(open(fp, encoding="utf-8"))["signals"]:
            dates.append(s["pub"][:10])
    if dates:
        manifest["pub_range"] = [min(dates), max(dates)]
    with open(os.path.join(config.SIGNALS_OUT_DIR, "_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print(f"\n语料就绪：{config.SIGNALS_OUT_DIR}")
    print(f"  共 {manifest['totals']['signals']} 条信号"
          f"（short {manifest['totals']['short']} / swing {manifest['totals']['swing']}）"
          f"· 发布 {manifest.get('pub_range')}")
    if manifest["dropped"]:
        print(f"  剔除 {manifest['dropped']}")


if __name__ == "__main__":
    build()
