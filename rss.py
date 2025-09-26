import asyncio
import aiohttp
import logging
import re
import os
import hashlib
import pytz
import fcntl
import time
import signal
import sys
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from feedparser import parse
from telegram import Bot
from telegram.error import BadRequest
from urllib.parse import urlparse
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from md2tgmd import escape
from tencentcloud.common import credential
from tencentcloud.common.profile.client_profile import ClientProfile
from tencentcloud.common.profile.http_profile import HttpProfile
from tencentcloud.tmt.v20180321 import tmt_client, models
from tencentcloud.common.exception.tencent_cloud_sdk_exception import TencentCloudSDKException

# ========== 环境加载 ==========
load_dotenv()
BASE_DIR = Path(__file__).resolve().parent
LOCK_FILE = BASE_DIR / "rss.lock"   # 锁文件路径
DATABASE_FILE = BASE_DIR / "rss.db" # SQLite 数据库文件路径

logging.basicConfig(
    filename=BASE_DIR / "rss.log",  # 日志文件路径
    level=logging.WARNING,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    encoding="utf-8"
)
logger = logging.getLogger(__name__) # 全局日志记录器

TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID").split(",")  # Telegram Chat IDs
TENCENTCLOUD_SECRET_ID = os.getenv("TENCENTCLOUD_SECRET_ID") # 腾讯云 Secret ID
TENCENTCLOUD_SECRET_KEY = os.getenv("TENCENTCLOUD_SECRET_KEY") # 腾讯云 Secret Key
TENCENT_REGION = os.getenv("TENCENT_REGION", "na-siliconvalley") # 腾讯云区域
TENCENT_SECRET_ID = os.getenv("TENCENT_SECRET_ID") # 腾讯 Secret ID
TENCENT_SECRET_KEY = os.getenv("TENCENT_SECRET_KEY")  # 腾讯 Secret Key
semaphore = asyncio.Semaphore(2)                     # 控制并发数 
BACKUP_DOMAINS_STR = os.getenv("BACKUP_DOMAINS", "") # 备用域名，多个用逗号分隔
BACKUP_DOMAINS = [domain.strip() for domain in BACKUP_DOMAINS_STR.split(",") if domain.strip()] # 备用域名列表
# 定义时间间隔 (秒)  600秒 = 10分钟   1200秒 = 20分钟   1800秒 = 30分钟  3600秒 = 1小时   7200秒 = 2小时   10800秒 = 3小时
RSS_GROUPS = [ # RSS 组配置列表
    # ================== 国际新闻组 ==================False: 关闭 / True: 开启
    {
        "name": "国际新闻",
        "urls": [
            'https://feeds.bbci.co.uk/news/world/rss.xml',  # BBC
            'https://www3.nhk.or.jp/rss/news/cat6.xml',     # NHK
       #     'https://www.cnbc.com/id/100003114/device/rss/rss.html',  # CNBC
         #   'https://feeds.a.dj.com/rss/RSSWorldNews.xml',  # 华尔街日报
        #    'https://feeds.content.dowjones.io/public/rss/RSSWorldNews',   # 华尔街日报
        #    'https://feeds.content.dowjones.io/public/rss/socialeconomyfeed',
           'https://www.aljazeera.com/xml/rss/all.xml',    # 半岛电视台
        #    'https://www.ft.com/?format=rss',                 # 金融时报
       #     'https://www3.nhk.or.jp/rss/news/cat5.xml',  # NHK 商业
       #     'http://rss.cnn.com/rss/cnn_topstories.rss',   # cnn
       #     'https://www.theguardian.com/world/rss',     # 卫报
      #      'https://www.theverge.com/rss/index.xml',   # The Verge:
        ],
        "group_key": "RSS_FEEDS",
        "interval": 3590,      # 60分钟 
        "history_days": 30,     # 新增，保留30天
        "bot_token": os.getenv("RSS_TWO"),    # Telegram Bot Token
        "processor": {
            "translate": True,       #翻译开
            "header_template": "📢 *{source}*\n",  # 新增标题模板 ★
            "template": "*{subject}*\n[more]({url})",
            "preview": False,         # 禁止预览
            "show_count": False        # ✅新增
        }
    },

    # ================== 国际新闻中文组 ==================False: 关闭 / True: 开启
    {
        "name": "国际新闻中文",
        "urls": [
            'https://www.ftchinese.com/rss/news',   # ft中文网
        ],
        "group_key": "RSS_FEEDS_INTERNATIONAL",
        "interval": 10790,      # 3小时
        "history_days": 30,     # 新增，保留30天
        "bot_token": os.getenv("RSS_TWO"),    # Telegram Bot Token
        "processor": {
            "translate": False,       #翻译 False: 关闭 / True: 开启
            "header_template": "📢 *{source}*\n",  # 新增标题模板 ★
            "template": "*{subject}*\n[more]({url})",
            "preview": False,         # 禁止预览
            "show_count": False        # ✅新增
        }
    },

    # ================== 快讯组 ==================
    {
        "name": "快讯",
        "urls": [
         #   'https://rsshub.app/10jqka/realtimenews', #同花顺财经
            'https://36kr.com/feed-newsflash',  # 36氪快讯
        #    'https://36kr.com/feed',  # 36氪综合
            
        ],
        "group_key": "FOURTH_RSS_FEEDS",
        "interval": 960,       # 20分钟 
        "history_days": 5,     # 新增，保留30天
        "bot_token": os.getenv("RSS_LINDA"),   # Telegram Bot Token
        "processor": {
            "translate": False,     #翻译开关
            "header_template": "📢 *{source}*\n",  # 新增标题模板 ★
            "template": "*{subject}*\n[more]({url})",
            "preview": False,            # 禁止预览
            "show_count": False          #计数
        }
    },
    # ================== 快讯组 ==================
    {
        "name": "快讯",
        "urls": [
            'https://rsshub.app/10jqka/realtimenews', #同花顺财经
         #   'https://36kr.com/feed-newsflash',  # 36氪快讯
        #    'https://36kr.com/feed',  # 36氪综合
            
        ],
        "group_key": "TOURTH_RSS_FEEDS",
        "interval": 650,       # 15分钟
        "history_days": 5,     # 新增，保留30天
        "bot_token": os.getenv("TONGHUASHUN_RSS"),  #   Telegram Bot Token
        "processor": {
            "translate": False,     #翻译开关
            "header_template": "📢 *{source}*\n",  # 新增标题模板 ★
            "template": "*{subject}*\n[more]({url})",
            "filter": {
                "enable": False,  # 过滤开关     False: 关闭 / True: 开启
                "mode": "allow",  # allow模式：包含关键词才发送 / block模式：包含关键词不发送
                "keywords": ["免", "cf", "cl", "黑", "低", "小", "卡", "年", "bug", "白", "github",  "节",  "闪",  "cc", "rn", "动", "cloudcone", "docker", "折"]  # 本组关键词列表
            },
            "preview": False,            # 禁止预览
            "show_count": False          #计数
        }
    },

    # ================== 新浪博客 ==================
    {
        "name": "社交媒体",
        "urls": [
            'https://rsshub.app/weibo/user/3194547262',  # 江西高速
            'https://rsshub.app/weibo/user/1699432410',  # 新华社
        #    'https://rsshub.app/weibo/user/2656274875',  # 央视新闻
            'https://rsshub.app/weibo/user/2716786595',  # 聚萍乡
            'https://rsshub.app/weibo/user/1891035762',  # 交警
       #     'https://rsshub.app/weibo/user/3917937138',  # 发布
        #    'https://rsshub.app/weibo/user/3213094623',  # 邮政
        #    'https://rsshub.app/weibo/user/2818241427',  # 冒险岛

        ],
        "group_key": "FIFTH_RSSSA_FEEDS",
        "interval": 10790,    # 3小时
        "history_days": 300,     # 新增，保留300天
        "bot_token": os.getenv("RRSS_LINDA"),  # Telegram Bot Token
        "processor": {
            "translate": False,     #翻译关
            "header_template": "📢 *{source}*\n",  # 新增标题模板 ★
         #   "template": "*{subject}*\n🔗 {url}",
            "template": "*{subject}*\n[more]({url})",
            "preview": False,        # 禁止预览
            "show_count": False     #计数
        }
    },

    # ================== 技术论坛组 ==================
    {
        "name": "技术论坛",
        "urls": [
            'https://rss.nodeseek.com',  # Nodeseek  
        ],
        "group_key": "FIFTH_RSS_RSS_SAN", 
        "interval": 240,       # 4分钟 
        "history_days": 3,     # 新增，保留30天
        "bot_token": os.getenv("RSS_SAN"), # Telegram Bot Token
        "processor": {
            "translate": False,                  #翻译关
            "header_template": "📢 *{source}*\n",  # 新增标题模板 ★
            "template": "*{subject}*\n[more]({url})", 
            "filter": {
                "enable": False,  # 过滤开关     False: 关闭 / True: 开启
                "mode": "allow",  # allow模式：包含关键词才发送 / block模式：包含关键词不发送
                "keywords": ["免", "cf", "cl", "黑", "低", "小", "卡", "年", "bug", "白", "github",  "节",  "闪",  "cc", "rn", "动", "cloudcone", "docker", "折"]  # 本组关键词列表
            },
            "preview": False,              # 禁止预览
            "show_count": False               #计数
        }
    },
    {
        "name": "社交媒体",
        "urls": [
        #    'https://lowendspirit.com/discussions/feed.rss', # lowendspirit
       #     'https://lowendtalk.com/discussions/feed.rss',   # lowendtalk
     
        ],
        "group_key": "FIFTHHHH_RSSS_FEEDS",
        "interval": 7190,      # 1小时56分钟
        "history_days": 30,     # 新增，保留30天
        "bot_token": os.getenv("RSS_SAN"),  # Telegram Bot Token
        "processor": {
            "translate": True,                     #翻译开
            "header_template": "📢 *{source}*\n",  # 新增标题模板 ★
         #   "template": "*{subject}*\n🔗 {url}",
            "template": "*{subject}*\n[more]({url})",  #新增
            "preview": False,       # 禁止预览
            "show_count": False     #计数
        }
    },
    # ================== YouTube频道组 ==================
    {
        "name": "YouTube频道",
        "urls": [
         #   'https://blog.090227.xyz/atom.xml',
         #   'https://www.freedidi.com/feed',
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCvijahEyGtvMpmMHBu4FS2w', # 零度解说
            'https://www.youtube.com/feeds/videos.xml?channel_id=UC96OvMh0Mb_3NmuE8Dpu7Gg', # 搞机零距离
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCQoagx4VHBw3HkAyzvKEEBA', # 科技共享
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCbCCUH8S3yhlm7__rhxR2QQ', # 不良林
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCMtXiCoKFrc2ovAGc1eywDg', # 一休
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCii04BCvYIdQvshrdNDAcww', # 悟空的日常
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCJMEiNh1HvpopPU3n9vJsMQ', # 理科男士
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCYjB6uufPeHSwuHs8wovLjg', # 中指通
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCSs4A6HYKmHA2MG_0z-F0xw', # 李永乐老师
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCZDgXi7VpKhBJxsPuZcBpgA', # 可恩KeEn
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCxukdnZiXnTFvjF5B5dvJ5w', # 甬哥侃侃侃ygkkk
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCUfT9BAofYBKUTiEVrgYGZw', # 科技分享
            'https://www.youtube.com/feeds/videos.xml?channel_id=UC51FT5EeNPiiQzatlA2RlRA', # 乌客wuke
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCDD8WJ7Il3zWBgEYBUtc9xQ', # jack stone
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCWurUlxgm7YJPPggDz9YJjw', # 一瓶奶油
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCvENMyIFurJi_SrnbnbyiZw', # 酷友社
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCmhbF9emhHa-oZPiBfcLFaQ', # WenWeekly
            'https://www.youtube.com/feeds/videos.xml?channel_id=UC3BNSKOaphlEoK4L7QTlpbA', # 中外观察
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCXk0rwHPG9eGV8SaF2p8KUQ', # 烏鴉笑笑
                    # ... 其他YouTube频道（共18个）
        ],
        "group_key": "YOUTUBE_RSSS_FEEDS", # YouTube频道
        "interval": 3590,      # 60分钟
        "history_days": 360,     # 新增，保留30天
        "bot_token": os.getenv("RSS_TOKEN"),   # Telegram Bot Token
        "processor": {
            "translate": False,                    #翻译关
            "header_template": "📢 *{source}*\n",  # 新增标题模板 ★
            "template": "*{subject}*\n[more]({url})",
            "preview": True,                # 预览
            "show_count": False               #计数
        }
    },

    # ================== 中文YouTube组 ==================
    {
        "name": "中文YouTube",
        "urls": [
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCUNciDq-y6I6lEQPeoP-R5A', # 苏恒观察
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCXkOTZJ743JgVhJWmNV8F3Q', # 寒國人
            'https://www.youtube.com/feeds/videos.xml?channel_id=UC2r2LPbOUssIa02EbOIm7NA', # 星球熱點
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCF-Q1Zwyn9681F7du8DMAWg', # 謝宗桓-老謝來了
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCSYBgX9pWGiUAcBxjnj6JCQ', # 郭正亮頻道
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCNiJNzSkfumLB7bYtXcIEmg', # 真的很博通
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCN0eCImZY6_OiJbo8cy5bLw', # 屈機TV
         #   'https://www.youtube.com/feeds/videos.xml?channel_id=UCb3TZ4SD_Ys3j4z0-8o6auA', # BBC News 中文
       #     'https://www.youtube.com/feeds/videos.xml?channel_id=UCiwt1aanVMoPYUt_CQYCPQg', # 全球大視野
            'https://www.youtube.com/feeds/videos.xml?channel_id=UC000Jn3HGeQSwBuX_cLDK8Q', # 我是柳傑克
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCQFEBaHCJrHu2hzDA_69WQg', # 国漫说
            'https://www.youtube.com/feeds/videos.xml?channel_id=UChJ8YKw6E1rjFHVS9vovrZw', # BNE TV - 新西兰中文国际频道
          #  'https://www.youtube.com/feeds/videos.xml?channel_id=UCJncdiH3BQUBgCroBmhsUhQ', # 观察者网
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCSYBgX9pWGiUAcBxjnj6JCQ', # 郭正亮頻道
        # 影视
            'https://www.youtube.com/feeds/videos.xml?channel_id=UC7Xeh7thVIgs_qfTlwC-dag', # Marc TV
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCCD14H7fJQl3UZNWhYMG3Mg', # 温城鲤
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCQO2T82PiHCYbqmCQ6QO6lw', # 月亮說
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCHW6W9g2TJL2_Lf7GfoI5kg', # 电影放映厅
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCi2GvcaxZCN-61a0co8Smnw', # 館長
   # bilibili
       #     'https://rsshub.app/bilibili/user/video/271034954', #无限海子
        #    'https://rsshub.app/bilibili/user/video/10720688', #乌客wuke
         #   'https://rsshub.app/bilibili/user/video/33683045', #张召忠
        #    'https://rsshub.app/bilibili/user/video/9458053', #李永乐
         #   'https://rsshub.app/bilibili/user/video/456664753', #央视新闻
          #  'https://rsshub.app/bilibili/user/video/95832115', #汐朵曼
          #  'https://rsshub.app/bilibili/user/video/3546741104183937', #油管精選字幕组
          #  'https://rsshub.app/bilibili/user/video/52165725', #王骁Albert
        ],
        "group_key": "FIFTH_RSS_YOUTUBE", # YouTube频道
        "interval": 35990,     # 10小时
        "history_days": 360,     # 新增，保留300天
        "bot_token": os.getenv("YOUTUBE_RSS"),    # Telegram Bot Token
        "processor": {
        "translate": False,                    #翻译关
        "header_template": "📢 *{source}*\n",  # 新增标题模板 ★
    #   "template": "*{subject}*\n🔗 {url}",
        "template": "*{subject}*\n[more]({url})",
        "preview": True,                       # 预览
        "show_count": False                    #计数
    }
    },
    # ================== 社交媒体组+翻译预览 ==================
    {
        "name": "社交媒体",
        "urls": [
        #    'https://rsshub.app/twitter/media/clawcloud43609', # claw.cloud
         #   'https://rsshub.app/twitter/media/ElonMuskAOC',   # Elon Musk
        #    'https://rsshub.app/twitter/media/elonmusk',   # Elon Musk
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCQeRaTukNYft1_6AZPACnog',  # Asmongold
        ],
        "group_key": "FIFTH_RSS_FEEDS",   # YouTube频道
        "interval": 17990,    # 5小时
        "history_days": 300,     # 新增，保留30天
        "bot_token": os.getenv("YOUTUBE_RSS"),  # Telegram Bot Token
        "processor": {
            "translate": True,          #翻译开
            "header_template": "📢 *{source}*\n",  # 新增标题模板 ★
         #   "template": "*{subject}*\n🔗 {url}",
            "template": "*{subject}*\n[more]({url})",
            "preview": True,        # 预览
            "show_count": False     #计数
        }
    },
    # ================== 中文媒体组 ==================
    {
        "name": "中文媒体", 
        "urls": [
            'https://rsshub.app/guancha/headline',
            'https://rsshub.app/guancha',
            'https://rsshub.app/zaobao/znews/china',
        ],
        "group_key": "THIRD_RSS_FEEDS",
        "interval": 7190,      # 2小时
        "history_days": 30,     # 新增，保留30天
        "bot_token": os.getenv("RSS_LINDA_YOUTUBE"), # Telegram Bot Token
        "processor": {
            "translate": False,                        #翻译开关
            "header_template": "📢 *{source}*\n",  # 新增标题模板 ★
            "template": "*{subject}*\n[more]({url})",
            "preview": False,                             # 禁止预览
            "show_count": False                       #计数
        }
    }
]

# ========== 数据库适配层 ==========
USE_PG = False  # 仅用本地数据库
PG_URL = None

class RSSDatabase:
    def __init__(self, loop=None):
        self.loop = loop or asyncio.get_event_loop()
        self.conn = None

    async def open(self):
        import sqlite3
        self.conn = sqlite3.connect(DATABASE_FILE, check_same_thread=False)

    async def close(self):
        if self.conn:
            self.conn.close()

    async def create_tables(self):
        def _create():
            c = self.conn.cursor()
            c.execute("""
            CREATE TABLE IF NOT EXISTS rss_status (
                feed_group TEXT,
                feed_url TEXT,
                entry_url TEXT,
                entry_content_hash TEXT,
                entry_timestamp REAL,
                PRIMARY KEY (feed_group, feed_url, entry_url)
            )""")
            c.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_group_content_hash
            ON rss_status(feed_group, entry_content_hash);
            """)
            c.execute("""
            CREATE TABLE IF NOT EXISTS timestamps (
                feed_group TEXT PRIMARY KEY,
                last_run_time REAL
            )""")
            c.execute("""
            CREATE TABLE IF NOT EXISTS cleanup_timestamps (
                feed_group TEXT PRIMARY KEY,
                last_cleanup_time REAL
            )""")
            self.conn.commit()
        await self.loop.run_in_executor(None, _create)

    async def save_status(self, feed_group, feed_url, entry_url, entry_content_hash, timestamp):
        def _save():
            c = self.conn.cursor()
            c.execute(
                "INSERT OR IGNORE INTO rss_status VALUES (?, ?, ?, ?, ?)",
                (feed_group, feed_url, entry_url, entry_content_hash, timestamp)
            )
            self.conn.commit()
        await self.loop.run_in_executor(None, _save)

    async def has_content_hash(self, feed_group, content_hash):
        def _has():
            c = self.conn.cursor()
            c.execute(
                "SELECT 1 FROM rss_status WHERE feed_group=? AND entry_content_hash=? LIMIT 1",
                (feed_group, content_hash)
            )
            return c.fetchone() is not None
        return await self.loop.run_in_executor(None, _has)

    async def load_status(self):
        def _load():
            c = self.conn.cursor()
            c.execute("SELECT feed_url, entry_url FROM rss_status")
            status = {}
            for feed_url, entry_url in c.fetchall():
                status.setdefault(feed_url, set()).add(entry_url)
            return status
        return await self.loop.run_in_executor(None, _load)

    async def load_last_run_time(self, feed_group):
        def _load():
            c = self.conn.cursor()
            c.execute("SELECT last_run_time FROM timestamps WHERE feed_group = ?", (feed_group,))
            result = c.fetchone()
            return result[0] if result else 0
        return await self.loop.run_in_executor(None, _load)

    async def save_last_run_time(self, feed_group, last_run_time):
        def _save():
            c = self.conn.cursor()
            c.execute("""
                INSERT OR REPLACE INTO timestamps (feed_group, last_run_time)
                VALUES (?, ?)
            """, (feed_group, last_run_time))
            self.conn.commit()
        await self.loop.run_in_executor(None, _save)

    async def cleanup_history(self, days, feed_group):
        now = time.time()
        cutoff_ts = now - days * 86400
        def _cleanup():
            c = self.conn.cursor()
            c.execute(
                "SELECT last_cleanup_time FROM cleanup_timestamps WHERE feed_group = ?",
                (feed_group,)
            )
            result = c.fetchone()
            last_cleanup = result[0] if result else 0
            if now - last_cleanup < 86400:
                return
            c.execute(
                "DELETE FROM rss_status WHERE feed_group=? AND entry_timestamp < ?",
                (feed_group, cutoff_ts)
            )
            c.execute("""
                INSERT OR REPLACE INTO cleanup_timestamps (feed_group, last_cleanup_time)
                VALUES (?, ?)
            """, (feed_group, now))
            self.conn.commit()
        await self.loop.run_in_executor(None, _cleanup)

# ========== 业务逻辑 ==========

def remove_html_tags(text):
    text = re.sub(r'#([^#\s]+)#', r'\1', text)
    text = re.sub(r'#\w+', '', text)
    text = re.sub(r'@[^\s]+', '', text).strip()
    text = re.sub(r'【\s*】', '', text)
    text = re.sub(r'(?<!\S)#(?!\S)', '', text)
    text = re.sub(r'(?<!\S)：(?!\S)', '', text)
    return text

def get_entry_identifier(entry):
    if hasattr(entry, 'guid') and entry.guid:
        return hashlib.sha256(entry.guid.encode()).hexdigest()
    link = getattr(entry, 'link', '')
    if link:
        try:
            parsed = urlparse(link)
            clean_link = parsed._replace(query=None, fragment=None).geturl().lower()
            return hashlib.sha256(clean_link.encode()).hexdigest()
        except Exception as e:
            logger.warning(f"URL解析失败 {link}: {e}")
    title = getattr(entry, 'title', '')
    pub_date = get_entry_timestamp(entry).isoformat() if get_entry_timestamp(entry) else ''
    return hashlib.sha256(f"{title}|||{pub_date}".encode()).hexdigest()

def get_entry_content_hash(entry):
    title = getattr(entry, 'title', '') or ''
    summary = getattr(entry, 'summary', '') or ''
    pub_date = ''
    if hasattr(entry, 'published'):
        pub_date = entry.published
    elif hasattr(entry, 'updated'):
        pub_date = entry.updated
    raw_text = (title.strip() + summary.strip() + pub_date.strip()).encode('utf-8')
    return hashlib.sha256(raw_text).hexdigest()

def signal_handler(signum, frame):
    logger.warning(f"收到信号 {signum}，程序即将退出。")
    sys.exit(0)
def get_entry_timestamp(entry):
    dt = datetime.now(pytz.UTC)
    if hasattr(entry, 'published_parsed') and entry.published_parsed:
        dt = datetime(*entry.published_parsed[:6], tzinfo=pytz.utc)
    elif hasattr(entry, 'pubDate_parsed') and entry.pubDate_parsed:
        dt = datetime(*entry.pubDate_parsed[:6], tzinfo=pytz.utc)
    elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
        dt = datetime(*entry.updated_parsed[:6], tzinfo=pytz.utc)
    return dt

@retry(
    stop=stop_after_attempt(1),
    wait=wait_exponential(multiplier=1, min=5, max=30),
    retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError)),
)
async def send_single_message(bot, chat_id, text, disable_web_page_preview=False):
    try:
        MAX_MESSAGE_LENGTH = 4096
        text_chunks = []
        current_chunk = []
        current_length = 0
        paragraphs = text.split('\n\n')
        for para in paragraphs:
            para_length = len(para.encode('utf-8'))
            if current_length + para_length + 2 > MAX_MESSAGE_LENGTH:
                text_chunks.append('\n\n'.join(current_chunk))
                current_chunk = []
                current_length = 0
            current_chunk.append(para)
            current_length += para_length + 2
        if current_chunk:
            text_chunks.append('\n\n'.join(current_chunk))
        for chunk in text_chunks:
            await bot.send_message(
                chat_id=chat_id,
                text=chunk,
                parse_mode='MarkdownV2',
                disable_web_page_preview=disable_web_page_preview,
                read_timeout=10,
                write_timeout=10
            )
    except BadRequest as e:
        logging.error(f"消息发送失败(Markdown错误): {e} - 文本片段: {chunk[:200]}...")
    except Exception as e:
        raise

@retry(
    stop=stop_after_attempt(1),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError)),
)
async def fetch_feed(session, feed_url):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/89.0.4389.82 Safari/537.36'}
    parsed = urlparse(feed_url)
    is_rsshub = parsed.netloc == "rsshub.app"
    if is_rsshub:
        try_domains = BACKUP_DOMAINS + ["rsshub.app"]
        canonical_url = feed_url.replace(parsed.netloc, "rsshub.app")
    else:
        try_domains = [parsed.netloc]
        canonical_url = feed_url
    for domain in try_domains:
        modified_url = feed_url.replace(parsed.netloc, domain)
        try:
            async with semaphore:
                async with session.get(modified_url, headers=headers, timeout=30) as response:
                    if response.status in (503, 403, 404, 429):
                        continue
                    response.raise_for_status()
                    return parse(await response.read()), canonical_url
        except aiohttp.ClientResponseError as e:
            if e.status in (503, 403, 404, 429):
                continue
        except Exception as e:
            logger.error(f"请求失败: {modified_url}, 错误: {e}")
            continue
    logger.error(f"所有域名尝试失败: {feed_url}")
    return None, canonical_url

async def translate_with_credentials(secret_id, secret_key, text):
    loop = asyncio.get_running_loop()
    text_bytes = text.encode('utf-8')
    if len(text_bytes) > 2000:
        safe_bytes = text_bytes[:2000]
        while safe_bytes[-1] & 0xC0 == 0x80:
            safe_bytes = safe_bytes[:-1]
        text = safe_bytes.decode('utf-8', errors='ignore')
        logger.warning(f"文本截断至 {len(text)} 字符 ({len(safe_bytes)} 字节)")
    try:
        return await loop.run_in_executor(
            None, 
            lambda: _sync_translate(secret_id, secret_key, text)
        )
    except Exception as e:
        logger.error(f"翻译执行失败: {type(e).__name__} - {str(e)}")
        raise

def _sync_translate(secret_id, secret_key, text):
    try:
        cred = credential.Credential(secret_id, secret_key)
        clientProfile = ClientProfile(httpProfile=HttpProfile(endpoint="tmt.tencentcloudapi.com"))
        client = tmt_client.TmtClient(cred, TENCENT_REGION, clientProfile)
        req = models.TextTranslateRequest()
        req.SourceText = remove_html_tags(text)
        req.Source = "auto"
        req.Target = "zh"
        req.ProjectId = 0
        return client.TextTranslate(req).TargetText
    except TencentCloudSDKException as e:
        error_details = {
            "code": e.code,
            "message": e.message,
            "request_id": e.request_id,
            "region": TENCENT_REGION
        }
        logger.error(f"腾讯云API错误详情: {error_details}")
        raise
    except Exception as e:
        logger.error(f"翻译过程中发生未知错误: {str(e)}")
        raise

@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def auto_translate_text(text):
    try:
        try:
            return await translate_with_credentials(
                TENCENTCLOUD_SECRET_ID, 
                TENCENTCLOUD_SECRET_KEY,
                text
            )
        except TencentCloudSDKException as e:
            logger.error(f"主密钥翻译失败: [Code: {e.code}] {e.message}")
            raise
        except Exception as e:
            logger.error(f"主密钥翻译未知错误: {type(e).__name__} - {str(e)}")
            raise
    except Exception as first_error:
        if TENCENT_SECRET_ID and TENCENT_SECRET_KEY:
            logger.warning("主翻译密钥失败，尝试备用密钥...")
            try:
                return await translate_with_credentials(
                    TENCENT_SECRET_ID,
                    TENCENT_SECRET_KEY,
                    text
                )
            except TencentCloudSDKException as e:
                logger.error(f"备用密钥翻译失败: [Code: {e.code}] {e.message}")
                raise
            except Exception as e:
                logger.error(f"备用密钥翻译未知错误: {type(e).__name__} - {str(e)}")
                raise
        else:
            logger.error("主翻译密钥失败，且未配置备用密钥")
            raise first_error
    except Exception as final_error:
        logger.error(f"所有翻译尝试均失败: {type(final_error).__name__}")
        cleaned = remove_html_tags(text)
        return escape(cleaned)

async def generate_group_message(feed_data, entries, processor):
    try:
        source_name = feed_data.feed.get('title', "未知来源")
        safe_source = escape(source_name)
        header = ""
        if "header_template" in processor:
            header = processor["header_template"].format(source=safe_source) + "\n"
        messages = []
        for entry in entries:
            raw_subject = remove_html_tags(entry.title or "无标题")
            if processor["translate"]:
                translated_subject = await auto_translate_text(raw_subject)
            else:
                translated_subject = raw_subject
            safe_subject = escape(translated_subject)
            raw_url = entry.link
            safe_url = escape(raw_url)
            message = processor["template"].format(
                subject=safe_subject,
                source=safe_source,
                url=safe_url
            )
            messages.append(message)
        full_message = header + "\n\n".join(messages)
        if processor.get("show_count", True):
            full_message += f"\n\n✅ 新增 {len(messages)} 条内容"
        return full_message
    except Exception as e:
        logger.error(f"生成消息失败: {str(e)}")
        return ""

async def process_group(session, group_config, global_status, db: RSSDatabase):
    group_name = group_config["name"]
    group_key = group_config["group_key"]
    processor = group_config["processor"]
    bot_token = group_config["bot_token"]
    try:
        last_run = await db.load_last_run_time(group_key)
        now = datetime.now(pytz.utc).timestamp()
        if (now - last_run) < group_config["interval"]:
            return
        bot = Bot(token=bot_token)
        for index, feed_url in enumerate(group_config["urls"]):
            try:
                if index > 0:
                    await asyncio.sleep(1)
                feed_data, canonical_url = await fetch_feed(session, feed_url)
                if not feed_data or not feed_data.entries:
                    continue
                processed_ids = global_status.get(canonical_url, set())
                new_entries = []
                seen_in_batch = set()
                for entry in feed_data.entries:
                    entry_id = get_entry_identifier(entry)
                    content_hash = get_entry_content_hash(entry)
                    # 如果内容hash已存在则跳过（即使entry_id不同）
                    if await db.has_content_hash(group_key, content_hash):
                        continue
                    if entry_id in processed_ids or entry_id in seen_in_batch:
                        continue
                    seen_in_batch.add(entry_id)
                    filter_config = processor.get("filter", {})
                    if filter_config.get("enable", False):
                        raw_title = remove_html_tags(entry.title or "")
                        keywords = filter_config.get("keywords", [])
                        match = any(kw.lower() in raw_title.lower() for kw in keywords)
                        if filter_config.get("mode", "allow") == "allow":
                            if not match:
                                continue
                        else:
                            if match:
                                continue
                    # 保存entry、hash、id
                    new_entries.append((entry, content_hash, entry_id))
                if new_entries:
                    feed_message = await generate_group_message(feed_data, [e for e,_,_ in new_entries], processor)
                    if feed_message:
                        try:
                            await send_single_message(
                                bot,
                                TELEGRAM_CHAT_ID[0],
                                feed_message,
                                disable_web_page_preview=not processor.get("preview", True)
                            )
                            for entry, content_hash, entry_id in new_entries:
                                await db.save_status(group_key, canonical_url, entry_id, content_hash, time.time())
                                processed_ids.add(entry_id)
                            global_status[canonical_url] = processed_ids
                        except Exception as send_error:
                            logger.error(f"❌ 发送消息失败 [{feed_url}]")
                            raise
            except Exception as e:
                logger.error(f"❌ 处理失败 [{feed_url}]")
        await db.save_last_run_time(group_key, now)
    except Exception as e:
        logger.critical(f"‼️ 处理组失败 [{group_key}]")

async def main():
    lock_file = None
    db = RSSDatabase()
    try:
        lock_file = open(LOCK_FILE, "w")
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        logger.warning("⛔ 无法获取文件锁，已有实例在运行，程序退出")
        return
    except Exception as e:
        logger.critical(f"‼️ 文件锁异常: {str(e)}")
        return
    try:
        await db.open()
        await db.create_tables()
    except Exception as e:
        logger.critical(f"‼️ 数据库初始化失败: {str(e)}")
        return
    for group in RSS_GROUPS:
        days = group.get("history_days", 30)
        try:
            await db.cleanup_history(days, group["group_key"])
        except Exception as e:
            logger.error(f"清理历史记录异常: 组={group['group_key']}, 错误={e}")
    async with aiohttp.ClientSession() as session:
        try:
            status = await db.load_status()
            tasks = []
            for group in RSS_GROUPS:
                try:
                    tasks.append(process_group(session, group, status, db))
                except Exception as e:
                    logger.error(f"⚠️ 创建任务失败 [{group['name']}]: {str(e)}")
            if tasks:
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for res in results:
                    if isinstance(res, Exception):
                        logger.error(f"任务执行异常: {res}")
            else:
                logger.warning("⛔ 未创建任何处理任务")
        except asyncio.CancelledError:
            logger.warning("⏹️ 任务被取消")
        except Exception as e:
            logger.critical(f"‼️ 主循环异常: {str(e)}", exc_info=True)
        finally:
            try:
                await session.close()
            except Exception as e:
                logger.error(f"⚠️ 关闭会话失败: {str(e)}")
    try:
        if lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()
    except Exception as e:
        logger.error(f"⚠️ 释放文件锁失败: {str(e)}")
    await db.close()

if __name__ == "__main__":
    for s in (signal.SIGINT, signal.SIGTERM):
        signal.signal(s, signal_handler)
    try:
        asyncio.run(main())
    except Exception as e:
        logger.critical(f"‼️ 主进程未捕获异常: {str(e)}", exc_info=True)
        sys.exit(1)