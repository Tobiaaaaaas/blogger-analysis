#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阶段2d 全库正文补抓驱动（移动端 SSR 修复后重跑，幂等可续）
背景: 阶段2b 的补正文用了旧桌面路径（www.toutiao.com/article/ 返回空壳），标题帖几乎全失败。
本脚本用修复后的 fetch_bodies_shard.py（m.toutiao.com/i<id>/ 移动端 SSR）重抓全部博主标题帖。
- 每个博主按标题帖数分片（≤100→1片 / ≤400→3片 / 更大→6片），最多3片并发
- fetch 完成后 merge_bodies_to_posts.py 合并（含 _placeholder 重试：登录墙/空正文会重抓）
- 产出日志: data/pipeline_phase2d.log
用法: python scripts/pipeline/phase2d_bodies.py [博主名...]   （不传=全库）
"""
import json, os, sys, time, glob, subprocess, shutil
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
POSTS_DIR = os.path.join(ROOT, "data", "posts")
PY = sys.executable
FETCH = [PY, os.path.join(ROOT, "scripts", "utils", "fetch_bodies_shard.py")]
MERGE = [PY, os.path.join(ROOT, "scripts", "utils", "merge_bodies_to_posts.py")]
VERIFY = [PY, os.path.join(ROOT, "scripts", "utils", "verify_posts.py")]
SINCE = "2026-01-01"
LOG_PATH = os.path.join(ROOT, "data", "pipeline_phase2d.log")


def log(msg):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load(name):
    try:
        return json.load(open(os.path.join(POSTS_DIR, f"{name}.json"), encoding="utf-8"))
    except Exception:
        return {}


def count_title_only(name):
    d = load(name)
    posts = d.get("posts") or []
    return sum(1 for p in posts
               if (p.get("publish_date") or "")[:10] >= SINCE
               and len(p.get("content") or "") < 60
               and p.get("content") != "[视频帖]")


def run(cmd, timeout):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=ROOT)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT"
    except Exception as e:
        return -2, str(e)


def run_shards(name, nshards):
    procs, max_conc = [], 2  # 2026-08-30: 3并发曾触发"当前网络环境无法查看"风控，降到 2
    for s in range(nshards):
        while sum(1 for p in procs if p.poll() is None) >= max_conc:
            time.sleep(2)
        p = subprocess.Popen(FETCH + [name, str(s), str(nshards), SINCE],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=ROOT)
        procs.append(p)
    out_all = ""
    for p in procs:
        try:
            o, e = p.communicate(timeout=7200)
            out_all += o + e
        except Exception as ex:
            p.kill()
            out_all += f"\n[shard timeout] {ex}\n"
    return out_all


def merge_and_check(name):
    rc, out = run(MERGE + [name], timeout=600)
    leftover = glob.glob(os.path.join(POSTS_DIR, f"{name}_bodies_s*.json"))
    if leftover:
        # merge 成功但残留分片 → 归档（merge 已消费），避免 verify 报未合并
        os.makedirs(os.path.join(ROOT, "archive"), exist_ok=True)
        dst = os.path.join(ROOT, "archive", "20260830-bodies-merged")
        os.makedirs(dst, exist_ok=True)
        for fp in leftover:
            shutil.move(fp, os.path.join(dst, os.path.basename(fp)))
    return rc == 0, out


def process(name):
    n = count_title_only(name)
    if n == 0:
        log(f"  ⏭ {name}: 无标题型帖，跳过")
        return "skip"
    nshards = 1 if n <= 100 else (3 if n <= 400 else 6)
    t0 = time.time()
    log(f"  ▶ {name}: 标题型{n}条/分{nshards}片 开始")
    out = run_shards(name, nshards)
    ok, mo = merge_and_check(name)
    dt = int(time.time() - t0)
    # 统计抓取后剩余标题帖（合并后）
    after = count_title_only(name)
    log(f"  {'✅' if ok else '❌'} {name}: 抓完合并后剩余标题帖 {after}/{n}（{dt}s）")
    return "ok" if ok else "err"


def main():
    args = sys.argv[1:]
    open(LOG_PATH, "a", encoding="utf-8").close()
    log("=" * 70)
    log(f"阶段2d 全库正文补抓启动  目标={args or '全库'}")
    if args:
        names = args
    else:
        names = sorted(
            base[:-5] for base in glob.glob(os.path.join(POSTS_DIR, "*.json"))
            if "_bodies_s" not in base and "_feed_check" not in base and base[:-5] != "posts"
        )
    ok, err = [], []
    for i, name in enumerate(names, 1):
        st = process(name)
        if st == "ok":
            ok.append(name)
        elif st == "err":
            err.append(name)
        time.sleep(2)
    log(f"阶段2d 结束: 完成 {len(ok)} | 异常 {len(err)} {err if err else ''}")


if __name__ == "__main__":
    main()
