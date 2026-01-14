#!/usr/bin/env bash
#
# nginx-smart-reverse-proxy-ultimate.sh
# 终极修复版（保留错误日志），在已有脚本基础上增加：
# - 生成反代配置（支持本地 IPv4/IPv6 选择、WebSocket）
# - 尝试启动/重载 nginx（优先重载）。若启动失败且端口被非-nginx 占用：
#     - 自动把站点改为 IPv6-only（listen [::]:80 ipv6only=on; listen [::]:443 ssl http2 ipv6only=on;）
#     - 对 IPv6 字面量后端自动用方括号（proxy_pass http://[::1]:PORT;）
#     - 再次测试并重载 nginx
# - 如果依然失败，输出诊断信息供人工处理
#
# 设计原则：生产稳定优先，尽量自动生效，不需要你手动停止占端口的进程。
#
# 使用：
#   sudo bash nginx-smart-reverse-proxy-ultimate.sh
#
# 注意：
# - 以 root 运行
# - 脚本会备份原始站点配置（/etc/nginx/conf.d/reverse_proxy_<DOMAIN>.conf.bak.*）
# - 错误日志保留在 /var/log/nginx/error.log
#
set -euo pipefail
IFS=$'\n\t'

YELLOW="\033[1;33m"
GREEN="\033[1;32m"
RED="\033[1;31m"
NC="\033[0m"
info(){ echo -e "${GREEN}$*${NC}"; }
warn(){ echo -e "${YELLOW}$*${NC}"; }
err(){ echo -e "${RED}$*${NC}"; }

# Helpers for service control
rc_reload(){ command -v rc-service >/dev/null 2>&1 && rc-service nginx reload >/dev/null 2>&1; }
rc_restart(){ command -v rc-service >/dev/null 2>&1 && rc-service nginx restart >/dev/null 2>&1; }
rc_start(){ command -v rc-service >/dev/null 2>&1 && rc-service nginx start >/dev/null 2>&1; }
systemd_reload(){ command -v systemctl >/dev/null 2>&1 && systemctl reload nginx >/dev/null 2>&1; }
systemd_restart(){ command -v systemctl >/dev/null 2>&1 && systemctl restart nginx >/dev/null 2>&1; }
systemd_start(){ command -v systemctl >/dev/null 2>&1 && systemctl start nginx >/dev/null 2>&1; }

# Ensure root
[ "$(id -u)" -eq 0 ] || { err "请以 root 或 sudo 运行此脚本。"; exit 1; }

# Minimal detection
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

# Install nginx if missing
if ! command -v nginx >/dev/null 2>&1; then
  info "安装 nginx 与依赖..."
  if [ "$DISTRO" = "alpine" ]; then
    apk update
    apk add --no-cache nginx openssl curl bash coreutils procps
    mkdir -p /run/nginx
  else
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y
    apt-get install -y nginx openssl curl ca-certificates procps
  fi
fi
info "nginx 就绪: $(command -v nginx)"

# Ensure runtime and log dirs
mkdir -p /var/log/nginx /var/lib/nginx/logs /run/nginx
touch /var/log/nginx/error.log /var/log/nginx/access.log /var/lib/nginx/logs/error.log || true
# set ownership safe
NGUSER="nginx"
if getent passwd nginx >/dev/null 2>&1; then NGUSER=nginx
elif getent passwd www-data >/dev/null 2>&1; then NGUSER=www-data
else NGUSER=root; fi
chown -R "${NGUSER}:${NGUSER}" /var/log/nginx /var/lib/nginx /run/nginx || true
chmod 750 /var/log/nginx /var/lib/nginx /run/nginx || true

# Smart tuning (safe include)
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
fi

# Read inputs
read -rp "请输入用于反代的子域名 (如 sub.example.com)： " DOMAIN
[ -n "$DOMAIN" ] || { err "域名不能为空"; exit 1; }

read -rp "请输入后端端口 (例如 8080)： " TARGET_PORT
echo "$TARGET_PORT" | grep -qE '^[0-9]+$' || { err "端口必须为数字"; exit 1; }

echo "选择后端 IP 源（脚本将自动使用本地地址）："
echo " 1) 本地 IPv4 (127.0.0.1)"
echo " 2) 本地 IPv6 (::1)"
read -rp "请选择 1 或 2（默认 1）: " IP_CHOICE
IP_CHOICE="${IP_CHOICE:-1}"
if [[ "$IP_CHOICE" =~ ^2$ ]]; then TARGET_HOST="::1"; else TARGET_HOST="127.0.0.1"; fi

read -rp "是否开启 WebSocket 1.1 支持？ (y/N)： " ENABLE_WS
ENABLE_WS="${ENABLE_WS:-n}"

# Prepare backend URI
if echo "$TARGET_HOST" | grep -q ":"; then BACKEND="http://[${TARGET_HOST}]:${TARGET_PORT}"; else BACKEND="http://${TARGET_HOST}:${TARGET_PORT}"; fi

info "将为 ${DOMAIN} 反代到 ${TARGET_HOST}:${TARGET_PORT} （WebSocket: ${ENABLE_WS}）"

# Backup and generate site conf
CONF="/etc/nginx/conf.d/reverse_proxy_${DOMAIN}.conf"
[ -f "$CONF" ] && cp -a "$CONF" "${CONF}.bak.$(date +%s)" || true

SSL_CRT="/etc/nginx/ssl/${DOMAIN}.crt"
SSL_KEY="/etc/nginx/ssl/${DOMAIN}.key"
mkdir -p /etc/nginx/ssl
# user may already have certs; script will not request them

WS_MAP=""
PROXY_WS=""
if [[ "${ENABLE_WS,,}" = "y" || "${ENABLE_WS,,}" = "yes" ]]; then
  WS_MAP='map $http_upgrade $connection_upgrade {
    default upgrade;
    "" close;
}'
  PROXY_WS='        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;'
fi

cat >"$CONF" <<EOF
# Auto-generated reverse proxy for ${DOMAIN}
$WS_MAP

server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};
    return 301 https://\$host\$request_uri;
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name ${DOMAIN};

    ssl_certificate ${SSL_CRT};
    ssl_certificate_key ${SSL_KEY};
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    access_log off;
    error_log /var/log/nginx/error.log crit;

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
${PROXY_WS}
    }
}
EOF

info "已写入配置： $CONF (备份在同目录 *.bak.* 若存在旧文件)"

# Test config first
if ! nginx -t 2>/tmp/nginx_test.err; then
  cat /tmp/nginx_test.err
  rm -f /tmp/nginx_test.err
  err "nginx -t 检查失败，退出。"
  exit 1
fi
rm -f /tmp/nginx_test.err
info "nginx 配置语法检查通过"

# Try reload or start, with tolerant logic
attempt_reload_or_start(){
  if pgrep -x nginx >/dev/null 2>&1; then
    warn "检测到 nginx 正在运行 -> 尝试重载"
    if command -v systemctl >/dev/null 2>&1; then
      if systemctl reload nginx >/dev/null 2>&1; then info "systemctl reload 成功"; return 0; fi
      if systemctl restart nginx >/dev/null 2>&1; then info "systemctl restart 成功"; return 0; fi
    fi
    if command -v rc-service >/dev/null 2>&1; then
      if rc-service nginx reload >/dev/null 2>&1; then info "rc-service reload 成功"; return 0; fi
      if rc-service nginx restart >/dev/null 2>&1; then info "rc-service restart 成功"; return 0; fi
    fi
    # fallback
    if nginx -s reload >/dev/null 2>&1; then info "nginx -s reload 成功"; return 0; fi

    warn "重载/重启返回非0，可能存在端口被占用或权限问题。"
    return 1
  else
    info "nginx 未运行 -> 尝试启动"
    if command -v systemctl >/dev/null 2>&1; then
      if systemctl start nginx >/dev/null 2>&1; then info "systemctl start 成功"; return 0; fi
    fi
    if command -v rc-service >/dev/null 2>&1; then
      if rc-service nginx start >/dev/null 2>&1; then info "rc-service start 成功"; return 0; fi
    fi
    if nginx >/dev/null 2>&1; then info "直接 nginx 启动成功"; return 0; fi

    warn "启动失败，尝试检测端口占用"
    return 1
  fi
}

# Run first attempt
if attempt_reload_or_start; then
  info "nginx 已应用配置（启动或重载成功）"
  exit 0
fi

# If we reach here, start/reload failed. Diagnose port owners and try IPv6-only fallback.
info "尝试诊断端口占用并进行 IPv6-only 回退（若能生效）..."

# show current listeners
ss -ltnp | egrep ':80|:443' || true

# collect owners
PORT80_OWNER=$(ss -ltnp '( sport = :80 )' 2>/dev/null | awk 'NR>1{print $6}' | head -n1 || true)
PORT443_OWNER=$(ss -ltnp '( sport = :443 )' 2>/dev/null | awk 'NR>1{print $6}' | head -n1 || true)

info "80 所属: ${PORT80_OWNER:-none}"
info "443 所属: ${PORT443_OWNER:-none}"

# Helper: check if owner is nginx
owner_is_nginx(){
  local owner="$1"
  if echo "$owner" | grep -qi nginx; then return 0; else return 1; fi
}

# If ports are occupied by nginx but reload/start failed, try killing stale master? safer to reload again
if owner_is_nginx "$PORT80_OWNER" || owner_is_nginx "$PORT443_OWNER"; then
  warn "检测到端口被 nginx 占用，但重载仍失败。尝试先优雅停止再启动（不会强杀）..."
  if command -v systemctl >/dev/null 2>&1; then systemctl stop nginx || true; sleep 1; systemctl start nginx || true; fi
  if command -v rc-service >/dev/null 2>&1; then rc-service nginx stop || true; sleep 1; rc-service nginx start || true; fi
  if pgrep -x nginx >/dev/null 2>&1; then
    info "操作后检测到 nginx 仍在运行，尝试重载"
    attempt_reload_or_start || warn "重载/重启仍失败"
  fi
fi

# Re-test whether ports are occupied by non-nginx processes
PORT80_OWNER_NEW=$(ss -ltnp '( sport = :80 )' 2>/dev/null | awk 'NR>1{print $6}' | head -n1 || true)
PORT443_OWNER_NEW=$(ss -ltnp '( sport = :443 )' 2>/dev/null | awk 'NR>1{print $6}' | head -n1 || true)

# If either port is occupied by non-nginx, attempt IPv6-only fallback
if ( [ -n "$PORT80_OWNER_NEW" ] && ! owner_is_nginx "$PORT80_OWNER_NEW" ) || ( [ -n "$PORT443_OWNER_NEW" ] && ! owner_is_nginx "$PORT443_OWNER_NEW" ); then
  warn "检测到 80/443 被非-nginx 进程占用，开始将站点配置回退为 IPv6-only（仅绑定 [::]:80/[::]:443）以绕开 IPv4 占用。"

  # Backup and patch conf
  cp -a "$CONF" "${CONF}.ipv6only.bak.$(date +%s)"
  sed -E -i \
    -e 's/^[[:space:]]*listen[[:space:]]+80[[:space:]]*;/    listen [::]:80 ipv6only=on;/' \
    -e 's/^[[:space:]]*listen[[:space:]]+\[::\]:80[[:space:]]*;/    listen [::]:80 ipv6only=on;/' \
    -e 's/^[[:space:]]*listen[[:space:]]+443[[:space:]]+ssl[[:space:]]+http2[[:space:]]*;/    listen [::]:443 ssl http2 ipv6only=on;/' \
    -e 's/^[[:space:]]*listen[[:space:]]+\[::\]:443[[:space:]]+ssl[[:space:]]+http2[[:space:]]*;/    listen [::]:443 ssl http2 ipv6only=on;/' \
    -e 's/^[[:space:]]*listen[[:space:]]+443[[:space:]]+ssl[[:space:]]*;/    listen [::]:443 ssl ipv6only=on;/' \
    "$CONF" || true

  # Ensure proxy_pass IPv6 literal bracketed (use perl)
  perl -0777 -pe '
    s{
      (proxy_pass\s+http://)\[?([0-9a-fA-F:]+)\]?:([0-9]+);
    }{$1"[".$2."]:" .$3 . ";"}gexmi
  ' -i "$CONF" || true

  info "已将 $CONF 修改为 IPv6-only（备份在 ${CONF}.ipv6only.bak.*）"

  # test and reload
  if nginx -t 2>/tmp/nginx_test2.err; then
    rm -f /tmp/nginx_test2.err
    info "nginx 配置检查通过，尝试重载/启动"
    if attempt_reload_or_start; then
      info "已应用 IPv6-only 配置并重载/启动 nginx 成功"
      exit 0
    else
      warn "重载/启动仍失败（在 IPv6-only 回退后）"
    fi
  else
    cat /tmp/nginx_test2.err
    rm -f /tmp/nginx_test2.err
    err "IPv6-only 修改导致 nginx -t 失败（不应发生），请检查配置备份"
    exit 1
  fi
fi

# If reached here, recovery failed. Print diagnostics for manual action.
err "自动修复未能完全解决问题。请手动检查以下信息并贴上来以便进一步诊断："
echo
echo "---- nginx -t 输出 ----"
nginx -t 2>&1 || true
echo
echo "---- 监听端口 (ss -ltnp | egrep ':80|:443') ----"
ss -ltnp | egrep ':80|:443' || true
echo
echo "---- 相关进程 ----"
ps aux | egrep 'nginx|warp|cloudflared|caddy|traefik|apache|httpd' | egrep -v egrep || true
echo
echo "我已在必要时将站点配置备份为 ${CONF}.bak.* 和 ${CONF}.ipv6only.bak.*。若你希望我强制停止占用端口的服务并用 nginx 绑定到 IPv4，请明确授权（我会尽量使用 systemctl/rc-service stop，而非 kill -9）。"
exit 2