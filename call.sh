#!/bin/bash

# 检查 call.py 是否在运行
if pgrep -f "call.py" > /dev/null; then
    echo "call.py is running. Stopping it..."
    # 停止 call.py 进程
    pkill -f "call.py"
else
    echo "call.py is not running."
fi
# 等待1秒
sleep 1
# 运行 call 脚本
source ~/rss/rss_venv/bin/activate
nohup python3 ~/rss/call.py > /dev/null 2>&1 &
# 这里的 deactivate 可能不会被执行，因为 nohup 让 call.py 在后台运行
# deactivate
