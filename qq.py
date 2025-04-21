import os
import re
import sqlite3
import time
import subprocess
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
from dotenv import load_dotenv
from tencentcloud.common import credential
from tencentcloud.common.profile.client_profile import ClientProfile
from tencentcloud.common.profile.http_profile import HttpProfile
from tencentcloud.tmt.v20180321 import tmt_client, models
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

load_dotenv()

class Config:
    def __init__(self):
        self.TELEGRAM_TOKEN = self._get_env('TELEGRAM_API_KEY')
        self.AUTHORIZED_CHAT_IDS = self._parse_chat_ids('TELEGRAM_CHAT_ID')
        self.TENCENT_SECRET_ID = self._get_env('TENCENT_SECRET_ID')
        self.TENCENT_SECRET_KEY = self._get_env('TENCENT_SECRET_KEY')
        self.TENCENT_REGION = os.getenv('TENCENT_REGION')
        self.TENCENT_PROJECT_ID = int(os.getenv('TENCENT_PROJECT_ID'))
        self.USD_SCRIPT_PATH = os.path.expanduser('~/rss/usd.sh')

    def _get_env(self, var_name: str) -> str:
        value = os.getenv(var_name)
        if not value:
            raise ValueError(f"Missing required environment variable: {var_name}")
        return value

    def _parse_chat_ids(self, var_name: str) -> List[int]:
        ids_str = self._get_env(var_name)
        try:
            return [int(id_str.strip()) for id_str in ids_str.split(',')]
        except ValueError:
            raise ValueError(f"Invalid {var_name} format")

config = Config()

class TranslationCache:
    def __init__(self, db_path: str = 'translations.db'):
        self.conn = sqlite3.connect(db_path)
        self._init_db()
    
    def _init_db(self) -> None:
        with self.conn:
            self.conn.execute('''
                CREATE TABLE IF NOT EXISTS translations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_text TEXT NOT NULL,
                    source_lang TEXT NOT NULL,
                    target_lang TEXT NOT NULL,
                    translated_text TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(source_text, source_lang, target_lang)
                )
            ''')
            self.conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_translations_key 
                ON translations(source_text, source_lang, target_lang)
            ''')
    
    def get(self, source_text: str, source_lang: str, target_lang: str) -> Optional[str]:
        cursor = self.conn.execute('''
            SELECT translated_text FROM translations 
            WHERE source_text=? AND source_lang=? AND target_lang=?
        ''', (source_text, source_lang, target_lang))
        result = cursor.fetchone()
        return result[0] if result else None
    
    def set(self, source_text: str, source_lang: str, target_lang: str, translated_text: str) -> bool:
        try:
            with self.conn:
                self.conn.execute('''
                    INSERT OR REPLACE INTO translations 
                    (source_text, source_lang, target_lang, translated_text) 
                    VALUES (?, ?, ?, ?)
                ''', (source_text, source_lang, target_lang, translated_text))
            return True
        except sqlite3.Error:
            return False

cache = TranslationCache()

def detect_language(text: str) -> str:
    if not text or not isinstance(text, str):
        return 'unknown'
    
    clean_text = re.sub(r'[^\w\u4e00-\u9fff]', '', text, flags=re.UNICODE)
    if not clean_text:
        return 'unknown'
    
    char_stats = {
        'zh': len(re.findall(r'[\u4e00-\u9fff]', clean_text)),
        'ja': len(re.findall(r'[\u3040-\u30ff\u31f0-\u31ff]', clean_text)),
        'ko': len(re.findall(r'[\uac00-\ud7af\u1100-\u11ff]', clean_text)),
        'ru': len(re.findall(r'[\u0400-\u04FF]', clean_text)),
        'en': len(re.findall(r'[a-zA-Z]', clean_text)),
    }
    
    dominant_lang, dominant_ratio = max(
        ((lang, count / len(clean_text)) for lang, count in char_stats.items()),
        key=lambda x: x[1]
    )
    
    return dominant_lang if dominant_ratio > 0.4 else 'other'

def get_translation_direction(text: str) -> Tuple[str, str]:
    lang = detect_language(text)
    return ('auto', 'zh') if lang != 'zh' else ('zh', 'en')

class TencentTranslator:
    def __init__(self):
        cred = credential.Credential(
            config.TENCENT_SECRET_ID,
            config.TENCENT_SECRET_KEY
        )
        
        http_profile = HttpProfile()
        http_profile.reqMethod = "POST"
        http_profile.reqTimeout = 30
        
        client_profile = ClientProfile()
        client_profile.httpProfile = http_profile
        client_profile.signMethod = "TC3-HMAC-SHA256"
        
        self.client = tmt_client.TmtClient(
            cred, 
            config.TENCENT_REGION, 
            client_profile
        )
    
    async def translate(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        max_retries: int = 3
    ) -> str:
        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                req = models.TextTranslateRequest()
                req.SourceText = text
                req.Source = source_lang
                req.Target = target_lang
                req.ProjectId = config.TENCENT_PROJECT_ID
                
                resp = self.client.TextTranslate(req)
                return resp.TargetText
            
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    time.sleep(min(2 ** attempt, 5))
        
        raise last_error if last_error else Exception("Translation error")

translator = TencentTranslator()

async def translate_message(
    text: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> None:
    source_lang, target_lang = get_translation_direction(text)
    
    cached = cache.get(text, source_lang, target_lang)
    if cached:
        await update.message.reply_text(cached)  
        return
    
    try:
        translated = await translator.translate(text, source_lang, target_lang)
        cache.set(text, source_lang, target_lang, translated)
        
        chunk_size = 4000
        chunks = [translated[i:i+chunk_size] for i in range(0, len(translated), chunk_size)]
        
        for chunk in chunks:
            await update.message.reply_text(chunk) 
    
    except Exception as e:
        await update.message.reply_text(f"Error: {str(e)}")  

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message.chat.id not in config.AUTHORIZED_CHAT_IDS:
        return
    
    if len(update.message.text) > 5000:
        return
    
    await translate_message(update.message.text, update, context)

async def usd_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message.chat.id not in config.AUTHORIZED_CHAT_IDS:
        return
    
    try:
        # 执行脚本
        result = subprocess.run(
            [config.USD_SCRIPT_PATH],
            capture_output=True,
            text=True,
            check=True
        )
        
        output = result.stdout or "Script executed successfully with no output"
        await update.message.reply_text(f"{output}")
    
    except subprocess.CalledProcessError as e:
        error_msg = f"Script failed with return code {e.returncode}:\n{e.stderr}"
        await update.message.reply_text(error_msg)
    except Exception as e:
        await update.message.reply_text(f"Error executing script: {str(e)}")
async def execute_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """终极优化版命令执行函数，完美处理交互式命令"""
    if update.message.chat.id not in config.AUTHORIZED_CHAT_IDS:
        return
    
    if not context.args:
        await update.message.reply_text("请提供要执行的命令，例如: /cmd ls -l")
        return
    
    command = ' '.join(context.args)
    base_cmd = command.strip().split()[0] if command.strip().split() else ""
    
    try:
        # 定义命令特殊处理方式
        command_handlers = {
            'ps': lambda: subprocess.run(['ps', '-ef'], capture_output=True, text=True, timeout=10),
            'top': lambda: subprocess.run(['top', '-b', '-n', '1'], capture_output=True, text=True, timeout=10),
            'htop': lambda: subprocess.run(
                ['bash', '-c', 'TERM=xterm-256color htop --no-color --delay=1'],
                capture_output=True, text=True, timeout=15, shell=True
            ),
            'reboot': lambda: subprocess.run(
                ['sudo', '-S', 'reboot'],
                input='NOPASSWD\n',  # 替换为实际密码或使用NOPASSWD
                capture_output=True, text=True, timeout=10
            ),
            'nano': lambda: subprocess.run(
                ['bash', '-c', 'echo "无法在非交互式终端中运行nano编辑器"'],
                capture_output=True, text=True, timeout=5
            ),
            'vim': lambda: subprocess.run(
                ['bash', '-c', 'echo "无法在非交互式终端中运行vim编辑器"'],
                capture_output=True, text=True, timeout=5
            )
        }
        
        # 执行命令
        if base_cmd in command_handlers:
            result = command_handlers[base_cmd]()
        else:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30
            )
        
        # 处理输出
        output = result.stdout.strip() or "命令执行成功，无输出"
        error_output = result.stderr.strip()
        
        # 特殊输出处理
        special_outputs = {
            'htop': "Htop需要真实终端环境才能运行\n建议使用SSH连接直接查看",
            'nano': "Nano编辑器需要真实终端环境\n请使用SSH连接进行编辑",
            'vim': "Vim编辑器需要真实终端环境\n请使用SSH连接进行编辑"
        }
        
        if base_cmd in special_outputs and not output:
            output = special_outputs[base_cmd]
        
        # 格式化输出
        max_length = 3000
        if len(output) > max_length:
            output = output[:max_length] + "\n... (输出被截断)"
        
        reply_msg = f"🖥️ 命令: {command}\n📋 输出:\n{output}"
        
        if error_output:
            if len(error_output) > max_length:
                error_output = error_output[:max_length] + "\n... (错误输出被截断)"
            reply_msg += f"\n\n❌ 错误:\n{error_output}"
        
        await update.message.reply_text(reply_msg)
    
    except subprocess.TimeoutExpired:
        await update.message.reply_text(f"⏳ 命令执行超时: {command}")
    except Exception as e:
        await update.message.reply_text(f"⚠️ 执行命令时出错: {str(e)}")
def main() -> None:
    app = Application.builder().token(config.TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CommandHandler("usd", usd_command))
    app.add_handler(CommandHandler("cmd", execute_command)) 
    app.run_polling()

if __name__ == "__main__":
    main()