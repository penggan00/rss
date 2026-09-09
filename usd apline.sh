#!/bin/bash
#(crontab -l 2>/dev/null | grep -v "usd.sh"; echo "10 06,16,23 * * 1-5 /bin/bash /root/rss/usd.sh > /dev/null 2>&1"; echo "10 06 * * 6-7 /bin/bash /root/rss/usd.sh > /dev/null 2>&1") | crontab -
# 检查usd.py进程是否在运行
if pgrep -f "usd.py" > /dev/null; then
    echo "检测到usd.py正在运行，正在停止该进程..."
    pkill -f "usd.py"
fi

sleep 2

# 启动usd.py脚本
/root/rss/rss_venv/bin/python /root/rss/usd.py

echo "脚本执行成功"