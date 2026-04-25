# source rss_venv/bin/activate
# pip install psutil python-dotenv tencentcloud-sdk-python python-telegram-bot aiosqlite
import os
import re
import asyncio
import psutil
import time
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

# ============================================================
# 基础配置（必须最先初始化）
# ============================================================
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(PROJECT_ROOT, 'qq.log')

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

logging.getLogger('telegram').setLevel(logging.WARNING)
logging.getLogger('tencentcloud').setLevel(logging.WARNING)
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
        self.TENCENT_SECRET_ID = self._get_env('TENCENT_SECRET_ID')
        self.TENCENT_SECRET_KEY = self._get_env('TENCENT_SECRET_KEY')
        self.TENCENT_REGION = self._get_env('TENCENT_REGION')
        self.TENCENT_PROJECT_ID = int(self._get_env('TENCENT_PROJECT_ID'))
        self.TERM_REPO_IDS = os.getenv('TENCENT_TERM_REPO_IDS', '')
        self.SENT_REPO_IDS = os.getenv('TENCENT_SENT_REPO_IDS', '')

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
# 成本控制跟踪器
# ============================================================
class TranslationCostTracker:
    """翻译成本跟踪器（基于腾讯云计费标准）"""
    
    # 腾讯云文本翻译计费标准
    MONTHLY_FREE_CHARS = 5_000_000  # 每月免费500万字符
    PRICE_TIER_1 = 58  # 0-100百万字符：58元/百万字符
    PRICE_TIER_2 = 50  # 100百万字符及以上：50元/百万字符
    
    def __init__(self):
        self.total_chars = 0
        self.total_requests = 0
        self.cache_hits = 0
        self._lock = asyncio.Lock()
        
    async def record_api_call(self, chars_used: int):
        async with self._lock:
            self.total_chars += chars_used
            self.total_requests += 1
            
    async def record_cache_hit(self):
        async with self._lock:
            self.cache_hits += 1
    
    def _calculate_cost(self, chars: int) -> tuple:
        """计算费用（人民币），返回(费用, 已用字符, 说明)"""
        if chars <= self.MONTHLY_FREE_CHARS:
            return 0, chars, "免费额度内"
        
        billable_chars = chars - self.MONTHLY_FREE_CHARS
        billable_millions = billable_chars / 1_000_000
        
        if billable_millions < 100:
            cost = billable_millions * self.PRICE_TIER_1
            tier = f"阶梯1 (¥{self.PRICE_TIER_1}/百万字符)"
        else:
            cost = billable_millions * self.PRICE_TIER_2
            tier = f"阶梯2 (¥{self.PRICE_TIER_2}/百万字符)"
            
        return round(cost, 2), chars, tier
            
    def get_stats(self) -> dict:
        cost, used, tier_info = self._calculate_cost(self.total_chars)
        remaining_free = max(0, self.MONTHLY_FREE_CHARS - self.total_chars)
        
        return {
            'total_chars': self.total_chars,
            'total_requests': self.total_requests,
            'cache_hits': self.cache_hits,
            'monthly_free': self.MONTHLY_FREE_CHARS,
            'remaining_free': remaining_free,
            'free_used_percent': f"{used/self.MONTHLY_FREE_CHARS*100:.2f}%",
            'cost': cost,
            'cost_display': f"¥{cost:.2f}" if cost > 0 else "免费",
            'tier_info': tier_info if cost > 0 else "免费额度",
        }

cost_tracker = TranslationCostTracker()

# ============================================================
# 数据库连接池管理
# ============================================================
class AsyncTranslationCache:
    """使用持久连接和WAL模式的翻译缓存"""
    
    def __init__(self, db_path: str = 'translations.db'):
        self.db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()
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
                    # 异步更新访问计数
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
            async with self._lock:
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
            logger.warning(f"Failed to update access count: {e}")
        
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
# 翻译器（修复了事件循环问题）
# ============================================================
class TencentTranslator:
    def __init__(self):
        cred = credential.Credential(
            config.TENCENT_SECRET_ID,
            config.TENCENT_SECRET_KEY
        )
        
        http_profile = HttpProfile()
        http_profile.reqMethod = "POST"
        http_profile.reqTimeout = 30
        http_profile.reqKeepAlive = True
        http_profile.endpoint = "tmt.tencentcloudapi.com"
        
        client_profile = ClientProfile()
        client_profile.httpProfile = http_profile
        client_profile.signMethod = "TC3-HMAC-SHA256"
        
        self.client = tmt_client.TmtClient(
            cred, 
            config.TENCENT_REGION, 
            client_profile
        )
        
        self._warmup_done = False
        
    async def warmup(self):
        """预热连接池"""
        if not self._warmup_done:
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self._do_warmup)
                self._warmup_done = True
                logger.info("Translator connection pool warmed up")
            except Exception as e:
                logger.warning(f"Connection pool warmup failed: {e}")
                
    def _do_warmup(self):
        """执行预热请求"""
        try:
            req = models.TextTranslateRequest()
            req.SourceText = "test"
            req.Source = "en"
            req.Target = "zh"
            req.ProjectId = config.TENCENT_PROJECT_ID
            self.client.TextTranslate(req)
        except Exception:
            pass

    async def translate(self, text: str, source_lang: str, target_lang: str, max_retries: int = 3) -> str:
        """带智能重试的翻译方法"""
        loop = asyncio.get_running_loop()
        last_error = None
        
        # 只对网络/服务器错误重试
        retryable_codes = {
            'InternalError',
            'InternalError.BackendTimeout',
            'InternalError.ErrorGetRoute',
            'InternalError.RequestFailed',
            'LimitExceeded.LimitedAccessFrequency',
        }
        
        for attempt in range(1, max_retries + 1):
            try:
                # 在线程池中执行同步API调用
                result = await loop.run_in_executor(
                    None, 
                    self._call_api_sync,  # 使用独立的同步方法
                    text, 
                    source_lang, 
                    target_lang
                )
                
                # 在主事件循环中记录成本
                await cost_tracker.record_api_call(len(text))
                
                return result
                
            except Exception as e:
                last_error = e
                error_code = getattr(e, 'code', '')
                
                # 非重试错误直接抛出
                if error_code and error_code not in retryable_codes:
                    logger.error(f"Non-retryable error: {error_code} - {e}")
                    raise
                
                # 重试次数用完
                if attempt >= max_retries:
                    logger.error(f"Translation failed after {max_retries} attempts: {e}")
                    break
                    
                # 指数退避重试
                wait_time = min(2 ** attempt, 5)
                logger.warning(f"Retry {attempt}/{max_retries} after {wait_time}s: {e}")
                await asyncio.sleep(wait_time)
                
        raise last_error if last_error else Exception("Translation error")
    
    def _call_api_sync(self, text: str, source_lang: str, target_lang: str) -> str:
        """同步API调用（在线程池中执行）"""
        req = models.TextTranslateRequest()
        req.SourceText = text
        req.Source = source_lang
        req.Target = target_lang
        req.ProjectId = config.TENCENT_PROJECT_ID
        
        # 添加术语库和例句库
        if config.TERM_REPO_IDS:
            req.TermRepoIDList = [id.strip() for id in config.TERM_REPO_IDS.split(',') if id.strip()]
        if config.SENT_REPO_IDS:
            req.SentRepoIDList = [id.strip() for id in config.SENT_REPO_IDS.split(',') if id.strip()]
        
        start_time = time.time()
        resp = self.client.TextTranslate(req)
        elapsed = time.time() - start_time
        
        logger.debug(f"API call took {elapsed:.2f}s, chars used: {resp.UsedAmount}")
        
        return resp.TargetText

# 初始化翻译器
translator = TencentTranslator()

# ============================================================
# 异步任务队列
# ============================================================
class TranslationQueue:
    """翻译任务队列"""
    
    def __init__(self, batch_timeout: float = 0.5):
        self.queue = asyncio.Queue()
        self.batch_timeout = batch_timeout
        self._worker_task = None
        self._running = False
        
    async def start(self):
        """启动后台工作协程"""
        self._running = True
        self._worker_task = asyncio.create_task(self._worker())
        logger.info("Translation queue worker started")
        
    async def stop(self):
        """停止工作协程"""
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        logger.info("Translation queue worker stopped")
        
    async def enqueue(self, update: Update, text: str, source_lang: str, target_lang: str):
        """将翻译任务加入队列"""
        future = asyncio.Future()
        await self.queue.put((update, text, source_lang, target_lang, future))
        return future
        
    async def _worker(self):
        """后台工作协程"""
        while self._running:
            try:
                # 获取任务
                try:
                    item = await asyncio.wait_for(self.queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                    
                update, text, source_lang, target_lang, future = item
                
                # 处理翻译
                try:
                    translated = await translator.translate(text, source_lang, target_lang)
                    if not future.done():
                        future.set_result((update, translated))
                except Exception as e:
                    if not future.done():
                        future.set_exception(e)
                        
            except Exception as e:
                logger.error(f"Queue worker error: {e}")
                await asyncio.sleep(1)

translation_queue = TranslationQueue()

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
            await cost_tracker.record_cache_hit()
            await send_long_message(update, cached)
            logger.info(f"Cache hit for: '{text[:50]}...'")
            return
    except Exception as e:
        logger.error(f"Cache get error: {e}")
    
    # 第二步：翻译
    try:
        translated = await translator.translate(text, source_lang, target_lang)
        
        # 缓存结果
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
            # 在空白字符处断句
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
# 系统状态命令
# ============================================================
@require_auth
async def htop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """显示系统状态和翻译统计"""
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
        
        cost_stats = cost_tracker.get_stats()
        # cache_stats = await cache.get_stats()  # 注释掉缓存统计

        message = (
            "🖥️ *系统状态*\n\n"
            f"*CPU:* {cpu_percent}%\n"
            f"*内存:* {memory_used_gb:.1f}/{memory_total_gb:.1f}GB ({memory.percent}%)\n"
            f"*磁盘:* {disk_used_gb:.1f}/{disk_total_gb:.1f}GB ({disk.percent}%)\n"
            f"*运行时间:* {str(uptime).split('.')[0]}\n"
            f"*网络发送:* {net_io.bytes_sent / (1024 ** 2):.1f}MB\n"
            f"*网络接收:* {net_io.bytes_recv / (1024 ** 2):.1f}MB\n\n"
            f"📊 *翻译统计 (腾讯云文本翻译)*\n"
            f"*API调用:* {cost_stats['total_requests']}次\n"
            f"*缓存命中:* {cost_stats['cache_hits']}次\n"
            f"*当前费用:* {cost_stats['cost_display']}\n\n"
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
    
    try:
        await translator.warmup()
        logger.info("Translator warmed up")
    except Exception as e:
        logger.warning(f"Translator warmup failed: {e}")
    
    logger.info(f"Bot started. Log: {LOG_FILE}")

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
    
    stats = cost_tracker.get_stats()
    logger.info(f"Final stats: {stats}")
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
        
        application.post_init = startup
        application.post_shutdown = shutdown
        
        logger.info("Bot is starting...")
        application.run_polling()
        
    except Exception as e:
        logger.critical(f"Failed to start bot: {e}")
        raise

if __name__ == "__main__":
    main()