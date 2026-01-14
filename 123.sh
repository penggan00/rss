#!/usr/bin/env bash
# fix-nginx-log-dirs.sh
# 一键修复 nginx 启动时因日志/运行目录不存在导致的错误（Alpine/Debian 兼容）
#
# 用法:
#   sudo bash fix-nginx-log-dirs.sh
# 可选（若你想把 error_log 指向 /dev/null，请设置环境变量）:
#   sudo REDIRECT_ERROR_TO_NULL=1 bash fix-nginx-log-dirs.sh
#
set -euo pipefail
IFS=$'\n\t'

TIMESTAMP="$(date +%Y%m%d%H%M%S)"
NGINX_CONF="/etc/nginx/nginx.conf"
BACKUP_DIR="/root/nginx_fix_backups_${TIMESTAMP}"
REDIRECT_ERROR_TO_NULL="${REDIRECT_ERROR_TO_NULL:-0}"

# Ensure running as root
if [ "$(id -u)" -ne 0 ]; then
  echo "请以 root 用户运行此脚本。"
  exit 1
fi

mkdir -p "$BACKUP_DIR"

echo "备份与环境检测..."
if [ -f "$NGINX_CONF" ]; then
  cp -a "$NGINX_CONF" "${BACKUP_DIR}/nginx.conf.bak" || true
  echo "已备份 $NGINX_CONF -> ${BACKUP_DIR}/nginx.conf.bak"
else
  echo "未找到 $NGINX_CONF（继续：可能尚未安装或路径不同）"
fi

# Detect nginx run user from config
NGINX_USER=""
NGINX_GROUP=""
if [ -f "$NGINX_CONF" ]; then
  # get first 'user' directive
  USERLINE=$(sed -n 's/^[ \t]*user[ \t]\+\([a-zA-Z0-9._-]\+\).*;/\1/p' "$NGINX_CONF" | head -n1 || true)
  if [ -n "$USERLINE" ]; then
    NGINX_USER="$USERLINE"
  fi
fi

# Fallback candidates
if [ -z "$NGINX_USER" ]; then
  if getent passwd nginx >/dev/null 2>&1; then
    NGINX_USER=nginx
  elif getent passwd www-data >/dev/null 2>&1; then
    NGINX_USER=www-data
  else
    # fallback to current user (root) — but prefer creating dirs owned by root if no nginx user exists
    NGINX_USER=root
  fi
fi

# try to get group
if getent passwd "$NGINX_USER" >/dev/null 2>&1; then
  NGINX_GROUP=$(getent passwd "$NGINX_USER" | cut -d: -f4)
  # if group number returned, try to convert to name
  if ! getent group "$NGINX_GROUP" >/dev/null 2>&1; then
    # not a name, try to find primary group name
    NGINX_GROUP=$(getent passwd "$NGINX_USER" | awk -F: '{printf $4}' | awk '{ print $1 }')
    # convert gid to group name
    if getent group "$NGINX_GROUP" >/dev/null 2>&1; then
      NGINX_GROUP_NAME="$(getent group "$NGINX_GROUP" | cut -d: -f1)"
      NGINX_GROUP="$NGINX_GROUP_NAME"
    else
      # fallback to user name
      NGINX_GROUP="$NGINX_USER"
    fi
  fi
else
  # user not found: fallback to root
  NGINX_USER=root
  NGINX_GROUP=root
fi

echo "检测到 nginx 运行用户: ${NGINX_USER}:${NGINX_GROUP}"

# Directories & files to ensure
DIRS=(
  "/var/log/nginx"
  "/var/lib/nginx/logs"
  "/var/lib/nginx"
  "/run/nginx"
)

FILES=(
  "/var/log/nginx/error.log"
  "/var/log/nginx/access.log"
  "/var/lib/nginx/logs/error.log"
)

echo "创建缺失目录与日志文件..."
for d in "${DIRS[@]}"; do
  if [ ! -d "$d" ]; then
    mkdir -p "$d"
    echo "已创建目录: $d"
  else
    echo "目录已存在: $d"
  fi
done

for f in "${FILES[@]}"; do
  if [ ! -f "$f" ]; then
    touch "$f"
    echo "已创建文件: $f"
  else
    echo "文件已存在: $f"
  fi
done

# Set ownership and permissions (safe defaults)
echo "设置权限与归属..."
# If nginx user exists, use it; otherwise use root
if getent passwd "$NGINX_USER" >/dev/null 2>&1; then
  chown -R "${NGINX_USER}:${NGINX_GROUP}" /var/log/nginx /var/lib/nginx /run/nginx || true
else
  chown -R root:root /var/log/nginx /var/lib/nginx /run/nginx || true
fi

chmod 750 /var/log/nginx /var/lib/nginx /run/nginx || true
chmod 640 /var/log/nginx/*.log /var/lib/nginx/logs/*.log || true

echo "已设置：/var/log/nginx 和 /var/lib/nginx/logs 的所有权与权限"

# Optional: redirect error_log to /dev/null if user set env var
if [ "${REDIRECT_ERROR_TO_NULL:-0}" != "0" ]; then
  if [ -f "$NGINX_CONF" ]; then
    cp -a "$NGINX_CONF" "${BACKUP_DIR}/nginx.conf.errorlog.bak"
    # replace error_log directives to /dev/null
    # This will replace lines like: error_log /path/to/file level;
    sed -E -i 's@^[[:space:]]*error_log[[:space:]]+[^[:space:]]+[[:space:]]+([a-zA-Z0-9_]+;|[a-zA-Z0-9_]+;|;|.+;)$@error_log /dev/null crit;@g' "$NGINX_CONF" || true
    echo "已将 $NGINX_CONF 中的 error_log 指向 /dev/null（备份在 ${BACKUP_DIR}）"
  else
    echo "未找到 nginx 配置文件，跳过 error_log 重定向。"
  fi
fi

# Test nginx config
echo "检测 nginx 配置语法..."
NGINX_TEST_OK=0
if command -v nginx >/dev/null 2>&1; then
  if nginx -t 2>&1 | tee "${BACKUP_DIR}/nginx_test_output.txt"; then
    echo "nginx -t 检查通过"
    NGINX_TEST_OK=1
  else
    echo "nginx -t 检查未通过，详情见 ${BACKUP_DIR}/nginx_test_output.txt"
  fi
else
  echo "未检测到 nginx 可执行文件 (command -v nginx)，请确认 nginx 是否已安装。"
fi

# Restart nginx service (support alpine openrc or systemd)
echo "尝试重启 nginx 服务..."
RESTART_OK=0
if command -v rc-service >/dev/null 2>&1; then
  # Alpine openrc
  if rc-service nginx restart >/dev/null 2>&1; then
    echo "使用 rc-service 成功重启 nginx（OpenRC）"
    RESTART_OK=1
  else
    echo "使用 rc-service 重启 nginx 失败，请手动查看日志或运行: rc-service nginx restart"
  fi
elif command -v systemctl >/dev/null 2>&1; then
  if systemctl restart nginx >/dev/null 2>&1; then
    echo "使用 systemctl 成功重启 nginx (systemd)"
    RESTART_OK=1
  else
    echo "使用 systemctl 重启 nginx 失败，请手动检查: systemctl status nginx"
  fi
else
  # fallback: try direct nginx start
  if command -v nginx >/dev/null 2>&1; then
    if nginx >/dev/null 2>&1; then
      echo "直接启动 nginx 成功"
      RESTART_OK=1
    else
      echo "直接启动 nginx 失败，请手动运行 nginx -t 查看错误"
    fi
  fi
fi

echo
echo "=== 操作总结 ==="
echo "备份目录: $BACKUP_DIR"
echo "确保的目录: ${DIRS[*]}"
echo "确保的文件: ${FILES[*]}"
echo "nginx 配置语法检测: $( [ "$NGINX_TEST_OK" -eq 1 ] && echo '通过' || echo '未通过' )"
echo "nginx 重启: $( [ "$RESTART_OK" -eq 1 ] && echo '成功' || echo '失败' )"

if [ "$RESTART_OK" -ne 1 ]; then
  echo
  echo "可能的后续排查步骤（按顺序）："
  echo " 1) 查看当前哪个进程占用 80/443： sudo ss -ltnp | egrep ':80|:443' 或 sudo netstat -ltnp | egrep ':80|:443'"
  echo " 2) 查看 nginx 错误详情： sudo nginx -t 或查看 ${BACKUP_DIR}/nginx_test_output.txt"
  echo " 3) 若希望仅监听 IPv6，请更新站点配置为 listen [::]:80 ipv6only=on; 并在 proxy_pass 对 IPv6 字面量使用方括号"
  echo " 4) 若想把错误日志指向 /dev/null 再运行： sudo REDIRECT_ERROR_TO_NULL=1 bash $0"
  echo
fi

echo "完成。若需要我自动为你把站点配置改为 IPv6-only 或把 proxy_pass 的 IPv6 后端地址自动用 [] 包起来，请回复“改为 IPv6-only”或粘贴 nginx -t 的输出/ss -ltnp 输出给我。"