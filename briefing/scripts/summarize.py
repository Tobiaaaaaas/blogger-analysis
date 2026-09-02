# -*- coding: utf-8 -*-
"""简报生成：DeepSeek 分批抽点 → 全局综合。

复用父仓库 scripts/pipeline/extract_signals_direction.py 的 DeepSeek 调用底座
（call_json / parse_response / watchdog 硬超时），保证与信号提取同一套稳定链路。

两步：
  1. 抽点：新帖按批（~8 帖/批）逐条提炼"观点要点"（方向/强度/周期/关键句/极端标记）。
  2. 综合：所有要点 + 行情 + 上期共识 + 博主画像 → 一次性推理出卡片 JSON。
"""
import importlib.util
import json
import logging

from . import paths

log = logging.getLogger("briefing")

MAX_POST_CHARS = 1200   # 单帖正文截断长度（足够承载一篇观点帖）
POINTS_BATCH = 8        # 抽点每批帖子数
SYNTH_MAX_ATTEMPTS = 3  # 综合最大尝试次数（模型偶发把 consensus 拍平到顶层/漏段，不完整则重试）

_extract_mod = None


def _extract():
    global _extract_mod
    if _extract_mod is None:
        spec = importlib.util.spec_from_file_location("extract_signals", paths.EXTRACT_MOD)
        _extract_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_extract_mod)
    return _extract_mod


POINTS_SYSTEM_PROMPT = """你是财经自媒体内容分析助手。你会收到一批今日头条财经博主的帖子（每条含博主名、标题、正文），请逐条判断博主是否对大A大盘给出了**明确的方向观点**。

明确的观点指对未来的方向判断：看涨/看跌、收阳/收阴、点位目标、支撑/压力突破判断、明确条件后的方向倾向，且对象是上证指数/大盘/主要指数（不含具体个股、与大盘无关的板块行情）。
判断要点：
- 明确未来方向才算观点；只描述现状（"今天缩量震荡""进入调整期"）、复盘已发生行情、仓位自述、理念分享、新闻评论 → stance=中性。
- 带条件的方向判断（"站稳X才能看多""回踩X是机会"）按条件倾向判多/空，quote 引原句。
- 情绪极端词（"史诗级""崩盘""必涨""满仓""清仓"）→ extreme=true。
- 一条帖多个观点时，只记最明确的那个方向态度。
- 没有观点 → stance=中性，summary 留空字符串。

输出严格 JSON（points 数组长度必须等于输入帖子数，顺序一一对应）：
{"points": [{"post_n": 0, "blogger": "博主名", "stance": "多|空|中性", "strength": "强|中|弱", "horizon": "今天|明天|近日|本周|下周|更长|无周期|未提", "quote": "关键原文一句(≤40字，无观点留空)", "extreme": false, "summary": "一句话概括(≤30字，中性则留空)"}, ...]}
只输出 JSON，无其他文字。"""


SYNTH_SYSTEM_PROMPT = """你是股票市场观点综合分析助手。输入包含：
1) 当前时段与日期
2) 大盘行情（实时/最近收盘）
3) **上期简报**的共识与重点博主（基准，用于对比全板变化）
4) **博主画像**：部分博主的画像档案（**风格特征**与主攻周期；不含准确率/得分等量化指标）
5) **本期更新观点博主**：本次推送窗口内发过新帖、观点发生更新的博主名单
6) **全板近期观点**：所有追踪博主的当前近期观点（每人一条：立场/强度/周期/引文/发帖时间）

核心概念——全板观点模型：每个博主维护一个"近期观点"（其最新一篇有观点帖的立场），博主发新帖则其近期观点更新；没发新帖的博主，其近期观点保持不变。**全板** = 所有追踪博主近期观点的集合。统计与综合必须基于**全板**，而不是只看本期新帖。发帖超过 7 天的观点视为过时（该博主退出统计，只看时效内观点）。

任务：把**全板近期观点**综合成一份**全板简报**。读者要能一眼看出"现在全板观点版图如何、相对上期哪些博主变了"。硬性要求：

【时间】
- 每条观点带发帖时间（输入已给），共识里点明本期时间背景（时段/窗口），不要脱离时间泛泛而谈。
- 只在时效内（发帖≤7 天）的观点上做统计与判断；过时博主的观点不出现、不参与。

【全板态势】（重中之重）
- 共识 stance 反映全板多空力量对比；summary **必须**以"较上期……"开头：先点出上期全板结论，再给本期全板结论，明确演变（维持/转强/转弱/转向/出现新对立）。重点说明**本期更新了观点的博主**带来的变化（哪些博主新发观点、方向如何）。首期则明确说"首期无基准"。
- 多空统计由系统按全板计算，**你不需要也不能输出数字**——只描述态势与演变。

【其他】
- 分歧：全板观点冲突或逻辑矛盾，写成简短条目（可含本期更新观点的博主）。
- 风险：全板极端预测、情绪化表态、与共识背离的强观点，标注博主与其风格画像备注。
- 中性观点（当前无明确方向）计入全板活动但不算入多空力量。
- 不输出重点博主（重点博主由系统按总榜排名自动选取 Top 5），也不输出多空数字。

输出严格 JSON（divergences/risks 可为空数组）：
{
  "consensus": {"stance": "偏多|偏空|均衡|未明", "summary": "以'较上期'开头的全板共识概括，含时间背景与整体演变"},
  "divergences": ["分歧1", "分歧2"],
  "risks": [{"blogger": "...", "desc": "风险观点", "note": "画像备注(风格)"}],
  "takeaways": ["本期要点1", "本期要点2", "本期要点3"]
}
- takeaways：**报告末尾的总览总结**，2~4 条、每条 ≤30 字，浓缩成行动级要点（整体结论 / 最重要的变化 / 最需警惕的风险），不重复前面小节原文。
- 只输出 JSON，无其他文字"""


def _fmt_post(blogger, p):
    content = (p.get("content") or "").strip()
    if len(content) > MAX_POST_CHARS:
        content = content[:MAX_POST_CHARS] + "…（已截断）"
    title = p.get("title") or ""
    header = f"【博主】{blogger}\n"
    if title:
        header += f"【标题】{title}\n"
    header += f"【正文】{content}"
    return header


def extract_points(blogger_posts, batch_size=POINTS_BATCH):
    """blogger_posts: [(blogger, post), ...] → 返回 ({post_n: point}, activity_counts)。

    跳过视频帖（无文字正文）。抽点调用 DeepSeek，失败批次重试后仍失败的帖子记中性。
    """
    ext = _extract()
    clean = []
    for blogger, p in blogger_posts:
        content = (p.get("content") or "").strip()
        if content == "[视频帖]" or len(content) < 5:
            continue
        clean.append((blogger, p))

    points = {}
    no_view = 0
    for i in range(0, len(clean), batch_size):
        chunk = clean[i:i + batch_size]
        user_msg = "\n\n----\n\n".join(
            f"[{j}] " + _fmt_post(b, p) for j, (b, p) in enumerate(chunk))
        result, raw = ext.call_json(None, POINTS_SYSTEM_PROMPT, user_msg, "briefing:points")
        if result is None:
            log.warning("  抽点批次失败（%d 帖），按中性处理", len(chunk))
            for j, (b, p) in enumerate(chunk):
                # 全局索引 i+j，避免后一批覆盖前一批（2026-09-02 修复批覆盖 bug）
                points[i + j] = {"post_n": i + j, "blogger": b, "stance": "中性",
                                 "strength": "弱", "horizon": "未提", "quote": "",
                                 "extreme": False, "summary": "",
                                 "pub_ts": p.get("publish_time")}
                no_view += 1
            continue
        pts = result.get("points") or []
        for j, (b, p) in enumerate(chunk):
            d = pts[j] if j < len(pts) and isinstance(pts[j], dict) else {}
            stance = d.get("stance")
            if stance not in ("多", "空", "中性"):
                stance = "中性"
            if stance == "中性":
                no_view += 1
            # 全局索引 i+j，避免后一批覆盖前一批（2026-09-02 修复批覆盖 bug）
            # pub_ts：来源帖发布时间，全板模型用它取"每博主最新帖立场"并做 7 天时效过滤
            points[i + j] = {
                "post_n": i + j, "blogger": b, "stance": stance,
                "strength": d.get("strength") or "中",
                "horizon": d.get("horizon") or "未提",
                "quote": (d.get("quote") or "").strip()[:40],
                "extreme": bool(d.get("extreme")),
                "summary": (d.get("summary") or "").strip()[:30],
                "pub_ts": p.get("publish_time"),
            }
    return points, no_view


def _board_txt(board):
    """全板近期观点 → prompt 文本（按博主名排序；每条带发帖时间，供综合的时间锚点）。"""
    if not board:
        return "（暂无时效内观点）"
    lines = []
    for name in sorted(board):
        e = board[name]
        ts = e.get("pub_ts")
        t = ""
        if ts:
            from datetime import datetime, timezone, timedelta
            dt = datetime.fromtimestamp(int(ts), tz=timezone(timedelta(hours=8)))
            t = dt.strftime("%m-%d %H:%M")
        strength = "·强" if e.get("strength") == "强" else ""
        horizon = e.get("horizon") or "未提"
        desc = e.get("quote") or e.get("summary") or ""
        lines.append(f"▍{name}（发帖 {t}）")
        lines.append(f"  - {e['stance']}{strength}·{horizon}｜“{desc}”")
    return "\n".join(lines)


def _build_profile_subset(profiles, bloggers):
    """只取出本期发帖博主的画像片段，供综合 prompt 注入。

    只给风格特征与主攻周期（报告不展示准确率/得分等量化指标）。
    """
    out = []
    for b in bloggers:
        pf = (profiles or {}).get(b)
        if not pf:
            continue
        bits = [f"{b}: 主攻{pf.get('horizon')}"]
        if pf.get("style"):
            bits.append(f"风格：{pf['style']}")
        out.append("；".join(bits))
    return "\n".join(out) if out else "（本期博主无画像档案）"


def _normalize_synth_result(result):
    """模型偶发把 consensus 拍平到顶层（顶层就是 {stance, summary}，无 consensus 键）。

    包回标准结构；其余段若也在顶层则原样保留。
    """
    if isinstance(result, dict) and not isinstance(result.get("consensus"), dict) and "stance" in result:
        flat = result
        result = {
            "consensus": {"stance": flat.get("stance"), "summary": flat.get("summary")},
            "divergences": flat.get("divergences") or [],
            "risks": flat.get("risks") or [],
            "takeaways": flat.get("takeaways") or [],
        }
    return result


def _synth_result_ok(card):
    """完整性检查：consensus 合法（dict + 合法 stance + summary），且除 consensus 外至少有一段实质内容。

    拍平到顶层 / 只有半截 consensus / 空 JSON 都会在此被拒，触发综合重试。
    """
    c = card.get("consensus")
    if not isinstance(c, dict):
        return False
    if c.get("stance") not in ("偏多", "偏空", "均衡", "未明") or not c.get("summary"):
        return False
    return bool(card.get("takeaways") or card.get("divergences") or card.get("risks"))


def synthesize(board, updated, market_text, prev_state, profiles, slot_label, date_str, window_txt=""):
    """全板综合 → 卡片 JSON。window_txt 描述本期窗口（如"自 14:00 以来 · 全板滚动更新"）。

    board:   全板近期观点 {博主: {stance, strength, horizon, quote, extreme, summary, pub_ts}}（已时效过滤）
    updated: 本期发新帖、观点更新的博主集合（首期 = 全板博主）
    多空数字不在此定——由 run_briefing 按全板计数覆盖（全板口径，非本期增量）。
    """
    ext = _extract()

    board_txt = _board_txt(board)
    updated_txt = "、".join(sorted(updated)) if updated else "（首期）"

    prev = prev_state or {}
    prev_txt = f"上期共识：{prev.get('consensus_text') or '（首期）'}"
    if prev.get("key_bloggers_text"):
        prev_txt += f"\n上期重点博主：{prev['key_bloggers_text']}"

    user_msg = f"""【时段】{date_str} {slot_label}（{window_txt or '本期'}）
【行情】{market_text}
【{prev_txt}】
【博主画像】
{_build_profile_subset(profiles, sorted(board.keys()))}

【本期更新观点博主】{updated_txt}
【全板近期观点】（时效内 {len(board)} 位博主）
{board_txt}"""

    card = None
    for attempt in range(SYNTH_MAX_ATTEMPTS):
        result, raw = ext.call_json(None, SYNTH_SYSTEM_PROMPT, user_msg, "briefing:synthesize", thinking=True)
        if result is None:
            if attempt < SYNTH_MAX_ATTEMPTS - 1:
                log.warning("综合调用无结果（attempt=%d），重试", attempt + 1)
                continue
            break
        norm = _normalize_synth_result(result)
        if _synth_result_ok(norm):
            card = norm
            break
        log.warning("综合输出不完整/格式异常（attempt=%d）：顶层 keys=%s，重试",
                    attempt + 1, list(result.keys()))
    if card is None:
        raise RuntimeError("综合调用多次失败或输出不完整，未能生成简报")
    card = _sanitize_card(card)
    return card, board_txt


def _sanitize_card(card):
    consensus = card.get("consensus") or {}
    stance = consensus.get("stance")
    if stance not in ("偏多", "偏空", "均衡", "未明"):
        stance = "未明"
    card["consensus"] = {
        "stance": stance,
        "bull": int(consensus.get("bull") or 0),
        "bear": int(consensus.get("bear") or 0),
        "neutral": int(consensus.get("neutral") or 0),
        "summary": (consensus.get("summary") or "").strip(),
    }
    for key in ("divergences", "risks"):
        card[key] = [x for x in (card.get(key) or []) if isinstance(x, (str, dict))]
    card["takeaways"] = [str(x).strip()[:40] for x in (card.get("takeaways") or [])
                         if isinstance(x, (str, int, float)) and str(x).strip()][:4]
    return card
