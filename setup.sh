#!/bin/bash

# 严格模式，遇到错误退出
set -euo pipefail

# 配置变量
RSS_DIR="$HOME/rss"
VENV_NAME="rss_venv"
REQUIREMENTS="$RSS_DIR/requirements.txt"

# 进入 rss 目录，失败则退出
cd "$RSS_DIR" || {
    echo "错误：无法进入 $RSS_DIR 目录"
    exit 1
}

# 确保脚本可执行
chmod +x "$RSS_DIR"/*.sh

# 检查是否已安装 Python3
if ! command -v python3 &> /dev/null; then
    echo "错误：Python3 未安装"
    exit 1
fi

# 创建虚拟环境（如果不存在）
if [ ! -d "$VENV_NAME" ]; then
    echo "创建虚拟环境..."
    python3 -m venv "$VENV_NAME"
fi

# 激活虚拟环境
source "$VENV_NAME/bin/activate"

# 安装依赖（严格保持requirements.txt中的版本）
echo "安装依赖..."
if [ -f "$REQUIREMENTS" ]; then
    # 禁用pip版本检查（避免警告干扰）
    pip install --disable-pip-version-check -r "$REQUIREMENTS"
else
    echo "错误：$REQUIREMENTS 文件不存在"
    exit 1
fi

# 验证安装版本
echo -e "\n已安装包版本："
pip freeze | grep -v "pkg-resources"

# 设置定时任务
echo -e "\n设置定时任务..."
add_cron_job() {
    local script_name=$1
    local schedule=$2
    local script_path="$RSS_DIR/$script_name"
    
    if ! crontab -l | grep -q "$script_path"; then
        (crontab -l 2>/dev/null; echo "$schedule /bin/bash $script_path") | crontab -
        echo "已添加: $schedule $script_name"
    else
        echo "已存在: $script_name"
    fi
}

# 添加定时任务（示例）
add_cron_job "mail.sh" "*/5 * * * *"
add_cron_job "rss.sh" "30 */1 * * *"
add_cron_job "call.sh" "20 10 * * *"
add_cron_job "usd.sh" "0 11,15 * * 1-5"

echo -e "\n当前定时任务列表:"
crontab -l

# 环境配置提示
echo -e "\n重要提示："
echo "1. 请确保 .env 文件已正确配置"
echo "2. 依赖版本已严格锁定，如需更新需手动修改 requirements.txt"
echo "3. 虚拟环境路径: $RSS_DIR/$VENV_NAME"

# 询问是否要编辑 .env 文件
read -rp "是否要立即编辑 .env 文件？[y/N] " answer
if [[ "$answer" =~ ^[Yy]$ ]]; then
    "${EDITOR:-nano}" "$RSS_DIR/.env"
fi