import imaplib
import email
from email.header import decode_header
import html2text
import telegram
from telegram.helpers import escape_markdown
import os
import asyncio
import re
import chardet
import logging
from dotenv import load_dotenv
from email.utils import parseaddr
import google.generativeai as genai
from typing import Optional
from md2tgmd import escape
import time
from collections import deque

# 版本检查
from telegram import __version__ as TG_VER
if int(TG_VER.split('.')[0]) < 20:
    raise RuntimeError(
        f"此代码需要python-telegram-bot>=20.0，当前版本是{TG_VER}。\n"
        "请执行: pip install --upgrade python-telegram-bot"
    )

load_dotenv()

# 配置信息
IMAP_SERVER = os.getenv("IMAP_SERVER")
EMAIL_ADDRESS = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_API_KEY")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
MAX_MESSAGE_LENGTH = 3800
DEBUG_MODE = os.getenv("DEBUG_MODE", "false").lower() == "true" # 
GEMINI_RETRY_DELAY = 5
GEMINI_MAX_RETRIES = 3
IMAP_TIMEOUT = 30  # IMAP连接超时时间(秒)
TELEGRAM_RATE_LIMIT = 1.0  # 每条消息之间的最小间隔(秒)
RUN_INTERVAL = 300  # 每5分钟运行一次(秒)

# 配置logging
logging.basicConfig(
    level=logging.DEBUG if DEBUG_MODE else logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

class EmailDecoder:
    @staticmethod
    def decode_email_header(header):
        """智能解码邮件头"""
        if not header:
            return ""
        try:
            decoded = decode_header(header)
            return ''.join([
                t[0].decode(t[1] or 'utf-8', errors='ignore')
                if isinstance(t[0], bytes)
                else str(t[0])
                for t in decoded
            ])
        except Exception as e:
            logging.error(f"Header decode error: {e}", exc_info=True)
            return str(header)

    @staticmethod
    def detect_encoding(content):
        """编码检测优化"""
        try:
            result = chardet.detect(content)
            if result['confidence'] > 0.7:
                return result['encoding']
            return 'gb18030' if b'\x80' in content[:100] else 'utf-8'
        except Exception as e:
            logging.error(f"Encoding detection error: {e}", exc_info=True)
            return 'utf-8'

class ContentProcessor:
    @staticmethod
    def normalize_newlines(text):
        """统一换行符并合并空行"""
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        return re.sub(r'\n{3,}', '\n\n', text)

    @staticmethod
    def clean_text(text):
        """文本清洗"""
        return text.strip()

    @staticmethod
    def extract_urls(html):
        """智能链接过滤"""
        url_pattern = re.compile(
            r'(https?://[^\s>"\'{}|\\^`]+)',
            re.IGNORECASE
        )
        urls = []
        seen = set()
        exclude_domains = {'w3.org', 'schema.org', 'example.com'}

        for match in url_pattern.finditer(html):
            raw_url = match.group(1)
            clean_url = raw_url.split('"')[0]

            if not (10 < len(clean_url) <= 200):
                continue

            if any(d in clean_url for d in exclude_domains):
                continue

            if clean_url not in seen:
                seen.add(clean_url)
                urls.append((raw_url, clean_url, match.start(), match.end()))

        return urls[:15]

    @staticmethod
    def convert_html_to_text(html_bytes):
        """HTML转换强化"""
        try:
            encoding = EmailDecoder.detect_encoding(html_bytes)
            html = html_bytes.decode(encoding, errors='replace')

            converter = html2text.HTML2Text()
            converter.body_width = 0
            converter.ignore_links = True
            converter.ignore_images = True
            converter.ignore_emphasis = True

            text = converter.handle(html)
            text = ContentProcessor.clean_text(text)

            urls = ContentProcessor.extract_urls(html)

            offset = 0
            for raw_url, clean_url, start, end in sorted(urls, key=lambda x: x[2]):
                text = text[:start + offset] + clean_url + text[end + offset:]
                offset += len(clean_url) - (end - start)

            final_text = text
            return ContentProcessor.normalize_newlines(final_text)

        except Exception as e:
            logging.error(f"HTML处理失败: {e}", exc_info=True)
            return "⚠️ 内容解析异常"

class EmailHandler:
    @staticmethod
    def get_email_content(msg):
        """统一内容获取"""
        try:
            content = ""
            for part in msg.walk():
                if part.get_content_type() == 'text/html':
                    html_bytes = part.get_payload(decode=True)
                    content = ContentProcessor.convert_html_to_text(html_bytes)
                    break

            if not content:
                for part in msg.walk():
                    if part.get_content_type() == 'text/plain':
                        text_bytes = part.get_payload(decode=True)
                        encoding = EmailDecoder.detect_encoding(text_bytes)
                        raw_text = text_bytes.decode(encoding, errors='replace')
                        content = ContentProcessor.clean_text(raw_text)
                        break

            if not content and any(part.get_content_maintype() == 'image' for part in msg.walk()):
                content = "📨 图片内容（文本信息如下）\n" + "\n".join(
                    f"{k}: {v}" for k, v in msg.items() if k.lower() in ['subject', 'from', 'date']
                )

            return ContentProcessor.normalize_newlines(content or "⚠️ 无法解析内容")

        except Exception as e:
            logging.error(f"内容提取失败: {e}", exc_info=True)
            return "⚠️ 内容提取异常"

class MessageFormatter:
    @staticmethod
    def format_message(sender, subject, content):
        """生成未转义的原始头部（后续统一用 md2tgmd 转义）"""
        realname, email_address = parseaddr(sender)
        header = (
            f"✉️ **{realname}** "  # 保留*不转义
            f"`{email_address}`\n"  # 保留`不转义
            f"_{subject}_\n\n"     # 保留_不转义
        )
        return header, content

    @staticmethod
    def split_content(text, max_length):
        """智能分割优化（返回分割后的块列表）"""
        chunks = []
        current_chunk = []
        current_length = 0

        paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]

        for para in paragraphs:
            potential_add = len(para) + (2 if current_chunk else 0)

            if current_length + potential_add > max_length:
                if current_chunk:
                    chunks.append('\n\n'.join(current_chunk))
                    current_chunk = []
                    current_length = 0

                    if len(para) > max_length:
                        start = 0
                        while start < len(para):
                            end = start + max_length
                            chunks.append(para[start:end])
                            start = end
                        continue
                    else:
                        current_chunk.append(para)
                        current_length = len(para)
                else:
                    start = 0
                    while start < len(para):
                        end = start + max_length
                        chunks.append(para[start:end])
                        start = end
            else:
                current_chunk.append(para)
                current_length += potential_add

        if current_chunk:
            chunks.append('\n\n'.join(current_chunk))

        final_chunks = []
        for chunk in chunks:
            while len(chunk) > max_length:
                final_chunks.append(chunk[:max_length])
                chunk = chunk[max_length:]
            if chunk:
                final_chunks.append(chunk)

        return final_chunks

class GeminiAI:
    def __init__(self, api_key):
        """初始化Gemini模型（增强媒体URL处理版）
        
        Args:
            api_key: Gemini API密钥
        """
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel('gemini-2.0-flash-exp')
        
        # 媒体URL正则（匹配http/https协议）
        self._media_url_regex = re.compile(
            r'((?:https?://)[^\s>"\'{}|\\^`]+\.(?:'
            r'png|jpe?g|gif|bmp|webp|svg|ico|tiff?|'  # 图片格式
            r'mp4|mov|avi|mkv|webm|flv|wmv|3gp|mpe?g|'  # 视频格式
            r'mp3|wav|ogg|flac|aac|m4a|wma))',  # 音频格式
            re.IGNORECASE
        )
        
        # 通用URL正则（用于去重）
        self._url_regex = re.compile(
            r'(https?://[^\s>"\'{}|\\^`]+)', 
            re.IGNORECASE
        )

    def generate_summary(self, text: str) -> Optional[str]:
        """生成邮件正文摘要"""
        prompt = """Please process the following message body content (only the processed information will be returned):
1. The content is not in Chinese, please translate it into Chinese while retaining technical terms
2. Use the standard Markdown format:
   - Bold: ** Important content **
   - Italic: _Comments_
   - Constant width font: `Code`
3. Necessary line breaks and spaces
4. For URLs:
   - Automatically find previous descriptions
   - Convert to Markdown hyperlink format: [Description](URL)
5. If the content is too long, extract key information and summarize it
6. Do not include any instructions on the processing process in the output
7. Ensure that the output can be sent directly as a Telegram markdown message and do not escape any characters"""  # 保持原有prompt不变
        try:
            processed_text = self._preprocess_text(text)
            response = self.model.generate_content(
                prompt + processed_text,
                generation_config={
                    "temperature": 0.3,
                    "max_output_tokens": 2000
                }
            )
            return response.text if response.text else None
        except Exception as e:
            logging.error(f"AI处理失败: {str(e)[:200]}...")  # 截断长错误信息
            return None

    def _preprocess_text(self, text: str) -> str:
        """
        文本预处理增强版：
        1. 过滤所有媒体URL（图片/视频/音频）
        2. 智能URL去重（忽略http/https协议差异）
        
        Args:
            text: 原始文本
            
        Returns:
            str: 处理后的文本
        """
        # 1. 过滤媒体URL（保留原始协议）
        text = self._media_url_regex.sub('[MEDIA]', text)
        
        # 2. URL去重处理
        seen_urls = set()
        def replace_duplicate_urls(match):
            url = match.group(1)
            # 标准化比较（忽略协议和大小写）
            normalized = url.lower().replace('http://', 'https://')
            if normalized in seen_urls:
                return ""  # 移除重复URL
            seen_urls.add(normalized)
            return url  # 保留原始URL
            
        return self._url_regex.sub(replace_duplicate_urls, text)

class TelegramBot:
    def __init__(self):
        self.bot = telegram.Bot(TELEGRAM_TOKEN)
        self.last_message_time = 0
        self.message_queue = deque()

    async def _rate_limited_send(self, raw_text, escaped_text=None, parse_mode='MarkdownV2'):
        """带速率限制的消息发送"""
        now = time.time()
        elapsed = now - self.last_message_time
        if elapsed < TELEGRAM_RATE_LIMIT:
            await asyncio.sleep(TELEGRAM_RATE_LIMIT - elapsed)
        
        try:
            await self.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=escaped_text or raw_text,
                parse_mode=parse_mode,
                disable_web_page_preview=True
            )
            self.last_message_time = time.time()
        except telegram.error.BadRequest:
            await self.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=raw_text,
                parse_mode=None,
                disable_web_page_preview=True
            )
            self.last_message_time = time.time()
        except Exception as e:
            logging.error(f"消息发送失败: {e}")

    async def send_message(self, raw_text, escaped_text=None, parse_mode='MarkdownV2'):
        """将消息加入队列并处理"""
        self.message_queue.append((raw_text, escaped_text, parse_mode))
        await self._process_queue()

    async def _process_queue(self):
        """处理消息队列"""
        while self.message_queue:
            raw_text, escaped_text, parse_mode = self.message_queue.popleft()
            await self._rate_limited_send(raw_text, escaped_text, parse_mode)

def clean_ai_text(text: str) -> str:
    """
    清理AI处理后的文本：
    1. 移除所有|符号
    2. 移除仅含-或多于2个连续-的行
    """
    if not text:
        return text
    
    # 1. 移除所有|符号
    text = text.replace('|', '')
    
    # 2. 清理无效的-行
    lines = text.split('\n')
    cleaned_lines = []
    
    for line in lines:
        stripped_line = line.strip()
        # 跳过仅含-的行
        if stripped_line.replace('-', '') == '' and len(stripped_line) >= 1:
            continue
        cleaned_lines.append(line)
    
    return '\n'.join(cleaned_lines)

async def check_emails():
    """检查邮件的核心逻辑"""
    bot = TelegramBot()
    gemini_ai = GeminiAI(api_key=os.getenv("GEMINI_API_KEY")) if os.getenv("GEMINI_API_KEY") else None

    try:
        # 使用带超时的IMAP连接
        with imaplib.IMAP4_SSL(IMAP_SERVER, timeout=IMAP_TIMEOUT) as mail:
            mail.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            mail.select("INBOX")

            _, nums = mail.search(None, "UNSEEN")
            if not nums[0]:
                logging.info("无未读邮件")
                return

            for num in nums[0].split():
                try:
                    # 1. 获取原始邮件内容
                    _, data = mail.fetch(num, "(RFC822)")
                    msg = email.message_from_bytes(data[0][1])
                    sender = EmailDecoder.decode_email_header(msg.get("From"))
                    subject = EmailDecoder.decode_email_header(msg.get("Subject"))
                    raw_content = EmailHandler.get_email_content(msg)

                    # 2. 生成未转义的头部和正文
                    header = (
                        f"✉️ **{parseaddr(sender)[0]}** "
                        f"`{parseaddr(sender)[1]}`\n"
                        f"_{subject}_\n\n"
                    )
                    body = raw_content

                    # 3. AI处理并清理文本
                    if gemini_ai:
                        body = gemini_ai.generate_summary(body) or body
                        body = clean_ai_text(body)

                    # 4. 准备发送内容
                    safe_message = escape(f"{header}{body}")
                    raw_message = f"{header}{body}"

                    # 5. 分割内容
                    chunks = MessageFormatter.split_content(safe_message, MAX_MESSAGE_LENGTH)
                    raw_chunks = MessageFormatter.split_content(raw_message, MAX_MESSAGE_LENGTH)

                    # 6. 发送消息
                    for safe_chunk, raw_chunk in zip(chunks, raw_chunks):
                        await bot.send_message(raw_chunk, safe_chunk)

                    # 7. 标记为已读
                    mail.store(num, "+FLAGS", "\\Seen")

                except Exception as e:
                    logging.error(f"邮件处理异常: {e}", exc_info=True)
                    continue

    except imaplib.IMAP4.abort as e:
        logging.error(f"IMAP连接超时: {e}", exc_info=True)
    except Exception as e:
        logging.error(f"IMAP连接异常: {e}", exc_info=True)

async def main():
    """主循环，每5分钟运行一次检查"""
    while True:
        try:
            await check_emails()
        except Exception as e:
            logging.error(f"主循环异常: {e}", exc_info=True)
        
        await asyncio.sleep(RUN_INTERVAL)

if __name__ == "__main__":
    asyncio.run(main())