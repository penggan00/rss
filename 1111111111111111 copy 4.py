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
        
class TextPreprocessor:
    @staticmethod
    def clean_dash_lines(text: str) -> str:
        """
        清除正文中的特殊格式：
        1. 删除所有竖线 | 字符（替换为空格）
        2. 删除仅由横线 - 组成的行（保留换行避免段落粘连）
        3. 删除连续3个以上的-符号（但保留--这样的符号）
        """
        # 删除所有 | 字符
        text = text.replace('|', ' ')
        
        # 删除仅含 - 的行
        text = re.sub(r'^\s*-+\s*$', '', text, flags=re.MULTILINE)
        
        # 删除连续3个以上的-符号
        return re.sub(r'(?<!-)-{3,}(?!-)', '', text)

    @staticmethod
    def normalize_newlines(text: str) -> str:
        """统一换行符并合并空行（应在其他清洗操作后调用）"""
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        return re.sub(r'\n{3,}', '\n\n', text)

    @staticmethod
    def remove_leading_trailing_spaces(text: str) -> str:
        """删除每行开头和结尾的空格"""
        return '\n'.join(line.strip() for line in text.split('\n'))

    @staticmethod
    def clean_repeated_chars(text: str, chars: str = '-=_*', max_repeat: int = 2) -> str:
        """清理重复的特殊字符"""
        for char in chars:
            pattern = re.escape(char) + '{' + str(max_repeat + 1) + ',}'
            text = re.sub(pattern, char * max_repeat, text)
        return text

    @staticmethod
    def preprocess(text: str) -> str:
        """
        文本预处理总入口（按合理顺序执行）
        处理流程：特殊字符 → 重复字符 → 首尾空格 → 换行符
        """
        text = TextPreprocessor.clean_dash_lines(text)
        text = TextPreprocessor.clean_repeated_chars(text)
        text = TextPreprocessor.remove_leading_trailing_spaces(text)
        return TextPreprocessor.normalize_newlines(text)
    
class ContentProcessor:
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
            f"✉️ **{realname}**"  # 保留*不转义
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
        prompt = """请处理邮件正文（不要处理发件人）：
    1. 总结内容并翻译为中文
    使用标准MarkdownV2格式：
       - 加粗：​**重点** 
       - 斜体：_备注_
       - 等宽：`代码`
    2. 保持换行和段落
    3. 不要转义任何字符（保留_*等符号）
    5. url自动寻找前面词组，替换为md超链接。
    正文："""
        try:
            # 在AI处理前先进行内容预处理
            processed_text = TextPreprocessor.preprocess_content(text)
            processed_text = self._preprocess_text(processed_text)
        
            response = self.model.generate_content(
                prompt + processed_text,
                generation_config={"temperature": 0.3}
            )
        
            # 对AI返回的结果也进行同样的处理
            if response.text:
                return TextPreprocessor.preprocess_content(response.text)
            return None
        except Exception as e:
            logging.error(f"AI处理失败: {e}")
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
    # 初始化
    from md2tgmd import escape
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
                    subject = EmailDecoder.decode_email_header(msg.get("Subject"))
                    raw_content = EmailHandler.get_email_content(msg)

                    # 2. 生成未转义的头部和正文
                    header = (
                        f"✉️ ​**{parseaddr(sender)[0]}**"
                        f"`{parseaddr(sender)[1]}`\n"
                        f"_{subject}_\n\n"
                    )
                    
                    # 3. 内容预处理
                    body = TextPreprocessor.preprocess_content(raw_content)

                    # 4. AI处理（不转义内部Markdown）
                    if gemini_ai:
                        body = gemini_ai.generate_summary(body) or body
                        body = TextPreprocessor.preprocess_content(body)

                    # 5. 关键步骤：用md2tgmd统一转义
                    safe_message = escape(f"{header}{body}")

                    # 6. 分割发送
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