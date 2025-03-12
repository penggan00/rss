0 11,15 * * * /bin/bash ~/rss/usd.sh
chmod +x ~/rss/usd.sh

crontab -e

# 创建虚拟环境
python3 -m venv rss_venv
# 激活虚拟环境
source rss_venv/bin/activate
python3 usd.py