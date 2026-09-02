#source rss_venv/bin/activate
#pip install aiohttp pytz aiosqlite python-dotenv feedparser python-telegram-bot tenacity md2tgmd tencentcloud-sdk-python langdetect
import asyncio
import aiohttp
import logging
import re
import os
import hashlib
import pytz
import fcntl
import time
import signal
import aiosqlite
import sys
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from feedparser import parse
from telegram import Bot
from telegram.error import BadRequest
from urllib.parse import urlparse
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from md2tgmd import escape
from tencentcloud.common import credential
from tencentcloud.common.profile.client_profile import ClientProfile
from tencentcloud.common.profile.http_profile import HttpProfile
from tencentcloud.tmt.v20180321 import tmt_client, models
from tencentcloud.common.exception.tencent_cloud_sdk_exception import TencentCloudSDKException
from collections import defaultdict
from langdetect import detect, LangDetectException
from rss_config import RSS_GROUPS

# ========== 全局退出标志 ==========
SHOULD_EXIT = False
# ========== 环境加载 ==========
load_dotenv()
BASE_DIR = Path(__file__).resolve().parent
LOCK_FILE = BASE_DIR / "rss.lock"
DATABASE_FILE = BASE_DIR / "rss.db"

def clean_old_log():
    """日志文件超过10MB就删除"""
    log_file = BASE_DIR / "rss.log"
    if log_file.exists():
        size_mb = log_file.stat().st_size / 1024 / 1024
        if size_mb > 10:  # 超过10MB
            log_file.unlink()  # 直接删除

logging.basicConfig(
    filename=BASE_DIR / "rss.log",
    level=logging.WARNING,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    encoding="utf-8"
)
logger = logging.getLogger(__name__)

TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID").split(",")
TENCENTCLOUD_SECRET_ID = os.getenv("TENCENTCLOUD_SECRET_ID")
TENCENTCLOUD_SECRET_KEY = os.getenv("TENCENTCLOUD_SECRET_KEY")
TENCENT_REGION = os.getenv("TENCENT_REGION", "na-siliconvalley")
TENCENT_SECRET_ID = os.getenv("TENCENT_SECRET_ID")
TENCENT_SECRET_KEY = os.getenv("TENCENT_SECRET_KEY")
semaphore = asyncio.Semaphore(2)
BACKUP_DOMAINS_STR = os.getenv("BACKUP_DOMAINS", "")
BACKUP_DOMAINS = [domain.strip() for domain in BACKUP_DOMAINS_STR.split(",") if domain.strip()]

# RSS_GROUPS = []  # 将在main函数中从配置文件加载

# ========== 数据库配置 ==========
PG_URL = os.getenv("PG_URL")
USE_PG = PG_URL is not None

# 日志记录数据库类型
if USE_PG:
    # 安全地记录数据库信息（隐藏密码）
    safe_pg_url = re.sub(r':([^@]+)@', ':****@', PG_URL) if PG_URL else "未配置"
    logger.info(f"🔧 使用 PostgreSQL 数据库: {safe_pg_url}")
    print(f"✅ PostgreSQL ")
else:
    logger.info(f"🔧 使用 SQLite 数据库: {DATABASE_FILE}")
    print(f"✅ SQLite : {DATABASE_FILE}")

if USE_PG:
    import asyncpg
class RSSDatabase:
    def __init__(self, loop=None):
        self.loop = loop or asyncio.get_event_loop()
        self.conn = None
        self.pg_pool = None

    async def open(self):
        if USE_PG:
            self.pg_pool = await asyncpg.create_pool(PG_URL)
        else:
            self.conn = await aiosqlite.connect(DATABASE_FILE)

    async def close(self):
        if USE_PG and self.pg_pool:
            await self.pg_pool.close()
        elif self.conn:
            await self.conn.close()

    async def ensure_initialized(self):
        """确保数据库表已创建"""
        await self.create_tables()

    async def create_tables(self):  # 这里缩进修复
        """改进的建表语句，确保 PostgreSQL 和 SQLite 索引一致"""
        if USE_PG:
            async with self.pg_pool.acquire() as conn:
                # 主表
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS rss_status (
                        feed_group TEXT,
                        feed_url TEXT,
                        entry_url TEXT,
                        entry_content_hash TEXT,
                        entry_timestamp DOUBLE PRECISION,
                        PRIMARY KEY (feed_group, feed_url, entry_url)
                    );
                """)
                # 确保内容哈希索引存在
                await conn.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_group_content_hash 
                    ON rss_status(feed_group, entry_content_hash);
                """)
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS timestamps (
                        feed_group TEXT PRIMARY KEY,
                        last_run_time DOUBLE PRECISION
                    );
                """)
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS cleanup_timestamps (
                        feed_group TEXT PRIMARY KEY,
                        last_cleanup_time DOUBLE PRECISION
                    );
                """)
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS pending_messages (
                        feed_group TEXT,
                        feed_url TEXT,
                        entry_id TEXT,
                        content_hash TEXT,
                        title TEXT,
                        translated_title TEXT,
                        link TEXT,
                        summary TEXT,
                        entry_timestamp DOUBLE PRECISION,
                        sent INTEGER DEFAULT 0,
                        feed_title TEXT,
                        PRIMARY KEY (feed_group, feed_url, entry_id)
                    );
                """)
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS batch_timestamps (
                        feed_group TEXT PRIMARY KEY,
                        last_batch_sent_time DOUBLE PRECISION
                    );
                """)
        else:
            async with self.conn.cursor() as c:
                await c.execute("""
                    CREATE TABLE IF NOT EXISTS rss_status (
                        feed_group TEXT,
                        feed_url TEXT,
                        entry_url TEXT,
                        entry_content_hash TEXT,
                        entry_timestamp REAL,
                        PRIMARY KEY (feed_group, feed_url, entry_url)
                    )""")
                await c.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_group_content_hash 
                    ON rss_status(feed_group, entry_content_hash);
                """)
                await c.execute("""
                    CREATE TABLE IF NOT EXISTS timestamps (
                        feed_group TEXT PRIMARY KEY,
                        last_run_time REAL
                    )""")
                await c.execute("""
                    CREATE TABLE IF NOT EXISTS cleanup_timestamps (
                        feed_group TEXT PRIMARY KEY,
                        last_cleanup_time REAL
                    )""")
                await c.execute("""
                    CREATE TABLE IF NOT EXISTS pending_messages (
                        feed_group TEXT,
                        feed_url TEXT,
                        entry_id TEXT,
                        content_hash TEXT,
                        title TEXT,
                        translated_title TEXT,
                        link TEXT,
                        summary TEXT,
                        entry_timestamp REAL,
                        sent INTEGER DEFAULT 0,
                        feed_title TEXT,
                        PRIMARY KEY (feed_group, feed_url, entry_id)
                    )
                """)
                await c.execute("""
                    CREATE TABLE IF NOT EXISTS batch_timestamps (
                        feed_group TEXT PRIMARY KEY,
                        last_batch_sent_time REAL
                    )
                """)
                await self.conn.commit()

    async def add_pending_message(self, feed_group, feed_url, entry_id, content_hash, title, translated_title, link, summary, timestamp, feed_title):
        if USE_PG:
            async with self.pg_pool.acquire() as conn:
                await conn.execute("""
                INSERT INTO pending_messages (feed_group, feed_url, entry_id, content_hash, title, translated_title, link, summary, entry_timestamp, sent, feed_title)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 0, $10)
                ON CONFLICT DO NOTHING
                """, feed_group, feed_url, entry_id, content_hash, title, translated_title, link, summary, timestamp, feed_title)
        else:
            async with self.conn.cursor() as c:
                await c.execute("""
                    INSERT OR IGNORE INTO pending_messages
                    (feed_group, feed_url, entry_id, content_hash, title, translated_title, link, summary, entry_timestamp, sent, feed_title)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                """, (feed_group, feed_url, entry_id, content_hash, title, translated_title, link, summary, timestamp, feed_title))
                await self.conn.commit()

    async def get_pending_messages(self, feed_group):
        if USE_PG:
            async with self.pg_pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT * FROM pending_messages
                    WHERE feed_group=$1 AND sent=0
                    ORDER BY entry_timestamp ASC
                """, feed_group)
                return [dict(row) for row in rows]
        else:
            async with self.conn.cursor() as c:
                await c.execute("""
                    SELECT * FROM pending_messages
                    WHERE feed_group=? AND sent=0
                    ORDER BY entry_timestamp ASC
                """, (feed_group,))
                keys = [d[0] for d in c.description]
                rows = await c.fetchall()
                return [dict(zip(keys, row)) for row in rows]

    async def mark_pending_as_sent(self, feed_group, ids):
        if not ids:
            return
        if USE_PG:
            async with self.pg_pool.acquire() as conn:
                await conn.executemany("""
                    UPDATE pending_messages SET sent=1
                    WHERE feed_group=$1 AND entry_id=$2
                """, [(feed_group, eid) for eid in ids])
        else:
            async with self.conn.cursor() as c:
                await c.executemany("""
                    UPDATE pending_messages SET sent=1
                    WHERE feed_group=? AND entry_id=?
                """, [(feed_group, eid) for eid in ids])
                await self.conn.commit()

    async def get_last_batch_sent_time(self, feed_group):
        if USE_PG:
            async with self.pg_pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT last_batch_sent_time FROM batch_timestamps WHERE feed_group=$1
                """, feed_group)
                return row['last_batch_sent_time'] if row else 0
        else:
            async with self.conn.cursor() as c:
                await c.execute("""
                    SELECT last_batch_sent_time FROM batch_timestamps WHERE feed_group=?
                """, (feed_group,))
                result = await c.fetchone()
                return result[0] if result else 0

    async def save_last_batch_sent_time(self, feed_group, ts):
        if USE_PG:
            async with self.pg_pool.acquire() as conn:
                await conn.execute("""
                INSERT INTO batch_timestamps (feed_group, last_batch_sent_time)
                VALUES ($1, $2)
                ON CONFLICT (feed_group) DO UPDATE SET last_batch_sent_time=EXCLUDED.last_batch_sent_time
                """, feed_group, ts)
        else:
            async with self.conn.cursor() as c:
                await c.execute("""
                    INSERT OR REPLACE INTO batch_timestamps (feed_group, last_batch_sent_time)
                    VALUES (?, ?)
                """, (feed_group, ts))
                await self.conn.commit()

    async def save_status(self, feed_group, feed_url, entry_url, entry_content_hash, timestamp):
        """改进的状态保存，确保去重一致性"""
        if USE_PG:
            async with self.pg_pool.acquire() as conn:
                # 使用 ON CONFLICT 确保唯一性
                await conn.execute("""
                    INSERT INTO rss_status (feed_group, feed_url, entry_url, entry_content_hash, entry_timestamp) 
                    VALUES($1, $2, $3, $4, $5) 
                    ON CONFLICT (feed_group, feed_url, entry_url) 
                    DO UPDATE SET 
                        entry_content_hash = EXCLUDED.entry_content_hash,
                        entry_timestamp = EXCLUDED.entry_timestamp
                """, feed_group, feed_url, entry_url, entry_content_hash, timestamp)
        else:
            async with self.conn.cursor() as c:
                await c.execute(
                    "INSERT OR REPLACE INTO rss_status VALUES (?, ?, ?, ?, ?)",
                    (feed_group, feed_url, entry_url, entry_content_hash, timestamp)
                )
                await self.conn.commit()

    async def has_content_hash(self, feed_group, content_hash):
        """改进的内容哈希检查，确保编码一致性"""
        if USE_PG:
            async with self.pg_pool.acquire() as conn:
                # 修复：PostgreSQL 参数占位符错误，应该是 $1, $2
                row = await conn.fetchrow(
                    "SELECT 1 FROM rss_status WHERE feed_group=$1 AND entry_content_hash=$2 LIMIT 1",
                    feed_group, content_hash
                )
                return row is not None
        else:
            async with self.conn.cursor() as c:
                await c.execute(
                    "SELECT 1 FROM rss_status WHERE feed_group=? AND entry_content_hash=? LIMIT 1",
                    (feed_group, content_hash)
                )
                return await c.fetchone() is not None

    async def load_status(self):
        if USE_PG:
            async with self.pg_pool.acquire() as conn:
                rows = await conn.fetch("SELECT feed_url, entry_url FROM rss_status")
                status = {}
                for row in rows:
                    feed_url, entry_url = row['feed_url'], row['entry_url']
                    status.setdefault(feed_url, set()).add(entry_url)
                return status
        else:
            async with self.conn.cursor() as c:
                await c.execute("SELECT feed_url, entry_url FROM rss_status")
                rows = await c.fetchall()
                status = {}
                for feed_url, entry_url in rows:
                    status.setdefault(feed_url, set()).add(entry_url)
                return status

    async def load_last_run_time(self, feed_group):
        if USE_PG:
            async with self.pg_pool.acquire() as conn:
                row = await conn.fetchrow("SELECT last_run_time FROM timestamps WHERE feed_group=$1", feed_group)
                return row['last_run_time'] if row else 0
        else:
            async with self.conn.cursor() as c:
                await c.execute("SELECT last_run_time FROM timestamps WHERE feed_group = ?", (feed_group,))
                result = await c.fetchone()
                return result[0] if result else 0

    async def save_last_run_time(self, feed_group, last_run_time):
        if USE_PG:
            async with self.pg_pool.acquire() as conn:
                await conn.execute("""
                INSERT INTO timestamps (feed_group, last_run_time)
                VALUES ($1, $2)
                ON CONFLICT (feed_group) DO UPDATE SET last_run_time=EXCLUDED.last_run_time
                """, feed_group, last_run_time)
        else:
            async with self.conn.cursor() as c:
                await c.execute("""
                    INSERT OR REPLACE INTO timestamps (feed_group, last_run_time)
                    VALUES (?, ?)
                """, (feed_group, last_run_time))
                await self.conn.commit()

    async def cleanup_history(self, days, feed_group):
        """清理历史数据，包括 rss_status 和 pending_messages"""
        now = time.time()
        cutoff_ts = now - days * 86400

        if USE_PG:
            async with self.pg_pool.acquire() as conn:
                # 检查上次清理时间，24小时内不重复清理
                row = await conn.fetchrow(
                    "SELECT last_cleanup_time FROM cleanup_timestamps WHERE feed_group=$1", 
                    feed_group
                )
                last_cleanup = row['last_cleanup_time'] if row else 0
                if now - last_cleanup < 86400:
                    return

                # 1. 清理 rss_status（原有逻辑）
                await conn.execute(
                    "DELETE FROM rss_status WHERE feed_group=$1 AND entry_timestamp<$2",
                    feed_group, cutoff_ts
                )

                # 2. ✅ 新增：清理 pending_messages（已发送）
                await conn.execute(
                    """
                    DELETE FROM pending_messages 
                    WHERE feed_group = $1 
                    AND sent = 1 
                    AND entry_timestamp < $2
                    """,
                    feed_group, cutoff_ts
                )

                # 3. ✅ 新增：清理 pending_messages（未发送但超时）
                await conn.execute(
                    """
                    DELETE FROM pending_messages 
                    WHERE feed_group = $1 
                    AND sent = 0 
                    AND entry_timestamp < $2
                    """,
                    feed_group, cutoff_ts
                )

                # 更新清理时间戳
                await conn.execute("""
                    INSERT INTO cleanup_timestamps (feed_group, last_cleanup_time)
                    VALUES ($1, $2)
                    ON CONFLICT (feed_group) DO UPDATE SET last_cleanup_time = EXCLUDED.last_cleanup_time
                """, feed_group, now)

        else:  # SQLite
            async with self.conn.cursor() as c:
                # 检查上次清理时间
                await c.execute(
                    "SELECT last_cleanup_time FROM cleanup_timestamps WHERE feed_group = ?",
                    (feed_group,)
                )
                result = await c.fetchone()
                last_cleanup = result[0] if result else 0
                if now - last_cleanup < 86400:
                    return

                # 1. 清理 rss_status
                await c.execute(
                    "DELETE FROM rss_status WHERE feed_group=? AND entry_timestamp < ?",
                    (feed_group, cutoff_ts)
                )

                # 2. ✅ 新增：清理 pending_messages（已发送）
                await c.execute(
                    """
                    DELETE FROM pending_messages 
                    WHERE feed_group = ? 
                    AND sent = 1 
                    AND entry_timestamp < ?
                    """,
                    (feed_group, cutoff_ts)
                )

                # 3. ✅ 新增：清理 pending_messages（未发送但超时）
                await c.execute(
                    """
                    DELETE FROM pending_messages 
                    WHERE feed_group = ? 
                    AND sent = 0 
                    AND entry_timestamp < ?
                    """,
                    (feed_group, cutoff_ts)
                )

                # 更新清理时间戳
                await c.execute(
                    "INSERT OR REPLACE INTO cleanup_timestamps (feed_group, last_cleanup_time) VALUES (?, ?)",
                    (feed_group, now)
                )
                await self.conn.commit()

# ========== 业务逻辑 ==========

def remove_html_tags(text):
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'#([^#\s]+)#', r'\1', text)
    text = re.sub(r'#\w+', '', text)
    text = re.sub(r'@[^\s]+', '', text).strip()
    text = re.sub(r'【\s*】', '', text)
    text = re.sub(r'(?<!\S)#(?!\S)', '', text)
    text = re.sub(r'(?<!\S)：(?!\S)', '', text)
    text = re.sub(r'(^|\s)[,?!；：。]', '', text)
 #   text = text.replace('.', '.\u200c')
    return text

def get_entry_identifier(entry):
    if hasattr(entry, 'guid') and entry.guid:
        return hashlib.sha256(entry.guid.encode()).hexdigest()
    link = getattr(entry, 'link', '')
    if link:
        try:
            parsed = urlparse(link)
            clean_link = parsed._replace(query=None, fragment=None).geturl().lower()
            return hashlib.sha256(clean_link.encode()).hexdigest()
        except Exception as e:
            logger.warning(f"URL解析失败 {link}: {e}")
    title = getattr(entry, 'title', '')
    pub_date = get_entry_timestamp(entry).isoformat() if get_entry_timestamp(entry) else ''
    return hashlib.sha256(f"{title}|||{pub_date}".encode()).hexdigest()

def get_entry_content_hash(entry):
    """改进的内容哈希计算，确保编码一致性"""
    title = getattr(entry, 'title', '') or ''
    summary = getattr(entry, 'summary', '') or ''
    
    # 统一处理编码和空格
    title = title.strip().encode('utf-8')
    summary = summary.strip().encode('utf-8')
    
    # 获取发布时间（如果有）
    pub_date = ''
    if hasattr(entry, 'published'):
        pub_date = entry.published
    elif hasattr(entry, 'updated'):
        pub_date = entry.updated
    
    pub_date = pub_date.strip().encode('utf-8')
    
    # 创建统一的哈希字符串
    raw_text = title + b'|||' + summary + b'|||' + pub_date
    return hashlib.sha256(raw_text).hexdigest()

def signal_handler(signum, frame):
    """改进的信号处理"""
    global SHOULD_EXIT
    logger.warning(f"收到信号 {signum}，正在优雅退出...")
    SHOULD_EXIT = True

def get_entry_timestamp(entry):
    dt = datetime.now(pytz.UTC)
    if hasattr(entry, 'published_parsed') and entry.published_parsed:
        dt = datetime(*entry.published_parsed[:6], tzinfo=pytz.utc)
    elif hasattr(entry, 'pubDate_parsed') and entry.pubDate_parsed:
        dt = datetime(*entry.pubDate_parsed[:6], tzinfo=pytz.utc)
    elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
        dt = datetime(*entry.updated_parsed[:6], tzinfo=pytz.utc)
    return dt

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
        paragraphs = text.split('\n\n')
        for para in paragraphs:
            para_length = len(para)  # 字符长度
            if current_length + para_length + 2 > MAX_MESSAGE_LENGTH:
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
                disable_web_page_preview=disable_web_page_preview,
                read_timeout=10,
                write_timeout=10
            )
    except BadRequest as e:
        logger.error(f"消息发送失败(Markdown错误): {e} - 文本长度: {len(text)}")
    except Exception as e:
        raise

@retry(
    stop=stop_after_attempt(1),
    wait=wait_exponential(multiplier=1, min=5, max=30),
    retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError)),
)
async def fetch_feed(session, feed_url):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/89.0.4389.82 Safari/537.36'}
    parsed = urlparse(feed_url)
    
    # 构建尝试的域名列表
    if parsed.netloc == "rsshub.app":
        try_domains = [parsed.netloc] + BACKUP_DOMAINS
    else:
        try_domains = [parsed.netloc]
    
    for domain in try_domains:
        current_url = feed_url.replace(parsed.netloc, domain)
        
        try:
            async with semaphore:
                async with session.get(current_url, headers=headers, timeout=30) as response:
                    if response.status in (503, 403, 404, 429):
                        continue
                    response.raise_for_status()
                    
                    feed_data = parse(await response.read())
                    
                    # ✅ 关键修复：无论用哪个备用域名，都返回原始feed_url
                    # 这样不同域名访问同一RSS源时，数据库状态会合并在一起
                    return feed_data, feed_url

        except aiohttp.ClientResponseError as e:
            if e.status in (503, 403, 404, 429):
                continue
        except Exception:
            continue
    
    return None, feed_url  # ✅ 失败时也返回原始feed_url

async def translate_with_credentials(secret_id, secret_key, text):
    loop = asyncio.get_running_loop()
    text_bytes = text.encode('utf-8')
    if len(text_bytes) > 2000:
        safe_bytes = text_bytes[:2000]
        while safe_bytes[-1] & 0xC0 == 0x80:
            safe_bytes = safe_bytes[:-1]
        text = safe_bytes.decode('utf-8', errors='ignore')
     #   logger.warning(f"文本截断至 {len(text)} 字符 ({len(safe_bytes)} 字节)")
    try:
        return await loop.run_in_executor(
            None, 
            lambda: _sync_translate(secret_id, secret_key, text)
        )
    except Exception as e:
    #    logger.error(f"翻译执行失败: {type(e).__name__} - {str(e)}")
        raise

def is_need_translate(text):
    try:
        lang = detect(text)
        # 只对英文、日文、韩文、阿拉伯文等非中文做翻译
        return lang not in ("zh-cn", "zh-tw", "zh", "yue")
    except LangDetectException:
        return False
    
def is_mostly_symbols(text):
    """检查文本是否主要由符号、数字组成"""
    if not text:
        return True
    
    # 计算字母比例
    alpha_count = sum(1 for char in text if char.isalpha())
    total_chars = len(text)
    
    # 如果字母比例低于30%，认为是符号/数字文本
    return alpha_count / total_chars < 0.3 if total_chars > 0 else True

def _sync_translate(secret_id, secret_key, text):
    try:
        cred = credential.Credential(secret_id, secret_key)
        clientProfile = ClientProfile(httpProfile=HttpProfile(endpoint="tmt.tencentcloudapi.com"))
        client = tmt_client.TmtClient(cred, TENCENT_REGION, clientProfile)
        req = models.TextTranslateRequest()
        req.SourceText = remove_html_tags(text)
        req.Source = "auto"
        req.Target = "zh"
        req.ProjectId = 0
        return client.TextTranslate(req).TargetText
    except TencentCloudSDKException as e:
        error_details = {
            "code": getattr(e, "code", ""),
            "message": getattr(e, "message", str(e)),
            "request_id": getattr(e, "request_id", ""),
            "region": TENCENT_REGION
        }
    #    logger.error(f"腾讯云API错误详情: {error_details}")
        raise
    except Exception as e:
      #  logger.error(f"翻译过程中发生未知错误: {str(e)}")
        raise

async def should_send_entry(entry, processor):
    filter_config = processor.get("filter", {})
    
    # 如果没有启用过滤，直接返回 True
    if not filter_config.get("enable", False):
        return True
        
    title = getattr(entry, "title", "") or ""      # 获取标题
    link = getattr(entry, "link", "") or ""        # 获取链接
    summary = getattr(entry, "summary", "") or ""  # 获取摘要
    
    # 获取过滤范围配置，默认为 "title"
    scope = filter_config.get("scope", "title")
    keywords = [kw.lower() for kw in filter_config.get("keywords", [])]
    mode = filter_config.get("mode", "allow")
    
    # 根据范围配置构建过滤内容
    content_parts = []
    
    if scope == "title":
        content_parts = [title]
    elif scope == "link":
        content_parts = [link]
    elif scope == "both":
        content_parts = [title, link]
    elif scope == "all":
        content_parts = [title, link, summary]
    elif scope == "title_summary":
        content_parts = [title, summary]
    elif scope == "link_summary":
        content_parts = [link, summary]
    else:  # 默认只过滤标题
        content_parts = [title]
    
    # 合并内容并进行过滤检查
    content = " ".join(content_parts).lower()
    has_keyword = any(keyword in content for keyword in keywords)
    
    # 记录过滤详情（调试用）
    logger.debug(f"[关键词过滤] 范围: {scope} | 标题: {title[:50]} | 链接: {link[:50]} | 关键词: {keywords} | 模式: {mode} | 命中: {has_keyword}")
    
    # 根据模式决定是否发送
    if not keywords:  # 如果没有关键词，根据模式决定
        return mode != "allow"
    elif mode == "allow":
        return has_keyword
    elif mode == "block":
        return not has_keyword
    else:
        return True
    
@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def auto_translate_text(text):
    cleaned_text = remove_html_tags(text).strip()
    
    # 如果文本过短或主要是符号/数字，直接返回原文
    if len(cleaned_text) <= 3 or is_mostly_symbols(cleaned_text):
      #  logger.debug(f"跳过翻译 - 文本过短或主要为符号: {cleaned_text}")
        return escape(cleaned_text)
    
    try:
        # 首先尝试主密钥
        try:
            return await translate_with_credentials(
                TENCENTCLOUD_SECRET_ID, 
                TENCENTCLOUD_SECRET_KEY,
                cleaned_text
            )
        except TencentCloudSDKException as e:
            if getattr(e, "code", "") == "FailedOperation.LanguageRecognitionErr":
             #   logger.warning(f"腾讯云语言识别失败，返回原文: {cleaned_text[:100]}")
                return escape(cleaned_text)
            else:
          #      logger.error(f"主密钥翻译失败: [Code: {e.code}] {e.message}")
                raise
                
    except Exception as first_error:
        # 只有在非语言识别错误的情况下才尝试备用密钥
        if TENCENT_SECRET_ID and TENCENT_SECRET_KEY:
        #    logger.warning("主翻译密钥失败（非语言识别错误），尝试备用密钥...")
            try:
                return await translate_with_credentials(
                    TENCENT_SECRET_ID,
                    TENCENT_SECRET_KEY,
                    cleaned_text
                )
            except TencentCloudSDKException as e:
                if getattr(e, "code", "") == "FailedOperation.LanguageRecognitionErr":
                 #   logger.warning(f"备用密钥语言识别失败，返回原文: {cleaned_text[:100]}")
                    return escape(cleaned_text)
                else:
                #    logger.error(f"备用密钥翻译失败: [Code: {e.code}] {e.message}")
                    raise
            except Exception as e:
         #       logger.error(f"备用密钥翻译未知错误: {type(e).__name__} - {str(e)}")
                raise
        else:
      #      logger.error("主翻译密钥失败，且未配置备用密钥")
            return escape(cleaned_text)

async def generate_group_message(feed_data, entries, processor):
    try:
        source_name = feed_data.feed.get('title', "未知来源")
        safe_source = escape(source_name)
        header = ""
        if "header_template" in processor:
            header = processor["header_template"].format(source=safe_source) + "\n"
        
        messages = []
        
        # 获取模板配置
        if "templates" in processor:
            templates = processor["templates"]
            normal_template = templates.get("normal", "{subject}\n[more]({url})")
            highlight_enabled = processor.get("highlight", {}).get("enable", False)
            if highlight_enabled:
                highlight_template = templates.get(processor["highlight"].get("use_template", "highlight"), normal_template)
        else:
            # 向后兼容：如果只有单个template
            normal_template = processor.get("template", "{subject}\n[more]({url})")
            highlight_template = normal_template
            highlight_enabled = False
        
        # 获取高亮配置
        highlight_config = processor.get("highlight", {})
        highlight_scope = highlight_config.get("scope", "title")
        highlight_keywords = highlight_config.get("keywords", [])
        
        for entry in entries:
            raw_subject = remove_html_tags(entry.title or "无标题")
            
            # 检查是否需要翻译
            if processor.get("translate", False):
                translated_subject = await auto_translate_text(raw_subject)
            else:
                translated_subject = raw_subject
            
            # 决定使用哪个模板
            selected_template = normal_template
            if highlight_enabled and highlight_keywords:
                # 检查标题中是否包含关键词
                subject_lower = translated_subject.lower()
                has_keyword_in_subject = any(keyword.lower() in subject_lower for keyword in highlight_keywords)
                
                # 根据scope配置检查摘要
                has_keyword_in_summary = False
                if highlight_scope == "all":
                    raw_summary = getattr(entry, "summary", "") or ""
                    summary_text = remove_html_tags(raw_summary).lower()
                    has_keyword_in_summary = any(keyword.lower() in summary_text for keyword in highlight_keywords)
                
                # 如果标题或摘要（根据scope）包含关键词，使用加粗模板
                if has_keyword_in_subject or has_keyword_in_summary:
                    selected_template = highlight_template
            
            # 在转义之前添加零宽字符处理
            translated_subject = translated_subject.replace('.', '.\u200c')
            safe_subject = escape(translated_subject)
            
            raw_url = entry.link
            safe_url = escape(raw_url)
            
            format_kwargs = {
                "subject": safe_subject,
                "source": safe_source,
                "url": safe_url
            }
            
            # 检查模板是否需要summary字段
            if "{summary}" in selected_template:
                raw_summary = getattr(entry, "summary", "") or ""
                cleaned_summary = remove_html_tags(raw_summary)
                cleaned_summary = cleaned_summary.replace('.', '.\u200c')
                safe_summary = escape(cleaned_summary)
                format_kwargs["summary"] = safe_summary
            
            # 使用选择的模板生成消息
            message = selected_template.format(**format_kwargs)
            messages.append(message)
        
        full_message = await _format_batch_message(header, messages, processor)
        return full_message
    except Exception as e:
        logger.error(f"生成消息失败: {str(e)}")
        return ""
async def generate_single_messages(feed_data, entries, processor):
    """为每个条目生成单独的消息"""
    try:
        source_name = feed_data.feed.get('title', "未知来源")
        safe_source = escape(source_name)
        
        messages = []
        
        # 获取模板配置
        if "templates" in processor:
            templates = processor["templates"]
            normal_template = templates.get("normal", "{subject}\n[more]({url})")
            highlight_enabled = processor.get("highlight", {}).get("enable", False)
            if highlight_enabled:
                highlight_template = templates.get(processor["highlight"].get("use_template", "highlight"), normal_template)
        else:
            normal_template = processor.get("template", "{subject}\n[more]({url})")
            highlight_template = normal_template
            highlight_enabled = False
        
        # 获取高亮配置
        highlight_config = processor.get("highlight", {})
        highlight_scope = highlight_config.get("scope", "title")
        highlight_keywords = highlight_config.get("keywords", [])
        
        for entry in entries:
            raw_subject = remove_html_tags(entry.title or "无标题")
            
            # 检查是否需要翻译
            if processor.get("translate", False):
                translated_subject = await auto_translate_text(raw_subject)
            else:
                translated_subject = raw_subject
            
            # 检查是否需要添加header
            header = ""
            if "header_template" in processor:
                header = processor["header_template"].format(source=safe_source) + "\n"
            
            # 决定使用哪个模板
            selected_template = normal_template
            if highlight_enabled and highlight_keywords:
                # 检查标题中是否包含关键词
                subject_lower = translated_subject.lower()
                has_keyword_in_subject = any(keyword.lower() in subject_lower for keyword in highlight_keywords)
                
                # 根据scope配置检查摘要
                has_keyword_in_summary = False
                if highlight_scope == "all":
                    raw_summary = getattr(entry, "summary", "") or ""
                    summary_text = remove_html_tags(raw_summary).lower()
                    has_keyword_in_summary = any(keyword.lower() in summary_text for keyword in highlight_keywords)
                
                # 如果标题或摘要（根据scope）包含关键词，使用加粗模板
                if has_keyword_in_subject or has_keyword_in_summary:
                    selected_template = highlight_template
            
            # 在转义之前添加零宽字符处理
            translated_subject = translated_subject.replace('.', '.\u200c')
            safe_subject = escape(translated_subject)
            
            raw_url = entry.link
            safe_url = escape(raw_url)
            
            format_kwargs = {
                "subject": safe_subject,
                "source": safe_source,
                "url": safe_url
            }
            
            # 检查模板是否需要summary字段
            if "{summary}" in selected_template:
                raw_summary = getattr(entry, "summary", "") or ""
                cleaned_summary = remove_html_tags(raw_summary)
                cleaned_summary = cleaned_summary.replace('.', '.\u200c')
                safe_summary = escape(cleaned_summary)
                format_kwargs["summary"] = safe_summary
            
            # 使用选择的模板生成消息
            message_content = selected_template.format(**format_kwargs)
            
            # 添加header到每条消息
            full_message = header + message_content
            
            messages.append({
                "content": full_message,
                "entry": entry
            })
        
        return messages
    except Exception as e:
        logger.error(f"生成单条消息失败: {str(e)}")
        return []

async def send_single_messages_separately(bot, chat_id, messages_data, processor):
    """单独发送每条消息"""
    sent_count = 0
    for msg_data in messages_data:
        try:
            await send_single_message(
                bot,
                chat_id,
                msg_data["content"],
                disable_web_page_preview=not processor.get("preview", True)
            )
            sent_count += 1
            # 在消息之间添加短暂延迟，避免发送过快
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"发送单条消息失败: {e}")
    
    return sent_count
async def _format_batch_message(header, messages, processor):
    """改进的批量消息格式化，确保Markdown格式完整"""
    MAX_MESSAGE_LENGTH = 4096
    
    if not messages:
        return ""
    
    # 尝试构建完整消息
    full_content = header + "\n\n".join(messages)
    if processor.get("show_count", False):
        full_content += f"\n\n✅ 新增 {len(messages)} 条内容"
    
    # 如果消息长度在限制内，直接返回
    if len(full_content) <= MAX_MESSAGE_LENGTH:
        return full_content
    
    # 消息过长，需要分段
    segments = []
    current_segment = header
    current_length = len(header)
    
    for i, message in enumerate(messages):
        # 新段的第一条消息不加分隔符，后续消息加分隔符
        if current_segment == header:
            message_with_separator = message
        else:
            message_with_separator = "\n\n" + message
        
        # 检查添加这条消息是否会超过限制（预留100字符给计数信息）
        if current_length + len(message_with_separator) > MAX_MESSAGE_LENGTH - 300:
            # 完成当前段
            if processor.get("show_count", False) and current_segment != header:
                segment_msg_count = current_segment.count("\n\n") + 1
                current_segment += f"\n\n✅ 本段包含 {segment_msg_count} 条内容"
            segments.append(current_segment)
            
            # 开始新段，重新添加header
            current_segment = header
            current_length = len(header)
            message_with_separator = message  # 新段的第一条消息不加分隔符
        
        current_segment += message_with_separator
        current_length += len(message_with_separator)
    
    # 添加最后一段
    if current_segment.strip() and current_segment != header:
        if processor.get("show_count", False):
            segment_msg_count = current_segment.count("\n\n") + 1
            current_segment += f"\n\n✅ 本段包含 {segment_msg_count} 条内容"
        segments.append(current_segment)
    
    return segments

async def send_batch_messages(bot, chat_id, message_content, disable_web_page_preview=False):
    """发送批量消息，处理分段"""
    if isinstance(message_content, list):  # 分段消息
        for i, segment in enumerate(message_content):
            if segment.strip():  # 确保段不为空
                try:
                    await send_single_message(
                        bot, chat_id, segment, 
                        disable_web_page_preview=disable_web_page_preview
                    )
                    if i < len(message_content) - 1:  # 不是最后一条
                        await asyncio.sleep(1)  # 避免发送过快
                except Exception as e:
                    logger.error(f"发送分段消息失败: {e}")
    else:  # 单条消息
        await send_single_message(
            bot, chat_id, message_content,
            disable_web_page_preview=disable_web_page_preview
        )

# 修改批量发送函数中的调用
async def process_batch_send(group, db: RSSDatabase):
    group_key = group["group_key"]
    bot_token = group["bot_token"]
    processor = group["processor"]
    batch_interval = group.get("batch_send_interval")
    
    if not batch_interval:
        return
        
    now = datetime.now(pytz.utc).timestamp()
    last_batch_sent = await db.get_last_batch_sent_time(group_key)
    if now - last_batch_sent < batch_interval:
        return
        
    pending = await db.get_pending_messages(group_key)
    if not pending:
        await db.save_last_batch_sent_time(group_key, now)
        return

    # 按 feed_url 分组消息
    feed_url_to_msgs = defaultdict(list)
    for row in pending:
        feed_url_to_msgs[row["feed_url"]].append(row)

    bot = Bot(token=bot_token)
    sent_entry_ids = []
    
    for feed_url, msgs in feed_url_to_msgs.items():
        feed_title = (msgs[0].get("feed_title") or group.get("name") or feed_url)
        
        # 创建模拟的feed和entry对象
        class DummyFeed:
            feed = {'title': feed_title}
            
        class Entry:
            def __init__(self, row):
                self.title = row["translated_title"] or row["title"]
                self.link = row["link"]
                self.summary = row.get("summary", "") or ""  # ✅ 新增摘要支持
        entries = [Entry(row) for row in msgs]
        
        try:
            # 生成消息内容
            feed_message = await generate_group_message(
                DummyFeed, entries, {**processor, "translate": False}
            )
            
            if feed_message:
                # 发送消息（支持分段）
                await send_batch_messages(
                    bot,
                    TELEGRAM_CHAT_ID[0],
                    feed_message,
                    disable_web_page_preview=not processor.get("preview", True)
                )
                # 记录已发送的消息ID
                sent_entry_ids.extend([row["entry_id"] for row in msgs])
                
        except Exception as e:
            logger.error(f"批量推送失败[{group_key}-{feed_url}]: {e}")
    
    # 标记已发送的消息
    if sent_entry_ids:
        await db.mark_pending_as_sent(group_key, sent_entry_ids)
    
    await db.save_last_batch_sent_time(group_key, now)

# ========== 组采集（采集但可选择是否立即推送） ==========
async def process_group(session, group_config, global_status, db: RSSDatabase):
    """处理单个RSS组"""
    try:  # ✅ 添加异常捕获
        group_name = group_config["name"]
        group_key = group_config["group_key"]
        processor = group_config["processor"]
        bot_token = group_config["bot_token"]
        batch_send_interval = group_config.get("batch_send_interval", None)
        send_separately = group_config.get("send_separately", False)
        
        try:
            last_run = await db.load_last_run_time(group_key)
            now = datetime.now(pytz.utc).timestamp()
            if (now - last_run) < group_config["interval"]:
                return
                
            bot = Bot(token=bot_token)
            for index, feed_url in enumerate(group_config["urls"]):
                try:
                    if index > 0:
                        await asyncio.sleep(1)
                        
                    feed_data, canonical_url = await fetch_feed(session, feed_url)
                    if not feed_data or not feed_data.entries:
                        continue
                        
                    processed_ids = global_status.get(canonical_url, set())
                    new_entries = []
                    seen_in_batch = set()
                    new_hashes_in_batch = set()  # 当前批次的内容哈希去重

                    for entry in feed_data.entries:
                        # 直接使用RSSHub返回的原始链接，不需要修改
                        entry_id = get_entry_identifier(entry)
                        content_hash = get_entry_content_hash(entry)
                        
                        # 统一使用内容哈希去重（主要修复）
                        if await db.has_content_hash(group_key, content_hash):
                            logger.debug(f"跳过重复内容哈希: {content_hash[:16]}...")
                            continue
                            
                        if entry_id in processed_ids or entry_id in seen_in_batch:
                            logger.debug(f"跳过重复条目ID: {entry_id[:16]}...")
                            continue
                            
                        # 在当前批次中也用内容哈希去重
                        if content_hash in new_hashes_in_batch:
                            logger.debug(f"跳过批次内重复内容哈希: {content_hash[:16]}...")
                            continue  
                            
                        # ✅ 过滤检查
                        if not await should_send_entry(entry, processor):
                            logger.debug(f"跳过不符合过滤条件的条目: {getattr(entry, 'title', '无标题')[:50]}")
                            continue

                        seen_in_batch.add(entry_id)
                        new_hashes_in_batch.add(content_hash)
                        new_entries.append((entry, content_hash, entry_id))
                                            
                    if new_entries:
                        if batch_send_interval and not send_separately:
                            # 批量发送模式：存入待发送队列
                            for entry, content_hash, entry_id in new_entries:
                                raw_subject = remove_html_tags(getattr(entry, "title", "") or "")
                                if processor.get("translate", False) and is_need_translate(raw_subject):
                                    translated_subject = await auto_translate_text(raw_subject)
                                else:
                                    translated_subject = raw_subject
                                    
                                await db.add_pending_message(
                                    group_key, 
                                    canonical_url, 
                                    entry_id, 
                                    content_hash,
                                    getattr(entry, "title", ""), 
                                    translated_subject, 
                                    getattr(entry, "link", ""), 
                                    getattr(entry, "summary", ""),
                                    get_entry_timestamp(entry).timestamp() if get_entry_timestamp(entry) else time.time(),
                                    feed_data.feed.get('title', "") 
                                )
                                await db.save_status(group_key, canonical_url, entry_id, content_hash, time.time())
                                processed_ids.add(entry_id)
                                
                            global_status[canonical_url] = processed_ids
                        elif send_separately:
                            # 单独发送模式：每条消息单独发送
                            messages_data = await generate_single_messages(
                                feed_data, 
                                [e for e,_,_ in new_entries], 
                                processor
                            )
                            
                            if messages_data:
                                sent_count = await send_single_messages_separately(
                                    bot,
                                    TELEGRAM_CHAT_ID[0],
                                    messages_data,
                                    processor
                                )
                                
                                # 保存已发送的消息状态
                                for i, (entry, content_hash, entry_id) in enumerate(new_entries):
                                    if i < sent_count:  # 只保存成功发送的消息
                                        await db.save_status(group_key, canonical_url, entry_id, content_hash, time.time())
                                        processed_ids.add(entry_id)
                                global_status[canonical_url] = processed_ids
                                
                                if processor.get("show_count", False):
                                    summary_msg = f"✅ {feed_data.feed.get('title', '未知来源')} 新增 {sent_count} 条内容"
                                    try:
                                        await send_single_message(
                                            bot,
                                            TELEGRAM_CHAT_ID[0],
                                            summary_msg,
                                            disable_web_page_preview=True
                                        )
                                    except:
                                        pass
                        else:
                            # 立即批量发送模式（原来的逻辑）
                            feed_message = await generate_group_message(feed_data, [e for e,_,_ in new_entries], processor)
                            if feed_message:
                                try:
                                    await send_single_message(
                                        bot,
                                        TELEGRAM_CHAT_ID[0],
                                        feed_message,
                                        disable_web_page_preview=not processor.get("preview", True)
                                    )
                                    for entry, content_hash, entry_id in new_entries:
                                        await db.save_status(group_key, canonical_url, entry_id, content_hash, time.time())
                                        processed_ids.add(entry_id)
                                    global_status[canonical_url] = processed_ids
                                except Exception as send_error:
                                    logger.error(f"❌ 发送消息失败 [{feed_url}]: {send_error}")
                                    raise
                                    
                except Exception as e:
                    logger.error(f"❌ 处理失败 [{feed_url}]: {e}")
                    continue  # ✅ 单个feed失败不影响其他feed
                    
            await db.save_last_run_time(group_key, now)
            
        except Exception as e:
            logger.critical(f"‼️ 处理组失败 [{group_key}]: {e}")
            raise  # ✅ 重新抛出，让调用方知道失败
            
    except Exception as e:  # ✅ 最外层异常捕获
        logger.error(f"❌ 组处理失败 [{group_config.get('name', '未知')}]: {e}", exc_info=True)
        raise  # ✅ 重新抛出，让上层知道失败

async def main():
    clean_old_log() 
    logger.info("🚀 RSS Bot 开始执行")
    
    start_time = time.time()
    max_retries = 3
    
    for attempt in range(max_retries):
        try:
            await run_main_logic()
            logger.info(f"✅ RSS Bot 执行完成，耗时: {time.time() - start_time:.2f}秒")
            return  # 成功就退出
            
        except Exception as e:
            logger.error(f"运行失败 (尝试 {attempt + 1}/{max_retries}): {e}", exc_info=True)
            if attempt < max_retries - 1:
                await asyncio.sleep(60)
            else:
                logger.critical("达到最大重试次数，程序退出")

async def run_main_logic():
    lock_file = None
    db = RSSDatabase()
    
    try:
        # 获取文件锁
        lock_file = open(LOCK_FILE, "w")
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        logger.info("🔒 成功获取文件锁")
        
        # 连接数据库（加超时）
        logger.info("🔗 正在连接数据库...")
        await asyncio.wait_for(db.open(), timeout=30)
        await db.ensure_initialized()
        logger.info("✅ 数据库连接成功")
        
        # 清理历史记录（每个组独立，失败不影响其他）
        for group in RSS_GROUPS:
            try:
                days = group.get("history_days", 30)
                await db.cleanup_history(days, group["group_key"])
            except Exception as e:
                logger.error(f"清理历史失败 [{group.get('name')}]: {e}")
        
        # 主处理
        logger.info("🚀 开始处理 RSS 订阅...")
        async with aiohttp.ClientSession() as session:
            status = await db.load_status()
            tasks = []
            
            for group in RSS_GROUPS:
                tasks.append(process_group(session, group, status, db))
            
            # 所有组并行处理，某个失败不影响其他
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # 记录失败
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(f"组 {RSS_GROUPS[i].get('name')} 失败: {result}")
            
            # 批量发送（同样容错）
            batch_tasks = []
            for group in RSS_GROUPS:
                if group.get("batch_send_interval"):
                    batch_tasks.append(process_batch_send(group, db))
            
            if batch_tasks:
                results = await asyncio.gather(*batch_tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, Exception):
                        logger.error(f"批量发送失败: {result}")
        
    except asyncio.TimeoutError:
        logger.error("❌ 数据库连接超时")
        raise
    except fcntl.error as e:
        logger.error(f"❌ 获取文件锁失败: {e}")
        raise
    except Exception as e:
        logger.error(f"主逻辑异常: {str(e)}", exc_info=True)
        raise
    finally:
        # 清理资源
        try:
            if db:
                await db.close()
                logger.debug("数据库连接已关闭")
        except Exception as e:
            logger.error(f"关闭数据库失败: {e}")
        
        try:
            if lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                lock_file.close()
                if LOCK_FILE.exists():
                    LOCK_FILE.unlink()
                    logger.debug("文件锁已释放")
        except Exception as e:
            logger.error(f"释放文件锁失败: {e}")

if __name__ == "__main__":
    for s in (signal.SIGINT, signal.SIGTERM):
        signal.signal(s, signal_handler)
    try:
        asyncio.run(main())
    except Exception as e:
        logger.critical(f"‼️ 主进程未捕获异常: {str(e)}", exc_info=True)
        sys.exit(1)