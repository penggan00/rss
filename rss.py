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

# 加载.env文件
load_dotenv()

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

# 清理超过7天的日志文件
def clean_old_logs():
    log_file = BASE_DIR / "rss.log"
    if log_file.exists():
        log_modified_time = datetime.fromtimestamp(log_file.stat().st_mtime)
        if datetime.now() - log_modified_time > timedelta(days=2):
            try:
                log_file.unlink()
             #   logger.info("已清理超过7天的日志文件")
            except Exception as e:
                logger.error(f"清理日志文件失败: {e}")

# 在程序启动时执行日志清理
clean_old_logs()

TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").split(",")
TENCENTCLOUD_SECRET_ID = os.getenv("TENCENTCLOUD_SECRET_ID")
TENCENTCLOUD_SECRET_KEY = os.getenv("TENCENTCLOUD_SECRET_KEY")

# FIFTH_RSS_RSS_SAN添加：关键词列表和开关
KEYWORDS = os.getenv("KEYWORDS", "").split(",")  # 从环境变量读取关键词，用逗号分隔
KEYWORD_FILTER_ENABLED = os.getenv("KEYWORD_FILTER_ENABLED", "False").lower() == "true" # 从环境变量读取开关

MAX_CONCURRENT_REQUESTS = 2      #并发控制
semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

# 定义时间间隔 (秒)  600秒 = 10分钟    1200秒 = 20分钟   1800秒 = 30分钟  3600秒 = 1小时   7200秒 = 2小时   10800秒 = 3小时
RSS_GROUPS = [
    # ================== 国际新闻组 (原RSS_FEEDS) ==================
    {
        "name": "国际新闻",
        "urls": [
            'https://feeds.bbci.co.uk/news/world/rss.xml',  # BBC
            'https://www3.nhk.or.jp/rss/news/cat6.xml',     # NHK
            'https://www.cnbc.com/id/100003114/device/rss/rss.html',  # CNBC
            'https://feeds.a.dj.com/rss/RSSWorldNews.xml',  # 华尔街日报
            'https://www.aljazeera.com/xml/rss/all.xml',    # 半岛电视台
            'https://www.ft.com/?format=rss',                 # 金融时报
            'https://www3.nhk.or.jp/rss/news/cat5.xml',  # NHK 商业
            'http://rss.cnn.com/rss/cnn_topstories.rss',   # cnn
            'https://www.theguardian.com/world/rss',     # 卫报
            'https://www.theverge.com/rss/index.xml',   # The Verge:
        ],
        "group_key": "RSS_FEEDS",
        "interval": 3300,      # 55分钟 (原RSSSS_FEEDS_INTERVAL)
        "bot_token": os.getenv("RSS_TWO"),  # 原TELEGRAM_BOT_TOKEN
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
        "interval": 700,       # 11分钟 (原FOURTH_RSS_FEEDS_INTERVAL)
        "bot_token": os.getenv("RSS_LINDA"),  # 原RSS_RSSSSS
        "processor": {
            "translate": False,
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
            'https://rsshub.app/twitter/media/elonmusk',   # Elon Musk
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCQeRaTukNYft1_6AZPACnog',  # Asmongold
        ],
        "group_key": "FIFTH_RSS_FEEDS",
        "interval": 7000,      # 1小时56分钟 (原FIFTH_RSS_FEEDS_INTERVAL)
        "bot_token": os.getenv("YOUTUBE_RSS"),  # 原RSSTWO_TOKEN
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
        "interval": 240,       # 4分钟 (原FIFTH_RSS_RSS_SAN_INTERVAL)
        "bot_token": os.getenv("RSS_SAN"),
        "processor": {
            "translate": False,
            "template": "*{subject}*\n[{source}]({url})",
            "keyword_filter": True,         #过滤
            "preview": False,               # 预览
            "show_count": False               #计数
        }
    },

    # ================== YouTube频道组 (原YOUTUBE_RSSS_FEEDS) ==================
    {
        "name": "YouTube频道",
        "urls": [
       #     'https://www.youtube.com/feeds/videos.xml?channel_id=UCvijahEyGtvMpmMHBu4FS2w', # 零度解说
       #     'https://www.youtube.com/feeds/videos.xml?channel_id=UC96OvMh0Mb_3NmuE8Dpu7Gg', # 搞机零距离
       #     'https://www.youtube.com/feeds/videos.xml?channel_id=UCQoagx4VHBw3HkAyzvKEEBA', # 科技共享
       #     'https://www.youtube.com/feeds/videos.xml?channel_id=UCbCCUH8S3yhlm7__rhxR2QQ', # 不良林
       #     'https://www.youtube.com/feeds/videos.xml?channel_id=UCMtXiCoKFrc2ovAGc1eywDg', # 一休
       #     'https://www.youtube.com/feeds/videos.xml?channel_id=UCii04BCvYIdQvshrdNDAcww', # 悟空的日常
       #     'https://www.youtube.com/feeds/videos.xml?channel_id=UCJMEiNh1HvpopPU3n9vJsMQ', # 理科男士
       #     'https://www.youtube.com/feeds/videos.xml?channel_id=UCYjB6uufPeHSwuHs8wovLjg', # 中指通
       #     'https://www.youtube.com/feeds/videos.xml?channel_id=UCSs4A6HYKmHA2MG_0z-F0xw', # 李永乐老师
       #     'https://www.youtube.com/feeds/videos.xml?channel_id=UCZDgXi7VpKhBJxsPuZcBpgA', # 可恩KeEn
        #    'https://www.youtube.com/feeds/videos.xml?channel_id=UCxukdnZiXnTFvjF5B5dvJ5w', # 甬哥侃侃侃ygkkk
       #     'https://www.youtube.com/feeds/videos.xml?channel_id=UCUfT9BAofYBKUTiEVrgYGZw', # 科技分享
       #     'https://www.youtube.com/feeds/videos.xml?channel_id=UC51FT5EeNPiiQzatlA2RlRA', # 乌客wuke
        #    'https://www.youtube.com/feeds/videos.xml?channel_id=UCDD8WJ7Il3zWBgEYBUtc9xQ', # jack stone
        #    'https://www.youtube.com/feeds/videos.xml?channel_id=UCWurUlxgm7YJPPggDz9YJjw', # 一瓶奶油
        #    'https://www.youtube.com/feeds/videos.xml?channel_id=UCvENMyIFurJi_SrnbnbyiZw', # 酷友社
        #    'https://www.youtube.com/feeds/videos.xml?channel_id=UCmhbF9emhHa-oZPiBfcLFaQ', # WenWeekly
        #    'https://www.youtube.com/feeds/videos.xml?channel_id=UC3BNSKOaphlEoK4L7QTlpbA', # 中外观察
        #    'https://www.youtube.com/feeds/videos.xml?channel_id=UCXk0rwHPG9eGV8SaF2p8KUQ', # 烏鴉笑笑
                    # ... 其他YouTube频道（共18个）
        ],
        "group_key": "YOUTUBE_RSSS_FEEDS",
        "interval": 3300,      # 55分钟 (原YOUTUBE_RSSS_FEEDS_INTERVAL)
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
          #  'https://blog.090227.xyz/atom.xml',
          #  'https://www.freedidi.com/feed',
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
        "interval": 10400,     # 2小时53分钟 (原FIFTH_RSS_YOUTUBE_INTERVAL)
        "bot_token": os.getenv("YOUTUBE_RSS"),
        "processor": {
        "translate": False,
        "header_template": "📢 *{source}*\n",  # 新增标题模板 ★
        "template": "*{subject}*\n🔗 {url}",  # 条目模板
        "preview": True,
        "show_count": False
    }
    },

    # ================== 中文媒体组 (原THIRD_RSS_FEEDS) ==================
    {
        "name": "中文媒体", 
        "urls": [
            'https://rsshub.app/guancha',
            'https://rsshub.app/zaobao/znews/china',
            'https://rsshub.app/guancha/headline',
        ],
        "group_key": "THIRD_RSS_FEEDS",
        "interval": 7000,      # 1小时56分钟 (原THIRD_RSS_FEEDS_INTERVAL)
        "bot_token": os.getenv("RSS_LINDA_YOUTUBE"),  # 原RSS_STWO
        "processor": {
            "translate": False,
            "template": "*{subject}*\n[{source}]({url})",
            "preview": False,
            "show_count": False
        }
    }
]

# 新增通用处理函数
async def process_group(session, group_config, global_status):
    """统一处理RSS组"""
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
        #    remaining = group_config["interval"] - (now - last_run)
        #    logger.info(f"⏳ 跳过 [{group_name}] 还需等待 {remaining:.0f}秒")
            return

   #     logger.info(f"🚀 开始处理 [{group_name}] 源...")
        bot = Bot(token=bot_token)
        all_messages = []

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

                # ------ 2.2 加载处理状态 ------
                processed_ids = global_status.get(feed_url, set())
                new_entries = []

                # ------ 2.3 处理每个条目 ------
                for entry in feed_data.entries:
                    entry_id = get_entry_identifier(entry)
                    if entry_id in processed_ids:
                        continue

                    # 关键词过滤
                    if processor.get("keyword_filter", False) and KEYWORD_FILTER_ENABLED:
                        raw_title = remove_html_tags(entry.title or "")
                        if not any(kw.lower() in raw_title.lower() for kw in KEYWORDS):
                            continue

                    new_entries.append(entry)
                    await save_single_status(group_key, feed_url, entry_id)
                    processed_ids.add(entry_id)

                global_status[feed_url] = processed_ids  # 更新内存状态

                # ========== 2.4 生成消息内容 ==========
                if new_entries:
                    await asyncio.sleep(1)  # 发送前延迟1秒
                    feed_message = await generate_group_message(feed_data, new_entries, processor)
                    if feed_message:  # 新增：立即发送当前源的消息
                        await send_single_message(
                            bot,
                            TELEGRAM_CHAT_ID[0],
                            feed_message,
                            disable_web_page_preview=not processor.get("preview", True)
                        )
              #          logger.info(f"📤 已发送 {len(new_entries)} 条内容 [{feed_url}]")

            except Exception as e:
                logger.error(f"❌ 处理源失败 [{feed_url}]: {str(e)}", exc_info=True)

        # ========== 3. 保存最后运行时间 ==========
        await save_last_run_time_to_db(group_key, now)
        # ========== 4. 最终延迟 ==========
        await asyncio.sleep(1)  # 组处理完成后延迟3秒

    except Exception as e:
        logger.critical(f"‼️ 处理组失败 [{group_name}]: {str(e)}", exc_info=True)
 #   finally:
     #   logger.info(f"🏁 完成处理 [{group_name}]")

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
            conn.commit()
       #     logger.info("成功创建/连接到本地 SQLite 数据库和表")
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
                if response.status in (503, 403):
                    logger.warning(f"RSS源暂时不可用（{response.status}）: {feed_url}")
                    return None  # 跳过当前源，下次运行会重试
                response.raise_for_status()
                return parse(await response.read())
    except aiohttp.ClientResponseError as e:
        if e.status in (503, 403):
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
    """仅从SQLite加载状态"""
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
          #  logger.info("本地状态加载成功")
        except sqlite3.Error as e:
            logger.error(f"本地状态加载失败: {e}")
        finally:
            conn.close()
    return status

async def save_single_status(feed_group, feed_url, entry_url):
    """仅保存到SQLite，使用事务和重试"""
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

async def clean_old_entries(feed_group, max_age_days=30):
    """仅清理SQLite旧记录"""
    cutoff_time = time.time() - max_age_days * 24 * 3600
    conn = create_connection()
    if conn:
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM rss_status WHERE feed_group = ? AND entry_timestamp < ?", 
                         (feed_group, cutoff_time))
            conn.commit()
          #  logger.info(f"本地记录清理完成: {feed_group}")
        except sqlite3.Error as e:
            logger.error(f"本地清理失败: {e}")
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
    try:
        create_table()
 #       logger.info("💾 数据库初始化完成")
    except Exception as e:
        logger.critical(f"‼️ 数据库初始化失败: {str(e)}")
        return

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
                "YOUTUBE_RSSS_FEEDS": 30,
                "FIFTH_RSS_YOUTUBE": 30
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
    # 确保先创建新表结构
    create_table()
    asyncio.run(main())