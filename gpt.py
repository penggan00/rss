#source rss_venv/bin/activate
#pip install python-dotenv python-telegram-bot Pillow google-generativeai md2tgmd aiohttp
import asyncio
import os
import time
import re
import functools
import logging
import traceback
import io
import json
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
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
ALLOWED_USER_IDS_STR = os.getenv("TELEGRAM_CHAT_ID")
DEFAULT_MODEL = os.getenv("GPT_ENGINE", "gemini-2.5-flash")

# 超时配置
STREAM_UPDATE_INTERVAL = float(os.getenv("STREAM_UPDATE_INTERVAL", "1.5"))
POLLING_TIMEOUT = int(os.getenv("POLLING_TIMEOUT", "30"))

# 重试配置
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
RETRY_DELAY = float(os.getenv("RETRY_DELAY", "2.0"))

# 可用模型列表
AVAILABLE_MODELS = {
    "gemini-2.0-flash": "(长下文本)",
    "gemini-2.5-flash": "(平衡性能)",
    "gemini-2.5-pro": "(最强能力)",
    "deepseek-chat": "(通用对话)",
    "deepseek-reasoner": "(推理专用)",
    "deepseek-coder": "(编程专用)"
}

# 错误信息配置
ERROR_INFO = "⚠️⚠️⚠️\n出了问题 !\n请尝试更改您的提示或联系管理员 !"
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

# 会话管理
class UserSession:
    def __init__(self, chat_session: genai.ChatSession = None, model_name: str = DEFAULT_MODEL, deepseek_history: List = None):
        self.chat_session = chat_session
        self.last_activity = time.time()
        self.model_name = model_name
        self.message_count = 0
        self.total_tokens = 0
        self.deepseek_history = deepseek_history or []

# 会话字典
user_sessions: Dict[int, UserSession] = {}

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
        
        if model_name.startswith("gemini"):
            model = genai.GenerativeModel(model_name)
            chat = model.start_chat(history=[])
            user_sessions[user_id] = UserSession(chat, model_name)
        else:
            # DeepSeek模型
            user_sessions[user_id] = UserSession(model_name=model_name, deepseek_history=[])
        
        logger.info(f"创建新会话: 用户 {user_id}, 模型 {model_name}")
    else:
        # 如果切换了模型，应该创建新的会话
        current_session = user_sessions[user_id]
        if model_name and model_name != current_session.model_name:
            logger.info(f"用户 {user_id} 切换模型: {current_session.model_name} -> {model_name}")
            
            if model_name.startswith("gemini"):
                model = genai.GenerativeModel(model_name)
                chat = model.start_chat(history=[])
                user_sessions[user_id] = UserSession(chat, model_name)
            else:
                # DeepSeek模型
                user_sessions[user_id] = UserSession(model_name=model_name, deepseek_history=[])
        else:
            user_sessions[user_id].last_activity = now
        
        # 智能上下文清理策略（仅对Gemini模型）
        session = user_sessions[user_id]
        if session.chat_session and hasattr(session.chat_session, 'history'):
            history_length = len(session.chat_session.history)
            
            # 根据历史长度决定清理策略
            if history_length > 20:
                keep_count = min(16, history_length)
                session.chat_session.history = session.chat_session.history[-keep_count:]
                logger.info(f"用户 {user_id} 上下文已清理: {history_length} -> {keep_count}")
            elif history_length > 15:
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

# ==================== DeepSeek API 调用 ====================
async def call_deepseek_api(user_message: str, user_session: UserSession) -> str:
    """调用DeepSeek API"""
    if not DEEPSEEK_API_KEY:
        raise Exception("DeepSeek API Key 未配置")
    
    # 构建消息历史
    messages = []
    
    # 添加上下文历史（最多保留6轮对话）
    history = user_session.deepseek_history[-12:]  # 保留最近6轮
    messages.extend(history)
    
    # 添加当前用户消息
    enhanced_message = f"请用中文回答以下问题：{user_message}"
    messages.append({"role": "user", "content": enhanced_message})
    
    # API请求数据
    data = {
        "model": user_session.model_name,
        "messages": messages,
        "stream": True,
        "max_tokens": 4000
    }
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}"
    }
    
    timeout = ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            "https://api.deepseek.com/chat/completions",
            json=data,
            headers=headers
        ) as response:
            if response.status != 200:
                error_text = await response.text()
                raise Exception(f"DeepSeek API 错误: {response.status} - {error_text}")
            
            full_response = ""
            async for line in response.content:
                if line:
                    line_text = line.decode('utf-8').strip()
                    if line_text.startswith('data: '):
                        json_str = line_text[6:]
                        if json_str == '[DONE]':
                            break
                        try:
                            data_chunk = json.loads(json_str)
                            if 'choices' in data_chunk and len(data_chunk['choices']) > 0:
                                delta = data_chunk['choices'][0].get('delta', {})
                                if 'content' in delta:
                                    content = delta['content']
                                    full_response += content
                        except json.JSONDecodeError:
                            continue
            
            # 更新对话历史
            user_session.deepseek_history.append({"role": "user", "content": enhanced_message})
            user_session.deepseek_history.append({"role": "assistant", "content": full_response})
            
            # 限制历史长度
            if len(user_session.deepseek_history) > 20:  # 最多10轮对话
                user_session.deepseek_history = user_session.deepseek_history[-20:]
            
            return full_response

# ==================== 流式响应核心功能 ====================
async def ai_stream_handler(bot, chat_id: int, message_id: int, user_message: str, model_type: str, user_id: int):
    """统一的AI流式处理函数"""
    sent_message = None
    try:
        # 1. 先发送生成中提示
        sent_message = await bot.send_message(
            chat_id, 
            BEFORE_GENERATE_INFO,
            reply_to_message_id=message_id
        )

        # 2. 获取或创建用户会话
        try:
            user_session = get_user_session(user_id, model_type)
        except Exception as e:
            logger.error(f"获取用户会话失败: {e}")
            clear_user_context(user_id)
            user_session = get_user_session(user_id, model_type)
        
        full_response = ""
        last_update = time.time()
        update_interval = STREAM_UPDATE_INTERVAL

        # 3. 根据模型类型调用不同的API
        if model_type.startswith("gemini"):
            # Gemini模型
            enhanced_message = f"请用中文回答以下问题：{user_message}"
            
            stream = user_session.chat_session.send_message(enhanced_message, stream=True)
            
            for chunk in stream:
                if hasattr(chunk, 'text') and chunk.text:
                    full_response += chunk.text
                    current_time = time.time()

                    if current_time - last_update >= update_interval:
                        try:
                            await bot.edit_message_text(
                                escape(full_response),
                                chat_id=chat_id,
                                message_id=sent_message.message_id,
                                parse_mode=ParseMode.MARKDOWN_V2
                            )
                        except Exception as e:
                            if "parse markdown" in str(e).lower() or "can't parse entities" in str(e).lower():
                                await bot.edit_message_text(
                                    full_response,
                                    chat_id=chat_id,
                                    message_id=sent_message.message_id
                                )
                            elif "message is not modified" not in str(e).lower():
                                logger.warning(f"消息更新失败: {e}")
                        last_update = current_time
        else:
            # DeepSeek模型
            full_response = await call_deepseek_api(user_message, user_session)
            
            # DeepSeek API不支持真正的流式，直接发送完整响应
            try:
                await bot.edit_message_text(
                    escape(full_response),
                    chat_id=chat_id,
                    message_id=sent_message.message_id,
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            except Exception as e:
                if "parse markdown" in str(e).lower() or "can't parse entities" in str(e).lower():
                    await bot.edit_message_text(
                        full_response,
                        chat_id=chat_id,
                        message_id=sent_message.message_id
                    )

        # 4. 最终更新完整响应（Gemini模型）
        if model_type.startswith("gemini") and full_response:
            try:
                await bot.edit_message_text(
                    escape(full_response),
                    chat_id=chat_id,
                    message_id=sent_message.message_id,
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            except Exception as e:
                try:
                    if "parse markdown" in str(e).lower() or "can't parse entities" in str(e).lower():
                        await bot.edit_message_text(
                            full_response,
                            chat_id=chat_id,
                            message_id=sent_message.message_id
                        )
                except Exception:
                    logger.error(f"最终消息更新失败: {e}")

    except asyncio.TimeoutError:
        logger.error(f"用户 {user_id} 请求超时")
        if sent_message:
            await bot.edit_message_text(
                "⏰ 请求超时，请稍后重试",
                chat_id=chat_id,
                message_id=sent_message.message_id
            )
    except Exception as e:
        logger.error(f"AI处理错误: {e}")
        traceback.print_exc()
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

# ==================== 图片处理功能 ====================
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
    """图片编辑处理函数"""
    try:
        # 下载图片通知
        processing_msg = await bot.send_message(chat_id, DOWNLOAD_PIC_NOTIFY, reply_to_message_id=message_id)
        
        # 处理图片
        image = Image.open(io.BytesIO(photo_file))
        
        # 获取用户会话（图片处理使用Gemini模型）
        user_session = get_user_session(user_id, "gemini-2.5-flash")
        
        # 在用户消息前添加中文回答提示
        enhanced_message = f"请用中文回答：{user_message}" if user_message else "请用中文描述这张图片"
        
        # 准备内容（文本+图片）
        contents = [enhanced_message, image]
        
        # 发送请求
        response = user_session.chat_session.send_message(contents)
        
        # 处理响应
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

# ==================== 命令处理函数 ====================
async def handle_start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理/start命令"""
    if not is_user_allowed(update):
        return
    
    help_text = """
🤖 **AI 助手机器人**

# 简化命令：
`/new` - 开始新对话（清空上下文）
`/model` - 切换AI模型
`/setup` - 设置选项

# 当前默认模型：
{model_info}

直接发送消息开始对话！
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
    """处理/model命令 - 切换AI模型"""
    if not is_user_allowed(update):
        return
    
    user_id = update.effective_user.id
    
    # 如果没有参数，显示模型切换界面
    if not context.args:
        current_model = get_current_model_info(user_id)
        
        model_text = f"""
🔄 **模型切换**

# 当前模型：
{current_model}

# 一键切换命令：
`/model gemini-2.0-flash` - (长上下文)
`/model gemini-2.5-flash` - (平衡性能)
`/model gemini-2.5-pro` - (最强能力)
`/model deepseek-chat` - (通用对话)
`/model deepseek-reasoner` - (推理专用)
`/model deepseek-coder` - (编程专用)

# 直接点击上面的命令即可切换
        """
        await update.message.reply_text(prepare_markdown_segment(model_text), 
                                      parse_mode=ParseMode.MARKDOWN_V2)
        return
    
    # 处理模型切换
    model_name = context.args[0].strip()
    if model_name not in AVAILABLE_MODELS:
        available_models = "\n".join([f"• `{model}` - {desc}" for model, desc in AVAILABLE_MODELS.items()])
        await update.message.reply_text(
            prepare_markdown_segment(f"❌ 无效的模型名称。\n\n可用模型：\n{available_models}"),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return
    
    # 切换模型会清空当前会话
    if user_id in user_sessions:
        # 检查是否是相同的模型
        if user_sessions[user_id].model_name == model_name:
            await update.message.reply_text(
                prepare_markdown_segment(f"ℹ️ 已经是 `{model_name}` 模型"),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return
        else:
            del user_sessions[user_id]
            logger.info(f"用户 {user_id} 切换模型到 {model_name}")
    
    # 创建新会话
    try:
        get_user_session(user_id, model_name)
        await update.message.reply_text(
            prepare_markdown_segment(f"✅ 已切换到模型：`{model_name}`\n{AVAILABLE_MODELS[model_name]}"),
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except Exception as e:
        logger.error(f"切换模型失败: {e}")
        await update.message.reply_text(
            prepare_markdown_segment(f"❌ 切换模型失败：{str(e)}"),
            parse_mode=ParseMode.MARKDOWN_V2
        )

async def handle_setup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理/setup命令 - 设置选项"""
    if not is_user_allowed(update):
        return
    
    setup_text = """
⚙️ **设置选项**

# 快捷操作：
`/new` - 🆕 清空对话历史
`/model` - 🔄 切换AI模型

# 系统状态：
• 默认模型：{model_info}
• 流式输出：✅ 开启
• 上下文管理：✅ 智能清理

# 使用提示：
直接发送消息即可开始对话！
发送图片可进行图像分析
    """.format(model_info=get_current_model_info(update.effective_user.id))
    
    await update.message.reply_text(prepare_markdown_segment(setup_text), 
                                  parse_mode=ParseMode.MARKDOWN_V2)

async def handle_clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """清空对话上下文"""
    if not is_user_allowed(update):
        return
    
    clear_user_context(update.effective_user.id)
    await update.message.reply_text("✅ 对话历史已清空")

async def handle_photo_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理图片消息"""
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
    """处理私聊消息"""
    if not is_user_allowed(update) or update.effective_chat.type != "private":
        return
    
    user_message = update.message.text.strip()
    user_id = update.effective_user.id
    
    # 使用当前会话的模型
    if user_id in user_sessions:
        model_type = user_sessions[user_id].model_name
    else:
        model_type = DEFAULT_MODEL
    
    await ai_stream_handler(
        context.bot,
        update.effective_chat.id,
        update.message.message_id,
        user_message,
        model_type,
        user_id
    )

# ==================== 清理任务 ====================
async def cleanup_task(context: ContextTypes.DEFAULT_TYPE):
    """清理过期会话"""
    now = time.time()
    expired = [uid for uid, session in user_sessions.items() if now - session.last_activity > 3600]
    for uid in expired:
        del user_sessions[uid]
    if expired:
        logger.info(f"清理了 {len(expired)} 个过期会话")

async def update_telegram_commands(application: Application):
    """更新Telegram机器人命令列表"""
    commands = [
        ("start", "开始使用"),
        ("new", "开始新对话"),
        ("model", "切换AI模型"),
        ("setup", "设置选项")
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
    
    logger.info("Starting AI Assistant Bot...")
    logger.info(f"可用模型: {', '.join(AVAILABLE_MODELS.keys())}")
    logger.info(f"默认模型: {DEFAULT_MODEL}")
    logger.info(f"流式更新间隔: {STREAM_UPDATE_INTERVAL}秒")
    
    # 创建Application
    application = Application.builder().token(TG_TOKEN).build()
    
    # 添加处理器
    application.add_handler(CommandHandler("start", handle_start_command))
    application.add_handler(CommandHandler("new", handle_new_command))
    application.add_handler(CommandHandler("model", handle_model_command))
    application.add_handler(CommandHandler("setup", handle_setup_command))
    application.add_handler(CommandHandler("clear", handle_clear_command))
    
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