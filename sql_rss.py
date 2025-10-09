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
import aiosqlite
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
from collections import defaultdict
from langdetect import detect, LangDetectException
import time
# ========== 全局退出标志 ==========
SHOULD_EXIT = False
# ========== 环境加载 ==========
load_dotenv()
# 设置时区（在cron环境中很重要）
os.environ['TZ'] = 'Asia/Singapore'
try:
    time.tzset()  # Linux系统
except AttributeError:
    pass  # Windows系统忽略
BASE_DIR = Path(__file__).resolve().parent
LOCK_FILE = BASE_DIR / "rss.lock"
DATABASE_FILE = BASE_DIR / "rss.db"

logging.basicConfig(
    filename=BASE_DIR / "rss.log",
    level=logging.WARNING,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    encoding="utf-8"
)
logger = logging.getLogger(__name__)

TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID").split(",")
TENCENTCLOUD_SECRET_ID = os.getenv("TENCENTCLOUD_SECRET_ID")
TENCENTCLOUD_SECRET_KEY = os.getenv("TENCENTCLOUD_SECRET_KEY")
TENCENT_REGION = os.getenv("TENCENT_REGION", "na-siliconvalley")
TENCENT_SECRET_ID = os.getenv("TENCENT_SECRET_ID")
TENCENT_SECRET_KEY = os.getenv("TENCENT_SECRET_KEY")
semaphore = asyncio.Semaphore(2)
BACKUP_DOMAINS_STR = os.getenv("BACKUP_DOMAINS", "")
BACKUP_DOMAINS = [domain.strip() for domain in BACKUP_DOMAINS_STR.split(",") if domain.strip()]

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
        "batch_send_interval": 14390,   # 4小时批量推送
        "history_days": 180,     # 新增，保留30天
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
        "interval": 3590,      # 1小时
        "batch_send_interval": 35990,   # 批量推送←加上即
        "history_days": 300,     # 新增，保留30天
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
        "interval": 700,       # 10分钟 
        "batch_send_interval": 3590,   # 批量推送
        "history_days": 3,     # 新增，保留3天
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
        #    'https://rsshub.app/eastmoney/report/strategyreport', # 东方财富策略
         #   'https://rsshub.app/jin10',  # 金十
       #     'https://rsshub.app/huijin-inv/news',
       #     'https://rsshub.app/eeo/kuaixun',
         #   'https://36kr.com/feed-newsflash',  # 36氪快讯
        #    'https://36kr.com/feed',  # 36氪综合
            
        ],
        "group_key": "TOURTH_RSS_FEEDS",
        "interval": 700,       # 10分钟
        "batch_send_interval": 3590,   # 批量推送
        "history_days": 3,     # 新增，保留3天
        "bot_token": os.getenv("TONGHUASHUN_RSS"),  #   Telegram Bot Token
        "processor": {
            "translate": False,     #翻译开关
            "header_template": "📢 *{source}*\n",  # 新增标题模板 ★
            "template": "*{subject}*\n[more]({url})",
            "preview": False,            # 禁止预览
            "show_count": False          #计数
        }
    },

    # ================== 新浪博客 ==================
    {
        "name": "社交媒体",
        "urls": [
            'https://rsshub.app/weibo/user/3194547262',  # 江西高速
        #    'https://rsshub.app/weibo/user/1699432410',  # 新华社
        #    'https://rsshub.app/weibo/user/2656274875',  # 央视新闻
            'https://rsshub.app/weibo/user/2716786595',  # 聚萍乡
            'https://rsshub.app/weibo/user/1891035762',  # 交警
       #     'https://rsshub.app/weibo/user/3917937138',  # 发布
        #    'https://rsshub.app/weibo/user/3213094623',  # 邮政
        #    'https://rsshub.app/weibo/user/2818241427',  # 冒险岛

        ],
        "group_key": "FIFTH_RSSSA_FEEDS",
        "interval": 7190,    # 2小时
        "batch_send_interval": 35990,   # 批量推送    
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
        "batch_send_interval": 3590,   # 批量推送
        "history_days": 3,     # 新增，保留30天
        "bot_token": os.getenv("RSS_SAN"), # Telegram Bot Token
        "processor": {
            "translate": False,                  #翻译关
            "header_template": "📢 *{source}*\n",  # 新增标题模板 ★
            "template": "*{subject}*\n[more]({url})", 
            "filter": {
                "enable": True,  # 过滤开关     False: 关闭 / True: 开启
                "mode": "allow",  # allow模式：包含关键词才发送 / block模式：包含关键词不发送
                "keywords": ["免", "cf", "cl", "黑", "低", "小", "卡", "年", "bug", "白", "github",  "节",  "闪",  "cc", "rn", "动", "cloudcone", "脚本", "代码", "docker", "剩", "折"]  # 本组关键词列表
            },
            "preview": False,              # 禁止预览
            "show_count": False               #计数
        }
    },
    # ================== vps ==================
    {
        "name": "vps",
        "urls": [
        #    'https://lowendspirit.com/discussions/feed.rss', # lowendspirit
            'https://lowendtalk.com/discussions/feed.rss',   # lowendtalk
        ],
        "group_key": "FIFTH_RSS_RRSS_SAN",
        "interval": 3590,      # 60分钟 
        "batch_send_interval": 17990,   # 批量推送
        "history_days": 60,     # 保留60天
        "bot_token": os.getenv("RSS_SAN"),    # Telegram Bot Token
        "processor": {
            "translate": True,       #翻译开
            "header_template": "📢 *{source}*\n",  # 新增标题模板 ★
            "template": "*{subject}*\n[more]({url})",
            "preview": False,         # 禁止预览
            "show_count": False        # ✅新增
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
       # "batch_send_interval": 10800,   # 批量推送
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
        "interval": 7180,     # 2小时
        "batch_send_interval": 35990,   # 批量推送
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
        "interval": 7000,    # 2小时
        "batch_send_interval": 36000,   # 批量推送
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
        "interval": 3590,      # 1小时
        "batch_send_interval": 14350,   # 批量推送
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

# ========== 数据库配置 ==========
PG_URL = os.getenv("PG_URL")
USE_PG = PG_URL is not None

# 日志记录数据库类型
if USE_PG:
    # 安全地记录数据库信息（隐藏密码）
    safe_pg_url = re.sub(r':([^@]+)@', ':****@', PG_URL) if PG_URL else "未配置"
    logger.info(f"🔧 使用 PostgreSQL 数据库: {safe_pg_url}")
    print(f"✅ PostgreSQL ")
else:
    logger.info(f"🔧 使用 SQLite 数据库: {DATABASE_FILE}")
    print(f"✅ SQLite : {DATABASE_FILE}")

if USE_PG:
    import asyncpg

class RSSDatabase:
    def __init__(self, loop=None):
        self.loop = loop or asyncio.get_event_loop()
        self.conn = None
        self.pg_pool = None

    async def open(self):
        if USE_PG:
            self.pg_pool = await asyncpg.create_pool(PG_URL)
        else:
            self.conn = await aiosqlite.connect(DATABASE_FILE)

    async def close(self):
        if USE_PG and self.pg_pool:
            await self.pg_pool.close()
        elif self.conn:
            await self.conn.close()

    async def ensure_initialized(self):
        """确保数据库表已创建"""
        await self.create_tables()

    async def create_tables(self):
        if USE_PG:
            async with self.pg_pool.acquire() as conn:
                await conn.execute("""
                CREATE TABLE IF NOT EXISTS rss_status (
                    feed_group TEXT,
                    feed_url TEXT,
                    entry_url TEXT,
                    entry_content_hash TEXT,
                    entry_timestamp DOUBLE PRECISION,
                    PRIMARY KEY (feed_group, feed_url, entry_url)
                );
                """)
                await conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_group_content_hash
                ON rss_status(feed_group, entry_content_hash);
                """)
                await conn.execute("""
                CREATE TABLE IF NOT EXISTS timestamps (
                    feed_group TEXT PRIMARY KEY,
                    last_run_time DOUBLE PRECISION
                );
                """)
                await conn.execute("""
                CREATE TABLE IF NOT EXISTS cleanup_timestamps (
                    feed_group TEXT PRIMARY KEY,
                    last_cleanup_time DOUBLE PRECISION
                );
                """)
                await conn.execute("""
                CREATE TABLE IF NOT EXISTS pending_messages (
                    feed_group TEXT,
                    feed_url TEXT,
                    entry_id TEXT,
                    content_hash TEXT,
                    title TEXT,
                    translated_title TEXT,
                    link TEXT,
                    summary TEXT,
                    entry_timestamp DOUBLE PRECISION,
                    sent INTEGER DEFAULT 0,
                    feed_title TEXT,
                    PRIMARY KEY (feed_group, feed_url, entry_id)
                );
                """)
                await conn.execute("""
                CREATE TABLE IF NOT EXISTS batch_timestamps (
                    feed_group TEXT PRIMARY KEY,
                    last_batch_sent_time DOUBLE PRECISION
                );
                """)
        else:
            async with self.conn.cursor() as c:
                await c.execute("""
                CREATE TABLE IF NOT EXISTS rss_status (
                    feed_group TEXT,
                    feed_url TEXT,
                    entry_url TEXT,
                    entry_content_hash TEXT,
                    entry_timestamp REAL,
                    PRIMARY KEY (feed_group, feed_url, entry_url)
                )""")
                await c.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_group_content_hash
                ON rss_status(feed_group, entry_content_hash);
                """)
                await c.execute("""
                CREATE TABLE IF NOT EXISTS timestamps (
                    feed_group TEXT PRIMARY KEY,
                    last_run_time REAL
                )""")
                await c.execute("""
                CREATE TABLE IF NOT EXISTS cleanup_timestamps (
                    feed_group TEXT PRIMARY KEY,
                    last_cleanup_time REAL
                )""")
                await c.execute("""
                CREATE TABLE IF NOT EXISTS pending_messages (
                    feed_group TEXT,
                    feed_url TEXT,
                    entry_id TEXT,
                    content_hash TEXT,
                    title TEXT,
                    translated_title TEXT,
                    link TEXT,
                    summary TEXT,
                    entry_timestamp REAL,
                    sent INTEGER DEFAULT 0,
                    feed_title TEXT,
                    PRIMARY KEY (feed_group, feed_url, entry_id)
                )
                """)
                await c.execute("""
                CREATE TABLE IF NOT EXISTS batch_timestamps (
                    feed_group TEXT PRIMARY KEY,
                    last_batch_sent_time REAL
                )
                """)
                await self.conn.commit()

    async def add_pending_message(self, feed_group, feed_url, entry_id, content_hash, title, translated_title, link, summary, timestamp, feed_title):
        if USE_PG:
            async with self.pg_pool.acquire() as conn:
                await conn.execute("""
                INSERT INTO pending_messages (feed_group, feed_url, entry_id, content_hash, title, translated_title, link, summary, entry_timestamp, sent, feed_title)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 0, $10)
                ON CONFLICT DO NOTHING
                """, feed_group, feed_url, entry_id, content_hash, title, translated_title, link, summary, timestamp, feed_title)
        else:
            async with self.conn.cursor() as c:
                await c.execute("""
                    INSERT OR IGNORE INTO pending_messages
                    (feed_group, feed_url, entry_id, content_hash, title, translated_title, link, summary, entry_timestamp, sent, feed_title)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                """, (feed_group, feed_url, entry_id, content_hash, title, translated_title, link, summary, timestamp, feed_title))
                await self.conn.commit()

    async def get_pending_messages(self, feed_group):
        if USE_PG:
            async with self.pg_pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT * FROM pending_messages
                    WHERE feed_group=$1 AND sent=0
                    ORDER BY entry_timestamp ASC
                """, feed_group)
                return [dict(row) for row in rows]
        else:
            async with self.conn.cursor() as c:
                await c.execute("""
                    SELECT * FROM pending_messages
                    WHERE feed_group=? AND sent=0
                    ORDER BY entry_timestamp ASC
                """, (feed_group,))
                keys = [d[0] for d in c.description]
                rows = await c.fetchall()
                return [dict(zip(keys, row)) for row in rows]

    async def mark_pending_as_sent(self, feed_group, ids):
        if not ids:
            return
        if USE_PG:
            async with self.pg_pool.acquire() as conn:
                await conn.executemany("""
                    UPDATE pending_messages SET sent=1
                    WHERE feed_group=$1 AND entry_id=$2
                """, [(feed_group, eid) for eid in ids])
        else:
            async with self.conn.cursor() as c:
                await c.executemany("""
                    UPDATE pending_messages SET sent=1
                    WHERE feed_group=? AND entry_id=?
                """, [(feed_group, eid) for eid in ids])
                await self.conn.commit()

    async def get_last_batch_sent_time(self, feed_group):
        if USE_PG:
            async with self.pg_pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT last_batch_sent_time FROM batch_timestamps WHERE feed_group=$1
                """, feed_group)
                return row['last_batch_sent_time'] if row else 0
        else:
            async with self.conn.cursor() as c:
                await c.execute("""
                    SELECT last_batch_sent_time FROM batch_timestamps WHERE feed_group=?
                """, (feed_group,))
                result = await c.fetchone()
                return result[0] if result else 0

    async def save_last_batch_sent_time(self, feed_group, ts):
        if USE_PG:
            async with self.pg_pool.acquire() as conn:
                await conn.execute("""
                INSERT INTO batch_timestamps (feed_group, last_batch_sent_time)
                VALUES ($1, $2)
                ON CONFLICT (feed_group) DO UPDATE SET last_batch_sent_time=EXCLUDED.last_batch_sent_time
                """, feed_group, ts)
        else:
            async with self.conn.cursor() as c:
                await c.execute("""
                    INSERT OR REPLACE INTO batch_timestamps (feed_group, last_batch_sent_time)
                    VALUES (?, ?)
                """, (feed_group, ts))
                await self.conn.commit()

    async def save_status(self, feed_group, feed_url, entry_url, entry_content_hash, timestamp):
        if USE_PG:
            async with self.pg_pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO rss_status (feed_group, feed_url, entry_url, entry_content_hash, entry_timestamp) "
                    "VALUES($1, $2, $3, $4, $5) ON CONFLICT DO NOTHING",
                    feed_group, feed_url, entry_url, entry_content_hash, timestamp
                )
        else:
            async with self.conn.cursor() as c:
                await c.execute(
                    "INSERT OR IGNORE INTO rss_status VALUES (?, ?, ?, ?, ?)",
                    (feed_group, feed_url, entry_url, entry_content_hash, timestamp)
                )
                await self.conn.commit()

    async def has_content_hash(self, feed_group, content_hash):
        if USE_PG:
            async with self.pg_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT 1 FROM rss_status WHERE feed_group=$1 AND entry_content_hash=$2 LIMIT 1",
                    feed_group, content_hash
                )
                return row is not None
        else:
            async with self.conn.cursor() as c:
                await c.execute(
                    "SELECT 1 FROM rss_status WHERE feed_group=? AND entry_content_hash=? LIMIT 1",
                    (feed_group, content_hash)
                )
                return await c.fetchone() is not None

    async def load_status(self):
        if USE_PG:
            async with self.pg_pool.acquire() as conn:
                rows = await conn.fetch("SELECT feed_url, entry_url FROM rss_status")
                status = {}
                for row in rows:
                    feed_url, entry_url = row['feed_url'], row['entry_url']
                    status.setdefault(feed_url, set()).add(entry_url)
                return status
        else:
            async with self.conn.cursor() as c:
                await c.execute("SELECT feed_url, entry_url FROM rss_status")
                rows = await c.fetchall()
                status = {}
                for feed_url, entry_url in rows:
                    status.setdefault(feed_url, set()).add(entry_url)
                return status

    async def load_last_run_time(self, feed_group):
        if USE_PG:
            async with self.pg_pool.acquire() as conn:
                row = await conn.fetchrow("SELECT last_run_time FROM timestamps WHERE feed_group=$1", feed_group)
                return row['last_run_time'] if row else 0
        else:
            async with self.conn.cursor() as c:
                await c.execute("SELECT last_run_time FROM timestamps WHERE feed_group = ?", (feed_group,))
                result = await c.fetchone()
                return result[0] if result else 0

    async def save_last_run_time(self, feed_group, last_run_time):
        if USE_PG:
            async with self.pg_pool.acquire() as conn:
                await conn.execute("""
                INSERT INTO timestamps (feed_group, last_run_time)
                VALUES ($1, $2)
                ON CONFLICT (feed_group) DO UPDATE SET last_run_time=EXCLUDED.last_run_time
                """, feed_group, last_run_time)
        else:
            async with self.conn.cursor() as c:
                await c.execute("""
                    INSERT OR REPLACE INTO timestamps (feed_group, last_run_time)
                    VALUES (?, ?)
                """, (feed_group, last_run_time))
                await self.conn.commit()

    async def cleanup_history(self, days, feed_group):
        now = time.time()
        cutoff_ts = now - days * 86400

        if USE_PG:
            async with self.pg_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT last_cleanup_time FROM cleanup_timestamps WHERE feed_group=$1", feed_group
                )
                last_cleanup = row['last_cleanup_time'] if row else 0
                if now - last_cleanup < 86400:
                    return
                await conn.execute(
                    "DELETE FROM rss_status WHERE feed_group=$1 AND entry_timestamp<$2",
                    feed_group, cutoff_ts
                )
                await conn.execute("""
                    INSERT INTO cleanup_timestamps (feed_group, last_cleanup_time)
                    VALUES ($1, $2)
                    ON CONFLICT (feed_group) DO UPDATE SET last_cleanup_time=EXCLUDED.last_cleanup_time
                """, feed_group, now)
        else:
            async with self.conn.cursor() as c:
                await c.execute(
                    "SELECT last_cleanup_time FROM cleanup_timestamps WHERE feed_group = ?",
                    (feed_group,)
                )
                result = await c.fetchone()
                last_cleanup = result[0] if result else 0
                if now - last_cleanup < 86400:
                    return
                await c.execute(
                    "DELETE FROM rss_status WHERE feed_group=? AND entry_timestamp < ?",
                    (feed_group, cutoff_ts)
                )
                await c.execute("""
                    INSERT OR REPLACE INTO cleanup_timestamps (feed_group, last_cleanup_time)
                    VALUES (?, ?)
                """, (feed_group, now))
                await self.conn.commit()

# ========== 业务逻辑 ==========

def remove_html_tags(text):
    text = re.sub(r'<[^>]+>', '', text)
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
    """改进的信号处理"""
    global SHOULD_EXIT
    logger.warning(f"收到信号 {signum}，正在优雅退出...")
    SHOULD_EXIT = True

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
            para_length = len(para)  # 字符长度
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
        logger.error(f"消息发送失败(Markdown错误): {e} - 文本片段: {chunk[:200]}...")  # 修复这里
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
        #    logger.error(f"请求失败: {modified_url}, 错误: {e}")
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

def is_need_translate(text):
    try:
        lang = detect(text)
        # 只对英文、日文、韩文、阿拉伯文等非中文做翻译
        return lang not in ("zh-cn", "zh-tw", "zh", "yue")
    except LangDetectException:
        return False
    
def is_mostly_symbols(text):
    """检查文本是否主要由符号、数字组成"""
    if not text:
        return True
    
    # 计算字母比例
    alpha_count = sum(1 for char in text if char.isalpha())
    total_chars = len(text)
    
    # 如果字母比例低于30%，认为是符号/数字文本
    return alpha_count / total_chars < 0.3 if total_chars > 0 else True

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
            "code": getattr(e, "code", ""),
            "message": getattr(e, "message", str(e)),
            "request_id": getattr(e, "request_id", ""),
            "region": TENCENT_REGION
        }
        logger.error(f"腾讯云API错误详情: {error_details}")
        raise
    except Exception as e:
        logger.error(f"翻译过程中发生未知错误: {str(e)}")
        raise

async def should_send_entry(entry, processor):
    filter_config = processor.get("filter", {})
    title = getattr(entry, "title", "") or ""
    summary = getattr(entry, "summary", "") or ""
    content = title.lower()   # 仅标题过滤
   # content = f"{title} {summary}".lower()  # 标题+摘要过滤 
    keywords = [kw.lower() for kw in filter_config.get("keywords", [])]
    mode = filter_config.get("mode", "allow")
    has_keyword = any(keyword in content for keyword in keywords)
    send = False
    if not filter_config.get("enable", False):
        send = True
    elif not keywords:
        send = mode != "allow"
    elif mode == "allow":
        send = has_keyword
    elif mode == "block":
        send = not has_keyword
    else:
        send = True
 #   logger.warning(f"[关键词过滤] 标题: {title} | 摘要: {summary} | 内容: {content} | 关键词: {keywords} | 模式: {mode} | 是否命中: {has_keyword} | 发送: {send}")
    return send

@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def auto_translate_text(text):
    cleaned_text = remove_html_tags(text).strip()
    
    # 如果文本过短或主要是符号/数字，直接返回原文
    if len(cleaned_text) <= 3 or is_mostly_symbols(cleaned_text):
        logger.debug(f"跳过翻译 - 文本过短或主要为符号: {cleaned_text}")
        return escape(cleaned_text)
    
    try:
        # 首先尝试主密钥
        try:
            return await translate_with_credentials(
                TENCENTCLOUD_SECRET_ID, 
                TENCENTCLOUD_SECRET_KEY,
                cleaned_text
            )
        except TencentCloudSDKException as e:
            if getattr(e, "code", "") == "FailedOperation.LanguageRecognitionErr":
                logger.warning(f"腾讯云语言识别失败，返回原文: {cleaned_text[:100]}")
                return escape(cleaned_text)
            else:
                logger.error(f"主密钥翻译失败: [Code: {e.code}] {e.message}")
                raise
                
    except Exception as first_error:
        # 只有在非语言识别错误的情况下才尝试备用密钥
        if TENCENT_SECRET_ID and TENCENT_SECRET_KEY:
            logger.warning("主翻译密钥失败（非语言识别错误），尝试备用密钥...")
            try:
                return await translate_with_credentials(
                    TENCENT_SECRET_ID,
                    TENCENT_SECRET_KEY,
                    cleaned_text
                )
            except TencentCloudSDKException as e:
                if getattr(e, "code", "") == "FailedOperation.LanguageRecognitionErr":
                    logger.warning(f"备用密钥语言识别失败，返回原文: {cleaned_text[:100]}")
                    return escape(cleaned_text)
                else:
                    logger.error(f"备用密钥翻译失败: [Code: {e.code}] {e.message}")
                    raise
            except Exception as e:
                logger.error(f"备用密钥翻译未知错误: {type(e).__name__} - {str(e)}")
                raise
        else:
            logger.error("主翻译密钥失败，且未配置备用密钥")
            return escape(cleaned_text)

# 改进的消息生成函数
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
            if processor.get("translate", False):
                translated_subject = await auto_translate_text(raw_subject)
            else:
                translated_subject = raw_subject
            safe_subject = escape(translated_subject)
            raw_url = entry.link
            safe_url = escape(raw_url)
            
            # 确保每条消息都有完整的加粗格式
            message = processor["template"].format(
                subject=safe_subject,
                source=safe_source,
                url=safe_url
            )
            messages.append(message)
        
        # 改进的分段逻辑，确保格式完整性
        full_message = await _format_batch_message(header, messages, processor)
        return full_message
    except Exception as e:
        logger.error(f"生成消息失败: {str(e)}")
        return ""


async def _format_batch_message(header, messages, processor):
    """改进的批量消息格式化，确保Markdown格式完整"""
    MAX_MESSAGE_LENGTH = 4096
    
    if not messages:
        return ""
    
    # 尝试构建完整消息
    full_content = header + "\n\n".join(messages)
    if processor.get("show_count", False):
        full_content += f"\n\n✅ 新增 {len(messages)} 条内容"
    
    # 如果消息长度在限制内，直接返回
    if len(full_content) <= MAX_MESSAGE_LENGTH:
        return full_content
    
    # 消息过长，需要分段
    segments = []
    current_segment = header
    current_length = len(header)
    
    for i, message in enumerate(messages):
        # 新段的第一条消息不加分隔符，后续消息加分隔符
        if current_segment == header:
            message_with_separator = message
        else:
            message_with_separator = "\n\n" + message
        
        # 检查添加这条消息是否会超过限制（预留100字符给计数信息）
        if current_length + len(message_with_separator) > MAX_MESSAGE_LENGTH - 300:
            # 完成当前段
            if processor.get("show_count", False) and current_segment != header:
                segment_msg_count = current_segment.count("\n\n") + 1
                current_segment += f"\n\n✅ 本段包含 {segment_msg_count} 条内容"
            segments.append(current_segment)
            
            # 开始新段，重新添加header
            current_segment = header
            current_length = len(header)
            message_with_separator = message  # 新段的第一条消息不加分隔符
        
        current_segment += message_with_separator
        current_length += len(message_with_separator)
    
    # 添加最后一段
    if current_segment.strip() and current_segment != header:
        if processor.get("show_count", False):
            segment_msg_count = current_segment.count("\n\n") + 1
            current_segment += f"\n\n✅ 本段包含 {segment_msg_count} 条内容"
        segments.append(current_segment)
    
    return segments

async def send_batch_messages(bot, chat_id, message_content, disable_web_page_preview=False):
    """发送批量消息，处理分段"""
    if isinstance(message_content, list):  # 分段消息
        for i, segment in enumerate(message_content):
            if segment.strip():  # 确保段不为空
                try:
                    await send_single_message(
                        bot, chat_id, segment, 
                        disable_web_page_preview=disable_web_page_preview
                    )
                    if i < len(message_content) - 1:  # 不是最后一条
                        await asyncio.sleep(1)  # 避免发送过快
                except Exception as e:
                    logger.error(f"发送分段消息失败: {e}")
    else:  # 单条消息
        await send_single_message(
            bot, chat_id, message_content,
            disable_web_page_preview=disable_web_page_preview
        )

# 修改批量发送函数中的调用
async def process_batch_send(group, db: RSSDatabase):
    group_key = group["group_key"]
    bot_token = group["bot_token"]
    processor = group["processor"]
    batch_interval = group.get("batch_send_interval")
    
    if not batch_interval:
        return
        
    now = datetime.now(pytz.utc).timestamp()
    last_batch_sent = await db.get_last_batch_sent_time(group_key)
    if now - last_batch_sent < batch_interval:
        return
        
    pending = await db.get_pending_messages(group_key)
    if not pending:
        await db.save_last_batch_sent_time(group_key, now)
        return

    # 按 feed_url 分组消息
    feed_url_to_msgs = defaultdict(list)
    for row in pending:
        feed_url_to_msgs[row["feed_url"]].append(row)

    bot = Bot(token=bot_token)
    sent_entry_ids = []
    
    for feed_url, msgs in feed_url_to_msgs.items():
        feed_title = (msgs[0].get("feed_title") or group.get("name") or feed_url)
        
        # 创建模拟的feed和entry对象
        class DummyFeed:
            feed = {'title': feed_title}
            
        class Entry:
            def __init__(self, row):
                self.title = row["translated_title"] or row["title"]
                self.link = row["link"]
                
        entries = [Entry(row) for row in msgs]
        
        try:
            # 生成消息内容
            feed_message = await generate_group_message(
                DummyFeed, entries, {**processor, "translate": False}
            )
            
            if feed_message:
                # 发送消息（支持分段）
                await send_batch_messages(
                    bot,
                    TELEGRAM_CHAT_ID[0],
                    feed_message,
                    disable_web_page_preview=not processor.get("preview", True)
                )
                # 记录已发送的消息ID
                sent_entry_ids.extend([row["entry_id"] for row in msgs])
                
        except Exception as e:
            logger.error(f"批量推送失败[{group_key}-{feed_url}]: {e}")
    
    # 标记已发送的消息
    if sent_entry_ids:
        await db.mark_pending_as_sent(group_key, sent_entry_ids)
    
    await db.save_last_batch_sent_time(group_key, now)

# ========== 组采集（采集但可选择是否立即推送） ==========
async def process_group(session, group_config, global_status, db: RSSDatabase):
    """在组处理中添加退出检查"""
    global SHOULD_EXIT
    
    if SHOULD_EXIT:
        logger.info("收到退出信号，停止处理组任务")
        return
    group_name = group_config["name"]
    group_key = group_config["group_key"]
    processor = group_config["processor"]
    bot_token = group_config["bot_token"]
    batch_send_interval = group_config.get("batch_send_interval", None)
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
                new_hashes_in_batch = set()  # 新增
                for entry in feed_data.entries:
                    entry_id = get_entry_identifier(entry)
                    content_hash = get_entry_content_hash(entry)
                    if await db.has_content_hash(group_key, content_hash):
                        continue
                    if entry_id in processed_ids or entry_id in seen_in_batch:
                        continue
                    if content_hash in new_hashes_in_batch:
                        continue  
                        # ✅ 新增：过滤检查
                    if not await should_send_entry(entry, processor):
                        continue  # 跳过不符合过滤条件的条目
                    seen_in_batch.add(entry_id)
                    new_hashes_in_batch.add(content_hash)
                    new_entries.append((entry, content_hash, entry_id))
                    
                if new_entries:
                    if batch_send_interval:
                        for entry, content_hash, entry_id in new_entries:
                            raw_subject = remove_html_tags(getattr(entry, "title", "") or "")
                            if processor["translate"] and is_need_translate(raw_subject):
                                translated_subject = await auto_translate_text(raw_subject)
                            else:
                                translated_subject = raw_subject
                            await db.add_pending_message(
                                group_key, canonical_url, entry_id, content_hash,
                                getattr(entry, "title", ""), translated_subject, getattr(entry, "link", ""), getattr(entry, "summary", ""),
                                get_entry_timestamp(entry).timestamp() if get_entry_timestamp(entry) else time.time(),
                                feed_data.feed.get('title', "") 
                            )
                            await db.save_status(group_key, canonical_url, entry_id, content_hash, time.time())
                            processed_ids.add(entry_id)
                        global_status[canonical_url] = processed_ids
                    else:
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
    logger.info("🚀 RSS Bot 开始执行")
    start_time = time.time()
    
    max_retries = 3
    retry_delay = 60  # 秒
    
    for attempt in range(max_retries):
        try:
            await run_main_logic()
            logger.info(f"✅ RSS Bot 执行完成，耗时: {time.time() - start_time:.2f}秒")
            break  # 成功执行则退出循环
        except Exception as e:
            logger.error(f"主程序运行失败 (尝试 {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                logger.info(f"{retry_delay}秒后重试...")
                await asyncio.sleep(retry_delay)
            else:
                logger.critical("达到最大重试次数，程序退出")
                return

async def run_main_logic():
    lock_file = None
    db = RSSDatabase()
    
    try:
        # 获取文件锁
        lock_file = open(LOCK_FILE, "w")
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        logger.warning("⛔ 无法获取文件锁，已有实例在运行，程序退出")
        return
    except Exception as e:
        logger.error(f"文件锁异常: {str(e)}")
        return
        
    try:
        # 数据库连接增加重试机制
        await connect_database_with_retry(db)
        
        # 清理历史记录
        for group in RSS_GROUPS:
            days = group.get("history_days", 30)
            try:
                await db.cleanup_history(days, group["group_key"])
            except Exception as e:
                logger.error(f"清理历史记录异常: 组={group['group_key']}, 错误={e}")
                
        # 主处理逻辑
        async with aiohttp.ClientSession() as session:
            status = await db.load_status()
            tasks = []
            
            for group in RSS_GROUPS:
                try:
                    task = asyncio.create_task(
                        process_group(session, group, status, db)
                    )
                    tasks.append(task)
                except Exception as e:
                    logger.error(f"⚠️ 创建任务失败 [{group['name']}]: {str(e)}")
            
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            
            # 批量发送任务
            batch_tasks = [
                process_batch_send(group, db) 
                for group in RSS_GROUPS 
                if group.get("batch_send_interval")
            ]
            if batch_tasks:
                await asyncio.gather(*batch_tasks, return_exceptions=True)
                
    except asyncio.CancelledError:
        logger.warning("⏹️ 任务被取消")
    except Exception as e:
        logger.error(f"主逻辑执行异常: {str(e)}")
        raise  # 重新抛出以便外层捕获
    finally:
        # 确保资源清理
        await cleanup_resources(db, lock_file)

async def connect_database_with_retry(db, max_attempts=3):
    """带重试的数据库连接"""
    for attempt in range(max_attempts):
        try:
            await db.open()
            await db.ensure_initialized()
            logger.info("✅ 数据库连接成功")
            return
        except Exception as e:
            logger.error(f"数据库连接失败 (尝试 {attempt + 1}/{max_attempts}): {e}")
            if attempt < max_attempts - 1:
                await asyncio.sleep(5)
            else:
                raise

async def cleanup_resources(db, lock_file):
    """清理资源"""
    try:
        if db:
            await db.close()
    except Exception as e:
        logger.error(f"关闭数据库失败: {e}")
    
    try:
        if lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()
            if LOCK_FILE.exists():
                LOCK_FILE.unlink()
    except Exception as e:
        logger.error(f"释放文件锁失败: {e}")

if __name__ == "__main__":
    for s in (signal.SIGINT, signal.SIGTERM):
        signal.signal(s, signal_handler)
    try:
        asyncio.run(main())
    except Exception as e:
        logger.critical(f"‼️ 主进程未捕获异常: {str(e)}", exc_info=True)
        sys.exit(1)