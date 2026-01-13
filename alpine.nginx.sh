#!/bin/sh

# ============================================
# Alpine Nginx 智能反代助手 (兼容sh/ash)
# 自动检测证书，极简配置
# ============================================

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() { echo "✅ $1"; }
info() { echo "ℹ️  $1"; }
warn() { echo "⚠️  $1"; }
error() { echo "❌ $1"; }

# 检查root权限
if [ "$(id -u)" != "0" ]; then
    echo "❌ 需要root权限"
    exit 1
fi

# 安装Nginx
install_nginx() {
    if ! command -v nginx >/dev/null 2>&1; then
        info "安装Nginx..."
        apk add --no-cache nginx openssl
    fi
    
    # 创建必要目录
    mkdir -p /etc/nginx/sites-available /etc/nginx/sites-enabled /etc/nginx/ssl /var/log/nginx
}

# 扫描证书
scan_certificates() {
    info "扫描系统证书..."
    
    # 检查常见的证书路径
    CERT_PATHS="
        /etc/nginx/ssl
        /etc/nginx/ssl/certs
        /etc/letsencrypt/live
        /root/.acme.sh
        /ssl
        /etc/ssl
    "
    
    for path in $CERT_PATHS; do
        if [ -d "$path" ]; then
            # 查找.crt文件
            find "$path" -name "*.crt" 2>/dev/null | while read cert; do
                # 尝试找到对应的key文件
                domain=$(basename "$cert" .crt)
                
                # 检查可能的key文件位置
                key_candidates="
                    $(dirname "$cert")/$domain.key
                    $(dirname "$cert")/privkey.pem
                    /etc/nginx/ssl/private/$domain/key.pem
                    /etc/nginx/ssl/$domain.key
                "
                
                for key in $key_candidates; do
                    if [ -f "$key" ]; then
                        echo "$domain:$cert:$key"
                        return 0
                    fi
                done
            done
            
            # 查找.pem文件 (fullchain)
            find "$path" -name "fullchain.pem" 2>/dev/null | while read cert; do
                # 尝试找到对应的key文件
                key=$(echo "$cert" | sed 's/fullchain\.pem/privkey.pem/')
                if [ -f "$key" ]; then
                    domain=$(basename $(dirname "$cert"))
                    echo "$domain:$cert:$key"
                    return 0
                fi
            done
        fi
    done
    
    return 1
}

# 选择证书
select_certificate() {
    info "正在扫描证书..."
    
    # 获取第一个找到的证书
    cert_info=$(scan_certificates | head -1)
    
    if [ -z "$cert_info" ]; then
        warn "未找到SSL证书"
        return 1
    fi
    
    # 解析证书信息
    CERT_DOMAIN=$(echo "$cert_info" | cut -d: -f1)
    CERT_FILE=$(echo "$cert_info" | cut -d: -f2)
    KEY_FILE=$(echo "$cert_info" | cut -d: -f3)
    
    if [ ! -f "$CERT_FILE" ] || [ ! -f "$KEY_FILE" ]; then
        warn "证书文件不完整"
        return 1
    fi
    
    log "找到证书: $CERT_DOMAIN"
    info "证书文件: $CERT_FILE"
    info "密钥文件: $KEY_FILE"
    
    return 0
}

# 创建默认配置
create_default_nginx_conf() {
    if [ ! -f /etc/nginx/nginx.conf ]; then
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
    
    log_format main '$remote_addr - $remote_user [$time_local] "$request" '
                    '$status $body_bytes_sent "$http_referer" '
                    '"$http_user_agent" "$http_x_forwarded_for"';
    
    access_log /var/log/nginx/access.log main;
    
    sendfile on;
    tcp_nopush on;
    tcp_nodelay on;
    keepalive_timeout 65;
    
    # 包含站点配置
    include /etc/nginx/sites-enabled/*;
    
    # 默认服务器
    server {
        listen 80 default_server;
        listen [::]:80 default_server;
        return 444;
    }
}
EOF
    fi
}

# 配置反向代理
configure_proxy() {
    echo ""
    echo "🔧 配置反向代理"
    echo "================"
    
    # 输入子域名
    while true; do
        printf "请输入子域名 (如: nz, app): "
        read SUBDOMAIN
        if [ -n "$SUBDOMAIN" ]; then
            break
        fi
        echo "❌ 子域名不能为空"
    done
    
    # 构建完整域名
    FULL_DOMAIN="${SUBDOMAIN}.${CERT_DOMAIN}"
    info "完整域名: $FULL_DOMAIN"
    
    # 输入端口
    while true; do
        printf "请输入本地端口 (如: 52774): "
        read PORT
        if echo "$PORT" | grep -q '^[0-9]\+$' && [ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ]; then
            break
        fi
        echo "❌ 请输入有效的端口号 (1-65535)"
    done
    
    # 上游地址
    UPSTREAM="http://127.0.0.1:$PORT"
    
    # 配置名
    CONFIG_NAME="${SUBDOMAIN}_${PORT}"
    
    echo ""
    echo "📋 配置摘要"
    echo "──────────"
    echo "• 域名: $FULL_DOMAIN"
    echo "• 端口: $PORT"
    echo "• 上游: $UPSTREAM"
    echo "• 证书: $CERT_DOMAIN"
    echo ""
    
    printf "确认创建配置？(y/N): "
    read confirm
    case "$confirm" in
        [yY]*) ;;
        *) echo "已取消"; exit 0;;
    esac
}

# 创建Nginx配置
create_nginx_config() {
    local config_file="/etc/nginx/sites-available/${CONFIG_NAME}.conf"
    
    info "生成Nginx配置..."
    
    # 构建listen指令
    if [ -n "$CERT_FILE" ] && [ -n "$KEY_FILE" ]; then
        LISTEN_DIRECTIVE="listen $PORT ssl;"
        if [ "$PORT" = "443" ]; then
            LISTEN_DIRECTIVE="$LISTEN_DIRECTIVE\n    listen $PORT ssl http2;"
        fi
        
        # 检查IPv6
        if ip -6 addr show 2>/dev/null | grep -q inet6; then
            LISTEN_DIRECTIVE="$LISTEN_DIRECTIVE\n    listen [::]:$PORT ssl;"
            [ "$PORT" = "443" ] && LISTEN_DIRECTIVE="$LISTEN_DIRECTIVE\n    listen [::]:$PORT ssl http2;"
        fi
        
        SSL_CONFIG="
    # SSL配置
    ssl_certificate $CERT_FILE;
    ssl_certificate_key $KEY_FILE;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-RSA-AES256-GCM-SHA512:DHE-RSA-AES256-GCM-SHA512;
    ssl_prefer_server_ciphers off;"
    else
        LISTEN_DIRECTIVE="listen $PORT;"
        if ip -6 addr show 2>/dev/null | grep -q inet6; then
            LISTEN_DIRECTIVE="$LISTEN_DIRECTIVE\n    listen [::]:$PORT;"
        fi
        SSL_CONFIG=""
    fi
    
    # 创建配置文件
    cat > "$config_file" << EOF
# 反向代理配置
# 域名: $FULL_DOMAIN
# 上游: $UPSTREAM
# 生成时间: $(date)

server {
    $LISTEN_DIRECTIVE
    
    server_name $FULL_DOMAIN;$SSL_CONFIG
    
    # 安全头
    add_header X-Frame-Options SAMEORIGIN;
    add_header X-Content-Type-Options nosniff;
    
    # 日志
    access_log /var/log/nginx/${CONFIG_NAME}.access.log;
    error_log /var/log/nginx/${CONFIG_NAME}.error.log;
    
    # 文件大小限制
    client_max_body_size 100M;
    
    # 代理配置
    location / {
        proxy_pass $UPSTREAM;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        
        # 超时设置
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
        
        # WebSocket支持
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
    }
    
    # 健康检查
    location /nginx-health {
        access_log off;
        return 200 'healthy\n';
        add_header Content-Type text/plain;
    }
}
EOF
    
    # 启用配置
    ln -sf "$config_file" "/etc/nginx/sites-enabled/"
    
    log "配置文件创建成功: $config_file"
}

# 重启Nginx
restart_nginx() {
    info "测试Nginx配置..."
    
    if nginx -t 2>/dev/null; then
        log "配置测试通过"
        
        # 检查nginx是否在运行
        if pgrep nginx >/dev/null 2>&1; then
            nginx -s reload 2>/dev/null && log "Nginx已重载" || {
                warn "重载失败，尝试重启..."
                pkill nginx 2>/dev/null
                sleep 1
                nginx && log "Nginx已重启" || error "Nginx启动失败"
            }
        else
            nginx && log "Nginx已启动" || error "Nginx启动失败"
        fi
    else
        error "配置测试失败"
        nginx -t
        return 1
    fi
}

# 显示结果
show_result() {
    echo ""
    echo "🎉 配置完成！"
    echo "=============="
    echo ""
    echo "📊 配置信息:"
    echo "• 域名: $FULL_DOMAIN"
    echo "• 端口: $PORT"
    echo "• 上游: $UPSTREAM"
    
    if [ -n "$CERT_FILE" ]; then
        if [ "$PORT" = "443" ]; then
            echo "• 访问地址: https://$FULL_DOMAIN"
        else
            echo "• 访问地址: https://$FULL_DOMAIN:$PORT"
        fi
    else
        if [ "$PORT" = "80" ]; then
            echo "• 访问地址: http://$FULL_DOMAIN"
        else
            echo "• 访问地址: http://$FULL_DOMAIN:$PORT"
        fi
    fi
    
    echo ""
    echo "📁 文件位置:"
    echo "• 配置文件: /etc/nginx/sites-available/${CONFIG_NAME}.conf"
    echo "• 访问日志: /var/log/nginx/${CONFIG_NAME}.access.log"
    echo "• 错误日志: /var/log/nginx/${CONFIG_NAME}.error.log"
    
    if [ -n "$CERT_FILE" ]; then
        echo ""
        echo "🔐 证书信息:"
        echo "• 证书文件: $CERT_FILE"
        echo "• 密钥文件: $KEY_FILE"
    fi
    
    echo ""
}

# 主程序
main() {
    echo ""
    echo "🚀 Alpine Nginx 反代助手"
    echo "========================"
    echo ""
    
    # 安装Nginx
    install_nginx
    
    # 创建默认配置
    create_default_nginx_conf
    
    # 选择证书
    if select_certificate; then
        # 配置代理
        configure_proxy
        
        # 创建配置
        create_nginx_config
        
        # 重启Nginx
        if restart_nginx; then
            # 显示结果
            show_result
        fi
    else
        warn "是否创建自签名证书？"
        printf "创建自签名证书？(y/N): "
        read create_cert
        case "$create_cert" in
            [yY]*)
                printf "输入域名: "
                read CERT_DOMAIN
                if [ -z "$CERT_DOMAIN" ]; then
                    error "域名不能为空"
                    exit 1
                fi
                
                mkdir -p "/etc/nginx/ssl/$CERT_DOMAIN"
                
                if openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
                    -keyout "/etc/nginx/ssl/$CERT_DOMAIN.key" \
                    -out "/etc/nginx/ssl/$CERT_DOMAIN.crt" \
                    -subj "/C=US/ST=State/L=City/O=Organization/CN=$CERT_DOMAIN" 2>/dev/null; then
                    
                    CERT_FILE="/etc/nginx/ssl/$CERT_DOMAIN.crt"
                    KEY_FILE="/etc/nginx/ssl/$CERT_DOMAIN.key"
                    
                    log "自签名证书已创建"
                    
                    # 配置代理
                    configure_proxy
                    
                    # 创建配置
                    create_nginx_config
                    
                    # 重启Nginx
                    if restart_nginx; then
                        show_result
                    fi
                else
                    error "证书创建失败"
                fi
                ;;
            *)
                warn "将使用HTTP协议"
                printf "输入主域名: "
                read CERT_DOMAIN
                if [ -z "$CERT_DOMAIN" ]; then
                    error "域名不能为空"
                    exit 1
                fi
                
                # 配置代理
                configure_proxy
                
                # 创建配置
                create_nginx_config
                
                # 重启Nginx
                if restart_nginx; then
                    show_result
                fi
                ;;
        esac
    fi
}

# 运行主程序
main