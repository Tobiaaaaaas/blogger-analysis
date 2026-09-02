# -*- coding: utf-8 -*-
"""
Direction 评估引擎 v5 — 严格按 SKILL.md（Direction 主技能，commit 769fe57）最新规则实现

规则要点（与 .claude/skills/analyze-blogger/SKILL.md §1~§8 一一对应）：
  §2 cat      : 仅 scored / unscored（spec=long 恒 unscored，不计分）
  §3 验证终点 : 以"信号日"（帖子发布自然日）为基准推算；非交易日发布"今天"→ 报错单列不计分
                （"明天/下周"等非 today 周期周末发布仍正常顺延）
  §4 参考价   : 交易时间中（9:30~11:30、13:00~15:00）→ 所处 30 分钟 K 线开盘价；
                 非交易时间（盘前/午休/盘后/周末假期）→ 上一根 30 分钟 K 线收盘价
  §5 打分     : score = direction × return × 100；终点价 = 终点日 15:00 bar 收盘
  §6 汇总     : 平均分为核心指标；正确率 = score>0 占比，score=0 计"平"不计入；
                另有 unscored（spec=long）/ 无效-过时 / 待验证 / 报错 单列不计分；
                抄底平均分/逃顶平均分 = 拐点当天+前一交易日（2 交易日）窗口内计分信号平均分
  §7 备注     : — / 日内 / 不计分 / 待验证 / 无效-过时 / 报错（非交易日"今天"）
  §8 报告     : 汇总指标（无最大回撤）+ 四个分类表（指数/周期三档/多空/抄底逃顶）
                + 月度表现 + 时间分布 + 逐条表 + 观察要点；
                参与打分资格（帖子跨度≥6月 且 2026以来信号>50）不满足者省略汇总/分类表

用法:
  python scripts/eval/run_direction.py [博主名 ...]    # 不传参数 = 全部
  python scripts/eval/run_direction.py --selftest      # 引擎自测

数据: data/direction_signals/<博主名>.json
信号记录 schema:
  计分:   {"pub": "YYYY-MM-DD HH:MM", "d": ±1, "s": 1|2, "idx": "指数", "spec": "...", "summary": "...", "cat": "scored"}
  单列:   {"pub": "...", "d": ±1, "idx": "指数", "spec": "long", "summary": "...", "cat": "unscored"}
  旧数据兼容: cat=无效-日内/无预测周期/目标点位 或 spec=yearend → 引擎按新规则映射（不计分/报错）
"""

import json
import os
import re
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
INTRADAY_DIR = os.path.join(ROOT, 'data', 'market', 'intraday')

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

# ---------------- 30 分钟日内数据（统一参考价/终点价来源） ----------------
def _load_intraday():
    """加载 7 指数 30 分钟线 → {idx: [(date, [(hhmm, bar), ...]), ...]}（按 date 升序）
    缺文件跳过（该指数信号退回日线口径，不静默丢信号）。"""
    out = {}
    for idx in IDX:
        p = os.path.join(INTRADAY_DIR, f'{idx}_30min.json')
        if not os.path.exists(p):
            print(f'⚠️ 缺少 30 分钟数据 {p}，{idx} 信号退回日线口径（SKILL 前置条件应已跑 fetch_market_intraday.py）')
            continue
        with open(p, encoding='utf-8') as f:
            bars = (json.load(f) or {}).get('bars', [])
        bydate = {}
        for b in bars:
            day, hhmm = b['time'][:10], b['time'][11:16]
            bydate.setdefault(day, []).append((hhmm, b))
        out[idx] = sorted((day, sorted(rows)) for day, rows in bydate.items())
    return out

INTRADAY = _load_intraday()

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


# ---------------- §5 信号参考价（日线口径，仅无 30 分钟数据时退回用） ----------------
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
    if spec in ('long', 'yearend'):
        # 不计分周期（SKILL §3）：无验证终点，直接返回 None，不抛异常
        return None
    pub = datetime.strptime(pub_date, '%Y-%m-%d')
    if spec == 'today':
        # 信号日当天收盘；非交易日发布 → 顺延至下一交易日收盘（SKILL §3）
        return pub_date if pub_date in CAL_SET else next_td(pub_date)
    if spec.startswith('t'):                                   # tN = 信号日之后第 N 个交易日
        d = pub_date
        for _ in range(int(spec[1:])):
            d = next_td(d)
            if d is None:
                return None
        return d
    if spec == 'week':                                         # 本周最后交易日
        base = pub_date if pub_date in CAL_SET else next_td(pub_date)
        if base is None:                                       # 发布日已超行情截止（如 08-29+）→ 无法验证，不计分
            return None
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
    if spec.startswith('d:'):                                  # 具体日期（非交易日顺延）
        target = spec[2:]
        return target if target in CAL_SET else next_td(target)
    raise ValueError(f'未知 spec: {spec}')


SPEC_TEXT = {'today': '今天', 't1': '明天', 't2': '后天/1-2天', 't3': '未来几天', 't5': '近期/短期',
             'nd': '无周期(5日)', 't10': '10天后', 'week': '本周', 'nweek': '下周', 'nweek_first': '下周初', 'month': '月底前',
             'nmonth': '下个月', 'long': '长期', 'yearend': '全年'}
IDX_SHORT = {'上证指数': '上证', '上证50': '上证50', '科创50': '科创50', '创业板指': '创业板',
             '双创': '双创', '沪深300': '沪深300', '中证500': '中证500', '中证1000': '中证1000'}


def period_text(spec):
    if spec in SPEC_TEXT:
        return SPEC_TEXT[spec]
    if spec.startswith('d:'):
        return spec[2:][5:]
    return spec


def bucket_of(r):
    """按预测期限三档归类：信号日→验证终点的交易日数（数交易日历中间交易日，非日历相减）。

    与 SKILL.md 输出部分「预测周期三档归类」算法一致（comparison 表1 同口径，comparison_all.py 复用本函数）：
    base = 发布日若为交易日，否则前一交易日；span = CAL.index(ep) − CAL.index(base)。
    无周期方向（spec=nd）独立成档（SKILL §8：不参与三档，避免污染有明确时间点的样本）。"""
    if r['spec'] == 'nd':
        return '无周期方向'
    pubd = r['pub'][:10]
    base = pubd if pubd in CAL_SET else prev_td(pubd)
    span = CAL.index(r['ep']) - CAL.index(base)
    if span <= 1:
        return '0-1个交易日（今天/明天）'
    if span <= 5:
        return '2-5个交易日（1周内）'
    return '6个交易日及以上（大于1周）'


# ---------------- 抄底/逃顶拐点（SKILL §6：拐点取自 knowledge/market_analysis.md §5.1/§5.2） ----------------
def load_pivots():
    """解析 market_analysis.md §5.1/§5.2 表格 → [(id, date, 'top'|'bottom'), ...]。
    只匹配表格行 `| M# | date | <标签> |`，标签含 顶/底 即判方向；未匹配到任何拐点 → 空列表（调用方自行告警）。"""
    path = os.path.join(ROOT, 'knowledge', 'market_analysis.md')
    if not os.path.exists(path):
        print(f'⚠️ 缺少拐点知识库 {path}，抄底/逃顶平均分将显示 —')
        return []
    text = open(path, encoding='utf-8').read()
    pivots = []
    for sec in re.findall(r'### 5\.[12][^\n]*\n(.*?)(?=\n### |\Z)', text, re.S):
        for m in re.finditer(r'\|\s*(M\d+|I\d+)\s*\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*\*{0,2}([^*|\n]*?)\*{0,2}\s*\|', sec):
            lid, date, label = m.groups()
            if '顶' in label:
                pivots.append((lid, date, 'top'))
            elif '底' in label:
                pivots.append((lid, date, 'bottom'))
    return pivots


PIVOTS = load_pivots()


def pivot_bucket(pub_date):
    """信号发布日落在哪个拐点窗口内 → '抄底'/'逃顶'/None。
    窗口 = 拐点交易日 + 其前一交易日（共 2 个交易日）；底部→抄底、顶部→逃顶。"""
    for _, date, kind in PIVOTS:
        win = {date, prev_td(date)}
        if pub_date in win:
            return '抄底' if kind == 'bottom' else '逃顶'
    return None


def pivot_split(scored_rows):
    """把计分信号按发布日归属为 (抄底行, 逃顶行)。"""
    bottom, top = [], []
    for r in scored_rows:
        b = pivot_bucket(r['pub'][:10])
        if b == '抄底':
            bottom.append(r)
        elif b == '逃顶':
            top.append(r)
    return bottom, top


# ---------------- 参与打分资格（SKILL 参与打分核心原则） ----------------
def posts_span_months(blogger):
    """帖子跨度（月）= posts 文件 publish_date 首末差/30.44；文件缺失/无日期返回 0。"""
    pf = os.path.join(ROOT, 'data', 'posts', f'{blogger}.json')
    if not os.path.exists(pf):
        return 0.0
    try:
        data = json.load(open(pf, encoding='utf-8'))
    except Exception:
        return 0.0
    posts = data.get('posts') if isinstance(data, dict) else data
    dates = [p.get('publish_date', '')[:10] for p in (posts or []) if p.get('publish_date')]
    if len(dates) < 2:
        return 0.0
    d0 = datetime.strptime(min(dates), '%Y-%m-%d')
    d1 = datetime.strptime(max(dates), '%Y-%m-%d')
    return (d1 - d0).days / 30.44


def signals_since_2026(blogger):
    """2026 年以来可提取信号数（data/direction_signals/<名>.json 全部 cat）。"""
    fp = os.path.join(DATA_DIR, f'{blogger}.json')
    if not os.path.exists(fp):
        return 0
    try:
        data = json.load(open(fp, encoding='utf-8'))
    except Exception:
        return 0
    return sum(1 for s in data.get('signals', []) if (s.get('pub') or '')[:10] >= '2026-01-01')


def eligibility(blogger):
    """是否参与打分：帖子跨度≥6个月 且 2026以来信号>50。返回 (ok, span_months, signal_count)。"""
    span = posts_span_months(blogger)
    n = signals_since_2026(blogger)
    return (span >= 6 and n > 50), span, n


# ---------------- 30 分钟线完整性检查（SKILL §2 前置条件） ----------------
def check_intraday():
    """打分前检查：7 指数 30 分钟线覆盖 2026-01-01 至最新交易日全部交易日、每交易日 8 根 bar。
    返回缺失告警列表（空 = 完整）。"""
    warns = []
    for idx in IDX:
        rows = INTRADAY.get(idx)
        if not rows:
            warns.append(f'⚠️ {idx} 缺少 30 分钟数据（先跑 fetch_market_intraday.py）')
            continue
        days = {d for d, _ in rows}
        missing = [d for d in CAL if d >= '2026-01-01' and d <= LAST[idx] and d not in days]
        if missing:
            warns.append(f'⚠️ {idx} 2026 缺失交易日 {len(missing)} 个：{missing[:5]}{"…" if len(missing) > 5 else ""}')
        bad = [d for d, r in rows if d >= '2026-01-01' and d <= LAST[idx] and len(r) != 8]
        if bad:
            warns.append(f'⚠️ {idx} 有 {len(bad)} 个交易日 bar 数 ≠ 8：{bad[:5]}')
    return warns


# ---------------- §6 打分（统一 30 分钟口径） ----------------
def _has_intraday(idx):
    """该指数是否有 30 分钟数据（双创需两指数都有）"""
    if idx == '双创':
        return '创业板指' in INTRADAY and '科创50' in INTRADAY
    return idx in INTRADAY


def ref_price_at(idx, pub):
    """SKILL §4 参考价：当前所能获取的最新价格。

    交易时间中（9:30~11:30、13:00~15:00）→ 所处 30 分钟 K 线开盘价（首根 t ≥ hhmm 的 bar 的 open，
    bar 时间=收盘时间，如 10:20 → 10:30 bar open）；
    非交易时间（盘前 <9:30 / 午休 11:30~13:00 / 盘后 ≥15:00 / 周末假期）→ 上一根 30 分钟 K 线收盘价
    （午休→11:30 bar close；盘后→当日 15:00 bar close；盘前/非交易日→上一交易日 15:00 bar close）。

    返回 (price, ok)；找不到 bar → (None, False)。双创取两指数均值。"""
    if idx == '双创':
        p1, k1 = ref_price_at('创业板指', pub)
        p2, k2 = ref_price_at('科创50', pub)
        if k1 and k2:
            return (p1 + p2) / 2, True
        return None, False
    days = INTRADAY.get(idx)
    if not days:
        return None, False
    pd_, hhmm = pub[:10], pub[11:]
    h, m = int(hhmm[:2]), int(hhmm[3:5])
    hm = h * 60 + m
    if pd_ not in CAL_SET or hm < 9 * 60 + 30:
        # 盘前（<9:30）或非交易日 → 上一交易日 15:00 bar 收盘价
        prev = prev_td(pd_)
        if prev is None:
            return None, False
        for day, rows in days:
            if day == prev:
                return rows[-1][1]['close'], True
        return None, False
    for day, rows in days:
        if day != pd_:
            continue
        if 9 * 60 + 30 <= hm < 11 * 60 + 30 or 13 * 60 <= hm < 15 * 60:
            # 交易时间中 → 所处 bar 开盘价（首根 t ≥ hhmm 的 bar）
            for t, b in rows:
                if t >= hhmm:
                    return b['open'], True
            return None, False
        if 11 * 60 + 30 <= hm < 13 * 60:
            # 午休 → 11:30 bar 收盘价
            for t, b in rows:
                if t == '11:30':
                    return b['close'], True
            return None, False
        # 盘后（≥15:00）→ 当日 15:00 bar 收盘价（末根）
        return rows[-1][1]['close'], True
    return None, False


def _ep_close(idx, ep):
    """终点价 = 验证终点日 15:00 bar 的 close。返回 (close, ok)；双创取两指数均值"""
    if idx == '双创':
        c1, k1 = _ep_close('创业板指', ep)
        c2, k2 = _ep_close('科创50', ep)
        if k1 and k2:
            return (c1 + c2) / 2, True
        return None, False
    for day, rows in INTRADAY.get(idx, []):
        if day == ep:
            return rows[-1][1]['close'], True   # 末根 = 15:00 bar，close == 日线收盘
    return None, False


def _calc_daily_fallback(sig):
    """无 30 分钟数据时的日线口径降级（参考价=最新已收盘日线收盘价，语义对齐 SKILL §4"最新收盘"）。
    盘中"今天"→ ref=当日开盘、ep=当日收盘；盘前"今天"→ ref=上一交易日收盘；非交易日"今天"→ 顺延计分。"""
    pub, d, idx = sig['pub'], sig['d'], normalize_idx(sig['idx'])
    spec = sig.get('spec')
    pd_, hhmm = pub[:10], pub[11:]
    if spec == 'today':
        if pd_ not in CAL_SET:                                  # 非交易日"今天"→ 报错（calc 已拦截，防御直调）
            return dict(sig, ref=None, ep=None, epc=None, ret=None, score=None, note='报错')
        in_session = '09:30' <= hhmm < '15:00'
        ref_date = pd_ if in_session else prev_td(pd_)          # 盘后"今天"已被 calc 判过时，到不了这里
        ep = pd_
        if ref_date is None:
            return dict(sig, ref=None, ep=ep, epc=None, ret=None, score=None, note='待验证')
        ref_ok = (ref_date in IDX['创业板指'] and ref_date in IDX['科创50']) if idx == '双创' \
            else ref_date in IDX.get(idx, {})
        if ep is None or ep > LAST[idx] or not ref_ok:
            return dict(sig, ref=None, ep=ep, epc=None, ret=None, score=None, note='待验证')
        rp = ref_price_of(idx, ref_date)
        epc = (IDX['创业板指'][ep]['收盘'] + IDX['科创50'][ep]['收盘']) / 2 if idx == '双创' \
            else IDX[idx][ep]['收盘']
        ret = epc / rp - 1
        note = '日内' if in_session else ''
        return dict(sig, ref=round(rp, 2), ep=ep, epc=round(epc, 2),
                    ret=ret, score=round(d * ret * 100, 2), note=note)
    ref_date = ref_date_of(pub)
    if idx == '双创':
        ref_ok = ref_date in IDX['创业板指'] and ref_date in IDX['科创50']
    else:
        ref_ok = ref_date in IDX[idx]
    rp = ref_price_of(idx, ref_date) if ref_ok else None
    ep = endpoint_of(pd_, spec)
    if ep is None or ep > LAST[idx]:
        return dict(sig, ref=(round(rp, 2) if rp is not None else None), ep=ep, epc=None, ret=None, score=None, note='待验证')
    if rp is None:
        return dict(sig, ref=None, ep=ep, epc=None, ret=None, score=None, note='待验证')
    if ep <= ref_date:
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


# ---------------- nd（无周期方向）未来 5 个交易日收益率单点计分（SKILL §5） ----------------
def _nd_window_days(pub_date):
    """nd 验证窗口 = 信号日之后第 1~5 个交易日（含终点第 5 日）。返回日期列表；覆盖不足返回 None。"""
    days, d = [], pub_date
    for _ in range(5):
        d = next_td(d)
        if d is None:
            return None
        days.append(d)
    return days


def _calc_nd(sig):
    """无周期方向（spec=nd）：未来 5 个交易日收益率单点计分。
    r5 = 信号日后第 5 个交易日收盘相对参考价的涨幅（与 ref 比较）；
    ret = r5，score = d × ret × 100；ref 同 §4（30 分钟口径，无则日线降级）。
    终点覆盖检查（第 5 个交易日超出数据 → 待验证）。"""
    pub, d, idx = sig['pub'], sig['d'], normalize_idx(sig['idx'])
    pd_ = pub[:10]
    days = _nd_window_days(pd_)
    if days is None:
        return dict(sig, ref=None, ep=None, epc=None, ret=None, score=None, note='待验证')
    ep = days[-1]
    if ep > LAST[idx]:
        return dict(sig, ref=None, ep=ep, epc=None, ret=None, score=None, note='待验证')
    # 参考价：优先 30 分钟口径，无 30 分钟数据退回日线（与 calc 主/降级路径一致）
    if _has_intraday(idx):
        ref, ok = ref_price_at(idx, pub)
    else:
        ref_date = ref_date_of(pub)
        ref_ok = (ref_date in IDX['创业板指'] and ref_date in IDX['科创50']) if idx == '双创' \
            else ref_date in IDX.get(idx, {})
        if not ref_ok:
            return dict(sig, ref=None, ep=ep, epc=None, ret=None, score=None, note='待验证')
        ref, ok = ref_price_of(idx, ref_date), True
    if not ok or ref is None:
        return dict(sig, ref=None, ep=ep, epc=None, ret=None, score=None, note='待验证')
    # 每日收盘（15:00 bar close == 日线收盘，与 _ep_close 同口径）
    closes = []
    for day in days:
        if idx == '双创':
            if day not in IDX['创业板指'] or day not in IDX['科创50']:
                return dict(sig, ref=round(ref, 2), ep=ep, epc=None, ret=None, score=None, note='待验证')
            closes.append((IDX['创业板指'][day]['收盘'] + IDX['科创50'][day]['收盘']) / 2)
        else:
            row = IDX.get(idx, {}).get(day)
            if row is None:
                return dict(sig, ref=round(ref, 2), ep=ep, epc=None, ret=None, score=None, note='待验证')
            closes.append(row['收盘'])
    # 未来 5 个交易日收益率单点（days 恒为 5 个交易日，索引 4 = 第 5 日）
    ret = closes[4] / ref - 1
    return dict(sig, ref=round(ref, 2), ep=ep, epc=round(closes[4], 2),
                ret=ret, score=round(d * ret * 100, 2), note='5日')


def calc(sig):
    """单条信号计算（统一 30 分钟口径）。返回行 dict：计分行带 ref/ep/epc/ret/score；单列行原样带 note"""
    sig = dict(sig)
    sig.setdefault('s', 1)     # 省略 s 的信号计分时按 moderate(1) 处理
    cat = sig.get('cat', 'scored')
    spec = sig.get('spec')
    # ① 单列不计分：新 schema unscored / spec=long / 旧数据 无预测周期·目标点位 / yearend
    if cat in ('无预测周期', '目标点位') or cat == 'unscored' or spec in ('long', 'yearend'):
        return dict(sig, ref=None, ep=None, epc=None, ret=None, score=None, note='不计分')
    # ② 旧数据 无效-日内 → 语义="今天"，按新规则重新判定（盘后/非交易日由下方有效性/顺延规则拦截）
    if cat == '无效-日内':
        spec = 'today'
        sig['spec'] = 'today'                          # 写回，供报告按周期归类/逐条表展示
    if spec is None:                                   # 防御：缺 spec 的手写单列（如 cat='待验证'）→ 无法定终点
        return dict(sig, ref=None, ep=None, epc=None, ret=None, score=None, note='待验证')
    pub, d, idx = sig['pub'], sig['d'], normalize_idx(sig['idx'])
    pd_, hhmm = pub[:10], pub[11:]
    # ②b 非交易日发布的"今天" → 报错单列不计分（SKILL §3：today 必须交易日发布，否则报错；不再顺延）
    if spec == 'today' and pd_ not in CAL_SET:
        return dict(sig, ref=None, ep=None, epc=None, ret=None, score=None, note='报错')
    if spec == 'nd':
        # 无周期方向：未来 5 个交易日收益率单点计分，独立成档（SKILL §3/§5）
        return _calc_nd(sig)
    ep = endpoint_of(pd_, spec)
    if ep is None:
        return dict(sig, ref=None, ep=None, epc=None, ret=None, score=None, note='待验证')
    # ③ 过时：终点早于发布日，或终点=发布日但已收盘（盘后"今天"）→ 无效-过时（无 无效-日内 备注）
    if ep < pd_ or (ep == pd_ and hhmm >= '15:00'):
        return dict(sig, ref=None, ep=ep, epc=None, ret=None, score=None, note='无效-过时')
    # ④ 无 30 分钟数据 → 退回日线口径（防御降级）
    if not _has_intraday(idx):
        return _calc_daily_fallback(sig)
    # ⑤ 参考价（SKILL §4）
    ref, ok = ref_price_at(idx, pub)
    if not ok:
        return dict(sig, ref=None, ep=ep, epc=None, ret=None, score=None, note='待验证')
    epc, ok = _ep_close(idx, ep)
    if not ok:                                         # 终点超出数据覆盖（未来）→ 待验证
        return dict(sig, ref=round(ref, 2), ep=ep, epc=None, ret=None, score=None, note='待验证')
    ret = epc / ref - 1
    # ⑥ 盘中"今天"（当日交易时间内发布）才标 日内；非交易日/盘前"今天"不标
    note = '日内' if (spec == 'today' and pd_ in CAL_SET and '09:30' <= hhmm < '15:00') else ''
    return dict(sig, ref=round(ref, 2), ep=ep, epc=round(epc, 2),
                ret=ret, score=round(d * ret * 100, 2), note=note)


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


def t_stat_of(rs):
    """t 值 = 平均分 / 标准误 = 平均分 / (波动率/√n)；信号不足 2 条或波动率为 0 时返回 None"""
    n = len(rs)
    v = vol_of(rs)
    if n < 2 or v == 0:
        return None
    return avg_of(rs) / (v / (n ** 0.5))


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
    scored_t = [r for r in scored if r['spec'] != 'nd']   # 显式周期信号（总榜/多空/三档口径，SKILL §8）
    nd_rows = [r for r in scored if r['spec'] == 'nd']    # 无周期方向（独立档）
    n_unc = sum(1 for r in rows if r['note'] == '不计分')
    n_day_intra = sum(1 for r in scored if r['note'] == '日内')
    n_pend = sum(1 for r in rows if r['note'] == '待验证')
    n_stale = sum(1 for r in rows if r['note'] == '无效-过时')
    n_err = sum(1 for r in rows if r['note'] == '报错')
    eligible, span_months, nsig = eligibility(blogger)
    bottom_rows, top_rows = pivot_split(scored)           # 抄底/逃顶保留 nd（拐点定位与周期无关）

    n_pos = sum(1 for r in scored_t if r['score'] > 0)
    n_zero = sum(1 for r in scored_t if r['score'] == 0)
    den = len(scored_t) - n_zero
    acc = n_pos / den * 100 if den else 0.0
    avg = avg_of(scored_t)
    if scored_t:
        mx = max(scored_t, key=lambda r: r['score'])
        mn = min(scored_t, key=lambda r: r['score'])
        bull = [r for r in scored_t if r['d'] == 1]
        bear = [r for r in scored_t if r['d'] == -1]
        strong = [r for r in scored_t if r['s'] == 2]
        moderate = [r for r in scored_t if r['s'] == 1]
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
    L.append(f'> 信号总数：{len(rows)} 条（显式周期计分 {len(scored_t)} + 无周期方向 {len(nd_rows)} + 不计分 {n_unc} + 待验证 {n_pend} + 无效-过时 {n_stale} + 报错 {n_err}）')
    L.append('')
    L.append('---')
    L.append('')
    if not eligible:
        L.append('> ⚠️ **不满足参与打分资格**（帖子跨度 ≥6 个月且 2026 以来信号 >50 条才参与打分）：')
        L.append(f'> 帖子跨度 {span_months:.1f} 个月' + ('（达标）' if span_months >= 6 else '（< 6 个月，不达标）') +
                 f'；2026 以来信号 {nsig} 条' + ('（达标）' if nsig > 50 else '（≤ 50 条，不达标）'))
        L.append('> 该博主**不参与打分与排名**，以下仅展示原始信号概览（无汇总指标/分类表）。')
        L.append('')
        L.append('---')
        L.append('')
    if eligible:
        L.append('## 📊 汇总指标')
        L.append('')
        L.append('```')
        L.append(f'信号总数：{len(scored_t)}')
        L.append(f'  另有：无周期方向 nd {len(nd_rows)} 条（并入总榜综合口径，未来 5 个交易日收益率单点计分，不参与多空/三档）/ unscored {n_unc} 条（spec=long）/ 无效-过时 {n_stale} 条 / 待验证 {n_pend} 条 / 报错 {n_err} 条（单列，不计分）')
        L.append(f'方向正确：{n_pos}（正确率 {acc:.1f}% = score>0 信号数 / {den}；score=0 计"平" {n_zero} 条，不计入分子分母）')
        L.append(f'  - strong 正确：{st[0]}/{st[1]}（正确率 {st[2]:.1f}%）' if st else '  - strong 正确：—')
        L.append(f'  - moderate 正确：{md[0]}/{md[1]}（正确率 {md[2]:.1f}%）' if md else '  - moderate 正确：—')
        L.append(f'平均分：{avg:+.2f}（= 单信号平均收益 %，核心指标，仅显式周期信号）')
        L.append(f'综合均分（含 nd）：{avg_of(scored):+.2f}（{len(scored)} 条）= 显式周期 + 无周期方向 nd 合并口径，总榜按 t 值排序采用')
        L.append(f'波动率：{vol_of(scored_t):.2f}（单信号 score 样本标准差）')
        sh = sharpe_of(scored_t)
        L.append(f'夏普：{sh:+.2f}（= 平均分 / 波动率）' if sh is not None else '夏普：—（信号 <2 条或波动率为 0）')
        L.append(f'最高分：{mx["score"]:+.2f} / 最低分：{mn["score"]:+.2f}' if mx else '最高分：— / 最低分：—')
        L.append(f'看多平均分：{avg_of(bull):+.2f}（{len(bull)} 条）  看空平均分：{avg_of(bear):+.2f}（{len(bear)} 条）')
        L.append(f'抄底平均分：{avg_of(bottom_rows):+.2f}（{len(bottom_rows)} 条）= 底部拐点当天及前一天（2 交易日）窗口内信号的平均分' if bottom_rows else '抄底平均分：—（0 条）')
        L.append(f'逃顶平均分：{avg_of(top_rows):+.2f}（{len(top_rows)} 条）= 顶部拐点当天及前一天（2 交易日）窗口内信号的平均分' if top_rows else '逃顶平均分：—（0 条）')
        L.append('```')
        L.append('')

    def class_table(title, groups):
        L.append(f'### {title}')
        L.append('')
        L.append('| 分类 | 信号数 | 平均分 | 胜率 | 波动率 | 夏普 |')
        L.append('|:---|:---:|:---:|:---:|:---:|:---:|')
        for label, rs in groups:
            if not rs:
                continue
            p, dd, rate = acc_of(rs)
            if len(rs) < 2:
                vol_txt = shp_txt = '—'
            else:
                sh = sharpe_of(rs)
                vol_txt = f'{vol_of(rs):.2f}'
                shp_txt = f'{sh:+.2f}' if sh is not None else '—'
            L.append(f'| {label} | {len(rs)} | {avg_of(rs):+.2f} | {rate:.1f}% | {vol_txt} | {shp_txt} |')
        L.append('')

    if eligible:
        # 按预测指数
        byidx = defaultdict(list)
        for r in scored_t:
            byidx[r['idx']].append(r)
        class_table('按预测指数分类', [(IDX_SHORT.get(k, k), v) for k, v in sorted(byidx.items())])

        # 按多空（仅显式周期信号）
        class_table('按多空分类', [('看多 bullish', bull), ('　└ strong', strong), ('　└ moderate', moderate),
                                  ('看空 bearish', bear)])

        # 按预测期限（三档归类：信号日→验证终点交易日数，与 comparison 表1 同口径，SKILL.md 输出部分；
        # 无周期方向 nd 独立成档，不混入三档）
        horizon = defaultdict(list)
        for r in scored_t:
            horizon[bucket_of(r)].append(r)
        today_sub = [r for r in scored_t if r['spec'] == 'today']
        groups = []
        for k in ['0-1个交易日（今天/明天）', '2-5个交易日（1周内）', '6个交易日及以上（大于1周）']:
            groups.append((k, horizon.get(k, [])))
            if k == '0-1个交易日（今天/明天）':
                groups.append(('　└ 其中：今天（盘前/盘中）', today_sub))
        groups.append(('无周期方向（无明确时间点，未来 5 个交易日收益率单点计分）', nd_rows))
        class_table('按预测周期分类（三档：信号日→验证终点交易日数 0-1/2-5/≥6；"今天（盘前/盘中）"在 0-1 档下单列子行；无周期方向独立成档）', groups)

        # 按抄底逃顶（SKILL §6：拐点取自 knowledge/market_analysis.md §5.1/§5.2，窗口=拐点当天+前一交易日）
        class_table('按抄底逃顶分类（底部拐点窗口内信号=抄底、顶部=逃顶，窗口=拐点当天+前一交易日）',
                    [('抄底（底部拐点窗口内）', bottom_rows), ('逃顶（顶部拐点窗口内）', top_rows)])

        # 月度表现（仅显式周期信号，nd 独立成档不混入）
        L.append('### 月度表现')
        L.append('')
        L.append('| 月份 | 信号数 | 平均分 | 胜率 | 波动率 | 夏普 |')
        L.append('|:---|:---:|:---:|:---:|:---:|:---:|')
        bymonth = defaultdict(list)
        for r in scored_t:
            bymonth[r['pub'][:7]].append(r)
        for mo in sorted(bymonth):
            rs = bymonth[mo]
            p, dd, rate = acc_of(rs)
            if len(rs) < 2:
                vol_txt = shp_txt = '—'
            else:
                sh = sharpe_of(rs)
                vol_txt = f'{vol_of(rs):.2f}'
                shp_txt = f'{sh:+.2f}' if sh is not None else '—'
            L.append(f'| {mo} | {len(rs)} | {avg_of(rs):+.2f} | {rate:.1f}% | {vol_txt} | {shp_txt} |')
        L.append('')
    else:
        # 不参与打分的博主仍展示信号时间分布（bymonth 供下方时间分布节使用）
        bymonth = defaultdict(list)
        for r in scored_t:
            bymonth[r['pub'][:7]].append(r)

    # 信号时间分布与集中度/覆盖度分析（口径=显式周期信号，nd 独立档不计入）
    L.append('### ⏱️ 信号时间分布与集中度')
    L.append('')
    first_d = min(r['pub'][:10] for r in scored_t) if scored_t else '-'
    last_d = max(r['pub'][:10] for r in scored_t) if scored_t else '-'
    top_mo = max(bymonth.items(), key=lambda kv: len(kv[1])) if bymonth else None
    top_n = len(top_mo[1]) if top_mo else 0
    conc = top_n / len(scored_t) * 100 if scored_t else 0
    L.append(f'- 覆盖：{first_d} ~ {last_d}，共 {len(bymonth)} 个月，{len(scored_t)} 条显式周期计分信号（另有无周期方向 {len(nd_rows)} 条）；单月最高占比 {conc:.0f}%'
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
    NOTE_TXT = {'不计分': '不计分', '无效-过时': '无效-过时', '待验证': '待验证', '报错': '报错'}
    for i, r in enumerate(rows, 1):
        ico = D_ICO.get(r['d'], '')
        stx = S_TXT.get(r.get('s', 1), 'mod')
        if r['score'] is not None:
            period = period_text(r['spec'])
        elif r['note'] == '不计分':
            # spec=long → "长期"；旧数据 无预测周期/目标点位（无 spec）→ 显示原 cat 标签
            period = period_text(r['spec']) if r.get('spec') == 'long' else CAT_PERIOD.get(r['cat'], '长期')
        elif r['note'] in ('待验证', '无效-过时') and r.get('spec'):
            period = period_text(r['spec'])      # 单列行显示实际预测周期（如"明天"）
        else:
            period = CAT_PERIOD.get(r['note'], r['note'])
        idx = IDX_SHORT.get(r['idx'], r['idx'])
        ref_txt = f"{r['ref']:.2f}" if r.get('ref') is not None else '-'
        if r['score'] is not None:
            note_txt = r['note'] if r.get('note') else '—'
            # nd 无周期方向：ep=第 5 交易日即 return 终点，直接显示真实日期（与其它行一致）
            ep_txt = r['ep'][5:]
            L.append(f"| {i} | {r['pub'][5:10]} | {r['summary']} | {ico} | {stx} | {period} | {idx} | {ref_txt} | {ep_txt} | {r['epc']:.2f} | {r['ret']*100:+.2f}% | {r['score']:+.2f} | {note_txt} |")
        elif r['note'] == '待验证':
            L.append(f"| {i} | {r['pub'][5:10]} | {r['summary']} | {ico} | {stx} | {period} | {idx} | {ref_txt} | - | - | - | - | 待验证 |")
        elif r['note'] == '无效-过时':
            L.append(f"| {i} | {r['pub'][5:10]} | {r['summary']} | {ico} | {stx} | {period} | {idx} | {ref_txt} | {r['ep'][5:]} | - | - | - | 无效-过时 |")
        else:
            L.append(f"| {i} | {r['pub'][5:10]} | {r['summary']} | {ico} | {stx} | {period} | {idx} | - | - | - | - | - | {NOTE_TXT.get(r['note'], r['note'])} |")
    L.append('')
    L.append('---')
    L.append('')
    if eligible:
        L.append('## 🔍 观察要点')
        L.append('')
        verdict = '具备统计优势' if acc >= 55 else '接近抛硬币水平，没有统计优势'
        L.append(f'- **方向正确率 {acc:.1f}%**（{n_pos}/{den}，终点收益判定；score=0 计"平" {n_zero} 条）——{verdict}。')
        L.append(f'- **看多 {len(bull)} 条平均 {avg_of(bull):+.2f} 分（胜率 {acc_of(bull)[2]:.1f}%）vs 看空 {len(bear)} 条平均 {avg_of(bear):+.2f} 分（胜率 {acc_of(bear)[2]:.1f}%）。')
        L.append(f'- **抄底/逃顶**：抄底平均 {avg_of(bottom_rows):+.2f} 分（{len(bottom_rows)} 条，底部拐点窗口内）；逃顶平均 {avg_of(top_rows):+.2f} 分（{len(top_rows)} 条，顶部拐点窗口内）。')
        if horizon:
            best_p = max(horizon.items(), key=lambda kv: (avg_of(kv[1]), len(kv[1])))
            worst_p = min(horizon.items(), key=lambda kv: (avg_of(kv[1]), -len(kv[1])))
            L.append(f'- **预测期限**："{best_p[0]}"最强（{len(best_p[1])} 条，平均 {avg_of(best_p[1]):+.2f} 分）；"{worst_p[0]}"最弱（{len(worst_p[1])} 条，平均 {avg_of(worst_p[1]):+.2f} 分）。')
        if byidx:
            best_i = max(byidx.items(), key=lambda kv: (avg_of(kv[1]), len(kv[1])))
            worst_i = min(byidx.items(), key=lambda kv: (avg_of(kv[1]), -len(kv[1])))
            L.append(f'- **预测指数**：{IDX_SHORT.get(best_i[0], best_i[0])} 最强（{len(best_i[1])} 条，平均 {avg_of(best_i[1]):+.2f} 分）；{IDX_SHORT.get(worst_i[0], worst_i[0])} 最弱（{len(worst_i[1])} 条，平均 {avg_of(worst_i[1]):+.2f} 分）。')
        if mx:
            L.append(f'- **最大单条命中**：{mx["pub"][5:10]}"{mx["summary"][:24]}"（{mx["score"]:+.2f} 分）。')
            L.append(f'- **最大单条失误**：{mn["pub"][5:10]}"{mn["summary"][:24]}"（{mn["score"]:+.2f} 分）。')
        if nd_rows:
            L.append(f'- **无周期方向 nd {len(nd_rows)} 条**：无明确时间点，按未来 5 个交易日收益率单点（r5）计分；平均 {avg_of(nd_rows):+.2f} 分（胜率 {acc_of(nd_rows)[2]:.1f}%），并入总榜综合口径，不参与看多看空/周期三档。')
        if n_day_intra:
            L.append(f'- **盘中"今天" {n_day_intra} 条已按 30 分钟线日内窗口计分**（参考价=所处 30 分钟 K 线开盘价，终点=当日收盘）。')
        if n_unc:
            L.append(f'- **unscored {n_unc} 条不计分（spec=long）**：无时间承诺的目标点位、年度预测、中长期等，单独统计。')
        if n_pend:
            L.append(f'- **待验证 {n_pend} 条**：验证终点超出数据覆盖范围，等数据覆盖后补算。')
        if n_stale:
            L.append(f'- **无效-过时 {n_stale} 条**：验证终点 ≤ 发布日且已收盘（如盘后发"今天"、周五盘后发"本周"），引擎自动单列不计分。')
        if n_err:
            L.append(f'- **报错 {n_err} 条**：非交易日发布"今天"（today 必须交易日发布），引擎自动单列不计分。')
        L.append('')
    out = os.path.join(REPORTS_DIR, f'{blogger}_direction.md')
    with open(out, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L))
    print(f'{blogger}: 参与打分 {len(scored_t)} (+无周期方向 {len(nd_rows)}) | 正确率 {acc:.1f}% ({n_pos}/{den}) | 平均分 {avg:+.2f} | 报告已写入 {out}')
    return {'blogger': blogger, 'scored': len(scored_t), 'nd': len(nd_rows), 'acc': acc, 'avg': avg}


def selftest():
    """引擎自测：锁定 SKILL §4 参考价 / §3 终点与不计分 / 三档归类边界"""
    errors = []

    def check(cond, msg):
        if not cond:
            errors.append(msg)

    # ── §4 参考价：交易时间中 → 所处 30 分钟 K 线开盘价（bar 时间=收盘时间） ──
    # 盘中"今天" 01-28 10:06 → 10:30 bar open=4147.715→4147.72；终点=当日收盘 4151.238→4151.24
    r = calc({'pub': '2026-01-28 10:06', 'd': 1, 's': 1, 'idx': '上证指数', 'spec': 'today',
              'summary': 'test', 'cat': 'scored'})
    check(r['score'] is not None, '盘中today无score')
    check(r['note'] == '日内', f"盘中today未标日内 note={r['note']}")
    check(r['ref'] == 4147.72, f"盘中today ref={r['ref']} 期望 4147.72")
    check(r['ep'] == '2026-01-28', f"盘中today ep={r['ep']}")
    check(r['epc'] == 4151.24, f"盘中today epc={r['epc']} 期望 4151.24")

    # ── §4 参考价：盘前（<9:30）→ 上一交易日 15:00 close（4112.601→4112.60）；计分非日内 ──
    r = calc({'pub': '2026-01-16 09:12', 'd': 1, 's': 1, 'idx': '上证指数', 'spec': 'today',
              'summary': 'test', 'cat': 'scored'})
    check(r['score'] is not None, '盘前today无score')
    check(r['note'] == '', f"盘前today note={r['note']}")
    check(r['ref'] == 4112.60, f"盘前today ref={r['ref']} 期望 4112.60")

    # ── §4 参考价：午休 11:30~13:00 → 11:30 close（4160.006→4160.01），终点正常 ──
    r = calc({'pub': '2026-01-28 12:05', 'd': 1, 's': 1, 'idx': '上证指数', 'spec': 't1',
              'summary': 'test', 'cat': 'scored'})
    check(r['ref'] == 4160.01, f"午休t1 ref={r['ref']} 期望 4160.01")
    check(r['ep'] == '2026-01-29', f"午休t1 ep={r['ep']}")

    # ── §4 参考价：盘后（≥15:00）→ 当日 15:00 close（4151.238→4151.24） ──
    r = calc({'pub': '2026-01-28 15:06', 'd': 1, 's': 1, 'idx': '上证指数', 'spec': 't1',
              'summary': 'test', 'cat': 'scored'})
    check(r['ref'] == 4151.24, f"盘后t1 ref={r['ref']} 期望 4151.24")
    check(r['ep'] == '2026-01-29', f"盘后t1 ep={r['ep']}")

    # ── §4 参考价：非交易日 → 上一交易日 15:00 close（01-23 close=4136.164→4136.16） ──
    r = calc({'pub': '2026-01-24 14:44', 'd': 1, 's': 1, 'idx': '上证指数', 'spec': 'nweek',
              'summary': 'test', 'cat': 'scored'})
    check(r['ref'] == 4136.16, f"周六nweek ref={r['ref']} 期望 4136.16")
    check(r['ep'] == '2026-01-30', f"周六nweek ep={r['ep']}")

    # ── 盘后"今天" → 无效-过时（不再有 无效-日内） ──
    r = calc({'pub': '2026-01-28 15:06', 'd': 1, 's': 1, 'idx': '上证指数', 'spec': 'today',
              'summary': 'test', 'cat': 'scored'})
    check(r['note'] == '无效-过时', f"盘后today未判无效-过时 note={r['note']}")
    check(r['score'] is None, '盘后today不应有score')

    # ── 非交易日"今天" → 报错单列不计分（SKILL §3：today 必须交易日发布，否则报错；不再顺延） ──
    r = calc({'pub': '2026-01-31 15:00', 'd': 1, 's': 1, 'idx': '上证指数', 'spec': 'today',
              'summary': 'test', 'cat': 'scored'})
    check(r['note'] == '报错', f"周六today未判报错 note={r['note']}")
    check(r['score'] is None, '周六today不应有score')

    # ── 双创盘中"今天" → 两指数 30 分钟均值（ref=2449.857→2449.86，epc=2439.183→2439.18） ──
    r = calc({'pub': '2026-01-28 10:06', 'd': 1, 's': 1, 'idx': '双创', 'spec': 'today',
              'summary': 'test', 'cat': 'scored'})
    check(r['score'] is not None, '双创today无score')
    check(r['ref'] == 2449.86, f"双创today ref={r['ref']} 期望 2449.86")
    check(r['epc'] == 2439.18, f"双创today epc={r['epc']} 期望 2439.18")

    # ── 不计分：新 schema unscored/long / 旧 无预测周期·目标点位 / yearend → note=不计分 score=None ──
    for sig in [
        {'pub': '2026-01-07 15:08', 'd': 1, 's': 1, 'idx': '上证指数', 'spec': 'long', 'summary': 't', 'cat': 'unscored'},
        {'pub': '2026-01-07 15:08', 'd': 1, 's': 1, 'idx': '上证指数', 'spec': 'long', 'summary': 't', 'cat': 'scored'},
        {'pub': '2026-01-07 15:08', 'd': 1, 's': 1, 'idx': '上证指数', 'spec': 'yearend', 'summary': 't', 'cat': 'scored'},
        {'pub': '2026-01-07 15:08', 'd': 1, 's': 1, 'idx': '上证指数', 'summary': 't', 'cat': '无预测周期'},
        {'pub': '2026-01-07 15:08', 'd': 1, 's': 1, 'idx': '上证指数', 'summary': 't', 'cat': '目标点位'},
    ]:
        r = calc(sig)
        check(r['note'] == '不计分', f"不计分失败 cat={sig['cat']} spec={sig.get('spec')} note={r['note']}")
        check(r['score'] is None, f"不计分却有score cat={sig['cat']} spec={sig.get('spec')}")

    # endpoint_of 对 long/yearend 返回 None 且不抛异常
    check(endpoint_of('2026-01-07', 'long') is None, 'endpoint_of(long) 应为 None')
    check(endpoint_of('2026-01-07', 'yearend') is None, 'endpoint_of(yearend) 应为 None')

    # ── 旧数据兼容：cat=无效-日内 → 推断 spec=today：盘中计分 / 盘后无效-过时 / 非交易日顺延 ──
    r = calc({'pub': '2026-02-02 11:35', 'd': 1, 's': 1, 'idx': '上证指数', 'summary': '午后大概率震荡回升', 'cat': '无效-日内'})
    check(r['score'] is not None, f"旧无效-日内盘中未计分 note={r['note']}")
    check(r['note'] == '日内', f"旧无效-日内盘中 note={r['note']}")
    check(r['spec'] == 'today', f"旧无效-日内未写回 spec={r.get('spec')}")
    r = calc({'pub': '2026-02-02 15:06', 'd': 1, 's': 1, 'idx': '上证指数', 'summary': '今天收红', 'cat': '无效-日内'})
    check(r['note'] == '无效-过时', f"旧无效-日内盘后未判无效-过时 note={r['note']}")
    r = calc({'pub': '2026-01-31 15:00', 'd': 1, 's': 1, 'idx': '上证指数', 'summary': '今天', 'cat': '无效-日内'})
    check(r['note'] == '报错', f"旧无效-日内非交易日未判报错 note={r['note']}")
    check(r['score'] is None, '旧无效-日内非交易日不应有score')

    # 缺 spec 防御：scored 信号无 spec → 无法定终点 → 待验证（不崩溃）
    r = calc({'pub': '2026-01-07 15:08', 'd': 1, 'idx': '上证指数', 'summary': 'test', 'cat': 'scored'})
    check(r['note'] == '待验证', f"缺 spec 防御失败 note={r['note']}")

    # ── bucket_of 三档归类（信号日→验证终点交易日数，SKILL.md/comparison 表1 同口径，全角标签）──
    r = calc({'pub': '2026-01-28 10:06', 'd': 1, 's': 1, 'idx': '上证指数', 'spec': 'today', 'summary': 'test', 'cat': 'scored'})
    check(bucket_of(r) == '0-1个交易日（今天/明天）', f"bucket 盘中today={bucket_of(r)}")
    r = calc({'pub': '2026-01-07 15:08', 'd': -1, 's': 1, 'idx': '上证指数', 'spec': 't1', 'summary': 'test', 'cat': 'scored'})
    check(bucket_of(r) == '0-1个交易日（今天/明天）', f"bucket t1={bucket_of(r)}")
    r = calc({'pub': '2026-01-24 14:44', 'd': 1, 's': 1, 'idx': '上证指数', 'spec': 'nweek', 'summary': 'test', 'cat': 'scored'})
    check(bucket_of(r) == '2-5个交易日（1周内）', f"bucket 周六nweek={bucket_of(r)}")
    r = calc({'pub': '2026-01-07 15:08', 'd': 1, 's': 1, 'idx': '上证指数', 'spec': 't10', 'summary': 'test', 'cat': 'scored'})
    check(bucket_of(r) == '6个交易日及以上（大于1周）', f"bucket t10={bucket_of(r)}")

    # ── nd 无周期方向：独立成档 bucket + 未来 5 个交易日收益率单点计分（ep=第 5 交易日，note=5日） ──
    r = calc({'pub': '2026-01-07 15:08', 'd': 1, 's': 1, 'idx': '上证指数', 'spec': 'nd', 'summary': 'test', 'cat': 'scored'})
    check(bucket_of(r) == '无周期方向', f"bucket nd={bucket_of(r)}")
    check(r['score'] is not None, 'nd 无 score')
    check(r['note'] == '5日', f"nd note={r['note']}")
    days = _nd_window_days('2026-01-07')
    check(days is not None and len(days) == 5, f"_nd_window_days 窗口长度={None if days is None else len(days)}")
    check(r['ep'] == days[-1], f"nd ep={r['ep']} 期望 {days[-1]}")
    closes = [IDX['上证指数'][d]['收盘'] for d in days]
    expect_epc = round(closes[4], 2)
    check(r['epc'] == expect_epc, f"nd epc={r['epc']} 期望 {expect_epc}（第 5 日收盘）")
    # ret = r5（未来 5 个交易日单点）；r 用未取整的 ref 计算（仅显示 ref 取整），与 calc 主路径一致
    ref_raw, _ = ref_price_at('上证指数', '2026-01-07 15:08')
    expect_ret = closes[4] / ref_raw - 1
    check(abs(r['ret'] - expect_ret) < 1e-9, f"nd ret={r['ret']} 与手算不符（期望 {expect_ret}）")
    # 覆盖不足（信号日离数据末日不足 5 个交易日）→ 待验证
    r2 = calc({'pub': '2026-08-25 15:08', 'd': 1, 's': 1, 'idx': '上证指数', 'spec': 'nd', 'summary': 'test', 'cat': 'scored'})
    check(r2['note'] == '待验证', f"nd 覆盖不足未判待验证 note={r2['note']}")
    check(r2['score'] is None, 'nd 覆盖不足不应有 score')
    # ── 边界锁定（审计确认的正确"反直觉"落位，防未来回归改错）──
    # 非交易日"明天"→base=前一交易日，ep=下周一，span=1 → 0-1 档（SKILL.md 边界示例）
    r = calc({'pub': '2026-01-24 09:00', 'd': 1, 's': 1, 'idx': '上证指数', 'spec': 't1', 'summary': 'test', 'cat': 'scored'})
    check(bucket_of(r) == '0-1个交易日（今天/明天）', f"bucket 非交易日t1={bucket_of(r)}")
    # 周五发"本周"→ep=当天，span=0 → 0-1 档
    r = calc({'pub': '2026-02-06 14:19', 'd': 1, 's': 1, 'idx': '上证指数', 'spec': 'week', 'summary': 'test', 'cat': 'scored'})
    check(bucket_of(r) == '0-1个交易日（今天/明天）', f"bucket 周五week={bucket_of(r)}")
    # 周三发"本周"→ep=次日(周四)，span=1 → 0-1 档
    r = calc({'pub': '2026-04-29 12:11', 'd': 1, 's': 1, 'idx': '上证指数', 'spec': 'week', 'summary': 'test', 'cat': 'scored'})
    check(bucket_of(r) == '0-1个交易日（今天/明天）', f"bucket 周三week={bucket_of(r)}")
    # 月底前最后交易日当天发→ep=当天，span=0 → 0-1 档
    r = calc({'pub': '2026-02-27 13:20', 'd': 1, 's': 1, 'idx': '上证指数', 'spec': 'month', 'summary': 'test', 'cat': 'scored'})
    check(bucket_of(r) == '0-1个交易日（今天/明天）', f"bucket 月末month={bucket_of(r)}")
    # 周三发"下周"、隔春节长假→span=8 → ≥6 档（数交易日非日历相减）
    r = calc({'pub': '2026-02-04 13:33', 'd': 1, 's': 1, 'idx': '上证指数', 'spec': 'nweek', 'summary': 'test', 'cat': 'scored'})
    check(bucket_of(r) == '6个交易日及以上（大于1周）', f"bucket 隔长假nweek={bucket_of(r)}")
    # 后天 t2→ep=后第2交易日，span=2 → 2-5 档
    r = calc({'pub': '2026-01-07 15:08', 'd': 1, 's': 1, 'idx': '上证指数', 'spec': 't2', 'summary': 'test', 'cat': 'scored'})
    check(bucket_of(r) == '2-5个交易日（1周内）', f"bucket t2={bucket_of(r)}")

    # ── 抄底/逃顶窗口（拐点取自 market_analysis.md §5.1/§5.2，窗口=拐点当天+前一交易日）──
    check(('M7', '2026-07-20', 'bottom') in PIVOTS, f"PIVOTS 缺 M7 底部拐点：{[p for p in PIVOTS if p[0]=='M7']}")
    check(('M6', '2026-05-14', 'top') in PIVOTS, f"PIVOTS 缺 M6 顶部拐点：{[p for p in PIVOTS if p[0]=='M6']}")
    check(('I8', '2026-01-14', 'top') in PIVOTS, f"PIVOTS 缺 I8 顶部拐点：{[p for p in PIVOTS if p[0]=='I8']}")
    check(pivot_bucket('2026-07-17') == '抄底', f"M7窗口前一天未判抄底：{pivot_bucket('2026-07-17')}")
    check(pivot_bucket('2026-07-20') == '抄底', f"M7当天未判抄底：{pivot_bucket('2026-07-20')}")
    check(pivot_bucket('2026-07-21') is None, f"M7次日不应判抄底：{pivot_bucket('2026-07-21')}")
    check(pivot_bucket('2026-05-14') == '逃顶', f"M6顶部当天未判逃顶：{pivot_bucket('2026-05-14')}")
    check(pivot_bucket('2026-05-13') == '逃顶', f"M6顶部前一天未判逃顶：{pivot_bucket('2026-05-13')}")
    check(pivot_bucket('2026-06-01') is None, f"非拐点日不应归入抄底/逃顶：{pivot_bucket('2026-06-01')}")

    # ── t 值（平均分/标准误；n<2 或波动率=0 → None）──
    check(t_stat_of([{'score': 1.0}]) is None, 't_stat_of n<2 应返回 None')
    check(t_stat_of([{'score': 2.0}, {'score': 2.0}]) is None, 't_stat_of 波动率=0 应返回 None')
    rs_t = [{'score': 1.0}, {'score': 3.0}, {'score': 2.0}]
    # avg=2, 样本std=1, n=3 → t = 2/(1/√3) = 2√3 ≈ 3.4641
    check(abs(t_stat_of(rs_t) - 2 * (3 ** 0.5)) < 1e-9, f"t_stat_of 手算不符: {t_stat_of(rs_t)}")

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
        # 过滤 _ 前缀：_<名>_run.json 是提取脚本的 gitignored 运行溯源（signals 为 int 计数），非信号文件
        names = sorted(f[:-5] for f in os.listdir(DATA_DIR)
                       if f.endswith('.json') and not f.startswith('_'))
    # 打分前 30 分钟线完整性检查（SKILL：覆盖 2026 全部交易日、每交易日 8 根 bar）
    intra_warns = check_intraday()
    if intra_warns:
        print('⚠️ 30 分钟线完整性检查告警：')
        for w in intra_warns:
            print('  ', w)
    else:
        print(f'✅ 30 分钟线完整性检查通过（{len(IDX)} 指数覆盖至 {EVAL_DATE}，每交易日 8 根 bar）')
    results = {}
    for name in names:
        results[name] = generate(name)
