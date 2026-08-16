# -*- coding: utf-8 -*-
"""枫叶补标注辅助：按日期区间导出 标题+正文，供 LLM 批量标注。
用法: python _fy_export.py 2026-03-15 2026-04-30
输出: _fy_batch_<起>_<止>.txt（每条: [N] 日期时间 | 标题 + 正文）
"""
import json, os, sys

BASE = os.path.dirname(os.path.abspath(__file__))
d0, d1 = sys.argv[1], sys.argv[2]

posts = json.load(open(os.path.join(BASE, 'data/posts/枫叶.json'), encoding='utf-8'))
posts = sorted(posts['posts'], key=lambda p: p['publish_date'])

bodies = {}
for f in ['枫叶_bodies_s0.json', '枫叶_bodies_s1.json', '枫叶_bodies_s2.json', '枫叶_bodies.json']:
    fp = os.path.join(BASE, 'data/posts', f)
    if os.path.exists(fp):
        d = json.load(open(fp, encoding='utf-8'))
        for pid, v in d.items():
            b = v.get('body', '')
            if len(b) > 20:
                bodies[v.get('url', '')] = b
            elif v.get('body'):
                bodies[v.get('url', '')] = b

out = []
n = 0
for p in posts:
    if not (d0 <= p['publish_date'][:10] <= d1):
        continue
    n += 1
    body = bodies.get(p['url'], '')
    txt = body if body else p['content']
    out.append(f"[{n}] {p['publish_date']} | {txt.strip()}\n")
    if len(p['content']) >= 60 and p['content'] != txt:
        out.append(f"    (列表页原文: {p['content'].strip()})\n")

opf = os.path.join(BASE, f'_fy_batch_{d0}_{d1}.txt')
with open(opf, 'w', encoding='utf-8') as f:
    f.write(f'枫叶帖子 {d0} ~ {d1}，共 {n} 条\n{"="*60}\n\n')
    f.writelines(out)
print(f'导出 {n} 条 -> {opf}')
