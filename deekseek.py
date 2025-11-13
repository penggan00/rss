import os
import logging
import asyncio
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackContext
import requests
import json
from dotenv import load_dotenv
from md2tgmd import escape

# 加载环境变量
load_dotenv()

# 配置日志
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 配置信息
DEEPSEEK_BOT_TOKEN = os.getenv('DEEPSEEK_BOT_TOKEN')
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')
DEEPSEEK_MODEL = os.getenv('DEEPSEEK_MODEL', 'deepseek-chat')
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"

# 对话超时时间（1小时）
CONVERSATION_TIMEOUT = 3600  # 秒

class MessageSplitter:
    """智能消息分段器，特别处理大型代码块"""
    
    @staticmethod
    def split_message(text, max_length=4000):
        """
        智能分段消息，特别处理大型代码块
        
        Args:
            text: 要分段的文本
            max_length: 每段最大长度
            
        Returns:
            list: 分段后的消息列表
        """
        if len(text) <= max_length:
            return [text]
        
        # 首先检查是否有大型代码块（超过800字节）
        large_code_blocks = MessageSplitter._find_large_code_blocks(text, 800)
        
        segments = []
        current_segment = ""
        last_pos = 0
        
        for code_block in large_code_blocks:
            start_pos, end_pos, block_content = code_block
            
            # 添加代码块之前的文本
            preceding_text = text[last_pos:start_pos]
            if preceding_text:
                if len(current_segment) + len(preceding_text) <= max_length:
                    current_segment += preceding_text
                else:
                    # 分段发送前面的文本
                    if current_segment:
                        segments.append(current_segment)
                    current_segment = preceding_text
            
            # 处理大型代码块 - 整个代码块单独发送
            if len(block_content) > max_length:
                # 如果当前段有内容，先发送
                if current_segment:
                    segments.append(current_segment)
                    current_segment = ""
                
                # 整个大型代码块单独作为一个段
                segments.append(block_content)
            else:
                # 如果代码块不大，可以合并到当前段
                if len(current_segment) + len(block_content) <= max_length:
                    current_segment += block_content
                else:
                    if current_segment:
                        segments.append(current_segment)
                    current_segment = block_content
            
            last_pos = end_pos
        
        # 添加剩余文本
        remaining_text = text[last_pos:]
        if remaining_text:
            if len(current_segment) + len(remaining_text) <= max_length:
                current_segment += remaining_text
            else:
                if current_segment:
                    segments.append(current_segment)
                segments.append(remaining_text)
        
        if current_segment:
            segments.append(current_segment)
        
        # 最后确保每个段都不超过最大长度
        final_segments = []
        for segment in segments:
            if len(segment) <= max_length:
                final_segments.append(segment)
            else:
                # 对于非代码块的超长文本，按段落分割
                final_segments.extend(MessageSplitter._split_regular_text(segment, max_length))
        
        return [seg for seg in final_segments if seg.strip()]
    
    @staticmethod
    def _find_large_code_blocks(text, min_size=800):
        """
        查找大型代码块
        
        Args:
            text: 要搜索的文本
            min_size: 最小字节数，超过这个大小的代码块被认为是大型代码块
            
        Returns:
            list: 包含(start_pos, end_pos, block_content)的元组列表
        """
        import re
        
        # 匹配代码块（支持多种语言标记）
        code_block_pattern = r'```(?:\w+)?\n(.*?)\n```'
        matches = list(re.finditer(code_block_pattern, text, re.DOTALL))
        
        large_blocks = []
        for match in matches:
            full_block = match.group(0)  # 包含 ``` 的完整代码块
            block_content = match.group(0)  # 整个代码块内容
            
            if len(block_content) >= min_size:
                large_blocks.append((
                    match.start(),
                    match.end(),
                    block_content
                ))
        
        return large_blocks
    
    @staticmethod
    def _split_regular_text(text, max_length):
        """分割普通文本（非代码块）"""
        if len(text) <= max_length:
            return [text]
        
        segments = []
        current_segment = ""
        
        # 按段落分割
        paragraphs = text.split('\n\n')
        
        for paragraph in paragraphs:
            # 如果段落本身超过最大长度，按行分割
            if len(paragraph) > max_length:
                lines = paragraph.split('\n')
                for line in lines:
                    if len(current_segment) + len(line) + 1 <= max_length:
                        current_segment += line + '\n'
                    else:
                        if current_segment:
                            segments.append(current_segment.strip())
                        current_segment = line + '\n'
            else:
                if len(current_segment) + len(paragraph) + 2 <= max_length:
                    current_segment += paragraph + '\n\n'
                else:
                    if current_segment:
                        segments.append(current_segment.strip())
                    current_segment = paragraph + '\n\n'
        
        if current_segment:
            segments.append(current_segment.strip())
        
        return segments
    
    @staticmethod
    def _split_by_code_blocks(text, max_length):
        """按代码块分割文本"""
        import re
        
        # 匹配代码块
        code_block_pattern = r'```.*?\n.*?\n```'
        matches = list(re.finditer(code_block_pattern, text, re.DOTALL))
        
        if not matches:
            return []
        
        segments = []
        current_segment = ""
        last_end = 0
        
        for match in matches:
            code_block = match.group(0)
            start_pos = match.start()
            end_pos = match.end()
            
            # 添加代码块之前的文本
            preceding_text = text[last_end:start_pos]
            if preceding_text:
                if len(current_segment) + len(preceding_text) <= max_length:
                    current_segment += preceding_text
                else:
                    if current_segment:
                        segments.append(current_segment)
                    current_segment = preceding_text
            
            # 处理代码块
            if len(code_block) > max_length:
                # 代码块太大，需要分割
                code_segments = MessageSplitter._split_large_code_block(code_block, max_length)
                for code_seg in code_segments:
                    if len(current_segment) + len(code_seg) <= max_length:
                        current_segment += code_seg
                    else:
                        if current_segment:
                            segments.append(current_segment)
                        current_segment = code_seg
            else:
                if len(current_segment) + len(code_block) <= max_length:
                    current_segment += code_block
                else:
                    if current_segment:
                        segments.append(current_segment)
                    current_segment = code_block
            
            last_end = end_pos
        
        # 添加剩余文本
        remaining_text = text[last_end:]
        if remaining_text:
            if len(current_segment) + len(remaining_text) <= max_length:
                current_segment += remaining_text
            else:
                if current_segment:
                    segments.append(current_segment)
                segments.append(remaining_text)
        
        if current_segment:
            segments.append(current_segment)
        
        return [seg for seg in segments if seg.strip()]
    
    @staticmethod
    def _split_large_code_block(code_block, max_length):
        """分割大型代码块"""
        lines = code_block.split('\n')
        segments = []
        current_segment = ""
        
        for line in lines:
            if len(current_segment) + len(line) + 1 <= max_length - 10:  # 预留代码块标记空间
                current_segment += line + '\n'
            else:
                if current_segment:
                    # 完成当前代码段
                    if current_segment.strip().startswith('```'):
                        segments.append(current_segment.rstrip() + '\n```')
                    else:
                        segments.append('```\n' + current_segment.rstrip() + '\n```')
                current_segment = line + '\n'
        
        if current_segment:
            if current_segment.strip().startswith('```'):
                segments.append(current_segment.rstrip() + '\n```')
            else:
                segments.append('```\n' + current_segment.rstrip() + '\n```')
        
        return segments
    
    @staticmethod
    def _split_code_block(code_block, max_length):
        """分割代码块"""
        lines = code_block.split('\n')
        segments = []
        current_segment_lines = []
        current_length = 0
        
        for line in lines:
            line_length = len(line) + 1  # +1 for newline
            
            if current_length + line_length > max_length - 10:  # 预留代码块标记空间
                if current_segment_lines:
                    # 完成当前段
                    segment_text = '\n'.join(current_segment_lines)
                    if segment_text.strip().startswith('```'):
                        segments.append(segment_text + '\n```')
                    else:
                        segments.append('```\n' + segment_text + '\n```')
                    
                    # 开始新段，继续相同的代码块
                    current_segment_lines = [line]
                    current_length = line_length
                else:
                    # 单行就超过限制，强制分割
                    segments.append(f'```\n{line}\n```')
            else:
                current_segment_lines.append(line)
                current_length += line_length
        
        if current_segment_lines:
            segment_text = '\n'.join(current_segment_lines)
            if segment_text.strip().startswith('```'):
                segments.append(segment_text + '\n```')
            else:
                segments.append('```\n' + segment_text + '\n```')
        
        return segments
    
    @staticmethod
    def _split_paragraph(paragraph, max_length):
        """分割段落"""
        sentences = []
        current_sentence = ""
        
        # 简单的句子分割（按句号、问号、感叹号）
        for char in paragraph:
            current_sentence += char
            if char in ['.', '?', '!', '\n']:
                if len(current_sentence.strip()) > 0:
                    sentences.append(current_sentence.strip())
                current_sentence = ""
        
        if current_sentence.strip():
            sentences.append(current_sentence.strip())
        
        # 如果句子分割不成功，按长度强制分割
        if not sentences:
            sentences = [paragraph[i:i+max_length-100] for i in range(0, len(paragraph), max_length-100)]
        
        return sentences

class DeepSeekBot:
    def __init__(self):
        self.application = Application.builder().token(DEEPSEEK_BOT_TOKEN).build()
        self.setup_handlers()
        self.setup_job_queue()
    
    def setup_handlers(self):
        """设置消息处理器"""
        # 命令处理器
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("reset", self.reset_context))
        self.application.add_handler(CommandHandler("status", self.status_command))
        
        # 消息处理器
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        
        # 错误处理器
        self.application.add_error_handler(self.error_handler)
    
    def setup_job_queue(self):
        """设置任务队列用于定时清理"""
        # 每分钟检查一次超时的对话
        self.application.job_queue.run_repeating(
            self.cleanup_expired_conversations,
            interval=60,  # 每60秒检查一次
            first=10      # 10秒后开始第一次检查
        )
    
    async def cleanup_expired_conversations(self, context: CallbackContext):
        """清理过期的对话上下文"""
        try:
            current_time = datetime.now()
            removed_count = 0
            
            # 遍历所有聊天数据
            for chat_id in list(context.application.chat_data.keys()):
                chat_data = context.application.chat_data[chat_id]
                
                if 'last_activity' in chat_data:
                    last_activity = chat_data['last_activity']
                    time_diff = (current_time - last_activity).total_seconds()
                    
                    # 如果超过1小时无活动，清理对话历史
                    if time_diff > CONVERSATION_TIMEOUT:
                        if 'conversation_history' in chat_data:
                            del chat_data['conversation_history']
                            removed_count += 1
                            logger.info(f"已清理聊天 {chat_id} 的过期对话历史")
            
            if removed_count > 0:
                logger.info(f"清理了 {removed_count} 个过期的对话上下文")
                
        except Exception as e:
            logger.error(f"清理过期对话时出错: {e}")
    
    def update_activity_time(self, context: ContextTypes.DEFAULT_TYPE):
        """更新最后活动时间"""
        context.chat_data['last_activity'] = datetime.now()
    
    def is_conversation_expired(self, context: ContextTypes.DEFAULT_TYPE):
        """检查对话是否过期"""
        if 'last_activity' not in context.chat_data:
            return True
        
        last_activity = context.chat_data['last_activity']
        time_diff = (datetime.now() - last_activity).total_seconds()
        return time_diff > CONVERSATION_TIMEOUT
    
    async def send_message(self, update: Update, text: str):
        """发送消息，自动分段并转义Markdown，特别处理大型代码块"""
        if not text or not text.strip():
            return
        
        # 转义Markdown文本
        escaped_text = escape(text)
        
        # 智能分段，特别处理大型代码块
        segments = MessageSplitter.split_message(escaped_text, 4000)
        
        for i, segment in enumerate(segments):
            try:
                if i == 0:
                    await update.message.reply_text(
                        segment,
                        parse_mode='MarkdownV2'
                    )
                else:
                    await update.message.reply_text(
                        segment,
                        parse_mode='MarkdownV2'
                    )
                
                # 短暂延迟，避免发送过快
                if len(segments) > 1:
                    await asyncio.sleep(0.3)
                    
            except Exception as e:
                logger.error(f"发送消息段时出错: {e}")
                # 如果Markdown发送失败，尝试发送纯文本
                try:
                    await update.message.reply_text(segment)
                except Exception as fallback_error:
                    logger.error(f"纯文本回退也失败: {fallback_error}")
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /start 命令"""
        self.update_activity_time(context)
        
        welcome_text = """
🤖 欢迎使用 DeepSeek AI 助手！

我可以帮你：
• 回答各种问题
• 协助写作和编程
• 进行对话交流
• 提供学习和工作建议

💡 特性：
- 我会记住我们的对话上下文
- 1小时无活动后自动重置对话
- 支持多轮连续对话
- 完美支持代码块和Markdown格式

直接发送消息即可开始对话！
使用 /reset 可以立即重置对话
使用 /help 查看详细帮助
        """
        await self.send_message(update, welcome_text)
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /help 命令"""
        self.update_activity_time(context)
        
        help_text = """
📖 DeepSeek AI 助手使用指南

🤖 功能特性：
- 智能对话交流
- 问题解答和知识查询
- 创作和编程协助
- 学习辅导和工作建议

⚡ 命令列表：
/start - 开始使用机器人
/help - 显示此帮助信息
/reset - 立即重置对话上下文
/status - 查看当前对话状态

⏰ 自动清理：
- 为了节省资源，1小时无对话后会自动重置
- 重置后开始全新的对话
- 使用 /reset 可手动立即重置

💡 使用提示：
- 直接发送消息即可与我对话
- 我会记住最近的对话上下文
- 完美支持代码块和Markdown格式显示
- 如果回复异常，使用 /reset 重置
        """
        await self.send_message(update, help_text)
    
    async def reset_context(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """重置对话上下文"""
        self.update_activity_time(context)
        
        if 'conversation_history' in context.chat_data:
            context.chat_data['conversation_history'] = []
        
        await self.send_message(update, "✅ 对话上下文已重置，我们可以重新开始了！")
    
    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """查看对话状态"""
        self.update_activity_time(context)
        
        # 计算对话历史长度
        history_length = len(context.chat_data.get('conversation_history', []))
        
        # 计算剩余时间
        if 'last_activity' in context.chat_data:
            last_activity = context.chat_data['last_activity']
            time_passed = (datetime.now() - last_activity).total_seconds()
            time_remaining = CONVERSATION_TIMEOUT - time_passed
            minutes_remaining = max(0, int(time_remaining // 60))
        else:
            minutes_remaining = 0
        
        status_text = f"""
📊 当前对话状态

🗣️ 对话轮次: {history_length}
⏰ 自动重置剩余: {minutes_remaining} 分钟
🤖 使用模型: {DEEPSEEK_MODEL}

💡 提示: 1小时无对话后会自动重置上下文
        """
        
        await self.send_message(update, status_text)
    
    def call_deepseek_api(self, message: str, conversation_history: list) -> str:
        """调用DeepSeek API"""
        try:
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}"
            }
            
            # 构建消息历史
            messages = conversation_history.copy()
            messages.append({"role": "user", "content": message})
            
            payload = {
                "model": DEEPSEEK_MODEL,
                "messages": messages,
                "stream": False,
                "max_tokens": 2048,
                "temperature": 0.7
            }
            
            response = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=60)
            response.raise_for_status()
            
            result = response.json()
            ai_response = result['choices'][0]['message']['content']
            
            # 更新对话历史（限制历史长度避免token过多）
            messages.append({"role": "assistant", "content": ai_response})
            if len(messages) > 10:  # 保持最近10轮对话
                messages = messages[-10:]
            
            return ai_response, messages
            
        except requests.exceptions.RequestException as e:
            logger.error(f"API请求错误: {e}")
            return "抱歉，网络连接出现问题，请稍后重试。", conversation_history
        except KeyError as e:
            logger.error(f"API响应格式错误: {e}")
            return "抱歉，AI服务响应异常，请稍后重试。", conversation_history
        except Exception as e:
            logger.error(f"未知错误: {e}")
            return "抱歉，发生了未知错误，请稍后重试。", conversation_history
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理用户消息"""
        user_message = update.message.text
        user_id = update.effective_user.id
        
        logger.info(f"收到来自用户 {user_id} 的消息: {user_message}")
        
        # 更新最后活动时间
        self.update_activity_time(context)
        
        # 检查对话是否过期
        if self.is_conversation_expired(context):
            if 'conversation_history' in context.chat_data:
                context.chat_data['conversation_history'] = []
            await self.send_message(update, "💤 检测到长时间无对话，已自动重置上下文开始新对话。")
        
        # 显示"正在输入"状态
        await update.message.chat.send_action(action="typing")
        
        # 获取或初始化对话历史
        if 'conversation_history' not in context.chat_data:
            context.chat_data['conversation_history'] = []
        
        # 调用DeepSeek API
        ai_response, updated_history = self.call_deepseek_api(
            user_message, 
            context.chat_data['conversation_history']
        )
        
        # 更新对话历史
        context.chat_data['conversation_history'] = updated_history
        
        # 再次更新活动时间（API调用后）
        self.update_activity_time(context)
        
        # 使用新的发送方法
        await self.send_message(update, ai_response)
    
    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理错误"""
        logger.error(f"异常发生时更新 {update} 导致错误: {context.error}")
        
        try:
            # 通知用户发生了错误
            if update and update.message:
                await self.send_message(update, "抱歉，发生了内部错误，请稍后重试。")
        except Exception as e:
            logger.error(f"在错误处理中发生异常: {e}")

def main():
    """主函数"""
    # 检查必要的环境变量
    if not DEEPSEEK_BOT_TOKEN or not DEEPSEEK_API_KEY:
        logger.error("请设置 TELEGRAM_BOT_TOKEN 和 DEEPSEEK_API_KEY 环境变量")
        return
    
    # 创建并启动机器人
    bot = DeepSeekBot()
    
    logger.info("🤖 DeepSeek AI 电报机器人启动成功！")
    logger.info(f"对话超时时间: {CONVERSATION_TIMEOUT} 秒 (1小时)")
    print("机器人正在运行... 按 Ctrl+C 停止")
    
    # 启动机器人
    bot.application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()