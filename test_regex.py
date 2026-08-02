import re
ev = "预计午后会有反弹"
if re.search(r"(?:预计|估计|猜|料).{0,10}(?:午后|下午).{0,10}(?:会|将|仍|继续)", ev):
    print("matched intraday")
print("test ok")
