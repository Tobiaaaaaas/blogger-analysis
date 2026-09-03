# -*- coding: utf-8 -*-
"""简报编排器（v11：超短板块 + 波段板块 双固定名单 · 交易日三推）。

流程：抓新帖 → 行情 → 按板块 LLM 行抽取（超短近3交易日 / 波段近7自然日）→
卡尾跨板块收敛总结 → 飞书推送。
- 超短板块（17 人）：只认 今天/明天(0-1日) 方向观点，回看近 SHORT_WINDOW_TRADING_DAYS 个交易日。
- 波段板块（21 人）：只认 近日/本周/下周/更长(2日+) 方向观点，回看近 SWING_WINDOW_CAL_DAYS 个自然日。
- 两板块前 8 位"双板块博主"重复上榜：读帖/抽取各按所属板块窗口与口径独立执行一次，两块可各自成行。
- 板块内只显示窗口内有该周期方向观点的博主；未表态者不点名不计数。
展示窗口 = 现读合并主文件 data/posts（按发帖时间过滤，不依赖上次推送）；
抓取窗口 = 增量 since state.last_run（只补主文件里还没有的新帖）。
每档推送前先抓新帖；每档对窗口内帖子做全量 LLM 重读（不按 post_id 缓存引文）。
只发一张双板块 roster 卡；旧 v8 共识卡 / 心跳已移出热路径（代码仍留作 LEGACY）。

用法：
  python -m briefing.scripts.run_briefing --push                       # cron 真实推送（自动判定时段）
  python -m briefing.scripts.run_briefing --dry-run --slot morning --no-scrape   # 复用已抓数据试跑
  python -m briefing.scripts.run_briefing --dry-run --slot morning --skip-calendar   # 非交易日强制试跑
  python -m briefing.scripts.run_briefing --push --slot late --max-bloggers 3      # 冒烟

调度（交易日 3 推；非交易日脚本自行 exit 0，weekday 宽松即可，节假日/调休由日历精确判定）：
  30 9 * * 1-5   morning  早盘 09:30
  0 13 * * 1-5   afternoon  午后 13:00
  30 14 * * 1-5  late      尾盘 14:30
  Windows：Task Scheduler 建 3 个每日任务透传 --slot（见 DEPLOY.md）。
"""
import argparse
import atexit
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone

from . import calendar, config, market, paths, render, scrape_merge, state
from .summarize import board_counts, extract_board_rows, summarize_boards

log = logging.getLogger("briefing")

WD = ["一", "二", "三", "四", "五", "六", "日"]
BEIJING_TZ = timezone(timedelta(hours=8))  # 时段/日期/窗口全用北京时，独立于宿主机时区


def _setup_logging():
    paths.ensure_dirs()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(paths.LOG_FILE, encoding="utf-8")],
    )


def _date_str(now: datetime) -> str:
    return f"{now.strftime('%m-%d')} 周{WD[now.weekday()]}"


def _resolve_slot(args, now):
    trading = True if args.skip_calendar else calendar.is_trading_day(now.date())
    if args.slot:
        key = args.slot.strip().lower()  # 大小写容错：Task Scheduler / bat 传 Morning 也能命中
        if key not in config.SLOTS:
            raise SystemExit(f"未知 slot: {args.slot}，可选 {list(config.SLOTS)}")
        if not trading:
            log.info("非交易日，跳过槽 %s", key)
            raise SystemExit(0)
        return key
    key = config.slot_for(now, trading)
    if key is None:
        log.info("当前时刻（%s，%s）不在推送时段，跳过",
                 now, "交易日" if trading else "非交易日")
        raise SystemExit(0)
    return key


def _beijing_midnight_epoch(d) -> int:
    """某日期（北京时）00:00 的 epoch 秒。作展示窗口下界：下界当日 00:00 ≤ 帖子发帖时刻。"""
    return int(datetime(d.year, d.month, d.day, tzinfo=BEIJING_TZ).timestamp())


def _read_window_posts(blogger, start_ts, now_ts):
    """现读合并主文件，返回该博主窗口内 [start_ts, now_ts] 的可读帖（新→旧）。

    过滤 [视频帖]/无正文短帖（与 extract_board_rows 口径一致），每博主只取最新
    ROWS_MAX_POSTS 条喂 LLM——行抽取只依赖这同一个列表，下标即引文来源。
    """
    fp = os.path.join(paths.POSTS_DIR, f"{blogger}.json")
    if not os.path.exists(fp):
        return []
    try:
        posts = json.load(open(fp, encoding="utf-8")).get("posts") or []
    except Exception:
        return []
    win = []
    for p in posts:
        ts = p.get("publish_time") or 0
        if start_ts <= ts <= now_ts:
            content = (p.get("content") or "").strip()
            if content == "[视频帖]" or len(content) < 5:
                continue
            win.append(p)
    win.sort(key=lambda x: x.get("publish_time") or 0, reverse=True)
    return win[:config.ROWS_MAX_POSTS]


def _migrate_state_v2(st):
    """旧 v8 state（recent_views/board_prev/previous）→ 新形状：只留 last_run/last_slot/seen。

    幂等，仅改内存态；落地由 _run 末尾统一原子写。last_run 复用为爬虫增量 since。
    """
    changed = False
    for k in ("recent_views", "board_prev", "previous"):
        if k in st:
            del st[k]
            changed = True
    return changed


def _save_history(date_str, slot_label, payload, preview):
    fn = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{slot_label}.json"
    with open(os.path.join(paths.BRIEFINGS_HIST_DIR, fn), "w", encoding="utf-8") as f:
        json.dump({"date": date_str, "slot": slot_label, "payload": payload, "preview": preview},
                  f, ensure_ascii=False, indent=2)
    return os.path.join(paths.BRIEFINGS_HIST_DIR, fn)


def _pid_alive(pid):
    """跨平台进程存活探测。注意：Windows 上 os.kill(pid,0) 是『终止进程』而非探测，绝不能用于 Windows。"""
    if not pid or pid <= 0:
        return False
    try:
        import psutil
        return psutil.pid_exists(pid)
    except Exception:
        pass
    if os.name == "nt":
        return False  # 无 psutil 时无法安全探测 → 交给 3h mtime 兜底接管
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _acquire_lock():
    """单实例锁：上一轮未结束则跳过；被杀/断电残留的锁自动接管（中断恢复）。

    规则：① 锁里 pid 存活且锁未超时 → 有进程在跑，跳过本轮；
          ② pid 已死（中断残留）→ 接管；
          ③ 锁超过 3 小时（pid 可能被复用）→ 无条件接管。
    """
    os.makedirs(paths.DATA_DIR, exist_ok=True)
    lock = paths.LOCK_FILE
    try:
        if os.path.exists(lock):
            old_pid = 0
            try:
                old_pid = int(open(lock, encoding="utf-8").read().strip() or 0)
            except Exception:
                pass
            try:
                age_min = (time.time() - os.path.getmtime(lock)) / 60
            except Exception:
                age_min = 0
            if old_pid > 0 and _pid_alive(old_pid) and age_min < 180:
                log.info("简报进程仍在运行（pid=%s），跳过本轮", old_pid)
                return False
            log.info("接管残留锁（pid=%s，距今 %.0f 分钟）", old_pid, age_min)
        with open(lock, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        return True
    except Exception as e:
        log.warning("锁操作异常 %s，放行本轮", e)
        return True


def _release_lock():
    try:
        if os.path.exists(paths.LOCK_FILE):
            os.remove(paths.LOCK_FILE)
    except Exception:
        pass


def _board_windows(now):
    """双板块展示窗口（北京时）：返回 {key: start_ts} 与合并窗口说明文本。

    short：近 SHORT_WINDOW_TRADING_DAYS 个交易日（下界=首交易日 00:00）；
    swing：近 SWING_WINDOW_CAL_DAYS 个自然日（下界=当日 00:00，覆盖周末）。
    """
    short_days = calendar.trading_days(now.date(), config.SHORT_WINDOW_TRADING_DAYS)
    swing_start_date = now.date() - timedelta(days=config.SWING_WINDOW_CAL_DAYS)
    starts = {
        "short": _beijing_midnight_epoch(short_days[0]),
        "swing": _beijing_midnight_epoch(swing_start_date),
    }
    window_txt = (f"超短板块：近{config.SHORT_WINDOW_TRADING_DAYS}个交易日"
                  f"（{short_days[0]:%m-%d}—{short_days[-1]:%m-%d}） · "
                  f"波段板块：近{config.SWING_WINDOW_CAL_DAYS}个自然日"
                  f"（{swing_start_date:%m-%d} 起）")
    return starts, window_txt


def _run(args):
    paths.load_env()
    _setup_logging()
    # Windows 控制台默认 GBK，print(preview) 遇 emoji 会崩（dry-run 崩在落盘之后；push 不走 print）。
    # 重配 stdout 为 UTF-8 + 替换错误 → 重定向到文件得干净 UTF-8，交互控制台也不崩。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    now = datetime.now(BEIJING_TZ)
    slot_key = _resolve_slot(args, now)
    slot_time, slot_label = config.SLOTS[slot_key]
    date_str = _date_str(now)
    log.info("== 简报 v11 %s %s（slot=%s）==", date_str, slot_time, slot_key)

    if not _acquire_lock():
        return 0  # 已有进程在跑（防重叠）
    atexit.register(_release_lock)  # 正常退出删锁；被杀/断电残留由下一轮自动接管

    st = state.load_state()
    _migrate_state_v2(st)  # v8 残留键清理（内存态，落盘在末尾）

    # 双板块展示窗口（滚动；每次推送现读主文件）＋ 抓取增量下界
    board_start, window_txt = _board_windows(now)
    now_ts = int(now.timestamp())
    log.info("展示窗口 %s", window_txt)
    since_str = (st.get("last_run")
                 or datetime.fromtimestamp(min(board_start.values()),
                                           tz=BEIJING_TZ).strftime("%Y-%m-%d %H:%M"))
    log.info("爬虫增量 since=%s", since_str)

    # 1) 抓新帖（只补主文件缺的新帖；展示窗口单独现读，不依赖抓取结果）
    window_posters = set()  # 两板块窗口内有可读帖的博主（区分"无人发帖"与"有帖无观点"）
    if args.no_scrape:
        log.info("--no-scrape：不抓取，直接读已合并主文件")
    else:
        results, errors = scrape_merge.fetch_all_new_posts(
            config.ALL_BLOGGERS, since_str, max_bloggers=args.max_bloggers,
            per_timeout=args.timeout, workers=args.workers)
        if errors:
            log.warning("本轮抓取失败博主：%s", errors)

    # 2) 行情
    quotes = market.fetch_quotes()
    mkt_text = market.market_line(quotes)
    log.info("行情: %s", mkt_text)

    # 3) 按板块读窗口帖子 → LLM 行抽取（每板块独立口径/窗口；共享 8 位博主两板块各抽一次）
    rows_by_board, directed = {}, 0
    for key in config.PANEL_KEYS:
        by_member = {}
        for name in config.PANELS[key]:
            win = _read_window_posts(name, board_start[key], now_ts)
            if win:
                by_member[name] = win
                window_posters.add(name)
        log.info("[%s] 窗口内有帖博主 %d/%d", key, len(by_member), len(config.PANELS[key]))
        rows, errs = extract_board_rows(key, by_member)
        if errs:
            log.warning("[%s] 行抽取失败博主（该板块不显示、不计数）：%s", key, errs)
        rows_by_board[key] = rows

    # 4) 系统计数（每板块 bull/bear/shown/members；未表态者不计）——卡片头行与总结的权威数字
    counts = board_counts(rows_by_board)
    for key in config.PANEL_KEYS:
        c = counts[key]
        log.info("板块 %s：%d多/%d空（%d/%d 表态）", key, c["bull"], c["bear"],
                 c["shown"], c["members"])
    directed = counts["short"]["shown"] + counts["swing"]["shown"]

    # 5) 渲染：有方向观点 → 全卡 + 卡尾跨板块收敛总结（仅此分支调总结 LLM）；
    #    零方向日 → 最小卡（健康信号），不调总结 LLM。
    if directed > 0:
        summary_text = summarize_boards(rows_by_board, counts, mkt_text, slot_label,
                                        date_str, window_txt=window_txt)
        payload = render.build_board_card_payload(
            rows_by_board, counts, mkt_text, slot_label, date_str,
            window_txt=window_txt, summary_text=summary_text)
    else:
        summary_text = ""
        note = ("超短/波段板块窗口内均无博主发帖更新" if not window_posters
                else "超短/波段板块窗口内均无人给出方向观点")
        payload = render.build_minimal_card_payload(
            mkt_text, slot_label, date_str,
            window_txt=window_txt, note_text=note, counts=counts)

    # 6) 推送 / 落盘
    if args.push:
        ok, resp = render.post_webhook(payload)
        log.info("简报推送 %s: %s", "成功" if ok else "失败", resp)
        if not ok:
            render.post_webhook(render.build_error_payload(resp, date_str, slot_label))
        # 无论成败都推进 last_run/last_slot（失败已发错误心跳；下轮不再重发同一批增量）
        st["last_run"] = now.strftime("%Y-%m-%d %H:%M")
        st["last_slot"] = slot_key
        state.save_state(st)
        fp = _save_history(date_str, slot_label, payload, resp if ok else "")
        log.info("简报历史已存 %s", fp)
        return 0 if ok else 1

    # dry-run：落盘预览，不改状态
    preview = _preview_text(rows_by_board, counts, mkt_text, slot_label, date_str,
                            summary_text=summary_text, window_txt=window_txt)
    fp = _save_history(date_str, slot_label, payload, preview)
    print(preview)
    print("\n[预览已存]", fp)
    print("[dry-run 未推送、未改状态]")
    return 0


def _preview_text(rows_by_board, counts, mkt_text, slot_label, date_str,
                  summary_text="", window_txt=""):
    """dry-run 预览：与卡 payload 同构的纯文本（双板块计数头行 + 名单行 + 卡尾总结）。"""
    from .render import _board_section_lines, _date_header
    lines = [_date_header(date_str, slot_label)]
    if window_txt:
        lines.append(f"🕐 覆盖：{window_txt}")
    lines.append("📈 " + mkt_text)
    lines.append("")
    for key in config.PANEL_KEYS:
        lines.extend(_board_section_lines(key, rows_by_board.get(key) or {},
                                          counts.get(key) or {}))
        lines.append("")
    if summary_text:
        lines.append(f"🧭 {summary_text}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="双板块博主观点速览卡 → 飞书（v11）")
    ap.add_argument("--push", action="store_true", help="真实推送到飞书并推进状态（cron 用）")
    ap.add_argument("--dry-run", action="store_true", help="只落盘预览，不推送、不改状态")
    ap.add_argument("--slot", default="", help="指定时段 key（默认按当前时刻自动判定）")
    ap.add_argument("--no-scrape", action="store_true", help="不爬取，直接读已合并主文件（试跑用）")
    ap.add_argument("--skip-calendar", action="store_true",
                    help="忽略交易日历，把今天当交易日跑（仅 dry-run 冒烟用）")
    ap.add_argument("--max-bloggers", type=int, default=None, help="只抓前 N 位博主（冒烟）")
    ap.add_argument("--timeout", type=int, default=240, help="单博主爬虫超时（秒）")
    ap.add_argument("--workers", type=int, default=5, help="博主并行抓取数（默认 5；1=串行）")
    args = ap.parse_args()
    if not args.push and not args.dry_run:
        args.dry_run = True
    sys.exit(_run(args))


if __name__ == "__main__":
    main()
