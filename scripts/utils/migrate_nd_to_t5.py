# -*- coding: utf-8 -*-
"""存量 nd → t5 机械重映射（无周期方向并入 t5，SKILL 2026-09-02）

背景：nd 编码把「无周期但有方向」独立成档（未来 5 个交易日收益率单点计分，
不参与总榜/多空/三档）。SKILL 改为：无预测周期但有明确预测方向 → 编码 t5，
验证终点 = 信号日之后第 5 个交易日，正常计分并参与多空/两档。
nd 的 r5 计分公式与 t5 标准公式数值完全一致（同一参考价 §4、同一终点日
第 5 个交易日、同一 score = d × return × 100），因此存量 nd 机械 remap 为 t5 无损。

用法: python scripts/utils/migrate_nd_to_t5.py [--dry-run]
输出: 每个文件的改动数 + 总改动数（预期 ≈ 存量 nd 数）
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
            if s.get('spec') == 'nd':
                s['spec'] = 't5'   # cat/s/d 不变；引擎按 t5 标准路径计分（同一终点、同一公式）
                n += 1
        if n:
            total += n
            print(f'{fn[:-5]:<24} {n:>5} 条 nd→t5')
            if not DRY:
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                    f.write('\n')
    print(f'\n{"[dry-run] " if DRY else ""}总改动 {total} 条（{len(files)} 个文件）')


if __name__ == '__main__':
    main()
