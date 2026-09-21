# source rss_venv/bin/activate
# pip install python-dotenv python-telegram-bot Pillow google-genai md2tgmd aiohttp
# sudo systemctl restart gpt.service
# /root/rss/rss_venv/bin/python /root/rss/gpt.py

import asyncio
import os
import time
import traceback
import io
import re
from typing import Dict, List, Optional
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ParseMode
from PIL import Image
from google import genai
from google.genai import types
from md2tgmd import escape
import aiohttp
from aiohttp import ClientTimeout


# ==================== 加载环境变量 ====================

load_dotenv()


# ==================== 配置信息 ====================

TG_TOKEN = os.getenv("TELEGRAM_GEMINI_KEY")
GOOGLE_GEMINI_KEY = os.getenv("GEMINI_API_KEY")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
ALLOWED_USER_IDS_STR = os.getenv("TELEGRAM_CHAT_ID")
DEFAULT_MODEL = os.getenv("GPT_ENGINE")


# ==================== 上下文配置 ====================

# 1轮 = 用户1条 + AI1条
MAX_HISTORY_TURNS = 10
MAX_HISTORY_MESSAGES = MAX_HISTORY_TURNS * 2

# 单条消息最大字节数
MAX_SINGLE_MESSAGE_BYTES = 4000


# ==================== 超时配置 ====================

POLLING_TIMEOUT = int(os.getenv("POLLING_TIMEOUT", "45"))

GEMINI_TIMEOUT = 60
DEEPSEEK_TIMEOUT = 60
IMAGE_DOWNLOAD_TIMEOUT = 30

# Telegram 长消息分段间隔
MESSAGE_SEGMENT_DELAY = 0.15


# ==================== 可用模型 ====================

AVAILABLE_MODELS = {
    "gemini-3.5-flash-lite": "(免费)",
    "deepseek-v4-flash": "(通用对话)",
    "deepseek-v4-pro": "(推理专用)"
}


# ==================== 错误信息 ====================

ERROR_INFO = "⚠️⚠️⚠️\n出了问题 !\n请尝试更改您的提示或联系管理员 !"
BEFORE_GENERATE_INFO = "🤖Generating🤖"
DOWNLOAD_PIC_NOTIFY = "🤖Loading picture🤖"


# ==================== 初始化允许用户 ====================

try:
    ALLOWED_USER_IDS = [
        int(user_id.strip())
        for user_id in ALLOWED_USER_IDS_STR.split(",")
    ] if ALLOWED_USER_IDS_STR else []
except ValueError:
    exit(1)


# ==================== 初始化 Gemini 客户端 ====================

try:
    client = genai.Client(api_key=GOOGLE_GEMINI_KEY)
except Exception as e:
    print(f"Gemini客户端初始化失败: {e}")
    exit(1)


# ==================== 用户会话 ====================

class UserSession:
    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        deepseek_history: List = None
    ):
        self.model_name = model_name
        self.last_activity = time.time()
        self.message_count = 0
        self.total_tokens = 0

        # Gemini 手动管理的对话历史
        self.gemini_history: List[Dict[str, str]] = []

        # DeepSeek 对话历史
        self.deepseek_history = deepseek_history or []


# 用户会话字典
user_sessions: Dict[int, UserSession] = {}


# ==================== 用户级锁 ====================

# 同一个用户的 AI 请求必须串行。
# 不同用户之间不会互相阻塞。
user_locks: Dict[int, asyncio.Lock] = {}


def get_user_lock(user_id: int) -> asyncio.Lock:
    """获取用户专属锁。"""
    if user_id not in user_locks:
        user_locks[user_id] = asyncio.Lock()

    return user_locks[user_id]


def cleanup_user_lock(user_id: int):
    """在用户没有会话时清理对应锁。"""
    lock = user_locks.get(user_id)

    if lock is not None and not lock.locked():
        user_locks.pop(user_id, None)


# ==================== HTTP Session ====================

# 复用 aiohttp ClientSession。
#
# 原来的代码每次 DeepSeek 请求、每次图片下载都会重新创建
# ClientSession。
#
# 现在整个 Application 生命周期内复用一个 Session。
http_session: Optional[aiohttp.ClientSession] = None


async def init_resources(application: Application):
    """Telegram Application 启动时初始化 HTTP Session。"""
    global http_session

    timeout = ClientTimeout(total=60)

    http_session = aiohttp.ClientSession(
        timeout=timeout
    )

    print("✅ aiohttp ClientSession 已初始化")


async def close_resources(application: Application):
    """Telegram Application 关闭时释放 HTTP Session。"""
    global http_session

    if http_session is not None and not http_session.closed:
        await http_session.close()
        print("✅ aiohttp ClientSession 已关闭")

    http_session = None


# ==================== 配置验证 ====================

def validate_config():
    """验证配置。"""

    errors = []

    if not TG_TOKEN:
        errors.append("TELEGRAM_GEMINI_KEY 未设置")

    if not GOOGLE_GEMINI_KEY:
        errors.append("GEMINI_API_KEY 未设置")

    if not ALLOWED_USER_IDS:
        errors.append("TELEGRAM_CHAT_ID 未设置")

    if errors:
        for error in errors:
            print(f"❌ {error}")

        return False

    return True


# ==================== 历史清理 ====================

def trim_history(history: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """
    统一的历史清理：

    1. 最多保留最近 MAX_HISTORY_MESSAGES 条
    2. 单条消息不超过 MAX_SINGLE_MESSAGE_BYTES
    """

    if not history:
        return history

    # 保留最近消息
    trimmed = history[-MAX_HISTORY_MESSAGES:]

    cleaned = []

    for msg in trimmed:
        content = msg.get("content", "")

        if len(content.encode("utf-8")) > MAX_SINGLE_MESSAGE_BYTES:

            encoded = content.encode("utf-8")[
                :MAX_SINGLE_MESSAGE_BYTES
            ]

            content = (
                encoded.decode("utf-8", errors="ignore")
                + "...[已截断]"
            )

        cleaned.append({
            "role": msg["role"],
            "content": content
        })

    return cleaned


# ==================== 辅助函数 ====================

def get_current_model_info(user_id: int) -> str:
    """获取当前模型信息。"""

    if user_id in user_sessions:

        model_name = user_sessions[user_id].model_name

        return (
            f"`{model_name}` - "
            f"{AVAILABLE_MODELS.get(model_name, '未知模型')}"
        )

    return (
        f"`{DEFAULT_MODEL}` - "
        f"{AVAILABLE_MODELS.get(DEFAULT_MODEL, '默认模型')}"
    )


def get_user_session(
    user_id: int,
    model_name: str = None
) -> UserSession:
    """获取或创建用户会话。"""

    now = time.time()

    # 清理超过 1 小时没有活动的会话
    expired_users = [
        uid
        for uid, session in user_sessions.items()
        if now - session.last_activity > 3600
    ]

    for uid in expired_users:
        del user_sessions[uid]
        cleanup_user_lock(uid)

    # 创建新会话
    if user_id not in user_sessions:

        if not model_name:
            model_name = DEFAULT_MODEL

        user_sessions[user_id] = UserSession(model_name)

    else:

        current_session = user_sessions[user_id]

        # 切换模型时创建新的会话
        if (
            model_name
            and model_name != current_session.model_name
        ):
            user_sessions[user_id] = UserSession(model_name)

        else:
            current_session.last_activity = now

    return user_sessions[user_id]


def clear_user_context(user_id: int):
    """清空用户对话上下文。"""

    if user_id in user_sessions:
        del user_sessions[user_id]


def is_user_allowed(update: Update):
    """检查用户权限。"""

    return (
        update.effective_user
        and update.effective_user.id in ALLOWED_USER_IDS
    )


def prepare_markdown_segment(text: str) -> str:
    """使用 md2tgmd.escape 统一转义文本。"""

    return escape(text)


# ============================================================
# Gemini API
# ============================================================

async def call_gemini_api(
    user_message: str,
    user_session: UserSession,
    image_data: bytes = None
) -> str:
    """调用新版 Gemini API。"""

    try:

        system_prompt = "请用中文回复所有内容。"

        # 清理历史
        history = trim_history(
            user_session.gemini_history
        )

        # ==================== 当前消息 ====================

        if image_data:

            content_parts = []

            content_parts.append(
                types.Part(
                    text=(
                        f"{system_prompt}\n{user_message}"
                        if not history
                        else user_message
                    )
                )
            )

            content_parts.append(
                types.Part(
                    inline_data=types.Blob(
                        mime_type="image/jpeg",
                        data=image_data
                    )
                )
            )

            current_content = types.Content(
                role="user",
                parts=content_parts
            )

        else:

            current_content = types.Content(
                role="user",
                parts=[
                    types.Part(
                        text=(
                            f"{system_prompt}\n{user_message}"
                            if not history
                            else user_message
                        )
                    )
                ]
            )

        # ==================== 构建历史 ====================

        full_history = []

        for h in history:

            full_history.append(
                types.Content(
                    role=h["role"],
                    parts=[
                        types.Part(
                            text=h["content"]
                        )
                    ]
                )
            )

        full_history.append(current_content)

        # ==================== 调用 Gemini ====================

        response = await asyncio.wait_for(
            asyncio.to_thread(
                client.models.generate_content,
                model=user_session.model_name,
                contents=full_history
            ),
            timeout=GEMINI_TIMEOUT
        )

        full_response = response.text

        # ==================== 更新历史 ====================

        user_session.gemini_history.append({
            "role": "user",
            "content": user_message
        })

        user_session.gemini_history.append({
            "role": "model",
            "content": full_response
        })

        user_session.gemini_history = trim_history(
            user_session.gemini_history
        )

        user_session.last_activity = time.time()
        user_session.message_count += 1

        return full_response

    except asyncio.TimeoutError:

        raise Exception(
            f"Gemini API 请求超时（{GEMINI_TIMEOUT}秒），请稍后重试"
        )

    except Exception as e:

        raise Exception(
            f"Gemini API 调用失败: {str(e)}"
        )


# ============================================================
# DeepSeek API
# ============================================================

async def call_deepseek_api(
    user_message: str,
    user_session: UserSession
) -> str:
    """
    调用 DeepSeek API。

    优化：
    1. 每次请求都发送 system prompt
    2. 复用全局 aiohttp ClientSession
    """

    if not DEEPSEEK_API_KEY:
        raise Exception("DeepSeek API Key 未配置")

    if http_session is None or http_session.closed:
        raise Exception("HTTP Session 尚未初始化")

    # ==================== 系统提示 ====================

    system_prompt = (
        "请用中文回复所有内容，使用标准Markdown格式。"
    )

    # ==================== 历史 ====================

    history = trim_history(
        user_session.deepseek_history
    )

    # ==================== 构建消息 ====================

    # 关键修改：
    # system prompt 每次请求都发送，
    # 而不是只有第一次对话发送。

    messages = [
        {
            "role": "system",
            "content": system_prompt
        }
    ]

    messages.extend(history)

    messages.append({
        "role": "user",
        "content": user_message
    })

    # ==================== API 数据 ====================

    data = {
        "model": user_session.model_name,
        "messages": messages,
        "stream": False,
        "max_tokens": 4000
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}"
    }

    # ==================== 请求 ====================

    try:

        async with http_session.post(
            "https://api.deepseek.com/chat/completions",
            json=data,
            headers=headers,
            timeout=DEEPSEEK_TIMEOUT
        ) as response:

            if response.status != 200:

                error_text = await response.text()

                raise Exception(
                    f"DeepSeek API 错误: "
                    f"{response.status} - {error_text}"
                )

            result = await response.json()

            try:
                full_response = (
                    result["choices"][0]
                    ["message"]["content"]
                )
            except (KeyError, IndexError, TypeError):
                raise Exception(
                    f"DeepSeek 返回数据格式异常: {result}"
                )

    except asyncio.TimeoutError:

        raise Exception(
            f"DeepSeek API 请求超时（{DEEPSEEK_TIMEOUT}秒），"
            "请稍后重试"
        )

    # ==================== 更新历史 ====================

    user_session.deepseek_history.append({
        "role": "user",
        "content": user_message
    })

    user_session.deepseek_history.append({
        "role": "assistant",
        "content": full_response
    })

    # 最多保留 20 条
    user_session.deepseek_history = trim_history(
        user_session.deepseek_history
    )

    user_session.last_activity = time.time()
    user_session.message_count += 1

    return full_response


# ============================================================
# 消息分割
# ============================================================

def split_messages(text: str) -> List[str]:
    """
    智能分割 Telegram 消息。

    规则：

    1. 优先按照空段落分割
    2. 不破坏代码块的基本结构
    3. 每段不超过 3900 字节
    4. 超长段落进一步按照中英文句号分割
    5. 支持：
       .
       !
       ?
       。
       ！
       ？
    """

    MAX_BYTES = 3900

    chunks = []
    current_chunk = ""

    # ==================== 第一阶段：段落分割 ====================

    paragraphs = text.split("\n\n")

    for para in paragraphs:

        para_bytes_len = len(
            para.encode("utf-8")
        )

        current_chunk_bytes_len = len(
            current_chunk.encode("utf-8")
        )

        if (
            current_chunk_bytes_len
            + 4
            + para_bytes_len
            > MAX_BYTES
        ):

            if current_chunk:
                chunks.append(current_chunk)

            current_chunk = para

        else:

            if current_chunk:

                current_chunk += (
                    "\n\n" + para
                )

            else:

                current_chunk = para

    if current_chunk:
        chunks.append(current_chunk)

    # ==================== 第二阶段：超长段落 ====================

    final_chunks = []

    for chunk in chunks:

        chunk_bytes_len = len(
            chunk.encode("utf-8")
        )

        if chunk_bytes_len <= MAX_BYTES:

            final_chunks.append(chunk)
            continue

        # 支持中文和英文标点
        #
        # 原来：
        # (?<=[.!?])\s+
        #
        # 现在：
        # (?<=[.!?。！？])\s*
        #
        # 中文句号后面通常没有空格，
        # 所以这里不能要求 \s+。
        sentences = re.split(
            r"(?<=[.!?。！？])\s*",
            chunk
        )

        current = ""
        current_bytes_len = 0

        for sent in sentences:

            if not sent:
                continue

            sent_bytes_len = len(
                sent.encode("utf-8")
            )

            # 如果单句本身就超过 Telegram 限制
            if sent_bytes_len > MAX_BYTES:

                if current:
                    final_chunks.append(current)

                # 按字节继续切割
                encoded = sent.encode("utf-8")

                start = 0

                while start < len(encoded):

                    piece = encoded[
                        start:start + MAX_BYTES
                    ]

                    piece_text = piece.decode(
                        "utf-8",
                        errors="ignore"
                    )

                    if piece_text:
                        final_chunks.append(
                            piece_text
                        )

                    start += MAX_BYTES

                current = ""
                current_bytes_len = 0

                continue

            # 普通句子
            extra_bytes = (
                sent_bytes_len
                if not current
                else 1 + sent_bytes_len
            )

            if (
                current_bytes_len
                + extra_bytes
                > MAX_BYTES
            ):

                if current:
                    final_chunks.append(current)

                current = sent
                current_bytes_len = sent_bytes_len

            else:

                if current:

                    current += " " + sent
                    current_bytes_len += (
                        1 + sent_bytes_len
                    )

                else:

                    current = sent
                    current_bytes_len = sent_bytes_len

        if current:
            final_chunks.append(current)

    return final_chunks


# ============================================================
# 分段发送
# ============================================================

async def send_segmented_message(
    bot,
    chat_id: int,
    message_id: int,
    text: str
):
    """分段发送 Telegram 消息。"""

    chunks = split_messages(text)

    if not chunks:
        return

    sent_messages = []

    for i, chunk in enumerate(chunks):

        try:

            if i == 0:

                sent_msg = await bot.send_message(
                    chat_id,
                    escape(chunk),
                    reply_to_message_id=message_id,
                    parse_mode=ParseMode.MARKDOWN_V2
                )

            else:

                sent_msg = await bot.send_message(
                    chat_id,
                    escape(chunk),
                    parse_mode=ParseMode.MARKDOWN_V2
                )

            sent_messages.append(sent_msg)

        except Exception:

            # Markdown V2 失败时发送普通文本
            if i == 0:

                sent_msg = await bot.send_message(
                    chat_id,
                    chunk,
                    reply_to_message_id=message_id
                )

            else:

                sent_msg = await bot.send_message(
                    chat_id,
                    chunk
                )

            sent_messages.append(sent_msg)

        # 比原来的 0.3 秒更快
        await asyncio.sleep(
            MESSAGE_SEGMENT_DELAY
        )

    return sent_messages


# ============================================================
# AI 处理
# ============================================================

async def ai_handler(
    bot,
    chat_id: int,
    message_id: int,
    user_message: str,
    model_type: str,
    user_id: int
):
    """
    统一 AI 处理函数。

    用户级锁由调用方负责。
    """

    sent_message = None

    try:

        # ==================== 生成中提示 ====================

        sent_message = await bot.send_message(
            chat_id,
            BEFORE_GENERATE_INFO,
            reply_to_message_id=message_id
        )

        # ==================== 获取用户会话 ====================

        try:

            user_session = get_user_session(
                user_id,
                model_type
            )

        except Exception:

            clear_user_context(user_id)

            user_session = get_user_session(
                user_id,
                model_type
            )

        full_response = ""

        # ==================== Gemini ====================

        if model_type.startswith("gemini"):

            try:

                full_response = await call_gemini_api(
                    user_message,
                    user_session
                )

            except Exception as e:

                await bot.edit_message_text(
                    f"{ERROR_INFO}\n错误详情: {str(e)}",
                    chat_id=chat_id,
                    message_id=sent_message.message_id
                )

                return

        # ==================== DeepSeek ====================

        else:

            try:

                full_response = await call_deepseek_api(
                    user_message,
                    user_session
                )

            except Exception as e:

                await bot.edit_message_text(
                    f"{ERROR_INFO}\n错误详情: {str(e)}",
                    chat_id=chat_id,
                    message_id=sent_message.message_id
                )

                return

        # ==================== 处理响应 ====================

        if not full_response:
            return

        response_bytes = len(
            full_response.encode("utf-8")
        )

        # ==================== 长消息 ====================

        if response_bytes > 3900:

            try:

                await bot.edit_message_text(
                    "📤 正在分段发送响应...",
                    chat_id=chat_id,
                    message_id=sent_message.message_id
                )

            except Exception as e:

                print(
                    f"编辑消息失败: {e}"
                )

            sent_messages = (
                await send_segmented_message(
                    bot,
                    chat_id,
                    message_id,
                    full_response
                )
            )

            # 删除 Generating
            if sent_messages:

                try:

                    await bot.delete_message(
                        chat_id,
                        sent_message.message_id
                    )

                except Exception:

                    try:

                        await bot.edit_message_text(
                            "✅ 响应已发送完成",
                            chat_id=chat_id,
                            message_id=sent_message.message_id
                        )

                    except Exception:
                        pass

        # ==================== 短消息 ====================

        else:

            try:

                await bot.edit_message_text(
                    escape(full_response),
                    chat_id=chat_id,
                    message_id=sent_message.message_id,
                    parse_mode=ParseMode.MARKDOWN_V2
                )

            except Exception:

                await bot.edit_message_text(
                    full_response,
                    chat_id=chat_id,
                    message_id=sent_message.message_id
                )

    except asyncio.TimeoutError:

        if sent_message:

            try:

                await bot.edit_message_text(
                    "⏰ 请求超时，请稍后重试",
                    chat_id=chat_id,
                    message_id=sent_message.message_id
                )

            except Exception:
                pass

    except Exception as e:

        if sent_message:

            try:

                await bot.edit_message_text(
                    f"{ERROR_INFO}\n错误详情: {str(e)}",
                    chat_id=chat_id,
                    message_id=sent_message.message_id
                )

            except Exception:

                await bot.send_message(
                    chat_id,
                    f"{ERROR_INFO}\n错误详情: {str(e)}",
                    reply_to_message_id=message_id
                )


# ============================================================
# 图片下载
# ============================================================

async def download_image_with_retry(
    file_id: str,
    application: Application
) -> Optional[bytes]:
    """
    图片下载。

    优化：
    使用全局 aiohttp ClientSession，
    不再每次创建新的 Session。
    """

    try:

        if http_session is None or http_session.closed:
            return None

        file = await application.bot.get_file(
            file_id
        )

        file_url = file.file_path

        async with http_session.get(
            file_url,
            timeout=IMAGE_DOWNLOAD_TIMEOUT
        ) as response:

            response.raise_for_status()

            return await response.read()

    except Exception as e:

        print(
            f"图片下载失败: {e}"
        )

        return None


# ============================================================
# 图片 Gemini 处理
# ============================================================

async def gemini_edit_handler(
    bot,
    chat_id: int,
    message_id: int,
    user_message: str,
    photo_file: bytes,
    user_id: int
):
    """图片分析处理。"""

    processing_msg = None

    try:

        processing_msg = await bot.send_message(
            chat_id,
            DOWNLOAD_PIC_NOTIFY,
            reply_to_message_id=message_id
        )

        # 图片固定使用 Gemini
        user_session = get_user_session(
            user_id,
            "gemini-3.5-flash-lite"
        )

        try:

            full_response = await call_gemini_api(
                user_message,
                user_session,
                photo_file
            )

            try:

                await bot.delete_message(
                    chat_id,
                    processing_msg.message_id
                )

            except Exception:
                pass

            if full_response:

                await send_segmented_message(
                    bot,
                    chat_id,
                    message_id,
                    full_response
                )

        except Exception as e:

            await bot.edit_message_text(
                f"{ERROR_INFO}\nError: {str(e)}",
                chat_id=chat_id,
                message_id=processing_msg.message_id
            )

    except Exception as e:

        await bot.send_message(
            chat_id,
            f"{ERROR_INFO}\nError: {str(e)}",
            reply_to_message_id=message_id
        )


# ============================================================
# /start
# ============================================================

async def handle_start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    """处理 /start。"""

    if not is_user_allowed(update):
        return

    help_text = """
🤖 **AI 助手机器人**

# 简化命令：

`/new`   - 开始新对话（清空上下文）
`/model` - 切换AI模型
`/setup` - 设置选项

# 当前默认模型：

{model_info}

直接发送消息开始对话！
""".format(
        model_info=get_current_model_info(
            update.effective_user.id
        )
    )

    await update.message.reply_text(
        prepare_markdown_segment(help_text),
        parse_mode=ParseMode.MARKDOWN_V2
    )


# ============================================================
# /new
# ============================================================

async def handle_new_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    """处理 /new。"""

    if not is_user_allowed(update):
        return

    user_id = update.effective_user.id

    # 与 AI 请求共用同一个用户锁。
    # 防止 /new 与正在进行的 AI 请求同时修改会话。
    lock = get_user_lock(user_id)

    async with lock:

        clear_user_context(user_id)

    await update.message.reply_text(
        "🆕 已开始新对话，上下文历史已清空"
    )


# ============================================================
# /model
# ============================================================

async def handle_model_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    """处理 /model。"""

    if not is_user_allowed(update):
        return

    user_id = update.effective_user.id

    lock = get_user_lock(user_id)

    async with lock:

        if not context.args:

            current_model = get_current_model_info(
                user_id
            )

            model_text = f"""
🔄 **模型切换**

**当前模型：**

{current_model}

**gemini:**

`/model gemini-3.5-flash-lite`  (免费)

**deekseek:**

`/model deepseek-v4-flash`          (通用对话)

`/model deepseek-v4-pro`  (推理专用)

**直接点击上面的命令即可切换**
"""

            await update.message.reply_text(
                prepare_markdown_segment(model_text),
                parse_mode=ParseMode.MARKDOWN_V2
            )

            return

        model_name = context.args[0].strip()

        if model_name not in AVAILABLE_MODELS:

            available_models = "\n".join(
                [
                    f"• `{model}` - {desc}"
                    for model, desc
                    in AVAILABLE_MODELS.items()
                ]
            )

            await update.message.reply_text(
                prepare_markdown_segment(
                    f"❌ 无效的模型名称。\n\n"
                    f"可用模型：\n"
                    f"{available_models}"
                ),
                parse_mode=ParseMode.MARKDOWN_V2
            )

            return

        if user_id in user_sessions:

            if (
                user_sessions[user_id].model_name
                == model_name
            ):

                await update.message.reply_text(
                    prepare_markdown_segment(
                        f"ℹ️ 已经是 `{model_name}` 模型"
                    ),
                    parse_mode=ParseMode.MARKDOWN_V2
                )

                return

            else:

                del user_sessions[user_id]

        try:

            get_user_session(
                user_id,
                model_name
            )

            await update.message.reply_text(
                prepare_markdown_segment(
                    f"✅ 已切换到模型："
                    f"`{model_name}`\n"
                    f"{AVAILABLE_MODELS[model_name]}"
                ),
                parse_mode=ParseMode.MARKDOWN_V2
            )

        except Exception as e:

            await update.message.reply_text(
                prepare_markdown_segment(
                    f"❌ 切换模型失败：{str(e)}"
                ),
                parse_mode=ParseMode.MARKDOWN_V2
            )


# ============================================================
# /setup
# ============================================================

async def handle_setup_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    """处理 /setup。"""

    if not is_user_allowed(update):
        return

    setup_text = """
⚙️ **设置选项**

# 快捷操作：

`/new`        - 🆕 清空对话历史
`/model`      - 🔄 切换AI模型
`/clear`      - 🔄 清空对话上下文
`/start`      - 🤖 显示帮助信息

**系统状态：**

• 默认模型：{model_info}
• 上下文管理：✅ 智能清理

**使用提示：**

直接发送消息即可开始对话！

发送图片可进行图像分析
""".format(
        model_info=get_current_model_info(
            update.effective_user.id
        )
    )

    await update.message.reply_text(
        prepare_markdown_segment(setup_text),
        parse_mode=ParseMode.MARKDOWN_V2
    )


# ============================================================
# /clear
# ============================================================

async def handle_clear_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    """清空对话上下文。"""

    if not is_user_allowed(update):
        return

    user_id = update.effective_user.id

    lock = get_user_lock(user_id)

    async with lock:

        clear_user_context(user_id)

    await update.message.reply_text(
        "✅ 对话历史已清空"
    )


# ============================================================
# 图片消息
# ============================================================

async def handle_photo_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    """处理图片消息。"""

    if not is_user_allowed(update):
        return

    user_id = update.effective_user.id

    file_id = update.message.photo[-1].file_id

    # 图片下载不占用用户 AI 锁，
    # 所以多个用户可以同时下载图片。
    photo_data = await download_image_with_retry(
        file_id,
        context.application
    )

    if not photo_data:

        await update.message.reply_text(
            "Failed to download image"
        )

        return

    user_message = (
        update.message.caption or ""
    )

    # ==================== 用户级串行 ====================

    lock = get_user_lock(user_id)

    async with lock:

        await gemini_edit_handler(
            context.bot,
            update.effective_chat.id,
            update.message.message_id,
            user_message,
            photo_data,
            user_id
        )


# ============================================================
# 普通私聊消息
# ============================================================

async def handle_private_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    """处理私聊消息。"""

    if (
        not is_user_allowed(update)
        or update.effective_chat.type != "private"
    ):
        return

    user_message = update.message.text.strip()

    user_id = update.effective_user.id

    if user_id in user_sessions:

        model_type = (
            user_sessions[user_id].model_name
        )

    else:

        model_type = DEFAULT_MODEL

    # ==================== 用户级串行 ====================
    #
    # 同一个用户：
    #
    # 问题1 → AI
    # 问题2 → 等待
    # 问题3 → 等待
    #
    # 不同用户：
    #
    # A → AI
    # B → AI
    # C → AI
    #
    # 可以同时运行。

    lock = get_user_lock(user_id)

    async with lock:

        await ai_handler(
            context.bot,
            update.effective_chat.id,
            update.message.message_id,
            user_message,
            model_type,
            user_id
        )


# ============================================================
# 定期清理
# ============================================================

async def cleanup_task(
    context: ContextTypes.DEFAULT_TYPE
):
    """清理超过 1 小时未活动的会话。"""

    now = time.time()

    expired = [
        uid
        for uid, session in user_sessions.items()
        if now - session.last_activity > 3600
    ]

    for uid in expired:

        del user_sessions[uid]

        cleanup_user_lock(uid)


# ============================================================
# 主函数
# ============================================================

def main():
    """主函数。"""

    if not validate_config():
        return

    # ========================================================
    # 初始化 Telegram Application
    # ========================================================

    application = (
        Application.builder()
        .token(TG_TOKEN)
        .post_init(init_resources)
        .post_shutdown(close_resources)
        .build()
    )

    # ========================================================
    # Commands
    # ========================================================

    application.add_handler(
        CommandHandler(
            "start",
            handle_start_command
        )
    )

    application.add_handler(
        CommandHandler(
            "new",
            handle_new_command
        )
    )

    application.add_handler(
        CommandHandler(
            "model",
            handle_model_command
        )
    )

    application.add_handler(
        CommandHandler(
            "setup",
            handle_setup_command
        )
    )

    application.add_handler(
        CommandHandler(
            "clear",
            handle_clear_command
        )
    )

    # ========================================================
    # 图片
    # ========================================================

    application.add_handler(
        MessageHandler(
            filters.PHOTO,
            handle_photo_message
        )
    )

    # ========================================================
    # 普通私聊文本
    # ========================================================

    application.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND
            & filters.ChatType.PRIVATE,
            handle_private_message
        )
    )

    # ========================================================
    # 定期清理
    # ========================================================

    job_queue = application.job_queue

    job_queue.run_repeating(
        cleanup_task,
        interval=3600,
        first=10
    )

    # ========================================================
    # 启动
    # ========================================================

    print("=========================================")
    print("🤖 AI Telegram Bot")
    print("=========================================")
    print("✅ Bot 正在启动...")
    print("✅ 用户级并发锁：已启用")
    print("✅ aiohttp Session 复用：已启用")
    print("✅ DeepSeek System Prompt：每次请求发送")
    print("✅ 中文消息分割：已优化")
    print(f"✅ 消息分段间隔：{MESSAGE_SEGMENT_DELAY}s")
    print("=========================================")

    application.run_polling(
        allowed_updates=[
            "message",
            "callback_query"
        ],
        timeout=POLLING_TIMEOUT
    )


# ============================================================
# 程序入口
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        pass

    except Exception:

        traceback.print_exc()