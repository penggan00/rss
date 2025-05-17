0 11,16,23 * * * /bin/bash ~/rss/usd.sh
chmod +x ~/rss/usd.sh
chmod +x ~/rss/ss.sh
crontab -e

# 创建虚拟环境
python3 -m venv rss_venv
# 激活虚拟环境
source rss_venv/bin/activate
python3 usd.py

30 09,15,23 * * 1-5 /bin/bash ~/rss/usd.sh
30 09 * * 6-7 /bin/bash ~/rss/usd.sh