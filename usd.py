import yfinance as yf
import requests
import os
from dotenv import load_dotenv
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

# 加载环境变量
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_API_KEY")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
JUHE_STOCK_KEY = os.getenv("JUHE_STOCK_KEY")
QWEATHER_API_KEY = os.getenv("QWEATHER_API_KEY")
QWEATHER_API_HOST = os.getenv("QWEATHER_API_HOST")

# 城市ID映射
CITIES = {
    "南昌": os.getenv("CITY_NANCHANG", "101240101"),
    "萍乡": os.getenv("CITY_PINGXIANG", "101240901")
}

# ETF列表（需要保留3位小数的品种）
ETF_SYMBOLS = {
    "510300.SS": "沪深300",
    "512660.SS": "军工ETF"
}

# 配置类
class Config:
    def __init__(self):
        self.YFINANCE_MAX_WORKERS = 2
        self.YFINANCE_MIN_INTERVAL = 1
        self.MAX_RETRIES = 3
        self.BASE_TIMEOUT = 10

config = Config()

# Markdown转义
def escape_markdown(text):
    for char in ['_', '*', '[', '`']:
        text = text.replace(char, f'\\{char}')
    return text

def format_price(price, is_etf=False):
    return f"{price:.3f}" if is_etf else f"{price:.2f}"

# 获取明天有雨的城市信息
def get_tomorrow_rain_info():
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    rainy_cities = []
    
    for city, city_id in CITIES.items():
        try:
            url = f"https://{QWEATHER_API_HOST}/v7/weather/3d"
            params = {"location": city_id, "key": QWEATHER_API_KEY}
            response = requests.get(url, params=params, timeout=10)
            data = response.json()
            
            if data.get("code") == "200":
                for day in data["daily"]:
                    if day["fxDate"] == tomorrow:
                        if "雨" in day["textDay"] or "雨" in day["textNight"]:
                            rainy_cities.append(
                                f"*{city}：{day['textDay']}，"
                                f"气温 {day['tempMin']}~{day['tempMax']}℃，"
                                f"湿度 {day['humidity']}%*"
                            )
                        break
        except:
            continue
    
    if rainy_cities:
        return "\n".join(rainy_cities) + "\n\n"
    return ""

# 获取美股指数（使用yfinance）
def get_us_index(symbol, name):
    try:
        ticker = yf.Ticker(symbol)
        data = ticker.history(period="2d")
        if len(data) >= 2:
            price = data['Close'].iloc[-1]
            prev_close = data['Close'].iloc[-2]
            change = price - prev_close
            percent = (change / prev_close) * 100
            
            emoji = "🔴" if change > 0 else "🔵"
            sign = "+" if change > 0 else ""
            return f"{emoji} {escape_markdown(name)}: *{escape_markdown(format_price(price))}* ({sign}{escape_markdown(format_price(change))}, {sign}{escape_markdown(f'{percent:.2f}%')})\n"
    except:
        pass
    return f"⚠️ 获取 {escape_markdown(name)} 数据失败\n"

# 获取A股数据（使用聚合数据）
def get_cn_stock(gid, name):
    params = {"key": JUHE_STOCK_KEY, "gid": gid}
    if gid not in ['sh000001', 'sz399001']:
        params['type'] = '0' if gid.startswith('sh') else '1'
    
    try:
        response = requests.get("http://web.juhe.cn/finance/stock/hs", params=params, timeout=10)
        data = response.json()
        
        if data.get('error_code') == 0:
            result = data['result']
            if isinstance(result, list):
                stock_data = result[0]['data']
                price = float(stock_data['nowPri'])
                change = float(stock_data['increase'])
                percent = float(stock_data['increPer'])
            else:
                price = float(result['nowpri'])
                change = float(result['increase'])
                percent = float(result['increPer'])
            
            emoji = "🔴" if change > 0 else "🔵"
            sign = "+" if change > 0 else ""
            return f"{emoji} {escape_markdown(name)}: *{escape_markdown(format_price(price))}* ({sign}{escape_markdown(format_price(change))}, {sign}{escape_markdown(f'{percent:.2f}%')})\n"
    except:
        pass
    return f"⚠️ 获取 {escape_markdown(name)} 数据失败\n"

# 获取ETF/股票数据（使用yfinance）
def get_yfinance_data(symbol, name):
    time.sleep(config.YFINANCE_MIN_INTERVAL)
    try:
        ticker = yf.Ticker(symbol)
        data = ticker.history(period="2d")
        if len(data) >= 2:
            price = data['Close'].iloc[-1]
            prev_close = data['Close'].iloc[-2]
            change = price - prev_close
            percent = (change / prev_close) * 100
            
            emoji = "🔴" if change > 0 else "🔵"
            sign = "+" if change > 0 else ""
            is_etf = symbol in ETF_SYMBOLS
            return f"{emoji} {escape_markdown(name)}: *{escape_markdown(format_price(price, is_etf))}* ({sign}{escape_markdown(format_price(change))}, {sign}{escape_markdown(f'{percent:.2f}%')})\n"
    except:
        pass
    return f"⚠️ 获取 {escape_markdown(name)} 数据失败\n"

# 发送到Telegram
def send_to_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except:
        pass

def main():
    # 检查明天有雨的城市
    rain_info = get_tomorrow_rain_info()
    
    # 构建消息
    message_parts = []
    
    # 如果有雨，添加天气信息
    if rain_info:
        message_parts.append(rain_info)
    
    # 添加市场数据标题
    message_parts.append("*📊 市场数据更新：*\n\n")
    
    # 获取指数数据（确保顺序：上证 > 深证 > 道琼斯 > 纳斯达克）
    sh_index = get_cn_stock('sh000001', '上证指数')
    sz_index = get_cn_stock('sz399001', '深证成指')
    dow_index = get_us_index('^DJI', '道琼斯')
    nasdaq_index = get_us_index('^IXIC', '纳斯达克')
    
    message_parts.append(sh_index if sh_index else "⚠️ 获取 上证指数 数据失败\n")
    message_parts.append(sz_index if sz_index else "⚠️ 获取 深证成指 数据失败\n")
    message_parts.append(dow_index if dow_index else "⚠️ 获取 道琼斯 数据失败\n")
    message_parts.append(nasdaq_index if nasdaq_index else "⚠️ 获取 纳斯达克 数据失败\n")
    
    # 股票数据
    message_parts.append("\n----------------------------\n")
    stock_symbols = [
        ("510300.SS", "沪深300"),
        ("512660.SS", "军工ETF"),
        ("300059.SZ", "东方财富"),
        ("600150.SS", "中国船舶"),
        ("000823.SZ", "超声电子"),
        ("000725.SZ", "京东方A"),
        ("300065.SZ", "海兰信"),
        ("300207.SZ", "欣旺达"),
        ("002594.SZ", "比亚迪")
    ]
    
    for symbol, name in stock_symbols:
        message_parts.append(get_yfinance_data(symbol, name))
    
    # 商品和汇率
    message_parts.append("----------------------------\n\n")
    commodities = [
        ("GC=F", "黄金"),
        ("BZ=F", "原油"),
        ("USDCNY=X", "USD/CNY")
    ]
    
    for symbol, name in commodities:
        message_parts.append(get_yfinance_data(symbol, name))
    
    # 合并并发送消息
    full_message = "".join(message_parts)
    send_to_telegram(full_message)

if __name__ == "__main__":
    main()