import imaplib
import email
from email.header import decode_header
import html2text
import telegram
import os
import asyncio
import re
import chardet
from dotenv import load_dotenv
from email.utils import parseaddr
from md2tgmd import escape
import logging
from tencentcloud.common import credential
from tencentcloud.common.profile.client_profile import ClientProfile
from tencentcloud.common.profile.http_profile import HttpProfile
from tencentcloud.tmt.v20180321 import tmt_client, models

load_dotenv()

# 配置信息
IMAP_SERVER = os.getenv("IMAP_SERVER")
EMAIL_ADDRESS = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_API_KEY")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
MAX_MESSAGE_LENGTH = 3800  # 保留安全余量
DEBUG_MODE = os.getenv("DEBUG_MODE", "false").lower() == "true"

# 腾讯翻译配置
TENCENTCLOUD_SECRET_ID = os.getenv("TENCENTCLOUD_SECRET_ID")
TENCENTCLOUD_SECRET_KEY = os.getenv("TENCENTCLOUD_SECRET_KEY")
TENCENT_REGION = os.getenv("TENCENT_REGION", "na-siliconvalley")
ENABLE_TRANSLATION = os.getenv("ENABLE_TRANSLATION", "true").lower() == "true"

# 设置日志
logging.basicConfig(level=logging.INFO if DEBUG_MODE else logging.WARNING)
logger = logging.getLogger(__name__)

def remove_html_tags(text):
    """移除HTML标签"""
    clean = re.compile('<.*?>')
    return re.sub(clean, '', text)

def translate_content_sync(text):
    """同步翻译文本为中文"""
    if not text or not ENABLE_TRANSLATION:
        return text
    
    if not TENCENTCLOUD_SECRET_ID or not TENCENTCLOUD_SECRET_KEY:
        logger.warning("缺少腾讯云翻译密钥，跳过翻译")
        return text
    
    try:
        cred = credential.Credential(TENCENTCLOUD_SECRET_ID, TENCENTCLOUD_SECRET_KEY)
        http_profile = HttpProfile(endpoint="tmt.tencentcloudapi.com")
        client_profile = ClientProfile(httpProfile=http_profile)
        client = tmt_client.TmtClient(cred, TENCENT_REGION, client_profile)
        
        # 确保文本长度在API限制内
        MAX_TRANSLATE_LENGTH = 2000
        if len(text) > MAX_TRANSLATE_LENGTH:
            text = text[:MAX_TRANSLATE_LENGTH] + " [...]"
        
        req = models.TextTranslateRequest()
        req.SourceText = remove_html_tags(text)
        req.Source = "auto"
        req.Target = "zh"
        req.ProjectId = 0
        
        resp = client.TextTranslate(req)
        return resp.TargetText
    except Exception as e:
        logger.error(f"翻译失败: {e}")
        return text

async def translate_content_async(text):
    """异步翻译文本为中文"""
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, translate_content_sync, text)
    except Exception as e:
        logger.error(f"异步翻译失败: {e}")
        return text

def is_mainly_chinese(text):
    """检测文本是否主要是中文"""
    if not text:
        return True
    
    # 计算中文字符的比例
    chinese_pattern = re.compile(r'[\u4e00-\u9fff]')
    chinese_chars = len(chinese_pattern.findall(text))
    total_chars = len(text)
    
    # 避免除零错误
    if total_chars == 0:
        return True
    
    # 如果中文字符超过10%的比例，则无需翻译
    return (chinese_chars / total_chars) > 0.1

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
            logger.error(f"Header decode error: {e}")
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
            logger.error(f"Encoding detection error: {e}")
            return 'gb18030'

class ContentProcessor:
    @staticmethod
    def normalize_newlines(text):
        """统一换行符并合并空行"""
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        return re.sub(r'\n{3,}', '\n\n', text)
    
    # 转义后清理连续空行，最多保留一个空行
    @staticmethod
    def collapse_empty_lines(text):
        """清理连续空行，最多保留一个空行"""
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r'^\n+', '', text)
        text = re.sub(r'\n+$', '', text)
        return text
    
    @staticmethod
    def clean_text(text):
        """终极文本清洗"""
        text = text.replace('|', '')
        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', text)
        text = ContentProcessor.normalize_newlines(text)
        text = '\n'.join(line.strip() for line in text.split('\n'))
        text = re.sub(r'<[^>]+>', '', text)
        return text.strip()

    @staticmethod
    def extract_urls(html):
        """
        智能链接过滤，排除图片、视频、CSS、字体、API等资源链接，只返回主要内容相关页面链接。
        最多返回3个有效链接。
        """
        url_pattern = re.compile(r'(https?://[^\s>"\'{}|\\^`]+)', re.IGNORECASE)
        urls = []
        seen = set()
        exclude_domains = {
            'w3.org', 'schema.org', 'example.com', 'mozilla.org',
            'fonts.googleapis.com', 'googleapis.com'
        }
        # 图片和视频扩展名
        media_extensions = {
            '.jpeg', '.jpg', '.png', '.gif', '.bmp', '.webp', '.svg', '.tiff', '.raw',
            '.mp4', '.mov', '.avi', '.mkv', '.flv', '.webm', '.wmv', '.mpeg', '.mpg', '.3gp', '.m4v', '.ts'
        }
        # 图片关键字
        media_keywords = {
            '/thumb/', '/image/', '/img/', '/cover/', '/poster/', '/gallery/',
            'picture', 'photo', 'snapshot', 'preview', 'thumbnail'
        }
        # 资源文件关键字
        resource_keywords = [
            '/css', '/js', '/font', '/api', '/assets', 'static.', 'cdn.',
            '.css', '.js', '.woff', '.ttf', '.svg'
        ]

        for match in url_pattern.finditer(html):
            raw_url = match.group(1)
            # 清理可能残留的特殊字符
            clean_url = re.sub(r'[{}|\\)(<>`]', '', raw_url.split('"')[0])
            # 基本长度过滤
            if not (10 < len(clean_url) <= 100):
                continue
            # 排除特定域名
            if any(domain in clean_url for domain in exclude_domains):
                continue
            # 排除内联图片
            if clean_url.startswith('data:image/'):
                continue
            # 排除图片和视频扩展名
            if any(ext in clean_url.lower() for ext in media_extensions):
                continue
            # 排除图片/视频关键字
            lower_url = clean_url.lower()
            if any(kw in lower_url for kw in media_keywords):
                continue
            # 排除资源文件
            if any(kw in lower_url for kw in resource_keywords):
                continue
            # 排除CDN和静态资源
            if '/cdn/' in lower_url or '/static/' in lower_url or '/assets/' in lower_url:
                continue
            # 确保URL有路径部分（至少3个斜杠，排除纯域名）
            if clean_url.count('/') < 3:
                continue
            # 检查是否重复
            if clean_url not in seen:
                seen.add(clean_url)
                urls.append(clean_url)
        return urls[:3]  # 最多返回3个链接

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
            
            final_text = text
            if urls:
                final_text += "\n\n相关链接：\n" + "\n".join(urls)
                
            return ContentProcessor.normalize_newlines(final_text)
            
        except Exception as e:
            logger.error(f"HTML处理失败: {e}")
            return "⚠️ 内容解析异常"

class EmailHandler:
    @staticmethod
    async def get_email_content(msg):
        """统一内容获取，添加翻译功能"""
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
                    f"{k}: {v}" for k,v in msg.items() if k.lower() in ['subject', 'from', 'date']
                )
            
            # 检测是否需要翻译
            if content and not is_mainly_chinese(content) and ENABLE_TRANSLATION:
                if DEBUG_MODE:
                    logger.info("检测到非中文内容，开始翻译...")
                translated = await translate_content_async(content)
                if translated and translated != content:
                    content = "以下内容已翻译:\n\n" + translated
                    if DEBUG_MODE:
                        logger.info("翻译完成")
            
            return ContentProcessor.normalize_newlines(content or "⚠️ 无法解析内容")
            
        except Exception as e:
            logger.error(f"内容提取失败: {e}")
            return "⚠️ 内容提取异常"

def clean_bill_data(input_data):
    cleaned_lines = []
    for line in input_data.split('\n'):
        if not line.strip():
            cleaned_lines.append(line)
            continue
            
        parts = [p.strip() for p in line.split('   ') if p.strip()]
        
        # 移除第二个日期（索引为1的部分）
        if len(parts) > 1:
            parts.pop(1)
        
        # 检查并移除重复的货币金额
        # 查找货币代码出现的位置（CNY, USD等）
        currency_indices = [i for i, part in enumerate(parts) 
                           if part in ['CNY', 'USD', 'EUR', 'JPY']]  # 可以添加更多货币代码
        
        if len(currency_indices) > 1:
            # 保留第一个货币和金额，移除后续重复
            first_currency_index = currency_indices[0]
            currency = parts[first_currency_index]
            # amount_after_first = parts[first_currency_index + 1]  # 可选，暂未用到
            
            # 移除后续所有相同货币和金额
            i = first_currency_index + 2
            while i < len(parts):
                if parts[i] == currency:
                    parts.pop(i)  # 移除货币
                    if i < len(parts):
                        parts.pop(i)  # 移除金额
                else:
                    i += 1
        
        cleaned_line = '   '.join(parts)
        cleaned_lines.append(cleaned_line)
    
    return '\n'.join(cleaned_lines)

class MessageFormatter:
    @staticmethod
    def format_message(sender, subject, content):
        """返回分离的header和body"""
        realname, email_address = parseaddr(sender)
        
        clean_realname = re.sub(r'[|]', '', realname).strip()
        clean_email = email_address.strip()
        clean_subject = re.sub(r'\s+', ' ', subject).replace('|', '')
        
        # 构建MarkdownV2格式的header部分
        sender_line = "✉️ "
        if clean_realname:
            sender_line += f"**{clean_realname}**"  # 用户名加粗
        if clean_email:
            if clean_realname:
                sender_line += " "  # 在用户名和邮箱之间加空格
            sender_line += f"`{clean_email}`"  # 邮箱等宽
            
        # 主题单独一行
        subject_line = f"_{clean_subject}_" if clean_subject else ""
        
        # 组合header部分
        if sender_line and subject_line:
            header = f"{sender_line}\n{subject_line}\n\n"
        elif sender_line:
            header = f"{sender_line}\n\n"
        elif subject_line:
            header = f"{subject_line}\n\n"
        else:
            header = ""
            
        formatted_content = ContentProcessor.normalize_newlines(content)
        
        return header, formatted_content

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

        # 最终长度校验
        final_chunks = []
        for chunk in chunks:
            while len(chunk) > max_length:
                final_chunks.append(chunk[:max_length])
                chunk = chunk[max_length:]
            if chunk:
                final_chunks.append(chunk)
        
        return final_chunks

class TelegramBot:
    def __init__(self):
        self.bot = telegram.Bot(TELEGRAM_TOKEN)
        
    async def send_message(self, text):
        """使用MarkdownV2格式发送，确保只转义一次"""
        try:
            final_text = ContentProcessor.normalize_newlines(text)
            final_text = re.sub(r'^\s*[-]{2,}\s*$', '', final_text, flags=re.MULTILINE)

            # 应用Markdown转义（只在这里转义一次）
            escaped_text = escape(final_text)
            
            # 转义后清理多余的#号，防止标题过度转义
            cleaned_hashtags = re.sub(r'^(\\)?#+', '', escaped_text, flags=re.MULTILINE)
               
            cleaned_text = ContentProcessor.collapse_empty_lines(cleaned_hashtags)
        # 发送消息
            await self.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=cleaned_text,
                parse_mode="MarkdownV2",
                disable_web_page_preview=True
            )
        except telegram.error.BadRequest as e:
            logger.error(f"消息过长错误: {str(e)[:200]}")
        except Exception as e:
            logger.error(f"发送失败: {str(e)[:200]}")

async def main():
    bot = TelegramBot()
    
    try:
        with imaplib.IMAP4_SSL(IMAP_SERVER) as mail:
            mail.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            mail.select("INBOX")
            
            _, nums = mail.search(None, "UNSEEN")
            if not nums[0]:
                logger.info("无未读邮件")
                return

            for num in nums[0].split():
                try:
                    _, data = mail.fetch(num, "(RFC822)")
                    msg = email.message_from_bytes(data[0][1])
                    
                    sender = EmailDecoder.decode_email_header(msg.get("From"))
                    subject = EmailDecoder.decode_email_header(msg.get("Subject"))
                    content = await EmailHandler.get_email_content(msg)

                    header, body = MessageFormatter.format_message(sender, subject, content)
                    header_len = len(header)
                    max_body_len = MAX_MESSAGE_LENGTH - header_len

                    # ------- 这里集成账单清洗逻辑 ---------
                    if "建设银行信用卡" in subject:
                        body = clean_bill_data(body)
                    # --------------------------------------

                    # 处理header过长的情况
                    if max_body_len <= 0:
                        header = header[:MAX_MESSAGE_LENGTH-4] + "..."
                        header_len = len(header)
                        max_body_len = MAX_MESSAGE_LENGTH - header_len

                    # 第一步：分割带header的首个消息
                    first_part_chunks = MessageFormatter.split_content(body, max_body_len)
                    
                    # 发送首个消息（如果有内容）
                    if first_part_chunks:
                        first_chunk = first_part_chunks[0]
                        await bot.send_message(header + first_chunk)
                        
                        # 第二步：处理剩余内容（不带header）
                        remaining_body = '\n\n'.join(
                            para 
                            for chunk in first_part_chunks[1:] 
                            for para in chunk.split('\n\n')
                        )
                    else:
                        remaining_body = body

                    # 第三步：分割剩余内容（使用完整长度限制）
                    subsequent_chunks = MessageFormatter.split_content(remaining_body, MAX_MESSAGE_LENGTH)
                    
                    # 发送后续消息
                    for chunk in subsequent_chunks:
                        await bot.send_message(chunk)
                        
                    mail.store(num, "+FLAGS", "\\Seen")
                    
                except Exception as e:
                    logger.error(f"处理异常: {str(e)[:200]}")
                    continue

    except Exception as e:
        logger.error(f"连接异常: {str(e)[:200]}")

if __name__ == "__main__":
    asyncio.run(main())