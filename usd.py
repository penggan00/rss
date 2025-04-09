import yfinance as yf
import requests
import os
from dotenv import load_dotenv
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
import pytz
from lunarcalendar import Converter, Solar, Lunar

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

# 设置香港时区
hongkong = pytz.timezone('Asia/Hong_Kong')
BASE_DATE = datetime(2024, 12, 6, tzinfo=hongkong)

# Markdown转义
def escape_markdown(text):
    for char in ['_', '*', '[', '`']:
        text = text.replace(char, f'\\{char}')
    return text

def format_price(price, is_etf=False):
    return f"{price:.3f}" if is_etf else f"{price:.2f}"

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

def get_reminders():
    now = datetime.now(hongkong)
    solar_today = Solar(now.year, now.month, now.day)
    messages = []

    # 1. 日常用药提醒
    messages.append('🕗 时间到，降压！')

    # 2. 每10天通行证续签
    days_since_base = (now - BASE_DATE).days
    if days_since_base % 10 == 0:
        messages.append('🔄 续签通行证！')

    # 3. 固定日期年提醒
    annual_reminders = {
        (3, 1): "🚗 小车打腊",
        (5, 1): "📝 从业资格证年审",
        (10, 5): "💍 结婚周年",
        (11, 26): "✈️ 离开,彭昊一",
        (12, 1): "📋 小车年检保险"
    }
    for (month, day), msg in annual_reminders.items():
        if now.month == month and now.day == day:
            messages.append(msg)

    # 4. 特定年份提醒
    specific_year_reminders = {
        (2025, 4, 5): "🔄 建行银行卡",
        (2026, 10, 5): "💎 结婚20周年",
        (2027, 5, 1): "🔄 女儿医保卡",
        (2027, 5, 11): "🔄 爸爸换身份证",
        (2028, 6, 1): "🔄 招商银行卡",
        (2030, 11, 1): "🔄 中国信用卡",
        (2037, 3, 22): "🆔 换身份证"
    }
    for (y, m, d), msg in specific_year_reminders.items():
        if now.year == y and now.month == m and now.day == d:
            messages.append(msg)

    # 5. 每月云闪付提醒
    if now.day == 1:
        messages.append('1号提醒，拍照')

    # 6. 农历生日处理
    lunar_today = Converter.Solar2Lunar(solar_today)
    lunar_birthdays = {
        (2, 1): "🎂 杜根华，生日",
        (2, 28): "🎂 彭佳文，生日",
        (3, 11): "🎂 刘裕萍，生日",
        (4, 12): "🎂 彭绍莲，生日",
        (4, 20): "🎂 邬思，生日",
        (4, 27): "🎂 彭博，生日",
        (5, 5): "🎂 周子君，生日",
        (5, 17): "🎂 杜俊豪，生日",
        (8, 19): "🎂 奶奶，生日",       
        (8, 17): "🎂 邬启元，生日",
        (10, 9): "🎂 彭付生，生日",
        (10, 18): "🎂 彭贝娜，生日",
        (11, 12): "🎂 彭辉，生日",
        (11, 22): "🎂 彭干，生日",
        (12, 1): "🎂 彭昊一，生日",
        (12, 29): "🎂 彭世庆，生日"
    }
    for (month, day), msg in lunar_birthdays.items():
        if lunar_today.month == month and lunar_today.day == day:
            messages.append(msg)

    return messages

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
        return "\n".join(rainy_cities) + "\n"
    return ""

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

def main():
    message_parts = []
    
    # 1. 添加提醒事项
    reminders = get_reminders()
    if reminders:
        message_parts.extend([f"• *{reminder}*\n" for reminder in reminders])    
    # 2. 添加天气信息
    rain_info = get_tomorrow_rain_info()
    if rain_info:
        message_parts.append(rain_info)
    message_parts.append("--------------------------------------\n")
    # 获取指数数据
    sh_index = get_cn_stock('sh000001', '上证指数')
    sz_index = get_cn_stock('sz399001', '深证成指')
    nasdaq_index = get_us_index('^IXIC', '纳斯达克')
    dow_index = get_us_index('^DJI', '道琼斯')
    
    message_parts.append(sh_index if sh_index else "⚠️ 获取 上证指数 数据失败\n")
    message_parts.append(sz_index if sz_index else "⚠️ 获取 深证成指 数据失败\n")
    message_parts.append(nasdaq_index if nasdaq_index else "⚠️ 获取 纳斯达克 数据失败\n")
    message_parts.append(dow_index if dow_index else "⚠️ 获取 道琼斯 数据失败\n")
    
    # 股票数据
    message_parts.append("--------------------------------------\n")
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
    message_parts.append("--------------------------------------\n")
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