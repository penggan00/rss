#!/bin/bash

echo "进入 rss 目录"
cd ~/rss || exit

chmod +x /root/rss/{rss.sh,call.sh,usd.sh,mail.sh,rss2.sh,tt.sh}

echo "创建虚拟环境！"
python3 -m venv rss_venv
sleep 1
echo "激活虚拟环境！"
source rss_venv/bin/activate
sleep 1
echo "安装 requirements.txt 中的库！"
# 安装 requirements.txt 中的库
pip install -r ~/rss/requirements.txt

echo "设置定时任务crontab -e"
# 检查是否已存在对应的 crontab 任务
# (crontab -l | grep -q '~/rss/mail.py') || (crontab -l; echo "*/5 * * * * /bin/bash ~/rss/mail.sh") | crontab -
(crontab -l | grep -q '~/rss/rss.py') || (crontab -l; echo "*/20 * * * * /bin/bash ~/rss/rss.sh") | crontab -
#(crontab -l | grep -q '~/rss/call.py') || (crontab -l; echo "20 10 * * * /bin/bash ~/rss/call.sh") | crontab -
(crontab -l | grep -q '~/rss/usa.py') || (crontab -l; echo "0 11,16,23 * * * /bin/bash ~/rss/usd.sh") | crontab -

echo "增加.env"
nano ~/rss/.env

echo "设置完成！"
