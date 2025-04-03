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
MAX_MESSAGE_LENGTH = 3900
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
    def clean_dash_lines(text: str) -> str:
        """
        清除正文中：
        1. 删除所有 | 字符
        2. 删除仅含 - 的行
        """
        # 先删除所有 | 字符
        text = text.replace('|', ' ')
        
        # 再删除仅含 - 的行（保留空格避免段落粘连）
        return re.sub(r'^\s*-+\s*$', '', text, flags=re.MULTILINE)

    @staticmethod
    def normalize_newlines(text: str) -> str:
        """统一换行符并合并空行（应在其他清洗操作后调用）"""
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        return re.sub(r'\n{3,}', '\n\n', text)

    @staticmethod
    def preprocess_content(text: str) -> str:
        """正文预处理总入口（按合理顺序执行）"""
        text = ContentProcessor.clean_dash_lines(text)  # 先处理特殊符号
        text = ContentProcessor.normalize_newlines(text)  # 最后统一换行
        return text

    @staticmethod
    def preprocess_content(text: str) -> str:
        """正文预处理总入口"""
        text = ContentProcessor.clean_dash_lines(text)
        return ContentProcessor.normalize_newlines(text)
    
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

            if not (10 < len(clean_url) <= 300):
                continue

            if any(d in clean_url for d in exclude_domains):
                continue

            if clean_url not in seen:
                seen.add(clean_url)
                urls.append((raw_url, clean_url, match.start(), match.end()))

        return urls[:6]

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
            f"✉️ ​**{realname}**\n"
            f"`{email_address}`\n"
            f"_{subject}_\n\n"
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
        """初始化Gemini模型（保留所有正则表达式）"""
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel('gemini-2.0-flash-exp')
        
        # 保留的敏感信息检测规则
        self._card_regex = re.compile(r'\b\d{12}(\d{4})\b')      # 信用卡号（保留后4位）
        self._base64_regex = re.compile(r'[a-zA-Z0-9+/]{50,}')   # Base64长字符串

    def _preprocess_text(self, text: str) -> str:
        """文本预处理（仍然使用正则表达式脱敏）"""
        text = self._card_regex.sub('****-****-****-\\1', text)  # 信用卡号脱敏
        text = self._base64_regex.sub('[DATA]', text)            # Base64替换
        return text

    def generate_summary(self, text: str) -> Optional[str]:
        """生成摘要（预处理后包含敏感信息过滤）"""
        try:
            cleaned_text = ContentProcessor.preprocess_content(text)  # 新增：清除短横线行
            processed_text = self._preprocess_text(cleaned_text)     # 原有：敏感信息过滤
            response = self.model.generate_content(
                "请处理邮件正文：\n" + processed_text,
                generation_config={"temperature": 0.3}
            )
            return response.text if response.text else None
        except Exception as e:
            logging.error(f"AI处理失败: {e}")
            return None

    def generate_summary(self, text: str) -> Optional[str]:
        """
        生成邮件正文摘要（输出原始Markdown不转义）
        """
        prompt = """请处理邮件正文（不要处理发件人）：
1. 总结内容并翻译为中文保留专业词汇
使用电报机器人标准MarkdownV2格式：
   - 加粗：​**重点** 
   - 斜体：_备注_
   - 等宽：`代码`
2. 保持换行和段落
3. 不要转义任何字符（保留_*等符号）
5. url自动寻找前面词组，替换为md超链接。
5.一行之中只有-这个符号或多个-符号时，直接删除这一行。
正文："""
        try:
            processed_text = self._preprocess_text(text)
            response = self.model.generate_content(
                prompt + processed_text,
                generation_config={"temperature": 0.3}
            )
            return response.text if response.text else None
        except Exception as e:
            logging.error(f"AI处理失败: {e}")
            return None

    def translate_subject(self, subject: str) -> Optional[str]:
        """翻译邮件主题（不保留原始主题）"""
        if not subject.strip():
            return None
            
        try:
            response = self.model.generate_content(
                "将以下邮件主题翻译为中文，直接返回结果不要带引号或其他修饰：\n" + subject,
                generation_config={"temperature": 0.1}
            )
            return response.text.strip() if response.text else None
        except Exception as e:
            logging.debug(f"主题翻译失败（原始主题: {subject}）: {e}")
            return None

    def _preprocess_text(self, text: str) -> str:
        """文本预处理"""
        text = self._card_regex.sub('****-****-****-\\1', text)
        return self._base64_regex.sub('[DATA]', text)

class TelegramBot:
    def __init__(self):
        self.bot = telegram.Bot(TELEGRAM_TOKEN)

    async def send_message(self, text, parse_mode='MarkdownV2'):
        """发送消息（自动处理转义）"""
        try:
            await self.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=text,
                parse_mode=parse_mode,
                disable_web_page_preview=True
            )
        except telegram.error.BadRequest:
            # 格式失败时回退纯文本
            await self.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=text,
                parse_mode=None
            )
        except Exception as e:
            logging.error(f"消息发送失败: {e}")

async def main():
    bot = TelegramBot()
    gemini_api_key = os.getenv("GEMINI_API_KEY")
    gemini_ai = GeminiAI(api_key=gemini_api_key) if gemini_api_key else None

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
                    # 获取原始邮件内容
                    _, data = mail.fetch(num, "(RFC822)")
                    msg = email.message_from_bytes(data[0][1])
                    sender = EmailDecoder.decode_email_header(msg.get("From"))
                    original_subject = EmailDecoder.decode_email_header(msg.get("Subject"))
                    raw_content = EmailHandler.get_email_content(msg)

                    # 处理主题翻译
                    final_subject = original_subject  # 默认使用原主题
                    if gemini_ai:
                        translated = gemini_ai.translate_subject(original_subject)
                        if translated:  # 只有翻译成功时才替换
                            final_subject = translated

                    # 生成消息头部（仅含翻译后/原始主题）
                    header = (
                        f"✉️ ​**{parseaddr(sender)[0]}**\n"
                        f"`{parseaddr(sender)[1]}`\n"
                        f"_{final_subject}_\n\n"
                    )
                    body = raw_content

                    # AI处理正文（不转义）
                    if gemini_ai:
                        body = gemini_ai.generate_summary(body) or body

                    # 统一转义（关键步骤）
                    safe_message = escape(f"{header}{body}")

                    # 分割发送
                    chunks = MessageFormatter.split_content(safe_message, MAX_MESSAGE_LENGTH)
                    for chunk in chunks:
                        await bot.send_message(
                            text=chunk,
                            parse_mode='MarkdownV2'
                        )

                    mail.store(num, "+FLAGS", "\\Seen")

                except Exception as e:
                    logging.error(f"邮件处理异常: {e}", exc_info=True)

    except Exception as e:
        logging.error(f"IMAP连接异常: {e}", exc_info=True)

if __name__ == "__main__":
    asyncio.run(main())