# -*- coding: utf-8 -*-
import json
base = r'd:/claude_code_ana/blogger-analysis'
sig = [
 {"pub":"2026-03-03 11:01","d":1,"s":1,"idx":"上证指数","spec":"yearend","summary":"大盘调整不用担心,今年就是大牛市","cat":"scored"},
 {"pub":"2026-03-05 05:42","d":1,"s":1,"idx":"上证指数","spec":"t5","summary":"市场情绪疯狂杀跌,外围都在救市,静等大奇迹日","cat":"scored"},
 {"pub":"2026-03-06 08:27","d":1,"idx":"上证指数","summary":"股市向上还有空间,大环境需要股市起来","cat":"无预测周期"},
 {"pub":"2026-03-11 16:04","d":-1,"idx":"上证指数","summary":"不断洗盘不断暴跌,但整体向上","cat":"无预测周期"},
 {"pub":"2026-03-21 13:38","d":-1,"s":1,"idx":"上证指数","spec":"month","summary":"月线级别调整,后面大涨","cat":"scored"},
 {"pub":"2026-03-21 21:57","d":-1,"s":1,"idx":"上证指数","spec":"month","summary":"月线级别调整,然后继续上涨","cat":"scored"},
 {"pub":"2026-03-22 05:49","d":-1,"s":1,"idx":"上证指数","spec":"month","summary":"股市月线级别调整,但这是最佳交易机会","cat":"scored"},
 {"pub":"2026-03-22 06:25","d":-1,"s":1,"idx":"上证指数","spec":"month","summary":"月线级别调整,然后继续上涨","cat":"scored"},
 {"pub":"2026-03-23 05:15","d":-1,"s":1,"idx":"上证指数","spec":"month","summary":"A股横盘3月,月线调整箭在弦上","cat":"scored"},
 {"pub":"2026-03-25 08:11","d":-1,"s":1,"idx":"上证指数","spec":"t5","summary":"调整一下,来个暴跌血洗,接着稳住缓慢上涨","cat":"scored"},
 {"pub":"2026-03-27 05:12","d":-1,"s":1,"idx":"上证指数","spec":"t5","summary":"涨势后继乏力,A股静待暴跌,四月下影线筑底","cat":"scored"},
 {"pub":"2026-03-27 08:04","d":-1,"s":1,"idx":"上证指数","spec":"t5","summary":"再来一个大暴跌就差不多了,搞个100点以上","cat":"scored"},
 {"pub":"2026-03-29 04:46","d":-1,"s":1,"idx":"上证指数","spec":"t5","summary":"大盘存在暴跌可能性","cat":"scored"},
 {"pub":"2026-03-30 04:40","d":-1,"s":1,"idx":"上证指数","spec":"month","summary":"关注股市月线级别风险,这是机会","cat":"scored"},
 {"pub":"2026-03-31 08:15","d":-1,"s":1,"idx":"上证指数","spec":"t5","summary":"就这里调整,一个暴跌后继续上涨","cat":"scored"},
 {"pub":"2026-04-05 05:06","d":-1,"idx":"上证指数","summary":"3800点抄底,4600点兑现:A股终极路线图","cat":"目标点位"},
 {"pub":"2026-04-09 21:29","d":-1,"s":1,"idx":"上证指数","spec":"month","summary":"月线级调整倒计时,静待4600点行情启动","cat":"scored"},
 {"pub":"2026-04-13 04:27","d":-1,"s":1,"idx":"上证指数","spec":"t5","summary":"A股调整没走完","cat":"scored"},
 {"pub":"2026-04-23 05:20","d":-1,"s":1,"idx":"上证指数","spec":"t5","summary":"4100点小心调整陷阱","cat":"scored"},
 {"pub":"2026-05-26 08:10","d":1,"idx":"上证指数","summary":"现在的股市是大牛市,拿住不要动","cat":"无预测周期"},
 {"pub":"2026-06-08 22:04","d":1,"s":1,"idx":"上证指数","spec":"t5","summary":"大跌形成恐慌,典型恐慌盘,机会已经来了","cat":"scored"},
 {"pub":"2026-06-27 08:54","d":1,"idx":"上证指数","summary":"当市场吵成一团,根本不会出现大顶","cat":"无预测周期"},
 {"pub":"2026-07-17 16:33","d":1,"s":1,"idx":"上证指数","spec":"t5","summary":"今天是机会,本人今天加仓了","cat":"scored"},
]
fp = base + '/data/direction_signals/道术合一.json'
import os
if os.path.exists(fp):
    d = json.load(open(fp, encoding='utf-8'))
else:
    d = {'blogger': '道术合一', 'signals': []}
keys = {(s['pub'], s.get('spec'), s.get('idx')) for s in d['signals']}
added = 0
for s in sig:
    if (s['pub'], s.get('spec'), s.get('idx')) in keys:
        continue
    d['signals'].append(s)
    added += 1
d['signals'].sort(key=lambda x: x['pub'])
json.dump(d, open(fp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print('新增', added, '条, 现共', len(d['signals']), '条')
