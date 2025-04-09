#!/bin/bash

# 检查rss2.py进程是否在运行
if pgrep -f "rss2.py" > /dev/null; then
    echo "检测到rss2.py正在运行，正在停止该进程..."
    # 终止rss2.py进程
    pkill -f "rss2.py"
fi
# 等待1秒确保进程完全终止
sleep 1
# 启动rss2.py脚本
source ~/rss/rss_venv/bin/activate
nohup python3 ~/rss/rss2.py > /dev/null 2>&1 &

echo "脚本执行成功"