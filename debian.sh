#!/bin/bash

# ===================================================
# Debian 12 Nginx 反向代理管理脚本
# 功能：初始化Nginx、管理SSL证书、管理反向代理
# 版本：1.0
# 作者：AI Assistant
# ===================================================

# 配置
INSTALL_DIR="/opt/cert-manager"
ACME_DIR="$INSTALL_DIR/acme.sh"
CONFIG_DIR="$INSTALL_DIR/config"
LOG_DIR="$INSTALL_DIR/logs"
NGINX_CONF_DIR="/etc/nginx/conf.d"
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

# 配置 Nginx 基础设置
configure_nginx_base() {
    log "配置 Nginx 基础设置..."
    
    # 备份原始配置
    if [ -f "/etc/nginx/nginx.conf" ]; then
        cp "/etc/nginx/nginx.conf" "$BACKUP_DIR/nginx.conf.backup.$(date +%Y%m%d-%H%M%S)"
    fi
    
    # 创建优化的 Nginx 配置
    cat > /etc/nginx/nginx.conf << 'EOF'
user www-data;
worker_processes auto;
pid /run/nginx.pid;

events {
    worker_connections 1024;
    multi_accept on;
    use epoll;
}

http {
    # 基础设置
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    
    # 日志格式
    log_format main '$remote_addr - $remote_user [$time_local] "$request" '
                    '$status $body_bytes_sent "$http_referer" '
                    '"$http_user_agent" "$http_x_forwarded_for"';
    
    log_format proxy '$remote_addr - $remote_user [$time_local] "$request" '
                     '$status $body_bytes_sent "$http_referer" '
                     '"$http_user_agent" "$http_x_forwarded_for" '
                     'proxy: $upstream_addr upstream_time: $upstream_response_time';
    
    # 优化设置
    sendfile on;
    tcp_nopush on;
    tcp_nodelay on;
    keepalive_timeout 65;
    types_hash_max_size 2048;
    client_max_body_size 100M;
    
    # SSL 优化
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-RSA-AES256-GCM-SHA512:DHE-RSA-AES256-GCM-SHA512:ECDHE-RSA-AES256-GCM-SHA384:DHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;
    
    # 访问控制
    server_tokens off;
    
    # 包含其他配置
    include /etc/nginx/conf.d/*.conf;
}
EOF
    
    log "Nginx 基础配置完成"
}

# 配置默认站点
configure_default_site() {
    log "配置默认站点..."
    
    # 生成自签名证书用于默认站点
    if [ ! -f "$SSL_DIR/fallback.key" ]; then
        openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
            -keyout "$SSL_DIR/fallback.key" \
            -out "$SSL_DIR/fallback.crt" \
            -subj "/C=CN/ST=Beijing/L=Beijing/O=Default/CN=invalid.local" \
            -addext "subjectAltName=DNS:invalid.local" 2>/dev/null
        chmod 600 "$SSL_DIR/fallback.key"
    fi
    
    # 默认站点配置
    cat > "$NGINX_CONF_DIR/00-default.conf" << 'EOF'
# 默认HTTP站点 - 重定向到HTTPS
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;
    
    # 安全头
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    
    # 重定向到HTTPS
    location / {
        return 301 https://$host$request_uri;
    }
}

# 默认HTTPS站点 - 阻止非法访问
server {
    listen 443 ssl http2 default_server;
    listen [::]:443 ssl http2 default_server;
    server_name _;
    
    # SSL证书
    ssl_certificate /etc/nginx/ssl/fallback.crt;
    ssl_certificate_key /etc/nginx/ssl/fallback.key;
    
    # 安全头
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Content-Security-Policy "default-src 'self';" always;
    
    # 记录访问日志
    access_log /var/log/nginx/proxy/default-access.log main;
    error_log /var/log/nginx/proxy/default-error.log;
    
    # 返回444（无响应）
    return 444;
}

# 静态文件服务
server {
    listen 8080;
    server_name localhost;
    
    root /var/www/html;
    index index.html;
    
    access_log /var/log/nginx/static-access.log main;
    error_log /var/log/nginx/static-error.log;
    
    location / {
        try_files $uri $uri/ =404;
    }
}
EOF
    
    log "默认站点配置完成"
}

# 设置开机自启
setup_autostart() {
    log "设置开机自启..."
    
    # 启用 Nginx 服务
    systemctl enable nginx
    
    # 创建系统服务文件
    if [ ! -f "/etc/systemd/system/proxy-manager.service" ]; then
        cat > /etc/systemd/system/proxy-manager.service << EOF
[Unit]
Description=Proxy Manager Service
After=network.target nginx.service
Wants=nginx.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/echo "Proxy manager service initialized"
ExecReload=/bin/echo "Proxy manager service reloaded"

[Install]
WantedBy=multi-user.target
EOF
        
        systemctl daemon-reload
        systemctl enable proxy-manager.service
    fi
    
    # 创建定时任务更新证书
    if ! crontab -l | grep -q "certbot renew"; then
        (crontab -l 2>/dev/null; echo "0 3 * * * /usr/bin/certbot renew --quiet --deploy-hook \"systemctl reload nginx\"") | crontab -
    fi
    
    log "开机自启设置完成"
}

# 初始化 Nginx
init_nginx() {
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    echo -e "${CYAN}          初始化 Nginx 配置                 ${NC}"
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    
    check_root
    install_deps
    init_dirs
    configure_nginx_base
    configure_default_site
    setup_autostart
    
    # 启动 Nginx
    systemctl start nginx
    systemctl status nginx --no-pager
    
    log "✅ Nginx 初始化完成！"
    echo ""
    info "访问地址: http://服务器IP"
    info "管理端口: 8080 (静态文件)"
    info "配置文件: /etc/nginx/conf.d/"
}

# 查看证书信息
view_certificates() {
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    echo -e "${CYAN}          证书信息查看                     ${NC}"
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    
    if [ -d "$CERT_ROOT" ]; then
        echo -e "${YELLOW}找到的证书:${NC}"
        echo ""
        
        for domain_dir in "$CERT_ROOT"/*; do
            if [ -d "$domain_dir" ]; then
                domain=$(basename "$domain_dir")
                cert_file="$domain_dir/fullchain.pem"
                
                if [ -f "$cert_file" ]; then
                    echo -e "${GREEN}域名: $domain${NC}"
                    echo "证书路径: $cert_file"
                    echo "私钥路径: $KEY_ROOT/$domain/key.pem"
                    
                    # 显示证书信息
                    if openssl x509 -in "$cert_file" -noout -text 2>/dev/null | grep -q "Not After"; then
                        expiry=$(openssl x509 -in "$cert_file" -noout -dates 2>/dev/null | grep "Not After" | cut -d= -f2)
                        echo "到期时间: $expiry"
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
    else
        warn "证书目录不存在: $CERT_ROOT"
    fi
    
    # 显示 acme.sh 管理的证书
    if [ -d "$ACME_DIR" ]; then
        echo ""
        echo -e "${YELLOW}acme.sh 管理的证书:${NC}"
        cd "$ACME_DIR"
        ./acme.sh --list
    fi
    
    echo ""
    info "使用 'add-proxy' 添加反向代理时，会自动创建符号链接"
}

# 添加反向代理
add_proxy() {
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    echo -e "${CYAN}          添加反向代理                     ${NC}"
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    
    read -p "请输入子域名 (例如: app、api、blog): " subdomain
    read -p "请输入主域名 (例如: example.com): " domain
    read -p "请输入本地端口号 (例如: 3000、8080、9000): " port
    
    full_domain="$subdomain.$domain"
    
    # 检查域名格式
    if [[ ! "$full_domain" =~ ^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$ ]]; then
        error "域名格式不正确"
        return 1
    fi
    
    # 检查端口是否数字
    if ! [[ "$port" =~ ^[0-9]+$ ]] || [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
        error "端口号无效 (1-65535)"
        return 1
    fi
    
    # 检查是否已存在配置
    if [ -f "$NGINX_CONF_DIR/$full_domain.conf" ]; then
        warn "配置已存在: $NGINX_CONF_DIR/$full_domain.conf"
        read -p "是否覆盖? (y/n): " overwrite
        if [[ ! "$overwrite" =~ ^[Yy]$ ]]; then
            return 1
        fi
    fi
    
    # 创建证书目录
    mkdir -p "$CERT_ROOT/$domain"
    mkdir -p "$KEY_ROOT/$domain"
    
    # 检查证书是否存在，如果不存在则询问是否申请
    if [ ! -f "$CERT_ROOT/$domain/fullchain.pem" ]; then
        echo ""
        warn "未找到 $domain 的证书"
        read -p "是否现在申请 SSL 证书? (y/n): " apply_cert
        
        if [[ "$apply_cert" =~ ^[Yy]$ ]]; then
            apply_certificate "$domain"
        else
            # 使用自签名证书
            warn "将使用自签名证书"
            generate_self_signed_cert "$domain"
        fi
    fi
    
    # 创建符号链接
    ln -sf "$CERT_ROOT/$domain/fullchain.pem" "/etc/nginx/ssl/$domain.crt" 2>/dev/null || true
    ln -sf "$KEY_ROOT/$domain/key.pem" "/etc/nginx/ssl/$domain.key" 2>/dev/null || true
    
    # 创建 Nginx 配置
    cat > "$NGINX_CONF_DIR/$full_domain.conf" << EOF
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
    add_header X-XSS-Protection "1; mode=block" always;
    
    # 记录日志
    access_log /var/log/nginx/proxy/${full_domain}-access.log main;
    error_log /var/log/nginx/proxy/${full_domain}-error.log;
    
    # 重定向到 HTTPS
    return 301 https://\$server_name\$request_uri;
}

# HTTPS 反向代理
server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name $full_domain;
    
    # SSL 证书
    ssl_certificate /etc/nginx/ssl/certs/$domain/fullchain.pem;
    ssl_certificate_key /etc/nginx/ssl/private/$domain/key.pem;
    
    # SSL 优化
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;
    
    # 安全头
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    
    # 记录日志
    access_log /var/log/nginx/proxy/${full_domain}-ssl-access.log proxy;
    error_log /var/log/nginx/proxy/${full_domain}-ssl-error.log;
    
    # 代理设置
    location / {
        proxy_pass http://localhost:$port;
        
        # 代理头
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Forwarded-Host \$host;
        proxy_set_header X-Forwarded-Port \$server_port;
        
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
    
    log "反向代理配置已创建: $NGINX_CONF_DIR/$full_domain.conf"
    
    # 测试配置并重载
    if nginx -t; then
        systemctl reload nginx
        log "✅ 反向代理配置成功！"
        echo ""
        info "访问地址: https://$full_domain"
        info "代理目标: http://localhost:$port"
        info "配置文件: $NGINX_CONF_DIR/$full_domain.conf"
    else
        error "Nginx 配置测试失败，请检查配置文件"
        return 1
    fi
}

# 申请 SSL 证书
apply_certificate() {
    local domain=$1
    
    echo -e "${YELLOW}为 $domain 申请 SSL 证书...${NC}"
    
    # 使用 certbot 申请证书
    if certbot certonly --nginx -d "$domain" -d "*.$domain" --non-interactive --agree-tos --email admin@$domain; then
        log "证书申请成功"
        
        # 复制证书到统一目录
        local cert_path="/etc/letsencrypt/live/$domain"
        if [ -d "$cert_path" ]; then
            mkdir -p "$CERT_ROOT/$domain"
            mkdir -p "$KEY_ROOT/$domain"
            
            cp "$cert_path/fullchain.pem" "$CERT_ROOT/$domain/"
            cp "$cert_path/privkey.pem" "$KEY_ROOT/$domain/key.pem"
            cp "$cert_path/cert.pem" "$CERT_ROOT/$domain/cert.pem"
            cp "$cert_path/chain.pem" "$CERT_ROOT/$domain/chain.pem"
            
            chmod 600 "$KEY_ROOT/$domain/key.pem"
            
            log "证书已复制到: $CERT_ROOT/$domain/"
            return 0
        fi
    else
        error "证书申请失败，尝试使用自签名证书"
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
    
    openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
        -keyout "$KEY_ROOT/$domain/key.pem" \
        -out "$CERT_ROOT/$domain/fullchain.pem" \
        -subj "/C=CN/ST=Beijing/L=Beijing/O=Self-Signed/CN=$domain" \
        -addext "subjectAltName=DNS:$domain,DNS:*.$domain" 2>/dev/null
    
    chmod 600 "$KEY_ROOT/$domain/key.pem"
    
    log "自签名证书已生成"
}

# 删除反向代理
remove_proxy() {
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    echo -e "${CYAN}          删除反向代理                     ${NC}"
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    
    echo -e "${YELLOW}当前的反向代理配置:${NC}"
    echo ""
    
    local count=0
    local configs=()
    
    for conf in "$NGINX_CONF_DIR"/*.conf; do
        if [ -f "$conf" ]; then
            filename=$(basename "$conf")
            # 跳过默认配置
            if [[ "$filename" != "00-default.conf" ]]; then
                server_name=$(grep -h "server_name " "$conf" | head -1 | awk '{print $2}' | sed 's/;//')
                proxy_pass=$(grep -h "proxy_pass " "$conf" | head -1 | awk '{print $2}' | sed 's/;//')
                
                if [ -n "$server_name" ] && [ -n "$proxy_pass" ]; then
                    ((count++))
                    configs+=("$filename")
                    echo "${count}. ${server_name} -> ${proxy_pass}"
                    echo "   配置文件: $filename"
                    echo ""
                fi
            fi
        fi
    done
    
    if [ $count -eq 0 ]; then
        warn "没有找到反向代理配置"
        return 0
    fi
    
    read -p "请输入要删除的配置编号 (1-$count): " choice
    
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
            if nginx -t; then
                systemctl reload nginx
                log "✅ 反向代理配置已删除: $config_file"
                log "配置文件已备份到: $backup_file"
            else
                error "Nginx 配置测试失败，已恢复备份"
                cp "$backup_file" "$NGINX_CONF_DIR/$config_file"
                systemctl reload nginx
                return 1
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
        if [ -f "$conf" ]; then
            filename=$(basename "$conf")
            
            # 显示配置详情（跳过默认配置）
            if [[ "$filename" != "00-default.conf" ]]; then
                server_name=$(grep -h "server_name " "$conf" | head -1 | awk '{print $2}' | sed 's/;//')
                proxy_pass=$(grep -h "proxy_pass " "$conf" | head -1 | awk '{print $2}' | sed 's/;//')
                
                if [ -n "$server_name" ] && [ -n "$proxy_pass" ]; then
                    ((count++))
                    echo -e "${GREEN}${count}. $server_name${NC}"
                    echo "  代理目标: $proxy_pass"
                    echo "  配置文件: $filename"
                    
                    # 检查 SSL 状态
                    if grep -q "listen 443 ssl" "$conf"; then
                        echo "  SSL 状态: ${GREEN}已启用${NC}"
                    else
                        echo "  SSL 状态: ${RED}未启用${NC}"
                    fi
                    
                    echo ""
                fi
            fi
        fi
    done
    
    if [ $count -eq 0 ]; then
        echo "没有配置反向代理"
    fi
    
    # 显示 Nginx 状态
    echo -e "${YELLOW}Nginx 状态:${NC}"
    systemctl status nginx --no-pager -l | head -20
}

# 重载 Nginx
reload_nginx() {
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    echo -e "${CYAN}          重载 Nginx 配置                 ${NC}"
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    
    log "测试 Nginx 配置..."
    
    if nginx -t; then
        log "配置测试成功，正在重载 Nginx..."
        systemctl reload nginx
        
        if [ $? -eq 0 ]; then
            log "✅ Nginx 重载成功"
            systemctl status nginx --no-pager | head -10
        else
            error "Nginx 重载失败"
            systemctl status nginx --no-pager
            return 1
        fi
    else
        error "Nginx 配置测试失败，请检查错误信息"
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
    
    # 备份主要配置文件
    tar czf "$backup_path" \
        /etc/nginx/nginx.conf \
        /etc/nginx/conf.d/ \
        /etc/nginx/ssl/ \
        /var/www/html/ \
        2>/dev/null
    
    if [ $? -eq 0 ]; then
        log "✅ 配置备份成功: $backup_path"
        ls -lh "$backup_path"
    else
        error "配置备份失败"
        return 1
    fi
}

# 显示菜单
show_menu() {
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    echo -e "${CYAN}      Nginx 反向代理管理脚本               ${NC}"
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    echo ""
    echo -e "${GREEN}1.${NC} 初始化 Nginx 环境"
    echo -e "${GREEN}2.${NC} 查看证书信息"
    echo -e "${GREEN}3.${NC} 添加反向代理"
    echo -e "${GREEN}4.${NC} 删除反向代理"
    echo -e "${GREEN}5.${NC} 查看反向代理列表"
    echo -e "${GREEN}6.${NC} 重载 Nginx 配置"
    echo -e "${GREEN}7.${NC} 备份当前配置"
    echo -e "${GREEN}8.${NC} 查看 Nginx 状态"
    echo -e "${GREEN}9.${NC} 查看访问日志"
    echo -e "${GREEN}0.${NC} 退出脚本"
    echo ""
    echo -e "${YELLOW}当前服务器: $(hostname)${NC}"
    echo -e "${YELLOW}服务器时间: $(date)${NC}"
    echo ""
}

# 查看日志
view_logs() {
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    echo -e "${CYAN}          查看 Nginx 日志                 ${NC}"
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    
    echo "1. 错误日志"
    echo "2. 访问日志"
    echo "3. 反向代理日志"
    echo "4. 实时日志监控"
    echo ""
    
    read -p "请选择要查看的日志类型 (1-4): " log_choice
    
    case $log_choice in
        1)
            echo -e "${YELLOW}=== Nginx 错误日志 (最后50行) ===${NC}"
            tail -50 /var/log/nginx/error.log
            ;;
        2)
            echo -e "${YELLOW}=== Nginx 访问日志 (最后50行) ===${NC}"
            tail -50 /var/log/nginx/access.log
            ;;
        3)
            echo -e "${YELLOW}=== 反向代理日志目录 ===${NC}"
            ls -la /var/log/nginx/proxy/
            echo ""
            read -p "输入要查看的日志文件名: " proxy_log
            if [ -f "/var/log/nginx/proxy/$proxy_log" ]; then
                tail -50 "/var/log/nginx/proxy/$proxy_log"
            else
                error "日志文件不存在"
            fi
            ;;
        4)
            echo -e "${YELLOW}=== 实时日志监控 (Ctrl+C 退出) ===${NC}"
            tail -f /var/log/nginx/access.log
            ;;
        *)
            error "选择无效"
            ;;
    esac
}

# 主函数
main() {
    check_root
    
    while true; do
        show_menu
        
        read -p "请输入选项 (0-9): " choice
        
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
                systemctl status nginx --no-pager -l
                ;;
            9)
                view_logs
                ;;
            0)
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
main "$@"