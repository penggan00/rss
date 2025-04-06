0 15 * * /bin/bash ~/rss/tt.sh
chmod +x ~/rss/tt.sh

crontab -e

# 创建虚拟环境
python3 -m venv rss_venv
# 激活虚拟环境
source rss_venv/bin/activate
python3 tt.py