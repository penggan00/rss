#!/bin/sh

if pgrep -f "rss.py" > /dev/null; then
    echo "检测到 rss.py 正在运行，正在停止..."
    pkill -f "rss.py"
fi

sleep 1

nohup /root/rss/rss_venv/bin/python /root/rss/rss.py > /dev/null 2>&1 &

echo "脚本执行成功"