import asyncio
import os
import time
import re
import functools
import logging
import traceback
import io
from typing import Dict, List, Optional
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ParseMode
from PIL import Image
import google.generativeai as genai
from md2tgmd import escape
import aiohttp
from aiohttp import ClientTimeout

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('gpt.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 加载环境变量
load_dotenv()

# 配置信息
TG_TOKEN = os.getenv("TELEGRAM_GEMINI_KEY")
GOOGLE_GEMINI_KEY = os.getenv("GEMINI_API_KEY")
ALLOWED_USER_IDS_STR = os.getenv("TELEGRAM_CHAT_ID")
DEFAULT_MODEL = os.getenv("GPT_ENGINE", "gemini-2.0-flash")

# 超时配置
STREAM_UPDATE_INTERVAL = float(os.getenv("STREAM_UPDATE_INTERVAL", "1.0"))  # 改为0.5秒，与原项目一致
POLLING_TIMEOUT = int(os.getenv("POLLING_TIMEOUT", "30"))

# 重试配置
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
RETRY_DELAY = float(os.getenv("RETRY_DELAY", "2.0"))

# 可用模型列表
AVAILABLE_MODELS = {
    "gemini-2.5-pro": "Gemini 2.5 Pro (最强能力)",
    "gemini-2.5-flash": "Gemini 2.5 Flash (平衡性能)",  
    "gemini-2.0-flash": "Gemini 2.0 Flash (快速响应)",
    "gemini-1.5-pro": "Gemini 1.5 Pro (长上下文)"  # 添加原项目的模型
}

# 错误信息配置（与原项目一致）
ERROR_INFO = "⚠️⚠️⚠️\nSomething went wrong !\nplease try to change your prompt or contact the admin !"
BEFORE_GENERATE_INFO = "🤖Generating🤖"
DOWNLOAD_PIC_NOTIFY = "🤖Loading picture🤖"

# 初始化配置
try:
    ALLOWED_USER_IDS = [int(user_id.strip()) for user_id in ALLOWED_USER_IDS_STR.split(",")] if ALLOWED_USER_IDS_STR else []
except ValueError:
    logger.error("ALLOWED_USER_IDS 必须是逗号分隔的整数列表。")
    exit(1)

# 初始化Gemini
try:
    genai.configure(api_key=GOOGLE_GEMINI_KEY)
    logger.info("Gemini API initialized")
except Exception as e:
    logger.error(f"Error initializing Gemini API: {e}")
    exit(1)

# 会话管理（与原项目类似的会话结构）
class UserSession:
    def __init__(self, chat_session: genai.ChatSession, model_name: str = DEFAULT_MODEL):
        self.chat_session = chat_session
        self.last_activity = time.time()
        self.model_name = model_name
        self.message_count = 0
        self.total_tokens = 0

# 会话字典（与原项目结构一致）
user_sessions: Dict[int, UserSession] = {}
default_model_dict: Dict[int, bool] = {}  # True: gemini-2.0-flash, False: gemini-1.5-pro

# 重试装饰器
def retry_on_exception(max_retries: int = MAX_RETRIES, delay: float = RETRY_DELAY):
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_retries - 1:
                        raise
                    logger.warning(f"尝试 {func.__name__} 失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                    await asyncio.sleep(delay * (attempt + 1))
            return None
        return wrapper
    return decorator

# 配置验证
def validate_config():
    """验证配置"""
    errors = []
    
    if not TG_TOKEN:
        errors.append("TELEGRAM_GEMINI_KEY 未设置")
    if not GOOGLE_GEMINI_KEY:
        errors.append("GEMINI_API_KEY 未设置")
    if not ALLOWED_USER_IDS:
        errors.append("TELEGRAM_CHAT_ID 未设置")
    
    if errors:
        logger.error("配置错误:")
        for error in errors:
            logger.error(f"  - {error}")
        return False
    
    return True

# 辅助函数
def get_current_model_info(user_id: int) -> str:
    """获取当前模型信息"""
    if user_id in user_sessions:
        model_name = user_sessions[user_id].model_name
        return f"`{model_name}` - {AVAILABLE_MODELS.get(model_name, '未知模型')}"
    return f"`{DEFAULT_MODEL}` - {AVAILABLE_MODELS.get(DEFAULT_MODEL, '默认模型')}"

def get_user_session(user_id: int, model_name: str = None) -> UserSession:
    """智能会话管理 - 自动清理过长上下文"""
    now = time.time()
    
    # 清理过期会话（1小时）
    expired_users = [uid for uid, session in user_sessions.items() if now - session.last_activity > 3600]
    for uid in expired_users:
        logger.info(f"清理过期会话: 用户 {uid}")
        del user_sessions[uid]

    if user_id not in user_sessions:
        if not model_name:
            model_name = DEFAULT_MODEL
        model = genai.GenerativeModel(model_name)
        chat = model.start_chat(history=[])
        user_sessions[user_id] = UserSession(chat, model_name)
        logger.info(f"创建新会话: 用户 {user_id}, 模型 {model_name}")
    else:
        user_sessions[user_id].last_activity = now
        
        # 智能上下文清理策略
        session = user_sessions[user_id]
        if hasattr(session.chat_session, 'history'):
            history_length = len(session.chat_session.history)
            
            # 根据历史长度决定清理策略
            if history_length > 20:
                # 保留最近8轮对话（16条消息）
                keep_count = min(16, history_length)
                session.chat_session.history = session.chat_session.history[-keep_count:]
                logger.info(f"用户 {user_id} 上下文已清理: {history_length} -> {keep_count}")
            elif history_length > 15:
                # 保留最近6轮对话（12条消息）
                keep_count = min(12, history_length)
                session.chat_session.history = session.chat_session.history[-keep_count:]
                logger.info(f"用户 {user_id} 上下文已优化: {history_length} -> {keep_count}")
        
    return user_sessions[user_id]

def clear_user_context(user_id: int):
    """清空用户对话上下文"""
    if user_id in user_sessions:
        del user_sessions[user_id]

def is_user_allowed(update: Update):
    """检查用户权限"""
    return update.effective_user.id in ALLOWED_USER_IDS

def prepare_markdown_segment(text: str) -> str:
    """使用md2tgmd.escape统一转义文本段"""
    return escape(text)

def split_messages(text: str) -> List[str]:
    """智能分割消息"""
    MAX_BYTES = 3800
    chunks = []
    current_chunk = ""

    paragraphs = text.split('\n\n')
    for para in paragraphs:
        para_bytes_len = len(para.encode('utf-8'))
        current_chunk_bytes_len = len(current_chunk.encode('utf-8'))

        if current_chunk_bytes_len + 4 + para_bytes_len > MAX_BYTES:
            if current_chunk:
                chunks.append(current_chunk)
            current_chunk = para
        else:
            current_chunk += '\n\n' + para if current_chunk else para

    if current_chunk:
        chunks.append(current_chunk)

    final_chunks = []
    for chunk in chunks:
        chunk_bytes_len = len(chunk.encode('utf-8'))
        if chunk_bytes_len <= MAX_BYTES:
            final_chunks.append(chunk)
        else:
            sentences = re.split(r'(?<=[.!?])\s+', chunk)
            current = ""
            current_bytes_len = 0
            for sent in sentences:
                sent_bytes_len = len(sent.encode('utf-8'))
                if current_bytes_len + 1 + sent_bytes_len > MAX_BYTES:
                    if current:
                        final_chunks.append(current)
                    current = sent
                    current_bytes_len = sent_bytes_len
                else:
                    current += ' ' + sent if current else sent
                    current_bytes_len += (1 + sent_bytes_len) if current else sent_bytes_len
            if current:
                final_chunks.append(current)

    return final_chunks

# ==================== 流式响应核心功能（基于原项目重构） ====================
async def gemini_stream_handler(bot, chat_id: int, message_id: int, user_message: str, model_type: str, user_id: int):
    """基于原项目的流式处理函数"""
    sent_message = None
    try:
        # 1. 先发送生成中提示（与原项目一致）
        sent_message = await bot.send_message(
            chat_id, 
            BEFORE_GENERATE_INFO,
            reply_to_message_id=message_id
        )

        # 2. 获取或创建用户会话
        user_session = get_user_session(user_id, model_type)
        
        # 3. 发送消息并获取流式响应
        stream = user_session.chat_session.send_message(user_message, stream=True)

        full_response = ""
        last_update = time.time()
        update_interval = STREAM_UPDATE_INTERVAL  # 使用配置的更新间隔

        # 4. 流式处理响应块（与原项目逻辑一致）
        for chunk in stream:
            if hasattr(chunk, 'text') and chunk.text:
                full_response += chunk.text
                current_time = time.time()

                # 定期更新消息（避免过于频繁）
                if current_time - last_update >= update_interval:
                    try:
                        await bot.edit_message_text(
                            escape(full_response),
                            chat_id=chat_id,
                            message_id=sent_message.message_id,
                            parse_mode=ParseMode.MARKDOWN_V2
                        )
                    except Exception as e:
                        # 处理Markdown解析错误（与原项目一致）
                        if "parse markdown" in str(e).lower() or "can't parse entities" in str(e).lower():
                            await bot.edit_message_text(
                                full_response,
                                chat_id=chat_id,
                                message_id=sent_message.message_id
                            )
                        elif "message is not modified" not in str(e).lower():
                            logger.warning(f"消息更新失败: {e}")
                    last_update = current_time

        # 5. 最终更新完整响应
        try:
            await bot.edit_message_text(
                escape(full_response),
                chat_id=chat_id,
                message_id=sent_message.message_id,
                parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception as e:
            # 降级处理：不使用Markdown（与原项目一致）
            try:
                if "parse markdown" in str(e).lower() or "can't parse entities" in str(e).lower():
                    await bot.edit_message_text(
                        full_response,
                        chat_id=chat_id,
                        message_id=sent_message.message_id
                    )
            except Exception:
                logger.error(f"最终消息更新失败: {e}")

    except Exception as e:
        logger.error(f"流式处理错误: {e}")
        traceback.print_exc()
        if sent_message:
            try:
                await bot.edit_message_text(
                    f"{ERROR_INFO}\nError details: {str(e)}",
                    chat_id=chat_id,
                    message_id=sent_message.message_id
                )
            except Exception:
                await bot.send_message(
                    chat_id,
                    f"{ERROR_INFO}\nError details: {str(e)}",
                    reply_to_message_id=message_id
                )
        else:
            await bot.send_message(
                chat_id,
                f"{ERROR_INFO}\nError details: {str(e)}",
                reply_to_message_id=message_id
            )

# ==================== 图片处理功能（基于原项目重构） ====================
@retry_on_exception(max_retries=2)
async def download_image_with_retry(file_id: str, application: Application) -> Optional[bytes]:
    """带重试机制的图片下载"""
    try:
        file = await application.bot.get_file(file_id)
        file_url = file.file_path
        
        timeout = ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(file_url) as response:
                response.raise_for_status()
                return await response.read()
    except Exception as e:
        logger.error(f"图片下载失败: {e}")
        return None

async def gemini_edit_handler(bot, chat_id: int, message_id: int, user_message: str, photo_file: bytes, user_id: int):
    """基于原项目的图片编辑处理函数"""
    try:
        # 下载图片通知
        processing_msg = await bot.send_message(chat_id, DOWNLOAD_PIC_NOTIFY, reply_to_message_id=message_id)
        
        # 处理图片（与原项目一致）
        image = Image.open(io.BytesIO(photo_file))
        
        # 获取用户会话
        user_session = get_user_session(user_id, "gemini-1.5-pro")  # 图片处理使用pro模型
        
        # 准备内容（文本+图片）
        contents = [user_message, image]
        
        # 发送请求
        response = user_session.chat_session.send_message(contents)
        
        # 处理响应（与原项目一致）
        for part in response.parts:
            if hasattr(part, 'text') and part.text:
                text = part.text
                # 长文本分片处理
                while len(text) > 4000:
                    await bot.send_message(chat_id, escape(text[:4000]), 
                                         parse_mode=ParseMode.MARKDOWN_V2,
                                         reply_to_message_id=message_id)
                    text = text[4000:]
                if text:
                    await bot.send_message(chat_id, escape(text), 
                                         parse_mode=ParseMode.MARKDOWN_V2,
                                         reply_to_message_id=message_id)
            elif hasattr(part, 'inline_data') and part.inline_data:
                # 处理生成的图片
                photo_data = part.inline_data.data
                await bot.send_photo(chat_id, photo_data, reply_to_message_id=message_id)
        
        # 删除处理中的消息
        await bot.delete_message(chat_id, processing_msg.message_id)
        
    except Exception as e:
        logger.error(f"图片处理错误: {e}")
        traceback.print_exc()
        await bot.send_message(chat_id, f"{ERROR_INFO}\nError: {str(e)}", reply_to_message_id=message_id)

# ==================== 绘图功能（基于原项目） ====================
async def gemini_draw_handler(bot, chat_id: int, message_id: int, user_message: str, user_id: int):
    """基于原项目的绘图功能"""
    try:
        # 发送绘图通知
        drawing_msg = await bot.send_message(chat_id, "Drawing...", reply_to_message_id=message_id)
        
        # 获取绘图专用会话
        user_session = get_user_session(user_id, "gemini-1.5-pro")
        
        # 发送绘图请求
        response = user_session.chat_session.send_message(user_message)
        
        # 处理响应
        for part in response.parts:
            if hasattr(part, 'text') and part.text:
                text = part.text
                while len(text) > 4000:
                    await bot.send_message(chat_id, escape(text[:4000]), 
                                         parse_mode=ParseMode.MARKDOWN_V2,
                                         reply_to_message_id=message_id)
                    text = text[4000:]
                if text:
                    await bot.send_message(chat_id, escape(text), 
                                         parse_mode=ParseMode.MARKDOWN_V2,
                                         reply_to_message_id=message_id)
            elif hasattr(part, 'inline_data') and part.inline_data:
                photo_data = part.inline_data.data
                await bot.send_photo(chat_id, photo_data, reply_to_message_id=message_id)
        
        # 删除绘图中的消息
        await bot.delete_message(chat_id, drawing_msg.message_id)
        
    except Exception as e:
        logger.error(f"绘图错误: {e}")
        await bot.send_message(chat_id, f"{ERROR_INFO}\nError: {str(e)}", reply_to_message_id=message_id)

# ==================== 命令处理函数 ====================
# ==================== 新增命令处理函数 ====================
async def handle_help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理/help命令 - 显示帮助信息"""
    if not is_user_allowed(update):
        return
    
    help_text = """
🤖 *Gemini AI 机器人帮助*

*基础命令：*
`/start` - 开始使用机器人
`/help` - 显示此帮助信息
`/new` - 开始新对话（清空上下文）
`/clear` - 清空对话历史

*模型命令：*
`/gemini` - 使用 gemini-2.0-flash 模型（快速）
`/gemini_pro` - 使用 gemini-2.5-pro 模型（强大）
`/model` - 查看或切换AI模型
`/switch` - 切换默认模型

*多媒体命令：*
`/draw` - 绘图功能
`/edit` - 编辑图片（发送图片+描述）

*状态命令：*
`/status` - 查看会话状态
`/context` - 查看上下文状态

*使用方式：*
1. 在私聊中直接发送消息
2. 使用命令后跟问题
3. 发送图片进行分析

*当前设置：*
• 默认模型：{model_info}
• 流式输出：开启
• 上下文：智能管理
    """.format(model_info=get_current_model_info(update.effective_user.id))
    
    await update.message.reply_text(prepare_markdown_segment(help_text), 
                                  parse_mode=ParseMode.MARKDOWN_V2)

async def handle_new_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理/new命令 - 开始新对话（清空上下文）"""
    if not is_user_allowed(update):
        return
    
    clear_user_context(update.effective_user.id)
    await update.message.reply_text("🆕 已开始新对话，上下文历史已清空")

async def handle_model_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理/model命令 - 查看或切换AI模型"""
    if not is_user_allowed(update):
        return
    
    user_id = update.effective_user.id
    
    # 如果没有参数，显示当前模型信息
    if not context.args:
        current_model = get_current_model_info(user_id)
        models_list = "\n".join([f"• `{key}` - {value}" for key, value in AVAILABLE_MODELS.items()])
        
        model_text = f"""
📊 *当前模型信息*

*您当前的模型：*
{current_model}

*可用模型列表：*
{models_list}

*切换模型：*
使用 `/model 模型名称` 来切换模型
例如：`/model gemini-2.5-pro`
        """
        await update.message.reply_text(prepare_markdown_segment(model_text), 
                                      parse_mode=ParseMode.MARKDOWN_V2)
        return
    
    # 处理模型切换
    model_name = context.args[0].strip()
    if model_name not in AVAILABLE_MODELS:
        available_models = ", ".join([f"`{model}`" for model in AVAILABLE_MODELS.keys()])
        await update.message.reply_text(
            prepare_markdown_segment(f"❌ 无效的模型名称。可用模型：{available_models}"),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return
    
    # 切换模型会清空当前会话
    if user_id in user_sessions:
        del user_sessions[user_id]
    
    # 创建新会话
    get_user_session(user_id, model_name)
    
    await update.message.reply_text(
        prepare_markdown_segment(f"✅ 已切换到模型：`{model_name}`\n{AVAILABLE_MODELS[model_name]}"),
        parse_mode=ParseMode.MARKDOWN_V2
    )

async def handle_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理/status命令 - 查看会话状态"""
    if not is_user_allowed(update):
        return
    
    user_id = update.effective_user.id
    session = user_sessions.get(user_id)
    
    if session:
        history_length = len(session.chat_session.history) if hasattr(session.chat_session, 'history') else 0
        status_text = f"""
📈 *会话状态*

*模型：* `{session.model_name}`
*消息数：* `{session.message_count}`
*历史长度：* `{history_length} 条消息`
*最后活动：* `{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(session.last_activity))}`
*总令牌数：* `{session.total_tokens}`
        """
    else:
        status_text = """
📈 *会话状态*

*当前状态：* 无活跃会话
*使用任何命令或发送消息来创建新会话*
        """
    
    await update.message.reply_text(prepare_markdown_segment(status_text), 
                                  parse_mode=ParseMode.MARKDOWN_V2)

async def handle_context_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理/context命令 - 查看上下文状态"""
    if not is_user_allowed(update):
        return
    
    user_id = update.effective_user.id
    session = user_sessions.get(user_id)
    
    if session and hasattr(session.chat_session, 'history'):
        history = session.chat_session.history
        context_text = f"""
📝 *上下文状态*

*总对话轮数：* `{len(history) // 2}`
*消息总数：* `{len(history)}`

*最近对话：*
"""
        # 显示最近3轮对话
        recent_messages = history[-6:]  # 最近3轮（每轮2条消息）
        for i, msg in enumerate(recent_messages):
            role = "👤 用户" if i % 2 == 0 else "🤖 AI"
            # 修正这里：需要检查消息结构
            if hasattr(msg, 'parts') and msg.parts:
                content = msg.parts[0].text if hasattr(msg.parts[0], 'text') else str(msg.parts[0])
            else:
                content = str(msg)
            preview = content[:100] + "..." if len(content) > 100 else content
            context_text += f"\n{role}: `{preview}`"
        
        if len(history) > 6:
            context_text += f"\n\n... 还有 `{len(history) - 6}` 条更早的消息"
    else:
        context_text = """
📝 *上下文状态*

*当前状态：* 无上下文历史
*开始对话后这里会显示最近的对话内容*
        """
    
    await update.message.reply_text(prepare_markdown_segment(context_text), 
                                  parse_mode=ParseMode.MARKDOWN_V2)
    
async def handle_gemini_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理/gemini命令（使用gemini-2.0-flash）"""
    if not is_user_allowed(update):
        return
    
    try:
        user_message = update.message.text.strip().split(maxsplit=1)[1].strip()
    except IndexError:
        await update.message.reply_text(
            escape("Please add what you want to say after /gemini. \nFor example: `/gemini Who is john lennon?`"),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return
    
    await gemini_stream_handler(
        context.bot,
        update.effective_chat.id,
        update.message.message_id,
        user_message,
        "gemini-2.0-flash",
        update.effective_user.id
    )

async def handle_gemini_pro_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理/gemini_pro命令（使用gemini-2.5-pro）"""
    if not is_user_allowed(update):
        return
    
    try:
        user_message = update.message.text.strip().split(maxsplit=1)[1].strip()
    except IndexError:
        await update.message.reply_text(
            escape("Please add what you want to say after /gemini_pro. \nFor example: `/gemini_pro Who is john lennon?`"),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return
    
    await gemini_stream_handler(
        context.bot,
        update.effective_chat.id,
        update.message.message_id,
        user_message,
        "gemini-2.5-pro",
        update.effective_user.id
    )

async def handle_draw_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理/draw命令"""
    if not is_user_allowed(update):
        return
    
    try:
        user_message = update.message.text.strip().split(maxsplit=1)[1].strip()
    except IndexError:
        await update.message.reply_text(
            escape("Please add what you want to draw after /draw. \nFor example: `/draw draw me a cat.`"),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return
    
    await gemini_draw_handler(
        context.bot,
        update.effective_chat.id,
        update.message.message_id,
        user_message,
        update.effective_user.id
    )

async def handle_edit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理/edit命令"""
    if not is_user_allowed(update):
        return
    
    if not update.message.photo:
        await update.message.reply_text("Please send a photo with caption for editing")
        return
    
    try:
        user_message = update.message.caption.strip().split(maxsplit=1)[1].strip() if update.message.caption else ""
    except IndexError:
        user_message = ""
    
    # 下载图片
    file_id = update.message.photo[-1].file_id
    photo_data = await download_image_with_retry(file_id, context.application)
    
    if not photo_data:
        await update.message.reply_text("Failed to download image")
        return
    
    await gemini_edit_handler(
        context.bot,
        update.effective_chat.id,
        update.message.message_id,
        user_message,
        photo_data,
        update.effective_user.id
    )

async def handle_clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """清空对话上下文（与原项目一致）"""
    if not is_user_allowed(update):
        return
    
    clear_user_context(update.effective_user.id)
    await update.message.reply_text("Your history has been cleared")

async def handle_switch_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """切换默认模型（与原项目一致）"""
    if not is_user_allowed(update):
        return
    
    user_id = update.effective_user.id
    
    if user_id not in default_model_dict:
        default_model_dict[user_id] = False
        await update.message.reply_text("Now you are using gemini-1.5-pro")
        return
    
    if default_model_dict[user_id]:
        default_model_dict[user_id] = False
        await update.message.reply_text("Now you are using gemini-1.5-pro")
    else:
        default_model_dict[user_id] = True
        await update.message.reply_text("Now you are using gemini-2.0-flash")

async def handle_photo_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理图片消息（与原项目一致）"""
    if not is_user_allowed(update):
        return
    
    # 下载图片
    file_id = update.message.photo[-1].file_id
    photo_data = await download_image_with_retry(file_id, context.application)
    
    if not photo_data:
        await update.message.reply_text("Failed to download image")
        return
    
    # 获取描述文本
    user_message = update.message.caption or ""
    
    await gemini_edit_handler(
        context.bot,
        update.effective_chat.id,
        update.message.message_id,
        user_message,
        photo_data,
        update.effective_user.id
    )

async def handle_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理私聊消息（与原项目一致）"""
    if not is_user_allowed(update) or update.effective_chat.type != "private":
        return
    
    user_message = update.message.text.strip()
    user_id = update.effective_user.id
    
    # 根据用户默认模型设置选择模型
    if user_id not in default_model_dict:
        default_model_dict[user_id] = True  # 默认使用gemini-2.0-flash
        model_type = "gemini-2.0-flash"
    else:
        model_type = "gemini-2.0-flash" if default_model_dict[user_id] else "gemini-1.5-pro"
    
    await gemini_stream_handler(
        context.bot,
        update.effective_chat.id,
        update.message.message_id,
        user_message,
        model_type,
        user_id
    )

# ==================== 原有的辅助函数 ====================
async def handle_start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """欢迎和帮助信息"""
    if not is_user_allowed(update):
        return
    
    help_text = """
🤖 *Gemini AI 机器人*

*可用命令：*
`/start` - 开始使用
`/gemini` - 使用 gemini-2.0-flash 模型
`/gemini_pro` - 使用 gemini-2.5-pro 模型  
`/draw` - 绘图功能
`/edit` - 编辑图片
`/clear` - 清空对话历史
`/switch` - 切换默认模型

*支持功能：*
• 文本对话（支持上下文）
• 图片识别和分析
• 流式输出（实时显示）
• 多模型选择

*当前默认模型：*
{model_info}

*流式模式：* `默认开启 (0.5秒间隔)`
    """.format(model_info=get_current_model_info(update.effective_user.id))
    
    await update.message.reply_text(prepare_markdown_segment(help_text), 
                                  parse_mode=ParseMode.MARKDOWN_V2)

# ==================== 清理和健康检查任务 ====================
async def cleanup_task(context: ContextTypes.DEFAULT_TYPE):
    """清理过期会话"""
    now = time.time()
    expired = [uid for uid, session in user_sessions.items() if now - session.last_activity > 3600]
    for uid in expired:
        del user_sessions[uid]
    logger.info(f"清理了 {len(expired)} 个过期会话")

async def update_telegram_commands(application: Application):
    """更新Telegram机器人命令列表"""
    commands = [
        ("start", "开始使用"),
        ("help", "显示帮助信息"),
        ("gemini", "使用gemini-2.0-flash模型"),
        ("gemini_pro", "使用gemini-2.5-pro模型"),
        ("new", "开始新对话（清空上下文）"),
        ("draw", "绘图功能"),
        ("edit", "编辑图片"),
        ("clear", "清空对话历史"),
        ("model", "查看或切换AI模型"),
        ("status", "查看会话状态"),
        ("context", "查看上下文状态"),
        ("switch", "切换默认模型")
    ]
    
    try:
        await application.bot.set_my_commands(commands)
        logger.info("✅ 机器人命令已更新")
    except Exception as e:
        logger.error(f"❌ 命令更新失败: {e}")

def main():
    """主函数"""
    if not validate_config():
        logger.error("配置验证失败，程序退出")
        return
    
    logger.info("Starting Gemini Telegram Bot...")
    logger.info(f"可用模型: {', '.join(AVAILABLE_MODELS.keys())}")
    logger.info(f"默认模型: {DEFAULT_MODEL}")
    logger.info(f"流式更新间隔: {STREAM_UPDATE_INTERVAL}秒")
    logger.info("🟢 流式模式: 默认开启 (0.5秒间隔)")
    
    # 创建Application
    application = Application.builder().token(TG_TOKEN).build()
    
    # 添加处理器
    application.add_handler(CommandHandler("start", handle_start_command))
    application.add_handler(CommandHandler("help", handle_help_command))
    application.add_handler(CommandHandler("gemini", handle_gemini_command))
    application.add_handler(CommandHandler("gemini_pro", handle_gemini_pro_command))
    application.add_handler(CommandHandler("new", handle_new_command))
    application.add_handler(CommandHandler("draw", handle_draw_command))
    application.add_handler(CommandHandler("edit", handle_edit_command))
    application.add_handler(CommandHandler("clear", handle_clear_command))
    application.add_handler(CommandHandler("model", handle_model_command))
    application.add_handler(CommandHandler("status", handle_status_command))
    application.add_handler(CommandHandler("context", handle_context_command))
    application.add_handler(CommandHandler("switch", handle_switch_command))
    
    # 添加消息处理器
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo_message))
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, 
        handle_private_message
    ))
    
    # 添加定时任务
    job_queue = application.job_queue
    job_queue.run_repeating(cleanup_task, interval=3600, first=10)
    
    # 启动时更新命令
    application.post_init = update_telegram_commands
    
    # 启动bot
    logger.info("Bot started successfully!")
    application.run_polling(
        allowed_updates=["message", "callback_query"],
        timeout=POLLING_TIMEOUT
    )

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("程序被用户中断")
    except Exception as e:
        logger.error(f"程序异常退出: {e}")
        traceback.print_exc()