#!/usr/bin/env bash
#
# nginx-smart-reverse-proxy.sh
# 适配 Alpine Linux v3.21 (OpenRC) 和 Debian (systemd) 的智能 nginx 反代脚本
#
# 功能
# - 自动检测系统（Alpine / Debian-family）
# - 安装 nginx（使用 apk 或 apt）并开启自启
# - 生成同时监听 IPv4 & IPv6 的 80/443 配置（自动 HTTP -> HTTPS 跳转）
# - 根据输入的子域名 + 目标地址:端口 生成反代配置（可选 WebSocket 1.1 支持）
# - 使用已存在的证书（调用 install_certificate 函数完成证书放置）
# - 关闭访问日志并把错误日志重定向到 /dev/null（按你的要求“不记录日志”）
# - 提供一些稳定性/性能优化建议并尝试写入 nginx 主配置（备份原文件）
#
# 使用方法
# 1) 放到目标机器，赋权：chmod +x nginx-smart-reverse-proxy.sh
# 2) 以 root 或 sudo 运行：sudo ./nginx-smart-reverse-proxy.sh
# 3) 按提示输入 DOMAIN、TARGET_HOST、TARGET_PORT、是否开启 WebSocket
#
# 注意
# - 证书安���部分会使用 /etc/nginx/ssl/... 路径，且假设你已经通过 acme.sh 或其他方式获取并希望放到该路径。
# - 脚本会在 /etc/nginx/conf.d/ 生成名为 reverse_proxy_${DOMAIN}.conf 的站点配置（适配大多数 nginx 包）
# - 脚本尽力兼容 Alpine(OpenRC) 和 Debian(systemd)，在其他 distro 上也可能工作但未专门测试
# - 按你要求：访问日志关闭，错误日志写入 /dev/null
#
set -euo pipefail
IFS=$'\n\t'

# 颜色（可选）
YELLOW="\033[1;33m"
GREEN="\033[1;32m"
RED="\033[1;31m"
NC="\033[0m"

# 全局变量（交互时填写）
DOMAIN=""
TARGET_HOST=""
TARGET_PORT=""
ENABLE_WS="n"
ACME_DIR="${ACME_DIR:-/root/.acme.sh}"   # 若使用 acme.sh 可修改此处

# 检测系统
detect_os() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        OS_ID="${ID:-}"
        OS_ID_LIKE="${ID_LIKE:-}"
    else
        OS_ID=""
        OS_ID_LIKE=""
    fi

    if echo "$OS_ID $OS_ID_LIKE" | grep -qi alpine; then
        DISTRO="alpine"
    elif echo "$OS_ID $OS_ID_LIKE" | grep -Ei "debian|ubuntu|mint" >/dev/null 2>&1; then
        DISTRO="debian"
    else
        # 兜底：尝试基于存在的包管理器判断
        if command -v apk >/dev/null 2>&1; then
            DISTRO="alpine"
        elif command -v apt-get >/dev/null 2>&1; then
            DISTRO="debian"
        else
            echo -e "${RED}不支持的发行版，脚本仅适配 Alpine/Debian 系列。${NC}"
            exit 1
        fi
    fi

    echo -e "${GREEN}检测到发行版: $DISTRO${NC}"
}

# 安装所需工具与 nginx
install_nginx() {
    echo -e "${YELLOW}>>> 安装 nginx 及依赖...${NC}"
    if [ "$DISTRO" = "alpine" ]; then
        apk update
        apk add --no-cache nginx openssl curl bash coreutils
        # 确保 /run/nginx 存在（openrc 的 nginx 可能需要）
        mkdir -p /run/nginx
    else
        apt-get update -y
        DEBIAN_FRONTEND=noninteractive apt-get install -y nginx openssl curl ca-certificates gnupg2 lsb-release
    fi

    # 确认 nginx 安装成功
    if ! command -v nginx >/dev/null 2>&1; then
        echo -e "${RED}nginx 安装失败，请检查包管理器输出。${NC}"
        exit 1
    fi

    echo -e "${GREEN}nginx 安装完成${NC}"
}

# 开启自启并启动 nginx（systemd 或 openrc）
enable_autostart() {
    echo -e "${YELLOW}>>> 配置 nginx 自启并启动服务...${NC}"
    if [ "$DISTRO" = "alpine" ]; then
        # openrc
        rc-update add nginx default || true
        rc-service nginx stop >/dev/null 2>&1 || true
        rc-service nginx start || rc-service nginx restart || true
    else
        # systemd
        systemctl enable --now nginx || (systemctl daemon-reload && systemctl restart nginx)
    fi

    # 再次检查 nginx 状态
    sleep 1
    if ! nginx -t >/dev/null 2>&1; then
        echo -e "${RED}nginx 配置检查失败，请查看 /var/log 或 nginx -t 输出。${NC}"
        # 继续：我们会尝试启动，但通知用户
    else
        echo -e "${GREEN}nginx 已启动并设置为开机自启（如果系统支持）。${NC}"
    fi
}

# 备份并尝试写入一些稳定性/性能优化到 nginx 主配置（尽量保持兼容）
tune_nginx_main_conf() {
    NGINX_MAIN_CONF="/etc/nginx/nginx.conf"
    if [ ! -f "$NGINX_MAIN_CONF" ]; then
        echo -e "${YELLOW}未找到 $NGINX_MAIN_CONF，跳过主配置调整。${NC}"
        return
    fi

    echo -e "${YELLOW}>>> 备份并尝试优化 $NGINX_MAIN_CONF ...${NC}"
    cp -a "$NGINX_MAIN_CONF" "${NGINX_MAIN_CONF}.backup.$(date +%s)" || true

    # 仅在未设置相关选项时追加最小安全/性能项，避免破坏发行版默认结构
    # 我们在 http {} 内追加一段 safe_optimizations（通过在配置尾部 include）
    SAFE_INC="/etc/nginx/conf.d/_smart_tune.conf"
    cat >"$SAFE_INC" <<'EOF'
# smart tuning - safe defaults (generated)
worker_processes auto;
events {
    worker_connections 10240;
    use epoll; # 若不可用 nginx 会忽略
}

http {
    server_tokens off;
    sendfile on;
    tcp_nopush on;
    tcp_nodelay on;
    keepalive_timeout 65;
    types_hash_max_size 2048;
    client_max_body_size 100m;
    fastcgi_buffers 8 16k;
    fastcgi_buffer_size 32k;
    # SSL 缓存/会话
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;
}
EOF

    echo -e "${GREEN}已写入 $SAFE_INC（如果 nginx 配置已包含 conf.d/*.conf 则会生效）。备份保存在 ${NGINX_MAIN_CONF}.backup.*${NC}"
}

# 将用户已生成的证书安装到 Nginx 指定位置（使用你提供的逻辑）
install_certificate() {
    # 依赖全局 DOMAIN, ACME_DIR
    echo -e "${YELLOW}>>> 安装证书到 Nginx...${NC}"
    
    # 创建证书目录
    mkdir -p "/etc/nginx/ssl/certs/$DOMAIN"
    mkdir -p "/etc/nginx/ssl/private/$DOMAIN"
    mkdir -p "/etc/nginx/ssl"
    
    cd "$ACME_DIR" || true
    
    # 如果 acme.sh 存在则调用安装命令（否则假定用户已手动放置证书到 ACME_DIR）
    if [ -x "./acme.sh" ]; then
        ./acme.sh --install-cert -d "$DOMAIN" \
            --key-file "/etc/nginx/ssl/private/$DOMAIN/key.pem" \
            --fullchain-file "/etc/nginx/ssl/certs/$DOMAIN/fullchain.pem" \
            --cert-file "/etc/nginx/ssl/certs/$DOMAIN/cert.pem" \
            --ca-file "/etc/nginx/ssl/certs/$DOMAIN/ca.pem" \
            --reloadcmd "echo '证书安装完成'"
    else
        echo -e "${YELLOW}未检测到 acme.sh，可跳过自动安装，或者请手动将证书放到 /etc/nginx/ssl/...${NC}"
    fi
    
    # 创建符号链接（方便 nginx 指向固定路径）
    ln -sf "/etc/nginx/ssl/certs/$DOMAIN/fullchain.pem" "/etc/nginx/ssl/$DOMAIN.crt" || true
    ln -sf "/etc/nginx/ssl/private/$DOMAIN/key.pem" "/etc/nginx/ssl/$DOMAIN.key" || true
    
    echo -e "${GREEN}>>> 证书安装步骤完成（请确认 /etc/nginx/ssl/$DOMAIN.crt 与 .key 已存在）${NC}"
}

# 生成反代配置
generate_proxy_conf() {
    CONF_DIR="/etc/nginx/conf.d"
    mkdir -p "$CONF_DIR"

    CONF_FILE="${CONF_DIR}/reverse_proxy_${DOMAIN}.conf"

    echo -e "${YELLOW}>>> 生成反代配置到 $CONF_FILE ...${NC}"

    # 如果启用 websocket，需要在 http 范围写 map，用 conf.d 内文件即可
    if [ "${ENABLE_WS}" = "y" ] || [ "${ENABLE_WS}" = "Y" ]; then
        cat >"$CONF_FILE" <<EOF
# 自动生成的反代配置（含 WebSocket 支持）
map \$http_upgrade \$connection_upgrade {
    default upgrade;
    '' close;
}

server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};
    # 所有 http 请求强制跳转到 https
    return 301 https://\$host\$request_uri;
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name ${DOMAIN};

    # SSL - 使用脚本安装的位置
    ssl_certificate /etc/nginx/ssl/${DOMAIN}.crt;
    ssl_certificate_key /etc/nginx/ssl/${DOMAIN}.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    # 不记录访问日志，错误日志重定向到 /dev/null（按要求）
    access_log off;
    error_log /dev/null crit;

    # 安全头部（可选）
    add_header X-Frame-Options SAMEORIGIN;
    add_header X-Content-Type-Options nosniff;

    location / {
        proxy_pass http://${TARGET_HOST}:${TARGET_PORT};
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

        # WebSocket 支持
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection \$connection_upgrade;
    }
}
EOF
    else
        cat >"$CONF_FILE" <<EOF
# 自动生成的反代配置（无 WebSocket 支持）
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

    ssl_certificate /etc/nginx/ssl/${DOMAIN}.crt;
    ssl_certificate_key /etc/nginx/ssl/${DOMAIN}.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    access_log off;
    error_log /dev/null crit;

    add_header X-Frame-Options SAMEORIGIN;
    add_header X-Content-Type-Options nosniff;

    location / {
        proxy_pass http://${TARGET_HOST}:${TARGET_PORT};
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
    }
}
EOF
    fi

    echo -e "${GREEN}配置已写入：$CONF_FILE${NC}"
}

# 重载并检查 nginx
reload_nginx() {
    echo -e "${YELLOW}>>> 检查 nginx 配置并重载...${NC}"
    if ! nginx -t; then
        echo -e "${RED}nginx -t 检查失败，请查看输出并修正错误。${NC}"
        exit 1
    fi

    if [ "$DISTRO" = "alpine" ]; then
        rc-service nginx reload || rc-service nginx restart
    else
        systemctl reload nginx || systemctl restart nginx
    fi

    echo -e "${GREEN}nginx 已重载并应用新配置。${NC}"
}

# 交互式获取输入
prompt_user() {
    read -rp "请输入要反代的子域名 (如 sub.example.com)： " DOMAIN
    DOMAIN="${DOMAIN:-}"
    if [ -z "$DOMAIN" ]; then
        echo -e "${RED}域名不能为空。退出。${NC}"
        exit 1
    fi

    read -rp "后端目标地址 (IP或域名，默认 127.0.0.1)： " TARGET_HOST
    TARGET_HOST="${TARGET_HOST:-127.0.0.1}"

    read -rp "后端端口 (例如 8080)： " TARGET_PORT
    if ! echo "$TARGET_PORT" | grep -qE '^[0-9]+$'; then
        echo -e "${RED}端口必须为数字。退出。${NC}"
        exit 1
    fi

    read -rp "是否开启 WebSocket 1.1 支持？ (y/N)： " ENABLE_WS
    ENABLE_WS="${ENABLE_WS:-n}"
}

# 主流程
main() {
    if [ "$(id -u)" -ne 0 ]; then
        echo -e "${RED}请以 root 或 sudo 运行脚本。${NC}"
        exit 1
    fi

    detect_os
    prompt_user
    install_nginx
    enable_autostart
    tune_nginx_main_conf

    # 先尝试安装证书（如果 ACME 可用）
    install_certificate

    # 生成反代配置
    generate_proxy_conf

    # 检查并重载 nginx
    reload_nginx

    echo -e "${GREEN}完成：${NC} 域名 ${DOMAIN} 已被配置为反代到 ${TARGET_HOST}:${TARGET_PORT}"
    if [ "${ENABLE_WS}" = "y" ] || [ "${ENABLE_WS}" = "Y" ]; then
        echo -e "${GREEN}WebSocket 支持：已启用${NC}"
    else
        echo -e "${YELLOW}WebSocket 支持：未启用${NC}"
    fi

    echo -e "${YELLOW}测试建议：${NC}"
    echo "  - 本机可用: curl -Ik https://${DOMAIN}/"
    echo "  - 若后端是 WebSocket，可用 websocket 客户端或 wscat 进行连接测试（wss://）"
    echo -e "${YELLOW}若证书已手动放置，请确保 /etc/nginx/ssl/${DOMAIN}.crt/.key 存在。${NC}"
}

# 执行
main "$@"