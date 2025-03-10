import asyncio
import aiohttp
import logging
import re
import os
import feedparser
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv
from telegram import Bot
from tencentcloud.common import credential
from tencentcloud.common.profile.client_profile import ClientProfile
from tencentcloud.common.profile.http_profile import HttpProfile
from tencentcloud.tmt.v20180321 import tmt_client, models

# 配置加载
load_dotenv()

# 异步安全配置
MAX_CONCURRENT_REQUESTS = 5
RETENTION_DAYS = 30  # 30天历史记录保留
MAX_HISTORY_ENTRIES = 1500  # 内存最大保留1500条
REQUEST_TIMEOUT = 30
TELEGRAM_DELAY = 0.3

# RSS源配置
RSS_FEEDS = [
     'https://feeds.bbci.co.uk/news/world/rss.xml',  # bbc
    # 'https://www3.nhk.or.jp/rss/news/cat6.xml',  # nhk
    # 'http://www3.nhk.or.jp/rss/news/cat5.xml',  # nhk金融
    # 'https://www.cnbc.com/id/100003114/device/rss/rss.html',  # CNBC
   #  'https://feeds.a.dj.com/rss/RSSWorldNews.xml',  # 华尔街日报
    # 'https://www.aljazeera.com/xml/rss/all.xml',  # 半岛电视台
    # 'https://www.ft.com/?format=rss',  # 金融时报
    # 'http://rss.cnn.com/rss/edition.rss',  # cnn
]

# 环境变量
TELEGRAM_BOT_TOKEN = os.getenv("RSS_TWO")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TENCENTCLOUD_SECRET_ID = os.getenv("TENCENTCLOUD_SECRET_ID")
TENCENTCLOUD_SECRET_KEY = os.getenv("TENCENTCLOUD_SECRET_KEY")

# 获取当前脚本的绝对路径
script_dir = os.path.dirname(os.path.abspath(__file__))
# 构建绝对路径
SENT_ENTRIES_FILE = os.path.join(script_dir, "rss.json")
LOG_FILE = os.path.join(script_dir, "rss_bot.log")

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)

class AsyncRSSBot:
    def __init__(self):
        self.sent_entries = []  # 存储格式：{'id': str, 'timestamp': float}
        self.lock = asyncio.Lock()
        self.bot = Bot(token=TELEGRAM_BOT_TOKEN)
        self.translate_client = None
        self.session = None

    async def initialize(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT))
        await self.load_history()
        self.init_translate_client()

    def init_translate_client(self):
        cred = credential.Credential(TENCENTCLOUD_SECRET_ID, TENCENTCLOUD_SECRET_KEY)
        http_profile = HttpProfile(endpoint="tmt.tencentcloudapi.com")
        client_profile = ClientProfile(httpProfile=http_profile)
        self.translate_client = tmt_client.TmtClient(cred, "na-siliconvalley", client_profile)

    async def cleanup(self):
        await self.session.close()
        await self.save_history()

    async def load_history(self):
        """加载历史记录并执行数据清理"""
        try:
            if os.path.exists(SENT_ENTRIES_FILE):
                with open(SENT_ENTRIES_FILE, 'r') as f:
                    entries = json.load(f)
                    
                    # 数据格式迁移（兼容旧版本）
                    converted = []
                    for entry in entries:
                        if isinstance(entry, str):
                            # 旧格式转换：添加当前时间戳（会被后续过滤）
                            converted.append({'id': entry, 'timestamp': datetime.now().timestamp()})
                        else:
                            converted.append(entry)
                    
                    # 执行双重过滤
                    cutoff = datetime.now().timestamp() - RETENTION_DAYS * 86400
                    valid_entries = [
                        e for e in converted
                        if e['timestamp'] >= cutoff
                    ][-MAX_HISTORY_ENTRIES:]  # 先时间过滤，再数量限制
                    
                    async with self.lock:
                        self.sent_entries = valid_entries
                    
                    logging.info(f"Loaded {len(valid_entries)} entries (last {RETENTION_DAYS} days & max {MAX_HISTORY_ENTRIES} items)")
        except Exception as e:
            logging.error(f"Error loading history: {str(e)}")

    async def save_history(self):
        """保存历史记录并执行清理"""
        try:
            async with self.lock:
                # 双重清理策略
                cutoff = datetime.now().timestamp() - RETENTION_DAYS * 86400
                valid_entries = [
                    e for e in self.sent_entries
                    if e['timestamp'] >= cutoff
                ][-MAX_HISTORY_ENTRIES:]  # 先按时间过滤，再按数量限制
                
                with open(SENT_ENTRIES_FILE, 'w') as f:
                    json.dump(valid_entries, f, indent=2)
                
                # 更新内存中的记录
                self.sent_entries = valid_entries
            
            logging.info(f"Saved {len(valid_entries)} entries (last {RETENTION_DAYS} days & max {MAX_HISTORY_ENTRIES} items)")
        except Exception as e:
            logging.error(f"Error saving history: {str(e)}")

    def sanitize_markdown(self, text):
        text = re.sub(r'<[^>]*>', '', text)
        return re.sub(r'([*_`|#\\[\\](){}<>~+\-=!@%^&])', r'\\\1', text)

    async def translate_text(self, text):
        try:
            req = models.TextTranslateRequest()
            req.SourceText = text
            req.Source = "auto"
            req.Target = "zh"
            req.ProjectId = 0
            resp = self.translate_client.TextTranslate(req)
            return resp.TargetText
        except Exception as e:
            logging.error(f"Translation error: {str(e)}")
            return text

    async def safe_send_message(self, chat_id, message):
        max_length = 4096
        chunks = [message[i:i+max_length] for i in range(0, len(message), max_length)]
        
        for chunk in chunks:
            for attempt in range(3):
                try:
                    await self.bot.send_message(
                        chat_id=chat_id,
                        text=chunk,
                        parse_mode='Markdown',
                        disable_web_page_preview=True
                    )
                    await asyncio.sleep(TELEGRAM_DELAY)
                    break
                except Exception as e:
                    logging.warning(f"Attempt {attempt+1} failed: {str(e)}")
                    if attempt == 2:
                        raise
                    await asyncio.sleep(2 ** attempt)

    async def process_entry(self, entry, source_name):
        entry_id = entry.get('link') or entry.get('id')
        if not entry_id:
            logging.warning("Entry missing ID, skipping")
            return False

        # 检查是否已存在
        async with self.lock:
            existing_ids = {e['id'] for e in self.sent_entries}
            if entry_id in existing_ids:
                return False

        title = self.sanitize_markdown(entry.get('title', 'Untitled'))
        summary = self.sanitize_markdown(entry.get('summary', 'No summary'))
        
        # 并行翻译
        translated_title, translated_summary = await asyncio.gather(
            self.translate_text(title),
            self.translate_text(summary)
        )
        
        message = f"*{translated_title}*\n\n{translated_summary}\n[{source_name}]({entry_id})"
        
        try:
            await self.safe_send_message(TELEGRAM_CHAT_ID, message)
            async with self.lock:
                # 添加新条目并执行内存清理
                self.sent_entries.append({
                    'id': entry_id,
                    'timestamp': datetime.now().timestamp()
                })
                # 实时维护内存数据
                cutoff = datetime.now().timestamp() - RETENTION_DAYS * 86400
                self.sent_entries = [
                    e for e in self.sent_entries
                    if e['timestamp'] >= cutoff
                ][-MAX_HISTORY_ENTRIES:]
            return True
        except Exception as e:
            logging.error(f"Failed to send message: {str(e)}")
            return False

    async def fetch_feed(self, feed_url):
        headers = {'User-Agent': 'Mozilla/5.0'}
        try:
            async with self.session.get(feed_url, headers=headers) as response:
                content = await response.text()
                return feedparser.parse(content)
        except Exception as e:
            logging.error(f"Failed to fetch {feed_url}: {str(e)}")
            return None

    async def process_feed(self, feed_url):
        feed = await self.fetch_feed(feed_url)
        if not feed or not feed.entries:
            return 0
        
        source_name = feed.feed.get('title', feed_url)
        count = 0
        
        for entry in reversed(feed.entries):
            if await self.process_entry(entry, source_name):
                count += 1
        return count

    async def run(self):
        await self.initialize()
        try:
            semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
            
            async def limited_task(feed_url):
                async with semaphore:
                    return await self.process_feed(feed_url)
            
            tasks = [limited_task(url) for url in RSS_FEEDS]
            results = await asyncio.gather(*tasks)
            logging.info(f"Total {sum(results)} new messages sent")
        finally:
            await self.cleanup()

if __name__ == "__main__":
    bot = AsyncRSSBot()
    asyncio.run(bot.run())
