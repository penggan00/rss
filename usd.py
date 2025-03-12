import os
import requests
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

class Config:
    STOCK_API_KEY = os.getenv("JUHE_STOCK_KEY")
    BOT_TOKEN = os.getenv("TELEGRAM_API_KEY")
    CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def get_cn_stock(gid: str, name: str):
    """通用A股数据获取（兼容个股/指数/科创板）"""
    try:
        # 动态设置请求参数
        params = {"key": Config.STOCK_API_KEY, "gid": gid}
        
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
            print(f"[{name}] 接口错误: {data.get('reason')}")
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
        print(f"[{name}] 处理异常: {str(e)}")
    return None

def get_us_index(gid: str, name: str):
    """美股指数获取（保持不变）"""
    try:
        response = requests.get(
            url="http://web.juhe.cn/finance/stock/usa",
            params={
                "key": Config.STOCK_API_KEY,
                "gid": gid.lower()
            },
            timeout=15
        )
        data = response.json()
        
        if data.get('error_code') != 0:
            print(f"[{name}] 接口错误: {data.get('reason')}")
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
        print(f"[{name}] 处理异常: {str(e)}")
    return None

# 数据源配置（保持不变）
DATA_SOURCES = [
    {'gid': 'sh000001', 'name': '上证指数'},
    {'gid': 'sz399001', 'name': '深证成指'},
]

US_INDEXES = [
    {'gid': 'IXIC', 'name': '纳斯达克'},
    {'gid': 'DJI', 'name': '道琼斯'}
]

# 格式化和发送函数（保持不变）
def format_change(value: float) -> str:
    if value > 0:
        return f'▲{abs(value):.2f}'
    elif value < 0:
        return f'▼{abs(value):.2f}'
    return '━'

def generate_report(data_list):
    report = [
        "🕒 <b>实时行情报告</b>",
        "══════════════",
    ]
    
    market_symbols = {
        '上证指数': '🇨🇳 CSI',
        '深证成指': '🇨🇳 SZSE',
        '纳斯达克': '🌐 NASDAQ',
        '道琼斯': '🌐 DOW30'
    }
    
    for data in data_list:
        if not data:
            continue
            
        precision = 2  # 统一保留两位小数
        symbol = market_symbols.get(data['name'], '📊')
        line = [
            f"{symbol} <b>{data['name']}</b>",
            f"价格：<code>{data['price']:,.{precision}f}</code>",
            f"涨跌：{format_change(data['change'])} <b>{data['change']:+.2f}</b>",
            f"幅度：<b>({data['percent']:+.2f}%)</b>",
            "────────────────"
        ]
        report.append("\n".join(line))
    
    report.append("")
    return "\n".join(report)

def send_telegram(message: str):
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{Config.BOT_TOKEN}/sendMessage",
            json={
                "chat_id": Config.CHAT_ID,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            },
            timeout=15
        )
        return response.status_code == 200
    except Exception as e:
        print(f"❌ 消息发送失败: {str(e)}")
        return False

def main():
    cn_data = [get_cn_stock(source['gid'], source['name']) for source in DATA_SOURCES]
    us_data = [get_us_index(source['gid'], source['name']) for source in US_INDEXES]
    valid_data = [d for d in cn_data + us_data if d]
    
    report = generate_report(valid_data)
    if send_telegram(report):
        print("✅ 行情推送成功")
    else:
        print("❌ 推送失败，请检查配置")

if __name__ == "__main__":
    main()