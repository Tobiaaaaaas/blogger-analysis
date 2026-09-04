# -*- coding: utf-8 -*-
"""research/validate.py — 回测验证（B4）：无未来函数 / poll 可复现 / 填单模式与费率敏感性。

用法：python -m research.validate
"""
import datetime
import json

from . import config
from . import backtest as bt
from . import poll as pollmod


def audit_no_future_and_determinism(board, allow_short=False):
    """全档位复算 poll，逐行与 ticks 比对；并审计无未来函数（投票 pub 严格早于决策时刻）。"""
    idx = pollmod.CorpusIndex()
    res1 = bt.run(board, index=idx, allow_short=allow_short)
    res2 = bt.run(board, index=idx, allow_short=allow_short)   # 二次运行复现
    same = (res1["ticks"] == res2["ticks"] and res1["trades"] == res2["trades"]
            and res1["daily"] == res2["daily"])
    assert same, f"{board}: 二次运行结果不一致（非确定性！）"
    bad_pub = bad_snap = 0
    checked = 0
    for t in res1["ticks"]:
        dt = datetime.datetime.strptime(t["dt"], "%Y-%m-%d %H:%M:%S%z")
        snap = pollmod.poll_tick(idx, board, dt)
        checked += 1
        # 无未来函数：所有计入票的 pub < 决策时刻
        for v in snap["votes"]:
            if v["pub"] >= t["dt"]:
                bad_pub += 1
        # poll 复算与 run() 行一致（防行级漂移）；trigger 为内存 bool，勿与字符串比较
        if (snap["expressed"], snap["bull"], snap["bear"], snap["mixed"], snap["clean"],
                snap["trigger_long"], snap["trigger_short"]) != (
                    int(t["expressed"]), int(t["bull"]), int(t["bear"]),
                    int(t["mixed"]), True, bool(t["trigger"]), bool(t["trigger_short"])):
            bad_snap += 1
    tag = "双向" if allow_short else "做多"
    print(f"[{board}·{tag}] 档位 {len(res1['ticks'])}：poll 复算一致 {checked - bad_snap}/{checked}，"
          f"投票 pub≥决策时刻 {bad_pub} 处"
          + (" ✓ 无未来函数" if bad_pub == 0 and bad_snap == 0 else " ✗ 异常"))
    return res1, bad_pub == 0 and bad_snap == 0


def sensitivity(allow_short=False):
    """fill instant vs delayed、费率 0 vs 0.0005 → 头部指标对比表（默认只做多，allow_short 时双向）。"""
    idx = pollmod.CorpusIndex()
    rows = []
    for b in ("short", "swing"):
        for fm in ("instant", "delayed"):
            for cost in (0.0, 0.0005):
                r = bt.run(b, cost=cost, fill_mode=fm, index=idx, allow_short=allow_short)
                s = r["stats"]
                rows.append((b, fm, cost, s["total_return"], s["buyhold_return"],
                             s["sharpe"], s["max_drawdown"]))
    print(f"\n=== 敏感性 {('双向' if allow_short else '做多')}（fill 模式 × 费率）===")
    print(f"{'板块':<6}{'fill':<9}{'费率':<8}{'策略累计':<10}{'基准':<9}{'Sharpe':<8}{'MDD':<8}")
    for b, fm, c, tr, bh, sp, mdd in rows:
        print(f"{b:<6}{fm:<9}{c:<8}{tr*100:+7.2f}%  {bh*100:+7.2f}%  {sp:6.2f}  {mdd*100:5.2f}%")
    return rows


if __name__ == "__main__":
    ok = True
    for b in ("short", "swing"):
        for as_ in (False, True):
            _r, good = audit_no_future_and_determinism(b, allow_short=as_)
            ok = ok and good
    sensitivity()
    sensitivity(allow_short=True)
    print("\n结论：", "全部通过 ✓" if ok else "存在异常 ✗ 请检查")
