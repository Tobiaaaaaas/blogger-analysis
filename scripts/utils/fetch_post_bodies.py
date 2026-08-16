# -*- coding: utf-8 -*-
"""
抓取博主的帖子正文（标题之外的文章正文）
用法: python scripts/utils/fetch_post_bodies.py <博主名>
输出: data/posts/<博主名>_bodies.json  {post_id: {title, body, url}}
断点续跑：已有的 post_id 跳过
"""
import json
import os
import sys
import time
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
POSTS = os.path.join(ROOT, 'data', 'posts')
OUT = os.path.join(POSTS, f'{sys.argv[1]}_bodies.json')

CONTENT_SELECTORS = [
    'article',
    '.article-content',
    '.tt-article-content',
    '.syl-article-base',
    '#article-content',
]


def main():
    name = sys.argv[1]
    with open(os.path.join(POSTS, f'{name}.json'), encoding='utf-8') as f:
        data = json.load(f)

    done = {}
    if os.path.exists(OUT):
        done = json.load(open(OUT, encoding='utf-8'))

    posts = sorted(data['posts'], key=lambda p: p['publish_date'])
    todo = [p for p in posts if p['post_id'] not in done and p['publish_date'] >= '2026-01-01']
    print(f'共 {len(posts)} 条，评估期 {len(todo)} 条待抓')

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=['--disable-blink-features=AutomationControlled', '--no-sandbox'])
        ctx = browser.new_context(viewport={'width': 1920, 'height': 1080},
                                  user_agent='Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1',
                                  locale='zh-CN', timezone_id='Asia/Shanghai')
        page = ctx.new_page()
        ok = fail = 0
        for i, post in enumerate(todo):
            pid = post['post_id']
            try:
                page.goto(post['url'], timeout=25000, wait_until='domcontentloaded')
                page.wait_for_timeout(1800)
                body = ''
                for sel in CONTENT_SELECTORS:
                    el = page.query_selector(sel)
                    if el:
                        body = el.inner_text()
                        if len(body) > 20:
                            break
                if not body:
                    # 兜底：收集文章区所有 p 标签
                    paras = page.eval_on_selector_all('p', 'els => els.map(e => e.innerText).join("\\n")')
                    body = paras
                done[pid] = {'title': post['content'], 'body': body.strip(), 'url': post['url']}
                ok += 1
                if len(body) > 20:
                    print(f'[{i+1}/{len(todo)}] {post["publish_date"][:10]} body {len(body)}字')
                else:
                    print(f'[{i+1}/{len(todo)}] {post["publish_date"][:10]} ⚠️ 正文为空')
            except Exception as e:
                fail += 1
                print(f'[{i+1}/{len(todo)}] {post["publish_date"][:10]} ❌ {str(e)[:60]}')
            if (i + 1) % 20 == 0:
                json.dump(done, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
                print(f'  进度保存 {len(done)} 条')
        browser.close()
    json.dump(done, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    empty = sum(1 for v in done.values() if len(v['body']) <= 20)
    print(f'完成：成功 {ok}，失败 {fail}，空正文 {empty}，共 {len(done)} 条 → {OUT}')


if __name__ == '__main__':
    main()
