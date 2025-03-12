import requests
from .telegram import telegram

class StockMonitor:
    def __init__(self):
        self.cn_sources = [
            {'gid': 'sh000001', 'name': '上证指数'},
            {'gid': 'sz399001', 'name': '深证成指'}
        ]
        
        self.us_sources = [
            {'gid': 'IXIC', 'name': '纳斯达克'},
            {'gid': 'DJI', 'name': '道琼斯'}
        ]
        
    def generate_report(self):
        # 原usd.py中的获取逻辑...
        
        if valid_data:
            plain, html = self._format_data(valid_data)
            return {
                'plain': "📈 行情数据\n" + plain,
                'html': f"<b>📈 行情数据</b><br/>{html}"
            }
        return None
    
    def _format_data(self, data_list):
        # 原generate_report逻辑...
        return plain, html