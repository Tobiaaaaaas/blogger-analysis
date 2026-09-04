# -*- coding: utf-8 -*-
"""research/quality/run_compare.py — CLI：波段专项榜（swing 板成员波段档 × 综合波段信号 同表对比）。

用法：
  python -m research.quality.run_compare            # 构建榜单 → 写 quality/reports/ 3 产物
  python -m research.quality.run_compare --check    # 先跑内联校验（无未来/确定性/同源重算）再写

产物（research/quality/reports/，UTF-8；csv 用 utf-8-sig）：
  composite_swing_compare.md       波段专项榜 + 口径注释 + 综合逐信号明细 + 观察
  composite_swing_signals.csv      综合逐信号明细（机器可读）
  composite_swing_compare.csv      榜单机器可读（含综合行）

排序口径：达标者（N≥10 且 平均分>0.1，对齐 comparison_all 波段档）按平均分降序；
未达标者列榜尾并标注原因。综合行高亮 ★。仅 Mac 本地离线跑，无外部调用、无密钥。
"""
import argparse
import csv
import os

from .. import config
from .. import poll as pollmod
from ..backtest.backtest import clean_days
from . import REPORTS_DIR, ensure_reports
from . import engine
from . import member_bands as mb
COLS = ["rank", "name", "type", "n", "acc", "avg", "vol", "sharpe",
        "bull_n", "bull_avg", "bear_n", "bear_avg", "qual"]


def _fmt(x, nd=2, pct=False):
    if x is None or (isinstance(x, float) and x != x):
        return "—"
    if pct:
        return f"{x * 100:.{nd}f}%"
    return f"{x:.{nd}f}"


def _fmt_sign(x, nd=2, pct=False):
    if x is None or (isinstance(x, float) and x != x):
        return "—"
    s = _fmt(x, nd, pct)                    # 负号由 _fmt 自带
    return f"{'+' if x > 0 else ''}{s}"     # 正数加显式 +


def write_csv(path, rows, fields):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in fields})


# ---------------- 内联校验（--check） ----------------

def _run_checks(rows, index, board=engine.BOARD):
    """无未来函数 + 14:30 触发复算一致 + ep 恒晚于决策日 + 行级同源重算 + 采样确定性。"""
    import datetime as _dt
    errs = []
    snap_checked = 0
    for r in rows:
        day = _dt.date.fromisoformat(r["date"])
        dt = _dt.datetime.strptime(f"{r['date']} {engine.SNAP_TICK}",
                                   "%Y-%m-%d %H:%M").replace(tzinfo=config.BEIJING_TZ)
        snap = engine._snap_at(index, day, engine.SNAP_TICK)
        snap_checked += 1
        # ① 无未来函数：所有计入票的 pub 严格早于决策时刻 14:30
        for v in snap["votes"]:
            if v["pub"] >= r["date"] + " 14:30":
                errs.append(f"{r['date']}: 投票 pub {v['pub']} 不早于 14:30（无未来函数违例）")
        # ② 复算触发与记录一致（方向必须来自真实 2/3 快照）
        want = 1 if snap["trigger_long"] else (-1 if snap["trigger_short"] else 0)
        if want != r["direction"]:
            errs.append(f"{r['date']}: 复算方向 {want} ≠ 记录 {r['direction']}")
        # ③ 终点恒晚于决策日，且当前快照下全部可打分
        ep = _dt.date.fromisoformat(r["ep"]) if r.get("ep") else None
        if ep is None or ep <= day:
            errs.append(f"{r['date']}: 终点 {r.get('ep')} 未晚于决策日")
        if r["score"] is None:
            errs.append(f"{r['date']}: 无分（note={r['note']}）—— 当前快照不应出现待验证")
        # ④ 行级同源：ret/分 用独立 load_daily 重算对拍
        daily = engine._daily()
        ref, epc = daily.get(r["date"], {}).get("收盘"), daily.get(r["ep"], {}).get("收盘")
        ret = epc / ref - 1.0
        if abs(ret - r["ret"]) > 1e-9 or abs(round(r["direction"] * ret * 100, 2) - r["score"]) > 0.005:
            errs.append(f"{r['date']}: ret/score 同源重算不一致")
    # ⑤ 采样确定性：engine.signal_rows 二次运行逐行一致
    again = engine.signal_rows(index)
    if again != rows:
        errs.append("二次采样结果不一致（非确定性）")
    print(f"[check] 综合信号 {len(rows)} 条：无未来/触发复算/终点/同源重算/确定性 逐行过 {snap_checked} 档"
          + (" ✓" if not errs else f" ✗ {len(errs)} 处异常"))
    for e in errs[:20]:
        print("   -", e)
    return not errs


# ---------------- 榜单组装 ----------------

def _qual_note(m):
    if m["n"] < 10:
        return f"波段信号 N={m['n']} < 10"
    if m["avg"] <= 0.1:
        return f"平均分 {_fmt_sign(m['avg'])} ≤ 0.1"
    return "达标"


def build_entries(index):
    """返回 (entries, composite) ；entries=[{'name','type','m','note'}...] swing 成员 + 综合。"""
    members = []
    for name in mb.MEMBERS:
        m = mb.band_metrics(name)
        members.append({"name": name, "type": "博主", "m": m})
    comp_rows = engine.signal_rows(index)
    comp_m = engine.summarize_metrics(comp_rows)
    composite = {"name": "综合（swing板 2/3 共识）", "type": "综合", "m": comp_m}
    return members, composite, comp_rows


def rank_entries(members, composite):
    """达标者按平均分降序编排名，未达标者列榜尾（保留原顺序），综合行高亮。"""
    quals = [e for e in members + [composite] if mb.qualified(e["m"])]
    quals.sort(key=lambda e: e["m"]["avg"], reverse=True)
    unqual = [e for e in members + [composite] if not mb.qualified(e["m"])]
    return quals, unqual


# ---------------- 报告渲染 ----------------

def render_compare(quals, unquals, composite, comp_rows, coverage, n_days):
    L = []
    L.append(f"# 波段专项榜：综合波段信号 vs swing 板 {len(mb.MEMBERS)} 位成员博主（博主评价口径）")
    L.append("")
    L.append("衡量 **综合 2/3 共识信号**的方向性判断质量（analyze-blogger 博主评价口径："
             "score=d×return×100 → 平均分/正确率/波动率/夏普），与 30 位成员各取“波段档”"
             "（验证跨度≥2 交易日）做同类对比。**这不是 trade-PnL 回测**（trade-PnL 见 "
             "`research/backtest/`），它把信号当“预测”在固定周期上验证，测的是判断质量。")
    L.append("")
    L.append("## 口径")
    L.append("")
    L.append(f"- 综合信号 = swing 板 **{len(config.PANELS['swing'])} 人**共识：每干净交易日取 14:30 "
             "快照定调 —— 每博主取窗口内时间序最新一条、swing 剔 spec=long、无 mixed 概念（分母=当日"
             "有波段观点者 多+空）；看多占比 >2/3（表态者≥3）→ 当日一条看多；对称地 看空>2/3 → 看空；"
             "都不达 → 无信号（**每日一票**）。")
    L.append("- 综合验证 = 参考价=决策日上证 15:00 收盘；终点=决策日后**第 5 个交易日**收盘；"
             "score=d×(ep收盘/参考价−1)×100。")
    L.append(f"- 成员侧 = swing 板 **{len(mb.MEMBERS)} 位成员**（综合 2/3 票的投票人，short 专属不出现在"
             "swing 表决）各自 data/direction_signals 全量信号，`run_direction.calc` 按其**自己的 spec 周期**"
             "与目标指数打分，取波段档（span≥2）聚合 —— 与 comparison_all 波段档同源重算。")
    L.append(f"- 覆盖干净日：swing {coverage}；综合信号按日采样（14:30 定调）得 "
             f"**N={composite['m']['n']}**（多 {composite['m']['bull_n']} / 空 {composite['m']['bear_n']}，"
             f"{n_days} 个信号日），全部在行情覆盖内、可打分。")
    L.append("- 榜单资格：N≥10 且 平均分>0.1（对齐 comparison_all 波段档），按**平均分**降序；"
             "未达标列榜尾。综合信号本质=固定 span5 的波段信号，同类可比。")
    L.append("- ⚠️ 口径不对称：综合=swing21 人共识·上证指数·t5 固定端点；成员=各自 spec/自身目标指数。"
             "同表排序比的是**方向性质量**，不是同一标的同一期限。看空腿仅 10 条，结论请以均分/正确率措辞。")
    L.append("")
    L.append("## 波段专项榜")
    L.append("")
    L.append("| " + " | ".join(["排名", "选手", "类型", "N", "正确率", "**平均分**", "波动率", "夏普",
                             "看多N", "看多均分", "看空N", "看空均分", "资格"]) + " |")
    L.append("|" + "---:|".join([":---", ":---", ":---:", ":---:", ":---:", ":---:", ":---:",
                               ":---:", ":---:", ":---:", ":---:", ":---:", ":---:"]) + "---:|")
    for i, e in enumerate(quals, 1):
        m = e["m"]
        mark = " ★" if e["type"] == "综合" else ""
        L.append(f"| {i} | {e['name']}{mark} | {e['type']} | {m['n']} | {_fmt(m['acc'],1,pct=True)} | "
                 f"**{_fmt_sign(m['avg'])}** | {_fmt(m['vol'])} | {_fmt_sign(m['sharpe'])} | "
                 f"{m['bull_n']} | {_fmt_sign(m['bull_avg'])} | {m['bear_n']} | {_fmt_sign(m['bear_avg'])} | ✓ |")
    if unquals:
        L.append("| — | （以下未达上榜资格 N≥10 且 平均分>0.1，仅全列） | | | | | | | | | | | |")
        for e in unquals:
            m = e["m"]
            mark = " ★" if e["type"] == "综合" else ""
            L.append(f"| — | {e['name']}{mark} | {e['type']} | {m['n']} | {_fmt(m['acc'],1,pct=True)} | "
                     f"{_fmt_sign(m['avg'])} | {_fmt(m['vol'])} | {_fmt_sign(m['sharpe'])} | "
                     f"{m['bull_n']} | {_fmt_sign(m['bull_avg'])} | {m['bear_n']} | {_fmt_sign(m['bear_avg'])} | "
                     f"{_qual_note(m)} |")
    L.append("")
    # 关键读数
    comp_pos = next((i for i, e in enumerate(quals, 1) if e["type"] == "综合"), None)
    if comp_pos:
        L.append(f"**综合波段信号（每日一票·+5日）位列波段专项榜第 {comp_pos} 位**"
                 f"（N={composite['m']['n']}，正确率 {_fmt(composite['m']['acc'],1,pct=True)}，"
                 f"平均分 {_fmt_sign(composite['m']['avg'])}，夏普 {_fmt_sign(composite['m']['sharpe'])}；"
                 f"看多 {composite['m']['bull_n']} 条均分 {_fmt_sign(composite['m']['bull_avg'])} / "
                 f"看空 {composite['m']['bear_n']} 条均分 {_fmt_sign(composite['m']['bear_avg'])}）。")
    else:
        L.append(f"综合波段信号未达上榜资格（N={composite['m']['n']}，"
                 f"平均分 {_fmt_sign(composite['m']['avg'])}）。")
    L.append("")
    L.append("## 综合波段信号 · 逐信号明细")
    L.append("")
    L.append("| # | 日期 | 当日板块表态(方向占比) | 方向 | 强度 | 周期 | 指数 | 参考价(当日收盘) "
             "| 终点日 | 终点收盘 | return | 分 | 备注 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(comp_rows, 1):
        L.append(f"| {i} | {r['date'][5:]} | {r['content']} | {r['dir_word']} | {r['strength']} | "
                 f"{r['period']} | {r['idx']} | {_fmt(r['ref'])} | "
                 f"{(r['ep'] or '—')[5:] if r['ep'] else '—'} | {_fmt(r['epc'])} | "
                 f"{_fmt_sign(r['ret'],2,pct=True)} | {_fmt_sign(r['score'])} | {r['note'] or '—'} |")
    L.append("")
    return "\n".join(L)


# ---------------- CLI ----------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="先跑内联校验（无未来/确定性/同源重算）再写报告")
    args = ap.parse_args()
    ensure_reports()

    index = pollmod.CorpusIndex()
    members, composite, comp_rows = build_entries(index)
    quals, unquals = rank_entries(members, composite)

    ok = True
    if args.check:
        ok = _run_checks(comp_rows, index)
        if not ok:
            print("校验未通过，不写报告。")
            return
    days = [r["date"] for r in comp_rows]
    cdays = clean_days(index, "swing")
    coverage = f"{cdays[0].isoformat()} → {cdays[-1].isoformat()}（{len(cdays)} 交易日）"
    sig_span = f"{days[0]} → {days[-1]}（{len(days)} 个信号日）" if days else "—"

    # 榜单 csv（含 30 成员 + 综合）
    rows_csv = []
    for i, e in enumerate(quals, 1):
        m = e["m"]
        rows_csv.append({"rank": i, "name": e["name"], "type": e["type"], "n": m["n"],
                         "acc": round(m["acc"], 4), "avg": round(m["avg"], 4),
                         "vol": round(m["vol"], 4), "sharpe": round(m["sharpe"], 4) if m["sharpe"] is not None else None,
                         "bull_n": m["bull_n"], "bull_avg": round(m["bull_avg"], 4),
                         "bear_n": m["bear_n"], "bear_avg": round(m["bear_avg"], 4), "qual": "达标"})
    for e in unquals:
        m = e["m"]
        rows_csv.append({"rank": "", "name": e["name"], "type": e["type"], "n": m["n"],
                         "acc": round(m["acc"], 4), "avg": round(m["avg"], 4),
                         "vol": round(m["vol"], 4), "sharpe": round(m["sharpe"], 4) if m["sharpe"] is not None else None,
                         "bull_n": m["bull_n"], "bull_avg": round(m["bull_avg"], 4),
                         "bear_n": m["bear_n"], "bear_avg": round(m["bear_avg"], 4), "qual": _qual_note(m)})
    write_csv(os.path.join(REPORTS_DIR, "composite_swing_compare.csv"), rows_csv, COLS)

    # 综合逐信号 csv
    sig_rows = [{
        "date": r["date"], "content": r["content"], "direction": r["direction"],
        "strength": r["strength"], "period": r["period"], "idx": r["idx"],
        "ref": r["ref"], "ep": r["ep"], "ep_close": r["epc"],
        "ret": round(r["ret"], 6) if r["ret"] is not None else None,
        "score": r["score"], "note": r["note"],
    } for r in comp_rows]
    write_csv(os.path.join(REPORTS_DIR, "composite_swing_signals.csv"), sig_rows,
              ["date", "content", "direction", "strength", "period", "idx", "ref", "ep",
               "ep_close", "ret", "score", "note"])

    # 报告 md
    md = render_compare(quals, unquals, composite, comp_rows, coverage, len(days))
    with open(os.path.join(REPORTS_DIR, "composite_swing_compare.md"), "w", encoding="utf-8") as f:
        f.write(md + "\n")

    # 控制台摘要
    m = composite["m"]
    comp_pos = next((i for i, e in enumerate(quals, 1) if e["type"] == "综合"), None)
    pos = f"波段专项榜第 {comp_pos}" if comp_pos else "未上榜"
    print(f"[quality] 综合波段（每日一票·+5日）N={m['n']}（多{m['bull_n']}/空{m['bear_n']}）| "
          f"命中 {m['hit']}/{m['denom']} 正确率 {m['acc']*100:.1f}% | 均分 {m['avg']:+.2f} | "
          f"波动率 {m['vol']:.2f} | 夏普 {m['sharpe'] if m['sharpe'] is not None else 0:.2f} | "
          f"信号日 {sig_span} | {pos}")
    print(f"[quality] 产物 → {REPORTS_DIR}/composite_swing_compare.{'md,csv'}")


if __name__ == "__main__":
    main()
