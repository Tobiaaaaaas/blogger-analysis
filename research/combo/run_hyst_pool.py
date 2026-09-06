# -*- coding: utf-8 -*-
"""research/combo/run_hyst_pool.py — CLI：滞回策略 · 波段委员会（博主池）换池实验。

默认参数**全部不动**（w5 · Q_open=Q_exit=10 · TO_LONG=2/3 · TX_LONG=1/2 = canonical combo_hyst.md 那套，
跑在"现役 30 人"上）；这里唯一自变量 = swing 波段投票委员会（博主池）。依据仓库 reports 的质量先验
（博主方向能力）构造池梯度：现役 30→逐步剔除未严格达标尾 2（刘海娃娃/顺应周期）→用现役外先验替换
（财牛/拉着幸福手）→按先验降序取 top-k，测委员会构成对滞回结果的影响。**纯研究、不动 live briefing
PANEL_SWING（S0 即现役 30，作对照基线）、不改 canonical combo_hyst.**。

关键工程点（探索确认）：
  · config.PANELS 是对 briefing/scripts/config.PANELS 的**同对象引用**，poll/daygrid/backtest 全部
    **调用时**读 config.PANELS[board] → 在 CorpusIndex() 后、每次 build_contexts 前原地改 config.PANELS["swing"]
    即换池（无需重建 index；CorpusIndex 只按成员并集载文件一次）。
  · 新候选博主不在 tracked research/signals/ 里 → 先把 config.SIGNALS_OUT_DIR 指到 tempfile，
    按全池成员并集 corpus.build() 归一化语料（只读 data/direction_signals 原文件），再建 CorpusIndex。
    全程不触碰 tracked research/signals/，跑完 rmtree。
  · 干净日随池子变 → **固定公共网格**：以 S0（现役 30）干净窗末日为公共 end，所有池
    build_contexts(..., end=S0末日) → 各池恒 n_clean=149 日且逐日 date 与 S0 一致（护栏断言）。

产物（research/combo/reports/，UTF-8 / csv utf-8-sig）：
  combo_hyst_pool.md   报告（口径头 → 全池汇总表（long/both 分表）→ 相对 S0 delta → 稳健列 → 逐月对比 → 读法警示）
  combo_hyst_pool.csv  18 行（9 池 × long/both）机器可读全量

用法：
  python -m research.combo.run_hyst_pool            # 全池回测 → reports/combo_hyst_pool.{md,csv}
  python -m research.combo.run_hyst_pool --check    # + S0 vs canonical 校验 + 确定性双跑 + §2 边界断言
"""
import argparse
import csv
import os
import shutil
import tempfile
from fractions import Fraction

from .. import config
from .. import corpus as corpusmod
from .. import poll as pollmod
from . import REPORTS_DIR, ensure_reports
from . import daygrid, hyst
from . import run_hyst as rh            # 复用 MODES/MODE_WORD/NAME/_fmt/_fmt_sign/write_csv

# ---- 参数 = canonical（零改动；只为可读性显式化）----
TO = config.HYST_TO_LONG            # 2/3
TX = config.HYST_TX_LONG            # 1/2
QO = config.HYST_Q_OPEN             # 10
QE = config.HYST_Q_EXIT             # 10
W = config.HYST_WINDOW              # 5

def _fq(x):
    return f"{x.numerator}/{x.denominator}"


# ---- 质量先验 ORDER（TK 池按此前缀切片；分数 = 波段档均分，2026-09-06 全量重算）----
# 分数口径 = run_direction 波段档（信号日→验证终点≥2 交易日）单信号均分；现役成员与榜外同口径可比。
# 榜外候选宇宙仅保留 SWAP30 要用的 财牛/拉着幸福手（top20 双榜余量最高者）。
ORDER = [
    ("一只小小牛", "1.01", "档2 波段"), ("股往金来oo", "0.96", "档2 波段"),
    ("家有高中生2", "0.91", "档2 波段"), ("故乡的云ZYH", "0.87", "档2 波段"),
    ("子房论市", "0.86", "档2 波段"), ("选对时机买对股", "0.83", "档2 波段"),
    ("香满衣", "0.78", "档2 波段"), ("知行合一", "0.63", "档2 波段"),
    ("智由智哉", "0.63", "档2 波段"), ("股评老陈", "0.60", "档2 波段"),
    ("云帆观市", "0.51", "档2 波段"), ("赵红力", "0.46", "档2 波段"),
    ("趋势巡航", "0.42", "档2 波段"), ("孙万林", "0.39", "档2 波段"),
    ("刘海娃娃", "0.39", "档2 波段"), ("大盘蜂向标", "0.38", "档2 波段"),
    ("谭阿坤", "0.37", "档2 波段"), ("白猫财眼", "0.36", "档2 波段"),
    ("禅壹", "0.36", "档2 波段"), ("爱生活的荷叶Rp", "0.35", "档2 波段"),
    ("时间轨迹", "0.32", "档2 波段"), ("四十二流光", "0.30", "档2 波段"),
    ("我觉醒了", "0.27", "档2 波段"), ("中国技术玩家", "0.26", "档2 波段"),
    ("山顶望星空的诗人", "0.26", "档2 波段"), ("衡山佛曰论股", "0.25", "档2 波段"),
    ("诸葛不亮", "0.25", "档2 波段"), ("江河之水终有入海之日", "0.22", "档2 波段"),
    ("时空鹰眼", "0.22", "档2 波段"), ("顺应周期", "0.17", "档2 波段"),
    ("财牛", "0.15", "档2 波段"), ("拉着幸福手", "0.12", "档2 波段"),
]
# 已移出候选宇宙者（不再入 ORDER / 任一池）：微风3241（已移出全部并集）、红红火火的老牛哥
# （超短专用，仅留 PANEL_SHORT）、及旧 21 时代其余未达标成员。

S0 = list(config.PANELS["swing"])       # 现役 30（import 时快照；护栏基线）


def _minus(base, *names):
    return [b for b in base if b not in names]


def _order_prefix(k):
    return [name for name, _s, _src in ORDER[:k]]


# ---- 池子表：(key, 成员名单, 构造说明) ----
POOLS = [
    ("S0", S0, "现役 30（护栏：必须复现 canonical combo_hyst.csv Q10 四行）"),
    ("X29", _minus(S0, "刘海娃娃"), "S0 ∖ 刘海娃娃（达标线外保留者一：波段胜率 48.1%<50%）"),
    ("X28", _minus(S0, "刘海娃娃", "顺应周期"),
     "S0 ∖ {刘海娃娃, 顺应周期}（= 三条件全严格达标 28 人）"),
    ("SWAP30", _minus(S0, "刘海娃娃", "顺应周期") + ["财牛", "拉着幸福手"],
     "X28 ∪ {财牛,拉着幸福手}：恒 30，榜外 top2 先验换未达标尾 2"),
    ("TK12", _order_prefix(12), "ORDER[:12]（候选宇宙质量先验降序前 12）"),
    ("TK15", _order_prefix(15), "ORDER[:15]"),
    ("TK18", _order_prefix(18), "ORDER[:18]"),
    ("TK21", _order_prefix(21), "ORDER[:21]"),
    ("TK24", _order_prefix(24), "ORDER[:24]"),
]


def _build_ctxs(index, pool, end):
    """设池 → build_contexts → (ctxs, n_clean)。end=None → 默认 END_DATE（S0 专用，自然端界）。"""
    config.PANELS["swing"] = pool
    config.WINDOW_TRADING_DAYS["swing"] = W
    if end is None:
        return daygrid.build_contexts(index)
    return daygrid.build_contexts(index, start=config.START_DATE, end=end)


def _half_excess(sim):
    """上半/下半期超额 = 各半期策略净值增长 − 同期买持。界 = 干净日序对半（len(navs)//2）。"""
    navs, closes = sim["navs"], sim["closes"]
    h = len(navs) // 2          # 动态半界（现 n_clean=149 → 74；不再硬编码 150/2=75）
    if len(navs) <= h:
        return float("nan"), float("nan")
    h1 = (navs[h] / navs[0] - 1.0) - (closes[h] / closes[0] - 1.0)
    h2 = (navs[-1] / navs[h] - 1.0) - (closes[-1] / closes[h] - 1.0)
    return h1, h2


def run_all():
    """建 temp 语料(全池成员并集) → 一个 CorpusIndex → 每池×mode 回测。

    返回 (results, s0_grid)。results[key] = {pool, size, note, ctxs, e:[...], sims:{mode:(sim,m)}}。
    s0_grid = S0 ctxs（其末日 = 公共 end）。temp 目录已清理。
    """
    tmp = tempfile.mkdtemp(prefix="combo_pool_")
    try:
        config.SIGNALS_OUT_DIR = tmp
        union = sorted({b for _k, pool, _n in POOLS for b in pool})
        config.PANELS["swing"] = union            # corpus.build 按 short|swing 并集写盘
        corpusmod.build()
        index = pollmod.CorpusIndex()

        # S0 先跑：自然端界（现役 30 干净窗）→ 公共 end
        ctxs_s0, n0 = _build_ctxs(index, S0, None)
        s0_grid = ctxs_s0
        s0_last = ctxs_s0[-1].date.isoformat()
        print(f"[pool] S0 干净窗 {ctxs_s0[0].date} → {ctxs_s0[-1].date} 共 {len(ctxs_s0)} 日（n_clean={n0}）")

        results = {}
        for key, pool, note in POOLS:
            if key == "S0":
                ctxs = ctxs_s0
            else:
                ctxs, _n = _build_ctxs(index, pool, s0_last)
            e_all = [c.expressed for c in ctxs]
            sims = {}
            for mode in rh.MODES:
                sim = hyst.simulate(ctxs, lambda cc, pos, m=mode:
                                    hyst._decide(cc.expressed, cc.bull, pos, m, QO, QE, TO, TX))
                sims[mode] = (sim, hyst.metrics(sim))
            results[key] = {"pool": pool, "key": key, "size": len(pool), "note": note,
                            "ctxs": ctxs, "e": e_all, "sims": sims}
            print(f"[pool] {key} size={len(pool)}: ctxs {len(ctxs)} 日, "
                  f"e {min(e_all)}..{max(e_all)}（均值 {sum(e_all) / len(e_all):.1f}）")
        return results, s0_grid
    finally:
        config.PANELS["swing"] = S0
        config.SIGNALS_OUT_DIR = os.path.join(config.RESEARCH_DIR, "signals")
        shutil.rmtree(tmp, ignore_errors=True)


def _near(a, b):
    return abs(float(a) - float(b)) < 1e-9


def _canonical_q10():
    """读 canonical combo_hyst.csv Q10 × long/both 两行 → 期望。"""
    path = os.path.join(REPORTS_DIR, "combo_hyst.csv")
    exp = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row["variant"] == "Q10":
                exp[row["mode_word"]] = row
    return exp


def checks(results, s0_grid):
    """S0 vs canonical、每池 149 日/date 一致、确定性双跑、§2 边界。返回 (ok, msgs)。"""
    ok, msgs = True, []
    edge_errs = hyst.assert_policy_edges()
    if edge_errs:
        ok = False
        msgs += [f"§2 边界断言：{e}" for e in edge_errs]

    s0_dates = [c.date for c in s0_grid]
    for key, r in results.items():
        dates = [c.date for c in r["ctxs"]]
        if len(dates) != len(s0_dates):
            ok, msgs = False, msgs + [f"{key}: {len(dates)} 日 ≠ S0 {len(s0_dates)} 日"]
        elif dates != s0_dates:
            bad = [(i, a, b) for i, (a, b) in enumerate(zip(dates, s0_dates)) if a != b]
            ok, msgs = False, msgs + [f"{key}: {len(bad)} 个日期 ≠ S0（首个 {bad[0]}）"]
        for mode in rh.MODES:
            navs = r["sims"][mode][0]["navs"]
            if any(x != x for x in navs):
                ok, msgs = False, msgs + [f"{key}/{mode}: navs 含 NaN"]

    # S0 vs canonical（精度 round 同 run_hyst_sweep.checks）
    exp = _canonical_q10()
    dec = {"total_return": 6, "annualized": 6, "buyhold_return": 6, "excess": 6,
           "sharpe": 4, "mdd": 6, "win_rate": 4, "avg_hold_legs": 2}
    for mode, mw in (("long", "仅做多"), ("both", "多空双向")):
        e = exp.get(mw)
        if e is None:
            ok, msgs = False, msgs + [f"canonical combo_hyst.csv 缺 Q10/{mw} 行"]
            continue
        m = results["S0"]["sims"][mode][1]
        for field, val in [("total_return", m["total_return"]), ("annualized", m["annualized"]),
                           ("buyhold_return", m["buyhold_return"]), ("excess", m["excess_vs_buyhold"]),
                           ("sharpe", m["sharpe"]), ("mdd", m["max_drawdown"]),
                           ("n_roundtrips", m["n_roundtrips"]), ("win_rate", m["win_rate"]),
                           ("avg_hold_legs", m["avg_hold_legs"])]:
            cmp = round(val, dec[field]) if field in dec else val
            if not _near(e[field], cmp):
                ok = False
                msgs.append(f"Q10/{mw} {field}: canonical {e[field]} ≠ S0 {val}")

    # 确定性双跑：S0 与 TK15 重建 ctxs 重模拟 → nav 逐位一致
    tmp = tempfile.mkdtemp(prefix="combo_pool_det_")
    try:
        config.SIGNALS_OUT_DIR = tmp
        union = sorted({b for b in results["S0"]["pool"]} | set(results["TK15"]["pool"]))
        config.PANELS["swing"] = union
        corpusmod.build()
        index = pollmod.CorpusIndex()
        end = s0_grid[-1].date.isoformat()
        for key in ("S0", "TK15"):
            pool = results[key]["pool"]
            ctxs, _ = _build_ctxs(index, pool, None if key == "S0" else end)
            for mode in rh.MODES:
                sim = hyst.simulate(ctxs, lambda cc, pos, m=mode:
                                    hyst._decide(cc.expressed, cc.bull, pos, m, QO, QE, TO, TX))
                ref = results[key]["sims"][mode][0]["navs"]
                if sim["navs"] != ref:
                    ok = False
                    msgs.append(f"确定性 ✗ {key}/{mode} navs 不一致")
    finally:
        config.PANELS["swing"] = S0
        config.SIGNALS_OUT_DIR = os.path.join(config.RESEARCH_DIR, "signals")
        shutil.rmtree(tmp, ignore_errors=True)
    return ok, msgs


# ---- 渲染 ----
def _metrics_row(key, r, mode):
    """一条 csv 行（含池级 e 统计与半期超额）。"""
    sim, m = r["sims"][mode]
    e = r["e"]
    h1, h2 = _half_excess(sim)
    return {
        "pool": key, "size": r["size"], "note": r["note"],
        "to_long": _fq(TO), "tx_long": _fq(TX), "q_open": QO, "q_exit": QE,
        "mode": rh.NAME[mode], "mode_word": rh.MODE_WORD[mode],
        "total_return": round(m["total_return"], 6), "annualized": round(m["annualized"], 6),
        "buyhold_return": round(m["buyhold_return"], 6), "excess": round(m["excess_vs_buyhold"], 6),
        "sharpe": round(m["sharpe"], 4), "bh_sharpe": round(m["bh_sharpe"], 4),
        "mdd": round(m["max_drawdown"], 6), "n_roundtrips": m["n_roundtrips"],
        "n_long_rt": m["n_long_rt"], "n_short_rt": m["n_short_rt"],
        "win_rate": round(m["win_rate"], 4) if m["win_rate"] == m["win_rate"] else "",
        "in_market_legs": m["in_market_days"], "in_market_frac": round(m["in_market"], 4),
        "avg_hold_legs": round(m["avg_hold_legs"], 2), "n_days": m["n_days"],
        "e_mean": round(sum(e) / len(e), 2), "e_gt10_frac": round(sum(1 for x in e if x > QO) / len(e), 4),
        "h1_excess": round(h1, 6), "h2_excess": round(h2, 6),
    }


def write_pool_csv(rows):
    path = os.path.join(REPORTS_DIR, "combo_hyst_pool.csv")
    rh.write_csv(path, rows, [
        "pool", "size", "note", "to_long", "tx_long", "q_open", "q_exit",
        "mode", "mode_word", "total_return", "annualized", "buyhold_return", "excess",
        "sharpe", "bh_sharpe", "mdd", "n_roundtrips", "n_long_rt", "n_short_rt",
        "win_rate", "in_market_legs", "in_market_frac", "avg_hold_legs", "n_days",
        "e_mean", "e_gt10_frac", "h1_excess", "h2_excess"])


def mode_table(results, mode):
    """md 单模式全池表 → (L 行, per_pool dict)。"""
    s0m = results["S0"]["sims"][mode][1]
    L = []
    L.append(f"### 模式：{rh.MODE_WORD[mode]}")
    L.append("")
    L.append("| 池 | 规模 | 超额 | Δvs S0 | Sharpe | Δvs S0 | MDD | 往返(L/S) | 胜率 | 在场K | 均持K | e均值 | e>10占比 | 上半超额/下半超额 |")
    L.append("|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    per = {}
    for key, r in results.items():
        sim, m = r["sims"][mode]
        h1, h2 = _half_excess(sim)
        e = r["e"]
        per[key] = m
        L.append(f"| {key} ({r['size']}) | {r['size']} | "
                 f"{rh._fmt_sign(m['excess_vs_buyhold'],2,pct=True)} | "
                 f"{rh._fmt_sign(m['excess_vs_buyhold'] - s0m['excess_vs_buyhold'],2,pct=True)} | "
                 f"{rh._fmt_sign(m['sharpe'])} | "
                 f"{rh._fmt_sign(m['sharpe'] - s0m['sharpe'])} | "
                 f"{rh._fmt(m['max_drawdown'],1,pct=True)} | "
                 f"{m['n_roundtrips']} ({m['n_long_rt']}/{m['n_short_rt']}) | "
                 f"{rh._fmt(m['win_rate'],1,pct=True)} | {m['in_market_days']} | "
                 f"{rh._fmt(m['avg_hold_legs'],1)} | {rh._fmt(sum(e) / len(e),1)} | "
                 f"{rh._fmt(100 * sum(1 for x in e if x > QO) / len(e),1)}% | "
                 f"{rh._fmt_sign(h1,2,pct=True)} / {rh._fmt_sign(h2,2,pct=True)} |")
    return L, per


def render_header(body, s0_grid, results):
    L = []
    d0, d1 = s0_grid[0].date.isoformat(), s0_grid[-1].date.isoformat()
    n = len(s0_grid)
    s0m_long = results["S0"]["sims"]["long"][1]
    bh = s0m_long["buyhold_return"]
    L.append("# 滞回策略 · 波段委员会换池：质量先验梯度 × 默认参数")
    L.append("")
    L.append(f"在 swing 波段板共识（信号=上证指数 · 交易=中证1000 · **固定公共 {n} 干净日** {d0} → {d1}）上，"
             f"参数全部锁定默认 **w{W} · Q_open=Q_exit={QO} · TO_LONG={_fq(TO)} · TX_LONG={_fq(TX)}**"
             f"（= canonical combo_hyst.md 那套），唯一自变量 = **波段投票委员会（博主池，S0=现役 30）**。"
             f"买持基准恒 {rh._fmt_sign(bh,2,pct=True)}。口径同 "
             f"[Swing_Timing.md](../../../.claude/skills/analyze-blogger/Swing_Timing.md)：每博主窗口内最新一条"
             f"波段观点（剔 spec=long），ρ=bull/e 唯一自变量；14:30 快照 → 15:00 收盘成交；long/both；"
             f"0 成本全仓 0/±1。")
    L.append("")
    L.append("## 口径与读法")
    L.append("")
    L.append(f"- 池构造依据 = 仓库 reports 质量先验（**样本内选择，见读法警示**）：现役成员与榜外统一取 "
             f"`reports/comparison_direction.md` 档2(波段) 均分（与 28 达标筛选同源同口径），"
             f"榜外仅保留 SWAP30 用到的 财牛/拉着幸福手。")
    L.append(f"- 池共 {len(POOLS)} 档（S0=现役 30 → X29/X28 逐步剔除未达标尾 2 → SWAP30 换榜外 → TK12..24 按先验 top-k），"
             f"每池 × long/both = {len(POOLS) * 2} 行。**只收语料完整覆盖成员**；固定网格内逐日 uncovered 探针全 OK"
             f"（护栏：每池 ctxs 恒 {n} 日且 date 与 S0 逐日一致）。")
    L.append("- 小池 ↔ Q10 法定人数交互：更小池 → 更多日 e≤10 → 开/平被冻 → 更少但更挑的动作"
             "（'越严越好看'的样本内表象）。下表 e>10占比 即每日表态过开/平门(>10)的天数占比。")
    L.append(f"- 上/下半超额按干净日序对半切（界 {n}//2={n // 2}，随 S0 干净窗动态）；N 扎堆某一半时暴露。")
    L.append(f"- S0 行与 canonical combo_hyst.csv Q10 逐格校验一致后才写本产物；不改 canonical combo_hyst.*、"
             f"不动 live briefing PANEL_SWING。")
    L.append("")
    return "\n".join(L) + "\n\n" + "\n".join(body) + "\n\n" + "\n".join(
        [
            "## 读法警示",
            "",
            "- 先验均分与回测落在**同一 2026 语料窗** → 用先验选池再回测 = 样本内选择，天然利好；"
            "'更好组合'需另起 OOS 评估才可信。",
            "- 单一样本（一段行情、约 7 个月干净日）+ 9 池 × 2 模式同时比较 → **多重比较风险**：最优池领先量只有 1~2 笔往返的分量。",
            "- 小池 = Q10 高门槛下的少动作 + 更挑信号，与参数扫描'平仓更早/越严越好看'同一表象，跨时段大概率均值回归。",
            "- cross-source 先验口径微差（composite 现役波段档 / comparison 档2 / top20 中期合并），仅作排序依据、非判据。",
            "- 多空双向为指数期货式线性、未计融券费/保证金；做空腿对费率敏感。",
            "- **结论措辞克制**：哪一档最好、剔除/替换方向是否单调；采纳前需另项 OOS 验证，本实验不改任何 live 配置。",
        ])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="S0 vs canonical + 确定性双跑 + §2 边界断言")
    args = ap.parse_args()
    ensure_reports()

    results, s0_grid = run_all()

    ok = True
    if args.check:
        ok, msgs = checks(results, s0_grid)
        print("[pool] 护栏" + (" ✓" if ok else " ✗"))
        for msg in msgs:
            print("[pool] ✗", msg)
    if not ok:
        print("[pool] 护栏 ✗ —— 拒写产物")
        return

    # ---- md ----
    body = []
    rows_all = []
    for mode in rh.MODES:
        L, per = mode_table(results, mode)
        s0m = per["S0"]
        items = [(k, per[k]) for k in per]
        bl = max(items, key=lambda t: t[1]["excess_vs_buyhold"])
        wo = min(items, key=lambda t: t[1]["excess_vs_buyhold"])
        better = sum(1 for k, m in items if k != "S0" and m["excess_vs_buyhold"] > s0m["excess_vs_buyhold"] + 1e-12)
        L.append("")
        L.append(f"- 超额最好 {bl[0]}（{rh._fmt_sign(bl[1]['excess_vs_buyhold'],2,pct=True)}）、最差 {wo[0]}"
                 f"（{rh._fmt_sign(wo[1]['excess_vs_buyhold'],2,pct=True)}）；"
                 f"{better}/{len(items) - 1} 池相对 S0 超额为正。")
        L.append("")
        body += L
        for key, r in results.items():
            rows_all.append(_metrics_row(key, r, mode))
    body.append("## 池构造明细")
    body.append("")
    for key, pool, note in POOLS:
        body.append(f"- **{key}**（{len(pool)} 人）：{note}  →  {'、'.join(pool)}")
    body.append("")
    body.append("## 逐月对照（both 模式，相对 S0）")
    body.append("")
    s0m_b = results["S0"]["sims"]["both"][1]
    s0mon = s0m_b["monthly"]
    non_s0 = {k: results[k]["sims"]["both"][1] for k in results if k != "S0"}
    bestk = max(non_s0, key=lambda k: non_s0[k]["excess_vs_buyhold"])
    worstk = min(non_s0, key=lambda k: non_s0[k]["excess_vs_buyhold"])
    for k, tag in ((bestk, "超额最优"), (worstk, "超额最差")):
        mon = non_s0[k]["monthly"]
        beats = [mk for mk in s0mon if mk in mon and mon[mk] > s0mon[mk] + 1e-12]
        d = non_s0[k]["excess_vs_buyhold"] - s0m_b["excess_vs_buyhold"]
        body.append(f"- **{k}**（{tag}，both，Δ超额 {rh._fmt_sign(d,2,pct=True)}）："
                    f"相对 S0 胜 {len(beats)}/{len(s0mon)} 个月"
                    + (f"（{', '.join(beats)}）" if beats else "") + "。")
    body.append("")

    md = render_header(body, s0_grid, results)
    with open(os.path.join(REPORTS_DIR, "combo_hyst_pool.md"), "w", encoding="utf-8") as f:
        f.write(md + "\n")
    write_pool_csv(rows_all)
    n = len(rows_all)
    print(f"[pool] 产物 → {REPORTS_DIR}/combo_hyst_pool.{{md,csv}}（{n} 行 = {n // 2} 池 × long/both）")


if __name__ == "__main__":
    main()
