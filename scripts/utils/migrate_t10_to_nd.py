# -*- coding: utf-8 -*-
"""存量 t10 → nd 机械重映射（无周期方向单列改造，SKILL 2026-09-01）

背景：t10 编码同时表达「10天后」与「无周期兜底」两种语义，且无周期信号混入
"6个交易日及以上"三档污染有明确时间点的样本。改造后：
- spec=nd：无明确预测周期但有明确方向 → 信号日后第 1~10 个交易日窗口等权计分，
  独立成档（不参与总榜/多空/三档），抄底/逃顶保留。
- spec=t10：仅表示「10天后」（未来提取才可能产生；存量 t10 的 summary 中
  "10天后/十天后"出现 0 次，99.7% 不含任何天数 → 全部 t10 机械 remap 为 nd，无损）。

用法: python scripts/utils/migrate_t10_to_nd.py [--dry-run]
输出: 每个文件的改动数 + 总改动数（预期 ≈ 存量 t10 数）
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(ROOT, 'data', 'direction_signals')

DRY = '--dry-run' in sys.argv


def main():
    files = sorted(f for f in os.listdir(DATA_DIR) if f.endswith('.json') and not f.startswith('_'))
    total = 0
    for fn in files:
        path = os.path.join(DATA_DIR, fn)
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        signals = data.get('signals', [])
        n = 0
        for s in signals:
            if s.get('spec') == 't10':
                s['spec'] = 'nd'   # cat/s/d 不变；引擎按 nd 分支（10 日窗口等权）计分
                n += 1
        if n:
            total += n
            print(f'{fn[:-5]:<24} {n:>5} 条 t10→nd')
            if not DRY:
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                    f.write('\n')
    print(f'\n{"[dry-run] " if DRY else ""}总改动 {total} 条（{len(files)} 个文件）')


if __name__ == '__main__':
    main()
