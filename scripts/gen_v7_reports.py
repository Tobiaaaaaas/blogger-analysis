"""
Generate v7 analysis reports from report_data.json.
Auto-fills all data tables; narrative sections use templated text.
"""
import json, os, sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)

with open(os.path.join(PROJECT_ROOT, 'data', 'report_data.json')) as f:
    REPORT_DATA = json.load(f)

def pct(v, default='N/A'):
    if v is None: return default
    return f"{v:+.2f}%" if isinstance(v, float) else str(v)

def wr_pct(v, default='N/A'):
    if v is None: return default
    return f"{v:.1f}%"

def generate_report(blogger_name):
    d = REPORT_DATA[blogger_name]
    sd = d['signal_dist']
    th = d.get('blogger_time_horizon', {})
    prec = d.get('precision', {})
    pairs = d.get('pairs', {})

    # Blog info
    posts_count = d['posts_count']
    time_range = d['time_range']
    user_info = d.get('user_info', {})
    followers = user_info.get('followers', '?')

    lines = []
    lines.append(f"# {blogger_name} 大盘分析能力评估")
    lines.append("")
    lines.append(f"> 评估时间：2026-07-31 | 平台：今日头条")
    lines.append(f"> 帖子数量：{posts_count} 条 | 时间跨度：{time_range}")
    lines.append(f"> 粉丝：{followers} | 信号数量：{sd['total']} 条（计入 {sd['valid']} 条）")
    lines.append(f"> 方法论版本：v7（预测意图检测 + 时间维度加权）")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 📈 市场走势回顾")
    lines.append("")
    lines.append("评估期间（2021.03-2026.07），A股经历完整牛熊周期：从2021年震荡下跌，到2024年9月探底2690后政策牛爆发（两周内上证+36.6%），再到2025年4月关税冲击底3041后的14个月主升浪（上证+40.1%，创业板+149.3%），最终在2026年5-6月三指数先后见顶后进入调整期（上证从4259跌至3741，-12.2%）。当前处于调整期底部待确认阶段。")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 📊 信号质量分布")
    lines.append("")
    lines.append("| 分类 | 数量 | 计入评分 |")
    lines.append("|------|:----:|:--------:|")
    lines.append(f"| strong + explicit_action | {sd['strong_explicit']} | ✅ 权重2x |")
    lines.append(f"| strong + directional_clear | {sd['strong_clear']} | ✅ 权重2x |")
    lines.append(f"| moderate + explicit_action | {sd['moderate_explicit']} | ✅ 权重1x |")
    lines.append(f"| moderate + directional_clear | {sd['moderate_clear']} | ✅ 权重1x |")
    lines.append(f"| directional_vague（骑墙） | {sd['vague']} | ❌ 排除 |")
    lines.append(f"| descriptive（描述性，非预测） | {sd['descriptive']} | ❌ **v7新增** |")
    lines.append(f"| **信号总计** | **{sd['total']}** | **{sd['valid']}条计入** |")
    lines.append("")

    extract_rate = sd['total'] / posts_count * 100 if posts_count > 0 else 0
    lines.append(f"> 帖子总数：{posts_count} 条，提取信号 {sd['total']} 条（提取率 {extract_rate:.1f}%）。")

    desc_count = sd.get('descriptive', 0)
    vague_count = sd.get('vague', 0)
    if desc_count > 50:
        lines.append(f"> v7 新增 descriptive 过滤：{desc_count} 条被识别为描述性内容并排除。该博主有较多行情描述、心理按摩或自我操作记录，这些不构成方向预测。")
    elif vague_count > 100:
        lines.append(f"> v7 信号质量：{vague_count} 条为骑墙/模糊观点（{vague_count/sd['total']*100:.0f}%），说明博主在大部分时候使用模糊表述。")
    elif desc_count > 0:
        lines.append(f"> v7 新增 descriptive 过滤：{desc_count} 条被排除。")

    lines.append("")
    lines.append("---")
    lines.append("")

    # Time horizon section
    lines.append("## ⏱️ 时间维度画像")
    lines.append("")
    if th:
        dist = th.get('distribution', {})
        total_h = th.get('total', 1)
        lines.append("| 时间维度 | 信号数 | 占比 |")
        lines.append("|:---|:---:|:---:|")
        lines.append(f"| 日内 | {dist.get('intraday',0)} | {dist.get('intraday',0)/total_h*100:.0f}% |")
        lines.append(f"| 短线（1-3日） | {dist.get('short',0)} | {dist.get('short',0)/total_h*100:.0f}% |")
        lines.append(f"| 中线（1-3周） | {dist.get('medium',0)} | {dist.get('medium',0)/total_h*100:.0f}% |")
        lines.append(f"| 长线（1月+） | {dist.get('long',0)} | {dist.get('long',0)/total_h*100:.0f}% |")
        lines.append(f"| 未指定 | {dist.get('unspecified',0)} | {dist.get('unspecified',0)/total_h*100:.0f}% |")
        lines.append("")
        lines.append(f"**博主类型**：{th.get('blogger_type', '混合型')}")
        lines.append("")

    # Window decay (bullish)
    bull = prec.get('bullish', {})
    bear = prec.get('bearish', {})

    for dim_key, dim_data, dim_label in [('bullish', bull, '看多'), ('bearish', bear, '看空')]:
        if not dim_data or dim_data.get('total', 0) == 0:
            continue
        hwr = dim_data.get('horizon_weighted_win_rate')
        decay = dim_data.get('decay_pattern', '?')
        opt_win = dim_data.get('optimal_window', '?')

        wr = dim_data.get('win_rates', {})

        if dim_key == 'bullish':
            lines.append(f"**{dim_label}窗口衰减特征**：")
            lines.append("| | T+5 | T+10 | T+15 | T+20 |")
            lines.append("|:---|:---:|:---:|:---:|:---:|")
            wrs = [wr_pct(wr.get(w)) for w in ['T+5','T+10','T+15','T+20']]
            lines.append(f"| 原始胜率 | {wrs[0]} | {wrs[1]} | {wrs[2]} | {wrs[3]} |")
            lines.append(f"| 时间加权胜率 | **{wr_pct(hwr)}** | — | — | — |")
            lines.append("")
            short_pct = th.get('short_pct', 0)
            if short_pct > 60:
                lines.append(f"> 该博主 {short_pct:.0f}% 的信号属于短线判断。传统 T+20 评估在信号有效期外引入噪音。")
                lines.append(f"> **时间加权综合胜率 {wr_pct(hwr)} 更能反映真实跟随体验。**")
            elif th.get('long_pct', 0) > 10:
                lines.append(f"> 该博主有 {th.get('long_pct',0):.0f}% 长线判断，短窗口评估对其不公平。加权评分予以调整。")

    lines.append("")
    lines.append("---")
    lines.append("")

    # Bullish precision section
    for dim_key, dim_data, dim_label, risk_label in [
        ('bullish', bull, '看多/抄底', '最大回撤（买入后最惨浮亏）'),
        ('bearish', bear, '看空/逃顶', '最大踏空（卖出后最大错过）'),
    ]:
        if not dim_data or dim_data.get('total', 0) == 0:
            lines.append(f"## 📊 {dim_label}能力评估（轨道二-{'A' if dim_key == 'bullish' else 'B'}）")
            lines.append("")
            lines.append("无有效信号。")
            lines.append("")
            lines.append("---")
            lines.append("")
            continue

        lines.append(f"## 📊 {dim_label}能力评估（轨道二-{'A' if dim_key == 'bullish' else 'B'}）")
        lines.append("")
        lines.append("### 各窗口表现（整体）")
        lines.append("")
        lines.append("| 窗口 | 胜率 | 平均收益 | 均不利波动 | 不利>3%占比 | 收益/不利比 |")
        lines.append("|:----:|:----:|:--------:|:----------:|:----------:|:--------:|")

        wr = dim_data.get('win_rates', {})
        ar = dim_data.get('avg_returns', {})
        risk = dim_data.get('risk', {})
        for w in ['T+5', 'T+10', 'T+15', 'T+20']:
            r = risk.get(w, {})
            lines.append(f"| {w} | {wr_pct(wr.get(w))} | {pct(ar.get(w))} | {pct(r.get('weighted_avg_adverse',0))} | {r.get('pct_adverse_gt3',0):.0f}% | {r.get('reward_risk_ratio',0):.2f} |")

        hwr = dim_data.get('horizon_weighted_win_rate')
        hret = dim_data.get('horizon_weighted_avg_return')
        decay = dim_data.get('decay_pattern', '?')
        opt_win = dim_data.get('optimal_window', '?')
        opt_wr = dim_data.get('optimal_window_win_rate', 0)
        worst = risk.get('T+20', {}).get('worst_adverse', 0)

        lines.append("")
        lines.append(f"信号数：{dim_data['total']} | 时间加权胜率：**{wr_pct(hwr)}** | 最坏{'回撤' if dim_key == 'bullish' else '踏空'}：{pct(worst)}")
        lines.append(f"衰减模式：{decay} | 最优窗口：{opt_win}（{wr_pct(opt_wr)}）")

        # Long window stats
        lws = dim_data.get('long_window_stats')
        if lws:
            lw = lws.get('windows', {})
            if lw:
                parts = []
                for wn in ['T+40', 'T+60']:
                    if wn in lw:
                        parts.append(f"{wn} {wr_pct(lw[wn]['win_rate'])}")
                if parts:
                    lines.append(f"长线扩展窗口（{lws['signal_count']}条）：{', '.join(parts)}")

        lines.append("")

        # Strong only
        swr = dim_data.get('strong_win_rates', {})
        sar = dim_data.get('strong_avg_returns', {})
        srisk = dim_data.get('strong_risk', {})
        strong_total = dim_data.get('strong_total', dim_data.get('strong', 0))
        if strong_total > 0:
            lines.append("### 仅 strong 信号")
            lines.append("")
            lines.append("| 信号数 | T+5 | T+10 | T+15 | T+20 | T+20收益 | >3%不利 |")
            lines.append("|:------:|:---:|:----:|:----:|:----:|:--------:|:------:|")
            s20_ret = sar.get('T+20', 0)
            s20_adv = srisk.get('T+20', {}).get('pct_adverse_gt3', 0)
            lines.append(f"| {strong_total} | {wr_pct(swr.get('T+5'))} | {wr_pct(swr.get('T+10'))} | {wr_pct(swr.get('T+15'))} | {wr_pct(swr.get('T+20'))} | {pct(s20_ret)} | {s20_adv:.0f}% |")
            lines.append("")

        # Pair trades (only for bullish)
        if dim_key == 'bullish' and pairs:
            ps = pairs.get('summary', {})
            if ps:
                lines.append("### 配对交易（加仓→减仓完整周期）")
                lines.append("")
                lines.append(f"| 指标 | 数值 |")
                lines.append(f"|:-----|:-----|")
                lines.append(f"| 总交易笔数 | {pairs.get('total_trades', 0)} 笔 |")
                lines.append(f"| 胜率 | {ps.get('win_rate', 0):.1f}% |")
                lines.append(f"| 平均收益 | {pct(ps.get('avg_return', 0))} |")
                lines.append(f"| 平均持仓 | {ps.get('avg_holding_days', 0):.0f} 天 |")
                lines.append(f"| 平均最大回撤 | {pct(ps.get('avg_max_drawdown', 0))} |")
                lines.append(f"| 最大单笔盈利 | {pct(ps.get('max_gain', 0))} |")
                lines.append(f"| 最大单笔亏损 | {pct(ps.get('max_loss', 0))} |")
                pf = ps.get('profit_factor')
                lines.append(f"| 盈亏比（Profit Factor） | {pf:.2f}" if pf else "| 盈亏比（Profit Factor） | N/A |")
                lines.append("")
                op = pairs.get('open_position')
                if op:
                    lines.append(f"当前持仓：{'有' if op else '无'}" + (f"（{op.get('entry_date','')} 入场价 {op.get('entry_price',0):.1f}，浮动收益 **{pct(op.get('floating_return',0))}**）" if op else ""))
                    lines.append("")

        # Summary
        lines.append(f"### {dim_label}能力总评")
        lines.append("")

        # Generate narrative based on data
        hwr_val = hwr or 0
        if hwr_val >= 65:
            verdict = "优秀"
        elif hwr_val >= 55:
            verdict = "中等偏上，可用"
        elif hwr_val >= 45:
            verdict = "中等偏下，谨慎使用"
        else:
            verdict = "不及格，不具备跟单价值"

        if dim_key == 'bullish':
            lines.append(f"{dim_label}能力{verdict}。")
        else:
            lines.append(f"{dim_label}能力{verdict}。")

        if decay == 'rising':
            lines.append(f"衰减模式为 rising——信号随持仓时间延长胜率提升，说明博主更适合中线持有而非短线交易。最优窗口为 {opt_win}（胜率 {wr_pct(opt_wr)}）。")
        elif decay == 'falling':
            lines.append(f"衰减模式为 falling——T+5 胜率最高，随持仓延长衰减。博主是典型的短线交易者，超过 {opt_win} 后信号有效性下降。")
        elif decay == 'hump':
            lines.append(f"衰减模式为 hump——在 {opt_win} 最佳，过早或过晚都会降低胜率。适合在最优窗口附近跟随。")

        if short_pct := th.get('short_pct', 0) > 60 and decay == 'rising':
            lines.append(f"有趣的是，虽然 {short_pct:.0f}% 的信号是短线判断，但胜率反而随时间提升——说明博主的短线择时准确度一般，但当其判断与趋势共振时，中长期表现更好。")

        lines.append("")
        lines.append("---")
        lines.append("")

    # Feature analysis
    lines.append("## 💡 博主特征分析")
    lines.append("")
    lines.append(f"- **分析风格**：参照 v6 报告。")
    lines.append(f"- **信号频率特征**：{posts_count} 条帖子中提取 {sd['total']} 条信号（{extract_rate:.1f}%），有效 {sd['valid']} 条。")

    if desc_count > 50:
        lines.append(f"- **信号克制度（v7）**：{desc_count} 条描述性内容被过滤（{desc_count/sd['total']*100:.0f}%），说明博主有较多非预测性内容（心理按摩、行情描述、自我记录）。有效信号 {sd['valid']} 条。")
    if vague_count > 50:
        lines.append(f"- **模糊度（v7）**：{vague_count} 条 vague 信号（{vague_count/sd['total']*100:.0f}%），{'极高' if vague_count/sd['total'] > 0.3 else '较高'}——博主在{'大部分' if vague_count/sd['total'] > 0.3 else '相当一部分'}时候使用骑墙/模糊表述。")

    if th:
        lines.append(f"- **时间维度特征（v7）**：{th.get('blogger_type', '混合型')}。短线（日内+short）占比 {th.get('short_pct',0):.0f}%，长线占比 {th.get('long_pct',0):.0f}%。")

    bull_wr = bull.get('horizon_weighted_win_rate', 0) if bull else 0
    bear_wr = bear.get('horizon_weighted_win_rate', 0) if bear else 0
    if bull_wr and bear_wr:
        if bull_wr > 55 and bear_wr < 45:
            lines.append(f"- **看多 vs 看空的行为差异**：看多加权胜率 {wr_pct(bull_wr)} 显著优于看空 {wr_pct(bear_wr)}。博主的价值在于抄底/看多方向，看空信号应忽略。")
        elif bear_wr > 55 and bull_wr < 45:
            lines.append(f"- **看多 vs 看空的行为差异**：看空加权胜率 {wr_pct(bear_wr)} 显著优于看多 {wr_pct(bull_wr)}。博主的价值在于逃顶/看空方向，看多信号应忽略。")
        elif bull_wr < 45 and bear_wr < 45:
            lines.append(f"- **看多 vs 看空的行为差异**：两个方向均不及格（看多 {wr_pct(bull_wr)}，看空 {wr_pct(bear_wr)}），不建议在任何方向跟单。")
        else:
            lines.append(f"- **看多 vs 看空的行为差异**：看多 {wr_pct(bull_wr)}，看空 {wr_pct(bear_wr)}。")

    lines.append("")
    lines.append("---")
    lines.append("")

    # Highlights / Weaknesses
    lines.append("## 🏆 亮点 / ⚠️ 短板")
    lines.append("")
    lines.append("### 看多/抄底")
    if bull:
        if bull_wr and bull_wr >= 55:
            lines.append(f"**亮点**：时间加权胜率 {wr_pct(bull_wr)}，在{'短线' if bull.get('decay_pattern') == 'falling' else '中长期'}窗口有一定跟随价值。")
        else:
            lines.append(f"**短板**：时间加权胜率仅 {wr_pct(bull_wr)}，不具备作为跟单信号的基础。")
    lines.append("")
    lines.append("### 看空/逃顶")
    if bear:
        if bear_wr and bear_wr >= 55:
            lines.append(f"**亮点**：时间加权胜率 {wr_pct(bear_wr)}，在{'短线' if bear.get('decay_pattern') == 'falling' else '中长期'}窗口有跟随价值。")
        else:
            lines.append(f"**短板**：时间加权胜率仅 {wr_pct(bear_wr)}，不具备作为跟单信号的基础。")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Current views placeholder
    lines.append("## 🔭 当前观点")
    lines.append("")
    lines.append("（参照 v6 报告中的最新观点。当前观点与评估数据无关，暂不更新。）")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Overall evaluation
    lines.append("## ⚖️ 总体评价")
    lines.append("")

    if bull_wr:
        lines.append(f"- **看多/抄底**：时间加权胜率 {wr_pct(bull_wr)}，衰减 {bull.get('decay_pattern','?')}，最优窗口 {bull.get('optimal_window','?')}。{'建议在最优窗口内跟随 strong 信号。' if bull_wr >= 55 else '建议谨慎或不跟。'}")
    if bear_wr:
        lines.append(f"- **看空/逃顶**：时间加权胜率 {wr_pct(bear_wr)}，衰减 {bear.get('decay_pattern','?')}，最优窗口 {bear.get('optimal_window','?')}。{'建议在最优窗口内跟随 moderate 信号。' if bear_wr >= 55 else '建议谨慎或不跟。'}")

    lines.append(f"- **适合什么类型的跟随者**：{'短线交易者' if th.get('blogger_type','') == '短线交易型' else '中线持有者'}，关注博主最擅长的方向和时间窗口。")
    lines.append("")

    return '\n'.join(lines)


def main():
    # Generate for bloggers that don't have v7 reports yet
    # Skip TL阳光 and 顺应周期 (already done by agents)
    skip = {'TL阳光', '顺应周期'}

    for name in sorted(REPORT_DATA.keys()):
        if name in skip:
            continue
        print(f"Generating {name}...")
        report = generate_report(name)
        out_path = os.path.join(PROJECT_ROOT, 'reports', f'{name}_analysis.md')
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(report)
        print(f"  → {out_path} ({len(report)} chars)")


if __name__ == '__main__':
    main()
