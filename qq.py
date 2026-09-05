# source rss_venv/bin/activate
# pip install psutil python-dotenv python-telegram-bot aiosqlite aiohttp
import os
import re
import asyncio
import psutil
import time
import subprocess
import shlex
import aiohttp
from datetime import datetime
from typing import List, Optional, Tuple
from functools import wraps
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CommandHandler
import aiosqlite
import logging

# ============================================================
# 基础配置（必须最先初始化）
# ============================================================
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

logging.getLogger('telegram').setLevel(logging.WARNING)
logging.getLogger('aiosqlite').setLevel(logging.WARNING)

# 加载环境变量
load_dotenv()

# ============================================================
# 配置类
# ============================================================
class Config:
    def __init__(self):
        self.TELEGRAM_TOKEN = self._get_env('TELEGRAM_API_KEY')
        self.AUTHORIZED_CHAT_IDS = self._parse_chat_ids('TELEGRAM_CHAT_ID')
        self.LIBRETRANSLATE_URL = self._get_env('LIBRETRANSLATE_URL')

    def _get_env(self, var_name: str) -> str:
        value = os.getenv(var_name)
        if not value:
            logger.error(f"Missing required environment variable: {var_name}")
            raise ValueError(f"Missing required environment variable: {var_name}")
        return value

    def _parse_chat_ids(self, var_name: str) -> List[int]:
        ids_str = self._get_env(var_name)
        try:
            return [int(id_str.strip()) for id_str in ids_str.split(',')]
        except ValueError:
            logger.error(f"Invalid {var_name} format")
            raise ValueError(f"Invalid {var_name} format")

# 初始化全局配置
try:
    config = Config()
    logger.info("Configuration loaded successfully")
    logger.info(f"Authorized chat IDs: {config.AUTHORIZED_CHAT_IDS}")
except Exception as e:
    logger.critical(f"Failed to load configuration: {e}")
    raise

# ============================================================
# 数据库连接池管理
# ============================================================
class AsyncTranslationCache:
    """使用持久连接和WAL模式的翻译缓存"""
    
    def __init__(self, db_path: str = 'translations.db'):
        self.db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()
        self._update_lock = asyncio.Lock()
        self._init_done = False
        
    async def init_db(self):
        """初始化数据库连接"""
        if self._init_done:
            return
            
        self._conn = await aiosqlite.connect(self.db_path)
        
        # 启用WAL模式
        await self._conn.execute('PRAGMA journal_mode=WAL')
        await self._conn.execute('PRAGMA synchronous=NORMAL')
        await self._conn.execute('PRAGMA cache_size=10000')
        await self._conn.execute('PRAGMA temp_store=MEMORY')
        
        # 创建表
        await self._conn.execute('''
            CREATE TABLE IF NOT EXISTS translations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_text TEXT NOT NULL,
                source_lang TEXT NOT NULL,
                target_lang TEXT NOT NULL,
                translated_text TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                access_count INTEGER DEFAULT 1,
                UNIQUE(source_text, source_lang, target_lang)
            )
        ''')
        
        await self._conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_translations_key 
            ON translations(source_text, source_lang, target_lang)
        ''')
        
        await self._conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_translations_created 
            ON translations(created_at)
        ''')
        
        await self._conn.commit()
        self._init_done = True
        logger.info(f"Database initialized: {self.db_path}")
        
    async def close(self):
        """关闭数据库连接"""
        if self._conn:
            await self._conn.close()
            self._conn = None
            self._init_done = False
            
    async def get(self, source_text: str, source_lang: str, target_lang: str) -> Optional[str]:
        """查询缓存"""
        try:
            async with self._lock:
                cursor = await self._conn.execute(
                    '''
                    SELECT translated_text FROM translations 
                    WHERE source_text=? AND source_lang=? AND target_lang=?
                    ''', 
                    (source_text, source_lang, target_lang)
                )
                row = await cursor.fetchone()
                if row:
                    asyncio.create_task(self._update_access_count(
                        source_text, source_lang, target_lang
                    ))
                    return row[0]
                return None
        except Exception as e:
            logger.error(f"Cache get error: {e}")
            return None
            
    async def _update_access_count(self, source_text: str, source_lang: str, target_lang: str):
        """更新访问计数"""
        try:
            async with self._update_lock:
                await self._conn.execute(
                    '''
                    UPDATE translations 
                    SET access_count = access_count + 1
                    WHERE source_text=? AND source_lang=? AND target_lang=?
                    ''',
                    (source_text, source_lang, target_lang)
                )
                await self._conn.commit()
        except Exception as e:
            logger.debug(f"Update access count skipped: {e}")
        
    async def set(self, source_text: str, source_lang: str, target_lang: str, translated_text: str) -> bool:
        """写入缓存"""
        try:
            async with self._lock:
                await self._conn.execute(
                    '''
                    INSERT OR REPLACE INTO translations 
                    (source_text, source_lang, target_lang, translated_text) 
                    VALUES (?, ?, ?, ?)
                    ''',
                    (source_text, source_lang, target_lang, translated_text)
                )
                await self._conn.commit()
            return True
        except Exception as e:
            logger.error(f"Cache set error: {e}")
            return False
            
    async def clean_expired(self, days: int = 99999):
        """清理过期缓存"""
        try:
            async with self._lock:
                await self._conn.execute(
                    """
                    DELETE FROM translations 
                    WHERE created_at < datetime('now', ?)
                    AND access_count < 5
                    """,
                    (f'-{days} days',)
                )
                await self._conn.commit()
            logger.info("Cache cleanup completed")
        except Exception as e:
            logger.error(f"Cache cleanup error: {e}")
            
    async def get_stats(self):
        """获取缓存统计"""
        try:
            async with self._lock:
                cursor = await self._conn.execute("SELECT COUNT(*) FROM translations")
                total = (await cursor.fetchone())[0]
                
                cursor = await self._conn.execute("SELECT COUNT(*) FROM translations WHERE access_count > 1")
                reused = (await cursor.fetchone())[0]
                
                return {
                    'total_entries': total,
                    'reused_entries': reused,
                    'reuse_rate': f"{reused/total*100:.1f}%" if total > 0 else "N/A"
                }
        except Exception as e:
            logger.error(f"Get cache stats error: {e}")
            return {'total_entries': 0, 'reused_entries': 0, 'reuse_rate': 'N/A'}

cache = AsyncTranslationCache()

# ============================================================
# 语言检测
# ============================================================
def detect_language(text: str) -> str:
    """检测文本语言"""
    if not text or not isinstance(text, str):
        return 'unknown'
    clean_text = re.sub(r'[^\w\u4e00-\u9fff]', '', text, flags=re.UNICODE)
    if not clean_text:
        return 'unknown'
    char_stats = {
        'zh': len(re.findall(r'[\u4e00-\u9fff]', clean_text)),
        'ja': len(re.findall(r'[\u3040-\u30ff\u31f0-\u31ff]', clean_text)),
        'ko': len(re.findall(r'[\uac00-\ud7af\u1100-\u11ff]', clean_text)),
        'ru': len(re.findall(r'[\u0400-\u04FF]', clean_text)),
        'en': len(re.findall(r'[a-zA-Z]', clean_text)),
    }
    dominant_lang, dominant_ratio = max(
        ((lang, count / len(clean_text)) for lang, count in char_stats.items()),
        key=lambda x: x[1]
    )
    return dominant_lang if dominant_ratio > 0.4 else 'other'

def get_translation_direction(text: str) -> Tuple[str, str]:
    """获取翻译方向"""
    lang = detect_language(text)
    if lang in ('zh', 'ja', 'ko', 'ru', 'en'):
        target = 'en' if lang == 'zh' else 'zh'
        return (lang, target)
    else:
        return ('en', 'zh')

# ============================================================
# 翻译器（仅 LibreTranslate）
# ============================================================
class LibreTranslator:
    def __init__(self):
        self.libretranslate_url = config.LIBRETRANSLATE_URL
        
    async def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """使用 LibreTranslate 翻译"""
        if not text or not text.strip():
            return text
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.libretranslate_url,
                    json={"q": text, "source": source_lang, "target": target_lang},
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        translated = result.get("translatedText")
                        if translated and translated != text:
                            logger.info("✅ LibreTranslate 翻译成功")
                            return translated
                        else:
                            logger.warning("⚠️ LibreTranslate 返回空或相同文本")
                            return text
                    else:
                        logger.warning(f"⚠️ LibreTranslate 返回状态码: {response.status}")
                        return text
        except asyncio.TimeoutError:
            logger.warning("⚠️ LibreTranslate 请求超时")
            return text
        except aiohttp.ClientError as e:
            logger.warning(f"⚠️ LibreTranslate 网络错误: {e}")
            return text
        except Exception as e:
            logger.warning(f"⚠️ LibreTranslate 翻译失败: {e}")
            return text

# 初始化翻译器
translator = LibreTranslator()

# ============================================================
# 权限装饰器
# ============================================================
def require_auth(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if update.effective_chat.id not in config.AUTHORIZED_CHAT_IDS:
            logger.warning(f"Unauthorized access: {update.effective_chat.id}")
            return
        return await func(update, context, *args, **kwargs)
    return wrapper

# ============================================================
# 消息处理器
# ============================================================
@require_auth
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理文本消息"""
    text = update.message.text
    if not text or len(text) > 5000:
        return
        
    source_lang, target_lang = get_translation_direction(text)
    logger.info(f"Chat {update.effective_chat.id}: [{source_lang}->{target_lang}] '{text[:80]}...'")
    
    # 第一步：检查缓存
    try:
        cached = await cache.get(text, source_lang, target_lang)
        if cached:
            await send_long_message(update, cached)
            logger.info(f"Cache hit for: '{text[:50]}...'")
            return
    except Exception as e:
        logger.error(f"Cache get error: {e}")
    
    # 第二步：翻译
    try:
        translated = await translator.translate(text, source_lang, target_lang)
        
        # 如果翻译结果和原文不同，缓存结果
        if translated != text:
            await cache.set(text, source_lang, target_lang, translated)
        
        # 发送结果
        await send_long_message(update, translated)
        
    except Exception as e:
        logger.error(f"Translation error: {e}")
        await update.message.reply_text(f"❌ 翻译出错: {str(e)}")

async def send_long_message(update: Update, text: str, chunk_size: int = 3900):
    """分片发送长消息"""
    idx, length = 0, len(text)
    while idx < length:
        end_idx = min(idx + chunk_size, length)
        if end_idx < length:
            while end_idx > idx and text[end_idx] not in (' ', '\n', '。', '，', '.', ','):
                end_idx -= 1
            if end_idx == idx:
                end_idx = min(idx + chunk_size, length)
                
        chunk = text[idx:end_idx]
        try:
            await update.message.reply_text(chunk)
        except Exception as e:
            logger.error(f"Send message error: {e}")
            break
        idx = end_idx

# ============================================================
# 系统命令执行
# ============================================================
@require_auth
async def cmd_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """执行系统命令"""
    command = ' '.join(context.args) if context.args else None
    
    if not command:
        await update.message.reply_text("用法: /cmd 命令")
        return
    if command == 'top' or command.startswith('top '):
        command = 'top -b -n 1 | head -20'
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=15, cwd='/root'
        )
        output = result.stdout or result.stderr or "(无输出)"
        
        if len(output) > 3500:
            output = output[:3500] + "\n...截断"
        
        await update.message.reply_text(f"```\n{output}\n```", parse_mode='Markdown')
    except subprocess.TimeoutExpired:
        await update.message.reply_text("⏰ 超时")
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")

# ============================================================
# 系统状态命令
# ============================================================
@require_auth
async def htop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """显示系统状态"""
    try:
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        memory_total_gb = memory.total / (1024 ** 3)
        memory_used_gb = memory.used / (1024 ** 3)
        
        disk = psutil.disk_usage('/')
        disk_total_gb = disk.total / (1024 ** 3)
        disk_used_gb = disk.used / (1024 ** 3)
        
        boot_time = datetime.fromtimestamp(psutil.boot_time())
        uptime = datetime.now() - boot_time
        net_io = psutil.net_io_counters()
        
        cache_stats = await cache.get_stats()

        message = (
            "🖥️ *系统状态*\n\n"
            f"*CPU:* {cpu_percent}%\n"
            f"*内存:* {memory_used_gb:.1f}/{memory_total_gb:.1f}GB ({memory.percent}%)\n"
            f"*磁盘:* {disk_used_gb:.1f}/{disk_total_gb:.1f}GB ({disk.percent}%)\n"
            f"*运行时间:* {str(uptime).split('.')[0]}\n"
            f"*网络发送:* {net_io.bytes_sent / (1024 ** 2):.1f}MB\n"
            f"*网络接收:* {net_io.bytes_recv / (1024 ** 2):.1f}MB\n\n"
            f"📊 *缓存统计*\n"
            f"*缓存条目:* {cache_stats['total_entries']}条\n"
            f"*复用条目:* {cache_stats['reused_entries']}条\n"
            f"*复用率:* {cache_stats['reuse_rate']}\n\n"
            f"*更新时间:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        
        await update.message.reply_text(message, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Htop command error: {e}")
        await update.message.reply_text(f"❌ 获取系统信息出错: {str(e)}")

# ============================================================
# 应用生命周期管理
# ============================================================
async def startup(application: Application):
    """应用启动初始化"""
    logger.info("Initializing bot services...")
    
    try:
        await cache.init_db()
        logger.info("Database initialized")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        raise
    
    logger.info("Bot started")

async def shutdown(application: Application):
    """应用关闭清理"""
    logger.info("Shutting down bot...")
    
    try:
        await cache.clean_expired()
        logger.info("Cache cleaned")
    except Exception as e:
        logger.error(f"Cache cleanup failed: {e}")
    
    try:
        await cache.close()
        logger.info("Database connection closed")
    except Exception as e:
        logger.error(f"Database close failed: {e}")
    
    logger.info("Bot shutdown complete")

# ============================================================
# 主函数
# ============================================================
def main():
    """启动机器人"""
    try:
        application = Application.builder().token(config.TELEGRAM_TOKEN).build()
        
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
        application.add_handler(CommandHandler("htop", htop_command))
        application.add_handler(CommandHandler("cmd", cmd_command))
        
        application.post_init = startup
        application.post_shutdown = shutdown
        
        logger.info("Bot is starting...")
        application.run_polling()
        
    except Exception as e:
        logger.critical(f"Failed to start bot: {e}")
        raise

if __name__ == "__main__":
    main()