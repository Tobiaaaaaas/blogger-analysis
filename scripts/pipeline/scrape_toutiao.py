"""
爬取今日头条博主帖子 — v5
策略：全部在Playwright内完成
1. 渲染用户主页
2. 用 page.evaluate() 在浏览器内调用 feed API（自带签名）
3. 翻页获取大量帖子
4. 页面内提取内容兜底

Usage:
  python scripts/scrape_toutiao.py <帖子链接> [--name <博主名>]
"""

import json
import sys
import time
import os
import argparse
from playwright.sync_api import sync_playwright
from datetime import datetime

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

        # 提取用户信息（API返回的用户名优先级最高，始终覆盖）
        user_data = item.get("user", {})
        if user_data and user_data.get("name"):
            user_info["name"] = user_data.get("name", "")
            user_info["user_id"] = str(user_data.get("id", ""))
            user_info["description"] = user_data.get("desc", "")

        posts.append({
            "post_id": post_id,
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
    parser = argparse.ArgumentParser(description="爬取今日头条博主全部帖子")
    parser.add_argument("url", nargs="?", default="", help="博主任意帖子链接")
    parser.add_argument("--name", "-n", default="", help="博主名称（可选，不提供则自动检测）")
    args = parser.parse_args()

    post_url = args.url or "https://www.toutiao.com/w/1872013328886923/"
    explicit_name = args.name.strip() if args.name else ""

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

        # === 步骤1: 加载帖子页面，提取token ===
        print("\n[1] 加载帖子页面...")
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
            import re
            m = re.search(r'/c/user/token/([A-Za-z0-9_=-]{30,})/', html)
            if m:
                profile_href = f"https://www.toutiao.com/c/user/token/{m.group(1)}/"
                print(f"  从HTML提取: {profile_href[:100]}...")

        # 提取token
        token = profile_href.split("/c/user/token/")[1].split("/")[0].split("?")[0] if profile_href else ""
        print(f"  Token: {token[:60]}...")

        # 从页面标题获取博主名（仅作后备，API数据更可靠会覆盖）

        # === 步骤2: 访问用户主页 ===
        print("\n[2] 访问用户主页...")
        if profile_href:
            page.goto(profile_href, timeout=30000, wait_until="domcontentloaded")
            time.sleep(4)

        # === 步骤3: 浏览器内翻页 ===
        print("\n[3] 浏览器内API翻页获取帖子...")
        seen_ids = set()
        max_behot_time = 0
        target = 5000
        max_pages = 200

        empty_streak = 0  # 连续空页计数
        for page_num in range(1, max_pages + 1):
            result = call_api_in_browser(page, max_behot_time=max_behot_time, token=token)
            if not result:
                print(f"  第{page_num}页: API返回空，停止")
                break
            if "error" in result:
                print(f"  第{page_num}页: JS错误 - {result['error']}，停止")
                break
            if result.get("message") != "success":
                print(f"  第{page_num}页: 状态异常，停止")
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

            if len(all_posts) >= target:
                print("  达到目标数量，翻页结束")
                break

            if not has_more and empty_streak >= 3:
                print("  has_more=False且连续空页，翻页结束")
                break

            if empty_streak >= 5:
                print("  连续5页无新帖，翻页结束")
                break

            if not next_max:
                print("  无下一页游标，翻页结束")
                break

            max_behot_time = next_max
            time.sleep(1)

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
        times = [p["publish_time"] for p in all_posts if p["publish_time"]]

        # Determine output filename: explicit --name > auto-detected name > fallback
        blogger_name = explicit_name or user_info.get("name", "").strip()
        if blogger_name:
            output_file = os.path.join(DATA_DIR, f"{blogger_name}.json")
        else:
            output_file = os.path.join(DATA_DIR, "posts.json")

        result = {
            "scrape_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source_url": post_url,
            "user_info": user_info,
            "total_posts": len(all_posts),
            "time_range": {
                "earliest": datetime.fromtimestamp(min(times)).strftime("%Y-%m-%d") if times else "",
                "latest": datetime.fromtimestamp(max(times)).strftime("%Y-%m-%d") if times else "",
            },
            "posts": sorted(all_posts, key=lambda x: x["publish_time"], reverse=True),
        }

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
