# source rss_venv/bin/activate
# pip install psutil python-dotenv python-telegram-bot aiohttp
import os
import re
import asyncio
import signal
import hashlib
import psutil
import time
import subprocess
import shlex
import aiohttp
from datetime import datetime
from typing import List, Optional, Tuple
from functools import wraps
from collections import OrderedDict
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CommandHandler
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
logging.getLogger('aiohttp').setLevel(logging.WARNING)

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
        self.DEEPL_API_KEY = self._get_env_optional('DEEPL_API_KEY')
        self.DEEPL_API_URL = self._get_env_optional('DEEPL_API_URL', 'https://api-free.deepl.com/v2/translate')

    def _get_env(self, var_name: str) -> str:
        value = os.getenv(var_name)
        if not value:
            logger.error(f"Missing required environment variable: {var_name}")
            raise ValueError(f"Missing required environment variable: {var_name}")
        return value

    def _get_env_optional(self, var_name: str, default: str = None) -> str:
        """获取可选环境变量，不存在时返回默认值"""
        value = os.getenv(var_name)
        return value if value else default

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
# 内存缓存（LRU，上限 2000 条）
# ============================================================
class InMemoryCache:
    """基于 OrderedDict 的 LRU 内存缓存，超过上限自动淘汰最久未使用的条目"""

    def __init__(self, max_size: int = 2000):
        self._data: OrderedDict = OrderedDict()
        self._max = max_size
        self._hits = 0
        self._misses = 0
        self._lock = asyncio.Lock()

    def _make_key(self, source_text: str, source_lang: str, target_lang: str) -> str:
        # 用 MD5 摘要作为 key，避免长文本把 key 本身撑大
        raw = f"{source_lang}|{target_lang}|{source_text}"
        return hashlib.md5(raw.encode('utf-8')).hexdigest()

    async def get(self, source_text: str, source_lang: str, target_lang: str) -> Optional[str]:
        key = self._make_key(source_text, source_lang, target_lang)
        async with self._lock:
            if key in self._data:
                self._data.move_to_end(key)   # 标记为最近使用
                self._hits += 1
                return self._data[key]
            self._misses += 1
            return None

    async def set(self, source_text: str, source_lang: str, target_lang: str, translated_text: str) -> bool:
        key = self._make_key(source_text, source_lang, target_lang)
        async with self._lock:
            self._data[key] = translated_text
            self._data.move_to_end(key)
            if len(self._data) > self._max:
                self._data.popitem(last=False)  # 淘汰最久未使用的
        return True

    async def get_stats(self) -> dict:
        async with self._lock:
            total = self._hits + self._misses
            return {
                'total_entries': len(self._data),
                'max_entries': self._max,
                'hits': self._hits,
                'misses': self._misses,
                'hit_rate': f"{self._hits / total * 100:.1f}%" if total > 0 else "N/A"
            }

cache = InMemoryCache(max_size=2000)

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
# 翻译器（LibreTranslate + DeepL 降级）
# ============================================================
class LibreTranslator:
    def __init__(self):
        self.libretranslate_url = config.LIBRETRANSLATE_URL
        self.deepl_api_key = config.DEEPL_API_KEY
        self.deepl_api_url = config.DEEPL_API_URL
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """复用全局 aiohttp session，避免每次翻译重新建连"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        """关闭全局 session（在 bot shutdown 时调用）"""
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def translate_with_libretranslate(self, text: str, source_lang: str, target_lang: str) -> Optional[str]:
        """使用 LibreTranslate 翻译"""
        try:
            session = await self._get_session()
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
                        return None
                else:
                    logger.warning(f"⚠️ LibreTranslate 返回状态码: {response.status}")
                    return None
        except asyncio.TimeoutError:
            logger.warning("⚠️ LibreTranslate 请求超时")
            return None
        except aiohttp.ClientError as e:
            logger.warning(f"⚠️ LibreTranslate 网络错误: {e}")
            return None
        except Exception as e:
            logger.warning(f"⚠️ LibreTranslate 翻译失败: {e}")
            return None

    async def translate_with_deepl(self, text: str, target_lang: str) -> Optional[str]:
        """使用 DeepL 翻译（备用）"""
        if not self.deepl_api_key:
            logger.warning("⚠️ DeepL API Key 未配置")
            return None

        lang_map = {
            'zh': 'ZH', 'en': 'EN', 'ja': 'JA', 'ko': 'KO', 'ru': 'RU',
            'fr': 'FR', 'de': 'DE', 'es': 'ES', 'it': 'IT',
            'pt': 'PT', 'nl': 'NL', 'pl': 'PL',
        }
        target = lang_map.get(target_lang, target_lang.upper())

        try:
            session = await self._get_session()
            async with session.post(
                self.deepl_api_url,
                json={"text": [text], "target_lang": target},
                headers={
                    "Authorization": f"DeepL-Auth-Key {self.deepl_api_key}",
                    "Content-Type": "application/json"
                },
                timeout=aiohttp.ClientTimeout(total=15)
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
                    error_text = await response.text()
                    logger.warning(f"⚠️ DeepL 返回状态码: {response.status}, 响应: {error_text}")
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

    async def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """
        翻译主方法
        优先级：LibreTranslate -> DeepL -> 原文
        """
        if not text or not text.strip():
            return text

        result = await self.translate_with_libretranslate(text, source_lang, target_lang)
        if result is not None:
            return result

        logger.info("🔄 尝试 DeepL 翻译（备用）...")
        result = await self.translate_with_deepl(text, target_lang)
        if result is not None:
            return result

        logger.info("ℹ️ 所有翻译服务均失败，返回原文")
        return text

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

    # 第一步：检查内存缓存
    cached = await cache.get(text, source_lang, target_lang)
    if cached:
        await send_long_message(update, cached)
        logger.info(f"Cache hit for: '{text[:50]}...'")
        return

    # 第二步：翻译
    try:
        translated = await translator.translate(text, source_lang, target_lang)

        if translated != text:
            await cache.set(text, source_lang, target_lang, translated)

        await send_long_message(update, translated)

    except Exception as e:
        logger.error(f"Translation error: {e}")
        await update.message.reply_text(f"❌ 翻译出错: {str(e)}")

async def send_long_message(update: Update, text: str, chunk_size: int = 3900):
    """分片发送长消息，分片之间加延时避免触发 Telegram 限流"""
    idx, length = 0, len(text)
    first_chunk = True
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
        # 分片之间加延时，避免 Telegram 每秒 1 条的软限制
        if idx < length:
            await asyncio.sleep(0.5)

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
            f"📊 *缓存统计（内存）*\n"
            f"*当前条目:* {cache_stats['total_entries']}/{cache_stats['max_entries']}条\n"
            f"*命中次数:* {cache_stats['hits']}次\n"
            f"*未命中:* {cache_stats['misses']}次\n"
            f"*命中率:* {cache_stats['hit_rate']}\n\n"
            f"*更新时间:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

        await update.message.reply_text(message, parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Htop command error: {e}")
        await update.message.reply_text(f"❌ 获取系统信息出错: {str(e)}")

# ============================================================
# 应用生命周期管理
# ============================================================
async def startup(application):
    logger.info("Bot started")

async def shutdown(application):
    logger.info("Shutting down bot...")
    try:
        await translator.close()
        logger.info("HTTP session closed")
    except Exception as e:
        logger.error(f"Close session failed: {e}")
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

        # ---- 优雅退出：注册 SIGINT / SIGTERM 信号处理 ----
        def _signal_handler(signum, frame):
            logger.info(f"Received signal {signum}, shutting down gracefully...")
            # stop_running() 会让 run_polling 退出循环，随后触发 post_shutdown
            if application.updater and application.updater.running:
                application.updater.stop()
            if application.running:
                application.stop_running()

        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)

        logger.info("Bot is starting...")
        application.run_polling()

    except Exception as e:
        logger.critical(f"Failed to start bot: {e}")
        raise

if __name__ == "__main__":
    main()