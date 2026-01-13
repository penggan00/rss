#!/bin/sh

# ==================== 生产力Alpine Nginx反代配置器 ====================
# 支持纯IPv6环境，交互式一键配置，自动SSL证书管理
# 使用方法：wget -O nginx-proxy.sh https://your-domain.com/nginx-proxy.sh && sh nginx-proxy.sh
# ===================================================================

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m'

# 日志函数
log() { echo -e "${GREEN}[$(date +'%H:%M:%S')]${NC} $1"; }
info() { echo -e "${BLUE}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }
success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
ask() { echo -e "${CYAN}[?]${NC} $1"; }

# 配置路径
CONFIG_DIR="/etc/nginx/proxy-configs"
SSL_DIR="/etc/nginx/ssl"
BACKUP_DIR="/etc/nginx/backups"
TEMPLATE_DIR="/etc/nginx/templates"

# 检查并安装依赖
install_dependencies() {
    log "检查系统依赖..."
    
    if ! command -v nginx >/dev/null 2>&1; then
        info "安装nginx..."
        apk add --no-cache nginx
    fi
    
    # 安装常用工具
    apk add --no-cache \
        bash \
        curl \
        wget \
        nano \
        openssl \
        certbot \
        python3 \
        py3-certbot-nginx || warn "部分可选包安装失败"
    
    # 创建目录结构
    mkdir -p "$CONFIG_DIR" "$SSL_DIR" "$BACKUP_DIR" "$TEMPLATE_DIR" \
        /etc/nginx/sites-available /etc/nginx/sites-enabled \
        /var/log/nginx/proxy
}

# 检测IPv6
detect_ipv6() {
    log "检测网络配置..."
    
    IPV4_ADDRESS=$(ip -4 addr show scope global | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -1)
    
    if ip -6 addr show scope global | grep -q inet6; then
        IPV6_ADDRESS=$(ip -6 addr show scope global | grep -oP '(?<=inet6\s)[0-9a-f:]+' | head -1)
        HAS_IPV6=true
        success "检测到IPv6地址: $IPV6_ADDRESS"
    else
        HAS_IPV6=false
        warn "未检测到IPv6地址，将仅配置IPv4"
    fi
}

# 备份现有配置
backup_config() {
    local timestamp=$(date +'%Y%m%d_%H%M%S')
    
    if [ -d /etc/nginx ]; then
        log "备份现有nginx配置..."
        tar -czf "$BACKUP_DIR/nginx_backup_$timestamp.tar.gz" -C /etc/nginx .
        success "配置已备份到: $BACKUP_DIR/nginx_backup_$timestamp.tar.gz"
    fi
}

# 扫描现有证书
scan_existing_certs() {
    log "扫描现有SSL证书..."
    
    local certs=()
    
    # 查找常见的证书路径
    for dir in /etc/ssl /etc/ssl/certs /etc/letsencrypt/live /etc/nginx/ssl; do
        if [ -d "$dir" ]; then
            find "$dir" -name "*.crt" -o -name "*.pem" 2>/dev/null | while read cert; do
                local key="${cert%.*}.key"
                if [ -f "$key" ]; then
                    certs+=("$cert")
                fi
            done
        fi
    done
    
    if [ ${#certs[@]} -eq 0 ]; then
        warn "未找到现有SSL证书"
        return 1
    else
        success "找到 ${#certs[@]} 个证书对"
        
        echo -e "\n${YELLOW}=== 现有证书列表 ===${NC}"
        for i in "${!certs[@]}"; do
            local cert="${certs[$i]}"
            local key="${cert%.*}.key"
            local subject=$(openssl x509 -in "$cert" -subject -noout 2>/dev/null | sed 's/subject=//')
            local expiry=$(openssl x509 -in "$cert" -enddate -noout 2>/dev/null | sed 's/notAfter=//')
            
            echo "$(($i+1)). ${CYAN}$cert${NC}"
            echo "   主题: $subject"
            echo "   到期: $expiry"
            echo "   密钥: $key"
            echo ""
        done
        return 0
    fi
}

# 显示可用证书菜单
show_cert_menu() {
    echo -e "\n${CYAN}=== SSL证书选择 ===${NC}"
    echo "1. 使用现有证书"
    echo "2. 使用自签名证书（测试用）"
    echo "3. 使用Certbot申请新证书"
    echo "4. 稍后配置"
    echo ""
}

# 创建自签名证书
create_self_signed_cert() {
    local domain="$1"
    
    log "为 $domain 创建自签名证书..."
    
    mkdir -p "$SSL_DIR/$domain"
    
    openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
        -keyout "$SSL_DIR/$domain/key.pem" \
        -out "$SSL_DIR/$domain/cert.pem" \
        -subj "/C=US/ST=State/L=City/O=Organization/CN=$domain" 2>/dev/null
    
    if [ $? -eq 0 ]; then
        success "自签名证书已创建: $SSL_DIR/$domain/"
        CERT_PATH="$SSL_DIR/$domain/cert.pem"
        KEY_PATH="$SSL_DIR/$domain/key.pem"
        return 0
    else
        error "创建自签名证书失败"
        return 1
    fi
}

# 配置基础nginx
configure_base_nginx() {
    log "配置基础nginx设置..."
    
    # 生成nginx主配置
    cat > /etc/nginx/nginx.conf <<'EOF'
user nginx;
worker_processes auto;
error_log /var/log/nginx/error.log warn;
pid /var/run/nginx.pid;

events {
    worker_connections 1024;
    use epoll;
    multi_accept on;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    
    # 日志格式
    log_format main '$remote_addr - $remote_user [$time_local] "$request" '
                    '$status $body_bytes_sent "$http_referer" '
                    '"$http_user_agent" "$http_x_forwarded_for"';
    
    log_format proxy '$remote_addr - $remote_user [$time_local] "$request" '
                     '$status $body_bytes_sent "$http_referer" "$upstream_addr" '
                     '$upstream_status $upstream_response_time "$http_user_agent"';
    
    access_log /var/log/nginx/access.log main;
    
    # 基础优化
    sendfile on;
    tcp_nopush on;
    tcp_nodelay on;
    keepalive_timeout 65;
    types_hash_max_size 2048;
    server_tokens off;
    client_max_body_size 100M;
    
    # Gzip压缩
    gzip on;
    gzip_vary on;
    gzip_proxied any;
    gzip_comp_level 6;
    gzip_types text/plain text/css text/xml text/javascript 
               application/json application/javascript application/xml+rss 
               application/atom+xml image/svg+xml;
    
    # 缓存
    open_file_cache max=1000 inactive=20s;
    open_file_cache_valid 30s;
    open_file_cache_min_uses 2;
    open_file_cache_errors on;
    
    # 加载模块
    include /etc/nginx/conf.d/*.conf;
    include /etc/nginx/sites-enabled/*;
    
    # 默认服务器 - 拒绝所有直接访问
    server {
        listen 80 default_server;
        listen [::]:80 default_server;
        listen 443 ssl default_server;
        listen [::]:443 ssl default_server;
        server_name _;
        
        ssl_certificate /etc/nginx/ssl/default/dummy.crt;
        ssl_certificate_key /etc/nginx/ssl/default/dummy.key;
        
        return 444;
    }
}
EOF
    
    # 创建默认证书（用于拒绝访问）
    mkdir -p "$SSL_DIR/default"
    if [ ! -f "$SSL_DIR/default/dummy.crt" ]; then
        openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
            -keyout "$SSL_DIR/default/dummy.key" \
            -out "$SSL_DIR/default/dummy.crt" \
            -subj "/CN=invalid" 2>/dev/null
    fi
}

# 交互式收集代理配置
collect_proxy_config() {
    echo -e "\n${PURPLE}=== 配置新的反向代理 ===${NC}"
    
    # 代理名称
    while true; do
        ask "请输入代理配置名称（英文，用于文件名）: "
        read PROXY_NAME
        if [ -n "$PROXY_NAME" ]; then
            break
        fi
        warn "配置名称不能为空"
    done
    
    # 域名
    ask "请输入域名（多个用空格分隔，如：example.com www.example.com）: "
    read DOMAINS
    
    # 上游服务器
    ask "请输入上游服务器地址（如：http://localhost:3000 或 http://192.168.1.100:8080）: "
    read UPSTREAM
    
    # 监听端口
    ask "请输入监听端口（默认443）: "
    read LISTEN_PORT
    LISTEN_PORT=${LISTEN_PORT:-443}
    
    # IPv6支持
    if [ "$HAS_IPV6" = true ]; then
        ask "是否启用IPv6支持？(y/n，默认y): "
        read ENABLE_IPV6
        ENABLE_IPV6=${ENABLE_IPV6:-y}
    else
        ENABLE_IPV6="n"
    fi
    
    # SSL选择
    show_cert_menu
    ask "请选择SSL证书选项（1-4）: "
    read SSL_CHOICE
    
    case $SSL_CHOICE in
        1)
            # 选择现有证书
            scan_existing_certs
            ask "请输入证书文件完整路径: "
            read CERT_PATH
            ask "请输入密钥文件完整路径: "
            read KEY_PATH
            ;;
        2)
            # 自签名证书
            if [ -n "$DOMAINS" ]; then
                local primary_domain=$(echo "$DOMAINS" | awk '{print $1}')
                create_self_signed_cert "$primary_domain"
            fi
            ;;
        3)
            # Certbot申请（如果有域名）
            if [ -n "$DOMAINS" ]; then
                ask "请输入邮箱地址: "
                read EMAIL
                info "将使用Certbot申请证书..."
                # 这里可以添加Certbot申请逻辑
            fi
            ;;
        4)
            info "SSL证书稍后配置"
            CERT_PATH=""
            KEY_PATH=""
            ;;
    esac
    
    # 高级选项
    echo -e "\n${YELLOW}=== 高级选项 ===${NC}"
    ask "是否启用WebSocket支持？(y/n，默认y): "
    read ENABLE_WEBSOCKET
    ENABLE_WEBSOCKET=${ENABLE_WEBSOCKET:-y}
    
    ask "是否启用HTTP/2？(y/n，默认y): "
    read ENABLE_HTTP2
    ENABLE_HTTP2=${ENABLE_HTTP2:-y}
    
    ask "是否启用缓存？(y/n，默认n): "
    read ENABLE_CACHE
    ENABLE_CACHE=${ENABLE_CACHE:-n}
    
    # 保存配置到文件
    save_proxy_config
}

# 保存代理配置
save_proxy_config() {
    local config_file="$CONFIG_DIR/${PROXY_NAME}.conf"
    
    cat > "$config_file" <<EOF
# ===========================================
# 代理配置: $PROXY_NAME
# 生成时间: $(date)
# ===========================================

# 域名
DOMAINS="$DOMAINS"

# 上游服务器
UPSTREAM="$UPSTREAM"

# 监听配置
LISTEN_PORT="$LISTEN_PORT"
ENABLE_IPV6="$ENABLE_IPV6"

# SSL配置
CERT_PATH="$CERT_PATH"
KEY_PATH="$KEY_PATH"

# 高级功能
ENABLE_WEBSOCKET="$ENABLE_WEBSOCKET"
ENABLE_HTTP2="$ENABLE_HTTP2"
ENABLE_CACHE="$ENABLE_CACHE"
EOF
    
    success "配置已保存到: $config_file"
}

# 生成nginx配置文件
generate_nginx_config() {
    local config_file="$CONFIG_DIR/${PROXY_NAME}.conf"
    local nginx_config="/etc/nginx/sites-available/${PROXY_NAME}.conf"
    local enabled_config="/etc/nginx/sites-enabled/${PROXY_NAME}.conf"
    
    # 加载配置
    source "$config_file"
    
    # 开始生成nginx配置
    log "生成nginx配置文件: $nginx_config"
    
    # 构建listen指令
    local listen_directives="listen ${LISTEN_PORT}"
    if [ "$ENABLE_HTTP2" = "y" ] && [ -n "$CERT_PATH" ]; then
        listen_directives="$listen_directives http2 ssl"
    elif [ -n "$CERT_PATH" ]; then
        listen_directives="$listen_directives ssl"
    fi
    
    # 添加IPv6监听
    if [ "$ENABLE_IPV6" = "y" ] && [ "$HAS_IPV6" = true ]; then
        listen_directives="$listen_directives;\n    listen [::]:${LISTEN_PORT}"
        if [ "$ENABLE_HTTP2" = "y" ] && [ -n "$CERT_PATH" ]; then
            listen_directives="$listen_directives http2 ssl"
        elif [ -n "$CERT_PATH" ]; then
            listen_directives="$listen_directives ssl"
        fi
    fi
    
    # 生成配置文件
    cat > "$nginx_config" <<EOF
# 反向代理配置: $PROXY_NAME
# 生成时间: $(date)

server {
    $listen_directives;
    
    # 域名
    server_name ${DOMAINS};
    
    # SSL配置
EOF
    
    if [ -n "$CERT_PATH" ] && [ -n "$KEY_PATH" ]; then
        cat >> "$nginx_config" <<EOF
    ssl_certificate $CERT_PATH;
    ssl_certificate_key $KEY_PATH;
    
    # SSL优化
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-RSA-AES256-GCM-SHA512:DHE-RSA-AES256-GCM-SHA512:ECDHE-RSA-AES256-GCM-SHA384:DHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;
    ssl_session_tickets off;
    
    # HSTS（生产环境建议启用）
    # add_header Strict-Transport-Security "max-age=63072000" always;
    
EOF
    fi
    
    cat >> "$nginx_config" <<EOF
    # 安全头
    add_header X-Frame-Options SAMEORIGIN;
    add_header X-Content-Type-Options nosniff;
    add_header X-XSS-Protection "1; mode=block";
    add_header Referrer-Policy "strict-origin-when-cross-origin";
    
    # 访问日志
    access_log /var/log/nginx/proxy/${PROXY_NAME}.access.log proxy;
    error_log /var/log/nginx/proxy/${PROXY_NAME}.error.log;
    
    # 上传大小限制
    client_max_body_size 100M;
    
    # 代理设置
    location / {
        proxy_pass $UPSTREAM;
        
        # 基础代理头
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        
        # 超时设置
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
        
        # 缓冲设置
        proxy_buffering on;
        proxy_buffer_size 4k;
        proxy_buffers 8 4k;
        proxy_busy_buffers_size 8k;
        
        # 禁用特定代理头
        proxy_hide_header X-Powered-By;
        proxy_hide_header Server;
EOF
    
    # WebSocket支持
    if [ "$ENABLE_WEBSOCKET" = "y" ]; then
        cat >> "$nginx_config" <<EOF
        
        # WebSocket支持
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
EOF
    fi
    
    # 缓存配置
    if [ "$ENABLE_CACHE" = "y" ]; then
        cat >> "$nginx_config" <<EOF
        
        # 缓存静态文件
        location ~* \.(jpg|jpeg|png|gif|ico|css|js|svg|woff|woff2|ttf|eot)$ {
            proxy_pass $UPSTREAM;
            proxy_cache proxy_cache;
            proxy_cache_valid 200 302 1d;
            proxy_cache_valid 404 1m;
            proxy_cache_use_stale error timeout updating http_500 http_502 http_503 http_504;
            add_header X-Cache-Status \$upstream_cache_status;
            
            # 保持代理头
            proxy_set_header Host \$host;
            proxy_set_header X-Real-IP \$remote_addr;
            proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto \$scheme;
        }
EOF
    fi
    
    # 健康检查
    cat >> "$nginx_config" <<EOF
    
    # 健康检查
    location /nginx-health {
        access_log off;
        return 200 "healthy\n";
        add_header Content-Type text/plain;
    }
    
    # 阻止隐藏文件
    location ~ /\. {
        deny all;
    }
}
EOF
    
    # 创建缓存配置（如果需要）
    if [ "$ENABLE_CACHE" = "y" ]; then
        cat >> /etc/nginx/nginx.conf <<'EOF'

# 代理缓存配置
proxy_cache_path /var/cache/nginx/proxy levels=1:2 keys_zone=proxy_cache:10m max_size=1g 
                 inactive=60m use_temp_path=off;
EOF
        mkdir -p /var/cache/nginx/proxy
    fi
    
    # 启用配置
    ln -sf "$nginx_config" "$enabled_config"
    success "nginx配置文件已生成: $nginx_config"
}

# 测试并重载nginx
test_and_reload_nginx() {
    log "测试nginx配置..."
    
    if nginx -t; then
        success "nginx配置测试通过"
        
        # 重载nginx
        if ps aux | grep -q "[n]ginx"; then
            nginx -s reload
            success "nginx已重载"
        else
            nginx
            success "nginx已启动"
        fi
        
        # 显示配置摘要
        show_config_summary
    else
        error "nginx配置测试失败，请检查错误信息"
        exit 1
    fi
}

# 显示配置摘要
show_config_summary() {
    echo -e "\n${GREEN}========================================${NC}"
    echo -e "${GREEN}       反向代理配置完成！               ${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo ""
    echo -e "${CYAN}配置名称:${NC} $PROXY_NAME"
    echo -e "${CYAN}域名:${NC} $DOMAINS"
    echo -e "${CYAN}上游服务器:${NC} $UPSTREAM"
    echo -e "${CYAN}监听端口:${NC} $LISTEN_PORT"
    echo -e "${CYAN}IPv6支持:${NC} $ENABLE_IPV6"
    
    if [ -n "$CERT_PATH" ]; then
        echo -e "${CYAN}SSL证书:${NC} $CERT_PATH"
        echo -e "${CYAN}SSL密钥:${NC} $KEY_PATH"
    else
        echo -e "${CYAN}SSL:${NC} 未配置"
    fi
    
    echo ""
    echo -e "${YELLOW}配置文件:${NC}"
    echo "  Nginx配置: /etc/nginx/sites-available/${PROXY_NAME}.conf"
    echo "  已启用配置: /etc/nginx/sites-enabled/${PROXY_NAME}.conf"
    echo "  代理配置备份: $CONFIG_DIR/${PROXY_NAME}.conf"
    echo ""
    echo -e "${YELLOW}日志文件:${NC}"
    echo "  访问日志: /var/log/nginx/proxy/${PROXY_NAME}.access.log"
    echo "  错误日志: /var/log/nginx/proxy/${PROXY_NAME}.error.log"
    echo ""
    echo -e "${GREEN}现在可以通过以下地址访问:${NC}"
    for domain in $DOMAINS; do
        if [ -n "$CERT_PATH" ]; then
            echo "  https://$domain"
        else
            echo "  http://$domain:$LISTEN_PORT"
        fi
    done
    echo ""
}

# 管理菜单
show_main_menu() {
    clear
    echo -e "${PURPLE}========================================${NC}"
    echo -e "${PURPLE}    生产力Alpine Nginx反代配置器        ${NC}"
    echo -e "${PURPLE}         支持IPv6 & SSL                ${NC}"
    echo -e "${PURPLE}========================================${NC}"
    echo ""
    echo "1. 创建新的反向代理配置"
    echo "2. 查看现有配置"
    echo "3. 删除代理配置"
    echo "4. 重新生成nginx配置"
    echo "5. 测试nginx配置"
    echo "6. 重新加载nginx"
    echo "7. 扫描现有SSL证书"
    echo "8. 退出"
    echo ""
}

# 查看现有配置
list_existing_configs() {
    echo -e "\n${CYAN}=== 现有代理配置 ===${NC}"
    
    if ls "$CONFIG_DIR"/*.conf 2>/dev/null; then
        for config in "$CONFIG_DIR"/*.conf; do
            local name=$(basename "$config" .conf)
            echo -e "\n${YELLOW}$name${NC}:"
            cat "$config" | grep -E "^(DOMAINS|UPSTREAM|LISTEN_PORT)=" | sed 's/^/  /'
        done
    else
        echo "暂无配置"
    fi
}

# 删除配置
delete_config() {
    list_existing_configs
    echo ""
    ask "请输入要删除的配置名称: "
    read CONFIG_NAME
    
    if [ -f "$CONFIG_DIR/${CONFIG_NAME}.conf" ]; then
        rm -f "$CONFIG_DIR/${CONFIG_NAME}.conf" \
              "/etc/nginx/sites-available/${CONFIG_NAME}.conf" \
              "/etc/nginx/sites-enabled/${CONFIG_NAME}.conf"
        success "配置 '$CONFIG_NAME' 已删除"
    else
        error "配置 '$CONFIG_NAME' 不存在"
    fi
}

# 主函数
main() {
    check_root
    install_dependencies
    detect_ipv6
    backup_config
    configure_base_nginx
    
    while true; do
        show_main_menu
        ask "请选择操作 (1-8): "
        read choice
        
        case $choice in
            1)
                collect_proxy_config
                generate_nginx_config
                test_and_reload_nginx
                ask "按回车键继续..."
                ;;
            2)
                list_existing_configs
                ask "按回车键继续..."
                ;;
            3)
                delete_config
                ask "按回车键继续..."
                ;;
            4)
                ask "请输入配置名称: "
                read CONFIG_NAME
                if [ -f "$CONFIG_DIR/${CONFIG_NAME}.conf" ]; then
                    PROXY_NAME="$CONFIG_NAME"
                    source "$CONFIG_DIR/${CONFIG_NAME}.conf"
                    generate_nginx_config
                    test_and_reload_nginx
                else
                    error "配置不存在"
                fi
                ask "按回车键继续..."
                ;;
            5)
                nginx -t && success "配置测试通过" || error "配置测试失败"
                ask "按回车键继续..."
                ;;
            6)
                nginx -s reload && success "nginx已重载" || error "重载失败"
                ask "按回车键继续..."
                ;;
            7)
                scan_existing_certs
                ask "按回车键继续..."
                ;;
            8)
                log "退出配置器"
                exit 0
                ;;
            *)
                warn "无效选择"
                ;;
        esac
    done
}

# 检查是否以root运行
if [ "$(id -u)" -ne 0 ]; then
    error "请以root用户运行此脚本"
    exit 1
fi

# 启动主程序
main "$@"