import asyncio
import aiohttp
import logging
import re
import os
import hashlib
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
import pytz
import fcntl
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type, wait_random
import sqlite3
import time
from urllib.parse import urlparse  # 添加在文件开头的导入部分
try:
    from supabase import create_client, Client
except ImportError:
    pass
from tenacity import retry, stop_after_attempt, wait_fixed

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
        if datetime.now() - log_modified_time > timedelta(days=3):
            try:
                log_file.unlink()
         #       logger.info("已清理超过7天的日志文件")
            except Exception as e:
                logger.error(f"清理日志文件失败: {e}")

# 在程序启动时执行日志清理
clean_old_logs()

RSS_FEEDS = [
    'https://feeds.bbci.co.uk/news/world/rss.xml', # bbc
    'https://www3.nhk.or.jp/rss/news/cat6.xml',  # nhk
    'https://www.cnbc.com/id/100003114/device/rss/rss.html', # CNBC
    'https://feeds.a.dj.com/rss/RSSWorldNews.xml', # 华尔街日报
    'https://www.aljazeera.com/xml/rss/all.xml',# 半岛电视台
  #  'https://www3.nhk.or.jp/rss/news/cat5.xml',# NHK 商业
    'https://www.ft.com/?format=rss', # 金融时报
  #  'http://rss.cnn.com/rss/edition.rss', # cnn
]
#主题
THIRD_RSS_FEEDS = [
    'https://rsshub.app/guancha',
    'https://rsshub.app/zaobao/znews/china',
    'https://rsshub.app/guancha/headline',
    
]
 # 主题
FOURTH_RSS_FEEDS = [
 #   'https://rsshub.app/10jqka/realtimenews',
     'https://36kr.com/feed-newsflash',
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
RSS_STWO = os.getenv("RSS_LINDA_YOUTUBE")   
RSS_RSSSSS = os.getenv("RSS_LINDA")    # RSS_LINDA
RSSTWO_TOKEN = os.getenv("YOUTUBE_RSS")
RSS_SANG = os.getenv("RSS_SAN")
YOUTUBE_RSS_FEEDSS = os.getenv("RSS_TOKEN")
YOUTUBE_RSSSS = os.getenv("YOUTUBE_RSS")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").split(",")
TENCENTCLOUD_SECRET_ID = os.getenv("TENCENTCLOUD_SECRET_ID")
TENCENTCLOUD_SECRET_KEY = os.getenv("TENCENTCLOUD_SECRET_KEY")

# FIFTH_RSS_RSS_SAN添加：关键词列表和开关
KEYWORDS = os.getenv("KEYWORDS", "").split(",")  # 从环境变量读取关键词，用逗号分隔
KEYWORD_FILTER_ENABLED = os.getenv("KEYWORD_FILTER_ENABLED", "False").lower() == "true" # 从环境变量读取开关

MAX_CONCURRENT_REQUESTS = 5
semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

# 定义时间间隔 (秒)  600秒 = 10分钟    1200秒 = 20分钟   1800秒 = 30分钟  3600秒 = 1小时   7200秒 = 2小时   10800秒 = 3小时
DEFAULT_INTERVAL = 3300  # 默认1小时
RSSSS_FEEDS_INTERVAL = DEFAULT_INTERVAL     # BBC   
THIRD_RSS_FEEDS_INTERVAL = 7000   # zaobao
FOURTH_RSS_FEEDS_INTERVAL = 700   #36KR
FIFTH_RSS_FEEDS_INTERVAL = 7000    # Asmongold TV
FIFTH_RSS_RSS_SAN_INTERVAL = 400   # nodeseek
YOUTUBE_RSSS_FEEDS_INTERVAL = DEFAULT_INTERVAL  # 10086 YOUTUBE
FIFTH_RSS_YOUTUBE_INTERVAL = 10400  # FIFTH_RSS_YOUTUBE，2 小时1800

# Supabase初始化
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
USE_SUPABASE = SUPABASE_URL and SUPABASE_KEY


if USE_SUPABASE:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
 #   logger.info("Supabase客户端已初始化")
else:
    logger.info("未找到Supabase配置，将使用本地SQLite")


def create_connection():
    """禁用所有SQLite连接"""
    return None

def create_table():
    """禁用本地表创建"""
    pass  # 空实现

async def load_last_run_time_from_db(feed_group):
    """仅使用Supabase"""
    if USE_SUPABASE:
        try:
            response = supabase.table('timestamps')\
                .select('last_run_time')\
                .eq('feed_group', feed_group)\
                .execute()
            return response.data[0]['last_run_time'] if response.data else 0
        except Exception as e:
            logger.error(f"从Supabase加载时间失败: {e}")
            return 0
    else:
        logger.error("SQLite已被禁用，请配置Supabase或恢复相关代码")
        return 0

async def save_last_run_time_to_db(feed_group, last_run_time):
    """仅使用Supabase"""
    if USE_SUPABASE:
        try:
            supabase.table('timestamps').upsert({
                'feed_group': feed_group,
                'last_run_time': last_run_time
            }).execute()
        except Exception as e:
            logger.error(f"Supabase时间戳保存失败: {e}")
    else:
        logger.error("SQLite已被禁用，请配置Supabase或恢复相关代码")

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

@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=2, max=15) + wait_random(0, 2),
    retry=retry_if_exception_type(aiohttp.ClientError)
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
    """使用Supabase分页查询获取全部记录"""
    status = {}
    if USE_SUPABASE:
        try:
            all_data = []
            start = 0
            page_size = 1000  # 每页获取1000条

            while True:
                # 使用range进行分页 (闭区间)
                response = supabase.table('rss_status')\
                    .select('feed_url, entry_url')\
                    .range(start, start + page_size - 1)\
                    .execute()
                
                current_page = response.data
                if not current_page:
                    break
                
                all_data.extend(current_page)
                start += page_size

                # 如果当前页不足page_size说明是最后一页
                if len(current_page) < page_size:
                    break

            # 构建状态字典
            for item in all_data:
                if item['feed_url'] not in status:
                    status[item['feed_url']] = set()
                status[item['feed_url']].add(item['entry_url'])
            
            logger.info(f"从Supabase加载了 {len(all_data)} 条状态记录")
            return status
        except Exception as e:
            logger.error(f"从Supabase加载状态失败: {e}")
            return {}
    else:
        logger.error("SQLite已被禁用，请配置Supabase或恢复相关代码")
        return {}

@retry(stop=stop_after_attempt(3), wait=wait_fixed(1))
async def save_single_status(feed_group, feed_url, entry_url):
    """仅使用Supabase"""
    timestamp = time.time()
    if USE_SUPABASE:
        try:
            response = supabase.table('rss_status').upsert({
                'feed_group': feed_group,
                'feed_url': feed_url,
                'entry_url': entry_url,
                'entry_timestamp': timestamp
            }).execute()
            if len(response.data) == 0:
                raise Exception("Supabase upsert 未返回数据")
        except Exception as e:
            logger.error(f"Supabase 写入失败: {str(e)}")
            raise
    else:
        logger.error("SQLite已被禁用，请配置Supabase或恢复相关代码")

async def clean_old_entries(feed_group, max_age_days=30):
    """安全清理旧记录的终极方案"""
    if not USE_SUPABASE:
        return

    cutoff = time.time() - max_age_days * 86400
    try:
        # 分阶段删除：按时间片逐步清理
        time_step = 3 * 86400  # 每次删除3天的数据
        current_cutoff = cutoff - time_step

        while current_cutoff > 0:  # 保护机制：防止删除全部数据
            # 直接使用Supabase的delete with filter
            response = supabase.table('rss_status')\
                .delete()\
                .eq('feed_group', feed_group)\
                .lt('entry_timestamp', current_cutoff)\
                .execute()

            deleted_count = len(response.data)
            if deleted_count == 0:
                break

            current_cutoff -= time_step

            # 安全间隔
            await asyncio.sleep(1)

    except Exception as e:
        logger.error(f"Supabase清理失败: {e}")
        # 关键错误时通知管理员
        await bot.send_message(
            chat_id=ADMIN_CHAT_ID, 
            text=f"‼️ 数据库清理失败: {str(e)[:200]}..."
        )

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
    
async def process_feed(session, feed_url, status, bot, translate=True):
        result = await process_feed_common(session, "RSS_FEEDS", feed_url, status)
        if not result:
            return ""
        feed_data, new_entries = result
    # 新增：检查 new_entries 是否为空
        if not new_entries:  # 空列表直接返回
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

        return merged_message

async def process_third_feed(session, feed_url, status, bot):
        result = await process_feed_common(session, "THIRD_RSS_FEEDS", feed_url, status)
        if not result:
            return ""
    
        feed_data, new_entries = result
    # 新增：检查 new_entries 是否为空
        if not new_entries:  # 空列表直接返回
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
          #  message_bytes = message_content.encode('utf-8')

           # if len(message_bytes) <= 111:
            merged_message += message_content + "\n\n"
           # else:
            #    title_link = f"*{safe_subject}*\n[{safe_source}]({safe_url})"
            #    merged_message += title_link + "\n\n"
        merged_message += f"✅ 新增 {len(new_entries)} 条内容"

        return merged_message

async def process_fourth_feed(session, feed_url, status, bot):
        result = await process_feed_common(session, "FOURTH_RSS_FEEDS", feed_url, status)
        if not result:
            return ""
    
        feed_data, new_entries = result
    # 新增：检查 new_entries 是否为空
        if not new_entries:  # 空列表直接返回
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
          #  message_bytes = message_content.encode('utf-8')

           # if len(message_bytes) <= 111:
            merged_message += message_content + "\n\n"
           # else:
           #     title_link = f"*{safe_subject}*\n[{safe_source}]({safe_url})"
            #    merged_message += title_link + "\n\n"
        merged_message += f"✅ 新增 {len(new_entries)} 条内容"

        return merged_message

async def process_fifth_feed(session, feed_url, status, bot, translate=True):
        result = await process_feed_common(session, "FIFTH_RSS_FEEDS", feed_url, status)
        if not result:
            return ""
    
        feed_data, new_entries = result
    # 新增：检查 new_entries 是否为空
        if not new_entries:  # 空列表直接返回
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

        return merged_message
    
async def process_san_feed(session, feed_url, status, bot):
        result = await process_feed_common(session, "FIFTH_RSS_RSS_SAN", feed_url, status)
        if not result:
            return ""
    
        feed_data, new_entries = result
    # 新增：检查 new_entries 是否为空
        if not new_entries:  # 空列表直接返回
            return ""
        
        # 关键词过滤
        if KEYWORD_FILTER_ENABLED:
            filtered_entries = []
            for entry in new_entries:
                raw_subject = remove_html_tags(entry.title or "无标题")
                for keyword in KEYWORDS:
                    if keyword.lower() in raw_subject.lower():
                        filtered_entries.append(entry)
                        break
            new_entries = filtered_entries  # Use filtered entries from now on

            if not new_entries:
                logger.info(f"关键词过滤后没有新条目需要处理: {feed_url}")
                return ""

        merged_message = ""
        source_name = feed_data.feed.get('title', feed_url)
        # 遍历新条目，添加序号
        for idx, entry in enumerate(new_entries, start=1):
            # 内容处理
            raw_subject = remove_html_tags(entry.title or "无标题")
            # raw_summary = remove_html_tags(getattr(entry, 'summary', "暂无简介"))
            raw_url = entry.link

            # Markdown转义
            safe_subject = escape_markdown_v2(raw_subject)
            # safe_summary = escape_markdown_v2(raw_summary)
            safe_source = escape_markdown_v2(source_name)
            safe_url = escape_markdown_v2(raw_url)

            # 消息构建
            # message_content = f"*{safe_subject}*\n{safe_summary}\n[{safe_source}]({safe_url})"
            message_content = f"*{safe_subject}*\n[{safe_source}]({safe_url})"
           # message_bytes = message_content.encode('utf-8')

           # if len(message_bytes) <= 111:
            merged_message += message_content + "\n\n"
           # else:
           #     title_link = f"*{safe_subject}*\n[{safe_source}]({safe_url})"
            #    merged_message += title_link + "\n\n"
        merged_message += f"✅ 新增 {len(new_entries)} 条内容"

        return merged_message
    
async def process_you_feed(session, feed_url, status, bot):
        result = await process_feed_common(session, "YOUTUBE_RSSS_FEEDS", feed_url, status)
        if not result:
            return ""
    
        feed_data, new_entries = result
    # 新增：检查 new_entries 是否为空
        if not new_entries:  # 空列表直接返回
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
          #  message_bytes = message_content.encode('utf-8')

           # if len(message_bytes) <= 111:
            merged_message += message_content + "\n\n"
           # else:
            #    title_link = f"*{safe_subject}*\n[{safe_source}]({safe_url})"
            #    merged_message += title_link + "\n\n"
        merged_message += f"✅ 新增 {len(new_entries)} 条内容"

        return merged_message

async def process_youtube_feed(session, feed_url, status, bot):
        result = await process_feed_common(session, "FIFTH_RSS_YOUTUBE", feed_url, status)
        if not result:
            return ""
    
        feed_data, new_entries = result
    # 新增：检查 new_entries 是否为空
        if not new_entries:  # 空列表直接返回
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

        return merged_message


async def main():
    # 尝试获取文件锁（防止多实例同时运行）
    try:
        lock_file = open(LOCK_FILE, "w")  # 打开/创建锁文件
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # 请求排他非阻塞锁
        logger.info("成功获取文件锁，程序开始运行...")
    except OSError:
        logger.warning("无法获取文件锁，另一个实例可能正在运行。程序退出。")
        return  # 如果获取锁失败，直接退出程序

    # ↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓ 这里开始主逻辑 ↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓
    async with aiohttp.ClientSession() as session:
        # 初始化各个bot
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        third_bot = Bot(token=RSS_STWO)
        fourth_bot = Bot(token=RSS_RSSSSS)
        fifth_bot = Bot(token=RSSTWO_TOKEN)
        rsssan_bot = Bot(token=RSS_SANG)
        youtube_bot = Bot(token=YOUTUBE_RSSSS)
        you_bot = Bot(token=YOUTUBE_RSS_FEEDSS)
        status = await load_status()
        
        try:
            # 定义每个feed组的保留天数配置
            FEED_GROUP_RETENTION = {
                "RSS_FEEDS": 30,          # 国际新闻保留30天
                "THIRD_RSS_FEEDS": 30,    # 中文媒体保留30天
                "FOURTH_RSS_FEEDS": 7,    # 快讯只保留7天
                "FIFTH_RSS_FEEDS": 30,    # 社交媒体保留30天
                "FIFTH_RSS_RSS_SAN": 7,   # 技术论坛保留7天
                "YOUTUBE_RSSS_FEEDS": 600,  # YouTube频道保留30天
                "FIFTH_RSS_YOUTUBE": 600    # 中文YouTube保留30天
            }

            # 清理旧记录
            for feed_group, max_age_days in FEED_GROUP_RETENTION.items():
                await clean_old_entries(feed_group, max_age_days)

            # =============== RSS_FEEDS 处理 ===============
            last_rss_feeds_run = await load_last_run_time_from_db("RSS_FEEDS")
            now = time.time()
            
            # 类型安全检查
            if last_rss_feeds_run is None or not isinstance(last_rss_feeds_run, (int, float)):
                logger.warning("RSS_FEEDS 时间戳无效，重置为0")
                last_rss_feeds_run = 0.0

            if (now - last_rss_feeds_run) >= RSSSS_FEEDS_INTERVAL:
            #    logger.info("开始处理 RSS_FEEDS 源...")
                for idx, url in enumerate(RSS_FEEDS):
                    if message := await process_feed(session, url, status, bot):
                        await send_single_message(bot, TELEGRAM_CHAT_ID[0], message, True)
                #        logger.info(f"成功处理 RSS_FEEDS 源 {idx + 1}/{len(RSS_FEEDS)}")
                await save_last_run_time_to_db("RSS_FEEDS", now)
           #     logger.info("RSS_FEEDS 源处理完成")
            else:
                remaining = RSSSS_FEEDS_INTERVAL - (now - last_rss_feeds_run)
                logger.info(f"跳过 RSS_FEEDS，还需等待 {remaining:.1f} 秒")

            # =============== THIRD_RSS_FEEDS 处理 ===============
            last_third_run = await load_last_run_time_from_db("THIRD_RSS_FEEDS")
            now = time.time()
            
            if last_third_run is None or not isinstance(last_third_run, (int, float)):
                logger.warning("THIRD_RSS_FEEDS 时间戳无效，重置为0")
                last_third_run = 0.0

            if (now - last_third_run) >= THIRD_RSS_FEEDS_INTERVAL:
             #   logger.info("开始处理 THIRD_RSS_FEEDS 源...")
                for idx, url in enumerate(THIRD_RSS_FEEDS):
                    if message := await process_third_feed(session, url, status, third_bot):
                        await send_single_message(third_bot, TELEGRAM_CHAT_ID[0], message, True)
                 #       logger.info(f"成功处理 THIRD_RSS_FEEDS 源 {idx + 1}/{len(THIRD_RSS_FEEDS)}")
                await save_last_run_time_to_db("THIRD_RSS_FEEDS", now)
            #    logger.info("THIRD_RSS_FEEDS 源处理完成")
            else:
                remaining = THIRD_RSS_FEEDS_INTERVAL - (now - last_third_run)
                logger.info(f"跳过 THIRD_RSS_FEEDS，还需等待 {remaining:.1f} 秒")

            # =============== FOURTH_RSS_FEEDS 处理 ===============
            last_fourth_run = await load_last_run_time_from_db("FOURTH_RSS_FEEDS")
            now = time.time()
            
            if last_fourth_run is None or not isinstance(last_fourth_run, (int, float)):
                logger.warning("FOURTH_RSS_FEEDS 时间戳无效，重置为0")
                last_fourth_run = 0.0

            if (now - last_fourth_run) >= FOURTH_RSS_FEEDS_INTERVAL:
          #      logger.info("开始处理 FOURTH_RSS_FEEDS 源...")
                for idx, url in enumerate(FOURTH_RSS_FEEDS):
                    if message := await process_fourth_feed(session, url, status, fourth_bot):
                        await send_single_message(fourth_bot, TELEGRAM_CHAT_ID[0], message, True)
               #         logger.info(f"成功处理 FOURTH_RSS_FEEDS 源 {idx + 1}/{len(FOURTH_RSS_FEEDS)}")
                await save_last_run_time_to_db("FOURTH_RSS_FEEDS", now)
          #      logger.info("FOURTH_RSS_FEEDS 源处理完成")
            else:
                remaining = FOURTH_RSS_FEEDS_INTERVAL - (now - last_fourth_run)
                logger.info(f"跳过 FOURTH_RSS_FEEDS，还需等待 {remaining:.1f} 秒")

            # =============== FIFTH_RSS_FEEDS 处理 ===============
            last_fifth_run = await load_last_run_time_from_db("FIFTH_RSS_FEEDS")
            now = time.time()
            
            if last_fifth_run is None or not isinstance(last_fifth_run, (int, float)):
                logger.warning("FIFTH_RSS_FEEDS 时间戳无效，重置为0")
                last_fifth_run = 0.0

            if (now - last_fifth_run) >= FIFTH_RSS_FEEDS_INTERVAL:
          #      logger.info("开始处理 FIFTH_RSS_FEEDS 源...")
                for idx, url in enumerate(FIFTH_RSS_FEEDS):
                    if message := await process_fifth_feed(session, url, status, fifth_bot):
                        await send_single_message(fifth_bot, TELEGRAM_CHAT_ID[0], message, False)
                 #       logger.info(f"成功处理 FIFTH_RSS_FEEDS 源 {idx + 1}/{len(FIFTH_RSS_FEEDS)}")
                await save_last_run_time_to_db("FIFTH_RSS_FEEDS", now)
        #        logger.info("FIFTH_RSS_FEEDS 源处理完成")
            else:
                remaining = FIFTH_RSS_FEEDS_INTERVAL - (now - last_fifth_run)
                logger.info(f"跳过 FIFTH_RSS_FEEDS，还需等待 {remaining:.1f} 秒")

            # =============== FIFTH_RSS_RSS_SAN 处理 ===============
            last_san_run = await load_last_run_time_from_db("FIFTH_RSS_RSS_SAN")
            now = time.time()
            
            if last_san_run is None or not isinstance(last_san_run, (int, float)):
                logger.warning("FIFTH_RSS_RSS_SAN 时间戳无效，重置为0")
                last_san_run = 0.0

            if (now - last_san_run) >= FIFTH_RSS_RSS_SAN_INTERVAL:
           #     logger.info("开始处理 FIFTH_RSS_RSS_SAN 源...")
                for idx, url in enumerate(FIFTH_RSS_RSS_SAN):
                    if message := await process_san_feed(session, url, status, rsssan_bot):
                        await send_single_message(rsssan_bot, TELEGRAM_CHAT_ID[0], message, True)
                  #      logger.info(f"成功处理 FIFTH_RSS_RSS_SAN 源 {idx + 1}/{len(FIFTH_RSS_RSS_SAN)}")
                await save_last_run_time_to_db("FIFTH_RSS_RSS_SAN", now)
          #      logger.info("FIFTH_RSS_RSS_SAN 源处理完成")
            else:
                remaining = FIFTH_RSS_RSS_SAN_INTERVAL - (now - last_san_run)
                logger.info(f"跳过 FIFTH_RSS_RSS_SAN，还需等待 {remaining:.1f} 秒")

            # =============== YOUTUBE_RSSS_FEEDS 处理 ===============
            last_youtube_run = await load_last_run_time_from_db("YOUTUBE_RSSS_FEEDS")
            now = time.time()
            
            if last_youtube_run is None or not isinstance(last_youtube_run, (int, float)):
                logger.warning("YOUTUBE_RSSS_FEEDS 时间戳无效，重置为0")
                last_youtube_run = 0.0

            if (now - last_youtube_run) >= YOUTUBE_RSSS_FEEDS_INTERVAL:
        #       logger.info("开始处理 YOUTUBE_RSSS_FEEDS 源...")
                for idx, url in enumerate(YOUTUBE_RSSS_FEEDS):
                    if message := await process_you_feed(session, url, status, you_bot):
                        await send_single_message(you_bot, TELEGRAM_CHAT_ID[0], message, False)
               #         logger.info(f"成功处理 YOUTUBE_RSSS_FEEDS 源 {idx + 1}/{len(YOUTUBE_RSSS_FEEDS)}")
                await save_last_run_time_to_db("YOUTUBE_RSSS_FEEDS", now)
         #       logger.info("YOUTUBE_RSSS_FEEDS 源处理完成")
            else:
                remaining = YOUTUBE_RSSS_FEEDS_INTERVAL - (now - last_youtube_run)
                logger.info(f"跳过 YOUTUBE_RSSS_FEEDS，还需等待 {remaining:.1f} 秒")

            # =============== FIFTH_RSS_YOUTUBE 处理 ===============
            last_fifth_youtube_run = await load_last_run_time_from_db("FIFTH_RSS_YOUTUBE")
            now = time.time()
            
            if last_fifth_youtube_run is None or not isinstance(last_fifth_youtube_run, (int, float)):
                logger.warning("FIFTH_RSS_YOUTUBE 时间戳无效，重置为0")
                last_fifth_youtube_run = 0.0

            if (now - last_fifth_youtube_run) >= FIFTH_RSS_YOUTUBE_INTERVAL:
          #      logger.info("开始处理 FIFTH_RSS_YOUTUBE 源...")
                for idx, url in enumerate(FIFTH_RSS_YOUTUBE):
                    if message := await process_youtube_feed(session, url, status, youtube_bot):
                        await send_single_message(youtube_bot, TELEGRAM_CHAT_ID[0], message, False)
                 #       logger.info(f"成功处理 FIFTH_RSS_YOUTUBE 源 {idx + 1}/{len(FIFTH_RSS_YOUTUBE)}")
                await save_last_run_time_to_db("FIFTH_RSS_YOUTUBE", now)
        #        logger.info("FIFTH_RSS_YOUTUBE 源处理完成")
            else:
                remaining = FIFTH_RSS_YOUTUBE_INTERVAL - (now - last_fifth_youtube_run)
                logger.info(f"跳过 FIFTH_RSS_YOUTUBE，还需等待 {remaining:.1f} 秒")

        except Exception as e:
            logger.critical(f"主循环发生致命错误: {str(e)}", exc_info=True)
        finally:
            # 释放锁
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                lock_file.close()
            #    logger.info("释放文件锁，程序运行完成，状态已保存")
            except Exception as e:
                logger.error(f"释放文件锁失败: {e}")
if __name__ == "__main__":
    if not USE_SUPABASE:
        create_table()
    asyncio.run(main())