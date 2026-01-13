#!/bin/sh

# ============================================
# Alpine Nginx 反代配置器 - 简单生产力版本
# 支持IPv6，交互式配置，SSL证书自动管理
# 使用方法: curl -fsSL https://raw.githubusercontent.com/penggan00/rss/main/simple-proxy.sh | sh
# ============================================

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# 日志函数
log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# 检查root权限
check_root() {
    if [ "$(id -u)" != "0" ]; then
        log_error "此脚本需要root权限运行"
        exit 1
    fi
}

# 检查Alpine系统
check_alpine() {
    if [ ! -f /etc/alpine-release ]; then
        log_warn "此脚本专为Alpine Linux设计，但检测到非Alpine系统"
        read -p "是否继续？(y/n): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi
}

# 安装必要软件
install_nginx() {
    log_info "更新软件包列表..."
    apk update
    
    log_info "安装Nginx和相关工具..."
    apk add --no-cache nginx openssl curl bash
    
    # 创建必要的目录
    mkdir -p /etc/nginx/sites-available /etc/nginx/sites-enabled /etc/nginx/ssl
    
    log_success "Nginx安装完成"
}

# 检测IPv6支持
detect_ipv6() {
    if ip -6 addr show | grep -q inet6; then
        HAS_IPV6=true
        IPV6_ADDRESS=$(ip -6 addr show scope global | grep -oP '(?<=inet6\s)[0-9a-f:]+' | head -n1)
        log_info "检测到IPv6地址: $IPV6_ADDRESS"
    else
        HAS_IPV6=false
        log_warn "未检测到IPv6地址"
    fi
}

# 显示菜单
show_menu() {
    clear
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}    Alpine Nginx 反向代理配置器        ${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo ""
    echo "1. 创建新的反向代理"
    echo "2. 查看现有配置"
    echo "3. 删除代理配置"
    echo "4. 测试Nginx配置"
    echo "5. 重启Nginx"
    echo "6. 扫描SSL证书"
    echo "7. 生成自签名证书"
    echo "8. 退出"
    echo ""
}

# 扫描现有SSL证书
scan_ssl_certs() {
    log_info "扫描现有SSL证书..."
    
    echo -e "${YELLOW}在以下位置搜索证书:${NC}"
    local cert_dirs=(
        "/etc/ssl"
        "/etc/ssl/certs"
        "/etc/letsencrypt/live"
        "/etc/nginx/ssl"
        "/root/.acme.sh"
    )
    
    for dir in "${cert_dirs[@]}"; do
        if [ -d "$dir" ]; then
            echo -e "\n${BLUE}目录: $dir${NC}"
            find "$dir" -name "*.crt" -o -name "*.pem" 2>/dev/null | while read cert; do
                local key="${cert%.*}.key"
                if [ -f "$key" ]; then
                    echo "  ✓ $(basename $cert)"
                fi
            done
        fi
    done
}

# 生成自签名证书
create_self_signed_cert() {
    echo ""
    read -p "请输入域名: " DOMAIN
    
    if [ -z "$DOMAIN" ]; then
        log_error "域名不能为空"
        return 1
    fi
    
    CERT_DIR="/etc/nginx/ssl/$DOMAIN"
    mkdir -p "$CERT_DIR"
    
    log_info "为 $DOMAIN 生成自签名证书..."
    
    openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
        -keyout "$CERT_DIR/key.pem" \
        -out "$CERT_DIR/cert.pem" \
        -subj "/C=US/ST=State/L=City/O=Organization/CN=$DOMAIN" 2>/dev/null
    
    if [ $? -eq 0 ]; then
        log_success "证书已生成到: $CERT_DIR/"
        echo -e "${YELLOW}证书文件:${NC}"
        echo "  $CERT_DIR/cert.pem"
        echo "  $CERT_DIR/key.pem"
    else
        log_error "证书生成失败"
    fi
}

# 创建反向代理配置
create_proxy() {
    echo ""
    echo -e "${GREEN}=== 创建新的反向代理 ===${NC}"
    
    # 配置名称
    while true; do
        read -p "配置名称（英文，用于文件名）: " CONFIG_NAME
        if [ -n "$CONFIG_NAME" ]; then
            CONFIG_FILE="/etc/nginx/sites-available/${CONFIG_NAME}.conf"
            if [ -f "$CONFIG_FILE" ]; then
                log_warn "配置文件已存在，请选择其他名称"
            else
                break
            fi
        fi
    done
    
    # 域名
    read -p "域名（多个用空格分隔）: " DOMAINS
    if [ -z "$DOMAINS" ]; then
        DOMAINS="_"
        log_warn "未指定域名，将配置为默认服务器"
    fi
    
    # 上游服务器
    while true; do
        read -p "上游服务器地址（如 http://localhost:3000）: " UPSTREAM
        if [ -n "$UPSTREAM" ]; then
            break
        fi
        log_error "上游服务器地址不能为空"
    done
    
    # 端口
    read -p "监听端口（默认80）: " PORT
    PORT=${PORT:-80}
    
    # SSL选项
    echo ""
    echo -e "${YELLOW}SSL选项:${NC}"
    echo "1. 不使用SSL（HTTP）"
    echo "2. 使用现有SSL证书"
    echo "3. 生成自签名证书"
    echo "4. 稍后配置"
    read -p "请选择（1-4）: " SSL_CHOICE
    
    case $SSL_CHOICE in
        2)
            read -p "证书文件路径（.crt或.pem）: " CERT_PATH
            read -p "私钥文件路径（.key）: " KEY_PATH
            ;;
        3)
            read -p "证书域名: " SSL_DOMAIN
            CERT_DIR="/etc/nginx/ssl/$SSL_DOMAIN"
            mkdir -p "$CERT_DIR"
            
            openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
                -keyout "$CERT_DIR/key.pem" \
                -out "$CERT_DIR/cert.pem" \
                -subj "/C=US/ST=State/L=City/O=Organization/CN=$SSL_DOMAIN" 2>/dev/null
            
            if [ $? -eq 0 ]; then
                CERT_PATH="$CERT_DIR/cert.pem"
                KEY_PATH="$CERT_DIR/key.pem"
                log_success "自签名证书已生成"
            else
                log_error "证书生成失败"
                CERT_PATH=""
                KEY_PATH=""
            fi
            ;;
        *)
            CERT_PATH=""
            KEY_PATH=""
            ;;
    esac
    
    # 高级选项
    echo ""
    echo -e "${YELLOW}高级选项:${NC}"
    read -p "启用WebSocket支持？(y/n, 默认y): " WEBSOCKET
    WEBSOCKET=${WEBSOCKET:-y}
    
    read -p "启用IPv6？(检测到IPv6: $HAS_IPV6) (y/n, 默认y): " ENABLE_IPV6
    ENABLE_IPV6=${ENABLE_IPV6:-y}
    
    # 生成配置文件
    generate_nginx_config "$CONFIG_NAME" "$DOMAINS" "$UPSTREAM" "$PORT" "$CERT_PATH" "$KEY_PATH" "$WEBSOCKET" "$ENABLE_IPV6"
    
    # 启用配置
    ln -sf "/etc/nginx/sites-available/${CONFIG_NAME}.conf" "/etc/nginx/sites-enabled/"
    
    log_success "配置文件已创建: /etc/nginx/sites-available/${CONFIG_NAME}.conf"
}

# 生成Nginx配置
generate_nginx_config() {
    local name="$1"
    local domains="$2"
    local upstream="$3"
    local port="$4"
    local cert_path="$5"
    local key_path="$6"
    local websocket="$7"
    local enable_ipv6="$8"
    
    local config_file="/etc/nginx/sites-available/${name}.conf"
    
    # 构建listen指令
    local listen_directives="listen ${port}"
    if [ -n "$cert_path" ] && [ -n "$key_path" ]; then
        listen_directives="${listen_directives} ssl"
        
        # 如果是443端口，启用HTTP/2
        if [ "$port" = "443" ] || [ "$port" = "8443" ]; then
            listen_directives="${listen_directives} http2"
        fi
    fi
    
    cat > "$config_file" <<EOF
# 反向代理配置: $name
# 生成时间: $(date)

server {
    $listen_directives;
    
    # IPv6支持
EOF
    
    if [ "$enable_ipv6" = "y" ] && [ "$HAS_IPV6" = true ]; then
        cat >> "$config_file" <<EOF
    listen [::]:${port};
EOF
        if [ -n "$cert_path" ] && [ -n "$key_path" ]; then
            cat >> "$config_file" <<EOF
    listen [::]:${port} ssl;
EOF
            if [ "$port" = "443" ] || [ "$port" = "8443" ]; then
                cat >> "$config_file" <<EOF
    listen [::]:${port} ssl http2;
EOF
            fi
        fi
    fi
    
    cat >> "$config_file" <<EOF
    
    server_name $domains;
    
    # SSL配置
EOF
    
    if [ -n "$cert_path" ] && [ -n "$key_path" ]; then
        cat >> "$config_file" <<EOF
    ssl_certificate $cert_path;
    ssl_certificate_key $key_path;
    
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-RSA-AES256-GCM-SHA512:DHE-RSA-AES256-GCM-SHA512;
    ssl_prefer_server_ciphers off;
    
    # 安全头
    add_header X-Frame-Options SAMEORIGIN;
    add_header X-Content-Type-Options nosniff;
    add_header X-XSS-Protection "1; mode=block";
    
EOF
    fi
    
    cat >> "$config_file" <<EOF
    # 访问日志
    access_log /var/log/nginx/${name}.access.log;
    error_log /var/log/nginx/${name}.error.log;
    
    # 上传大小限制
    client_max_body_size 100M;
    
    location / {
        proxy_pass $upstream;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
EOF
    
    if [ "$websocket" = "y" ]; then
        cat >> "$config_file" <<EOF
        
        # WebSocket支持
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
EOF
    fi
    
    cat >> "$config_file" <<EOF
    }
    
    # 健康检查端点
    location /nginx-health {
        access_log off;
        return 200 "healthy\\n";
        add_header Content-Type text/plain;
    }
}
EOF
    
    log_success "配置文件已生成"
}

# 查看现有配置
list_configs() {
    echo ""
    echo -e "${GREEN}=== 现有Nginx配置 ===${NC}"
    
    if [ -d /etc/nginx/sites-available ]; then
        for config in /etc/nginx/sites-available/*.conf; do
            if [ -f "$config" ]; then
                local name=$(basename "$config" .conf)
                local enabled=""
                if [ -f "/etc/nginx/sites-enabled/$name.conf" ]; then
                    enabled="${GREEN}[已启用]${NC}"
                else
                    enabled="${RED}[未启用]${NC}"
                fi
                echo -e "  $name $enabled"
                
                # 显示基本信息
                grep -E "server_name|proxy_pass|listen" "$config" | head -3 | sed 's/^/    /'
                echo ""
            fi
        done
    else
        log_warn "未找到配置文件"
    fi
}

# 删除配置
delete_config() {
    list_configs
    echo ""
    read -p "请输入要删除的配置名称: " CONFIG_NAME
    
    if [ -z "$CONFIG_NAME" ]; then
        log_error "配置名称不能为空"
        return
    fi
    
    local config_file="/etc/nginx/sites-available/${CONFIG_NAME}.conf"
    local enabled_file="/etc/nginx/sites-enabled/${CONFIG_NAME}.conf"
    
    if [ -f "$config_file" ]; then
        rm -f "$config_file" "$enabled_file"
        log_success "配置 '$CONFIG_NAME' 已删除"
    else
        log_error "配置 '$CONFIG_NAME' 不存在"
    fi
}

# 主函数
main() {
    # 检查权限
    check_root
    
    # 检查Alpine
    check_alpine
    
    # 安装Nginx（如果未安装）
    if ! command -v nginx >/dev/null 2>&1; then
        install_nginx
    else
        log_info "Nginx已安装"
    fi
    
    # 检测IPv6
    detect_ipv6
    
    # 创建Nginx基础配置（如果需要）
    if [ ! -f /etc/nginx/nginx.conf ] || ! grep -q "sites-enabled" /etc/nginx/nginx.conf; then
        create_base_config
    fi
    
    # 主循环
    while true; do
        show_menu
        read -p "请选择操作 (1-8): " choice
        
        case $choice in
            1)
                create_proxy
                test_nginx
                restart_nginx
                ;;
            2)
                list_configs
                ;;
            3)
                delete_config
                ;;
            4)
                test_nginx
                ;;
            5)
                restart_nginx
                ;;
            6)
                scan_ssl_certs
                ;;
            7)
                create_self_signed_cert
                ;;
            8)
                log_info "退出"
                exit 0
                ;;
            *)
                log_error "无效选择"
                ;;
        esac
        
        echo ""
        read -p "按回车键继续..."
    done
}

# 创建基础Nginx配置
create_base_config() {
    log_info "创建基础Nginx配置..."
    
    cat > /etc/nginx/nginx.conf <<'EOF'
user nginx;
worker_processes auto;
error_log /var/log/nginx/error.log warn;
pid /var/run/nginx.pid;

events {
    worker_connections 1024;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    
    log_format main '$remote_addr - $remote_user [$time_local] "$request" '
                    '$status $body_bytes_sent "$http_referer" '
                    '"$http_user_agent" "$http_x_forwarded_for"';
    
    access_log /var/log/nginx/access.log main;
    
    sendfile on;
    tcp_nopush on;
    tcp_nodelay on;
    keepalive_timeout 65;
    types_hash_max_size 2048;
    
    # 包含站点配置
    include /etc/nginx/sites-enabled/*;
    
    # 默认服务器 - 拒绝所有直接访问
    server {
        listen 80 default_server;
        listen [::]:80 default_server;
        server_name _;
        return 444;
    }
}
EOF
    
    log_success "基础配置已创建"
}

# 测试Nginx配置
test_nginx() {
    log_info "测试Nginx配置..."
    if nginx -t; then
        log_success "Nginx配置测试通过"
    else
        log_error "Nginx配置测试失败"
    fi
}

# 重启Nginx
restart_nginx() {
    log_info "重启Nginx..."
    
    if ps aux | grep -q "[n]ginx"; then
        nginx -s reload && log_success "Nginx已重载" || log_error "Nginx重载失败"
    else
        nginx && log_success "Nginx已启动" || log_error "Nginx启动失败"
    fi
}

# 运行主函数
main