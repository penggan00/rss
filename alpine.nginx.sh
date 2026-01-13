#!/bin/sh

# ============================================
# Alpine Nginx 反代 - 极简版
# 兼容ash，无bash特定语法
# ============================================

# 安装Nginx
install_nginx() {
    echo "安装Nginx..."
    apk add --no-cache nginx openssl
    mkdir -p /etc/nginx/sites-enabled /var/log/nginx
}

# 查找证书
find_cert() {
    echo "搜索证书..."
    
    # 检查nginx ssl目录
    if [ -d /etc/nginx/ssl ]; then
        for cert in /etc/nginx/ssl/*.crt; do
            [ -f "$cert" ] || continue
            domain=$(basename "$cert" .crt)
            key="/etc/nginx/ssl/$domain.key"
            if [ -f "$key" ]; then
                echo "找到证书: $domain"
                echo "$domain" > /tmp/nginx_cert_domain
                echo "$cert" > /tmp/nginx_cert_file
                echo "$key" > /tmp/nginx_key_file
                return 0
            fi
        done
    fi
    
    # 检查acme.sh目录
    if [ -d /root/.acme.sh ]; then
        for domain in /root/.acme.sh/*; do
            [ -d "$domain" ] || continue
            domain=$(basename "$domain")
            cert="$domain/fullchain.cer"
            key="$domain/$domain.key"
            if [ -f "$cert" ] && [ -f "$key" ]; then
                echo "找到证书: $domain"
                echo "$domain" > /tmp/nginx_cert_domain
                echo "$cert" > /tmp/nginx_cert_file
                echo "$key" > /tmp/nginx_key_file
                return 0
            fi
        done
    fi
    
    echo "未找到证书"
    return 1
}

# 创建自签名证书
create_cert() {
    echo "创建自签名证书..."
    read -p "输入域名: " domain
    mkdir -p /etc/nginx/ssl
    openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
        -keyout "/etc/nginx/ssl/$domain.key" \
        -out "/etc/nginx/ssl/$domain.crt" \
        -subj "/C=US/ST=State/L=City/O=Organization/CN=$domain" 2>/dev/null
    echo "$domain" > /tmp/nginx_cert_domain
    echo "/etc/nginx/ssl/$domain.crt" > /tmp/nginx_cert_file
    echo "/etc/nginx/ssl/$domain.key" > /tmp/nginx_key_file
}

# 主配置
main() {
    echo "=========================="
    echo "Alpine Nginx 反代配置器"
    echo "=========================="
    echo ""
    
    # 检查root
    if [ "$(id -u)" != "0" ]; then
        echo "错误: 需要root权限"
        exit 1
    fi
    
    # 安装Nginx
    if ! command -v nginx >/dev/null; then
        install_nginx
    fi
    
    # 查找证书
    if ! find_cert; then
        read -p "是否创建自签名证书？(y/N): " choice
        case "$choice" in
            [yY]*) create_cert ;;
            *) 
                read -p "输入主域名: " domain
                echo "$domain" > /tmp/nginx_cert_domain
                ;;
        esac
    fi
    
    # 读取证书信息
    if [ -f /tmp/nginx_cert_domain ]; then
        DOMAIN=$(cat /tmp/nginx_cert_domain)
        if [ -f /tmp/nginx_cert_file ] && [ -f /tmp/nginx_key_file ]; then
            CERT=$(cat /tmp/nginx_cert_file)
            KEY=$(cat /tmp/nginx_key_file)
            HAS_SSL=true
        else
            HAS_SSL=false
        fi
    else
        read -p "输入主域名: " DOMAIN
        HAS_SSL=false
    fi
    
    # 输入配置
    echo ""
    read -p "输入子域名 (如: nz): " SUBDOMAIN
    read -p "输入端口 (如: 52774): " PORT
    
    FULL_DOMAIN="${SUBDOMAIN}.${DOMAIN}"
    CONFIG_NAME="${SUBDOMAIN}_${PORT}"
    
    echo ""
    echo "配置摘要:"
    echo "  域名: $FULL_DOMAIN"
    echo "  端口: $PORT"
    echo "  上游: http://localhost:$PORT"
    if $HAS_SSL; then
        echo "  证书: 已配置"
    else
        echo "  证书: 未配置"
    fi
    
    read -p "确认创建？(y/N): " confirm
    case "$confirm" in
        [yY]*) ;;
        *) echo "已取消"; exit 0;;
    esac
    
    # 生成Nginx配置
    CONFIG_FILE="/etc/nginx/sites-available/$CONFIG_NAME.conf"
    
    echo "生成配置..."
    
    # 构建listen指令
    if $HAS_SSL; then
        LISTEN="listen $PORT ssl;"
        if [ "$PORT" = "443" ]; then
            LISTEN="$LISTEN\n    listen $PORT ssl http2;"
        fi
        
        # IPv6支持
        if ip -6 addr show 2>/dev/null | grep -q inet6; then
            LISTEN="$LISTEN\n    listen [::]:$PORT ssl;"
            if [ "$PORT" = "443" ]; then
                LISTEN="$LISTEN\n    listen [::]:$PORT ssl http2;"
            fi
        fi
        
        SSL_CONFIG="
    ssl_certificate $CERT;
    ssl_certificate_key $KEY;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;"
    else
        LISTEN="listen $PORT;"
        if ip -6 addr show 2>/dev/null | grep -q inet6; then
            LISTEN="$LISTEN\n    listen [::]:$PORT;"
        fi
        SSL_CONFIG=""
    fi
    
    # 写入配置
    cat > "$CONFIG_FILE" << EOF
server {
    $LISTEN
    server_name $FULL_DOMAIN;$SSL_CONFIG
    
    location / {
        proxy_pass http://localhost:$PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
        
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
EOF
    
    # 启用配置
    mkdir -p /etc/nginx/sites-enabled
    ln -sf "$CONFIG_FILE" "/etc/nginx/sites-enabled/"
    
    # 创建主配置
    if [ ! -f /etc/nginx/nginx.conf ] || ! grep -q "sites-enabled" /etc/nginx/nginx.conf; then
        cat > /etc/nginx/nginx.conf << 'EOF'
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
    
    access_log /var/log/nginx/access.log;
    
    sendfile on;
    tcp_nopush on;
    tcp_nodelay on;
    keepalive_timeout 65;
    
    include /etc/nginx/sites-enabled/*;
    
    server {
        listen 80 default_server;
        listen [::]:80 default_server;
        server_name _;
        return 444;
    }
}
EOF
    fi
    
    # 测试并重启
    echo "测试配置..."
    if nginx -t; then
        echo "配置测试通过"
        if pgrep nginx >/dev/null; then
            nginx -s reload && echo "Nginx已重载" || {
                echo "重载失败，尝试重启..."
                pkill nginx 2>/dev/null
                sleep 1
                nginx && echo "Nginx已启动"
            }
        else
            nginx && echo "Nginx已启动"
        fi
    else
        echo "配置测试失败"
        nginx -t
        exit 1
    fi
    
    # 显示结果
    echo ""
    echo "✅ 配置完成！"
    echo "域名: $FULL_DOMAIN"
    echo "端口: $PORT"
    if $HAS_SSL; then
        if [ "$PORT" = "443" ]; then
            echo "访问: https://$FULL_DOMAIN"
        else
            echo "访问: https://$FULL_DOMAIN:$PORT"
        fi
    else
        if [ "$PORT" = "80" ]; then
            echo "访问: http://$FULL_DOMAIN"
        else
            echo "访问: http://$FULL_DOMAIN:$PORT"
        fi
    fi
    echo "配置文件: $CONFIG_FILE"
    
    # 清理临时文件
    rm -f /tmp/nginx_cert_domain /tmp/nginx_cert_file /tmp/nginx_key_file
}

# 运行
main "$@"