#!/bin/bash

# 检查usd.py进程是否在运行
if pgrep -f "usd.py" > /dev/null; then
    echo "检测到usd.py正在运行，正在停止该进程..."
    # 终止usd.py进程
    pkill -f "usd.py"
fi
# 等待2秒确保进程完全终止
sleep 2
# 启动usd.py脚本
source ~/rss/rss_venv/bin/activate
nohup python3 ~/rss/usd.py > /dev/null 2>&1 &

echo "脚本执行成功"