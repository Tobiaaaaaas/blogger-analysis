# -*- coding: utf-8 -*-
"""简报编排器（v9：18 人窗口速览卡 · 交易日三推）。

流程：抓新帖 → 行情 → LLM 逐博主行抽取（近 3 交易日方向观点）→ 卡底收敛总结 → 飞书推送。
展示窗口 = 滚动「近 3 个交易日」（现读合并主文件 data/posts，按发帖时间过滤，不依赖上次推送）；
抓取窗口 = 增量 since state.last_run（只补主文件里还没有的新帖）。
每档推送前先抓新帖；每档对窗口内帖子做全量 LLM 重读（不按 post_id 缓存引文）。
只发一张固定 18 行的 roster 卡；旧 v8 共识卡 / 心跳已移出热路径（代码仍留作 LEGACY）。

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
from .summarize import count_rows, extract_window_rows, summarize_window

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
    """某日期（北京时）00:00 的 epoch 秒。作展示窗口下界：首交易日 00:00 ≤ 帖子发帖时刻。"""
    return int(datetime(d.year, d.month, d.day, tzinfo=BEIJING_TZ).timestamp())


def _read_window_posts(blogger, start_ts, now_ts):
    """现读合并主文件，返回该博主窗口内 [start_ts, now_ts] 的可读帖（新→旧）。

    过滤 [视频帖]/无正文短帖（与 extract_window_rows 口径一致），每博主只取最新
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
    """旧 v8 state（recent_views/board_prev/previous）→ v9 形状：只留 last_run/last_slot/seen。

    幂等，仅改内存态；落地由 _run 末尾统一原子写。last_run 复用为爬虫增量 since。
    """
    changed = False
    for k in ("recent_views", "board_prev", "previous"):
        if k in st:
            del st[k]
            changed = True
    return changed


def _row_counts(rows):
    """v10：分档计数（超短/波段各自多空 + 观望 + 无更新）→ summarize.count_rows（断言合计=18）。"""
    return count_rows(rows)


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
    log.info("== 简报 v9 %s %s（slot=%s）==", date_str, slot_time, slot_key)

    if not _acquire_lock():
        return 0  # 已有进程在跑（防重叠）
    atexit.register(_release_lock)  # 正常退出删锁；被杀/断电残留由下一轮自动接管

    st = state.load_state()
    _migrate_state_v2(st)  # v8 残留键清理（内存态，落盘在末尾）

    # 展示窗口：滚动近 3 个交易日，下界=首交易日 00:00（北京）；每次推送现读主文件
    win_days = calendar.trading_days(now.date(), config.WINDOW_TRADING_DAYS)
    start_ts = _beijing_midnight_epoch(win_days[0])
    now_ts = int(now.timestamp())
    window_txt = (f"近{config.WINDOW_TRADING_DAYS}个交易日"
                  f"（{win_days[0]:%m-%d}—{win_days[-1]:%m-%d}）")
    log.info("展示窗口 %s（start_ts=%d）", window_txt, start_ts)

    # 抓取窗口：增量 since state.last_run（首跑/缺 → 窗口首日 00:00，兼容 v8 残留 last_run）
    since_str = (st.get("last_run")
                 or datetime.fromtimestamp(start_ts, tz=BEIJING_TZ).strftime("%Y-%m-%d %H:%M"))
    log.info("爬虫增量 since=%s", since_str)

    # 1) 抓新帖（只补主文件缺的新帖；展示窗口单独现读，不依赖抓取结果）
    errors = []
    if args.no_scrape:
        log.info("--no-scrape：不抓取，直接读已合并主文件")
    else:
        results, errors = scrape_merge.fetch_all_new_posts(
            config.ROSTER, since_str, max_bloggers=args.max_bloggers,
            per_timeout=args.timeout, workers=args.workers)
        if errors:
            log.warning("本轮抓取失败博主：%s", errors)

    # 2) 读展示窗口帖子（滚动 3 交易日，现读合并主文件 data/posts/<b>.json）
    by_blogger = {}
    for name in config.ROSTER:
        win = _read_window_posts(name, start_ts, now_ts)
        if win:
            by_blogger[name] = win
            log.info("  %s 窗口内可读帖 %d 条", name, len(win))
    log.info("窗口内有帖博主 %d/%d", len(by_blogger), len(config.ROSTER))

    # 3) 行情
    quotes = market.fetch_quotes()
    mkt_text = market.market_line(quotes)
    log.info("行情: %s", mkt_text)

    # 4) LLM 行抽取：每博主近 3 交易日方向观点 → 摘要 + 逐字原话（引文时间系统回填）。
    #    无帖/全视频帖博主不进 extract（由它直接占位）；此处把 18 人补齐成确定集合。
    rows, row_errors = extract_window_rows(by_blogger)
    for name in config.ROSTER:
        if name not in rows:
            rows[name] = {"blogger": name, "has_view": False, "stance": "", "horizon": "",
                          "bucket": "", "summary": "", "quote": "", "quote_ts": None, "n_posts": 0}
    if row_errors:
        log.warning("行抽取失败博主（降级占位）：%s", row_errors)

    # 5) 系统分档计数（超短/波段各多空 + 观望/无更新）——卡片与总结引用的权威数字
    counts = _row_counts(rows)
    s, w = counts["short"], counts["swing"]
    log.info("行抽取完成：超短 %d多/%d空 波段 %d多/%d空 · 观望 %d · 无更新 %d",
             s["bull"], s["bear"], w["bull"], w["bear"],
             counts["neutral"], counts["none"])
    directed = s["bull"] + s["bear"] + w["bull"] + w["bear"]

    # 6) 渲染：有方向观点 → 全卡 + 卡底收敛总结（仅此分支调总结 LLM）；
    #    零方向日 → 最小卡（健康信号），不调总结 LLM。
    if directed > 0:
        summary_text = summarize_window(rows, counts, mkt_text, slot_label, date_str,
                                        window_txt=window_txt)
        payload = render.build_roster_card_payload(
            rows, mkt_text, slot_label, date_str,
            window_txt=window_txt, summary_text=summary_text, counts=counts)
    else:
        summary_text = ""
        note = ("近 3 个交易日各博主均无明确方向观点" if counts["none"] < len(config.ROSTER)
                else "近 3 个交易日无博主发帖更新")
        payload = render.build_minimal_card_payload(
            mkt_text, slot_label, date_str,
            window_txt=window_txt, note_text=note, counts=counts)

    # 7) 推送 / 落盘
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
    preview = _preview_text(rows, counts, mkt_text, slot_label, date_str,
                            summary_text=summary_text, window_txt=window_txt)
    fp = _save_history(date_str, slot_label, payload, preview)
    print(preview)
    print("\n[预览已存]", fp)
    print("[dry-run 未推送、未改状态]")
    return 0


def _preview_text(rows, counts, mkt_text, slot_label, date_str, summary_text="", window_txt=""):
    """dry-run 预览：与卡片 payload 同构的纯文本（分段 18 行 + 分档计数角标）。"""
    from .render import _date_header, _roster_section_lines
    lines = [_date_header(date_str, slot_label)]
    if window_txt:
        lines.append(f"🕐 覆盖：{window_txt}")
    lines.append("📈 " + mkt_text)
    lines.append("")
    lines.extend(_roster_section_lines(rows))
    lines.append("")
    if summary_text:
        lines.append(f"🧭 {summary_text}")
    lines.append(f"（{config.format_counts(counts)}）")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="18 人博主观点窗口速览卡 → 飞书（v9）")
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
