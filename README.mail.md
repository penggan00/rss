import imaplib
import email
import requests
import re
import logging
import mysql.connector
from datetime import datetime
import pytz
from mysql.connector import pooling
from bs4 import BeautifulSoup
from email.utils import parsedate_to_datetime
from telegram.helpers import escape_markdown
from email.utils import parseaddr
import os
from dotenv import load_dotenv

# 设置日志记录并分级
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()

# 加载 .env 文件
load_dotenv()

# 从环境变量获取配置信息
email_user = os.getenv("EMAIL_USER")
email_password = os.getenv("EMAIL_PASSWORD")
imap_server = os.getenv("IMAP_SERVER")

TELEGRAM_API_KEY = os.getenv("TELEGRAM_API_KEY")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

dbconfig = {
    "host": os.getenv("DB_HOST"),
    "database": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "pool_name": "mypool",
    "pool_size": 5
}

connection_pool = mysql.connector.pooling.MySQLConnectionPool(**dbconfig)

# 获取数据库连接
def get_connection():
    try:
        return connection_pool.get_connection()
    except mysql.connector.Error as e:
        logger.error(f"Error getting database connection: {e}")
        return None

# 加载已发送的邮件记录
def load_sent_mail():
    connection = get_connection()
    if connection is None:
        return set()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT message_id FROM sent_mail")
            rows = cursor.fetchall()
            sent_mail_ids = {row[0] for row in rows}
            logger.info(f"Loaded sent mail IDs: {sent_mail_ids}")
            return sent_mail_ids
    except mysql.connector.Error as e:
        logger.error(f"Error fetching sent mail IDs: {e}")
        return set()
    finally:
        if connection:
            connection.close()

# 批量保存已发送的邮件记录
def save_sent_mail_to_db(mail_entries):
    if not mail_entries:
        return
    connection = get_connection()
    if connection is None:
        return
    try:
        with connection.cursor() as cursor:
            sql = "INSERT IGNORE INTO sent_mail (url, subject, message_id) VALUES (%s, %s, %s)"
            cursor.executemany(sql, [(entry['url'], entry['subject'], entry['message_id']) for entry in mail_entries])
            connection.commit()  # 批量提交事务
            logger.info(f"Saved {len(mail_entries)} mail entries: {mail_entries}")
    except mysql.connector.Error as e:
        logger.error(f"Error saving sent mail: {e}")
    finally:
        if connection:
            connection.close()

# 发送消息到 Telegram
def send_message(text):
    try:
        MAX_MESSAGE_LENGTH = 4000
        messages = [text[i:i + MAX_MESSAGE_LENGTH] for i in range(0, len(text), MAX_MESSAGE_LENGTH)]
        
        for message_part in messages:
            # 尝试使用 Markdown 格式推送
            response = requests.post(f'https://api.telegram.org/bot{TELEGRAM_API_KEY}/sendMessage',
                                     data={'chat_id': TELEGRAM_CHAT_ID, 'text': message_part, 'parse_mode': 'Markdown'})
            
            if response.status_code == 200:
                logger.info(f"Message sent successfully in Markdown format: {message_part[:50]}...")  # 记录成功推送的前50字符
                continue  # 推送成功，继续发送下一条

            elif response.status_code == 400:
                logger.warning("Markdown format push failed, switching to plain text.")

                # 失败时使用纯文本推送
                response = requests.post(f'https://api.telegram.org/bot{TELEGRAM_API_KEY}/sendMessage',
                                         data={'chat_id': TELEGRAM_CHAT_ID, 'text': message_part})

                if response.status_code == 200:
                    logger.info(f"Message sent successfully in plain text format: {message_part[:50]}...")
                else:
                    logger.error(f"Failed to send message even in plain text format: {message_part[:50]}..., Status Code: {response.status_code}")
                    response.raise_for_status()

            elif response.status_code == 429:
                retry_after = int(response.headers.get('Retry-After', 1))
                logger.warning(f"Telegram API rate limited, retrying after {retry_after} seconds")
                time.sleep(retry_after)
            else:
                logger.error(f"Unexpected error: Status Code {response.status_code}")
                response.raise_for_status()

    except requests.exceptions.RequestException as e:
        logger.error(f"RequestException while sending message to Telegram: {e}")
    except Exception as e:
        logger.error(f"Error sending message to Telegram: {e}")

# 解码邮件头
def decode_header(header):
    decoded_fragments = email.header.decode_header(header)
    return ''.join(
        fragment.decode(encoding or 'utf-8') if isinstance(fragment, bytes) else fragment
        for fragment, encoding in decoded_fragments
    )

# 清理邮件正文
def clean_email_body(body):
    soup = BeautifulSoup(body, 'html.parser')
    text = soup.get_text()
    text = re.sub(r'\n\s*\n+', '\n', text)
    return text.strip()

# 获取邮件正文
def get_email_body(msg):
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            charset = part.get_content_charset() or 'utf-8'
            
            if content_type in ['text/html', 'text/plain']:
                body = part.get_payload(decode=True).decode(charset, errors='ignore')
                break
    else:
        charset = msg.get_content_charset() or 'utf-8'
        body = msg.get_payload(decode=True).decode(charset, errors='ignore')
        
    return clean_email_body(body)

# 清理邮件主题
def clean_subject(subject):
    return re.sub(r'[^\w\s]', '', subject)
def clean_subject(name):
    return re.sub(r'[^\w\s]', '', name)
def clean_subject(body):
    # 去除 Markdown 中的斜体星号 *
    return re.sub(r'\*', '', body)

# 获取并处理未读邮件
def fetch_emails():
    sent_mail = load_sent_mail()  # 已发送的邮件集合
    new_sent_mail = []

    try:
        mail = imaplib.IMAP4_SSL(imap_server)
        mail.login(email_user, email_password)
        mail.select('inbox')

        status, messages = mail.search(None, '(UNSEEN)')
        if status != 'OK':
            logger.error("Error searching for emails")
            return
        
        email_ids = messages[0].split()
        
        for email_id in email_ids:
            try:
                _, msg_data = mail.fetch(email_id, '(RFC822)')
                msg = email.message_from_bytes(msg_data[0][1])

                message_id = msg['Message-ID']
                if not message_id:
                    logger.warning(f"Email ID {email_id} has no Message-ID, generating a unique identifier.")
                    subject = clean_subject(decode_header(msg['subject']))
                    sender = decode_header(msg['from'])
                    url = f"https://mail.qq.com/{sender}"
                    message_id = f"{url}-{subject}"  # 组合 url 和 subject 生成唯一标识符

                if message_id in sent_mail:
                    logger.info(f"Email already sent: {message_id}")
                    continue

                subject = clean_subject(decode_header(msg['subject']))
                sender = decode_header(msg['from'])

                # 解析发件人
                name, email_address = parseaddr(sender)
                # 检查邮件日期字段
                date_str = msg.get('date')
                if date_str is None:
                    logger.warning(f"Email ID {email_id} has no Date header, using current time as fallback.")
                    email_date_bj = datetime.now(pytz.timezone('Asia/Shanghai'))
                else:
                    try:
                        email_date = parsedate_to_datetime(date_str)
                        email_date_bj = email_date.astimezone(pytz.timezone('Asia/Shanghai'))
                    except Exception as e:
                        logger.warning(f"Error parsing date for email ID {email_id}: {e}, using current time as fallback.")
                        email_date_bj = datetime.now(pytz.timezone('Asia/Shanghai'))

                body = get_email_body(msg)

                url = f"https://mail.qq.com/{sender}"

                message = f'''
✉️ *{name}* <{email_address}>
{subject}

{body}
'''
                send_message(message)

                new_sent_mail.append({
                    'url': url,
                    'subject': subject,
                    'message_id': message_id
                })

            except Exception as e:
                logger.error(f"Error processing email ID {email_id}: {e}")

    except Exception as e:
        logger.error(f"Error fetching emails: {e}")
    finally:
        mail.logout()
        if new_sent_mail:
            save_sent_mail_to_db(new_sent_mail)

if __name__ == '__main__':
    fetch_emails()
