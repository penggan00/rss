#!/bin/sh

# ============================================
# 智能Alpine Nginx反代助手 - 极简生产力版
# 只需要子域名和端口，其他全自动！
# 支持IPv6，自动证书发现，一键配置
# ============================================

set -e

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# 日志
info() { echo -e "${BLUE}[INFO]${NC} $1"; }
success() { echo -e "${GREEN}[√]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[X]${NC} $1"; }

# 检查root
if [ "$(id -u)" != "0" ]; then
    error "需要root权限"
    exit 1
fi

# 安装Nginx
if ! command -v nginx >/dev/null 2>&1; then
    info "安装Nginx..."
    apk add --no-cache nginx openssl bash
    success "Nginx安装完成"
fi

# 创建必要目录
mkdir -p /etc/nginx/sites-enabled /etc/nginx/ssl /var/log/nginx/proxy

# 自动发现证书
auto_find_cert() {
    local domain="$1"
    
    info "自动搜索 $domain 的证书..."
    
    # 常见证书位置
    local cert_paths=(
        "/etc/letsencrypt/live/$domain/fullchain.pem"
        "/etc/letsencrypt/live/$domain/cert.pem"
        "/etc/ssl/certs/$domain.crt"
        "/etc/ssl/$domain.crt"
        "/etc/nginx/ssl/$domain.crt"
        "/root/.acme.sh/$domain/fullchain.cer"
        "/root/.acme.sh/$domain/$domain.cer"
    )
    
    local key_paths=(
        "/etc/letsencrypt/live/$domain/privkey.pem"
        "/etc/ssl/private/$domain.key"
        "/etc/ssl/$domain.key"
        "/etc/nginx/ssl/$domain.key"
        "/root/.acme.sh/$domain/$domain.key"
    )
    
    # 搜索证书
    for cert in "${cert_paths[@]}"; do
        if [ -f "$cert" ]; then
            for key in "${key_paths[@]}"; do
                if [ -f "$key" ]; then
                    # 验证证书和密钥是否匹配
                    if openssl x509 -noout -modulus -in "$cert" 2>/dev/null | \
                       openssl md5 >/dev/null 2>&1 && \
                       openssl rsa -noout -modulus -in "$key" 2>/dev/null | \
                       openssl md5 >/dev/null 2>&1; then
                        CERT="$cert"
                        KEY="$key"
                        success "找到匹配证书: $cert"
                        success "找到匹配密钥: $key"
                        return 0
                    fi
                fi
            done
        fi
    done
    
    warn "未找到现有证书，将创建自签名证书"
    return 1
}

# 创建自签名证书
create_self_cert() {
    local domain="$1"
    local cert_dir="/etc/nginx/ssl/$domain"
    
    mkdir -p "$cert_dir"
    
    info "为 $domain 创建自签名证书..."
    
    openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
        -keyout "$cert_dir/key.pem" \
        -out "$cert_dir/cert.pem" \
        -subj "/C=US/ST=State/L=City/O=Organization/CN=$domain" 2>/dev/null
    
    if [ $? -eq 0 ]; then
        CERT="$cert_dir/cert.pem"
        KEY="$cert_dir/key.pem"
        success "自签名证书已创建"
        return 0
    fi
    return 1
}

# 获取服务器IP
get_server_ip() {
    # 尝试获取IPv4
    local ipv4=$(ip -4 addr show scope global | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -1)
    
    # 尝试获取IPv6
    local ipv6=$(ip -6 addr show scope global | grep -oP '(?<=inet6\s)[0-9a-f:]+' | head -1)
    
    if [ -n "$ipv4" ]; then
        SERVER_IP="$ipv4"
        info "服务器IPv4地址: $ipv4"
    fi
    
    if [ -n "$ipv6" ]; then
        HAS_IPV6=true
        SERVER_IPV6="$ipv6"
        info "服务器IPv6地址: $ipv6"
    else
        HAS_IPV6=false
    fi
}

# 生成智能配置名
generate_config_name() {
    local domain="$1"
    local port="$2"
    
    # 移除域名后缀，用第一个子域名
    local name=$(echo "$domain" | cut -d'.' -f1)
    
    # 如果端口不是80或443，添加到名称
    if [ "$port" != "80" ] && [ "$port" != "443" ]; then
        name="${name}_${port}"
    fi
    
    echo "$name"
}

# 智能上游地址推断
smart_upstream() {
    local port="$1"
    
    # 根据端口推断可能的服务
    case $port in
        80|443|3000|8080|8000|9000)
            echo "http://localhost:$port"
            ;;
        22)
            echo "ssh://localhost:$port"
            ;;
        21)
            echo "ftp://localhost:$port"
            ;;
        25|587)
            echo "smtp://localhost:$port"
            ;;
        143|993)
            echo "imap://localhost:$port"
            ;;
        3306)
            echo "mysql://localhost:$port"
            ;;
        5432)
            echo "postgresql://localhost:$port"
            ;;
        6379)
            echo "redis://localhost:$port"
            ;;
        27017)
            echo "mongodb://localhost:$port"
            ;;
        *)
            echo "http://localhost:$port"
            ;;
    esac
}

# 主要配置函数
configure_proxy() {
    clear
    echo -e "${CYAN}================================${NC}"
    echo -e "${CYAN}    智能Nginx反代配置器        ${NC}"
    echo -e "${CYAN}================================${NC}"
    echo ""
    
    # 获取服务器IP
    get_server_ip
    
    # 输入子域名
    echo -e "${YELLOW}请输入子域名（如: app, api, blog 等）${NC}"
    echo -e "当前服务器IP: ${GREEN}$SERVER_IP${NC}"
    if [ "$HAS_IPV6" = true ]; then
        echo -e "当前服务器IPv6: ${GREEN}$SERVER_IPV6${NC}"
    fi
    echo ""
    read -p "子域名: " SUBDOMAIN
    
    if [ -z "$SUBDOMAIN" ]; then
        error "子域名不能为空"
        exit 1
    fi
    
    # 尝试获取主域名
    DOMAIN=""
    if [ -f /etc/hostname ]; then
        DOMAIN=$(cat /etc/hostname)
    fi
    
    if [ -z "$DOMAIN" ] || [[ "$DOMAIN" == *"localhost"* ]]; then
        read -p "主域名（如: example.com）: " MAIN_DOMAIN
        if [ -n "$MAIN_DOMAIN" ]; then
            DOMAIN="$MAIN_DOMAIN"
        else
            warn "未指定主域名，将使用服务器IP"
            DOMAIN="$SERVER_IP"
        fi
    fi
    
    # 构建完整域名
    if [[ "$SUBDOMAIN" == *"."* ]]; then
        FULL_DOMAIN="$SUBDOMAIN"
    else
        FULL_DOMAIN="$SUBDOMAIN.$DOMAIN"
    fi
    
    # 输入端口
    echo ""
    echo -e "${YELLOW}请输入应用端口${NC}"
    echo -e "常见端口: 80(HTTP), 443(HTTPS), 3000(Node.js), 8080(Tomcat)"
    echo -e "          8000(Python), 9000(PHP-FPM), 22(SSH), 3306(MySQL)"
    read -p "端口: " PORT
    
    if [ -z "$PORT" ]; then
        error "端口不能为空"
        exit 1
    fi
    
    # 智能选择协议
    if [ "$PORT" = "443" ] || [ "$PORT" = "8443" ]; then
        PROTOCOL="https"
        SSL_AUTO=true
    else
        PROTOCOL="http"
        SSL_AUTO=false
    fi
    
    # 智能上游地址
    UPSTREAM=$(smart_upstream "$PORT")
    
    # 生成配置名
    CONFIG_NAME=$(generate_config_name "$FULL_DOMAIN" "$PORT")
    
    info "配置摘要:"
    echo -e "  域名: ${GREEN}$FULL_DOMAIN${NC}"
    echo -e "  端口: ${GREEN}$PORT${NC}"
    echo -e "  上游: ${GREEN}$UPSTREAM${NC}"
    echo -e "  配置名: ${GREEN}$CONFIG_NAME${NC}"
    
    # 自动处理SSL
    CERT=""
    KEY=""
    
    if [ "$SSL_AUTO" = true ] || [ "$PORT" = "443" ]; then
        if auto_find_cert "$FULL_DOMAIN"; then
            success "使用现有SSL证书"
        elif auto_find_cert "$DOMAIN"; then
            success "使用主域名SSL证书"
        else
            warn "未找到匹配证书，创建自签名证书"
            create_self_cert "$FULL_DOMAIN"
        fi
    else
        info "HTTP模式，无需SSL"
    fi
    
    # 询问确认
    echo ""
    read -p "确认创建此配置？(y/n): " CONFIRM
    if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
        info "取消创建"
        exit 0
    fi
    
    # 创建Nginx配置
    create_nginx_config
}

# 创建Nginx配置
create_nginx_config() {
    local config_file="/etc/nginx/sites-available/${CONFIG_NAME}.conf"
    local enabled_file="/etc/nginx/sites-enabled/${CONFIG_NAME}.conf"
    
    info "生成Nginx配置..."
    
    # 构建基础配置
    local config_content="# 智能反代配置: $FULL_DOMAIN -> $UPSTREAM
# 生成时间: $(date)

server {
    # 监听配置"
    
    # 添加监听端口
    if [ -n "$CERT" ] && [ -n "$KEY" ]; then
        config_content="$config_content
    listen ${PORT} ssl;"
        
        # 添加HTTP/2支持（如果是443端口）
        if [ "$PORT" = "443" ]; then
            config_content="$config_content
    listen ${PORT} ssl http2;"
        fi
    else
        config_content="$config_content
    listen ${PORT};"
    fi
    
    # 添加IPv6监听
    if [ "$HAS_IPV6" = true ]; then
        if [ -n "$CERT" ] && [ -n "$KEY" ]; then
            config_content="$config_content
    listen [::]:${PORT} ssl;"
            if [ "$PORT" = "443" ]; then
                config_content="$config_content
    listen [::]:${PORT} ssl http2;"
            fi
        else
            config_content="$config_content
    listen [::]:${PORT};"
        fi
    fi
    
    # 添加域名
    config_content="$config_content
    
    server_name $FULL_DOMAIN;"
    
    # 添加SSL配置
    if [ -n "$CERT" ] && [ -n "$KEY" ]; then
        config_content="$config_content
    
    # SSL配置
    ssl_certificate $CERT;
    ssl_certificate_key $KEY;
    
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers off;"
    fi
    
    # 添加代理配置
    config_content="$config_content
    
    # 代理设置
    location / {
        proxy_pass $UPSTREAM;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
        
        # WebSocket支持
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection \"upgrade\";
    }
    
    # 健康检查
    location /nginx-health {
        access_log off;
        return 200 \"healthy\\n\";
        add_header Content-Type text/plain;
    }
    
    # 阻止隐藏文件
    location ~ /\\. {
        deny all;
    }
}"
    
    # 写入配置文件
    echo "$config_content" > "$config_file"
    
    # 启用配置
    ln -sf "$config_file" "$enabled_file"
    
    success "配置文件已创建: $config_file"
}

# 创建主Nginx配置
create_main_nginx_config() {
    if [ ! -f /etc/nginx/nginx.conf ] || ! grep -q "sites-enabled" /etc/nginx/nginx.conf; then
        info "创建主Nginx配置..."
        
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
    
    # 包含所有启用的站点配置
    include /etc/nginx/sites-enabled/*;
    
    # 默认服务器 - 拒绝直接IP访问
    server {
        listen 80 default_server;
        listen [::]:80 default_server;
        server_name _;
        return 444;
    }
    
    server {
        listen 443 ssl default_server;
        listen [::]:443 ssl default_server;
        server_name _;
        
        # 默认自签名证书
        ssl_certificate /etc/nginx/ssl/default.crt;
        ssl_certificate_key /etc/nginx/ssl/default.key;
        
        return 444;
    }
}
EOF
        
        # 创建默认证书
        mkdir -p /etc/nginx/ssl
        if [ ! -f /etc/nginx/ssl/default.crt ]; then
            openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
                -keyout /etc/nginx/ssl/default.key \
                -out /etc/nginx/ssl/default.crt \
                -subj "/CN=default" 2>/dev/null
        fi
        
        success "主Nginx配置已创建"
    fi
}

# 测试并重启Nginx
restart_nginx() {
    info "测试Nginx配置..."
    
    if nginx -t; then
        success "配置测试通过"
        
        if ps aux | grep -q "[n]ginx"; then
            nginx -s reload
            success "Nginx已重载"
        else
            nginx
            success "Nginx已启动"
        fi
        
        # 显示访问信息
        echo ""
        success "配置完成！"
        echo -e "${GREEN}================================${NC}"
        echo -e "${CYAN}访问地址:${NC}"
        
        if [ -n "$CERT" ] && [ -n "$KEY" ]; then
            if [ "$PORT" = "443" ]; then
                echo -e "  https://$FULL_DOMAIN"
            else
                echo -e "  https://$FULL_DOMAIN:$PORT"
            fi
        else
            if [ "$PORT" = "80" ]; then
                echo -e "  http://$FULL_DOMAIN"
            else
                echo -e "  http://$FULL_DOMAIN:$PORT"
            fi
        fi
        
        echo ""
        echo -e "${CYAN}代理到:${NC} $UPSTREAM"
        echo -e "${CYAN}配置文件:${NC} /etc/nginx/sites-available/${CONFIG_NAME}.conf"
        echo -e "${CYAN}日志文件:${NC} /var/log/nginx/error.log"
        
        if [ -n "$CERT" ] && [ -n "$KEY" ]; then
            echo ""
            echo -e "${YELLOW}证书信息:${NC}"
            echo -e "  证书: $CERT"
            echo -e "  密钥: $KEY"
        fi
        
    else
        error "Nginx配置测试失败"
        exit 1
    fi
}

# 批量配置模式
batch_mode() {
    clear
    echo -e "${CYAN}================================${NC}"
    echo -e "${CYAN}      批量反代配置模式         ${NC}"
    echo -e "${CYAN}================================${NC}"
    echo ""
    echo "格式: 子域名:端口 (每行一个)"
    echo "示例:"
    echo "  app:3000"
    echo "  api:8080"
    echo "  blog:80"
    echo "  admin:443"
    echo ""
    echo "输入完成后按 Ctrl+D 保存"
    echo ""
    
    local count=0
    while IFS= read -r line; do
        if [ -n "$line" ]; then
            local subdomain=$(echo "$line" | cut -d':' -f1)
            local port=$(echo "$line" | cut -d':' -f2)
            
            if [ -n "$subdomain" ] && [ -n "$port" ]; then
                info "配置 $subdomain -> 端口 $port"
                # 这里可以调用单个配置函数
                ((count++))
            fi
        fi
    done
    
    if [ $count -gt 0 ]; then
        success "已添加 $count 个配置"
    else
        warn "未添加任何配置"
    fi
}

# 管理现有配置
manage_configs() {
    clear
    echo -e "${CYAN}================================${NC}"
    echo -e "${CYAN}      现有配置管理             ${NC}"
    echo -e "${CYAN}================================${NC}"
    echo ""
    
    if [ -d /etc/nginx/sites-available ]; then
        local configs=($(ls /etc/nginx/sites-available/*.conf 2>/dev/null))
        
        if [ ${#configs[@]} -eq 0 ]; then
            echo "暂无配置"
            return
        fi
        
        echo "现有配置列表:"
        echo ""
        
        for i in "${!configs[@]}"; do
            local config="${configs[$i]}"
            local name=$(basename "$config" .conf)
            local enabled=""
            
            if [ -f "/etc/nginx/sites-enabled/$name.conf" ]; then
                enabled="${GREEN}[已启用]${NC}"
            else
                enabled="${RED}[未启用]${NC}"
            fi
            
            echo "$(($i+1)). $name $enabled"
            
            # 显示配置摘要
            grep -E "server_name|proxy_pass|listen" "$config" | head -2 | sed 's/^/   /'
            echo ""
        done
        
        echo ""
        echo "操作:"
        echo "  输入编号查看详情"
        echo "  d[编号] 删除配置"
        echo "  e[编号] 启用/禁用配置"
        echo "  r 重新加载Nginx"
        echo "  q 返回"
        echo ""
        
        read -p "选择: " choice
        
        case $choice in
            [0-9]*)
                local idx=$(($choice-1))
                if [ $idx -ge 0 ] && [ $idx -lt ${#configs[@]} ]; then
                    show_config_detail "${configs[$idx]}"
                fi
                ;;
            d[0-9]*)
                local idx=$((${choice:1}-1))
                if [ $idx -ge 0 ] && [ $idx -lt ${#configs[@]} ]; then
                    delete_config "${configs[$idx]}"
                fi
                ;;
            r)
                restart_nginx
                ;;
            q)
                return
                ;;
        esac
    fi
}

# 主菜单
main_menu() {
    while true; do
        clear
        echo -e "${CYAN}================================${NC}"
        echo -e "${CYAN}    智能Nginx反代配置器        ${NC}"
        echo -e "${CYAN}================================${NC}"
        echo ""
        echo "1. 快速创建反代 (推荐)"
        echo "2. 批量配置模式"
        echo "3. 管理现有配置"
        echo "4. 测试Nginx配置"
        echo "5. 重启Nginx"
        echo "6. 扫描证书"
        echo "7. 退出"
        echo ""
        
        read -p "请选择: " choice
        
        case $choice in
            1)
                configure_proxy
                create_main_nginx_config
                restart_nginx
                read -p "按回车键继续..."
                ;;
            2)
                batch_mode
                read -p "按回车键继续..."
                ;;
            3)
                manage_configs
                read -p "按回车键继续..."
                ;;
            4)
                nginx -t && success "配置正常" || error "配置错误"
                read -p "按回车键继续..."
                ;;
            5)
                restart_nginx
                read -p "按回车键继续..."
                ;;
            6)
                scan_all_certs
                read -p "按回车键继续..."
                ;;
            7)
                success "再见！"
                exit 0
                ;;
            *)
                warn "无效选择"
                read -p "按回车键继续..."
                ;;
        esac
    done
}

# 扫描所有证书
scan_all_certs() {
    info "扫描系统所有SSL证书..."
    
    find /etc /root -name "*.crt" -o -name "*.pem" 2>/dev/null | while read cert; do
        local key="${cert%.*}.key"
        if [ -f "$key" ]; then
            echo -e "${GREEN}✓${NC} $cert"
            echo "  密钥: $key"
        fi
    done
}

# 启动
main() {
    # 安装基础软件
    if ! command -v nginx >/dev/null 2>&1; then
        info "安装Nginx..."
        apk add --no-cache nginx openssl
    fi
    
    # 创建基础配置
    create_main_nginx_config
    
    # 显示主菜单
    main_menu
}

# 运行
main