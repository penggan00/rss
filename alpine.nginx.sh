#!/bin/bash
set -euo pipefail

# ==================== 全局配置与颜色定义 ====================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

ACME_DIR="${HOME}/.acme.sh"
NGINX_CONF_DIR="/etc/nginx/conf.d"
NGINX_MAIN_CONF="/etc/nginx/nginx.conf"
LISTEN_PORTS="80 443"
DISABLE_LOGS=true

# ==================== 前置函数：系统检测与工具准备 ====================
detect_os() {
    if [ -f /etc/alpine-release ]; then
        echo "alpine"
    elif [ -f /etc/debian_version ] || [ -f /etc/ubuntu_version ]; then
        echo "debian"
    else
        echo -e "${RED}>>> 错误：不支持当前操作系统，仅支持 Alpine Linux 和 Debian/Ubuntu 系列${NC}"
        exit 1
    fi
}

install_dependencies() {
    local OS=$(detect_os)
    echo -e "${BLUE}>>> 正在安装系统必要依赖...${NC}"
    
    if [ "$OS" = "alpine" ]; then
        apk update > /dev/null 2>&1
        # 修复：移除 nginx-ssl，直接安装 nginx（内置 SSL），补充 openrc（服务管理）、sudo
        apk add --no-cache curl sudo openssl nginx openrc
    else
        apt update > /dev/null 2>&1
        apt install -y --no-install-recommends curl sudo openssl gnupg2 ca-certificates
        if ! [ -f /etc/apt/sources.list.d/nginx.list ]; then
            echo "deb http://nginx.org/packages/debian $(lsb_release -cs) nginx" | sudo tee /etc/apt/sources.list.d/nginx.list
            curl -fsSL https://nginx.org/keys/nginx_signing.key | sudo gpg --dearmor -o /etc/apt/trusted.gpg.d/nginx.gpg
            apt update > /dev/null 2>&1
        fi
        apt install -y --no-install-recommends nginx
    fi
}

# ==================== 核心功能：旧版 Nginx 清理与环境初始化 ====================
clean_old_nginx() {
    echo -e "${YELLOW}>>> 正在清空旧版 Nginx 环境...${NC}"
    local OS=$(detect_os)
    
    # 停止正在运行的 Nginx 进程（兼容 Alpine 和 Debian）
    if pgrep nginx > /dev/null 2>&1; then
        if [ "$OS" = "alpine" ]; then
            sudo rc-service nginx stop > /dev/null 2>&1 || sudo pkill nginx > /dev/null 2>&1
        else
            sudo systemctl stop nginx > /dev/null 2>&1 || sudo pkill nginx > /dev/null 2>&1
        fi
    fi
    
    # 卸载 Nginx 程序（修复：Alpine 仅卸载 nginx，移除 nginx-ssl）
    if [ "$OS" = "alpine" ]; then
        apk del --purge nginx > /dev/null 2>&1 || true
    else
        apt remove -y --purge nginx nginx-common nginx-full > /dev/null 2>&1 || true
        apt autoremove -y --purge > /dev/null 2>&1
    fi
    
    # 删除残留配置、证书、日志目录
    sudo rm -rf /etc/nginx \
                /var/lib/nginx \
                /var/log/nginx \
                /usr/local/nginx \
                /home/*/.acme.sh/nginx* > /dev/null 2>&1 || true
    
    echo -e "${GREEN}>>> 旧版 Nginx 清理完成${NC}"
}

init_nginx_environment() {
    clean_old_nginx
    install_dependencies
    
    configure_nginx_main() {
        echo -e "${BLUE}>>> 正在配置 Nginx 主配置文件...${NC}"
        sudo cp "$NGINX_MAIN_CONF" "${NGINX_MAIN_CONF}.bak" > /dev/null 2>&1 || true
        
        sudo tee "$NGINX_MAIN_CONF" > /dev/null << EOF
user nginx;
worker_processes auto;
error_log /dev/null;
pid /var/run/nginx.pid;

events {
    worker_connections 10240;
    use epoll;
    multi_accept on;
}

http {
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;

    access_log /dev/null;
    log_not_found off;
    client_body_buffer_size 128k;
    client_header_buffer_size 1k;
    large_client_header_buffers 4 4k;

    keepalive_timeout 65;
    keepalive_requests 1000;
    tcp_nodelay on;
    tcp_nopush on;

    gzip on;
    gzip_vary on;
    gzip_min_length 1024;
    gzip_proxied any;
    gzip_types text/plain text/css text/javascript application/json application/javascript application/xml+rss application/xhtml+xml;
    gzip_comp_level 6;

    ipv6only off;
    include /etc/nginx/conf.d/*.conf;
}
EOF
    }
    
    configure_nginx_main
    
    # 创建必要目录并设置权限
    sudo mkdir -p "$NGINX_CONF_DIR" \
                 "/etc/nginx/ssl/certs" \
                 "/etc/nginx/ssl/private" \
                 "/var/run/nginx" \
                 "/var/lib/nginx/tmp" > /dev/null 2>&1
    sudo chown -R nginx:nginx /var/run/nginx /var/lib/nginx /etc/nginx/ssl > /dev/null 2>&1
    sudo chmod 700 /etc/nginx/ssl/private > /dev/null 2>&1
    
    # 配置 Nginx 自启并启动服务（兼容 Alpine openrc 和 Debian systemd）
    echo -e "${BLUE}>>> 正在配置 Nginx 开机自启并启动服务...${NC}"
    local OS=$(detect_os)
    if [ "$OS" = "alpine" ]; then
        # Alpine：使用 openrc 配置自启并启动
        sudo rc-update add nginx default > /dev/null 2>&1
        sudo rc-service nginx start > /dev/null 2>&1
    else
        # Debian：使用 systemctl 配置自启并启动
        sudo systemctl enable nginx > /dev/null 2>&1
        sudo systemctl start nginx > /dev/null 2>&1
    fi
    
    # 验证 Nginx 是否启动成功
    if ! pgrep nginx > /dev/null 2>&1; then
        echo -e "${RED}>>> 错误：Nginx 启动失败，请检查配置${NC}"
        exit 1
    fi
    
    echo -e "${GREEN}>>> Nginx 新环境初始化完成，服务已启动并设置开机自启${NC}"
}

# ==================== 核心功能：证书安装（沿用原逻辑优化） ====================
install_certificate() {
    local DOMAIN=$1
    if [ -z "$DOMAIN" ] || ! [ -d "$ACME_DIR" ]; then
        echo -e "${RED}>>> 错误：子域名为空或 acme.sh 目录不存在${NC}"
        exit 1
    fi
    
    echo -e "${YELLOW}>>> 安装证书到 Nginx...${NC}"
    
    mkdir -p "/etc/nginx/ssl/certs/$DOMAIN" \
             "/etc/nginx/ssl/private/$DOMAIN" \
             "/etc/nginx/ssl"
    
    cd "$ACME_DIR" || { echo -e "${RED}>>> 错误：无法进入 acme.sh 目录${NC}"; exit 1; }
    
    ./acme.sh --install-cert -d "$DOMAIN" \
        --key-file "/etc/nginx/ssl/private/$DOMAIN/key.pem" \
        --fullchain-file "/etc/nginx/ssl/certs/$DOMAIN/fullchain.pem" \
        --cert-file "/etc/nginx/ssl/certs/$DOMAIN/cert.pem" \
        --ca-file "/etc/nginx/ssl/certs/$DOMAIN/ca.pem" \
        --force \
        --reloadcmd "if [ \$(detect_os) = 'alpine' ]; then rc-service nginx reload; else systemctl reload nginx; fi"
    
    ln -sf "/etc/nginx/ssl/certs/$DOMAIN/fullchain.pem" "/etc/nginx/ssl/$DOMAIN.crt"
    ln -sf "/etc/nginx/ssl/private/$DOMAIN/key.pem" "/etc/nginx/ssl/$DOMAIN.key"
    
    if ! [ -f "/etc/nginx/ssl/$DOMAIN.crt" ] || ! [ -f "/etc/nginx/ssl/$DOMAIN.key" ]; then
        echo -e "${RED}>>> 错误：证书安装失败，文件不存在${NC}"
        exit 1
    fi
    
    echo -e "${GREEN}>>> 证书安装完成${NC}"
}

# ==================== 核心功能：反代配置生成（支持 WebSocket 可选） ====================
create_reverse_proxy() {
    echo -e "${BLUE}====================================${NC}"
    read -p "请输入你的子域名（例如：demo.example.com）：" DOMAIN
    read -p "请输入被反代的目标端口（例如：8080）：" TARGET_PORT
    read -p "是否开启 WebSocket 1.1 支持？（y/n，默认：n）：" WS_ENABLE
    
    if [ -z "$DOMAIN" ] || [ -z "$TARGET_PORT" ] || ! [[ "$TARGET_PORT" =~ ^[0-9]{1,5}$ ]] || [ "$TARGET_PORT" -gt 65535 ]; then
        echo -e "${RED}>>> 错误：子域名或目标端口输入无效（端口需为 1-65535 之间的数字）${NC}"
        exit 1
    fi
    WS_ENABLE=${WS_ENABLE:-n}
    
    install_certificate "$DOMAIN"
    
    local PROXY_CONF="${NGINX_CONF_DIR}/${DOMAIN}.conf"
    echo -e "${BLUE}>>> 正在生成 ${DOMAIN} 的反代配置文件...${NC}"
    
    sudo tee "$PROXY_CONF" > /dev/null << EOF
# ${DOMAIN} 反代配置（自动生成，禁用日志）
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;
    
    return 301 https://\$host\$request_uri;
    access_log /dev/null;
    error_log /dev/null;
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name $DOMAIN;

    ssl_certificate /etc/nginx/ssl/$DOMAIN.crt;
    ssl_certificate_key /etc/nginx/ssl/$DOMAIN.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:DHE-RSA-AES128-GCM-SHA256:DHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers on;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;
    ssl_stapling on;
    ssl_stapling_verify on;
    resolver 1.1.1.1 8.8.8.8 valid=300s;
    resolver_timeout 5s;

    access_log /dev/null;
    error_log /dev/null;

    location / {
        proxy_pass http://127.0.0.1:$TARGET_PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Forwarded-Port \$server_port;

        proxy_connect_timeout 30s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;

        proxy_buffering on;
        proxy_buffer_size 16k;
        proxy_buffers 4 64k;
        proxy_busy_buffers_size 128k;
EOF

    if [ "$WS_ENABLE" = "y" ] || [ "$WS_ENABLE" = "Y" ]; then
        echo -e "${YELLOW}>>> 已开启 WebSocket 1.1 支持${NC}"
        sudo tee -a "$PROXY_CONF" > /dev/null << EOF
        # WebSocket 1.1 支持配置（RFC 6455 标准）
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 86400s;
EOF
    fi

    sudo tee -a "$PROXY_CONF" > /dev/null << EOF
    }
}
EOF

    echo -e "${BLUE}>>> 正在验证 Nginx 配置语法...${NC}"
    if ! sudo nginx -t > /dev/null 2>&1; then
        echo -e "${RED}>>> 错误：Nginx 配置语法有误，请检查${NC}"
        sudo rm -f "$PROXY_CONF"
        exit 1
    fi
    
    # 平滑重载 Nginx（兼容 Alpine 和 Debian）
    local OS=$(detect_os)
    if [ "$OS" = "alpine" ]; then
        sudo rc-service nginx reload > /dev/null 2>&1
    else
        sudo systemctl reload nginx > /dev/null 2>&1
    fi
    
    echo -e "${GREEN}>>> ${DOMAIN} 反代配置生效成功！${NC}"
    echo -e "${GREEN}>>> 可通过 https://${DOMAIN} 访问被反代的 ${TARGET_PORT} 端口服务${NC}"
}

# ==================== 交互菜单：主程序入口 ====================
main_menu() {
    clear
    echo -e "${BLUE}====================================${NC}"
    echo -e "${GREEN}      Nginx 智能交互反代脚本${NC}"
    echo -e "${GREEN}      支持 Alpine/Debian 双系统${NC}"
    echo -e "${BLUE}====================================${NC}"
    echo "1. 初始化 Nginx 新环境（清空旧版+安装+自启）"
    echo "2. 添加新的 HTTPS 反代配置（重载 Nginx，不中断服务）"
    echo "3. 退出脚本"
    echo -e "${BLUE}====================================${NC}"
    read -p "请选择操作选项（1/2/3）：" OPTION
    
    case "$OPTION" in
        1)
            init_nginx_environment
            ;;
        2)
            if ! command -v nginx > /dev/null 2>&1; then
                echo -e "${YELLOW}>>> 未检测到 Nginx，将先初始化 Nginx 环境...${NC}"
                init_nginx_environment
            fi
            create_reverse_proxy
            ;;
        3)
            echo -e "${GREEN}>>> 脚本退出，感谢使用${NC}"
            exit 0
            ;;
        *)
            echo -e "${RED}>>> 错误：无效选项，请重新选择${NC}"
            sleep 2
            main_menu
            ;;
    esac
}

# 启动主菜单
main_menu