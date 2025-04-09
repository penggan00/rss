#!/bin/bash

# 检查call.py进程是否在运行
if pgrep -f "call.py" > /dev/null; then
    echo "检测到call.py正在运行，正在停止该进程..."
    # 终止call.py进程
    pkill -f "call.py"
fi
# 等待1秒确保进程完全终止
sleep 1
# 启动call.py脚本
source ~/rss/rss_venv/bin/activate
nohup python3 ~/rss/call.py > /dev/null 2>&1 &

echo "脚本执行成功"