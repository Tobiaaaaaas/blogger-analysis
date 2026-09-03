# -*- coding: utf-8 -*-
"""简报生成：DeepSeek 抽取与收敛。

复用父仓库 scripts/pipeline/extract_signals_direction.py 的 DeepSeek 调用底座
（call_json / parse_response / watchdog 硬超时），保证与信号提取同一套稳定链路。

v9（2026-09 redesign：18 人窗口速览卡）主路径：
  1. extract_window_rows：每博主近 3 交易日窗口内帖子 → 最新方向观点行（摘要+逐字原话）。
  2. summarize_window：18 行快照 + 系统多空计数 → 一段收敛总结。
旧 v8 全板共识路径（POINTS_SYSTEM_PROMPT / SYNTH_SYSTEM_PROMPT / extract_points / synthesize）
已不再被 run_briefing 调用，仅保留作 LEGACY。
"""
import importlib.util
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from . import config, paths

log = logging.getLogger("briefing")

MAX_POST_CHARS = 1200   # 单帖正文截断长度（足够承载一篇观点帖）
POINTS_BATCH = 8        # 抽点每批帖子数（LEGACY）
SYNTH_MAX_ATTEMPTS = 3  # 综合最大尝试次数（LEGACY）

ROW_MAX_ATTEMPTS = 3     # 行抽取 / 收敛总结最大尝试次数
ROW_SUMMARY_MAX = 60     # summary ≤60 字
ROW_QUOTE_MAX = 60       # 逐字原话 ≤60 字
BEIJING = timezone(timedelta(hours=8))

# LLM horizon 标签 → 两档（与父引擎 SKILL 两档口径一致：今天/明天=超短 0-1；其余=波段 2+）
HORIZON_BUCKET = {
    "今天": config.BUCKET_SHORT, "明天": config.BUCKET_SHORT,
    "近日": config.BUCKET_SWING, "本周": config.BUCKET_SWING, "下周": config.BUCKET_SWING,
    "更长": config.BUCKET_SWING, "未提": config.BUCKET_SWING,
}
HORIZONS = ("今天", "明天", "近日", "本周", "下周", "更长", "未提")

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
# v9：18 人窗口行抽取（近 3 交易日方向观点 → 摘要 + 逐字原话）
# =====================================================================

WINDOW_ROW_SYSTEM_PROMPT = """你是财经观点摘要助手。你会收到若干位今日头条博主近期的帖子：每位博主名下若干条，按【发帖时间从新到旧】排列，每条前有编号 [0][1]… 并带发帖时间。你的任务：对**每位博主**判定其近 3 个交易日内是否对 上证指数/大盘/主要指数 给出过**明确方向观点**，并产出该博主当前最相关的一条方向观点。

判定"明确方向观点"：
- 有明确方向才算：看涨/看跌、收阳/收阴、点位目标（"目标3900""站稳4000看多"）、带明确条件的后市倾向（"放量站上X则看多"）。
- 周期只有 今天/明天 的超短线也算方向观点（horizon 如实给"今天/明天"）。
- 纯状态描述（"缩量震荡""进入调整"）、复盘已发生行情、仓位自述、理念分享、只谈个股/与大盘无关 → has_view=false。
- 博主名下最新一条帖若非方向观点、但更早仍在窗口内的帖有方向观点 → 仍取那条方向观点（quote_post_n 指向它；系统按它真实的发帖时间标注，你不需要写时间）。

输出规则：
- 窗口内多次方向表态：以**最新一条方向观点**为主立场；若期间翻转（如先多后空），summary 用一句带过。
- quote = 该方向观点的**原话关键句**，逐字引用 ≤60 字，**不许改写、润色、拼凑、编造**；quote_post_n = 该帖在博主帖子清单里的编号（[0] 即 0）。
- horizon 从 {今天,明天,近日,本周,下周,更长,未提} 里选，依据引文里博主自己的时间表述；博主没给时间 → 未提。
- summary = 1~2 句核心立场概括（≤60 字），写明方向与要点。
- stance 只有 "多" 或 "空"；中性/无方向 → has_view=false。
- 无方向观点 → has_view=false，summary/quote/horizon 填空字符串，quote_post_n 填 0。

输出严格 JSON，rows 数量与输入博主数一致、顺序一一对应：
{"rows":[{"blogger":"博主名","has_view":true,"stance":"多","horizon":"明天","summary":"概括(≤60字)","quote":"原话(≤60字)","quote_post_n":0}]}
只输出 JSON，无其他文字。"""


def _row_placeholder(blogger, n_posts=0):
    """无观点 / 无帖 / 抽取失败的占位行。n_posts>0 表示"有发帖但未明确表态"。"""
    return {"blogger": blogger, "has_view": False, "stance": "", "horizon": "",
            "bucket": "", "summary": "", "quote": "", "quote_ts": None, "n_posts": n_posts}


def _row_from_llm(blogger, d, posts):
    """把某博主的单条 LLM 输出规整成行；quote_ts 由系统回填（引文时间权威在帖子，模型不誊写时间）。"""
    d = d if isinstance(d, dict) else {}
    has = bool(d.get("has_view")) and d.get("stance") in ("多", "空")
    if not has:
        return _row_placeholder(blogger, n_posts=len(posts))
    horizon = d.get("horizon") if d.get("horizon") in HORIZONS else "未提"
    bucket = HORIZON_BUCKET.get(horizon, config.BUCKET_SWING)
    # 引文来源帖 = quote_post_n 指向（posts 为窗口内 新→旧 列表）。越界/缺 → 回退最新帖并记 warning。
    quote_ts = None
    try:
        ni = int(d.get("quote_post_n") or 0)
    except (TypeError, ValueError):
        ni = -1
    if 0 <= ni < len(posts) and posts[ni].get("publish_time"):
        quote_ts = int(posts[ni]["publish_time"])
    elif posts and posts[0].get("publish_time"):
        quote_ts = int(posts[0]["publish_time"])
        log.warning("  %s quote_post_n=%r 越界，引文时间回退到其最新帖", blogger, d.get("quote_post_n"))
    return {"blogger": blogger, "has_view": True, "stance": d["stance"], "horizon": horizon,
            "bucket": bucket, "summary": (d.get("summary") or "").strip()[:ROW_SUMMARY_MAX],
            "quote": (d.get("quote") or "").strip()[:ROW_QUOTE_MAX],
            "quote_ts": quote_ts, "n_posts": len(posts)}


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


def extract_window_rows(by_blogger):
    """每博主近 3 交易日方向观点 → ({博主: row}, errors)。

    by_blogger: {博主: [窗口内帖子 新→旧]}（已按 config.ROWS_MAX_POSTS 裁剪由调用方，或此处再裁）。
    返回行行字段见 _row_from_llm；无帖/视频帖博主不调 LLM，直接占位。失败博主 → errors + 占位行。
    """
    ext = _extract()
    rows, errors = {}, []
    work = {}
    for b, posts in by_blogger.items():
        clean = []
        for p in posts:
            content = (p.get("content") or "").strip()
            if content == "[视频帖]" or len(content) < 5:
                continue
            clean.append(p)
        clean = clean[:config.ROWS_MAX_POSTS]
        if not clean:
            rows[b] = _row_placeholder(b, n_posts=0)
        else:
            work[b] = clean
    if not work:
        return rows, errors

    items = sorted(work.items())  # 确定序，便于 batch 对齐
    bsize = config.ROWS_BATCH_BLOGGERS

    def _call_group(group):
        user_msg = "\n\n----\n\n".join(_fmt_window_posts(b, posts) for b, posts in group)
        label = "briefing:rows:" + "&".join(b for b, _ in group)
        for _attempt in range(ROW_MAX_ATTEMPTS):
            result, raw = ext.call_json(None, WINDOW_ROW_SYSTEM_PROMPT, user_msg, label)
            if result is not None:
                return result, group
        return None, group

    groups = [items[i:i + bsize] for i in range(0, len(items), bsize)]
    with ThreadPoolExecutor(max_workers=config.ROWS_WORKERS) as pool:
        for result, group in pool.map(_call_group, groups):
            if result is None:
                for b, _posts in group:
                    errors.append(b)
                    rows[b] = _row_placeholder(b, n_posts=len(work[b]))
                continue
            got = result.get("rows") or []
            for j, (b, _posts) in enumerate(group):
                d = got[j] if j < len(got) else {}
                rows[b] = _row_from_llm(b, d, work[b])
    return rows, errors


# =====================================================================
# v9：卡底收敛总结（18 行快照 + 系统分档计数 → 一段散文）
# =====================================================================

SUMMARY_SYSTEM_PROMPT = """你是财经观点收敛总结助手。给你近 3 个交易日内 18 位追踪博主的方向观点快照：每位一行（博主名/多空/周期原话词/一句核心/引文发帖时间），外加**系统统计的分档权威计数**（超短(0-1日) 几多几空、波段(2日+) 几多几空、观望、无更新）与当前大盘行情。

周期分层视角（最重要的原则）：观点按周期分两层——「超短(0-1日)」= 博主指今天/明天；「波段(2日+)」= 近日/本周/下周/更远。**跨周期的不同方向不是对立，而是层叠**：
- 例：超短多人看"今日/明日反弹"，同时波段多人看空"调整未结束/还要寻底"——两层完全兼容：反弹常是波段调整中的反抽/减仓窗口，不代表反转。此时应写层叠关系（"波段偏弱下，超短反弹更像反抽而非反转信号"），**严禁**用"多空对立/对峙/拉锯/分歧显著/多空阵营 X 比 X"描述跨周期组合。
- 只有**同周期内真反向**（如同为超短：有人看今天反弹、有人看今天续跌）才算方向分歧，才可点出。
- 同周期同方向但操作取向相反（都看今天反弹，一个持有待涨、一个提示反弹减仓）→ 写"反弹共识下的操作分化"，不算方向对立。

输出**一段收敛总结**（≤240 字，中文流畅一段；不要分点/列表/小标题）：
① 开头按层陈述版图，用系统给的**分档计数**，不要合并成总比数、不要重算、不要编造（如：超短 X 多 Y 空、多数看今日/明日反弹；波段 U 多 V 空、多数认为调整未结束）。
② 中间给层内代表观点（每层可点名 1~2 位，只点快照里出现过的）；若两层方向相反，解释它们的层叠关系（怎么兼容、落点差异）；只在同周期真反向时用"分歧"。
③ 结尾落到操作参考：超短(0-1日) 一句 + 波段(2日+) 一句。
禁止复述引文原话；禁止提快照之外的博主或内容。
输出严格 JSON：{"summary": "收敛总结一段(≤240字)"}。只输出 JSON，无其他文字。"""


def count_rows(rows):
    """18 行口径**分档**计数（v10：超短/波段各自多空，废弃跨周期合并总比数）。

    rows: {博主: row}（字段见 _row_from_llm）。每博主必落一格：
      多/空 + bucket∈{超短(0-1日), 波段(2日+)} → short/swing 对应格；
      has_view=False 且 n_posts>0 → neutral（有发帖未明确表态）；
      否则 → none（无更新）。断言合计=18。
    """
    c = {"short": {"bull": 0, "bear": 0},
         "swing": {"bull": 0, "bear": 0},
         "neutral": 0, "none": 0}
    for name in config.ROSTER:
        r = rows.get(name) or {}
        if not r.get("has_view"):
            if r.get("n_posts"):
                c["neutral"] += 1
            else:
                c["none"] += 1
            continue
        stance = r.get("stance")
        if stance not in ("多", "空"):
            if r.get("n_posts"):
                c["neutral"] += 1
            else:
                c["none"] += 1
            continue
        key = "bull" if stance == "多" else "bear"
        bucket = r.get("bucket")
        c["short" if bucket == config.BUCKET_SHORT else "swing"][key] += 1
    total = (c["short"]["bull"] + c["short"]["bear"] + c["swing"]["bull"] + c["swing"]["bear"]
             + c["neutral"] + c["none"])
    if total != len(config.ROSTER):
        log.warning("计数异常：超短%d多/%d空 波段%d多/%d空 观望%d 无更新%d ≠ %d",
                    c["short"]["bull"], c["short"]["bear"], c["swing"]["bull"],
                    c["swing"]["bear"], c["neutral"], c["none"], len(config.ROSTER))
    return c


def summarize_window(rows, counts, market_text, slot_label, date_str, window_txt=""):
    """18 行方向快照 → 一段收敛总结（周期分层视角）。分档计数系统权威注入，模型只写散文。

    counts 为 count_rows 的分档 shape；失败兜底返回分档计数行文案。
    """
    ext = _extract()
    snap = []
    for b in config.ROSTER:
        r = rows.get(b)
        if not r or not r.get("has_view"):
            continue
        ts = r.get("quote_ts")
        t = datetime.fromtimestamp(int(ts), tz=BEIJING).strftime("%m-%d %H:%M") if ts else ""
        snap.append(f"▍{b}：{r['stance']}·{r.get('bucket')}｜{r.get('horizon')}｜{r.get('summary')}｜引文 {t}")
    snapshot_txt = "\n".join(snap) if snap else "（无方向观点）"
    counts_txt = config.format_counts(counts)
    user_msg = f"""【时段】{date_str} {slot_label}（{window_txt or '近3个交易日'}）
【行情】{market_text}
【系统统计（权威，勿重算勿合并）】{counts_txt}
【博主方向快照】
{snapshot_txt}"""
    for _attempt in range(ROW_MAX_ATTEMPTS):
        result, raw = ext.call_json(None, SUMMARY_SYSTEM_PROMPT, user_msg, "briefing:summarize_window")
        if result is None:
            continue
        s = (result.get("summary") or "").strip()
        if s:
            return s[:300]
    return f"多空版图（分档）：{counts_txt}"
