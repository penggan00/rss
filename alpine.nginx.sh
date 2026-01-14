#!/bin/bash
set -euo pipefail

# ==================== 全局配置与颜色定义 ====================
# 颜色常量（提升交互体验）
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # 恢复默认颜色

# 核心配置（可根据需求调整）
ACME_DIR="${HOME}/.acme.sh"  # acme.sh 目录（与你的证书安装逻辑对齐）
NGINX_CONF_DIR="/etc/nginx/conf.d"  # Nginx 虚拟主机配置目录
NGINX_MAIN_CONF="/etc/nginx/nginx.conf"  # Nginx 主配置文件
LISTEN_PORTS="80 443"  # 监听的 HTTP/HTTPS 端口
DISABLE_LOGS=true  # 禁用 Nginx 日志（符合需求）

# ==================== 前置函数：系统检测与工具准备 ====================
# 检测当前操作系统发行版
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

# 检查并安装必要依赖（curl、sudo 等）
install_dependencies() {
    local OS=$(detect_os)
    echo -e "${BLUE}>>> 正在安装系统必要依赖...${NC}"
    
    if [ "$OS" = "alpine" ]; then
        apk update > /dev/null 2>&1
        apk add --no-cache curl sudo openssl nginx-ssl  # Alpine 直接安装 nginx-ssl（自带 SSL 支持）
    else
        apt update > /dev/null 2>&1
        apt install -y --no-install-recommends curl sudo openssl gnupg2 ca-certificates
        # Debian 添加 Nginx 官方源（保证版本稳定，避免系统自带旧版本）
        if ! [ -f /etc/apt/sources.list.d/nginx.list ]; then
            echo "deb http://nginx.org/packages/debian $(lsb_release -cs) nginx" | sudo tee /etc/apt/sources.list.d/nginx.list
            curl -fsSL https://nginx.org/keys/nginx_signing.key | sudo gpg --dearmor -o /etc/apt/trusted.gpg.d/nginx.gpg
            apt update > /dev/null 2>&1
        fi
        apt install -y --no-install-recommends nginx
    fi
}

# ==================== 核心功能：旧版 Nginx 清理与环境初始化 ====================
# 完全清空旧版不兼容 Nginx（配置、程序、数据全删除）
clean_old_nginx() {
    echo -e "${YELLOW}>>> 正在清空旧版 Nginx 环境...${NC}"
    local OS=$(detect_os)
    
    # 停止正在运行的 Nginx 进程
    if pgrep nginx > /dev/null 2>&1; then
        sudo systemctl stop nginx > /dev/null 2>&1 || sudo pkill nginx > /dev/null 2>&1
    fi
    
    # 卸载 Nginx 程序
    if [ "$OS" = "alpine" ]; then
        apk del --purge nginx nginx-ssl > /dev/null 2>&1 || true
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

# 初始化 Nginx 新环境（安装+基础配置+自启+目录准备）
init_nginx_environment() {
    # 先清理旧环境
    clean_old_nginx
    
    # 安装依赖与新版 Nginx
    install_dependencies
    
    # 配置 Nginx 主配置（禁用日志、开启 IPv4/IPv6、优化性能）
    configure_nginx_main() {
        echo -e "${BLUE}>>> 正在配置 Nginx 主配置文件...${NC}"
        
        # 备份原始主配置（防止意外）
        sudo cp "$NGINX_MAIN_CONF" "${NGINX_MAIN_CONF}.bak" > /dev/null 2>&1 || true
        
        # 写入优化后的主配置（无日志、双栈监听、性能调优）
        sudo tee "$NGINX_MAIN_CONF" > /dev/null << EOF
user nginx;
worker_processes auto;  # 自动匹配 CPU 核心数（性能优化）
error_log /dev/null;  # 禁用错误日志（符合需求）
pid /var/run/nginx.pid;

events {
    worker_connections 10240;  # 提高最大连接数（高并发优化）
    use epoll;  # 高效 I/O 模型（Linux 专属，Alpine/Debian 均支持）
    multi_accept on;  # 一次性接受所有新连接（性能优化）
}

http {
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;

    # 禁用访问日志和日志缓冲区（符合需求，减少磁盘 I/O）
    access_log /dev/null;
    log_not_found off;
    client_body_buffer_size 128k;  # 客户端请求体缓冲区（优化小请求）
    client_header_buffer_size 1k;  # 客户端请求头缓冲区
    large_client_header_buffers 4 4k;  # 大请求头缓冲区

    # 连接优化
    keepalive_timeout 65;  # 长连接超时时间
    keepalive_requests 1000;  # 单个长连接最大请求数（高并发优化）
    tcp_nodelay on;  # 禁用 Nagle 算法（降低延迟，适合反代）
    tcp_nopush on;  # 开启 TCP 推送（提高静态资源传输效率）

    # Gzip 压缩（优化传输速度，反代场景推荐开启）
    gzip on;
    gzip_vary on;
    gzip_min_length 1024;
    gzip_proxied any;
    gzip_types text/plain text/css text/javascript application/json application/javascript application/xml+rss application/xhtml+xml;
    gzip_comp_level 6;  # 压缩级别（平衡压缩比和性能）

    # IPv6 支持（强制开启，监听双栈）
    ipv6only off;
    include /etc/nginx/conf.d/*.conf;  # 引入虚拟主机配置
}
EOF
    }
    
    # 执行主配置
    configure_nginx_main
    
    # 创建必要目录（配置、证书、运行目录）
    sudo mkdir -p "$NGINX_CONF_DIR" \
                 "/etc/nginx/ssl/certs" \
                 "/etc/nginx/ssl/private" \
                 "/var/run/nginx" \
                 "/var/lib/nginx/tmp" > /dev/null 2>&1
    
    # 设置目录权限（Nginx 可读写）
    sudo chown -R nginx:nginx /var/run/nginx /var/lib/nginx /etc/nginx/ssl > /dev/null 2>&1
    sudo chmod 700 /etc/nginx/ssl/private > /dev/null 2>&1  # 私钥目录严格权限（安全优化）
    
    # 配置 Nginx 自启并启动服务
    echo -e "${BLUE}>>> 正在配置 Nginx 开机自启并启动服务...${NC}"
    sudo systemctl enable nginx > /dev/null 2>&1
    if ! sudo systemctl start nginx; then
        echo -e "${RED}>>> 错误：Nginx 启动失败，请检查配置${NC}"
        exit 1
    fi
    
    echo -e "${GREEN}>>> Nginx 新环境初始化完成，服务已启动并设置开机自启${NC}"
}

# ==================== 核心功能：证书安装（沿用你的逻辑并优化） ====================
install_certificate() {
    local DOMAIN=$1
    if [ -z "$DOMAIN" ] || ! [ -d "$ACME_DIR" ]; then
        echo -e "${RED}>>> 错误：子域名为空或 acme.sh 目录不存在${NC}"
        exit 1
    fi
    
    echo -e "${YELLOW}>>> 安装证书到 Nginx...${NC}"
    
    # 创建证书目录（避免重复创建报错，添加 -p 参数）
    mkdir -p "/etc/nginx/ssl/certs/$DOMAIN" \
             "/etc/nginx/ssl/private/$DOMAIN" \
             "/etc/nginx/ssl"
    
    cd "$ACME_DIR" || { echo -e "${RED}>>> 错误：无法进入 acme.sh 目录${NC}"; exit 1; }
    
    # 安装证书（优化：添加 --force 确保覆盖旧证书，reloadcmd 改为实际重载 Nginx）
    ./acme.sh --install-cert -d "$DOMAIN" \
        --key-file "/etc/nginx/ssl/private/$DOMAIN/key.pem" \
        --fullchain-file "/etc/nginx/ssl/certs/$DOMAIN/fullchain.pem" \
        --cert-file "/etc/nginx/ssl/certs/$DOMAIN/cert.pem" \
        --ca-file "/etc/nginx/ssl/certs/$DOMAIN/ca.pem" \
        --force \
        --reloadcmd "sudo systemctl reload nginx"
    
    # 创建符号链接（简化配置引用）
    ln -sf "/etc/nginx/ssl/certs/$DOMAIN/fullchain.pem" "/etc/nginx/ssl/$DOMAIN.crt"
    ln -sf "/etc/nginx/ssl/private/$DOMAIN/key.pem" "/etc/nginx/ssl/$DOMAIN.key"
    
    # 验证证书文件是否存在（安全校验）
    if ! [ -f "/etc/nginx/ssl/$DOMAIN.crt" ] || ! [ -f "/etc/nginx/ssl/$DOMAIN.key" ]; then
        echo -e "${RED}>>> 错误：证书安装失败，文件不存在${NC}"
        exit 1
    fi
    
    echo -e "${GREEN}>>> 证书安装完成${NC}"
}

# ==================== 核心功能：反代配置生成（支持 WebSocket 可选） ====================
create_reverse_proxy() {
    # 交互获取用户输入
    echo -e "${BLUE}====================================${NC}"
    read -p "请输入你的子域名（例如：demo.example.com）：" DOMAIN
    read -p "请输入被反代的目标端口（例如：8080）：" TARGET_PORT
    read -p "是否开启 WebSocket 1.1 支持？（y/n，默认：n）：" WS_ENABLE
    
    # 输入校验
    if [ -z "$DOMAIN" ] || [ -z "$TARGET_PORT" ] || ! [[ "$TARGET_PORT" =~ ^[0-9]{1,5}$ ]] || [ "$TARGET_PORT" -gt 65535 ]; then
        echo -e "${RED}>>> 错误：子域名或目标端口输入无效（端口需为 1-65535 之间的数字）${NC}"
        exit 1
    fi
    WS_ENABLE=${WS_ENABLE:-n}  # 默认关闭 WebSocket
    
    # 先安装证书
    install_certificate "$DOMAIN"
    
    # 生成反代配置文件
    local PROXY_CONF="${NGINX_CONF_DIR}/${DOMAIN}.conf"
    echo -e "${BLUE}>>> 正在生成 ${DOMAIN} 的反代配置文件...${NC}"
    
    # 写入反代配置（支持 IPv4/IPv6 双栈监听、HTTPS 强制、可选 WebSocket）
    sudo tee "$PROXY_CONF" > /dev/null << EOF
# ${DOMAIN} 反代配置（自动生成，禁用日志）
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;
    
    # HTTP 强制跳转 HTTPS（SEO 优化+安全优化）
    return 301 https://\$host\$request_uri;
    access_log /dev/null;
    error_log /dev/null;
}

server {
    listen 443 ssl http2;  # 开启 HTTP/2（优化传输速度，比 HTTP/1.1 更高效）
    listen [::]:443 ssl http2;
    server_name $DOMAIN;

    # SSL 配置（安全优化，禁用弱加密算法，开启 TLS 1.2/1.3）
    ssl_certificate /etc/nginx/ssl/$DOMAIN.crt;
    ssl_certificate_key /etc/nginx/ssl/$DOMAIN.key;
    ssl_protocols TLSv1.2 TLSv1.3;  # 禁用旧版 TLS 1.0/1.1（安全优化）
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:DHE-RSA-AES128-GCM-SHA256:DHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers on;
    ssl_session_cache shared:SSL:10m;  # SSL 会话缓存（优化 TLS 握手性能）
    ssl_session_timeout 10m;
    ssl_stapling on;  # OCSP 装订（优化 SSL 验证速度，提高安全性）
    ssl_stapling_verify on;
    resolver 1.1.1.1 8.8.8.8 valid=300s;  # 公共 DNS 解析（保证 OCSP 正常工作）
    resolver_timeout 5s;

    # 禁用日志（符合需求）
    access_log /dev/null;
    error_log /dev/null;

    # 反代核心配置
    location / {
        proxy_pass http://127.0.0.1:$TARGET_PORT;  # 转发到本地目标端口
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;  # 传递真实客户端 IP（被反代服务可获取真实 IP）
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;  # 传递协议类型（HTTP/HTTPS）
        proxy_set_header X-Forwarded-Port \$server_port;

        # 连接超时配置（优化反代稳定性，避免长时间无响应断开）
        proxy_connect_timeout 30s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;

        # 缓冲区配置（优化传输性能，减少磁盘 I/O）
        proxy_buffering on;
        proxy_buffer_size 16k;
        proxy_buffers 4 64k;
        proxy_busy_buffers_size 128k;
EOF

    # 可选：添加 WebSocket 1.1 支持配置
    if [ "$WS_ENABLE" = "y" ] || [ "$WS_ENABLE" = "Y" ]; then
        echo -e "${YELLOW}>>> 已开启 WebSocket 1.1 支持${NC}"
        sudo tee -a "$PROXY_CONF" > /dev/null << EOF
        # WebSocket 1.1 支持配置（RFC 6455 标准）
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 86400s;  # WebSocket 长连接超时时间（24 小时，优化稳定性）
EOF
    fi

    # 闭合配置文件
    sudo tee -a "$PROXY_CONF" > /dev/null << EOF
    }
}
EOF

    # 验证 Nginx 配置语法（避免配置错误导致服务异常）
    echo -e "${BLUE}>>> 正在验证 Nginx 配置语法...${NC}"
    if ! sudo nginx -t > /dev/null 2>&1; then
        echo -e "${RED}>>> 错误：Nginx 配置语法有误，请检查${NC}"
        sudo rm -f "$PROXY_CONF"  # 删除错误配置
        exit 1
    fi
    
    # 重载 Nginx 配置（无需重启，平滑生效，不中断现有连接）
    sudo systemctl reload nginx
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
            # 检查 Nginx 是否已安装
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