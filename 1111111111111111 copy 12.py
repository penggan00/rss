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
DEBUG_MODE = os.getenv("DEBUG_MODE", "false").lower() == "true"
GEMINI_RETRY_DELAY = 5
GEMINI_MAX_RETRIES = 3

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
   #     exclude_domains = {'w3.org', 'schema.org', 'example.com'}

        for match in url_pattern.finditer(html):
            raw_url = match.group(1)
            clean_url = raw_url.split('"')[0]

            if not (10 < len(clean_url) <= 200):
                continue

    #        if any(d in clean_url for d in exclude_domains):
    #            continue

            if clean_url not in seen:
                seen.add(clean_url)
                urls.append((raw_url, clean_url, match.start(), match.end()))

        return urls[:5]

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
        """初始化Gemini模型"""
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel('gemini-2.0-flash-exp')
        self._card_regex = re.compile(r'\b\d{12}(\d{4})\b')
        self._base64_regex = re.compile(r'[a-zA-Z0-9+/]{50,}')

    def generate_summary(self, text: str) -> Optional[str]:
        """
        生成邮件正文摘要（输出原始Markdown不转义）
        """
        prompt = """Please process the email body (do not process sender or subject):
1.Organize streamlining in Chinese while retaining technical terms
Use telegram MarkdownV2 format:
   - Bold: ​**important** 
   - Italic: _note_
   - Monospace: `code`
2. Maintain line breaks and paragraphs
3. Do not escape any characters (keep _* etc. as-is)
4. For URLs, automatically find preceding or above-line phrases and convert to Markdown hyperlinks.
5.Delete the picture link. If there is a duplicate link, only one should be kept,
6.Strictly implement the prompt words urls
"""
        try:
            # processed_text = self._preprocess_text(text)  # Remove this line
            response = self.model.generate_content(
                prompt + text,  # Use text directly
                generation_config={"temperature": 0.3}
            )
            return response.text if response.text else None
        except Exception as e:
            logging.error(f"AI处理失败: {e}")
            return None


class TelegramBot:
    def __init__(self):
        self.bot = telegram.Bot(TELEGRAM_TOKEN)

    async def send_message(self, raw_text, escaped_text=None, parse_mode='MarkdownV2'):
        """发送消息，失败时回退到未转义的原始文本"""
        try:
            await self.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=escaped_text or raw_text,  # 优先用转义后的
                parse_mode=parse_mode,
                disable_web_page_preview=True
            )
        except telegram.error.BadRequest:
            # 回退到未转义的原始文本
            await self.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=raw_text,  # 发送未转义的原始内容
                parse_mode=None,
                disable_web_page_preview=True
            )
        except Exception as e:
            logging.error(f"消息发送失败: {e}")

async def main():
    # 初始化
    from md2tgmd import escape
    bot = TelegramBot()
    gemini_ai = GeminiAI(api_key=os.getenv("GEMINI_API_KEY")) if os.getenv("GEMINI_API_KEY") else None

    def clean_ai_text(text: str) -> str:
        """
        清理AI处理后的文本：
        1. 移除所有|符号
        2. 移除仅含-或多于2个连续-的行
        """
        if not text:
            return text
        
        # 1. 移除所有|符号
        text = text.replace('|', ' ')
        
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

    try:
        with imaplib.IMAP4_SSL(IMAP_SERVER) as mail:
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
                        body = clean_ai_text(body)  # 清理|和-行

                    # 4. 准备发送内容
                    safe_message = escape(f"{header}{body}")  # 转义后的Markdown
                    raw_message = f"{header}{body}"           # 原始未转义文本

                    # 5. 分割内容
                    chunks = MessageFormatter.split_content(safe_message, MAX_MESSAGE_LENGTH)
                    raw_chunks = MessageFormatter.split_content(raw_message, MAX_MESSAGE_LENGTH)

                    # 6. 发送消息
                    for safe_chunk, raw_chunk in zip(chunks, raw_chunks):
                        await bot.send_message(
                            raw_text=raw_chunk,
                            escaped_text=safe_chunk,
                            parse_mode='MarkdownV2'
                        )

                    # 7. 标记为已读
                    mail.store(num, "+FLAGS", "\\Seen")

                except Exception as e:
                    logging.error(f"邮件处理异常: {e}", exc_info=True)
                    continue

    except Exception as e:
        logging.error(f"IMAP连接异常: {e}", exc_info=True)

if __name__ == "__main__":
    asyncio.run(main())
