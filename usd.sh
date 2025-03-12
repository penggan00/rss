#!/bin/bash

# 检查 usd.py 是否在运行
if pgrep -f "usd.py" > /dev/null; then
    echo "usd.py is running. Stopping it..."
    # 停止 usd.py 进程
    pkill -f "usd.py"
else
    echo "usd.py is not running."
fi
# 等待1秒
sleep 1
# 运行 usd 脚本
source ~/rss/rss_venv/bin/activate
nohup python3 ~/rss/usd.py > /dev/null 2>&1 &
# 这里的 deactivate 可能不会被执行，因为 nohup 让 usd.py 在后台运行
# deactivate
