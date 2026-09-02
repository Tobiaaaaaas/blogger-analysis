# -*- coding: utf-8 -*-
"""
全部博主 Direction 横向对比（对齐 SKILL 769fe57：只保留各榜单）
用法: python scripts/eval/comparison_all.py
输出: reports/comparison_direction.md

结构：参与打分博主清单 → 总榜 → 三档分榜（含"其中：今天（盘前/盘中）"子榜）→ 方向榜单（看多/看空）→ 无周期方向榜 → 覆盖/集中度警告

排名资格（SKILL §横向对比）：
- 仅纳入参与打分的博主（帖子跨度≥6 月 且 2026 以来信号>50）；不参与打分者单列注明
- 总榜口径 = 综合（显式周期 + 无周期方向 nd 合并）；三档 / 方向榜单口径 = 显式周期信号（spec ≠ nd）
- 总榜：综合计分信号（含 nd）≥ 30 条 且 综合平均分 > 0；按 t 值（= 平均分/标准误）降序；不足者不参与排名、单列注明（统计无意义）
- 三档分榜 / 方向榜单 / 无周期方向榜：该组信号 ≥ 10 条 且 平均分 > 0.1，仅取前 10 名上榜；表格同总榜形式
- 方向榜单：看多=d=1、看空=d=-1；子集方向固定故不含看多/看空列
- 无周期方向榜：spec=nd（无明确时间点，未来 5 个交易日收益率单点计分），独立排名；nd 同时并入总榜综合口径
"""
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_direction as eng

RANK_MIN_SIGNALS = 30      # 总榜排名资格：计分信号 ≥ 30 条
BUCKET_MIN_SIGNALS = 10    # 三档/方向榜单资格：该组信号 ≥ 10 条
TOP_N = 10                 # 三档/方向榜单仅取前 10 名上榜
AVG_MIN = 0.1              # 分榜（三档/方向/无周期方向榜）资格：平均分 > 0.1（统计无意义者不上榜）
RANK_AVG_MIN = 0.0         # 总榜资格：综合平均分 > 0（2026-09-01 放宽口径）

# 跳过 _ 前缀文件：_<名>_run.json 是提取脚本的 gitignored 运行溯源（signals 为 int 计数），非信号文件
ALL_BLOGGERS = sorted(f[:-5] for f in os.listdir(eng.DATA_DIR) if f.endswith('.json') and not f.startswith('_'))

rows_all, meta = {}, {}
for b in ALL_BLOGGERS:
    data = json.load(open(os.path.join(eng.DATA_DIR, f'{b}.json'), encoding='utf-8'))
    scored = [r for r in (eng.calc(s) for s in data['signals']) if r['score'] is not None]
    rows_all[b] = scored
    meta[b] = {'signals': len(data['signals']), 'scored': len(scored)}


def explicit(b):
    """显式周期信号（spec ≠ nd）：总榜/三档/方向榜单口径（SKILL §8）"""
    return [r for r in rows_all[b] if r['spec'] != 'nd']


def ndrows(b):
    """无周期方向信号（spec=nd，未来 5 个交易日收益率单点计分）：独立「无周期方向榜」"""
    return [r for r in rows_all[b] if r['spec'] == 'nd']


def allrows(b):
    """综合口径 = 显式周期 + 无周期方向 nd 合并：总榜（按 t 值排序）"""
    return rows_all[b]


# 参与打分资格（跨度≥6月 且 2026以来信号>50）：仅合格者进入榜单，不合格者单列注明
ELIGIBLE, INELIGIBLE = [], []
for b in ALL_BLOGGERS:
    ok, span, n = eng.eligibility(b)
    (ELIGIBLE if ok else INELIGIBLE).append(b)

BUCKET_KEYS = ['0-1个交易日（今天/明天）', '2-5个交易日（1周内）', '6个交易日及以上（大于1周）']

# 分榜分组：每个合格博主 → 各档显式周期信号 / 今天（盘前/盘中）信号（nd 独立，不进三档分组）
bucket_rows = {k: {b: [] for b in ELIGIBLE} for k in BUCKET_KEYS}
today_rows = {b: [] for b in ELIGIBLE}
for b in ELIGIBLE:
    for r in explicit(b):
        bucket_rows[eng.bucket_of(r)][b].append(r)
        if r['spec'] == 'today':
            today_rows[b].append(r)


def qualifies(b, getter, min_n, min_avg=AVG_MIN):
    """排名资格：该组信号 ≥ min_n 条 且 平均分 > min_avg（总榜用 RANK_AVG_MIN，分榜默认 AVG_MIN）"""
    rs = getter(b)
    return len(rs) >= min_n and eng.avg_of(rs) > min_avg


def acc_of(rs):
    return eng.acc_of(rs)[2]


def bull_bear_txt(rs):
    return f'{sum(1 for r in rs if r["d"] == 1)}', f'{sum(1 for r in rs if r["d"] == -1)}'


def sharpe_txt(rs):
    """夏普单元格文本；<2 条信号或波动率为 0 → '—'"""
    sh = eng.sharpe_of(rs)
    return f'{sh:+.2f}' if sh is not None else '—'


def tstat_txt(rs):
    """t 值单元格文本（总榜用）；<2 条信号或波动率为 0 → '—'"""
    t = eng.t_stat_of(rs)
    return f'{t:+.2f}' if t is not None else '—'


def fmt_row(i, b, rs):
    bull, bear = bull_bear_txt(rs)
    return (f'| {i} | {b} | {len(rs)} | {acc_of(rs):.1f}% | **{eng.avg_of(rs):+.2f}** | '
            f'{eng.vol_of(rs):.2f} | {sharpe_txt(rs)} | {bull} | {bear} |')


def fmt_row_t(i, b, rs):
    """总榜行（综合口径，含 t 值列）"""
    bull, bear = bull_bear_txt(rs)
    return (f'| {i} | {b} | {len(rs)} | {acc_of(rs):.1f}% | **{tstat_txt(rs)}** | **{eng.avg_of(rs):+.2f}** | '
            f'{eng.vol_of(rs):.2f} | {sharpe_txt(rs)} | {bull} | {bear} |')


def fmt_row_dir(i, b, rs):
    return (f'| {i} | {b} | {len(rs)} | {acc_of(rs):.1f}% | **{eng.avg_of(rs):+.2f}** | '
            f'{eng.vol_of(rs):.2f} | {sharpe_txt(rs)} |')


def emit_leaderboard(title, getter):
    """三档/方向榜单：该组 ≥10 条 且 均分>0.1，仅取前 10 名；表格同总榜形式"""
    L.append(f'### {title}')
    L.append('')
    L.append('| 排名 | 博主 | 信号数 | 正确率 | **平均分** | 波动率 | 夏普 | 看多 | 看空 |')
    L.append('|:---:|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|')
    in_rank = [b for b in ELIGIBLE if qualifies(b, getter, BUCKET_MIN_SIGNALS)]
    in_rank.sort(key=lambda b: -eng.avg_of(getter(b)))
    top = in_rank[:TOP_N]
    if not top:
        L.append('| — | 无符合资格博主（该组信号 < 10 条 或 平均分 ≤ 0.1） | | | | | | | |')
    for i, b in enumerate(top, 1):
        L.append(fmt_row(i, b, getter(b)))
    L.append('')
    below = [b for b in ELIGIBLE if b not in top and len(getter(b)) >= 5]
    others = [b for b in ELIGIBLE if b not in top and 0 < len(getter(b)) < 5]
    notes = []
    if below:
        notes.append('、'.join(f'{b}（{len(getter(b))} 条）' for b in below) + ' 未达 10 条/均分≤0.1 未上榜')
    if others:
        notes.append(f'另 {len(others)} 位该组信号 < 5 条')
    if notes:
        L.append('> ' + '；'.join(notes))
        L.append('')


def emit_dir_leaderboard(title, pred):
    """方向/无周期榜单：看多=d=1 / 看空=d=-1 / 无周期方向 nd；子集方向固定或独立档故不含看多/看空列"""
    L.append(f'### {title}')
    L.append('')
    L.append('| 排名 | 博主 | 信号数 | 正确率 | **平均分** | 波动率 | 夏普 |')
    L.append('|:---:|:---|:---:|:---:|:---:|:---:|:---:|')
    in_rank = [b for b in ELIGIBLE if qualifies(b, pred, BUCKET_MIN_SIGNALS)]
    in_rank.sort(key=lambda b: -eng.avg_of(pred(b)))
    top = in_rank[:TOP_N]
    if not top:
        L.append('| — | 无符合资格博主（该组信号 < 10 条 或 平均分 ≤ 0.1） | | | | |')
    for i, b in enumerate(top, 1):
        L.append(fmt_row_dir(i, b, pred(b)))
    L.append('')
    below = [b for b in ELIGIBLE if b not in top and len(pred(b)) >= 5]
    others = [b for b in ELIGIBLE if b not in top and 0 < len(pred(b)) < 5]
    notes = []
    if below:
        notes.append('、'.join(f'{b}（{len(pred(b))} 条）' for b in below) + ' 未达 10 条/均分≤0.1 未上榜')
    if others:
        notes.append(f'另 {len(others)} 位该组信号 < 5 条')
    if notes:
        L.append('> ' + '；'.join(notes))
        L.append('')


L = []
L.append('# 全部博主 Direction 横向对比（平均分 = 单信号平均收益 %；总榜综合口径按 t 值排序）')
L.append('')
L.append(f'> 数据截止 {eng.EVAL_DATE} | 参与打分博主 {len(ELIGIBLE)} 位（帖子跨度≥6 月 且 2026 以来信号>50）| '
         f'总榜资格：综合计分信号（显式周期 + 无周期方向 nd）≥ {RANK_MIN_SIGNALS} 条 且 综合平均分 > 0，按 t 值排序 | '
         f'分榜资格：该组 ≥ {BUCKET_MIN_SIGNALS} 条 且 平均分 > {AVG_MIN}（仅取前 {TOP_N}）| '
         f'无周期方向（nd，无明确时间点）并入总榜综合口径；三档/方向榜单仍为显式周期信号口径')
L.append('')
if INELIGIBLE:
    parts = []
    for b in INELIGIBLE:
        ok, span, n = eng.eligibility(b)
        parts.append(f'{b}（跨度 {span:.1f} 月，2026 信号 {n} 条）')
    L.append(f'> **不参与打分与排名**（帖子跨度<6 月 或 2026 以来信号≤50，共 {len(INELIGIBLE)} 位）：'
             + '、'.join(parts))
    L.append('')

# ── 总榜（口径=综合：显式周期 + 无周期方向 nd 合并，按 t 值排序）──
L.append('## 🏆 总榜（综合口径 = 显式周期 + 无周期方向 nd，按 t 值排序；综合计分信号 ≥ 30 条 且 综合平均分 > 0 才参与排名）')
L.append('')
L.append('| 排名 | 博主 | 计分信号 | 正确率 | **t 值** | **综合均分** | 波动率 | 夏普 | 看多 | 看空 |')
L.append('|:---:|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|')
ranked = [b for b in ELIGIBLE if qualifies(b, allrows, RANK_MIN_SIGNALS, RANK_AVG_MIN)]
ranked.sort(key=lambda b: -(eng.t_stat_of(rows_all[b]) or -999.0))
for i, b in enumerate(ranked, 1):
    L.append(fmt_row_t(i, b, rows_all[b]))
L.append('')
excluded = [b for b in ELIGIBLE if not qualifies(b, allrows, RANK_MIN_SIGNALS, RANK_AVG_MIN)]
if excluded:
    L.append(f'> 不参与总榜排名（综合计分信号 < {RANK_MIN_SIGNALS} 或 综合平均分 ≤ 0，统计无意义）：'
             + '、'.join(f'{b}（{len(rows_all[b])} 条，综合均分 {eng.avg_of(rows_all[b]):+.2f}）' for b in excluded))
    L.append('')

# ── 三档分榜（口径=显式周期信号，nd 独立成档不混入）──
L.append('## 🏅 三档分榜（每档 ≥ 10 条 且 平均分 > 0.1 参与排名，仅取前 10 名，表格同总榜；无周期方向 nd 独立成档见下方）')
L.append('')
emit_leaderboard(f'档 1：{BUCKET_KEYS[0]}', lambda b: bucket_rows[BUCKET_KEYS[0]][b])
emit_leaderboard('档 1 子榜：其中：今天（盘前/盘中）', lambda b: today_rows[b])
emit_leaderboard(f'档 2：{BUCKET_KEYS[1]}', lambda b: bucket_rows[BUCKET_KEYS[1]][b])
emit_leaderboard(f'档 3：{BUCKET_KEYS[2]}', lambda b: bucket_rows[BUCKET_KEYS[2]][b])

# ── 方向榜单（口径=显式周期信号）──
L.append('## 🎯 方向榜单（看多=d=1 / 看空=d=-1；≥ 10 条 且 平均分 > 0.1，仅取前 10 名；口径=显式周期信号）')
L.append('')
emit_dir_leaderboard('看多榜（只看多信号）', lambda b: [r for r in explicit(b) if r['d'] == 1])
emit_dir_leaderboard('看空榜（只看空信号）', lambda b: [r for r in explicit(b) if r['d'] == -1])

# ── 无周期方向榜（spec=nd，独立档）──
L.append('## 🕐 无周期方向榜（无明确时间点，未来 5 个交易日收益率单点计分；≥ 10 条 且 平均分 > 0.1，仅取前 10 名）')
L.append('')
emit_dir_leaderboard('无周期方向榜（无明确时间点，独立档）', ndrows)

# ── 覆盖/集中度警告 ──
L.append('## ⚠️ 信号覆盖 / 集中度警告（覆盖 <3 个月 ⚠️；单月占比 ≥50% 高度集中、≥33% 轻度集中；相邻月份缺 ≥3 个月为严重缺口）')
L.append('')
for b in ELIGIBLE:
    rs = rows_all[b]
    if not rs:
        L.append(f'- **{b}**：0 条可打分信号')
        continue
    bymo = defaultdict(list)
    for r in rs:
        bymo[r['pub'][:7]].append(r)
    first_d = min(r['pub'][:10] for r in rs)
    last_d = max(r['pub'][:10] for r in rs)
    top_mo, top_rs = max(bymo.items(), key=lambda kv: len(kv[1]))
    top_n = len(top_rs)
    conc = top_n / len(rs) * 100
    warns = []
    if len(bymo) < 3:
        warns.append('⚠️ 覆盖不足 3 个月')
    if conc >= 50:
        warns.append(f'⚠️ 高度集中（{top_mo} 占 {conc:.0f}%）')
    elif conc >= 33:
        warns.append(f'轻度集中（{top_mo} 占 {conc:.0f}%）')
    mos = sorted(bymo)
    for a, c in zip(mos, mos[1:]):
        ya, ma = int(a[:4]), int(a[5:7])
        yb, mb = int(c[:4]), int(c[5:7])
        diff = (yb - ya) * 12 + (mb - ma)
        if diff >= 4:
            warns.append(f'⚠️ {a}~{c} 缺 {diff - 1} 个月')
        elif diff >= 2:
            warns.append(f'{a}~{c} 缺 {diff - 1} 个月')
    if warns:
        L.append(f'- **{b}**（{len(rs)} 条，{first_d} ~ {last_d}）：' + '；'.join(warns))
L.append('')

out = os.path.join(eng.REPORTS_DIR, 'comparison_direction.md')
with open(out, 'w', encoding='utf-8') as f:
    f.write('\n'.join(L))
print(f'对比表已写入 {out} | 参与打分博主 {len(ELIGIBLE)} / 全部 {len(ALL_BLOGGERS)} | 总榜上榜 {len(ranked)} 位')
for b in ranked:
    rs = rows_all[b]
    print(f'  {b}: {len(rs)} 综合（显式 {len(explicit(b))}+nd {len(ndrows(b))}）| {acc_of(rs):.1f}% | 综合均分 {eng.avg_of(rs):+.2f} | t={eng.t_stat_of(rs):+.2f}')
