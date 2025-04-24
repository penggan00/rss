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
from supabase.client import Client, create_client

# 加载.env文件
load_dotenv()

# Supabase 配置
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
USE_SUPABASE = SUPABASE_URL and SUPABASE_KEY
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").split(",")
TENCENTCLOUD_SECRET_ID = os.getenv("TENCENTCLOUD_SECRET_ID")
TENCENTCLOUD_SECRET_KEY = os.getenv("TENCENTCLOUD_SECRET_KEY")
MAX_CONCURRENT_REQUESTS = 2      #并发控制
semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

# 配置绝对路径
BASE_DIR = Path(__file__).resolve().parent

# 创建锁文件
LOCK_FILE = BASE_DIR / "rss.lock"

# SQLite 数据库初始化
DATABASE_FILE = BASE_DIR / "rss_status.db"

# 增强日志配置
logging.basicConfig(
    filename=BASE_DIR / "rss.log",
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    encoding="utf-8"
)
logger = logging.getLogger(__name__)

# 初始化全局客户端
supabase: Client = None

def init_supabase():
    global supabase
    if not USE_SUPABASE:
        return
    
    try:
        # 使用正确的异步初始化方式
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        
        # 验证连接
        test = supabase.table('rss_status').select("count", count="estimated").execute()
        logger.info(f"✅ Supabase连接成功 | 测试响应: {test}")
    except Exception as e:
        logger.critical(f"‼️ Supabase连接失败: {str(e)}")
        exit(1)

# 清理超过7天的日志文件
def clean_old_logs():
    log_file = BASE_DIR / "rss.log"
    if log_file.exists():
        log_modified_time = datetime.fromtimestamp(log_file.stat().st_mtime)
        if datetime.now() - log_modified_time > timedelta(days=3):
            try:
                log_file.unlink()
             #   logger.info("已清理超过7天的日志文件")
            except Exception as e:
                logger.error(f"清理日志文件失败: {e}")

# 在程序启动时执行日志清理
clean_old_logs()

# 定义时间间隔 (秒)  600秒 = 10分钟    1200秒 = 20分钟   1800秒 = 30分钟  3600秒 = 1小时   7200秒 = 2小时   10800秒 = 3小时
RSS_GROUPS = [
    # ================== 国际新闻组 (原RSS_FEEDS) ==================
    {
        "name": "国际新闻",
        "urls": [
            'https://feeds.bbci.co.uk/news/world/rss.xml',  # BBC
            'https://www3.nhk.or.jp/rss/news/cat6.xml',     # NHK
       #     'https://www.cnbc.com/id/100003114/device/rss/rss.html',  # CNBC
       #     'https://feeds.a.dj.com/rss/RSSWorldNews.xml',  # 华尔街日报
            'https://www.aljazeera.com/xml/rss/all.xml',    # 半岛电视台
       #     'https://www.ft.com/?format=rss',                 # 金融时报
       #     'https://www3.nhk.or.jp/rss/news/cat5.xml',  # NHK 商业
       #     'http://rss.cnn.com/rss/cnn_topstories.rss',   # cnn
       #     'https://www.theguardian.com/world/rss',     # 卫报
      #      'https://www.theverge.com/rss/index.xml',   # The Verge:
        ],
        "group_key": "RSS_FEEDS",
        "interval": 3300,      # 55分钟 
        "bot_token": os.getenv("RSS_TWO"), 
        "processor": {
            "translate": True,       #翻译开
            "template": "*{subject}*\n[{source}]({url})",
            "preview": False,         # 禁止预览
            "show_count": False        # ✅新增
        }
    },

    # ================== 快讯组 (原FOURTH_RSS_FEEDS) ==================
    {
        "name": "快讯",
        "urls": [
    #        'https://rsshub.app/10jqka/realtimenews',
            'https://36kr.com/feed-newsflash',  # 36氪快讯
        ],
        "group_key": "FOURTH_RSS_FEEDS",
        "interval": 700,       # 11分钟 
        "bot_token": os.getenv("RSS_LINDA"),  
        "processor": {
            "translate": False,     #翻译开关
            "template": "*{subject}*\n[{source}]({url})",
            "preview": False,            # 预览
            "show_count": False          #计数
        }
    },

    # ================== 社交媒体组 (原FIFTH_RSS_FEEDS) ==================
    {
        "name": "社交媒体",
        "urls": [
        #    'https://rsshub.app/twitter/media/clawcloud43609', # claw.cloud
            'https://rsshub.app/twitter/media/ElonMuskAOC',   # Elon Musk
        #    'https://rsshub.app/twitter/media/elonmusk',   # Elon Musk
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCQeRaTukNYft1_6AZPACnog',  # Asmongold
        ],
        "group_key": "FIFTH_RSS_FEEDS",
        "interval": 7000,      # 1小时56分钟
        "bot_token": os.getenv("YOUTUBE_RSS"), 
        "processor": {
            "translate": True,
            "header_template": "📢 *{source}*\n",  # 新增标题模板 ★
            "template": "*{subject}*\n🔗 {url}",
            "preview": True,        # 预览
            "show_count": False     #计数
        }
    },

    # ================== 技术论坛组 (原FIFTH_RSS_RSS_SAN) ==================
    {
        "name": "技术论坛",
        "urls": [
            'https://rss.nodeseek.com/',  # Nodeseek
        ],
        "group_key": "FIFTH_RSS_RSS_SAN",
        "interval": 240,       # 4分钟 
        "bot_token": os.getenv("RSS_SAN"),
        "processor": {
            "translate": False,                  #翻译开关
            "template": "*{subject}*\n[{source}]({url})",
            "filter": {
                "enable": False,  # 过滤开关     False: 关闭 / True: 开启
                "mode": "allow",  # allow模式：包含关键词才发送 / block模式：包含关键词不发送
                "keywords": ["免", "c", "黑", "活", "出", "福", "低", "香", "永", "收", "小", "卡", "年", "优", "bug", "值", "白","折"]  # 本组关键词列表
            },
            "preview": False,               # 预览
            "show_count": False               #计数
        }
    },

    # ================== YouTube频道组 (原YOUTUBE_RSSS_FEEDS) ==================
    {
        "name": "YouTube频道",
        "urls": [
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
        "interval": 3300,      # 55分钟
        "bot_token": os.getenv("RSS_TOKEN"),
        "processor": {
            "translate": False,
            "template": "*{subject}*\n[{source}]({url})",
            "preview": True,                # 预览
            "show_count": False               #计数
        }
    },

    # ================== 中文YouTube组 (原FIFTH_RSS_YOUTUBE) ==================
    {
        "name": "中文YouTube",
        "urls": [
            'https://blog.090227.xyz/atom.xml',
        #    'https://www.freedidi.com/feed',
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCUNciDq-y6I6lEQPeoP-R5A', # 苏恒观察
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCXkOTZJ743JgVhJWmNV8F3Q', # 寒國人
            'https://www.youtube.com/feeds/videos.xml?channel_id=UC2r2LPbOUssIa02EbOIm7NA', # 星球熱點
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCF-Q1Zwyn9681F7du8DMAWg', # 謝宗桓-老謝來了
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCSYBgX9pWGiUAcBxjnj6JCQ', # 郭正亮頻道
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCNiJNzSkfumLB7bYtXcIEmg', # 真的很博通
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCN0eCImZY6_OiJbo8cy5bLw', # 屈機TV
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCb3TZ4SD_Ys3j4z0-8o6auA', # BBC News 中文
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCiwt1aanVMoPYUt_CQYCPQg', # 全球大視野
            'https://www.youtube.com/feeds/videos.xml?channel_id=UC000Jn3HGeQSwBuX_cLDK8Q', # 我是柳傑克
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCQFEBaHCJrHu2hzDA_69WQg', # 国漫说
            'https://www.youtube.com/feeds/videos.xml?channel_id=UChJ8YKw6E1rjFHVS9vovrZw', # BNE TV - 新西兰中文国际频道
        # 影视
            'https://www.youtube.com/feeds/videos.xml?channel_id=UC7Xeh7thVIgs_qfTlwC-dag', # Marc TV
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCCD14H7fJQl3UZNWhYMG3Mg', # 温城鲤
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCQO2T82PiHCYbqmCQ6QO6lw', # 月亮說
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCHW6W9g2TJL2_Lf7GfoI5kg', # 电影放映厅

        ],
        "group_key": "FIFTH_RSS_YOUTUBE",
        "interval": 10400,     # 2小时53分钟
        "bot_token": os.getenv("YOUTUBE_RSS"),
        "processor": {
        "translate": False,                    #翻译开关
        "header_template": "📢 *{source}*\n",  # 新增标题模板 ★
        "template": "*{subject}*\n🔗 {url}",  # 条目模板
        "preview": True,                       # 预览
        "show_count": False                    #计数
    }
    },

    # ================== 中文媒体组 (原THIRD_RSS_FEEDS) ==================
    {
        "name": "中文媒体", 
        "urls": [
            'https://rsshub.app/guancha',
            'https://rsshub.app/china',
            'https://rsshub.app/guancha/headline',
        ],
        "group_key": "THIRD_RSS_FEEDS",
        "interval": 7000,      # 1小时56分钟 (原THIRD_RSS_FEEDS_INTERVAL)
        "bot_token": os.getenv("RSS_LINDA_YOUTUBE"),
        "processor": {
            "translate": False,                        #翻译开关
            "template": "*{subject}*\n[{source}]({url})",
            "preview": False,                              # 预览
            "show_count": False                       #计数
        }
    }
]

# 新增通用处理函数
async def process_group(session, group_config, global_status):
    """统一处理RSS组（优化版：确保发送成功后才保存状态）"""
    group_name = group_config["name"]
    group_key = group_config["group_key"]
    processor = group_config["processor"]
    bot_token = group_config["bot_token"]
    
    try:
        # ========== 0. 初始延迟 ==========
        await asyncio.sleep(1)  # 组间初始延迟1秒

        # ========== 1. 检查时间间隔 ==========
        last_run = await load_last_run_time_from_db(group_key)
        now = time.time()
        if (now - last_run) < group_config["interval"]:
            return  # 未到间隔时间，跳过处理

  #      logger.info(f"🚀 开始处理 [{group_name}] 源...")
        bot = Bot(token=bot_token)

        # ========== 2. 处理每个URL源 ==========
        for index, feed_url in enumerate(group_config["urls"]):
            try:
                # ===== 2.0 源间延迟 =====
                if index > 0:  # 第一个源不需要延迟
                    await asyncio.sleep(1)  # 源间延迟1秒

                # ------ 2.1 获取Feed数据 ------
                feed_data = await fetch_feed(session, feed_url)
                if not feed_data or not feed_data.entries:
                    logger.warning(f"⚠️ 空数据源 [{feed_url}]")
                    continue

                # ------ 2.2 加载处理状态 & 收集新条目 ------
                processed_ids = global_status.get(feed_url, set())
                new_entries = []
                pending_entry_ids = []  # 待保存的条目ID（发送成功后才保存）
                seen_in_batch = set()  # 临时存储当前批次的ID，避免重复

                for entry in feed_data.entries:
                    entry_id = get_entry_identifier(entry)
                    if entry_id in processed_ids or entry_id in seen_in_batch:  # 新增批次内去重
                        continue
                    seen_in_batch.add(entry_id)

                    # 关键词过滤（如果启用）
                    filter_config = processor.get("filter", {})
                    if filter_config.get("enable", False):
                        raw_title = remove_html_tags(entry.title or "")
                        keywords = filter_config.get("keywords", [])
                        match = any(kw.lower() in raw_title.lower() for kw in keywords)
                        # 根据模式判断是否跳过
                        if filter_config.get("mode", "allow") == "allow":
                            if not match:  # 允许模式：不包含关键词则跳过
                                continue
                        else:  # block模式
                            if match:     # 包含关键词则跳过
                                continue

                    new_entries.append(entry)
                    pending_entry_ids.append(entry_id)  # 暂存，不立即保存

                # ===== 2.3 发送消息（成功后保存状态） =====
                if new_entries:
                    await asyncio.sleep(1)  # 发送前延迟1秒
                    feed_message = await generate_group_message(feed_data, new_entries, processor)
                    if feed_message:
                        try:
                            # 尝试发送消息
                            await send_single_message(
                                bot,
                                TELEGRAM_CHAT_ID[0],
                                feed_message,
                                disable_web_page_preview=not processor.get("preview", True)
                            )
                 #           logger.info(f"📤 已发送 {len(new_entries)} 条内容 [{feed_url}]")

                            # 发送成功，保存所有条目状态
                            for entry_id in pending_entry_ids:
                                await save_single_status(group_key, feed_url, entry_id)
                                processed_ids.add(entry_id)

                            # 更新内存状态
                            global_status[feed_url] = processed_ids

                        except Exception as send_error:
                            logger.error(f"❌ 发送消息失败 [{feed_url}]: {str(send_error)}")
                            raise  # 抛出异常，阻止后续保存操作

            except Exception as e:
                logger.error(f"❌ 处理源失败 [{feed_url}]: {str(e)}", exc_info=True)

        # ========== 3. 保存最后运行时间 ==========
        await save_last_run_time_to_db(group_key, now)

        # ========== 4. 最终延迟 ==========
        await asyncio.sleep(1)  # 组处理完成后延迟3秒

    except Exception as e:
        logger.critical(f"‼️ 处理组失败 [{group_name}]: {str(e)}", exc_info=True)
   # finally:
    #    logger.info(f"🏁 完成处理 [{group_name}]")

async def generate_group_message(feed_data, entries, processor):
    """生成标准化消息内容"""
    try:
        # ===== 1. 基础信息处理 =====
        source_name = feed_data.feed.get('title', "未知来源")
        safe_source = escape_markdown_v2(source_name)
        
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
            safe_subject = escape_markdown_v2(translated_subject)

            # -- 2.2 链接处理 --
            raw_url = entry.link
            safe_url = escape_markdown_v2(raw_url)

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

# 修改后的数据库初始化函数
def create_table():
    """仅验证表结构存在"""
    global USE_SUPABASE
    
    # 强制重新加载环境变量
    load_dotenv(override=True)
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_KEY = os.getenv("SUPABASE_KEY")
    USE_SUPABASE = bool(SUPABASE_URL and SUPABASE_KEY)
    
  #  logger.info(f"🛢️ 当前使用数据库类型: {'Supabase' if USE_SUPABASE else 'SQLite'}")
    
    if USE_SUPABASE:
        init_supabase()  # 调用初始化函数
        try:
            # 简单查询验证表存在
            supabase.table("rss_status").select("*").limit(1).execute()
            supabase.table("timestamps").select("*").limit(1).execute()
       #     logger.info("✅ Supabase表结构验证通过")
        except Exception as e:
            logger.critical(f"‼️ 表结构验证失败: {str(e)}")
            logger.critical("请先在Supabase控制台手动创建表结构")
            exit(1)
    else:
        try:
            conn = sqlite3.connect(DATABASE_FILE)
            cursor = conn.cursor()
            
            # SQLite表结构
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
                CREATE INDEX IF NOT EXISTS idx_entry_timestamp 
                ON rss_status (entry_timestamp)
            """)
            
            conn.commit()
       #     logger.info("✅ SQLite表结构验证完成")
        except sqlite3.Error as e:
            logger.critical(f"‼️ SQLite初始化失败: {str(e)}")
            exit(1)
        finally:
            conn.close()

async def load_last_run_time_from_db(feed_group):
    """加载最后运行时间"""
    if USE_SUPABASE:
        try:
            data = supabase.table('timestamps')\
                .select('last_run_time')\
                .eq('feed_group', feed_group)\
                .execute()
            return data.data[0]['last_run_time'] if data.data else 0
        except Exception as e:
            logger.error(f"Supabase时间加载失败: {e}")
            return 0
    else:
        conn = sqlite3.connect(DATABASE_FILE)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT last_run_time FROM timestamps WHERE feed_group = ?", (feed_group,))
            result = cursor.fetchone()
            return result[0] if result else 0
        except sqlite3.Error as e:
            logger.error(f"SQLite时间加载失败: {e}")
            return 0
        finally:
            conn.close()

async def save_last_run_time_to_db(feed_group, last_run_time):
    """保存最后运行时间"""
    last_run_time = int(last_run_time)  # 转换为整数
    if USE_SUPABASE:
        try:
            supabase.table('timestamps').upsert({
                'feed_group': feed_group,
                'last_run_time': last_run_time
            }).execute()
        except Exception as e:
            logger.error(f"Supabase时间保存失败: {e}")
    else:
        conn = sqlite3.connect(DATABASE_FILE)
        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO timestamps 
                VALUES (?, ?)
            """, (feed_group, last_run_time))
            conn.commit()
        except sqlite3.Error as e:
            logger.error(f"SQLite时间保存失败: {e}")
        finally:
            conn.close()

# 函数 (保持不变，除非另有说明)
def remove_html_tags(text):
    """彻底移除hashtags, @符号, 以及"【 】" 样式的符号"""
    text = re.sub(r'#\w+', '', text)  # 移除hashtags
    text = re.sub(r'@[^\s]+', '', text).strip()  # 删除@后面的字符
    text = re.sub(r'【\s*】', '', text)  # 移除"【 】" 样式的符号，包含中间的空格
    return text

def escape_markdown_v2(text, exclude=None):
    """自定义MarkdownV2转义函数"""
    if exclude is None:
        exclude = []
    chars = '_*[]()~`>#+-=|{}.!'
    chars_to_escape = [c for c in chars if c not in exclude]
    pattern = re.compile(f'([{"".join(map(re.escape, chars_to_escape))}])')
    return pattern.sub(r'\\\1', text)

@retry(
    stop=stop_after_attempt(3),
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
        logging.error(f"消息发送失败: {e}")
        raise

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError)),
)
async def fetch_feed(session, feed_url):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/89.0.4389.82 Safari/537.36'}
    try:
        async with semaphore:
            async with session.get(feed_url, headers=headers, timeout=30) as response:
                # 统一处理临时性错误（503/403）
                if response.status in (503, 403,404,429):
                    logger.warning(f"RSS源暂时不可用（{response.status}）: {feed_url}")
                    return None  # 跳过当前源，下次运行会重试
                response.raise_for_status()
                return parse(await response.read())
    except aiohttp.ClientResponseError as e:
        if e.status in (503, 403,404,429):
            logger.warning(f"RSS源暂时不可用（{e.status}）: {feed_url}")
            return None
        logging.error(f"HTTP 错误 {e.status} 抓取失败 {feed_url}: {e}")
        raise
    except Exception as e:
        logging.error(f"抓取失败 {feed_url}: {e}")
        raise

async def auto_translate_text(text):
    try:
        cred = credential.Credential(TENCENTCLOUD_SECRET_ID, TENCENTCLOUD_SECRET_KEY)
        clientProfile = ClientProfile(httpProfile=HttpProfile(endpoint="tmt.tencentcloudapi.com"))
        client = tmt_client.TmtClient(cred, "na-siliconvalley", clientProfile)

        req = models.TextTranslateRequest()
        req.SourceText = remove_html_tags(text)  # 翻译前先移除HTML
        req.Source = "auto"
        req.Target = "zh"
        req.ProjectId = 0

        return client.TextTranslate(req).TargetText
    except Exception as e:
        logging.error(f"翻译错误: {e}")
        return text

async def load_status():
    """加载处理状态"""
    status = {}
    if USE_SUPABASE:
        try:
            data = supabase.table('rss_status')\
                .select('feed_url, entry_url')\
                .execute()
            for item in data.data:
                if item['feed_url'] not in status:
                    status[item['feed_url']] = set()
                status[item['feed_url']].add(item['entry_url'])
        except Exception as e:
            logger.error(f"Supabase状态加载失败: {e}")
    else:
        conn = sqlite3.connect(DATABASE_FILE)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT feed_url, entry_url FROM rss_status")
            for feed_url, entry_url in cursor.fetchall():
                if feed_url not in status:
                    status[feed_url] = set()
                status[feed_url].add(entry_url)
        except sqlite3.Error as e:
            logger.error(f"SQLite状态加载失败: {e}")
        finally:
            conn.close()
    return status

async def save_single_status(feed_group, feed_url, entry_url):
    """保存单条状态"""
    timestamp = int(time.time())  # 强制转换为整数
    if USE_SUPABASE:
        try:
            supabase.table('rss_status').upsert({
                'feed_group': feed_group,
                'feed_url': feed_url,
                'entry_url': entry_url,
                'entry_timestamp': timestamp  # 确保是整数
            }).execute()
        except Exception as e:
            logger.error(f"Supabase状态保存失败: {e}")
    else:
        conn = sqlite3.connect(DATABASE_FILE)
        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR IGNORE INTO rss_status 
                VALUES (?, ?, ?, ?)
            """, (feed_group, feed_url, entry_url, timestamp))
            conn.commit()
        except sqlite3.Error as e:
            logger.error(f"SQLite状态保存失败: {e}")
        finally:
            conn.close()

async def clean_old_entries(feed_group, max_age_days=30):
    """清理旧记录"""
    cutoff_time = int(time.time() - max_age_days * 24 * 3600)  # 强制转换为整数
    if USE_SUPABASE:
        try:
            supabase.table('rss_status')\
                .delete()\
                .lt('entry_timestamp', cutoff_time)\
                .eq('feed_group', feed_group)\
                .execute()
        except Exception as e:
            logger.error(f"Supabase清理失败: {e}")
    else:
        conn = sqlite3.connect(DATABASE_FILE)
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM rss_status WHERE feed_group = ? AND entry_timestamp < ?", 
                         (feed_group, cutoff_time))
            conn.commit()
        except sqlite3.Error as e:
            logger.error(f"SQLite清理失败: {e}")
        finally:
            conn.close()

def get_entry_identifier(entry):
    # 优先使用guid
    if hasattr(entry, 'guid') and entry.guid:
        return hashlib.sha256(entry.guid.encode()).hexdigest()
    
    # 标准化链接处理
    link = getattr(entry, 'link', '')
    if link:
        try:
            parsed = urlparse(link)
            # 移除查询参数、片段，并统一为小写
            clean_link = parsed._replace(query=None, fragment=None).geturl().lower()
            return hashlib.sha256(clean_link.encode()).hexdigest()
        except Exception as e:
            logger.warning(f"URL解析失败 {link}: {e}")
    
    # 最后使用标题+发布时间组合
    title = getattr(entry, 'title', '')
    pub_date = get_entry_timestamp(entry).isoformat() if get_entry_timestamp(entry) else ''
    return hashlib.sha256(f"{title}|||{pub_date}".encode()).hexdigest()

def get_entry_timestamp(entry):
    """返回UTC时间"""
    dt = datetime.now(pytz.UTC)  # 默认值
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
            # 立即保存到数据库
            await save_single_status(feed_group, feed_url, entry_id)
            # 更新内存中的状态，防止同一批次内重复
            processed_ids.add(entry_id)

        status[feed_url] = processed_ids  # 更新内存状态
        return feed_data, new_entries

    except Exception as e:
        logger.error(f"处理源异常 {feed_url}: {e}")
        return None

async def main():
    # 在程序开始时强制重新加载.env文件
    from dotenv import load_dotenv
    load_dotenv(override=True)  # 添加override参数
    """主处理函数"""
    # ================== 1. 文件锁处理 ==================
    lock_file = None
    try:
        lock_file = open(LOCK_FILE, "w")
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    #    logger.info("🔒 成功获取文件锁，启动处理流程")
    except OSError:
        logger.warning("⛔ 无法获取文件锁，已有实例在运行，程序退出")
        return
    except Exception as e:
        logger.critical(f"‼️ 文件锁异常: {str(e)}")
        return

    # ================== 2. 数据库初始化 ==================
    global supabase
    if USE_SUPABASE:
        try:
            supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
            # 验证连接有效性
            supabase.table("rss_status").select("count", count="exact").execute()
            logger.info("🔌 Supabase连接验证成功")
        except Exception as e:
            logger.critical(f"‼️ Supabase连接失败: {str(e)}")
            exit(1)

    # ================== 3. 旧日志清理 ==================
    try:
        clean_old_logs()
    #    logger.info("🗑️ 旧日志清理完成")
    except Exception as e:
        logger.error(f"⚠️ 日志清理失败: {str(e)}")

    # ================== 4. 主处理流程 ==================
    async with aiohttp.ClientSession() as session:
        try:
            # ===== 4.1 加载处理状态 =====
            status = await load_status()
     #       logger.info("📂 加载历史状态完成")

            # ===== 4.2 清理旧记录 =====
            retention_config = {
                "RSS_FEEDS": 30,
                "THIRD_RSS_FEEDS": 30,
                "FOURTH_RSS_FEEDS": 7,
                "FIFTH_RSS_FEEDS": 30,
                "FIFTH_RSS_RSS_SAN": 7,
                "YOUTUBE_RSSS_FEEDS": 600,
                "FIFTH_RSS_YOUTUBE": 600
            }
            
            for group in RSS_GROUPS:
                try:
                    await clean_old_entries(
                        group["group_key"], 
                        retention_config.get(group["group_key"], 30)
                    )
                except Exception as e:
                    logger.error(f"⚠️ 清理旧记录失败 [{group['name']}]: {str(e)}")

            # ===== 4.3 创建处理任务 =====
            tasks = []
            for group in RSS_GROUPS:
                try:
                    tasks.append(process_group(session, group, status))
              #      logger.debug(f"📨 已创建处理任务 [{group['name']}]")
                except Exception as e:
                    logger.error(f"⚠️ 创建任务失败 [{group['name']}]: {str(e)}")

            # ===== 4.4 并行执行任务 =====
            if tasks:
                await asyncio.gather(*tasks)
          #      logger.info("🚩 所有处理任务已完成")
            else:
                logger.warning("⛔ 未创建任何处理任务")

        except asyncio.CancelledError:
            logger.warning("⏹️ 任务被取消")
        except Exception as e:
            logger.critical(f"‼️ 主循环异常: {str(e)}", exc_info=True)
        finally:
            # ===== 4.5 最终清理 =====
            try:
                await session.close()
     #           logger.info("🔌 已关闭网络会话")
            except Exception as e:
                logger.error(f"⚠️ 关闭会话失败: {str(e)}")

    # ================== 5. 释放文件锁 ==================
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()
   #     logger.info("🔓 文件锁已释放")
    except Exception as e:
        logger.error(f"⚠️ 释放文件锁失败: {str(e)}")

    # ================== 6. 最终状态报告 ==================
 #   logger.info("🏁 程序运行结束\n" + "="*50 + "\n")

if __name__ == "__main__":
    create_table()
    asyncio.run(main())