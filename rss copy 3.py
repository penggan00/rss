import asyncio
import aiohttp
import logging
import re
import os
import json
import hashlib
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from feedparser import parse
from telegram import Bot
from telegram.error import BadRequest
from tencentcloud.common import credential
from tencentcloud.common.profile.client_profile import ClientProfile
from tencentcloud.common.profile.http_profile import HttpProfile
from tencentcloud.tmt.v20180321 import tmt_client, models
import pytz
import fcntl
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type, wait_random
import sqlite3
import time

# 加载.env文件
load_dotenv()

# 配置绝对路径
BASE_DIR = Path(__file__).resolve().parent

# 增强日志配置
logging.basicConfig(
    filename=BASE_DIR / "rss.log",
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    encoding="utf-8"
)
logger = logging.getLogger(__name__)

#RSS 源列表 (保持不变)
RSS_FEEDS = [
    'https://feeds.bbci.co.uk/news/world/rss.xml', # bbc
    'https://www3.nhk.or.jp/rss/news/cat6.xml',  # nhk
]
#主题
THIRD_RSS_FEEDS = [
    'https://36kr.com/feed-newsflash',
    'https://rsshub.215155.xyz/guancha',
    'https://rsshub.215155.xyz/zaobao/znews/china',
    'https://rsshub.215155.xyz/guancha/headline',
    
]
 # 主题
FOURTH_RSS_FEEDS = [
    'https://rsshub.215155.xyz/10jqka/realtimenews',
]

# 翻译主题+链接的
FIFTH_RSS_FEEDS = [
    'https://rsshub.app/twitter/media/elonmusk',  #elonmusk
    'https://www.youtube.com/feeds/videos.xml?channel_id=UCQeRaTukNYft1_6AZPACnog', # Asmongold TV

]
# 主题
FIFTH_RSS_RSS_SAN = [
    'https://rss.nodeseek.com/',  # nodeseek
]
# 10086
YOUTUBE_RSSS_FEEDS = [
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
]
# youtube
FIFTH_RSS_YOUTUBE = [
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
    'https://www.youtube.com/feeds/videos.xml?channel_id=UClyVC2wh_2fQhU0hPdXA4rw', # 热门古风
    'https://www.youtube.com/feeds/videos.xml?channel_id=UCHW6W9g2TJL2_Lf7GfoI5kg', # 电影放映厅
]

# Telegram配置 (保持不变)
TELEGRAM_BOT_TOKEN = os.getenv("RSS_TWO")  # 10086 bbc
RSS_TWO = os.getenv("RSS_TWO")
RSS_TOKEN = os.getenv("RSS_LINDA")    # RSS_LINDA
RSSTWO_TOKEN = os.getenv("RSS_TWO")
RSS_SANG = os.getenv("RSS_SAN")
YOUTUBE_RSS_FEEDSS = os.getenv("RSS_LINDA_YOUTUBE")
YOUTUBE_RSSS = os.getenv("YOUTUBE_RSS")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").split(",")
TENCENTCLOUD_SECRET_ID = os.getenv("TENCENTCLOUD_SECRET_ID")
TENCENTCLOUD_SECRET_KEY = os.getenv("TENCENTCLOUD_SECRET_KEY")

MAX_CONCURRENT_REQUESTS = 5
semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

# 定义时间间隔 (秒)
DEFAULT_INTERVAL = 3500  # 默认1小时
RSSSS_FEEDS_INTERVAL = DEFAULT_INTERVAL     # BBC
THIRD_RSS_FEEDS_INTERVAL = DEFAULT_INTERVAL     #36KR
FOURTH_RSS_FEEDS_INTERVAL = 1700  # 10jqka
FIFTH_RSS_FEEDS_INTERVAL = DEFAULT_INTERVAL    # Asmongold TV
FIFTH_RSS_RSS_SAN_INTERVAL = 1700  # nodeseek
YOUTUBE_RSSS_FEEDS_INTERVAL = DEFAULT_INTERVAL  # 10086 YOUTUBE
FIFTH_RSS_YOUTUBE_INTERVAL = 7300  # FIFTH_RSS_YOUTUBE，2 小时1800


# 创建锁文件
LOCK_FILE = BASE_DIR / "rss.lock"
# SQLite 数据库初始化
DATABASE_FILE = BASE_DIR / "rss_status.db"

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
    """创建 rss_status 和 timestamp 表"""
    conn = create_connection()
    if conn is not None:
        try:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS rss_status (
                    feed_url TEXT PRIMARY KEY,
                    identifier TEXT,
                    timestamp TEXT
                )
            """)
            # 添加 timestamp 表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS timestamps (
                    feed_group TEXT PRIMARY KEY,
                    last_run_time REAL
                )
            """)
            conn.commit()
            logger.info("成功创建/连接到数据库和表")
        except sqlite3.Error as e:
            logger.error(f"创建表失败: {e}")
        finally:
            conn.close()
    else:
        logger.error("无法创建数据库连接")

create_table()

def load_last_run_time_from_db(feed_group):
    """从数据库加载上次运行时间"""
    conn = create_connection()
    if conn is not None:
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT last_run_time FROM timestamps WHERE feed_group = ?", (feed_group,))
            result = cursor.fetchone()
            if result:
                return result[0]
            else:
                return 0  # 默认值为 0
        except sqlite3.Error as e:
            logger.error(f"从数据库加载上次运行时间失败: {e}")
            return 0
        finally:
            conn.close()
    else:
        logger.error("无法创建数据库连接")
        return 0

def save_last_run_time_to_db(feed_group, last_run_time):
    """将上次运行时间保存到数据库"""
    conn = create_connection()
    if conn is not None:
        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO timestamps (feed_group, last_run_time)
                VALUES (?, ?)
            """, (feed_group, last_run_time))
            conn.commit()
            logger.info(f"保存上次运行时间到数据库成功 (feed_group: {feed_group})")
        except sqlite3.Error as e:
            logger.error(f"保存上次运行时间到数据库失败: {e}")
        finally:
            conn.close()
    else:
        logger.error("无法创建数据库连接")

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
    """创建 rss_status 和 timestamp 表"""
    conn = create_connection()
    if conn is not None:
        try:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS rss_status (
                    feed_url TEXT PRIMARY KEY,
                    identifier TEXT,
                    timestamp TEXT
                )
            """)
            # 添加 timestamp 表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS timestamps (
                    feed_group TEXT PRIMARY KEY,
                    last_run_time REAL
                )
            """)
            conn.commit()
            logger.info("成功创建/连接到数据库和表")
        except sqlite3.Error as e:
            logger.error(f"创建表失败: {e}")
        finally:
            conn.close()
    else:
        logger.error("无法创建数据库连接")

create_table()


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

async def send_single_message(bot, chat_id, text, disable_web_page_preview=False):
    try:
        MAX_MESSAGE_LENGTH = 4096
        text_chunks = []
        current_chunk = []
        current_length = 0

        # 按换行符分割保持段落结构
        paragraphs = text.split('\n\n')
        for para in paragraphs:
            para_length = len(para.encode('utf-8'))
            if current_length + para_length + 2 > MAX_MESSAGE_LENGTH:  # +2 是换行符
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
                disable_web_page_preview=disable_web_page_preview
            )
    except BadRequest as e:
        logging.error(f"消息发送失败(Markdown错误): {e} - 文本片段: {chunk[:200]}...")
    except Exception as e:
        logging.error(f"消息发送失败: {e}")

# 自定义重试条件
def retry_if_transient_error(exception):
    """
    如果异常是瞬态错误（如连接错误、超时），则重试。
    不重试 4xx 错误。
    """
    if isinstance(exception, aiohttp.ClientError):
        return True
    if isinstance(exception, aiohttp.ClientResponseError) and 400 <= exception.status < 500:
        return False  # 不重试 4xx 错误
    return False

@retry(
    stop=stop_after_attempt(5),  # 增加重试次数
    wait=wait_exponential(multiplier=1, min=2, max=15) + wait_random(0, 2),  # 增加随机抖动
    retry=retry_if_exception_type(aiohttp.ClientError),  # 仅重试 ClientError
    before_sleep=lambda retry_state: logging.warning(f"重试中... (尝试次数: {retry_state.attempt_number})")
)
async def fetch_feed(session, feed_url):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/89.0.4389.82 Safari/537.36'}
    try:
        async with semaphore:
            async with session.get(feed_url, headers=headers, timeout=40) as response:
                response.raise_for_status()
                return parse(await response.read())
    except aiohttp.ClientResponseError as e:
        logging.error(f"HTTP 错误 {e.status} 抓取失败 {feed_url}: {e}")
        raise  # 重新抛出，让 tenacity 判断是否需要重试
    except Exception as e:
        logging.error(f"抓取失败 {feed_url}: {e}")
        raise  # 重新抛出，让 tenacity 判断是否需要重试

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
    """从 SQLite 加载状态"""
    status = {}
    conn = create_connection()
    if conn is not None:
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT feed_url, identifier, timestamp FROM rss_status")
            rows = cursor.fetchall()
            for row in rows:
                status[row[0]] = {'identifier': row[1], 'timestamp': row[2]}
            logger.info("从数据库加载状态成功")
        except sqlite3.Error as e:
            logger.error(f"从数据库加载状态失败: {e}")
        finally:
            conn.close()
    return status


async def save_single_status(feed_url, status_data):
    """保存单个feed状态到 SQLite"""
    conn = create_connection()
    if conn is not None:
        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO rss_status (feed_url, identifier, timestamp)
                VALUES (?, ?, ?)
            """, (feed_url, status_data['identifier'], status_data['timestamp']))
            conn.commit()
            logger.info(f"保存状态 {feed_url} 到数据库成功")
        except sqlite3.Error as e:
            logger.error(f"保存状态 {feed_url} 到数据库失败: {e}")
        finally:
            conn.close()

def get_entry_identifier(entry):
    """使用SHA256哈希生成稳定标识符"""
    identifier_str = ""
    for field in ['guid', 'link', 'id', 'title']:
        if hasattr(entry, field):
            identifier_str += str(getattr(entry, field))
    if not identifier_str:
        entry_time = get_entry_timestamp(entry).isoformat()
        identifier_str = f"{entry_time}-{hash(frozenset(entry.items()))}"
    return hashlib.sha256(identifier_str.encode()).hexdigest()

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

async def process_feed(session, feed_url, status, bot, translate=True):
    logger.info(f"开始处理源: {feed_url}")  # 在处理开始时记录状态
    logger.info(f"当前状态: {json.dumps(status.get(feed_url, {}), default=str)}")
    try:
        feed_data = await fetch_feed(session, feed_url)
        if not feed_data or not feed_data.entries:
            logger.info(f"源 {feed_url} 没有新条目")
            return ""

        # 状态跟踪增强
        last_status = status.get(feed_url, {})
        last_identifier = last_status.get('identifier')
        last_timestamp = last_status.get('timestamp')
        last_timestamp_dt = datetime.fromisoformat(last_timestamp).astimezone(pytz.utc) if last_timestamp else None

        logger.debug(f"上次记录标识符: {last_identifier}")
        logger.debug(f"上次记录时间: {last_timestamp}")

        new_entries = []
        current_latest = None

        # 修改条目处理顺序为正向时间顺序
        for entry in feed_data.entries:
            try:
                entry_time = get_entry_timestamp(entry)
                identifier = get_entry_identifier(entry)
                logger.debug(f"检查条目: {identifier[:50]}... 时间: {entry_time}")

                if last_identifier and identifier == last_identifier:
                    logger.info(f"找到精确匹配标识符，停止处理")
                    break

                if last_timestamp_dt and entry_time <= last_timestamp_dt:
                    logger.info(f"时间 {entry_time} <= 上次时间 {last_timestamp_dt}，停止处理")
                    break

                new_entries.append(entry)
                if not current_latest or entry_time > get_entry_timestamp(current_latest):
                    current_latest = entry
            except Exception as e:
                logger.error(f"处理条目失败: {str(e)}")
                continue

        if not new_entries:
            logger.info(f"没有新条目需要处理: {feed_url}")
            return ""

        # 处理消息
        merged_message = ""
        source_name = feed_data.feed.get('title', feed_url)
        # 遍历新条目，添加序号
        for idx, entry in enumerate(new_entries, start=1):
            # 原始内容处理
            raw_subject = remove_html_tags(entry.title or "无标题")
       #     raw_summary = remove_html_tags(getattr(entry, 'summary', "暂无简介"))
            raw_url = entry.link

            # 翻译处理
            if translate:
                translated_subject = await auto_translate_text(raw_subject)
          #     translated_summary = await auto_translate_text(raw_summary)
            else:
                translated_subject = raw_subject
          #     translated_summary = raw_summary

            # Markdown转义
            safe_subject = escape_markdown_v2(translated_subject)
      #      safe_summary = escape_markdown_v2(translated_summary)
            safe_source = escape_markdown_v2(source_name)
            safe_url = escape_markdown_v2(raw_url)

            # 构建消息
      #      message = f"*{safe_subject}*\n{safe_summary}\n[{safe_source}]({safe_url})"
            message = f"*{safe_subject}*\n[{safe_source}]({safe_url})"
            merged_message += message + "\n\n"
        merged_message += f"✅ 新增 {len(new_entries)} 条内容"

        # 更新状态:
        if current_latest:
            current_latest_identifier = get_entry_identifier(current_latest)
            current_latest_timestamp = get_entry_timestamp(current_latest).isoformat()

            status[feed_url] = {
                "identifier": current_latest_identifier,
                "timestamp": current_latest_timestamp
            }

            await save_single_status(feed_url, status[feed_url])  #  <----  添加这行代码
        return merged_message

    except Exception as e:
        logger.error(f"处理源 {feed_url} 时发生错误: {str(e)}")
        return ""

async def process_third_feed(session, feed_url, status, bot):
    logger.info(f"开始处理源: {feed_url}")  # 在处理开始时记录状态
    logger.info(f"当前状态: {json.dumps(status.get(feed_url, {}), default=str)}")
    try:
        feed_data = await fetch_feed(session, feed_url)
        if not feed_data or not feed_data.entries:
            logger.info(f"源 {feed_url} 没有新条目")
            return ""

        last_status = status.get(feed_url, {})
        last_identifier = last_status.get('identifier')
        last_timestamp = last_status.get('timestamp')
        last_timestamp_dt = datetime.fromisoformat(last_timestamp).astimezone(pytz.utc) if last_timestamp else None

        new_entries = []
        current_latest = None

        for entry in feed_data.entries:
            entry_time = get_entry_timestamp(entry)
            identifier = get_entry_identifier(entry)

            if last_identifier and identifier == last_identifier:
                logger.info(f"找到精确匹配标识符，停止处理")
                break

            if last_timestamp_dt and entry_time <= last_timestamp_dt:
                logger.info(f"时间 {entry_time} <= 上次时间 {last_timestamp_dt}，停止处理")
                break

            new_entries.append(entry)
            if not current_latest or entry_time > get_entry_timestamp(current_latest):
                current_latest = entry

        if not new_entries:
            logger.info(f"没有新条目需要处理: {feed_url}")
            return ""

        merged_message = ""
        source_name = feed_data.feed.get('title', feed_url)
        # 遍历新条目，添加序号
        for idx, entry in enumerate(new_entries, start=1):
            # 内容处理
            raw_subject = remove_html_tags(entry.title or "无标题")
         #   raw_summary = remove_html_tags(getattr(entry, 'summary', "暂无简介"))
            raw_url = entry.link

            # Markdown转义
            safe_subject = escape_markdown_v2(raw_subject)
      #      safe_summary = escape_markdown_v2(raw_summary)
            safe_source = escape_markdown_v2(source_name)
            safe_url = escape_markdown_v2(raw_url)

            # 消息构建
      #      message_content = f"*{safe_subject}*\n{safe_summary}\n[{safe_source}]({safe_url})"
            message_content = f"*{safe_subject}*\n[{safe_source}]({safe_url})"
            message_bytes = message_content.encode('utf-8')

            if len(message_bytes) <= 444:
                merged_message += message_content + "\n\n"
            else:
                title_link = f"*{safe_subject}*\n[{safe_source}]({safe_url})"
                merged_message += title_link + "\n\n"
        merged_message += f"✅ 新增 {len(new_entries)} 条内容"

        # 更新状态:
        if current_latest:
            current_latest_identifier = get_entry_identifier(current_latest)
            current_latest_timestamp = get_entry_timestamp(current_latest).isoformat()

            status[feed_url] = {
                "identifier": current_latest_identifier,
                "timestamp": current_latest_timestamp
            }
            await save_single_status(feed_url, status[feed_url])  #  <----  添加这行代码
        return merged_message
    except Exception as e:
        logger.error(f"处理源 {feed_url} 时发生错误: {str(e)}")
        return ""

async def process_fourth_feed(session, feed_url, status, bot):
    logger.info(f"开始处理源: {feed_url}")  # 在处理开始时记录状态
    logger.info(f"当前状态: {json.dumps(status.get(feed_url, {}), default=str)}")
    try:
        feed_data = await fetch_feed(session, feed_url)
        if not feed_data or not feed_data.entries:
            logger.info(f"源 {feed_url} 没有新条目")
            return ""

        last_status = status.get(feed_url, {})
        last_identifier = last_status.get('identifier')
        last_timestamp = last_status.get('timestamp')
        last_timestamp_dt = datetime.fromisoformat(last_timestamp).astimezone(pytz.utc) if last_timestamp else None

        new_entries = []
        current_latest = None

        for entry in feed_data.entries:
            entry_time = get_entry_timestamp(entry)
            identifier = get_entry_identifier(entry)

            if last_identifier and identifier == last_identifier:
                logger.info(f"找到精确匹配标识符，停止处理")
                break

            if last_timestamp_dt and entry_time <= last_timestamp_dt:
                logger.info(f"时间 {entry_time} <= 上次时间 {last_timestamp_dt}，停止处理")
                break

            new_entries.append(entry)
            if not current_latest or entry_time > get_entry_timestamp(current_latest):
                current_latest = entry

        if not new_entries:
            logger.info(f"没有新条目需要处理: {feed_url}")
            return ""

        merged_message = ""
        source_name = feed_data.feed.get('title', feed_url)
        # 遍历新条目，添加序号
        for idx, entry in enumerate(new_entries, start=1):
            # 内容处理
            raw_subject = remove_html_tags(entry.title or "无标题")
         #   raw_summary = remove_html_tags(getattr(entry, 'summary', "暂无简介"))
            raw_url = entry.link

            # Markdown转义
            safe_subject = escape_markdown_v2(raw_subject)
      #      safe_summary = escape_markdown_v2(raw_summary)
            safe_source = escape_markdown_v2(source_name)
            safe_url = escape_markdown_v2(raw_url)

            # 消息构建
      #      message_content = f"*{safe_subject}*\n{safe_summary}\n[{safe_source}]({safe_url})"
            message_content = f"*{safe_subject}*\n[{safe_source}]({safe_url})"
            message_bytes = message_content.encode('utf-8')

            if len(message_bytes) <= 444:
                merged_message += message_content + "\n\n"
            else:
                title_link = f"*{safe_subject}*\n[{safe_source}]({safe_url})"
                merged_message += title_link + "\n\n"
        merged_message += f"✅ 新增 {len(new_entries)} 条内容"

        # 更新状态:
        if current_latest:
            current_latest_identifier = get_entry_identifier(current_latest)
            current_latest_timestamp = get_entry_timestamp(current_latest).isoformat()

            status[feed_url] = {
                "identifier": current_latest_identifier,
                "timestamp": current_latest_timestamp
            }
            await save_single_status(feed_url, status[feed_url])  #  <----  添加这行代码
        return merged_message
    except Exception as e:
        logger.error(f"处理源 {feed_url} 时发生错误: {str(e)}")
        return ""

async def process_fifth_feed(session, feed_url, status, bot, translate=True):
    logger.info(f"开始处理源: {feed_url}")  # 在处理开始时记录状态
    logger.info(f"当前状态: {json.dumps(status.get(feed_url, {}), default=str)}")
    try:
        feed_data = await fetch_feed(session, feed_url)
        if not feed_data or not feed_data.entries:
            logger.info(f"源 {feed_url} 没有新条目")
            return ""

        # 状态处理
        last_status = status.get(feed_url, {})
        last_identifier = last_status.get('identifier')
        last_timestamp = last_status.get('timestamp')
        last_timestamp_dt = datetime.fromisoformat(last_timestamp).astimezone(pytz.utc) if last_timestamp else None

        new_entries = []
        current_latest = None

        for entry in feed_data.entries:
            entry_time = get_entry_timestamp(entry)
            identifier = get_entry_identifier(entry)

            if last_identifier and identifier == last_identifier:
                logger.info(f"找到精确匹配标识符，停止处理")
                break

            if last_timestamp_dt and entry_time <= last_timestamp_dt:
                logger.info(f"时间 {entry_time} <= 上次时间 {last_timestamp_dt}，停止处理")
                break

            new_entries.append(entry)
            if not current_latest or entry_time > get_entry_timestamp(current_latest):
                current_latest = entry

        if not new_entries:
            logger.info(f"没有新条目需要处理: {feed_url}")
            return ""

        # 处理消息
        merged_message = ""
        source_name = feed_data.feed.get('title', feed_url)
        feed_title = escape_markdown_v2(source_name)

        # 添加统计信息
        merged_message += f"📢 *{feed_title}*\n\n"
        # 遍历新条目，添加序号
        for idx, entry in enumerate(new_entries, start=1):
            # 原始内容处理
            raw_subject = remove_html_tags(entry.title or "无标题")
            raw_url = entry.link

            # 翻译处理
            if translate:
                translated_subject = await auto_translate_text(raw_subject)
            else:
                translated_subject = raw_subject
            # Markdown转义
            safe_subject = escape_markdown_v2(translated_subject)
        #    safe_source = escape_markdown_v2(source_name)
            safe_url = escape_markdown_v2(raw_url)

            # 构建消息, 只发送主题和链接
            message = f"*{safe_subject}*\n🔗 {safe_url}"
            merged_message += message + "\n\n"
        merged_message += f"✅ 新增 {len(new_entries)} 条内容"

        # 更新状态:
        if current_latest:
            current_latest_identifier = get_entry_identifier(current_latest)
            current_latest_timestamp = get_entry_timestamp(current_latest).isoformat()

            status[feed_url] = {
                "identifier": current_latest_identifier,
                "timestamp": current_latest_timestamp
            }
            await save_single_status(feed_url, status[feed_url])  #  <----  添加这行代码
        return merged_message
    except Exception as e:
        logger.error(f"处理源 {feed_url} 时发生错误: {str(e)}")
        return ""
    
async def process_san_feed(session, feed_url, status, bot):
    logger.info(f"开始处理源: {feed_url}")  # 在处理开始时记录状态
    logger.info(f"当前状态: {json.dumps(status.get(feed_url, {}), default=str)}")
    try:
        feed_data = await fetch_feed(session, feed_url)
        if not feed_data or not feed_data.entries:
            logger.info(f"源 {feed_url} 没有新条目")
            return ""

        last_status = status.get(feed_url, {})
        last_identifier = last_status.get('identifier')
        last_timestamp = last_status.get('timestamp')
        last_timestamp_dt = datetime.fromisoformat(last_timestamp).astimezone(pytz.utc) if last_timestamp else None

        new_entries = []
        current_latest = None

        for entry in feed_data.entries:
            entry_time = get_entry_timestamp(entry)
            identifier = get_entry_identifier(entry)

            if last_identifier and identifier == last_identifier:
                logger.info(f"找到精确匹配标识符，停止处理")
                break

            if last_timestamp_dt and entry_time <= last_timestamp_dt:
                logger.info(f"时间 {entry_time} <= 上次时间 {last_timestamp_dt}，停止处理")
                break

            new_entries.append(entry)
            if not current_latest or entry_time > get_entry_timestamp(current_latest):
                current_latest = entry

        if not new_entries:
            logger.info(f"没有新条目需要处理: {feed_url}")
            return ""

        merged_message = ""
        source_name = feed_data.feed.get('title', feed_url)
        # 遍历新条目，添加序号
        for idx, entry in enumerate(new_entries, start=1):
            # 内容处理
            raw_subject = remove_html_tags(entry.title or "无标题")
         #   raw_summary = remove_html_tags(getattr(entry, 'summary', "暂无简介"))
            raw_url = entry.link

            # Markdown转义
            safe_subject = escape_markdown_v2(raw_subject)
      #      safe_summary = escape_markdown_v2(raw_summary)
            safe_source = escape_markdown_v2(source_name)
            safe_url = escape_markdown_v2(raw_url)

            # 消息构建
      #      message_content = f"*{safe_subject}*\n{safe_summary}\n[{safe_source}]({safe_url})"
            message_content = f"*{safe_subject}*\n[{safe_source}]({safe_url})"
            message_bytes = message_content.encode('utf-8')

            if len(message_bytes) <= 444:
                merged_message += message_content + "\n\n"
            else:
                title_link = f"*{safe_subject}*\n[{safe_source}]({safe_url})"
                merged_message += title_link + "\n\n"
        merged_message += f"✅ 新增 {len(new_entries)} 条内容"

        # 更新状态:
        if current_latest:
            current_latest_identifier = get_entry_identifier(current_latest)
            current_latest_timestamp = get_entry_timestamp(current_latest).isoformat()

            status[feed_url] = {
                "identifier": current_latest_identifier,
                "timestamp": current_latest_timestamp
            }
            await save_single_status(feed_url, status[feed_url])  #  <----  添加这行代码
        return merged_message
    except Exception as e:
        logger.error(f"处理源 {feed_url} 时发生错误: {str(e)}")
        return ""
    
async def process_you_feed(session, feed_url, status, bot):
    logger.info(f"开始处理源: {feed_url}")  # 在处理开始时记录状态
    logger.info(f"当前状态: {json.dumps(status.get(feed_url, {}), default=str)}")
    try:
        feed_data = await fetch_feed(session, feed_url)
        if not feed_data or not feed_data.entries:
            logger.info(f"源 {feed_url} 没有新条目")
            return ""

        last_status = status.get(feed_url, {})
        last_identifier = last_status.get('identifier')
        last_timestamp = last_status.get('timestamp')
        last_timestamp_dt = datetime.fromisoformat(last_timestamp).astimezone(pytz.utc) if last_timestamp else None

        new_entries = []
        current_latest = None

        for entry in feed_data.entries:
            entry_time = get_entry_timestamp(entry)
            identifier = get_entry_identifier(entry)

            if last_identifier and identifier == last_identifier:
                logger.info(f"找到精确匹配标识符，停止处理")
                break

            if last_timestamp_dt and entry_time <= last_timestamp_dt:
                logger.info(f"时间 {entry_time} <= 上次时间 {last_timestamp_dt}，停止处理")
                break

            new_entries.append(entry)
            if not current_latest or entry_time > get_entry_timestamp(current_latest):
                current_latest = entry

        if not new_entries:
            logger.info(f"没有新条目需要处理: {feed_url}")
            return ""

        merged_message = ""
        source_name = feed_data.feed.get('title', feed_url)
        # 遍历新条目，添加序号
        for idx, entry in enumerate(new_entries, start=1):
            # 内容处理
            raw_subject = remove_html_tags(entry.title or "无标题")
         #   raw_summary = remove_html_tags(getattr(entry, 'summary', "暂无简介"))
            raw_url = entry.link

            # Markdown转义
            safe_subject = escape_markdown_v2(raw_subject)
      #      safe_summary = escape_markdown_v2(raw_summary)
            safe_source = escape_markdown_v2(source_name)
            safe_url = escape_markdown_v2(raw_url)

            # 消息构建
      #      message_content = f"*{safe_subject}*\n{safe_summary}\n[{safe_source}]({safe_url})"
            message_content = f"*{safe_subject}*\n[{safe_source}]({safe_url})"
            message_bytes = message_content.encode('utf-8')

            if len(message_bytes) <= 444:
                merged_message += message_content + "\n\n"
            else:
                title_link = f"*{safe_subject}*\n[{safe_source}]({safe_url})"
                merged_message += title_link + "\n\n"
    #    merged_message += f"✅ 新增 {len(new_entries)} 条内容"

        # 更新状态:
        if current_latest:
            current_latest_identifier = get_entry_identifier(current_latest)
            current_latest_timestamp = get_entry_timestamp(current_latest).isoformat()

            status[feed_url] = {
                "identifier": current_latest_identifier,
                "timestamp": current_latest_timestamp
            }
            await save_single_status(feed_url, status[feed_url])  #  <----  添加这行代码
        return merged_message
    except Exception as e:
        logger.error(f"处理源 {feed_url} 时发生错误: {str(e)}")
        return ""
    
async def process_youtube_feed(session, feed_url, status, bot):
    logger.info(f"开始处理源: {feed_url}")  # 在处理开始时记录状态
    logger.info(f"当前状态: {json.dumps(status.get(feed_url, {}), default=str)}")
    try:
        feed_data = await fetch_feed(session, feed_url)
        if not feed_data or not feed_data.entries:
            logger.info(f"源 {feed_url} 没有新条目")
            return ""

        last_status = status.get(feed_url, {})
        last_identifier = last_status.get('identifier')
        last_timestamp = last_status.get('timestamp')
        last_timestamp_dt = datetime.fromisoformat(last_timestamp).astimezone(pytz.utc) if last_timestamp else None

        new_entries = []
        current_latest = None

        for entry in feed_data.entries:
            entry_time = get_entry_timestamp(entry)
            identifier = get_entry_identifier(entry)

            if last_identifier and identifier == last_identifier:
                logger.info(f"找到精确匹配标识符，停止处理")
                break

            if last_timestamp_dt and entry_time <= last_timestamp_dt:
                logger.info(f"时间 {entry_time} <= 上次时间 {last_timestamp_dt}，停止处理")
                break

            new_entries.append(entry)
            if not current_latest or entry_time > get_entry_timestamp(current_latest):
                current_latest = entry

        if not new_entries:
            logger.info(f"没有新条目需要处理: {feed_url}")
            return ""

        merged_message = ""
        source_name = feed_data.feed.get('title', feed_url)
        feed_title = escape_markdown_v2(source_name)

        # 添加统计信息
        merged_message += f"📢 *{feed_title}*\n\n"

        # 遍历新条目，添加序号
        for idx, entry in enumerate(new_entries, start=1):
            # 内容处理
            raw_subject = remove_html_tags(entry.title or "无标题")
            raw_url = entry.link

            # Markdown转义
            safe_subject = escape_markdown_v2(raw_subject)
            safe_url = escape_markdown_v2(raw_url)

            # 构建消息，添加序号
            merged_message += f"*{safe_subject}*\n🔗 {safe_url}\n\n"
        merged_message += f"✅ 新增 {len(new_entries)} 条内容"

        # 更新状态:
        if current_latest:
            current_latest_identifier = get_entry_identifier(current_latest)
            current_latest_timestamp = get_entry_timestamp(current_latest).isoformat()

            status[feed_url] = {
                "identifier": current_latest_identifier,
                "timestamp": current_latest_timestamp
            }
            await save_single_status(feed_url, status[feed_url])  #  <----  添加这行代码
        return merged_message
    except Exception as e:
        logger.error(f"处理源 {feed_url} 时发生错误: {str(e)}")
        return ""

async def main():
    # 尝试获取锁
    try:
        lock_file = open(LOCK_FILE, "w")
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # Non-blocking lock
        logger.info("成功获取文件锁，程序开始运行...")
    except OSError:
        logger.warning("无法获取文件锁，另一个实例可能正在运行。程序退出。")
        return  # 直接退出

    async with aiohttp.ClientSession() as session:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        third_bot = Bot(token=RSS_TWO)
        fourth_bot = Bot(token=RSS_TOKEN)
        fifth_bot = Bot(token=RSSTWO_TOKEN)
        rsssan_bot = Bot(token=RSS_SANG)
        youtube_bot = Bot(token=YOUTUBE_RSSS)
        you_bot = Bot(token=YOUTUBE_RSS_FEEDSS)
        status = await load_status()  # 改为异步加载

        try:
            # 处理 RSS_FEEDS
            last_rss_feeds_run = load_last_run_time_from_db("RSS_FEEDS")
            now = time.time()
            if now - last_rss_feeds_run >= RSSSS_FEEDS_INTERVAL:
                logger.info("开始处理 RSS_FEEDS 源...")
                for idx, url in enumerate(RSS_FEEDS):
                    if message := await process_feed(session, url, status, bot):
                        await send_single_message(bot, TELEGRAM_CHAT_ID[0], message, True)
                        logger.info(f"成功处理 RSS_FEEDS 源 {idx + 1}/{len(RSS_FEEDS)}")
                save_last_run_time_to_db("RSS_FEEDS", time.time())
                logger.info("RSS_FEEDS 源处理完成。")
            else:
                logger.info(
                    f"距离上次运行 RSS_FEEDS 不足 {RSSSS_FEEDS_INTERVAL / 3600} 小时，跳过 RSS_FEEDS 处理。")

            # 处理 THIRD_RSS_FEEDS
            last_third_rss_feeds_run = load_last_run_time_from_db("THIRD_RSS_FEEDS")
            now = time.time()
            if now - last_third_rss_feeds_run >= THIRD_RSS_FEEDS_INTERVAL:
                logger.info("开始处理 THIRD_RSS_FEEDS 源...")
                for idx, url in enumerate(THIRD_RSS_FEEDS):
                    if message := await process_third_feed(session, url, status, third_bot):
                        await send_single_message(third_bot, TELEGRAM_CHAT_ID[0], message, True)
                        logger.info(
                            f"成功处理 THIRD_RSS_FEEDS 源 {idx + 1}/{len(THIRD_RSS_FEEDS)}")
                save_last_run_time_to_db("THIRD_RSS_FEEDS", time.time())
                logger.info("THIRD_RSS_FEEDS 源处理完成。")
            else:
                logger.info(f"距离上次运行 THIRD_RSS_FEEDS 不足 {THIRD_RSS_FEEDS_INTERVAL / 3600} 小时，跳过 THIRD_RSS_FEEDS 处理。")


            # 处理 FOURTH_RSS_FEEDS
            last_fourth_rss_feeds_run = load_last_run_time_from_db("FOURTH_RSS_FEEDS")
            now = time.time()
            if now - last_fourth_rss_feeds_run >= FOURTH_RSS_FEEDS_INTERVAL:
                logger.info("开始处理 FOURTH_RSS_FEEDS 源...")
                for idx, url in enumerate(FOURTH_RSS_FEEDS):
                    if message := await process_fourth_feed(session, url, status, fourth_bot):
                        await send_single_message(fourth_bot, TELEGRAM_CHAT_ID[0], message, True)
                        logger.info(
                            f"成功处理 FOURTH_RSS_FEEDS 源 {idx + 1}/{len(FOURTH_RSS_FEEDS)}")
                save_last_run_time_to_db("FOURTH_RSS_FEEDS", time.time())
                logger.info("FOURTH_RSS_FEEDS 源处理完成。")
            else:
                logger.info(f"距离上次运行 FOURTH_RSS_FEEDS 不足 {FOURTH_RSS_FEEDS_INTERVAL / 3600} 小时，跳过 FOURTH_RSS_FEEDS 处理。")

            # 处理 FIFTH_RSS_FEEDS
            last_fifth_rss_feeds_run = load_last_run_time_from_db("FIFTH_RSS_FEEDS")
            now = time.time()
            if now - last_fifth_rss_feeds_run >= FIFTH_RSS_FEEDS_INTERVAL:
                logger.info("开始处理 FIFTH_RSS_FEEDS 源...")
                for idx, url in enumerate(FIFTH_RSS_FEEDS):
                    if message := await process_fifth_feed(session, url, status, fifth_bot):
                        await send_single_message(fifth_bot, TELEGRAM_CHAT_ID[0], message, False)  # 根据需要调整True不浏览
                        logger.info(
                            f"成功处理 FIFTH_RSS_FEEDS 源 {idx + 1}/{len(FIFTH_RSS_FEEDS)}")
                save_last_run_time_to_db("FIFTH_RSS_FEEDS", time.time())
                logger.info("FIFTH_RSS_FEEDS 源处理完成。")
            else:
                logger.info(f"距离上次运行 FIFTH_RSS_FEEDS 不足 {FIFTH_RSS_FEEDS_INTERVAL / 3600} 小时，跳过 FIFTH_RSS_FEEDS 处理。")

            # 处理 FIFTH_RSS_RSS_SAN
            last_fifth_rss_rss_san_run = load_last_run_time_from_db("FIFTH_RSS_RSS_SAN")
            now = time.time()
            if now - last_fifth_rss_rss_san_run >= FIFTH_RSS_RSS_SAN_INTERVAL:
                logger.info("开始处理 FIFTH_RSS_RSS_SAN 源...")
                for idx, url in enumerate(FIFTH_RSS_RSS_SAN):
                    if message := await process_san_feed(session, url, status, rsssan_bot):
                        await send_single_message(rsssan_bot, TELEGRAM_CHAT_ID[0], message, True)  # 根据需要调整True不浏览
                        logger.info(
                            f"成功处理 FIFTH_RSS_RSS_SAN 源 {idx + 1}/{len(FIFTH_RSS_RSS_SAN)}")
                save_last_run_time_to_db("FIFTH_RSS_RSS_SAN", time.time())
                logger.info("FIFTH_RSS_RSS_SAN 源处理完成。")
            else:
                logger.info(f"距离上次运行 FIFTH_RSS_RSS_SAN 不足 {FIFTH_RSS_RSS_SAN_INTERVAL / 3600} 小时，跳过 FIFTH_RSS_RSS_SAN 处理。")

            # 处理 YOUTUBE_RSSS_FEEDS
            last_youtube_rsss_feeds_run = load_last_run_time_from_db("YOUTUBE_RSSS_FEEDS")
            now = time.time()
            if now - last_youtube_rsss_feeds_run >= YOUTUBE_RSSS_FEEDS_INTERVAL:
                logger.info("开始处理 YOUTUBE_RSSS_FEEDS 源...")
                for idx, url in enumerate(YOUTUBE_RSSS_FEEDS):
                    if message := await process_you_feed(session, url, status, you_bot):
                        await send_single_message(you_bot, TELEGRAM_CHAT_ID[0], message, False)  # 根据需要调整True不浏览
                        logger.info(
                            f"成功处理 YOUTUBE_RSSS_FEEDS 源 {idx + 1}/{len(YOUTUBE_RSSS_FEEDS)}")
                save_last_run_time_to_db("YOUTUBE_RSSS_FEEDS", time.time())
                logger.info("YOUTUBE_RSSS_FEEDS 源处理完成。")
            else:
                logger.info(f"距离上次运行 YOUTUBE_RSSS_FEEDS 不足 {YOUTUBE_RSSS_FEEDS_INTERVAL / 3600} 小时，跳过 YOUTUBE_RSSS_FEEDS 处理。")

            # 处理 FIFTH_RSS_YOUTUBE
            last_fifth_rss_youtube_run = load_last_run_time_from_db("FIFTH_RSS_YOUTUBE")
            now = time.time()
            if now - last_fifth_rss_youtube_run >= FIFTH_RSS_YOUTUBE_INTERVAL:
                logger.info("开始处理 FIFTH_RSS_YOUTUBE 源...")
                for idx, url in enumerate(FIFTH_RSS_YOUTUBE):
                    message = await process_youtube_feed(session, url, status, youtube_bot)
                    if message:  # 只有当 process_youtube_feed 返回消息时才发送
                        await send_single_message(youtube_bot, TELEGRAM_CHAT_ID[0], message, False)  # 根据需要调整True不浏览
                        logger.info(
                            f"成功处理 FIFTH_RSS_YOUTUBE 源 {idx + 1}/{len(FIFTH_RSS_YOUTUBE)}")
                    else:
                        logger.info(f"FIFTH_RSS_YOUTUBE 源 {idx + 1}/{len(FIFTH_RSS_YOUTUBE)} 没有新内容或处理失败")

                save_last_run_time_to_db("FIFTH_RSS_YOUTUBE", time.time())
                logger.info("FIFTH_RSS_YOUTUBE 源处理完成。")
            else:
                logger.info(f"距离上次运行 FIFTH_RSS_YOUTUBE 不足 {FIFTH_RSS_YOUTUBE_INTERVAL / 3600} 小时，跳过 FIFTH_RSS_YOUTUBE 处理。")

        except Exception as e:
            logger.critical(f"主循环发生致命错误: {str(e)}")
        finally:
            # 释放锁
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                lock_file.close()
                logger.info("释放文件锁，程序运行完成，状态已保存")
            except Exception as e:
                logger.error(f"释放文件锁失败: {e}")



if __name__ == "__main__":
    asyncio.run(main())
