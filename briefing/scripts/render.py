# -*- coding: utf-8 -*-
"""飞书推送：简报卡片渲染 + 心跳消息 + webhook 发送。

自定义机器人 webhook：POST https://open.feishu.cn/open-apis/bot/v2/hook/<token>
卡片为 msg_type=interactive（富文本分节），心跳为 msg_type=text。
"""
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

import requests

from . import config

log = logging.getLogger("briefing")

BEIJING_TZ = timezone(timedelta(hours=8))
STANCE_BADGE = {"多": "🔴看多", "空": "🟢看空", "中性": "⚪中性"}
STANCE_EMOJI = {"多": "🔴", "空": "🟢", "中性": "⚪"}
STANCE_TEXT = {"多": "看多", "空": "看空", "中性": "中性"}
HORIZON_BADGE = {"今天": "今日", "明天": "明日", "近日": "近几日", "本周": "本周",
                 "下周": "下周", "更长": "长周期", "无周期": "无周期", "未提": "周期未提"}
HEADER_TEMPLATE = "blue"
KEY_BLOGGERS_TOP = 5   # 重点博主只挑排名最高的（用户要求"挑重点"，不展示全部有观点者）


def _rank_of(name):
    """总榜排名（1-based）。TRACKED 即综合口径 t 值 top-30 快照（顺序即排名，见 config.py 注释）；
    不在追踪名单返回 None（理论不出现：简报只收集 TRACKED 博主观点）。
    """
    try:
        return config.TRACKED.index(name) + 1
    except ValueError:
        return None


def select_key_bloggers(card, top=KEY_BLOGGERS_TOP):
    """按总榜排名选取重点博主（挑重点）。返回 (selected, 原始条数)。"""
    kbs = [x for x in (card.get("key_bloggers") or []) if isinstance(x, dict)]
    kbs.sort(key=lambda x: (_rank_of(x.get("name") or "") or 10 ** 9, str(x.get("name") or "")))
    return kbs[:top], len(kbs)


def fmt_post_time(ts):
    """帖子的相对时间标注：今日HH:MM / 昨日HH:MM / MM-DD HH:MM（北京时）。"""
    if not ts:
        return ""
    try:
        dt = datetime.fromtimestamp(int(ts), tz=BEIJING_TZ)
    except (TypeError, ValueError, OSError):
        return ""
    now = datetime.now(BEIJING_TZ)
    today, yest = now.date(), now.date() - timedelta(days=1)
    if dt.date() == today:
        return f"今日{dt.strftime('%H:%M')}"
    if dt.date() == yest:
        return f"昨日{dt.strftime('%H:%M')}"
    return dt.strftime("%m-%d %H:%M")


def _fmt_key_blogger(x):
    """两行式（博主观点展示 v4）：#rank 🟢 **名字** · 画像风格\n看空·强·近几日｜“quote”（时间）。

    用户确认：第一行=博主名+风格（画像 style），换行第二行=博主观点；多个博主用空行隔开（调用处 join "\n\n"）。
    风格取画像档案 style；立场 emoji 放在名字前便于扫读，观点行带立场文字+周期+引文+帖子时间。
    """
    name = x.get("name") or "?"
    rank = _rank_of(name)
    emoji = STANCE_EMOJI.get(x.get("stance"), "")
    stext = STANCE_TEXT.get(x.get("stance"), x.get("stance") or "")
    strength = "·强" if x.get("strength") == "强" else ""
    horizon = HORIZON_BADGE.get(x.get("horizon"), x.get("horizon") or "")
    quote = x.get("quote") or ""
    t = fmt_post_time(x.get("pub_ts"))
    style = (x.get("style") or "").strip()

    line1 = f"#{rank} " if rank else ""
    line1 += f"{emoji} **{name}**"
    if style:
        line1 += f" · {style}"

    line2 = f"{stext}{strength}"
    if horizon:
        line2 += f"·{horizon}"
    if quote:
        line2 += f"｜“{quote}”"
    if t:
        line2 += f"（{t}）"
    return f"{line1}\n{line2}"


def _fmt_takeaway(t):
    """本期要点一条：一句完整总结句（如"全板偏空，空头占优，短期以防守为主"）。

    兼容旧 {theme, detail, action} dict（历史卡重渲染）→ 压成一句。
    """
    if isinstance(t, dict):
        bits = [str(t.get("theme") or "").strip()]
        d = str(t.get("detail") or "").strip()
        a = str(t.get("action") or "").strip()
        if d:
            bits.append(d)
        line = "，".join(b for b in bits if b)
        if a and a not in line:
            line += f"；{a}"
        return line
    return str(t or "").strip()


def _date_header(date_str, slot_label):
    return f"📊 {slot_label}简报 · {date_str}"


# ── LEGACY（v8 全板共识卡；2026-09 redesign 后 run_briefing 不再调用，仅保留供历史卡重渲染）──
def build_card_payload(card, market_text, slot_label, date_str, window_txt=""):
    """card JSON → 飞书 interactive card payload。window_txt 如"自 14:00 以来"。

    大结构（v2，用户确认保留）：窗口 → 行情 → 共识 → 重点博主 → 分歧 → 风险 → 活动角标 → 关注点。
    关注点（该关注的若干点）放末尾总结位；重点博主=博主名+风格行 / 观点行，博主间空行隔开。
    """
    c = card["consensus"]
    elements = []

    # 增量窗口（时间锚点）
    if window_txt:
        elements.append({"tag": "note", "elements": [
            {"tag": "plain_text", "content": f"🕐 本期覆盖：{window_txt}"}]})

    # 行情
    elements.append({"tag": "markdown", "content": f"📈 {market_text}"})
    elements.append({"tag": "hr"})

    # 共识（立场行 + 丰富分析段落，恢复 v2 长段落式全板共识）
    bull, bear, neutral = c["bull"], c["bear"], c["neutral"]
    cons_line = f"🧭 **共识：{c['stance']}**（{bull}多 / {bear}空 / {neutral}中性）"
    if c.get("summary"):
        cons_line += f"\n{c['summary']}"
    if c.get("evolution"):
        cons_line += f"\n（演变）{c['evolution']}"
    elements.append({"tag": "markdown", "content": cons_line})

    # 重点博主（总榜 Top N；博主名+风格行 / 观点行，博主间空行隔开）
    if card.get("key_bloggers"):
        sel, total = select_key_bloggers(card)
        header = "⭐ **重点博主**"
        if total > len(sel):
            header += f"（总榜 Top {len(sel)}）"
        lines = [header] + [_fmt_key_blogger(x) for x in sel]
        elements.append({"tag": "markdown", "content": "\n\n".join(lines)})

    # 分歧
    div = card.get("divergences") or []
    if div:
        lines = ["⚔️ **关键分歧**"]
        lines += [f"· {d}" if isinstance(d, str) else f"· {d.get('desc', d)}" for d in div]
        elements.append({"tag": "markdown", "content": "\n".join(lines)})

    # 风险
    risks = card.get("risks") or []
    if risks:
        lines = ["⚠️ **风险 / 极端信号**"]
        for r in risks:
            if isinstance(r, str):
                lines.append(f"· {r}")
            else:
                lines.append(f"· {r.get('desc', '')}（{r.get('blogger', '')}）"
                             + (f" {r.get('note', '')}" if r.get('note') else ""))
        elements.append({"tag": "markdown", "content": "\n".join(lines)})

    # 活动角标（全板口径：每位博主一近期观点，随时更新）
    act = card.get("activity") or {}
    if act.get("posting") is not None:
        foot = f"📋 全板 {act.get('posting')} 位博主有近期观点"
        if act.get("no_view"):
            foot += f"（其中 {act['no_view']} 中性）"
        bloggers = act.get("bloggers") or []
        if bloggers:
            foot += "：· " + " · ".join(str(b) for b in bloggers[:8])
        elements.append({"tag": "note", "elements": [{"tag": "plain_text", "content": foot}]})

    # 本期要点（收尾总结：整板凝结成若干点，每点一句总结句，不拆行）
    takeaways = card.get("takeaways") or card.get("focus") or []
    if takeaways:
        lines = ["🎯 **本期要点**"]
        lines += [f"· {_fmt_takeaway(t)}" for t in takeaways[:5] if _fmt_takeaway(t)]
        elements.append({"tag": "markdown", "content": "\n".join(lines)})

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {"template": HEADER_TEMPLATE,
                       "title": {"tag": "plain_text", "content": _date_header(date_str, slot_label)}},
            "elements": elements,
        },
    }


def build_heartbeat_payload(market_text, slot_label, date_str, window_txt=""):
    """LEGACY（v8）：心跳消息——无新增观点时推一条极简文本。v9 不再发心跳。"""
    win = f"\n覆盖时段：{window_txt}" if window_txt else ""
    return {
        "msg_type": "text",
        "content": {"text": f"✅ {_date_header(date_str, slot_label)} 本时段无新增观点{win}\n"
                            f"行情：{market_text}\n系统正常"},
    }


def build_error_payload(err, date_str, slot_label):
    return {
        "msg_type": "text",
        "content": {"text": f"⚠️ {_date_header(date_str, slot_label)} 简报生成失败：{err}\n"
                            f"详情见服务器日志 briefing.log"},
    }


def post_webhook(payload, webhook_url=None, retries=3):
    """发送到飞书 webhook；返回 (ok, resp_text)。"""
    url = webhook_url or os.environ.get("FEISHU_WEBHOOK_URL")
    if not url:
        return False, "未配置 FEISHU_WEBHOOK_URL"
    last = ""
    for i in range(retries):
        try:
            r = requests.post(url, json=payload, timeout=20)
            body = r.text[:200]
            try:
                code = r.json().get("code")
                if code == 0:
                    return True, body
                last = f"飞书返回 code={code}: {body}"
            except Exception:
                last = f"非JSON响应 {r.status_code}: {body}"
        except Exception as e:
            last = f"请求异常: {e}"
        time.sleep(3 * (i + 1))
    return False, last


# =====================================================================
# v9：18 人窗口速览卡（固定名单行式表格 + 卡底收敛总结）
# =====================================================================

_CN_NUMS = ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩",
            "⑪", "⑫", "⑬", "⑭", "⑮", "⑯", "⑰", "⑱"]
_MAX_MD_BYTES = 30000  # 单 markdown 元素上限粗估；超长则把 18 行拆成两段


def _fmt_roster_row(row, inline_bucket=True):
    """单博主行：观点行（1 行头 + 重点原话 + 摘要）或占位行（近3日无观点更新）。

    row 字段见 summarize._row_from_llm：blogger/has_view/stance/bucket/horizon/
    summary/quote/quote_ts/n_posts。占位行区分"无帖"与"有帖未明确表态"。
    inline_bucket=False：行不打印周期档标签（v10 按档分段后段标题已表意，行首只留
    周期原话词 今日/明日/本周… 避免重复）。
    """
    name = row.get("blogger") or "?"
    if not row.get("has_view"):
        base = f"**{name}**：{config.ROWS_NO_VIEW_TEXT}"
        if row.get("n_posts"):
            base += "（有发帖未明确表态）"
        return base
    emoji = STANCE_EMOJI.get(row.get("stance"), "")
    stext = STANCE_TEXT.get(row.get("stance"), "")
    line1 = f"{emoji} **{name}** {stext}"
    if inline_bucket and row.get("bucket"):
        line1 += f" · {row['bucket']}"
    if row.get("horizon") and row["horizon"] != "未提":
        line1 += f" · {row['horizon']}"
    lines = [line1]
    if row.get("quote"):
        t = fmt_post_time(row.get("quote_ts"))
        lines.append(f"　重点原话：“{row['quote']}”" + (f"（{t}）" if t else ""))
    if row.get("summary"):
        lines.append(f"　摘要：{row['summary']}")
    return "\n".join(lines)


_BUCKET_HEAD = {
    config.BUCKET_SHORT: "⏱️ **超短(0-1日)**",
    config.BUCKET_SWING: "🌊 **波段(2日+)**",
}
_NO_DIRECTION_HEAD = "📭 **无明确方向**"


def _roster_section_lines(rows):
    """18 行按周期分段的渲染块序列（v10）。

    段 = 超短(0-1日) / 波段(2日+) / 无明确方向（观望 + 无更新 博主尾段）。
    段内按 config.ROSTER 原序；编号 ①–⑱ = 原名单位次不变（博主挪段编号不变，便于对位）。
    空段不渲染标题；无方向博主必有尾段。每块是段标题或一个博主行（含其内部换行）。
    同一逻辑块同时供卡 payload 与 dry-run 预览使用（run_briefing._preview_text 调用），防止两处漂移。
    """
    sections = {config.BUCKET_SHORT: [], config.BUCKET_SWING: []}
    tail = []
    for i, name in enumerate(config.ROSTER):
        row = dict(rows.get(name) or {})
        row.setdefault("blogger", name)
        line = f"{_CN_NUMS[i]} {_fmt_roster_row(row, inline_bucket=False)}"
        if row.get("has_view") and row.get("bucket") in sections:
            sections[row["bucket"]].append(line)
        else:
            tail.append(line)
    lines = []
    for bucket in (config.BUCKET_SHORT, config.BUCKET_SWING):
        if sections[bucket]:
            lines.append(_BUCKET_HEAD[bucket])
            lines.extend(sections[bucket])
    if tail:
        lines.append(_NO_DIRECTION_HEAD)
        lines.extend(tail)
    return lines


def build_roster_card_payload(rows, market_text, slot_label, date_str, window_txt="",
                              summary_text="", counts=None):
    """v10 主卡：header + 覆盖窗口 + 行情 + 按周期分段的 18 行 + 卡底收敛总结 + 分档计数角标。

    rows: {博主: row}（缺的博主按占位行兜底，18 行必齐）。
    counts: 分档 shape（见 summarize.count_rows），作角标与总结上下文。
    """
    counts = counts or {}
    elements = []
    if window_txt:
        elements.append({"tag": "note", "elements": [
            {"tag": "plain_text", "content": f"🕐 覆盖：{window_txt}"}]})
    elements.append({"tag": "markdown", "content": f"📈 {market_text}"})
    elements.append({"tag": "hr"})

    blocks = _roster_section_lines(rows)

    roster_md = "\n\n".join(blocks)
    if len(roster_md.encode("utf-8", "replace")) > _MAX_MD_BYTES:
        half = len(blocks) // 2
        elements.append({"tag": "markdown", "content": "\n\n".join(blocks[:half])})
        elements.append({"tag": "markdown", "content": "\n\n".join(blocks[half:])})
    else:
        elements.append({"tag": "markdown", "content": roster_md})
    elements.append({"tag": "hr"})

    # 卡底收敛总结（无总结文案时降级为分档计数一行，保证卡片信息完整）
    counts_line = config.format_counts(counts)
    foot = f"🧭 {summary_text}" if summary_text else f"🧭 多空版图：{counts_line}"
    elements.append({"tag": "markdown", "content": foot})
    elements.append({"tag": "note", "elements": [
        {"tag": "plain_text", "content": counts_line}]})

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {"template": HEADER_TEMPLATE,
                       "title": {"tag": "plain_text", "content": _date_header(date_str, slot_label)}},
            "elements": elements,
        },
    }


def build_minimal_card_payload(market_text, slot_label, date_str, window_txt="", note_text="", counts=None):
    """零方向日的最小交互卡：确认系统存活 + 分档计数角标，不渲染 18 行空表。"""
    counts_txt = config.format_counts(counts or {})
    elements = []
    if window_txt:
        elements.append({"tag": "note", "elements": [
            {"tag": "plain_text", "content": f"🕐 覆盖：{window_txt}"}]})
    elements.append({"tag": "markdown", "content": f"📈 {market_text}"})
    if note_text:
        elements.append({"tag": "markdown", "content": f"💤 {note_text}"})
    elements.append({"tag": "note", "elements": [{"tag": "plain_text", "content": counts_txt}]})
    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {"template": HEADER_TEMPLATE,
                       "title": {"tag": "plain_text", "content": _date_header(date_str, slot_label)}},
            "elements": elements,
        },
    }
