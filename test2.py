import re
ev = "今天不会大跌，甚至收微红"
if re.search(r"今天.{0,15}(?:不会|收|维持|仍|继续).{0,10}(?:跌|涨|红|绿|震荡)", ev):
    print("matched: intraday")
# Test with double quotes in Python
x = "hello"
print(x)
