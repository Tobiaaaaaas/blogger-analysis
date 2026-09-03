# -*- coding: utf-8 -*-
"""路径与运行环境解析。

briefing/ 是独立部署单元：部署时把整个 briefing/ 目录拷到服务器即可。
它依赖父仓库（blogger_ana）的两样东西：
  1) 数据：data/posts、data/direction_signals、data/market、reports
  2) 爬虫/正文脚本：scripts/pipeline/scrape_toutiao.py、scripts/utils/fetch_bodies_shard.py、
     scripts/utils/merge_bodies_to_posts.py、scripts/pipeline/extract_signals_direction.py（DeepSeek 调用底座）

父仓库路径可通过环境变量 REPO_ROOT 覆盖（部署到服务器时若目录结构不同）。
"""
import os

BRIEFING_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.environ.get("REPO_ROOT") or os.path.dirname(BRIEFING_DIR)

# 父仓库关键路径
POSTS_DIR = os.path.join(REPO_ROOT, "data", "posts")
SIGNALS_DIR = os.path.join(REPO_ROOT, "data", "direction_signals")
MARKET_DIR = os.path.join(REPO_ROOT, "data", "market")
REPORTS_DIR = os.path.join(REPO_ROOT, "reports")

SCRAPE_SCRIPT = os.path.join(REPO_ROOT, "scripts", "pipeline", "scrape_toutiao.py")
BODIES_SCRIPT = os.path.join(REPO_ROOT, "scripts", "utils", "fetch_bodies_shard.py")
MERGE_BODIES_MOD = os.path.join(REPO_ROOT, "scripts", "utils", "merge_bodies_to_posts.py")
EXTRACT_MOD = os.path.join(REPO_ROOT, "scripts", "pipeline", "extract_signals_direction.py")

# 本项目运行时目录
DATA_DIR = os.path.join(BRIEFING_DIR, "data")
BRIEFINGS_HIST_DIR = os.path.join(DATA_DIR, "briefings")
STATE_FILE = os.path.join(DATA_DIR, "state.json")
ROWS_CACHE_FILE = os.path.join(DATA_DIR, "rows_cache.json")  # 行抽取缓存（DeepSeek 增量复用）
LOCK_FILE = os.path.join(DATA_DIR, "run.lock")
PROFILES_FILE = os.path.join(DATA_DIR, "profiles.json")
LOG_FILE = os.path.join(DATA_DIR, "briefing.log")
CALENDAR_CACHE = os.path.join(DATA_DIR, "trade_calendar.json")


def ensure_dirs():
    os.makedirs(POSTS_DIR, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(BRIEFINGS_HIST_DIR, exist_ok=True)


def load_env():
    """加载 .env（briefing/.env）与父仓库 .deepseek_keys.env；已存在的环境变量优先。"""
    env_files = [
        os.path.join(BRIEFING_DIR, ".env"),
        os.path.join(REPO_ROOT, ".deepseek_keys.env"),
    ]
    for fp in env_files:
        if not os.path.exists(fp):
            continue
        with open(fp, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
