# pip install beautifulsoup4 html5lib requests pdfplumber python-dotenv python-telegram-bot
import re
import imaplib
import email
import logging
import sys
import os
import asyncio
import tempfile
from email.header import decode_header
from email.utils import parseaddr
from pathlib import Path
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from telegram import Bot
from telegram.constants import ParseMode
import pdfplumber

# ============ 路径 ============
BASE_DIR = Path(__file__).parent.absolute()
LOG_FILE = BASE_DIR / "mail.log"

# ============ 环境变量 ============
load_dotenv(BASE_DIR / ".env")

IMAP_SERVER = os.getenv('IMAP_SERVER', 'imap.qq.com')
IMAP_PORT = int(os.getenv('IMAP_PORT', '993'))
EMAIL_USER = os.getenv('EMAIL_USER')
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD')

TELEGRAM_API_KEY = os.getenv('TELEGRAM_API_KEY')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

ENABLE_TRANSLATION = os.getenv('ENABLE_TRANSLATION', 'false').lower() == 'true'
LIBRETRANSLATE_URL = os.getenv('LIBRETRANSLATE_URL')
DEEPL_API_KEY = os.getenv('DEEPL_API_KEY')
DEEPL_API_URL = os.getenv('DEEPL_API_URL', 'https://api-free.deepl.com/v2/translate')

VERBOSE_LOG = os.getenv('VERBOSE_LOG', 'false').lower() == 'true'

# ============ 日志 ============
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


def vprint(*args, **kwargs):
    if VERBOSE_LOG:
        print(*args, **kwargs)


# ============ HTML 清理器 ============
class TelegramHTMLCleaner:
    """
    把邮件 HTML 清理成 Telegram 支持的 HTML：
    只保留 <b> <i> <u> <s> <code> <pre> <a href>
    """

    TAG_MAP = {
        'strong': 'b', 'b': 'b',
        'em': 'i', 'i': 'i',
        'u': 'u', 'ins': 'u',
        's': 's', 'strike': 's', 'del': 's',
        'code': 'code', 'pre': 'pre',
        'a': 'a',
    }

    UNWRAP_TAGS = [
        'div', 'span', 'p', 'font', 'section', 'article',
        'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'table', 'thead', 'tbody', 'tr', 'td', 'th',
        'ul', 'ol', 'li', 'blockquote',
        'center', 'small', 'big', 'label', 'figure',
    ]

    REMOVE_TAGS = [
        'script', 'style', 'noscript', 'meta', 'link', 'head',
        'iframe', 'object', 'embed', 'applet', 'svg',
        'form', 'input', 'button', 'select', 'textarea',
        'nav', 'footer', 'header', 'aside',
        'img', 'video', 'audio', 'canvas',
    ]

    BLOCK_TAGS = {
        'div', 'p', 'section', 'article',
        'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'tr', 'li', 'blockquote',
    }

    def __init__(self):
        self.stats = {}

    def clean(self, html):
        if not html or not html.strip():
            return ""
        try:
            soup = BeautifulSoup(html, 'html5lib')
            vprint(f"📥 原始 HTML 长度: {len(html)}")

            self._remove_tags(soup)
            self._remove_empty_links(soup)
            self._unwrap_tags(soup)
            self._map_tags(soup)
            self._clean_attributes(soup)
            self._preserve_breaks(soup)

            result = str(soup)
            result = self._final_cleanup(result)
            vprint(f"📤 清理后 HTML 长度: {len(result)}")
            vprint(f"📊 清理统计: {self.stats}")
            return result.strip()

        except Exception as e:
            logger.error(f"HTML 清理失败: {e}")
            return html

    def _remove_tags(self, soup):
        count = 0
        for tag_name in self.REMOVE_TAGS:
            for el in soup.find_all(tag_name):
                el.decompose()
                count += 1
        self.stats['removed'] = count

    def _remove_empty_links(self, soup):
        count = 0
        for link in soup.find_all('a'):
            text = link.get_text(strip=True)
            href = link.get('href', '')
            invalid = (
                not href or href == '#' or
                href.startswith(('javascript:', 'mailto:')) or
                not text
            )
            if invalid:
                link.unwrap()
                count += 1
        self.stats['empty_links'] = count

    def _unwrap_tags(self, soup):
        count = 0
        for tag_name in self.UNWRAP_TAGS:
            for el in soup.find_all(tag_name):
                if tag_name in self.BLOCK_TAGS:
                    el.insert_before(soup.new_string('\n'))
                    el.insert_after(soup.new_string('\n'))
                el.unwrap()
                count += 1
        self.stats['unwrapped'] = count

    def _map_tags(self, soup):
        count = 0
        for tag in soup.find_all(True):
            name = tag.name.lower()
            if name in self.TAG_MAP:
                new_name = self.TAG_MAP[name]
                if new_name != name:
                    tag.name = new_name
                    count += 1
        self.stats['mapped'] = count

    def _clean_attributes(self, soup):
        for tag in soup.find_all(True):
            if tag.name == 'a' and 'href' in tag.attrs:
                tag.attrs = {'href': tag['href']}
            else:
                tag.attrs = {}

    def _preserve_breaks(self, soup):
        for br in soup.find_all('br'):
            br.replace_with(soup.new_string('\n'))

    def _final_cleanup(self, html):
        html = re.sub(r'<!--.*?-->', '', html, flags=re.DOTALL)
        html = re.sub(r'(\n\s*){3,}', '\n\n', html)
        html = re.sub(r'>\s+<', '><', html)
        return html


# ============ 邮件 → Telegram ============
class EmailToTelegram:

    def __init__(self):
        missing = []
        if not EMAIL_USER: missing.append('EMAIL_USER')
        if not EMAIL_PASSWORD: missing.append('EMAIL_PASSWORD')
        if not TELEGRAM_API_KEY: missing.append('TELEGRAM_API_KEY')
        if not TELEGRAM_CHAT_ID: missing.append('TELEGRAM_CHAT_ID')
        if missing:
            logger.error(f"缺少环境变量: {', '.join(missing)}")
            sys.exit(1)

        self.bot = Bot(token=TELEGRAM_API_KEY)
        self.chat_id = TELEGRAM_CHAT_ID.split(',')[0].strip()
        self.cleaner = TelegramHTMLCleaner()

    # ---------- IMAP ----------
    def connect(self):
        try:
            mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
            mail.login(EMAIL_USER, EMAIL_PASSWORD)
            logger.info("✅ 邮箱连接成功")
            return mail
        except Exception as e:
            logger.error(f"❌ 邮箱连接失败: {e}")
            return None

    def get_unread(self, mail):
        try:
            mail.select('INBOX')
            status, messages = mail.search(None, 'UNSEEN')
            if status != 'OK':
                return []
            ids = messages[0].split()
            logger.info(f"📬 未读邮件: {len(ids)} 封")
            return ids
        except Exception as e:
            logger.error(f"❌ 获取未读邮件失败: {e}")
            return []

    # ---------- 解析邮件 ----------
    def decode(self, text):
        if not text:
            return ""
        parts = decode_header(text)
        out = ""
        for part, enc in parts:
            if isinstance(part, bytes):
                out += part.decode(enc or 'utf-8', errors='ignore')
            else:
                out += part
        return out

    def extract(self, msg):
        subject = self.decode(msg.get('Subject', '无主题'))
        from_ = self.decode(msg.get('From', '未知'))
        html_content = ""
        plain_content = ""

        if msg.is_multipart():
            for part in msg.walk():
                ctype = part.get_content_type()
                disp = str(part.get('Content-Disposition'))
                if 'attachment' in disp:
                    continue
                if ctype == 'text/plain' and not plain_content:
                    try:
                        body = part.get_payload(decode=True)
                        plain_content = body.decode(
                            part.get_content_charset() or 'utf-8',
                            errors='ignore')
                    except Exception:
                        pass
                elif ctype == 'text/html' and not html_content:
                    try:
                        body = part.get_payload(decode=True)
                        html_content = body.decode(
                            part.get_content_charset() or 'utf-8',
                            errors='ignore')
                    except Exception:
                        pass
        else:
            ctype = msg.get_content_type()
            body = msg.get_payload(decode=True)
            charset = msg.get_content_charset() or 'utf-8'
            try:
                if ctype == 'text/plain':
                    plain_content = body.decode(charset, errors='ignore')
                elif ctype == 'text/html':
                    html_content = body.decode(charset, errors='ignore')
            except Exception:
                pass

        return {
            'subject': subject,
            'from': from_,
            'html': html_content,
            'plain': plain_content,
        }

    # ---------- PDF 提取 ----------
    def extract_pdf_text(self, msg):
        texts = []
        for part in msg.walk():
            ctype = part.get_content_type()
            filename = part.get_filename() or ""
            disp = str(part.get('Content-Disposition', ''))
            is_pdf = (
                ctype == 'application/pdf' or
                filename.lower().endswith('.pdf') or
                ('attachment' in disp and 'pdf' in ctype.lower())
            )
            if not is_pdf:
                continue
            data = part.get_payload(decode=True)
            if not data:
                continue
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as f:
                    f.write(data)
                    path = f.name
                try:
                    with pdfplumber.open(path) as pdf:
                        for page in pdf.pages:
                            t = page.extract_text()
                            if t:
                                texts.append(t)
                finally:
                    os.unlink(path)
                logger.info(f"📄 PDF 已解析: {filename}")
            except Exception as e:
                logger.warning(f"PDF 解析失败 {filename}: {e}")
        return "\n".join(texts)

    # ---------- 中文检测 ----------
    def is_chinese(self, text):
        if not text:
            return True
        plain = re.sub(r'<[^>]+>', '', text)
        cn = len(re.findall(r'[\u4e00-\u9fff]', plain))
        return cn / max(len(plain), 1) > 0.1

    # ---------- 翻译 ----------
    def translate(self, text):
        if not text or not ENABLE_TRANSLATION:
            return text
        if len(text.encode('utf-8')) > 1900:
            return self._translate_long(text)
        r = self._libre(text)
        if r is not None:
            return r
        r = self._deepl(text)
        if r is not None:
            return r
        return text

    def _translate_long(self, text):
        out = []
        cur = ""
        for p in text.split('\n\n'):
            if not p.strip():
                continue
            if len((cur + "\n\n" + p).encode('utf-8')) > 1900:
                if cur:
                    out.append(self.translate(cur))
                cur = p
            else:
                cur = cur + "\n\n" + p if cur else p
        if cur:
            out.append(self.translate(cur))
        return "\n\n".join(out)

    def _libre(self, text):
        if not LIBRETRANSLATE_URL:
            return None
        try:
            import requests
            r = requests.post(
                LIBRETRANSLATE_URL,
                json={"q": text, "source": "auto", "target": "zh"},
                timeout=15)
            if r.status_code == 200:
                t = r.json().get("translatedText")
                if t and t != text:
                    logger.info("✅ LibreTranslate 翻译成功")
                    return t
            else:
                logger.warning(f"LibreTranslate 状态码: {r.status_code}")
        except Exception as e:
            logger.warning(f"LibreTranslate 失败: {e}")
        return None

    def _deepl(self, text):
        if not DEEPL_API_KEY:
            return None
        try:
            import requests
            r = requests.post(
                DEEPL_API_URL,
                json={"text": [text], "target_lang": "ZH"},
                headers={"Authorization": f"DeepL-Auth-Key {DEEPL_API_KEY}"},
                timeout=15)
            if r.status_code == 200:
                t = r.json().get("translations", [{}])[0].get("text")
                if t and t != text:
                    logger.info("✅ DeepL 翻译成功")
                    return t
            else:
                logger.warning(f"DeepL 状态码: {r.status_code}")
        except Exception as e:
            logger.warning(f"DeepL 失败: {e}")
        return None

    # ---------- 切分 HTML 段 ----------
    def split_html_segments(self, html):
        segments = []
        i = 0
        n = len(html)
        while i < n:
            if html[i] == '<':
                j = html.find('>', i)
                if j == -1:
                    segments.append(('text', html[i:]))
                    break
                segments.append(('tag', html[i:j+1]))
                i = j + 1
                continue
            j = html.find('<', i)
            if j == -1:
                j = n
            segments.append(('text', html[i:j]))
            i = j
        return segments

    def is_url(self, text):
        return bool(re.search(r'https?://\S+', text))

    def translate_html(self, html):
        """
        切分标签和纯文本，只翻译纯文本。
        标签不翻译，URL 不翻译。
        """
        if not html or not ENABLE_TRANSLATION:
            return html

        segments = self.split_html_segments(html)
        result = []
        for typ, content in segments:
            if typ == 'tag':
                result.append(content)
            else:
                parts = re.split(r'(https?://\S+)', content)
                for p in parts:
                    if p.startswith('http'):
                        result.append(p)
                    else:
                        result.append(self.translate(p))
        return ''.join(result)

    # ---------- 转义 HTML ----------
    def escape_html(self, text):
        if not text:
            return ""
        text = text.replace('&', '&amp;')
        text = text.replace('<', '&lt;')
        text = text.replace('>', '&gt;')
        return text

    # ---------- 构造消息 ----------
    def build(self, data, msg=None):
        subject = data['subject']
        from_ = data['from']
        name, addr = parseaddr(from_)
        if not name and addr:
            name = addr.split('@')[0]
        if name:
            name = re.sub(r'[<>]', '', name).strip()

        # 1. 清理 HTML
        if data['html']:
            content = self.cleaner.clean(data['html'])
        elif data['plain']:
            content = self.escape_html(data['plain'])
        else:
            content = "【此邮件无正文内容】"

        # 2. PDF
        if msg is not None:
            pdf_text = self.extract_pdf_text(msg)
            if pdf_text:
                content += "\n\n<b>📄 PDF 附件内容</b>\n\n"
                content += self.escape_html(pdf_text)

        # 3. 判断要不要翻译
        need_translation = ENABLE_TRANSLATION and not self.is_chinese(content)
        logger.info(f"🌐 需要翻译: {need_translation}")

        if need_translation:
            subject = self.translate(subject) or subject
            content = self.translate_html(content)

        # 4. 组装（header 里的标签是自己加的，不转义；内容已处理）
        parts = []
        if name:
            parts.append(f"<b>{self.escape_html(name)}</b>")
        if addr:
            parts.append(f"<code>{self.escape_html(addr)}</code>")
        header = " ".join(parts)
        if subject:
            header += f"\n<i>{self.escape_html(subject)}</i>"
        return f"{header}\n\n{content}"

    # ---------- 发送 ----------
    async def send(self, text):
        """
        发送前只转义一次——针对正文里的 & < >
        但标签和已经转义过的内容不再转义。
        """
        # 压缩空行
        text = re.sub(r'(\n\s*){3,}', '\n\n', text).strip()

        vprint("=" * 60)
        vprint("📤 准备发送:")
        vprint(text[:800])
        vprint("=" * 60)

        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            logger.info("✅ 发送成功 (HTML)")
            return True
        except Exception as e:
            logger.warning(f"HTML 发送失败，降级纯文本: {e}")
            plain = re.sub(r'<[^>]+>', '', text)
            plain = plain.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
            try:
                await self.bot.send_message(
                    chat_id=self.chat_id,
                    text=plain,
                    parse_mode=None,
                    disable_web_page_preview=True,
                )
                logger.info("✅ 发送成功 (纯文本)")
                return True
            except Exception as e2:
                logger.error(f"❌ 纯文本也失败: {e2}")
                return False

    # ---------- 主流程 ----------
    async def run(self):
        logger.info("=" * 60)
        logger.info("🚀 邮件 → Telegram 启动")
        logger.info("=" * 60)

        mail = self.connect()
        if not mail:
            return False

        try:
            ids = self.get_unread(mail)
            if not ids:
                logger.info("没有未读邮件")
                return True

            ok = 0
            for eid in ids:
                try:
                    logger.info(f"📧 处理邮件 {eid}")
                    status, data = mail.fetch(eid, '(RFC822)')
                    if status != 'OK':
                        logger.warning(f"获取邮件 {eid} 失败")
                        continue

                    msg = email.message_from_bytes(data[0][1])
                    info = self.extract(msg)
                    logger.info(f"   主题: {info['subject']}")
                    logger.info(f"   发件人: {info['from']}")

                    text = self.build(info, msg)
                    if await self.send(text):
                        mail.store(eid, '+FLAGS', '\\Seen')
                        ok += 1
                        logger.info(f"✅ 邮件 {eid} 已发送并标记已读")
                    else:
                        logger.error(f"❌ 邮件 {eid} 发送失败")
                except Exception as e:
                    logger.error(f"❌ 处理邮件 {eid} 出错: {e}")
                await asyncio.sleep(2)

            logger.info(f"完成: {ok}/{len(ids)}")
            return ok > 0
        finally:
            try:
                mail.close()
                mail.logout()
            except Exception:
                pass

UNWRAP_TAGS = [
async def main():
    bot = EmailToTelegram()
    await bot.run()


if __name__ == '__main__':
    try:
        asyncio.run(main())
        sys.exit(0)
    except Exception as e:
        logger.error(f"致命错误: {e}")
        sys.exit(1)