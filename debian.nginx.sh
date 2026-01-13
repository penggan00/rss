#!/bin/bash

# ================= Debian 12 Nginx 反向代理管理器 =================
# 专为 Debian 12 设计，使用 systemd、ufw
# ================================================================

NGINX_CONF_DIR="/etc/nginx/conf.d"
SSL_DIR="/etc/nginx/ssl"
DOMAIN_LIST="/opt/cert-manager/config/domains.list"
NGINX_LOG_DIR="/var/log/nginx"

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# 检查 Root 权限
if [ "$(id -u)" -ne 0 ]; then
    echo -e "${RED}错误: 必须使用 root 权限运行此脚本${NC}"
    exit 1
fi

# 检查是否为 Debian 12
check_debian() {
    if [ ! -f /etc/debian_version ]; then
        echo -e "${RED}错误: 此脚本仅适用于 Debian 系统${NC}"
        exit 1
    fi
    
    DEBIAN_VERSION=$(cat /etc/debian_version)
    if [[ ! "$DEBIAN_VERSION" =~ ^12 ]]; then
        echo -e "${YELLOW}警告: 此脚本专为 Debian 12 设计，当前版本: $DEBIAN_VERSION${NC}"
        read -p "继续? (y/N): " CONTINUE
        if [ "$CONTINUE" != "y" ] && [ "$CONTINUE" != "Y" ]; then
            exit 1
        fi
    fi
    
    echo -e "${GREEN}检测到 Debian $DEBIAN_VERSION${NC}"
}

# 检查证书管理器
check_cert_manager() {
    if [ ! -f "$DOMAIN_LIST" ]; then
        echo -e "${RED}错误: 证书管理器未安装${NC}"
        echo "请先运行证书申请脚本:"
        echo "bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/penggan00/rss/main/https.sh)\""
        exit 1
    fi
    
    if [ ! -s "$DOMAIN_LIST" ]; then
        echo -e "${RED}错误: 没有可用的证书${NC}"
        echo "请先申请证书"
        exit 1
    fi
}

# 初始化 Nginx
init_nginx() {
    echo -e "${YELLOW}>>> 初始化 Nginx...${NC}"
    
    # 安装 Nginx（如果未安装）
    if ! command -v nginx &> /dev/null; then
        echo -e "${YELLOW}安装 Nginx...${NC}"
        apt-get update
        apt-get install -y nginx
    fi
    
    # 创建目录
    mkdir -p "$NGINX_CONF_DIR" "$SSL_DIR" "$NGINX_LOG_DIR"
    mkdir -p /var/lib/nginx
    
    # 创建默认证书
    if [ ! -f "$SSL_DIR/fallback.key" ]; then
        echo -e "${YELLOW}生成默认证书...${NC}"
        openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
            -keyout "$SSL_DIR/fallback.key" \
            -out "$SSL_DIR/fallback.crt" \
            -subj "/CN=Invalid" 2>/dev/null
        chmod 600 "$SSL_DIR/fallback.key"
    fi
    
    # 创建 Nginx 主配置
    if [ ! -f /etc/nginx/nginx.conf ] || ! grep -q "conf.d" /etc/nginx/nginx.conf; then
        echo -e "${YELLOW}创建 Nginx 配置...${NC}"
        cat > /etc/nginx/nginx.conf <<'EOF'
user www-data;
worker_processes auto;
pid /run/nginx.pid;
include /etc/nginx/modules-enabled/*.conf;

events {
    worker_connections 1024;
    multi_accept on;
    use epoll;
}

http {
    # 基础设置
    sendfile on;
    tcp_nopush on;
    tcp_nodelay on;
    keepalive_timeout 65;
    types_hash_max_size 2048;
    server_tokens off;
    
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    
    # 日志
    access_log /var/log/nginx/access.log;
    error_log /var/log/nginx/error.log;
    
    # Gzip 压缩
    gzip on;
    gzip_vary on;
    gzip_proxied any;
    gzip_comp_level 6;
    gzip_types text/plain text/css text/xml text/javascript 
               application/json application/javascript application/xml+rss 
               application/atom+xml image/svg+xml;
    
    # SSL 设置
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;
    
    # 包含用户配置
    include /etc/nginx/conf.d/*.conf;
    
    # 默认服务器 - 禁止直接IP访问
    server {
        listen 80 default_server;
        listen [::]:80 default_server;
        listen 443 ssl default_server;
        listen [::]:443 ssl default_server;
        server_name _;
        
        ssl_certificate /etc/nginx/ssl/fallback.crt;
        ssl_certificate_key /etc/nginx/ssl/fallback.key;
        
        return 444;
    }
}
EOF
    fi
    
    # 设置权限
    chown -R www-data:www-data "$SSL_DIR"
    chmod 750 "$SSL_DIR"
    
    # 启动 Nginx 服务
    systemctl enable nginx 2>/dev/null
    systemctl restart nginx 2>/dev/null
    
    echo -e "${GREEN}>>> Nginx 初始化完成${NC}"
}

# 选择域名
select_domain() {
    echo -e "${YELLOW}可用的证书域名:${NC}"
    
    if [ ! -s "$DOMAIN_LIST" ]; then
        echo -e "${RED}暂无证书，请先申请证书${NC}"
        return 1
    fi
    
    cat -n "$DOMAIN_LIST"
    echo ""
    
    read -p "请选择域名编号: " DOMAIN_NUM
    
    if [[ ! "$DOMAIN_NUM" =~ ^[0-9]+$ ]]; then
        echo -e "${RED}请输入数字编号${NC}"
        return 1
    fi
    
    DOMAIN=$(sed -n "${DOMAIN_NUM}p" "$DOMAIN_LIST" 2>/dev/null)
    
    if [ -z "$DOMAIN" ]; then
        echo -e "${RED}无效选择${NC}"
        return 1
    fi
    
    # 检查证书文件
    if [ -f "$SSL_DIR/$DOMAIN.crt" ] && [ -f "$SSL_DIR/$DOMAIN.key" ]; then
        echo -e "${GREEN}选择域名: $DOMAIN${NC}"
        return 0
    else
        echo -e "${RED}错误: 找不到 $DOMAIN 的证书文件${NC}"
        return 1
    fi
}

# 添加反向代理
add_proxy() {
    echo -e "${YELLOW}=== 添加反向代理 ===${NC}"
    
    # 选择域名
    if ! select_domain; then
        return 1
    fi
    
    echo ""
    echo "当前域名: $DOMAIN"
    echo "示例: 输入 'api' 会生成 api.$DOMAIN"
    echo ""
    
    read -p "请输入子域名前缀 (如 api): " PREFIX
    
    # 清理前缀
    PREFIX=$(echo "$PREFIX" | sed "s/\.$DOMAIN//g" | sed "s/\.$//g")
    
    if [ -z "$PREFIX" ]; then
        echo -e "${RED}前缀不能为空${NC}"
        return 1
    fi
    
    read -p "请输入后端端口号 (例如 52655): " PORT
    
    # 校验端口
    if ! [[ "$PORT" =~ ^[0-9]+$ ]] || [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then
        echo -e "${RED}无效端口号${NC}"
        return 1
    fi
    
    FULL_DOMAIN="$PREFIX.$DOMAIN"
    CONF_FILE="$NGINX_CONF_DIR/$FULL_DOMAIN.conf"
    
    # 检查是否已存在配置
    if [ -f "$CONF_FILE" ]; then
        echo -e "${YELLOW}配置已存在: $CONF_FILE${NC}"
        read -p "是否覆盖? (y/N): " OVERWRITE
        if [ "$OVERWRITE" != "y" ] && [ "$OVERWRITE" != "Y" ]; then
            return 1
        fi
    fi
    
    # 证书路径
    CERT_FILE="$SSL_DIR/$DOMAIN.crt"
    KEY_FILE="$SSL_DIR/$DOMAIN.key"
    
    # 检查证书文件
    if [ ! -f "$CERT_FILE" ] || [ ! -f "$KEY_FILE" ]; then
        echo -e "${RED}证书文件不存在${NC}"
        return 1
    fi
    
    # 创建 Nginx 配置
    echo -e "${YELLOW}创建配置: $CONF_FILE${NC}"
    
    cat > "$CONF_FILE" <<EOF
# Debian 12 反向代理配置
# 域名: $FULL_DOMAIN
# 后端: 127.0.0.1:$PORT
# 生成时间: $(date)

# HTTP 重定向到 HTTPS
server {
    listen 80;
    listen [::]:80;
    server_name $FULL_DOMAIN;
    
    # 记录访问日志
    access_log $NGINX_LOG_DIR/${FULL_DOMAIN}_access.log;
    error_log $NGINX_LOG_DIR/${FULL_DOMAIN}_error.log;
    
    # 重定向到 HTTPS
    return 301 https://\$host\$request_uri;
}

# HTTPS 服务器
server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name $FULL_DOMAIN;
    
    # SSL 证书
    ssl_certificate $CERT_FILE;
    ssl_certificate_key $KEY_FILE;
    
    # SSL 配置
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;
    
    # 安全头
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    
    # 日志
    access_log $NGINX_LOG_DIR/${FULL_DOMAIN}_ssl_access.log;
    error_log $NGINX_LOG_DIR/${FULL_DOMAIN}_ssl_error.log;
    
    # 根目录（可选）
    root /var/www/html;
    index index.html index.htm;
    
    # 反向代理配置
    location / {
        proxy_pass http://127.0.0.1:$PORT;
        
        # 基础头部
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Forwarded-Host \$host;
        
        # 超时设置
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
        
        # 缓冲区
        proxy_buffer_size 128k;
        proxy_buffers 4 256k;
        proxy_busy_buffers_size 256k;
        
        # WebSocket 支持
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
    }
    
    # 禁止访问隐藏文件
    location ~ /\. {
        deny all;
        access_log off;
        log_not_found off;
    }
    
    # 静态文件缓存（可选）
    location ~* \.(jpg|jpeg|png|gif|ico|css|js)$ {
        expires 1y;
        add_header Cache-Control "public, immutable";
    }
}
EOF
    
    echo -e "${GREEN}>>> Nginx 配置已创建${NC}"
    
    # 配置 ufw 防火墙
    configure_ufw "$PORT"
    
    # 测试并应用配置
    test_and_reload "$CONF_FILE" "$FULL_DOMAIN" "$PORT"
}

# 配置 ufw 防火墙
configure_ufw() {
    local PORT=$1
    
    echo -e "${YELLOW}>>> 配置 UFW 防火墙...${NC}"
    
    # 检查 ufw 是否安装
    if ! command -v ufw &> /dev/null; then
        echo -e "${YELLOW}安装 UFW...${NC}"
        apt-get update
        apt-get install -y ufw
    fi
    
    # 启用 ufw（如果未启用）
    if ! ufw status | grep -q "Status: active"; then
        echo -e "${YELLOW}启用 UFW...${NC}"
        ufw --force enable
    fi
    
    # 检查规则是否已存在
    if ufw status | grep -q "$PORT/tcp.*DENY"; then
        echo "端口 $PORT 已被封锁，跳过"
        return
    fi
    
    # 添加规则：禁止外部访问，允许本地访问
    ufw deny "$PORT/tcp" 2>/dev/null
    ufw allow from 127.0.0.1 to any port "$PORT" 2>/dev/null
    
    echo -e "${GREEN}端口 $PORT 已加锁 (UFW)${NC}"
}

# 测试并重载 Nginx
test_and_reload() {
    local CONF_FILE="$1"
    local FULL_DOMAIN="$2"
    local PORT="$3"
    
    echo -e "${YELLOW}>>> 测试 Nginx 配置...${NC}"
    
    # 测试配置
    if nginx -t 2>/dev/null; then
        echo -e "${GREEN}配置测试通过${NC}"
        
        # 重载 Nginx
        systemctl reload nginx 2>/dev/null || nginx -s reload 2>/dev/null
        
        echo -e "${GREEN}>>> 配置成功!${NC}"
        echo ""
        echo -e "${GREEN}========================================${NC}"
        echo -e "${GREEN}      反向代理配置成功                  ${NC}"
        echo -e "${GREEN}========================================${NC}"
        echo ""
        echo "访问地址: https://$FULL_DOMAIN"
        echo "后端地址: http://127.0.0.1:$PORT"
        echo "配置文件: $CONF_FILE"
        echo ""
        echo -e "${YELLOW}防火墙状态:${NC}"
        ufw status | grep "$PORT/tcp" || echo "端口 $PORT: 已封锁（仅限本地访问）"
        echo ""
        echo -e "${YELLOW}下一步:${NC}"
        echo "1. 确保后端服务在端口 $PORT 上运行"
        echo "2. 访问 https://$FULL_DOMAIN 测试"
        echo "3. 如需外部访问后端，运行: ufw delete deny $PORT/tcp"
    else
        echo -e "${RED}配置测试失败${NC}"
        echo "错误信息:"
        nginx -t 2>&1
        echo ""
        echo -e "${YELLOW}正在删除有问题的配置...${NC}"
        rm -f "$CONF_FILE"
        return 1
    fi
}

# 查看配置
list_proxies() {
    echo -e "${YELLOW}=== 当前反向代理配置 ===${NC}"
    
    if [ ! -d "$NGINX_CONF_DIR" ] || [ -z "$(ls -A $NGINX_CONF_DIR/*.conf 2>/dev/null)" ]; then
        echo "暂无配置"
        return
    fi
    
    CONFIGS=$(ls "$NGINX_CONF_DIR"/*.conf 2>/dev/null)
    
    for CONF in $CONFIGS; do
        CONF_NAME=$(basename "$CONF")
        DOMAIN_NAME=$(grep "server_name" "$CONF" | head -1 | awk '{print $2}' | sed 's/;//' 2>/dev/null || echo '未知')
        BACKEND=$(grep "proxy_pass" "$CONF" | head -1 | awk '{print $2}' | sed 's/;//' 2>/dev/null || echo '未知')
        
        echo ""
        echo "配置文件: $CONF_NAME"
        echo "域名: $DOMAIN_NAME"
        echo "后端: $BACKEND"
        
        # 检查证书
        CERT_FILE=$(grep "ssl_certificate" "$CONF" | head -1 | awk '{print $2}' | sed 's/;//' 2>/dev/null)
        if [ -n "$CERT_FILE" ] && [ -f "$CERT_FILE" ]; then
            echo "证书: 有效"
        else
            echo "证书: ${RED}无效${NC}"
        fi
    done
}

# 移除反向代理
remove_proxy() {
    echo -e "${YELLOW}=== 移除反向代理 ===${NC}"
    
    if [ ! -d "$NGINX_CONF_DIR" ] || [ -z "$(ls -A $NGINX_CONF_DIR/*.conf 2>/dev/null)" ]; then
        echo -e "${RED}暂无反向代理配置${NC}"
        return
    fi
    
    echo -e "${YELLOW}当前配置:${NC}"
    ls -1 "$NGINX_CONF_DIR"/*.conf 2>/dev/null | xargs -n1 basename | nl
    
    read -p "请选择要删除的配置编号: " CONF_NUM
    
    if [[ ! "$CONF_NUM" =~ ^[0-9]+$ ]]; then
        echo -e "${RED}请输入数字编号${NC}"
        return
    fi
    
    CONF_NAME=$(ls -1 "$NGINX_CONF_DIR"/*.conf 2>/dev/null | sed -n "${CONF_NUM}p" | xargs basename 2>/dev/null)
    
    if [ -z "$CONF_NAME" ]; then
        echo -e "${RED}无效选择${NC}"
        return
    fi
    
    CONF_FILE="$NGINX_CONF_DIR/$CONF_NAME"
    
    if [ ! -f "$CONF_FILE" ]; then
        echo -e "${RED}配置文件不存在${NC}"
        return
    fi
    
    # 显示配置信息
    echo ""
    echo -e "${YELLOW}配置信息:${NC}"
    echo "文件名: $CONF_NAME"
    DOMAIN_NAME=$(grep "server_name" "$CONF_FILE" | head -1 | awk '{print $2}' | sed 's/;//' 2>/dev/null || echo '未知')
    BACKEND=$(grep "proxy_pass" "$CONF_FILE" | head -1 | awk '{print $2}' | sed 's/;//' 2>/dev/null || echo '未知')
    echo "域名: $DOMAIN_NAME"
    echo "后端: $BACKEND"
    echo ""
    
    # 确认删除
    read -p "确认删除此配置? (y/N): " CONFIRM
    if [ "$CONFIRM" = "y" ] || [ "$CONFIRM" = "Y" ]; then
        # 提取端口号解锁防火墙
        PORT=$(grep -o "proxy_pass.*:[0-9]\+" "$CONF_FILE" | grep -o "[0-9]\+" | head -1)
        
        rm "$CONF_FILE"
        echo -e "${GREEN}配置已删除${NC}"
        
        # 解锁防火墙
        if [ -n "$PORT" ] && command -v ufw &> /dev/null; then
            echo -e "${YELLOW}解锁端口 $PORT ...${NC}"
            ufw delete deny "$PORT/tcp" 2>/dev/null
            ufw delete allow from 127.0.0.1 to any port "$PORT" 2>/dev/null
            echo "防火墙规则已清除"
        fi
        
        # 重载 Nginx
        systemctl reload nginx 2>/dev/null || nginx -s reload 2>/dev/null
        echo "Nginx 已重载"
    else
        echo "取消删除"
    fi
}

# 检查 Nginx 状态
check_nginx_status() {
    echo -e "${YELLOW}=== Nginx 状态检查 ===${NC}"
    
    # 检查服务状态
    if systemctl is-active nginx &>/dev/null; then
        echo -e "${GREEN}Nginx 服务: 运行中${NC}"
    else
        echo -e "${RED}Nginx 服务: 未运行${NC}"
    fi
    
    # 检查配置
    echo -e "${YELLOW}配置检查:${NC}"
    if nginx -t 2>/dev/null; then
        echo -e "${GREEN}配置语法: 正确${NC}"
    else
        echo -e "${RED}配置语法: 错误${NC}"
        nginx -t 2>&1
    fi
    
    # 检查监听端口
    echo -e "${YELLOW}监听端口:${NC}"
    ss -tlnp | grep nginx || echo "Nginx 未监听任何端口"
}

# 重载 Nginx
reload_nginx() {
    echo -e "${YELLOW}>>> 重载 Nginx...${NC}"
    
    if systemctl reload nginx 2>/dev/null; then
        echo -e "${GREEN}Nginx 重载成功${NC}"
    else
        echo -e "${YELLOW}尝试使用 nginx -s reload...${NC}"
        if nginx -s reload 2>/dev/null; then
            echo -e "${GREEN}Nginx 重载成功${NC}"
        else
            echo -e "${RED}Nginx 重载失败，尝试重启...${NC}"
            systemctl restart nginx 2>/dev/null && echo -e "${GREEN}Nginx 重启成功${NC}" || echo -e "${RED}Nginx 重启失败${NC}"
        fi
    fi
}

# 主菜单
main_menu() {
    # 检查系统
    check_debian
    
    # 检查证书
    check_cert_manager
    
    # 初始化 Nginx
    init_nginx
    
    while true; do
        echo ""
        echo -e "${YELLOW}===== Debian 12 Nginx 反向代理管理器 ====="
        echo "1. 添加反向代理"
        echo "2. 移除反向代理"
        echo "3. 查看当前配置"
        echo "4. 重载 Nginx"
        echo "5. 检查 Nginx 状态"
        echo "6. 初始化/修复 Nginx"
        echo "7. 查看防火墙状态"
        echo "0. 退出"
        echo -e "================================================${NC}"
        
        read -p "请选择: " OPT
        
        case $OPT in
            1) add_proxy ;;
            2) remove_proxy ;;
            3) list_proxies ;;
            4) reload_nginx ;;
            5) check_nginx_status ;;
            6) init_nginx ;;
            7) 
                echo -e "${YELLOW}UFW 防火墙状态:${NC}"
                ufw status numbered
                ;;
            0) 
                echo "再见！"
                exit 0
                ;;
            *) echo -e "${RED}无效选项${NC}" ;;
        esac
    done
}

# 运行主函数
main_menu