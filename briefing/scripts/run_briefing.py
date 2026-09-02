# -*- coding: utf-8 -*-
"""简报编排器：抓新帖 → 行情 → DeepSeek 综合 → 飞书推送（或心跳）。

用法：
  python -m briefing.scripts.run_briefing --push                     # cron 真实推送（自动判定时段）
  python -m briefing.scripts.run_briefing --dry-run --slot evening   # 本机试跑：落盘不推送、不改状态
  python -m briefing.scripts.run_briefing --dry-run --slot am --no-scrape   # 复用已抓数据试跑
  python -m briefing.scripts.run_briefing --push --slot close --max-bloggers 3  # 冒烟

调度（服务器 cron，TZ=Asia/Shanghai，交易日历在脚本内判断，非交易日自动只留 20:00）：
  15 9 * * *  0 10 * * *  0 11 * * *  45 12 * * *  0 14 * * *  0 15 * * *  0 20 * * *
"""
import argparse
import atexit
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone

from . import calendar, config, market, paths, profiles, render, scrape_merge, state
from .state import mark_seen, seen_ids
from .summarize import extract_points, synthesize

log = logging.getLogger("briefing")

WD = ["一", "二", "三", "四", "五", "六", "日"]
BEIJING_TZ = timezone(timedelta(hours=8))  # 时段/日期/窗口全用北京时，独立于宿主机时区
FIRST_RUN_FETCH_DAYS = 7   # 首期：爬虫刷新窗口（宽于时效上限，覆盖低频博主）
VIEW_FRESHNESS_DAYS = 7    # 全板观点时效：博主近期观点发帖超过 N 天视为过时（退出统计），同首报口径


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
    trading = calendar.is_trading_day(now.date())
    if args.slot:
        key = args.slot
        if key not in config.SLOTS:
            raise SystemExit(f"未知 slot: {key}，可选 {list(config.SLOTS)}")
        if not trading and key != "evening":
            log.info("非交易日，跳过盘中槽 %s", key)
            raise SystemExit(0)
        return key
    key = config.slot_for(now, trading)
    if key is None:
        log.info("当前时刻（%s，%s）不在推送时段，跳过", now, "交易日" if trading else "非交易日")
        raise SystemExit(0)
    return key


def _load_profiles():
    if os.path.exists(paths.PROFILES_FILE):
        try:
            return json.load(open(paths.PROFILES_FILE, encoding="utf-8")).get("bloggers", {})
        except Exception:
            pass
    log.info("画像档案缺失，自动生成…")
    return profiles.build()


def _read_delta_no_scrape(tracked, since_str):
    """--no-scrape 用：直接读主文件中 since 之后、未入简报的新帖（供复用已抓数据试跑）。"""
    since_ts = int(datetime.strptime(since_str, "%Y-%m-%d %H:%M").timestamp())
    results, errors = {}, []
    st = state.load_state()
    for blogger in tracked:
        fp = os.path.join(paths.POSTS_DIR, f"{blogger}.json")
        if not os.path.exists(fp):
            continue
        try:
            posts = json.load(open(fp, encoding="utf-8")).get("posts") or []
        except Exception:
            continue
        seen = seen_ids(st, blogger)
        fresh = [p for p in posts
                 if (p.get("publish_time") or 0) >= since_ts
                 and p.get("post_id") and p["post_id"] not in seen]
        if fresh:
            results[blogger] = fresh
            log.info("  (no-scrape) %s 新增 %d 条", blogger, len(fresh))
    return results, errors


def _collect_fresh(results, st):
    """结果按 seen 过滤，返回 [(blogger, post), ...]。"""
    fresh = []
    for blogger, posts in results.items():
        seen = seen_ids(st, blogger)
        for p in posts:
            if p.get("post_id") and p["post_id"] not in seen:
                fresh.append((blogger, p))
    return fresh


def _collect_firstrun_views(tracked, max_age_days=VIEW_FRESHNESS_DAYS):
    """首报：每位追踪博主取最近一篇有实质内容的观点帖（时效性把关）。

    无 2 天窗口限制——直接读主文件全量历史，取每博主最新一篇可读观点；
    最新有内容的帖若距今天数超过 max_age_days 则视为过时（该博主无近期观点，不入首报）。
    跳过视频帖/无正文短帖（与 summarize.extract_points 的过滤口径一致）。
    """
    now_ts = int(time.time())
    views, stale = [], []
    for blogger in tracked:
        fp = os.path.join(paths.POSTS_DIR, f"{blogger}.json")
        if not os.path.exists(fp):
            stale.append(blogger)
            continue
        try:
            posts = json.load(open(fp, encoding="utf-8")).get("posts") or []
        except Exception:
            stale.append(blogger)
            continue
        best = None
        for p in sorted(posts, key=lambda x: x.get("publish_time") or 0, reverse=True):
            content = (p.get("content") or "").strip()
            if content == "[视频帖]" or len(content) < 5:
                continue
            ts = p.get("publish_time") or 0
            if ts and (now_ts - ts) > max_age_days * 86400:
                break  # 最新有内容的帖子已过时效，更早的不用再看
            best = p
            break
        if best:
            views.append((blogger, best))
        else:
            stale.append(blogger)
    if stale:
        log.info("  首期：%d 位博主无近期观点（无数据或最新观点已过时效）: %s", len(stale), "、".join(stale[:8]) + ("…" if len(stale) > 8 else ""))
    return views


def _merge_board(board, points):
    """用本期新帖观点更新全板：每博主取最新帖立场（同一博主多帖按 pub_ts 取最新）。

    全板观点模型：每博主只保留一个"近期观点"，博主发新帖即更新，没发帖的博主保持不变。
    """
    by_blogger = {}
    for pt in points.values():
        by_blogger.setdefault(pt["blogger"], []).append(pt)
    for b, pts in by_blogger.items():
        latest = max(pts, key=lambda x: x.get("pub_ts") or 0)
        board[b] = {"stance": latest["stance"], "strength": latest["strength"],
                    "horizon": latest["horizon"], "quote": latest["quote"],
                    "extreme": latest["extreme"], "summary": latest["summary"],
                    "pub_ts": latest.get("pub_ts")}
    return board


def _prune_board(board, now_ts, days=VIEW_FRESHNESS_DAYS):
    """移除时效外的近期观点：博主发帖超过 days 天 → 该博主退出全板统计。"""
    cutoff = now_ts - days * 86400
    return {b: e for b, e in board.items() if (e.get("pub_ts") or 0) >= cutoff}


def _board_counts(board):
    """全板多空计数（全板口径：对全部博主近期观点统计，非本期增量）。"""
    bull = sum(1 for e in board.values() if e["stance"] == "多")
    bear = sum(1 for e in board.values() if e["stance"] == "空")
    neutral = sum(1 for e in board.values() if e["stance"] == "中性")
    return bull, bear, neutral


def _board_key_bloggers(board, profiles_map, top=render.KEY_BLOGGERS_TOP):
    """全板 Top-N 重点博主：按总榜排名选（有明确方向者；中性无观点可展示，不入选）。"""
    sel = []
    for name, e in board.items():
        if e["stance"] == "中性":
            continue
        sel.append({"name": name, "stance": e["stance"], "strength": e["strength"],
                    "horizon": e["horizon"], "quote": e["quote"],
                    "pub_ts": e.get("pub_ts"),
                    "style": (profiles_map or {}).get(name, {}).get("style") or ""})
    sel, _ = render.select_key_bloggers({"key_bloggers": sel}, top=top)
    return sel


def _save_history(date_str, slot_label, payload, preview):
    fn = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{slot_label}.json"
    with open(os.path.join(paths.BRIEFINGS_HIST_DIR, fn), "w", encoding="utf-8") as f:
        json.dump({"date": date_str, "slot": slot_label, "payload": payload, "preview": preview},
                  f, ensure_ascii=False, indent=2)
    return os.path.join(paths.BRIEFINGS_HIST_DIR, fn)


def _previous_from_card(card):
    c = card["consensus"]
    cons = f"{c['stance']}（{c['bull']}多/{c['bear']}空） {c.get('summary','')}".strip()
    sel, _ = render.select_key_bloggers(card)  # 与卡片展示一致：只取总榜排名靠前者
    kbs = []
    for x in sel:
        h = x.get("horizon") or ""
        kbs.append(f"{x.get('name')}({x.get('stance')}{'/' + h if h else ''}):{x.get('quote','')}")
    return cons, "；".join(kbs)[:300]


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
    now = datetime.now(BEIJING_TZ)
    slot_key = _resolve_slot(args, now)
    slot_time, slot_label = config.SLOTS[slot_key]
    date_str = _date_str(now)
    log.info("== 简报 %s %s（slot=%s）==", date_str, slot_time, slot_key)

    if not _acquire_lock():
        return 0  # 已有进程在跑（防重叠）
    atexit.register(_release_lock)  # 正常退出删锁；被杀/断电残留由下一轮自动接管

    st = state.load_state()
    # 全板观点模型：state.recent_views 存每博主当前近期观点（首期空 → 建板；之后增量更新）。
    # 共识统计=对全部博主近期观点计数（非本期增量帖统计），博主发新帖则其近期观点更新。
    first_board = not st.get("recent_views")
    if first_board:
        since_str = (now - timedelta(days=FIRST_RUN_FETCH_DAYS)).strftime("%Y-%m-%d %H:%M")
        base_txt = f"首期 · 全名单近期观点（≤{VIEW_FRESHNESS_DAYS} 天）· 无上期基准"
    else:
        since_str = st["last_run"] or now.strftime("%Y-%m-%d %H:%M")
        base_txt = f"自 {since_str[-5:]} 以来"
    log.info("简报窗口 since=%s（first_board=%s）", since_str, first_board)

    # 1) 抓新帖（首期以 7 天窗口刷新主文件供建板；之后严格增量）
    if args.no_scrape:
        results, errors = _read_delta_no_scrape(config.TRACKED, since_str)
    else:
        results, errors = scrape_merge.fetch_all_new_posts(
            config.TRACKED, since_str, max_bloggers=args.max_bloggers, per_timeout=args.timeout)

    # 2) 全板维护：首期建板（每博主取最新观点帖），增量用新帖观点更新（每博主取最新帖立场）
    board = dict(st.get("recent_views") or {})
    views = []
    if first_board:
        views = _collect_firstrun_views(config.TRACKED, max_age_days=VIEW_FRESHNESS_DAYS)
        log.info("首期：全名单近期观点 %d 条（%d 位博主）", len(views), len({b for b, _ in views}))
    else:
        views = _collect_fresh(results, st)
        log.info("本期新增 %d 条（发帖博主 %d）", len(views), len({b for b, _ in views}))
    if views:
        points, no_view = extract_points(views)
        log.info("抽点完成：%d 条观点（%d 中性）", len(points), no_view)
        board = _merge_board(board, points)
    board = _prune_board(board, int(time.time()))
    updated = set(board) if first_board else {b for b, _ in views}
    log.info("全板时效内博主 %d 位", len(board))

    # 3) 行情
    quotes = market.fetch_quotes()
    mkt_text = market.market_line(quotes)
    log.info("行情: %s", mkt_text)

    # 4) 空板 / 无更新 → 心跳（板不变）
    if not board or (not first_board and not views):
        payload = render.build_heartbeat_payload(market.heartbeat_line(quotes), slot_label, date_str,
                                                 window_txt=base_txt)
        if args.push:
            ok, resp = render.post_webhook(payload)
            log.info("心跳推送 %s: %s", "成功" if ok else "失败", resp)
            st["last_run"] = now.strftime("%Y-%m-%d %H:%M")
            st["last_slot"] = slot_key
            st["recent_views"] = board  # 顺手提交（已时效裁剪）
            state.save_state(st)
        else:
            fp = _save_history(date_str, slot_label, payload, payload["content"]["text"])
            print("【心跳】", payload["content"]["text"])
            print("已存", fp)
        if errors:
            log.warning("本轮抓取失败博主：%s", errors)
        return 0

    # 5) 综合（全板输入；多空数字由系统按全板计数覆盖，非本期增量）
    window_txt = base_txt
    if not first_board:
        window_txt += f" · {len(updated)} 位博主更新观点 · 全板 {len(board)}/{len(config.TRACKED)} 位有近期观点"
    profiles_map = _load_profiles()
    card, _ = synthesize(board, updated, mkt_text, st.get("previous"), profiles_map,
                         slot_label, date_str, window_txt=window_txt)
    bull, bear, neutral = _board_counts(board)
    card["consensus"]["bull"] = bull
    card["consensus"]["bear"] = bear
    card["consensus"]["neutral"] = neutral
    card["activity"] = {"posting": len(board), "no_view": neutral,
                        "bloggers": sorted(board.keys())}
    card["key_bloggers"] = _board_key_bloggers(board, profiles_map)
    log.info("综合完成: 共识=%s 全板 %d多/%d空/%d中性", card["consensus"]["stance"], bull, bear, neutral)

    # 6) 渲染 + 推送 / 落盘
    payload = render.build_card_payload(card, mkt_text, slot_label, date_str, window_txt=window_txt)
    if args.push:
        ok, resp = render.post_webhook(payload)
        log.info("简报推送 %s: %s", "成功" if ok else "失败", resp)
        if not ok:
            err_payload = render.build_error_payload(resp, date_str, slot_label)
            render.post_webhook(err_payload)
        # 无论成败都推进 seen/全板/上期（失败时错误心跳已通知；下次不再重发同一批）
        for blogger, plist in results.items():
            mark_seen(st, blogger, plist)
        st["recent_views"] = board  # 提交全板（下轮在此基础上增量更新）
        cons, kbs = _previous_from_card(card)
        st["previous"] = {"date": date_str, "slot": slot_label,
                          "consensus_text": cons, "key_bloggers_text": kbs}
        st["last_run"] = now.strftime("%Y-%m-%d %H:%M")
        st["last_slot"] = slot_key
        state.save_state(st)
        fp = _save_history(date_str, slot_label, payload, resp if ok else "")
        log.info("简报历史已存 %s", fp)
        return 0 if ok else 1

    # dry-run：落盘预览，不改状态
    preview = _preview_text(card, mkt_text, slot_label, date_str, window_txt=window_txt)
    fp = _save_history(date_str, slot_label, payload, preview)
    print(preview)
    print("\n[预览已存]", fp)
    print("[dry-run 未推送、未改状态]")
    return 0


def _preview_text(card, mkt_text, slot_label, date_str, window_txt=""):
    from .render import _date_header, _fmt_key_blogger, select_key_bloggers
    lines = [_date_header(date_str, slot_label)]
    if window_txt:
        lines.append(f"🕐 本期覆盖：{window_txt}")
    lines.append("📈 " + mkt_text + "")
    c = card["consensus"]
    lines.append(f"🧭 共识：{c['stance']}（{c['bull']}多/{c['bear']}空/{c['neutral']}中性）")
    if c.get("summary"):
        lines.append("   " + c["summary"])
    if card.get("key_bloggers"):
        sel, total = select_key_bloggers(card)
        header = "⭐ 重点博主"
        if total > len(sel):
            header += f"（总榜 Top {len(sel)}）"
        lines.append(header)
        lines += [_fmt_key_blogger(x) for x in sel]
    for d in card.get("divergences") or []:
        lines.append(f"⚔️ 分歧：{d if isinstance(d, str) else d.get('desc', d)}")
    for r in card.get("risks") or []:
        if isinstance(r, str):
            lines.append(f"⚠️ 风险：{r}")
        else:
            lines.append(f"⚠️ 风险：{r.get('desc','')}（{r.get('blogger','')}）{r.get('note','')}")
    act = card.get("activity") or {}
    if act.get("posting") is not None:
        lines.append(f"📋 全板 {act.get('posting')} 位博主有近期观点（{act.get('no_view')} 中性）")
    for t in card.get("takeaways") or []:
        lines.append(f"🎯 要点：{t}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="优质博主观点简报 → 飞书")
    ap.add_argument("--push", action="store_true", help="真实推送到飞书并推进状态（cron 用）")
    ap.add_argument("--dry-run", action="store_true", help="只落盘预览，不推送、不改状态")
    ap.add_argument("--slot", default="", help="指定时段 key（默认按当前时刻自动判定）")
    ap.add_argument("--no-scrape", action="store_true", help="不爬取，直接读主文件增量（试跑用）")
    ap.add_argument("--max-bloggers", type=int, default=None, help="只抓前 N 位博主（冒烟）")
    ap.add_argument("--timeout", type=int, default=240, help="单博主爬虫超时（秒）")
    args = ap.parse_args()
    if not args.push and not args.dry_run:
        args.dry_run = True
    sys.exit(_run(args))


if __name__ == "__main__":
    main()
