import yfinance as yf
import requests
import os
import logging
from dotenv import load_dotenv

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 加载环境变量
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_API_KEY")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# 检查环境变量是否设置
if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    logging.error("请设置 TELEGRAM_API_KEY 和 TELEGRAM_CHAT_ID 环境变量")
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

def get_price(symbol, name):
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
                color = f"*{escape_markdown(format_price(price))}* (+{escape_markdown(format_price(price_change))}点, +{escape_markdown(f'{percent_change:.2f}%')})"
            else:
                emoji = "🔵"
                color = f"*{escape_markdown(format_price(price))}* ({escape_markdown(format_price(price_change))}点, {escape_markdown(f'{percent_change:.2f}%')})"

            return f"{emoji} {escape_markdown(name)}: {color}\n"  # 使用换行符
        else:
            logging.warning(f"未能获取 {name} ({symbol}) 的足够数据")
            return f"⚠️ 未能获取 {escape_markdown(name)} 的数据\n" # 使用换行符
    except Exception as e:
        logging.error(f"获取 {name} ({symbol}) 数据时出错: {e}")
        return f"⚠️ 获取 {escape_markdown(name)} 数据时出错\n" # 使用换行符


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



def main():
    message = "*📊 市场数据更新：*\n\n" # 使用 Markdown 格式

    # ✅ 指数
    message += get_price("000001.SS", "上证指数")
    message += get_price("399001.SZ", "深证成指")
    message += get_price("^DJI", "道琼斯")
    message += get_price("^IXIC", "纳斯达克")

    # ✅ 添加分割线
    message += "\n----------------------------\n" # 使用 Markdown 格式

    # ✅ 股票
    message += get_price("510300.SS", "沪深300ETF")
    message += get_price("512660.SS", "军工ETF")
    message += get_price("300059.SZ", "东方财富")
    message += get_price("600150.SS", "中国船舶")
    message += get_price("000823.SZ", "超声电子")
    message += get_price("000725.SZ", "京东方A")
    message += get_price("300065.SZ", "海兰信")
    message += get_price("300207.SZ", "欣旺达")

    # ✅ 添加分割线
    message += "----------------------------\n\n" # 使用 Markdown 格式

    # ✅ 商品 & 汇率
    message += get_price("GC=F", "黄金")
    message += get_price("BZ=F", "布伦特原油")
    message += get_price("USDCNY=X", "USD/CNY汇率")

    # ✅ 发送到Telegram
    send_to_telegram(message)

if __name__ == "__main__":
    main()
