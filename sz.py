import tushare as ts
import pandas as pd
from dotenv import load_dotenv
import os

# 加载 .env 文件
load_dotenv()

# 从环境变量中获取 Token
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")

# 检查 Token 是否存在
if not TUSHARE_TOKEN:
    print("Error: TUSHARE_TOKEN not found in .env file.")
    exit()

# 设置你的 Tushare Pro API Token
ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

# 定义要查询的股票代码和名称
ts_codes = ['002594.SZ', '000725.SZ', '300207.SZ', '300059.SZ', '300065.SZ', '000823.SZ', '600150.SH']
names = ['比亚迪', '京东方A', '欣旺达', '东方财富', '海兰信', '超声电子', '中国船舶']

# 获取日线行情数据并只打印实时价格
def get_realtime_price(ts_codes):
    for i, ts_code in enumerate(ts_codes):
        try:
            today = pd.Timestamp.today().strftime('%Y%m%d')

            # 判断是股票还是指数/基金  (简化判断，假设以0,3,6开头的是股票)
            if ts_code.startswith(('0', '3', '6')):
                df = pro.daily(ts_code=ts_code, trade_date=today) # 股票
            else:
                df = pro.index_daily(ts_code=ts_code, trade_date=today) # 指数/基金

            if not df.empty:
                close_price = df['close'][0]  # 获取今天的收盘价
                print(f"{names[i]}: {close_price}")
            else:
                print(f"未能获取 {names[i]} ({ts_code}) 的数据。")

        except Exception as e:
            print(f"获取 {names[i]} ({ts_code}) 数据出错: {e}")


get_realtime_price(ts_codes)

print("完成!")
