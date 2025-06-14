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
#from md2tgmd import escape
from cron import RSS_GROUPS

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
        now = datetime.now(pytz.utc).timestamp()
        if (now - last_run) < group_config["interval"]:
            return  # 未到间隔时间，跳过处理

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

                            # 发送成功，保存所有条目状态
                            for entry_id in pending_entry_ids:
                                await save_single_status(group_key, feed_url, entry_id)
                                processed_ids.add(entry_id)

                            # 更新内存状态
                            global_status[feed_url] = processed_ids

                        except Exception as send_error:
                            logger.error(f"❌ 发送消息失败 [{feed_url}]")
                            raise  # 抛出异常，阻止后续保存操作

            except Exception as e:
                logger.error(f"❌ 处理失败 [{feed_url}]")

        # ========== 3. 保存最后运行时间 ==========
        await save_last_run_time_to_db(group_key, now)

        # ========== 4. 最终延迟 ==========
        await asyncio.sleep(1)
    except Exception as e:
        logger.critical(f"‼️ 处理组失败 [{group_key}]")

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
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS cleanup_timestamps (
                    feed_group TEXT PRIMARY KEY,
                    last_cleanup_time REAL
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
    text = re.sub(r'#\w+', '', text)    # 移除 hashtags
    text = re.sub(r'@[^\s]+', '', text).strip()     # 移除 @提及
    text = re.sub(r'【\s*】', '', text)    # 移除 【】符号（含中间空格）
    # 新增：如果 # 前后都是空格（或不存在字符），就删除 #
    text = re.sub(r'(?<!\S)#(?!\S)', '', text)
    text = re.sub(r'(?<!\S):(?!\S)', '', text)
    # 仅替换 英文单词.英文单词 的情况（如 example.com → example．com）
 #   text = re.sub(
 #       r'\.([a-zA-Z])',  # 匹配 `.` 后接一个字母（不关心前面是什么）
  #      lambda m: f'．{m.group(1)}',  # 替换 `.` 为 `．`，并保留后面的字母
  #      text
  #  )
    return text

def escape_markdown_v2(text):
    """统一使用此函数进行MarkdownV2转义"""
    chars_to_escape = r'_*[]()~`>#+-=|{}.!\\'
    return re.sub(r'([{}])'.format(re.escape(chars_to_escape)), r'\\\1', text)

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
     #   logging.error(f"消息发送失败: {e}")
        raise

@retry(
    stop=stop_after_attempt(1),
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
                #    logger.warning(f"RSS源暂时不可用（{response.status}）: {feed_url}")
                    return None  # 跳过当前源，下次运行会重试
                response.raise_for_status()
                return parse(await response.read())
    except aiohttp.ClientResponseError as e:
        if e.status in (503, 403,404,429):
         #   logger.warning(f"RSS源暂时不可用{feed_url}")
            return None
    #    logging.error(f"HTTP 错误 {e.status} 抓取失败 {feed_url}: {e}")
        raise
    except Exception as e:
     #   logging.error(f"抓取失败 {feed_url}: {e}")
        raise

# 修改 auto_translate_text 函数
@retry(
    stop=stop_after_attempt(1),
    wait=wait_exponential(multiplier=1, min=2, max=5),
)
async def auto_translate_text(text):
    """翻译文本，失败时返回清理后的原始文本"""
    try:
        # 文本长度处理
        max_length = 2000
        if len(text) > max_length:
            logger.warning(f"⚠️ 文本过长({len(text)}字符)，截断处理")
            text = text[:max_length]
        
        # 第一组密钥尝试
        try:
            return await translate_with_credentials(
                TENCENTCLOUD_SECRET_ID, 
                TENCENTCLOUD_SECRET_KEY,
                text
            )
        except Exception as first_error:
            # 第一组失败且存在备用密钥时尝试第二组
            if TENCENT_SECRET_ID and TENCENT_SECRET_KEY:
            #    logger.warning("⚠️ 主翻译密钥失败，尝试备用密钥...")
                try:
                    return await translate_with_credentials(
                        TENCENT_SECRET_ID,
                        TENCENT_SECRET_KEY,
                        text
                    )
                except Exception as second_error:
                    logger.error(f"备用密钥翻译失败: {second_error}")
            
            # 所有尝试失败时返回清理后的原始文本
            logger.error(f"所有翻译尝试均失败，返回原始文本")
            return remove_html_tags(text)
            
    except Exception as e:
        logging.error(f"翻译过程异常: {e}")
        return remove_html_tags(text)  # 确保返回可用的文本

# 新增辅助翻译函数
async def translate_with_credentials(secret_id, secret_key, text):
    """使用指定凭证进行翻译"""
    cred = credential.Credential(secret_id, secret_key)
    clientProfile = ClientProfile(httpProfile=HttpProfile(endpoint="tmt.tencentcloudapi.com"))
    client = tmt_client.TmtClient(cred, TENCENT_REGION, clientProfile)

    req = models.TextTranslateRequest()
    req.SourceText = remove_html_tags(text)  # 确保文本已清理
    req.Source = "auto"
    req.Target = "zh"
    req.ProjectId = 0

    return client.TextTranslate(req).TargetText

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
      #  logger.error(f"处理源异常 {feed_url}")
        return None
    
def cleanup_history(days, feed_group):
    """仅在超过24小时时执行清理"""
    conn = create_connection()
    if conn:
        try:
            # 检查上次清理时间
            cursor = conn.cursor()
            cursor.execute(
                "SELECT last_cleanup_time FROM cleanup_timestamps WHERE feed_group = ?", 
                (feed_group,)
            )
            result = cursor.fetchone()
            last_cleanup = result[0] if result else 0
            
            now = time.time()
            # 24小时内不清理 (86400秒 = 24小时)
            if now - last_cleanup < 86400:
                return
                
            # 执行清理
            cutoff_ts = now - days * 86400
            cursor.execute(
                "DELETE FROM rss_status WHERE feed_group=? AND entry_timestamp < ?",
                (feed_group, cutoff_ts)
            )
            affected_rows = cursor.rowcount
            
            # 更新清理时间
            cursor.execute("""
                INSERT OR REPLACE INTO cleanup_timestamps (feed_group, last_cleanup_time)
                VALUES (?, ?)
            """, (feed_group, now))
            
            conn.commit()
     #       logger.info(f"✅ 日志清理: 组={feed_group}, 保留天数={days}, 删除条数={affected_rows}")
        except sqlite3.Error as e:
            logger.error(f"❌ 日志清理失败: 组={feed_group}, 错误={e}")
        finally:
            conn.close()

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
    # ================== 3. 清理历史记录 ==================
    for group in RSS_GROUPS:
        days = group.get("history_days", 30)  # 默认30天
        try:
            cleanup_history(days, group["group_key"])
        except Exception as e:
            logger.error(f"清理历史记录异常: 组={group['group_key']}, 错误={e}")
    # ================== 4. 主处理流程 ==================
    async with aiohttp.ClientSession() as session:
        try:
            # ===== 4.1 加载处理状态 =====
            status = await load_status()
     #       logger.info("📂 加载历史状态完成")

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