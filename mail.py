#source rss_venv/bin/activate
#pip install html2text requests pdfplumber beautifulsoup4 md2tgmd python-dotenv tencentcloud-sdk-python python-telegram-bot
import html2text
import re
import imaplib
import email
import pdfplumber
import tempfile
from email.header import decode_header
import logging
import sys
import os
from bs4 import BeautifulSoup
from md2tgmd import escape
from dotenv import load_dotenv
import asyncio
from tencentcloud.common import credential
from tencentcloud.common.profile.http_profile import HttpProfile
from tencentcloud.common.profile.client_profile import ClientProfile
from tencentcloud.tmt.v20180321 import tmt_client, models
from pathlib import Path
from telegram import Bot
from telegram.constants import ParseMode
# 加载环境变量
load_dotenv()

# 获取当前脚本所在的绝对目录
current_dir = Path(__file__).parent.absolute()
log_file_path = current_dir / "mail.log"

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file_path),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)
telegram_bot_logger = logging.getLogger('telegram.bot')
telegram_bot_logger.setLevel(logging.WARNING)
urllib3_logger = logging.getLogger('urllib3.connectionpool')
urllib3_logger.setLevel(logging.WARNING)
telegram_ext_logger = logging.getLogger('telegram.ext')
telegram_ext_logger.setLevel(logging.WARNING)
# logger.info(f"日志文件路径: {log_file_path}")

# 翻译配置
ENABLE_TRANSLATION = os.getenv('ENABLE_TRANSLATION', 'false').lower() == 'true'
TENCENTCLOUD_SECRET_ID = os.getenv('TENCENTCLOUD_SECRET_ID')
TENCENTCLOUD_SECRET_KEY = os.getenv('TENCENTCLOUD_SECRET_KEY')
TENCENT_REGION = os.getenv('TENCENT_REGION', 'ap-beijing')

class AdvancedHTMLPreprocessor:
    """使用BeautifulSoup的高级HTML预处理器"""
    
    def __init__(self):
        self.removed_elements_count = 0
        
    def preprocess_html(self, html_content):
        """
        完整的HTML预处理流程
        """
        if not html_content or not html_content.strip():
            return ""
            
        try:
            soup = BeautifulSoup(html_content, 'html5lib')
            
            # 记录初始状态
            initial_length = len(str(soup))
            
            # 执行预处理步骤
            self._remove_empty_links(soup)  # 新增：先移除空链接
            self._remove_unwanted_elements(soup)
            self._remove_empty_elements(soup)
            self._clean_attributes(soup)
            self._preserve_line_breaks(soup)
            self._optimize_structure(soup)
            
            # 获取处理后的HTML
            processed_html = str(soup)
            
            # 最终清理
            processed_html = self._final_cleanup(processed_html)
            
            # 记录处理效果
        #    final_length = len(processed_html)
         #   reduction = ((initial_length - final_length) / initial_length) * 100
        #    logging.info(f"HTML预处理: 长度从 {initial_length} 减少到 {final_length} ({reduction:.1f}% 减少)")
       #     logging.info(f"移除了 {self.removed_elements_count} 个无用元素")
            
            return processed_html
            
        except Exception as e:
            logging.error(f"HTML预处理失败: {e}")
            return html_content
    
    def _remove_empty_links(self, soup):
        """移除空的或只有空白字符的链接"""
        links = soup.find_all('a')
        
        for link in links:
            # 获取链接文本内容（不包括子元素）
            link_text = link.get_text(strip=True)
            
            # 检查是否为空链接或只有不可见字符
            is_empty_link = (
                not link_text or  # 完全空文本
                link_text.isspace() or  # 只有空白字符
                len(link_text.strip()) == 0 or  # 清理后为空
                link_text in ['.', '-', '·', '•']  # 无意义的单个字符
            )
            
            # 检查是否只有图片但无文本
            has_only_img = len(link.find_all()) == 1 and link.find('img') and not link_text
            
            # 检查href是否为空或无效
            href = link.get('href', '')
            is_invalid_href = (
                not href or
                href.startswith(('javascript:', 'mailto:')) or
                href == '#' or
                href.strip() == ''
            )
            
            if is_empty_link or has_only_img or is_invalid_href:
                # 移除这个空链接，但保留文本内容
                link.unwrap()  # 使用unwrap()而不是decompose()来保留文本
                self.removed_elements_count += 1
            #    logging.debug(f"移除了空链接: {href}")
    
    def _remove_unwanted_elements(self, soup):
        """移除不需要的HTML元素"""
        unwanted_selectors = [
            'script', 'style', 'noscript', 'meta', 'link', 'head',
            'iframe', 'object', 'embed', 'applet',
            'form', 'input', 'button', 'select', 'textarea',
            'nav', 'footer', 'header', 'aside',
        ]
        
        for selector in unwanted_selectors:
            elements = soup.find_all(selector)
            self.removed_elements_count += len(elements)
            for element in elements:
                element.decompose()

    def _remove_empty_elements(self, soup):
        """移除空的或只有空白字符的元素"""
        # 检查这些标签是否为空
        tags_to_check = ['p', 'div', 'span', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 
                        'td', 'th', 'li', 'ul', 'ol', 'section', 'article']
        
        for tag in tags_to_check:
            elements = soup.find_all(tag)
            for element in elements:
                # 检查是否为空或只有空白字符
                text_content = element.get_text(strip=True)
                has_visible_children = bool(element.find_all(True))
                
                if not text_content and not has_visible_children:
                    element.decompose()
                    self.removed_elements_count += 1
                elif text_content and len(text_content.strip()) < 2:  # 只有1-2个字符
                    # 检查父元素，如果父元素有其他内容则移除这个空元素
                    parent = element.parent
                    if parent and len(parent.get_text(strip=True)) > len(text_content):
                        element.decompose()
                        self.removed_elements_count += 1
    
    def _clean_attributes(self, soup):
        """清理HTML属性，保留必要的"""
        for tag in soup.find_all(True):  # True 匹配所有标签
            attrs_to_remove = []
            
            for attr in tag.attrs:
                # 移除样式相关属性
                if attr in ['style', 'class', 'id']:
                    attrs_to_remove.append(attr)
                # 移除事件处理器
                elif attr.startswith('on'):
                    attrs_to_remove.append(attr)
                # 移除数据属性（通常用于JavaScript）
                elif attr.startswith('data-'):
                    attrs_to_remove.append(attr)
                # 移除一些特定的属性
                elif attr in ['width', 'height', 'border', 'cellpadding', 'cellspacing']:
                    attrs_to_remove.append(attr)
            
            # 移除属性
            for attr in attrs_to_remove:
                del tag[attr]
            
            # 对于链接，确保href属性存在且有效
            if tag.name == 'a' and 'href' in tag.attrs:
                href = tag['href']
                # 清理JavaScript链接
                if href.startswith(('javascript:', 'mailto:')):
                    # 将链接转换为纯文本
                    tag.replace_with(tag.get_text())
    
    def _preserve_line_breaks(self, soup):
        """保护重要的换行结构"""
        # 保护段落标签的换行
        for tag in soup.find_all(['p', 'div', 'br']):
            if tag.name == 'br':
                # 确保br标签后面有换行
                if tag.next_sibling and not str(tag.next_sibling).startswith('\n'):
                    tag.insert_after(soup.new_string('\n'))
            elif tag.name in ['p', 'div']:
                # 确保块级元素前后有换行
                if tag.previous_sibling and not str(tag.previous_sibling).endswith('\n'):
                    tag.insert_before(soup.new_string('\n'))
                if tag.next_sibling and not str(tag.next_sibling).startswith('\n'):
                    tag.insert_after(soup.new_string('\n'))
    
    def _optimize_structure(self, soup):
        """优化HTML结构"""
        # 移除嵌套过深的div
        divs = soup.find_all('div')
        for div in divs:
            # 如果div只包含一个子元素且也是div，可以考虑简化
            children = div.find_all(recursive=False)
            if len(children) == 1 and children[0].name == 'div':
                children[0].unwrap()  # 移除外层div
    
    def _final_cleanup(self, html_content):
        """最终清理"""
        # 移除HTML注释
        html_content = re.sub(r'<!--.*?-->', '', html_content, flags=re.DOTALL)
        
        # 去除 --- 或者多个 --- --- 格式
        html_content = re.sub(r'---( ---)*', '', html_content)
        
        # 去除 -- 或者多个 -- -- 格式  
        html_content = re.sub(r'--( --)*', '', html_content)
        
        # 去除 -- 或者多个 -- -- 格式  
        html_content = re.sub(r'[·.]{3,}', '··', html_content)
        html_content = re.sub(r'^\s*\\+\s*$', '', html_content)
        
        # 删除连续的 ''''''（6个单引号）
        html_content = re.sub(r"'{6}", '', html_content)
        
        # 精准删除连续的 ' s ' 模式
        # 匹配模式：' s ' 重复出现，中间可能有换行或空格
        html_content = re.sub(r"('\s*s\s*')+", '', html_content)
        
        return html_content.strip()

class EmailToTelegramBot:
    def __init__(self):
        """
        初始化 - 从环境变量读取配置
        """
        # 从环境变量加载配置
        self.email_config = {
            'imap_server': os.getenv('IMAP_SERVER', 'imap.qq.com'),
            'imap_port': 993,
            'email': os.getenv('EMAIL_USER'),
            'password': os.getenv('EMAIL_PASSWORD'),
            'ssl': True
        }
        
        # 修改Telegram配置
        self.telegram_config = {
            'bot_token': os.getenv('TELEGRAM_API_KEY'),
            'chat_ids': self._parse_chat_ids(os.getenv('TELEGRAM_CHAT_ID', ''))
        }
        
        # 初始化Telegram Bot
        self.bot = Bot(token=self.telegram_config['bot_token'])
        
        # 验证必要配置
        self._validate_config()
        
        # 初始化HTML预处理器
        self.html_preprocessor = AdvancedHTMLPreprocessor()
        
        # 配置HTML到Markdown转换器
        self.h = html2text.HTML2Text()
        self.h.body_width = 0
        self.h.ignore_links = False
        self.h.ignore_images = True
        self.h.ignore_emphasis = False
        self.h.ignore_tables = False
        self.h.mark_code = True
            
    def _parse_chat_ids(self, chat_ids_str):
        """解析聊天ID，只支持单个ID"""
        if not chat_ids_str:
            logging.error("TELEGRAM_CHAT_ID 环境变量为空")
            return []
        
        # 只取第一个ID，忽略逗号分隔的其他ID
        chat_id = chat_ids_str.split(',')[0].strip()
        
        if not chat_id:
            logging.error("聊天ID格式错误")
            return []
        
        # 清理聊天ID
        chat_id = str(chat_id).replace('"', '').replace("'", "").strip()
        
     #   logging.info(f"使用的聊天ID: {chat_id}")
        return [chat_id]
    
    def _validate_config(self):
        """验证配置是否完整"""
        missing_vars = []
        
        if not self.email_config['email']:
            missing_vars.append('EMAIL_USER')
        if not self.email_config['password']:
            missing_vars.append('EMAIL_PASSWORD')
        if not self.telegram_config['bot_token']:
            missing_vars.append('TELEGRAM_API_KEY')
        if not self.telegram_config['chat_ids']:
            missing_vars.append('TELEGRAM_CHAT_ID')
            
        if missing_vars:
            logging.error(f"缺少必要的环境变量: {', '.join(missing_vars)}")
            logging.error("请检查 .env 文件配置")
            sys.exit(1)
        
    #   logging.info(f"配置验证成功，将发送到 {len(self.telegram_config['chat_ids'])} 个聊天: {self.telegram_config['chat_ids']}")
    
    def connect_email(self):
        """连接到邮箱服务器"""
        try:
            if self.email_config['ssl']:
                mail = imaplib.IMAP4_SSL(self.email_config['imap_server'], self.email_config['imap_port'])
            else:
                mail = imaplib.IMAP4(self.email_config['imap_server'], self.email_config['imap_port'])
            
            mail.login(self.email_config['email'], self.email_config['password'])
       #     logging.info("邮箱登录成功")
            return mail
        except Exception as e:
            logging.error(f"邮箱连接失败: {e}")
            return None
    
    def get_unread_emails(self, mail):
        """获取未读邮件"""
        try:
            # 选择收件箱
            mail.select("INBOX")
            
            # 搜索未读邮件
            status, messages = mail.search(None, 'UNSEEN')
            if status != 'OK':
         #       logging.info("没有找到未读邮件")
                return []
            
            email_ids = messages[0].split()
        #    logging.info(f"找到 {len(email_ids)} 封未读邮件")
            return email_ids
        except Exception as e:
            logging.error(f"获取未读邮件失败: {e}")
            return []
    
    def decode_mime_words(self, text):
        """解码邮件头"""
        if text is None:
            return ""
        decoded_parts = decode_header(text)
        decoded_text = ""
        for part, encoding in decoded_parts:
            if isinstance(part, bytes):
                if encoding:
                    decoded_text += part.decode(encoding)
                else:
                    decoded_text += part.decode('utf-8', errors='ignore')
            else:
                decoded_text += part
        return decoded_text
    
    def extract_email_content(self, msg):
        """提取邮件内容"""
        subject = self.decode_mime_words(msg.get("Subject", "无主题"))
        from_ = self.decode_mime_words(msg.get("From", "未知发件人"))
        date = msg.get("Date", "未知日期")
        
        # 提取邮件正文
        html_content = ""
        plain_content = ""
        
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition"))
                
                # 跳过附件
                if "attachment" in content_disposition:
                    continue
                    
                if content_type == "text/plain" and not plain_content:
                    try:
                        body = part.get_payload(decode=True)
                        charset = part.get_content_charset() or 'utf-8'
                        plain_content = body.decode(charset, errors='ignore')
                    except Exception as e:
                        logging.warning(f"解析纯文本内容失败: {e}")
                
                elif content_type == "text/html" and not html_content:
                    try:
                        body = part.get_payload(decode=True)
                        charset = part.get_content_charset() or 'utf-8'
                        html_content = body.decode(charset, errors='ignore')
                    except Exception as e:
                        logging.warning(f"解析HTML内容失败: {e}")
        else:
            # 单部分邮件
            content_type = msg.get_content_type()
            body = msg.get_payload(decode=True)
            charset = msg.get_content_charset() or 'utf-8'
            
            try:
                if content_type == "text/plain":
                    plain_content = body.decode(charset, errors='ignore')
                elif content_type == "text/html":
                    html_content = body.decode(charset, errors='ignore')
            except Exception as e:
                logging.warning(f"解析邮件内容失败: {e}")
        
        return {
            'subject': subject,
            'from': from_,
            'date': date,
            'html_content': html_content,
            'plain_content': plain_content
        }
    
    def convert_email_to_markdown(self, email_data):
        """将邮件内容转换为Markdown格式"""
        subject = email_data['subject']
        from_ = email_data['from']
        date = email_data['date']
        
        # 解析发件人信息
        from_name, from_email = self._parse_sender_info(from_)
        
        # 处理用户名中的点号
        if from_name:
            from_name = from_name.replace('.', '.\u200c')
        
        # 处理主题：清理下划线并替换点号
        if subject:
            subject = subject.replace('_', 'ˍ')  # 清理下划线
            subject = subject.replace('.', '.\u200c')  # 替换点号
            subject = subject.replace(r'\\', ' ') # 去除连续的反斜杠

        # 处理邮箱地址：去除反斜杠
        if from_email:
            from_email = from_email.replace('\\', ' ')  # 去除反斜杠
            
        # 优先使用HTML内容，如果没有则使用纯文本
        if email_data['html_content']:
            # 在转换前先预处理HTML（包括移除空链接）
            content = self.convert_html_to_markdown(email_data['html_content'])
        elif email_data['plain_content']:
            content = email_data['plain_content']
        else:
            content = "【此邮件无正文内容】"
        
        # 检测是否需要翻译
        need_translation = ENABLE_TRANSLATION and not self.is_mainly_chinese(content)
        
        if need_translation:
       #     logging.info("检测到非中文内容，开始安全翻译...")
            try:
                # 翻译主题 - 使用安全翻译
                translated_subject = self.translate_content_sync_safe(subject)
                if translated_subject and translated_subject != subject:
                    subject = translated_subject
               #     logging.info("主题安全翻译完成")
                
                # 翻译内容 - 使用安全翻译
                translated_content = self.translate_content_sync_safe(content)
                if translated_content and translated_content != content:
                    content = translated_content.replace('_', 'ˍ')
               #     logging.info("内容安全翻译完成")
                
            except Exception as e:
                logging.error(f"安全翻译失败: {e}")
                # 翻译失败时保留原文
        
        # 构建符合要求的Markdown消息格式
        markdown_message = ""
        
        # 用户名（粗体）
        if from_name:
            markdown_message += f"**{from_name}**"
        
        # 邮箱地址（等宽）
        if from_email:
            if from_name:
                markdown_message += " "  # 用户名和邮箱之间加空格
            markdown_message += f"`{from_email}`"
        
        markdown_message += "\n"
        
        # 主题（斜体）
        if subject:
            markdown_message += f"_{subject}_\n\n"

        # 内容
        markdown_message += content
        
        return markdown_message

    def convert_html_to_markdown(self, html_content):
        """将HTML转换为Markdown"""
        if not html_content:
            return ""
        
        # 1. 预处理HTML（这里已经包含了移除空链接）
        cleaned_html = self.html_preprocessor.preprocess_html(html_content)
        
        # 2. 转换为Markdown
        markdown = self.h.handle(cleaned_html)
        
        # 4. 后处理Markdown - 确保这里包含了空链接清理
        final_markdown = self.postprocess_markdown(markdown)
        
        # 5. 最终的空链接清理（确保万无一失）
        final_markdown = self.final_clean_empty_links(final_markdown)
        
        # 6. 处理星号：保留开头的*，保留**，删除单独的*
        final_markdown = self.process_asterisks(final_markdown)
        # 新增：安全替换特殊字符（保护URL、邮箱等格式）
       # markdown = self.replace_special_chars_safely(markdown)
        # 7. 新增：将点号替换为全角点号+Em空格（排除URL和等体字）
    #    final_markdown = self.replace_dots_safely(final_markdown)
        return final_markdown
    
    def replace_dots_safely(self, text):
        """
        安全替换点号和@符号，保护特定格式
        """
        if not text:
            return text
        
        def replace_unprotected_chars(match):
            content = match.group(0)
            
            # 扩展保护区域判断
            if (content.startswith(('http://', 'https://', 'ftp://')) or  # 各种URL
                content.startswith('[') and '](' in content and content.endswith(')') or  # Markdown链接
                content.startswith('`') and content.endswith('`') or  # 行内代码
                content.startswith('```') and content.endswith('```') or  # 代码块
                re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', content)):  # 邮箱地址
                return content
            
            # 只有当内容确实包含点号时才替换
            if '.' in content:
                content = content.replace('.', '.\u200c')

            return content
        
        # 更精确的模式：只匹配可能包含点号的文本片段
        pattern = r'https?://[^\s]+|ftp://[^\s]+|\[[^\]]+\]\([^)]+\)|`[^`]+`|```[^`]+```|[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}|[^\s]+'
        
        # 先进行特殊字符替换
        text = re.sub(pattern, replace_unprotected_chars, text)
        
        return text
    
    def process_asterisks(self, text):
        """
        处理星号：如果开头是*就保留，保留**连续的星号，删除单独的*
        
        Args:
            text: 输入的文本
            
        Returns:
            处理后的文本
        """
        if not text:
            return text
        
        lines = text.split('\n')
        processed_lines = []
        
        for line in lines:
            # 处理开头是*的情况（如列表项）
            if line.strip().startswith('*'):
                # 保留开头的*，但处理行内单独的*
                processed_line = self._process_line_asterisks(line)
            else:
                # 处理整行中的单独*
                processed_line = self._process_line_asterisks(line)
            
            processed_lines.append(processed_line)
        
        # 处理完成后，检查是否有行只有连续的星号（至少1个）
        final_lines = []
        for line in processed_lines:
            stripped_line = line.strip()
            # 如果一行之中只有连续的星号（至少1个），就删除星号但保留空行
            if stripped_line and all(c == '*' for c in stripped_line):
                final_lines.append('')  # 删除星号但保留空行
            else:
                final_lines.append(line)
        
        return '\n'.join(final_lines)

    def _process_line_asterisks(self, line):
        """
        处理单行中的星号
        
        Args:
            line: 单行文本
            
        Returns:
            处理后的单行文本
        """
        if not line or '*' not in line:
            return line
        
        # 用于构建结果
        result = []
        i = 0
        length = len(line)
        
        while i < length:
            if line[i] == '*':
                # 检查是否是连续的**
                if i + 1 < length and line[i + 1] == '*':
                    # 保留**
                    result.append('**')
                    i += 2
                else:
                    # 单独的*，检查是否需要保留
                    # 如果是行首的*（前面只有空格），则保留
                    if i == 0 or (i > 0 and all(c == ' ' for c in line[:i])):
                        result.append('*')
                        i += 1
                    else:
                        # 删除单独的*
                        i += 1
            else:
                result.append(line[i])
                i += 1
        
        return ''.join(result)

    def final_clean_empty_links(self, markdown):
        """最终的空链接清理"""
        if not markdown:
            return ""
        
        # 多次清理确保没有漏网之鱼
        patterns = [
            r'\[\s*\]\s*\([^)]*\)',  # []()
            r'\[\s+\]\s*\([^)]*\)',  # [   ]()
            r'\[([.\-\s]{1,2})\]\s*\([^)]*\)',  # [.]()、[-]()等
        ]
        
        for pattern in patterns:
            markdown = re.sub(pattern, '', markdown)
        
        return markdown
    
    def _parse_sender_info(self, sender_string):
        """解析发件人信息，返回(姓名, 邮箱)"""
        if not sender_string:
            return "", ""
        
        try:
            # 使用email.utils.parseaddr解析发件人信息
            from email.utils import parseaddr
            name, email_addr = parseaddr(sender_string)
            
            # 清理姓名中的特殊字符
            if name:
                name = re.sub(r'[<>]', '', name).strip()
            
            # 如果没有姓名，尝试从邮箱中提取用户名
            if not name and email_addr:
                name = email_addr.split('@')[0]
                
            return name, email_addr
            
        except Exception as e:
            logging.warning(f"解析发件人信息失败: {e}")
            # 如果解析失败，返回原始字符串作为姓名
            return sender_string, ""
    
    def clean_special_characters(self, text):
        """内容清理特殊字符：| 和 ---，确保保留换行符"""
        if not text:
            return ""
        # 替换 _ 为类似字符，避免与Markdown语法冲突
        text = text.replace(r'_', 'ˍ')
        text = text.replace('\\', ' ')
        text = re.sub(r'#+', '# ', text)
        # 清理 | 符号
        text = re.sub(r'(?<!\|)\|(?!\|)', ' ', text)

        # 按行处理
        lines = text.split('\n')
        processed_lines = []

        for line in lines:
            # 检查是否是分隔线
            is_separator = (
                re.match(r'^\s*-{3,}\s*$', line) or
                re.match(r'^\s*—{1,}\s*$', line) or
                re.match(r'^\s*(-\s*){2,}$', line) or
                re.match(r'^\s*(-\s*){3,}$', line)
            )
        
            if is_separator:
                processed_lines.append("")
            else:
                # 清理行首的横线
                cleaned_line = re.sub(r'^\s*(-\s*){2,}', '', line)
                cleaned_line = re.sub(r'^\s*—+\s*', '', cleaned_line)
                processed_lines.append(cleaned_line)

        # 重新组合
        result = '\n'.join(processed_lines)
        
        return result
    
    def normalize_whitespace(self, text):
        """标准化空白字符，确保连续空行最多2个"""
        if not text:
            return ""
        
        # 1. 首先标准化换行符
        text = re.sub(r'\r\n', '\n', text)
        text = re.sub(r'\r', '\n', text)
        
        # 2. 清理特殊字符
        text = self.clean_special_characters(text)
        
        # 3. 关键步骤：把所有3个及以上的连续换行（包括中间有空白字符的）替换为2个换行
        text = re.sub(r'(\n\s*){3,}', '\n\n', text)

        # 4. 按行处理，清理行首行尾空格
        lines = text.split('\n')
        processed_lines = []
        
        for line in lines:
            stripped_line = line.strip()
            processed_lines.append(stripped_line)
        
        # 5. 重新组合
        result = '\n'.join(processed_lines)
        
        return result
    
    def postprocess_markdown(self, markdown):
        """后处理Markdown内容 - 优化空行和特殊字符处理"""
        if not markdown:
            return ""

        # 清理特殊字符和标准化空白
        markdown = self.normalize_whitespace(markdown)
        # 新增：删除整行都是不可见字符的行
        markdown = self.remove_invisible_lines(markdown)

        # 新增：专门清理空文本的Markdown链接
        markdown = self.remove_empty_markdown_links(markdown)
        
        # 新增：移除超长URL
        markdown = self.remove_long_urls(markdown)
        
        # 新增：将邮箱地址转换为等宽字体
        markdown = self.format_email_addresses(markdown)
        
        # 新增：清理序号间的空行（保持独立功能）
        #  markdown = self.remove_blank_lines_between_sequences(markdown)

        # 新增：去除空的 [] 和 () 组合
        markdown = self.remove_empty_brackets(markdown)

        return markdown

    def remove_empty_brackets(self, text):
        """
        去除空的 []、()、{} 等各种括号组合
        """
        if not text:
            return text
        
        # 定义各种空括号模式
        empty_bracket_patterns = {
            'square': (r'\[\s*\]', '[]'),      # 方括号
            'round': (r'\(\s*\)', '()'),       # 圆括号
            'curly': (r'\{\s*\}', '{}'),       # 花括号
            'angle': (r'\<\s*\>', '<>'),       # 尖括号
            'single_quote': (r"'\s*'", "''"),  # 单引号
            'double_quote': (r'"\s*"', '""'),  # 双引号
        }
        
        result = text
        removal_stats = {}
        
        for bracket_type, (pattern, display_name) in empty_bracket_patterns.items():
            count_before = len(re.findall(pattern, result))
            if count_before > 0:
                result = re.sub(pattern, '', result)
                removal_stats[display_name] = count_before
        
        # 记录清理效果
        if removal_stats:
            stats_str = ', '.join([f'{name} {count}个' for name, count in removal_stats.items()])
        #    logging.info(f"清理空括号组合: 移除 {stats_str}")
        
        return result

    
    def format_email_addresses(self, text):
        """
        将邮箱地址用等宽字体标记，但不处理URL
        """
        if not text:
            return text
        
        # 邮箱地址正则表达式模式
        email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        
        def wrap_email_in_monospace(match):
            email = match.group(0)
            return f'`{email}`'
        
        # 使用正则表达式替换邮箱地址
        text = re.sub(email_pattern, wrap_email_in_monospace, text)
        
        # 删除URL包装为等体字的功能
        # 原来的URL处理代码已移除
        
        return text

    def fix_url_format(self, url):
        """
        修复URL格式（只有被标记为等宽字体的单独URL才会调用此函数）：
        1. 统一英文字符（中文冒号转英文）
        2. 补全缺失的//
        3. 删除空格
        4. 确保协议完整
        """
        if not url:
            return url
        
        original_url = url
        
        try:
            # 1. 删除所有空格
            url = re.sub(r'\s+', '', url)
            
            # 2. 统一英文字符：中文冒号转英文冒号
            url = url.replace('：', ':')
            
            # 3. 补全协议和//
            if url.startswith('http'):
                # 处理 http:example.com -> http://example.com
                if re.match(r'https?:[^/]', url):
                    url = url.replace(':', '://', 1)
                # 处理 http:/example.com -> http://example.com  
                elif re.match(r'https?:/[^/]', url):
                    url = url.replace(':/', '://', 1)
                # 处理 http//example.com -> http://example.com
                elif re.match(r'https?//', url):
                    url = url.replace('//', '://', 1)
            
            # 4. 特殊处理racknerd.com相关URL
            if 'racknerd' in url.lower():
                # 确保racknerd域名完整
                url = re.sub(r'racknerd\s*\.\s*com', 'racknerd.com', url, flags=re.IGNORECASE)
                # 处理aff参数
                url = re.sub(r'aff\s*=\s*(\d+)', r'aff=\1', url, flags=re.IGNORECASE)
                # 处理aff.php? aff格式
                url = re.sub(r'aff\.php\?\s*aff', 'aff.php?aff', url, flags=re.IGNORECASE)
            
       #     logging.debug(f"URL修复: {original_url} -> {url}")
            return url
            
        except Exception as e:
            logging.warning(f"URL修复失败 {original_url}: {e}")
            return original_url
    
    def remove_long_urls(self, text, max_url_length=300):  # 降低到300字符更安全
        """
        增强版URL清理 - 更彻底地移除长链接
        """
        if not text:
            return text
        
        def replace_long_markdown_link(match):
            link_text = match.group(1)
            url = match.group(2)
            
            url_length = len(url)
            if url_length > max_url_length:
            #    logging.info(f"🚫 移除超长Markdown链接: {url[:30]}... (长度: {url_length})")
                return link_text  # 只保留链接文本
            else:
                return match.group(0)  # 保留完整链接
        
        def replace_long_plain_url(match):
            url = match.group(0)
            url_length = len(url)
            if url_length > max_url_length:
      #          logging.info(f"🚫 移除超长纯文本URL: {url[:30]}... (长度: {url_length})")
                return ""  # 完全移除
            else:
                return url  # 保留
        
        # 增强的URL匹配模式
        url_patterns = [
            # Markdown链接 [文本](URL)
            (r'\[([^\]]+)\]\(([^)]+)\)', replace_long_markdown_link),
            
            # 纯文本URL（更全面的匹配）
            (r'https?://[^\s<>"{}|\\^`\[\]()]{10,}', replace_long_plain_url),
            
            # 没有协议的URL（如 www.example.com/path）
            (r'www\.[^\s<>"{}|\\^`\[\]()]{10,}', replace_long_plain_url),
        ]
        
        # 多次处理确保没有漏网之鱼
        for pattern, replacement_func in url_patterns:
            text = re.sub(pattern, replacement_func, text)
        
        return text
    
    def remove_invisible_lines(self, text):
        """
        删除整行中的不可见字符，但保留行结构
        
        Args:
            text: 输入的文本
            
        Returns:
            处理后的文本
        """
        if not text:
            return text
        
        lines = text.split('\n')
        processed_lines = []
        
        # 不可见字符的正则表达式模式
        invisible_chars = r'[\s\u034f\u00ad\u200b\u200c\u200d\u2060\u0000-\u001f\u007f-\u009f]'
        invisible_pattern = re.compile(f'^{invisible_chars}*$')
        
        for line in lines:
            # 检查整行是否只包含不可见字符或空白字符
            if invisible_pattern.match(line):
                # 如果整行都是不可见字符，替换为空行（保留行结构）
                processed_lines.append("")
            else:
                # 如果行中有可见内容，只删除不可见字符但保留可见内容
                # 删除行内的不可见字符，但保留空格结构
                cleaned_line = re.sub(r'[\u034f\u00ad\u200b\u200c\u200d\u2060\u0000-\u001f\u007f-\u009f]', '', line)
                processed_lines.append(cleaned_line)
        
        return '\n'.join(processed_lines)

    
    def remove_empty_markdown_links(self, markdown):
        """专门移除Markdown中的空文本链接"""
        if not markdown:
            return ""
        
        # 模式1: 完全空的链接文本 []()
        markdown = re.sub(r'\[\s*\]\s*\([^)]*\)', '', markdown)
        
        # 模式2: 只有空白字符的链接文本 [   ]()
        markdown = re.sub(r'\[\s+\]\s*\([^)]*\)', '', markdown)
        
        # 模式3: 链接文本很短且无意义（如单个点、空格等）
        # 匹配 [.]()、[-]() 等无意义短文本
        markdown = re.sub(r'\[([.\-\s]{1,2})\]\s*\([^)]*\)', '', markdown)
        
        # 模式4: 链接文本与URL相同但显示为空的情况
        # 这种情况需要更复杂的处理
        lines = markdown.split('\n')
        processed_lines = []
        
        for line in lines:
            # 查找所有链接模式
            links = re.findall(r'\[([^\]]*)\]\(([^)]*)\)', line)
            for link_text, link_url in links:
                # 如果链接文本为空或只有空白，移除整个链接
                if not link_text.strip():
                    line = line.replace(f'[{link_text}]({link_url})', '')
                # 如果链接文本很短且可能是无意义的
                elif len(link_text.strip()) <= 2 and link_text.strip() in ['.', '-', '·', '•']:
                    line = line.replace(f'[{link_text}]({link_url})', '')
            
            processed_lines.append(line)
        
        return '\n'.join(processed_lines)
   
    def normalize_essential_symbols(self, text):
        """只处理MarkdownV2必须处理的符号"""
        translation_map = str.maketrans({
            # 必须处理的（影响Markdown语法）
            '（': '(',  # 括号
            '）': ')',
            '【': '[',
            '】': ']',
            '＃': '#',  # 井号
            
            # 建议处理的
            '：': ':',  # 冒号
            '！': '!',  # 感叹号
        })
        
        text = text.translate(translation_map)
        
        # 额外的正则处理
        import re
        # 处理 ] 和 ( 之间的空格
        text = re.sub(r'\]\s*\(', '](', text)
        # 处理 [ 和 ] 之间的空格
        text = re.sub(r'\[\s*', '[', text)
        text = re.sub(r'\s*\]', ']', text)
        
        return text
    
    def escape_markdown_v2(self, text):
        """使用md2tgmd进行MarkdownV2格式转义，然后清理等体字中的反斜杠并修复等体字内的URL"""
        if not text:
            return ""
        
        print(f"🔤 原始文本: {text}")
        
        # 第一步：安全替换点号（在翻译后处理）
        text = self.replace_dots_safely(text)
   #     print(f"🔤 替换点号后: {text}")
        
        # 新增：在转义之前清理符号
        text = re.sub(r'#+', '# ', text)
        text = re.sub(r'\u200c+', '\u200c', text)
        
        text = self.normalize_essential_symbols(text)

        # 第二步：使用md2tgmd进行转义
        escaped_text = escape(text)
        print(f"🔄 转义后文本: {escaped_text}")
        
        # 第三步：在转义之后，等体字处理之前，检查前3行并替换 \_ 为 _
        def replace_underscore_escape_in_first_lines(text):
            r"""替换前4行中的 \_ 为 _"""
            lines = text.split('\n')
            if len(lines) <= 3:
                return text
                
            processed_lines = []
            for i, line in enumerate(lines):
                if i < 4:  # 只处理前4行
                    # 将 \_ 替换为 _
                    processed_line = line.replace('\\_', '_')
                    if processed_line != line:
                        print(f"📝 第{i+1}行替换 \\_ 为 _: '{line}' → '{processed_line}'")
                    processed_lines.append(processed_line)
                else:
                    processed_lines.append(line)
            return '\n'.join(processed_lines)
        
        # 执行前3行 \_ 替换
        escaped_text = replace_underscore_escape_in_first_lines(escaped_text)
        
        # 第四步：专门处理等体字：清理反斜杠 + 修复URL
        processed_text = self.clean_and_fix_monospace_urls(escaped_text)
        print(f"🔗 处理等体字后: {processed_text}")
        
        # 第五步：修复：保护主题相关的下划线（包括整个主题）
        final_text = self.protect_theme_underscores_complete(processed_text)
        print(f"🎨 保护主题下划线后: {final_text}")
        
        return final_text

    def protect_theme_underscores_complete(self, text):
        """
        完整保护主题下划线 - 清理整个主题两端的转义斜杠
        """
        if not text:
            return text
        
        result = text
        
        # 1. 首先处理整个主题的斜体格式
        # 匹配模式：主题前后的 \_...\_
        # 例如：\_\[GitHub\] penggan 00/CF-Workers-Buttons中的"上游同步"工作流已被禁用\_
        theme_pattern = r'\\_([^_]+)\\_'
        
        def restore_theme_handler(match):
            content = match.group(1)
            print(f"🛡️ 修复主题斜体: '{content}'")
            return f"_{content}_"
        
        # 应用主题修复
        result = re.sub(theme_pattern, restore_theme_handler, result)
        
        # 2. 处理特定格式的主题：主题：\_内容\_
        specific_pattern = r'主题[：:]\s*\\_([^_]+)\\_'
        
        def restore_specific_handler(match):
            content = match.group(1)
            return f"主题：_{content}_"
        
        result = re.sub(specific_pattern, restore_specific_handler, result)
        
        # 3. 处理被错误转义的其他斜体内容
        # 匹配单独的 \_ 转义（不在等体字内）
        isolated_underscore_pattern = r'(?<!`)\\_(?!`)'
        result = re.sub(isolated_underscore_pattern, '_', result)
        
        # 调试信息
        theme_fixes = len(re.findall(theme_pattern, text))
        specific_fixes = len(re.findall(specific_pattern, text))
        
        if theme_fixes + specific_fixes > 0:
            print(f"🛡️ 主题下划线保护: 修复了 {theme_fixes} 个完整主题和 {specific_fixes} 个特定格式")
        
        return result
    
    def clean_and_fix_monospace_urls(self, text):
        """专门处理等体字：安全清理反斜杠 + 修复URL - 只在等体字内操作"""
        print(f"🔍 开始处理等体字，文本长度: {len(text)}")
        
        def process_monospace_content(match):
            content = match.group(1)
            print(f"\n🔍 找到等体字内容: '{content}' (长度: {len(content)})")
            
            # 第一步：智能清理反斜杠
            if self.looks_like_url(content):
                # URL特殊处理：保护URL结构
                cleaned_content = self.clean_url_backslashes_safe(content)
                print(f"🔗 等体字内URL反斜杠清理: '{content}' → '{cleaned_content}'")
            else:
                # 非URL内容：安全清理，只移除Markdown转义字符前的反斜杠
                cleaned_content = self.safe_remove_markdown_backslashes(content)
                if cleaned_content != content:
                    print(f"📝 等体字内非URL反斜杠清理: '{content}' → '{cleaned_content}'")
            
            # 第二步：如果是URL则修复格式
            if self.looks_like_url(cleaned_content):
             #   print(f"🌐 等体字内检测到URL，开始修复: '{cleaned_content}'")
                fixed_content = self.fix_translated_url_specific(cleaned_content)
            #    print(f"✅ 等体字内URL修复结果: '{cleaned_content}' → '{fixed_content}'")
                return f'`{fixed_content}`'
            else:
            #    print(f"❌ 等体字内不是URL，跳过修复: '{cleaned_content}'")
                return f'`{cleaned_content}`'
        
        # 只处理等体字内的内容，使用正则匹配 `内容`
        result = re.sub(r'`([^`]*)`', process_monospace_content, text)
        print(f"\n📝 等体字处理完成")
        return result

    def clean_url_backslashes_safe(self, url_content):
        """安全清理URL中的反斜杠，保护URL结构"""
        if not url_content:
            return url_content
        
        original = url_content
        
        try:
            # 逐步清理，保护URL关键部分
            steps = [
                # 1. 修复协议部分：https:\/\/ → https://
                (r'https?\\\\?/\\\\?/', lambda m: m.group(0).replace('\\', '')),
                # 2. 修复路径分隔符：path\/to → path/to
                (r'([^/])\\\\?/', r'\1/'),
                # 3. 修复查询参数分隔符：?\&aff= → ?&aff=
                (r'([?&])\\\\?', r'\1'),
                # 4. 修复等号：aff\=123 → aff=123
                (r'\\=', '='),
                # 5. 谨慎清理其他反斜杠：只清理明显多余的反斜杠
                (r'\\([^a-zA-Z0-9])', r'\1'),  # 只清理非字母数字前的反斜杠
            ]
            
            result = url_content
            for pattern, replacement in steps:
                result = re.sub(pattern, replacement, result)
            
            # 最终检查：如果还有连续的反斜杠，但URL结构看起来正常，就保留
            if '\\' in result and self.is_valid_url_structure(result):
                print(f"⚠️  URL中仍有反斜杠，但结构正常，保留: '{result}'")
            
            return result
            
        except Exception as e:
            print(f"❌ URL反斜杠清理出错: {e}")
            return original

    def safe_remove_markdown_backslashes(self, text):
        """
        安全地移除Markdown转义字符前的反斜杠
        只移除Telegram MarkdownV2特殊字符前的反斜杠
        """
        if not text:
            return text
        
        # Telegram MarkdownV2需要转义的特殊字符
        markdown_special_chars = '_*[]()~`>#+-=|{}.!'
        
        result = text
        for char in markdown_special_chars:
            # 只移除特殊字符前的反斜杠，保留其他反斜杠
            pattern = re.escape('\\' + char)
            result = re.sub(pattern, char, result)
        
        return result

    def is_valid_url_structure(self, text):
        """检查文本是否具有有效的URL结构"""
        url_indicators = [
            r'https?://',
            r'www\.',
            r'\.(com|org|net|io|cn)',
            r'/\w',
            r'\?',
            r'=',
        ]
        
        return any(re.search(pattern, text, re.IGNORECASE) for pattern in url_indicators)

    def fix_translated_url_specific(self, url_content):
        """专门修复等体字内被翻译破坏的URL内容"""
        if not url_content:
            return url_content
        
        original_content = url_content
        print(f"🛠️ 开始修复等体字内URL: '{original_content}'")
        
        # 记录每一步的变化
        steps = []
        
        # 1. 修复中文冒号
        before_colon = url_content
        url_content = url_content.replace('：', ':')
        if url_content != before_colon:
            steps.append(f"中文冒号修复: '{before_colon}' → '{url_content}'")
        
        # 2. 修复协议部分 - 增强处理
        before_protocol = url_content
        url_content = re.sub(r'https?[\s：:]*//', 'https://', url_content)
        url_content = re.sub(r'http[\s：:]*//', 'http://', url_content)
        
        # 3. 处理缺少协议的情况
        before_protocol_add = url_content
        if not url_content.startswith(('http://', 'https://')):
            # 如果是 racknerd 域名，添加 https://
            if url_content.startswith('my.racknerd'):
                url_content = 'https://' + url_content
                steps.append(f"添加协议: '{before_protocol_add}' → '{url_content}'")
            # 处理 https:example.com 这种情况
            elif re.match(r'https?:[^/]', url_content):
                url_content = url_content.replace(':', '://', 1)
                steps.append(f"添加//: '{before_protocol_add}' → '{url_content}'")
            # 处理直接域名的情况
            elif re.match(r'[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', url_content):
                url_content = 'https://' + url_content
                steps.append(f"添加https协议: '{before_protocol_add}' → '{url_content}'")
        
        # 4. 彻底删除所有空格
        before_spaces = url_content
        url_content = re.sub(r'\s+', '', url_content)
        if url_content != before_spaces:
            steps.append(f"删除空格: '{before_spaces}' → '{url_content}'")
        
        # 5. 专门修复racknerd参数格式 - 增强处理
        if 'racknerd' in url_content.lower():
            before_aff = url_content
            
            # 修复 aff 14818 → aff=14818 (多种格式)
            url_content = re.sub(r'aff\s*=\s*(\d+)', r'aff=\1', url_content, flags=re.IGNORECASE)
            url_content = re.sub(r'aff\s*(\d+)', r'aff=\1', url_content, flags=re.IGNORECASE)
            url_content = re.sub(r'\.php\?\s*', '.php?', url_content)
            
            # 确保完整的URL格式
            if 'aff.php?' in url_content and 'aff=' not in url_content:
                url_content = re.sub(r'aff\.php\?(\d+)', r'aff.php?aff=\1', url_content)
            
            if url_content != before_aff:
                steps.append(f"aff参数修复: '{before_aff}' → '{url_content}'")
        
        # 6. 最终验证和清理
        before_final = url_content
        # 确保URL以协议开头
        if not url_content.startswith(('http://', 'https://')) and '://' not in url_content:
            if 'racknerd' in url_content.lower():
                url_content = 'https://' + url_content.lstrip('/')
                steps.append(f"最终协议修复: '{before_final}' → '{url_content}'")
        
        # 输出修复步骤
        if steps:
            print(f"📋 等体字内URL修复步骤:")
            for step in steps:
                print(f"   {step}")
        else:
            print(f"ℹ️ 等体字内URL无需修复")
        
        print(f"🎉 等体字内URL修复完成: '{original_content}' → '{url_content}'")
        
        return url_content

    def looks_like_url(self, text):
        """增强的URL检测，处理各种被破坏的URL格式"""
        if not text:
            return False
        
        # 清理文本以便检测
        cleaned = text.replace('：', ':').replace(' ', '')
        
        # 扩展URL特征模式 - 更宽松的检测
        url_indicators = [
            r'https?[:：]',              # 包含 http: 或 https:
            r'https?[:：][^/]',          # 包含 http:example.com (缺少//)
            r'my\.racknerd',             # 包含 racknerd 域名
            r'racknerd.*aff',            # racknerd相关aff链接
            r'\.php\?',                  # PHP参数
            r'aff\.php',                 # aff.php文件
            r'aff.*\d+',                 # aff加数字
            r'^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}',  # 域名模式
        ]
        
        for pattern in url_indicators:
            if re.search(pattern, cleaned, re.IGNORECASE):
                print(f"🔗 识别为URL: '{text}' → 匹配模式: {pattern}")
                return True
        
        print(f"🚫 不是URL: '{text}'")
        return False
    
    def split_message(self, text, max_length=3800):
        """分割长消息以适应Telegram限制（考虑转义后的长度）"""
        if len(text) <= max_length:
            return [text]
        
        parts = []
        while text:
            if len(text) <= max_length:
                parts.append(text)
                break
            
            # 在最大长度附近找换行符分割
            split_pos = text.rfind('\n', 0, max_length)
            if split_pos == -1:
                # 如果没有找到换行符，就在单词边界分割
                split_pos = text.rfind(' ', 0, max_length)
                if split_pos == -1:
                    split_pos = max_length
            
            parts.append(text[:split_pos])
            text = text[split_pos:].lstrip()
            
            # 添加续接标识
            if text:
                parts[-1] += "\n\n【消息续接...】"
                text = "【接上条消息】\n" + text
            
        return parts
    
    async def send_to_telegram_async(self, markdown_content, chat_id):
        """使用python-telegram-bot发送Markdown内容"""
        
        # 转义Markdown内容
        escaped_content = self.escape_markdown_v2(markdown_content)
        escaped_content = re.sub(r'(\n\s*){3,}', '\n\n', escaped_content)
        escaped_content = re.sub(r'^\n+', '', escaped_content)
        escaped_content = re.sub(r'\n+$', '', escaped_content)

        # 在发送前打印到终端
        print("\n" + "="*80)
        print("📤 准备发送到 Telegram 的消息内容:")
        print("="*80)
        print(markdown_content)
        print("="*80)
        print("🔤 转义后的消息内容:")
        print("="*80)
        print(escaped_content)
        print("="*80)
        print(f"💬 目标聊天ID: {chat_id}")
        print("="*80 + "\n")

        try:
            # 首先尝试发送MarkdownV2格式
            await self.bot.send_message(
                chat_id=chat_id,
                text=escaped_content,
                parse_mode=ParseMode.MARKDOWN_V2,
                disable_web_page_preview=True
            )
      #      logging.info(f"消息成功发送到聊天 {chat_id} (MarkdownV2格式)")
            return True
            
        except Exception as e:
            logging.warning(f"MarkdownV2发送失败，尝试纯文本格式: {e}")
            
            # Markdown发送失败，降级到纯文本
            return await self._send_as_plaintext_async(markdown_content, chat_id)

    async def _send_as_plaintext_async(self, original_content, chat_id):
        """以纯文本格式发送消息"""
        try:
            # 清理内容，移除Markdown特殊字符但保留基本格式
            plain_text = self._convert_to_plaintext(original_content)
            
            await self.bot.send_message(
                chat_id=chat_id,
                text=plain_text,
                parse_mode=None,  # 不使用Markdown
                disable_web_page_preview=True
            )
          #  logging.info(f"消息成功发送到聊天 {chat_id} (纯文本格式)")
            return True
            
        except Exception as e:
            logging.error(f"纯文本发送也失败: {e}")
            return False

    def _convert_to_plaintext(self, markdown_content):
        """将Markdown内容转换为安全的纯文本"""
        if not markdown_content:
            return ""
        
        text = markdown_content
        
        # 分步骤清理Markdown语法
        # 1. 移除代码块
        text = re.sub(r'```.*?\n(.*?)\n```', r'\1', text, flags=re.DOTALL)
        
        # 2. 移除行内代码
        text = re.sub(r'`(.*?)`', r'\1', text)
        
        # 3. 移除粗体和斜体标记但保留内容
        text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)  # 粗体
        text = re.sub(r'\*(.*?)\*', r'\1', text)      # 斜体
        text = re.sub(r'__(.*?)__', r'\1', text)      # 下划线粗体
        text = re.sub(r'_(.*?)_', r'\1', text)        # 下划线斜体
        
        # 4. 移除链接标记但保留文本
     #   text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)  # [文本](链接) -> 文本
        
        # 5. 移除可能引起问题的特殊字符（但保留基本标点）
      #  problematic_chars = r'[\\`*_{}[\]()#+-.!|~>]'
        problematic_chars = r'[\\#]'  # 只匹配反斜杠和井号
        text = re.sub(problematic_chars, ' ', text)
        
        # 6. 标准化空白（保留段落结构）
       # text = re.sub(r'[ \t]+', ' ', text)  # 合并多个空格
        text = re.sub(r'\n[ \t]*\n[ \t]*\n+', '\n\n', text)  # 保留最多两个连续空行
        text = re.sub(r'^\n+', '', text)  # 移除开头的空行
        text = re.sub(r'\n+$', '', text)  # 移除结尾的空行
        
        return text.strip()
    
    async def send_to_all_chats_async(self, markdown_content):
        """将消息发送到单个配置的聊天"""
        message_parts = self.split_message(markdown_content)
        
        # 打印分段信息
        print(f"\n📦 消息被分割成 {len(message_parts)} 部分")
        for i, part in enumerate(message_parts, 1):
            print(f"📄 第 {i}/{len(message_parts)} 部分 (长度: {len(part)} 字符):")
            print("-" * 40)
            print(part[:200] + "..." if len(part) > 200 else part)
            print("-" * 40)
        
        # 只发送到第一个聊天ID
        if not self.telegram_config['chat_ids']:
            logging.error("没有配置聊天ID")
            return False
        
        chat_id = self.telegram_config['chat_ids'][0]
        chat_success = True
        
        for i, part in enumerate(message_parts):
      #      print(f"\n🚀 正在发送到聊天 {chat_id} - 第 {i+1}/{len(message_parts)} 部分")
            success = await self.send_to_telegram_async(part, chat_id)
            if not success:
                chat_success = False
                logging.error(f"聊天 {chat_id} 的第 {i+1} 部分发送失败")
                break
            
            # 短暂延迟避免速率限制
            if i < len(message_parts) - 1:
                await asyncio.sleep(1)
        
        if chat_success:
            pass
        else:
            logging.error(f"消息发送到聊天 {chat_id} 失败")
        
        return chat_success

    async def process_single_email_async(self, mail, email_id):
        """异步处理单封邮件"""
        try:
            # 获取邮件数据
            status, msg_data = mail.fetch(email_id, '(RFC822)')
            if status != 'OK':
                logging.warning(f"获取邮件 {email_id} 内容失败")
                return False
            
            # 解析邮件
            msg = email.message_from_bytes(msg_data[0][1])
            email_data = self.extract_email_content(msg)
            
          #  print(f"\n📧 处理邮件:")
         #   print(f"   主题: {email_data['subject']}")
         #   print(f"   发件人: {email_data['from']}")
         #   print(f"   日期: {email_data['date']}")
            
       #     logging.info(f"处理邮件 - 主题: {email_data['subject']}, 发件人: {email_data['from']}")
            
            # 检查是否是中国银行信用卡邮件
            if self.is_boc_credit_card_email(email_data):
             #   print(f"\n🏦 检测到中国银行信用卡邮件，开始处理PDF附件")
                pdf_content = self.extract_and_parse_pdf_attachments(msg)
                
                if pdf_content:
                #    print(f"✅ 成功解析PDF附件，最终内容长度: {len(pdf_content)} 字符")
                    markdown_content = self.create_pdf_message(email_data, pdf_content)
                else:
                    print(f"❌ 未找到PDF附件或解析失败，发送普通邮件内容")
                    markdown_content = self.convert_email_to_markdown(email_data)
                    markdown_content = "🏦 中国银行信用卡邮件（无PDF附件）\n\n" + markdown_content
                
                success = await self.send_to_all_chats_async(markdown_content)
            
            # 检查是否是建设银行信用卡邮件
            elif self.is_ccb_credit_card_email(email_data):
             #   print(f"\n🏦 检测到建设银行信用卡邮件，开始处理HTML内容")
                original_markdown = self.convert_email_to_markdown(email_data)
                markdown_content = self.format_ccb_email_content(email_data, original_markdown)
                
            #    print(f"\n📤 准备发送的完整消息:")
                print("="*80)
                print(markdown_content)
                print("="*80)
                
                success = await self.send_to_all_chats_async(markdown_content)
            
            else:
                # 正常处理其他邮件
                print(f"📧 普通邮件，正常处理")
                markdown_content = self.convert_email_to_markdown(email_data)
                success = await self.send_to_all_chats_async(markdown_content)
            
            if success:
                # 标记为已读
                mail.store(email_id, '+FLAGS', '\\Seen')
                print(f"✅ 邮件 {email_id} 处理完成并标记为已读")
            else:
                print(f"❌ 邮件 {email_id} 发送到部分Telegram聊天失败")
            
            return success
            
        except Exception as e:
            print(f"❌ 处理邮件 {email_id} 时发生错误: {e}")
            import traceback
            traceback.print_exc()
            logging.error(f"处理邮件 {email_id} 时发生错误: {e}")
            return False

    def format_boc_statement(self, pdf_content):
        """格式化中国银行信用卡账单内容 - 修复交易明细显示"""
        try:
            print(f"\n💰 开始格式化账单内容")
            print(f"   原始PDF内容长度: {len(pdf_content)} 字符")
            
            # 提取关键信息
            account_info = self.extract_account_info(pdf_content)
            transaction_details = self.extract_transaction_details(pdf_content)
            summary_info = self.extract_summary_info(pdf_content)
            
            print(f"   提取到账户信息: {len(account_info)} 项")
            print(f"   提取到交易明细: {len(transaction_details)} 条")
            print(f"   提取到账单概览: {len(summary_info)} 项")
            
            formatted_message = ""  
            # 账户基本信息
            formatted_message += "**📋 账户信息**\n"
            formatted_message += f"持卡人: {account_info.get('holder_name', '未知')}\n"
            formatted_message += f"卡号: {account_info.get('card_number', '未知')}\n"
            formatted_message += f"账单周期: {account_info.get('billing_period', '未知')}\n"
            formatted_message += f"账单日: {account_info.get('statement_date', '未知')}\n"
            formatted_message += f"到期还款日: {account_info.get('due_date', '未知')}\n\n"
            
            # 账单概览
            formatted_message += "**💰 账单概览**\n"
            formatted_message += f"本期人民币欠款: ¥{summary_info.get('min_payment', '0.00')}\n"
            formatted_message += f"本期外币欠款: ${summary_info.get('foreign_balance', '0.00')}\n"
            formatted_message += f"最低还款额: ¥{summary_info.get('rmb_balance', '0.00')}\n"
            formatted_message += f"账单可分期金额: ¥{summary_info.get('installment_available', '0.00')}\n\n"
            
            # 交易明细 - 显示所有记录
            if transaction_details:
                formatted_message += "**💳 交易明细**\n"
                total_expenditure = 0
                total_deposit = 0
                
                # 移除数量限制，显示所有交易记录
                for i, transaction in enumerate(transaction_details, 1):
                    date = transaction.get('date', '未知日期')
                    description = transaction.get('description', '')
                    amount = transaction.get('amount', '0.00')
                    tx_type = transaction.get('type', '支出')
                    
                    # 计算总支出和总存入
                    if tx_type == "支出":
                        total_expenditure += float(amount)
                        # 支出用 - 号
                        formatted_message += f"{i}. `{date}` {description} "
                        formatted_message += f" ¥:-{amount}\n"
                    else:
                        total_deposit += float(amount)
                        # 存入用 + 号
                        formatted_message += f"{i}. `{date}` {description} "
                        formatted_message += f" ¥:+{amount}\n"
                
                # 显示交易统计
                formatted_message += f"\n**📊 交易统计**\n"
                formatted_message += f"本月总支出: ¥{total_expenditure:.2f}\n"
                formatted_message += f"本月总存入: ¥{total_deposit:.2f}\n"
                formatted_message += f"交易笔数: {len(transaction_details)} 笔\n"
                formatted_message += f"净支出: ¥{total_expenditure - total_deposit:.2f}\n"
            else:
                formatted_message += "**💳 交易明细**\n"
                formatted_message += "无交易记录\n"
            
            # 还款提醒
            formatted_message += "\n**⏰ 还款提醒**\n"
            formatted_message += f"请于 {account_info.get('due_date', '到期日')} 前还款\n"
            formatted_message += f"全额还款: ¥{summary_info.get('min_payment', '0.00')}\n"
            formatted_message += f"最低还款: ¥{summary_info.get('rmb_balance', '0.00')}\n"
            
            print(f"✅ 账单格式化完成，最终消息长度: {len(formatted_message)} 字符")
            
            return formatted_message
            
        except Exception as e:
            print(f"❌ 格式化账单失败: {e}")
            import traceback
            traceback.print_exc()
            return "**📄 账单内容:**\n" + pdf_content
        
    def extract_transaction_details_from_table(self, table_data):
        """从表格数据中提取交易明细"""
        transactions = []
        
        try:
            print(f"\n🔍 从表格数据提取交易明细...")
            
            # 假设table_data是二维数组
            for row_num, row in enumerate(table_data):
                if len(row) >= 6:  # 确保有足够的列
                    date = row[0] if row[0] else None
                    description = row[3] if len(row) > 3 else ""
                    deposit = row[4] if len(row) > 4 else ""  # 存入列
                    expenditure = row[5] if len(row) > 5 else ""  # 支出列
                    
                    # 清理数据
                    if date and re.match(r'\d{4}-\d{2}-\d{2}', date):
                        description = re.sub(r'\s+', ' ', description).strip()
                        
                        if deposit and deposit != '0.00':
                            # 存入交易
                            transactions.append({
                                'date': date,
                                'description': description,
                                'amount': deposit,
                                'type': '存入'
                            })
                            print(f"💰 表格存入: {date} {description} +{deposit}")
                        elif expenditure and expenditure != '0.00':
                            # 支出交易
                            transactions.append({
                                'date': date,
                                'description': description,
                                'amount': expenditure,
                                'type': '支出'
                            })
                            print(f"💸 表格支出: {date} {description} -{expenditure}")
            
            print(f"✅ 表格提取完成: {len(transactions)} 条记录")
            
        except Exception as e:
            print(f"❌ 表格提取失败: {e}")
        
        return transactions
    
    def extract_account_info(self, pdf_content):
        """提取账户基本信息"""
        account_info = {}
        
        try:
            # 提取持卡人姓名
            name_match = re.search(r'(\S+)\s+先生', pdf_content)
            if name_match:
                account_info['holder_name'] = name_match.group(1)
            
            # 提取账单周期
            period_match = re.search(r'信用卡账单\((\d{4}年\d{1,2}月)\)', pdf_content)
            if period_match:
                account_info['billing_period'] = period_match.group(1)
            
            # 提取账单日和到期日
            date_matches = re.findall(r'(\d{4}-\d{2}-\d{2})', pdf_content)
            if len(date_matches) >= 2:
                account_info['statement_date'] = date_matches[1]  # 账单日
                account_info['due_date'] = date_matches[0]       # 到期日
            
            # 提取卡号
            card_match = re.search(r'6259\s+0747\s+\*\*\*\*\s+(\d{4})', pdf_content)
            if card_match:
                account_info['card_number'] = f"6259 0747 **** {card_match.group(1)}"
            
        except Exception as e:
            logging.error(f"提取账户信息失败: {e}")
        
        return account_info

    def extract_summary_info(self, pdf_content):
        """提取账单概览信息"""
        summary_info = {}
        
        try:
            # 提取最低还款额
            rmb_match = re.search(r'本期人民币欠款总计.*?(\d+\.\d{2})', pdf_content)
            if rmb_match:
                summary_info['rmb_balance'] = rmb_match.group(1)
            
            # 提取外币欠款
            foreign_match = re.search(r'本期外币欠款总计.*?(\d+\.\d{2})', pdf_content)
            if foreign_match:
                summary_info['foreign_balance'] = foreign_match.group(1)
            
            # 提取人民币欠款
            min_payment_match = re.search(r'人民币RMB.*?(\d+\.\d{2})', pdf_content)
            if min_payment_match:
                summary_info['min_payment'] = min_payment_match.group(1)
            
            # 提取可分期金额
            installment_match = re.search(r'账单可分期金额.*?(\d+\.\d{2})', pdf_content)
            if installment_match:
                summary_info['installment_available'] = installment_match.group(1)
            
        except Exception as e:
            logging.error(f"提取账单概览失败: {e}")
        
        return summary_info

    def extract_transaction_details(self, pdf_content):
        """提取交易明细 - 修复存入交易丢失问题"""
        transactions = []
        
        try:
            print(f"\n🔍 开始提取交易明细...")
            
            # 从表格数据中提取交易记录
            lines = pdf_content.split('\n')
            in_transaction_section = False
            
            for line in lines:
                # 检测交易明细部分
                if '人民币交易明细' in line or '交易描述' in line:
                    in_transaction_section = True
                    print(f"✅ 进入交易明细部分")
                    continue
                
                if in_transaction_section:
                    # 匹配交易记录行 (包含存入和支出)
                    # 匹配格式: 日期 日期 卡号 描述 存入金额 支出金额
                    transaction_match = re.match(r'(\d{4}-\d{2}-\d{2})\s+(\d{4}-\d{2}-\d{2})\s+(\d{4})\s+(.*?)\s+(\d+\.\d{2})?\s*(\d+\.\d{2})?', line)
                    if transaction_match:
                        transaction_date = transaction_match.group(1)
                        description = transaction_match.group(4).strip()
                        deposit_amount = transaction_match.group(5)  # 存入金额
                        expenditure_amount = transaction_match.group(6)  # 支出金额
                        
                        # 清理描述文本
                        description = re.sub(r'CHN$', '', description).strip()
                        
                        # 确定交易类型和金额
                        if deposit_amount and deposit_amount != '0.00':
                            # 存入交易
                            transaction_type = "存入"
                            amount = deposit_amount
                            print(f"💰 发现存入交易: {transaction_date} {description} +{amount}")
                        elif expenditure_amount and expenditure_amount != '0.00':
                            # 支出交易
                            transaction_type = "支出" 
                            amount = expenditure_amount
                            print(f"💸 发现支出交易: {transaction_date} {description} -{amount}")
                        else:
                            # 无效交易记录
                            continue
                        
                        transactions.append({
                            'date': transaction_date,
                            'description': description,
                            'amount': amount,
                            'type': transaction_type
                        })
            
            # 如果没有从文本中提取到，尝试从表格数据提取
            if not transactions:
                print(f"⚠️ 文本提取失败，尝试表格提取...")
                # 从表格格式提取
                table_pattern = r'(\d{4}-\d{2}-\d{2})\s*\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*(\d{4})\s*\|\s*(.*?)\s*\|\s*(\d+\.\d{2})?\s*\|\s*(\d+\.\d{2})?'
                table_matches = re.findall(table_pattern, pdf_content)
                
                print(f"📊 表格匹配到 {len(table_matches)} 条记录")
                
                for i, match in enumerate(table_matches):
                    transaction_date = match[0]
                    description = match[3].strip()
                    deposit_amount = match[4]  # 存入金额
                    expenditure_amount = match[5]  # 支出金额
                    
                    # 确定交易类型
                    if deposit_amount and deposit_amount != '0.00':
                        transaction_type = "存入"
                        amount = deposit_amount
                        print(f"💰 表格存入交易 {i+1}: {transaction_date} {description} +{amount}")
                    elif expenditure_amount and expenditure_amount != '0.00':
                        transaction_type = "支出"
                        amount = expenditure_amount
                        print(f"💸 表格支出交易 {i+1}: {transaction_date} {description} -{amount}")
                    else:
                        continue
                    
                    transactions.append({
                        'date': transaction_date,
                        'description': description,
                        'amount': amount,
                        'type': transaction_type
                    })
            
            # 按日期排序
            transactions.sort(key=lambda x: x['date'], reverse=True)
            
            print(f"✅ 交易明细提取完成: 共 {len(transactions)} 条记录")
            for i, tx in enumerate(transactions, 1):
                print(f"   {i}. {tx['date']} {tx['type']} {tx['description']} {tx['amount']}")
            
        except Exception as e:
            print(f"❌ 提取交易明细失败: {e}")
            import traceback
            traceback.print_exc()
        
        return transactions

    def is_boc_credit_card_email(self, email_data):
        """检测是否是中国银行信用卡邮件 - 包含详细输出"""
        subject = email_data.get('subject', '').lower()
        from_email = email_data.get('from', '').lower()
        
      #  print(f"\n🔍 检测中国银行信用卡邮件:")
     #   print(f"   主题: {subject}")
       # print(f"   发件人: {from_email}")
        
        # 检查主题是否包含关键词（扩展关键词列表）
        boc_keywords = [
            '中国银行信用卡', '中行信用卡'
        ]
        
        has_boc_subject = any(keyword in subject for keyword in boc_keywords)
        
        # 检查发件人是否来自中国银行（扩展域名列表）
        is_boc_sender = any(domain in from_email for domain in [
            'boc.cn', 'bankofchina.com', 'boczhangdan@bankofchina.com'
        ])
        
        result = has_boc_subject or is_boc_sender
     #   print(f"✅ 检测结果: {'是中国银行信用卡邮件' if result else '不是中国银行信用卡邮件'}")
      #  print(f"   主题匹配: {has_boc_subject}, 发件人匹配: {is_boc_sender}")
        
        return result

    def is_ccb_credit_card_email(self, email_data):
        """检测是否是建设银行信用卡邮件"""
        subject = email_data.get('subject', '').lower()
        from_email = email_data.get('from', '').lower()
        
     #   print(f"\n🔍 检测建设银行信用卡邮件:")
     #   print(f"   主题: {subject}")
     #   print(f"   发件人: {from_email}")
        
        # 检查主题是否包含关键词
        ccb_keywords = [
            '建设银行信用卡', '建行信用卡', 'ccb credit card', 'ccb信用卡'
        ]
        
        has_ccb_subject = any(keyword in subject for keyword in ccb_keywords)
        
        # 检查发件人是否来自建设银行
        is_ccb_sender = any(domain in from_email for domain in [
            'ccb.com', 'ccb.cn', '建设银行', 'creditcard.ccb.com'
        ])
        
        result = has_ccb_subject or is_ccb_sender
     #   print(f"✅ 检测结果: {'是建设银行信用卡邮件' if result else '不是建设银行信用卡邮件'}")
      #  print(f"   主题匹配: {has_ccb_subject}, 发件人匹配: {is_ccb_sender}")
        
        return result

    def format_ccb_email_content(self, email_data, original_content):
        """格式化建设银行邮件内容 - 添加统一的头部信息"""
        print(f"\n🏦 开始格式化建设银行邮件内容")
        
        subject = email_data['subject']
        from_ = email_data['from']
        
        # 解析发件人信息
        from_name, from_email = self._parse_sender_info(from_)
        
        # 构建消息头（与其他邮件保持一致）
        message = ""
        
        # 用户名（粗体）
        if from_name:
            message += f"**{from_name}**"
        
        # 邮箱地址（等宽）
        if from_email:
            if from_name:
                message += " "  # 用户名和邮箱之间加空格
            message += f"`{from_email}`"
        
        message += "\n"
        
        # 主题（斜体）
        if subject:
            message += f"_{subject}_\n\n"
        
        # 彻底清理，只保留账单主体内容
        cleaned_content = self.extract_ccb_bill_content(original_content)
        message += cleaned_content
        
        print(f"✅ 建设银行邮件格式化完成，总长度: {len(message)} 字符")
        
        return message

    def extract_ccb_bill_content(self, input_data):
        """提取建设银行账单主体内容，移除所有邮件头部信息"""
        if not input_data:
            return ""
        
        lines = input_data.split('\n')
        bill_lines = []
        in_bill_content = False
        
        # 关键词标识账单内容开始
        bill_start_keywords = [
            '交易日期', '记账日期', '人民币交易明细', 
            '账单周期', '卡号', '信用额度'
        ]
        
        for line in lines:
            stripped_line = line.strip()
            
            # 检测账单内容开始
            if not in_bill_content:
                if any(keyword in stripped_line for keyword in bill_start_keywords):
                    in_bill_content = True
                else:
                    continue  # 跳过头部信息
            
            # 一旦进入账单内容区域，开始收集
            if in_bill_content:
                if stripped_line:
                    bill_lines.append(stripped_line)
        
        # 如果没有找到标准的关键词，返回原始清理内容
        if not bill_lines:
            return self.clean_ccb_bill_data(input_data)
        
        # 将收集到的账单内容合并并用 clean_ccb_bill_data 清理
        bill_content = '\n'.join(bill_lines)
        return self.clean_ccb_bill_data(bill_content)

    def clean_ccb_bill_data(self, input_data):
        """清理建设银行账单数据，只处理表格行"""
        cleaned_lines = []
        for line in input_data.split('\n'):
            if not line.strip():
                cleaned_lines.append(line)
                continue
            
            # 只处理看起来像表格数据的行（包含多个空格分隔的部分）
            # 跳过超链接和其他格式的行
            if '   ' in line and not line.startswith('[') and '](' not in line:
                parts = [p.strip() for p in line.split('   ') if p.strip()]
                
                # 移除第二个日期（索引为1的部分）
                if len(parts) > 1:
                    parts.pop(1)
                
                # 检查并移除重复的货币金额
                currency_indices = [i for i, part in enumerate(parts) 
                                if part in ['CNY', 'USD', 'EUR', 'JPY']]
                
                if len(currency_indices) > 1:
                    first_currency_index = currency_indices[0]
                    currency = parts[first_currency_index]
                    
                    i = first_currency_index + 2
                    while i < len(parts):
                        if parts[i] == currency:
                            parts.pop(i)
                            if i < len(parts):
                                parts.pop(i)
                        else:
                            i += 1
                
                cleaned_line = '   '.join(parts)
                cleaned_lines.append(cleaned_line)
            else:
                # 非表格行直接保留
                cleaned_lines.append(line)
        
        return '\n'.join(cleaned_lines)
    
    def extract_and_parse_pdf_attachments(self, msg):
        """提取并解析PDF附件 - 增强版，包含完整终端输出"""
        pdf_content = ""
        pdf_found = False
        
        print("\n" + "="*80)
        print("📄 开始提取PDF附件")
        print("="*80)
        
        try:
            if msg.is_multipart():
                for part_num, part in enumerate(msg.walk(), 1):
                    content_type = part.get_content_type()
                    content_disposition = str(part.get("Content-Disposition", ""))
                    filename = part.get_filename() or ""
                    
                    print(f"\n🔍 检查第 {part_num} 个邮件部分:")
                    print(f"   📝 内容类型: {content_type}")
                    print(f"   📎 内容描述: {content_disposition}")
                    print(f"   📁 文件名: {filename}")
                    
                    # 检查是否是PDF附件（放宽条件）
                    is_pdf_attachment = (
                        content_type == "application/pdf" or 
                        filename.lower().endswith('.pdf')
                    )
                    
                    # 或者是其他可能包含PDF的附件类型
                    is_possible_pdf = (
                        "attachment" in content_disposition and 
                        (content_type in ["application/octet-stream", "application/x-pdf"] or
                        "pdf" in filename.lower())
                    )
                    
                    if is_pdf_attachment or is_possible_pdf:
                        print(f"✅ 找到PDF附件: {filename}")
                        pdf_found = True
                        
                        # 提取PDF内容
                        pdf_data = part.get_payload(decode=True)
                        if pdf_data:
                            print(f"📊 PDF数据大小: {len(pdf_data)} 字节")
                            print(f"🔄 开始解析PDF内容...")
                            
                            content = self.parse_pdf_content(pdf_data)
                            if content:
                                print(f"✅ PDF解析成功，内容长度: {len(content)} 字符")
                                pdf_content += f"\n\n**PDF文件: {filename}**\n\n{content}"
                                
                                # 打印完整的PDF内容（不再截断）
                                print(f"\n📋 PDF完整内容:")
                                print("="*80)
                                print(content)
                                print("="*80)
                            else:
                                print(f"❌ PDF解析失败或内容为空")
                                pdf_content += f"\n\n**PDF文件: {filename}**\n\n（无法解析内容或内容为空）"
                        else:
                            print(f"❌ PDF附件 {filename} 没有数据")
                            pdf_content += f"\n\n**PDF文件: {filename}**\n\n（附件数据为空）"
                    else:
                        print(f"⏭️  跳过非PDF部分")
            
            if not pdf_found:
                print(f"❌ 在邮件中未找到PDF附件")
            else:
                print(f"\n✅ PDF附件处理完成，总内容长度: {len(pdf_content)} 字符")
                
            print("="*80)
            return pdf_content.strip()
        
        except Exception as e:
            print(f"❌ 解析PDF附件失败: {e}")
            import traceback
            traceback.print_exc()
            return ""
        
    def parse_pdf_content(self, pdf_data):
        """解析PDF文件内容 - 包含详细终端输出"""
        try:
            content = ""
            
            print(f"\n📖 开始解析PDF数据 ({len(pdf_data)} 字节)")
            
            # 创建临时文件
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as temp_file:
                temp_file.write(pdf_data)
                temp_file_path = temp_file.name
                print(f"📁 创建临时文件: {temp_file_path}")
            
            try:
                # 使用pdfplumber解析PDF
                with pdfplumber.open(temp_file_path) as pdf:
                    total_pages = len(pdf.pages)
                    print(f"📄 PDF总页数: {total_pages}")
                    
                    for page_num, page in enumerate(pdf.pages, 1):
                        print(f"\n📄 解析第 {page_num}/{total_pages} 页...")
                        
                        # 提取文本
                        text = page.extract_text()
                        if text:
                            print(f"📝 第 {page_num} 页文本长度: {len(text)} 字符")
                            # 清理文本
                            cleaned_text = self.clean_pdf_text(text)
                            if cleaned_text:
                                content += f"--- 第 {page_num} 页 ---\n{cleaned_text}\n\n"
                                # 打印完整的页面内容（不再截断）
                                print(f"📋 第 {page_num} 页完整内容:")
                                print("-" * 80)
                                print(cleaned_text)
                                print("-" * 80)
                            else:
                                print(f"⚠️  第 {page_num} 页清理后内容为空")
                        else:
                            print(f"⚠️  第 {page_num} 页无文本内容")
                        
                        # 提取表格（如果有）
                        tables = page.extract_tables()
                        if tables:
                            print(f"📊 第 {page_num} 页发现 {len(tables)} 个表格")
                            for table_num, table in enumerate(tables, 1):
                                if table and any(any(cell for cell in row) for row in table):
                                    table_text = self.format_table(table)
                                    if table_text:
                                        content += f"--- 第 {page_num} 页表格 {table_num} ---\n{table_text}\n\n"
                                        print(f"📋 表格 {table_num} 完整内容:")
                                        print("-" * 80)
                                        print(table_text)
                                        print("-" * 80)
                                    else:
                                        print(f"⚠️  表格 {table_num} 格式化后为空")
                                else:
                                    print(f"⚠️  表格 {table_num} 为空")
                        else:
                            print(f"ℹ️  第 {page_num} 页无表格")
                    
                    print(f"\n✅ PDF解析完成，总内容长度: {len(content)} 字符")
            
            finally:
                # 删除临时文件
                import os
                os.unlink(temp_file_path)
                print(f"🗑️  删除临时文件: {temp_file_path}")
            
            return content.strip()
        
        except Exception as e:
            print(f"❌ 解析PDF内容失败: {e}")
            import traceback
            traceback.print_exc()
            return ""
    def clean_pdf_text(self, text):
        """清理PDF提取的文本"""
        if not text:
            return ""
        
        # 移除过多的空白字符
        text = re.sub(r'\s+', ' ', text)
        
        # 移除页眉页脚等常见噪声
        lines = text.split('\n')
        cleaned_lines = []
        
        for line in lines:
            line = line.strip()
            # 跳过可能是页眉页脚的行（包含页码、日期等）
            if (len(line) < 100 and 
                (re.match(r'^\d+$', line) or  # 纯数字（可能是页码）
                re.match(r'^\d+/\d+$', line) or  # 页码格式 1/10
                re.match(r'^\d{4}[-/]\d{1,2}[-/]\d{1,2}', line) or  # 日期
                re.match(r'.*(页|第.*页).*', line))):  # 包含"页"字
                continue
            
            if line and len(line) > 2:  # 跳过过短的行
                cleaned_lines.append(line)
        
        return '\n'.join(cleaned_lines)

    def format_table(self, table):
        """格式化表格数据 - 显示完整内容"""
        if not table:
            return ""
        
        formatted_lines = []
        
        for row_num, row in enumerate(table):
            # 清理每行的数据
            cleaned_row = []
            for cell_num, cell in enumerate(row):
                cell_text = str(cell) if cell is not None else ""
                # 移除过多的空白
                cell_text = re.sub(r'\s+', ' ', cell_text).strip()
                cleaned_row.append(cell_text)
            
            # 只添加非空行
            if any(cleaned_row):
                formatted_line = " | ".join(cleaned_row)
                formatted_lines.append(formatted_line)
                print(f"   第 {row_num+1} 行: {formatted_line}")
        
        result = "\n".join(formatted_lines) if formatted_lines else ""
        print(f"   表格总行数: {len(formatted_lines)}")
        
        return result

    def create_pdf_message(self, email_data, pdf_content):
        """创建包含PDF内容的邮件消息 - 使用统一头部格式"""
        subject = email_data['subject']
        from_ = email_data['from']
        
        # 解析发件人信息
        from_name, from_email = self._parse_sender_info(from_)
        
        # 构建消息头（与其他邮件保持一致）
        message = ""
        
        # 用户名（粗体）
        if from_name:
            message += f"**{from_name}**"
        
        # 邮箱地址（等宽）
        if from_email:
            if from_name:
                message += " "  # 用户名和邮箱之间加空格
            message += f"`{from_email}`"
        
        message += "\n"
        
        # 主题（斜体）
        if subject:
            message += f"_{subject}_\n\n"
        
        if pdf_content:
            # 使用格式化函数处理账单内容
            formatted_content = self.format_boc_statement(pdf_content)
            message += formatted_content
        else:
            message += "**❌ 未找到PDF附件内容**\n"
            message += "邮件中可能不包含PDF附件，或者附件格式不支持。"
        
        return message

    async def process_all_unread_emails_async(self):
        """异步处理所有未读邮件"""
   #     logging.info("开始检查未读邮件...")
        
        # 连接邮箱
        mail = self.connect_email()
        if not mail:
            return False
        
        try:
            # 获取未读邮件
            email_ids = self.get_unread_emails(mail)
            if not email_ids:
            #    logging.info("没有未读邮件需要处理")
                return True
            
            # 处理每封邮件
            success_count = 0
            for email_id in email_ids:
                if await self.process_single_email_async(mail, email_id):
                    success_count += 1
                
                # 处理间隔，避免过快
                await asyncio.sleep(2)
            
          #  logging.info(f"邮件处理完成: 成功 {success_count}/{len(email_ids)}")
            return success_count > 0
            
        except Exception as e:
            logging.error(f"处理未读邮件时发生错误: {e}")
            return False
        finally:
            try:
                mail.close()
                mail.logout()
            except:
                pass

    def is_mainly_chinese(self, text):
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
    
    def translate_content_sync_safe(self, text):
        """安全翻译，支持长文本分段且保护URL"""
        if not text or not ENABLE_TRANSLATION:
            return text
        
        # 1. 先分割URL和纯文本
        segments = self.split_text_around_urls(text)
        final_segments = []
        
        for segment in segments:
            if self.contains_url_or_code(segment):
                # URL部分直接保留
                final_segments.append(segment)
            else:
                # 纯文本部分需要检查长度并可能分段
                if len(segment.encode('utf-8')) <= 1900:
                    # 短文本直接翻译
                    translated = self.translate_segment_safe(segment)
                    final_segments.append(translated)
                else:
                    # 长文本需要进一步分段翻译
                    segmented_translation = self.translate_long_text_safe(segment)
                    final_segments.append(segmented_translation)
        
        return ''.join(final_segments)

    def translate_long_text_safe(self, long_text):
        """安全地翻译长文本（分段处理）"""
        # 这里可以复用 translate_content_sync() 中的分段逻辑
        # 但要确保只处理纯文本，不包含URL
        MAX_BYTES = 1900
        segments = []
        current_segment = ""
        
        # 按段落分割
        paragraphs = [p for p in long_text.split('\n\n') if p.strip()]
        
        for para in paragraphs:
            para_bytes = para.encode('utf-8')
            new_segment = current_segment + ("\n\n" + para if current_segment else para)
            
            if len(new_segment.encode('utf-8')) > MAX_BYTES:
                if current_segment:
                    # 翻译已积累的内容
                    translated = self.translate_segment_safe(current_segment)
                    segments.append(translated)
                
                # 处理超长段落
                if len(para_bytes) > MAX_BYTES:
                    # 按句子进一步分割
                    sentences = re.split(r'[。.!?？]\s*', para)
                    temp_segment = ""
                    for sentence in sentences:
                        if not sentence.strip():
                            continue
                        sentence_with_punct = sentence + "。"
                        if len((temp_segment + sentence_with_punct).encode('utf-8')) > MAX_BYTES:
                            if temp_segment:
                                translated = self.translate_segment_safe(temp_segment)
                                segments.append(translated)
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
        
        # 处理最后一段
        if current_segment:
            translated = self.translate_segment_safe(current_segment)
            segments.append(translated)
        
        return "\n\n".join(segments)

    def split_text_around_urls(self, text):
        """将文本分割为URL/代码部分和纯文本部分"""
        if not text:
            return [text]
        
        segments = []
        last_end = 0
        
        # 匹配所有需要保护的模式
        patterns = [
            r'`[^`]*`',  # 等体字
            r'\[[^\]]+\]\([^)]+\)',  # Markdown链接
            r'https?://[^\s<>"{}|\\^`\[\]()]+',  # 纯URL
        ]
        
        # 组合所有模式
        combined_pattern = '|'.join(patterns)
        
        for match in re.finditer(combined_pattern, text):
            # 添加匹配前的纯文本
            if match.start() > last_end:
                segments.append(text[last_end:match.start()])
            
            # 添加匹配的URL/代码（不翻译）
            segments.append(match.group(0))
            last_end = match.end()
        
        # 添加剩余文本
        if last_end < len(text):
            segments.append(text[last_end:])
        
        return segments

    def contains_url_or_code(self, text):
        """检查文本是否包含URL或代码"""
        patterns = [
            r'`[^`]*`',
            r'\[[^\]]+\]\([^)]+\)', 
            r'https?://[^\s<>"{}|\\^`\[\]()]+',
            r'www\.[^\s<>"{}|\\^`\[\]()]+',
        ]
        
        for pattern in patterns:
            if re.search(pattern, text):
                return True
        return False

    def translate_segment_safe(self, text):
        """安全地翻译文本片段"""
        if not text.strip():
            return text
        
        try:
            cred = credential.Credential(TENCENTCLOUD_SECRET_ID, TENCENTCLOUD_SECRET_KEY)
            http_profile = HttpProfile(endpoint="tmt.tencentcloudapi.com")
            client_profile = ClientProfile(httpProfile=http_profile)
            client = tmt_client.TmtClient(cred, TENCENT_REGION, client_profile)
            
            req = models.TextTranslateRequest()
            req.SourceText = text
            req.Source = "auto"
            req.Target = "zh"
            req.ProjectId = 0
            
            resp = client.TextTranslate(req)
            return resp.TargetText
            
        except Exception as e:
            logging.error(f"翻译片段失败: {e}")
            return text  

async def main_async():
    """异步主函数"""
#   logging.info("=== 邮件到Telegram转发器启动 ===")
    
    # 初始化处理器
    processor = EmailToTelegramBot()
    
    # 处理未读邮件
    success = await processor.process_all_unread_emails_async()
    
    if success:
        pass
    else:
        logging.error("=== 处理过程中出现错误 ===")
    
    return success

def main():
    """同步主函数，保持向后兼容"""
    return asyncio.run(main_async())

if __name__ == "__main__":
    main()