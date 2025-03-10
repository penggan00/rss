#!/bin/bash

# 设置环境变量 (如果需要)
# export VARIABLE_NAME="value"

# 进入脚本所在目录
cd /root/rss

# 激活虚拟环境
source rss_venv/bin/activate

# 定义日志文件路径
LOG_FILE="rss_error.log"

# 执行 Python 脚本，并将标准错误输出重定向到日志文件
python3 rss.py 2>> "$LOG_FILE"

# 检查 Python 脚本的退出状态码
if [ $? -ne 0 ]; then
  echo "$(date) - 脚本执行失败，请查看错误日志: $LOG_FILE" >> rss.log
else
  echo "$(date) - 脚本执行完毕" >> rss.log
fi

# 退出虚拟环境 (可选)
deactivate
