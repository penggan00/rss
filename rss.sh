#!/bin/bash

# 检查rss.py进程是否在运行
if pgrep -f "rss.py" > /dev/null; then
    echo "检测到rss.py正在运行，正在停止该进程..."
    # 终止rss.py进程
    pkill -f "rss.py"
fi
# 等待2秒确保进程完全终止
sleep 2
# 启动rss.py脚本
source ~/rss/rss_venv/bin/activate
nohup python3 ~/rss/rss.py > /dev/null 2>&1 &

echo "脚本执行成功"