# -*- coding: utf-8 -*-
"""博主画像档案生成（一次性/可重跑）：data/briefing/profiles.json。

画像字段（注入综合 prompt，让点评有依据）：
  avg 平均分（历史单信号平均收益%，越高越可靠）· acc 方向正确率% · n 信号数
  bull/bear 多空偏好 · horizon 主攻周期 · style 风格一句话（DeepSeek 生成，失败留空）

用法：python -m briefing.scripts.profiles
"""
import importlib.util
import json
import logging
import os
import re
from collections import Counter

from . import config, paths
from .summarize import _extract

log = logging.getLogger("briefing")

STYLE_SYSTEM_PROMPT = """你是财经博主风格分析师。给定博主的历史观点摘要（每条为对大盘的方向预测），用一句 ≤40 字的话概括其**风格特点**（话术类型/主攻周期/风格倾向）。

示例："盯盘话术型，短周期情绪派" / "中长线趋势派，常给点位目标" / "谨慎保守，观点常留余地" / "高密度发帖，多空都要说"。
输出严格 JSON：{"styles": [{"blogger": "博主名", "style": "风格一句话"}]}
- 条目数与输入博主数一致、顺序对应；只输出 JSON。"""


def _horizon_of(spec):
    if not spec:
        return "未提"
    if spec == "today":
        return "今天"
    if spec == "t1":
        return "明天"
    if re.match(r"^t[2-5]$", spec):
        return "近日"
    if spec == "week":
        return "本周"
    if spec == "nweek" or spec == "nweek_first":
        return "下周"
    if spec == "nd":
        return "无周期"
    return "更长"  # t6+ / month / nmonth / long / d:YYYY-MM-DD


def _signal_stats(name):
    """从信号文件算多空偏好与主攻周期；从打分报告解析平均分/正确率。"""
    stats = {"n": 0, "bull": 0, "bear": 0, "horizon": "未提", "avg": None, "acc": None}
    fp = os.path.join(paths.SIGNALS_DIR, f"{name}.json")
    if os.path.exists(fp):
        try:
            signals = json.load(open(fp, encoding="utf-8")).get("signals") or []
            bull = sum(1 for s in signals if s.get("d") == 1)
            bear = sum(1 for s in signals if s.get("d") == -1)
            stats["n"] = len(signals)
            stats["bull"], stats["bear"] = bull, bear
            horizons = Counter(_horizon_of(s.get("spec")) for s in signals)
            if horizons:
                stats["horizon"] = horizons.most_common(1)[0][0]
        except Exception:
            pass

    rp = os.path.join(paths.REPORTS_DIR, f"{name}_direction.md")
    if os.path.exists(rp):
        try:
            txt = open(rp, encoding="utf-8").read()
            m = re.search(r"平均分[:：]\s*([+-]?\d+(?:\.\d+)?)", txt)
            if m:
                stats["avg"] = float(m.group(1))
            m = re.search(r"正确率\s*(\d+(?:\.\d+)?)%", txt)
            if m:
                stats["acc"] = float(m.group(1))
        except Exception:
            pass
    return stats


def _style_chunk(bloggers):
    """一批博主 → DeepSeek 生成风格一句话。"""
    ext = _extract()
    user_msg = "\n\n".join(
        f"▍{b}：\n" + "\n".join(
            f"  - {s}" for s in _recent_summaries(b, 12))
        for b in bloggers)
    result, raw = ext.call_json(None, STYLE_SYSTEM_PROMPT, user_msg, "profiles:style")
    if result is None:
        return {}
    out = {}
    for x in (result.get("styles") or []):
        if isinstance(x, dict) and x.get("blogger"):
            out[x["blogger"]] = (x.get("style") or "").strip()[:40]
    return out


def _recent_summaries(name, limit):
    fp = os.path.join(paths.SIGNALS_DIR, f"{name}.json")
    try:
        signals = json.load(open(fp, encoding="utf-8")).get("signals") or []
    except Exception:
        return []
    return [s.get("summary") for s in signals[:limit] if s.get("summary")]


def _all_bloggers():
    """data/posts 下所有博主 + config.TRACKED 并集（随时新增博主也能自动纳入画像）。

    排除：_backup 目录、_bodies_s*.json（正文分片）、*_feed_check.json（抓取校验文件）。
    """
    names = set(config.TRACKED)
    try:
        for fn in os.listdir(paths.POSTS_DIR):
            if (fn.endswith(".json") and "_feed_check" not in fn
                    and "_bodies_s" not in fn):
                names.add(fn[:-5])
    except OSError:
        pass
    return sorted(names)


def build():
    paths.load_env()
    # 增量：已画像的博主直接沿用，只处理没跑过的（随时新增博主自动纳入）
    profiles = {}
    if os.path.exists(paths.PROFILES_FILE):
        try:
            profiles = json.load(open(paths.PROFILES_FILE, encoding="utf-8")).get("bloggers") or {}
        except Exception:
            profiles = {}
    all_names = _all_bloggers()
    batch = [b for b in all_names if b not in profiles]
    if not batch:
        print(f"无新增博主（已有 {len(profiles)} 位画像），跳过")
        return profiles
    log.info("新增画像 %d 位：%s", len(batch), "、".join(batch))
    for i in range(0, len(batch), 10):
        chunk = batch[i:i + 10]
        styles = _style_chunk(chunk)
        for b in chunk:
            st = _signal_stats(b)
            st["style"] = styles.get(b, "")
            profiles[b] = st
            log.info("  画像 %s: %s", b, {k: v for k, v in st.items() if k != "style"})
    paths.ensure_dirs()
    meta = {"updated": __import__("time").strftime("%Y-%m-%d %H:%M:%S"), "bloggers": profiles}
    with open(paths.PROFILES_FILE, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"画像已写入 {paths.PROFILES_FILE}（共 {len(profiles)} 位）")
    return profiles


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    build()
