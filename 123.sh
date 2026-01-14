#!/usr/bin/env bash
# 一键：为 reverse_proxy_<DOMAIN>.conf 创建 upstream 池（127.0.0.1 与 [::1]），替换 proxy_pass 并重载 nginx
# Usage: sudo bash nginx-create-upstream-and-reload.sh nz.215155.xyz 25774
set -euo pipefail
DOMAIN="${1:-nz.215155.xyz}"
PORT="${2:-25774}"
CONF="/etc/nginx/conf.d/reverse_proxy_${DOMAIN}.conf"
TIMESTAMP="$(date +%s)"

if [ "$(id -u)" -ne 0 ]; then
  echo "请以 root 运行"
  exit 1
fi

if [ ! -f "$CONF" ]; then
  echo "未找到配置: $CONF"
  ls -1 /etc/nginx/conf.d/reverse_proxy_*.conf 2>/dev/null || true
  exit 1
fi

echo "备份 $CONF -> ${CONF}.bak.${TIMESTAMP}"
cp -a "$CONF" "${CONF}.bak.${TIMESTAMP}"

UP_NAME="up_${DOMAIN}_${PORT}"

echo "在 http (global) 作用域插入 upstream 名称 $UP_NAME（若已有同名 upstream 会跳过插入）..."
# insert upstream at top of file /etc/nginx/conf.d/_upstreams.conf (safe single file)
UPSTREAMS_FILE="/etc/nginx/conf.d/_upstreams.conf"
if ! grep -q "upstream ${UP_NAME}" "$UPSTREAMS_FILE" 2>/dev/null; then
  cat >> "$UPSTREAMS_FILE" <<EOF

# generated upstream for ${DOMAIN} port ${PORT} (backup at ${TIMESTAMP})
upstream ${UP_NAME} {
    server 127.0.0.1:${PORT} max_fails=3 fail_timeout=5s;
    server [::1]:${PORT} max_fails=3 fail_timeout=5s;
    keepalive 16;
}
EOF
  echo "已写入 $UPSTREAMS_FILE"
else
  echo "已存在 $UP_NAME，跳过写入"
fi

# Replace proxy_pass lines in site conf to use upstream
# Replace any proxy_pass http://...:PORT; with proxy_pass http://$UP_NAME;
perl -0777 -pe '
  s{\bproxy_pass\s+http://\[?[0-9a-fA-F:\.]+\]?:'"${PORT}"'\s*;}{proxy_pass http://'"${UP_NAME}"';}gmi;
' -i "$CONF" || true

# If replacement didn't happen (e.g., proxy_pass used DNS), attempt to replace by port only occurrence
if ! grep -q "proxy_pass http://${UP_NAME}" "$CONF"; then
  perl -0777 -pe '
    s{\bproxy_pass\s+http://([^;\s]+):'"${PORT}"'\s*;}{proxy_pass http://'"${UP_NAME}"';}gmi;
  ' -i "$CONF" || true
fi

echo "替换完成，测试 nginx 配置..."
if nginx -t 2>&1 | tee /tmp/nginx_test_${TIMESTAMP}.log; then
  echo "nginx -t 通过，尝试重载 nginx..."
  if command -v systemctl >/dev/null 2>&1; then
    systemctl reload nginx || systemctl restart nginx || true
  elif command -v rc-service >/dev/null 2>&1; then
    rc-service nginx reload || rc-service nginx restart || true
  else
    nginx -s reload || true
  fi
  echo "已重载，查看监听与后端连通性："
  ss -ltnp | egrep ':80|:443|:'"${PORT}" || true
  echo "检查 /var/log/nginx/error.log 以验证无 connect() failed 错误。"
  exit 0
else
  echo "nginx -t 未通过，已将测试输出保存在 /tmp/nginx_test_${TIMESTAMP}.log"
  cat /tmp/nginx_test_${TIMESTAMP}.log
  echo "回滚配置..."
  mv "${CONF}.bak.${TIMESTAMP}" "$CONF" || true
  exit 1
fi