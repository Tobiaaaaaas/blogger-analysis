# -*- coding: utf-8 -*-
"""飞书推送：简报卡片渲染 + 心跳消息 + webhook 发送。

自定义机器人 webhook：POST https://open.feishu.cn/open-apis/bot/v2/hook/<token>
卡片为 msg_type=interactive（富文本分节），心跳为 msg_type=text。

v13（2026-09-03）：超短/波段拆两群两卡。每板块一张独立卡（标题含板块+时刻，
见 config.board_title），各推各群 webhook（webhook_for 读 config.WEBHOOK_ENV）——
webhook 未配置该板块按失败处理，**绝不回落** FEISHU_WEBHOOK_URL（防波段卡误发旧群）。
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
    """帖子的绝对时间标注：MM-DD HH:MM（北京时）。

    v12 起不再用 今日/昨日 相对速览——引文是昨天/前天的帖子，其相对词要靠
    真实日期才对得上（"昨天说的明天"）。一律按发帖真实日期标注。
    """
    if not ts:
        return ""
    try:
        dt = datetime.fromtimestamp(int(ts), tz=BEIJING_TZ)
    except (TypeError, ValueError, OSError):
        return ""
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


# =====================================================================
# v13：webhook 路由 + 板块错误心跳（两群两卡）
# =====================================================================

def webhook_for(board_key):
    """本板块应推的飞书 webhook URL（config.WEBHOOK_ENV[key] 对应 env）。

    未配置 → 返回 None，调用方须按"该板块推送失败"处理；**绝不回落**
    FEISHU_WEBHOOK_URL——否则波段卡会误发到超短群。
    """
    env = config.WEBHOOK_ENV.get(board_key)
    url = os.environ.get(env) if env else None
    if not url:
        log.error("  webhook 未配置：env %s 缺失（板块 %s 不推送，也不回落旧群）", env, board_key)
    return url


def build_board_error_payload(err, board_key, date_str, hm):
    """某板块生成失败的文本心跳（走该板块自己的 webhook）。"""
    title = config.board_title(board_key, date_str, hm)
    return {
        "msg_type": "text",
        "content": {"text": f"⚠️ {title} 生成失败：{err}\n"
                            f"详情见服务器日志 briefing.log"},
    }


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
# 板块行渲染原语（_fmt_board_row/_board_section_lines/_chunk_lines）：供 v13 单板块卡
# （build_board_card_payload / build_minimal_card_payload）与 LEGACY 双板块版共用，
# 单一来源防漂移。
# =====================================================================

_MAX_MD_BYTES = 30000  # 单 markdown 元素上限粗估；超长按行拆成多段


def _fmt_board_row(name, row):
    """板块内单博主行（无编号）：标题行 + 重点原话(发帖时间) + 摘要。

    row 字段见 summarize：blogger/has_view/stance/horizon/anchor/summary/quote/
    quote_ts（anchor 由 resolve_anchors 日期锚定：超短=目标日 MM-DD；波段=周词+
    周日期段；未提=空 不打印）。旧行无 anchor 时回退 horizon 周期词。
    """
    emoji = STANCE_EMOJI.get(row.get("stance"), "")
    stext = STANCE_TEXT.get(row.get("stance"), row.get("stance") or "")
    line1 = f"{emoji} **{name}** {stext}"
    anchor = row.get("anchor")
    if anchor is None:  # 兜底：未锚定的历史行退回周期词
        horizon = row.get("horizon") or "未提"
        anchor = "" if horizon == "未提" else horizon
    if anchor:
        line1 += f" · {anchor}"
    lines = [line1]
    if row.get("quote"):
        t = fmt_post_time(row.get("quote_ts"))
        lines.append(f"　重点原话：“{row['quote']}”" + (f"（{t}）" if t else ""))
    if row.get("summary"):
        lines.append(f"　摘要：{row['summary']}")
    return "\n".join(lines)


def _board_section_lines(board_key, rows, counts):
    """单个板块渲染块序列（v11）：计数头行 + 按名单序的成员行 / 空板块提示。

    rows: 该板块 {博主: row}（仅 has_view）；counts: board_counts[board_key]。
    同一块序列同时供卡 payload 与 dry-run 预览（run_briefing._preview_text）调用，
    单一来源防漂移。未表态成员不占行。
    """
    meta = config.BOARD_META[board_key]
    c = counts or {}
    head = (f"{meta['emoji']} **{meta['label']}**"
            f" · {c.get('bull', 0)}多/{c.get('bear', 0)}空"
            f"（{c.get('shown', 0)}/{c.get('members', 0)} 表态）")
    lines = [head]
    shown = 0
    for name in config.PANELS[board_key]:
        row = rows.get(name)
        if not row:
            continue
        lines.append(_fmt_board_row(name, row))
        shown += 1
    if not shown:
        lines.append(meta["empty_note"])
    return lines


def _chunk_lines(lines, cap=_MAX_MD_BYTES):
    """行序列按 UTF-8 字节上限切成若干块（每块单独成 markdown 元素）。"""
    if not lines:
        return []
    joined = "\n\n".join(lines)
    if len(joined.encode("utf-8", "replace")) <= cap:
        return [joined]
    chunks, cur, cur_bytes = [], [], 0
    for ln in lines:
        nb = len(ln.encode("utf-8", "replace")) + 2
        if cur and cur_bytes + nb > cap:
            chunks.append("\n\n".join(cur))
            cur, cur_bytes = [], 0
        cur.append(ln)
        cur_bytes += nb
    if cur:
        chunks.append("\n\n".join(cur))
    return chunks


def build_boards_card_payload_LEGACY(rows_by_board, counts, market_text, slot_label, date_str,
                                     window_txt="", summary_text=""):
    """LEGACY（v12 双板块一卡）：v13 拆两群两卡后不再接线，保留供历史复刻/回退。

    rows_by_board: {key: {博主: row}}；counts: summarize.board_counts。
    summary_text 为空 → 降级为系统计数一行（双板块合并，多空版图）。
    """
    elements = []
    if window_txt:
        elements.append({"tag": "note", "elements": [
            {"tag": "plain_text", "content": f"🕐 覆盖：{window_txt}"}]})
    elements.append({"tag": "markdown", "content": f"📈 {market_text}"})
    elements.append({"tag": "hr"})

    for key in config.PANEL_KEYS:
        section = _board_section_lines(key, rows_by_board.get(key) or {},
                                       counts.get(key) or {})
        for chunk in _chunk_lines(section):
            elements.append({"tag": "markdown", "content": chunk})
        elements.append({"tag": "hr"})

    if not summary_text:
        summary_text = f"多空版图：{config.format_board_counts(counts)}"
    elements.append({"tag": "markdown", "content": f"🧭 {summary_text}"})

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {"template": HEADER_TEMPLATE,
                       "title": {"tag": "plain_text", "content": _date_header(date_str, slot_label)}},
            "elements": elements,
        },
    }


def _board_card_elements(board_key, rows, counts, market_text, window_txt, summary_text):
    """单板块卡主体元素（覆盖→行情→本板块名单→🧭 本板块总结）。

    与 LEGACY 双板块版不同：只渲染 board_key 一块名单（_board_section_lines 单块）、
    总结降级用单板块 format_board_count —— 杜绝双板块计数混入单板块卡。
    """
    elements = []
    if window_txt:
        elements.append({"tag": "note", "elements": [
            {"tag": "plain_text", "content": f"🕐 覆盖：{window_txt}"}]})
    elements.append({"tag": "markdown", "content": f"📈 {market_text}"})
    elements.append({"tag": "hr"})
    for chunk in _chunk_lines(_board_section_lines(board_key, rows, counts)):
        elements.append({"tag": "markdown", "content": chunk})
    elements.append({"tag": "hr"})
    if not summary_text:
        summary_text = f"多空版图：{config.format_board_count(board_key, counts or {})}"
    elements.append({"tag": "markdown", "content": f"🧭 {summary_text}"})
    return elements


def build_board_card_payload(board_key, rows, counts, market_text, date_str, hm,
                             window_txt="", summary_text=""):
    """v13 单板块主卡：header(板块+时刻) + 覆盖窗口 + 行情 + 本板块名单 + 🧭 本板块总结。

    rows = 该板块 {博主: row}（仅 has_view，已 resolve_anchors）；counts = 该板块单键
    计数 {bull,bear,shown,members}；hm='HH:MM'（与 date_str 一起进标题，config.board_title）。
    summary_text 空 → 降级为单板块计数行。
    """
    elements = _board_card_elements(board_key, rows, counts, market_text, window_txt, summary_text)
    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {"template": HEADER_TEMPLATE,
                       "title": {"tag": "plain_text",
                                  "content": config.board_title(board_key, date_str, hm)}},
            "elements": elements,
        },
    }


def build_minimal_card_payload_LEGACY(market_text, slot_label, date_str, window_txt="",
                                      note_text="", counts=None):
    """LEGACY（v12 双板块空板）：v13 空板也按单板块发最小卡，本函数不再接线。"""
    counts_txt = config.format_board_counts(counts or {})
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


def build_minimal_card_payload(board_key, counts, market_text, date_str, hm,
                               window_txt="", note_text=""):
    """v13 单板块零方向最小卡：确认存活 + 本板块计数一行，不渲染空名单。

    note_text 区分空档原因（近窗口无人发帖 / 有人发帖但无该板块方向观点）。
    """
    counts_txt = config.format_board_count(board_key, counts or {})
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
                       "title": {"tag": "plain_text",
                                  "content": config.board_title(board_key, date_str, hm)}},
            "elements": elements,
        },
    }
