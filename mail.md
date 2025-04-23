pip install html2text chardet python-dotenv google-generativeai md2tgmd
pip install python-telegram-bot

chmod +x ~/rss/mail.sh

crontab -e

# 创建虚拟环境
python3 -m venv rss_venv
# 激活虚拟环境
source rss_venv/bin/activate
python3 mail.py

*/5 * * * * /bin/bash ~/rss/mail.sh
