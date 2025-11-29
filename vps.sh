#!/bin/bash

# 检查vps.py进程是否在运行
if pgrep -f "vps.py" > /dev/null; then
    echo "检测到vps.py正在运行，正在停止该进程..."
    # 终止vps.py进程
    pkill -f "vps.py"
fi
# 等待2秒确保进程完全终止
sleep 2
# 启动vps.py脚本
source ~/rss/rss_venv/bin/activate
nohup python3 ~/rss/vps.py > /dev/null 2>&1 &

echo "脚本执行成功"