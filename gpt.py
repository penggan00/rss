#source rss_venv/bin/activate
#pip install python-dotenv python-telegram-bot Pillow google-generativeai md2tgmd aiohttp
# sudo systemctl restart gpt.service
import asyncio
import os
import time
import traceback
import io
import re
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

# 加载环境变量
load_dotenv()

# 配置信息
TG_TOKEN = os.getenv("TELEGRAM_GEMINI_KEY")
GOOGLE_GEMINI_KEY = os.getenv("GEMINI_API_KEY")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
ALLOWED_USER_IDS_STR = os.getenv("TELEGRAM_CHAT_ID")
DEFAULT_MODEL = os.getenv("GPT_ENGINE", "gemini-2.5-flash")

# 超时配置
POLLING_TIMEOUT = int(os.getenv("POLLING_TIMEOUT", "45"))

# 可用模型列表
AVAILABLE_MODELS = {
    "gemini-2.5-flash": "(平衡性能)",
    "deepseek-chat":    "(通用对话)",
    "deepseek-reasoner":"(推理专用)",
    "deepseek-coder":   "(编程专用)"
}

# 错误信息配置
ERROR_INFO = "⚠️⚠️⚠️\n出了问题 !\n请尝试更改您的提示或联系管理员 !"
BEFORE_GENERATE_INFO = "🤖Generating🤖"
DOWNLOAD_PIC_NOTIFY = "🤖Loading picture🤖"

# 初始化配置
try:
    ALLOWED_USER_IDS = [int(user_id.strip()) for user_id in ALLOWED_USER_IDS_STR.split(",")] if ALLOWED_USER_IDS_STR else []
except ValueError:
    exit(1)

# 初始化Gemini
try:
    genai.configure(api_key=GOOGLE_GEMINI_KEY)
except Exception as e:
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
    else:
        # 如果切换了模型，应该创建新的会话
        current_session = user_sessions[user_id]
        if model_name and model_name != current_session.model_name:
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
            if history_length > 20:
                keep_count = min(16, history_length)
                session.chat_session.history = session.chat_session.history[-keep_count:]
            elif history_length > 15:
                keep_count = min(12, history_length)
                session.chat_session.history = session.chat_session.history[-keep_count:]
        
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
    
    # 构建系统提示词，优化 Telegram Markdown V2 格式输出
    system_prompt = """standard Markdown format"""
    
    # 如果是新对话，添加系统提示
    if not history:
        messages.insert(0, {"role": "system", "content": system_prompt})
    
    # 添加当前用户消息
    messages.append({"role": "user", "content": user_message})
    
    # API请求数据
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
            
            result = await response.json()
            full_response = result['choices'][0]['message']['content']
            
            # 更新对话历史
            user_session.deepseek_history.append({"role": "user", "content": user_message})
            user_session.deepseek_history.append({"role": "assistant", "content": full_response})
            
            # 限制历史长度
            if len(user_session.deepseek_history) > 20:
                user_session.deepseek_history = user_session.deepseek_history[-20:]
            
            return full_response

# ==================== 消息分割功能 ====================
def split_messages(text: str) -> List[str]:
    """
    智能分割消息，确保：
    1. 优先在段落边界分割
    2. 不破坏代码块结构
    3. 每段不超过3900字节
    """
    MAX_BYTES = 3900
    chunks = []
    current_chunk = ""

    # 按段落分割
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

    # 二次分割超长段落
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

async def send_segmented_message(bot, chat_id: int, message_id: int, text: str):
    """分段发送消息 - 修复版本"""
    chunks = split_messages(text)
    
    if not chunks:
        return
    
    sent_messages = []
    
    # 发送所有段落
    for i, chunk in enumerate(chunks):
        try:
            if i == 0:  # 第一段作为回复
                sent_msg = await bot.send_message(
                    chat_id,
                    escape(chunk),
                    reply_to_message_id=message_id,
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            else:  # 后续段落作为新消息
                sent_msg = await bot.send_message(
                    chat_id,
                    escape(chunk),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            sent_messages.append(sent_msg)
        except Exception as e:
            # 如果Markdown发送失败，尝试普通文本
            if i == 0:
                sent_msg = await bot.send_message(
                    chat_id,
                    chunk,
                    reply_to_message_id=message_id
                )
            else:
                sent_msg = await bot.send_message(chat_id, chunk)
            sent_messages.append(sent_msg)
        
        await asyncio.sleep(0.3)  # 避免发送过快
    
    return sent_messages

# ==================== AI 处理函数 ====================
async def ai_handler(bot, chat_id: int, message_id: int, user_message: str, model_type: str, user_id: int):
    """统一的AI处理函数 - 优化版本"""
    sent_message = None
    try:
        # 发送生成中提示
        sent_message = await bot.send_message(
            chat_id, 
            BEFORE_GENERATE_INFO,
            reply_to_message_id=message_id
        )

        # 获取或创建用户会话
        try:
            user_session = get_user_session(user_id, model_type)
        except Exception as e:
            clear_user_context(user_id)
            user_session = get_user_session(user_id, model_type)
        
        full_response = ""

        # 根据模型类型调用不同的API
        if model_type.startswith("gemini"):
            enhanced_message = f"用中文回复：{user_message}"
            
            try:
                response = user_session.chat_session.send_message(enhanced_message)
                full_response = response.text
            except Exception as e:
                await bot.edit_message_text(
                    f"{ERROR_INFO}\n错误详情: {str(e)}",
                    chat_id=chat_id,
                    message_id=sent_message.message_id
                )
                return
                
        else:
            enhanced_message = f"用中文回复：{user_message}"
            full_response = await call_deepseek_api(enhanced_message, user_session)
        
        # 处理完整响应
        if full_response:
            response_bytes = len(full_response.encode('utf-8'))
            
            if response_bytes > 3900:
                # 长消息：保留Generating提示，直接分段发送回复
                await send_segmented_message(bot, chat_id, message_id, full_response)
                # Generating提示保持显示，让用户知道生成已完成
                    
            else:
                # 短消息：直接编辑Generating提示为最终回复
                try:
                    await bot.edit_message_text(
                        escape(full_response),
                        chat_id=chat_id,
                        message_id=sent_message.message_id,
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
                except Exception as e:
                    await bot.edit_message_text(
                        full_response,
                        chat_id=chat_id,
                        message_id=sent_message.message_id
                    )

    except asyncio.TimeoutError:
        if sent_message:
            await bot.edit_message_text(
                "⏰ 请求超时，请稍后重试",
                chat_id=chat_id,
                message_id=sent_message.message_id
            )
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

# ==================== 图片处理功能 ====================
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
        return None

async def gemini_edit_handler(bot, chat_id: int, message_id: int, user_message: str, photo_file: bytes, user_id: int):
    """图片编辑处理函数"""
    try:
        processing_msg = await bot.send_message(chat_id, DOWNLOAD_PIC_NOTIFY, reply_to_message_id=message_id)
        
        image = Image.open(io.BytesIO(photo_file))
        user_session = get_user_session(user_id, "gemini-2.5-flash")
        
        enhanced_message = f"用中文回复：{user_message}" if user_message else "用中文描述这张图片"
        contents = [enhanced_message, image]
        
        response = user_session.chat_session.send_message(contents)
        
        response_text = ""
        for part in response.parts:
            if hasattr(part, 'text') and part.text:
                response_text += part.text
        
        await bot.delete_message(chat_id, processing_msg.message_id)
        
        if response_text:
            await send_segmented_message(bot, chat_id, message_id, response_text)
        
    except Exception as e:
        await bot.send_message(chat_id, f"{ERROR_INFO}\nError: {str(e)}", reply_to_message_id=message_id)

# ==================== 命令处理函数 ====================
async def handle_start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理/start命令"""
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
    
    if not context.args:
        current_model = get_current_model_info(user_id)
        
        model_text = f"""
🔄 **模型切换**

**当前模型：**
{current_model}

**gemini:**
`/model gemini-2.5-flash`  (平衡性能)

**deekseek:**
`/model deepseek-chat`          (通用对话)
`/model deepseek-coder`        (编程专用)
`/model deepseek-reasoner`  (推理专用)

**直接点击上面的命令即可切换**
        """
        await update.message.reply_text(prepare_markdown_segment(model_text), 
                                      parse_mode=ParseMode.MARKDOWN_V2)
        return
    
    model_name = context.args[0].strip()
    if model_name not in AVAILABLE_MODELS:
        available_models = "\n".join([f"• `{model}` - {desc}" for model, desc in AVAILABLE_MODELS.items()])
        await update.message.reply_text(
            prepare_markdown_segment(f"❌ 无效的模型名称。\n\n可用模型：\n{available_models}"),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return
    
    if user_id in user_sessions:
        if user_sessions[user_id].model_name == model_name:
            await update.message.reply_text(
                prepare_markdown_segment(f"ℹ️ 已经是 `{model_name}` 模型"),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return
        else:
            del user_sessions[user_id]
    
    try:
        get_user_session(user_id, model_name)
        await update.message.reply_text(
            prepare_markdown_segment(f"✅ 已切换到模型：`{model_name}`\n{AVAILABLE_MODELS[model_name]}"),
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except Exception as e:
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
`/new`        - 🆕 清空对话历史
`/model` - 🔄 切换AI模型
`/clear` - 🔄 清空对话上下文
`/start` - 🤖 显示帮助信息

** 系统状态：**
• 默认模型：{model_info}
• 上下文管理：✅ 智能清理

**使用提示：**
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
    
    file_id = update.message.photo[-1].file_id
    photo_data = await download_image_with_retry(file_id, context.application)
    
    if not photo_data:
        await update.message.reply_text("Failed to download image")
        return
    
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
    
    if user_id in user_sessions:
        model_type = user_sessions[user_id].model_name
    else:
        model_type = DEFAULT_MODEL
    
    await ai_handler(
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

def main():
    """主函数"""
    if not validate_config():
        return
    
    application = Application.builder().token(TG_TOKEN).build()
    
    application.add_handler(CommandHandler("start", handle_start_command))
    application.add_handler(CommandHandler("new", handle_new_command))
    application.add_handler(CommandHandler("model", handle_model_command))
    application.add_handler(CommandHandler("setup", handle_setup_command))
    application.add_handler(CommandHandler("clear", handle_clear_command))
    
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo_message))
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, 
        handle_private_message
    ))
    
    job_queue = application.job_queue
    job_queue.run_repeating(cleanup_task, interval=3600, first=10)
    
    application.run_polling(
        allowed_updates=["message", "callback_query"],
        timeout=POLLING_TIMEOUT
    )

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        traceback.print_exc()