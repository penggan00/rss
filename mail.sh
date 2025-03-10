#!/bin/bash

# 检查 mail.py 是否在运行
if pgrep -f "mail.py" > /dev/null; then
    echo "mail.py is running. Stopping it..."
    # 停止 mail.py 进程
    pkill -f "mail.py"
else
    echo "mail.py is not running."
fi
# 等待2秒
sleep 1
# 运行 MAIL 脚本
source ~/rss/rss_venv/bin/activate
python3 ~/rss/mail.py &
deactivate