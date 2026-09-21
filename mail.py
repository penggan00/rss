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
        'html', 'body',
        'div', 'span', 'p', 'font', 'section', 'article',
        'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'table', 'thead', 'tbody', 'tr', 'td', 'th',
        'ul', 'ol', 'li', 'blockquote',
        'center', 'small', 'big', 'label', 'figure',
    ]

    ALLOWED_TAGS = {'b', 'i', 'u', 's', 'code', 'pre', 'a'}

    REMOVE_TAGS = [
        'script', 'style', 'noscript', 'meta', 'link', 'head',
        'iframe', 'object', 'embed', 'applet', 'svg',
        'form', 'input', 'button', 'select', 'textarea',
        'nav', 'footer', 'header', 'aside',
        'img', 'video', 'audio', 'canvas',
        'hr',
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
            self._unwrap_unknown_tags(soup)      # ← 加这行
            self._clean_attributes(soup)
            self._preserve_breaks(soup)

            # 只取 body 内容，避免 <html>/<head>/<body>/<!DOCTYPE> 传给 Telegram
            if soup.body:
                result = ''.join(str(c) for c in soup.body.children)
            else:
                result = str(soup)

            result = self._final_cleanup(result)
            vprint(f"📤 清理后 HTML 长度: {len(result)}")
            vprint(f"📊 清理统计: {self.stats}")
            logger.info(f"🔍 clean 后前 300 字符：{repr(result[:300])}")   # ← 加这行
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
                    el.insert_after(soup.new_string('\n\n'))
                el.unwrap()
                count += 1
        self.stats['unwrapped'] = count

    def _map_tags(self, soup):             # ← 现在要补这个
        count = 0
        for tag in soup.find_all(True):
            name = tag.name.lower()
            if name in self.TAG_MAP:
                new_name = self.TAG_MAP[name]
                if new_name != name:
                    tag.name = new_name
                    count += 1
        self.stats['mapped'] = count
        
    def _unwrap_unknown_tags(self, soup):
        """解包所有 Telegram 不支持的标签"""
        count = 0
        for _ in range(5):
            unknown = [t for t in soup.find_all(True)
                    if t.name not in self.ALLOWED_TAGS]
            if not unknown:
                break
            for tag in unknown:
                tag.unwrap()
                count += 1
        self.stats['unknown_unwrapped'] = count

    def _clean_attributes(self, soup):
        for tag in soup.find_all(True):
            if tag.name == 'a' and 'href' in tag.attrs:
                href = tag['href']
                # 转义 href 里的 &，避免 Telegram 截断链接
                href = href.replace('&', '&amp;')
                tag.attrs = {'href': href}
            else:
                tag.attrs = {}

    def _preserve_breaks(self, soup):
        for br in soup.find_all('br'):
            br.replace_with(soup.new_string('\n'))

    def _final_cleanup(self, html):
        html = re.sub(r'<!DOCTYPE[^>]*>', '', html, flags=re.IGNORECASE)
        html = re.sub(r'<!--.*?-->', '', html, flags=re.DOTALL)
        html = re.sub(r'(\n\s*){3,}', '\n\n', html)
        html = re.sub(r'>[ \t]+<', '><', html)   # ← 只删空格/制表符，保留 \n
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

    def _dump(self, title, content, max_len=999999):
        if not VERBOSE_LOG:
            return
        sep = "=" * 70
        header = f"📋 {title}  (长度={len(content) if content else 0})"
        body = content or "(空)"
        if len(body) > max_len:
            body = body[:max_len] + f"\n... [已截断，共 {len(content)} 字符]"
        logger.info("\n%s\n%s\n%s\n%s\n%s", sep, header, sep, body, sep)
    def _debug_escape(self, html, stage, force=False):
        """调试：检查 HTML 里的转义问题

        - 默认只在 VERBOSE_LOG=true 时输出
        - force=True 时无视 VERBOSE_LOG，强制输出（用于发送失败等关键场景）
        """
        if not VERBOSE_LOG and not force:
            return

        if not html:
            logger.debug(f"[{stage}] HTML 为空")
            return

        logger.info(f"[{stage}] 长度={len(html)}")

        # 1. 统计裸 & （未转义的 &）
        bare_amp = re.findall(
            r'&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)',
            html
        )
        if bare_amp:
            ctx = []
            for m in re.finditer(
                r'.{0,20}&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);).{0,20}',
                html
            ):
                ctx.append(m.group(0))
            logger.warning(f"[{stage}] ⚠️ 发现 {len(bare_amp)} 个裸 & ：{ctx[:5]}")

        # 2. 统计可疑的 < （后面不是合法标签名）
        bare_lt = re.findall(
            r'<(?!/?(?:b|i|u|s|code|pre|a)(?:\s|/?>))[^>]{0,50}>',
            html
        )
        if bare_lt:
            logger.warning(f"[{stage}] ⚠️ 发现 {len(bare_lt)} 个可疑 < ：{bare_lt[:5]}")

        # 3. 统计双重转义
        dbl = re.findall(r'&amp;(?:amp|lt|gt|quot);', html)
        if dbl:
            logger.warning(f"[{stage}] ⚠️ 发现 {len(dbl)} 个双重转义：{dbl[:5]}")

        # 4. 检查 href
        hrefs = re.findall(r'href="([^"]*)"', html)
        for h in hrefs:
            if re.search(r'&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)', h):
                logger.warning(f"[{stage}] ⚠️ href 里有裸 & ：{h[:100]}")
            if '&amp;amp;' in h:
                logger.warning(f"[{stage}] ⚠️ href 双重转义：{h[:100]}")

        # 5. 检查未闭合标签
        for tag in ['a', 'b', 'i', 'u', 's', 'code', 'pre']:
            opens = len(re.findall(rf'<{tag}(?:\s|>)', html))
            closes = len(re.findall(rf'</{tag}>', html))
            if opens != closes:
                logger.warning(
                    f"[{stage}] ⚠️ <{tag}> 不配对: 开={opens} 闭={closes}"
                )

    def remove_long_urls(self, html, max_url_length=500):
        """
        移除 HTML 里的超长 URL：
        - <a href="超长URL">文本</a> → 只保留文本
        - 裸 URL 超长 → 替换成 [链接已移除]
        """
        if not html:
            return html

        # 1. 处理 <a href="...">
        def replace_long_href(match):
            href = match.group(1)
            text = match.group(2)
            if len(href) > max_url_length:
                logger.info(f"🚫 移除超长链接: {href[:50]}... ({len(href)} 字符)")
                return text  # 只保留链接文本
            return match.group(0)

        html = re.sub(
            r'<a\s+href="([^"]+)"[^>]*>(.*?)</a>',
            replace_long_href,
            html,
            flags=re.DOTALL | re.IGNORECASE
        )

        # 2. 处理裸 URL（不在标签里的）
        def replace_long_plain(match):
            url = match.group(0)
            if len(url) > max_url_length:
                logger.info(f"🚫 移除超长裸 URL: {url[:50]}... ({len(url)} 字符)")
                return '[链接已移除]'
            return url

        # 裸 URL 匹配：不在 <...> 里的 http(s)://
        html = re.sub(
            r'(?<!["\'=])https?://[^\s<>"\']+',
            replace_long_plain,
            html
        )

        return html

    def _preprocess_blank_lines(self, html):
        if not html:
            return html

        INVISIBLE = (
            '\u00ad'
            '\u2000-\u200a'
            '\u200b\u200c\u200d\u2060\ufeff'
            '\u00a0\u202f\u205f\u3000'
            '\t\r'
        )

        segments = self.split_html_segments(html)
        result = []
        for typ, content in segments:
            if typ == 'tag':
                result.append(content)
                continue

            lines = content.split('\n')
            cleaned = []
            for line in lines:
                # 去掉隐藏/空白后，看还有没有可见内容
                stripped = re.sub(f'[{INVISIBLE}]+', '', line.strip())
                if stripped:
                    cleaned.append(line)     # 有文字 → 原样保留（行内隐藏字符不动）
                else:
                    cleaned.append('')       # 整行只有隐藏/空白 → 清空
            result.append('\n'.join(cleaned))

        text = ''.join(result)
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = text.strip('\n')
        return text

    def is_boc_mail(self, data):
        from_ = (data.get('from') or '').lower()
        subject = data.get('subject') or ''
        return 'bankofchina.com' in from_ or 'boc.cn' in from_ or '中国银行' in subject
    
    def is_ccb_mail(self, data):
        """判断是否建行信用卡邮件"""
        from_ = (data.get('from') or '').lower()
        subject = data.get('subject') or ''
        return 'ccb.com' in from_ or '中国建设银行' in subject

    def format_ccb_summary(self, text):
        """建行账单汇总精简：去掉啰嗦表头，压缩成键值一行"""
        if not text:
            return text

        AMT = r'(-?[\d,]+(?:\.\d+)?)'

        # ---- 1. 删掉账单汇总表头块 ----
        text = re.sub(
            r'账户币种Currency\s*\n'
            r'\s*上期全部应还款额Last Statement Balance\s*\n'
            r'\s*\+\s*\n'
            r'\s*消费/取现/其它费用New Spending/Cash advance/Charges\s*\n'
            r'\s*-\s*\n'
            r'\s*还款/退货/费用返还Payment/Credit\s*\n'
            r'\s*=\s*\n'
            r'\s*<b>本期全部应还款额</b><b>New Balance</b>\s*\n',
            '',
            text
        )

        # ---- 2. 币种汇总：人民币/美元/欧元 各 4 个金额 → 一行（宽松版） ----
        for cn, en in [('人民币', 'CNY'), ('美元', 'USD'), ('欧元', 'EUR')]:
            pat = re.compile(
                rf'{cn}（{en}）\s*\n'
                rf'\s*{AMT}\s*\n'
                rf'\s*{AMT}\s*\n'
                rf'\s*{AMT}\s*\n'
                rf'\s*<b>{AMT}</b>',
                re.MULTILINE
            )
            text = pat.sub(
                lambda m: (f"{cn} 上期{m.group(1)} +消费{m.group(2)} "
                        f"-还款{m.group(3)} =应还{m.group(4)}"),
                text
            )

        # ---- 3. 信用信息：本期账单日 / 授信额度 / 取现额度 / 可用额度 ----
        text = re.sub(
            r'本期账单日\s*\n\s*Statement Date\s*\n\s*\n\s*(\d{4}-\d{2}-\d{2})',
            r'本期账单日 \1',
            text
        )
        # 授信额度（宽松版）
        text = re.sub(
            r'授信额度\s*\n\s*Credit Limit\s*\n+'
            r'\s*<a href="[^"]*">\s*\n+'
            r'\s*((?:CNY\s*)?[\d,]+(?:\.\d+)?)</a>',
            r'授信额度 \1',
            text
        )
        text = re.sub(
            r'取现额度\s*\n\s*Cash Advance Limit\s*\n\s*\n\s*((?:CNY\s*)?[\d,]+(?:\.\d+)?)',
            r'取现额度 \1',
            text
        )
        text = re.sub(
            r'截止本期账单日\s*\n\s*可用额度\s*\n\s*Available Limit\s*\n\s*\n\s*([\d,]+(?:\.\d+)?)',
            r'可用额度 \1',
            text
        )

        # ---- 4. 积分余额：整行删（宽松版） ----
        text = re.sub(
            r'<b>\s*积分余额.*?点击查询积分</a></b>',
            '',
            text,
            flags=re.DOTALL
        )

        # ---- 5. 账单周期/到期还款日 ----
        text = re.sub(
            r'账单周期Statement Cycle\s*\n'
            r'\s*<b>([^<]+)</b>'
            r'<b>本期到期还款日</b><b>Payment Due Date</b><b>([^<]+)</b>',
            r'账单周期 \1\n到期还款日 \2',
            text
        )

        # ---- 6. 应还款表头删掉 ----
        text = re.sub(
            r'账户币种Currency\s*\n'
            r'\s*<b>本期全部应还款额</b><b>New Balance</b>\s*\n'
            r'\s*最低还款额Min\.Payment\s*\n'
            r'\s*争议款/笔数Dispute Amt/Nbr\s*\n',
            '',
            text
        )

        # ---- 7. 应还款行：人民币（CNY） 应还 最低 争议 ----
        text = re.sub(
            rf'^(人民币（CNY）)\s*\n'
            rf'\s*<b>{AMT}</b>\s*\n'
            rf'\s*{AMT}\s*\n'
            r'\s*(-)',
            lambda m: f"{m.group(1)} 应还{m.group(2)} 最低{m.group(3)} 争议{m.group(4)}",
            text,
            flags=re.MULTILINE
        )

        # ---- 8. 专项分期：整块替换（宽松版） ----
        text = re.sub(
            r'<b>\s*专项分期剩余总金额：\s*CNY\s*[\d.]+</b>.*?服务。',
            '专项分期：CNY 0.00',
            text,
            flags=re.DOTALL
        )

        # ---- 9. 应还款明细表头删掉 ----
        text = re.sub(
            r'信用卡卡号Card Number\s*\n'
            r'\s*账户币种Currency\s*\n'
            r'\s*应还款额/溢缴款New Balance\s*\n'
            r'\s*最低还款额Min\.Payment\s*\n'
            r'\s*账户币种Currency\s*\n'
            r'\s*应还款额/溢缴款New Balance\s*\n'
            r'\s*最低还款额Min\.Payment\s*\n',
            '',
            text
        )

        # ---- 10. 应还款明细行（卡号那行） ----
        text = re.sub(
            rf'^(\d{{8}}\*{{4}}\d{{4}})\s*\n'
            rf'\s*(人民币\(CNY\))\s*\n'
            rf'\s*{AMT}\s*\n'
            rf'\s*{AMT}',
            lambda m: f"{m.group(1)} {m.group(2)} 应还{m.group(3)} 最低{m.group(4)}",
            text,
            flags=re.MULTILINE
        )

        # ---- 11. 交易明细表头删掉 ----
        text = re.sub(
            r'交易日\s*\n'
            r'\s*银行记账日\s*\n'
            r'\s*卡号后四位\s*\n'
            r'\s*交易描述\s*\n'
            r'\s*交易币/金额\s*\n'
            r'\s*结算币/金额\s*\n'
            r'\s*T-Date\s*\n'
            r'\s*P-Date\s*\n'
            r'\s*Card Number\s*\n'
            r'\s*Description\s*\n'
            r'\s*Trans\.Curr/Amt\s*\n'
            r'\s*Sett\.Curr/Amt\s*\n',
            '',
            text
        )

        # ---- 12. [人民币账户] 那行删掉 ----
        text = re.sub(
            r'\[人民币账户\] RMB Account\s*\n'
            r'\s*上期账单余额\(Previous Balance\)\s*\n'
            r'\s*[\d,]+(?:\.\d+)?\s*\n',
            '',
            text
        )

        # ---- 13. 去掉行首缩进 ----
        lines = text.split('\n')
        lines = [ln.lstrip() if ln.strip() else '' for ln in lines]
        text = '\n'.join(lines)

        # ---- 14. 压缩空行 ----
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip('\n')
    
    def format_ccb_records(self, text):
        """建行交易明细：8 行一条 → 2 行一条"""
        if not text:
            return text

        DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
        CARD_RE = re.compile(r'^\d{4}$')
        CUR_RE = re.compile(r'^[A-Z]{3}$')
        AMT_RE = re.compile(r'^-?[\d,]+(?:\.\d+)?$')

        lines = text.split('\n')
        out = []
        i = 0
        n = len(lines)

        while i < n:
            if not lines[i].strip():
                out.append('')
                i += 1
                continue

            # 收集接下来 8 个非空字段
            fields = []
            j = i
            while j < n and len(fields) < 8:
                s = lines[j].strip()
                if s:
                    fields.append(s)
                j += 1

            if (len(fields) == 8
                    and DATE_RE.match(fields[0])
                    and DATE_RE.match(fields[1])
                    and CARD_RE.match(fields[2])
                    and CUR_RE.match(fields[4])
                    and CUR_RE.match(fields[6])
                    and AMT_RE.match(fields[5])
                    and AMT_RE.match(fields[7])):
                out.append(f"{fields[0]} {fields[3]}")
                out.append(f"{fields[2]} {fields[4]} {fields[5]}")
                out.append('')
                i = j
            else:
                out.append(lines[i].rstrip())
                i += 1

        result = '\n'.join(out)
        result = re.sub(r'\n{3,}', '\n\n', result)
        return result.strip('\n')

    def format_boc_pdf(self, pdf_text):
        """中国银行信用卡账单 PDF 格式化"""
        if not pdf_text:
            return pdf_text

        lines = pdf_text.split('\n')
        out = []

        # 头部：账单月份
        m = re.search(r'中国银行信用卡账单\((\d{4}年\d{2}月)\)', pdf_text)
        if m:
            out.append(f"中国银行信用卡账单 {m.group(1)}")
            out.append('')

        # 总览
        m = re.search(r'(\d{4}-\d{2}-\d{2})\s+(\d{4}-\d{2}-\d{2})\s+([\d.]+)', pdf_text)
        if m:
            out.append(f"到期还款日 {m.group(1)}")
            out.append(f"账单日 {m.group(2)}")
            out.append(f"本期人民币欠款 {m.group(3)}")
            out.append('')

        # 卡列表（卡号 + 应还 + 最低）
        card_lines = re.findall(
            r'(\d{4}\s+\d{4}\s+\*{4}\s+\d{4})\s+([\d.]+)\s+([\d.]+)',
            pdf_text
        )
        for card, balance, minpay in card_lines:
            out.append(f"【卡 {card.replace(' ', '')}】")
            out.append(f"本期应还 {balance} 最低 {minpay}")
            out.append('')

        # 明细：按"人民币交易明细"分块
        # 每块对应一张卡，直到下一个卡名或"第 N 页"
        blocks = re.split(r'人民币交易明细/RMB Transaction Detailed List', pdf_text)

        for block in blocks[1:]:   # 跳过第一块（表头之前）
            # 找卡名和卡号
            card_m = re.search(r'(.+?)\(卡号：(\d{4})\)', block)
            card_name = card_m.group(1).strip() if card_m else ''
            card_no = card_m.group(2) if card_m else ''

            # 找汇总行
            sum_m = re.search(
                r'人民币/RMB\s+(欠款/DEBT|存款/CREDIT)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)',
                block
            )

            if card_name:
                out.append(f"━━━ 卡 {card_no}（{card_name}）━━━")
            if sum_m:
                out.append(f"本期支出 {sum_m.group(3)} 存入 {sum_m.group(2)} 余额 {sum_m.group(4)}")

            # 明细行：扫描 block，遇到日期行就提取
            # 描述可能跨行，攒"上一行"
            block_lines = block.split('\n')
            pending_desc = []
            for line in block_lines:
                line = line.strip()
                if not line:
                    continue
                # 匹配明细行：日期 日期 卡号 [描述] 金额
                dm = re.match(
                    r'^(\d{4}-\d{2}-\d{2})\s+(\d{4}-\d{2}-\d{2})\s+(\d{4})\s+(.*?)\s+([\d,]+\.\d{2})$',
                    line
                )
                if dm:
                    date, post, cn, desc, amt = dm.groups()
                    full_desc = ' '.join(pending_desc + [desc]).strip()
                    full_desc = re.sub(r'\s*CHN\s*$', '', full_desc)
                    out.append(f"{date} {full_desc} 支出{amt}")
                    pending_desc = []
                else:
                    # 攒可能属于下一行描述的内容
                    if not line.startswith(('交易日', '银行记账日', '第', '卡号后四位',
                                            'Transaction', 'Posting', 'Description',
                                            'Digits', 'of Card', '人民币交易明细')):
                        pending_desc.append(line)

            out.append('')

        return '\n'.join(out).strip('\n')
    # ---------- PDF 提取 ----------
    def extract_pdf_text(self, msg):
        texts = []
        for part in msg.walk():
            ctype = part.get_content_type()
            filename = part.get_filename() or ""
            disp = str(part.get('Content-Disposition', ''))

            is_pdf = (
                ctype == 'application/pdf'
                or ctype in ('application/x-pdf', 'application/octet-stream')
                or filename.lower().endswith('.pdf')
                or ('attachment' in disp and 'pdf' in ctype.lower())
                or 'pdf' in filename.lower()
            )
            if not is_pdf:
                continue
            data = part.get_payload(decode=True)
            if not data:
                logger.warning(f"📄 PDF {filename} 数据为空")
                continue
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as f:
                    f.write(data)
                    path = f.name
                try:
                    page_count = 0
                    with pdfplumber.open(path) as pdf:
                        page_count = len(pdf.pages)
                        for page in pdf.pages:
                            t = page.extract_text()
                            if t:
                                texts.append(t)
                    total = sum(len(t) for t in texts)
                    logger.info(f"📄 PDF 已解析: {filename} 页数={page_count} 提取字符={total}")
                    if total == 0:
                        logger.warning(f"📄 PDF {filename} 提取到 0 字符——可能是扫描件或图片型 PDF")
                finally:
                    os.unlink(path)
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
        if len(text.encode('utf-8')) > 8000:
            return self._translate_long(text)
        r = self._libre(text)
        if r is not None:
            return r
        r = self._deepl(text)
        if r is not None:
            return r
        return text

    def _translate_long(self, text):
        MAX = 8000
        out = []
        cur = ""
        for p in text.split('\n\n'):
            if not p.strip():
                continue
            # 如果单段就超限，按句子再切
            if len(p.encode('utf-8')) > MAX:
                sentences = re.split(r'(?<=[.!?。！？])\s+', p)
                for s in sentences:
                    if len((cur + " " + s).encode('utf-8')) > MAX:
                        if cur:
                            out.append(self.translate(cur))
                        cur = s
                    else:
                        cur = cur + " " + s if cur else s
            else:
                if len((cur + "\n\n" + p).encode('utf-8')) > MAX:
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
                json={"q": text, "source": "auto", "target": "zh", "format": "html"},
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
        if not html or not ENABLE_TRANSLATION:
            return html

        # ============ 1. 保护不该翻译的内容 ============
        placeholders = {}
        counter = [0]

        def protect(m):
            key = f"ZXQPH{counter[0]}ZXQ"
            placeholders[key] = m.group(0)
            counter[0] += 1
            return f"<code>{key}</code>"

        # URL
        html = re.sub(r'https?://[^\s<>"\']+', protect, html)
        # 带空格文件名（如 balenaEtcher-2.1.7 Setup.exe）
        html = re.sub(
            r'\b[\w][\w.-]*\s+[A-Z][\w.-]*\.(?:exe|msi|dmg|pkg|rpm|deb|zip)\b',
            protect, html, flags=re.IGNORECASE
        )
        # 普通文件名
        html = re.sub(
            r'\b[\w][\w.-]*\.(?:rpm|deb|dmg|exe|zip|tar\.gz|tgz|txt|json|AppImage|snap|msi|pkg|apk|7z|gz|bz2|xz)\b',
            protect, html, flags=re.IGNORECASE
        )
        # SHA256SUMS
        html = re.sub(r'\bSHA256SUMS\b', protect, html)
        # owner/repo
        html = re.sub(r'(?<=[\s>])[\w.-]+/[\w.-]+(?=[\s<])', protect, html)
        # 版本号
        html = re.sub(r'\bv\d+\.\d+\.\d+\b', protect, html)
        # commit hash
        html = re.sub(r'\b(?=[0-9a-f]*\d)[0-9a-f]{7,40}\b', protect, html)
        # 其他符号保护（避免翻译器乱加空格）
        html = re.sub(r'[;_\\$|]', protect, html)

        # ============ 2. 保护换行 ============
        html = html.replace('\n\n', '<code>ZXQNL2ZXQ</code>')
        html = html.replace('\n', '<code>ZXQNL1ZXQ</code>')

        # ============ 3. 简化 clean_text ============
        def clean_text(t):
            t = re.sub(r'-{2,}', '-', t)
            return t

        segments = self.split_html_segments(html)
        result = []
        for typ, content in segments:
            if typ == 'tag':
                result.append(content)
            else:
                result.append(clean_text(content))
        html = ''.join(result)

        # ============ 4. 翻译 ============
        translated = self.translate(html)

        # ============ 5. 还原占位符 ============
        for key, val in placeholders.items():
            translated = translated.replace(f"<code>{key}</code>", val)
            translated = translated.replace(key, val)

        # 还原换行
        translated = translated.replace('<code>ZXQNL2ZXQ</code>', '\n\n')
        translated = translated.replace('ZXQNL2ZXQ', '\n\n')
        translated = translated.replace('<code>ZXQNL1ZXQ</code>', '\n')
        translated = translated.replace('ZXQNL1ZXQ', '\n')

        return translated
    
    def _clean_translate_artifacts(self, text):
        """清理翻译后产生的语言标注括号和 ASS 样式串

        只在翻译成功后调用，不翻译时不会触发。
        """
        if not text:
            return text

        # 1. ASS/SSA 样式串：{\fn黑体\fs22\bord1...}
        text = re.sub(r'\{[^{}]*\\[^{}]*\}', '', text)

        # 2. 语言标注括号：(英语) (韩语) (中文(简体)) 等
        lang_names = (
            r'中文(?:\([^)]*\))?|繁体中文|简体中文'
            r'|英语|英文'
            r'|韩语|韩文|朝鲜语'
            r'|日语|日文'
            r'|俄语|俄文'
            r'|法语|法文'
            r'|德语|德文'
            r'|西班牙语|西班牙文'
            r'|葡萄牙语|葡萄牙文'
            r'|意大利语|意大利文'
            r'|阿拉伯语|阿拉伯文'
        )
        text = re.sub(rf'[（(]\s*(?:{lang_names})\s*[）)]', '', text)

        return text
    def _resanitize_href(self, html):
        """翻译后修复 href 里的转义"""
        if 'href=' not in html:
            return html
        try:
            soup = BeautifulSoup(html, 'html5lib')
            for a in soup.find_all('a', href=True):
                href = a['href']
                href = href.replace('&amp;', '&').replace('&', '&amp;')
                a['href'] = href
            # 只取 body 内容
            if soup.body:
                return ''.join(str(c) for c in soup.body.children)
            return str(soup)
        except Exception as e:
            logger.warning(f"_resanitize_href 失败: {e}")
            return html
    # ---------- 转义 HTML ----------
    def escape_html(self, text):
        if not text:
            return ""
        text = text.replace('&', '&amp;')
        text = text.replace('<', '&lt;')
        text = text.replace('>', '&gt;')
        return text

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
                # ★ 中行 PDF 格式化
                if self.is_boc_mail(data):
                    pdf_text = self.format_boc_pdf(pdf_text)
                content += "\n\n<b>📄 PDF 附件内容</b>\n\n"
                content += self.escape_html(pdf_text)

        # 3. 超长 URL 过滤
        content = self.remove_long_urls(content)

        # 3.5 隐藏行清理 + 空行压缩
        content = self._preprocess_blank_lines(content)
        # ★ 建行明细格式化
        if self.is_ccb_mail(data):
            content = self.format_ccb_summary(content)   # ← 先精简汇总
            content = self.format_ccb_records(content)   # ← 再格式化明细
            self._dump("③.5 建行格式化后", content)
        # 4. 翻译
        need_translation = ENABLE_TRANSLATION and not self.is_chinese(content)
        logger.info(f"🌐 需要翻译: {need_translation}")

        if need_translation:
            subject = self.translate(subject) or subject
            subject = self._clean_translate_artifacts(subject)

            content = self.translate_html(content)
            content = self._resanitize_href(content)
            content = self._clean_translate_artifacts(content)


        # 5. 组装
        parts = []
        if name:
            parts.append(f"<b>{self.escape_html(name)}</b>")
        if addr:
            parts.append(f"<code>{self.escape_html(addr)}</code>")
        header = " ".join(parts)
        if subject:
            header += f"\n<i>{self.escape_html(subject)}</i>"
        result = f"{header}\n\n{content}"

        return result

    # ---------- 发送 ----------
    def split_message(self, text, max_length=3800):
        """分段发送，按 \n\n 切分"""
        if len(text) <= max_length:
            return [text]
        parts = []
        while text:
            if len(text) <= max_length:
                parts.append(text)
                break
            pos = text.rfind('\n\n', 0, max_length)
            if pos == -1:
                pos = text.rfind('\n', 0, max_length)
            if pos == -1:
                pos = text.rfind(' ', 0, max_length)
            if pos == -1:
                pos = max_length
            parts.append(text[:pos])
            text = text[pos:].lstrip()
            if text:
                parts[-1] += "\n\n【消息续接...】"
                text = "【接上条消息】\n" + text
        return parts

    async def send(self, text):
        text = re.sub(r'(\n\s*){3,}', '\n\n', text).strip()
        parts = self.split_message(text)

        for i, p in enumerate(parts):
            self._dump(f"② 发送前 第 {i+1}/{len(parts)} 段", p)   # ← 新增

        success = True
        for i, part in enumerate(parts):
            if not await self._send_one(part, i + 1, len(parts)):
                success = False
                break
            if i < len(parts) - 1:
                await asyncio.sleep(1)
        return success

    async def _send_one(self, part, idx, total):
        """发送单段"""
        vprint("=" * 60)
        vprint(f"📤 发送 {idx}/{total}:")
        vprint(part[:500])
        vprint("=" * 60)

        # 发送前也检查一遍（可选，建议加）
        self._debug_escape(part, f"发送前 {idx}/{total}")

        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=part,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            logger.info(f"✅ 发送成功 (HTML) {idx}/{total}")
            return True
        except Exception as e:
            logger.warning(f"❌ HTML 发送失败: {e}")
            logger.warning(f"❌ 失败段落全文（{len(part)} 字符）：\n{part}")
            self._debug_escape(part, f"发送失败 {idx}/{total}", force=True)

            # ---- 回退到纯文本 ----
            plain = re.sub(r'<[^>]+>', '', part)
            plain = plain.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')

            try:
                await self.bot.send_message(
                    chat_id=self.chat_id,
                    text=plain,
                    parse_mode=None,
                    disable_web_page_preview=True,
                )
                logger.info(f"✅ 发送成功 (纯文本) {idx}/{total}")
                return True
            except Exception as e2:
                logger.error(f"❌ 纯文本也失败: {e2}")
                logger.error(f"❌ plain 全文（{len(plain)} 字符）：\n{plain}")
                self._debug_escape(plain, f"纯文本发送失败 {idx}/{total}", force=True)
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
                    if info['html']:
                        self._dump(f"① 解析后 HTML（邮件 {eid}）", info['html'])
                    elif info['plain']:
                        self._dump(f"① 解析后 plain（邮件 {eid}）", info['plain'])
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