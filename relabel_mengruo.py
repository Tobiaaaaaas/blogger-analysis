#!/usr/bin/env python3
"""
Re-label time_horizon for ALL signals in 梦若神机.json
Key principle: identify the PREDICTION TARGET timeframe, not background context.
Approach: Only change labels when there's high-confidence evidence the current label is wrong.
"""

import json
import re
from datetime import datetime

def get_weekday(publish_time):
    """0=Monday, 6=Sunday"""
    try:
        dt = datetime.strptime(publish_time[:10], '%Y-%m-%d')
        return dt.weekday()
    except:
        return 3  # Default to Thursday

def classify(evidence, publish_time):
    """
    Determine prediction time horizon.
    Returns one of: intraday, short, weekly, biweekly, monthly, long, unspecified
    """
    ev = evidence
    wd = get_weekday(publish_time)

    # ================================================================
    # STEP 1: Check for SPECIFIC TODAY predictions → intraday
    # ================================================================
    # CRITICAL: "今天" must have a FUTURE prediction, not just description.
    # "今天会涨" = prediction, "今天跌了" = description.

    # Afternoon predictions (very clear intraday signal)
    afternoon_pred = [
        r'下午.{0,15}?(?:会|将|要|就会|一定会|必将|必定|肯定|必然|铁定|一定|大概率|很大概率|必|妥妥|稳稳).{0,10}?(?:涨|跌|反弹|调整|拉升|拉红|拉起来|涨起来|涨上去|会红|收阳|收红|翻红|V型|反转|微型反转|绝地反击|反攻|修复|更上一层楼|直冲云霄)',
        r'午后.{0,15}?(?:会|将|要|就会|一定会|必将|必定|肯定|必然|铁定|一定|大概率|很大概率|必).{0,10}?(?:涨|跌|反弹|调整|拉升|拉红|拉起来|涨上去|翻红|V型|反转|反攻|修复)',
        r'^下午.{0,10}?(?:会涨|要涨|要跌|会跌|必涨|必跌|V型|拉升|反弹|收阳|收红|翻红|会红|有看头)',
        r'^下午.{0,15}?(?:看涨|看跌|看反弹|看V|看修复)',
        r'下午.*?(?:V型|微型|绝地).*?(?:反转|反弹|反击)',
        r'坚信下午.*?(?:会|能|要|一定)',
        r'下午.*?(?:必定|必然|肯定|一定|铁定|绝对).*?(?:涨|跌|反弹|V|拉升)',
    ]

    for pat in afternoon_pred:
        if re.search(pat, ev):
            return 'intraday'

    # Morning → afternoon predictions
    am_pm_pred = [
        r'上午.{0,30}?下午.{0,10}?(?:会|将|要|就会|必然|一定|大概率|必定|铁定).{0,5}?(?:涨|跌|反弹|调整|拉升|V型|反转|翻红|收阳)',
        r'上午.*?(?:跌|杀|砸|洗).{0,20}?下午.*?(?:涨|拉|V|反弹|修复|回升|走强|翻红)',
        r'(?:早盘|开盘).{0,20}?(?:先|大幅|快速).{0,5}?(?:跌|杀|恐慌|跳水|低开).{0,15}?(?:后|然后|就会|就会|随后).{0,5}?(?:拉|涨|V|反弹|走高|回升)',
    ]

    for pat in am_pm_pred:
        if re.search(pat, ev):
            return 'intraday'

    # 今天 + FUTURE auxiliary → intraday
    today_future = [
        # 今天 + future auxiliary + direction
        r'今天.{0,25}?(?:会涨|会跌|必涨|必跌|要涨|要跌|一定会涨|一定会跌|必将上涨|必将下跌|必定会上涨|必定会下跌|肯定涨|肯定跌|还要涨|还要跌|能涨|能跌|继续涨|延续涨|延续跌)',
        r'今天.{0,25}?(?:大涨|大跌|暴涨|暴跌|反弹|翻红|翻绿|收阳|收阴|拉升|走高|上行|下行|走强|走弱|V型|低开高走|高开低走|高走|阳包阴|阴包阳|阴转晴|晴转阴)',
        r'今天.*(?:肯定|必然|必定|铁定|绝对|一定|妥妥|稳稳|大概率|很大概率).{0,10}?(?:涨|跌|反弹|调整|翻红|翻绿|收阳|收阴|拉升|走强|走弱)',
        r'今天.*(?:就是|就会|会是|将是).{0,10}?(?:涨|跌|反弹|调整|翻红|收阳|拉升|走强|走弱|V型|反转|大阳|大阴|突破)',
        r'今天.*?剧本.{0,10}?(?:低开高走|高开低走|V型|反弹|拉升|冲高回落)',
        r'今天.*?开盘.{0,10}?(?:会|将|要|就会|必然|一定|大概率|必定|铁定|很大概率).{0,5}?(?:涨|跌|反弹|调整|拉升|走强|走弱|跳水|低开|高开|V)',
        # Very direct: "今天会涨", "今天必涨"
        r'^.{0,5}今天.{0,15}?(?:会涨|会跌|必涨|必跌|要涨|要跌|大涨|大跌|暴涨|暴跌)$',
        r'^今天.{0,2}?(?:A股|大盘|盘面|市场|行情).{0,10}?(?:必涨|必跌|会涨|会跌|大涨|暴跌)',
        # "今天是/就是大涨"
        r'今天(?:是|就是|会是).{0,10}?(?:大涨的|大跌的|暴涨的|暴跌的|反弹的|调整的|普涨的)',
        # 今日 + future auxiliary
        r'今日.{0,25}?(?:会涨|会跌|必涨|必跌|要涨|要跌|一定会涨|一定会跌|必将上涨|必将下跌|肯定涨|肯定跌|还要涨|还要跌|继续涨|延续涨)',
        r'今日.{0,25}?(?:大涨|大跌|暴涨|暴跌|反弹|翻红|收阳|收阴|拉升|走高|上行|下行|走强|走弱|V型|低开高走|阳包阴)',
        r'今日.*(?:肯定|必然|必定|铁定|绝对|一定|大概率).{0,10}?(?:涨|跌|反弹|调整|翻红|收阳|收阴|拉升|走强|走弱)',
        r'今日.*(?:就是|就会|会是|将是).{0,10}?(?:涨|跌|反弹|调整|翻红|收阳|拉升|走强|走弱)',
        r'^今日.{0,2}?(?:A股|大盘|盘面|市场).{0,10}?(?:必涨|必跌|会涨|会跌|大涨|暴跌)',
        # Today should show certain pattern
        r'今天.*?(?:低开高走|高开低走|探底回升|触底反弹|冲高回落).{0,10}?(?:是|就是|会|将|大概率|很大概率)',
        r'今日.*?(?:低开高走|高开低走|探底回升|触底反弹|冲高回落).{0,10}?(?:是|就是|会|将|大概率|很大概率)',
        # "今天要高开" etc
        r'今天.{0,10}?(?:要涨|要跌|会涨|会跌|看涨|看跌).{0,30}$',
        # "今天指数不好看，但..." → prediction about today
        r'^今天.{0,15}?(?:指数|大盘|盘面|行情|个股).{0,15}?(?:不好|难看|难|不会|要|会|将)',
    ]

    for pat in today_future:
        if re.search(pat, ev):
            return 'intraday'

    # 上午 prediction about THIS morning (not a reference to past action)
    morning_future = [
        r'上午.{0,10}?(?:会|将|要|就会|一定会|必将|必定|肯定|必然|铁定|一定|大概率).{0,5}?(?:涨|跌|反弹|调整|拉升|V型|反转|收阳|翻红)',
        r'^上午.{0,15}?(?:看涨|看跌|会涨|会跌|V型)',
    ]

    for pat in morning_future:
        if re.search(pat, ev):
            return 'intraday'

    # 开盘 for today (without "明天" context)
    # "开盘如果大跌就是机会，一定会V起来的" - 开盘 defaults to today
    if re.search(r'开盘.{0,20}?(?:一定会|必定|肯定|绝对|必然|铁定).{0,10}?(?:V|涨|反弹|反转|拉升)', ev) and '明天' not in ev and '明日' not in ev:
        return 'intraday'

    # "今天/今日" at or very near the start + directional language → intraday default
    if (ev.startswith('今天') or ev.startswith('今日')) and len(ev) < 25:
        if re.search(r'(?:涨|跌|反弹|调整|拉升|走高|走低|走强|走弱|收阳|收阴|飘红|普涨|普跌|翻红|翻绿)', ev):
            return 'intraday'

    # ================================================================
    # STEP 2: Check for TOMORROW / WITHIN 3 DAYS predictions → short
    # ================================================================

    # 明天/明日 → short (always the strongest signal for short term)
    if re.search(r'明天|明日', ev):
        return 'short'

    # 节后第一天 → short
    if re.search(r'节后第一天|节后第1天', ev):
        return 'short'

    # 节后 + immediate implication → short
    if re.search(r'节后.{0,15}?(?:又|就|会|将|是|马上|很快).{0,10}?(?:逼空|大涨|暴跌|上涨|下跌|反弹|调整|行情|波动|开门红|高开)', ev):
        return 'short'

    # 后天 → short
    if re.search(r'后天', ev):
        return 'short'

    # 明后天 → short
    if re.search(r'明后天|明后两[天日]', ev):
        return 'short'

    # 下周一 → need to check context
    # From Thu(3)/Fri(4)/Sat(5)/Sun(6) → short (within 3 trading days)
    # From Mon(0)/Tue(1)/Wed(2) → weekly
    if re.search(r'下周一', ev):
        if wd >= 3:  # Thursday or later → only 1-3 calendar days = within 3 trading days
            return 'short'
        else:
            return 'weekly'

    # 周一 (without 下) - depends on whether it's this Monday or next Monday
    # If posted on weekend (Sat/Sun) → short (immediate next trading day)
    if re.search(r'(?:^|[^下])周一', ev):
        if wd >= 5:  # Saturday or Sunday → Monday is < 3 trading days
            return 'short'
        # Otherwise, Monday could be this week (already past) or next week
        # "周一A股XXX" → referring to upcoming Monday → check context
        if re.search(r'周一.{0,10}?(?:低开|高开|会涨|会跌|必涨|必跌|反弹|必定|肯定|必然|绝对|铁定)', ev):
            if wd >= 3:  # Thu onwards
                return 'short'
            return 'weekly'

    # 即将 / 马上 / 很快 → short
    if re.search(r'即将.{0,10}?(?:上涨|下跌|大涨|大跌|暴跌|反弹|调整|反转|变盘|突破|开启|迎来|启动)', ev):
        return 'short'
    if re.search(r'马上.{0,10}?(?:上涨|下跌|大涨|大跌|反弹|调整|开盘|就要|就会)', ev):
        return 'short'
    if re.search(r'很快.{0,10}?(?:会|将|就|要|就能).{0,5}?(?:涨|跌|反弹|调整|拉升|回落|站上|突破|修复|企稳)', ev):
        return 'short'

    # 短期/短线/近期 → short
    if re.search(r'短期(?:内|来看|来说)?(?:看涨|看跌|不看好|会涨|会跌|还会跌|还会涨|继续跌|继续涨)', ev):
        return 'short'
    if re.search(r'短线(?:层面|来说|来看|看涨|看跌|不看好|不碰|不乐观|没戏|彻底|回避)', ev):
        return 'short'
    if re.search(r'近期.{0,10}?(?:看涨|看跌|不看好|反弹|调整|上涨|下跌)', ev):
        return 'short'

    # 反弹一触即发 → short (imminent)
    if re.search(r'反弹.*?一触即发|一触即发.*?反弹', ev):
        return 'short'

    # 最后一跌 / 最后洗盘 (imminent implication) → short
    if re.search(r'最后(?:一跌|的洗盘|一次洗盘|的诱空|一次诱空)', ev):
        return 'short'

    # 就在明天
    if re.search(r'就在明天', ev):
        return 'short'

    # 这几天 / 这两三天
    if re.search(r'(?:这几天|这几日|这两三天|近两天|近几日)', ev):
        return 'short'

    # 随时会 → short
    if re.search(r'随时.{0,10}?(?:会|将|要|可能|有可能).{0,5}?(?:涨|跌|反弹|调整|反转|突破|拉升|回落)', ev):
        return 'short'

    # 接下来几天/两三天/四五天 → short
    if re.search(r'接下来.{0,5}?(?:几天|两三天|三四天|四五天)', ev):
        return 'short'

    # ================================================================
    # STEP 3: Check for WEEKLY predictions (~1 week)
    # ================================================================

    # 下周 (without specific "下周一" which was handled above)
    if re.search(r'下周', ev):
        return 'weekly'

    # 下星期 → weekly
    if re.search(r'下(?:个)?星期', ev):
        return 'weekly'

    # 本周 / 这周 / 这个星期 → weekly
    if re.search(r'本周|这周|这个星期|这一个星期|这一个周', ev):
        return 'weekly'

    # 这一周 → weekly
    if re.search(r'这一周', ev):
        return 'weekly'

    # 一周以上 → weekly
    if re.search(r'一周以上|至少.*?调整.*?一(?:个)?周', ev):
        return 'weekly'

    # 接下来一周 → weekly
    if re.search(r'接下来(?:一|这)(?:周|星期|礼拜)', ev):
        return 'weekly'

    # 本周后半周 → weekly
    if re.search(r'本周后半周', ev):
        return 'weekly'

    # ================================================================
    # STEP 4: Check for BIWEEKLY (~2 weeks)
    # ================================================================

    if re.search(r'半个月.{0,10}?(?:内|以内|之内|后|之后|会|将|要|就)', ev):
        return 'biweekly'
    if re.search(r'至少.*?半个月|半个月.*?至少', ev):
        return 'biweekly'

    # ================================================================
    # STEP 5: Check for MONTHLY (~1 month)
    # ================================================================

    # 下个月/下月 → monthly
    if re.search(r'下个?月', ev):
        return 'monthly'

    # 年前 → monthly (approximately December through CNY)
    if re.search(r'年前.{0,20}?(?:站上|突破|达到|冲上|会涨|会跌|反弹|大涨|大跌)', ev):
        return 'monthly'

    # 元旦前/元旦后 → monthly
    if re.search(r'元旦(?:前|后)', ev):
        return 'monthly'

    # X月份 → monthly/long based on how far
    m = re.search(r'([一二三四五六七八九十\d]+)月(?:份)?', ev)
    if m:
        month_str = m.group(1)
        # Check if it's a prediction about that month (not just a date reference)
        if re.search(rf'{month_str}月(?:份)?.{{0,10}}?(?:会涨|会跌|反弹|调整|大涨|暴跌|行情|走势|看涨|看多|看跌|看空|看好|全面|全线)', ev):
            # Try to determine how many months ahead
            from datetime import datetime
            try:
                pub_month = int(publish_time[5:7])
                # Convert month_str to number
                num_map = {'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10,'十一':11,'十二':12}
                target = num_map.get(month_str) or int(month_str)
                if target == pub_month:
                    return 'monthly'
                elif target - pub_month in [1, -11]:  # Next month
                    return 'monthly'
                else:
                    return 'long'  # Far future month
            except:
                return 'monthly'

    # 节后行情 → monthly (broad post-holiday outlook)
    if re.search(r'(?:五一|十一|春节|清明|端午|中秋)节后.{0,10}?(?:行情|看涨|看多|看跌|看空|看好|反弹|大涨|爆发|启动)', ev):
        return 'monthly'

    # 月底/月末 → monthly
    if re.search(r'(?:月底|月末).{0,15}?(?:站上|突破|达到|冲上|会涨|会跌|反弹|调整)', ev):
        return 'monthly'

    # 一两个月 → monthly
    if re.search(r'一两个月', ev):
        return 'monthly'

    # 这个月 → monthly
    if re.search(r'这个月', ev):
        return 'monthly'

    # ================================================================
    # STEP 6: Check for LONG predictions (>1 month, structural)
    # ================================================================

    # Explicit long-term
    if re.search(r'长期|中长期|长线', ev) and re.search(r'(?:看涨|看多|看好|上涨|下跌|趋势|牛市|熊市|不改|目标|向上|向下|格局)', ev):
        return 'long'

    # Bull/bear market declarations
    if re.search(r'(?:没有|不会有|不可能是|不是|难有|不会|哪里来的|从来没有).{0,5}?牛市', ev):
        return 'long'
    if re.search(r'(?:就是|这是|迎来|开启|进入|处在|处于|还是|这就是|真是|才是).{0,5}?牛市', ev):
        return 'long'
    if re.search(r'牛市.*?(?:起点|开启|来了|还在|仍在|格局|不改|继续|刚刚开始|不改)', ev):
        return 'long'
    if re.search(r'熊市.*?(?:来了|还在|继续|开启|格局|共振|调整周期)', ev):
        return 'long'
    if re.search(r'(?:进入|已进入|已经进入|彻底进入|完全进入|走入).{0,5}?熊市', ev):
        return 'long'
    if re.search(r'这波牛市|大牛市格局|牛屎|马年.*?牛市', ev):
        return 'long'

    # Extended timeframe
    if re.search(r'半年.{0,30}?(?:之内|以内|内|后|站上|达到|冲上|突破|上到|看涨|看多|看好|必定|必然|一定|目标|极度|不会)', ev):
        return 'long'
    if re.search(r'(?:六个月|6个月).{0,30}?(?:之内|以内|内|后|站上|达到|冲上|突破|上到|看涨|看多|看好|目标|极度|一定)', ev):
        return 'long'
    if re.search(r'未来.*?(?:6个月|六个月|半年)', ev):
        return 'long'
    if re.search(r'一年.{0,30}?(?:之内|以内|内|后|站上|达到|突破|冲上|看涨|看多|目标|展望|看到|之中)', ev):
        return 'long'
    if re.search(r'两年.{0,15}?(?:之内|以内|内|站上|达到|突破|看涨|看多|目标|看到)', ev):
        return 'long'
    if re.search(r'\d年之内.{0,15}?(?:站上|达到|冲上|突破|看到|目标)', ev):
        return 'long'

    # Structural predictions
    if re.search(r'大趋势.{0,10}?(?:向上|向下|不变|未变|不会变|不改|已定|已确立|确立|形成|走好|走坏|走弱|走强|反转)', ev):
        return 'long'
    if re.search(r'大方向.{0,10}?(?:向上|向下|不变|不改|已定)', ev):
        return 'long'

    # 下半年
    if re.search(r'下半年.{0,20}?(?:的|大|行情|走势|趋势|目标|不会|看涨|看跌|看空|不看好|行情|在)', ev):
        return 'long'

    # Year-scale
    if re.search(r'2026年.{0,30}?(?:目标|铁定|站上|冲上|突破|看到|剑指|上到|达到|展望|牛市|熊市|稳稳|必然|一定会|必定)', ev):
        return 'long'
    if re.search(r'2027年.{0,30}?(?:目标|铁定|站上|冲上|突破|看到|剑指|上到|达到|牛市|熊市)', ev):
        return 'long'

    # Long-term targets
    if re.search(r'4500(?:点|关).{0,30}?(?:目标|不到|没到|以下|从来|坚持|坚定|始终|未变|不变|不远|指日可待|触手可及|唾手可得|征程|关口|远景|信仰|从不)', ev):
        return 'long'
    if re.search(r'5000(?:点|关).{0,30}?(?:目标|展望|不是|也可|也有|很远|遥远|看到|剑指|远景|关口|大关|征途|征程|不是梦|不是奢望|也行|也不是|而是)', ev):
        return 'long'
    if re.search(r'6000(?:点|关).{0,30}?(?:目标|展望|不是|也可|也有|很远|遥远|看到|可能|不是梦|可以|也不|也不是)', ev):
        return 'long'
    if re.search(r'10000点|万点', ev):
        return 'long'

    # Mid-term
    if re.search(r'中期目标', ev):
        return 'long'
    if re.search(r'中期.{0,5}?(?:看涨|看多|看好|向上|向下|不看)', ev):
        return 'long'

    # Long duration descriptions
    if re.search(r'长期阴跌|漫长阴跌|漫长.{0,5}?(?:调整|下跌|上涨)', ev):
        return 'long'
    if re.search(r'为期数[个]?月|至少要调整.{0,2}?个月', ev):
        return 'long'

    # Extended holding commitment
    if re.search(r'长期持有|天长地久|天地.{0,2}?久|天昏地暗', ev):
        return 'long'

    # 最终/终究 → long
    if re.search(r'(?:最终|终究|终归|迟早).{0,10}?(?:会|要|将).{0,5}?(?:涨|跌|回落|调整|反弹|反转|上涨|下跌)', ev):
        return 'long'

    # Future months
    if re.search(r'未来.*?(?:几个|数月).{0,5}?(?:月|个月).{0,10}?(?:会涨|会跌|反弹|调整|上涨|下跌|阴跌)', ev):
        return 'long'

    # 十年难遇 → long
    if re.search(r'十年.{0,5}?(?:难遇|一遇|不遇|罕见)', ev):
        return 'long'

    # Technological structural change
    if re.search(r'科技.*?(?:长期|漫长|为期数月|数年).{0,10}?(?:调整|下跌|阴跌)', ev):
        return 'long'

    # ================================================================
    # STEP 7: REMAINING CHECKS
    # ================================================================

    # Check for "今天" that wasn't caught but has direction → last chance intraday
    if re.search(r'今天(?:的|很大概率|大概率|基本|肯定|必然|必定|铁定).{0,10}?(?:涨|跌|反弹|调整|走强|走弱|收阳|收阴)', ev):
        return 'intraday'

    # Very short evidence about today
    if (ev.startswith('今天') or ev.startswith('今日')) and len(ev) < 20:
        return 'intraday'

    # "下午" alone with prediction in short evidence
    if re.search(r'下午', ev) and len(ev) < 30 and re.search(r'(?:涨|跌|反弹|调整|拉升|走高|走低|V|反转|修复)', ev):
        return 'intraday'

    # 开盘 references (without 明天 context) → intraday
    if re.search(r'早盘|开盘|盘中', ev) and '明天' not in ev and '明日' not in ev and len(ev) < 30:
        if re.search(r'(?:会|将|要|就会|一定|大概率|必定).{0,5}?(?:涨|跌|反弹|拉升|跳水|杀跌|低开|高开|V)', ev):
            return 'intraday'

    # High-confidence catch for any remaining "今天" predictives
    if re.search(r'今天.*?(?:看涨|看跌|会涨|会跌|要涨|要跌|必涨|必跌)$', ev):
        return 'intraday'

    # ================================================================
    # STEP 8: DEFAULT — directional content → short, else unspecified
    # ================================================================
    # Market commentary without explicit timeframe typically refers to near-term
    # moves. Default to "short" for directional content, "unspecified" otherwise.

    has_direction = re.search(r'(?:看涨|看跌|看多|看空|看好|不看好|会涨|会跌|要涨|要跌|必涨|必跌|大涨|大跌|暴涨|暴跌|反弹|调整|变盘|突破|反转|上涨|下跌|上行|下行|走强|走弱|拉升|拉升|回落|上冲|下探|起飞|暴涨|暴跌|修复|冲高|走高|走低)', ev)

    if has_direction:
        return 'short'

    return 'unspecified'


def main():
    filepath = r'D:\claude_code_ana\blogger-analysis\data\signals\梦若神机.json'

    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)

    signals = data['signals']
    total = len(signals)
    changes = []
    change_details = []

    print(f"Processing {total} signals...")

    for i, signal in enumerate(signals):
        old_h = signal['time_horizon']
        evidence = signal['evidence']
        pub_time = signal.get('publish_time', '')

        new_h = classify(evidence, pub_time)

        if new_h != old_h:
            changes.append(i)
            change_details.append({
                'idx': i,
                'post_n': signal['post_n'],
                'old': old_h,
                'new': new_h,
                'dir': signal['direction'],
                'str': signal['strength'],
                'evidence': evidence,
                'time': pub_time
            })
            print(f"  [{i}] post_n={signal['post_n']} {old_h}->{new_h} [{signal['direction']}/{signal['strength']}]")
            print(f"       {evidence[:200]}")
            print()

        signal['time_horizon'] = new_h

    print(f"\n{'='*60}")
    print(f"Total signals: {total}")
    print(f"Changed: {len(changes)}")

    # Transition summary
    from collections import Counter
    trans = Counter(f"{c['old']}->{c['new']}" for c in change_details)
    print(f"\nTransition summary:")
    for t, cnt in trans.most_common():
        print(f"  {t}: {cnt}")

    # Recalculate header counts
    sb = {'strong': 0, 'moderate': 0, 'intraday': 0, 'short': 0, 'weekly': 0, 'biweekly': 0, 'monthly': 0, 'long': 0, 'unspecified': 0}
    sbr = {'strong': 0, 'moderate': 0, 'intraday': 0, 'short': 0, 'weekly': 0, 'biweekly': 0, 'monthly': 0, 'long': 0, 'unspecified': 0}

    for sig in signals:
        d = sig['direction']
        s = sig['strength']
        h = sig['time_horizon']
        if d == 'bullish':
            sb[s] = sb.get(s, 0) + 1
            sb[h] = sb.get(h, 0) + 1
        else:
            sbr[s] = sbr.get(s, 0) + 1
            sbr[h] = sbr.get(h, 0) + 1

    data['scored_bullish'] = sb
    data['scored_bearish'] = sbr

    print(f"\nNew scored_bullish: {json.dumps(sb, ensure_ascii=False)}")
    print(f"New scored_bearish: {json.dumps(sbr, ensure_ascii=False)}")

    # Verify: strong+moderate should equal sum of all horizon counts
    bull_horizon_sum = sum(sb.get(h, 0) for h in ['intraday','short','weekly','biweekly','monthly','long','unspecified'])
    bull_str_sum = sb.get('strong', 0) + sb.get('moderate', 0)
    bear_horizon_sum = sum(sbr.get(h, 0) for h in ['intraday','short','weekly','biweekly','monthly','long','unspecified'])
    bear_str_sum = sbr.get('strong', 0) + sbr.get('moderate', 0)
    print(f"\nValidation:")
    print(f"  Bullish: strong+moderate={bull_str_sum}, horizon_sum={bull_horizon_sum}, match={bull_str_sum==bull_horizon_sum}")
    print(f"  Bearish: strong+moderate={bear_str_sum}, horizon_sum={bear_horizon_sum}, match={bear_str_sum==bear_horizon_sum}")

    # Write back
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\nFile written: {filepath}")

    # Save change log
    logpath = r'D:\claude_code_ana\blogger-analysis\relabel_changes_mengruo.log'
    with open(logpath, 'w', encoding='utf-8') as f:
        f.write(f"Total signals: {total}\n")
        f.write(f"Changed: {len(changes)}\n\n")
        f.write(f"Transition summary:\n")
        for t, cnt in trans.most_common():
            f.write(f"  {t}: {cnt}\n")
        f.write(f"\nDetailed changes:\n\n")
        for c in change_details:
            f.write(f"[{c['idx']}] post_n={c['post_n']} {c['old']}->{c['new']} [{c['dir']}/{c['str']}]: {c['evidence'][:250]}... ({c['time'][:10]})\n")

    print(f"Change log written: {logpath}")


if __name__ == '__main__':
    main()
