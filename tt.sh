#!/bin/bash

# 检查 tt.py 是否在运行
if pgrep -f "tt.py" > /dev/null; then
    echo "tt.py is running. Stopping it..."
    # 停止 tt.py 进程
    pkill -f "tt.py"
else
    echo "tt.py is not running."
fi
# 等待1秒
sleep 1
# 运行 tt 脚本
source ~/rss/rss_venv/bin/activate
nohup python3 ~/rss/tt.py > /dev/null 2>&1 &
# 这里的 deactivate 可能不会被执行，因为 nohup 让 tt.py 在后台运行
# deactivate
