#source rss_venv/bin/activate
#pip install aiohttp pytz aiosqlite python-dotenv feedparser python-telegram-bot tenacity md2tgmd langdetect
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
semaphore = asyncio.Semaphore(2)
BACKUP_DOMAINS_STR = os.getenv("BACKUP_DOMAINS", "")
BACKUP_DOMAINS = [domain.strip() for domain in BACKUP_DOMAINS_STR.split(",") if domain.strip()]
LIBRETRANSLATE_URL = os.getenv("LIBRETRANSLATE_URL")
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

    async def create_tables(self):
        """改进的建表语句，确保 PostgreSQL 和 SQLite 索引一致"""
        if USE_PG:
            async with self.pg_pool.acquire() as conn:
                # 主表（新字段：entry_link_hash + entry_title_hash）
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS rss_status (
                        feed_group TEXT,
                        feed_url TEXT,
                        entry_url TEXT,
                        entry_link_hash TEXT,
                        entry_title_hash TEXT,
                        entry_timestamp DOUBLE PRECISION,
                        PRIMARY KEY (feed_group, feed_url, entry_url)
                    );
                """)
                # 链接哈希唯一索引
                await conn.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_group_link_hash 
                    ON rss_status(feed_group, entry_link_hash);
                """)
                # 标题哈希唯一索引
                await conn.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_group_title_hash 
                    ON rss_status(feed_group, entry_title_hash);
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
                        link_hash TEXT,
                        title_hash TEXT,
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
                        entry_link_hash TEXT,
                        entry_title_hash TEXT,
                        entry_timestamp REAL,
                        PRIMARY KEY (feed_group, feed_url, entry_url)
                    )""")
                await c.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_group_link_hash 
                    ON rss_status(feed_group, entry_link_hash);
                """)
                await c.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_group_title_hash 
                    ON rss_status(feed_group, entry_title_hash);
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
                        link_hash TEXT,
                        title_hash TEXT,
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

    async def add_pending_message(self, feed_group, feed_url, entry_id, content_hash, link_hash, title_hash, title, translated_title, link, summary, timestamp, feed_title):
        if USE_PG:
            async with self.pg_pool.acquire() as conn:
                await conn.execute("""
                INSERT INTO pending_messages (feed_group, feed_url, entry_id, content_hash, link_hash, title_hash, title, translated_title, link, summary, entry_timestamp, sent, feed_title)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, 0, $12)
                ON CONFLICT DO NOTHING
                """, feed_group, feed_url, entry_id, content_hash, link_hash, title_hash, title, translated_title, link, summary, timestamp, feed_title)
        else:
            async with self.conn.cursor() as c:
                await c.execute("""
                    INSERT OR IGNORE INTO pending_messages
                    (feed_group, feed_url, entry_id, content_hash, link_hash, title_hash, title, translated_title, link, summary, entry_timestamp, sent, feed_title)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                """, (feed_group, feed_url, entry_id, content_hash, link_hash, title_hash, title, translated_title, link, summary, timestamp, feed_title))
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

    async def batch_save_status(self, records):
        """批量写入，失败时降级为单个写入"""
        if not records:
            return
        
        if USE_PG:
            async with self.pg_pool.acquire() as conn:
                try:
                    await conn.executemany("""
                        INSERT INTO rss_status (feed_group, feed_url, entry_url, entry_link_hash, entry_title_hash, entry_timestamp) 
                        VALUES ($1, $2, $3, $4, $5, $6) 
                        ON CONFLICT (feed_group, feed_url, entry_url) 
                        DO UPDATE SET 
                            entry_link_hash = EXCLUDED.entry_link_hash,
                            entry_title_hash = EXCLUDED.entry_title_hash,
                            entry_timestamp = EXCLUDED.entry_timestamp
                    """, records)
                #    logger.warning(f"💾 [批量写入成功] 共 {len(records)} 条")
                except Exception as e:
                    logger.warning(f"⚠️ [批量写入失败] {len(records)} 条 | error={e}，降级为单个写入")
                    # ✅ 降级：逐条写入
                    success_count = 0
                    fail_count = 0
                    for record in records:
                        try:
                            await conn.execute("""
                                INSERT INTO rss_status (feed_group, feed_url, entry_url, entry_link_hash, entry_title_hash, entry_timestamp) 
                                VALUES ($1, $2, $3, $4, $5, $6) 
                                ON CONFLICT (feed_group, feed_url, entry_url) 
                                DO UPDATE SET 
                                    entry_link_hash = EXCLUDED.entry_link_hash,
                                    entry_title_hash = EXCLUDED.entry_title_hash,
                                    entry_timestamp = EXCLUDED.entry_timestamp
                            """, *record)
                            success_count += 1
                        except Exception as single_error:
                            fail_count += 1
                            logger.error(f"❌ [单条写入失败] {record[2][:12]} | error={single_error}")
                    
                    logger.warning(f"💾 [降级写入完成] 成功 {success_count} 条，失败 {fail_count} 条")
        else:
            async with self.conn.cursor() as c:
                try:
                    await c.executemany(
                        "INSERT OR REPLACE INTO rss_status VALUES (?, ?, ?, ?, ?, ?)",
                        records
                    )
                    await self.conn.commit()
                #  logger.warning(f"💾 [批量写入成功] 共 {len(records)} 条")
                except Exception as e:
                    logger.warning(f"⚠️ [批量写入失败] {len(records)} 条 | error={e}，降级为单个写入")
                    # ✅ 降级：逐条写入
                    success_count = 0
                    fail_count = 0
                    for record in records:
                        try:
                            await c.execute(
                                "INSERT OR REPLACE INTO rss_status VALUES (?, ?, ?, ?, ?, ?)",
                                record
                            )
                            success_count += 1
                        except Exception as single_error:
                            fail_count += 1
                            logger.error(f"❌ [单条写入失败] {record[2][:12]} | error={single_error}")
                    
                    await self.conn.commit()
                    logger.warning(f"💾 [降级写入完成] 成功 {success_count} 条，失败 {fail_count} 条")

    async def batch_has_hashes(self, feed_group, link_hashes, title_hashes=None):
        """批量查询：查 link_hash，如果 title_hashes 非空也查 title_hash"""
        existing_links = set()
        existing_titles = set()
        
        if not link_hashes:
            return existing_links, existing_titles
        
        if USE_PG:
            async with self.pg_pool.acquire() as conn:
                # 查 link_hash
                rows = await conn.fetch(
                    "SELECT entry_link_hash FROM rss_status WHERE feed_group=$1 AND entry_link_hash = ANY($2)",
                    feed_group, list(link_hashes)
                )
                existing_links = {row['entry_link_hash'] for row in rows}
                
                # 查 title_hash（如果有）
                if title_hashes:
                    rows = await conn.fetch(
                        "SELECT entry_title_hash FROM rss_status WHERE feed_group=$1 AND entry_title_hash = ANY($2)",
                        feed_group, list(title_hashes)
                    )
                    existing_titles = {row['entry_title_hash'] for row in rows if row['entry_title_hash']}
        else:
            async with self.conn.cursor() as c:
                placeholders = ','.join('?' * len(link_hashes))
                await c.execute(
                    f"SELECT entry_link_hash FROM rss_status WHERE feed_group=? AND entry_link_hash IN ({placeholders})",
                    [feed_group] + list(link_hashes)
                )
                existing_links = {row[0] for row in await c.fetchall()}
                
                if title_hashes:
                    placeholders = ','.join('?' * len(title_hashes))
                    await c.execute(
                        f"SELECT entry_title_hash FROM rss_status WHERE feed_group=? AND entry_title_hash IN ({placeholders})",
                        [feed_group] + list(title_hashes)
                    )
                    existing_titles = {row[0] for row in await c.fetchall() if row[0]}
        
        return existing_links, existing_titles

    async def load_status(self):
        if USE_PG:
            async with self.pg_pool.acquire() as conn:
                rows = await conn.fetch("SELECT feed_group, feed_url, entry_url FROM rss_status")
                status = {}
                
                for row in rows:
                    feed_group, feed_url, entry_url = row['feed_group'], row['feed_url'], row['entry_url']
                    
                    # 1. 按 feed_url 去重（entry_url）
                    status.setdefault(feed_url, set()).add(entry_url)
                    
                    # 2. 整组共享去重（entry_url）
                    group_key = f"group_{feed_group}"
                    status.setdefault(group_key, set()).add(entry_url)
                
                return status
        else:  # SQLite
            async with self.conn.cursor() as c:
                await c.execute("SELECT feed_group, feed_url, entry_url FROM rss_status")
                rows = await c.fetchall()
                status = {}
                
                for feed_group, feed_url, entry_url in rows:
                    status.setdefault(feed_url, set()).add(entry_url)
                    status.setdefault(f"group_{feed_group}", set()).add(entry_url)
                
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

    async def cleanup_history(self, feed_group, shared_dedup=False, keep_count=100):
        """清理历史数据：保留最近 keep_count 条"""
        now = time.time()
        
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

                # 1. 清理 rss_status（保留 keep_count 条）
                if shared_dedup:
                    # 整组共享：用 entry_url 唯一判断
                    await conn.execute(f"""
                        DELETE FROM rss_status
                        WHERE (feed_group, entry_url) NOT IN (
                            SELECT feed_group, entry_url
                            FROM rss_status AS s2
                            WHERE s2.feed_group = rss_status.feed_group
                            ORDER BY s2.entry_timestamp DESC, s2.entry_url DESC
                            LIMIT {keep_count}
                        );
                    """)
                else:
                    # 每个源独立：用 entry_url 唯一判断
                    await conn.execute(f"""
                        DELETE FROM rss_status
                        WHERE (feed_group, feed_url, entry_url) NOT IN (
                            SELECT feed_group, feed_url, entry_url
                            FROM rss_status AS s2
                            WHERE s2.feed_group = rss_status.feed_group
                            AND s2.feed_url = rss_status.feed_url
                            ORDER BY s2.entry_timestamp DESC, s2.entry_url DESC
                            LIMIT {keep_count}
                        );
                    """)

                # 2. 已发送的立即删除（不保留）
                await conn.execute(
                    """
                    DELETE FROM pending_messages 
                    WHERE feed_group = $1 
                    AND sent = 1
                    """,
                    feed_group
                )

                # 3. 未发送超过3天，强制标记为已发送（记录日志）
                expired_rows = await conn.fetch(
                    """
                    SELECT entry_id, feed_url, title, translated_title, link, 
                           entry_timestamp, feed_title
                    FROM pending_messages 
                    WHERE feed_group = $1 
                    AND sent = 0 
                    AND entry_timestamp < $2
                    """,
                    feed_group, now - 3 * 86400
                )
                
                if expired_rows:
                    logger.warning(
                        f"🗑️ [清理过期未发送] 组={feed_group} | "
                        f"共 {len(expired_rows)} 条超过3天仍未发送成功，强制标记为已发送"
                    )
                    for row in expired_rows:
                        age_days = (now - row['entry_timestamp']) / 86400
                        expired_dt = datetime.fromtimestamp(
                            row['entry_timestamp'], tz=pytz.utc
                        ).strftime('%Y-%m-%d %H:%M:%S')
                        logger.warning(
                            f"   ❌ 发送失败 | 组={feed_group} | "
                            f"源={row['feed_title'] or row['feed_url']} | "
                            f"标题={row['translated_title'] or row['title']} | "
                            f"链接={row['link']} | "
                            f"发布时间={expired_dt} (UTC) | "
                            f"滞留={age_days:.1f}天 | "
                            f"entry_id={row['entry_id'][:12]}"
                        )
                
                await conn.execute(
                    """
                    UPDATE pending_messages 
                    SET sent = 1 
                    WHERE feed_group = $1 
                    AND sent = 0 
                    AND entry_timestamp < $2
                    """,
                    feed_group, now - 3 * 86400
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

                # 1. 清理 rss_status（保留 keep_count 条）
                if shared_dedup:
                    await c.execute(f"""
                        DELETE FROM rss_status
                        WHERE rowid NOT IN (
                            SELECT rowid
                            FROM rss_status AS s2
                            WHERE s2.feed_group = rss_status.feed_group
                            ORDER BY s2.entry_timestamp DESC, s2.rowid DESC
                            LIMIT {keep_count}
                        );
                    """)
                else:
                    await c.execute(f"""
                        DELETE FROM rss_status
                        WHERE rowid NOT IN (
                            SELECT rowid
                            FROM rss_status AS s2
                            WHERE s2.feed_group = rss_status.feed_group
                            AND s2.feed_url = rss_status.feed_url
                            ORDER BY s2.entry_timestamp DESC, s2.rowid DESC
                            LIMIT {keep_count}
                        );
                    """)

                # 2. 已发送的立即删除（不保留）
                await c.execute(
                    """
                    DELETE FROM pending_messages 
                    WHERE feed_group = ? 
                    AND sent = 1
                    """,
                    (feed_group,)
                )

                # 3. 未发送超过3天，强制标记为已发送（记录日志）
                await c.execute(
                    """
                    SELECT entry_id, feed_url, title, translated_title, link, 
                           entry_timestamp, feed_title
                    FROM pending_messages 
                    WHERE feed_group = ? 
                    AND sent = 0 
                    AND entry_timestamp < ?
                    """,
                    (feed_group, now - 3 * 86400)
                )
                expired_rows = await c.fetchall()
                
                if expired_rows:
                    logger.warning(
                        f"🗑️ [清理过期未发送] 组={feed_group} | "
                        f"共 {len(expired_rows)} 条超过3天仍未发送成功，强制标记为已发送"
                    )
                    for row in expired_rows:
                        (entry_id, feed_url, title, translated_title, 
                         link, entry_timestamp, feed_title) = row
                        age_days = (now - entry_timestamp) / 86400
                        expired_dt = datetime.fromtimestamp(
                            entry_timestamp, tz=pytz.utc
                        ).strftime('%Y-%m-%d %H:%M:%S')
                        logger.warning(
                            f"   ❌ 发送失败 | 组={feed_group} | "
                            f"源={feed_title or feed_url} | "
                            f"标题={translated_title or title} | "
                            f"链接={link} | "
                            f"发布时间={expired_dt} (UTC) | "
                            f"滞留={age_days:.1f}天 | "
                            f"entry_id={entry_id[:12]}"
                        )
                
                await c.execute(
                    """
                    UPDATE pending_messages 
                    SET sent = 1 
                    WHERE feed_group = ? 
                    AND sent = 0 
                    AND entry_timestamp < ?
                    """,
                    (feed_group, now - 3 * 86400)
                )

                # 更新清理时间戳
                await c.execute(
                    "INSERT OR REPLACE INTO cleanup_timestamps (feed_group, last_cleanup_time) VALUES (?, ?)",
                    (feed_group, now)
                )
                await self.conn.commit()

# ========== 业务逻辑 ==========
def protect_special_content(text):
    """翻译前：把不该被翻译/会被破坏的内容替换成占位符
    返回 (protected_text, placeholders_dict)
    注：只用于 RSS 标题（单行），不处理换行
    """
    if not text:
        return text, {}

    placeholders = {}
    counter = [0]

    def protect(m):
        key = f"⟦{counter[0]}⟧"
        placeholders[key] = m.group(0)
        counter[0] += 1
        return key

    # ---- 1. URL ----
    text = re.sub(r'https?://[^\s<>"\']+', protect, text)

    # ---- 2. 带空格文件名（如 balenaEtcher-2.1.7 Setup.exe） ----
    text = re.sub(
        r'\b[\w][\w.-]*\s+[A-Z][\w.-]*\.(?:exe|msi|dmg|pkg|rpm|deb|zip)\b',
        protect, text, flags=re.IGNORECASE
    )

    # ---- 3. 普通文件名 ----
    text = re.sub(
        r'\b[\w][\w.-]*\.(?:rpm|deb|dmg|exe|zip|tar\.gz|tgz|txt|json|AppImage|snap|msi|pkg|apk|7z|gz|bz2|xz)\b',
        protect, text, flags=re.IGNORECASE
    )

    # ---- 4. SHA256SUMS ----
    text = re.sub(r'\bSHA256SUMS\b', protect, text)

    # ---- 5. owner/repo ----
    text = re.sub(r'(?<=[\s>])[\w.-]+/[\w.-]+(?=[\s<])', protect, text)

    # ---- 6. 版本号 ----
    text = re.sub(r'\bv\d+\.\d+\.\d+\b', protect, text)

    # ---- 7. commit hash ----
    text = re.sub(r'\b(?=[0-9a-f]*\d)[0-9a-f]{7,40}\b', protect, text)

    # ---- 8. 翻译会破坏的字符：; _ \ $ | ----
    text = re.sub(r"[;_\\$|]", protect, text)

    return text, placeholders


def restore_special_content(text, placeholders):
    if not text:
        return text
    for key, val in placeholders.items():
        text = text.replace(key, val)
    return text

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

def get_entry_hashes(entry, title_dedup=False):
    """返回 (link_hash, title_hash)
    - title_dedup=False: title_hash 返回 None
    - title_dedup=True: 两个都返回
    """
    # 链接哈希（始终计算）
    link = getattr(entry, 'link', '') or ''
    try:
        parsed = urlparse(link)
        clean_link = parsed._replace(query=None, fragment=None).geturl().lower()
    except Exception:
        clean_link = link.lower()
    link_hash = hashlib.sha256(clean_link.encode('utf-8')).hexdigest()
    
    # 标题哈希（可选）
    title_hash = None
    if title_dedup:
        title = getattr(entry, 'title', '') or ''
        title_hash = hashlib.sha256(title.strip().encode('utf-8')).hexdigest()
    
    return link_hash, title_hash

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
    stop=stop_after_attempt(2),
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
   # logger.debug(f"[关键词过滤] 范围: {scope} | 标题: {title[:50]} | 链接: {link[:50]} | 关键词: {keywords} | 模式: {mode} | 命中: {has_keyword}")
    
    # 根据模式决定是否发送
    if not keywords:  # 如果没有关键词，根据模式决定
        return mode != "allow"
    elif mode == "allow":
        return has_keyword
    elif mode == "block":
        return not has_keyword
    else:
        return True
    
# ========== LibreTranslate 翻译 ==========
async def translate_with_libretranslate(text):
    """使用 LibreTranslate 翻译（备用）"""
    if not text or len(text.strip()) < 3:
        return text
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                LIBRETRANSLATE_URL,
                json={"q": text, "source": "auto", "target": "zh"},
                timeout=10
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    translated = result.get("translatedText")
                    if translated and translated != text:
                        logger.info("✅ LibreTranslate 翻译成功")
                        return translated
                    else:
                        logger.warning("⚠️ LibreTranslate 返回空或相同文本")
                else:
                    logger.warning(f"⚠️ LibreTranslate 返回状态码: {response.status}")
    except asyncio.TimeoutError:
        logger.warning("⚠️ LibreTranslate 请求超时")
    except aiohttp.ClientError as e:
        logger.warning(f"⚠️ LibreTranslate 网络错误: {e}")
    except Exception as e:
        logger.warning(f"⚠️ LibreTranslate 翻译失败: {e}")
    
    return None  # 返回 None 表示失败，让调用方尝试备用

# ========== DeepL 翻译 ==========
async def translate_with_deepl(text):
    """使用 DeepL 翻译（首选）"""
    if not text or len(text.strip()) < 3:
        return None
    
    DEEPL_API_KEY = os.getenv("DEEPL_API_KEY")
    if not DEEPL_API_KEY:
        logger.warning("⚠️ DeepL API Key 未配置")
        return None
    
    # DeepL 语言代码映射
    lang_map = {
        'zh': 'ZH',
        'en': 'EN',
        'ja': 'JA',
        'ko': 'KO',
        'ru': 'RU',
        'fr': 'FR',
        'de': 'DE',
        'es': 'ES',
        'it': 'IT',
        'pt': 'PT',
        'nl': 'NL',
        'pl': 'PL',
    }
    
    # 检测源语言（用于提高准确率）
    try:
        detected_lang = detect(text)
        source_lang = lang_map.get(detected_lang, None)
    except:
        source_lang = None
    
    try:
        async with aiohttp.ClientSession() as session:
            payload = {
                "text": [text],
                "target_lang": "ZH"
            }
            if source_lang:
                payload["source_lang"] = source_lang
            
            async with session.post(
                os.getenv("DEEPL_API_URL", "https://api-free.deepl.com/v2/translate"),
                json=payload,
                headers={
                    "Authorization": f"DeepL-Auth-Key {DEEPL_API_KEY}",
                    "Content-Type": "application/json"
                },
                timeout=10
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    translated = result.get("translations", [{}])[0].get("text")
                    if translated and translated != text:
                        logger.info("✅ DeepL 翻译成功")
                        return translated
                    else:
                        logger.warning("⚠️ DeepL 返回空或相同文本")
                        return None
                else:
                    logger.warning(f"⚠️ DeepL 返回状态码: {response.status}")
                    return None
    except asyncio.TimeoutError:
        logger.warning("⚠️ DeepL 请求超时")
        return None
    except aiohttp.ClientError as e:
        logger.warning(f"⚠️ DeepL 网络错误: {e}")
        return None
    except Exception as e:
        logger.warning(f"⚠️ DeepL 翻译失败: {e}")
        return None

@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def _translate_raw(text):
    """底层翻译：文本已由外层清理和保护，这里只负责调翻译服务"""
    cleaned_text = text.strip()

    if not cleaned_text:
        return cleaned_text

    # 第一优先级：DeepL（质量好）
    try:
        translated = await translate_with_deepl(cleaned_text)
        if translated is not None:
            return translated
    except Exception as e:
        logger.warning(f"DeepL 失败: {e}")

    # 第二优先级：LibreTranslate（备用）
    try:
        translated = await translate_with_libretranslate(cleaned_text)
        if translated is not None:
            return translated
    except Exception as e:
        logger.warning(f"LibreTranslate 失败: {e}")

    logger.info("ℹ️ 所有翻译服务均失败，返回原文")
    return cleaned_text

# ========== 翻译主函数（DeepL → LibreTranslate → 原文） ==========
async def auto_translate_text(text):
    """翻译前保护 → 翻译 → 还原"""
    if not text or not text.strip():
        return text

    cleaned = remove_html_tags(text).strip()

    if len(cleaned) <= 3 or is_mostly_symbols(cleaned):
        return cleaned

    # 1. 占位符保护
    protected, placeholders = protect_special_content(cleaned)
    logger.warning(f"🔍 翻译前: {repr(protected)}")       # 👈 加

    translated = await _translate_raw(protected)
   # logger.warning(f"🔍 翻译后: {repr(translated)}")      # 👈 加

    restored = restore_special_content(translated, placeholders)
    logger.warning(f"🔍 还原后: {repr(restored)}")        # 👈 加
    return restored

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
    
    MAX_RETRY_COUNT = 3
    timeout_seconds = batch_interval * MAX_RETRY_COUNT
    
    now = datetime.now(pytz.utc).timestamp()
    last_batch_sent = await db.get_last_batch_sent_time(group_key)
    if now - last_batch_sent < batch_interval:
        return
    
    pending = await db.get_pending_messages(group_key)
    if not pending:
        await db.save_last_batch_sent_time(group_key, now)
        return

    timeout_cutoff = now - timeout_seconds

    # 按 feed_url 分组消息
    feed_url_to_msgs = defaultdict(list)
    for row in pending:
        feed_url_to_msgs[row["feed_url"]].append(row)

    bot = Bot(token=bot_token)
    sent_entry_ids = []
    force_sent_entry_ids = []

    for feed_url, msgs in feed_url_to_msgs.items():
        feed_title = (msgs[0].get("feed_title") or group.get("name") or feed_url)
        
        class DummyFeed:
            feed = {'title': feed_title}
            
        class Entry:
            def __init__(self, row):
                self.title = row["translated_title"] or row["title"]
                self.link = row["link"]
                self.summary = row.get("summary", "") or ""
        entries = [Entry(row) for row in msgs]
        
        try:
            feed_message = await generate_group_message(
                DummyFeed, entries, {**processor, "translate": False}
            )
            
            if feed_message:
                await send_batch_messages(
                    bot,
                    TELEGRAM_CHAT_ID[0],
                    feed_message,
                    disable_web_page_preview=not processor.get("preview", True)
                )
                # ✅ 发送成功 → 记录
                sent_entry_ids.extend([row["entry_id"] for row in msgs])
                
                # ✅ 关键改动：发送成功后，批量写入 rss_status
                records = [
                    (group_key, row["feed_url"], row["entry_id"], 
                    row["link_hash"], row["title_hash"], time.time())
                    for row in msgs
                ]
                await db.batch_save_status(records)
                
        except Exception as e:
            logger.error(f"批量推送失败[{group_key}-{feed_url}]: {e}")
            
            for row in msgs:
                if row["entry_timestamp"] < timeout_cutoff:
                    force_sent_entry_ids.append(row["entry_id"])
                    age_hours = (now - row["entry_timestamp"]) / 3600
                    pub_dt = datetime.fromtimestamp(
                        row["entry_timestamp"], tz=pytz.utc
                    ).strftime('%Y-%m-%d %H:%M:%S')
                    logger.warning(
                        f"   ❌ 发送失败(批量) | 组={group_key} | "
                        f"源={row.get('feed_title') or feed_url} | "
                        f"标题={row.get('translated_title') or row.get('title')} | "
                        f"链接={row.get('link')} | "
                        f"发布时间={pub_dt} (UTC) | "
                        f"滞留={age_hours:.1f}小时 | "
                        f"entry_id={row['entry_id'][:12]}"
                    )
    
    # 标记成功发送的
    if sent_entry_ids:
        await db.mark_pending_as_sent(group_key, sent_entry_ids)
    
    # 标记强制放弃的
    if force_sent_entry_ids:
        await db.mark_pending_as_sent(group_key, force_sent_entry_ids)
    
    await db.save_last_batch_sent_time(group_key, now)
        
# ========== 组采集（采集但可选择是否立即推送） ==========
async def process_group(session, group_config, global_status, db: RSSDatabase):
    """处理单个RSS组"""
    try:
        group_name = group_config["name"]
        group_key = group_config["group_key"]
        processor = group_config["processor"]
        bot_token = group_config["bot_token"]
        batch_send_interval = group_config.get("batch_send_interval", None)
        send_separately = group_config.get("send_separately", False)
        shared_dedup = group_config.get("shared_dedup", False)
        title_dedup = group_config.get("title_dedup", False)
        
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
                    
                    # ✅ 1. 计算哈希
                    entries_with_hashes = []
                    for entry in feed_data.entries:
                        entry_id = get_entry_identifier(entry)
                        link_hash, title_hash = get_entry_hashes(entry, title_dedup)
                        entries_with_hashes.append((entry, link_hash, title_hash, entry_id))
                    
                    # ✅ 2. 批量查询
                    all_link_hashes = [lh for _, lh, _, _ in entries_with_hashes]
                    all_title_hashes = [th for _, _, th, _ in entries_with_hashes if th] if title_dedup else None
                    existing_links, existing_titles = await db.batch_has_hashes(
                        group_key, all_link_hashes, all_title_hashes
                    )
                    
                    # ✅ 3. 过滤
                    new_entries = []
                    seen_links = set()
                    seen_titles = set()
                    for entry, link_hash, title_hash, entry_id in entries_with_hashes:
                        if link_hash in existing_links:
                  #          logger.warning(f"✅ 命中 link 重复: {link_hash[:12]}")
                            continue
                        if title_dedup and title_hash and title_hash in existing_titles:
                  #          logger.warning(f"✅ 命中 title 重复: {title_hash[:12]}")
                            continue
                        if link_hash in seen_links:
                            continue
                        if title_dedup and title_hash and title_hash in seen_titles:
                            continue
                        if not await should_send_entry(entry, processor):
                            continue
                        seen_links.add(link_hash)
                        if title_hash:
                            seen_titles.add(title_hash)
                        new_entries.append((entry, link_hash, title_hash, entry_id))
                    
                    # ✅ 4. 发送 + 批量写入
                    if new_entries:
                        if batch_send_interval and not send_separately:
                            # 批量发送模式
                            for entry, link_hash, title_hash, entry_id in new_entries:
                                raw_subject = remove_html_tags(getattr(entry, "title", "") or "")
                                if processor.get("translate", False) and is_need_translate(raw_subject):
                                    translated_subject = await auto_translate_text(raw_subject)
                                else:
                                    translated_subject = raw_subject
                                    
                                await db.add_pending_message(
                                    group_key, canonical_url, entry_id, 
                                    link_hash,   # content_hash（兼容旧字段）
                                    link_hash,   # link_hash
                                    title_hash,  # title_hash
                                    getattr(entry, "title", ""), translated_subject,
                                    getattr(entry, "link", ""), getattr(entry, "summary", ""),
                                    get_entry_timestamp(entry).timestamp() if get_entry_timestamp(entry) else time.time(),
                                    feed_data.feed.get('title', "")
                                )
                                                            
                            # ✅ 关键改动：不在这里写 rss_status
                            # 等 process_batch_send 发送成功后再写
                                
                        elif send_separately:
                            # 单独发送模式
                            messages_data = await generate_single_messages(
                                feed_data, [e for e,_,_,_ in new_entries], processor
                            )
                            
                            if messages_data:
                                sent_count = await send_single_messages_separately(
                                    bot, TELEGRAM_CHAT_ID[0], messages_data, processor
                                )
                                
                                # ✅ 只批量写入发送成功的
                                records = []
                                for i, (entry, link_hash, title_hash, entry_id) in enumerate(new_entries):
                                    if i < sent_count:
                                        records.append((group_key, canonical_url, entry_id, link_hash, title_hash, time.time()))
                                if records:
                                    await db.batch_save_status(records)
                                
                                if processor.get("show_count", False):
                                    summary_msg = f"✅ {feed_data.feed.get('title', '未知来源')} 新增 {sent_count} 条内容"
                                    try:
                                        await send_single_message(bot, TELEGRAM_CHAT_ID[0], summary_msg, disable_web_page_preview=True)
                                    except:
                                        pass
                        else:
                            # 立即批量发送模式
                            feed_message = await generate_group_message(feed_data, [e for e,_,_,_ in new_entries], processor)
                            if feed_message:
                                try:
                                    await send_single_message(
                                        bot, TELEGRAM_CHAT_ID[0], feed_message,
                                        disable_web_page_preview=not processor.get("preview", True)
                                    )
                                    # ✅ 发送成功后批量写入
                                    records = [
                                        (group_key, canonical_url, entry_id, link_hash, title_hash, time.time())
                                        for entry, link_hash, title_hash, entry_id in new_entries
                                    ]
                                    await db.batch_save_status(records)
                                except Exception as send_error:
                                    logger.error(f"❌ 发送消息失败 [{feed_url}]: {send_error}")
                                    raise
                                    
                except Exception as e:
                    logger.error(f"❌ 处理失败 [{feed_url}]: {e}")
                    continue
                    
            await db.save_last_run_time(group_key, now)
            
        except Exception as e:
            logger.critical(f"‼️ 处理组失败 [{group_key}]: {e}")
            raise
            
    except Exception as e:
        logger.error(f"❌ 组处理失败 [{group_config.get('name', '未知')}]: {e}", exc_info=True)
        raise

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
                shared_dedup = group.get("shared_dedup", False)
                keep_count = group.get("keep_count", 100)
                await db.cleanup_history(group["group_key"], shared_dedup, keep_count)
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
    except OSError as e:
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