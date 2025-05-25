import asyncio
import traceback
import sys
import telebot
from telebot.async_telebot import AsyncTeleBot
from telebot.types import Message
import google.generativeai as genai
from md2tgmd import escape
import os
from dotenv import load_dotenv
import time
from typing import Dict, Tuple, List, Optional
import re

# 加载环境变量
load_dotenv()

# 配置信息
TG_TOKEN = os.getenv("TELEGRAM_GEMINI_KEY")
GOOGLE_GEMINI_KEY = os.getenv("GEMINI_API_KEY")
ALLOWED_USER_IDS_STR = os.getenv("TELEGRAM_CHAT_ID")
MODEL_NAME = os.getenv("GPT_ENGINE")

# 初始化配置
try:
    ALLOWED_USER_IDS = [int(user_id.strip()) for user_id in ALLOWED_USER_IDS_STR.split(",")]
except ValueError:
    print("Error: ALLOWED_USER_IDS 必须是逗号分隔的整数列表。")
    exit(1)

# 初始化Gemini
try:
    genai.configure(api_key=GOOGLE_GEMINI_KEY)
    model = genai.GenerativeModel(MODEL_NAME)
    print(f"Gemini API initialized with model: {MODEL_NAME}")
except Exception as e:
    print(f"Error initializing Gemini API: {e}")
    exit(1)

# 初始化Telegram机器人
bot = AsyncTeleBot(TG_TOKEN)
print("Telegram bot initialized.")

# 会话管理
user_chats: Dict[int, Tuple[genai.ChatSession, float]] = {}

# 辅助函数
def get_user_chat(user_id: int) -> genai.ChatSession:
    """获取或创建用户的聊天会话（1小时过期）"""
    now = time.time()
    expired_users = [uid for uid, (_, t) in user_chats.items() if now - t > 3600]
    for uid in expired_users:
        del user_chats[uid]

    if user_id not in user_chats:
        chat = model.start_chat(history=[])
        user_chats[user_id] = (chat, now)
    else:
        chat, _ = user_chats[user_id]
        user_chats[user_id] = (chat, now)
    return chat

def clear_user_context(user_id: int):
    """清空用户对话上下文"""
    if user_id in user_chats:
        del user_chats[user_id]

def is_user_allowed(message):
    """检查用户权限"""
    return message.from_user.id in ALLOWED_USER_IDS

def prepare_markdown_segment(text: str) -> str:
    """使用md2tgmd.escape统一转义文本段（包括代码块）"""
    return escape(text)

def split_messages(text: str) -> List[str]:
    """
    智能分割消息，确保：
    1. 优先在段落边界分割
    2. 不破坏代码块结构
    3. 每段不超过3800字节
    """
    MAX_BYTES = 3800
    chunks = []
    current_chunk = ""

    # 按段落分割
    paragraphs = text.split('\n\n')
    for para in paragraphs:
        para_bytes_len = len(para.encode('utf-8')) # 预计算字节长度
        current_chunk_bytes_len = len(current_chunk.encode('utf-8')) # 预计算当前 chunk 字节长度

        # 如果当前段落加上已有内容会超限
        if current_chunk_bytes_len + 4 + para_bytes_len > MAX_BYTES:  # 4 is the bytes length of '\n\n'
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
        chunk_bytes_len = len(chunk.encode('utf-8')) # 预计算字节长度
        if chunk_bytes_len <= MAX_BYTES:
            final_chunks.append(chunk)
        else:
            # 按句子分割超长段落
            sentences = re.split(r'(?<=[.!?])\s+', chunk)
            current = ""
            current_bytes_len = 0 # 当前bytes长度
            for sent in sentences:
                sent_bytes_len = len(sent.encode('utf-8'))
                if current_bytes_len + 1 + sent_bytes_len > MAX_BYTES: # 1 for space
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

async def send_with_status(chat_id: int, text: str):
    """带状态提示的消息发送（完全使用md2tgmd转义）"""
    try:
        # 分割消息（保持代码块完整）
        chunks = split_messages(text)

        # 发送每个分段（全部使用md2tgmd转义）
        for i, chunk in enumerate(chunks):
            prepared = prepare_markdown_segment(chunk)
            await bot.send_message(chat_id, prepared,
                                 parse_mode="MarkdownV2",
                                 disable_web_page_preview=True)

    except Exception as e:
        print(f"消息发送失败: {e}")

        # 回退到纯文本发送
        await send_plain_text(chat_id, text)

async def send_plain_text(chat_id: int, text: str):
    """纯文本发送保障"""
    MAX_BYTES = 3800
    encoded = text.encode('utf-8')
    
    for i in range(0, len(encoded), MAX_BYTES):
        chunk = encoded[i:i+MAX_BYTES].decode('utf-8', errors='ignore')
        await bot.send_message(chat_id, chunk, disable_web_page_preview=True)

async def cleanup_task():
    """清理过期会话"""
    while True:
        await asyncio.sleep(600)
        now = time.time()
        expired = [uid for uid, (_, t) in user_chats.items() if now - t > 3600]
        for uid in expired:
            del user_chats[uid]
        print(f"清理了 {len(expired)} 个过期会话")

# 命令处理
@bot.message_handler(commands=['new'])
async def handle_new_command(message: Message):
    """重置对话上下文"""
    if not is_user_allowed(message):
        return
    clear_user_context(message.from_user.id)
    await bot.send_message(message.chat.id, "🔄 已开启新对话，上下文历史已清空")

# 消息处理
@bot.message_handler(func=lambda message: is_user_allowed(message))
async def echo_all(message: Message):
    try:
        chat = get_user_chat(message.from_user.id)
        user_message = f"请用中文回答：{message.text}"  # <-- 关键修改

        # 调用Gemini API
        try:
            response = await asyncio.to_thread(chat.send_message, user_message)
            if not response.text:
                raise ValueError("Empty response from API")

            gemini_response = response.text

            # 发送优化后的消息
            await send_with_status(message.chat.id, gemini_response)

        except Exception as e:
            print(f"Gemini API error: {e}")
            await send_plain_text(message.chat.id, f"Gemini API错误: {str(e)[:300]}")

    except Exception as e:
        traceback.print_exc()
        await send_plain_text(message.chat.id, f"处理错误: {str(e)[:300]}")

async def main():
    """主函数"""
    print("Starting Gemini Telegram Bot...")
    asyncio.create_task(cleanup_task())
    await bot.polling(none_stop=True)

if __name__ == "__main__":
    asyncio.run(main())