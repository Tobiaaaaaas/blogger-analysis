# -*- coding: utf-8 -*-
"""
全部博主 Direction 横向对比汇总（配合引擎 v2）
用法: python scripts/eval/comparison_all.py
输出: reports/comparison_direction.md
"""
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_direction as eng

SPARSE_THRESHOLD = 20
BLOGGERS = sorted(f[:-5] for f in os.listdir(eng.DATA_DIR) if f.endswith('.json'))

rows_all, meta = {}, {}
for b in BLOGGERS:
    data = json.load(open(os.path.join(eng.DATA_DIR, f'{b}.json'), encoding='utf-8'))
    rows = [r for r in (eng.calc(s) for s in data['signals']) if r['score'] is not None]
    rows_all[b] = rows
    all_rows = [eng.calc(s) for s in data['signals']]
    meta[b] = {'signals': len(data['signals']), 'scored': len(rows),
               'bull': sum(1 for r in rows if r['d'] == 1), 'bear': sum(1 for r in rows if r['d'] == -1),
               'inv': sum(1 for r in all_rows if r['note'] == '无效-日内'),
               'np': sum(1 for r in all_rows if r['note'] == '无预测周期'),
               'tp': sum(1 for r in all_rows if r['note'] == '目标点位'),
               'pend': sum(1 for r in all_rows if r['note'] == '待验证')}

def cell(rs):
    if not rs:
        return '—'
    return f'{eng.avg_of(rs):+.2f}({len(rs)})'

def acc_of(rs):
    return eng.acc_of(rs)[2]

def period_key(r):
    return '具体日期' if r['spec'].startswith('d:') else eng.period_text(r['spec'])

L = []
L.append('# 全部博主 Direction 横向对比（平均分 = 单信号平均收益 %，括号内为信号数）')
L.append('')
L.append(f'博主数：{len(BLOGGERS)} | 数据截止 {eng.EVAL_DATE} | 稀疏信号阈值：计分信号 < {SPARSE_THRESHOLD} 条')
L.append('')

# 总榜
L.append('## 🏆 总榜（按平均分排序）')
L.append('')
L.append('| 排名 | 博主 | 计分信号 | 正确率 | **平均分** | 看多 | 看空 |')
L.append('|:---:|:---|:---:|:---:|:---:|:---:|:---:|')
ranked = sorted(BLOGGERS, key=lambda b: -(eng.avg_of(rows_all[b]) if rows_all[b] else -99))
for i, b in enumerate(ranked, 1):
    rs = rows_all[b]
    if not rs:
        L.append(f'| {i} | {b} | 0 | — | — | — | — |')
        continue
    L.append(f'| {i} | {b} | {len(rs)} | {acc_of(rs):.1f}% | **{eng.avg_of(rs):+.2f}** | {meta[b]["bull"]} | {meta[b]["bear"]} |')
L.append('')

# 表1 预测周期（三档归类：交易日 0-1 / 2-5 / 6+，附"今天(盘前)"子行）
L.append('## 1. 按预测周期分类（三档：0-1/2-5/6个及以上交易日）')
L.append('')
def bucket_of(r):
    pubd = r['pub'][:10]
    base = pubd if pubd in eng.CAL_SET else eng.prev_td(pubd)
    span = eng.CAL.index(r['ep']) - eng.CAL.index(base)
    if span <= 1: return '0-1个交易日(今天明天)'
    if span <= 5: return '2-5个交易日(1周内)'
    return '6个交易日及以上(大于1周)'
L.append('| 预测周期 | ' + ' | '.join(BLOGGERS) + ' |')
L.append('|:---|' + ':---:|' * len(BLOGGERS))
groups = {b: defaultdict(list) for b in BLOGGERS}
today_groups = {b: [] for b in BLOGGERS}
for b in BLOGGERS:
    for r in rows_all[b]:
        groups[b][bucket_of(r)].append(r)
        if r['spec'] == 'today':
            today_groups[b].append(r)
for p in ['0-1个交易日(今天明天)', '　└ 其中:今天(盘前)', '2-5个交易日(1周内)', '6个交易日及以上(大于1周)']:
    if p == '　└ 其中:今天(盘前)':
        L.append(f'| {p} | ' + ' | '.join(cell(today_groups[b]) for b in BLOGGERS) + ' |')
    else:
        L.append(f'| {p} | ' + ' | '.join(cell(groups[b].get(p, [])) for b in BLOGGERS) + ' |')
L.append('| **全部** | ' + ' | '.join(cell(rows_all[b]) for b in BLOGGERS) + ' |')
L.append('')

# 表2 多空
L.append('## 2. 按看多看空分类')
L.append('')
L.append('| 方向 | ' + ' | '.join(BLOGGERS) + ' |')
L.append('|:---|' + ':---:|' * len(BLOGGERS))
for label, fn in [('看多', lambda r: r['d'] == 1), ('看空', lambda r: r['d'] == -1)]:
    L.append(f'| {label} | ' + ' | '.join(cell([r for r in rows_all[b] if fn(r)]) for b in BLOGGERS) + ' |')
L.append('')

# 表3 指数
L.append('## 3. 按预测指数分类')
L.append('')
all_idx = set()
for b in BLOGGERS:
    all_idx |= {r['idx'] for r in rows_all[b]}
L.append('| 预测指数 | ' + ' | '.join(BLOGGERS) + ' |')
L.append('|:---|' + ':---:|' * len(BLOGGERS))
for idx in ['上证指数', '上证50', '科创50', '创业板指', '双创', '沪深300', '中证500', '中证1000']:
    if idx not in all_idx:
        continue
    L.append(f'| {eng.IDX_SHORT[idx]} | ' + ' | '.join(cell([r for r in rows_all[b] if r["idx"] == idx]) for b in BLOGGERS) + ' |')
L.append('')

# 表4 月份
L.append('## 4. 按月份分类')
L.append('')
months = sorted({r['pub'][:7] for b in BLOGGERS for r in rows_all[b]})
L.append('| 月份 | ' + ' | '.join(BLOGGERS) + ' |')
L.append('|:---|' + ':---:|' * len(BLOGGERS))
for mo in months:
    L.append(f'| {mo} | ' + ' | '.join(cell([r for r in rows_all[b] if r['pub'][:7] == mo]) for b in BLOGGERS) + ' |')
L.append('')

# 稀疏信号提醒
L.append('## ⚠️ 稀疏信号 / 数据覆盖提醒（统计无意义或需谨慎解读）')
L.append('')
for b in BLOGGERS:
    n = meta[b]['scored']
    if n < SPARSE_THRESHOLD:
        L.append(f'- **{b}**：仅 {n} 条可打分信号（阈值 {SPARSE_THRESHOLD}），正确率/平均分不具备统计意义，仅作参考不作结论')
L.append('')

out = os.path.join(eng.REPORTS_DIR, 'comparison_direction.md')
with open(out, 'w', encoding='utf-8') as f:
    f.write('\n'.join(L))
print(f'对比表已写入 {out} | 博主数 {len(BLOGGERS)}')
for b in ranked:
    rs = rows_all[b]
    if not rs:
        print(f'  {b}: 0 计分')
        continue
    print(f'  {b}: {len(rs)} 计分 | {acc_of(rs):.1f}% | {eng.avg_of(rs):+.2f}')
