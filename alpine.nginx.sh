#!/usr/bin/env bash
#
# nginx-smart-reverse-proxy-fixed.sh
# 终极稳定版（保留错误日志、支持 IPv4+IPv6 双栈 / IPv6-only / loopback 模式）
# 功能概览：
# - 兼容 Alpine (OpenRC) 与 Debian (systemd)
# - 安装 nginx（如未安装），确保 /var/log/nginx /run/nginx 等目录存在并权限正确
# - 交互式设置：域名、后端端口、后端地址来源（本地 IPv4/IPv6）、监听模式（双栈/IPv6-only/回环）
# - 自动为 IPv6 字面量后端加方括号（proxy_pass http://[::1]:PORT;）
# - 生成站点配置 /etc/nginx/conf.d/reverse_proxy_<DOMAIN>.conf（备份原文件）
# - 保留错误日志到 /var/log/nginx/error.log；access_log 默认关闭
# - 每次运行：若 nginx 未运行则尝试启动；若已运行则重载。
#   若因 0.0.0.0:80/443 被非-nginx 占用导致启动失败，脚本会提示并（若选择）自动回退到 IPv6-only 并重试（备份原文件）
# - 所有修改均有备份，遇到问题会打印诊断输出
#
# 使用：
#   sudo bash nginx-smart-reverse-proxy-fixed.sh
#
# 注：请以 root 运行。若证书已准备好，请确保证书放置在 /etc/nginx/ssl/<DOMAIN>.crt 与 .key
#
set -euo pipefail
IFS=$'\n\t'

# 颜色
YELLOW="\033[1;33m"
GREEN="\033[1;32m"
RED="\033[1;31m"
NC="\033[0m"
info(){ echo -e "${GREEN}$*${NC}"; }
warn(){ echo -e "${YELLOW}$*${NC}"; }
err(){ echo -e "${RED}$*${NC}"; }

# Ensure root
[ "$(id -u)" -eq 0 ] || { err "请以 root 运行此脚本（sudo）。"; exit 1; }

# Detect distro
DISTRO=""
if [ -f /etc/os-release ]; then . /etc/os-release; fi
if echo "${ID:-} ${ID_LIKE:-}" | grep -qi alpine; then DISTRO=alpine
elif echo "${ID:-} ${ID_LIKE:-}" | grep -Ei "debian|ubuntu|mint" >/dev/null 2>&1; then DISTRO=debian
else
  if command -v apk >/dev/null 2>&1; then DISTRO=alpine
  elif command -v apt-get >/dev/null 2>&1; then DISTRO=debian
  else err "不支持的发行版"; fi
fi
info "检测到发行版: $DISTRO"

# Install nginx and basic tools if missing
install_prereqs() {
  if ! command -v nginx >/dev/null 2>&1; then
    info "安装 nginx 及依赖..."
    if [ "$DISTRO" = "alpine" ]; then
      apk update
      apk add --no-cache nginx curl openssl bash coreutils procps
      mkdir -p /run/nginx
    else
      export DEBIAN_FRONTEND=noninteractive
      apt-get update -y
      apt-get install -y nginx curl openssl ca-certificates procps
    fi
  else
    info "检测到 nginx 已安装"
  fi
}

# Ensure log/run dirs
ensure_dirs() {
  mkdir -p /var/log/nginx /var/lib/nginx/logs /run/nginx /etc/nginx/conf.d
  touch /var/log/nginx/error.log /var/log/nginx/access.log /var/lib/nginx/logs/error.log || true

  # Determine nginx user
  NGINX_USER="nginx"
  if [ -f /etc/nginx/nginx.conf ]; then
    U=$(sed -n 's/^[ \t]*user[ \t]\+\([a-zA-Z0-9._-]\+\).*;/\1/p' /etc/nginx/nginx.conf | head -n1 || true)
    [ -n "$U" ] && NGINX_USER="$U"
  fi
  if ! getent passwd "$NGINX_USER" >/dev/null 2>&1; then
    if getent passwd www-data >/dev/null 2>&1; then NGINX_USER=www-data; else NGINX_USER=root; fi
  fi
  chown -R "${NGINX_USER}:${NGINX_USER}" /var/log/nginx /var/lib/nginx /run/nginx || true
  chmod 750 /var/log/nginx /var/lib/nginx /run/nginx || true
  chmod 640 /var/log/nginx/*.log /var/lib/nginx/logs/*.log || true
  info "已确保日志与运行目录存在并设置权限 (/var/log/nginx, /run/nginx)"
}

# Smart tuning include (safe defaults)
write_smart_tune() {
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
    info "已写入 $SAFE_INC（安全优化）"
  fi
}

# Helper: bracket IPv6 literal backend
make_backend_uri() {
  local host="$1" port="$2"
  if echo "$host" | grep -q ":"; then
    echo "http://[${host}]:${port}"
  else
    echo "http://${host}:${port}"
  fi
}

# Generate site config based on mode
generate_site_conf() {
  local domain="$1" backend="$2" mode="$3" ws="$4"
  local conf="/etc/nginx/conf.d/reverse_proxy_${domain}.conf"
  cp -a "$conf" "${conf}.bak.$(date +%s)" 2>/dev/null || true

  local listen_http listen_http_v6 listen_https listen_https_v6
  case "$mode" in
    dual)
      listen_http="listen 0.0.0.0:80;"
      listen_http_v6="listen [::]:80 ipv6only=on;"
      listen_https="listen 0.0.0.0:443 ssl http2;"
      listen_https_v6="listen [::]:443 ssl http2 ipv6only=on;"
      ;;
    ipv6-only)
      listen_http="listen [::]:80 ipv6only=on;"
      listen_http_v6=""
      listen_https="listen [::]:443 ssl http2 ipv6only=on;"
      listen_https_v6=""
      ;;
    loopback)
      listen_http="listen 127.0.0.1:80;"
      listen_http_v6="listen [::1]:80 ipv6only=on;"
      listen_https="listen 127.0.0.1:443 ssl http2;"
      listen_https_v6="listen [::1]:443 ssl http2 ipv6only=on;"
      ;;
    *)
      err "未知监听模式: $mode"; exit 1
      ;;
  esac

  # WebSocket support blocks
  local ws_map ws_headers
  if [[ "${ws,,}" = "y" || "${ws,,}" = "yes" ]]; then
    ws_map='map $http_upgrade $connection_upgrade {
    default upgrade;
    "" close;
}'
    ws_headers='        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;'
  else
    ws_map=''
    ws_headers=''
  fi

  mkdir -p /etc/nginx/ssl
  local sslcrt="/etc/nginx/ssl/${domain}.crt"
  local sslkey="/etc/nginx/ssl/${domain}.key"

  cat >"$conf" <<EOF
# Auto-generated reverse proxy for ${domain}
$ws_map

server {
    $listen_http
    $listen_http_v6
    server_name ${domain};
    return 301 https://\$host\$request_uri;
}

server {
    $listen_https
    $listen_https_v6
    server_name ${domain};

    ssl_certificate ${sslcrt};
    ssl_certificate_key ${sslkey};
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    access_log off;
    error_log /var/log/nginx/error.log crit;

    add_header X-Frame-Options SAMEORIGIN;
    add_header X-Content-Type-Options nosniff;

    location / {
        proxy_pass ${backend};
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
${ws_headers}
    }
}
EOF

  info "已生成站点配置： $conf （备份：${conf}.bak.*）"
}

# Test nginx config
test_nginx() {
  if nginx -t 2>&1 | tee /tmp/nginx_test.$$; then
    rm -f /tmp/nginx_test.$$
    info "nginx 配置语法检查通过"
    return 0
  else
    cat /tmp/nginx_test.$$
    rm -f /tmp/nginx_test.$$
    return 1
  fi
}

# Start or reload nginx
start_or_reload() {
  if pgrep -x nginx >/dev/null 2>&1; then
    warn "检测到 nginx 正在运行，尝试重载..."
    if command -v systemctl >/dev/null 2>&1; then
      systemctl reload nginx 2>/dev/null || systemctl restart nginx 2>/dev/null || true
    elif command -v rc-service >/dev/null 2>&1; then
      rc-service nginx reload 2>/dev/null || rc-service nginx restart 2>/dev/null || true
    else
      nginx -s reload 2>/dev/null || true
    fi
  else
    info "尝试启动 nginx..."
    if command -v systemctl >/dev/null 2>&1; then
      systemctl start nginx 2>/dev/null || systemctl restart nginx 2>/dev/null || true
    elif command -v rc-service >/dev/null 2>&1; then
      rc-service nginx start 2>/dev/null || rc-service nginx restart 2>/dev/null || true
    else
      nginx >/dev/null 2>&1 || true
    fi
  fi
}

# Diagnose port owners
port_owners() {
  ss -ltnp | egrep ':80|:443' || true
}

# Main flow
install_prereqs
ensure_dirs
write_smart_tune

# Interactive inputs
read -rp "请输入用于反代的子域名 (如 sub.example.com)： " DOMAIN
[ -n "$DOMAIN" ] || { err "域名不能为空"; exit 1; }

read -rp "请输入后端端口 (例如 8080)： " TARGET_PORT
echo "$TARGET_PORT" | grep -qE '^[0-9]+$' || { err "端口必须为数字"; exit 1; }

echo "选择后端本地地址来源（脚本会自动使用该地址作为后端，不需手动输入 IP）："
echo " 1) 本地 IPv4 (127.0.0.1)"
echo " 2) 本地 IPv6 (::1)"
read -rp "请选择 1 或 2（默认 1）: " IP_CHOICE
IP_CHOICE="${IP_CHOICE:-1}"
if [[ "$IP_CHOICE" =~ ^2$ ]]; then TARGET_HOST="::1"; else TARGET_HOST="127.0.0.1"; fi

read -rp "是否开启 WebSocket 1.1 支持？ (y/N)： " ENABLE_WS
ENABLE_WS="${ENABLE_WS:-n}"

echo "选择监听模式（默认 1 公网双栈）："
echo " 1) 公网双栈（0.0.0.0 + [::]） - 允许 IPv4 & IPv6 访问"
echo " 2) IPv6-only（仅 [::]） - 仅 IPv6 可达"
echo " 3) 本机回环（127.0.0.1 + [::1]） - 仅本机访问"
read -rp "请选择 1/2/3（默认 1）: " MODE_CHOICE
MODE_CHOICE="${MODE_CHOICE:-1}"
case "$MODE_CHOICE" in
  1) LISTEN_MODE="dual" ;;
  2) LISTEN_MODE="ipv6-only" ;;
  3) LISTEN_MODE="loopback" ;;
  *) LISTEN_MODE="dual" ;;
esac

# Prepare backend URI
BACKEND=$(make_backend_uri "$TARGET_HOST" "$TARGET_PORT")

# Backup main nginx.conf
[ -f /etc/nginx/nginx.conf ] && cp -a /etc/nginx/nginx.conf /etc/nginx/nginx.conf.bak.$(date +%s) || true

# Optionally try to install certificate via acme.sh if available; otherwise expect user to place cert
if [ -x "/root/.acme.sh/acme.sh" ]; then
  warn "检测到 acme.sh，可自动尝试安装证书（若已申请）"
  /root/.acme.sh/acme.sh --install-cert -d "$DOMAIN" \
    --key-file "/etc/nginx/ssl/${DOMAIN}.key" \
    --fullchain-file "/etc/nginx/ssl/${DOMAIN}.crt" \
    --reloadcmd "echo 'acme.sh 已安装证书'"
fi

# Ensure links if acme created certs
if [ -f "/etc/nginx/ssl/certs/${DOMAIN}/fullchain.pem" ] && [ -f "/etc/nginx/ssl/private/${DOMAIN}/key.pem" ]; then
  ln -sf "/etc/nginx/ssl/certs/${DOMAIN}/fullchain.pem" "/etc/nginx/ssl/${DOMAIN}.crt" || true
  ln -sf "/etc/nginx/ssl/private/${DOMAIN}/key.pem" "/etc/nginx/ssl/${DOMAIN}.key" || true
fi

# Generate site conf
generate_site_conf "$DOMAIN" "$BACKEND" "$LISTEN_MODE" "$ENABLE_WS"

# Ensure proxy_pass IPv6 literal bracketed (defensive)
perl -0777 -pe 's{(proxy_pass\s+http://)\[?([0-9a-fA-F:]+)\]?:([0-9]+);}{$1"[".$2."]:" .$3 . ";"}gexmi' -i "/etc/nginx/conf.d/reverse_proxy_${DOMAIN}.conf" || true

# Test nginx config
if test_nginx; then
  start_or_reload
  sleep 1
  # If reload/start failed, check diagnostics
  if ! pgrep -x nginx >/dev/null 2>&1; then
    warn "nginx 未能成功启动或重载。检测端口占用信息："
    port_owners
    warn "若 0.0.0.0:80 或 0.0.0.0:443 被非-nginx 占用，且你当前选择的是 dual 模式，脚本可自动回退为 IPv6-only。"
    read -rp "是否自动回退为 IPv6-only 并重试？ (y/N)： " DO_FALLBACK
    DO_FALLBACK="${DO_FALLBACK:-n}"
    if [[ "${DO_FALLBACK,,}" = "y" ]]; then
      info "开始 IPv6-only 回退：备份并修改 /etc/nginx/conf.d/reverse_proxy_${DOMAIN}.conf"
      cp -a "/etc/nginx/conf.d/reverse_proxy_${DOMAIN}.conf" "/etc/nginx/conf.d/reverse_proxy_${DOMAIN}.conf.ipv6only.bak.$(date +%s)"
      sed -E -i \
        -e 's/^[[:space:]]*listen[[:space:]]+0\.0\.0\.0:80[[:space:]]*;/    listen [::]:80 ipv6only=on;/' \
        -e 's/^[[:space:]]*listen[[:space:]]+0\.0\.0\.0:443[[:space:]]+ssl[[:space:]]+http2[[:space:]]*;/    listen [::]:443 ssl http2 ipv6only=on;/' \
        -e 's/^[[:space:]]*listen[[:space:]]+0\.0\.0\.0:443[[:space:]]+ssl[[:space:]]*;/    listen [::]:443 ssl ipv6only=on;/' \
        "/etc/nginx/conf.d/reverse_proxy_${DOMAIN}.conf" || true
      # ensure backend brackets
      perl -0777 -pe 's{(proxy_pass\s+http://)\[?([0-9a-fA-F:]+)\]?:([0-9]+);}{$1"[".$2."]:" .$3 . ";"}gexmi' -i "/etc/nginx/conf.d/reverse_proxy_${DOMAIN}.conf" || true
      if test_nginx; then
        start_or_reload
        sleep 1
      else
        err "回退后 nginx -t 仍然失败，请检查 /var/log/nginx/error.log"
      fi
    else
      err "未进行回退。请手动解决端口占用后重试（或授权脚本回退）。"
    fi
  else
    info "nginx 已在运行（启动/重载成功）"
  fi
else
  err "nginx 配置语法不通过，请查看 /var/log/nginx/error.log 与 nginx -t 输出"
  exit 1
fi

# Final reminders & tests
info "完成。域名 ${DOMAIN} 已配置反代到 ${BACKEND}"
info "监听模式: ${LISTEN_MODE} ; WebSocket: ${ENABLE_WS}"
echo
echo "本机到后端连通性测试（示例）:"
if echo "$BACKEND" | grep -q '\['; then
  echo "  curl -6 -I 'http://[::1]:${TARGET_PORT}/'"
else
  echo "  curl -4 -I 'http://127.0.0.1:${TARGET_PORT}/' || curl -6 -I 'http://[::1]:${TARGET_PORT}/'"
fi
echo
echo "外部 HTTPS 测试（DNS 指向本机公网 IP）："
echo "  curl -Ik https://${DOMAIN}/"
echo
echo "错误日志路径： /var/log/nginx/error.log"
echo "若仍有问题，请把以下输出贴给我： nginx -t 的输出； rc-service nginx status 或 systemctl status nginx； ss -ltnp | egrep ':80|:443'。"