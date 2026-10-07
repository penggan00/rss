# source rss_venv/bin/activate
# pip install psutil python-dotenv python-telegram-bot aiohttp
import os
import re
import json
import uuid
import asyncio
import signal
import hashlib
import psutil
import time
import subprocess
import shlex
import aiohttp
import logging
from datetime import datetime
from typing import List, Optional, Tuple
from functools import wraps
from collections import OrderedDict
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CommandHandler
from alibabacloud_alimt20181012.client import Client as AlimtClient
from alibabacloud_alimt20181012 import models as alimt_models
from alibabacloud_tea_openapi import models as open_api_models
# ============================================================
# 基础配置
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

load_dotenv()


# ============================================================
# 配置类
# ============================================================
class Config:
    def __init__(self):
        # Telegram
        self.TELEGRAM_TOKEN = self._get_env('TELEGRAM_API_KEY')
        self.AUTHORIZED_CHAT_IDS = self._parse_chat_ids('TELEGRAM_CHAT_ID')

        # 阿里云机器翻译（主）
        self.ALIYUN_AK_ID = self._get_env('ALIYUN_AK_ID')
        self.ALIYUN_AK_SECRET = self._get_env('ALIYUN_AK_SECRET')
        self.ALIYUN_MT_REGION = self._get_env_optional('ALIYUN_MT_REGION', 'cn-hangzhou')

        # DeepL（备用）
        self.DEEPL_API_KEY = self._get_env('DEEPL_API_KEY')
        self.DEEPL_API_URL = self._get_env_optional(
            'DEEPL_API_URL', 'https://api-free.deepl.com/v2/translate'
        )

    def _get_env(self, var_name: str) -> str:
        value = os.getenv(var_name)
        if not value:
            logger.error(f"Missing required environment variable: {var_name}")
            raise ValueError(f"Missing required environment variable: {var_name}")
        return value

    def _get_env_optional(self, var_name: str, default: str = None) -> str:
        value = os.getenv(var_name)
        return value if value else default

    def _parse_chat_ids(self, var_name: str) -> List[int]:
        ids_str = self._get_env(var_name)
        try:
            return [int(id_str.strip()) for id_str in ids_str.split(',')]
        except ValueError:
            logger.error(f"Invalid {var_name} format")
            raise ValueError(f"Invalid {var_name} format")


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
    def __init__(self, max_size: int = 2000):
        self._data: OrderedDict = OrderedDict()
        self._max = max_size
        self._hits = 0
        self._misses = 0
        self._lock = asyncio.Lock()

    def _make_key(self, source_text: str, source_lang: str, target_lang: str) -> str:
        raw = f"{source_lang}|{target_lang}|{source_text}"
        return hashlib.md5(raw.encode('utf-8')).hexdigest()

    async def get(self, source_text: str, source_lang: str, target_lang: str) -> Optional[str]:
        key = self._make_key(source_text, source_lang, target_lang)
        async with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
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
                self._data.popitem(last=False)
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
    if lang in ('zh', 'ja', 'ko', 'ru', 'en'):
        target = 'en' if lang == 'zh' else 'zh'
        return (lang, target)
    else:
        return ('en', 'zh')


# ============================================================
# 阿里云机器翻译（主）
# ============================================================
class AliyunTranslator:
    """阿里云机器翻译，作为主力引擎"""

    LANG_MAP = {
        'zh': 'zh', 'en': 'en', 'ja': 'ja', 'ko': 'ko',
        'ru': 'ru', 'fr': 'fr', 'de': 'de', 'es': 'es',
        'it': 'it', 'pt': 'pt', 'nl': 'nl', 'pl': 'pl',
        'ar': 'ar', 'th': 'th', 'vi': 'vi', 'id': 'id',
        'tr': 'tr', 'hi': 'hi',
    }

    def __init__(self):
        cfg = open_api_models.Config(
            access_key_id=config.ALIYUN_AK_ID,
            access_key_secret=config.ALIYUN_AK_SECRET,
        )
        cfg.endpoint = f"mt.{config.ALIYUN_MT_REGION}.aliyuncs.com"
        self.client = AlimtClient(cfg)

    async def translate(self, text: str, source_lang: str, target_lang: str) -> Optional[str]:
        """返回译文；失败返回 None，让上层切到 DeepL"""
        if not text or not text.strip():
            return text

        src = self.LANG_MAP.get(source_lang, source_lang)
        tgt = self.LANG_MAP.get(target_lang, target_lang)

        try:
            request = alimt_models.TranslateGeneralRequest(
                format_type='text',
                source_language=src,
                target_language=tgt,
                source_text=text,
                scene='general',
            )
            # 阿里云 SDK 同步，放线程池避免阻塞事件循环
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None, self.client.translate_general, request
            )

            translated = None
            if response and response.body and response.body.data:
                translated = response.body.data.translated

            if translated and translated != text:
                logger.info("✅ 阿里云翻译成功")
                return translated
            logger.warning("⚠️ 阿里云返回空或相同文本")
            return None

        except Exception as e:
            logger.warning(f"⚠️ 阿里云翻译失败: {e}，切换到 DeepL")
            return None

    async def close(self):
        pass  # 阿里云 SDK 无需关闭 session

# ============================================================
# DeepL 翻译（备用）
# ============================================================
class DeepLTranslator:
    LANG_MAP = {
        'zh': 'ZH', 'en': 'EN', 'ja': 'JA', 'ko': 'KO', 'ru': 'RU',
        'fr': 'FR', 'de': 'DE', 'es': 'ES', 'it': 'IT',
        'pt': 'PT', 'nl': 'NL', 'pl': 'PL',
    }

    def __init__(self):
        self.api_key = config.DEEPL_API_KEY
        self.api_url = config.DEEPL_API_URL
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def translate(self, text: str, source_lang: str, target_lang: str) -> Optional[str]:
        """返回译文；失败返回 None"""
        if not text or not text.strip():
            return text

        target = self.LANG_MAP.get(target_lang, target_lang.upper())

        try:
            session = await self._get_session()
            async with session.post(
                self.api_url,
                json={"text": [text], "target_lang": target},
                headers={
                    "Authorization": f"DeepL-Auth-Key {self.api_key}",
                    "Content-Type": "application/json"
                },
                timeout=aiohttp.ClientTimeout(total=15)
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    translated = result.get("translations", [{}])[0].get("text")
                    if translated and translated != text:
                        logger.info("✅ DeepL 翻译成功（备用）")
                        return translated
                    logger.warning("⚠️ DeepL 返回空或相同文本")
                    return None
                else:
                    error_text = await response.text()
                    logger.warning(f"⚠️ DeepL HTTP {response.status}: {error_text[:200]}")
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


# ============================================================
# 统一翻译入口：火山 → DeepL → 原文
# ============================================================
aliyun_translator = AliyunTranslator()
deepl_translator = DeepLTranslator()


async def translate_with_fallback(text: str, source_lang: str, target_lang: str) -> Tuple[str, str]:
    """
    返回 (译文, 使用的引擎)
    引擎: 'volc' / 'deepl' / 'raw'
    """
    # 1) 阿里云
    result = await aliyun_translator.translate(text, source_lang, target_lang)
    if result:
        return result, 'aliyun'

    # 2) DeepL
    result = await deepl_translator.translate(text, source_lang, target_lang)
    if result:
        return result, 'deepl'

    # 3) 原文兜底
    logger.warning("⚠️ 阿里云和 DeepL 都失败，返回原文")
    return text, 'raw'


async def get_deepl_usage() -> Optional[dict]:
    """获取 DeepL 账号额度信息"""
    try:
        session = await deepl_translator._get_session()
        async with session.get(
            config.DEEPL_API_URL.replace('/translate', '/usage'),
            headers={"Authorization": f"DeepL-Auth-Key {config.DEEPL_API_KEY}"},
            timeout=aiohttp.ClientTimeout(total=10)
        ) as response:
            if response.status == 200:
                data = await response.json()
                used = data.get('character_count', 0)
                limit = data.get('character_limit', 0)
                return {
                    'used': used,
                    'limit': limit,
                    'percent': (used / limit * 100) if limit > 0 else 0
                }
            logger.warning(f"DeepL usage API 返回状态码: {response.status}")
            return None
    except Exception as e:
        logger.warning(f"获取 DeepL 额度失败: {e}")
        return None


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
    text = update.message.text
    if not text or len(text) > 5000:
        return

    source_lang, target_lang = get_translation_direction(text)
    logger.info(f"Chat {update.effective_chat.id}: [{source_lang}->{target_lang}] '{text[:80]}...'")

    # 缓存
    cached = await cache.get(text, source_lang, target_lang)
    if cached:
        await send_long_message(update, cached)
        logger.info(f"Cache hit for: '{text[:50]}...'")
        return

    try:
        translated, engine = await translate_with_fallback(text, source_lang, target_lang)

        if engine != 'raw':
            await cache.set(text, source_lang, target_lang, translated)

        # 若引擎是 raw，加个提示前缀方便识别
        if engine == 'raw':
            await send_long_message(update, f"⚠️ 翻译失败，返回原文：\n\n{translated}")
        else:
            await send_long_message(update, translated)

    except Exception as e:
        logger.error(f"Translation error: {e}")
        await update.message.reply_text(f"❌ 翻译出错: {str(e)}")


async def send_long_message(update: Update, text: str, chunk_size: int = 3900):
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
        if idx < length:
            await asyncio.sleep(0.5)


# ============================================================
# 系统命令
# ============================================================
@require_auth
async def cmd_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
        deepl_usage = await get_deepl_usage()

        if deepl_usage:
            deepl_line = (
                f"*已用:* {deepl_usage['used']:,} / {deepl_usage['limit']:,} 字符\n"
                f"*剩余:* {deepl_usage['limit'] - deepl_usage['used']:,} 字符 "
                f"({deepl_usage['percent']:.1f}%)\n"
            )
        else:
            deepl_line = "*额度:* 获取失败\n"

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
            f"🔤 *DeepL（备用）*\n"
            f"{deepl_line}\n"
            f"*更新时间:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

        await update.message.reply_text(message, parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Htop command error: {e}")
        await update.message.reply_text(f"❌ 获取系统信息出错: {str(e)}")


# ============================================================
# 生命周期
# ============================================================
async def startup(application):
    logger.info("Bot started")

async def shutdown(application):
    logger.info("Shutting down bot...")
    try:
        await aliyun_translator.close()
        await deepl_translator.close()
        logger.info("HTTP sessions closed")
    except Exception as e:
        logger.error(f"Close session failed: {e}")
    logger.info("Bot shutdown complete")


# ============================================================
# 主函数
# ============================================================
def main():
    try:
        application = Application.builder().token(config.TELEGRAM_TOKEN).build()

        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
        application.add_handler(CommandHandler("htop", htop_command))
        application.add_handler(CommandHandler("cmd", cmd_command))

        application.post_init = startup
        application.post_shutdown = shutdown

        def _signal_handler(signum, frame):
            logger.info(f"Received signal {signum}, shutting down gracefully...")
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