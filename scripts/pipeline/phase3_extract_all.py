#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阶段3 全量信号提取驱动（幂等可重跑）
对 data/posts/ 全部博主跑 extract_signals_direction.py --runs 1
前置: DEEPSEEK_API_KEY 环境变量（绝不落盘）；阶段2 数据管线已跑完
用法:
  DEEPSEEK_API_KEY=... python scripts/pipeline/phase3_extract_all.py            # 全部
  DEEPSEEK_API_KEY=... python scripts/pipeline/phase3_extract_all.py --names 红红火火的老牛哥,股傲   # 只跑指定博主
产出: data/direction_signals/<名>.json（含 _<名>_run.json 溯源，gitignored）
"""
import json, os, sys, time, glob, subprocess, argparse
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
POSTS_DIR = os.path.join(ROOT, "data", "posts")
SIG_DIR = os.path.join(ROOT, "data", "direction_signals")
PY = sys.executable
EXTRACT = [PY, os.path.join(ROOT, "scripts", "pipeline", "extract_signals_direction.py")]
LOG_PATH = os.path.join(ROOT, "data", "pipeline_phase3.log")


def log(msg):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def bloggers():
    names = []
    for fp in glob.glob(os.path.join(POSTS_DIR, "*.json")):
        base = os.path.basename(fp)[:-5]
        if "_bodies_s" in base or base in ("posts",):
            continue
        names.append(base)
    return sorted(names)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--names", default="", help="只跑指定博主（逗号分隔，默认全部）")
    args = parser.parse_args()
    if not os.environ.get("DEEPSEEK_API_KEY"):
        log("❌ DEEPSEEK_API_KEY 未设置，拒绝启动（绝不落盘，只经环境变量）")
        sys.exit(2)
    open(LOG_PATH, "a", encoding="utf-8").close()
    names = bloggers()
    if args.names:
        sel = [x.strip() for x in args.names.split(",") if x.strip()]
        missing = [x for x in sel if x not in names]
        if missing:
            log(f"⚠️ 指定博主不在 posts 目录: {missing}")
        names = [n for n in names if n in sel]
        if not names:
            log("❌ --names 过滤后为空")
            sys.exit(1)
    log(f"阶段3 启动: {len(names)} 位博主，--runs 1 单次提取")
    ok, fail = [], []
    for i, name in enumerate(names, 1):
        t0 = time.time()
        cmd = EXTRACT + [name, "--runs", "1"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=7200, cwd=ROOT)  # 2h：大博主 70+ 批 + 自查；内部已有硬性 API 超时，不会真挂死
            out = (r.stdout or "") + (r.stderr or "")
            ok_marker = f"✅" in out or "完成" in out and r.returncode == 0
            # 提取脚本应无报错退出码；以 returncode==0 为准，错误信息写入日志
            if r.returncode == 0:
                ok.append(name)
                # 统计提取信号条数（从输出里抓）
                m = None
                for line in out.splitlines():
                    if "信号" in line and ("条" in line or "个" in line):
                        m = line.strip()[-80:]
                log(f"  ✅ {i}/{len(names)} {name} ({time.time()-t0:.0f}s)" + (f" | {m}" if m else ""))
            else:
                fail.append(name)
                log(f"  ❌ {i}/{len(names)} {name} rc={r.returncode} ({time.time()-t0:.0f}s)")
                for line in out.splitlines()[-5:]:
                    log(f"      {line}")
        except subprocess.TimeoutExpired:
            fail.append(name)
            log(f"  ❌ {i}/{len(names)} {name} TIMEOUT")
        except Exception as e:
            fail.append(name)
            log(f"  ❌ {i}/{len(names)} {name} 异常 {e}")
        time.sleep(1)
    log(f"阶段3 完成: 成功{len(ok)} 失败{len(fail)}" + (f" 失败: {fail}" if fail else ""))


if __name__ == "__main__":
    main()
