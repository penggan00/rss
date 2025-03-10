# rss2合并消息推送
import asyncio
import aiohttp
import aiomysql
import logging
import datetime
from feedparser import parse
from telegram import Bot
import re
import os
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()

# RSS 源列表
RSS_FEEDS = [
  #  'https://blog.090227.xyz/atom.xml',  # CM
   # 'https://www.freedidi.com/feed', # 零度解说
   # 'https://rsshub.app/bilibili/hot-search', # bilibili
   # 'https://rss.mifaw.com/articles/5c8bb11a3c41f61efd36683e/5c91d2e23882afa09dff4901', # 36氪 - 24小时热榜
   # 'https://rss.mifaw.com/articles/5c8bb11a3c41f61efd36683e/5cac99a7f5648c90ed310e18', # 微博热搜
   # 'https://rss.mifaw.com/articles/5c8bb11a3c41f61efd36683e/5cf92d7f0cc93bc69d082608', # 百度热搜榜
   # 'https://rsshub.app/guancha/headline', # 观察网
   # 'https://rsshub.app/zaobao/znews/china', # 联合早报
   # 'https://36kr.com/feed',    # 36氪 
    # 添加更多 RSS 源
]

SECOND_RSS_FEEDS = [
    'https://www.youtube.com/feeds/videos.xml?channel_id=UCUNciDq-y6I6lEQPeoP-R5A', # 苏恒观察
 #   'https://www.youtube.com/feeds/videos.xml?channel_id=UCMtXiCoKFrc2ovAGc1eywDg', # 一休
 #   'https://www.youtube.com/feeds/videos.xml?channel_id=UCii04BCvYIdQvshrdNDAcww', # 悟空的日常
 #   'https://www.youtube.com/feeds/videos.xml?channel_id=UCJMEiNh1HvpopPU3n9vJsMQ', # 理科男士
  #  'https://www.youtube.com/feeds/videos.xml?channel_id=UCYjB6uufPeHSwuHs8wovLjg', # 中指通
  #  'https://www.youtube.com/feeds/videos.xml?channel_id=UCSs4A6HYKmHA2MG_0z-F0xw', # 李永乐老师
  #  'https://www.youtube.com/feeds/videos.xml?channel_id=UCZDgXi7VpKhBJxsPuZcBpgA', # 可恩Ke En
  #  'https://www.youtube.com/feeds/videos.xml?channel_id=UCbCCUH8S3yhlm7__rhxR2QQ', # 不良林
    'https://www.youtube.com/feeds/videos.xml?channel_id=UCXkOTZJ743JgVhJWmNV8F3Q', # 寒國人
    'https://www.youtube.com/feeds/videos.xml?channel_id=UC2r2LPbOUssIa02EbOIm7NA', # 星球熱點
    'https://www.youtube.com/feeds/videos.xml?channel_id=UC000Jn3HGeQSwBuX_cLDK8Q', # 我是柳傑克
 #   'https://www.youtube.com/feeds/videos.xml?channel_id=UCQoagx4VHBw3HkAyzvKEEBA', # 科技共享
    'https://www.youtube.com/feeds/videos.xml?channel_id=UCF-Q1Zwyn9681F7du8DMAWg', # 謝宗桓-老謝來了
    'https://www.youtube.com/feeds/videos.xml?channel_id=UCOSmkVK2xsihzKXQgiXPS4w', # 历史哥
    'https://www.youtube.com/feeds/videos.xml?channel_id=UCSYBgX9pWGiUAcBxjnj6JCQ', # 郭正亮頻道
    'https://www.youtube.com/feeds/videos.xml?channel_id=UCNiJNzSkfumLB7bYtXcIEmg', # 真的很博通
    'https://www.youtube.com/feeds/videos.xml?channel_id=UCG_gH6S-2ZUOtEw27uIS_QA', # 7Car小七車觀點
    'https://www.youtube.com/feeds/videos.xml?channel_id=UCJ5rBA0z4WFGtUTS83sAb_A', # POP Radio聯播網
    'https://www.youtube.com/feeds/videos.xml?channel_id=UCiwt1aanVMoPYUt_CQYCPQg', # 全球大視野
    'https://www.youtube.com/feeds/videos.xml?channel_id=UCb3TZ4SD_Ys3j4z0-8o6auA', # BBC News 中文
  #  'https://www.youtube.com/feeds/videos.xml?channel_id=UC96OvMh0Mb_3NmuE8Dpu7Gg', # 搞机零距离

# 影视
    'https://www.youtube.com/feeds/videos.xml?channel_id=UC7Xeh7thVIgs_qfTlwC-dag', # Marc TV
    'https://www.youtube.com/feeds/videos.xml?channel_id=UCqWNOHjgfL8ADEdXGznzwUw', # 悦耳音乐酱
    'https://www.youtube.com/feeds/videos.xml?channel_id=UCCD14H7fJQl3UZNWhYMG3Mg', # 温城鲤
    'https://www.youtube.com/feeds/videos.xml?channel_id=UCQO2T82PiHCYbqmCQ6QO6lw', # 月亮說
    'https://www.youtube.com/feeds/videos.xml?channel_id=UCKyDmY3R_xGKz8IjvbijiHA', # 珊珊追剧社
    'https://www.youtube.com/feeds/videos.xml?channel_id=UClyVC2wh_2fQhU0hPdXA4rw', # 热门古风曲
    'https://www.youtube.com/feeds/videos.xml?channel_id=UC1ISajIKfRN359MMmtckUTg', # Taiwanese Pop Mix
    'https://www.youtube.com/feeds/videos.xml?channel_id=UCQFyMGc6h30NMCd6HCk0ZPA', # 哔哩哔哩动画
]

# Telegram 配置
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SECOND_TELEGRAM_BOT_TOKEN = os.getenv("SECOND_TELEGRAM_BOT_TOKEN")
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

async def fetch_feed(session, feed):
    try:
        async with session.get(feed, timeout=88) as response:
            response.raise_for_status()
            content = await response.read()
            return parse(content)
    except Exception as e:
        logging.error(f"Error fetching {feed}: {e}")
        return None

async def send_message(bot, chat_id, text, chunk_size=4000):
    for i in range(0, len(text), chunk_size):
        chunk = text[i:i + chunk_size]
        try:
            # 直接用纯文本推送消息
            await bot.send_message(chat_id=chat_id, text=chunk)
        except Exception as e:
            logging.error(f"Failed to send message: {e}")

async def process_feed(session, feed, sent_entries, pool, bot, TELEGRAM_CHAT_ID, table_name):
    feed_data = await fetch_feed(session, feed)
    if feed_data is None:
        return []

    new_entries = []
    messages = []  # 用于存储新条目的消息

    for entry in feed_data.entries:
        subject = entry.title if entry.title else None
        url = entry.link if entry.link else None

        # 使用正则表达式去除主题中的特殊符号
        if subject:
            subject = re.sub(r'[^a-zA-Z0-9\u4e00-\u9fa5]+', '', subject)  # 保留中英文及数字

        message_id = f"{subject}_{url}" if subject and url else None

        if (url, subject, message_id) not in sent_entries:
            message = f"{subject}\n{url}"  # 使用清理后的标题
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

    # 合并为一个消息进行推送
    if messages:
        combined_message = "\n\n".join(messages)
        for chat_id in TELEGRAM_CHAT_ID:
            await send_message(bot, chat_id, combined_message)
        await asyncio.sleep(6)  # 避免触发 Telegram API 速率限制

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
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        second_bot = Bot(token=SECOND_TELEGRAM_BOT_TOKEN)

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
