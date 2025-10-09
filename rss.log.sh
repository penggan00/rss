#!/bin/bash

# ==============================================
# 配置部分
# ==============================================
ENV_FILE="$HOME/rss/.env"
LOG_FILE="$HOME/rss/rss.log"
MAX_MESSAGE_LENGTH=4000  # Telegram消息长度限制
TIMESTAMP_FILE="$HOME/rss/last_send.timestamp"  # 记录上次发送时间
ARCHIVE_DIR="$HOME/rss/log_archive"  # 日志归档目录

# ==============================================
# 初始化检查
# ==============================================

# 检查.env文件
if [ ! -f "$ENV_FILE" ]; then
    echo "❌ 错误: 未找到.env文件: $ENV_FILE" >&2
    exit 1
fi

# 加载环境变量
while IFS= read -r line; do
    if [[ "$line" =~ ^[^#]*= ]]; then
        export "$line"
    fi
done < "$ENV_FILE"

# 检查必要变量
if [ -z "$TELEGRAM_API_KEY" ]; then
    echo "❌ 错误: TELEGRAM_API_KEY 未设置" >&2
    exit 1
fi

if [ -z "$TELEGRAM_CHAT_ID" ]; then
    echo "❌ 错误: TELEGRAM_CHAT_ID 未设置" >&2
    exit 1
fi

# 创建归档目录
mkdir -p "$ARCHIVE_DIR"

# ==============================================
# 日志内容检查
# ==============================================

# 检查日志是否为空
if [ ! -s "$LOG_FILE" ]; then
    echo "ℹ️ 日志文件为空，跳过发送"
    exit 0
fi

# 获取上次发送时间（如果文件不存在则默认为1970年）
LAST_SEND=$(stat -c %Y "$TIMESTAMP_FILE" 2>/dev/null || echo 0)

# 获取日志最后修改时间
LAST_MODIFIED=$(stat -c %Y "$LOG_FILE")

# 检查是否有新内容
if [ "$LAST_MODIFIED" -le "$LAST_SEND" ]; then
    echo "ℹ️ 日志没有新内容，跳过发送"
    exit 0
fi

# ==============================================
# 日志处理
# ==============================================

# 1. 备份当前日志内容
LOG_CONTENT=$(cat "$LOG_FILE")
ARCHIVE_FILE="$ARCHIVE_DIR/rss_$(date +%Y%m%d_%H%M%S).log"
echo "$LOG_CONTENT" > "$ARCHIVE_FILE"
echo "📦 日志已归档到: $ARCHIVE_FILE"

# 2. 准备发送内容
CURRENT_TIME=$(date '+%Y-%m-%d %H:%M:%S')
MESSAGE_HEADER="🔄 RSS日志更新 ($CURRENT_TIME)"
FULL_MESSAGE="$MESSAGE_HEADER\n\n$LOG_CONTENT"

# 3. 处理消息长度限制
if [ ${#FULL_MESSAGE} -gt $MAX_MESSAGE_LENGTH ]; then
    TRUNCATED_MSG="${FULL_MESSAGE:0:$MAX_MESSAGE_LENGTH}"
    FULL_MESSAGE="${TRUNCATED_MSG}\n...[消息过长被截断]"
fi

# ==============================================
# Telegram发送函数（带重试机制）
# ==============================================
send_to_telegram() {
    local chat_id="$1"
    local text="$2"
    local attempt=0
    local max_attempts=3
    local delay=2
    
    while [ $attempt -lt $max_attempts ]; do
        response=$(curl -s -X POST \
            "https://api.telegram.org/bot${TELEGRAM_API_KEY}/sendMessage" \
            -d chat_id="${chat_id}" \
            -d text="${text}" \
            -d disable_notification="true" \
            -w "\n%{http_code}")
        
        http_code=$(echo "$response" | tail -n1)
        response_content=$(echo "$response" | head -n1)
        
        if [ "$http_code" -eq 200 ]; then
            echo "✅ 成功发送到Chat ID ${chat_id}"
            return 0
        else
            attempt=$((attempt + 1))
            echo "⚠️ 尝试 ${attempt}/${max_attempts} 失败 (HTTP ${http_code})"
            echo "错误响应: $response_content"
            sleep $delay
        fi
    done
    
    echo "❌ 发送到Chat ID ${chat_id} 最终失败"
    return 1
}

# ==============================================
# 主执行逻辑
# ==============================================
echo "=== 开始处理RSS日志 ==="
echo "📅 当前时间: $CURRENT_TIME"
echo "📜 日志文件: $LOG_FILE"
echo "🔄 最后修改时间: $(date -d @$LAST_MODIFIED '+%Y-%m-%d %H:%M:%S')"
echo "⏱️ 上次发送时间: $(date -d @$LAST_SEND '+%Y-%m-%d %H:%M:%S')"

# 分割Chat ID列表
IFS=',' read -ra CHAT_IDS <<< "$TELEGRAM_CHAT_ID"

# 发送状态标志
ALL_SENT_SUCCESSFULLY=true

# 逐个发送
for chat_id in "${CHAT_IDS[@]}"; do
    chat_id=$(echo "$chat_id" | xargs)  # 去除空格
    echo "--- 发送给Chat ID: $chat_id ---"
    
    if ! send_to_telegram "$chat_id" "$FULL_MESSAGE"; then
        echo "⚠️ 尝试发送精简版本..."
        SHORT_MESSAGE="$MESSAGE_HEADER\n\n[日志内容摘要]\n${LOG_CONTENT:0:1000}...[完整内容请查看服务器日志]"
        if ! send_to_telegram "$chat_id" "$SHORT_MESSAGE"; then
            ALL_SENT_SUCCESSFULLY=false
        fi
    fi
    
    sleep 1  # 避免速率限制
done

# 只有所有发送都成功时才清空日志
if $ALL_SENT_SUCCESSFULLY; then
    echo "♻️ 清空日志文件..."
    > "$LOG_FILE"
    date +%s > "$TIMESTAMP_FILE"
    echo "✅ 日志已发送并清空"
else
    echo "⚠️ 部分发送失败，保留日志文件"
fi

echo "=== 处理完成 ==="