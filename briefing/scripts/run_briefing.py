# -*- coding: utf-8 -*-
"""简报编排器 v14：超短/波段拆两群两卡 · 盘中 30 分档 · 行缓存增量复用 · 交易日窗口。

节奏与调度：
- 交易日盘中每 30 分钟一档（config.TRADING_TICKS：09:30…15:00）。墙钟命中网格才跑；
  门禁在抓取/锁之前（12:00/12:30/15:30 等伪 tick 与 StartWhenAvailable 离网格补跑
  → log 后 exit 0，不碰锁不碰状态）。
- 超短板块每档推一张卡；波段板块仅在 config.SWING_TICKS（09:30/11:00/14:30）三档额外
  推一张。两板块各自读帖→抽取→锚定→计数→收敛总结，各推各群
  （render.webhook_for 读 config.WEBHOOK_ENV；该板块 webhook 缺失按失败处理，**不回落**
  FEISHU_WEBHOOK_URL——防波段卡误发超短群）。
- 窗口（v14 交易日口径）：超短 = 前一交易日 00:00 至 now、波段 = 前 3 个交易日 00:00 至 now
  （config.WINDOW_TRADING_DAYS；起点用 calendar.n_trading_days_ago，非自然日相减）。
  由此周一早晨窗口含上周五帖（消除 v13 自然日取舍）。
- 每档必发：板块有方向观点→全卡 + 本板块收敛总结；空→单板块最小卡（区分窗口
  无人发帖 / 有人发帖但无该板块方向观点）；内容没变也发；不加 🆕 标记。

状态 / 增量（2026-09 v13）：
- fetched_at（爬虫水位：抓取+merge 完成即写，不等待推送成败）与 last_run（推送水位：
  全板块推完才写）分离——避免推送失败下一档重抓已抓过的增量。
- rows_cache.json（行抽取缓存）：某博主窗口帖集合未变 → 跳过 DeepSeek 复用缓存行。
- 历史文件名 {wall:%Y%m%d}_{HHMM}_{board_key}.json（09:30 同秒双卡靠 board_key 互不覆盖；
  同档重跑幂等覆盖）。

用法：
  python -m briefing.scripts.run_briefing --push                          # 墙钟调度（auto 门禁+自动判板块）
  python -m briefing.scripts.run_briefing --push --time 09:30 --board both # 暖场/手动补推
  python -m briefing.scripts.run_briefing --dry-run --time 11:00 --board swing --no-scrape
  python -m briefing.scripts.run_briefing --dry-run --time 09:30 --board short --no-scrape --skip-calendar
  python -m briefing.scripts.run_briefing --push --board short --max-bloggers 3   # 冒烟

--time 只覆盖"决策时刻"（窗口/锚定/标题/总结措辞）；state/历史文件名/水位一律用真实
墙钟 wall —— 防止模拟档回拨爬虫水位或把假日期写进历史。
Windows：单任务 BriefingIntraday 盘中每 30 分运行 --push（见 DEPLOY.md）。
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
from .summarize import (board_counts, extract_board_rows, resolve_anchors,
                        summarize_board)

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


def _parse_hhmm(s):
    """'HH:MM' → (h, m)；非法退出。"""
    try:
        h, m = s.strip().split(":", 1)
        return int(h), int(m)
    except Exception:
        raise SystemExit(f"--time 需 HH:MM 格式（收到 {s!r}）")


def _explicit_boards(word):
    if word == "short":
        return ["short"]
    if word == "swing":
        return ["swing"]
    if word == "both":
        return ["short", "swing"]
    raise SystemExit(f"--board 需 auto|short|swing|both（收到 {word!r}）")


def _resolve_boards(args, wall):
    """返回应推板块列表与决策时刻 now。auto 不在档 → exit 0（在锁/抓取之前）。

    now = wall，除非给 --time（只改决策时刻，改日期仍用墙钟当天）；模拟档同样过门禁，
    使 --time 12:00 / 09:00 / 15:30 / 10:13 等 off-grid 时刻也能复现"跳过"路径。
    """
    now = wall
    if args.time:
        h, m = _parse_hhmm(args.time)
        now = wall.replace(hour=h, minute=m, second=0, microsecond=0)
    if args.board and args.board != "auto":
        # 显式指定板块：不门禁（测试/暖场用）；非交易日强制真推危险 → 拒绝并 exit 0。
        if not (args.skip_calendar or calendar.is_trading_day(now.date())):
            if args.push:
                log.warning("非交易日 %s 强制 --board %s：拒绝真实推送", now.date(), args.board)
                raise SystemExit(0)
            log.info("非交易日 %s 强制 --board %s：dry-run 放行测试", now.date(), args.board)
        return _explicit_boards(args.board), now
    # auto：交易日 + 盘中 30 分网格
    trading = True if args.skip_calendar else calendar.is_trading_day(now.date())
    if not trading:
        log.info("非交易日（%s），跳过", now.date())
        raise SystemExit(0)
    if not config.in_intraday_grid(now):
        log.info("时刻 %s 不在盘中 30 分网格，跳过", now.strftime("%H:%M"))
        raise SystemExit(0)
    return config.due_boards(now), now


def _beijing_midnight_epoch(d) -> int:
    """某日期（北京时）00:00 的 epoch 秒。作展示窗口下界：下界当日 00:00 ≤ 帖子发帖时刻。"""
    return int(datetime(d.year, d.month, d.day, tzinfo=BEIJING_TZ).timestamp())


def _window_start_ts(key, now):
    """某板块展示窗口下界（v14 交易日口径）= 前一/前N个交易日 00:00（北京时 epoch 秒）。

    N = config.WINDOW_TRADING_DAYS[key]；起点日由 calendar.n_trading_days_ago 按交易日
    回看得到，上界仍 ≤ now。周末/盘前模拟（now 非交易日）按最近交易日取参考日。
    """
    start = calendar.n_trading_days_ago(now.date(), config.WINDOW_TRADING_DAYS[key])
    return _beijing_midnight_epoch(start)


def _window_txt(key, now):
    """某板块覆盖窗口说明（进卡/总结）。如 '超短板块 前1个交易日到现在（09-03 起）'。"""
    start = calendar.n_trading_days_ago(now.date(), config.WINDOW_TRADING_DAYS[key])
    return (f"{config.BOARD_WORD[key]}板块 前{config.WINDOW_TRADING_DAYS[key]}个交易日"
            f"到现在（{start:%m-%d} 起）")


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

    幂等，仅改内存态；落地由 _run 末尾统一原子写。
    """
    changed = False
    for k in ("recent_views", "board_prev", "previous"):
        if k in st:
            del st[k]
            changed = True
    return changed


def _migrate_state_v3(st):
    """v12→v13：爬虫水位 fetched_at 回填。幂等，仅改内存态。

    旧版只有 last_run（爬虫 since 与推送同源）：首跑 v13 若无 fetched_at 而 last_run
    非空 → 回填 fetched_at=last_run，既不漏抓 last_run 之前的旧帖也不重抓太多。
    """
    changed = False
    if not st.get("fetched_at") and st.get("last_run"):
        st["fetched_at"] = st["last_run"]
        changed = True
    return changed


def _save_history(wall, hm, board_key, payload, preview):
    """历史文件名 {wall:%Y%m%d}_{HHMM}_{board_key}.json（HHMM 无冒号，Windows 文件名安全）。"""
    fn = f"{wall:%Y%m%d}_{hm.replace(':', '')}_{board_key}.json"
    with open(os.path.join(paths.BRIEFINGS_HIST_DIR, fn), "w", encoding="utf-8") as f:
        json.dump({"date": f"{wall:%m-%d} 周{WD[wall.weekday()]}", "hm": hm,
                   "board": board_key, "payload": payload, "preview": preview},
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


def _preview_lines(board_key, counts, rows, mkt_text, date_str, hm,
                   window_txt="", summary_text=""):
    """dry-run 预览：单板块标题 + 覆盖 + 行情 + 名单（与卡同构，_board_section_lines 单源）。"""
    from .render import _board_section_lines
    lines = [config.board_title(board_key, date_str, hm)]
    if window_txt:
        lines.append(f"🕐 覆盖：{window_txt}")
    lines.append("📈 " + mkt_text)
    lines.append("")
    lines.extend(_board_section_lines(board_key, rows, counts))
    if summary_text:
        lines.append("")
        lines.append(f"🧭 {summary_text}")
    return "\n".join(lines)


def _process_board(key, mkt_text, now, date_str, hm):
    """单板块全链路：读窗口帖 → 抽取(缓存) → 锚定 → 计数 → 全卡/最小卡。返回 (payload, preview)。

    抽取按板块传 window_start_ts（缓存行引文落窗的二道防御）；resolve_anchors 每轮对
    （缓存复用的）行重锚——目标日随卡片日变。空板区分 近窗口无人发帖 / 有人无方向观点。
    """
    start_ts = _window_start_ts(key, now)
    now_ts = int(now.timestamp())
    by_member, posters = {}, set()
    for name in config.PANELS[key]:
        win = _read_window_posts(name, start_ts, now_ts)
        if win:
            by_member[name] = win
            posters.add(name)
    log.info("[%s] 窗口内有帖博主 %d/%d", key, len(by_member), len(config.PANELS[key]))

    rows_raw, errs = extract_board_rows(key, by_member, window_start_ts=start_ts)
    if errs:
        log.warning("[%s] 行抽取失败博主（该板块不显示、不计数）：%s", key, errs)
    anchored = resolve_anchors({key: rows_raw}, now)[key]  # 单板块包装；锚定+剔已过目标
    c = board_counts({key: anchored})[key]
    log.info("[%s] %d多/%d空（%d/%d 表态）", key, c["bull"], c["bear"], c["shown"], c["members"])

    win_txt = _window_txt(key, now)
    if c["shown"] > 0:
        summary_text = summarize_board(key, anchored, c, mkt_text, date_str,
                                       window_txt=win_txt, now=now)
        payload = render.build_board_card_payload(key, anchored, c, mkt_text, date_str, hm,
                                                  window_txt=win_txt, summary_text=summary_text)
    else:
        summary_text = ""
        note = (f"前{config.WINDOW_TRADING_DAYS[key]}个交易日起窗口内无博主发帖" if not posters
                else config.BOARD_META[key]["empty_note"])
        payload = render.build_minimal_card_payload(key, c, mkt_text, date_str, hm,
                                                    window_txt=win_txt, note_text=note)
    preview = _preview_lines(key, c, anchored, mkt_text, date_str, hm,
                             window_txt=win_txt, summary_text=summary_text)
    return payload, preview


def _run(args):
    paths.load_env()
    _setup_logging()
    # Windows 控制台默认 GBK，print(preview) 遇 emoji 会崩（dry-run 崩在落盘之后；push 不走 print）。
    # 重配 stdout 为 UTF-8 + 替换错误 → 重定向到文件得干净 UTF-8，交互控制台也不崩。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    wall = datetime.now(BEIJING_TZ)          # 真实墙钟：状态/历史名/水位一律用它
    boards, now = _resolve_boards(args, wall)  # 决策时刻：窗口/锚定/标题/措辞
    date_str = _date_str(now)
    hm = now.strftime("%H:%M")
    log.info("== 简报 v13(双卡·30分网格) %s %s 板块=%s ==", date_str, hm, ",".join(boards))

    if not _acquire_lock():
        return 0  # 已有进程在跑（防重叠）
    atexit.register(_release_lock)  # 正常退出删锁；被杀/断电残留由下一轮自动接管

    st = state.load_state()
    _migrate_state_v2(st)  # v8 残留键清理
    _migrate_state_v3(st)  # v13：无 fetched_at → 回填旧 last_run（内存态，落盘在末尾）

    # 1) 一次抓取（since=爬虫水位 fetched_at；无则回退最旧板块窗口下界）。
    #    merge 完成即写 fetched_at=wall——本档推送成败不影响下一档增量下界。
    if args.no_scrape:
        log.info("--no-scrape：不抓取，直接读已合并主文件")
    else:
        since_str = st.get("fetched_at")
        if not since_str:
            oldest = min(_window_start_ts(k, now) for k in config.PANEL_KEYS)
            since_str = datetime.fromtimestamp(oldest, tz=BEIJING_TZ).strftime("%Y-%m-%d %H:%M")
        log.info("爬虫增量 since=%s", since_str)
        results, errors = scrape_merge.fetch_all_new_posts(
            config.ALL_BLOGGERS, since_str, max_bloggers=args.max_bloggers,
            per_timeout=args.timeout, workers=args.workers)
        if errors:
            log.warning("本轮抓取失败博主：%s", errors)
        if args.push:
            st["fetched_at"] = wall.strftime("%Y-%m-%d %H:%M")
            state.save_state(st)
            log.info("爬虫水位已推进 fetched_at=%s", st["fetched_at"])

    # 2) 行情一次（全板块共用同一份）
    quotes = market.fetch_quotes()
    mkt_text = market.market_line(quotes)
    log.info("行情: %s", mkt_text)

    # 3) 逐板块处理 + 各自 webhook 推送；单板块异常不拖垮另一板块。
    ok_all = True
    for key in boards:
        try:
            payload, preview = _process_board(key, mkt_text, now, date_str, hm)
        except Exception as e:
            log.exception("[%s] 板块处理异常：%s", key, e)
            if args.push:
                url = render.webhook_for(key)
                if url:
                    render.post_webhook(
                        render.build_board_error_payload(str(e), key, date_str, hm),
                        webhook_url=url)
                else:
                    log.error("[%s] 板块异常且未配置 webhook，无错误心跳", key)
            ok_all = False
            continue

        if args.push:
            url = render.webhook_for(key)
            if url is None:
                ok, resp = False, f"未配置 {config.WEBHOOK_ENV[key]}（不回落旧群）"
            else:
                ok, resp = render.post_webhook(payload, webhook_url=url)
                if not ok:
                    render.post_webhook(
                        render.build_board_error_payload(resp, key, date_str, hm),
                        webhook_url=url)
            log.info("[%s] 推送 %s: %s", key, "成功" if ok else "失败", resp)
            ok_all = ok_all and ok
            fp = _save_history(wall, hm, key, payload, resp if ok else "")
            log.info("[%s] 历史已存 %s", key, fp)
        else:
            fp = _save_history(wall, hm, key, payload, preview)
            print(preview)
            print("\n[预览已存]", fp)
            print("[dry-run 未推送、未改状态]")
            ok_all = ok_all and True

    # 4) 全板块推完推进推送水位（板块级失败已发错误心跳；下一档不再重发同一批增量）
    if args.push:
        st["last_run"] = wall.strftime("%Y-%m-%d %H:%M")
        st["last_slot"] = ",".join(boards)
        state.save_state(st)
        log.info("推送水位已推进 last_run=%s（boards=%s）", st["last_run"], st["last_slot"])
    return 0 if ok_all else 1


def main():
    ap = argparse.ArgumentParser(description="超短/波段 双群盘中速览卡 → 飞书（v13）")
    ap.add_argument("--push", action="store_true", help="真实推送到飞书并推进状态（调度用）")
    ap.add_argument("--dry-run", action="store_true", help="只落盘预览，不推送、不改状态")
    ap.add_argument("--time", default="", help="模拟决策时刻 HH:MM（跳过墙钟，只影响窗口/锚定/标题；不加则用墙钟）")
    ap.add_argument("--board", default="auto",
                    help="auto|short|swing|both（auto=按墙钟判板块；显式=强制，仅供测试/暖场）")
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
