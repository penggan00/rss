#!/usr/bin/env bash
#
# nginx-smart-reverse-proxy-final.sh
# 终极稳定版（保留错误日志）：
# - 兼容 Alpine (OpenRC) 与 Debian (systemd)
# - 选择 1=本地 IPv4 (127.0.0.1) 或 2=本地 IPv6 (::1)（不需手动输入 IP）
# - 支持可选 WebSocket 1.1
# - 自动处理 IPv6 字面量（proxy_pass 中自动用 [::1] 格式）
# - 每次运行：若 nginx 未运行尝试启动；若已运行则重载（不论 80/443 是否被占用）
# - 保留错误日志（/var/log/nginx/error.log），access_log 默认关闭
#
# 使用：以 root 运行
#   sudo bash nginx-smart-reverse-proxy-final.sh
#
set -euo pipefail
IFS=$'\n\t'

YELLOW="\033[1;33m"
GREEN="\033[1;32m"
RED="\033[1;31m"
NC="\033[0m"

info() { echo -e "${GREEN}$*${NC}"; }
warn() { echo -e "${YELLOW}$*${NC}"; }
die()  { echo -e "${RED}$*${NC}" >&2; exit 1; }

ACME_DIR="${ACME_DIR:-/root/.acme.sh}"

# require root
[ "$(id -u)" -eq 0 ] || die "请以 root 或 sudo 运行此脚本。"

detect_os() {
  if [ -f /etc/os-release ]; then . /etc/os-release; fi
  if echo "${ID:-} ${ID_LIKE:-}" | grep -qi alpine; then DISTRO=alpine
  elif echo "${ID:-} ${ID_LIKE:-}" | grep -Ei "debian|ubuntu|mint" >/dev/null 2>&1; then DISTRO=debian
  else
    if command -v apk >/dev/null 2>&1; then DISTRO=alpine
    elif command -v apt-get >/dev/null 2>&1; then DISTRO=debian
    else die "不支持的发行版，脚本仅适配 Alpine/Debian 系列。"
    fi
  fi
  info "检测到发行版: $DISTRO"
}

install_nginx_and_tools() {
  info "安装 nginx 与必要工具..."
  if [ "$DISTRO" = "alpine" ]; then
    apk update
    apk add --no-cache nginx openssl curl bash coreutils procps
    mkdir -p /run/nginx
  else
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y
    apt-get install -y nginx openssl curl ca-certificates lsb-release procps
  fi
  command -v nginx >/dev/null 2>&1 || die "nginx 安装失败，请检查包管理器输出。"
  info "nginx 已安装"
}

ensure_dirs_and_logs() {
  info "确保日志与运行目录存在并有合适权限..."
  mkdir -p /var/log/nginx /var/lib/nginx/logs /run/nginx
  touch /var/log/nginx/error.log /var/log/nginx/access.log /var/lib/nginx/logs/error.log || true

  # detect nginx user from config; fallback to nginx/www-data/root
  NGINX_USER="nginx"
  if [ -f /etc/nginx/nginx.conf ]; then
    USERLINE=$(sed -n 's/^[ \t]*user[ \t]\+\([a-zA-Z0-9._-]\+\).*;/\1/p' /etc/nginx/nginx.conf | head -n1 || true)
    [ -n "$USERLINE" ] && NGINX_USER="$USERLINE"
  fi
  if getent passwd "$NGINX_USER" >/dev/null 2>&1; then
    NGINX_GROUP=$(getent passwd "$NGINX_USER" | cut -d: -f4)
    if ! getent group "$NGINX_GROUP" >/dev/null 2>&1; then NGINX_GROUP="$NGINX_USER"; fi
  else
    NGINX_USER=root
    NGINX_GROUP=root
  fi

  chown -R "${NGINX_USER}:${NGINX_GROUP}" /var/log/nginx /var/lib/nginx /run/nginx || true
  chmod 750 /var/log/nginx /var/lib/nginx /run/nginx || true
  chmod 640 /var/log/nginx/*.log /var/lib/nginx/logs/*.log || true

  info "已创建并设置 /var/log/nginx, /var/lib/nginx/logs, /run/nginx"
}

install_certificate() {
  [ -n "${DOMAIN:-}" ] || return
  info "尝试使用 acme.sh 安装证书（若存在），否则假设您已手动放置证书到 /etc/nginx/ssl/${DOMAIN}.crt 与 .key"
  mkdir -p "/etc/nginx/ssl/certs/${DOMAIN}" "/etc/nginx/ssl/private/${DOMAIN}" "/etc/nginx/ssl"
  if [ -x "${ACME_DIR}/acme.sh" ]; then
    (cd "$ACME_DIR" && ./acme.sh --install-cert -d "$DOMAIN" \
      --key-file "/etc/nginx/ssl/private/${DOMAIN}/key.pem" \
      --fullchain-file "/etc/nginx/ssl/certs/${DOMAIN}/fullchain.pem" \
      --cert-file "/etc/nginx/ssl/certs/${DOMAIN}/cert.pem" \
      --ca-file "/etc/nginx/ssl/certs/${DOMAIN}/ca.pem" \
      --reloadcmd "echo '证书安装完成'") || warn "acme.sh --install-cert 返回非零"
  fi
  if [ -f "/etc/nginx/ssl/certs/${DOMAIN}/fullchain.pem" ] && [ -f "/etc/nginx/ssl/private/${DOMAIN}/key.pem" ]; then
    ln -sf "/etc/nginx/ssl/certs/${DOMAIN}/fullchain.pem" "/etc/nginx/ssl/${DOMAIN}.crt"
    ln -sf "/etc/nginx/ssl/private/${DOMAIN}/key.pem" "/etc/nginx/ssl/${DOMAIN}.key"
    info "证书已链接到 /etc/nginx/ssl/${DOMAIN}.{crt,key}"
  else
    warn "未检测到自动安装的证书，请确保 /etc/nginx/ssl/${DOMAIN}.crt 与 /etc/nginx/ssl/${DOMAIN}.key 存在"
  fi
}

generate_smart_tune() {
  SAFE_INC="/etc/nginx/conf.d/_smart_tune.conf"
  if [ ! -f "$SAFE_INC" ]; then
    cat >"$SAFE_INC" <<'EOF'
# safe tuning defaults (generated)
worker_processes auto;
events {
    worker_connections 10240;
    use epoll;
}
http {
    server_tokens off;
    sendfile on;
    tcp_nopush on;
    tcp_nodelay on;
    keepalive_timeout 65;
    types_hash_max_size 2048;
    client_max_body_size 100m;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;
}
EOF
    info "已写入 $SAFE_INC"
  fi
}

# normalize backend: bracket IPv6 literal if present
backend_uri() {
  local host="$1" port="$2"
  if echo "$host" | grep -q ":"; then
    echo "http://[${host}]:${port}"
  else
    echo "http://${host}:${port}"
  fi
}

generate_proxy_conf() {
  CONF_DIR="/etc/nginx/conf.d"
  mkdir -p "$CONF_DIR"
  CONF_FILE="${CONF_DIR}/reverse_proxy_${DOMAIN}.conf"
  [ -f "$CONF_FILE" ] && cp -a "$CONF_FILE" "${CONF_FILE}.bak.$(date +%s)" || true

  LISTEN_HTTP="listen 80;"
  LISTEN_HTTP_V6="listen [::]:80;"
  LISTEN_HTTPS="listen 443 ssl http2;"
  LISTEN_HTTPS_V6="listen [::]:443 ssl http2;"

  SSL_CRT="/etc/nginx/ssl/${DOMAIN}.crt"
  SSL_KEY="/etc/nginx/ssl/${DOMAIN}.key"

  ERROR_LOG_CONF="error_log /var/log/nginx/error.log crit;"

  if [[ "${ENABLE_WS,,}" = "y" || "${ENABLE_WS,,}" = "yes" ]]; then
    WS_MAP_BLOCK='map $http_upgrade $connection_upgrade {
    default upgrade;
    "" close;
}'
    PROXY_WS_HEADERS='        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;'
  else
    WS_MAP_BLOCK=''
    PROXY_WS_HEADERS=''
  fi

  cat >"$CONF_FILE" <<EOF
# reverse proxy for ${DOMAIN} (generated)
$WS_MAP_BLOCK

server {
    $LISTEN_HTTP
    $LISTEN_HTTP_V6
    server_name ${DOMAIN};
    return 301 https://\$host\$request_uri;
}

server {
    $LISTEN_HTTPS
    $LISTEN_HTTPS_V6
    server_name ${DOMAIN};

    ssl_certificate ${SSL_CRT};
    ssl_certificate_key ${SSL_KEY};
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    access_log off;
    ${ERROR_LOG_CONF}

    add_header X-Frame-Options SAMEORIGIN;
    add_header X-Content-Type-Options nosniff;

    location / {
        proxy_pass ${BACKEND};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Forwarded-Host \$host;
        proxy_set_header X-Forwarded-Port \$server_port;

        proxy_buffering off;
        proxy_connect_timeout 60s;
        proxy_send_timeout 180s;
        proxy_read_timeout 360s;
${PROXY_WS_HEADERS}
    }
}
EOF

  info "已生成站点配置： $CONF_FILE"
}

test_and_reload_nginx() {
  if ! nginx -t 2>&1 | tee /tmp/nginx_test_out.$$; then
    cat /tmp/nginx_test_out.$$
    rm -f /tmp/nginx_test_out.$$
    die "nginx -t 检查失败，请修正输出中的错误后重试。"
  fi
  rm -f /tmp/nginx_test_out.$$
  info "nginx 配置语法检查通过"

  if pgrep -x nginx >/dev/null 2>&1; then
    warn "检测到 nginx 已在运行，尝试重载..."
    if command -v rc-service >/dev/null 2>&1; then
      rc-service nginx reload 2>&1 || rc-service nginx restart 2>&1 || warn "rc-service reload/restart 返回非0"
    elif command -v systemctl >/dev/null 2>&1; then
      systemctl reload nginx 2>&1 || systemctl restart nginx 2>&1 || warn "systemctl reload/restart 返回非0"
    else
      nginx -s reload 2>&1 || warn "nginx -s reload 返回非0"
    fi
    info "已尝试重载 nginx"
  else
    info "nginx 未运行，尝试启动..."
    if command -v rc-service >/dev/null 2>&1; then
      if rc-service nginx start 2>&1; then info "nginx 已启动 (OpenRC)"; else warn "rc-service start 失败，尝试重载/重启"; rc-service nginx restart 2>&1 || true; fi
    elif command -v systemctl >/dev/null 2>&1; then
      if systemctl start nginx 2>&1; then info "nginx 已启动 (systemd)"; else warn "systemctl start 失败，尝试重载/重启"; systemctl restart nginx 2>&1 || true; fi
    else
      if nginx >/dev/null 2>&1; then info "nginx 直接启动成功"; else warn "直接启动 nginx 失败，检查端口占用或配置"; fi
    fi
  fi
}

# ---------------- main ----------------
detect_os
install_nginx_and_tools
ensure_dirs_and_logs
generate_smart_tune

read -rp "请输入用于反代的子域名 (如 sub.example.com)： " DOMAIN
[ -n "$DOMAIN" ] || die "域名不能为空。"

read -rp "请输入后端端口 (例如 8080)： " TARGET_PORT
echo "$TARGET_PORT" | grep -qE '^[0-9]+$' || die "端口必须为数字"

echo "选择后端 IP 源（脚本将自动使用本地地址）："
echo " 1) 本地 IPv4 (127.0.0.1)"
echo " 2) 本地 IPv6 (::1)"
read -rp "请选择 1 或 2（默认 1）: " IP_CHOICE
IP_CHOICE="${IP_CHOICE:-1}"
if [[ "$IP_CHOICE" =~ ^2$ ]]; then TARGET_HOST="::1"; else TARGET_HOST="127.0.0.1"; fi

read -rp "是否开启 WebSocket 1.1 支持？ (y/N)： " ENABLE_WS
ENABLE_WS="${ENABLE_WS:-n}"

info "将为 ${DOMAIN} 反代到 ${TARGET_HOST}:${TARGET_PORT} （WebSocket: ${ENABLE_WS}）"

BACKEND=$(backend_uri "$TARGET_HOST" "$TARGET_PORT")

# backup main config
[ -f /etc/nginx/nginx.conf ] && cp -a /etc/nginx/nginx.conf /etc/nginx/nginx.conf.backup.$(date +%s) || true

# install cert if possible and link
install_certificate

# generate proxy conf and apply
generate_proxy_conf
test_and_reload_nginx

# final notes and tests
info "完成：域名 ${DOMAIN} 已配置反代到 ${TARGET_HOST}:${TARGET_PORT}"
if [[ "${ENABLE_WS,,}" = "y" || "${ENABLE_WS,,}" = "yes" ]]; then info "WebSocket 支持：已启用"; else info "WebSocket 支持：未启用"; fi

echo
echo "本地连通性测试（在本机运行）:"
if echo "$TARGET_HOST" | grep -q ":"; then
  echo "  curl -6 -I 'http://[::1]:${TARGET_PORT}/'"
else
  echo "  curl -4 -I 'http://127.0.0.1:${TARGET_PORT}/'"
fi
echo
echo "外部 HTTPS 测试（DNS 解析到本机公网 IP）:"
echo "  curl -Ik https://${DOMAIN}/"
echo
echo "错误日志保留在： /var/log/nginx/error.log"
info "如果遇到问题，请贴出：nginx -t 输出、rc-service nginx status 或 systemctl status nginx、以及 ss -ltnp | egrep ':80|:443' 输出。"