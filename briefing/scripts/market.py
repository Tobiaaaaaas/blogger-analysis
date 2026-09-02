# -*- coding: utf-8 -*-
"""大盘行情背景：指数实时点位/涨跌幅/成交额。

数据源（免费、无需密钥）：
  主：腾讯行情接口 qt.gtimg.cn（字段 [3]现价 [4]昨收 [30]时间戳 [32]涨跌幅% [37]成交额万）
  兜底1：新浪 hq.sinajs.cn
  兜底2：仓库内 data/market/market_data.json 最近收盘（离线/接口全挂时）
"""
import json
import os
import re

import requests

from . import paths

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36")

# 需要展示的指数：symbol, 中文名
QUOTES = [
    ("sh000001", "上证指数"),
    ("sz399001", "深证成指"),
    ("sz399006", "创业板指"),
]

TENCENT_URL = "https://qt.gtimg.cn/q={}"
SINA_URL = "https://hq.sinajs.cn/list={}"


def _parse_tencent(text):
    m = re.search(r'v_[a-z]+\d+="(.*)"', text)
    if not m:
        return None
    f = m.group(1).split("~")
    try:
        return {
            "name": f[1], "code": f[2],
            "price": float(f[3]), "prev_close": float(f[4]),
            "ts": f[30], "pct": float(f[32]),
            "amount_wan": float(f[37]),  # 成交额（万元）
        }
    except (IndexError, ValueError):
        return None


def _parse_sina(text):
    m = re.search(r'="(.*)"', text)
    if not m:
        return None
    f = m.group(1).split(",")
    try:
        return {
            "name": f[0], "price": float(f[3]), "prev_close": float(f[2]),
            "amount_wan": float(f[9]) / 1e4,  # 元 → 万元
        }
    except (IndexError, ValueError):
        return None


def _fetch_tencent(symbol):
    r = requests.get(TENCENT_URL.format(symbol), timeout=10,
                     headers={"User-Agent": UA, "Referer": "https://gu.qq.com/"})
    r.encoding = "gbk"
    return _parse_tencent(r.text)


def _fetch_sina(symbol):
    r = requests.get(SINA_URL.format(symbol), timeout=10,
                     headers={"User-Agent": UA, "Referer": "https://finance.sina.com.cn/"})
    r.encoding = "gbk"
    return _parse_sina(r.text)


def _fallback_from_repo(name):
    """接口全挂时用仓库日线最近收盘。"""
    try:
        fp = os.path.join(paths.MARKET_DIR, "market_data.json")
        with open(fp, encoding="utf-8") as f:
            mkt = json.load(f)
        rows = mkt.get(name, [])
        if rows:
            last = rows[-1]
            prev = rows[-2] if len(rows) > 1 else last
            price, prev_close = last["收盘"], prev["收盘"]
            pct = (price - prev_close) / prev_close * 100 if prev_close else 0.0
            return {"name": name, "price": price, "prev_close": prev_close,
                    "pct": pct, "amount_wan": None, "source": "repo-daily"}
    except Exception:
        pass
    return None


def fetch_quotes():
    """返回 {指数名: {name, price, prev_close, pct, amount_wan, source}}。"""
    out = {}
    for symbol, name in QUOTES:
        q = None
        try:
            q = _fetch_tencent(symbol)
        except Exception:
            q = None
        if not q:
            try:
                q = _fetch_sina(symbol)
            except Exception:
                q = None
        if not q:
            q = _fallback_from_repo(name)
        if q:
            q.setdefault("source", "tencent" if q.get("ts") is not None else "sina")
            out[name] = q
    return out


def _fmt_amount(wan):
    """万元 → 亿/万亿 可读字符串。"""
    if wan is None:
        return "?"
    yi = wan / 1e4
    if yi >= 10000:
        return f"{yi / 1e4:.2f}万亿"
    return f"{yi:.0f}亿"


def market_line(quotes: dict) -> str:
    """渲染卡片行情行：上证 xx (+0.32%) · 深成 … · 两市 1234亿"""
    parts = []
    for name in ["上证指数", "深证成指", "创业板指"]:
        q = quotes.get(name)
        if not q:
            continue
        sign = "+" if q["pct"] >= 0 else ""
        parts.append(f"{q['name'].replace('指数', '')} {q['price']:.2f} {sign}{q['pct']:.2f}%")
    # 两市成交额 = 沪市 + 深市
    total_wan = sum(q["amount_wan"] for k, q in quotes.items()
                    if q["amount_wan"] and k in ("上证指数", "深证成指"))
    if total_wan:
        parts.append(f"两市 {_fmt_amount(total_wan)}")
    return " · ".join(parts) if parts else "行情数据获取失败"


def heartbeat_line(quotes: dict) -> str:
    """心跳消息行情部分：上证 3979.89 -0.16% · 深成 13872 -1.02%"""
    parts = []
    for name in ["上证指数", "深证成指"]:
        q = quotes.get(name)
        if not q:
            continue
        sign = "+" if q["pct"] >= 0 else ""
        parts.append(f"{q['name'].replace('指数', '')} {q['price']:.2f} {sign}{q['pct']:.2f}%")
    return " | ".join(parts) if parts else "行情数据获取失败"
