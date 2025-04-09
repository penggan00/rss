#!/bin/bash

# 检查tt.py进程是否在运行
if pgrep -f "tt.py" > /dev/null; then
    echo "检测到tt.py正在运行，正在停止该进程..."
    # 终止tt.py进程
    pkill -f "tt.py"
fi
# 等待1秒确保进程完全终止
sleep 1
# 启动tt.py脚本
source ~/rss/rss_venv/bin/activate
nohup python3 ~/rss/tt.py > /dev/null 2>&1 &

echo "脚本执行成功"