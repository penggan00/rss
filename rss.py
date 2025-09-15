import asyncio
import aiohttp
import logging
import re
import os
import hashlib
import pytz
import fcntl
import sqlite3
import time
import signal
import sys
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv
from feedparser import parse
from telegram import Bot
from telegram.error import BadRequest
from tencentcloud.common import credential
from tencentcloud.common.profile.client_profile import ClientProfile
from tencentcloud.common.profile.http_profile import HttpProfile
from tencentcloud.tmt.v20180321 import tmt_client, models
from urllib.parse import urlparse
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from md2tgmd import escape
from tencentcloud.common.exception.tencent_cloud_sdk_exception import TencentCloudSDKException

# 加载.env文件
load_dotenv()

# 配置绝对路径
BASE_DIR = Path(__file__).resolve().parent
# 创建锁文件
LOCK_FILE = BASE_DIR / "rss.lock"
# SQLite 数据库初始化
DATABASE_FILE = BASE_DIR / "rss.db"

# 增强日志配置
logging.basicConfig(
    filename=BASE_DIR / "rss.log",
    level=logging.WARNING,  # 只记录 WARNING/ERROR/CRITICAL
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    encoding="utf-8"
)

logger = logging.getLogger(__name__)

TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID").split(",")
TENCENTCLOUD_SECRET_ID = os.getenv("TENCENTCLOUD_SECRET_ID")
TENCENTCLOUD_SECRET_KEY = os.getenv("TENCENTCLOUD_SECRET_KEY")
TENCENT_REGION = os.getenv("TENCENT_REGION", "na-siliconvalley")
# 在环境变量加载后添加备用密钥配置
TENCENT_SECRET_ID = os.getenv("TENCENT_SECRET_ID")
TENCENT_SECRET_KEY = os.getenv("TENCENT_SECRET_KEY")
semaphore = asyncio.Semaphore(2)  # 并发控制，限制同时最多2个请求
# 配置备用域名（最多支持任意数量）
BACKUP_DOMAINS_STR = os.getenv("BACKUP_DOMAINS", "")
BACKUP_DOMAINS = [domain.strip() for domain in BACKUP_DOMAINS_STR.split(",") if domain.strip()]
# 定义时间间隔 (秒)  600秒 = 10分钟   1200秒 = 20分钟   1800秒 = 30分钟  3600秒 = 1小时   7200秒 = 2小时   10800秒 = 3小时

RSS_GROUPS = [
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
        "interval": 3590,      # 55分钟 
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
            "translate": False,       #翻译关
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
    #        'https://rsshub.app/10jqka/realtimenews',
            'https://36kr.com/feed-newsflash',  # 36氪快讯
        #    'https://36kr.com/feed',  # 36氪综合
            
        ],
        "group_key": "FOURTH_RSS_FEEDS",
        "interval": 960,       # 11分钟 
        "history_days": 5,     # 新增，保留30天
        "bot_token": os.getenv("RSS_LINDA"),  
        "processor": {
            "translate": False,     #翻译开关
            "header_template": "📢 *{source}*\n",  # 新增标题模板 ★
            "template": "*{subject}*\n[more]({url})",
            "preview": False,            # 预览
            "show_count": False          #计数
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
        "group_key": "FIFTH_RSS_FEEDS",
        "interval": 17990,    # 5小时
        "history_days": 300,     # 新增，保留30天
        "bot_token": os.getenv("YOUTUBE_RSS"), 
        "processor": {
            "translate": True,
            "header_template": "📢 *{source}*\n",  # 新增标题模板 ★
         #   "template": "*{subject}*\n🔗 {url}",
            "template": "*{subject}*\n[more]({url})",
            "preview": True,        # 预览
            "show_count": False     #计数
        }
    },
    # ================== 新浪博客 ==================
    {
        "name": "社交媒体",
        "urls": [
            'https://rsshub.app/weibo/user/3194547262',  # 江西高速
         #   'https://rsshub.app/weibo/user/1699432410',  # 新华社
        #    'https://rsshub.app/weibo/user/2656274875',  # 央视新闻
            'https://rsshub.app/weibo/user/2716786595',  # 聚萍乡
            'https://rsshub.app/weibo/user/1891035762',  # 交警
       #     'https://rsshub.app/weibo/user/3917937138',  # 发布
        #    'https://rsshub.app/weibo/user/3213094623',  # 邮政
        #    'https://rsshub.app/weibo/user/2818241427',  # 冒险岛

        
        ],
        "group_key": "FIFTH_RSSSA_FEEDS",
        "interval": 10800,    # 3小时 
        "history_days": 300,     # 新增，保留300天
        "bot_token": os.getenv("RRSS_LINDA"), 
        "processor": {
            "translate": False,
            "header_template": "📢 *{source}*\n",  # 新增标题模板 ★
         #   "template": "*{subject}*\n🔗 {url}",
            "template": "*{subject}*\n[more]({url})",
            "preview": False,        # 预览
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
        "interval": 290,       # 4分钟 
        "history_days": 3,     # 新增，保留30天
        "bot_token": os.getenv("RSS_SAN"),
        "processor": {
            "translate": False,                  #翻译开关
            "header_template": "📢 *{source}*\n",  # 新增标题模板 ★
            "template": "*{subject}*\n[more]({url})",
            "filter": {
                "enable": False,  # 过滤开关     False: 关闭 / True: 开启
                "mode": "allow",  # allow模式：包含关键词才发送 / block模式：包含关键词不发送
                "keywords": ["免", "cf", "cl", "黑", "低", "小", "卡", "年", "bug", "白", "github",  "节",  "闪",  "cc", "rn", "动", "cloudcone", "docker", "折"]  # 本组关键词列表
            },
            "preview": False,               # 预览
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
        "bot_token": os.getenv("RSS_SAN"), 
        "processor": {
            "translate": True,
            "header_template": "📢 *{source}*\n",  # 新增标题模板 ★
         #   "template": "*{subject}*\n🔗 {url}",
            "template": "*{subject}*\n[more]({url})",
            "preview": False,        # 预览
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
        "group_key": "YOUTUBE_RSSS_FEEDS",
        "interval": 3590,      # 55分钟
        "history_days": 360,     # 新增，保留30天
        "bot_token": os.getenv("RSS_TOKEN"),
        "processor": {
            "translate": False,
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
        "group_key": "FIFTH_RSS_YOUTUBE",
        "interval": 35990,     # 10小时
        "history_days": 360,     # 新增，保留300天
        "bot_token": os.getenv("YOUTUBE_RSS"),
        "processor": {
        "translate": False,                    #翻译开关
        "header_template": "📢 *{source}*\n",  # 新增标题模板 ★
    #   "template": "*{subject}*\n🔗 {url}",
        "template": "*{subject}*\n[more]({url})",
        "preview": True,                       # 预览
        "show_count": False                    #计数
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
        "interval": 6990,      # 1小时56分钟
        "history_days": 30,     # 新增，保留30天
        "bot_token": os.getenv("RSS_LINDA_YOUTUBE"),
        "processor": {
            "translate": False,                        #翻译开关
            "header_template": "📢 *{source}*\n",  # 新增标题模板 ★
            "template": "*{subject}*\n[more]({url})",
            "preview": False,                              # 预览
            "show_count": False                       #计数
        }
    }
]

# 新增通用处理函数
async def process_group(session, group_config, global_status):
    """统一处理RSS组（确保发送成功后才保存状态，所有状态都用canonical_url）"""
    group_name = group_config["name"]
    group_key = group_config["group_key"]
    processor = group_config["processor"]
    bot_token = group_config["bot_token"]

    try:
        # ========== 1. 检查时间间隔 ==========
        last_run = await load_last_run_time_from_db(group_key)
        now = datetime.now(pytz.utc).timestamp()
        if (now - last_run) < group_config["interval"]:
            return  # 未到间隔时间，跳过处理

        bot = Bot(token=bot_token)

        # ========== 2. 处理每个URL源 ==========
        for index, feed_url in enumerate(group_config["urls"]):
            try:
                # ===== 2.0 源间延迟 =====
                if index > 0:
                    await asyncio.sleep(1)  # 源间延迟1秒

                # ------ 2.1 获取Feed数据 ------
                feed_data, canonical_url = await fetch_feed(session, feed_url)
                if not feed_data or not feed_data.entries:
                    continue
                processed_ids = global_status.get(canonical_url, set())

                # ------ 2.2 加载处理状态 & 收集新条目 ------
                new_entries = []
                pending_entry_ids = []
                seen_in_batch = set()

                for entry in feed_data.entries:
                    entry_id = get_entry_identifier(entry)
                    if entry_id in processed_ids or entry_id in seen_in_batch:
                        continue
                    seen_in_batch.add(entry_id)

                    # 关键词过滤（如果启用）
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

                    new_entries.append(entry)
                    pending_entry_ids.append(entry_id)

                # ===== 2.3 发送消息（成功后保存状态） =====
                if new_entries:
                    feed_message = await generate_group_message(feed_data, new_entries, processor)
                    if feed_message:
                        try:
                            await send_single_message(
                                bot,
                                TELEGRAM_CHAT_ID[0],
                                feed_message,
                                disable_web_page_preview=not processor.get("preview", True)
                            )
                            for entry_id in pending_entry_ids:
                                await save_single_status(group_key, canonical_url, entry_id)
                                processed_ids.add(entry_id)

                            global_status[canonical_url] = processed_ids

                        except Exception as send_error:
                            logger.error(f"❌ 发送消息失败 [{feed_url}]")
                            raise

            except Exception as e:
                logger.error(f"❌ 处理失败 [{feed_url}]")

        # ========== 3. 保存最后运行时间 ==========
        await save_last_run_time_to_db(group_key, now)
    except Exception as e:
        logger.critical(f"‼️ 处理组失败 [{group_key}]")

async def generate_group_message(feed_data, entries, processor):
    """生成标准化消息内容"""
    try:
        # ===== 1. 基础信息处理 =====
        source_name = feed_data.feed.get('title', "未知来源")
        safe_source = escape(source_name)
        
        # ===== 新增：标题处理 =====
        header = ""
        if "header_template" in processor:
            header = processor["header_template"].format(source=safe_source) + "\n"
        
        messages = []

        # ===== 2. 处理每个条目 =====
        for entry in entries:
            # -- 2.1 标题处理 --
            raw_subject = remove_html_tags(entry.title or "无标题")
            if processor["translate"]:
                translated_subject = await auto_translate_text(raw_subject)
            else:
                translated_subject = raw_subject
            safe_subject = escape(translated_subject)

            # -- 2.2 链接处理 --
            raw_url = entry.link
            safe_url = escape(raw_url)

            # -- 2.3 构建消息 --
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

def create_connection():
    """创建 SQLite 数据库连接"""
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        return conn
    except sqlite3.Error as e:
        logger.error(f"连接数据库失败: {e}")
    return conn

def create_table():
    conn = create_connection()
    if conn is not None:
        try:
            cursor = conn.cursor()
            # 仅保留SQLite建表语句
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS rss_status (
                    feed_group TEXT,
                    feed_url TEXT,
                    entry_url TEXT,
                    entry_timestamp REAL,
                    PRIMARY KEY (feed_group, feed_url, entry_url)
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS timestamps (
                    feed_group TEXT PRIMARY KEY,
                    last_run_time REAL
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS cleanup_timestamps (
                    feed_group TEXT PRIMARY KEY,
                    last_cleanup_time REAL
                )
            """)
            conn.commit()
        except sqlite3.Error as e:
            logger.error(f"创建本地表失败: {e}")
        finally:
            conn.close()

async def load_last_run_time_from_db(feed_group):
    """仅使用SQLite加载时间戳"""
    conn = create_connection()
    if conn is not None:
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT last_run_time FROM timestamps WHERE feed_group = ?", (feed_group,))
            result = cursor.fetchone()
            return result[0] if result else 0
        except sqlite3.Error as e:
            logger.error(f"从本地数据库加载时间失败: {e}")
            return 0
        finally:
            conn.close()
    return 0

async def save_last_run_time_to_db(feed_group, last_run_time):
    conn = create_connection()
    if conn is not None:
        try:
            cursor = conn.cursor()
            cursor.execute("BEGIN TRANSACTION")
            cursor.execute("""
                INSERT OR REPLACE INTO timestamps (feed_group, last_run_time)
                VALUES (?, ?)
            """, (feed_group, last_run_time))
            conn.commit()
        except sqlite3.Error as e:
            logger.error(f"时间戳保存失败: {e}")
            conn.rollback()
        finally:
            conn.close()

def remove_html_tags(text):
    text = re.sub(r'#([^#\s]+)#', r'\1', text)  # 匹配 #文字# → 文字
    text = re.sub(r'#\w+', '', text)    # 移除 hashtags
    text = re.sub(r'@[^\s]+', '', text).strip()     # 移除 @提及
    text = re.sub(r'【\s*】', '', text)    # 移除 【】符号（含中间空格）
    text = re.sub(r'(?<!\S)#(?!\S)', '', text)
    text = re.sub(r'(?<!\S)：(?!\S)', '', text)
    return text


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

        # 按段落分割保持结构
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

        # v20.x 的正确参数
        for chunk in text_chunks:
            await bot.send_message(
                chat_id=chat_id,
                text=chunk,
                parse_mode='MarkdownV2',
                disable_web_page_preview=disable_web_page_preview,
                read_timeout=10,  # 读取超时
                write_timeout=10  # 写入超时
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
    """
    获取RSS Feed数据，只有rsshub.app主域名才用备用域名，否则只用原始域名。
    返回: feed_data, canonical_url
    """
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/89.0.4389.82 Safari/537.36'}
    parsed = urlparse(feed_url)
    is_rsshub = parsed.netloc == "rsshub.app"  # 只允许主域名使用备用域名

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
                        continue  # 尝试下一个域名
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

async def load_status():
    status = {}
    conn = create_connection()
    if conn is not None:
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT feed_url, entry_url FROM rss_status")
            for feed_url, entry_url in cursor.fetchall():
                if feed_url not in status:
                    status[feed_url] = set()
                status[feed_url].add(entry_url)
        except sqlite3.Error as e:
            logger.error(f"本地状态加载失败: {e}")
        finally:
            conn.close()
    return status

async def save_single_status(feed_group, feed_url, entry_url):
    timestamp = time.time()
    max_retries = 3
    for attempt in range(max_retries):
        conn = create_connection()
        if conn:
            try:
                cursor = conn.cursor()
                cursor.execute("BEGIN TRANSACTION")
                cursor.execute("""
                    INSERT OR IGNORE INTO rss_status 
                    VALUES (?, ?, ?, ?)
                """, (feed_group, feed_url, entry_url, timestamp))
                conn.commit()
                return
            except sqlite3.OperationalError as e:
                if "locked" in str(e) and attempt < max_retries - 1:
                    await asyncio.sleep(0.1 * (attempt + 1))
                    continue
                logger.error(f"SQLite保存失败（尝试{attempt+1}次）: {e}")
            except sqlite3.Error as e:
                logger.error(f"SQLite错误: {e}")
            finally:
                conn.close()

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

def get_entry_timestamp(entry):
    dt = datetime.now(pytz.UTC)
    if hasattr(entry, 'published_parsed') and entry.published_parsed:
        dt = datetime(*entry.published_parsed[:6], tzinfo=pytz.utc)
    elif hasattr(entry, 'pubDate_parsed') and entry.pubDate_parsed:
        dt = datetime(*entry.pubDate_parsed[:6], tzinfo=pytz.utc)
    elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
        dt = datetime(*entry.updated_parsed[:6], tzinfo=pytz.utc)
    return dt

async def process_feed_common(session, feed_group, feed_url, status):
    try:
        feed_data = await fetch_feed(session, feed_url)
        if not feed_data or not feed_data.entries:
            return None
        processed_ids = status.get(feed_url, set())
        new_entries = []
        for entry in feed_data.entries:
            entry_id = get_entry_identifier(entry)
            if entry_id in processed_ids:
                continue
            new_entries.append(entry)
            await save_single_status(feed_group, feed_url, entry_id)
            processed_ids.add(entry_id)
        status[feed_url] = processed_ids  # 更新内存状态
        return feed_data, new_entries
    except Exception as e:
        return None

def cleanup_history(days, feed_group):
    conn = create_connection()
    if conn:
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT last_cleanup_time FROM cleanup_timestamps WHERE feed_group = ?", 
                (feed_group,)
            )
            result = cursor.fetchone()
            last_cleanup = result[0] if result else 0
            now = time.time()
            if now - last_cleanup < 86400:
                return
            cutoff_ts = now - days * 86400
            cursor.execute(
                "DELETE FROM rss_status WHERE feed_group=? AND entry_timestamp < ?",
                (feed_group, cutoff_ts)
            )
            affected_rows = cursor.rowcount
            cursor.execute("""
                INSERT OR REPLACE INTO cleanup_timestamps (feed_group, last_cleanup_time)
                VALUES (?, ?)
            """, (feed_group, now))
            conn.commit()
        except sqlite3.Error as e:
            logger.error(f"❌ 日志清理失败: 组={feed_group}, 错误={e}")
        finally:
            conn.close()

def signal_handler(signum, frame):
    logger.warning(f"收到信号 {signum}，程序即将退出。")
    sys.exit(0)

async def main():
    lock_file = None
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
        create_table()
    except Exception as e:
        logger.critical(f"‼️ 数据库初始化失败: {str(e)}")
        return
    for group in RSS_GROUPS:
        days = group.get("history_days", 30)
        try:
            cleanup_history(days, group["group_key"])
        except Exception as e:
            logger.error(f"清理历史记录异常: 组={group['group_key']}, 错误={e}")
    async with aiohttp.ClientSession() as session:
        try:
            status = await load_status()
            tasks = []
            for group in RSS_GROUPS:
                try:
                    tasks.append(process_group(session, group, status))
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

if __name__ == "__main__":
    for s in (signal.SIGINT, signal.SIGTERM):
        signal.signal(s, signal_handler)
    create_table()
    try:
        asyncio.run(main())
    except Exception as e:
        logger.critical(f"‼️ 主进程未捕获异常: {str(e)}", exc_info=True)
        sys.exit(1)