import asyncio
import aiohttp
import logging
import json
import os
import re
from datetime import datetime
from feedparser import parse
from telegram import Bot
from dotenv import load_dotenv
from aiohttp import ClientTimeout

# 加载环境变量
load_dotenv()

# 配置RSS源
RSS_FEEDS = [   
    'https://blog.090227.xyz/atom.xml',
    'https://www.freedidi.com/feed',
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
]

SECOND_RSS_FEEDS = [
    'https://www.youtube.com/feeds/videos.xml?channel_id=UCUNciDq-y6I6lEQPeoP-R5A', # 苏恒观察
    'https://www.youtube.com/feeds/videos.xml?channel_id=UCXkOTZJ743JgVhJWmNV8F3Q', # 寒國人
    'https://www.youtube.com/feeds/videos.xml?channel_id=UC2r2LPbOUssIa02EbOIm7NA', # 星球熱點
    'https://www.youtube.com/feeds/videos.xml?channel_id=UCF-Q1Zwyn9681F7du8DMAWg', # 謝宗桓-老謝來了
   # 'https://www.youtube.com/feeds/videos.xml?channel_id=UCOSmkVK2xsihzKXQgiXPS4w', # 历史哥
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

# 获取当前脚本所在的目录
script_dir = os.path.dirname(os.path.abspath(__file__))
# 定义 rss2.json 文件的绝对路径
RSS_STATUS_FILE = os.path.join(script_dir, 'rss2.json')
# 配置日志
log_path = os.path.join(script_dir, 'rss2.log')

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(log_path),  # 使用绝对路径
        logging.StreamHandler()
    ]
)

def load_rss_status():
    """加载处理状态（同步版本）"""
    try:
        with open(RSS_STATUS_FILE, 'r') as f:  # 使用绝对路径
            return json.loads(f.read())
    except FileNotFoundError:
        logging.info("状态文件不存在，将创建新文件")
        return {}
    except json.JSONDecodeError:
        logging.warning("状态文件损坏，重置为默认状态")
        return {}

def save_rss_status(status):
    """保存处理状态（同步版本）"""
    with open(RSS_STATUS_FILE, 'w') as f:  # 使用绝对路径
        json.dump(status, f, indent=2, ensure_ascii=False)

def clean_title(title):
    """清理标题特殊字符"""
    return re.sub(r'[^\w\s\u4e00-\u9fa5.,!?;:"\'()\-]+', '', title).strip()

def parse_datetime(pub_date):
    """解析发布时间"""
    try:
        return datetime(*pub_date[:6]) if pub_date else datetime.now()
    except TypeError:
        return datetime.now()

async def process_feed(session, feed_url, status, bot, chat_ids, max_retries=3):
    """处理单个RSS源, 带有重试机制"""
    for attempt in range(max_retries):
        try:
            # 获取RSS内容
            async with session.get(feed_url, timeout=30) as response:
                if response.status != 200:
                    logging.warning(f"请求失败: {feed_url} 状态码 {response.status}")
                    return

                feed = parse(await response.text())
                if not feed.entries:
                    logging.warning(f"无效的RSS源: {feed_url}")
                    return

                # 解析条目
                feed_title = feed.feed.get('title', '未知来源').strip()
                entries = []
                for entry in feed.entries:
                    try:
                        pub_date = parse_datetime(entry.get('published_parsed'))
                        entries.append({
                            'guid': entry.get('id', entry.link),
                            'pubdate': pub_date,
                            'title': clean_title(entry.title),
                            'link': entry.link
                        })
                    except Exception as e:
                        logging.warning(f"条目解析失败: {e}")
                        continue

                # 排序和筛选新条目
                entries.sort(key=lambda x: x['pubdate'], reverse=True)
                last_status = status.get(feed_url, {})
                last_guid = last_status.get('guid')
                last_date = datetime.fromisoformat(last_status['pubdate']) if 'pubdate' in last_status else None

                new_entries = []
                for entry in entries:
                    if entry['guid'] == last_guid or (last_date and entry['pubdate'] <= last_date):
                        break
                    new_entries.append(entry)

                # 发送通知
                if new_entries:
                    message = [f"📢 {feed_title}\n"]
                    for idx, entry in enumerate(reversed(new_entries), 1):
                        message.append(f"{idx}. {entry['title']}\n🔗 {entry['link']}\n")
                    message.append(f"✅ 新增 {len(new_entries)} 条内容")

                    for chat_id in chat_ids:
                        await bot.send_message(
                            chat_id=chat_id,
                            text="\n".join(message),
                            disable_web_page_preview=False
                        )
                        await asyncio.sleep(1)

                    # 更新状态
                    status[feed_url] = {
                        'guid': entries[0]['guid'],
                        'pubdate': entries[0]['pubdate'].isoformat()
                    }
                    save_rss_status(status)  # 同步保存
                return  # 成功处理，退出重试循环

        except Exception as e:
            logging.error(f"处理源时发生错误 {feed_url} (尝试 {attempt+1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(5 * (attempt + 1))  # 指数退避

    logging.error(f"处理源 {feed_url} 失败，已达到最大重试次数")


async def process_feed_with_semaphore(semaphore, session, feed_url, status, bot, chat_ids):
    async with semaphore:
        await process_feed(session, feed_url, status, bot, chat_ids)


async def main():
    """主函数"""
    # 读取配置
    RSS_TOKEN = os.getenv("RSS_TOKEN")
    YOUTUBE_TOKEN = os.getenv("YOUTUBE_RSS")
    CHAT_IDS = os.getenv("TELEGRAM_CHAT_ID", "").split(",")

    if not RSS_TOKEN or not YOUTUBE_TOKEN:
        logging.error("缺少机器人Token配置")
        return

    # 初始化机器人
    main_bot = Bot(token=RSS_TOKEN)
    second_bot = Bot(token=YOUTUBE_TOKEN)
    status = load_rss_status()  # 同步加载

    # 创建信号量
    semaphore = asyncio.Semaphore(5)  # 限制并发数为 5

    timeout = ClientTimeout(total=60) # 设置总超时时间为60秒
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=100), timeout=timeout) as session: # 增大连接池大小
        tasks = []
        for feed in RSS_FEEDS:
            tasks.append(process_feed_with_semaphore(semaphore, session, feed, status, main_bot, CHAT_IDS))
            await asyncio.sleep(0.5) #延迟 0.5 秒

        for feed in SECOND_RSS_FEEDS:
            tasks.append(process_feed_with_semaphore(semaphore, session, feed, status, second_bot, CHAT_IDS))
            await asyncio.sleep(0.5)  # 延迟 0.5 秒

        await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(main())
