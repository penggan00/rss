import os
import requests
import html
from dotenv import load_dotenv

load_dotenv()

class TelegramSender:
    def __init__(self):
        self.token = os.getenv('TELEGRAM_API_KEY')
        self.chat_id = os.getenv('TELEGRAM_CHAT_ID')
    
    def send(self, content_blocks: list):
        """发送组合消息"""
        plain_parts = []
        html_parts = []
        
        for block in content_blocks:
            plain_parts.append(block['plain'])
            html_parts.append(block['html'])
        
        plain = '\n\n'.join(plain_parts)
        html_content = '<br/><br/>'.join(html_parts)
        
        try:
            response = requests.post(
                f'https://api.telegram.org/bot{self.token}/sendMessage',
                json={
                    'chat_id': self.chat_id,
                    'text': html_content,
                    'parse_mode': 'HTML',
                    'disable_web_page_preview': True
                },
                timeout=10
            )
            response.raise_for_status()
            print("✓ 组合消息发送成功")
            return True
        except Exception as e:
            print(f"✗ 消息发送失败: {str(e)}")
            return False

telegram = TelegramSender()