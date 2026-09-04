# -*- coding: utf-8 -*-
"""简报生成：DeepSeek 抽取与收敛。

复用父仓库 scripts/pipeline/extract_signals_direction.py 的 DeepSeek 调用底座
（call_json / parse_response / watchdog 硬超时），保证与信号提取同一套稳定链路。

v13（2026-09-03 redesign：超短/波段拆两群两卡 + 盘中 30 分档）→ v14（2026-09-04 交易日窗口）：
  1. extract_board_rows(board_key, by_member, window_start_ts)：按板块抽取——超短只认
     今天/明天（窗口=前一交易日 00:00 至 now）、波段只认 近日/本周/下周/更长
     （窗口=前 3 个交易日 00:00 至 now，v14 交易日口径）。
     带 rows_cache 增量：窗口帖集合（含正文指纹）未变 → 跳过 DeepSeek 复用缓存行。
  2. resolve_anchors：按引文发帖日锚定绝对目标（超短剔已过/未指今明，波段剔目标周已过）。
  3. board_counts：单板块多空计数。
  4. summarize_board(board_key, …)：单板块快照 + 本板块计数 → 一段本板块收敛总结。
v12 跨板块 summarize_boards / SUMMARY_SYSTEM_PROMPT 保留作 LEGACY（不再接线，供历史复刻）。
旧 v8 全板共识路径（POINTS_SYSTEM_PROMPT / SYNTH_SYSTEM_PROMPT / extract_points / synthesize）
亦 LEGACY；v9/v10 的 18 行名单路径已整段替换。
"""
import hashlib
import importlib.util
import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from . import calendar, config, paths

log = logging.getLogger("briefing")

MAX_POST_CHARS = 1200   # 单帖正文截断长度（足够承载一篇观点帖）
POINTS_BATCH = 8        # 抽点每批帖子数（LEGACY）
SYNTH_MAX_ATTEMPTS = 3  # 综合最大尝试次数（LEGACY）

ROW_MAX_ATTEMPTS = 3     # 行抽取 / 收敛总结最大尝试次数
ROW_SUMMARY_MAX = 60     # summary ≤60 字
ROW_QUOTE_MAX = 60       # 逐字原话 ≤60 字
BEIJING = timezone(timedelta(hours=8))

# 各板块接受的 horizon 词（v11 落档由固定名单决定，horizon 只作行内装饰；
# 板块周期白名单外 → 判为该板块无表态，不显示不计数）
PANEL_HORIZONS = {
    "short": ("今天", "明天"),
    "swing": ("近日", "本周", "下周", "更长", "未提"),
}

_extract_mod = None


def _extract():
    global _extract_mod
    if _extract_mod is None:
        spec = importlib.util.spec_from_file_location("extract_signals", paths.EXTRACT_MOD)
        _extract_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_extract_mod)
    return _extract_mod


# ── LEGACY（v8 全板共识卡；2026-09 redesign 后不再被 run_briefing 调用，仅保留供历史复刻/回退）──
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
3) **上期板况**（【较上期】块：上期/本期多空计数与本期观点翻转名单——仅供共识开头一句"较上期"对比；**严禁照抄或复述**）
4) **博主画像**：部分博主的画像档案（**风格特征**与主攻周期；不含准确率/得分等量化指标）
5) **本期更新观点博主**：本次推送窗口内发过新帖、观点发生更新的博主名单
6) **全板近期观点**：所有追踪博主的当前近期观点（每人一条：立场/强度/周期/引文/发帖时间）

核心概念——全板观点模型：每个博主维护一个"近期观点"（其最新一篇有观点帖的立场），博主发新帖则其近期观点更新；没发新帖的博主，其近期观点保持不变。**全板** = 所有追踪博主近期观点的集合。统计与综合必须基于**全板**，而不是只看本期新帖。发帖超过 7 天的观点视为过时（该博主退出统计，只看时效内观点）。

任务：把**全板近期观点**综合成一份**全板简报**。读者要能一眼看出"现在全板观点版图如何、相对上期哪些博主变了、**我该关注什么**"。硬性要求：

【时间】
- 每条观点带发帖时间（输入已给），共识里点明本期时间背景（时段/窗口），不要脱离时间泛泛而谈。
- 只在时效内（发帖≤7 天）的观点上做统计与判断；过时博主的观点不出现、不参与。

【共识】（全板态势，重中之重）
- consensus.summary：**丰富的一段全板共识分析**（4~7 句，写成流畅段落，不要只给结论）。开头第一句按【本轮性质】二选一：
  · **首期** → 固定写"较上期：首期无基准"。
  · **增量** → 用【较上期】给的上期/本期多空与翻转名单写**一句真实对比**（如"较上期：全板偏空、空头占优；本期延续偏空，仅个别人转多"），一句话即可，不展开。
  之后正文只分析**本期**全板：① 本期时间背景（时段/窗口与行情状态）；② 多空力量对比与占优方；③ **本期更新观点的博主**带来的变化（点名谁新发观点、方向如何、代表观点——点名**只能出自【本期更新观点博主】名单**）；④ 中性观望者的态度；⑤ 整体风险偏好。
  **硬性要求：正文是本期全板的原创分析，必须基于【全板近期观点】与【本期更新观点博主】；严禁复述、转抄或沿用【较上期】/【上期板况】/上期卡片中的措辞、点名与引文。若本期结论与上期相近，用"较上期延续偏空"一笔带过，其余篇幅仍写本期。**
- 多空统计由系统按全板计算，**你不需要也不能输出数字**——只描述态势与演变。

【本期要点】（收尾总结，把整板凝结成读者该关注的若干点）
- takeaways 数组 **3~5 条**（通常 4 条），每条**一句总结句**（≤35字，完整成句、可独立读），像"全板偏空，空头占优，短期以防守为主"这样的收束句。**不拆分主题/说明/行动**，不堆博主名与风格后缀，可点名个别关键博主但以观点概括为主。
- 必须综合全板提炼，覆盖：整体共识结论（含短期操作取向）、最关键的多空对立、最重要的变化（谁转多转空）、明日/短期最关键的触发点或点位、最需警惕的风险。

【分歧】
- 分歧：全板观点冲突或逻辑矛盾，写成简短条目（可含本期更新观点的博主）。

【风险】
- 风险：全板极端预测、情绪化表态、与共识背离的强观点，标注博主与其风格画像备注。

【其他】
- 中性观点（当前无明确方向）计入全板活动但不算入多空力量。
- 不输出重点博主（重点博主由系统按总榜排名自动选取 Top 5），也不输出多空数字。

输出严格 JSON（divergences/risks 可为空数组，takeaways 至少 3 条）：
{
  "consensus": {"stance": "偏多|偏空|均衡|未明", "summary": "丰富全板共识段落(4~7句；首期开头写'较上期：首期无基准'，增量以'较上期'真实对比)"},
  "divergences": ["分歧1", "分歧2"],
  "risks": [{"blogger": "...", "desc": "风险观点", "note": "画像备注(风格)"}],
  "takeaways": ["总结句1(≤35字)", "总结句2", "总结句3", "总结句4"]
}
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
    """模型偶发把 consensus 拍平到顶层（顶层就是 {stance, summary, evolution}，无 consensus 键）。

    包回标准结构；其余段若也在顶层则原样保留。
    """
    if isinstance(result, dict) and not isinstance(result.get("consensus"), dict) and "stance" in result:
        flat = result
        result = {
            "consensus": {"stance": flat.get("stance"), "summary": flat.get("summary"),
                          "evolution": flat.get("evolution")},
            "divergences": flat.get("divergences") or [],
            "risks": flat.get("risks") or [],
            "takeaways": flat.get("takeaways") or flat.get("focus") or [],
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
    return bool(card.get("takeaways") or card.get("focus") or card.get("divergences") or card.get("risks"))


def _count_board(board):
    """全板多空计数（口径与 run_briefing._board_counts 一致，供【较上期】块现算）。"""
    bull = sum(1 for e in board.values() if e.get("stance") == "多")
    bear = sum(1 for e in board.values() if e.get("stance") == "空")
    neutral = sum(1 for e in board.values() if e.get("stance") == "中性")
    return bull, bear, neutral


def _nature_block(first_board):
    """本轮性质行：显式引导共识开头写法（首期 vs 增量），杜绝"首期无基准"被误带到增量档。"""
    if first_board:
        return "【本轮性质】首期建板：无上期基准，共识段落开头固定写\"较上期：首期无基准\"。"
    return "【本轮性质】增量更新：共识段落开头须用【较上期】真实对比上期结论，禁止写\"首期无基准\"。"


def _prev_block(board, board_prev, first_board):
    """【较上期】结构化块：只喂上期多空计数 + 本期全板计数 + 本期观点翻转名单。

    全部由账本（当前 board / 上期 board_prev 快照）现算，**不含任何上期散文**——模型无从照抄。
    board_prev 可能只有 counts（旧版迁移，无逐博主立场）→ 只给计数、无翻转名单。
    """
    if first_board:
        return "【较上期】（首期：无上期基准）"
    if not board_prev:
        return "【较上期】（无上期快照：勿编造上期数据；结论相近可用'延续上期'一笔带过）"
    date = board_prev.get("date") or ""
    slot = board_prev.get("slot") or ""
    when = f"{date} {slot}".strip() or "上期"
    views = board_prev.get("views") or {}
    if views:
        pb, pa, pn = _count_board(views)
        p_n = len(views)
    else:
        c = board_prev.get("counts") or {}
        pb, pa = int(c.get("bull") or 0), int(c.get("bear") or 0)
        pn = c.get("neutral")
        p_n = 0
    cb, ca, cn = _count_board(board)
    head = f"{pb}多/{pa}空"
    if pn is not None:
        head += f"/{pn}中性"
    if p_n:
        head += f"（{p_n} 位）"
    lines = [f"【较上期】上期（{when}）：{head}；本期全板：{cb}多/{ca}空/{cn}中性"]
    if views:
        flips = []
        for n, pe in views.items():
            ps = pe.get("stance")
            ce = board.get(n)
            cs = ce.get("stance") if ce else None
            if ps and cs and cs != ps:
                flips.append(f"{n}（{ps}→{cs}）")
        if flips:
            lines.append("本期观点翻转：" + "、".join(sorted(flips)))
        else:
            lines.append("本期观点翻转：无（更新博主维持原方向）")
    return "\n".join(lines)


def synthesize(board, updated, market_text, board_prev, profiles, slot_label, date_str,
               window_txt="", first_board=False):
    """全板综合 → 卡片 JSON。window_txt 描述本期窗口（如"自 12:52 以来 · 全板滚动更新"）。

    board:       全板近期观点 {博主: {stance, strength, horizon, quote, extreme, summary, pub_ts}}（已时效过滤）
    updated:     本期发新帖、观点更新的博主集合（首期 = 全板博主）
    board_prev:  上期推送时落盘的板况快照 {date, slot, views|counts}——只用于算"较上期"计数/翻转名单，
                 绝**不回灌上期散文**（2026-09-02 修：模型曾整段照抄上期卡片）。
    first_board: 首期建板（无上期基准，共识开头写"较上期：首期无基准"）。
    多空数字不在此定——由 run_briefing 按全板计数覆盖（全板口径，非本期增量）。
    """
    ext = _extract()

    board_txt = _board_txt(board)
    updated_txt = "、".join(sorted(updated)) if updated else "（首期）"

    user_msg = f"""【时段】{date_str} {slot_label}（{window_txt or '本期'}）
【行情】{market_text}
{_nature_block(first_board)}
{_prev_block(board, board_prev, first_board)}
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
        "summary": (consensus.get("summary") or "").strip()[:400],
        "evolution": (consensus.get("evolution") or "").strip()[:120],
    }
    for key in ("divergences", "risks"):
        card[key] = [x for x in (card.get(key) or []) if isinstance(x, (str, dict))]
    card["takeaways"] = _norm_takeaways(card.get("takeaways") or card.get("focus") or [])
    card.pop("focus", None)  # 本期要点统一存 takeaways（旧 focus 结构在 _norm_takeaways 中压成一句）
    return card


def _norm_takeaways(items):
    """本期要点归一化为字符串数组（≤60字/条，最多 5 条）。兼容旧 {theme,detail,action} dict → 压成一句。"""
    out = []
    for x in items:
        if isinstance(x, dict):
            bits = [str(x.get("theme") or "").strip()]
            d = str(x.get("detail") or "").strip()
            a = str(x.get("action") or "").strip()
            if d:
                bits.append(d)
            text = "，".join(b for b in bits if b)
            if a and a not in text:
                text += f"；{a}"
        elif isinstance(x, str):
            text = x.strip()
        else:
            text = str(x).strip()
        if text:
            out.append(text[:60])
    return out[:5]


# =====================================================================
# v11：双板块行抽取（超短板块 / 波段板块 固定名单 × 各自窗口与周期口径）
# =====================================================================

# 每板块 system prompt：只认该板块周期——超短板块只认 今天/明天(0-1日)，
# 波段板块只认 近日/本周/下周/更长/结构性中期。一条帖同含两层时只引本板块那层。
# quote_post_n 只回帖子下标，发帖时间由系统按该帖真实 publish_time 回填（模型不誊写时间）。
SHORT_ROW_SYSTEM_PROMPT = """你是财经观点摘要助手。给你若干位「超短板块」博主在**前一交易日 00:00 至现在**（交易日窗口，含今日盘中已发的帖）内的帖子：每位博主名下若干条，按发帖时间从新到旧排列，每条前有编号 [0][1]… 并带发帖时间。任务：判定每人在窗口内是否对 上证指数/大盘/主要指数 给出过 **超短(0-1日，今天/明天)** 的明确方向观点，若有则产出该人最新一条超短方向表态。

时间口径（重要）：每条帖里的"今天/明天"都以**该帖自己的发帖日**为基准（帖子昨日发，则它说的"今天"=昨日、"明天"=今日）；目标日不在卡片当天/下一交易日的表态由系统自动剔除，你无需推算卡片是哪天，只按发帖日判断词义。

判定"超短方向观点"（只采纳今天/明天）：
- 对象必须是大盘/主要指数：只谈某个行业板块自身行情（科技/券商/房地产…）不算；行业消息需落到指数方向判断才算。
- 看涨/看跌、收阳/收阴、今日/明日的点位或方向判断（"今天反抽目标4010""明天还有一跌""今日收红"）。
- 带明确条件的今明倾向（"站稳4000今天就看多""明天不破X则反弹"）按条件倾向判多/空。
- 周期在 近日/本周/下周/更长，或时间模糊的中期判断 → 不属于超短，忽略（归波段板块）。
- 纯状态描述（"缩量震荡""进入调整"）、复盘已发生行情、仓位自述、只谈个股/与大盘无关 → has_view=false。
- 一条帖可能同时含超短与波段两层（如"今天反弹但本周仍调整"）——只取**超短那层**，quote 引今天/明天 的关键原话。
- 博主名下最新帖若不是超短表态、更早仍在窗口内的帖有 → 取更早那条超短表态（quote_post_n 指向它；发帖时间由系统标注，你不需要写时间）。

输出规则：
- 窗口内多次超短表态：取最新一次，无需交代更早翻转。
- stance 只允许 "多"/"空"；没有方向 → has_view=false。
- horizon 只从 {今天,明天} 选，按博主自己的时间词；原文不含今天/明天的超短表态 → has_view=false。
- quote = 该表态的**原话关键句**，逐字引用 ≤60 字，不许改写/润色/拼凑/编造；quote_post_n = 该帖编号（[0] 即 0）。
- summary = 1~2 句核心立场概括（≤60 字），写明方向与要点；**只写超短层**（今天/明天 怎么做），别把同一帖里 近日/本周/波段 的判断混进来。summary 里**禁用 今天/明天/今日/明日/昨日/昨天/后天 等相对日词**（各帖发帖日不同、词义会错位；目标日由系统在行头以绝对日期标注），只写方向/触发条件/目标位/应对。

输出严格 JSON，rows 数量与输入博主数一致、顺序一一对应：
{"rows":[{"blogger":"博主名","has_view":true,"stance":"多","horizon":"明天","summary":"概括(≤60字)","quote":"原话(≤60字)","quote_post_n":0}]}
只输出 JSON，无其他文字。"""


SWING_ROW_SYSTEM_PROMPT = """你是财经观点摘要助手。给你若干位「波段板块」博主在**前 3 个交易日 00:00 至现在**（交易日窗口，含今日盘中已发的帖）内的帖子：每位博主名下若干条，按发帖时间从新到旧排列，每条前有编号 [0][1]… 并带发帖时间。任务：判定每人在窗口内是否对 上证指数/大盘/主要指数 给出过 **波段(2日+)** 的明确方向观点，若有则产出该人最新一条波段表态。

时间口径（重要）：每条帖里的 本周/下周 以**该帖自己的发帖日**为基准（本周=发帖日所在周（周一~周五），下周=其后一周）；目标周已整体过去的表态由系统自动剔除，你无需推算卡片是哪天，只按发帖日判断词义。

判定"波段方向观点"（只采纳波段周期）：
- 对象必须是大盘/主要指数（上证指数尤其）：某行业/板块自身的趋势（"房地产要走2.3年结构性牛""券商主升"）不算大盘表态，除非明确落到指数方向/点位（"券商带动上证攻4000"）。
- 周期落在 近日/本周/下周/更长 的方向判断：看涨/看跌、某阶段收阳收阴、点位目标/支撑压力/顶底判断（"目标3900""本周调整""反弹见顶""回踩3800是波段底""下周还要寻底"）。
- 没有日历时间词、但明确是**一轮波段/结构性中期判断**（如"反弹见顶，接下来漫漫熊途""这波反弹结束后还要回踩3800"）→ 算波段，horizon 给 未提。
- 周期只在 今天/明天 → 不是波段表态，忽略（归超短板块）。
- 纯状态描述（"缩量震荡""进入调整"）、复盘已发生行情、仓位自述、理念分享、只谈个股/与大盘无关 → has_view=false。
- 一条帖可能同时含超短与波段两层（如"今天反弹但本周仍调整"）——只取**波段那层**，quote 引波段层关键原话。
- 博主名下最新帖若不是波段表态、更早仍在窗口内的帖有 → 取更早那条波段表态（quote_post_n 指向它；发帖时间由系统标注，你不需要写时间）。

输出规则：
- 窗口内多次波段表态：取最新一次，无需交代更早翻转。
- stance 只允许 "多"/"空"；没有方向 → has_view=false。
- horizon 只从 {近日,本周,下周,更长,未提} 选，按博主自己的时间词（只说目标点位、没给时间 → 未提）。
- quote = 该表态的**原话关键句**，逐字引用 ≤60 字，不许改写/润色/拼凑/编造；quote_post_n = 该帖编号（[0] 即 0）。
- summary = 1~2 句核心立场概括（≤60 字），写明方向与要点；**只写波段层**（阶段趋势/目标位），别把同一帖里 今天/明天 的超短赌性混进来。summary 里**禁用 今天/明天/今日/明日/昨日/昨天/后天/本周/下周/近日 等相对时间词**（各帖发帖日/周不同、会错位；本周/下周 的目标周由系统在行头以绝对周日期标注），只写方向/趋势/目标位/应对。

输出严格 JSON，rows 数量与输入博主数一致、顺序一一对应：
{"rows":[{"blogger":"博主名","has_view":true,"stance":"空","horizon":"本周","summary":"概括(≤60字)","quote":"原话(≤60字)","quote_post_n":0}]}
只输出 JSON，无其他文字。"""


PANEL_ROW_PROMPT = {
    "short": SHORT_ROW_SYSTEM_PROMPT,
    "swing": SWING_ROW_SYSTEM_PROMPT,
}


def _fmt_window_posts(blogger, posts):
    """窗口帖清单文本：[0] 发帖 MM-DD HH:MM ｜标题\n正文。posts 约定 新→旧。"""
    lines = [f"【博主】{blogger}（{len(posts)} 条窗口帖，新→旧）"]
    for i, p in enumerate(posts):
        content = (p.get("content") or "").strip()
        if len(content) > MAX_POST_CHARS:
            content = content[:MAX_POST_CHARS] + "…（已截断）"
        pub = (p.get("publish_date") or "")[:16]
        title = (p.get("title") or "").strip()
        head = f"[{i}] 发帖 {pub}"
        if title:
            head += f"｜{title}"
        lines.append(head + "\n" + content)
    return "\n".join(lines)


def _row_from_board_llm(blogger, board_key, d, posts):
    """把某博主某板块的单条 LLM 输出规整成板块行。

    只接受本板块周期白名单内的 多/空；越界（如超短板块给出 本周）→ 返回 None
    （该博主本板块不显示、不计数，log 提示）。quote_ts 由系统回填（引文时间权威
    在帖子，模型不誊写时间）。
    """
    d = d if isinstance(d, dict) else {}
    has = bool(d.get("has_view")) and d.get("stance") in ("多", "空")
    if not has:
        return None
    horizon = d.get("horizon")
    if horizon not in PANEL_HORIZONS[board_key]:
        log.warning("  %s [%s] horizon=%r 不在本板块周期内，判为无该周期观点",
                    blogger, board_key, horizon)
        return None
    quote_ts = None
    try:
        ni = int(d.get("quote_post_n") or 0)
    except (TypeError, ValueError):
        ni = -1
    if 0 <= ni < len(posts) and posts[ni].get("publish_time"):
        quote_ts = int(posts[ni]["publish_time"])
    elif posts and posts[0].get("publish_time"):
        quote_ts = int(posts[0]["publish_time"])
        log.warning("  %s [%s] quote_post_n=%r 越界，引文时间回退到其最新帖",
                    blogger, board_key, d.get("quote_post_n"))
    return {"blogger": blogger, "has_view": True, "stance": d["stance"],
            "horizon": horizon,
            "summary": _strip_rel_time((d.get("summary") or "").strip())[:ROW_SUMMARY_MAX],
            "quote": (d.get("quote") or "").strip()[:ROW_QUOTE_MAX],
            "quote_ts": quote_ts}


# ── v13：行抽取缓存（rows_cache.json，DeepSeek 增量复用）──
# 高频盘中档（30 分/档）若每档都把窗口帖重抽一遍 DeepSeek 太贵；某博主窗口帖
# 集合（含正文指纹）与上次一致 → 直接复用缓存行。缓存 key=博主；指纹必须含正文
# 内容（正文 done-dict 回填会让同 post_id 从标题帖变全文，只比 post_id 会命中
# 过期缓存）。缓存的是"未锚定行"：目标日随卡片日变，引用侧每轮仍 resolve_anchors。
_ROWS_CACHE_VERSION = 1


def _post_sig(p):
    """单帖内容指纹：标题+正文的前 8 位 sha1（正文回填 = 同 post_id 内容变了）。"""
    raw = ((p.get("title") or "") + "\n" + (p.get("content") or "")).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:8]


def _fingerprint(posts):
    """窗口帖集合指纹：每帖 f"{post_id}:{sig}"，顺序 = 喂给 LLM 的新→旧。"""
    return [f"{p.get('post_id') or i}:{_post_sig(p)}" for i, p in enumerate(posts)]


def _load_rows_cache():
    """rows_cache.json → {"version":1,"boards":{...}}；缺失/损坏返回空结构。"""
    try:
        with open(paths.ROWS_CACHE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("boards"), dict):
            return data
    except Exception:
        pass
    return {"version": _ROWS_CACHE_VERSION, "boards": {}}


def _save_rows_cache(cache):
    """原子写 rows_cache.json（先临时文件再 os.replace）。"""
    paths.ensure_dirs()
    tmp = paths.ROWS_CACHE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    os.replace(tmp, paths.ROWS_CACHE_FILE)


def extract_board_rows(board_key, by_member, window_start_ts=None):
    """某板块行抽取：{博主: [窗口内帖子 新→旧]} → (rows, errors)。

    只保留 has_view 的板块行；无该周期观点 / 无帖 / 抽取失败 → 不入 rows
    （板块不显示、不计数）。同一博主在 超短/波段 两板块各调一次、互不串扰。

    v13 增量缓存：博主窗口帖集合指纹命中 → 跳过 DeepSeek 复用缓存行（含
    has_view=false 的 null 缓存，省一档重抽）；LLM 失败不写缓存（下档重试）。
    window_start_ts = 本板块窗口下界 epoch（北京时 now.date()-K 当天 00:00），
    缓存行引文早于它 → 判失效重抽（防御；指纹一致时理论上恒不触发）。
    """
    ext = _extract()
    system_prompt = PANEL_ROW_PROMPT[board_key]
    rows, errors = {}, []
    work = {}
    for b, posts in by_member.items():
        clean = []
        for p in posts:
            content = (p.get("content") or "").strip()
            if content == "[视频帖]" or len(content) < 5:
                continue
            clean.append(p)
        clean = clean[:config.ROWS_MAX_POSTS]
        if clean:
            work[b] = clean
    if not work:
        return rows, errors

    cache = _load_rows_cache()
    bc = cache["boards"].setdefault(board_key, {})
    fps = {}
    hits, todo = 0, []
    for b, posts in work.items():
        fp = _fingerprint(posts)
        fps[b] = fp
        ent = bc.get(b)
        if isinstance(ent, dict) and ent.get("posts") == fp:
            crow = ent.get("row")
            if crow is None:
                hits += 1          # 缓存判定：该博主无本板块观点 → 命中跳过
                continue
            if isinstance(crow, dict) and crow.get("quote_ts"):
                q = int(crow["quote_ts"])
                if window_start_ts is None or q >= window_start_ts:
                    rows[b] = crow  # 缓存行复用（resolve_anchors 每轮重锚）
                    hits += 1
                    continue
        todo.append(b)
    if not todo:
        log.info("  [%s] 行缓存：%d 位博主全部命中，跳过 DeepSeek", board_key, hits)
        return rows, errors
    if hits:
        log.info("  [%s] 行缓存：复用 %d / 需新抽 %d", board_key, hits, len(todo))

    items = [(b, work[b]) for b in sorted(todo)]  # 确定序，便于 batch 对齐
    bsize = config.ROWS_BATCH_BLOGGERS
    now_txt = datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M")

    def _call_group(group):
        user_msg = "\n\n----\n\n".join(_fmt_window_posts(b, posts) for b, posts in group)
        label = f"briefing:{board_key}:" + "&".join(b for b, _ in group)
        for _attempt in range(ROW_MAX_ATTEMPTS):
            result, raw = ext.call_json(None, system_prompt, user_msg, label)
            if result is not None:
                return result, group
        return None, group

    wrote = False
    groups = [items[i:i + bsize] for i in range(0, len(items), bsize)]
    with ThreadPoolExecutor(max_workers=config.ROWS_WORKERS) as pool:
        for result, group in pool.map(_call_group, groups):
            if result is None:
                for b, _posts in group:
                    errors.append(b)   # LLM 失败不写缓存，下档整组重试
                continue
            got = result.get("rows") or []
            for j, (b, _posts) in enumerate(group):
                d = got[j] if j < len(got) else {}
                row = _row_from_board_llm(b, board_key, d, work[b])
                if row:
                    rows[b] = row
                bc[b] = {"posts": fps[b], "row": row, "at": now_txt}  # row=None 也缓存（无观点）
                wrote = True
    if wrote:
        _save_rows_cache(cache)
        log.info("  [%s] 行缓存已更新：共 %d 条", board_key, len(bc))
    return rows, errors


# =====================================================================
# v12：日期锚定——把博主帖子里的相对时间词换算成绝对目标日/周
# =====================================================================
# 行抽取拿到的是博主原话里的相对词（今天/明天；本周/下周），它们以
# **发帖日**为基准，卡片日一变就错位（"昨天说的明天"=今天却标成明天）。
# 这里统一按引文发帖时间换算成绝对目标：
#   - 超短：目标日 = 发帖日(今天) / 发帖日之下一交易日(明天)；只保留
#     目标日落在 {卡片日, 卡片日之下一交易日} 的表态（已兑现/过期自动剔除）。
#   - 波段：本周/下周 锚定到发帖日所在周（周一~周五）；目标周已整体过去 → 剔除。
#     anchor = 周词(相对卡片日) + 周一~周五日期段。
#   - 近日/更长/未提：无具体日期可锚，原词保留（未提 → 行头不打印周期）。
# 计数/渲染/总结都只基于解析后的 rows_by_board（本板块不显示即不计数）。

def _bj_date(ts):
    """epoch → 北京时日期；无/非法返回 None。"""
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(int(ts), tz=BEIJING).date()
    except (TypeError, ValueError, OSError):
        return None


def _week_monday(d):
    """博主视角"本周"周一：周一~周五取当周周一；周六/日取下一周一（周末帖多预判将临一周）。"""
    m = d - timedelta(days=d.weekday())
    if d.weekday() >= 5:
        m += timedelta(days=7)
    return m


def _fmt_week_range(monday):
    """周展示段：周一~周五。"""
    return f"{monday:%m-%d}~{(monday + timedelta(days=4)):%m-%d}"


def next_trading_day(d):
    """d 之后最近一个交易日（委托 calendar；跨周末/节假日顺延）。"""
    return calendar.next_trading_day(d)


# 行摘要的相对时间词剥离（v12 系统侧兜底，不靠模型自觉）：头行 anchor 与引文
# 绝对时间已把博主相对词锚定到具体日期，摘要再出现 今天/明天/本周 只会制造
# "昨天说的明天"式错位 → 一律剔除。周X（周五）、周初/周中/周内等"锚定周内"表述
# 与纯数字日期保留（周由头行 anchor 钉住）。
_REL_TIME_RE = re.compile(
    r"(今天|今日|明天|明日|昨天|昨日|后天|本周|下周|上周|近日|当周)"
    r"(?![一二三四五六日末初内天])"
)


def _strip_rel_time(s):
    if not s:
        return s
    s = _REL_TIME_RE.sub("", s)
    s = s.lstrip("，。；、 ")
    s = re.sub(r"[，。；]{2,}", lambda m: m.group(0)[0], s)  # 剔除后"，，"→"，"
    return re.sub(r"\s+", " ", s).strip()


def _anchor_row(board_key, row, card):
    """把单条板块行按卡片日解析出绝对目标 anchor + 过期过滤。

    返回规整后的 row（带 anchor），或 None（该博主本板块不显示）。
    """
    horizon = row.get("horizon") or "未提"
    qd = _bj_date(row.get("quote_ts"))
    if board_key == "short":
        if qd is None:
            return None  # 无引文发帖时间无法解析目标日，不外显（防编造）
        target = qd if horizon == "今天" else next_trading_day(qd)  # 明天 = 下一交易日
        if target not in (card, next_trading_day(card)):
            log.info("  %s [short] %s(发帖 %s) 目标日 %s 不在今/下一交易日 → 不显示",
                     row.get("blogger"), horizon, qd, target)
            return None
        out = dict(row)
        out["anchor"] = target.strftime("%m-%d")
        return out
    # 波段
    if horizon in ("本周", "下周"):
        if qd is None:
            out = dict(row)
            out["anchor"] = horizon
            return out
        mon = _week_monday(qd) + (timedelta(days=7) if horizon == "下周" else timedelta())
        if mon + timedelta(days=4) < card:
            log.info("  %s [swing] %s 目标周 %s 已整体过去 → 不显示",
                     row.get("blogger"), horizon, _fmt_week_range(mon))
            return None
        cw = _week_monday(card)
        if mon == cw:
            word = "本周"
        elif mon == cw + timedelta(days=7):
            word = "下周"
        else:
            word = ""  # 极端情况只留日期段
        out = dict(row)
        out["anchor"] = f"{word} {_fmt_week_range(mon)}" if word else _fmt_week_range(mon)
        return out
    out = dict(row)
    out["anchor"] = horizon if horizon != "未提" else ""
    return out


def resolve_anchors(rows_by_board, now):
    """按卡片日对两板块行做日期锚定 + 过期剔除 → {key: {博主: 规整行(带 anchor)}}。

    now 为北京时 datetime（卡片日 = now.date()）。超短剔除目标已过/未指向今明者；
    波段剔除目标周已过者。
    """
    card = now.date()
    out = {}
    for key in config.PANEL_KEYS:
        live = {}
        for name, row in (rows_by_board.get(key) or {}).items():
            resolved = _anchor_row(key, row, card)
            if resolved is not None:
                live[name] = resolved
        out[key] = live
    return out


# =====================================================================
# v11/v12：双板块计数 + LEGACY 跨板块收敛总结（v13 单板块总结见文件尾）
# =====================================================================

def board_counts(rows_by_board):
    """双板块计数：每板块在成员名单内统计 多/空，shown=bull+bear，members=名单长度。

    rows_by_board: {key: {博主: row}}（仅 has_view 行，见 extract_board_rows）。
    未表态成员不计数（也不显示），无"合计=xx"断言。
    """
    out = {}
    for key in config.PANEL_KEYS:
        members = config.PANELS[key]
        rows = rows_by_board.get(key) or {}
        bull = sum(1 for n in members if (rows.get(n) or {}).get("stance") == "多")
        bear = sum(1 for n in members if (rows.get(n) or {}).get("stance") == "空")
        out[key] = {"bull": bull, "bear": bear, "shown": bull + bear,
                    "members": len(members)}
    return out


SUMMARY_SYSTEM_PROMPT = """你是财经观点收敛总结助手。给你当前推送的两板块博主方向观点快照：两板块各自成组（组内每行 = 一位博主在该板块周期内的最新表态：多/空 · 周期词 · 一句核心 · 引文发帖时间），外加**系统统计的分档权威计数**（每板块 几多几空、N/M 人表态）与大盘行情。

板块即两层：
- 「超短(0-1日)」= 博主对今天/明天的表态；「波段(2日+)」= 对近日/本周/下周/更远的表态。
- 开头 8 位博主两板块都上榜（同一人既给短线看法也给波段看法），属同一位作者的**跨周期分层**，不是观点翻转；他们在两块立场可能不同甚至相反，这是自洽的（短线反弹≠波段反转）。
- **跨板块方向相反不是对立而是层叠**：如"超短板块多人看今日/明日反弹 + 波段板块多人看调整未结束"——两层兼容，反弹常是波段调整中的反抽/减仓窗口。**严禁**用"多空对立/对峙/拉锯/分歧显著/阵营 X 比 X"描述跨板块组合。
- 只有**同板块内真反向**（同为超短板块：有人看反弹、有人看续跌）才算方向分歧，才可点出；某板块清一色同向、或仅 1~2 人表态时，直接陈述方向即可，不要为凑"分歧/反向"字样而硬造。
- 同板块同方向但操作取向相反（都看反弹、一个持有、一个反弹减仓）→ 写"反弹共识下的操作分化"，不算方向对立。
- **日期纪律**：卡片日见输入【日期锚点】。快照每行的 anchor/引文时间已是系统按引文发帖日换算好的绝对结果（如 ▍孙万林：空·09-03｜…｜引文 09-02 15:06 ＝ 该博主 09-02 帖里写的"明天"，实指 09-03）。涉及某位博主的目标日，**只能照抄该行 anchor 或引文日期，禁止自己再用 明天/今日/下周 等词做任何推算**（原话再怎么写也别展开）；拿不准就只讲方向/逻辑/应对，不写具体日期。板块级可写 今日/明日/本周，但必须对应【日期锚点】的卡片日/下一交易日/本周周段；结尾操作句不必带日期。

输出**一段收敛总结**（≤280 字，中文流畅一段；不要分点/列表/小标题）：
① 开头按板块陈述版图，用系统给的**分档计数**，不要合并总比数、不要重算（如：超短板块 X 多 Y 空、N 人表态，多数看今日/明日…；波段板块 U 多 V 空、M 人表态，多数认为…）。
② 中间每板块点 1~2 位代表博主（只点快照里出现过的）；两板块方向相反时解释它们的层叠关系；只在同板块真反向时用"分歧"；若 8 位双板块博主里有人短线与波段立场不同，可点名讲其分层自洽。
③ 结尾落到操作参考：超短(0-1日) 一句 + 波段(2日+) 一句。
禁止复述引文原话；禁止提快照之外的博主或内容；禁止编造计数。
输出严格 JSON：{"summary": "收敛总结一段(≤280字)"}。只输出 JSON，无其他文字。"""


def summarize_boards(rows_by_board, counts, market_text, slot_label, date_str,
                     window_txt="", now=None):
    """LEGACY（v12 跨板块两层收敛总结）：v13 已拆成单板块 summarize_board，
    本函数不再被 run_briefing 接线，仅保留供历史复刻/回退。

    两板块方向快照 + 系统计数 → 一段跨板块收敛总结（板块即两层）。
    rows_by_board/counts 形状见 extract_board_rows/board_counts（rows 应已过
    resolve_anchors 日期锚定）；now 为北京时 datetime（决定卡片日与"今天/明日"措辞）。
    失败兜底返回双板块计数行。
    """
    now = now or datetime.now(BEIJING)
    card = now.date()
    nm = calendar.next_trading_day(card)
    cw = _week_monday(card)
    wd = ("一", "二", "三", "四", "五", "六", "日")[card.weekday()]
    anchor_note = (f"卡片日={card:%m-%d}（周{wd}）｜下一交易日(明日)={nm:%m-%d}｜"
                   f"本周={_fmt_week_range(cw)}｜下周={_fmt_week_range(cw + timedelta(days=7))}")
    ext = _extract()
    snap = []
    for key in config.PANEL_KEYS:
        c = counts.get(key) or {}
        meta = config.BOARD_META[key]
        lines = [f"【{meta['label']} · {c.get('bull', 0)}多/{c.get('bear', 0)}空"
                 f"（{c.get('shown', 0)}/{c.get('members', 0)} 表态）】"]
        rows = rows_by_board.get(key) or {}
        for b in config.PANELS[key]:
            r = rows.get(b)
            if not r:
                continue
            ts = r.get("quote_ts")
            t = datetime.fromtimestamp(int(ts), tz=BEIJING).strftime("%m-%d %H:%M") if ts else ""
            lab = r.get("anchor")
            if lab is None:  # 兜底：未锚定的历史行退回周期词
                h = r.get("horizon") or "未提"
                lab = "" if h == "未提" else h
            lines.append(f"▍{b}：{r['stance']}" + (f"·{lab}" if lab else "")
                         + f"｜{r.get('summary')}｜引文 {t}")
        snap.append("\n".join(lines))
    snapshot_txt = "\n".join(snap) if any(s.strip() for s in snap) else "（两板块均无方向观点）"
    counts_txt = config.format_board_counts(counts)
    user_msg = f"""【时段】{date_str} {slot_label}（{window_txt or '本期'}）
【日期锚点】{anchor_note}
【行情】{market_text}
【系统统计（权威，勿重算勿合并）】{counts_txt}
【两板块方向快照】
{snapshot_txt}"""
    for _attempt in range(ROW_MAX_ATTEMPTS):
        result, raw = ext.call_json(None, SUMMARY_SYSTEM_PROMPT, user_msg, "briefing:summarize_boards")
        if result is None:
            continue
        s = (result.get("summary") or "").strip()
        if s:
            return s[:320]
    return f"多空版图：{counts_txt}"


# =====================================================================
# v13：单板块收敛总结（超短/波段 各推各群、各自一段，无跨板块层叠）
# =====================================================================
# 与 LEGACY 跨板块 prompt 的区别：快照只注入本板块名单行、计数用单板块
# format_board_count、日期纪律按板块收窄（short：今日/明日 = 卡片日/下一交易
# 日；swing：波段目标不可能是今明，本周/下周 周段见锚点）、无任何"板块即两层 /
# 前 8 位双板块博主 / 跨板块层叠禁对立"等跨板块句子。

SHORT_SUMMARY_SYSTEM_PROMPT = """你是财经观点收敛总结助手。给你「超短板块」博主的方向观点快照：每行 = 一位博主的最新**超短(0-1日)**表态 —— 多/空 · 目标日(anchor，如 ·09-03) · 一句核心 · 引文发帖时间；外加**系统统计的本板块权威计数**（X 多/Y 空、N/M 人表态）与大盘行情。

口径（本卡只有超短这一层，不存在另一板块）：
- 快照成员都是只对 今天/明天 做过方向表态、且目标日落在【日期锚点】的 卡片日/下一交易日 的博主；行头 anchor 即系统按发帖日换算的绝对目标日（如 ·09-03），引文时间为发帖时刻。
- 同板块内真反向（同对今/明：有人看反弹、有人看续跌）才算方向分歧、才可点出；清一色同向、或仅 1~2 人表态时直接陈述方向，不要为凑"分歧"而硬造。
- 同方向但操作取向相反（都看反弹、一个持有、一个反弹减仓）→ 写"反弹共识下的操作分化"，不算方向对立。
- **日期纪律**：卡片日与下一交易日见【日期锚点】。涉及某位博主的目标日**只能照抄该行 anchor 或引文日期**，禁止自己用 今天/明天/今日 等词推算任何博主表态所指的日子（原话再怎么写也别展开）；拿不准就只讲方向/逻辑/应对。板块整体措辞可写 今日/明日，但必须对应【日期锚点】的 卡片日/下一交易日；结尾操作句不必带日期。

输出**一段收敛总结**（≤240 字，中文流畅一段；不要分点/列表/小标题）：
① 开头用系统给的**本板块计数**陈述版图（如：超短板块 X 多 Y 空、N 人表态，多数看今日反弹…）；
② 中间点 1~2 位代表博主（只点快照里出现过的，讲观点要点与操作取向）；
③ 结尾一句超短(0-1日)操作参考。
禁止复述引文原话；禁止提快照之外的博主或内容；禁止编造计数。
输出严格 JSON：{"summary": "收敛总结一段(≤240字)"}。只输出 JSON，无其他文字。"""


SWING_SUMMARY_SYSTEM_PROMPT = """你是财经观点收敛总结助手。给你「波段板块」博主的方向观点快照：每行 = 一位博主的最新**波段(2日+)**表态 —— 多/空 · 目标周/周期(anchor，如 本周 08-31~09-04，或 近日/更长 原词) · 一句核心 · 引文发帖时间；外加**系统统计的本板块权威计数**（X 多/Y 空、N/M 人表态）与大盘行情。

口径（本卡只有波段这一层，不存在另一板块）：
- 快照成员都是对 近日/本周/下周/更长 做过方向表态、且目标周未整体过去的博主；行头 anchor 是系统按发帖日锚定的绝对周段（本周/下周 = 周一~周五日期段）或 近日/更长 原词。
- 同板块内真反向才算方向分歧、才可点出；清一色同向、或仅 1~2 人表态时直接陈述方向，不要为凑"分歧"而硬造。
- 同方向但操作取向相反（都看震荡调整、一个减仓、一个等待低吸）→ 写"共识下的操作分化"，不算方向对立。
- **日期纪律**：卡片日与 本周/下周 周段见【日期锚点】。波段目标不可能是 今天/明天 这类超短词——涉及某位博主的目标周**只能照抄该行 anchor 或引文日期**，禁止自己用 本周/下周/周X 推算；近日/更长 表态只讲方向逻辑、不补具体日期。板块整体可写 本周/下周，但必须对应【日期锚点】的周段；结尾操作句不必带日期。

输出**一段收敛总结**（≤240 字，中文流畅一段；不要分点/列表/小标题）：
① 开头用系统给的**本板块计数**陈述版图（如：波段板块 X 多 Y 空、N 人表态，多数认为…）；
② 中间点 1~2 位代表博主（只点快照里出现过的，讲观点要点）；
③ 结尾一句波段(2日+)操作参考。
禁止复述引文原话；禁止提快照之外的博主或内容；禁止编造计数。
输出严格 JSON：{"summary": "收敛总结一段(≤240字)"}。只输出 JSON，无其他文字。"""


PANEL_SUMMARY_PROMPT = {
    "short": SHORT_SUMMARY_SYSTEM_PROMPT,
    "swing": SWING_SUMMARY_SYSTEM_PROMPT,
}


def summarize_board(board_key, rows, counts, market_text, date_str,
                    window_txt="", now=None):
    """单板块方向快照 + 本板块计数 → 一段本板块收敛总结（v13 主路径）。

    rows = 该板块 {博主: 已过 resolve_anchors 的规整行}；counts = 该板块单键计数
    {bull,bear,shown,members}；now 为北京时 datetime（决定卡片日 / 本周周段措辞）。
    失败兜底返回 config.format_board_count 单板块计数行。
    """
    now = now or datetime.now(BEIJING)
    card = now.date()
    wd = ("一", "二", "三", "四", "五", "六", "日")[card.weekday()]
    if board_key == "short":
        nm = calendar.next_trading_day(card)
        anchor_note = f"卡片日={card:%m-%d}（周{wd}）｜下一交易日(明日)={nm:%m-%d}"
    else:
        cw = _week_monday(card)
        anchor_note = (f"卡片日={card:%m-%d}（周{wd}）｜"
                       f"本周={_fmt_week_range(cw)}｜下周={_fmt_week_range(cw + timedelta(days=7))}")
    meta = config.BOARD_META[board_key]
    c = counts or {}
    lines = [f"【{meta['label']} · {c.get('bull', 0)}多/{c.get('bear', 0)}空"
             f"（{c.get('shown', 0)}/{c.get('members', 0)} 表态）】"]
    for b in config.PANELS[board_key]:
        r = rows.get(b)
        if not r:
            continue
        ts = r.get("quote_ts")
        t = datetime.fromtimestamp(int(ts), tz=BEIJING).strftime("%m-%d %H:%M") if ts else ""
        lab = r.get("anchor")
        if lab is None:  # 兜底：未锚定的历史行退回周期词
            h = r.get("horizon") or "未提"
            lab = "" if h == "未提" else h
        lines.append(f"▍{b}：{r['stance']}" + (f"·{lab}" if lab else "")
                     + f"｜{r.get('summary')}｜引文 {t}")
    snapshot_txt = "\n".join(lines)
    counts_txt = config.format_board_count(board_key, c)
    user_msg = f"""【时段】{date_str}（{window_txt or '本期'}）
【日期锚点】{anchor_note}
【行情】{market_text}
【系统统计（权威，勿重算）】{counts_txt}
【{meta['label']}方向快照】
{snapshot_txt}"""
    ext = _extract()
    prompt = PANEL_SUMMARY_PROMPT[board_key]
    for _attempt in range(ROW_MAX_ATTEMPTS):
        result, raw = ext.call_json(None, prompt, user_msg, f"briefing:summarize:{board_key}")
        if result is None:
            continue
        s = (result.get("summary") or "").strip()
        if s:
            return s[:240]
    return f"多空版图：{counts_txt}"
