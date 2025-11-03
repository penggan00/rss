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
import fitz

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
    """同步翻译文本为中文，支持长文本分段翻译"""
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
        
        cleaned_text = remove_html_tags(text)
        
        # 检查长度，如果超过限制则分段翻译
        MAX_BYTES = 1900  # 保留一些余量
        text_bytes = cleaned_text.encode('utf-8')
        
        if len(text_bytes) <= MAX_BYTES:
            # 短文本直接翻译
            req = models.TextTranslateRequest()
            req.SourceText = cleaned_text
            req.Source = "auto"
            req.Target = "zh"
            req.ProjectId = 0
            resp = client.TextTranslate(req)
            return resp.TargetText
        else:
            # 长文本分段翻译
            logger.info("检测到长文本，开始分段翻译...")
            segments = []
            current_segment = ""
            
            # 按段落分割
            paragraphs = [p for p in cleaned_text.split('\n\n') if p.strip()]
            
            for para in paragraphs:
                para_bytes = para.encode('utf-8')
                new_segment = current_segment + ("\n\n" + para if current_segment else para)
                new_segment_bytes = new_segment.encode('utf-8')
                
                if len(new_segment_bytes) > MAX_BYTES:
                    # 当前段落会超出限制，先翻译已积累的内容
                    if current_segment:
                        req = models.TextTranslateRequest()
                        req.SourceText = current_segment
                        req.Source = "auto"
                        req.Target = "zh"
                        req.ProjectId = 0
                        resp = client.TextTranslate(req)
                        segments.append(resp.TargetText)
                    
                    # 如果单个段落就超过限制，单独处理
                    if len(para_bytes) > MAX_BYTES:
                        # 按句子分割大段落
                        sentences = re.split(r'[。.!?？]\s*', para)
                        temp_segment = ""
                        for sentence in sentences:
                            if not sentence.strip():
                                continue
                            sentence_with_punct = sentence + "。"
                            temp_bytes = (temp_segment + sentence_with_punct).encode('utf-8')
                            
                            if len(temp_bytes) > MAX_BYTES and temp_segment:
                                req = models.TextTranslateRequest()
                                req.SourceText = temp_segment
                                req.Source = "auto"
                                req.Target = "zh"
                                req.ProjectId = 0
                                resp = client.TextTranslate(req)
                                segments.append(resp.TargetText)
                                temp_segment = sentence_with_punct
                            else:
                                temp_segment += sentence_with_punct
                        
                        if temp_segment:
                            current_segment = temp_segment
                        else:
                            current_segment = ""
                    else:
                        current_segment = para
                else:
                    current_segment = new_segment
            
            # 翻译最后一段
            if current_segment:
                req = models.TextTranslateRequest()
                req.SourceText = current_segment
                req.Source = "auto"
                req.Target = "zh"
                req.ProjectId = 0
                resp = client.TextTranslate(req)
                segments.append(resp.TargetText)
            
            return "\n\n".join(segments)
            
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
    def repair_broken_urls(html):
        """修复被HTML标签污染的URL"""
        try:
            # 修复 URL 后跟 <br> 标签的情况
            html = re.sub(r'(https?://[^\s<]+)<br[^>]*>\s*', r'\1 ', html, flags=re.IGNORECASE)
            # 修复 URL 后跟其他标签的情况  
            html = re.sub(r'(https?://[^\s<]+)</a>\s*', r'\1 ', html, flags=re.IGNORECASE)
            # 修复 URL 被随机字符污染的情况
            html = re.sub(r'(https?://[^\s<]+)[{}|\\)(<>`]', r'\1', html)
            # 新增：移除URL中的空格（表明URL被截断）
            html = re.sub(r'(https?://[^\s]*)\s+', r'\1', html)
            return html
        except Exception as e:
            logger.error(f"URL修复失败: {e}")
            return html

    @staticmethod
    def extract_urls(html):
        """改进的URL提取"""
        # 先修复被污染的URL
        cleaned_html = ContentProcessor.repair_broken_urls(html)
        cleaned_html = re.sub(r'<br\s*/?>', ' ', cleaned_html, flags=re.IGNORECASE)
        cleaned_html = re.sub(r'</?[^>]+>', ' ', cleaned_html)
        
        # 改进的URL正则，更好地处理参数
        url_pattern = re.compile(
            r'https?://'  # http:// or https://
            r'(?:[-\w.]|(?:%[\da-fA-F]{2}))+'  # domain
            r'(?::\d+)?'  # port
            r'(?:/[-\w$.+!*(),;:@&=%~#?]*)'  # path
            r'?',  # make path optional
            re.IGNORECASE
        )
        
        urls = []
        seen = set()
        
        # 不需要的域名和路径模式
        blacklist_patterns = [
            'use.typekit.net',
            'www.w3.org',
            'fonts.googleapis.com',
            'fonts.gstatic.com',
            'schema.org',
            'example.com',
            'localhost',
            'tracking.',
            'analytics.',
            'pixel.',
            'beacon.',
            '/wp-json/',
            '/static/',
            '/assets/',
            '.png',
            '.jpg',
            '.gif',
            '.css',
            '.js',
            '.woff',
            '.woff2',
            '.ttf',
            '.eot'
        ]
        
        for match in url_pattern.finditer(cleaned_html):
            raw_url = match.group(0)
            
            # 清理URL：移除尾部的空格和特殊字符
            clean_url = raw_url.split()[0].rstrip()  # 推荐的清理方式
            
            # 检查URL有效性
            from urllib.parse import urlparse
            try:
                parsed_url = urlparse(clean_url)
                domain = parsed_url.netloc.lower()
                path = parsed_url.path.lower()
                
                # 检查域名或路径是否匹配黑名单模式
                if any(pattern in domain or pattern in path for pattern in blacklist_patterns):
                    if DEBUG_MODE:
                        logger.info(f"过滤黑名单域名: {domain}")
                    continue
                
                # 检查URL是否包含空格（不完整的URL）
                if ' ' in clean_url:
                    if DEBUG_MODE:
                        logger.info(f"过滤包含空格的URL: {clean_url}")
                    continue
                    
                # 检查URL是否完整
                if not parsed_url.netloc or not '.' in parsed_url.netloc:
                    if DEBUG_MODE:
                        logger.info(f"过滤不完整域名: {clean_url}")
                    continue
                    
            except Exception as e:
                if DEBUG_MODE:
                    logger.warning(f"URL解析失败 {clean_url}: {e}")
                continue
            
            # 长度和内容检查
            char_length = len(clean_url)
            byte_length = len(clean_url.encode('utf-8'))
            
            # 更严格的过滤条件
            if (10 <= byte_length <= 200 and
            #    clean_url.count('/') >= 2 and
           #     len(clean_url.split('/')) > 2 and
          #      '.' in clean_url.split('/')[2] and
                not any(font_ext in clean_url.lower() for font_ext in ['.woff', '.woff2', '.ttf', '.eot']) and
                ' ' not in clean_url and  # 确保没有空格
                clean_url not in seen):
            
                seen.add(clean_url)
                urls.append(clean_url)
        
        return urls[:3]

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
        
def format_boc_bill_data(pdf_text):
    """格式化中国银行信用卡账单 - 修复版"""
    try:
        lines = pdf_text.split('\n')
        formatted_lines = []
        
        # 账单关键信息
        bill_info = {
            '到期还款日': '',
            '账单日': '',
            '本期人民币欠款总计': '',
            '本期最小还款额': '',
            '卡号': ''
        }
        
        transactions = []
        current_section = None
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # 检测章节变化
            if '账单信息总览' in line or 'Account Summary' in line:
                current_section = 'summary'
                continue
            elif '人民币交易明细' in line or 'RMB Transaction Detailed List' in line:
                current_section = 'transactions'
                continue
            elif '卡号' in line and '本期应还款额' in line:
                current_section = 'card_details'
                continue
            
            # 提取账单摘要信息
            if current_section == 'summary':
                if '到期还款日' in line:
                    # 查找日期格式
                    date_match = re.search(r'(\d{4}-\d{2}-\d{2})', line)
                    if date_match:
                        bill_info['到期还款日'] = date_match.group(1)
                elif '账单日' in line:
                    date_match = re.search(r'(\d{4}-\d{2}-\d{2})', line)
                    if date_match:
                        bill_info['账单日'] = date_match.group(1)
                elif '本期人民币欠款总计' in line:
                    # 查找金额
                    amount_match = re.search(r'(\d+\.\d{2})', line)
                    if amount_match:
                        bill_info['本期人民币欠款总计'] = amount_match.group(1)
                elif '本期最小还款' in line:
                    amount_match = re.search(r'(\d+\.\d{2})', line)
                    if amount_match:
                        bill_info['本期最小还款额'] = amount_match.group(1)
            
            # 提取卡号信息
            elif current_section == 'card_details':
                if re.match(r'\d{4}\s\d{4}\s\s\d{4}', line):
                    bill_info['卡号'] = line.strip()
                elif '本期应还款额' in line:
                    amount_match = re.search(r'(\d+\.\d{2})', line)
                    if amount_match and not bill_info['本期人民币欠款总计']:
                        bill_info['本期人民币欠款总计'] = amount_match.group(1)
            
            # 提取交易记录
            elif current_section == 'transactions':
                # 检测交易记录行（包含日期）
                if re.match(r'\d{4}-\d{2}-\d{2}', line):
                    transaction = {
                        'date': line.strip(),
                        'card_last_four': '',
                        'description': '',
                        'amount': ''
                    }
                    
                    # 这是一个交易开始，我们需要收集后续的相关行
                    transactions.append(transaction)
                elif transactions:
                    # 为最后一个交易添加信息
                    last_transaction = transactions[-1]
                    
                    # 检测卡号后四位
                    if re.match(r'^\d{4}$', line):
                        last_transaction['card_last_four'] = line
                    # 检测金额（数字格式）
                    elif re.match(r'^-?\d+\.?\d*$', line) and not last_transaction['amount']:
                        last_transaction['amount'] = line
                    # 检测交易描述（包含中文或英文，且不是纯数字）
                    elif (not re.match(r'^-?\d+\.?\d*$', line) and 
                          not re.match(r'^\d{4}$', line) and 
                          len(line) > 2 and 
                          not last_transaction['description']):
                        # 清理描述文本
                        clean_desc = line.replace('CHN', '').strip()
                        clean_desc = re.sub(r'[^\w\s\u4e00-\u9fff\-·]', '', clean_desc)  # 保留中文、英文、连字符等
                        last_transaction['description'] = clean_desc
        
        # 构建格式化输出
        final_output = []
        
        # 账单摘要
        final_output.append("🏦 中国银行信用卡账单摘要")
        if bill_info['到期还款日']:
            final_output.append(f"⏰ 到期还款日: {bill_info['到期还款日']}")
        if bill_info['账单日']:
            final_output.append(f"📅 账单日: {bill_info['账单日']}")
        if bill_info['本期人民币欠款总计']:
            final_output.append(f"💰 本期欠款总额: {bill_info['本期人民币欠款总计']}元")
        if bill_info['本期最小还款额']:
            final_output.append(f"💳 最低还款额: {bill_info['本期最小还款额']}元")
        if bill_info['卡号']:
            final_output.append(f"🔢 卡号: {bill_info['卡号']}")
        
        # 交易明细（去重和清理）
        if transactions:
            final_output.append("\n--- 交易明细 ---")
            
            # 清理交易记录：移除不完整的记录，合并相同日期的连续交易
            cleaned_transactions = []
            for trans in transactions:
                if trans['description'] and trans['amount']:
                    # 如果商家名称太短，尝试从原始文本中获取更完整的描述
                    if len(trans['description']) <= 2:
                        continue
                    cleaned_transactions.append(trans)
            
            # 输出交易记录
            for trans in cleaned_transactions:
                emoji = "🍔" if "汉堡" in trans['description'] else "🛒"
                final_output.append(f"📅 {trans['date']} | 💳 {trans['card_last_four']} | {emoji} {trans['description']} | 💰 {trans['amount']}元")
        
        # 如果没有提取到交易记录，添加提示
        if not transactions or len(cleaned_transactions) == 0:
            final_output.append("\n⚠️ 交易明细提取不完整，建议查看原PDF")
        
        return '\n'.join(final_output)
        
    except Exception as e:
        logger.error(f"中国银行账单格式化失败: {e}")
        return f"账单格式化异常: {str(e)}"
    
class EmailHandler:
    @staticmethod
    async def get_email_content(msg):
        """统一内容获取，添加翻译功能"""
        try:
            subject = EmailDecoder.decode_email_header(msg.get("Subject", ""))
            
            # 检查是否是中国银行邮件且有PDF附件
            if "中国银行" in subject:
                pdf_content = await EmailHandler.extract_pdf_attachment(msg)
                if pdf_content:
                    logger.info("检测到中国银行PDF附件，优先处理PDF内容")
                    return pdf_content
            
            # 原有逻辑（非中国银行邮件或没有PDF附件）
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
                original_length = len(content)
                translated = await translate_content_async(content)
                if translated and translated != content:
                    if not content.startswith("以下内容已翻译:"):
                        content = "以下内容已翻译:\n\n" + translated
                    if DEBUG_MODE:
                        logger.info(f"翻译完成，原始长度: {original_length}, 翻译后长度: {len(translated)}")
            
            return ContentProcessor.normalize_newlines(content or "⚠️ 无法解析内容")
            
        except Exception as e:
            logger.error(f"内容提取失败: {e}")
            return "⚠️ 内容提取异常"

    @staticmethod
    async def extract_pdf_attachment(msg):
        """提取PDF附件内容 - 修复版"""
        try:
            pdf_attachments = []
            
            for part in msg.walk():
                content_type = part.get_content_type()
                filename = part.get_filename()
                content_disposition = str(part.get("Content-Disposition", ""))
                
                # 解码文件名（如果是编码的）
                if filename and '=?' in filename:
                    try:
                        filename = EmailDecoder.decode_email_header(filename)
                        logger.info(f"解码后文件名: {filename}")
                    except Exception as e:
                        logger.error(f"文件名解码失败: {e}")
                
                logger.info(f"检查部分: type={content_type}, filename={filename}, disposition={content_disposition}")
                
                # 检测PDF附件 - 包括 application/octet-stream 类型
                is_pdf = (content_type in ["application/pdf", "application/octet-stream"] or 
                         (filename and filename.lower().endswith('.pdf')) or
                         (filename and '账单' in filename) or
                         (filename and 'bill' in filename.lower()))
                
                if is_pdf:
                    pdf_data = part.get_payload(decode=True)
                    if pdf_data and len(pdf_data) > 100:
                        actual_filename = filename or "bill.pdf"
                        # 验证PDF文件头
                        if len(pdf_data) >= 4 and pdf_data[:4] == b'%PDF':
                            pdf_attachments.append((actual_filename, pdf_data))
                            logger.info(f"确认找到PDF文件: {actual_filename}, 大小: {len(pdf_data)} 字节")
                        else:
                            logger.info(f"找到疑似文件但文件头不匹配: {actual_filename}")
            
            # 如果有PDF附件，处理第一个
            if pdf_attachments:
                filename, pdf_data = pdf_attachments[0]
                logger.info(f"开始处理PDF附件: {filename}")
                
                # 异步处理PDF内容
                loop = asyncio.get_running_loop()
                pdf_text = await loop.run_in_executor(
                    None, 
                    EmailHandler.extract_pdf_text, 
                    pdf_data
                )
                
                if pdf_text:
                    logger.info(f"PDF内容提取成功，长度: {len(pdf_text)} 字符")
                    
                    # 如果是中国银行账单，进行格式化
                    if "中国银行" in filename or "BOC" in filename.upper():
                        logger.info("检测到中国银行账单，进行格式化处理")
                        formatted_text = format_boc_bill_data(pdf_text)
                        pdf_text = f"📄 中国银行信用卡账单:\n\n{formatted_text}"
                    
                    # 检测是否需要翻译
                    if not is_mainly_chinese(pdf_text) and ENABLE_TRANSLATION:
                        translated = await translate_content_async(pdf_text)
                        if translated and translated != pdf_text:
                            pdf_text = f"📄 PDF内容已翻译 ({filename}):\n\n{translated}"
                        else:
                            pdf_text = f"📄 PDF内容 ({filename}):\n\n{pdf_text}"
                    else:
                        pdf_text = f"📄 PDF内容 ({filename}):\n\n{pdf_text}"
                    
                    return ContentProcessor.normalize_newlines(pdf_text)
                else:
                    logger.warning(f"PDF附件无法提取文本: {filename}")
                    return f"📄 找到PDF附件但无法提取文本: {filename}"
            
            logger.info("未找到PDF附件")
            return None
            
        except Exception as e:
            logger.error(f"PDF附件处理失败: {e}")
            return None

    @staticmethod
    def extract_pdf_text(pdf_data):
        """同步提取PDF文本内容 - 改进版"""
        try:
            # 打开PDF文档
            pdf_document = fitz.open(stream=pdf_data, filetype="pdf")
            all_text = []
            
            # 提取每一页的文本，使用更好的提取选项
            for page_num in range(len(pdf_document)):
                page = pdf_document.load_page(page_num)
                
                # 使用不同的文本提取选项
                text_options = [
                    page.get_text(),  # 默认提取
                    page.get_text("text", sort=True),  # 排序文本
                    page.get_text("words")  # 按单词提取
                ]
                
                for text in text_options:
                    if isinstance(text, str) and text.strip():
                        all_text.append(text.strip())
                    elif isinstance(text, list):
                        # 处理单词列表
                        words_text = ' '.join([word[4] for word in text if len(word) > 4])
                        if words_text.strip():
                            all_text.append(words_text.strip())
            
            pdf_document.close()
            
            # 合并所有文本
            combined_text = '\n'.join(all_text)
            
            if combined_text.strip():
                # 清理文本但保留更多信息
                cleaned_text = ContentProcessor.clean_text(combined_text)
                # 移除过多的空行但保留段落结构
                cleaned_text = re.sub(r'\n{3,}', '\n\n', cleaned_text)
                return cleaned_text
            else:
                logger.warning("PDF文档没有可提取的文本内容")
                return None
                
        except Exception as e:
            logger.error(f"PDF文本提取失败: {e}")
            return None
        
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
    async def format_message(sender, subject, content):
        """返回分离的header和body，主题翻译成功后只保留翻译主题"""
        realname, email_address = parseaddr(sender)
        
        clean_realname = re.sub(r'[|]', '', realname).strip()
        clean_email = email_address.strip()
        clean_subject = re.sub(r'\s+', ' ', subject).replace('|', '')
        
        # 主题翻译（如果是非中文且启用了翻译）
        final_subject = clean_subject
        if clean_subject and not is_mainly_chinese(clean_subject) and ENABLE_TRANSLATION:
            if DEBUG_MODE:
                logger.info("检测到非中文主题，开始翻译...")
            translated_subject = await translate_content_async(clean_subject)
            if translated_subject and translated_subject != clean_subject:
                # 修改这里：翻译成功后只保留翻译主题，清除原主题
                final_subject = translated_subject
                if DEBUG_MODE:
                    logger.info(f"主题翻译完成: {clean_subject} -> {final_subject}")
        
        # 构建MarkdownV2格式的header部分
        sender_line = "✉️ "
        if clean_realname:
            sender_line += f"**{clean_realname}**"  # 用户名加粗
        if clean_email:
            if clean_realname:
                sender_line += " "  # 在用户名和邮箱之间加空格
            sender_line += f"`{clean_email}`"  # 邮箱等宽
            
        # 主题单独一行（使用可能翻译后的主题）
        subject_line = f"_{final_subject}_" if final_subject else ""
        
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
                    
                    # 记录邮件信息
                    logger.info(f"处理邮件 - 发件人: {sender}, 主题: {subject}")
                    
                    # 调试：分析邮件结构
                    if "中国银行" in subject and DEBUG_MODE:
                        logger.info("=== 邮件结构分析 ===")
                        for part in msg.walk():
                            content_type = part.get_content_type()
                            filename = part.get_filename()
                            content_disposition = str(part.get("Content-Disposition", ""))
                            logger.info(f"部分: {content_type}, 文件名: {filename}, 处置: {content_disposition}")
                        logger.info("=== 分析结束 ===")
                    
                    content = await EmailHandler.get_email_content(msg)

                    header, body = await MessageFormatter.format_message(sender, subject, content)
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