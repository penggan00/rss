#!/bin/sh

if pgrep -f "usd.py" > /dev/null; then
    echo "检测到 usd.py 正在运行，正在停止..."
    pkill -f "usd.py"
fi

sleep 1

nohup /root/rss/rss_venv/bin/python /root/rss/usd.py > /dev/null 2>&1 &

echo "脚本执行成功"