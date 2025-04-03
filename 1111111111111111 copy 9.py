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
GEMINI_RETRY_DELAY = 2
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
        exclude_domains = {'w3.org', 'schema.org', 'example.com'}

        for match in url_pattern.finditer(html):
            raw_url = match.group(1)
            clean_url = raw_url.split('"')[0]

            if not (10 < len(clean_url) <= 100):
                continue

            if any(d in clean_url for d in exclude_domains):
                continue

            if clean_url not in seen:
                seen.add(clean_url)
                urls.append((raw_url, clean_url, match.start(), match.end()))

        return urls[:10]

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
        prompt = """请处理邮件正文（不要处理发件人）：
1. 总结内容并翻译为中文
使用标准MarkdownV2格式：
   - 加粗：**重点** 
   - 斜体：_备注_
   - 等宽：`代码`
2. 保持换行和段落
3. 不要转义任何字符（保留_*等符号）
4. url自动寻找前面或上面词组，替换为md超链接。
5.总结与翻译二选一项即可，优先翻译。（不要注释总结和翻译直接给内容）
正文："""
        try:
            # 1. AI处理原始文本（不提前清理符号）
            processed_text = self._preprocess_text(text)  # 仅脱敏敏感信息
            response = self.model.generate_content(
                prompt + processed_text,
                generation_config={"temperature": 0.3}
            )
        
            # 2. AI返回后清理符号
            if response.text:
                cleaned_text = self._postprocess_text(response.text)  # 新增后处理
                return cleaned_text
            return None
        except Exception as e:
            logging.error(f"AI处理失败: {e}")
            return None

def _postprocess_text(self, text: str) -> str:
    """后处理：清理AI返回结果中的符号"""
    # 1. 删除单独一行中的 - 或多个 -
    text = re.sub(r'^[-]+$', '', text, flags=re.MULTILINE)
    # 2. 删除 | 符号
    text = text.replace("|", "")
    return text.strip()

    def _preprocess_text(self, text: str) -> str:
        """文本预处理"""
        text = self._card_regex.sub('****-****-****-\\1', text)
        return self._base64_regex.sub('[DATA]', text)

class TelegramBot:
    def __init__(self):
        self.bot = telegram.Bot(TELEGRAM_TOKEN)

    async def send_message(self, text, parse_mode='MarkdownV2'):
        """异步发送消息（自动处理转义）"""
        try:
            await self.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=escape(text) if parse_mode == 'MarkdownV2' else text,
                parse_mode=parse_mode,
                disable_web_page_preview=True
            )
        except telegram.error.BadRequest:
            # 回退到纯文本（不转义）
            await self.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=text,
                parse_mode=None
            )
        except Exception as e:
            logging.error(f"消息发送失败: {e}")

async def main():
    # 初始化
    bot = TelegramBot()
    gemini_ai = GeminiAI(api_key=os.getenv("GEMINI_API_KEY")) if os.getenv("GEMINI_API_KEY") else None

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
                    subject = EmailDecoder.decode_email_header(msg.get("Subject")) or parseaddr(sender)[1]  # 无主题时用邮箱地址替代
                    raw_content = EmailHandler.get_email_content(msg)

                    # 2. 生成未转义的头部和正文（保留*_`等符号）
                    realname, email_address = parseaddr(sender)
                    header = (
                        f"✉️ ​**{realname}** "
                        f"`{email_address}`\n"
                        f"_{subject}_\n\n"
                    )
                    body = raw_content

                    # 3. AI处理（不转义内部Markdown）
                    if gemini_ai:
                        body = gemini_ai.generate_summary(body) or body

                    # 4. 合并消息（不立即转义）
                    full_message = f"{header}{body}"

                    # 5. 分割发送（由send_message内部处理转义）
                    chunks = MessageFormatter.split_content(full_message, MAX_MESSAGE_LENGTH)
                    for chunk in chunks:
                        await bot.send_message(
                            text=chunk,
                            parse_mode='MarkdownV2'  # 在send_message内部处理转义
                        )

                    mail.store(num, "+FLAGS", "\\Seen")

                except Exception as e:
                    logging.error(f"邮件处理异常: {e}", exc_info=True)
                    # 尝试用纯文本发送原始内容（不含格式）
                    try:
                        plain_text = f"新邮件来自: {parseaddr(sender)[0]} <{parseaddr(sender)[1]}>\n"
                        plain_text += f"主题: {subject}\n\n"
                        plain_text += raw_content[:MAX_MESSAGE_LENGTH]
                        await bot.send_message(
                            text=plain_text,
                            parse_mode=None
                        )
                    except Exception as fallback_error:
                        logging.error(f"纯文本回退发送也失败: {fallback_error}")

    except Exception as e:
        logging.error(f"IMAP连接异常: {e}", exc_info=True)
        # 尝试发送错误通知
        try:
            await bot.send_message(
                text=f"⚠️ 邮件检查失败: {str(e)[:500]}",
                parse_mode=None
            )
        except Exception as notify_error:
            logging.error(f"错误通知发送失败: {notify_error}")

if __name__ == "__main__":
    asyncio.run(main())