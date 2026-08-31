#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阶段2 数据管线驱动（幂等可重跑）
流程: ①重爬56位已有博主到08-28 → ②抓22条新链接(运行时解析博主) → ③全量补正文(fetch+merge) → ④verify全库
账号改名自愈: feed 主体名≠文件名但 user_id 相同 → 采纳新名（旧文件归档 _backup/），改名传播到后续步骤
产出日志: data/pipeline_phase2.log  用法: python scripts/pipeline/phase2_refresh.py
"""
import json, os, sys, time, glob, subprocess, re, shutil
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
POSTS_DIR = os.path.join(ROOT, "data", "posts")
BACKUP_DIR = os.path.join(POSTS_DIR, "_backup")
PY = sys.executable
SCRAPE = [PY, os.path.join(ROOT, "scripts", "pipeline", "scrape_toutiao.py")]
FETCH = [PY, os.path.join(ROOT, "scripts", "utils", "fetch_bodies_shard.py")]
MERGE = [PY, os.path.join(ROOT, "scripts", "utils", "merge_bodies_to_posts.py")]
VERIFY = [PY, os.path.join(ROOT, "scripts", "utils", "verify_posts.py")]
SINCE = "2026-01-01"
LOG_PATH = os.path.join(ROOT, "data", "pipeline_phase2.log")

NEW_LINKS = [
    "https://www.toutiao.com/w/1870592724492295/",
    "https://www.toutiao.com/article/7677554937022267933/",
    "https://www.toutiao.com/w/1874646575521802/",
    "https://www.toutiao.com/article/7678988209254056506/",
    "https://www.toutiao.com/w/1874776629926921/",
    "https://www.toutiao.com/w/1874405920609280/",
    "https://www.toutiao.com/w/1874734382785536/",
    "https://www.toutiao.com/w/1874751935261771/",
    "https://www.toutiao.com/w/1874737948774475/",
    "https://www.toutiao.com/w/1874692647047180/",
    "https://www.toutiao.com/article/7679045777070588460/",
    "https://www.toutiao.com/article/7679024987348451842/",
    "https://www.toutiao.com/article/7678533084731539994/",
    "https://www.toutiao.com/w/1874724793921548/",
    "https://www.toutiao.com/w/1874034364223492/",
    "https://www.toutiao.com/w/1874740342544516/",
    "https://www.toutiao.com/w/7678942021406655271/",
    "https://www.toutiao.com/article/7679450216864432669/",
    "https://www.toutiao.com/w/1874823538682892/",
    "https://www.toutiao.com/w/1874823886431242/",
    "https://www.toutiao.com/w/1874840284076297/",
    "https://www.toutiao.com/w/1874383720567820/",
]


def log(msg):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def run(cmd, timeout):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=ROOT)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT"
    except Exception as e:
        return -2, str(e)


def existing_bloggers():
    names = []
    for fp in glob.glob(os.path.join(POSTS_DIR, "*.json")):
        base = os.path.basename(fp)[:-5]
        if "_bodies_s" in base or "_feed_check" in base or base in ("posts",):
            continue
        names.append(base)
    return sorted(names)


def load(name):
    try:
        return json.load(open(os.path.join(POSTS_DIR, f"{name}.json"), encoding="utf-8"))
    except Exception:
        return {}


def normalize_url(u):
    """归一化到桌面版 www.toutiao.com，去查询参：m.toutiao.com/w/<id>?ts= → www.toutiao.com/w/<id>/"""
    m = re.search(r"toutiao\.com/(?:group|article|w)/(\d+)", u)
    if m:
        return f"https://www.toutiao.com/w/{m.group(1)}/"
    m = re.search(r"toutiao\.com/i(\d+)", u)
    if m:
        return f"https://www.toutiao.com/article/{m.group(1)}/"
    return u.split("?")[0]


def top_urls(name, k=3):
    d = load(name)
    posts = sorted(d.get("posts") or [], key=lambda p: p.get("publish_time") or 0, reverse=True)
    urls = []
    for p in posts:
        u = (p.get("url") or "").strip()
        if not u or not ("toutiao.com" in u or "/w/" in u):
            continue
        u = normalize_url(u)
        if u not in urls:
            urls.append(u)
        if len(urls) >= k:
            break
    return urls


def count_title_only(name, since=SINCE):
    d = load(name)
    posts = d.get("posts") or []
    return sum(1 for p in posts
               if (p.get("publish_date") or "")[:10] >= since
               and len(p.get("content") or "") < 60
               and p.get("content") != "[视频帖]")


def user_id_of(name):
    return (load(name).get("user_info") or {}).get("user_id")


# ── ① 重爬已有博主（含改名自愈） ───────────────
def adopt_rename(name, feed_name, renames):
    """同账号改名（user_id 一致）→ 采纳新名：feed_check→新文件，旧文件归档备份"""
    fc = os.path.join(POSTS_DIR, f"{name}_feed_check.json")
    target = os.path.join(POSTS_DIR, f"{feed_name}.json")
    if os.path.exists(target):
        log(f"  ⚠️ {name}: 改名目标「{feed_name}」已有文件，人工处理")
        return False
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.move(fc, target)
    old = os.path.join(POSTS_DIR, f"{name}.json")
    if os.path.exists(old):
        shutil.move(old, os.path.join(BACKUP_DIR, f"{name}_renamed_{ts}.json"))
    renames[name] = feed_name
    log(f"  ✅ 改名采纳 {name} → {feed_name}（user_id 一致，旧文件归档 _backup/）")
    return True


def step_rescrape(names, renames):
    log(f"═══ ① 重爬 {len(names)} 位已有博主 → {SINCE} 至今 ═══")
    ok, rename_done, fail = [], [], []
    for i, name in enumerate(names, 1):
        done = False
        for url in top_urls(name):
            rc, out = run(SCRAPE + [url, "--name", name, "--since", SINCE], timeout=1800)
            if "未能提取到用户 token" in out or "未抓取到任何帖子" in out or "拒绝覆盖" in out:
                time.sleep(15)
                continue
            m = re.search(r"主体用户是「(.+?)」", out)
            if m and "⚠️ 警告" in out:
                feed_name = m.group(1)
                if user_id_of(name) and user_id_of(f"{name}_feed_check") == user_id_of(name):
                    adopt_rename(name, feed_name, renames)
                    rename_done.append(f"{name}→{feed_name}")
                else:
                    fail.append(name)
                    log(f"  ❌ {i}/{len(names)} {name}: 名称不符且非同名账号（人工检查 {name}_feed_check.json）")
                done = True
                break
            if rc == 0:
                done = True
                break
            time.sleep(15)
        if done and name not in rename_done and name not in fail:
            ok.append(name)
            log(f"  ✅ {i}/{len(names)} 重爬 {name}")
        elif name not in rename_done and name not in fail:
            fail.append(name)
            log(f"  ❌ {i}/{len(names)} 重爬失败 {name}")
        time.sleep(3)
    log(f"① 完成: 重爬{len(ok)} | 改名{len(rename_done)} {rename_done} | 失败{len(fail)} {fail}")
    return ok, rename_done, fail


# ── ② 抓新链接 ──────────────────────────────────
def step_new_links(orig_names):
    log(f"═══ ② 抓 {len(NEW_LINKS)} 条新链接（运行时解析博主）═══")
    produced = {}
    for i, link in enumerate(NEW_LINKS, 1):
        rc, out = run(SCRAPE + [link, "--since", SINCE], timeout=1800)
        m = re.search(r"结果: (\S+?\.json)", out)
        outname = os.path.basename(m.group(1))[:-5] if m else None
        bad = "未能提取到用户 token" in out or "未抓取到任何帖子" in out or not outname
        if rc == 0 and not bad and outname not in (None, "posts"):
            produced[link] = outname
            log(f"  ✅ {i}/{len(NEW_LINKS)} {link} → {outname}.json")
        else:
            produced[link] = None
            log(f"  ❌ {i}/{len(NEW_LINKS)} {link} 失败 rc={rc}")
        time.sleep(3)
    new_names = sorted({v for v in produced.values() if v and v not in orig_names})
    dup_names = sorted({v for v in produced.values() if v and v in orig_names})
    log(f"② 完成: 新博主 {len(new_names)} 位 {new_names} | 命中已有 {len(dup_names)} 位 {dup_names}")
    return new_names, dup_names


# ── ③ 补正文 + 合并 ─────────────────────────────
def run_shards(name, nshards):
    procs, max_conc = [], 3
    for s in range(nshards):
        while sum(1 for p in procs if p.poll() is None) >= max_conc:
            time.sleep(2)
        p = subprocess.Popen(FETCH + [name, str(s), str(nshards), SINCE],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=ROOT)
        procs.append(p)
    out_all = ""
    for p in procs:
        o, e = p.communicate(timeout=7200)
        out_all += o + e
    return out_all


def step_bodies(names):
    log(f"═══ ③ 全量补正文（fetch+merge）{len(names)} 位 ═══")
    fetched, no_need, fail = [], [], []
    for i, name in enumerate(names, 1):
        n = count_title_only(name)
        if n == 0:
            no_need.append(name)
            log(f"  ⏭ {i}/{len(names)} {name}: 无标题型帖，跳过")
            continue
        nshards = 1 if n <= 100 else (3 if n <= 400 else 6)
        try:
            run_shards(name, nshards)
            rc, merge_out = run(MERGE + [name], timeout=600)
            leftover = glob.glob(os.path.join(POSTS_DIR, f"{name}_bodies_s*.json"))
            if leftover:
                log(f"  ⚠️ {name}: 合并后仍残留 {len(leftover)} 片，手动处理")
            log(f"  ✅ {i}/{len(names)} {name}: 标题型{n}条/分{nshards}片 + 合并")
            fetched.append(name)
        except Exception as e:
            fail.append(name)
            log(f"  ❌ {i}/{len(names)} {name}: 补正文异常 {e}")
    log(f"③ 完成: 补正文{len(fetched)} | 无需{len(no_need)} | 异常{len(fail)}"
        + (f" 异常清单: {fail}" if fail else ""))
    return fetched, no_need, fail


# ── ④ verify ────────────────────────────────────
def step_verify(names):
    log(f"═══ ④ verify 全库（{len(names)} 位，需 hard=0）═══")
    good, hard_fail = [], []
    for i, name in enumerate(names, 1):
        rc, out = run(VERIFY + [name, "--since", SINCE], timeout=180)
        m = re.search(r"VERIFY_RESULT hard=(\d+) warn=(\d+)", out)
        hard = int(m.group(1)) if m else -1
        if rc == 0 and hard == 0:
            good.append(name)
            log(f"  ✅ {i}/{len(names)} {name}: hard=0" + (f" warn={m.group(2)}" if m else ""))
        else:
            hard_fail.append((name, rc, hard))
            log(f"  ❌ {i}/{len(names)} {name}: rc={rc} hard={hard}")
    log(f"④ 完成: 通过{len(good)} 硬失败{len(hard_fail)}"
        + (f" 清单: {hard_fail}" if hard_fail else ""))
    return good, hard_fail


def report_final(all_names, renames):
    log("═══ 阶段2 收尾检查 ═══")
    if renames:
        log(f"📛 账号改名汇总: {sorted(f'{k}→{v}' for k, v in renames.items())}")
    leftover = glob.glob(os.path.join(POSTS_DIR, "*_bodies_s*.json"))
    if leftover:
        log(f"⚠️ 残留未合并分片 {len(leftover)} 份: {[os.path.basename(x) for x in leftover]}")
    feed_chk = glob.glob(os.path.join(POSTS_DIR, "*_feed_check.json"))
    if feed_chk:
        log(f"⚠️ 遗留 feed_check {len(feed_chk)} 份: {[os.path.basename(x) for x in feed_chk]}")
    fallback = os.path.join(POSTS_DIR, "posts.json")
    if os.path.exists(fallback):
        log("⚠️ 存在兜底 posts.json（有链接未解析出博主名）")
    by_uid = {}
    for name in all_names:
        uid = user_id_of(name)
        if uid:
            by_uid.setdefault(uid, []).append(name)
    for uid, names in by_uid.items():
        if len(names) > 1:
            log(f"⚠️ 疑似同账号多文件 {names} (user_id={uid[:12]}…)")
    stale = []
    for name in all_names:
        posts = load(name).get("posts") or []
        latest = max((p.get("publish_date", "")[:10] for p in posts if p.get("publish_date")), default="")
        if latest and latest < "2026-08-27":
            stale.append((name, latest))
    if stale:
        log(f"⚠️ {len(stale)} 位博主最新帖早于08-27（可能真停更，需人工确认）: {stale}")
    log("═══ 阶段2 全部结束 ═══")


def main():
    open(LOG_PATH, "a", encoding="utf-8").close()
    log("=" * 70)
    log(f"阶段2 启动  python={PY}")
    orig = existing_bloggers()
    log(f"已有博主 {len(orig)} 位")
    renames = {}
    step_rescrape(orig, renames)
    new_names, dup = step_new_links(orig)
    active = sorted((set(orig) - set(renames)) | set(renames.values()) | set(new_names))
    log(f"有效博主 {len(active)} 位（重命名后）")
    step_bodies(active)
    step_verify(active)
    report_final(active, renames)


if __name__ == "__main__":
    main()
