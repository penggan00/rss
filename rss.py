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
import shutil
from pathlib import Path
from dotenv import load_dotenv
from feedparser import parse
from telegram import Bot
from telegram.error import BadRequest
from telegram.ext import Application
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
from datetime import datetime, timezone
import importlib.util

# ========== 首先加载环境变量 ==========
load_dotenv()  # 在顶部立即加载

# ========== 配置同步设置 ==========
CONFIG_URL = os.getenv("CONFIG_URL")
LOCAL_CONFIG_FILE = Path(__file__).parent / "rss_config.py"
CONFIG_CACHE_TIME = 24 * 3600  # 24小时缓存

# 全局配置管理器
config_manager = None

# ========== 原有的全局变量和配置 ==========
SHOULD_EXIT = False

# 设置时区（在cron环境中很重要）
os.environ['TZ'] = 'Asia/Singapore'
try:
    time.tzset()  # Linux系统
except AttributeError:
    pass  # Windows系统忽略

BASE_DIR = Path(__file__).resolve().parent
LOCK_FILE = BASE_DIR / "rss.lock"
DATABASE_FILE = BASE_DIR / "rss.db"

logging.basicConfig(
    filename=BASE_DIR / "rss.log",
    level=logging.WARNING,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    encoding="utf-8"
)
logger = logging.getLogger(__name__)

# 现在这些环境变量会在 load_dotenv() 之后正确加载
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").split(",")
TENCENTCLOUD_SECRET_ID = os.getenv("TENCENTCLOUD_SECRET_ID")
TENCENTCLOUD_SECRET_KEY = os.getenv("TENCENTCLOUD_SECRET_KEY")
TENCENT_REGION = os.getenv("TENCENT_REGION", "na-siliconvalley")
TENCENT_SECRET_ID = os.getenv("TENCENT_SECRET_ID")
TENCENT_SECRET_KEY = os.getenv("TENCENT_SECRET_KEY")
semaphore = asyncio.Semaphore(2)
BACKUP_DOMAINS_STR = os.getenv("BACKUP_DOMAINS", "")
BACKUP_DOMAINS = [domain.strip() for domain in BACKUP_DOMAINS_STR.split(",") if domain.strip()]

# ========== 全局 RSS_GROUPS 变量 ==========
RSS_GROUPS = []  # 将在main函数中从配置文件加载

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

class ConfigManager:
    def __init__(self, db=None):
        self.last_update = 0
        self.db = db
        
    async def ensure_fresh_config(self):
        """确保配置是最新的"""
        # 如果本地配置文件不存在，强制从GitHub下载
        if not LOCAL_CONFIG_FILE.exists():
            logger.info("📥 首次运行，本地配置文件不存在，从GitHub下载配置...")
            success = await self._update_config_from_github()
            if success:
                logger.info("✅ 首次配置下载成功")
                if self.db:
                    await self.db.save_config_update_time()
                    logger.info("✅ 配置更新时间已保存到数据库")
            else:
                logger.error("❌ 首次配置下载失败，程序无法继续")
                # 可以在这里创建默认配置或退出程序
            return
            
        # 本地配置文件存在，按时间缓存检查
        if not await self._is_config_fresh():
            logger.info("🔄 配置已过期，从GitHub更新...")
            success = await self._update_config_from_github()
            if success and self.db:
                await self.db.save_config_update_time()
                logger.info("✅ 配置更新时间已保存到数据库")
        else:
            logger.info("✅ 使用本地缓存配置")
    
    async def _is_config_fresh(self):
        """检查配置是否在24小时内更新过"""
        # 如果本地文件不存在，应该返回False强制更新，但这种情况应该在上层处理
        if not LOCAL_CONFIG_FILE.exists():
            return False
            
        if not self.db:
            # 如果没有数据库，回退到文件时间检查
            return await self._fallback_config_fresh_check()
            
        try:
            last_update = await self.db.get_last_config_update_time()
            if last_update is None:
                logger.info("📝 首次运行或未找到配置更新时间记录")
                return False
                
            is_fresh = (time.time() - last_update) < CONFIG_CACHE_TIME
            if is_fresh:
                logger.info(f"🕒 配置仍在有效期内，剩余 {int((CONFIG_CACHE_TIME - (time.time() - last_update)) / 3600)} 小时")
            return is_fresh
        except Exception as e:
            logger.warning(f"⚠️ 数据库检查失败，回退到文件检查: {e}")
            return await self._fallback_config_fresh_check()
    
    async def _fallback_config_fresh_check(self):
        """回退方案：使用文件修改时间检查"""
        if not LOCAL_CONFIG_FILE.exists():
            return False
        
        file_mtime = LOCAL_CONFIG_FILE.stat().st_mtime
        is_fresh = (time.time() - file_mtime) < CONFIG_CACHE_TIME
        
        if is_fresh:
            remaining_hours = int((CONFIG_CACHE_TIME - (time.time() - file_mtime)) / 3600)
            logger.info(f"🕒 文件检查：配置仍在有效期内，剩余 {remaining_hours} 小时")
        
        return is_fresh
    
    async def _update_config_from_github(self):
        """从GitHub更新配置"""
        if not CONFIG_URL:
            logger.error("❌ CONFIG_URL 未设置，无法从GitHub更新配置")
            return False
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
    
        print(f"🔄 正在从 GitHub 下载配置: {CONFIG_URL}")
    
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(CONFIG_URL, headers=headers, timeout=30) as response:
                    print(f"📡 GitHub 响应状态码: {response.status}")
                
                    if response.status == 200:
                        content = await response.text()
                        print(f"📥 下载内容长度: {len(content)} 字符")
                    
                    # 安全检查
                        if not self._is_safe_python_content(content):
                            logger.error("❌ 下载的配置包含不安全代码")
                            return False
                    
                    # 备份当前配置（如果存在）
                        if LOCAL_CONFIG_FILE.exists():
                            await self._backup_current_config()
                    
                    # 保存新配置
                        with open(LOCAL_CONFIG_FILE, 'w', encoding='utf-8') as f:
                            f.write(content)
                    
                        print(f"💾 配置文件已保存到: {LOCAL_CONFIG_FILE}")
                        logger.info("✅ 配置已从GitHub更新")
                        return True
                    else:
                        logger.error(f"❌ GitHub请求失败: HTTP {response.status}")
                        print(f"❌ GitHub请求失败: HTTP {response.status}")
                        return False
        except Exception as e:
            logger.error(f"❌ 配置更新失败: {e}")
            print(f"❌ 配置更新失败: {e}")
            return False
    
    async def _backup_current_config(self):
        """备份当前配置"""
        if LOCAL_CONFIG_FILE.exists():
            backup_file = LOCAL_CONFIG_FILE.with_suffix(f'.backup.{int(time.time())}.py')
            try:
                shutil.copy2(LOCAL_CONFIG_FILE, backup_file)
                logger.info(f"📦 配置已备份到: {backup_file.name}")
            except Exception as e:
                logger.warning(f"⚠️ 配置备份失败: {e}")
    
    def _is_safe_python_content(self, content):
        """安全检查配置内容"""
        dangerous_keywords = [
            '__import__', 'eval(', 'exec(', 'compile(',
            'os.system', 'os.popen', 'subprocess.', 'import requests',
            'import urllib', 'import http'
        ]
        
        content_lower = content.lower()
        for keyword in dangerous_keywords:
            if keyword in content_lower:
                logger.warning(f"⚠️ 检测到不安全代码: {keyword}")
                return False
        return True
    
    def load_local_config(self):
        """加载本地配置文件"""
        try:
            if not LOCAL_CONFIG_FILE.exists():
                logger.error("❌ 本地配置文件不存在")
                return []
                
            spec = importlib.util.spec_from_file_location("rss_config", LOCAL_CONFIG_FILE)
            config_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(config_module)
            
            if hasattr(config_module, 'RSS_GROUPS'):
                # 跳过验证配置
                # if hasattr(config_module, 'validate_config'):
                #     config_module.validate_config()
                return config_module.RSS_GROUPS
            else:
                raise Exception("配置文件中未找到RSS_GROUPS变量")
                
        except Exception as e:
            logger.error(f"❌ 本地配置加载失败: {e}")
            # 返回空配置，让程序优雅退出
            return []

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

    async def get_last_config_update_time(self):  # 🔧 这个函数需要缩进到类内部
        """获取最后配置更新时间"""
        if USE_PG:
            async with self.pg_pool.acquire() as conn:
                # 先确保表存在
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS app_config (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    )
                """)
                row = await conn.fetchrow("SELECT value FROM app_config WHERE key = 'config_last_update'")
                return float(row['value']) if row else None
        else:
            async with self.conn.cursor() as c:
                # 先确保表存在
                await c.execute("""
                    CREATE TABLE IF NOT EXISTS app_config (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    )
                """)
                await c.execute("SELECT value FROM app_config WHERE key = 'config_last_update'")
                result = await c.fetchone()
                return float(result[0]) if result else None

    async def save_config_update_time(self):  # 🔧 这个函数需要缩进到类内部
        """保存配置更新时间"""
        current_time = time.time()
        if USE_PG:
            async with self.pg_pool.acquire() as conn:
                # 先确保表存在
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS app_config (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    )
                """)
                await conn.execute("""
                    INSERT INTO app_config (key, value) 
                    VALUES ('config_last_update', $1)
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                """, str(current_time))
        else:
            async with self.conn.cursor() as c:
                # 先确保表存在
                await c.execute("""
                    CREATE TABLE IF NOT EXISTS app_config (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    )
                """)
                await c.execute("""
                    INSERT OR REPLACE INTO app_config (key, value)
                    VALUES ('config_last_update', ?)
                """, (str(current_time),))
                await self.conn.commit()

    async def create_tables(self):  # 🔧 这个函数需要缩进到类内部
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
                # 新增：应用配置表
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS app_config (
                        key TEXT PRIMARY KEY,
                        value TEXT
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
                # 新增：应用配置表
                await c.execute("""
                    CREATE TABLE IF NOT EXISTS app_config (
                        key TEXT PRIMARY KEY,
                        value TEXT
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
        now = time.time()
        cutoff_ts = now - days * 86400

        if USE_PG:
            async with self.pg_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT last_cleanup_time FROM cleanup_timestamps WHERE feed_group=$1", feed_group
                )
                last_cleanup = row['last_cleanup_time'] if row else 0
                if now - last_cleanup < 86400:
                    return
                await conn.execute(
                    "DELETE FROM rss_status WHERE feed_group=$1 AND entry_timestamp<$2",
                    feed_group, cutoff_ts
                )
                await conn.execute("""
                    INSERT INTO cleanup_timestamps (feed_group, last_cleanup_time)
                    VALUES ($1, $2)
                    ON CONFLICT (feed_group) DO UPDATE SET last_cleanup_time=EXCLUDED.last_cleanup_time
                """, feed_group, now)
        else:
            async with self.conn.cursor() as c:
                await c.execute(
                    "SELECT last_cleanup_time FROM cleanup_timestamps WHERE feed_group = ?",
                    (feed_group,)
                )
                result = await c.fetchone()
                last_cleanup = result[0] if result else 0
                if now - last_cleanup < 86400:
                    return
                await c.execute(
                    "DELETE FROM rss_status WHERE feed_group=? AND entry_timestamp < ?",
                    (feed_group, cutoff_ts)
                )
                await c.execute("""
                    INSERT OR REPLACE INTO cleanup_timestamps (feed_group, last_cleanup_time)
                    VALUES (?, ?)
                """, (feed_group, now))
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
    text = text.replace('.', '.\u200c')
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
            para_length = len(para)
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
                disable_web_page_preview=disable_web_page_preview
                # 移除 timeout 参数
            )
            
    except BadRequest as e:
        logger.error(f"消息发送失败(Markdown错误): {e} - 文本片段: {chunk[:200]}...")
        # 可以尝试不使用 Markdown 重新发送
        try:
            for chunk in text_chunks:
                await bot.send_message(
                    chat_id=chat_id,
                    text=chunk,
                    disable_web_page_preview=disable_web_page_preview
                )
        except Exception as fallback_error:
            logger.error(f"纯文本回退发送也失败: {fallback_error}")
    except Exception as e:
        logger.error(f"消息发送未知错误: {e}")
        raise

@retry(
    stop=stop_after_attempt(1),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError)),
)
async def fetch_feed(session, feed_url):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/89.0.4389.82 Safari/537.36'}
    parsed = urlparse(feed_url)
    is_rsshub = parsed.netloc == "rsshub.app"
    if is_rsshub:
        try_domains = BACKUP_DOMAINS + ["rsshub.app"]
        canonical_url = feed_url.replace(parsed.netloc, "rsshub.app")
    else:
        try_domains = [parsed.netloc]
        canonical_url = feed_url
    for domain in try_domains:
        modified_url = feed_url.replace(parsed.netloc, domain)
        try:
            async with semaphore:
                async with session.get(modified_url, headers=headers, timeout=30) as response:
                    if response.status in (503, 403, 404, 429):
                        continue
                    response.raise_for_status()
                    return parse(await response.read()), canonical_url
        except aiohttp.ClientResponseError as e:
            if e.status in (503, 403, 404, 429):
                continue
        except Exception as e:
        #    logger.error(f"请求失败: {modified_url}, 错误: {e}")
            continue
   # logger.error(f"所有域名尝试失败: {feed_url}")
    return None, canonical_url

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
        
        # ✅ 检查模板是否需要摘要
        template_needs_summary = "{summary}" in processor["template"]
        
        # ✅ 现在可以安全地使用 template_needs_summary
        logger.debug(f"[摘要处理] 组: {source_name}, 需要摘要: {template_needs_summary}")
        
        for entry in entries:
            raw_subject = remove_html_tags(entry.title or "无标题")
            if processor.get("translate", False):
                translated_subject = await auto_translate_text(raw_subject)
            else:
                translated_subject = raw_subject
            safe_subject = escape(translated_subject)
            raw_url = entry.link
            safe_url = escape(raw_url)
            
            # ✅ 只在需要时处理摘要
            format_kwargs = {
                "subject": safe_subject,
                "source": safe_source,
                "url": safe_url
            }
            
            if template_needs_summary:
                raw_summary = getattr(entry, "summary", "") or ""
                cleaned_summary = remove_html_tags(raw_summary)
                safe_summary = escape(cleaned_summary)
                format_kwargs["summary"] = safe_summary
            
            message = processor["template"].format(**format_kwargs)
            messages.append(message)
        
        full_message = await _format_batch_message(header, messages, processor)
        return full_message
    except Exception as e:
        logger.error(f"生成消息失败: {str(e)}")
        return ""

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
    """在组处理中添加退出检查"""
    global SHOULD_EXIT
    
    if SHOULD_EXIT:
        logger.info("收到退出信号，停止处理组任务")
        return
        
    group_name = group_config["name"]
    group_key = group_config["group_key"]
    processor = group_config["processor"]
    bot_token = group_config["bot_token"]
    batch_send_interval = group_config.get("batch_send_interval", None)
    
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
                    entry_id = get_entry_identifier(entry)
                    content_hash = get_entry_content_hash(entry)
                    
                    # 统一使用内容哈希去重（主要修复）
                    if await db.has_content_hash(group_key, content_hash):
                        continue
                        
                    if entry_id in processed_ids or entry_id in seen_in_batch:
                        continue
                        
                    # 在当前批次中也用内容哈希去重
                    if content_hash in new_hashes_in_batch:
                        continue  
                        
                    # ✅ 过滤检查
                    if not await should_send_entry(entry, processor):
                        continue  # 跳过不符合过滤条件的条目

                    seen_in_batch.add(entry_id)
                    new_hashes_in_batch.add(content_hash)
                    new_entries.append((entry, content_hash, entry_id))
                    
                if new_entries:
                    if batch_send_interval:
                        # 批量发送模式：存入待发送队列
                        for entry, content_hash, entry_id in new_entries:
                            raw_subject = remove_html_tags(getattr(entry, "title", "") or "")
                            if processor["translate"] and is_need_translate(raw_subject):
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
                    else:
                        # 立即发送模式
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
                
        await db.save_last_run_time(group_key, now)
        
    except Exception as e:
        logger.critical(f"‼️ 处理组失败 [{group_key}]: {e}")

async def main():
    logger.info("🚀 RSS Bot 开始执行")
    
    # 添加环境变量验证
    print("🔍 环境变量检查:")
    print(f"  CONFIG_URL: {CONFIG_URL}")
    print(f"  TELEGRAM_CHAT_ID: {TELEGRAM_CHAT_ID}")
    print(f"  TENCENTCLOUD_SECRET_ID: {'已设置' if TENCENTCLOUD_SECRET_ID else '未设置'}")
    print(f"  TENCENTCLOUD_SECRET_KEY: {'已设置' if TENCENTCLOUD_SECRET_KEY else '未设置'}")
    
    # 验证必要的环境变量
    if not CONFIG_URL:
        print("❌ 错误: CONFIG_URL 环境变量未设置")
        logger.error("CONFIG_URL 环境变量未设置")
        return
    
    if not TELEGRAM_CHAT_ID or not TELEGRAM_CHAT_ID[0]:
        print("❌ 错误: TELEGRAM_CHAT_ID 环境变量未设置")
        logger.error("TELEGRAM_CHAT_ID 环境变量未设置")
        return
    
    print(f"🔍 当前工作目录: {os.getcwd()}")
    print(f"🔍 配置文件路径: {LOCAL_CONFIG_FILE}")
    print(f"🔍 配置文件是否存在: {LOCAL_CONFIG_FILE.exists()}")
    
    # 先初始化数据库
    db = RSSDatabase()
    try:
        await db.open()
        await db.ensure_initialized()
        logger.info("✅ 数据库连接成功")
        
        # 1. 初始化配置管理器并传入数据库实例
        global config_manager
        config_manager = ConfigManager(db)
        
        # 2. 确保配置是最新的 - 添加详细日志
        print("🔄 开始检查配置更新...")
        await config_manager.ensure_fresh_config()
        print("✅ 配置检查完成")
        
        # 3. 再次检查配置文件是否存在
        print(f"🔍 下载后配置文件是否存在: {LOCAL_CONFIG_FILE.exists()}")
        
        if not LOCAL_CONFIG_FILE.exists():
            logger.error("❌ 配置文件不存在，程序退出")
            print("❌ 错误：配置文件不存在，请检查 CONFIG_URL 设置")
            return
        
        # 4. 加载配置
        global RSS_GROUPS
        RSS_GROUPS = config_manager.load_local_config()
        
        if not RSS_GROUPS:
            logger.error("❌ 无法加载RSS配置，程序退出")
            print("❌ 错误：无法加载RSS配置")
            return
        
        logger.info(f"📋 加载了 {len(RSS_GROUPS)} 个RSS组")
        print(f"✅ 成功加载 {len(RSS_GROUPS)} 个RSS组配置")
        
        # 5. 原有的主逻辑
        start_time = time.time()
        max_retries = 3
        retry_delay = 60
        
        for attempt in range(max_retries):
            try:
                await run_main_logic(db)
                logger.info(f"✅ RSS Bot 执行完成，耗时: {time.time() - start_time:.2f}秒")
                break
            except Exception as e:
                logger.error(f"主程序运行失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    logger.info(f"{retry_delay}秒后重试...")
                    await asyncio.sleep(retry_delay)
                else:
                    logger.critical("达到最大重试次数，程序退出")
                    return
                    
    except Exception as e:
        logger.error(f"❌ 初始化失败: {e}")
        print(f"❌ 初始化失败: {e}")
    finally:
        if db:
            await db.close()

async def run_main_logic(db):  # 修改：接收db参数
    lock_file = None
    # 注意：这里不再新建db实例，而是使用传入的db
    
    try:
        # 获取文件锁
        lock_file = open(LOCK_FILE, "w")
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        logger.info("🔒 成功获取文件锁")
        
        # 数据库连接已经在main中建立，这里直接使用
        
        # 清理历史记录
        logger.info("🧹 正在清理历史记录...")
        for group in RSS_GROUPS:
            days = group.get("history_days", 30)
            try:
                await db.cleanup_history(days, group["group_key"])
            except Exception as e:
                logger.error(f"清理历史记录异常: 组={group['group_key']}, 错误={e}")
                
        # 主处理逻辑
        logger.info("🚀 开始处理 RSS 订阅...")
        async with aiohttp.ClientSession() as session:
            status = await db.load_status()
            tasks = []
            
            for group in RSS_GROUPS:
                try:
                    task = asyncio.create_task(
                        process_group(session, group, status, db)
                    )
                    tasks.append(task)
                except Exception as e:
                    logger.error(f"⚠️ 创建任务失败 [{group['name']}]: {str(e)}")
            
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            
            # 批量发送任务
            batch_tasks = [
                process_batch_send(group, db) 
                for group in RSS_GROUPS 
                if group.get("batch_send_interval")
            ]
            if batch_tasks:
                await asyncio.gather(*batch_tasks, return_exceptions=True)
                
    except asyncio.CancelledError:
        logger.warning("⏹️ 任务被取消")
    except Exception as e:
        logger.error(f"主逻辑执行异常: {str(e)}")
        raise
    finally:
        # 注意：这里不再关闭db，因为db在main函数中统一管理
        await cleanup_resources(None, lock_file)  # 修改：传入None而不是db

async def cleanup_resources(db, lock_file):
    """清理资源"""
    # 注意：db现在在main函数中统一管理，这里只处理锁文件
    try:
        if lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()
            if LOCK_FILE.exists():
                LOCK_FILE.unlink()
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