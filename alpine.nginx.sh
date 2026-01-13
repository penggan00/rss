#!/bin/sh

# ============================================
# Alpine Nginx 智能反代助手
# 自动检测证书路径，隐私友好，一键配置
# ============================================

set -e

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
NC='\033[0m'

log() { echo -e "${GREEN}[+]${NC} $1"; }
info() { echo -e "${BLUE}[i]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[x]${NC} $1"; }

# 检查root
[ "$(id -u)" = "0" ] || { error "需要root权限"; exit 1; }

# 安装Nginx
install_nginx() {
    if ! command -v nginx >/dev/null; then
        log "安装Nginx..."
        apk add --no-cache nginx openssl
    fi
    
    # 创建目录结构
    mkdir -p /etc/nginx/sites-{available,enabled} \
             /etc/nginx/ssl \
             /var/log/nginx/proxy
}

# 自动发现证书
auto_detect_cert() {
    info "搜索证书..."
    
    # 方法1: 查找nginx ssl目录
    if ls /etc/nginx/ssl/*.crt 2>/dev/null; then
        for cert in /etc/nginx/ssl/*.crt; do
            local domain=$(basename "$cert" .crt)
            local key="/etc/nginx/ssl/$domain.key"
            local cert2="/etc/nginx/ssl/certs/$domain/fullchain.pem"
            local key2="/etc/nginx/ssl/private/$domain/key.pem"
            
            if [ -f "$key" ]; then
                log "找到证书: $cert"
                echo "$domain:$cert:$key"
                return 0
            elif [ -f "$cert2" ] && [ -f "$key2" ]; then
                log "找到证书: $cert2"
                echo "$domain:$cert2:$key2"
                return 0
            fi
        done
    fi
    
    # 方法2: 查找acme.sh证书
    if [ -d /root/.acme.sh ]; then
        for domain_dir in /root/.acme.sh/*/; do
            if [ -d "$domain_dir" ]; then
                local domain=$(basename "$domain_dir")
                local cert="$domain_dir/fullchain.cer"
                local key="$domain_dir/$domain.key"
                
                if [ -f "$cert" ] && [ -f "$key" ]; then
                    log "找到acme.sh证书: $domain"
                    echo "$domain:$cert:$key"
                    return 0
                fi
            fi
        done
    fi
    
    # 方法3: 查找letsencrypt证书
    if [ -d /etc/letsencrypt/live ]; then
        for domain_dir in /etc/letsencrypt/live/*/; do
            if [ -d "$domain_dir" ]; then
                local domain=$(basename "$domain_dir")
                local cert="$domain_dir/fullchain.pem"
                local key="$domain_dir/privkey.pem"
                
                if [ -f "$cert" ] && [ -f "$key" ]; then
                    log "找到Let's Encrypt证书: $domain"
                    echo "$domain:$cert:$key"
                    return 0
                fi
            fi
        done
    fi
    
    warn "未找到SSL证书"
    return 1
}

# 选择证书
select_certificate() {
    local certs=()
    
    info "扫描证书..."
    
    # 收集所有证书
    {
        # nginx ssl目录
        for cert in /etc/nginx/ssl/*.crt 2>/dev/null; do
            [ -f "$cert" ] && certs+=("$cert")
        done
        
        # acme.sh证书
        for cert in /root/.acme.sh/*/fullchain.cer 2>/dev/null; do
            [ -f "$cert" ] && certs+=("$cert")
        done
        
        # letsencrypt证书
        for cert in /etc/letsencrypt/live/*/fullchain.pem 2>/dev/null; do
            [ -f "$cert" ] && certs+=("$cert")
        done
        
        # nginx ssl/certs目录
        for cert in /etc/nginx/ssl/certs/*/fullchain.pem 2>/dev/null; do
            [ -f "$cert" ] && certs+=("$cert")
        done
    }
    
    if [ ${#certs[@]} -eq 0 ]; then
        warn "未找到证书"
        return 1
    fi
    
    echo ""
    echo -e "${PURPLE}=== 发现以下证书 ===${NC}"
    
    for i in "${!certs[@]}"; do
        local cert="${certs[$i]}"
        local domain=""
        local key=""
        
        # 提取域名
        if [[ "$cert" == *".acme.sh/"* ]]; then
            domain=$(echo "$cert" | grep -o '\.acme.sh/[^/]*' | cut -d'/' -f2)
            key="/root/.acme.sh/$domain/$domain.key"
        elif [[ "$cert" == *"letsencrypt/live/"* ]]; then
            domain=$(echo "$cert" | grep -o 'live/[^/]*' | cut -d'/' -f2)
            key="/etc/letsencrypt/live/$domain/privkey.pem"
        elif [[ "$cert" == *"nginx/ssl/certs/"* ]]; then
            domain=$(echo "$cert" | grep -o 'certs/[^/]*' | cut -d'/' -f2)
            key="/etc/nginx/ssl/private/$domain/key.pem"
        elif [[ "$cert" == *"nginx/ssl/"* ]]; then
            domain=$(basename "$cert" .crt)
            key="/etc/nginx/ssl/$domain.key"
        fi
        
        # 检查密钥是否存在
        if [ -f "$key" ]; then
            echo "$(($i+1)). 域名: ${GREEN}$domain${NC}"
            echo "   证书: $cert"
            echo "   密钥: $key"
            echo ""
        fi
    done
    
    # 如果有多个证书，让用户选择
    if [ ${#certs[@]} -gt 1 ]; then
        read -p "选择证书编号 (1-${#certs[@]}): " choice
        if [[ "$choice" =~ ^[0-9]+$ ]] && [ "$choice" -ge 1 ] && [ "$choice" -le ${#certs[@]} ]; then
            local cert="${certs[$((choice-1))]}"
            # 提取选中的证书信息
            if [[ "$cert" == *".acme.sh/"* ]]; then
                CERT_DOMAIN=$(echo "$cert" | grep -o '\.acme.sh/[^/]*' | cut -d'/' -f2)
                CERT_FILE="$cert"
                KEY_FILE="/root/.acme.sh/$CERT_DOMAIN/$CERT_DOMAIN.key"
            elif [[ "$cert" == *"letsencrypt/live/"* ]]; then
                CERT_DOMAIN=$(echo "$cert" | grep -o 'live/[^/]*' | cut -d'/' -f2)
                CERT_FILE="$cert"
                KEY_FILE="/etc/letsencrypt/live/$CERT_DOMAIN/privkey.pem"
            elif [[ "$cert" == *"nginx/ssl/certs/"* ]]; then
                CERT_DOMAIN=$(echo "$cert" | grep -o 'certs/[^/]*' | cut -d'/' -f2)
                CERT_FILE="$cert"
                KEY_FILE="/etc/nginx/ssl/private/$CERT_DOMAIN/key.pem"
            else
                CERT_DOMAIN=$(basename "$cert" .crt)
                CERT_FILE="$cert"
                KEY_FILE="/etc/nginx/ssl/$CERT_DOMAIN.key"
            fi
        else
            error "无效选择"
            return 1
        fi
    else
        # 只有一个证书
        local cert="${certs[0]}"
        if [[ "$cert" == *".acme.sh/"* ]]; then
            CERT_DOMAIN=$(echo "$cert" | grep -o '\.acme.sh/[^/]*' | cut -d'/' -f2)
            CERT_FILE="$cert"
            KEY_FILE="/root/.acme.sh/$CERT_DOMAIN/$CERT_DOMAIN.key"
        elif [[ "$cert" == *"letsencrypt/live/"* ]]; then
            CERT_DOMAIN=$(echo "$cert" | grep -o 'live/[^/]*' | cut -d'/' -f2)
            CERT_FILE="$cert"
            KEY_FILE="/etc/letsencrypt/live/$CERT_DOMAIN/privkey.pem"
        elif [[ "$cert" == *"nginx/ssl/certs/"* ]]; then
            CERT_DOMAIN=$(echo "$cert" | grep -o 'certs/[^/]*' | cut -d'/' -f2)
            CERT_FILE="$cert"
            KEY_FILE="/etc/nginx/ssl/private/$CERT_DOMAIN/key.pem"
        else
            CERT_DOMAIN=$(basename "$cert" .crt)
            CERT_FILE="$cert"
            KEY_FILE="/etc/nginx/ssl/$CERT_DOMAIN.key"
        fi
    fi
    
    [ -f "$CERT_FILE" ] && [ -f "$KEY_FILE" ] || {
        error "证书文件不存在"
        return 1
    }
    
    log "使用证书: $CERT_DOMAIN"
    return 0
}

# 配置代理
configure_proxy() {
    echo ""
    echo -e "${PURPLE}=== 配置反向代理 ===${NC}"
    
    # 输入子域名
    while true; do
        read -p "输入子域名 (如: nz, app, api): " SUBDOMAIN
        [ -n "$SUBDOMAIN" ] && break
        warn "子域名不能为空"
    done
    
    # 构建完整域名
    FULL_DOMAIN="$SUBDOMAIN.$CERT_DOMAIN"
    log "完整域名: $FULL_DOMAIN"
    
    # 输入端口
    while true; do
        read -p "本地服务端口 (如: 52774): " PORT
        if [[ "$PORT" =~ ^[0-9]+$ ]] && [ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ]; then
            break
        fi
        warn "端口必须是1-65535之间的数字"
    done
    
    # 上游地址
    UPSTREAM="http://localhost:$PORT"
    
    # 配置名
    CONFIG_NAME="${SUBDOMAIN}_${PORT}"
    
    echo ""
    echo -e "${YELLOW}配置摘要:${NC}"
    echo "  域名: $FULL_DOMAIN"
    echo "  端口: $PORT"
    echo "  上游: $UPSTREAM"
    echo "  证书: $CERT_DOMAIN"
    echo ""
    
    read -p "确认创建配置？(y/N): " confirm
    [[ "$confirm" =~ ^[Yy]$ ]] || {
        info "已取消"
        exit 0
    }
}

# 创建nginx配置
create_nginx_config() {
    local config_file="/etc/nginx/sites-available/$CONFIG_NAME.conf"
    
    info "生成Nginx配置..."
    
    # 判断是否启用SSL
    local ssl_config=""
    if [ -n "$CERT_FILE" ] && [ -n "$KEY_FILE" ]; then
        ssl_config="
    # SSL配置
    ssl_certificate $CERT_FILE;
    ssl_certificate_key $KEY_FILE;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-RSA-AES256-GCM-SHA512:DHE-RSA-AES256-GCM-SHA512;
    ssl_prefer_server_ciphers off;"
    fi
    
    # 构建listen指令
    local listen_ports=""
    if [ -n "$ssl_config" ]; then
        listen_ports="    listen $PORT ssl;"
        if [ "$PORT" = "443" ]; then
            listen_ports="$listen_ports\n    listen $PORT ssl http2;"
        fi
    else
        listen_ports="    listen $PORT;"
    fi
    
    # 检测IPv6
    if ip -6 addr show | grep -q inet6; then
        if [ -n "$ssl_config" ]; then
            listen_ports="$listen_ports\n    listen [::]:$PORT ssl;"
            [ "$PORT" = "443" ] && listen_ports="$listen_ports\n    listen [::]:$PORT ssl http2;"
        else
            listen_ports="$listen_ports\n    listen [::]:$PORT;"
        fi
    fi
    
    # 写入配置文件
    cat > "$config_file" << EOF
# 反向代理配置: $FULL_DOMAIN -> $UPSTREAM
# 生成时间: $(date)

server {
$listen_ports
    
    server_name $FULL_DOMAIN;
$ssl_config
    # 安全头
    add_header X-Frame-Options SAMEORIGIN;
    add_header X-Content-Type-Options nosniff;
    add_header X-XSS-Protection "1; mode=block";
    
    # 日志
    access_log /var/log/nginx/proxy/$CONFIG_NAME.access.log;
    error_log /var/log/nginx/proxy/$CONFIG_NAME.error.log;
    
    # 文件大小限制
    client_max_body_size 100M;
    
    # 代理设置
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
        return 200 "healthy\\n";
        add_header Content-Type text/plain;
    }
}
EOF
    
    # 启用配置
    ln -sf "$config_file" "/etc/nginx/sites-enabled/"
    
    log "配置文件: $config_file"
}

# 配置主nginx
setup_main_nginx() {
    if [ ! -f /etc/nginx/nginx.conf ] || ! grep -q "sites-enabled" /etc/nginx/nginx.conf; then
        info "配置主nginx..."
        
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
    types_hash_max_size 2048;
    client_max_body_size 100M;
    
    # 包含站点配置
    include /etc/nginx/sites-enabled/*;
    
    # 默认服务器 - 拒绝直接访问
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
        
        ssl_certificate /etc/nginx/ssl/default.crt;
        ssl_certificate_key /etc/nginx/ssl/default.key;
        
        return 444;
    }
}
EOF
        
        # 创建默认证书
        if [ ! -f /etc/nginx/ssl/default.crt ]; then
            openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
                -keyout /etc/nginx/ssl/default.key \
                -out /etc/nginx/ssl/default.crt \
                -subj "/CN=invalid" 2>/dev/null
        fi
    fi
}

# 测试并重启nginx
restart_nginx() {
    info "测试配置..."
    
    if nginx -t 2>/dev/null; then
        log "配置测试通过"
        
        # 启动或重载nginx
        if pgrep nginx >/dev/null; then
            nginx -s reload 2>/dev/null && log "Nginx已重载" || {
                # 如果重载失败，尝试重启
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
    echo -e "${PURPLE}═══════════════════════════════${NC}"
    echo -e "${PURPLE}       配置完成！              ${NC}"
    echo -e "${PURPLE}═══════════════════════════════${NC}"
    echo ""
    echo -e "${GREEN}✓ 域名:${NC} $FULL_DOMAIN"
    echo -e "${GREEN}✓ 端口:${NC} $PORT"
    echo -e "${GREEN}✓ 上游:${NC} $UPSTREAM"
    
    if [ -n "$CERT_FILE" ]; then
        if [ "$PORT" = "443" ]; then
            echo -e "${GREEN}✓ 访问地址:${NC} https://$FULL_DOMAIN"
        else
            echo -e "${GREEN}✓ 访问地址:${NC} https://$FULL_DOMAIN:$PORT"
        fi
    else
        if [ "$PORT" = "80" ]; then
            echo -e "${GREEN}✓ 访问地址:${NC} http://$FULL_DOMAIN"
        else
            echo -e "${GREEN}✓ 访问地址:${NC} http://$FULL_DOMAIN:$PORT"
        fi
    fi
    
    echo ""
    echo -e "${YELLOW}配置文件:${NC}"
    echo "  /etc/nginx/sites-available/$CONFIG_NAME.conf"
    echo ""
    echo -e "${YELLOW}日志文件:${NC}"
    echo "  /var/log/nginx/proxy/$CONFIG_NAME.access.log"
    echo "  /var/log/nginx/proxy/$CONFIG_NAME.error.log"
    echo ""
}

# 主函数
main() {
    clear
    echo -e "${PURPLE}═══════════════════════════════${NC}"
    echo -e "${PURPLE}    Alpine Nginx 反代助手     ${NC}"
    echo -e "${PURPLE}═══════════════════════════════${NC}"
    echo ""
    
    # 安装nginx
    install_nginx
    
    # 选择证书
    if select_certificate; then
        # 配置代理
        configure_proxy
        
        # 创建配置
        create_nginx_config
        
        # 设置主nginx
        setup_main_nginx
        
        # 重启nginx
        restart_nginx
        
        # 显示结果
        show_result
    else
        warn "未找到证书，是否创建自签名证书？"
        read -p "创建自签名证书？(y/N): " create_cert
        if [[ "$create_cert" =~ ^[Yy]$ ]]; then
            read -p "输入域名: " CERT_DOMAIN
            mkdir -p /etc/nginx/ssl/$CERT_DOMAIN
            openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
                -keyout "/etc/nginx/ssl/$CERT_DOMAIN.key" \
                -out "/etc/nginx/ssl/$CERT_DOMAIN.crt" \
                -subj "/C=US/ST=State/L=City/O=Organization/CN=$CERT_DOMAIN" 2>/dev/null
            
            CERT_FILE="/etc/nginx/ssl/$CERT_DOMAIN.crt"
            KEY_FILE="/etc/nginx/ssl/$CERT_DOMAIN.key"
            
            if [ -f "$CERT_FILE" ] && [ -f "$KEY_FILE" ]; then
                log "自签名证书已创建"
                # 重新配置
                configure_proxy
                create_nginx_config
                setup_main_nginx
                restart_nginx
                show_result
            else
                error "证书创建失败"
            fi
        else
            # 不使用SSL
            CERT_FILE=""
            KEY_FILE=""
            read -p "输入主域名: " CERT_DOMAIN
            configure_proxy
            create_nginx_config
            setup_main_nginx
            restart_nginx
            show_result
        fi
    fi
}

# 运行
main "$@"