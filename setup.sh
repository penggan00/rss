#!/bin/bash

echo "进入 rss 目录"
cd ~/rss || exit

chmod +x /root/rss/{rss.sh,usd.sh,mail.sh，call.sh,rss.log.sh}

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
#(crontab -l | grep -q '~/rss/rss.py') || (crontab -l; echo "*/10 * * * * /bin/bash ~/rss/rss.sh") | crontab -
#(crontab -l | grep -q '~/rss/rss.py') || (crontab -l; echo "0,10,20,30,40,50 * * * * /bin/bash ~/rss/rss.sh") | crontab -
#(crontab -l | grep -q '~/rss/rss.py') || (crontab -l; echo "5,15,25,35,45,55 * * * * /bin/bash ~/rss/rss.sh") | crontab -
#(crontab -l | grep -q '~/rss/call.py') || (crontab -l; echo "20 10 * * * /bin/bash ~/rss/call.sh") | crontab -
#(crontab -l | grep -q '~/rss/usa.py') || (crontab -l; echo "30 06,15,23 * * 1-5 /bin/bash ~/rss/usd.sh") | crontab -
#(crontab -l | grep -q '~/rss/usa.py') || (crontab -l; echo "30 06 * * 6-7 /bin/bash ~/rss/usd.sh") | crontab -
#(crontab -l | grep -q '~/rss/rss.log.sh') || (crontab -l; echo "0 1 * * * /bin/bash ~/rss/rss.log.sh") | crontab -

echo "2秒钟后增加/rss/.env"
sleep 2
nano ~/rss/.env
sleep 1
echo "2秒钟后增加/rss/usd.py"
sleep 2
nano ~/rss/usd.py
echo "可以使用命令：crontab -e 查看定时任务"
echo "可以使用命令：source ~/rss/rss_venv/bin/activate 激活虚拟环境"
echo "可以使用命令：deactivate 退出虚拟环境"
echo "可以使用命令：python ~/rss/rss.py 手动运行rss"
echo "可以使用命令：python ~/rss/usd.py 手动运行usd"
