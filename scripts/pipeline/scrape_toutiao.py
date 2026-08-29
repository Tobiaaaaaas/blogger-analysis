"""
爬取今日头条博主帖子 — v5
策略：全部在Playwright内完成
1. 渲染用户主页
2. 用 page.evaluate() 在浏览器内调用 feed API（自带签名）
3. 翻页获取大量帖子
4. 页面内提取内容兜底

Usage:
  python scripts/pipeline/scrape_toutiao.py <帖子链接> [--name <博主名>] [--since YYYY-MM-DD] [--force]
"""

import json
import sys
import time
import os
import argparse
from playwright.sync_api import sync_playwright
from datetime import datetime

# Windows GBK 控制台兼容：输出含 emoji/中文
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
DATA_DIR = os.path.join(PROJECT_ROOT, "data", "posts")
os.makedirs(DATA_DIR, exist_ok=True)

user_info = {}
all_posts = []
raw_api_calls = []  # for debugging


def intercept_response(response):
    """拦截API响应（用于调试）"""
    url = response.url
    if "/api/pc/" in url:
        try:
            body = response.json()
            raw_api_calls.append({"url": url, "data": body})
        except:
            pass


def call_api_in_browser(page, max_behot_time=0, category="profile_all", token=None):
    """在浏览器内通过JS调用API"""
    js_code = f"""
        async () => {{
            const params = new URLSearchParams({{
                category: '{category}',
                token: '{token}',
            }});
            if ({max_behot_time}) {{
                params.append('max_behot_time', '{max_behot_time}');
            }}
            const url = 'https://www.toutiao.com/api/pc/list/user/feed?' + params.toString();
            try {{
                const resp = await fetch(url, {{
                    method: 'GET',
                    credentials: 'include',
                }});
                const data = await resp.json();
                return data;
            }} catch(e) {{
                return {{error: e.message}};
            }}
        }}
    """
    try:
        result = page.evaluate(js_code)
        return result
    except Exception as e:
        print(f"  JS API调用异常: {e}")
        return None


def parse_items(data):
    """解析feed API返回"""
    posts = []
    items = data.get("data", [])
    if not items:
        return posts
    for item in items:
        if not item or not isinstance(item, dict):
            continue
        if not isinstance(item, dict):
            continue

        content = item.get("content") or item.get("title") or item.get("abstract") or ""
        if isinstance(content, dict):
            content = content.get("text") or content.get("title") or str(content)
        if not content:
            share = item.get("itemCell", {}).get("shareInfo", {})
            content = share.get("title", "")
        if isinstance(content, dict):
            content = str(content)

        if not content or not isinstance(content, str) or len(content.strip()) < 5:
            continue

        # 使用 publish_time（真正的发布时间），fallback到create_time，最后behot_time
        pub_time = item.get("publish_time") or item.get("create_time") or item.get("behot_time", 0)
        if isinstance(pub_time, (int, float)):
            if pub_time > 1e12:
                pub_time = int(pub_time / 1000)
        else:
            pub_time = 0

        post_id = str(
            item.get("id") or
            item.get("thread_id_str") or
            item.get("item_id") or
            item.get("log_pb", {}).get("group_id_str", "") or
            ""
        )

        share_url = (
            item.get("share_url") or
            item.get("itemCell", {}).get("shareInfo", {}).get("shareURL", "") or
            (f"https://www.toutiao.com/w/{post_id}/" if post_id else "")
        )

        # 提取用户信息（统计 feed 中出现最多的用户作为账号主体，防止被转载内容覆盖）
        user_data = item.get("user", {})
        user_name = user_data.get("name", "") if user_data else ""
        if user_name:
            user_info.setdefault("_user_counter", {})
            user_info["_user_counter"][user_name] = user_info["_user_counter"].get(user_name, 0) + 1
            user_info["_user_detail"] = user_info.get("_user_detail", {})
            user_info["_user_detail"][user_name] = {
                "user_id": str(user_data.get("id", "")),
                "description": user_data.get("desc", ""),
            }

        posts.append({
            "post_id": post_id,
            "user": user_data.get("name", "") if user_data else "",
            "content": content.strip(),
            "publish_time": int(pub_time) if pub_time else 0,
            "publish_date": datetime.fromtimestamp(pub_time).strftime("%Y-%m-%d %H:%M") if pub_time else "",
            "url": share_url,
            "digg_count": item.get("digg_count", 0),
            "comment_count": item.get("comment_count", 0),
            "read_count": item.get("read_count", 0) or item.get("display_count", 0),
        })
    return posts


def main():
    global all_posts
    parser = argparse.ArgumentParser(description="爬取今日头条博主全部帖子")
    parser.add_argument("url", nargs="?", default="", help="博主任意帖子链接")
    parser.add_argument("--name", "-n", default="", help="博主名称（可选，不提供则自动检测）")
    parser.add_argument("--since", default="", help="只爬取该日期之后(含)的帖子，如 2026-01-01（默认全量）")
    parser.add_argument("--force", action="store_true", help="异常停（未覆盖到 --since）时也强制覆盖保存（默认拒绝覆盖，防截断数据落盘）")
    args = parser.parse_args()

    post_url = args.url or "https://www.toutiao.com/w/1872013328886923/"
    explicit_name = args.name.strip() if args.name else ""
    since_ts = 0
    if args.since:
        since_ts = int(datetime.strptime(args.since, "%Y-%m-%d").timestamp())
        print(f"  只爬取 {args.since} 之后的帖子")

    print("=" * 60)
    print("今日头条帖子爬虫 v5 - 浏览器内API调用")
    print("=" * 60)

    # OUTPUT_FILE is computed after we detect the name
    output_file = None

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        )
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        page = context.new_page()
        page.on("response", intercept_response)

        # === 步骤1: 提取token（优先从 URL 直接解析，避免页面链接指向他人主页） ===
        print("\n[1] 提取 token...")
        import re
        token = ""
        m = re.search(r'/c/user/token/([A-Za-z0-9_=-]{30,})', post_url)
        if m:
            token = m.group(1)
            profile_href = f"https://www.toutiao.com/c/user/token/{token}/"
            print(f"  从URL提取 token: {token[:60]}...")
        else:
            # URL 无 token（普通帖子链接）→ 加载页面，从页面链接提取
            print("  URL 无 token，加载页面提取...")
            page.goto(post_url, timeout=30000, wait_until="domcontentloaded")
            time.sleep(3)

            # 等待用户主页链接出现
            try:
                page.wait_for_selector('a[href*="/c/user/token/"]', timeout=10000)
            except:
                pass
            time.sleep(2)

            # 获取用户主页链接
            profile_href = page.evaluate("""
                () => {
                    const links = document.querySelectorAll('a[href*="/c/user/token/"]');
                    return links.length > 0 ? links[0].href : '';
                }
            """)
            print(f"  用户主页: {profile_href[:100]}...")

            if not profile_href:
                print("  ❌ 找不到用户主页链接，尝试从HTML提取...")
                html = page.content()
                m = re.search(r'/c/user/token/([A-Za-z0-9_=-]{30,})/', html)
                if m:
                    profile_href = f"https://www.toutiao.com/c/user/token/{m.group(1)}/"
                    print(f"  从HTML提取: {profile_href[:100]}...")

            token = profile_href.split("/c/user/token/")[1].split("/")[0].split("?")[0] if profile_href else ""
            print(f"  Token: {token[:60]}...")

        if not token:
            print("  ❌ 未能提取到用户 token，退出（不抓取推荐流）")
            browser.close()
            return

        # 无论 token 从哪来，都先访问一次原始链接以建立 cookie（feed API 依赖）
        print("  访问原始链接建立 cookie...")
        page.goto(post_url, timeout=30000, wait_until="domcontentloaded")
        time.sleep(3)
        # 等待重定向链结束、页面稳定（m_redirect 会自我跳转一次）
        try:
            page.wait_for_load_state('networkidle', timeout=20000)
        except Exception:
            pass
        time.sleep(3)

        # 从页面标题获取博主名（仅作后备，API数据更可靠会覆盖）

        # === 步骤2: 访问用户主页 ===
        # 若 post_url 本身就是用户主页（含 token），步骤1 已访问过，跳过以避免跳转链冲突
        if profile_href and '/c/user/token/' not in post_url:
            print("\n[2] 访问用户主页...")
            page.goto(profile_href, timeout=30000, wait_until="domcontentloaded")
            time.sleep(4)
        else:
            print("\n[2] 已在用户主页（post_url 含 token），等待页面稳定...")
            time.sleep(4)

        # === 步骤3: 浏览器内翻页 ===
        print("\n[3] 浏览器内API翻页获取帖子...")
        seen_ids = set()
        max_behot_time = 0
        target = 5000
        max_pages = 200

        empty_streak = 0  # 连续空页计数
        stop_reason = ""  # 翻页停止原因，写入 result 供 verify_posts.py 二次检查判读
        for page_num in range(1, max_pages + 1):
            # 页面仍可能被重定向链导航，API 调用失败时短暂等待后重试
            result = None
            for attempt in range(3):
                try:
                    result = call_api_in_browser(page, max_behot_time=max_behot_time, token=token)
                except Exception as e:
                    print(f"  第{page_num}页: JS调用异常({attempt + 1}/3) - {e}")
                if result and "error" not in result:
                    break
                time.sleep(3)
            if not result:
                print(f"  第{page_num}页: API返回空，停止")
                stop_reason = "api_empty"
                break
            if "error" in result:
                print(f"  第{page_num}页: JS错误 - {result['error']}，停止")
                stop_reason = "js_error"
                break
            if result.get("message") != "success":
                print(f"  第{page_num}页: 状态异常，停止")
                stop_reason = "status_abnormal"
                break

            try:
                new_posts = parse_items(result)
            except Exception as e:
                print(f"  第{page_num}页: 解析错误 - {e}，跳过")
                continue
            new_count = 0
            for p in new_posts:
                pid = p["post_id"]
                if pid and pid not in seen_ids:
                    seen_ids.add(pid)
                    all_posts.append(p)
                    new_count += 1

            has_more = result.get("has_more", False)
            next_info = result.get("next", {}) or {}
            next_max = next_info.get("max_behot_time", 0)

            # 进度
            min_t = min((p["publish_time"] for p in new_posts), default=0)
            date_str = datetime.fromtimestamp(min_t).strftime("%Y-%m-%d") if min_t else "?"

            if new_count == 0:
                empty_streak += 1
            else:
                empty_streak = 0

            print(f"  第{page_num}页: +{new_count}条 | 累计{len(all_posts)}条 | 最远{date_str} | more={has_more} | 空页连续{empty_streak}")

            # 只爬 --since 之后的帖子：本页最远时间已早于 since → 后续页全为更早，停止
            if since_ts and min_t and min_t < since_ts:
                print(f"  已到起始日期 {args.since} 之前的帖子（本页最远 {date_str}），翻页结束")
                stop_reason = "since"
                break

            if len(all_posts) >= target:
                print("  达到目标数量，翻页结束")
                stop_reason = "target"
                break

            if not has_more and empty_streak >= 3:
                print("  has_more=False且连续空页，翻页结束")
                stop_reason = "no_more"
                break

            if empty_streak >= 5:
                print("  连续5页无新帖，翻页结束")
                stop_reason = "empty_streak"
                break

            if not next_max:
                print("  无下一页游标，翻页结束")
                stop_reason = "no_cursor"
                break

            max_behot_time = next_max
            time.sleep(1)

        if not stop_reason:
            stop_reason = "max_pages"  # 翻页到上限未触发停止条件

        # === 步骤4: 提取用户统计 ===
        print("\n[4] 提取用户统计...")
        for call in raw_api_calls:
            if "fans_stat" in call["url"]:
                d = call["data"].get("data", {})
                user_info["followers"] = d.get("fans", "")
                user_info["digg_count"] = d.get("digg_count", "")
                user_info["following"] = d.get("following", "")
                print(f"  粉丝: {user_info['followers']}, 获赞: {user_info['digg_count']}")
                break

        # 从页面提取
        if not user_info.get("followers"):
            stats = page.evaluate("""
                () => {
                    const el = document.querySelector('[class*="fans"], [class*="follow"]');
                    return el ? el.innerText : '';
                }
            """)
            if stats:
                print(f"  DOM提取统计: {stats}")

        # === 步骤5: 保存 ===
        print("\n[5] 保存结果...")
        if not all_posts:
            print("  ❌ 未抓取到任何帖子，不保存（避免覆盖已有数据）。请检查网络/页面状态后重试。")
            browser.close()
            return
        times = [p["publish_time"] for p in all_posts if p["publish_time"]]

        # 确定 feed 主体用户（出现次数最多者）
        counter = user_info.pop("_user_counter", {})
        user_detail = user_info.pop("_user_detail", {})
        dominant = max(counter, key=counter.get) if counter else ""
        if dominant:
            user_info["name"] = dominant
            user_info.update(user_detail.get(dominant, {}))
            others = {k: v for k, v in sorted(counter.items(), key=lambda kv: -kv[1]) if k != dominant}
            if others:
                user_info["_other_users_in_feed"] = others

        # 帖子级过滤：feed 中混入他人帖子时，只保留主体用户的帖子
        with_user = [p for p in all_posts if p.get("user")]
        if with_user and dominant:
            before = len(all_posts)
            all_posts = [p for p in all_posts if not p.get("user") or p["user"] == dominant]
            if len(all_posts) < before:
                print(f"  按主体用户「{dominant}」过滤：{before} → {len(all_posts)} 条")
        for p in all_posts:
            p.pop("user", None)

        # Determine output filename: explicit --name > auto-detected name > fallback
        blogger_name = explicit_name or user_info.get("name", "").strip()
        if blogger_name:
            output_file = os.path.join(DATA_DIR, f"{blogger_name}.json")
        else:
            output_file = os.path.join(DATA_DIR, "posts.json")

        # 名称校验：feed 主体用户与 --name 不一致时，不覆盖目标文件（防止抓错账号毁数据）
        if explicit_name and dominant and dominant != explicit_name:
            output_file = os.path.join(DATA_DIR, f"{explicit_name}_feed_check.json")
            print(f"  ⚠️ 警告：feed 主体用户是「{dominant}」，与 --name「{explicit_name}」不一致，请检查 token/链接是否正确！")
            print(f"  ⚠️ 结果另存为 {output_file}，不覆盖 {explicit_name}.json")

        result = {
            "scrape_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source_url": post_url,
            "stop_reason": stop_reason,
            "user_info": user_info,
            "total_posts": len(all_posts),
            "time_range": {
                "earliest": datetime.fromtimestamp(min(times)).strftime("%Y-%m-%d") if times else "",
                "latest": datetime.fromtimestamp(max(times)).strftime("%Y-%m-%d") if times else "",
            },
            "posts": sorted(all_posts, key=lambda x: x["publish_time"], reverse=True),
        }

        # 覆盖前保护：--since 未覆盖到起点且异常停 → 拒绝覆盖（防截断数据静默落盘）
        TRUNCATED = {"api_empty", "js_error", "status_abnormal", "empty_streak", "max_pages"}
        min_ts = min((p["publish_time"] for p in all_posts if p["publish_time"]), default=0)
        if since_ts and min_ts > since_ts and stop_reason in TRUNCATED and not args.force:
            print(f"  ❌ 异常停（stop_reason={stop_reason}），最远只到 "
                  f"{datetime.fromtimestamp(min_ts).strftime('%Y-%m-%d')}，未覆盖到 --since {args.since}")
            print(f"    拒绝覆盖 {output_file}（数据可能截断）。确认后加 --force 强制覆盖，或重试爬取。")
            browser.close()
            sys.exit(1)
        elif stop_reason in TRUNCATED:
            print(f"  ⚠️ 停止原因={stop_reason}（异常停）：建议运行 python scripts/utils/verify_posts.py <博主名> 二次检查")

        # 保存前备份旧文件，防止覆盖丢失历史数据
        if output_file and os.path.exists(output_file):
            backup_dir = os.path.join(DATA_DIR, "_backup")
            os.makedirs(backup_dir, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = os.path.join(backup_dir, f"{blogger_name}_{ts}.json")
            import shutil
            shutil.copy2(output_file, backup_file)
            print(f"  旧文件已备份到 {backup_file}")

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        print(f"\n{'=' * 60}")
        print(f"爬取完成!")
        print(f"用户: {user_info.get('name', '未知')}")
        print(f"粉丝: {user_info.get('followers', '未知')}")
        print(f"帖子数: {len(all_posts)}")
        if times:
            print(f"时间: {datetime.fromtimestamp(min(times)).strftime('%Y-%m-%d')} ~ {datetime.fromtimestamp(max(times)).strftime('%Y-%m-%d')}")
        print(f"结果: {output_file}")
        print(f"{'=' * 60}")

        browser.close()


if __name__ == "__main__":
    main()
