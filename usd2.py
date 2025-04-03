import yfinance as yf
import requests
import os
import logging
from dotenv import load_dotenv
import time

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 加载环境变量
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_API_KEY")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
JUHE_STOCK_KEY = os.getenv("JUHE_STOCK_KEY")

# 检查环境变量是否设置
if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID or not JUHE_STOCK_KEY:
    logging.error("请设置 TELEGRAM_API_KEY, TELEGRAM_CHAT_ID 和 JUHE_STOCK_KEY 环境变量")
    exit(1)

# ✅  MarkdownV1 特殊字符转义
def escape_markdown(text):
    text = text.replace("_", "\\_")
    text = text.replace("*", "\\*")
    text = text.replace("[", "\\[")
    text = text.replace("`", "\\`")
    return text

def format_price(price):
    return f"{price:.2f}"

def get_price(symbol, name, retries=3):
    for i in range(retries):
        try:
            ticker = yf.Ticker(symbol)
            data = ticker.history(period="2d")
            if len(data) >= 2:
                price = data['Close'].iloc[-1]
                prev_close = data['Close'].iloc[-2]
                price_change = price - prev_close  # 涨跌点数
                percent_change = ((price - prev_close) / prev_close) * 100

                if price_change > 0:
                    emoji = "🔴"
                    color = f"*{escape_markdown(format_price(price))}* (+{escape_markdown(format_price(price_change))}, +{escape_markdown(f'{percent_change:.2f}%')})"
                else:
                    emoji = "🔵"
                    color = f"*{escape_markdown(format_price(price))}* ({escape_markdown(format_price(price_change))}, {escape_markdown(f'{percent_change:.2f}%')})"

                return f"{emoji} {escape_markdown(name)}: {color}\n"  # 使用换行符
            else:
                logging.warning(f"未能获取 {name} ({symbol}) 的足够数据")
                return f"⚠️ 未能获取 {escape_markdown(name)} 的数据\n" # 使用换行符
        except Exception as e:
            logging.error(f"获取 {name} ({symbol}) 数据时出错 (尝试 {i+1}/{retries}): {e}")
            if i < retries - 1:
                time.sleep(2)  # 等待2秒后重试
            else:
                return f"⚠️ 获取 {escape_markdown(name)} 数据时出错\n" # 使用换行符
    return f"⚠️ 获取 {escape_markdown(name)} 数据时出错\n"

def send_to_telegram(message, retries=3):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"  # 设置 parse_mode 为 Markdown
    }

    # 检查消息长度
    if len(message) > 4096:
        logging.warning(f"消息长度超过限制 ({len(message)} > 4096)，将截断消息")
        message = message[:4096]
        payload["text"] = message


    for i in range(retries):
        try:
            logging.debug(f"尝试发送消息 (尝试 {i+1}/{retries}): {message}")  # 打印消息
            response = requests.post(url, json=payload)
            response.raise_for_status()  # 抛出 HTTPError 异常，如果状态码不是 200

            if response.status_code == 200:
                logging.info("成功发送到 Telegram")
                return
            else:
                logging.error(f"发送失败 (尝试 {i+1}/{retries}): {response.json()}")

        except requests.exceptions.RequestException as e:
            logging.error(f"发送到 Telegram 时发生网络错误 (尝试 {i+1}/{retries}): {e}")

    logging.error("多次尝试后发送到 Telegram 失败")

def get_cn_stock(gid: str, name: str):
    """通用A股数据获取（兼容个股/指数/科创板）"""
    try:
        # 动态设置请求参数
        params = {"key": JUHE_STOCK_KEY, "gid": gid}
        
        # 如果是股票代码（非指数），添加市场类型参数
        if not gid in ['sh000001', 'sz399001']:
            market_type = '0' if gid.startswith('sh') else '1'
            params['type'] = market_type

        response = requests.get(
            url="http://web.juhe.cn/finance/stock/hs",
            params=params,
            timeout=15
        )
        data = response.json()
        
        if data.get('error_code') != 0:
            logging.error(f"[{name}] 接口错误: {data.get('reason')}")
            return None

        result = data['result']
        
        # 解析不同数据结构
        if isinstance(result, list):
            # 个股数据结构（含科创板）
            stock_data = result[0]['data']
            price = stock_data.get('nowPri')
            increase = stock_data.get('increase')
            percent = stock_data.get('increPer')
        else:
            # 指数数据结构
            price = result.get('nowpri')
            increase = result.get('increase')
            percent = result.get('increPer')
        
        # 字段验证
        if not all([price, increase, percent]):
            raise ValueError(f"缺失关键字段: {data}")
            
        return {
            'gid': gid,
            'name': name,
            'price': float(price),
            'change': float(increase),
            'percent': float(percent)
        }

    except Exception as e:
        logging.error(f"[{name}] 处理异常: {str(e)}")
    return None

def get_us_index(gid: str, name: str):
    """美股指数获取（保持不变）"""
    try:
        response = requests.get(
            url="http://web.juhe.cn/finance/stock/usa",
            params={
                "key": JUHE_STOCK_KEY,
                "gid": gid.lower()
            },
            timeout=15
        )
        data = response.json()
        
        if data.get('error_code') != 0:
            logging.error(f"[{name}] 接口错误: {data.get('reason')}")
            return None

        stock_data = data['result'][0]['data']
        return {
            'gid': gid,
            'name': name,
            'price': float(stock_data['lastestpri'].replace(',', '')),
            'change': float(stock_data['uppic']),
            'percent': float(stock_data['limit'])
        }

    except Exception as e:
        logging.error(f"[{name}] 处理异常: {str(e)}")
    return None

def format_stock_info(data):
    if not data:
        return ""

    name = escape_markdown(data['name'])
    price = format_price(data['price'])
    change = format_price(data['change'])
    percent = f"{data['percent']:.2f}%"

    if data['change'] > 0:
        emoji = "🔴"
        color = f"*{escape_markdown(price)}* (+{escape_markdown(change)}, +{escape_markdown(percent)})"
    else:
        emoji = "🔵"
        color = f"*{escape_markdown(price)}* ({escape_markdown(change)}, {escape_markdown(percent)})"

    return f"{emoji} {name}: {color}\n"


def main():
    message = "*📊 市场数据更新：*\n\n" # 使用 Markdown 格式

    # ✅ 指数 (使用新方法)
    cn_indexes = [
        {'gid': 'sh000001', 'name': '上证指数'},
        {'gid': 'sz399001', 'name': '深证成指'},
    ]

    us_indexes = [
        {'gid': 'IXIC', 'name': '纳斯达克'},
        {'gid': 'DJI', 'name': '道琼斯'}
    ]

    for index in cn_indexes:
        data = get_cn_stock(index['gid'], index['name'])
        if data:
            message += format_stock_info(data)
        else:
            message += f"⚠️ 获取 {escape_markdown(index['name'])} 数据时出错\n"

    for index in us_indexes:
        data = get_us_index(index['gid'], index['name'])
        if data:
            message += format_stock_info(data)
        else:
            message += f"⚠️ 获取 {escape_markdown(index['name'])} 数据时出错\n"

    # ✅ 添加分割线
    message += "\n----------------------------\n" # 使用 Markdown 格式

    # ✅ 股票
    message += get_price("510300.SS", "沪深300")
    message += get_price("512660.SS", "军工ETF")
    message += get_price("300059.SZ", "东方财富")
    message += get_price("600150.SS", "中国船舶")
    message += get_price("000823.SZ", "超声电子")
    message += get_price("000725.SZ", "京东方A")
    message += get_price("300065.SZ", "海兰信")
    message += get_price("300207.SZ", "欣旺达")
    message += get_price("002594.SZ", "比亚迪")

    # ✅ 添加分割线
    message += "----------------------------\n\n" # 使用 Markdown 格式

    # ✅ 商品 & 汇率
    message += get_price("GC=F", "黄金")
    message += get_price("BZ=F", "原油")
    message += get_price("USDCNY=X", "USD/CNY")

    # ✅ 发送到Telegram
    send_to_telegram(message)

if __name__ == "__main__":
    main()
