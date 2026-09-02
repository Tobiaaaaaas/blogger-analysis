#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepSeek 自动提取 Direction 方向信号（替代 Claude 人工逐条标注）。

流程：读 data/posts/<博主>.json 的帖子 + <博主>_bodies_s*.json 的正文
      → 分批调 DeepSeek flash 按 SKILL.md §1~§8 规则逐条判断
      → 脚本强校验（spec/idx/cat/日期/去重，非法条目丢弃）
      → 自查：把「已提取信号 + 原文」回喂 DeepSeek 做独立审查（keep/fix/drop/补加）
      → 写 data/direction_signals/<博主>.json

用法：
  export DEEPSEEK_API_KEY="sk-..."      # 只经环境变量，绝不写入文件/提交
  python scripts/pipeline/extract_signals_direction.py <博主名>
  python scripts/pipeline/extract_signals_direction.py <博主名> --batch-size 25
  python scripts/pipeline/extract_signals_direction.py <博主名> --limit 30          # 冒烟：只处理前 30 条
  python scripts/pipeline/extract_signals_direction.py <博主名> --out /tmp/x.json   # 写指定路径（不动正式数据）
  python scripts/pipeline/extract_signals_direction.py <博主名> --runs 3            # 3 次运行共识合并（聚合更稳，推荐）
  python scripts/pipeline/extract_signals_direction.py <博主名> --no-verify         # 跳过自查（更快）
  python scripts/pipeline/extract_signals_direction.py <博主名> --dry-run           # 不调 API、不写文件

稳定性：DeepSeek 同 prompt 下每次运行有少量批间随机差异（信号数 ±3 条量级）。
--runs N 会完整跑 N 次（提取+自查），只保留 ≥(N//2+1) 次运行都出现的信号，
直接压掉单次噪声，保证聚合结论稳定。运行元数据写入 data/direction_signals/_<名>_run.json
（以 _ 开头，已被 .gitignore 忽略，不会提交）。
"""

import argparse
import glob
import json
import os
import re
import sys
import threading
import time
from collections import Counter

from openai import OpenAI

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))

# ── Configuration ──
MODEL = "deepseek-v4-flash"
BASE_URL = "https://api.deepseek.com"
DEFAULT_BATCH_SIZE = 15
VERIFY_BATCH_SIZE = 10  # 自查批次：每批审几帖
MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds
# 硬性 wall-clock 超时（秒）。DeepSeek 服务端偶尔会拖拽连接/维持连接不响应，
# OpenAI SDK 的 timeout 只对"完全无数据"生效——服务端持续发字节会重置读超时→无限挂起
# （2026-08-31 连挂 4 位博主：红红火火的老牛哥/纽约音乐厨房/老简说交易/股傲）。
# 用守护线程强制放弃：单次调用绝不超 API_CALL_DEADLINE，3 次重试最坏 ~9 分钟/批。
API_CALL_DEADLINE = 180

# 只提取 2026-01-01 及之后发布的信号
SIGNAL_START = "2026-01-01"

# ── Direction schema 合法集（引擎 run_direction.py 直接读取，非法值会导致崩溃）──
VALID_IDX = {"上证指数", "上证50", "沪深300", "中证500", "中证1000", "创业板指", "科创50", "双创"}
VALID_CAT = {"scored", "unscored"}
SPEC_RE = re.compile(r"^(today|week|nweek|nweek_first|month|nmonth|long|t\d+|d:\d{4}-\d{2}-\d{2})$")


# ── System Prompt（SKILL.md §1~§8 规则精简版，Direction schema）──
SYSTEM_PROMPT = """你是财经内容分析助手。你的任务是阅读今日头条财经博主的帖子，判断每条帖子是否包含对上证综指/大盘的**明确方向预测**，并按给定规则逐条提取方向信号。

帖子以「标题：…\\n正文：…」并列呈现——**标题与正文同等重要**：标题常直接给出预测结论（"明天看涨""站稳4130就能到4250"），勿因正文冗长而漏读标题；疑问句/引流话术式标题（"明天A股会怎么走？"）不代表预测，去正文找结论。微帖（标题即正文）只显示一次。

## 提取为信号的标准（可打分性）
必须满足：**明确的态度**（看涨/看跌、收阳/收阴、涨/跌）。有明确预测周期最好；**没有明确预测周期但有明确方向，也提取**（标 spec=t5，验证终点=信号日之后第 5 个交易日，正常计分）。
- ❌ 仅描述形态而无方向态度：震荡、筑底、洗盘、蓄势、拉锯、考验X点支撑、探底回升、冲高回落 → 不提取
- ⚠️ "震荡"本身不代表明确方向；"震荡下跌/震荡上涨"才表示有明确方向（震荡上涨→看多 d=1、震荡下跌→看空 d=-1）
- ❌ 仓位自述（"还剩4成仓""满仓持股"）、行情回顾、投资理念、新闻评论 → 不提取
- ❌ **模棱两可/方向不明**（"可能涨也可能跌""不好说""边走边看""看市场情绪""不确定"）→ 整条忽略不提取（无方向可打，连单列都不记）
- ❌ **纯复盘/对过去的分析**（回顾行情、评价已发生走势、事后总结）→ 忽略不提取（不是对未来的预测；同一帖内既有复盘又有未来预测则只提取未来预测部分）
- ❌ **状态描述**（"已经进入调整阶段""顶背离已经出现""目前处于上升趋势"）→ 是对现状的描述而非对未来的预测 → 不提取
- ❌ 预测对象是不可映射板块（有色/钢铁/医药/创新药/恒科/半导体/电池/房地产/航天/军工等）→ 忽略不提取

## 默认规则与板块→指数映射
帖子未写明任何指数或板块名称 → 默认判断上证综指，idx=上证指数。
| 帖中提到 | idx |
| 创业板/创业板指/创业板ETF | 创业板指 |
| 科创/科创50/科创板 | 科创50 |
| 小盘/成长/中证1000 | 中证1000 |
| 中证500 | 中证500 |
| 上证50/50ETF/老登/大金融/银行/保险/券商/证券/白酒/酒 | 上证50 |
| 沪深300 | 沪深300 |
| 上证综指/上证/综指/大盘 | 上证指数 |
| 双创 | 双创（创业板指+科创50各半） |

## 预测周期 → spec 编码
- "今天/今日/下午/午后/尾盘" → spec=today，cat=scored（**无论何时发布**：盘前/盘中/盘后/非交易日都一样提取；是否过时由打分引擎自动判定，提取阶段不判无效）
- "明天/明日/次日" → t1；"后天" → t2；"N天后/N日内" → tN（如 t3、t10）
- "X-Y天"（如1-2天、3-5天）→ 取窗口最后一天 tY（1-2天→t2，3-5天→t5）
- "未来几天" → t3；"近期/短期/很快/马上/即将/不久/临近"（向前展望）→ t5
- "本周/本周内" → week；"下周" → nweek；"下周一" → nweek_first
- "月底前/月末前" → month；"下个月/下月" → nmonth
- "X月X日"（未来具体日期）→ d:YYYY-MM-DD
- **无明确预测周期但有明确方向**（"大趋势向上""随时会涨""肯定要涨但不知道什么时候"）→ spec=t5，cat=scored（验证终点=信号日之后第 5 个交易日，正常计分）
- **有明确预测点位 + 预测周期** → 视作有明确方向预测：预期点位高于参考价=看涨（d=1）、低于参考价=看跌（d=-1），**按周期计分**（例："今天下午能站上4000"→today、看涨；"下周要回踩3800"→nweek、看跌）

## 长期/不计分（spec=long，cat=unscored）
以下方向明确的预测**不计分**（cat=unscored，spec=long，不填 s）：
- **目标点位无时间承诺**（只给点位、不给时间）："目标是4260点""看到X点""还有X点空间""背驰点在4423" → unscored；**给定了时间的点位预测**（"明天站上4100""下周到4000"）→ **scored 按周期计分**，见上
- **年度预测**："下半年""全年""今年""2026年"
- **中长期/远期**："中长期""未来几个月""三年""长线""长期来看"

## 信号字段
- post_n：输入批次中帖子的编号（从 0 开始），必须对应
- d：1=看多，-1=看空
- s：2=strong（"一定""必将""满仓""清仓""坚决""毫无疑问"）；1=moderate（"偏多""倾向于""大概率""有望"）；仅 cat=scored 时填
- idx：上面映射出的指数 key（默认"上证指数"）
- spec：上面的周期编码；cat=scored 时必填，cat=unscored 时恒为 "long"
- summary：≤50字，预测关键句概括
- cat：scored（参与计分）/ unscored（不计分，单列）
  - **禁止写"待验证""无效-过时""无效-日内""无预测周期""目标点位"**（引擎自动判定这些，提取阶段只写 scored/unscored）
- 一条帖子可对应多条信号：不同周期**或不同方向**的明确预测各自成条（如"短期看空+长期看多"=2 条：短期看空→t5、d=-1、scored；长期看多→long、d=1、unscored）；同一周期重复表述只记一条

## 输出格式
只返回一个 JSON 对象，无任何其他文字：
{"signals": [{"post_n": 0, "d": 1, "s": 1, "idx": "上证指数", "spec": "t1", "summary": "明天看涨", "cat": "scored"}, {"post_n": 0, "d": 1, "idx": "上证指数", "spec": "long", "summary": "今年看涨到4250", "cat": "unscored"}, ...]}
- 没有信号的帖子不用出现在 signals 里
- cat=scored 时 spec、s 必填；cat=unscored 时 spec=long、不填 s
- 只返回 JSON，不要有任何其他文字"""

# ── 自查 System Prompt（对「已提取信号+原文」做独立审查）──
VERIFY_SYSTEM_PROMPT = """你是信号审查助手。你会收到「已提取信号 + 其原文帖子」，请审查信号是否被原文**明确支持**，并修正或补加。

帖子以「标题：…\\n正文：…」并列呈现，标题与正文同等重要——标题里的明确预测结论（"明天看涨"等）同样要审查、不可漏。

## 第一步：判定主结论句
先找到帖子的**主结论句**——形如「周三：大盘探底回升，我看涨」「明天：只卖不买」「下周一我看跌」等**带明确方向**的句子。主结论句是全帖最高优先级的预测：**任何条件句、风险提示、走势分类、点位预演都不能替代或覆盖主结论句**。若提取信号与主结论句方向/周期不一致 → 必须 action=fix，改为主结论句的方向/周期。

## 其他标准
1. **周期支持**：spec 必须对应原文**明确出现**的周期词。**原文无明确周期但有明确方向**（"随时""大趋势向上""肯定涨但不知道什么时候""上涨没结束"等结构/无时限表述）→ 不能给 long 单列，fix 为 spec=t5、cat=scored（验证终点=信号日之后第 5 个交易日，正常计分）。**目标点位无时间承诺**（"目标是X点""背驰点在4423"）→ fix 为 spec=long、cat=unscored（不填 s）。**有明确点位 + 明确周期**（"明天站上4100""下周回踩3800"）→ fix 为 scored 按周期计分，点位高于参考价=看涨 d=1、低于=看跌 d=-1。**年度预测**（"今年/下半年/全年/2026"）与**中长期/远期**（"未来几个月""三年""长期来看"）→ fix 为 spec=long、cat=unscored。
2. **可打分性**：只有形态描述（震荡/筑底/洗盘/冲高回落/支撑位）而无明确方向态度 → drop（"震荡下跌/震荡上涨"除外，属明确方向）；**模棱两可/方向不明（"可能涨也可能跌""边走边看""不好说"）→ drop（连单列都不记）；纯复盘/对过去的分析（回顾行情、评价已发生走势、事后总结）→ drop；状态描述（"已经进入调整阶段""顶背离已出现"）→ drop**；仓位自述 → drop。
3. **盘中/盘前"今天"**："今天/今日/下午/午后/尾盘"预测 → spec=today、cat=scored（**无论盘前/盘中/盘后/非交易日发布都保持**，是否过时由打分引擎自动判定，审查阶段不做无效判断）。
4. **补加**：原文有比已提取信号**更明确或被漏掉**的预测结论 → 放入 add。典型遗漏：① 同一帖子里还有**第二个观点**（不同方向或不同周期，如"短期看空、长期看多"只提取了短期看空 → add 长期看多那条：long/unscored）；② 应属 t5（无周期有方向）或 long/unscored 而未被提取。
5. **保留**：信号方向与周期都有原文支持、且就是主结论句 → action=keep。

## 输出 JSON（只输出 JSON，无其他文字）
**必须对「已提取信号」的每一条都给出 verdict（keep/fix/drop），不得遗漏任何一条**——漏掉任何一条都会被当作未评审而按原样保留；只有你明确 drop 的信号才会被删除。
{"verdicts": [{"vidx": <帖子编号>, "sig": <该帖已提取信号下标，0基>, "action": "keep"|"fix"|"drop", "d":1|-1, "s":1|2, "spec":"...", "cat":"...", "summary":"≤50字", "reason":"一句话"}],
 "add": [{"vidx": <帖子编号>, "d":1|-1, "s":1|2, "spec":"...", "cat":"...", "summary":"≤50字"}]}
- action=keep：只输出 vidx/sig/action/reason
- action=fix：输出修正后的完整字段（d/s/spec/cat/summary）
- action=drop：只输出 vidx/sig/action/reason
- cat=scored 时 spec、s 必填；cat=unscored 时 spec=long、不填 s
- 无修正 → verdicts 为空数组；无补加 → add 为空数组；都无 → {"verdicts":[],"add":[]}"""


def load_posts_and_bodies(blogger):
    """读 data/posts/<名>.json 的帖子 + 合并 <名>_bodies_s*.json 正文，过滤 2026+。"""
    posts_path = os.path.join(PROJECT_ROOT, "data", "posts", f"{blogger}.json")
    if not os.path.exists(posts_path):
        print(f"ERROR: Posts file not found: {posts_path}")
        return None, None, None, None

    data = json.load(open(posts_path, encoding="utf-8"))
    all_posts = data.get("posts", [])

    bodies = {}
    for fp in sorted(glob.glob(os.path.join(PROJECT_ROOT, "data", "posts", f"{blogger}_bodies_s*.json"))):
        try:
            bodies.update(json.load(open(fp, encoding="utf-8")))
        except Exception as e:
            print(f"WARN: 读取正文文件失败跳过 {os.path.basename(fp)}: {e}")

    eval_posts, pre_count = [], 0
    for p in all_posts:
        pd = (p.get("publish_date") or "").strip()
        if not pd:
            continue
        if pd[:10] < SIGNAL_START:
            pre_count += 1
        else:
            eval_posts.append(p)
    return all_posts, eval_posts, pre_count, bodies


def post_text(post, bodies):
    """标题 + 正文同等重要：有正文时两者并列呈现（标题常直接含预测结论，勿丢弃）。

    优先级：
    - 标题：bodies.title → post.title（合并后保留的标题字段）→ 无正文时的原 content
    - 正文：bodies.body（仅排除登录墙占位）→ 已合并的 post.content
    微帖（正文即标题，一句话预测）只显示一次，避免重复。
    """
    pid = str(post.get("post_id", ""))
    b = bodies.get(pid, {}) if isinstance(bodies, dict) else {}
    title = b.get("title", "") if isinstance(b, dict) else ""
    body = b.get("body", "") if isinstance(b, dict) else ""
    if body and "登录" in body and "验证码" in body:
        body = ""  # 登录墙占位（手机登录/扫码登录/获取验证码），非真实正文
    content = post.get("content", "") or ""
    post_title = post.get("title", "") or ""
    t = (title or post_title).strip()
    if body:
        body = body.strip()
        if t and t != body:
            return _truncate(f"标题：{t}\n正文：{body}")
        return _truncate(body)  # 微帖：标题即正文，只显示一次
    # 无正文（bodies 未抓取 / 已归档 / 登录墙占位）
    if t and t != content.strip():
        return _truncate(f"标题：{t}\n正文：{content.strip()}")
    return _truncate(content)


def _truncate(text, limit=500):
    if len(text) > limit:
        return text[:300] + "\n...[省略中间内容]...\n" + text[-250:]
    return text


def build_batches(posts, batch_size):
    for i in range(0, len(posts), batch_size):
        yield posts[i:i + batch_size]


def format_post_for_prompt(post, index, bodies):
    pd = post.get("publish_date", "?")
    return f"[Post #{index}] {pd}\n{post_text(post, bodies)}\n"


def _call_with_deadline(fn, deadline, label):
    """守护线程执行 fn，硬性 wall-clock 超时——服务端拖拽连接也强制放弃，绝不无限挂起。"""
    box = {}

    def runner():
        try:
            box["value"] = fn()
        except BaseException as e:  # noqa: BLE001 —— 守护线程里任何异常都应装盒上报，不能杀主流程
            box["error"] = e

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    t.join(deadline)
    if t.is_alive():
        raise TimeoutError(f"{label}: 超过 {deadline}s 硬性超时，放弃本次调用")
    if "error" in box:
        raise box["error"]
    return box["value"]


def call_json(client, system_prompt, user_message, label, thinking=False):
    """调 DeepSeek，返回 (parsed_dict, raw) 或 (None, None)。

    thinking=False：关推理（提取阶段，省 token）；thinking=True：开推理（自查阶段，更仔细）。

    每次 attempt 用全新 client + 硬性 wall-clock 超时：
    服务端偶尔拖拽连接不响应，SDK timeout 不生效（读超时被字节流重置），必须守护线程硬切，
    否则单次调用可挂死整轮（2026-08-31 曾连挂 4 位博主）。新 client 避免复用被拖拽的毒连接。
    """
    for attempt in range(MAX_RETRIES):
        c = OpenAI(api_key=os.environ["DEEPSEEK_API_KEY"], base_url=BASE_URL, timeout=API_CALL_DEADLINE)
        try:
            response = _call_with_deadline(
                lambda c=c: c.chat.completions.create(
                    model=MODEL,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    temperature=0,
                    max_tokens=8192,
                    extra_body={"thinking": {"type": "enabled" if thinking else "disabled"}},
                ),
                API_CALL_DEADLINE,
                f"{label} attempt {attempt + 1}",
            )
            raw = response.choices[0].message.content
            result = parse_response(raw)
            if result is not None:
                return result, raw
            print(f"  {label} attempt {attempt + 1}: JSON parse failed, retrying...")
            time.sleep(RETRY_DELAY * (attempt + 1))
        except Exception as e:
            print(f"  {label} attempt {attempt + 1}: API error: {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY * (attempt + 1))
            else:
                raise
    return None, None


def parse_response(raw):
    """解析 LLM 返回的 JSON（任意顶层 dict），处理 markdown 代码块与截断 JSON。"""
    if not raw:
        return None

    text = raw.strip()

    # 去 markdown 代码块
    if text.startswith("```"):
        nl = text.find("\n")
        if nl >= 0:
            text = text[nl + 1:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    # 直接解析
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    # 括号配对扫描：找最外层对象
    for m in re.finditer(r"\{", text):
        start = m.start()
        depth, end = 0, -1
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end > start:
            try:
                data = json.loads(text[start:end])
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                continue

    # 兜底：补全未闭合括号
    open_braces = text.count("{") - text.count("}")
    open_brackets = text.count("[") - text.count("]")
    if open_braces > 0 or open_brackets > 0:
        try:
            data = json.loads(text + "]" * open_brackets + "}" * open_braces)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    return None


def normalize_signal(row, post):
    """把一条候选信号行归一化为 Direction schema。返回 (sig, None) 或 (None, 原因)。"""
    pub = (post.get("publish_date") or "").strip()
    if not pub or pub[:10] < SIGNAL_START:
        return None, "非 2026"

    d = row.get("d")
    if isinstance(d, str):
        try:
            d = int(d)
        except ValueError:
            d = None
    if d not in (1, -1):
        return None, "d 非法"

    cat = row.get("cat") or "scored"
    if cat not in VALID_CAT:
        return None, f"cat 非法: {cat}"

    idx = row.get("idx") or "上证指数"
    if idx in ("上证综指", "上证", "综指"):
        idx = "上证指数"
    if idx not in VALID_IDX:
        return None, f"idx 非法: {idx}"

    summary = (row.get("summary") or "").strip()
    if not summary:
        return None, "summary 缺失"
    summary = summary[:50]

    sig = {"pub": pub, "d": d, "idx": idx, "summary": summary}
    if cat == "scored":
        spec = str(row.get("spec") or "")
        if spec == "long":
            # spec=long 恒不计分（SKILL §3：目标点位/年度/中长期）→ 强制转 unscored
            sig["spec"] = "long"
            sig["cat"] = "unscored"
            return sig, None
        if not SPEC_RE.match(spec):
            return None, f"spec 非法/缺失: {spec or '(空)'}"
        s = row.get("s", 1)
        try:
            s = int(s)
        except (TypeError, ValueError):
            s = 1
        if s not in (1, 2):
            s = 1
        sig["s"] = s
        sig["spec"] = spec
        sig["cat"] = "scored"
    else:
        # unscored：spec 统一归一为 long（供报告展示"长期/不计分"）
        sig["spec"] = "long"
        sig["cat"] = "unscored"
    return sig, None


def validate_signals(raw_signals, batch_posts):
    """把 DeepSeek 输出映射/校验成 Direction schema。返回 (ok_signals, dropped_counter, posts_aligned)。"""
    ok, dropped, posts_aligned = [], Counter(), []
    for r in raw_signals:
        if not isinstance(r, dict):
            dropped["非对象"] += 1
            continue
        try:
            post_n = int(r.get("post_n", -1))
        except (TypeError, ValueError):
            post_n = -1
        if not (0 <= post_n < len(batch_posts)):
            dropped["post_n 越界"] += 1
            continue
        post = batch_posts[post_n]
        sig, reason = normalize_signal(r, post)
        if sig is None:
            dropped[reason] += 1
            continue
        ok.append(sig)
        posts_aligned.append(post)
    return ok, dropped, posts_aligned


def verify_signals(client, signals, posts, bodies, blogger):
    """自查：把「已提取信号 + 原文」回喂 DeepSeek 审查，返回 (final_signals, final_posts, stats)。"""
    # 按帖子分组合并（同一帖子的多条信号一起审）
    groups, seen = [], {}
    for sig, post in zip(signals, posts):
        gi = seen.get(id(post))
        if gi is None:
            gi = len(groups)
            seen[id(post)] = gi
            groups.append({"post": post, "signals": []})
        groups[gi]["signals"].append(sig)

    final_signals, final_posts = [], []
    stats = Counter()
    total_groups = len(groups)

    batch_lo = 0
    for start in range(0, total_groups, VERIFY_BATCH_SIZE):
        grp = groups[start:start + VERIFY_BATCH_SIZE]
        batch_hi = start + len(grp)
        lines = []
        for i, g in enumerate(grp):
            vidx = start + i
            sigs = [{"d": s["d"], "s": s.get("s"), "spec": s.get("spec"),
                     "cat": s.get("cat", "scored"), "summary": s["summary"]} for s in g["signals"]]
            lines.append(f"[Post #{vidx}] {g['post'].get('publish_date', '?')}\n"
                         f"{post_text(g['post'], bodies)}\n"
                         f"已提取信号: {json.dumps(sigs, ensure_ascii=False)}\n")
        label = f"Verify {start // VERIFY_BATCH_SIZE + 1}/{(total_groups - 1) // VERIFY_BATCH_SIZE + 1}"
        print(f"  [{label}] 审查 {len(grp)} 帖...", end=" ", flush=True)
        user_message = (f"请审查以下 {len(grp)} 帖子的已提取信号是否被原文支持，并按规则修正（vidx 对应帖子编号，仅限本批次 {start}~{batch_hi - 1}）。\n\n"
                        + "\n".join(lines))
        try:
            result, _ = call_json(client, VERIFY_SYSTEM_PROMPT, user_message, label, thinking=False)
        except Exception as e:
            # 单批 API 拖死（3 次重试后仍超时）→ 保留该批原信号，不拖垮整位博主（2026-08-31 加固）
            print(f"FAILED({e}), 保留原信号")
            stats["verify 批次失败"] += 1
            for g in grp:
                for s in g["signals"]:
                    final_signals.append(s)
                    final_posts.append(g["post"])
            continue
        if result is None:
            print("FAILED, 保留原信号")
            stats["verify 批次失败"] += 1
            for g in grp:
                for s in g["signals"]:
                    final_signals.append(s)
                    final_posts.append(g["post"])
            continue

        # 处理 verdicts（vidx 必须落在本批次范围内，防跨批错配/重复）
        verdicts = result.get("verdicts") or []
        judged = set()  # (vidx, sigi) 已被明确评审（keep/fix/drop）
        for v in verdicts:
            vidx = v.get("vidx")
            sigi = v.get("sig")
            if not (isinstance(vidx, int) and batch_lo <= vidx < batch_hi):
                stats["vidx 越界"] += 1
                continue
            g = groups[vidx]
            if not (isinstance(sigi, int) and 0 <= sigi < len(g["signals"])):
                stats["sig 越界"] += 1
                continue
            judged.add((vidx, sigi))
            action = v.get("action")
            if action == "keep":
                stats["keep"] += 1
                final_signals.append(g["signals"][sigi])
                final_posts.append(g["post"])
            elif action == "drop":
                stats["drop"] += 1
            elif action == "fix":
                sig, reason = normalize_signal(v, g["post"])
                if sig is None:
                    stats[f"fix 非法({reason})"] += 1
                    final_signals.append(g["signals"][sigi])
                    final_posts.append(g["post"])
                else:
                    stats["fix"] += 1
                    final_signals.append(sig)
                    final_posts.append(g["post"])
            else:
                stats[f"action 非法: {action}"] += 1
                final_signals.append(g["signals"][sigi])
                final_posts.append(g["post"])

        # 处理补加（vidx 同样限制在本批次）
        adds = result.get("add") or []
        for a in adds:
            vidx = a.get("vidx")
            if not (isinstance(vidx, int) and batch_lo <= vidx < batch_hi):
                stats["add vidx 越界"] += 1
                continue
            g = groups[vidx]
            sig, reason = normalize_signal(a, g["post"])
            if sig is None:
                stats[f"add 非法({reason})"] += 1
                continue
            stats["add"] += 1
            final_signals.append(sig)
            final_posts.append(g["post"])

        # 默认保留：未被任何 verdict 覆盖的信号。verify 只负责「显式 drop 掉的误报」，
        # 未提及 ≠ 判为误报——静默丢弃会系统性丢失真实信号（鸟瞰股市 144→83 的根因）。
        for i, g in enumerate(grp):
            vidx = start + i
            for j, s in enumerate(g["signals"]):
                if (vidx, j) not in judged:
                    stats["未评默认保留"] += 1
                    final_signals.append(s)
                    final_posts.append(g["post"])

        batch_lo = batch_hi
        print(f"✓ keep={stats['keep']} fix={stats['fix']} drop={stats['drop']} add={stats['add']} "
              f"未评={stats['未评默认保留']} 越界={stats['vidx 越界'] + stats['sig 越界']}")

    return final_signals, final_posts, stats


def dedup_and_sort(signals):
    """去重：同日同周期同方向同指数→1 条（保留当天最晚发布）。unscored 统一用字面量作去重键。"""
    seen = {}
    for s in signals:
        date = s["pub"][:10]
        key = (date, s.get("spec") if s["cat"] == "scored" else "unscored", s["d"], s["idx"])
        cur = seen.get(key)
        if cur is None or s["pub"] > cur["pub"]:
            seen[key] = s
    return sorted(seen.values(), key=lambda s: s["pub"])


def consensus_key(sig):
    """多次运行共识的信号键：scored 用 (pub, spec, d, idx)，unscored 用 (pub, "unscored", d, idx)。
    含 idx：同日同周期同方向的预测若针对不同指数（如上证明天涨 + 创业板明天涨）是两条独立信号，不得合并。"""
    if sig["cat"] == "scored":
        return (sig["pub"], "scored", sig["spec"], sig["d"], sig["idx"])
    return (sig["pub"], "unscored", None, sig["d"], sig["idx"])


def consensus_merge(runs_signals, min_votes):
    """合并多次运行结果：只保留出现次数 ≥ min_votes 的信号；summary 取出现最多次的。"""
    votes = {}
    for sig in runs_signals:
        k = consensus_key(sig)
        d = votes.setdefault(k, {"n": 0, "summaries": Counter(), "template": sig})
        d["n"] += 1
        d["summaries"][sig["summary"]] += 1
    merged = []
    for k, d in votes.items():
        if d["n"] < min_votes:
            continue
        sig = dict(d["template"])
        sig["summary"] = d["summaries"].most_common(1)[0][0]
        merged.append(sig)
    return dedup_and_sort(merged)


def extract_once(client, blogger, eval_posts, bodies, batch_size, no_verify, run_label=""):
    """单次完整运行：分批提取 + 自查。返回 (signals, verify_stats, failed_batches, elapsed)。"""
    all_signals, all_posts_ref, all_dropped = [], [], Counter()
    start_time = time.time()
    failed_batches = 0
    batches = list(build_batches(eval_posts, batch_size))
    for batch_num, batch_posts in enumerate(batches, 1):
        print(f"  {run_label}[Batch {batch_num}/{len(batches)}] {len(batch_posts)} 条...", end=" ", flush=True)
        try:
            lines = [format_post_for_prompt(p, i, bodies) for i, p in enumerate(batch_posts)]
            user_message = (f"以下是博主「{blogger}」的 {len(batch_posts)} 条帖子。请逐条分析，"
                            f"判断是否包含对上证/大盘的明确方向预测，并返回标注 JSON。\n\n" + "\n".join(lines))
            result, _ = call_json(client, SYSTEM_PROMPT, user_message,
                                  f"{run_label}Batch {batch_num}/{len(batches)}", thinking=False)
            if result is None:
                failed_batches += 1
                print("FAILED after %d retries, skipping batch" % MAX_RETRIES)
                continue
            ok, dropped, posts_aligned = validate_signals(result.get("signals", []), batch_posts)
            all_signals.extend(ok)
            all_posts_ref.extend(posts_aligned)
            all_dropped.update(dropped)
            print(f"✓ {len(ok)} 信号 | 累计 {len(all_signals)}")
        except Exception as e:
            failed_batches += 1
            print(f"ERROR: {e}")
            continue
        if batch_num < len(batches):
            time.sleep(0.5)

    extract_elapsed = time.time() - start_time
    verify_stats = None
    if not no_verify and all_signals:
        print(f"  {run_label}[自查] 回喂 {len(all_signals)} 条信号...", flush=True)
        t0 = time.time()
        all_signals, all_posts_ref, verify_stats = verify_signals(client, all_signals, all_posts_ref, bodies, blogger)
        print(f"  {run_label}[自查] 完成，耗时 {time.time() - t0:.0f}s")
    return all_signals, verify_stats, failed_batches, extract_elapsed


def main():
    parser = argparse.ArgumentParser(description="DeepSeek 自动提取 Direction 方向信号")
    parser.add_argument("blogger", help="博主名（data/posts/<名>.json）")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="每批帖子数")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 条帖子（冒烟测试）")
    parser.add_argument("--out", default="", help="输出文件路径（默认 data/direction_signals/<名>.json）")
    parser.add_argument("--runs", type=int, default=1, help="完整运行次数（≥2 时按多数共识合并，推荐 3）")
    parser.add_argument("--no-verify", action="store_true", help="跳过信号自查")
    parser.add_argument("--dry-run", action="store_true", help="不调 API、不写文件")
    args = parser.parse_args()

    blogger = args.blogger
    all_posts, eval_posts, pre_count, bodies = load_posts_and_bodies(blogger)
    if all_posts is None:
        sys.exit(1)
    if args.runs < 1:
        print("ERROR: --runs 至少为 1")
        sys.exit(1)

    if args.limit > 0:
        eval_posts = eval_posts[:args.limit]

    total_eval = len(eval_posts)
    total_batches = (total_eval + args.batch_size - 1) // args.batch_size
    runs_count = args.runs if args.limit == 0 else 1  # 冒烟(--limit)只跑一次
    min_votes = (runs_count // 2) + 1 if runs_count > 1 else 1

    print("=" * 60)
    print(f"Direction 信号提取（DeepSeek {MODEL}）：{blogger}")
    print("=" * 60)
    print(f"帖子总数: {len(all_posts)} | 2026 前剔除: {pre_count} | 参与提取: {total_eval}")
    print(f"批次: {total_batches}（batch-size={args.batch_size}）| 自查: {'开' if not args.no_verify else '关'} | 运行: {runs_count} 次")

    if args.dry_run:
        print(f"\n[DRY RUN] 将向 DeepSeek 发送 {total_batches} 批帖子 × {args.runs} 次（不调 API、不写文件）")
        return

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print("ERROR: DEEPSEEK_API_KEY 环境变量未设置（只经环境变量传入，绝不写入文件）")
        print("  export DEEPSEEK_API_KEY='sk-...'")
        sys.exit(1)

    client = OpenAI(api_key=api_key, base_url=BASE_URL, timeout=120.0)  # 120s 超时：防止 DeepSeek API 调用挂死整轮提取（2026-08-31 红红火火的老牛哥曾卡 99 分钟）

    start_time = time.time()
    all_candidates, all_verify, total_failed = [], [], 0

    print(f"\n[提取] {runs_count} 次运行（共识阈值 ≥{min_votes}/次）..." if runs_count > 1 else "\n[提取] 单次运行...")
    for r in range(1, runs_count + 1):
        if runs_count > 1:
            print(f"\n── 运行 {r}/{runs_count} ──")
        signals, vstats, failed, ee = extract_once(
            client, blogger, eval_posts, bodies, args.batch_size, args.no_verify,
            run_label=f"[Run {r}] " if runs_count > 1 else "")
        all_candidates.extend(signals)
        if vstats:
            all_verify.append(vstats)
        total_failed += failed
        if runs_count > 1:
            print(f"  Run {r} 完成: {len(signals)} 条（提取 {ee:.0f}s）")

    if runs_count > 1:
        print(f"\n[共识] {len(all_candidates)} 条候选 → 保留 ≥{min_votes} 次运行都出现的信号...")
        signals = consensus_merge(all_candidates, min_votes)
    else:
        signals = dedup_and_sort(all_candidates)

    cat_counts = Counter(s["cat"] for s in signals)
    elapsed = time.time() - start_time
    print(f"\n{'=' * 60}")
    print(f"✅ 提取完成: {blogger} | 总耗时 {elapsed:.0f}s | 失败批次 {total_failed}")
    print(f"参与提取: {total_eval} | 运行 {runs_count} 次 | 提取信号: {len(signals)}")
    print(f"  按 cat: {dict(cat_counts)}")
    if all_verify:
        k = sum(v.get("keep", 0) for v in all_verify)
        f_ = sum(v.get("fix", 0) for v in all_verify)
        d_ = sum(v.get("drop", 0) for v in all_verify)
        a = sum(v.get("add", 0) for v in all_verify)
        print(f"  自查合计(跨运行): keep={k} fix={f_} drop={d_} add={a}")

    partial = args.limit > 0
    if partial and not args.out:
        print("\n[部分运行] 仅处理前 %d 条，未写正式文件（加 --out 可写指定路径）" % args.limit)
        return

    out_path = args.out or os.path.join(PROJECT_ROOT, "data", "direction_signals", f"{blogger}.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"blogger": blogger, "signals": signals}, f, ensure_ascii=False, indent=2)
    print(f"输出: {out_path}（{len(signals)} 条信号）")

    # ── 可复现性记录（gitignored，仅本地溯源）──
    try:
        meta_path = os.path.join(os.path.dirname(out_path), f"_{blogger}_run.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump({
                "blogger": blogger,
                "model": MODEL,
                "runs": runs_count,
                "min_votes": min_votes,
                "batch_size": args.batch_size,
                "verify": not args.no_verify,
                "posts_total": len(all_posts),
                "posts_eval": total_eval,
                "signals": len(signals),
                "cat": dict(cat_counts),
                "failed_batches": total_failed,
                "extracted_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }, f, ensure_ascii=False, indent=2)
        print(f"运行记录: {meta_path}（gitignored，仅本地溯源）")
    except Exception as e:
        print(f"WARN: 写运行记录失败: {e}")


if __name__ == "__main__":
    main()
