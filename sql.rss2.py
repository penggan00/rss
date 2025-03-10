import asyncio
import aiohttp
import aiomysql
import logging
import datetime
import os
import re
from dotenv import load_dotenv
from feedparser import parse
from telegram import Bot
from telegram.constants import ParseMode
import urllib.parse

# 加载 .env 文件
load_dotenv()

# RSS 源列表
RSS_FEEDS = [
   # 'https://blog.090227.xyz/atom.xml',
   # 'https://www.freedidi.com/feed',
   # 'https://www.youtube.com/feeds/videos.xml?channel_id=UCvijahEyGtvMpmMHBu4FS2w', # 零度解说
   # 'https://www.youtube.com/feeds/videos.xml?channel_id=UC96OvMh0Mb_3NmuE8Dpu7Gg', # 搞机零距离
   # 'https://www.youtube.com/feeds/videos.xml?channel_id=UCQoagx4VHBw3HkAyzvKEEBA', # 科技共享
   # 'https://www.youtube.com/feeds/videos.xml?channel_id=UCbCCUH8S3yhlm7__rhxR2QQ', # 不良林
   # 'https://www.youtube.com/feeds/videos.xml?channel_id=UCMtXiCoKFrc2ovAGc1eywDg', # 一休 
   # 'https://www.youtube.com/feeds/videos.xml?channel_id=UCii04BCvYIdQvshrdNDAcww', # 悟空的日常 
   # 'https://www.youtube.com/feeds/videos.xml?channel_id=UCJMEiNh1HvpopPU3n9vJsMQ', # 理科男士 
   # 'https://www.youtube.com/feeds/videos.xml?channel_id=UCYjB6uufPeHSwuHs8wovLjg', # 中指通 
   # 'https://www.youtube.com/feeds/videos.xml?channel_id=UCSs4A6HYKmHA2MG_0z-F0xw', # 李永乐老师 
   # 'https://www.youtube.com/feeds/videos.xml?channel_id=UCZDgXi7VpKhBJxsPuZcBpgA', # 可恩KeEn  
   # 'https://www.youtube.com/feeds/videos.xml?channel_id=UCxukdnZiXnTFvjF5B5dvJ5w', # 甬哥侃侃侃ygkkk  
   # 'https://www.youtube.com/feeds/videos.xml?channel_id=UCUfT9BAofYBKUTiEVrgYGZw', # 科技分享  
   # 'https://www.youtube.com/feeds/videos.xml?channel_id=UC51FT5EeNPiiQzatlA2RlRA', # 乌客wuke  
   # 'https://www.youtube.com/feeds/videos.xml?channel_id=UCDD8WJ7Il3zWBgEYBUtc9xQ', # jack stone  
   # 'https://www.youtube.com/feeds/videos.xml?channel_id=UCWurUlxgm7YJPPggDz9YJjw', # 一瓶奶油
   # 'https://www.youtube.com/feeds/videos.xml?channel_id=UC6-ZYliTgo4aTKcLIDUw0Ag', # 音樂花園
   # 'https://www.youtube.com/feeds/videos.xml?channel_id=UCvENMyIFurJi_SrnbnbyiZw', # 酷友社
]

SECOND_RSS_FEEDS = [
    'https://www.youtube.com/feeds/videos.xml?channel_id=UCUNciDq-y6I6lEQPeoP-R5A', # 苏恒观察
    'https://www.youtube.com/feeds/videos.xml?channel_id=UCXkOTZJ743JgVhJWmNV8F3Q', # 寒國人
    'https://www.youtube.com/feeds/videos.xml?channel_id=UC2r2LPbOUssIa02EbOIm7NA', # 星球熱點
    'https://www.youtube.com/feeds/videos.xml?channel_id=UCF-Q1Zwyn9681F7du8DMAWg', # 謝宗桓-老謝來了
  #  'https://www.youtube.com/feeds/videos.xml?channel_id=UCOSmkVK2xsihzKXQgiXPS4w', # 历史哥
    'https://www.youtube.com/feeds/videos.xml?channel_id=UCSYBgX9pWGiUAcBxjnj6JCQ', # 郭正亮頻道
    'https://www.youtube.com/feeds/videos.xml?channel_id=UCNiJNzSkfumLB7bYtXcIEmg', # 真的很博通
 #   'https://www.youtube.com/feeds/videos.xml?channel_id=UCG_gH6S-2ZUOtEw27uIS_QA', # 7Car小七車觀點
  #  'https://www.youtube.com/feeds/videos.xml?channel_id=UCJ5rBA0z4WFGtUTS83sAb_A', # POP Radio聯播網
  #  'https://www.youtube.com/feeds/videos.xml?channel_id=UCQeRaTukNYft1_6AZPACnog', # Asmongold TV 
    'https://rss.penggan.us.kg/rss/4734eed5ffb55689bfe8ebc4f55e63bd_chinese_simplified', # Asmongold TV 
    'https://www.youtube.com/feeds/videos.xml?channel_id=UCN0eCImZY6_OiJbo8cy5bLw', # 屈機TV 
    'https://www.youtube.com/feeds/videos.xml?channel_id=UCb3TZ4SD_Ys3j4z0-8o6auA', # BBC News 中文
    'https://www.youtube.com/feeds/videos.xml?channel_id=UCiwt1aanVMoPYUt_CQYCPQg', # 全球大視野
    'https://www.youtube.com/feeds/videos.xml?channel_id=UC000Jn3HGeQSwBuX_cLDK8Q', # 我是柳傑克
    'https://www.youtube.com/feeds/videos.xml?channel_id=UCQFEBaHCJrHu2hzDA_69WQg', # 国漫说
    'https://www.youtube.com/feeds/videos.xml?channel_id=UChJ8YKw6E1rjFHVS9vovrZw', # BNE TV - 新西兰中文国际频道
    'https://www.youtube.com/feeds/videos.xml?channel_id=UCXk0rwHPG9eGV8SaF2p8KUQ', # 烏鴉笑笑
    
# 影视
    'https://www.youtube.com/feeds/videos.xml?channel_id=UC7Xeh7thVIgs_qfTlwC-dag', # Marc TV
  #  'https://www.youtube.com/feeds/videos.xml?channel_id=UCqWNOHjgfL8ADEdXGznzwUw', # 悦耳音乐酱
    'https://www.youtube.com/feeds/videos.xml?channel_id=UCCD14H7fJQl3UZNWhYMG3Mg', # 温城鲤
    'https://www.youtube.com/feeds/videos.xml?channel_id=UCQO2T82PiHCYbqmCQ6QO6lw', # 月亮說
   # 'https://www.youtube.com/feeds/videos.xml?channel_id=UCKyDmY3R_xGKz8IjvbijiHA', # 珊珊追剧社
    'https://www.youtube.com/feeds/videos.xml?channel_id=UClyVC2wh_2fQhU0hPdXA4rw', # 热门古风
  #  'https://www.youtube.com/feeds/videos.xml?channel_id=UC1ISajIKfRN359MMmtckUTg', # Taiwanese Pop Mix
  #  'https://www.youtube.com/feeds/videos.xml?channel_id=UCQFyMGc6h30NMCd6HCk0ZPA', # 哔哩哔哩动画
  #  'https://www.youtube.com/feeds/videos.xml?channel_id=UCQatgKoA7lylp_UzvsLCgcw', # 腾讯视频
  #  'https://www.youtube.com/feeds/videos.xml?channel_id=UCUhpu5MJQ_bjPkXO00jyxsw', # 爱奇艺
    'https://www.youtube.com/feeds/videos.xml?channel_id=UCHW6W9g2TJL2_Lf7GfoI5kg', # 电影放映厅
]

# Telegram 配置
RSS_TOKEN = os.getenv("RSS_TOKEN")
YOUTUBE_RSS = os.getenv("YOUTUBE_RSS")
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

async def fetch_feed(session, feed_url):
    try:
        async with session.get(feed_url, timeout=88) as response:
            response.raise_for_status()
            content = await response.read()
            parsed_feed = parse(content)
            feed_title = parsed_feed.feed.get('title', '未命名来源')  # 获取 RSS 名称
            return parsed_feed, feed_title
    except Exception as e:
        logging.error(f"Error fetching {feed_url}: {e}")
        return None, None


async def send_message(bot, chat_id, text, chunk_size=4096):
    for i in range(0, len(text), chunk_size):
        chunk = text[i:i + chunk_size]
        try:
            # 使用 HTML 格式推送消息
            await bot.send_message(chat_id=chat_id, text=chunk, parse_mode=ParseMode.HTML)
        except Exception as e:
            logging.error(f"Failed to send HTML message: {e}. Retrying with plain text.")
            try:
                # 回退到原文推送
                await bot.send_message(chat_id=chat_id, text=chunk)
            except Exception as e:
                logging.error(f"Failed to send fallback plain text message: {e}")

async def process_feed(session, feed_url, sent_entries, pool, bot, TELEGRAM_CHAT_ID, table_name):
    feed_data, feed_title = await fetch_feed(session, feed_url)
    if feed_data is None or feed_title is None:
        return []

    new_entries = []
    messages = []

    for entry in feed_data.entries:
        subject = entry.title if entry.title else None
        url = entry.link if entry.link else None

        if subject:
            # 添加来源名称到消息中并保留标点符号
            subject = f"{subject}"
            # 修改正则表达式以保留标点符号
            subject = re.sub(r'[^\w\s\u4e00-\u9fa5.,!?;:"\'()\-]+', '', subject)

        message_id = f"{subject}_{url}" if subject and url else None

        if (url, subject, message_id) not in sent_entries:
            message = f"{feed_title}\n<b>{subject}</b>\n{url}"
            messages.append(message)
            new_entries.append((url, subject, message_id))

            current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            await save_sent_entry_to_db(
                pool,
                url if url else current_time,
                subject if subject else current_time,
                message_id if message_id else current_time,
                table_name
            )
            sent_entries.add((url, subject, message_id))

    if messages:
        combined_message = "\n\n".join(messages)  # 使用换行符拼接消息
        for chat_id in TELEGRAM_CHAT_ID:
            await send_message(bot, chat_id, combined_message)
        await asyncio.sleep(6)

    return new_entries

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

    sent_entries = await load_sent_entries_from_db(pool, "sent_rss")
    sent_entries_second = await load_sent_entries_from_db(pool, "sent_youtube")

    async with aiohttp.ClientSession() as session:
        bot = Bot(token=RSS_TOKEN)
        second_bot = Bot(token=YOUTUBE_RSS)

        tasks = [
            process_feed(session, feed, sent_entries, pool, bot, TELEGRAM_CHAT_ID, "sent_rss")
            for feed in RSS_FEEDS
        ]
        tasks += [
            process_feed(session, feed, sent_entries_second, pool, second_bot, TELEGRAM_CHAT_ID, "sent_youtube")
            for feed in SECOND_RSS_FEEDS
        ]

        await asyncio.gather(*tasks)

    pool.close()
    await pool.wait_closed()

if __name__ == "__main__":
    asyncio.run(main())