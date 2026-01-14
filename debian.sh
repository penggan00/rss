#!/bin/bash

# ===================================================
# Debian 12 Nginx 反向代理管理脚本
# 功能：初始化Nginx、管理SSL证书、管理反向代理
# 版本：1.1
# 作者：AI Assistant
# ===================================================

# 配置
INSTALL_DIR="/opt/cert-manager"
ACME_DIR="$INSTALL_DIR/acme.sh"
CONFIG_DIR="$INSTALL_DIR/config"
LOG_DIR="$INSTALL_DIR/logs"
NGINX_CONF_DIR="/etc/nginx/conf.d"
NGINX_SITES_AVAILABLE="/etc/nginx/sites-available"
NGINX_SITES_ENABLED="/etc/nginx/sites-enabled"
SSL_DIR="/etc/nginx/ssl"
CERT_ROOT="/etc/nginx/ssl/certs"
KEY_ROOT="/etc/nginx/ssl/private"
BACKUP_DIR="/etc/nginx/backup"

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# 日志函数
log() {
    echo -e "${GREEN}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} $1"
}

error() {
    echo -e "${RED}[错误]${NC} $1" >&2
}

warn() {
    echo -e "${YELLOW}[警告]${NC} $1"
}

info() {
    echo -e "${BLUE}[信息]${NC} $1"
}

# 检查 Root 权限
check_root() {
    if [ "$(id -u)" -ne 0 ]; then
        error "必须使用 root 权限运行此脚本"
        exit 1
    fi
}

# 安装依赖
install_deps() {
    log "安装系统依赖..."
    apt-get update
    
    # 检查并安装必要软件
    local deps=("curl" "git" "openssl" "certbot" "python3-certbot-nginx")
    
    for dep in "${deps[@]}"; do
        if ! dpkg -l | grep -q "^ii  $dep "; then
            apt-get install -y "$dep"
        fi
    done
    
    # 安装 Nginx
    if ! command -v nginx &> /dev/null; then
        apt-get install -y nginx
    fi
    
    log "依赖安装完成"
}

# 修复 Nginx 配置问题
fix_nginx_config() {
    log "修复 Nginx 配置问题..."
    
    # 检查并创建必要的配置文件
    if [ ! -f "/etc/nginx/mime.types" ]; then
        warn "缺少 mime.types 文件，从 Nginx 包中恢复"
        apt-get install --reinstall -y nginx-common
    fi
    
    # 确保 sites-available 和 sites-enabled 目录存在
    mkdir -p "$NGINX_SITES_AVAILABLE" "$NGINX_SITES_ENABLED"
    
    # 如果 nginx.conf 被破坏，恢复默认配置
    if ! nginx -t 2>/dev/null; then
        warn "Nginx 配置损坏，恢复默认配置..."
        
        # 备份当前配置
        cp /etc/nginx/nginx.conf "$BACKUP_DIR/nginx.conf.broken.$(date +%Y%m%d-%H%M%S)"
        
        # 获取默认配置
        cat > /etc/nginx/nginx.conf << 'EOF'
user www-data;
worker_processes auto;
pid /run/nginx.pid;

events {
    worker_connections 768;
}

http {
    sendfile on;
    tcp_nopush on;
    tcp_nodelay on;
    keepalive_timeout 65;
    types_hash_max_size 2048;
    client_max_body_size 100M;
    
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;
    
    access_log /var/log/nginx/access.log;
    error_log /var/log/nginx/error.log;
    
    gzip on;
    
    include /etc/nginx/conf.d/*.conf;
    include /etc/nginx/sites-enabled/*;
}
EOF
    fi
    
    # 确保默认站点配置存在
    if [ ! -f "$NGINX_SITES_AVAILABLE/default" ]; then
        cat > "$NGINX_SITES_AVAILABLE/default" << 'EOF'
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    
    root /var/www/html;
    index index.html index.htm index.nginx-debian.html;
    
    server_name _;
    
    location / {
        try_files $uri $uri/ =404;
    }
}
EOF
    fi
    
    # 启用默认站点
    ln -sf "$NGINX_SITES_AVAILABLE/default" "$NGINX_SITES_ENABLED/default" 2>/dev/null
    
    log "Nginx 配置修复完成"
}

# 初始化目录和权限
init_dirs() {
    log "初始化目录结构..."
    
    # 创建必要目录
    mkdir -p "$INSTALL_DIR" "$CONFIG_DIR" "$LOG_DIR" "$BACKUP_DIR"
    mkdir -p "$NGINX_CONF_DIR"
    mkdir -p "$SSL_DIR" "$CERT_ROOT" "$KEY_ROOT"
    mkdir -p "/var/www/html"
    
    # 创建日志目录
    mkdir -p "/var/log/nginx/proxy"
    
    # 设置权限
    chmod 755 "$INSTALL_DIR"
    chmod 700 "$KEY_ROOT"
    chmod 644 "$CERT_ROOT" 2>/dev/null || true
    
    # 创建默认首页
    if [ ! -f "/var/www/html/index.html" ]; then
        cat > /var/www/html/index.html << 'EOF'
<!DOCTYPE html>
<html>
<head>
    <title>Nginx 反向代理管理</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 50px; text-align: center; }
        h1 { color: #333; }
        p { color: #666; }
        .status { 
            background: #f0f0f0; 
            padding: 20px; 
            border-radius: 5px; 
            display: inline-block;
            margin: 20px;
        }
    </style>
</head>
<body>
    <h1>🎉 Nginx 反向代理已就绪</h1>
    <div class="status">
        <p>服务器运行正常</p>
        <p>使用管理脚本进行配置：</p>
        <code>bash proxy-manager.sh</code>
    </div>
</body>
</html>
EOF
    fi
    
    log "目录初始化完成"
}

# 配置 Nginx 优化设置
configure_nginx_optimization() {
    log "配置 Nginx 优化设置..."
    
    # 备份原始配置
    if [ -f "/etc/nginx/nginx.conf" ]; then
        cp "/etc/nginx/nginx.conf" "$BACKUP_DIR/nginx.conf.backup.$(date +%Y%m%d-%H%M%S)"
    fi
    
    # 在 nginx.conf 的 http 块中添加优化设置
    if grep -q "http {" /etc/nginx/nginx.conf; then
        # 读取现有配置
        cat /etc/nginx/nginx.conf | while IFS= read -r line; do
            if [[ "$line" == "http {" ]]; then
                echo "$line"
                cat << 'EOF'
    # 优化设置
    sendfile on;
    tcp_nopush on;
    tcp_nodelay on;
    keepalive_timeout 65;
    types_hash_max_size 2048;
    client_max_body_size 100M;
    
    # SSL 优化
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-RSA-AES256-GCM-SHA512:DHE-RSA-AES256-GCM-SHA512;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;
    
    # 日志格式
    log_format main '$remote_addr - $remote_user [$time_local] "$request" '
                    '$status $body_bytes_sent "$http_referer" '
                    '"$http_user_agent" "$http_x_forwarded_for"';
    
    log_format proxy '$remote_addr - $remote_user [$time_local] "$request" '
                     '$status $body_bytes_sent "$http_referer" '
                     '"$http_user_agent" "$http_x_forwarded_for" '
                     'proxy: $upstream_addr upstream_time: $upstream_response_time';
    
    # 访问控制
    server_tokens off;
EOF
            else
                echo "$line"
            fi
        done > /tmp/nginx.conf.new
        
        mv /tmp/nginx.conf.new /etc/nginx/nginx.conf
    fi
    
    log "Nginx 优化配置完成"
}

# 配置安全默认站点
configure_default_site() {
    log "配置安全默认站点..."
    
    # 生成自签名证书用于默认站点
    if [ ! -f "$SSL_DIR/fallback.key" ]; then
        openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
            -keyout "$SSL_DIR/fallback.key" \
            -out "$SSL_DIR/fallback.crt" \
            -subj "/C=CN/ST=Beijing/L=Beijing/O=Default/CN=invalid.local" \
            -addext "subjectAltName=DNS:invalid.local" 2>/dev/null
        chmod 600 "$SSL_DIR/fallback.key"
    fi
    
    # 创建阻止非法访问的默认站点
    cat > "$NGINX_CONF_DIR/00-block-default.conf" << 'EOF'
# 阻止直接IP访问和非法域名
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;
    
    # 安全头
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    
    # 记录访问日志
    access_log /var/log/nginx/blocked-access.log main;
    
    # 返回444（无响应）
    return 444;
}

server {
    listen 443 ssl http2 default_server;
    listen [::]:443 ssl http2 default_server;
    server_name _;
    
    # SSL证书
    ssl_certificate /etc/nginx/ssl/fallback.crt;
    ssl_certificate_key /etc/nginx/ssl/fallback.key;
    
    # 安全头
    add_header Strict-Transport-Security "max-age=31536000" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    
    # 记录访问日志
    access_log /var/log/nginx/blocked-ssl-access.log main;
    
    # 返回444（无响应）
    return 444;
}
EOF
    
    # 创建管理页面站点
    cat > "$NGINX_SITES_AVAILABLE/proxy-admin" << 'EOF'
# 管理页面
server {
    listen 8080;
    server_name localhost;
    
    root /var/www/html;
    index index.html;
    
    access_log /var/log/nginx/admin-access.log main;
    error_log /var/log/nginx/admin-error.log;
    
    location / {
        try_files $uri $uri/ =404;
    }
    
    # 禁止访问隐藏文件
    location ~ /\. {
        deny all;
        access_log off;
        log_not_found off;
    }
}
EOF
    
    # 启用管理页面
    ln -sf "$NGINX_SITES_AVAILABLE/proxy-admin" "$NGINX_SITES_ENABLED/" 2>/dev/null
    
    log "安全默认站点配置完成"
}

# 设置开机自启
setup_autostart() {
    log "设置开机自启..."
    
    # 启用 Nginx 服务
    systemctl enable nginx 2>/dev/null || true
    
    # 检查 Nginx 是否正在运行
    if ! systemctl is-active nginx &>/dev/null; then
        log "启动 Nginx 服务..."
        systemctl start nginx || {
            error "Nginx 启动失败，尝试手动修复"
            nginx -t
            return 1
        }
    fi
    
    # 创建证书更新定时任务
    if [ -f "/usr/bin/certbot" ]; then
        if ! crontab -l | grep -q "certbot renew"; then
            (crontab -l 2>/dev/null; echo "0 3 * * * /usr/bin/certbot renew --quiet --deploy-hook \"systemctl reload nginx\"") | crontab -
            log "证书自动续期定时任务已添加"
        fi
    fi
    
    log "开机自启设置完成"
}

# 测试 Nginx 配置
test_nginx_config() {
    log "测试 Nginx 配置..."
    
    if nginx -t; then
        log "✅ Nginx 配置测试通过"
        return 0
    else
        error "❌ Nginx 配置测试失败"
        return 1
    fi
}

# 初始化 Nginx
init_nginx() {
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    echo -e "${CYAN}          初始化 Nginx 配置                 ${NC}"
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    
    check_root
    
    log "开始初始化 Nginx..."
    
    # 安装依赖
    install_deps
    
    # 修复配置问题
    fix_nginx_config
    
    # 初始化目录
    init_dirs
    
    # 配置优化
    configure_nginx_optimization
    
    # 配置默认站点
    configure_default_site
    
    # 测试配置
    if test_nginx_config; then
        # 设置开机自启
        setup_autostart
        
        # 显示状态
        echo ""
        log "✅ Nginx 初始化完成！"
        echo ""
        info "访问地址: http://服务器IP"
        info "管理页面: http://服务器IP:8080"
        info "配置文件: /etc/nginx/"
        info "日志文件: /var/log/nginx/"
        echo ""
        systemctl status nginx --no-pager | head -10
    else
        error "Nginx 初始化失败，请检查错误信息"
        return 1
    fi
}

# 查看证书信息
view_certificates() {
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    echo -e "${CYAN}          证书信息查看                     ${NC}"
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    
    if [ -d "$CERT_ROOT" ]; then
        echo -e "${YELLOW}找到的证书:${NC}"
        echo ""
        
        local found=0
        for domain_dir in "$CERT_ROOT"/*; do
            if [ -d "$domain_dir" ]; then
                domain=$(basename "$domain_dir")
                cert_file="$domain_dir/fullchain.pem"
                
                if [ -f "$cert_file" ]; then
                    found=1
                    echo -e "${GREEN}域名: $domain${NC}"
                    echo "证书路径: $cert_file"
                    echo "私钥路径: $KEY_ROOT/$domain/key.pem"
                    
                    # 显示证书信息
                    if openssl x509 -in "$cert_file" -noout -text 2>/dev/null | grep -q "Not After"; then
                        expiry=$(openssl x509 -in "$cert_file" -noout -dates 2>/dev/null | grep "Not After" | cut -d= -f2)
                        echo "到期时间: $expiry"
                        
                        # 检查证书是否即将过期（30天内）
                        expiry_ts=$(date -d "$expiry" +%s)
                        now_ts=$(date +%s)
                        days_left=$(( (expiry_ts - now_ts) / 86400 ))
                        
                        if [ $days_left -lt 30 ]; then
                            echo -e "${RED}警告: 证书将在 $days_left 天后过期！${NC}"
                        fi
                    fi
                    
                    # 检查符号链接
                    if [ -L "/etc/nginx/ssl/$domain.crt" ]; then
                        echo "快捷链接: /etc/nginx/ssl/$domain.crt ✓"
                    else
                        echo "快捷链接: 未创建"
                    fi
                    
                    echo "----------------------------------------"
                fi
            fi
        done
        
        if [ $found -eq 0 ]; then
            echo "未找到任何证书"
        fi
    else
        warn "证书目录不存在: $CERT_ROOT"
    fi
    
    # 显示 Let's Encrypt 证书
    if [ -d "/etc/letsencrypt/live" ]; then
        echo ""
        echo -e "${YELLOW}Let's Encrypt 证书:${NC}"
        for domain_dir in /etc/letsencrypt/live/*; do
            if [ -d "$domain_dir" ] && [ -f "$domain_dir/fullchain.pem" ]; then
                domain=$(basename "$domain_dir")
                echo "- $domain"
            fi
        done
    fi
    
    # 显示 acme.sh 管理的证书
    if [ -d "$ACME_DIR" ]; then
        echo ""
        echo -e "${YELLOW}acme.sh 管理的证书:${NC}"
        cd "$ACME_DIR"
        ./acme.sh --list 2>/dev/null || echo "acme.sh 未安装或无法访问"
    fi
    
    echo ""
    info "使用 '添加反向代理' 功能时会自动处理证书"
}

# 添加反向代理
add_proxy() {
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    echo -e "${CYAN}          添加反向代理                     ${NC}"
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    
    # 检查 Nginx 是否运行
    if ! systemctl is-active nginx &>/dev/null; then
        error "Nginx 未运行，请先初始化 Nginx"
        return 1
    fi
    
    read -p "请输入子域名 (例如: app、api、blog，直接回车使用根域名): " subdomain
    read -p "请输入主域名 (例如: example.com): " domain
    read -p "请输入本地端口号 (例如: 3000、8080、9000): " port
    
    if [ -z "$subdomain" ]; then
        full_domain="$domain"
    else
        full_domain="$subdomain.$domain"
    fi
    
    # 检查域名格式
    if [[ ! "$full_domain" =~ ^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$ ]]; then
        error "域名格式不正确: $full_domain"
        return 1
    fi
    
    # 检查端口是否数字
    if ! [[ "$port" =~ ^[0-9]+$ ]] || [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
        error "端口号无效 (1-65535)"
        return 1
    fi
    
    # 检查是否已存在配置
    local config_file="$NGINX_CONF_DIR/$full_domain.conf"
    if [ -f "$config_file" ]; then
        warn "配置已存在: $config_file"
        read -p "是否覆盖? (y/n): " overwrite
        if [[ ! "$overwrite" =~ ^[Yy]$ ]]; then
            return 1
        fi
        # 备份旧配置
        cp "$config_file" "$BACKUP_DIR/${full_domain}.conf.backup.$(date +%Y%m%d-%H%M%S)"
    fi
    
    # 创建证书目录
    mkdir -p "$CERT_ROOT/$domain"
    mkdir -p "$KEY_ROOT/$domain"
    
    # 检查证书是否存在
    local cert_file="$CERT_ROOT/$domain/fullchain.pem"
    local key_file="$KEY_ROOT/$domain/key.pem"
    
    if [ ! -f "$cert_file" ] || [ ! -f "$key_file" ]; then
        echo ""
        warn "未找到 $domain 的 SSL 证书"
        echo "1. 使用现有证书"
        echo "2. 申请 Let's Encrypt 证书"
        echo "3. 生成自签名证书"
        echo "4. 跳过 SSL（仅 HTTP）"
        read -p "请选择 (1-4): " cert_choice
        
        case $cert_choice in
            1)
                read -p "请输入证书文件路径: " custom_cert
                read -p "请输入私钥文件路径: " custom_key
                if [ -f "$custom_cert" ] && [ -f "$custom_key" ]; then
                    cp "$custom_cert" "$cert_file"
                    cp "$custom_key" "$key_file"
                    chmod 600 "$key_file"
                    log "证书已复制"
                else
                    error "证书文件不存在，将生成自签名证书"
                    generate_self_signed_cert "$domain"
                fi
                ;;
            2)
                apply_certificate "$domain"
                ;;
            3)
                generate_self_signed_cert "$domain"
                ;;
            4)
                # 标记为不使用 SSL
                cert_file=""
                key_file=""
                ;;
            *)
                warn "选择无效，将生成自签名证书"
                generate_self_signed_cert "$domain"
                ;;
        esac
    fi
    
    # 创建符号链接
    if [ -n "$cert_file" ] && [ -f "$cert_file" ]; then
        ln -sf "$cert_file" "/etc/nginx/ssl/$domain.crt" 2>/dev/null || true
        ln -sf "$key_file" "/etc/nginx/ssl/$domain.key" 2>/dev/null || true
    fi
    
    # 创建 Nginx 配置
    if [ -n "$cert_file" ] && [ -f "$cert_file" ]; then
        # HTTPS 配置
        cat > "$config_file" << EOF
# 反向代理配置: $full_domain -> localhost:$port
# 生成时间: $(date)
# 管理脚本: proxy-manager.sh

# HTTP 重定向到 HTTPS
server {
    listen 80;
    listen [::]:80;
    server_name $full_domain;
    
    # 安全头
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    
    # 记录日志
    access_log /var/log/nginx/${full_domain}-access.log main;
    
    # 重定向到 HTTPS
    return 301 https://\$server_name\$request_uri;
}

# HTTPS 反向代理
server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name $full_domain;
    
    # SSL 证书
    ssl_certificate $cert_file;
    ssl_certificate_key $key_file;
    
    # SSL 优化
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;
    
    # 安全头
    add_header Strict-Transport-Security "max-age=31536000" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    
    # 记录日志
    access_log /var/log/nginx/${full_domain}-ssl-access.log main;
    error_log /var/log/nginx/${full_domain}-ssl-error.log;
    
    # 代理设置
    location / {
        proxy_pass http://localhost:$port;
        
        # 代理头
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        
        # 超时设置
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
        
        # WebSocket 支持
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
    }
    
    # 阻止访问敏感文件
    location ~ /\.(?!well-known) {
        deny all;
        access_log off;
        log_not_found off;
    }
    
    # 健康检查
    location /nginx-health {
        access_log off;
        return 200 "healthy\n";
        add_header Content-Type text/plain;
    }
}
EOF
    else
        # HTTP only 配置
        cat > "$config_file" << EOF
# HTTP 反向代理配置: $full_domain -> localhost:$port
# 生成时间: $(date)

server {
    listen 80;
    listen [::]:80;
    server_name $full_domain;
    
    # 安全头
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    
    # 记录日志
    access_log /var/log/nginx/${full_domain}-access.log main;
    error_log /var/log/nginx/${full_domain}-error.log;
    
    # 代理设置
    location / {
        proxy_pass http://localhost:$port;
        
        # 代理头
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        
        # 超时设置
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }
    
    # 健康检查
    location /nginx-health {
        access_log off;
        return 200 "healthy\n";
        add_header Content-Type text/plain;
    }
}
EOF
    fi
    
    log "反向代理配置已创建: $config_file"
    
    # 测试配置并重载
    if test_nginx_config; then
        systemctl reload nginx
        log "✅ 反向代理配置成功！"
        echo ""
        if [ -n "$cert_file" ] && [ -f "$cert_file" ]; then
            info "HTTPS 地址: https://$full_domain"
        fi
        info "HTTP 地址: http://$full_domain"
        info "代理目标: http://localhost:$port"
        info "配置文件: $config_file"
        echo ""
        info "请确保 DNS 已正确解析到服务器 IP"
    else
        error "Nginx 配置测试失败"
        if [ -f "$config_file" ]; then
            echo "配置文件内容:"
            cat "$config_file"
        fi
        return 1
    fi
}

# 申请 SSL 证书
apply_certificate() {
    local domain=$1
    
    echo -e "${YELLOW}为 $domain 申请 SSL 证书...${NC}"
    
    # 检查是否安装 certbot
    if ! command -v certbot &> /dev/null; then
        error "certbot 未安装，正在安装..."
        apt-get install -y certbot python3-certbot-nginx
    fi
    
    # 检查是否需要添加泛域名
    read -p "是否申请泛域名证书 (*.$domain)? (y/n): " wildcard
    if [[ "$wildcard" =~ ^[Yy]$ ]]; then
        cert_domains="-d $domain -d *.$domain"
    else
        cert_domains="-d $domain"
    fi
    
    # 使用 certbot 申请证书
    if certbot certonly --nginx $cert_domains --non-interactive --agree-tos --email admin@$domain 2>/dev/null; then
        log "证书申请成功"
        
        # 复制证书到统一目录
        local cert_path="/etc/letsencrypt/live/$domain"
        if [ -d "$cert_path" ]; then
            mkdir -p "$CERT_ROOT/$domain"
            mkdir -p "$KEY_ROOT/$domain"
            
            cp "$cert_path/fullchain.pem" "$CERT_ROOT/$domain/"
            cp "$cert_path/privkey.pem" "$KEY_ROOT/$domain/key.pem"
            
            chmod 600 "$KEY_ROOT/$domain/key.pem"
            
            log "证书已复制到: $CERT_ROOT/$domain/"
            return 0
        fi
    else
        error "Let's Encrypt 证书申请失败"
        warn "尝试使用自签名证书..."
        generate_self_signed_cert "$domain"
        return 1
    fi
}

# 生成自签名证书
generate_self_signed_cert() {
    local domain=$1
    
    warn "为 $domain 生成自签名证书..."
    
    mkdir -p "$CERT_ROOT/$domain"
    mkdir -p "$KEY_ROOT/$domain"
    
    # 生成证书
    openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
        -keyout "$KEY_ROOT/$domain/key.pem" \
        -out "$CERT_ROOT/$domain/fullchain.pem" \
        -subj "/C=CN/ST=Beijing/L=Beijing/O=Self-Signed/CN=$domain" \
        -addext "subjectAltName=DNS:$domain,DNS:*.$domain" 2>/dev/null
    
    chmod 600 "$KEY_ROOT/$domain/key.pem"
    
    log "自签名证书已生成: $CERT_ROOT/$domain/fullchain.pem"
}

# 删除反向代理
remove_proxy() {
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    echo -e "${CYAN}          删除反向代理                     ${NC}"
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    
    # 查找所有反代配置
    local configs=()
    local count=0
    
    echo -e "${YELLOW}当前的反向代理配置:${NC}"
    echo ""
    
    for conf in "$NGINX_CONF_DIR"/*.conf; do
        if [ -f "$conf" ] && [[ "$(basename "$conf")" != "00-block-default.conf" ]]; then
            server_name=$(grep -h "server_name " "$conf" | head -1 | awk '{print $2}' | sed 's/;//')
            if [ -n "$server_name" ]; then
                ((count++))
                configs+=("$(basename "$conf")")
                echo "${count}. $server_name"
                echo "   文件: $(basename "$conf")"
                echo ""
            fi
        fi
    done
    
    if [ $count -eq 0 ]; then
        warn "没有找到反向代理配置"
        return 0
    fi
    
    read -p "请输入要删除的配置编号 (1-$count，或输入 'a' 删除所有): " choice
    
    if [[ "$choice" =~ ^[0-9]+$ ]] && [ "$choice" -ge 1 ] && [ "$choice" -le $count ]; then
        index=$((choice-1))
        config_file="${configs[$index]}"
        
        read -p "确定要删除 $config_file 吗? (y/n): " confirm
        
        if [[ "$confirm" =~ ^[Yy]$ ]]; then
            # 备份配置
            backup_file="$BACKUP_DIR/${config_file}.backup.$(date +%Y%m%d-%H%M%S)"
            cp "$NGINX_CONF_DIR/$config_file" "$backup_file"
            
            # 删除配置
            rm -f "$NGINX_CONF_DIR/$config_file"
            
            # 测试并重载 Nginx
            if test_nginx_config; then
                systemctl reload nginx
                log "✅ 反向代理配置已删除: $config_file"
                log "配置文件已备份到: $backup_file"
            else
                error "Nginx 配置测试失败，已恢复备份"
                cp "$backup_file" "$NGINX_CONF_DIR/$config_file"
                return 1
            fi
        fi
    elif [[ "$choice" == "a" ]]; then
        read -p "确定要删除所有反向代理配置吗? (y/n): " confirm
        if [[ "$confirm" =~ ^[Yy]$ ]]; then
            # 备份并删除所有配置
            backup_dir="$BACKUP_DIR/all-proxies-$(date +%Y%m%d-%H%M%S)"
            mkdir -p "$backup_dir"
            
            for conf in "$NGINX_CONF_DIR"/*.conf; do
                if [ -f "$conf" ] && [[ "$(basename "$conf")" != "00-block-default.conf" ]]; then
                    cp "$conf" "$backup_dir/"
                    rm -f "$conf"
                fi
            done
            
            if test_nginx_config; then
                systemctl reload nginx
                log "✅ 所有反向代理配置已删除"
                log "配置已备份到: $backup_dir"
            fi
        fi
    else
        error "选择无效"
        return 1
    fi
}

# 查看反向代理列表
list_proxies() {
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    echo -e "${CYAN}          反向代理列表                     ${NC}"
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    
    local count=0
    
    for conf in "$NGINX_CONF_DIR"/*.conf; do
        if [ -f "$conf" ] && [[ "$(basename "$conf")" != "00-block-default.conf" ]]; then
            filename=$(basename "$conf")
            server_name=$(grep -h "server_name " "$conf" | head -1 | awk '{print $2}' | sed 's/;//')
            proxy_pass=$(grep -h "proxy_pass " "$conf" | head -1 | awk '{print $2}' | sed 's/;//')
            
            if [ -n "$server_name" ]; then
                ((count++))
                echo -e "${GREEN}${count}. $server_name${NC}"
                
                if [ -n "$proxy_pass" ]; then
                    echo "  代理目标: $proxy_pass"
                fi
                
                echo "  配置文件: $filename"
                
                # 检查 SSL 状态
                if grep -q "listen 443 ssl" "$conf"; then
                    echo "  SSL 状态: ${GREEN}已启用 HTTPS${NC}"
                else
                    echo "  SSL 状态: ${YELLOW}仅 HTTP${NC}"
                fi
                
                # 显示日志文件
                access_log=$(grep -h "access_log " "$conf" | head -1 | awk '{print $2}' | sed 's/;//')
                if [ -n "$access_log" ]; then
                    echo "  访问日志: $access_log"
                fi
                
                echo ""
            fi
        fi
    done
    
    if [ $count -eq 0 ]; then
        echo "没有配置反向代理"
    fi
    
    echo -e "${YELLOW}Nginx 状态:${NC}"
    if systemctl is-active nginx &>/dev/null; then
        echo -e "${GREEN}✅ Nginx 正在运行${NC}"
        systemctl status nginx --no-pager | grep "Active:" | head -1
    else
        echo -e "${RED}❌ Nginx 未运行${NC}"
    fi
}

# 重载 Nginx
reload_nginx() {
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    echo -e "${CYAN}          重载 Nginx 配置                 ${NC}"
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    
    if test_nginx_config; then
        log "正在重载 Nginx..."
        
        if systemctl reload nginx; then
            log "✅ Nginx 重载成功"
            
            # 显示状态
            echo ""
            systemctl status nginx --no-pager | head -10
        else
            error "Nginx 重载失败"
            
            # 尝试重启
            echo ""
            read -p "是否尝试重启 Nginx? (y/n): " restart_choice
            if [[ "$restart_choice" =~ ^[Yy]$ ]]; then
                systemctl restart nginx
                systemctl status nginx --no-pager | head -10
            fi
        fi
    else
        error "Nginx 配置测试失败，无法重载"
        return 1
    fi
}

# 备份配置
backup_config() {
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    echo -e "${CYAN}          备份 Nginx 配置                 ${NC}"
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    
    local backup_name="nginx-backup-$(date +%Y%m%d-%H%M%S).tar.gz"
    local backup_path="$BACKUP_DIR/$backup_name"
    
    log "正在备份 Nginx 配置..."
    
    # 备份主要配置文件
    tar czf "$backup_path" \
        /etc/nginx/nginx.conf \
        /etc/nginx/conf.d/ \
        /etc/nginx/sites-available/ \
        /etc/nginx/sites-enabled/ \
        /etc/nginx/ssl/ \
        /var/www/html/ \
        2>/dev/null
    
    if [ $? -eq 0 ] && [ -f "$backup_path" ]; then
        log "✅ 配置备份成功: $backup_path"
        echo ""
        echo "备份文件信息:"
        ls -lh "$backup_path"
        echo ""
        echo "包含内容:"
        tar tzf "$backup_path" | head -20
        echo "..."
    else
        error "配置备份失败"
        return 1
    fi
}

# 查看日志
view_logs() {
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    echo -e "${CYAN}          查看 Nginx 日志                 ${NC}"
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    
    echo "1. 查看错误日志 (最后50行)"
    echo "2. 查看访问日志 (最后50行)"
    echo "3. 查看反向代理日志"
    echo "4. 实时监控错误日志"
    echo "5. 实时监控访问日志"
    echo "6. 清空日志文件"
    echo ""
    
    read -p "请选择 (1-6): " log_choice
    
    case $log_choice in
        1)
            echo -e "${YELLOW}=== Nginx 错误日志 (最后50行) ===${NC}"
            if [ -f "/var/log/nginx/error.log" ]; then
                tail -50 /var/log/nginx/error.log
            else
                error "错误日志文件不存在"
            fi
            ;;
        2)
            echo -e "${YELLOW}=== Nginx 访问日志 (最后50行) ===${NC}"
            if [ -f "/var/log/nginx/access.log" ]; then
                tail -50 /var/log/nginx/access.log
            else
                error "访问日志文件不存在"
            fi
            ;;
        3)
            echo -e "${YELLOW}=== 反向代理日志目录 ===${NC}"
            if [ -d "/var/log/nginx/proxy" ]; then
                ls -la /var/log/nginx/proxy/
                echo ""
                read -p "输入要查看的日志文件名 (或按回车返回): " proxy_log
                if [ -n "$proxy_log" ]; then
                    if [ -f "/var/log/nginx/proxy/$proxy_log" ]; then
                        tail -50 "/var/log/nginx/proxy/$proxy_log"
                    else
                        error "日志文件不存在"
                    fi
                fi
            else
                echo "反向代理日志目录不存在"
            fi
            ;;
        4)
            echo -e "${YELLOW}=== 实时监控错误日志 (Ctrl+C 退出) ===${NC}"
            if [ -f "/var/log/nginx/error.log" ]; then
                tail -f /var/log/nginx/error.log
            else
                error "错误日志文件不存在"
            fi
            ;;
        5)
            echo -e "${YELLOW}=== 实时监控访问日志 (Ctrl+C 退出) ===${NC}"
            if [ -f "/var/log/nginx/access.log" ]; then
                tail -f /var/log/nginx/access.log
            else
                error "访问日志文件不存在"
            fi
            ;;
        6)
            read -p "确定要清空所有日志文件吗? (y/n): " confirm
            if [[ "$confirm" =~ ^[Yy]$ ]]; then
                if [ -f "/var/log/nginx/error.log" ]; then
                    > /var/log/nginx/error.log
                    log "错误日志已清空"
                fi
                if [ -f "/var/log/nginx/access.log" ]; then
                    > /var/log/nginx/access.log
                    log "访问日志已清空"
                fi
                if [ -d "/var/log/nginx/proxy" ]; then
                    rm -f /var/log/nginx/proxy/*
                    log "代理日志已清空"
                fi
            fi
            ;;
        *)
            error "选择无效"
            ;;
    esac
}

# 修复 Nginx 问题
fix_nginx() {
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    echo -e "${CYAN}          修复 Nginx 问题                 ${NC}"
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    
    log "开始诊断 Nginx 问题..."
    
    # 检查 Nginx 状态
    if systemctl is-active nginx &>/dev/null; then
        log "Nginx 正在运行"
    else
        error "Nginx 未运行"
    fi
    
    # 测试配置
    echo ""
    log "测试 Nginx 配置..."
    nginx -t
    
    # 显示错误日志
    echo ""
    log "最近的错误日志:"
    tail -20 /var/log/nginx/error.log 2>/dev/null || echo "错误日志文件不存在"
    
    # 提供修复选项
    echo ""
    echo "修复选项:"
    echo "1. 修复配置文件"
    echo "2. 重新安装 Nginx"
    echo "3. 重置为默认配置"
    echo "4. 检查端口占用"
    echo ""
    
    read -p "请选择 (1-4): " fix_choice
    
    case $fix_choice in
        1)
            fix_nginx_config
            ;;
        2)
            log "重新安装 Nginx..."
            apt-get install --reinstall -y nginx nginx-common
            ;;
        3)
            log "重置为默认配置..."
            cp /etc/nginx/nginx.conf "$BACKUP_DIR/nginx.conf.bak.$(date +%Y%m%d-%H%M%S)"
            apt-get install --reinstall -y nginx-common
            ;;
        4)
            log "检查端口占用..."
            netstat -tulpn | grep -E ":80|:443|:8080"
            ;;
    esac
    
    # 测试修复后
    echo ""
    if test_nginx_config; then
        systemctl restart nginx
        log "✅ 修复完成，Nginx 已重启"
    else
        error "修复后配置仍然有问题"
    fi
}

# 显示系统信息
show_system_info() {
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    echo -e "${CYAN}          系统信息                         ${NC}"
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    
    echo -e "${YELLOW}服务器信息:${NC}"
    echo "主机名: $(hostname)"
    echo "系统: $(lsb_release -ds 2>/dev/null || cat /etc/os-release | grep PRETTY_NAME | cut -d= -f2 | tr -d '\"')"
    echo "内核: $(uname -r)"
    echo "架构: $(uname -m)"
    echo ""
    
    echo -e "${YELLOW}网络信息:${NC}"
    echo "IP地址: $(hostname -I 2>/dev/null | awk '{print $1}')"
    echo "公网IP: $(curl -s ifconfig.me 2>/dev/null || echo "无法获取")"
    echo ""
    
    echo -e "${YELLOW}Nginx 信息:${NC}"
    if command -v nginx &> /dev/null; then
        nginx -v 2>&1
        echo "配置文件: /etc/nginx/nginx.conf"
        echo "配置目录: /etc/nginx/conf.d/"
        echo "日志目录: /var/log/nginx/"
    else
        echo "Nginx 未安装"
    fi
    echo ""
    
    echo -e "${YELLOW}磁盘使用:${NC}"
    df -h / | tail -1
    echo ""
    
    echo -e "${YELLOW}内存使用:${NC}"
    free -h | head -2
}

# 显示菜单
show_menu() {
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    echo -e "${CYAN}      Nginx 反向代理管理脚本 v1.1         ${NC}"
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    echo ""
    echo -e "${GREEN}1.${NC}  初始化 Nginx 环境"
    echo -e "${GREEN}2.${NC}  查看证书信息"
    echo -e "${GREEN}3.${NC}  添加反向代理"
    echo -e "${GREEN}4.${NC}  删除反向代理"
    echo -e "${GREEN}5.${NC}  查看反向代理列表"
    echo -e "${GREEN}6.${NC}  重载 Nginx 配置"
    echo -e "${GREEN}7.${NC}  备份当前配置"
    echo -e "${GREEN}8.${NC}  查看 Nginx 状态"
    echo -e "${GREEN}9.${NC}  查看日志"
    echo -e "${GREEN}10.${NC} 修复 Nginx 问题"
    echo -e "${GREEN}11.${NC} 系统信息"
    echo -e "${GREEN}12.${NC} 退出脚本"
    echo ""
    echo -e "${YELLOW}当前服务器: $(hostname)${NC}"
    echo -e "${YELLOW}服务器时间: $(date)${NC}"
    echo -e "${YELLOW}脚本目录: $INSTALL_DIR${NC}"
    echo ""
}

# 主函数
main() {
    check_root
    
    while true; do
        show_menu
        
        read -p "请输入选项 (1-12): " choice
        
        case $choice in
            1)
                init_nginx
                ;;
            2)
                view_certificates
                ;;
            3)
                add_proxy
                ;;
            4)
                remove_proxy
                ;;
            5)
                list_proxies
                ;;
            6)
                reload_nginx
                ;;
            7)
                backup_config
                ;;
            8)
                echo -e "${CYAN}════════════════════════════════════════════${NC}"
                echo -e "${CYAN}          Nginx 状态信息                   ${NC}"
                echo -e "${CYAN}════════════════════════════════════════════${NC}"
                systemctl status nginx --no-pager -l
                ;;
            9)
                view_logs
                ;;
            10)
                fix_nginx
                ;;
            11)
                show_system_info
                ;;
            12)
                echo "再见！"
                exit 0
                ;;
            *)
                error "无效选项，请重新输入"
                ;;
        esac
        
        echo ""
        read -p "按 Enter 键继续..."
        clear
    done
}

# 启动脚本
clear
echo -e "${CYAN}正在启动 Nginx 反向代理管理脚本...${NC}"
echo ""

# 检查是否首次运行
if [ ! -d "$INSTALL_DIR" ]; then
    warn "检测到首次运行，正在创建目录..."
    mkdir -p "$INSTALL_DIR" "$CONFIG_DIR" "$LOG_DIR"
fi

# 检查 Nginx 是否安装
if ! command -v nginx &> /dev/null; then
    warn "Nginx 未安装，将自动安装"
    apt-get update
    apt-get install -y nginx
fi

main "$@"