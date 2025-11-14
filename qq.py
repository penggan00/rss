#source rss_venv/bin/activate
#pip install psutil python-dotenv tencentcloud-sdk-python python-telegram-bot aiosqlite
import os
import re
import asyncio
import psutil
from datetime import datetime
from typing import List, Optional, Tuple
from functools import wraps
from dotenv import load_dotenv
from tencentcloud.common import credential
from tencentcloud.common.profile.client_profile import ClientProfile
from tencentcloud.common.profile.http_profile import HttpProfile
from tencentcloud.tmt.v20180321 import tmt_client, models
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CommandHandler
import aiosqlite
import logging

# 获取项目根目录路径
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(PROJECT_ROOT, 'qq.log')

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),  # 文件处理器
        logging.StreamHandler()  # 控制台处理器（可选）
    ]
)
logger = logging.getLogger(__name__)

# 可选：设置其他库的日志级别
logging.getLogger('telegram').setLevel(logging.WARNING)
logging.getLogger('tencentcloud').setLevel(logging.WARNING)
logging.getLogger('aiosqlite').setLevel(logging.WARNING)

load_dotenv()

class Config:
    def __init__(self):
        self.TELEGRAM_TOKEN = self._get_env('TELEGRAM_API_KEY')
        self.AUTHORIZED_CHAT_IDS = self._parse_chat_ids('TELEGRAM_CHAT_ID')
        self.TENCENT_SECRET_ID = self._get_env('TENCENT_SECRET_ID')
        self.TENCENT_SECRET_KEY = self._get_env('TENCENT_SECRET_KEY')
        self.TENCENT_REGION = os.getenv('TENCENT_REGION')
        self.TENCENT_PROJECT_ID = int(os.getenv('TENCENT_PROJECT_ID'))

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

config = Config()

class AsyncTranslationCache:
    def __init__(self, db_path: str = 'translations.db'):
        self.db_path = db_path

    async def init_db(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                CREATE TABLE IF NOT EXISTS translations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_text TEXT NOT NULL,
                    source_lang TEXT NOT NULL,
                    target_lang TEXT NOT NULL,
                    translated_text TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(source_text, source_lang, target_lang)
                )
            ''')
            await db.execute('''
                CREATE INDEX IF NOT EXISTS idx_translations_key 
                ON translations(source_text, source_lang, target_lang)
            ''')
            await db.commit()

    async def get(self, source_text: str, source_lang: str, target_lang: str) -> Optional[str]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                '''
                SELECT translated_text FROM translations 
                WHERE source_text=? AND source_lang=? AND target_lang=?
                ''', (source_text, source_lang, target_lang)
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None

    async def set(self, source_text: str, source_lang: str, target_lang: str, translated_text: str) -> bool:
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    '''
                    INSERT OR REPLACE INTO translations 
                    (source_text, source_lang, target_lang, translated_text) 
                    VALUES (?, ?, ?, ?)
                    ''',
                    (source_text, source_lang, target_lang, translated_text)
                )
                await db.commit()
            return True
        except Exception as e:
            logger.error(f"Cache set error: {e}")
            return False

    async def clean_expired(self, days: int = 300):
        """可选：定期清理超过 days 天的缓存"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM translations WHERE created_at < datetime('now', ?)",
                (f'-{days} days',)
            )
            await db.commit()

cache = AsyncTranslationCache()

def detect_language(text: str) -> str:
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
    lang = detect_language(text)
    # 可扩展多语种支持
    if lang == 'zh':
        return ('zh', 'en')
    elif lang == 'ja':
        return ('ja', 'zh')
    elif lang == 'ko':
        return ('ko', 'zh')
    elif lang == 'ru':
        return ('ru', 'zh')
    elif lang == 'en':
        return ('en', 'zh')
    else:
        return ('auto', 'zh')

class TencentTranslator:
    def __init__(self):
        cred = credential.Credential(
            config.TENCENT_SECRET_ID,
            config.TENCENT_SECRET_KEY
        )
        http_profile = HttpProfile()
        http_profile.reqMethod = "POST"
        http_profile.reqTimeout = 30
        client_profile = ClientProfile()
        client_profile.httpProfile = http_profile
        client_profile.signMethod = "TC3-HMAC-SHA256"
        self.client = tmt_client.TmtClient(
            cred, 
            config.TENCENT_REGION, 
            client_profile
        )

    async def translate(self, text: str, source_lang: str, target_lang: str, max_retries: int = 3) -> str:
        # 用线程池防止阻塞主事件循环
        loop = asyncio.get_running_loop()
        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                def call_api():
                    req = models.TextTranslateRequest()
                    req.SourceText = text
                    req.Source = source_lang
                    req.Target = target_lang
                    req.ProjectId = config.TENCENT_PROJECT_ID
                    resp = self.client.TextTranslate(req)
                    return resp.TargetText
                result = await loop.run_in_executor(None, call_api)
                return result
            except Exception as e:
                last_error = e
                logger.error(f"Tencent translate error: {e}")
                if attempt < max_retries:
                    await asyncio.sleep(min(2 ** attempt, 5))
        raise last_error if last_error else Exception("Translation error")

translator = TencentTranslator()

def require_auth(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if update.effective_chat.id not in config.AUTHORIZED_CHAT_IDS:
            logger.warning(f"Unauthorized access: {update.effective_chat.id}")
            return
        return await func(update, context, *args, **kwargs)
    return wrapper

@require_auth
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text
    if not text or len(text) > 5000:
        return
    source_lang, target_lang = get_translation_direction(text)
    try:
        cached = await cache.get(text, source_lang, target_lang)
    except Exception as e:
        logger.error(f"Cache get error: {e}")
        cached = None
    if cached:
        await send_long_message(update, cached)
        return
    try:
        translated = await translator.translate(text, source_lang, target_lang)
        await cache.set(text, source_lang, target_lang, translated)
        await send_long_message(update, translated)
    except Exception as e:
        logger.error(f"Translation error: {e}")
        await update.message.reply_text(f"❌ 翻译出错: {str(e)}")

async def send_long_message(update: Update, text: str, chunk_size: int = 3900):
    # 按 Telegram 限制分片，避免多字节字符截断
    idx, length = 0, len(text)
    while idx < length:
        chunk = text[idx:idx+chunk_size]
        await update.message.reply_text(chunk)
        idx += chunk_size

@require_auth
async def htop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """显示 VPS 系统状态信息"""
    try:
        # CPU 使用率
        cpu_percent = psutil.cpu_percent(interval=1)
        
        # 内存使用情况
        memory = psutil.virtual_memory()
        memory_total_gb = memory.total / (1024 ** 3)
        memory_used_gb = memory.used / (1024 ** 3)
        memory_percent = memory.percent
        
        # 磁盘使用情况
        disk = psutil.disk_usage('/')
        disk_total_gb = disk.total / (1024 ** 3)
        disk_used_gb = disk.used / (1024 ** 3)
        disk_percent = disk.percent
        
        # 系统启动时间
        boot_time = datetime.fromtimestamp(psutil.boot_time())
        uptime = datetime.now() - boot_time
        
        # 网络 I/O
        net_io = psutil.net_io_counters()
        
        # 构建系统状态消息
        message = (
            "🖥️ *VPS 系统状态*\n\n"
            f"*CPU 使用率:* {cpu_percent}%\n"
            f"*内存使用:* {memory_used_gb:.1f}GB / {memory_total_gb:.1f}GB ({memory_percent}%)\n"
            f"*磁盘使用:* {disk_used_gb:.1f}GB / {disk_total_gb:.1f}GB ({disk_percent}%)\n"
            f"*系统运行时间:* {str(uptime).split('.')[0]}\n"
            f"*网络发送:* {net_io.bytes_sent / (1024 ** 2):.1f} MB\n"
            f"*网络接收:* {net_io.bytes_recv / (1024 ** 2):.1f} MB\n\n"
            f"*更新时间:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        
        await update.message.reply_text(message, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Htop command error: {e}")
        await update.message.reply_text(f"❌ 获取系统信息出错: {str(e)}")

async def startup(application: Application):
    await cache.init_db()
    logger.info("Database initialized")
    logger.info(f"Log file location: {LOG_FILE}")

async def shutdown(application: Application):
    logger.info("Bot is shutting down...")

def main():
    # 创建应用实例
    application = Application.builder().token(config.TELEGRAM_TOKEN).build()
    
    # 添加消息处理器
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    # 添加命令处理器
    application.add_handler(CommandHandler("htop", htop_command))
    
    # 设置启动和关闭处理
    application.post_init = startup
    application.post_shutdown = shutdown
    
    # 启动机器人
    logger.info("Bot is starting...")
    application.run_polling()

if __name__ == "__main__":
    main()