"""
Generate quantitative sections for v10 analysis reports.
Reads signal/score/post data and outputs the data-heavy sections.
The qualitative sections are filled by report agents.

Usage: python scripts/gen_report_data.py --blogger 大盘蜂向标
"""

import json
import os
import sys
import argparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))

INFLECTION_INFO = {
    "M2": ("2024-10-08", "顶部", "Major", 3674),
    "I1": ("2024-10-18", "底部", "Intermediate", 3153),
    "I2": ("2024-11-08", "顶部", "Intermediate", 3510),
    "I3": ("2024-11-27", "底部", "Intermediate", 3227),
    "I4": ("2024-12-10", "顶部", "Intermediate", 3495),
    "I5": ("2025-01-13", "底部", "Intermediate", 3141),
    "I6": ("2025-03-19", "顶部", "Intermediate", 3439),
    "M3": ("2025-04-07", "底部", "Major", 3041),
    "M4": ("2025-11-14", "顶部", "Major", 4034),
    "I9": ("2025-12-16", "底部", "Intermediate", 3816),
    "I10": ("2026-01-14", "顶部", "Intermediate", 4191),
    "I11": ("2026-02-03", "底部", "Intermediate", 4003),
    "I12": ("2026-03-03", "顶部", "Intermediate", 4197),
    "M5": ("2026-03-23", "底部", "Major", 3795),
    "M6": ("2026-05-14", "顶部", "Major", 4259),
    "I13": ("2026-06-08", "底部", "Intermediate", 3928),
    "I14": ("2026-06-23", "顶部", "Intermediate", 4175),
    "M8": ("2026-07-20", "底部", "Major", 3741),
    "M7": ("2026-06-25", "顶部", "Major", 4380),
}


def load_json(*parts):
    path = os.path.join(PROJECT_ROOT, *parts)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def gen_post_breakdown(sig_d):
    total = sig_d.get("total_posts", 0)
    pre = sig_d.get("pre_2024_06", 0)
    evals = sig_d.get("evaluation_posts", total - pre)
    ne = sig_d.get("not_extracted", {})
    ne_total = sum(ne.values())
    sig_count = len(sig_d.get("signals", []))
    rate = sig_count / evals * 100 if evals > 0 else 0
    sb = sig_d.get("scored_bullish", {})
    sk = sig_d.get("scored_bearish", {})
    bull = sum(sb.values())
    bear = sum(sk.values())
    strong = sb.get("strong", 0) + sk.get("strong", 0)
    moderate = sb.get("moderate", 0) + sk.get("moderate", 0)
    long_s = sb.get("long", 0) + sk.get("long", 0)
    medium_s = sb.get("medium", 0) + sk.get("medium", 0)
    short_s = sb.get("short", 0) + sk.get("short", 0)
    intraday_s = sb.get("intraday", 0) + sk.get("intraday", 0)
    unspecified_s = sb.get("unspecified", 0) + sk.get("unspecified", 0)

    def pct(a, b):
        return a / b * 100 if b > 0 else 0

    lines = []
    lines.append("### 帖子数量逐层拆解")
    lines.append("")
    lines.append("```")
    lines.append(f"帖子总数：{total} 条")
    if pre > 0:
        lines.append(f"  ├── 2024年6月之前：{pre} 条（不纳入评估）")
        lines.append(f"  └── 评估期帖子（2024-06 起）：{evals} 条")
    else:
        lines.append(f"  └── 评估期帖子（均为 2024-06 之后）：{evals} 条")
    lines.append(f"        ├── ❌ 不提取为信号：{ne_total} 条（{pct(ne_total, evals):.1f}%）")
    lines.append(f"        │     ├── 无市场话题（纯生活/娱乐/社会新闻等）：{ne.get('no_market_topic', 0)} 条")
    lines.append(f"        │     ├── 仅描述行情（回顾走势、总结，无方向判断）：{ne.get('pure_description', 0)} 条")
    lines.append(f'        │     ├── 方向模糊/骑墙（"可能涨也可能跌"类摇摆表态）：{ne.get("directional_vague", 0)} 条')
    lines.append(f"        │     ├── 仅针对板块/个股/其他指数（未提及上证/大盘）：{ne.get('other_index_sector', 0)} 条")
    lines.append(f"        │     └── 其他（条件未触发、纯转发、心理按摩等）：{ne.get('other', 0)} 条")
    lines.append(f"        └── ✅ 提取为信号：{sig_count} 条（提取率 {rate:.1f}%）")
    lines.append(f"              ├── 📈📉 按方向拆解：")
    lines.append(f"              │     ├── 看多(bullish)：{bull} 条（{pct(bull, sig_count):.1f}%）")
    lines.append(f"              │     └── 看空(bearish)：{bear} 条（{pct(bear, sig_count):.1f}%）")
    lines.append(f"              ├── 💪 按强度拆解：")
    lines.append(f"              │     ├── strong：{strong} 条（{pct(strong, sig_count):.1f}%）")
    lines.append(f"              │     └── moderate：{moderate} 条（{pct(moderate, sig_count):.1f}%）")
    lines.append(f"              └── ⏱️ 按时间跨度拆解：")
    lines.append(f"                    ├── long（月级以上）：{long_s} 条")
    lines.append(f"                    ├── medium（数周-月）：{medium_s} 条")
    lines.append(f"                    ├── short（1-2天）：{short_s} 条")
    lines.append(f"                    ├── intraday（日内）：{intraday_s} 条")
    lines.append(f"                    └── unspecified（无明确时间范围）：{unspecified_s} 条")
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def gen_segment_table(score_d):
    segs = score_d.get("segments", {})
    lines = []
    lines.append("### 线段总览")
    lines.append("")
    lines.append("| 线段 | 起止 | 方向 | 信号数 | 看多 | 看空 | 得分 |")
    lines.append("|:---|:---|:---:|:---:|:---:|:---:|:---:|")
    for sk, sv in segs.items():
        sl = sv.get("start_label", "?")
        el = sv.get("end_label", "?")
        si = INFLECTION_INFO.get(sl, ("?", "?", "?", 0))
        ei = INFLECTION_INFO.get(el, ("?", "?", "?", 0))
        sp = si[3] if si[3] else "?"
        ep = ei[3] if ei[3] else "?"
        sd = "↑ 上升" if sv["direction"] == "rising" else "↓ 下降"
        lines.append(f"| {sl}→{el} | {sp}→{ep} | {sd} | {sv['total']} | {sv['bull']} | {sv['bear']} | {sv['score']:+.2f} |")
    lines.append("")
    return "\n".join(lines)


def gen_14scores(score_d):
    s = score_d.get("scores", {})
    lines = []
    lines.append("### 十四分数")
    lines.append("")
    lines.append("| 维度 | 总得分 | 信号数量 | 平均分 |")
    lines.append("|------|--------|:---:|------|")
    for dim in ["综合", "上升段", "下降段", "抄底", "逃顶", "看多", "看空"]:
        d = s.get(dim, {})
        lines.append(f"| {dim} | {d.get('total', 0):+.2f} | {d.get('count', 0)} | {d.get('avg_pct', 0):+.2f}% |")
    lines.append("")
    return "\n".join(lines)


def gen_inflection_table(score_d):
    inf_d = score_d.get("inflection_details", {})
    lines = []
    lines.append("## 🔴 关键拐点逐点分析")
    lines.append("")
    lines.append("| 拐点 | 日期 | 类型 | 信号数 | 总得分 | 平均分 | 代表性信号 |")
    lines.append("|:---|:---|:---|:---:|:---:|:---:|:---|")

    covered = set()
    for label in sorted(inf_d.keys()):
        info = INFLECTION_INFO.get(label, ("?", "?", "?", 0))
        date = info[0]
        itype = info[1]
        d = inf_d[label]
        sigs = d.get("signals", [])
        total = d.get("total_score", 0)
        count = d.get("signal_count", 0)
        avg = d.get("avg_score_pct", 0)
        sorted_sigs = sorted(sigs, key=lambda x: x.get("score", 0), reverse=True)
        rep_parts = []
        if sorted_sigs:
            best = sorted_sigs[0]
            d_arrow = "↑" if best.get("direction") == "bullish" else "↓"
            s_str = best.get("strength", "moderate")
            ev = best.get("evidence", "")[:50]
            rep_parts.append(f'"{ev}…"（{s_str} {d_arrow}，得分 {best.get("score", 0):+.3f}）')
        if len(sorted_sigs) > 1:
            worst = sorted_sigs[-1]
            if worst != sorted_sigs[0]:
                d_arrow = "↑" if worst.get("direction") == "bullish" else "↓"
                s_str = worst.get("strength", "moderate")
                ev = worst.get("evidence", "")[:50]
                rep_parts.append(f'"{ev}…"（{s_str} {d_arrow}，得分 {worst.get("score", 0):+.3f}）')
        rep = "<br>".join(rep_parts) if rep_parts else "—"
        lines.append(f"| {label} | {date} | {itype} | {count} | {total:+.2f} | {avg:+.1f}% | {rep} |")
        covered.add(label)

    all_major = ["M2", "M3", "M4", "M5", "M6", "M7", "M8"]
    uncovered = [m for m in all_major if m not in covered]
    if uncovered:
        lines.append("")
        lines.append(f"> ⚠️ 以下 Major 拐点无信号覆盖（博主未在 ±1 天内表态）：{', '.join(uncovered)}")
    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--blogger", required=True)
    args = parser.parse_args()
    blogger = args.blogger

    sig_d = load_json("data", "signals", f"{blogger}.json")
    # Try _v11 first, fall back to non-suffixed
    score_path = os.path.join(PROJECT_ROOT, "data", "scores", f"{blogger}_v11.json")
    if not os.path.exists(score_path):
        score_path = os.path.join(PROJECT_ROOT, "data", "scores", f"{blogger}.json")
    with open(score_path, encoding="utf-8") as f:
        score_d = json.load(f)
    posts_d = load_json("data", "posts", f"{blogger}.json")

    ui = posts_d.get("user_info", {})
    followers = ui.get("followers", ui.get("fans", "?")) if isinstance(ui, dict) else "?"
    tr = posts_d.get("time_range", {})
    earliest = tr.get("earliest", "?") if isinstance(tr, dict) else "?"
    latest = tr.get("latest", "?") if isinstance(tr, dict) else "?"
    total_posts = posts_d.get("total_posts", len(posts_d.get("posts", [])))
    sig_count = len(sig_d.get("signals", []))

    s = score_d.get("scores", {})

    report = []
    report.append(f"# {blogger} 大盘分析能力评估")
    report.append("")
    report.append(f"> 评估时间：2026-08-03 | 平台：今日头条")
    report.append(f"> 帖子数量：{total_posts} 条 | 时间跨度：{earliest} ~ {latest}")
    report.append(f"> 粉丝：{followers} | 信号数量：{sig_count} 条")
    report.append(f"> 方法论版本：v11（LLM全量读取 + A/B/C/D 双因子打分，3日均价 short_return）")
    report.append("")
    report.append("---")
    report.append("")
    report.append("## 📈 市场走势回顾")
    report.append("")
    report.append(f"博主覆盖期：{earliest} ~ {latest}。上证在此期间的完整 zigzag 拐点链（M1-M8, I1-I15）见 `knowledge/market_analysis.md`。")
    report.append("")
    report.append(gen_segment_table(score_d))
    report.append("---")
    report.append("")
    report.append("## 📊 信号质量分布")
    report.append("")
    report.append(gen_post_breakdown(sig_d))
    report.append("### 信号分布特点")
    report.append("")
    report.append("<!-- LLM_ANALYSIS: 1段话概括 -->")
    report.append("")
    report.append("---")
    report.append("")
    report.append("## 📊 拐点线段评估")
    report.append("")
    report.append(gen_14scores(score_d))
    report.append(gen_inflection_table(score_d))
    report.append("<!-- LLM_ANALYSIS: 对每个有信号的拐点写1-2句定性分析，引用原文证据。对无覆盖的重要拐点也要提及。 -->")
    report.append("")
    report.append("---")
    report.append("")
    report.append("## 💡 博主特征分析")
    report.append("")
    report.append("<!-- LLM_ANALYSIS: 分析风格（技术/基本/情绪面）、信号频率、信号克制度、框架可证伪性、看多vs看空行为差异、strong vs moderate差异、关键线段中态度一致性 -->")
    report.append("")
    report.append("---")
    report.append("")
    report.append("## 🏆 亮点 / ⚠️ 短板")
    report.append("")
    report.append("### 看多/抄底")
    report.append("**亮点**：<!-- LLM_ANALYSIS: 1-2个 -->")
    report.append("")
    report.append("**短板**：<!-- LLM_ANALYSIS: 1个 -->")
    report.append("")
    report.append("### 看空/逃顶")
    report.append("**亮点**：<!-- LLM_ANALYSIS: 1-2个 -->")
    report.append("")
    report.append("**短板**：<!-- LLM_ANALYSIS: 1个 -->")
    report.append("")
    report.append("---")
    report.append("")
    report.append("## 🔭 当前观点（截至最新帖子日期）")
    report.append("")
    report.append("<!-- LLM_ANALYSIS: 从最新3-5条帖子提取博主的短期/中期/长期市场观点、仓位建议、关键点位 -->")
    report.append("")
    report.append("---")
    report.append("")
    report.append("## ⚖️ 总体评价")
    report.append("")
    report.append(f"- **看多/抄底**：看多得分 {s.get('看多',{}).get('total',0):+.2f}（{s.get('看多',{}).get('count',0)} 条，平均 {s.get('看多',{}).get('avg_pct',0):+.2f}%），抄底得分 {s.get('抄底',{}).get('total',0):+.2f}（{s.get('抄底',{}).get('count',0)} 条，平均 {s.get('抄底',{}).get('avg_pct',0):+.2f}%）。<!-- LLM_ANALYSIS -->")
    report.append(f"- **看空/逃顶**：看空得分 {s.get('看空',{}).get('total',0):+.2f}（{s.get('看空',{}).get('count',0)} 条，平均 {s.get('看空',{}).get('avg_pct',0):+.2f}%），逃顶得分 {s.get('逃顶',{}).get('total',0):+.2f}（{s.get('逃顶',{}).get('count',0)} 条，平均 {s.get('逃顶',{}).get('avg_pct',0):+.2f}%）。<!-- LLM_ANALYSIS -->")
    report.append(f"- **综合**：综合得分 {s.get('综合',{}).get('total',0):+.2f}（{s.get('综合',{}).get('count',0)} 条，平均 {s.get('综合',{}).get('avg_pct',0):+.2f}%），上升段 {s.get('上升段',{}).get('total',0):+.2f}，下降段 {s.get('下降段',{}).get('total',0):+.2f}。<!-- LLM_ANALYSIS -->")
    report.append(f"- **适合什么类型的跟随者**：<!-- LLM_ANALYSIS -->")
    report.append("")

    out_path = os.path.join(PROJECT_ROOT, "reports", "bloggers", f"{blogger}_analysis.md")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report))
    print(f"✅ {blogger}: {len(report)} lines → {out_path}")


if __name__ == "__main__":
    main()
