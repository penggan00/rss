import asyncio
import aiohttp
import aiomysql
import logging
import re
import os
from dotenv import load_dotenv
from feedparser import parse
from telegram import Bot
from tencentcloud.common import credential
from tencentcloud.common.profile.client_profile import ClientProfile
from tencentcloud.common.profile.http_profile import HttpProfile
from tencentcloud.tmt.v20180321 import tmt_client, models

# 加载.env 文件
load_dotenv()

# 主题+翻意内容+预览
RSS_FEEDS = [
  #  'https://feeds.bbci.co.uk/news/world/rss.xml', # bbc
  #  'https://www3.nhk.or.jp/rss/news/cat6.xml',  # nhk
  #  'http://www3.nhk.or.jp/rss/news/cat5.xml',  # nhk金融
  #  'https://www.cnbc.com/id/100003114/device/rss/rss.html', # CNBC
  #  'https://feeds.a.dj.com/rss/RSSWorldNews.xml', # 华尔街日报
  #  'https://www.aljazeera.com/xml/rss/all.xml',# 半岛电视台
  #  'https://www3.nhk.or.jp/rss/news/cat5.xml',# NHK 商业
  #  'https://www.ft.com/?format=rss', # 金融时报
  #  'http://rss.cnn.com/rss/edition.rss', # cnn

]
#主题+内容+预览
THIRD_RSS_FEEDS = [ 
    'https://36kr.com/feed-newsflash',
    'https://rsshub.app/10jqka/realtimenews',

]
 # 主题+预览
FOURTH_RSS_FEEDS = [
   # 'https://www.youtube.com/feeds/videos.xml?channel_id=UCvijahEyGtvMpmMHBu4FS2w', # 零度解说
  #  'https://www.youtube.com/feeds/videos.xml?channel_id=UC96OvMh0Mb_3NmuE8Dpu7Gg', # 搞机零距离
   # 'https://www.youtube.com/feeds/videos.xml?channel_id=UCQoagx4VHBw3HkAyzvKEEBA', # 科技共享
   # 'https://www.youtube.com/feeds/videos.xml?channel_id=UCbCCUH8S3yhlm7__rhxR2QQ', # 不良林
  #  'https://www.youtube.com/feeds/videos.xml?channel_id=UCMtXiCoKFrc2ovAGc1eywDg', # 一休 
  #  'https://www.youtube.com/feeds/videos.xml?channel_id=UCii04BCvYIdQvshrdNDAcww', # 悟空的日常 
  #  'https://www.youtube.com/feeds/videos.xml?channel_id=UCJMEiNh1HvpopPU3n9vJsMQ', # 理科男士 
  #  'https://www.youtube.com/feeds/videos.xml?channel_id=UCYjB6uufPeHSwuHs8wovLjg', # 中指通 
  #  'https://www.youtube.com/feeds/videos.xml?channel_id=UCSs4A6HYKmHA2MG_0z-F0xw', # 李永乐老师 
  #  'https://www.youtube.com/feeds/videos.xml?channel_id=UCZDgXi7VpKhBJxsPuZcBpgA', # 可恩KeEn  
  #  'https://www.youtube.com/feeds/videos.xml?channel_id=UCxukdnZiXnTFvjF5B5dvJ5w', # 甬哥侃侃侃ygkkk  
  #  'https://www.youtube.com/feeds/videos.xml?channel_id=UCUfT9BAofYBKUTiEVrgYGZw', # 科技分享  
  #  'https://www.youtube.com/feeds/videos.xml?channel_id=UC51FT5EeNPiiQzatlA2RlRA', # 乌客wuke  
  #  'https://www.youtube.com/feeds/videos.xml?channel_id=UCDD8WJ7Il3zWBgEYBUtc9xQ', # jack stone  
  #  'https://www.youtube.com/feeds/videos.xml?channel_id=UCWurUlxgm7YJPPggDz9YJjw', # 一瓶奶油
  #  'https://www.youtube.com/feeds/videos.xml?channel_id=UC6-ZYliTgo4aTKcLIDUw0Ag', # 音樂花園
  #  'https://www.youtube.com/feeds/videos.xml?channel_id=UCvENMyIFurJi_SrnbnbyiZw', # 酷友社
  #  'https://www.youtube.com/feeds/videos.xml?channel_id=UCmhbF9emhHa-oZPiBfcLFaQ', # WenWeekly
  #  'https://www.youtube.com/feeds/videos.xml?channel_id=UC3BNSKOaphlEoK4L7QTlpbA', # 中外观察
]

# Telegram 配置
TELEGRAM_BOT_TOKEN = os.getenv("RSS_TWO")      #10086
RSS_TWO = os.getenv("RSS_TWO")    #10086
RSS_TOKEN = os.getenv("RSS_TOKEN")  #好烟   
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").split(",")

# 数据库连接配置
DB_CONFIG = {
    'host': os.getenv("DB_HOST"),
    'db': os.getenv("DB_NAME"),
    'user': os.getenv("DB_USER"),
    'password': os.getenv("DB_PASSWORD"),
    'minsize': 1,
    'maxsize': 10
}

TENCENTCLOUD_SECRET_ID = os.getenv("TENCENTCLOUD_SECRET_ID")
TENCENTCLOUD_SECRET_KEY = os.getenv("TENCENTCLOUD_SECRET_KEY")

def sanitize_markdown(text):
    # 首先去除 HTML 标签
    text = re.sub(r'<[^>]*>', '', text)
    # 然后去除 Telegram 不支持的 Markdown 符号
    text = re.sub(r'[*_`|#\\[\\](){}<>]', '', text)
    return text

async def send_single_message(bot, chat_id, text, disable_web_page_preview=False):
    try:
        # Telegram 最大消息字节数限制：4096字节
        MAX_MESSAGE_LENGTH = 4096
        # 计算消息的字节数
        if len(text.encode('utf-8')) > MAX_MESSAGE_LENGTH:
            # 如果超长，拆分为多个消息
            for i in range(0, len(text), MAX_MESSAGE_LENGTH):
                await bot.send_message(
                    chat_id=chat_id, 
                    text=text[i:i+MAX_MESSAGE_LENGTH], 
                    parse_mode='Markdown', 
                    disable_web_page_preview=disable_web_page_preview
                )
        else:
            # 如果没有超长，直接发送
            await bot.send_message(
                chat_id=chat_id, 
                text=text, 
                parse_mode='Markdown', 
                disable_web_page_preview=disable_web_page_preview
            )
    except Exception as e:
        logging.error(f"Failed to send message: {e}")

async def fetch_feed(session, feed_url):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/89.0.4389.82 Safari/537.36'
    }
    try:
        async with session.get(feed_url, headers=headers, timeout=40) as response:
            response.raise_for_status()
            content = await response.read()
            return parse(content)
    except Exception as e:
        logging.error(f"Error fetching {feed_url}: {e}")
        return None

async def auto_translate_text(text):
    try:
        cred = credential.Credential(TENCENTCLOUD_SECRET_ID, TENCENTCLOUD_SECRET_KEY)
        httpProfile = HttpProfile(endpoint="tmt.tencentcloudapi.com")
        clientProfile = ClientProfile(httpProfile=httpProfile)
        client = tmt_client.TmtClient(cred, "na-siliconvalley", clientProfile)

        req = models.TextTranslateRequest()
        req.SourceText = text
        req.Source = "auto"
        req.Target = "zh"
        req.ProjectId = 0

        resp = client.TextTranslate(req)
        return resp.TargetText
    except Exception as e:
        logging.error(f"Translation error for text '{text}': {e}")
        return text
# 主题+翻意内容+预览
async def process_feed(session, feed_url, sent_entries, pool, bot, table_name, translate=True):
    feed_data = await fetch_feed(session, feed_url)
    if feed_data is None:
        return []

    source_name = feed_data.feed.get('title', feed_url)  # 动态获取源名称
    new_entries = []

    for entry in feed_data.entries:
        subject = entry.title or "*无标题*"
        url = entry.link
        summary = getattr(entry, 'summary', "暂无简介")
        message_id = f"{subject}_{url}"

        if (url, subject, message_id) not in sent_entries:
            if translate:
                translated_subject = await auto_translate_text(subject)
                translated_summary = await auto_translate_text(summary)
            else:
                translated_subject = subject
                translated_summary = summary

            cleaned_subject = sanitize_markdown(translated_subject)
            message = f"*{cleaned_subject}*\n{translated_summary}\n[{source_name}]({url})"
            await send_single_message(bot, TELEGRAM_CHAT_ID[0], message)

            new_entries.append((url, subject, message_id))
            await save_sent_entry_to_db(pool, url, subject, message_id, table_name)
            sent_entries.add((url, subject, message_id))

    return new_entries
# 主题+内容 超过333字节不发送
async def process_third_feed(session, feed_url, sent_entries, pool, bot, table_name):
    feed_data = await fetch_feed(session, feed_url)
    if feed_data is None:
        return []

    source_name = feed_data.feed.get('title', feed_url)  # 动态获取源名称
    merged_message = ""

    for entry in feed_data.entries:
        subject = entry.title or "*无标题*"
        url = entry.link
        summary = getattr(entry, 'summary', "暂无简介")
        summary = sanitize_markdown(summary)
        message_id = f"{subject}_{url}"

        if (url, subject, message_id) not in sent_entries:
            cleaned_subject = sanitize_markdown(subject)

            # 检查主题和内容的字节长度
            total_length = len(cleaned_subject.encode('utf-8')) + len(summary.encode('utf-8'))
            if total_length > 333:
                # 超过 333 字节的内容直接跳过，不发送
                continue
            else:
                # 如果字节长度不超过 333 字节，则合并发送
                merged_message += f"*{cleaned_subject}*\n{summary}\n[{source_name}]({url})\n\n"
                sent_entries.add((url, subject, message_id))
                await save_sent_entry_to_db(pool, url, subject, message_id, table_name)

    if merged_message:
        # 发送合并后的消息
        await send_single_message(bot, TELEGRAM_CHAT_ID[0], merged_message, disable_web_page_preview=True)

    return []

# 主题+预览
async def process_fourth_feed(session, feed_url, sent_entries, pool, bot, table_name):
    feed_data = await fetch_feed(session, feed_url)
    if feed_data is None:
        return []

    source_name = feed_data.feed.get('title', feed_url)  # 动态获取源名称
    merged_message = ""

    for entry in feed_data.entries:
        subject = entry.title or "*无标题*"
        url = entry.link
        message_id = f"{subject}_{url}"

        if (url, subject, message_id) not in sent_entries:
            cleaned_subject = sanitize_markdown(subject)
            merged_message += f"{source_name}\n*{cleaned_subject}*\n{url}\n\n"

            sent_entries.add((url, subject, message_id))
            await save_sent_entry_to_db(pool, url, subject, message_id, table_name)

    if merged_message:
        await send_single_message(bot, TELEGRAM_CHAT_ID[0], merged_message, disable_web_page_preview=False)

    return []

async def connect_to_db_pool():
    try:
        return await aiomysql.create_pool(**DB_CONFIG)
    except Exception as e:
        logging.error(f"Database connection error: {e}")
        return None

async def load_sent_entries_from_db(pool, table_name):
    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(f"SELECT url, subject, message_id FROM {table_name}")
                rows = await cursor.fetchall()
                return {(row[0], row[1], row[2]) for row in rows}
    except Exception as e:
        logging.error(f"Error loading sent entries: {e}")
        return set()

async def save_sent_entry_to_db(pool, url, subject, message_id, table_name):
    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    f"INSERT IGNORE INTO {table_name} (url, subject, message_id) VALUES (%s, %s, %s)",
                    (url, subject, message_id)
                )
                await conn.commit()
    except Exception as e:
        logging.error(f"Error saving entry: {e}")

async def main():
    pool = await connect_to_db_pool()
    if not pool:
        logging.error("Failed to connect to the database.")
        return

    async with pool:
        sent_entries = await load_sent_entries_from_db(pool, "sent_rss")
        sent_entries_third = await load_sent_entries_from_db(pool, "sent_rss2")
        sent_entries_fourth = await load_sent_entries_from_db(pool, "sent_rss")

        async with aiohttp.ClientSession() as session:
            bot = Bot(token=TELEGRAM_BOT_TOKEN)
            third_bot = Bot(token=RSS_TWO)
            fourth_bot = Bot(token=RSS_TOKEN)

            tasks = [
                process_feed(session, feed_url, sent_entries, pool, bot, "sent_rss", translate=True)
                for feed_url in RSS_FEEDS
            ] + [
                process_third_feed(session, feed_url, sent_entries_third, pool, third_bot, "sent_rss2")
                for feed_url in THIRD_RSS_FEEDS
            ] + [
                process_fourth_feed(session, feed_url, sent_entries_fourth, pool, fourth_bot, "sent_rss")
                for feed_url in FOURTH_RSS_FEEDS
            ]

            await asyncio.gather(*tasks)

        pool.close()
        await pool.wait_closed()

if __name__ == "__main__":
    asyncio.run(main())