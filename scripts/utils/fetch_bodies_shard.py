# -*- coding: utf-8 -*-
"""分片抓取正文：python scripts/utils/fetch_bodies_shard.py <博主名> <shard> <总片数> [起始日期YYYY-MM-DD]"""
import json, os, re, sys
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _normalize_url(u):
    """归一化到桌面版 www.toutiao.com，避免跳转到需登录的移动站。
    兼容 group/<id> / article/<id> / w/<id> / i<id> 四种 URL 形态。"""
    m = re.search(r'/(?:group|article|w)/(\d+)', u) or re.search(r'/i(\d+)', u)
    if m:
        return f'https://www.toutiao.com/article/{m.group(1)}/'
    return u
name, shard, nshards = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
MIN_DATE = sys.argv[4] if len(sys.argv) > 4 else '2026-01-01'
with open(os.path.join(ROOT, 'data', 'posts', f'{name}.json'), encoding='utf-8') as f:
    data = json.load(f)
OUT = os.path.join(ROOT, 'data', 'posts', f'{name}_bodies_s{shard}.json')
done = json.load(open(OUT, encoding='utf-8')) if os.path.exists(OUT) else {}
posts = sorted(data['posts'], key=lambda p: p['publish_date'])
def _placeholder(b):
    """登录墙文本视为未抓成功（手机登录/扫码登录/获取验证码），需要重抓。"""
    if len(b) <= 20:
        return True
    if '登录' in b and '验证码' in b:
        return True
    return False

todo = [p for i, p in enumerate(posts) if i % nshards == shard
        and p['publish_date'] >= MIN_DATE and len(p['content']) < 60
        and (p['post_id'] not in done or _placeholder(done[p['post_id']].get('body', '')))]
print(f'shard {shard}: 待抓 {len(todo)} 条')
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=['--disable-blink-features=AutomationControlled', '--no-sandbox'])
    ctx = browser.new_context(viewport={'width': 1920, 'height': 1080},
                              user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
                              locale='zh-CN')
    page = ctx.new_page()
    for i, post in enumerate(todo):
        pid = post['post_id']
        try:
            page.goto(_normalize_url(post['url']), timeout=12000, wait_until='commit')
            # SPA 页面：等待正文内容渲染出来（最多 6s），比固定 sleep 更快更稳
            try:
                page.wait_for_selector('article, .article-content, .tt-article-content, .syl-article-base, .article-text, p', timeout=6000)
            except Exception:
                pass
            body = ''
            for sel in ['article', '.article-content', '.tt-article-content', '.syl-article-base', '.article-text']:
                el = page.query_selector(sel)
                if el:
                    body = el.inner_text()
                    if len(body) > 20: break
            if not body:
                # 注意：表达式会被 Playwright 包进模板字符串求值，\n 转义会变成真实换行导致 JS 语法错误，
                # 所以换行符必须用 String.fromCharCode(10) 表达
                body = page.eval_on_selector_all('p', 'els => els.map(e => e.innerText).join(String.fromCharCode(10))')
            done[pid] = {'title': post['content'], 'body': body.strip(), 'url': post['url']}
        except Exception as e:
            done[pid] = {'title': post['content'], 'body': '', 'url': post['url'], 'err': str(e)[:80]}
        if (i+1) % 30 == 0:
            json.dump(done, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
            print(f'shard {shard}: {i+1}/{len(todo)}')
    browser.close()
json.dump(done, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
nok = sum(1 for v in done.values() if len(v.get('body','')) > 20)
print(f'shard {shard} 完成: {len(done)} 条, 正文非空 {nok}')
