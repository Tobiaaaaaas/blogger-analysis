# -*- coding: utf-8 -*-
"""分片抓取正文：python scripts/utils/fetch_bodies_shard.py <博主名> <shard> <总片数> [起始日期YYYY-MM-DD]

抓取策略（2026-08-30 重构，规避"当前网络环境无法查看"风控墙）：
- 文章帖（group/<id> / article/<id> / i<id> 非微头条）：用 m.toutiao.com/i<id>/info/ JSON API，
  单请求即返回全文，绕过 SSR 页面墙（该墙按请求量触发、刷新后旧帖新帖都会拦，info API 不受影响）。
- 微头条（w/<id> 形态）：m.toutiao.com/i<id>/ 移动端 SSR 渲染（info API 对微头条返回空）。
- 视频帖：直接标记 [视频帖] 跳过。
- 风控识别：正文含"当前网络环境无法查看"视为未抓成功，记录空串（占位符，下次重跑会重试），
  绝不当成有效正文（否则 merge 会因 len>20 误存为正文）。
"""
import json, os, re, sys, time, html as htmlmod
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 视频帖标记：无文字正文，抓取时直接跳过。写入 done 使后续重跑不再访问该 URL；
# 合并/提取脚本因 len<=20 会把它当占位符忽略，不会回填成正文。
VIDEO_MARKER = '[视频帖]'
WALL_TEXT = '当前网络环境无法查看'

# 移动端 SSR 的 iPhone UA（info API 同样适用）
MOBILE_UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1'


def _pid_of(u):
    # 兼容 feed 接口新 URL 形态：m.toutiaoimg.cn/a<id>（文章 CDN 直链）与 /group/<id>
    m = (re.search(r'/(?:group|article|w)/(\d+)', u)
         or re.search(r'/(?:a|i)(\d+)', u)
         or re.search(r'/i(\d+)', u))
    return m.group(1) if m else None


def _is_microheadline_url(u):
    """微头条 share URL 形态：m.toutiao.com/w/<id> 或 www.toutiao.com/w/<id>。"""
    return bool(re.search(r'/(?:w)/\d+', u or ''))


def _extract_info_content(j):
    """从 info API JSON 提取纯文本正文；无正文返回空串。"""
    try:
        d = j.get('data') or {}
        content = d.get('content') or ''
    except Exception:
        return ''
    if not content or len(content) <= 20:
        return ''
    if WALL_TEXT in content:
        return ''
    # HTML → 纯文本：去标签、去空白行
    text = re.sub(r'<[^>]+>', '', content)
    text = htmlmod.unescape(text)
    text = re.sub(r'[ \t　]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    return text


name, shard, nshards = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
MIN_DATE = sys.argv[4] if len(sys.argv) > 4 else '2026-01-01'
with open(os.path.join(ROOT, 'data', 'posts', f'{name}.json'), encoding='utf-8') as f:
    data = json.load(f)
OUT = os.path.join(ROOT, 'data', 'posts', f'{name}_bodies_s{shard}.json')
done = json.load(open(OUT, encoding='utf-8')) if os.path.exists(OUT) else {}
posts = sorted(data['posts'], key=lambda p: p['publish_date'])
def _placeholder(b):
    """登录墙文本视为未抓成功（手机登录/扫码登录/获取验证码），需要重抓。"""
    if '[视频帖]' in (b or ''):
        return False  # 视频帖已处理，绝不重试
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
    ctx = browser.new_context(viewport={'width': 390, 'height': 844},
                              user_agent=MOBILE_UA, locale='zh-CN')
    page = ctx.new_page()
    info_empty_run = 0  # info API 连续空响应计数（防风控）
    # 访问一次首页建立 cookie（info API 与 SSR 都用到）
    try:
        page.goto('https://www.toutiao.com/', timeout=12000, wait_until='domcontentloaded')
        page.wait_for_timeout(1000)
    except Exception:
        pass
    for i, post in enumerate(todo):
        pid = post['post_id']
        # 视频帖检测不再用 URL 域名判断：新爬虫把普通文章也存成 m.toutiaoimg.cn/<a|group>/<id>
        # 形态，URL 含 m.toutiaoimg.cn 不代表视频。真视频由 SSR 页面含 xgplayer 标记兜底识别。
        body = ''
        if True:  # 所有帖（含微头条）先走 info API 快速路径：文本微头条可拿到正文，视频可标记 [视频帖]
            time.sleep(1.2)  # 节流
            try:
                api = f"https://m.toutiao.com/i{_pid_of(post['url'])}/info/"
                resp = ctx.request.get(api, timeout=10000,
                                       headers={'Referer': f"https://m.toutiao.com/i{_pid_of(post['url'])}/"})
                jdata = resp.json()
                body = _extract_info_content(jdata)
                # 视频帖：info API 返回 play_auth_token_v2（视频播放凭证）→ 无文字正文，标 [视频帖]
                if not body and (jdata.get('data') or {}).get('play_auth_token_v2'):
                    done[pid] = {'title': post['content'], 'body': VIDEO_MARKER, 'url': post['url']}
                    continue
            except Exception:
                body = ''
            if body:
                info_empty_run = 0
            else:
                # info API 空响应（少数文章/新帖）→ 计数，连续 5 次疑似风控则暂停 60s
                info_empty_run += 1
                if info_empty_run >= 5:
                    print(f'shard {shard}: info API 连续 {info_empty_run} 次空响应，暂停 60s 避风控', flush=True)
                    time.sleep(60)
                    info_empty_run = 0
        if not body:
            # SSR 路径（微头条 + info API 失败的文章）
            time.sleep(1.5)
            try:
                page.goto(f'https://m.toutiao.com/i{_pid_of(post["url"])}/', timeout=12000, wait_until='domcontentloaded')
                try:
                    page.wait_for_selector('article, .article-content, .tt-article-content, .syl-article-base, .article-text, .weitoutiao-content, .r-content, p', timeout=4000)
                except Exception:
                    try:
                        html = page.content()
                    except Exception:
                        html = ''
                    if 'xgplayer' in html:
                        done[pid] = {'title': post['content'], 'body': VIDEO_MARKER, 'url': post['url']}
                        continue
                    try:
                        page.wait_for_selector('article, .article-content, .tt-article-content, .syl-article-base, .article-text, .weitoutiao-content, .r-content, p', timeout=4000)
                    except Exception:
                        pass
                for sel in ['article', '.article-content', '.tt-article-content', '.syl-article-base', '.article-text', '.weitoutiao-content', '.r-content']:
                    el = page.query_selector(sel)
                    if el:
                        body = el.inner_text()
                        if len(body) > 20: break
                if not body:
                    # 注意：表达式会被 Playwright 包进模板字符串求值，\n 转义会变成真实换行导致 JS 语法错误，
                    # 所以换行符必须用 String.fromCharCode(10) 表达
                    body = page.eval_on_selector_all('p', 'els => els.map(e => e.innerText).join(String.fromCharCode(10))')
            except Exception:
                body = ''
        if WALL_TEXT in body:
            # 命中风控墙 → 记空串（占位符），下次重跑重试；绝不当作有效正文
            body = ''
        if body and '打开今日头条查看图片' in body and len(body) < 60:
            # 图片-only 微头条：SSR 只回图片占位文案，正文应保留标题而非占位符
            body = ''
        if body and '视频加载中' in body:
            # 视频帖 SSR 占位文案：无文字正文，标 [视频帖]
            done[pid] = {'title': post['content'], 'body': VIDEO_MARKER, 'url': post['url']}
            continue
        done[pid] = {'title': post['content'], 'body': body.strip(), 'url': post['url']}
        if (i+1) % 30 == 0:
            json.dump(done, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
            print(f'shard {shard}: {i+1}/{len(todo)}')
    browser.close()
json.dump(done, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
nok = sum(1 for v in done.values() if len(v.get('body','')) > 20)
print(f'shard {shard} 完成: {len(done)} 条, 正文非空 {nok}')
