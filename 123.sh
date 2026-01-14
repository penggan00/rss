#!/usr/bin/env bash
#
# fix-nginx-upstream-address.sh
# 目的：当 nginx 报错 "connect() failed (111: Connection refused) while connecting to upstream"
#       常因 proxy_pass 指向与后端实际监听地址(IPv4/IPv6)不匹配导致（例如 proxy_pass http://[::1]:PORT;
#       而后端仅监听 127.0.0.1:PORT 或反之）。本脚本自动检测后端端口的监听地址并把 nginx 配置
#       中的 proxy_pass 规范化为最合适的后端地址（127.0.0.1 或 [::1]），然后测试并重载 nginx。
#
# 使用：以 root 运行
#   sudo bash fix-nginx-upstream-address.sh
#
# 注意：
# - 脚本只修改 /etc/nginx/conf.d/reverse_proxy_<DOMAIN>.conf（会备份原文件）。
# - 如果后端为 docker 容器并被 docker-proxy 绑定到 0.0.0.0:PORT，脚本会选择 127.0.0.1:PORT（适配 host 网络与 docker-proxy）。
# - 执行后会自动运行 `nginx -t` 并尝试重载（rc-service systemctl nginx -s reload 三种方式）。
# - 若自动修复失败，会打印诊断信息供人工处理。
#

set -euo pipefail
IFS=$'\n\t'

GREEN="\033[1;32m"
YELLOW="\033[1;33m"
RED="\033[1;31m"
NC="\033[0m"
info(){ echo -e "${GREEN}$*${NC}"; }
warn(){ echo -e "${YELLOW}$*${NC}"; }
err(){ echo -e "${RED}$*${NC}"; }

[ "$(id -u)" -eq 0 ] || { err "请以 root 运行此脚本"; exit 1; }

read -rp "输入要修复的域名（对应 /etc/nginx/conf.d/reverse_proxy_<DOMAIN>.conf），默认 nz.215155.xyz: " DOMAIN
DOMAIN="${DOMAIN:-nz.215155.xyz}"
CONF="/etc/nginx/conf.d/reverse_proxy_${DOMAIN}.conf"
if [ ! -f "$CONF" ]; then
  err "未找到配置文件: $CONF"
  echo "可用的 reverse_proxy_*.conf 列表："
  ls -1 /etc/nginx/conf.d/reverse_proxy_*.conf 2>/dev/null || true
  exit 1
fi

info "正在解析 $CONF 中的 proxy_pass ..."
# 找第一个 proxy_pass 并提取 host和port（支持 http://host:port 和 http://[::1]:port）
UPSTREAM_LINE=$(grep -m1 -E 'proxy_pass\s+http://' "$CONF" || true)
if [ -z "$UPSTREAM_LINE" ]; then
  err "在 $CONF 中未找到 proxy_pass http://... 条目。"
  exit 1
fi

# parse host and port
# examples matched:
#  proxy_pass http://127.0.0.1:25774;
#  proxy_pass http://[::1]:25774;
#  proxy_pass http://example.local:8080;
UPSTREAM_RAW=$(echo "$UPSTREAM_LINE" | sed -E 's/.*proxy_pass\s+http:\/\///; s/\s*;//; s/\s.*$//')
# now UPSTREAM_RAW is like [::1]:25774 or 127.0.0.1:25774 or example.com:8080
# extract port
PORT=$(echo "$UPSTREAM_RAW" | awk -F: '{print $NF}')
if ! echo "$PORT" | grep -qE '^[0-9]+$'; then
  err "无法解析端口：$UPSTREAM_RAW"
  exit 1
fi
info "解析到上游端口: $PORT (来自 $UPSTREAM_RAW)"

# find listeners for this port
info "检测本机对端口 $PORT 的监听情况 (ss -ltnp)..."
LISTEN_INFO=$(ss -ltnp "( sport = :$PORT )" 2>/dev/null || ss -ltnp | egrep ":$PORT" || true)
echo "$LISTEN_INFO"

# Decide preferred backend address:
# Preference:
# 1) if any IPv6 listener exists (contains '[' or visible as [::] or [::1]) -> use [::1] if loopback or [::] if only all-ipv6
# 2) else if any 127.0.0.1 or 0.0.0.0 -> use 127.0.0.1
# 3) else if only specific IPv4 local address -> use that IP
PREFERRED=""
# check for explicit [::1] or [::]
if echo "$LISTEN_INFO" | grep -qi '\[::1\]\|:\:\]'; then
  PREFERRED="[::1]"
elif echo "$LISTEN_INFO" | grep -qi '\[::\]\|:::'; then
  # all IPv6 listening; prefer IPv6 loopback if exists, else use [::] (but we will use [::1] which most services accept if bound to ::)
  PREFERRED="[::1]"
fi

# check IPv4
if [ -z "$PREFERRED" ]; then
  if echo "$LISTEN_INFO" | grep -q '127.0.0.1:'; then
    PREFERRED="127.0.0.1"
  elif echo "$LISTEN_INFO" | grep -q '0.0.0.0:'; then
    # docker-proxy or service bound to all IPv4
    PREFERRED="127.0.0.1"
  else
    # If there is a specific IPv4 like 192.168.x.x:
    SPECIFIC_IPV4=$(echo "$LISTEN_INFO" | awk '{print $4}' | sed 's/:.*$//' | egrep -v '^$' | head -n1 || true)
    if [ -n "$SPECIFIC_IPV4" ]; then
      PREFERRED="$SPECIFIC_IPV4"
    fi
  fi
fi

if [ -z "$PREFERRED" ]; then
  warn "未检测到任何本地监听地址（可能端口尚未监听）。请确认后端服务已启动并监听端口 $PORT。"
  echo "当前 ss -ltnp 输出："
  ss -ltnp | egrep ":$PORT" || true
  exit 2
fi

info "建议使用后端地址: $PREFERRED:$PORT"

# Compose replacement backend URI
if echo "$PREFERRED" | grep -q ":"; then
  # IPv6 literal
  NEW_BACKEND="http://[${PREFERRED}]:${PORT}"
else
  NEW_BACKEND="http://${PREFERRED}:${PORT}"
fi

info "将把 $CONF 中的 proxy_pass 更新为: $NEW_BACKEND （先备份原文件）"
cp -a "$CONF" "${CONF}.bak.$(date +%s)"

# replace proxy_pass lines robustly
perl -0777 -pe '
  s{(proxy_pass\s+)http://\[?[0-9a-fA-F:\.]+\]?:[0-9]+;}{ $1 . "'"${NEW_BACKEND}"'" . ";" }gexmi
' -i "$CONF"

# Also handle cases proxy_pass used DNS names; attempt to replace if it pointed to same port
# If no occurrence replaced, try replacing by port only
if ! grep -q -E "proxy_pass\s+http://\[?[0-9a-fA-F:\.]+\]?:$PORT;" "$CONF"; then
  # fallback: replace any proxy_pass that contains :PORT
  perl -0777 -pe '
    s{(proxy_pass\s+)http://([^;:\s]+\:?)'"${PORT}"'[;]}{ $1 . "'"${NEW_BACKEND}"'" . ";" }gexmi
  ' -i "$CONF" || true
fi

info "完成替换。现在检测 nginx 配置语法..."
if nginx -t 2>&1 | tee /tmp/nginx_test.$$; then
  rm -f /tmp/nginx_test.$$
  info "nginx -t 通过，尝试重载 nginx..."
  if command -v systemctl >/dev/null 2>&1; then
    systemctl reload nginx 2>/dev/null || systemctl restart nginx 2>/dev/null || warn "systemctl reload/restart 返回非0"
  elif command -v rc-service >/dev/null 2>&1; then
    rc-service nginx reload 2>/dev/null || rc-service nginx restart 2>/dev/null || warn "rc-service reload/restart 返回非0"
  else
    nginx -s reload 2>/dev/null || warn "nginx -s reload 返回非0"
  fi
  info "操作完成。请检验 /var/log/nginx/error.log 是否仍有 connect() failed 错误。"
  echo
  info "快速检测："
  ss -ltnp | egrep ":$PORT" || true
  if echo "$NEW_BACKEND" | grep -q '\['; then
    echo "本机连通性测试示例： curl -6 -I 'http://[::1]:${PORT}/' "
  else
    echo "本机连通性测试示例： curl -4 -I 'http://${PREFERRED}:${PORT}/' "
  fi
  echo "若仍报 connect() failed (111: Connection refused)，请确认后端服务在 $PREFERRED:$PORT 正常监听并接受连接（请检查后端日志或进程）。"
  exit 0
else
  cat /tmp/nginx_test.$$
  rm -f /tmp/nginx_test.$$
  err "nginx -t 未通过，已恢复备份并退出。请检查 /var/log/nginx/error.log 与 /etc/nginx/conf.d/*.bak 文件。"
  # restore backup
  mv "${CONF}.bak."* "$CONF" 2>/dev/null || true
  exit 3
fi