# pip install yfinance requests python-dotenv pytz lunarcalendar pandas
# 不再需要 yfinance
import requests
import os
from dotenv import load_dotenv
import time
from datetime import datetime, timedelta
import pytz
from lunarcalendar import Converter, Solar


# 加载环境变量
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_API_KEY")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
JUHE_STOCK_KEY = os.getenv("JUHE_STOCK_KEY")
LIAO_STOCK_KEY = os.getenv("LIAO_STOCK_KEY")
QWEATHER_API_KEY = os.getenv("QWEATHER_API_KEY")
QWEATHER_API_HOST = os.getenv("QWEATHER_API_HOST")
JUHE_GOLD_KEY = os.getenv("JUHE_GOLD_KEY")
OILPRICE_API_KEY = os.getenv("OILPRICE_API_KEY")

# 配置参数
def get_cities():
    """从环境变量读取城市列表"""
    cities = {}
    for key, value in os.environ.items():
        if key.startswith("CITY_") and value:
            city_name = key.replace("CITY_", "")
            cities[city_name] = value
    return cities


# 新增原油 API 配置
OIL_CODES = {
    "brent": "BRENT_CRUDE_USD",
    "wti": "WTI_CRUDE_USD",
}

# 黄金品种配置（聚合数据API）
GOLD_VARIETIES = {
    "Au99.99": "Au99.99",
}

class MarketConfig:
    USA_API = 'http://web.juhe.cn/finance/stock/usa'
    HK_API = 'http://web.juhe.cn/finance/stock/hk'
    GOLD_API = 'http://web.juhe.cn/finance/gold/shgold'
    USA_INDEXES = {
        '纳斯达克': {'code': 'IXIC', 'unit': ''},
        '道琼斯': {'code': 'DJI', 'unit': ''}
    }
    HK_INDEXES = {
        '恒生指数': {'code': 'HSI', 'unit': ''}
    }

# ================== 新增 Oil Price API 服务 ==================
class OilPriceService:
    @staticmethod
    def get_oil_price(code: str = "BRENT_CRUDE_USD"):
        """获取原油价格"""
        url = f"https://api.oilpriceapi.com/v1/prices/latest?by_code={code}"
        headers = {"Authorization": f"Token {OILPRICE_API_KEY}"}
        
        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                return response.json()
            return None
        except Exception as e:
            print(f"获取原油价格失败: {e}")
            return None
    
    @staticmethod
    def format_oil_price(code: str, name: str) -> str:
        """格式化原油价格为消息"""
        try:
            data = OilPriceService.get_oil_price(code)
            if not data or data.get("error"):
                return ""
            
            attributes = data.get("data", {}).get("attributes", {})
            price = attributes.get("price", 0)
            currency = attributes.get("currency", "USD")
            
            # 获取涨跌幅（如果有）
            change = attributes.get("change_percent", None)
            
            if change is not None:
                emoji = "🔴" if change > 0 else "🔵"
                sign = "+" if change > 0 else ""
                change_str = f"(*{sign}{abs(change):.2f}%*, "
                # 计算涨跌金额（简化，API可能不直接提供）
                return f"{emoji} {escape_markdown(name)}: *{escape_markdown(f'{price:.2f}')}* {change_str}{sign}{escape_markdown(f'{abs(change*price/100):.2f}')})\n"
            else:
                return f"🟡 {escape_markdown(name)}: *{escape_markdown(f'{price:.2f}')}* {currency}\n"
                
        except Exception as e:
            print(f"格式化原油数据失败: {e}")
            return ""

# ================== 新增服务类 ==================
class StockService:
    @staticmethod
    def fetch_data(api_url, params):
        try:
            response = requests.get(api_url, params=params, timeout=10)
            if response.status_code == 200:
                result = response.json()
                if result.get('error_code') == 0:
                    return result.get('result')
            return None
        except Exception as e:
            print(f"请求异常: {e}")
            return None

    @classmethod
    def get_usa_data(cls, gid):
        params = {'key': JUHE_STOCK_KEY, 'gid': gid.lower()}
        return cls.fetch_data(MarketConfig.USA_API, params)
    
    @classmethod
    def get_hk_data(cls, num):
        params = {'key': JUHE_STOCK_KEY, 'num': num}
        return cls.fetch_data(MarketConfig.HK_API, params)
    
    @classmethod
    def get_gold_data(cls):
        params = {'key': JUHE_GOLD_KEY, 'v': '1'}
        return cls.fetch_data(MarketConfig.GOLD_API, params)
    
class DataProcessor:
    @staticmethod
    def parse_usa_index(data):
        if not data or not isinstance(data, list) or len(data) == 0:
            return None
        index_data = data[0].get('data', {})
        
        def format_number(value, is_percent=False):
            try:
                num = float(str(value).replace('%', ''))
                if is_percent:
                    return round(num, 2)
                return round(num, 2)
            except:
                return None

        return {
            'price': format_number(index_data.get('lastestpri')),
            'change_percent': format_number(index_data.get('limit'), True),
            'change_point': format_number(index_data.get('uppic')),
            'unit': '',
            'is_positive': format_number(index_data.get('uppic')) >= 0
        }
    
    @staticmethod
    def parse_hk_index(data):
        if not data or not isinstance(data, list) or len(data) == 0:
            return None
            
        hsi_data = data[0].get('hengsheng_data', {})
        
        def format_number(value, is_percent=False):
            try:
                num = float(str(value).replace('%', ''))
                if is_percent:
                    return round(num, 2)
                return round(num, 2)
            except:
                return None

        return {
            'price': format_number(hsi_data.get('lastestpri')),
            'change_percent': format_number(hsi_data.get('limit'), True),
            'change_point': format_number(hsi_data.get('uppic')),
            'unit': '',
            'is_positive': format_number(hsi_data.get('uppic')) >= 0
        }
    
    @staticmethod
    def parse_gold_data(data):
        if not data or not isinstance(data, list) or len(data) == 0:
            return None
        
        result = {}
        gold_items = data[0]
        
        for key, item in gold_items.items():
            variety = item.get('variety', '')
            if variety in GOLD_VARIETIES:
                try:
                    latest_price = float(item.get('latestpri', 0))
                    change_str = item.get('limit', '0%')
                    change_percent = float(change_str.replace('%', ''))
                    
                    result[variety] = {
                        'price': latest_price,
                        'change_percent': change_percent,
                        'is_positive': change_percent > 0,
                        'open_price': float(item.get('openpri', 0)),
                        'high_price': float(item.get('maxpri', 0)),
                        'low_price': float(item.get('minpri', 0)),
                        'prev_close': float(item.get('yespri', 0)),
                        'volume': float(item.get('totalvol', 0)),
                        'time': item.get('time', '')
                    }
                except (ValueError, KeyError) as e:
                    print(f"解析黄金品种 {variety} 失败: {e}")
                    continue
        
        return result

# ================== 黄金数据获取函数 ==================
def get_gold_data_formatted():
    """获取并格式化Au99.99黄金数据"""
    try:
        raw_data = StockService.get_gold_data()
        parsed_data = DataProcessor.parse_gold_data(raw_data)
        
        if not parsed_data or 'Au99.99' not in parsed_data:
            return ""
        
        data = parsed_data['Au99.99']
        name = "Au99.99"
        
        price_str = f"{data['price']:.2f}"
        change_percent = abs(data['change_percent'])
        change_percent_str = f"{change_percent:.2f}%"
        
        change_amount = data['price'] - data['prev_close']
        change_amount_abs = abs(change_amount)
        change_amount_str = f"{change_amount_abs:.2f}"
        
        is_positive = data['is_positive']
        emoji = "🔴" if is_positive else "🔵"
        sign = "+" if is_positive else "-"
        
        return (
            f"{emoji} {escape_markdown(name)}: *{escape_markdown(price_str)}* "
            f"(*{sign}{escape_markdown(change_percent_str)}*, "
            f"{sign}{escape_markdown(change_amount_str)})\n"
        )
    
    except Exception as e:
        print(f"获取黄金数据异常: {str(e)}")
        return ""

# ================== 原有函数 ==================
# 设置时区
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

    daily_reminder = os.getenv("DAILY_REMINDER", "")
    if daily_reminder:
        messages.append(daily_reminder)

    passport_reminder = os.getenv("PASSPORT_REMINDER", "")
    if passport_reminder:
        parts = passport_reminder.split(':', 1)
        if len(parts) == 2:
            try:
                interval = int(parts[0])
                reminder_msg = parts[1]
                if interval > 0:
                    days_since_base = (now - BASE_DATE).days
                    if days_since_base % interval == 0:
                        messages.append(reminder_msg)
            except ValueError:
                pass

    annual_reminders_str = os.getenv("ANNUAL_REMINDERS", "")
    if annual_reminders_str:
        for item in annual_reminders_str.split(';'):
            if not item.strip():
                continue
            parts = item.strip().split(':', 1)
            if len(parts) == 2:
                date_str, msg = parts
                month, day = map(int, date_str.split(','))
                if now.month == month and now.day == day:
                    messages.append(msg)

    specific_year_reminders_str = os.getenv("SPECIFIC_YEAR_REMINDERS", "")
    if specific_year_reminders_str:
        for item in specific_year_reminders_str.split(';'):
            if not item.strip():
                continue
            parts = item.strip().split(':', 1)
            if len(parts) == 2:
                date_str, msg = parts
                year, month, day = map(int, date_str.split(','))
                if now.year == year and now.month == month and now.day == day:
                    messages.append(msg)

    monthly_reminder = os.getenv("MONTHLY_REMINDER", "")
    if monthly_reminder and now.day == 1:
        messages.append(monthly_reminder)

    lunar_birthdays_str = os.getenv("LUNAR_BIRTHDAYS", "")
    if lunar_birthdays_str:
        lunar_today = Converter.Solar2Lunar(solar_today)
        for item in lunar_birthdays_str.split(';'):
            if not item.strip():
                continue
            parts = item.strip().split(':', 1)
            if len(parts) == 2:
                date_str, msg = parts
                month, day = map(int, date_str.split(','))
                if lunar_today.month == month and lunar_today.day == day:
                    messages.append(msg)

    return messages

def get_tomorrow_rain_info():
    beijing_tz = pytz.timezone('Asia/Shanghai')
    tomorrow_date = (datetime.now(beijing_tz) + timedelta(days=1)).strftime("%Y-%m-%d")
    rainy_cities = []
    
    RAIN_KEYWORDS = {
        'cn': ["雨", "阵雨", "雷雨", "小雨", "中雨", "大雨", "暴雨", "毛毛雨", "冰雹"],
        'en': ["rain", "shower", "storm", "drizzle", "thunderstorm"]
    }

    cities = get_cities()
    for city_name, city_id in cities.items():
        try:
            url = f"https://{QWEATHER_API_HOST}/v7/weather/3d"
            params = {"location": city_id, "key": QWEATHER_API_KEY}
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            if data.get("code") == "200":
                for daily_data in data["daily"]:
                    if daily_data["fxDate"] == tomorrow_date:
                        weather_text = "".join([
                            daily_data.get("textDay", "").lower().strip(),
                            daily_data.get("textNight", "").lower().strip()
                        ])
                        
                        has_rain = any(
                            keyword in weather_text 
                            for lang in RAIN_KEYWORDS.values() 
                            for keyword in lang
                        )
                        
                        if has_rain:
                            report = (
                                f"*{city_name}：{daily_data['textDay']}转{daily_data['textNight']}，"
                                f"气温 {daily_data['tempMin']}~{daily_data['tempMax']}℃*"
                            )
                            rainy_cities.append(report)
                        break
                else:
                    print(f"[WARNING] {city_name} 未找到明日天气数据")
            else:
                print(f"[API ERROR] {city_name} 请求失败: {data.get('code')}-{data.get('message')}")
                
        except requests.exceptions.RequestException as e:
            print(f"[NETWORK ERROR] 获取{city_name}天气失败: {str(e)}")
        except KeyError as e:
            print(f"[DATA ERROR] {city_name} 数据解析异常，缺少字段: {str(e)}")
    
    if rainy_cities:
        return "\n".join(rainy_cities) + "\n"
    return ""

# ================== 删除 get_financial_data（不再需要 yfinance） ==================

# ================== 新增 get_usd_cny_data（保持不变） ==================
def get_usd_cny_data():
    apiUrl = 'http://web.juhe.cn/finance/exchange/frate'
    apiKey = os.getenv("JUHE_FOREX_KEY")
    params = {
        'key': apiKey,
        'type': '',
    }
    
    try:
        response = requests.get(apiUrl, params=params, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        if data.get('error_code') != 0:
            print(f"API返回错误：{data.get('reason')}")
            return None
        
        result_list = data.get('result', [])
        if not result_list or not isinstance(result_list, list):
            return None
            
        forex_data = result_list[0].get('data8', {})
        if not forex_data:
            return None
        
        price = float(forex_data['closePri'])
        change_point = float(forex_data['diffAmo'])
        change_percent = float(forex_data['diffPer'].replace('%', ''))
        
        return {
            'price': price,
            'change_percent': change_percent,
            'change_point': change_point
        }
        
    except requests.exceptions.RequestException as e:
        print(f"外汇API请求失败: {str(e)}")
    except (KeyError, IndexError, ValueError) as e:
        print(f"数据解析异常: {str(e)}")
    
    return None

def get_usd_cny_formatted():
    data = get_usd_cny_data()
    if data:
        is_positive = data['change_point'] >= 0
        emoji = "🔴" if is_positive else "🔵"
        sign = "+" if is_positive else ""
        
        price_str = f"{data['price']:.2f}"
        percent_str = f"{abs(data['change_percent']):.2f}%"
        point_str = f"{abs(data['change_point']):.2f}"
        
        price_str = escape_markdown(price_str)
        percent_str = escape_markdown(f"{sign}{percent_str}")
        point_str = escape_markdown(f"{sign}{point_str}")
        
        return f"{emoji} USD/CNY: *{price_str}* ({percent_str}, {point_str})\n"
    return ""

def get_usa_index(index_code, index_name):
    try:
        time.sleep(1)  
        raw_data = StockService.get_usa_data(index_code)
        parsed_data = DataProcessor.parse_usa_index(raw_data)
        
        if not parsed_data or None in parsed_data.values():
            return f"⚠️ 获取 {escape_markdown(index_name)} 数据失败\n"
  
        price_str = f"{parsed_data['price']:.2f}" if parsed_data['price'] is not None else 'N/A'
        change_point_str = f"{abs(parsed_data['change_point']):.2f}" if parsed_data['change_point'] is not None else 'N/A'
        change_percent_str = f"{abs(parsed_data['change_percent']):.2f}%" if parsed_data['change_percent'] is not None else 'N/A'

        is_positive = parsed_data.get('is_positive', False)
        emoji = "🔴" if is_positive else "🔵"
        sign = "+" if is_positive else "-"
        
        return (
            f"{emoji} {escape_markdown(index_name)}: *{escape_markdown(price_str)}* "
            f"(*{sign}{escape_markdown(change_percent_str)}*, "
            f"{sign}{escape_markdown(change_point_str)})\n"
        )
    except Exception as e:
        print(f"获取美股指数异常: {str(e)}")
        return ""

def get_cn_stock(gid, name):
    params = {"key": LIAO_STOCK_KEY, "gid": gid}
    try:
        time.sleep(1)  
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
            return f"{emoji} {escape_markdown(name)}: *{escape_markdown(format_price(price))}* (*{sign}{escape_markdown(f'{percent:.2f}')}*%, {sign}{escape_markdown(format_price(change))})\n"
    except:
        pass
    return ""

def get_ci_stock(gid, name):
    params = {"key": JUHE_STOCK_KEY, "gid": gid}
    try:
        time.sleep(1)  
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
            return f"{emoji} {escape_markdown(name)}: *{escape_markdown(format_price(price))}* (*{sign}{escape_markdown(f'{percent:.2f}')}*%, {sign}{escape_markdown(format_price(change))})\n"
    except:
        pass
    return ""

def get_etf_stock(gid, name):
    params = {"key": JUHE_STOCK_KEY, "gid": gid}
    try:
        time.sleep(1)  
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
        return (
            f"{emoji} {escape_markdown(name)}: *{escape_markdown(f'{price:.3f}')}* "
            f"(*{sign}{escape_markdown(f'{percent:.2f}')}%*, "
            f"{sign}{escape_markdown(f'{change:.3f}')})\n"
        )
    except:
        pass
    return ""

def get_hk_index(index_code, index_name):
    try:
        time.sleep(1)  
        raw_data = StockService.get_hk_data(index_code)
        parsed_data = DataProcessor.parse_hk_index(raw_data)
        
        if not parsed_data or None in parsed_data.values():
            return ""
  
        price_str = f"{parsed_data['price']:.2f}" if parsed_data['price'] is not None else 'N/A'
        change_point_str = f"{abs(parsed_data['change_point']):.2f}" if parsed_data['change_point'] is not None else 'N/A'
        change_percent_str = f"{abs(parsed_data['change_percent']):.2f}%" if parsed_data['change_percent'] is not None else 'N/A'

        is_positive = parsed_data.get('is_positive', False)
        emoji = "🔴" if is_positive else "🔵"
        sign = "+" if is_positive else "-"
        
        return (
            f"{emoji} {escape_markdown(index_name)}: *{escape_markdown(price_str)}* "
            f"(*{sign}{escape_markdown(change_percent_str)}*, "
            f"{sign}{escape_markdown(change_point_str)})\n"
        )
    except Exception as e:
        print(f"获取香港指数异常: {str(e)}")
        return ""

def get_stock_list():
    stocks = []
    stock_str = os.getenv("STOCK_LIST", "")
    if stock_str:
        for item in stock_str.split(';'):
            if item.strip():
                parts = item.strip().split(':', 1)
                if len(parts) == 2:
                    stocks.append((parts[0], parts[1]))
    return stocks

def get_etf_list():
    etfs = []
    etf_str = os.getenv("STOCK_ETF", "")
    if etf_str:
        for item in etf_str.split(';'):
            if item.strip():
                parts = item.strip().split(':', 1)
                if len(parts) == 2:
                    etfs.append((parts[0], parts[1]))
    return etfs

def get_index_list():
    indexes = []
    index_str = os.getenv("INDEX_LIST", "")
    if index_str:
        for item in index_str.split(';'):
            if item.strip():
                parts = item.strip().split(':', 1)
                if len(parts) == 2:
                    indexes.append((parts[0], parts[1]))
    return indexes

def main():
    message_parts = []
    
    # 日期信息
    now = datetime.now(hongkong)
    weekday_map = {0: "一", 1: "二", 2: "三", 3: "四", 4: "五", 5: "六", 6: "日"}
    message_parts.append(f"*{now.year}年{now.month}月{now.day}日  星期{weekday_map[now.weekday()]}*  ")
    
    # 农历日期
    solar_today = Solar(now.year, now.month, now.day)
    lunar_today = Converter.Solar2Lunar(solar_today)
    lunar_month_names = ["正月", "二月", "三月", "四月", "五月", "六月", 
                        "七月", "八月", "九月", "十月", "冬月", "腊月"]
    lunar_day_names = ["初一", "初二", "初三", "初四", "初五", "初六", "初七", "初八", "初九", "初十",
                      "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十",
                      "廿一", "廿二", "廿三", "廿四", "廿五", "廿六", "廿七", "廿八", "廿九", "三十"]
    
    message_parts.append(f"  农历{lunar_month_names[lunar_today.month-1]}{lunar_day_names[lunar_today.day-1]}\n\n")
    
    # 提醒事项
    reminders = get_reminders()
    if reminders:
        message_parts.extend([f"• *{reminder}*\n" for reminder in reminders])
    
    # 天气信息
    rain_info = get_tomorrow_rain_info()
    if rain_info:
        message_parts.append(rain_info)
    
    message_parts.append("-" * 38 + "\n")
    
    # 获取主要指数（从环境变量读取）
    for code, name in get_index_list():
        if code.startswith('sh') or code.startswith('sz'):
            message_parts.append(get_ci_stock(code, name))
        elif code == 'HSI':
            message_parts.append(get_hk_index(code, name))
        elif code in ['IXIC', 'DJI']:
            message_parts.append(get_usa_index(code, name))
    
    message_parts.append("-" * 38 + "\n")
    
    # 获取ETF数据
    for code, name in get_etf_list():
        message_parts.append(get_etf_stock(code, name))
    
    # 获取A股数据
    for code, name in get_stock_list():
        message_parts.append(get_cn_stock(code, name))

    message_parts.append("-" * 38 + "\n")
    
    # 获取黄金数据
    gold_data = get_gold_data_formatted()
    if gold_data:
        message_parts.append(gold_data)
    
    # ================== 替换 yfinance 为 Oil Price API ==================
    # 获取原油数据（使用 Oil Price API）
    brent_data = OilPriceService.format_oil_price("BRENT_CRUDE_USD", "布伦特原油")
    if brent_data:
        message_parts.append(brent_data)
    
    wti_data = OilPriceService.format_oil_price("WTI_CRUDE_USD", "WTI原油")
    if wti_data:
        message_parts.append(wti_data)
    
    # 获取 USD/CNY 汇率
    message_parts.append(get_usd_cny_formatted())
    
    # 发送消息
    full_message = "".join([str(part) for part in message_parts if part])
    send_to_telegram(full_message)

if __name__ == "__main__":
    main()