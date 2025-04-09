#!/bin/bash

# 检查mail.py进程是否在运行
if pgrep -f "mail.py" > /dev/null; then
    echo "检测到mail.py正在运行，正在停止该进程..."
    # 终止mail.py进程
    pkill -f "mail.py"
fi
# 等待1秒确保进程完全终止
sleep 1
# 启动mail.py脚本
source ~/rss/rss_venv/bin/activate
nohup python3 ~/rss/mail.py > /dev/null 2>&1 &

echo "脚本执行成功"