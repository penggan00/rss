#!/bin/sh

if pgrep -f "vps.py" > /dev/null; then
    echo "检测到 vps.py 正在运行，正在停止..."
    pkill -f "vps.py"
fi

sleep 1

nohup /root/rss/rss_venv/bin/python /root/rss/vps.py > /dev/null 2>&1 &

echo "脚本执行成功"