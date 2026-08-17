# -*- coding: utf-8 -*-
"""
Direction 评估引擎 v2 — 严格按 SKILL.md（Direction 主技能）最新规则实现

规则要点（与 .claude/skills/analyze-blogger/SKILL.md §1~§8 一一对应）：
  §1 有效性   : 盘中/盘后发布的"今天"预测无效（无效-日内）；盘前(<9:30)"今天"有效
  §3 可打分性 : 必须同时有明确预测周期 + 明确态度（看涨看跌/收阳收阴）
  §4 验证终点 : 以"信号日"（帖子发布自然日）为基准推算
  §5 参考价   : 最新收盘价，与开盘价无关（盘中/盘后→当天收盘；盘前/非交易日→上一交易日收盘）
  §6 打分     : score = direction × return × 100
  §7 汇总     : 平均分为核心指标；正确率 = score>0 占比，score=0 计"平"不计入
  §8 报告     : 逐条表 + 按预测指数/多空/预测周期三分类表 + 月度表现

用法:
  python scripts/eval/run_direction.py [博主名 ...]    # 不传参数 = 全部
  python scripts/eval/run_direction.py --selftest      # 引擎自测

数据: data/direction_signals/<博主名>.json
信号记录 schema:
  计分:   {"pub": "YYYY-MM-DD HH:MM", "d": ±1, "s": 1|2, "idx": "指数", "spec": "...", "summary": "...", "cat": "scored"}
  单列:   {"pub": "...", "d": ±1, "idx": "指数", "summary": "...", "cat": "无效-日内|无预测周期|目标点位|待验证"}
"""

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

# Windows GBK 控制台兼容：自测与摘要打印含 emoji
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(ROOT, 'data', 'direction_signals')
REPORTS_DIR = os.path.join(ROOT, 'reports')

# ---------------- 行情数据 ----------------
def _load_market():
    with open(os.path.join(ROOT, 'data', 'market', 'market_data.json'), encoding='utf-8') as f:
        return json.load(f)

MARKET = _load_market()
CAL = sorted(r['日期'] for r in MARKET['上证指数'])          # 交易日历（上证指数为基准）
CAL_SET = set(CAL)
IDX = {k: {r['日期']: r for r in v} for k, v in MARKET.items()}
LAST = {k: max(v) for k, v in IDX.items()}
LAST['双创'] = min(LAST['创业板指'], LAST['科创50'])          # 双创取两者较早的数据末日
EVAL_DATE = LAST['上证指数']                                  # 评估时间 = 市场数据最新交易日

IDX_ALIASES = {'上证综指': '上证指数', '上证': '上证指数', '综指': '上证指数'}


def normalize_idx(idx):
    return IDX_ALIASES.get(idx, idx)


# ---------------- 交易日历 ----------------
def next_td(d):
    """d 之后最近一个交易日（不含 d）"""
    for x in CAL:
        if x > d:
            return x
    return None


def prev_td(d):
    """d 之前最近一个交易日（不含 d）"""
    for x in reversed(CAL):
        if x < d:
            return x
    return None


# ---------------- §5 信号参考价：最新收盘价，与开盘价无关 ----------------
def ref_date_of(pub):
    """参考价对应的交易日：盘中/盘后→当天；盘前/非交易日→上一交易日"""
    pd_, hhmm = pub[:10], pub[11:]
    if pd_ in CAL_SET:
        h, m = int(hhmm[:2]), int(hhmm[3:5])
        if h * 60 + m >= 9 * 60 + 30:      # 9:30 及以后（盘中/盘后）
            return pd_
    return prev_td(pd_)                    # 盘前 / 非交易日 → 最新收盘价


def ref_price_of(idx, ref_date):
    """参考价 = ref_date 的收盘价（双创取两指数收盘均值）"""
    idx = normalize_idx(idx)
    if idx == '双创':
        return (IDX['创业板指'][ref_date]['收盘'] + IDX['科创50'][ref_date]['收盘']) / 2
    return IDX[idx][ref_date]['收盘']


# ---------------- §4 验证终点：以信号日（发布自然日）为基准 ----------------
def endpoint_of(pub_date, spec):
    pub = datetime.strptime(pub_date, '%Y-%m-%d')
    if spec == 'today':
        return pub_date
    if spec.startswith('t'):                                   # tN = 信号日之后第 N 个交易日
        d = pub_date
        for _ in range(int(spec[1:])):
            d = next_td(d)
            if d is None:
                return None
        return d
    if spec == 'week':                                         # 本周最后交易日
        base = pub_date if pub_date in CAL_SET else next_td(pub_date)
        y, w, _ = datetime.strptime(base, '%Y-%m-%d').isocalendar()
        days = [d for d in CAL if datetime.strptime(d, '%Y-%m-%d').isocalendar()[:2] == (y, w)]
        return days[-1] if days else None
    if spec in ('nweek', 'nweek_first'):                       # 下周最后/第一个交易日
        nd = pub + timedelta(days=7)
        y, w, _ = nd.isocalendar()
        days = [d for d in CAL if datetime.strptime(d, '%Y-%m-%d').isocalendar()[:2] == (y, w)]
        if not days:
            return None
        return days[0] if spec == 'nweek_first' else days[-1]
    if spec == 'month':                                        # 当月最后交易日
        days = [d for d in CAL if d[:7] == pub_date[:7]]
        return days[-1] if days else None
    if spec == 'nmonth':                                       # 下月最后交易日
        y, m = int(pub_date[:4]), int(pub_date[5:7])
        m += 1
        if m > 12:
            y, m = y + 1, 1
        days = [d for d in CAL if d[:7] == f'{y:04d}-{m:02d}']
        return days[-1] if days else None
    if spec == 'yearend':                                      # 当年最后交易日（数据未覆盖→待验证）
        days = [d for d in CAL if d[:4] == f'{pub.year:04d}']
        return days[-1] if days else None
    if spec.startswith('d:'):                                  # 具体日期（非交易日顺延）
        target = spec[2:]
        return target if target in CAL_SET else next_td(target)
    raise ValueError(f'未知 spec: {spec}')


SPEC_TEXT = {'today': '今天', 't1': '明天', 't2': '后天/1-2天', 't3': '未来几天', 't5': '近期/短期',
             'week': '本周', 'nweek': '下周', 'nweek_first': '下周初', 'month': '月底前',
             'nmonth': '下个月', 'yearend': '全年'}
IDX_SHORT = {'上证指数': '上证', '上证50': '上证50', '科创50': '科创50', '创业板指': '创业板',
             '双创': '双创', '沪深300': '沪深300', '中证500': '中证500', '中证1000': '中证1000'}


def period_text(spec):
    if spec in SPEC_TEXT:
        return SPEC_TEXT[spec]
    if spec.startswith('d:'):
        return spec[2:][5:]
    return spec


def period_group(spec):
    """按预测周期分类的组标签：具体日期类（d:）统一归入"具体日期"，其余沿用 period_text"""
    return '具体日期' if spec.startswith('d:') else period_text(spec)


# ---------------- §6 打分 ----------------
def calc(sig):
    """单条信号计算。返回行 dict：计分行带 ref/ep/epc/ret/score；单列行原样带 note"""
    cat = sig.get('cat', 'scored')
    if cat in ('无效-日内', '无预测周期', '目标点位'):
        return dict(sig, ref=None, ep=None, epc=None, ret=None, score=None, note=cat)
    pub, d, idx = sig['pub'], sig['d'], normalize_idx(sig['idx'])
    spec = sig.get('spec')
    if spec is None:                                   # 防御：缺 spec 的手写单列（如 cat='待验证'）→ 无法定终点
        return dict(sig, ref=None, ep=None, epc=None, ret=None, score=None, note='无预测周期')
    if spec == 'today':                                # §1 防御：盘中/盘后/非交易日发布的"今天"一律无效-日内
        pd_, hhmm = pub[:10], pub[11:]
        if pd_ in CAL_SET:
            h, m = int(hhmm[:2]), int(hhmm[3:5])
            if h * 60 + m >= 9 * 60 + 30:
                return dict(sig, ref=None, ep=None, epc=None, ret=None, score=None, note='无效-日内')
        else:
            return dict(sig, ref=None, ep=None, epc=None, ret=None, score=None, note='无效-日内')
    ref_date = ref_date_of(pub)
    if idx == '双创':                                  # 参考价数据覆盖防御（双创需两指数都有该日行情）
        ref_ok = ref_date in IDX['创业板指'] and ref_date in IDX['科创50']
    else:
        ref_ok = ref_date in IDX[idx]
    rp = ref_price_of(idx, ref_date) if ref_ok else None
    ep = endpoint_of(pub[:10], spec)
    if ep is None or ep > LAST[idx]:
        return dict(sig, ref=(round(rp, 2) if rp is not None else None), ep=ep, epc=None, ret=None, score=None, note='待验证')
    if rp is None:
        return dict(sig, ref=None, ep=ep, epc=None, ret=None, score=None, note='待验证')
    if ep <= ref_date:                                 # 过时预测防御：终点 ≤ 参考价日（如周五盘后发"本周"）
        return dict(sig, ref=round(rp, 2), ep=ep, epc=None, ret=None, score=None, note='无效-过时')
    if idx == '双创':
        r1 = IDX['创业板指'][ep]['收盘'] / IDX['创业板指'][ref_date]['收盘'] - 1
        r2 = IDX['科创50'][ep]['收盘'] / IDX['科创50'][ref_date]['收盘'] - 1
        ret = (r1 + r2) / 2
        epc = (IDX['创业板指'][ep]['收盘'] + IDX['科创50'][ep]['收盘']) / 2
    else:
        ret = IDX[idx][ep]['收盘'] / rp - 1
        epc = IDX[idx][ep]['收盘']
    score = d * ret * 100
    return dict(sig, ref=round(rp, 2), ep=ep, epc=round(epc, 2),
                ret=ret, score=round(score, 2), note='')


def acc_of(rs):
    """胜率 = score>0 占比；score=0 计'平'，不计入分子分母"""
    p = sum(1 for r in rs if r['score'] > 0)
    z = sum(1 for r in rs if r['score'] == 0)
    dd = len(rs) - z
    return p, dd, (p / dd * 100 if dd else 0.0)


def avg_of(rs):
    return sum(r['score'] for r in rs) / len(rs) if rs else 0.0


def vol_of(rs):
    """波动率 = 单信号 score 的样本标准差（n-1 分母）；<2 条信号时为 0"""
    if len(rs) < 2:
        return 0.0
    m = avg_of(rs)
    return (sum((r['score'] - m) ** 2 for r in rs) / (len(rs) - 1)) ** 0.5


def sharpe_of(rs):
    """夏普 = 平均分 / 波动率；波动率为 0 或信号不足 2 条时返回 None"""
    v = vol_of(rs)
    return avg_of(rs) / v if v > 0 else None


def max_dd_of(rs):
    """最大回撤 = 按发布时间顺序累加单信号 score 的曲线峰值回撤（%）"""
    if not rs:
        return 0.0
    peak = cur = 0.0
    mdd = 0.0
    for r in sorted(rs, key=lambda x: x['pub']):
        cur += r['score']
        peak = max(peak, cur)
        mdd = max(mdd, peak - cur)
    return mdd


# ---------------- 报告生成 ----------------
def post_count(blogger):
    """读取 posts 文件的帖子总数（报告头部用）。文件缺失/损坏返回 None。"""
    pf = os.path.join(ROOT, 'data', 'posts', f'{blogger}.json')
    if not os.path.exists(pf):
        return None
    try:
        with open(pf, encoding='utf-8') as f:
            data = json.load(f)
        posts = data.get('posts') if isinstance(data, dict) else data
        return len(posts) if isinstance(posts, list) else None
    except Exception:
        return None


def generate(blogger):
    with open(os.path.join(DATA_DIR, f'{blogger}.json'), encoding='utf-8') as f:
        data = json.load(f)
    rows = [calc(s) for s in data['signals']]
    scored = [r for r in rows if r['score'] is not None]
    n_inv = sum(1 for r in rows if r['note'] == '无效-日内')
    n_np = sum(1 for r in rows if r['note'] == '无预测周期')
    n_tp = sum(1 for r in rows if r['note'] == '目标点位')
    n_pend = sum(1 for r in rows if r['note'] == '待验证')
    n_stale = sum(1 for r in rows if r['note'] == '无效-过时')

    n_pos = sum(1 for r in scored if r['score'] > 0)
    n_zero = sum(1 for r in scored if r['score'] == 0)
    den = len(scored) - n_zero
    acc = n_pos / den * 100 if den else 0.0
    avg = avg_of(scored)
    if scored:
        mx = max(scored, key=lambda r: r['score'])
        mn = min(scored, key=lambda r: r['score'])
        bull = [r for r in scored if r['d'] == 1]
        bear = [r for r in scored if r['d'] == -1]
        strong = [r for r in scored if r['s'] == 2]
        moderate = [r for r in scored if r['s'] == 1]
        st = acc_of(strong)
        md = acc_of(moderate)
    else:
        mx = mn = None
        bull = bear = strong = moderate = []
        st = md = None

    n_posts = post_count(blogger)
    L = []
    L.append(f'# {blogger} 方向预测评估（Direction）')
    L.append('')
    L.append(f'> 评估时间：{EVAL_DATE} | 方法论：SKILL.md（Direction，逐条验证，score = direction × return）')
    L.append(f'> 帖子总数：{n_posts} 条' if n_posts is not None else '> 帖子总数：未知（posts 文件缺失或不可读）')
    L.append(f'> 信号总数：{len(rows)} 条（参与打分 {len(scored)} + 待验证 {n_pend} + 无效-日内 {n_inv} + 无预测周期 {n_np} + 目标点位 {n_tp} + 无效-过时 {n_stale}）')
    L.append('')
    L.append('---')
    L.append('')
    L.append('## 📊 汇总指标')
    L.append('')
    L.append('```')
    L.append(f'信号总数：{len(scored)}')
    L.append(f'  另有：无效-日内 {n_inv} 条 / 无预测周期 {n_np} 条 / 目标点位-不计时 {n_tp} 条 / 无效-过时 {n_stale} 条 / 待验证 {n_pend} 条（单列，不计分）')
    L.append(f'方向正确：{n_pos}（正确率 {acc:.1f}% = score>0 信号数 / {den}；score=0 计"平" {n_zero} 条，不计入分子分母）')
    L.append(f'  - strong 正确：{st[0]}/{st[1]}（正确率 {st[2]:.1f}%）' if st else '  - strong 正确：—')
    L.append(f'  - moderate 正确：{md[0]}/{md[1]}（正确率 {md[2]:.1f}%）' if md else '  - moderate 正确：—')
    L.append(f'平均分：{avg:+.2f}（= 单信号平均收益 %，核心指标）')
    L.append(f'波动率：{vol_of(scored):.2f}（单信号 score 样本标准差）')
    sh = sharpe_of(scored)
    L.append(f'夏普：{sh:+.2f}（= 平均分 / 波动率）' if sh is not None else '夏普：—（信号 <2 条或波动率为 0）')
    L.append(f'最大回撤：-{max_dd_of(scored):.2f}（按信号发布时间顺序累加 score 的曲线峰值回撤）')
    L.append(f'最高分：{mx["score"]:+.2f} / 最低分：{mn["score"]:+.2f}' if mx else '最高分：— / 最低分：—')
    L.append(f'看多平均分：{avg_of(bull):+.2f}（{len(bull)} 条）  看空平均分：{avg_of(bear):+.2f}（{len(bear)} 条）')
    L.append('```')
    L.append('')

    def class_table(title, groups):
        L.append(f'### {title}')
        L.append('')
        L.append('| 分类 | 信号数 | 平均分 | 胜率 |')
        L.append('|:---|:---:|:---:|:---:|')
        for label, rs in groups:
            if not rs:
                continue
            p, dd, rate = acc_of(rs)
            L.append(f'| {label} | {len(rs)} | {avg_of(rs):+.2f} | {rate:.1f}% |')
        L.append('')

    # 按预测指数
    byidx = defaultdict(list)
    for r in scored:
        byidx[r['idx']].append(r)
    class_table('按预测指数分类', [(IDX_SHORT.get(k, k), v) for k, v in sorted(byidx.items())])

    # 按多空
    class_table('按多空分类', [('看多 bullish', bull), ('　└ strong', strong), ('　└ moderate', moderate),
                              ('看空 bearish', bear)])

    # 按预测周期
    byperiod = defaultdict(list)
    for r in scored:
        byperiod[period_group(r['spec'])].append(r)
    order = ['今天', '明天', '后天/1-2天', '未来几天', '近期/短期', '本周', '下周', '下周初', '月底前', '下个月', '全年', '具体日期']
    groups = [(k, byperiod[k]) for k in order if k in byperiod]
    groups += [(k, v) for k, v in sorted(byperiod.items()) if k not in order]
    class_table('按预测周期分类', groups)

    # 月度表现
    L.append('### 月度表现')
    L.append('')
    L.append('| 月份 | 信号数 | 平均分 | 胜率 |')
    L.append('|:---|:---:|:---:|:---:|')
    bymonth = defaultdict(list)
    for r in scored:
        bymonth[r['pub'][:7]].append(r)
    for mo in sorted(bymonth):
        rs = bymonth[mo]
        p, dd, rate = acc_of(rs)
        L.append(f'| {mo} | {len(rs)} | {avg_of(rs):+.2f} | {rate:.1f}% |')
    L.append('')

    # 信号时间分布与集中度/覆盖度分析
    L.append('### ⏱️ 信号时间分布与集中度')
    L.append('')
    first_d = min(r['pub'][:10] for r in scored) if scored else '-'
    last_d = max(r['pub'][:10] for r in scored) if scored else '-'
    top_mo = max(bymonth.items(), key=lambda kv: len(kv[1])) if bymonth else None
    top_n = len(top_mo[1]) if top_mo else 0
    conc = top_n / len(scored) * 100 if scored else 0
    L.append(f'- 覆盖：{first_d} ~ {last_d}，共 {len(bymonth)} 个月，{len(scored)} 条计分信号；单月最高占比 {conc:.0f}%'
             + (f'（{top_mo[0]} {top_n} 条）' if top_mo else ''))
    warns = []
    if len(bymonth) < 3:
        warns.append('⚠️ 信号覆盖不足 3 个月，样本期过短，排名参考价值低')
    if conc >= 50:
        warns.append(f'⚠️ 信号高度集中于单月（{top_mo[0]} 占 {conc:.0f}%）')
    elif conc >= 33:
        warns.append(f'提示：{top_mo[0]} 单月占比 {conc:.0f}%，存在轻度集中')
    mos = sorted(bymonth)
    gaps = []
    for a, b in zip(mos, mos[1:]):
        ya, ma = int(a[:4]), int(a[5:7])
        yb, mb = int(b[:4]), int(b[5:7])
        diff = (yb - ya) * 12 + (mb - ma)
        if diff >= 4:
            gaps.append(f'⚠️ {a} 与 {b} 之间缺 {diff - 1} 个月（严重覆盖缺口）')
        elif diff >= 2:
            gaps.append(f'提示：{a} 与 {b} 之间缺 {diff - 1} 个月')
    warns += gaps
    if warns:
        for w in warns:
            L.append('- ' + w)
    else:
        L.append('- 分布均匀，无集中度/覆盖度警告')
    L.append('')
    L.append('---')
    L.append('')
    L.append('## 📋 逐条汇总表')
    L.append('')
    L.append('| # | 日期 | 内容(≤50字) | 方向 | 强度 | 预测周期 | 目标指数 | 参考价 | 终点日 | 终点收盘 | return | 分 | 备注 |')
    L.append('|:---|:---|:---|:---:|:---:|:---|:---|:---:|:---:|:---:|:---:|:---:|:---|')
    D_ICO = {1: '↑', -1: '↓'}
    S_TXT = {2: 'str', 1: 'mod'}
    CAT_PERIOD = {'无效-日内': '今天', '无预测周期': '无预测周期', '目标点位': '目标点位'}
    NOTE_TXT = {'无效-日内': '无效-日内', '无预测周期': '无预测周期', '目标点位': '目标点位-不计时',
                '无效-过时': '无效-过时', '待验证': '待验证'}
    for i, r in enumerate(rows, 1):
        ico = D_ICO.get(r['d'], '')
        stx = S_TXT.get(r.get('s', 1), 'mod')
        if r['score'] is not None:
            period = period_text(r['spec'])
        elif r['note'] in ('待验证', '无效-过时') and r.get('spec'):
            period = period_text(r['spec'])      # 单列行显示实际预测周期（如"全年"）
        else:
            period = CAT_PERIOD.get(r['note'], r['note'])
        idx = IDX_SHORT.get(r['idx'], r['idx'])
        ref_txt = f"{r['ref']:.2f}" if r.get('ref') is not None else '-'
        if r['score'] is not None:
            L.append(f"| {i} | {r['pub'][5:10]} | {r['summary']} | {ico} | {stx} | {period} | {idx} | {ref_txt} | {r['ep'][5:]} | {r['epc']:.2f} | {r['ret']*100:+.2f}% | {r['score']:+.2f} | — |")
        elif r['note'] == '待验证':
            L.append(f"| {i} | {r['pub'][5:10]} | {r['summary']} | {ico} | {stx} | {period} | {idx} | {ref_txt} | - | - | - | - | 待验证 |")
        elif r['note'] == '无效-过时':
            L.append(f"| {i} | {r['pub'][5:10]} | {r['summary']} | {ico} | {stx} | {period} | {idx} | {ref_txt} | {r['ep'][5:]} | - | - | - | 无效-过时 |")
        else:
            L.append(f"| {i} | {r['pub'][5:10]} | {r['summary']} | {ico} | {stx} | {period} | {idx} | - | - | - | - | - | {NOTE_TXT.get(r['note'], r['note'])} |")
    L.append('')
    L.append('---')
    L.append('')
    L.append('## 🔍 观察要点')
    L.append('')
    verdict = '具备统计优势' if acc >= 55 else '接近抛硬币水平，没有统计优势'
    L.append(f'- **方向正确率 {acc:.1f}%**（{n_pos}/{den}，终点收益判定；score=0 计"平" {n_zero} 条）——{verdict}。')
    L.append(f'- **看多 {len(bull)} 条平均 {avg_of(bull):+.2f} 分（胜率 {acc_of(bull)[2]:.1f}%）vs 看空 {len(bear)} 条平均 {avg_of(bear):+.2f} 分（胜率 {acc_of(bear)[2]:.1f}%）。')
    if byperiod:
        best_p = max(byperiod.items(), key=lambda kv: (avg_of(kv[1]), len(kv[1])))
        worst_p = min(byperiod.items(), key=lambda kv: (avg_of(kv[1]), -len(kv[1])))
        L.append(f'- **预测周期**："{best_p[0]}"最强（{len(best_p[1])} 条，平均 {avg_of(best_p[1]):+.2f} 分）；"{worst_p[0]}"最弱（{len(worst_p[1])} 条，平均 {avg_of(worst_p[1]):+.2f} 分）。')
    if byidx:
        best_i = max(byidx.items(), key=lambda kv: (avg_of(kv[1]), len(kv[1])))
        worst_i = min(byidx.items(), key=lambda kv: (avg_of(kv[1]), -len(kv[1])))
        L.append(f'- **预测指数**：{IDX_SHORT.get(best_i[0], best_i[0])} 最强（{len(best_i[1])} 条，平均 {avg_of(best_i[1]):+.2f} 分）；{IDX_SHORT.get(worst_i[0], worst_i[0])} 最弱（{len(worst_i[1])} 条，平均 {avg_of(worst_i[1]):+.2f} 分）。')
    if mx:
        L.append(f'- **最大单条命中**：{mx["pub"][5:10]}"{mx["summary"][:24]}"（{mx["score"]:+.2f} 分）。')
        L.append(f'- **最大单条失误**：{mn["pub"][5:10]}"{mn["summary"][:24]}"（{mn["score"]:+.2f} 分）。')
    if n_tp:
        L.append(f'- **目标点位 {n_tp} 条不计分**：无时间承诺的点位表述，单独统计。')
    if n_np:
        L.append(f'- **无预测周期 {n_np} 条不计分**：模糊周期（"未来一段时间""中长期"等）与完全无时间信息，单独统计。')
    if n_pend:
        L.append(f'- **待验证 {n_pend} 条**：验证终点超出数据覆盖范围，等数据覆盖后补算。')
    if n_stale:
        L.append(f'- **无效-过时 {n_stale} 条**：验证终点 ≤ 参考价日（如周五盘后发"本周"），引擎自动单列不计分。')
    L.append('')
    out = os.path.join(REPORTS_DIR, f'{blogger}_direction.md')
    with open(out, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L))
    print(f'{blogger}: 参与打分 {len(scored)} | 正确率 {acc:.1f}% ({n_pos}/{den}) | 平均分 {avg:+.2f} | 报告已写入 {out}')
    return {'blogger': blogger, 'scored': len(scored), 'acc': acc, 'avg': avg}


def selftest():
    """引擎自测：校验参考价/验证终点/打分的关键路径"""
    cases = [
        # (pub, d, idx, spec, 期望参考价日, 期望终点日)
        ('2026-01-07 15:08', -1, '上证指数', 't1', '2026-01-07', '2026-01-08'),   # 盘后→当天收盘；明天=次一交易日
        ('2026-01-16 09:12', 1, '上证指数', 'today', '2026-01-15', '2026-01-16'),  # 盘前→上一交易日收盘；今天=当天
        ('2026-01-24 14:44', 1, '上证指数', 'nweek', '2026-01-23', '2026-01-30'),  # 周六→周五收盘；下周=下一周最后交易日
        ('2026-05-06 18:43', 1, '上证指数', 't1', '2026-05-06', '2026-05-07'),
        ('2026-01-09 18:17', 1, '上证指数', 'd:2026-02-13', '2026-01-09', '2026-02-13'),  # 具体日期
    ]
    errors = []
    for pub, d, idx, spec, exp_ref, exp_ep in cases:
        rd = ref_date_of(pub)
        ep = endpoint_of(pub[:10], spec)
        if rd != exp_ref or ep != exp_ep:
            errors.append(f'{pub} {spec}: ref={rd}(期望{exp_ref}) ep={ep}(期望{exp_ep})')
    # 打分验证：01-07 盘后看空明天 → ref=01-07收盘 4085.77, ep=01-08收盘 4082.98 → ret≈-0.068% → score≈+0.07
    sig = {'pub': '2026-01-07 15:08', 'd': -1, 's': 1, 'idx': '上证指数', 'spec': 't1',
           'summary': 'test', 'cat': 'scored'}
    r = calc(sig)
    exp_score = round((IDX['上证指数']['2026-01-08']['收盘'] / IDX['上证指数']['2026-01-07']['收盘'] - 1) * -100, 2)
    if abs(r['score'] - exp_score) > 0.02:
        errors.append(f'score={r["score"]} 期望 {exp_score}')
    # 过时防御：月末周末发布"月底前" → 终点=当月最后交易日 ≤ 参考价日 → 无效-过时
    r = calc({'pub': '2026-01-31 15:00', 'd': 1, 's': 1, 'idx': '上证指数', 'spec': 'month',
              'summary': 'test', 'cat': 'scored'})
    if r['note'] != '无效-过时':
        errors.append(f'过时预测未被标记（note={r["note"]}）')
    # §1 防御：盘中/盘后/非交易日发布"今天" → 无效-日内
    for bad_pub in ('2026-01-28 10:06', '2026-01-28 15:06', '2026-01-31 15:00'):
        r = calc({'pub': bad_pub, 'd': 1, 's': 1, 'idx': '上证指数', 'spec': 'today',
                  'summary': 'test', 'cat': 'scored'})
        if r['note'] != '无效-日内':
            errors.append(f'{bad_pub} today 未判无效（note={r["note"]}）')
    # 缺 spec 防御：手写单列无 spec → 无预测周期，不崩溃
    r = calc({'pub': '2026-01-07 15:08', 'd': 1, 'idx': '上证指数', 'summary': 'test', 'cat': '待验证'})
    if r['note'] != '无预测周期':
        errors.append(f'缺 spec 防御失败（note={r["note"]}）')
    if errors:
        print('❌ 自测失败:')
        for e in errors:
            print('  ', e)
        sys.exit(1)
    print('✅ 引擎自测通过')


if __name__ == '__main__':
    if '--selftest' in sys.argv:
        selftest()
        sys.exit(0)
    names = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not names:
        names = sorted(f[:-5] for f in os.listdir(DATA_DIR) if f.endswith('.json'))
    results = {}
    for name in names:
        results[name] = generate(name)
