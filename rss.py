import asyncio
import aiohttp
import logging
import re
import os
import json
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

# 加载.env文件
load_dotenv()

# 配置绝对路径
BASE_DIR = Path(__file__).resolve().parent
STATUS_FILE = BASE_DIR / "rss.json"

# 增强日志配置
logging.basicConfig(
    filename=BASE_DIR / "rss.log",
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    encoding="utf-8"
)
logger = logging.getLogger(__name__)

# RSS配置
RSS_FEEDS = [
    'https://feeds.bbci.co.uk/news/world/rss.xml', # bbc
    'https://www3.nhk.or.jp/rss/news/cat6.xml',  # nhk
  #  'https://www.cnbc.com/id/100003114/device/rss/rss.html', # CNBC
  #  'https://feeds.a.dj.com/rss/RSSWorldNews.xml', # 华尔街日报
  #  'https://www.aljazeera.com/xml/rss/all.xml',# 半岛电视台
  #  'https://www3.nhk.or.jp/rss/news/cat5.xml',# NHK 商业
  #  'https://www.ft.com/?format=rss', # 金融时报
  #  'http://rss.cnn.com/rss/edition.rss', # cnn
  #  'https://www.youtube.com/feeds/videos.xml?channel_id=UCupvZG-5ko_eiXAupbDfxWw', # cnn
  #  'https://www.youtube.com/feeds/videos.xml?channel_id=UCQeRaTukNYft1_6AZPACnog', # Asmongold TV
]
#主题+内容
THIRD_RSS_FEEDS = [
    'https://36kr.com/feed-newsflash',
  #  'https://rss.owo.nz/10jqka/realtimenews',
]
 # 主题+预览
FOURTH_RSS_FEEDS = [
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

# 翻译主题+链接的
FIFTH_RSS_FEEDS = [
    'https://rsshub.app/twitter/media/elonmusk',  #elonmusk
    'https://www.youtube.com/feeds/videos.xml?channel_id=UCupvZG-5ko_eiXAupbDfxWw', # cnn
    'https://rss.penggan.us.kg/rss/4734eed5ffb55689bfe8ebc4f55e63bd_chinese_simplified', # Asmongold TV

]

# Telegram配置
TELEGRAM_BOT_TOKEN = os.getenv("RSS_TWO")  # bbc
RSS_TWO = os.getenv("RSS_TWO")
RSS_TOKEN = os.getenv("RSS_TOKEN")    # 10086
RSSTWO_TOKEN = os.getenv("RSS_TWO")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").split(",")
TENCENTCLOUD_SECRET_ID = os.getenv("TENCENTCLOUD_SECRET_ID")
TENCENTCLOUD_SECRET_KEY = os.getenv("TENCENTCLOUD_SECRET_KEY")

MAX_CONCURRENT_REQUESTS = 5
semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

def remove_html_tags(text):
    """彻底移除HTML标签"""
    return re.sub(r'<[^>]*>', '', text)

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

async def fetch_feed(session, feed_url):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/89.0.4389.82 Safari/537.36'}
    try:
        async with semaphore:
            async with session.get(feed_url, headers=headers, timeout=40) as response:
                response.raise_for_status()
                return parse(await response.read())
    except Exception as e:
        logging.error(f"抓取失败 {feed_url}: {e}")
        return None

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

def load_status():
    try:
        with open(STATUS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_status(status):
    try:
        with open(STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(status, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logging.error(f"状态保存失败: {e}")

def get_entry_identifier(entry):
    """增强版唯一标识符生成"""
    # 优先使用稳定字段
    if hasattr(entry, 'guid') and entry.guid:
        logger.debug(f"使用GUID作为标识符: {entry.guid}")
        return entry.guid
    if hasattr(entry, 'link') and entry.link:
        logger.debug(f"使用链接作为标识符: {entry.link}")
        return entry.link
    if hasattr(entry, 'id') and entry.id:
        logger.debug(f"使用ID作为标识符: {entry.id}")
        return entry.id

    # 时间相关字段
    time_fields = [
        ('published_parsed', 'published'),
        ('pubDate_parsed', 'pubDate'),
        ('updated_parsed', 'updated')
    ]
    for parsed_field, raw_field in time_fields:
        if hasattr(entry, parsed_field) and getattr(entry, parsed_field):
            dt = datetime(*getattr(entry, parsed_field)[:6])
            logger.debug(f"使用时间字段 {parsed_field} 作为标识符: {dt.isoformat()}")
            return dt.isoformat()
        if hasattr(entry, raw_field) and getattr(entry, raw_field):
            logger.debug(f"使用原始时间字段 {raw_field} 作为标识符: {getattr(entry, raw_field)}")
            return getattr(entry, raw_field)

    # 最终回退方案
    fallback = f"{entry.get('title', '')}-{entry.get('link', '')}"
    logger.warning(f"使用回退标识符: {fallback}")
    return fallback

def get_entry_timestamp(entry):
    """获取标准化时间戳"""
    if hasattr(entry, 'published_parsed') and entry.published_parsed:
        return datetime(*entry.published_parsed[:6])
    if hasattr(entry, 'pubDate_parsed') and entry.pubDate_parsed:
        return datetime(*entry.pubDate_parsed[:6])
    return datetime.now()

async def process_feed(session, feed_url, status, bot, translate=True):
    try:
        feed_data = await fetch_feed(session, feed_url)
        if not feed_data or not feed_data.entries:
            logger.info(f"源 {feed_url} 没有新条目")
            return ""

        # 状态跟踪增强
        last_status = status.get(feed_url, {})
        last_identifier = last_status.get('identifier')
        last_timestamp = datetime.fromisoformat(last_status.get('timestamp')) if last_status.get('timestamp') else None

        logger.info(f"开始处理源: {feed_url}")
        logger.debug(f"上次记录标识符: {last_identifier}")
        logger.debug(f"上次记录时间: {last_timestamp}")

        # 按时间排序（增强容错）
        sorted_entries = []
        for entry in feed_data.entries:
            try:
                entry_time = get_entry_timestamp(entry)
                sorted_entries.append((entry_time, entry))
            except Exception as e:
                logger.error(f"处理条目时间失败: {str(e)}")
                continue
        sorted_entries.sort(key=lambda x: x[0], reverse=True)

        new_entries = []
        current_latest = None
        found_break = False

        # 精确匹配检查
        for entry_time, entry in sorted_entries:
            identifier = get_entry_identifier(entry)
            logger.debug(f"检查条目: {identifier[:50]}... 时间: {entry_time}")

            # 精确匹配检查
            if last_identifier and identifier == last_identifier:
                logger.info(f"找到精确匹配标识符，停止处理")
                found_break = True
                break

            # 时间比对（仅当标识符不可用时）
            if last_timestamp and entry_time <= last_timestamp:
                logger.info(f"时间 {entry_time} <= 上次时间 {last_timestamp}，停止处理")
                found_break = True
                break

            new_entries.append(entry)
            if not current_latest or entry_time > get_entry_timestamp(current_latest):
                current_latest = entry

        if not found_break and last_identifier:
            logger.warning("未找到精确匹配标识符，可能发生数据回滚或标识符变化")

        if not new_entries:
            logger.info(f"没有新条目需要处理: {feed_url}")
            return ""

        # 更新状态
        if current_latest:
            status[feed_url] = {
                "identifier": get_entry_identifier(current_latest),
                "timestamp": get_entry_timestamp(current_latest).isoformat()
            }
            logger.info(f"更新状态: {feed_url} - Identifier: {status[feed_url]['identifier'][:50]}... Timestamp: {status[feed_url]['timestamp']}")

        # 处理消息
        merged_message = ""
        source_name = feed_data.feed.get('title', feed_url)
        # 遍历新条目，添加序号
        for idx, entry in enumerate(reversed(new_entries), start=1):
            # 原始内容处理
            raw_subject = remove_html_tags(entry.title or "无标题")
            raw_summary = remove_html_tags(getattr(entry, 'summary', "暂无简介"))
            raw_url = entry.link

            # 翻译处理
            if translate:
                translated_subject = await auto_translate_text(raw_subject)
                translated_summary = await auto_translate_text(raw_summary)
            else:
                translated_subject = raw_subject
                translated_summary = raw_summary

            # Markdown转义
            safe_subject = escape_markdown_v2(translated_subject, exclude=['*'])
            safe_summary = escape_markdown_v2(translated_summary)
            safe_source = escape_markdown_v2(source_name, exclude=['[', ']'])
            safe_url = escape_markdown_v2(raw_url)

            # 构建消息
            message = f"*{safe_subject}*\n{safe_summary}\n[{safe_source}]({safe_url})"
            merged_message += message + "\n\n"
        merged_message += f"✅ 新增 {len(new_entries)} 条内容"
        return merged_message

    except Exception as e:
        logger.error(f"处理源 {feed_url} 时发生错误: {str(e)}")
        return ""

async def process_third_feed(session, feed_url, status, bot):
    try:
        feed_data = await fetch_feed(session, feed_url)
        if not feed_data or not feed_data.entries:
            logger.info(f"源 {feed_url} 没有新条目")
            return ""

        last_status = status.get(feed_url, {})
        last_identifier = last_status.get('identifier')
        last_timestamp = datetime.fromisoformat(last_status.get('timestamp')) if last_status.get('timestamp') else None

        sorted_entries = sorted(feed_data.entries,
                              key=lambda x: get_entry_timestamp(x),
                              reverse=True)

        new_entries = []
        current_latest = None
        found_break = False

        for entry in sorted_entries:
            entry_time = get_entry_timestamp(entry)
            identifier = get_entry_identifier(entry)

            # 精确匹配检查
            if last_identifier and identifier == last_identifier:
                logger.info(f"找到精确匹配标识符，停止处理")
                found_break = True
                break

            # 时间比对（仅当标识符不可用时）
            if last_timestamp and entry_time <= last_timestamp:
                logger.info(f"时间 {entry_time} <= 上次时间 {last_timestamp}，停止处理")
                found_break = True
                break

            new_entries.append(entry)
            if not current_latest or entry_time > get_entry_timestamp(current_latest):
                current_latest = entry

        if not found_break and last_identifier:
            logger.warning("未找到精确匹配标识符，可能发生数据回滚或标识符变化")

        if not new_entries:
            logger.info(f"没有新条目需要处理: {feed_url}")
            return ""

        if current_latest:
            status[feed_url] = {
                "identifier": get_entry_identifier(current_latest),
                "timestamp": get_entry_timestamp(current_latest).isoformat()
            }
            logger.info(f"更新状态: {feed_url} - Identifier: {status[feed_url]['identifier'][:50]}... Timestamp: {status[feed_url]['timestamp']}")

        merged_message = ""
        source_name = feed_data.feed.get('title', feed_url)
        # 遍历新条目，添加序号
        for idx, entry in enumerate(reversed(new_entries), start=1):
            # 内容处理
            raw_subject = remove_html_tags(entry.title or "无标题")
            raw_summary = remove_html_tags(getattr(entry, 'summary', "暂无简介"))
            raw_url = entry.link

            # Markdown转义
            safe_subject = escape_markdown_v2(raw_subject, exclude=['*'])
            safe_summary = escape_markdown_v2(raw_summary)
            safe_source = escape_markdown_v2(source_name, exclude=['[', ']'])
            safe_url = escape_markdown_v2(raw_url)

            # 消息构建
            message_content = f"*{safe_subject}*\n{safe_summary}\n[{safe_source}]({safe_url})"
            message_bytes = message_content.encode('utf-8')

            if len(message_bytes) <= 444:
                merged_message += message_content + "\n\n"
            else:
                title_link = f"*{safe_subject}*\n[{safe_source}]({safe_url})"
                merged_message += title_link + "\n\n"
        merged_message += f"✅ 新增 {len(new_entries)} 条内容"
        return merged_message
    except Exception as e:
        logger.error(f"处理源 {feed_url} 时发生错误: {str(e)}")
        return ""

async def process_fourth_feed(session, feed_url, status, bot):
    try:
        feed_data = await fetch_feed(session, feed_url)
        if not feed_data or not feed_data.entries:
            logger.info(f"源 {feed_url} 没有新条目")
            return ""

        last_status = status.get(feed_url, {})
        last_identifier = last_status.get('identifier')
        last_timestamp = datetime.fromisoformat(last_status.get('timestamp')) if last_status.get('timestamp') else None

        sorted_entries = sorted(feed_data.entries,
                              key=lambda x: get_entry_timestamp(x),
                              reverse=True)

        new_entries = []
        current_latest = None
        found_break = False

        for entry in sorted_entries:
            entry_time = get_entry_timestamp(entry)
            identifier = get_entry_identifier(entry)

            # 精确匹配检查
            if last_identifier and identifier == last_identifier:
                logger.info(f"找到精确匹配标识符，停止处理")
                found_break = True
                break

            # 时间比对（仅当标识符不可用时）
            if last_timestamp and entry_time <= last_timestamp:
                logger.info(f"时间 {entry_time} <= 上次时间 {last_timestamp}，停止处理")
                found_break = True
                break

            new_entries.append(entry)
            if not current_latest or entry_time > get_entry_timestamp(current_latest):
                current_latest = entry

        if not found_break and last_identifier:
            logger.warning("未找到精确匹配标识符，可能发生数据回滚或标识符变化")

        if not new_entries:
            logger.info(f"没有新条目需要处理: {feed_url}")
            return ""

        if current_latest:
            status[feed_url] = {
                "identifier": get_entry_identifier(current_latest),
                "timestamp": get_entry_timestamp(current_latest).isoformat()
            }
            logger.info(f"更新状态: {feed_url} - Identifier: {status[feed_url]['identifier'][:50]}... Timestamp: {status[feed_url]['timestamp']}")

        merged_message = ""
        source_name = feed_data.feed.get('title', feed_url)
        feed_title = f"**{escape_markdown_v2(source_name, exclude=['*'])}**"  # 转义并加粗标题

        # 添加统计信息
        merged_message += f"📢 *{feed_title}*\n\n"

        # 遍历新条目，添加序号
        for idx, entry in enumerate(reversed(new_entries), start=1):
            # 内容处理
            raw_subject = remove_html_tags(entry.title or "无标题")
            raw_url = entry.link

            clean_subject = re.sub(r'[^\w\s\u4e00-\u9fa5.,!?;:"\'()\-]+', '', raw_subject).strip()
            # Markdown转义
            safe_subject = escape_markdown_v2(clean_subject, exclude=['*'])
            safe_url = escape_markdown_v2(raw_url)

            # 构建消息，添加序号
            merged_message += f"*{safe_subject}*\n🔗 {safe_url}\n\n"
        merged_message += f"✅ 新增 {len(new_entries)} 条内容"
        return merged_message
    except Exception as e:
        logger.error(f"处理源 {feed_url} 时发生错误: {str(e)}")
        return ""

async def process_fifth_feed(session, feed_url, status, bot, translate=True):
    try:
        feed_data = await fetch_feed(session, feed_url)
        if not feed_data or not feed_data.entries:
            logger.info(f"源 {feed_url} 没有新条目")
            return ""

        # 状态处理
        last_status = status.get(feed_url, {})
        last_identifier = last_status.get('identifier')
        last_timestamp = datetime.fromisoformat(last_status.get('timestamp')) if last_status.get('timestamp') else None

        # 按时间排序
        sorted_entries = sorted(feed_data.entries,
                              key=lambda x: get_entry_timestamp(x),
                              reverse=True)

        new_entries = []
        current_latest = None
        found_break = False

        for entry in sorted_entries:
            entry_time = get_entry_timestamp(entry)
            identifier = get_entry_identifier(entry)

            # 精确匹配检查
            if last_identifier and identifier == last_identifier:
                logger.info(f"找到精确匹配标识符，停止处理")
                found_break = True
                break

            # 时间比对（仅当标识符不可用时）
            if last_timestamp and entry_time <= last_timestamp:
                logger.info(f"时间 {entry_time} <= 上次时间 {last_timestamp}，停止处理")
                found_break = True
                break

            new_entries.append(entry)
            if not current_latest or entry_time > get_entry_timestamp(current_latest):
                current_latest = entry

        if not found_break and last_identifier:
            logger.warning("未找到精确匹配标识符，可能发生数据回滚或标识符变化")

        if not new_entries:
            logger.info(f"没有新条目需要处理: {feed_url}")
            return ""

        # 更新状态
        if current_latest:
            status[feed_url] = {
                "identifier": get_entry_identifier(current_latest),
                "timestamp": get_entry_timestamp(current_latest).isoformat()
            }
            logger.info(f"更新状态: {feed_url} - Identifier: {status[feed_url]['identifier'][:50]}... Timestamp: {status[feed_url]['timestamp']}")

        # 处理消息
        merged_message = ""
        source_name = feed_data.feed.get('title', feed_url)
        feed_title = f"**{escape_markdown_v2(source_name, exclude=['*'])}**"  # 转义并加粗标题

        # 添加统计信息
        merged_message += f"📢 *{feed_title}*\n\n"
        # 遍历新条目，添加序号
        for idx, entry in enumerate(reversed(new_entries), start=1):
            # 原始内容处理
            raw_subject = remove_html_tags(entry.title or "无标题")
            raw_url = entry.link

            # 翻译处理
            if translate:
                translated_subject = await auto_translate_text(raw_subject)
            else:
                translated_subject = raw_subject

            # Markdown转义
            safe_subject = escape_markdown_v2(translated_subject, exclude=['*'])
            safe_source = escape_markdown_v2(source_name, exclude=['[', ']'])
            safe_url = escape_markdown_v2(raw_url)

            # 构建消息, 只发送主题和链接
            message = f"*{safe_subject}*\n🔗 {safe_url}"
            merged_message += message + "\n\n"
        merged_message += f"✅ 新增 {len(new_entries)} 条内容"
        return merged_message
    except Exception as e:
        logger.error(f"处理源 {feed_url} 时发生错误: {str(e)}")
        return ""

async def main():
    async with aiohttp.ClientSession() as session:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        third_bot = Bot(token=RSS_TWO)
        fourth_bot = Bot(token=RSS_TOKEN)
        fifth_bot = Bot(token=RSSTWO_TOKEN)
        status = load_status()

        try:
            # 处理第一类源
            for idx, url in enumerate(RSS_FEEDS):
                if message := await process_feed(session, url, status, bot):
                    await send_single_message(bot, TELEGRAM_CHAT_ID[0], message, True)
                    save_status(status)  # 每个源处理后立即保存
                    logger.info(f"成功处理第一类源 {idx+1}/{len(RSS_FEEDS)}")

            # 处理第三类源
            for idx, url in enumerate(THIRD_RSS_FEEDS):
                if message := await process_third_feed(session, url, status, third_bot):
                    await send_single_message(third_bot, TELEGRAM_CHAT_ID[0], message, True)
                    save_status(status)  # 每个源处理后立即保存
                    logger.info(f"成功处理第三类源 {idx + 1}/{len(THIRD_RSS_FEEDS)}")

            # 处理第四类源
            for idx, url in enumerate(FOURTH_RSS_FEEDS):
                if message := await process_fourth_feed(session, url, status, fourth_bot):
                    await send_single_message(fourth_bot, TELEGRAM_CHAT_ID[0], message)
                    save_status(status)  # 每个源处理后立即保存
                    logger.info(f"成功处理第四类源 {idx + 1}/{len(FOURTH_RSS_FEEDS)}")

            # 处理第五类源
            for idx, url in enumerate(FIFTH_RSS_FEEDS):
                if message := await process_fifth_feed(session, url, status, fifth_bot):
                    await send_single_message(fifth_bot, TELEGRAM_CHAT_ID[0], message, False)  # 根据需要调整True不浏览
                    save_status(status)  # 每个源处理后立即保存
                    logger.info(f"成功处理第五类源 {idx + 1}/{len(FIFTH_RSS_FEEDS)}")

        except Exception as e:
            logger.critical(f"主循环发生致命错误: {str(e)}")
        finally:
            save_status(status)  # 最终确保保存
            logger.info("程序运行完成，状态已保存")

if __name__ == "__main__":
    asyncio.run(main())
