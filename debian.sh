#!/bin/bash

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# 系统检测
detect_os() {
    if [ -f /etc/alpine-release ]; then
        OS="alpine"
    elif [ -f /etc/debian_version ]; then
        OS="debian"
    elif [ -f /etc/centos-release ] || [ -f /etc/redhat-release ]; then
        OS="centos"
    else
        OS="unknown"
    fi
    echo "$OS"
}

OS=$(detect_os)

# 日志函数
log() {
    local level=$1
    local message=$2
    local color=$NC
    
    case $level in
        "INFO") color=$GREEN ;;
        "WARN") color=$YELLOW ;;
        "ERROR") color=$RED ;;
        "DEBUG") color=$BLUE ;;
    esac
    
    echo -e "${color}[$(date '+%Y-%m-%d %H:%M:%S')] [$level] $message${NC}"
}

# 安装依赖（兼容不同系统）
install_dependencies() {
    local deps=("$@")
    
    case $OS in
        "alpine")
            apk add --no-cache "${deps[@]}" 2>/dev/null
            ;;
        "debian"|"ubuntu")
            apt-get update
            DEBIAN_FRONTEND=noninteractive apt-get install -y "${deps[@]}" 2>/dev/null
            ;;
        "centos"|"rhel"|"fedora")
            yum install -y "${deps[@]}" 2>/dev/null
            ;;
    esac
}

# 检查依赖
check_dependencies() {
    local deps=("nginx" "openssl")
    
    if [ "$OS" = "debian" ] || [ "$OS" = "ubuntu" ]; then
        deps+=("tree" "procps")  # Debian需要procps来使用pgrep
    else
        deps+=("tree")
    fi
    
    local missing=()
    
    for dep in "${deps[@]}"; do
        if ! command -v $dep &> /dev/null; then
            missing+=("$dep")
        fi
    done
    
    if [ ${#missing[@]} -gt 0 ]; then
        log "WARN" "缺少以下依赖: ${missing[*]}"
        read -p "是否安装缺失的依赖？(y/n): " choice
        if [[ $choice =~ ^[Yy]$ ]]; then
            install_dependencies "${missing[@]}"
            if [ $? -ne 0 ]; then
                log "ERROR" "依赖安装失败"
                exit 1
            fi
        fi
    fi
}

# 初始化目录结构
init_directories() {
    log "INFO" "初始化目录结构..."
    
    # 根据系统确定Nginx用户
    local nginx_user="nginx"
    local nginx_group="nginx"
    
    if [ "$OS" = "debian" ] || [ "$OS" = "ubuntu" ]; then
        nginx_user="www-data"
        nginx_group="www-data"
    fi
    
    # 创建必要的目录
    local dirs=(
        "/etc/nginx/ssl/certs"
        "/etc/nginx/ssl/private"
        "/etc/nginx/sites-available"
        "/etc/nginx/sites-enabled"
        "/var/log/nginx/ssl"
        "/var/www/html"
    )
    
    for dir in "${dirs[@]}"; do
        if [ ! -d "$dir" ]; then
            mkdir -p "$dir"
            chmod 750 "$dir"
            log "DEBUG" "创建目录: $dir"
        fi
    done
    
    # 设置权限
    chown -R $nginx_user:$nginx_group /etc/nginx/ssl/private
    chmod 700 /etc/nginx/ssl/private
    chmod 644 /etc/nginx/ssl/certs/* 2>/dev/null
    
    # Debian特有：确保sites-enabled在nginx.conf中被包含
    if [ "$OS" = "debian" ] || [ "$OS" = "ubuntu" ]; then
        if [ -f /etc/nginx/nginx.conf ] && ! grep -q "sites-enabled" /etc/nginx/nginx.conf; then
            # 检查是否已经包含sites-enabled
            if grep -q "include /etc/nginx/conf.d/\*.conf" /etc/nginx/nginx.conf; then
                # 在conf.d行后添加sites-enabled
                sed -i '/include \/etc\/nginx\/conf.d\/\*.conf;/a\    include /etc/nginx/sites-enabled/*;' /etc/nginx/nginx.conf
            fi
        fi
    fi
    
    log "INFO" "目录结构初始化完成"
}

# 域名验证函数（简化版，兼容Alpine ash和Bash）
validate_domain() {
    local domain=$1
    
    # 空值检查
    if [ -z "$domain" ]; then
        echo -e "${RED}错误: 域名不能为空${NC}"
        return 1
    fi
    
    # 长度检查
    if [ ${#domain} -gt 255 ]; then
        echo -e "${RED}错误: 域名太长${NC}"
        return 1
    fi
    
    # 简单检查：至少有一个点号
    if [[ "$domain" != *.* ]]; then
        echo -e "${YELLOW}警告: 域名缺少点号，但将继续处理${NC}"
        return 0
    fi
    
    # 检查是否以点号开头或结尾
    if [[ "$domain" == .* ]] || [[ "$domain" == *. ]]; then
        echo -e "${RED}错误: 域名不能以点号开头或结尾${NC}"
        return 1
    fi
    
    # 检查连续点号
    if [[ "$domain" == *..* ]]; then
        echo -e "${RED}错误: 域名不能有连续点号${NC}"
        return 1
    fi
    
    echo -e "${GREEN}✅ 域名格式验证通过${NC}"
    return 0
}

# 查找证书
find_certificates() {
    local domain=$1
    local clean_domain=${domain#*//}
    clean_domain=${clean_domain%%/*}
    
    log "DEBUG" "查找证书，域名: [域名已隐藏]"
    
    # 特别处理特定域名的子域名（通用化版本）
    # 检查是否有父域名证书可用
    if [[ "$clean_domain" == *.* ]]; then  # 至少有一个点
        log "DEBUG" "检测到多级域名，尝试查找父域名证书"
        
        # 提取父域名（移除第一个子域名）
        local parent_domain="${clean_domain#*.}"
        
        # 检查父域名证书
        local parent_cert="/root/.acme.sh/${parent_domain}_ecc/fullchain.cer"
        local parent_key="/root/.acme.sh/${parent_domain}_ecc/${parent_domain}.key"
        
        if [ -f "$parent_cert" ] && [ -f "$parent_key" ]; then
            CERT_FILE="$parent_cert"
            KEY_FILE="$parent_key"
            log "INFO" "找到父域名证书"
            return 0
        fi
        
        # 检查不带_ecc的路径
        local parent_cert2="/root/.acme.sh/${parent_domain}/fullchain.cer"
        local parent_key2="/root/.acme.sh/${parent_domain}/${parent_domain}.key"
        
        if [ -f "$parent_cert2" ] && [ -f "$parent_key2" ]; then
            CERT_FILE="$parent_cert2"
            KEY_FILE="$parent_key2"
            log "INFO" "找到父域名证书（非ECC）"
            return 0
        fi
    fi
    
    # 可能的证书路径（包含.cer格式）
    local cert_paths=(
        "/etc/nginx/ssl/certs/${clean_domain}/fullchain.pem"
        "/etc/nginx/ssl/certs/${clean_domain}.crt"
        "/etc/nginx/ssl/${clean_domain}.crt"
        "/etc/ssl/certs/${clean_domain}/fullchain.pem"
        "/etc/letsencrypt/live/${clean_domain}/fullchain.pem"
        "/root/.acme.sh/${clean_domain}/fullchain.cer"
        "/root/.acme.sh/${clean_domain}_ecc/fullchain.cer"
        "/root/.acme.sh/${clean_domain}/${clean_domain}.cer"
        "/root/.acme.sh/${clean_domain}_ecc/${clean_domain}.cer"
    )
    
    local key_paths=(
        "/etc/nginx/ssl/private/${clean_domain}/key.pem"
        "/etc/nginx/ssl/private/${clean_domain}.key"
        "/etc/nginx/ssl/${clean_domain}.key"
        "/etc/ssl/private/${clean_domain}/key.pem"
        "/etc/letsencrypt/live/${clean_domain}/privkey.pem"
        "/root/.acme.sh/${clean_domain}/${clean_domain}.key"
        "/root/.acme.sh/${clean_domain}_ecc/${clean_domain}.key"
    )
    
    # 查找证书文件
    for cert in "${cert_paths[@]}"; do
        if [ -f "$cert" ]; then
            CERT_FILE="$cert"
            log "INFO" "找到证书文件"
            break
        fi
    done
    
    # 查找密钥文件
    for key in "${key_paths[@]}"; do
        if [ -f "$key" ]; then
            KEY_FILE="$key"
            log "INFO" "找到密钥文件"
            break
        fi
    done
    
    if [ -n "$CERT_FILE" ] && [ -n "$KEY_FILE" ]; then
        return 0
    else
        # 尝试通配符证书
        if [[ "$clean_domain" == *.* ]]; then
            local wildcard_domain="*.${clean_domain#*.}"
            local wildcard_cert_paths=(
                "/etc/nginx/ssl/certs/${wildcard_domain}/fullchain.pem"
                "/etc/nginx/ssl/${wildcard_domain}.crt"
                "/root/.acme.sh/${wildcard_domain}/fullchain.cer"
                "/root/.acme.sh/${wildcard_domain}_ecc/fullchain.cer"
            )
            
            local wildcard_key_paths=(
                "/etc/nginx/ssl/private/${wildcard_domain}/key.pem"
                "/etc/nginx/ssl/${wildcard_domain}.key"
                "/root/.acme.sh/${wildcard_domain}/${wildcard_domain}.key"
                "/root/.acme.sh/${wildcard_domain}_ecc/${wildcard_domain}.key"
            )
            
            for cert in "${wildcard_cert_paths[@]}"; do
                if [ -f "$cert" ]; then
                    CERT_FILE="$cert"
                    log "INFO" "找到通配符证书"
                    break
                fi
            done
            
            for key in "${wildcard_key_paths[@]}"; do
                if [ -f "$key" ]; then
                    KEY_FILE="$key"
                    log "INFO" "找到通配符密钥"
                    break
                fi
            done
            
            if [ -n "$CERT_FILE" ] && [ -n "$KEY_FILE" ]; then
                return 0
            fi
        fi
    fi
    
    return 1
}

# 生成自签名证书
generate_self_signed_cert() {
    local domain=$1
    local cert_dir="/etc/nginx/ssl/certs/${domain}"
    local key_dir="/etc/nginx/ssl/private/${domain}"
    
    mkdir -p "$cert_dir" "$key_dir"
    
    log "INFO" "为 $domain 生成自签名证书..."
    
    openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
        -keyout "${key_dir}/key.pem" \
        -out "${cert_dir}/fullchain.pem" \
        -subj "/C=CN/ST=Beijing/L=Beijing/O=Development/CN=${domain}" \
        2>/dev/null
    
    if [ $? -eq 0 ]; then
        CERT_FILE="${cert_dir}/fullchain.pem"
        KEY_FILE="${key_dir}/key.pem"
        
        # 根据系统确定Nginx用户
        local nginx_user="nginx"
        if [ "$OS" = "debian" ] || [ "$OS" = "ubuntu" ]; then
            nginx_user="www-data"
        fi
        
        # 设置权限
        chmod 644 "$CERT_FILE"
        chmod 600 "$KEY_FILE"
        chown $nginx_user:$nginx_user "$KEY_FILE"
        
        log "INFO" "自签名证书生成成功"
        return 0
    else
        log "ERROR" "自签名证书生成失败"
        return 1
    fi
}

# 创建反向代理配置
create_proxy_config() {
    log "INFO" "开始创建反向代理配置"
    
    # 获取用户输入
    while true; do
        echo -ne "${CYAN}请输入域名${NC} (例如: api.example.com): "
        read DOMAIN
        
        if [ -n "$DOMAIN" ]; then
            validate_domain "$DOMAIN" && break
        else
            echo -e "${RED}错误: 域名不能为空${NC}"
        fi
    done
    
    # 验证端口
    while true; do
        echo -ne "${CYAN}请输入后端服务端口${NC} (例如: 3000): "
        read BACKEND_PORT
        
        if [[ "$BACKEND_PORT" =~ ^[0-9]+$ ]] && [ "$BACKEND_PORT" -ge 1 ] && [ "$BACKEND_PORT" -le 65535 ]; then
            break
        else
            echo -e "${RED}错误: 端口号必须是1-65535之间的数字${NC}"
        fi
    done
    
    # 其他选项
    echo -ne "${CYAN}是否启用WebSocket支持？${NC} (y/n): "
    read -n 1 WS_CHOICE
    echo
    [[ $WS_CHOICE =~ ^[Yy]$ ]] && WEBSOCKET=true || WEBSOCKET=false
    
    echo -ne "${CYAN}是否强制HTTPS？${NC} (y/n): "
    read -n 1 HTTPS_CHOICE
    echo
    [[ $HTTPS_CHOICE =~ ^[Yy]$ ]] && FORCE_HTTPS=true || FORCE_HTTPS=false
    
    echo -ne "${CYAN}是否启用缓存？${NC} (y/n): "
    read -n 1 CACHE_CHOICE
    echo
    [[ $CACHE_CHOICE =~ ^[Yy]$ ]] && ENABLE_CACHE=true || ENABLE_CACHE=false
    
    # 查找证书
    log "INFO" "正在查找证书..."
    SSL_AVAILABLE=false
    
    if find_certificates "$DOMAIN"; then
        SSL_AVAILABLE=true
        log "INFO" "找到SSL证书"
    else
        log "WARN" "未找到SSL证书"
        echo -ne "${YELLOW}是否生成自签名证书？${NC} (y/n): "
        read -n 1 CERT_CHOICE
        echo
        
        if [[ $CERT_CHOICE =~ ^[Yy]$ ]]; then
            if generate_self_signed_cert "$DOMAIN"; then
                SSL_AVAILABLE=true
                log "INFO" "已生成自签名证书"
            fi
        fi
        
        if [ "$SSL_AVAILABLE" = false ] && [ "$FORCE_HTTPS" = true ]; then
            log "WARN" "选择了强制HTTPS但未找到证书，将使用HTTP模式"
            FORCE_HTTPS=false
        fi
    fi
    
    # 配置文件名
    CONFIG_FILE="/etc/nginx/sites-available/${DOMAIN}.conf"
    
    log "INFO" "生成配置文件: $CONFIG_FILE"
    
    # 生成配置
    cat > "$CONFIG_FILE" << EOF
# 反向代理配置: $DOMAIN -> 127.0.0.1:$BACKEND_PORT
# 生成时间: $(date)
# SSL: $( [ "$SSL_AVAILABLE" = true ] && echo "已启用" || echo "未启用" )
# WebSocket: $( [ "$WEBSOCKET" = true ] && echo "已启用" || echo "未启用" )

# HTTP服务器 - 用于重定向或直接服务
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;
    
    # 安全头部
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    
    # 访问日志
    access_log /var/log/nginx/${DOMAIN}_access.log;
    error_log /var/log/nginx/${DOMAIN}_error.log warn;
EOF

    # 如果有证书且强制HTTPS，添加重定向
    if [ "$SSL_AVAILABLE" = true ] && [ "$FORCE_HTTPS" = true ]; then
        cat >> "$CONFIG_FILE" << EOF
    
    # 强制HTTPS重定向
    return 301 https://\$server_name\$request_uri;
}
EOF
    else
        # HTTP直接代理
        cat >> "$CONFIG_FILE" << EOF
    
    # 代理设置
    location / {
        proxy_pass http://127.0.0.1:$BACKEND_PORT;
        proxy_http_version 1.1;
        
        # 基础代理头
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Forwarded-Host \$server_name;
        
        # 连接设置
        proxy_buffering off;
        proxy_buffer_size 4k;
        proxy_buffers 8 4k;
        proxy_read_timeout 60s;
        proxy_connect_timeout 60s;
        
        # 保持活动连接
        proxy_set_header Connection "";
        
        # 禁用代理缓冲
        proxy_request_buffering off;
    }
EOF
        
        # 如果启用缓存
        if [ "$ENABLE_CACHE" = true ]; then
            cat >> "$CONFIG_FILE" << EOF
    
    # 静态文件缓存
    location ~* \.(jpg|jpeg|png|gif|ico|css|js)$ {
        proxy_pass http://127.0.0.1:$BACKEND_PORT;
        proxy_cache proxy_cache;
        proxy_cache_valid 200 302 1h;
        proxy_cache_valid 404 1m;
        proxy_cache_use_stale error timeout updating http_500 http_502 http_503 http_504;
        add_header X-Cache-Status \$upstream_cache_status;
    }
EOF
        fi
        
        # 如果启用WebSocket，添加配置
        if [ "$WEBSOCKET" = true ]; then
            cat >> "$CONFIG_FILE" << EOF
    
    # WebSocket支持
    location /ws/ {
        proxy_pass http://127.0.0.1:$BACKEND_PORT;
        proxy_http_version 1.1;
        
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "Upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
    
    location ~ ^/(socket\.io|websocket)/ {
        proxy_pass http://127.0.0.1:$BACKEND_PORT;
        proxy_http_version 1.1;
        
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "Upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
EOF
        fi
        
        echo "}" >> "$CONFIG_FILE"
    fi
    
    # 如果有证书，添加HTTPS服务器配置
    if [ "$SSL_AVAILABLE" = true ]; then
        cat >> "$CONFIG_FILE" << EOF

# HTTPS服务器配置
server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name $DOMAIN;
    
    # SSL证书
    ssl_certificate $CERT_FILE;
    ssl_certificate_key $KEY_FILE;
    
    # SSL优化
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers 'ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384';
    ssl_prefer_server_ciphers off;
    
    # 安全头部
    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    
    # 访问日志
    access_log /var/log/nginx/ssl/${DOMAIN}_access.log;
    error_log /var/log/nginx/ssl/${DOMAIN}_error.log warn;
    
    # 代理设置
    location / {
        proxy_pass http://127.0.0.1:$BACKEND_PORT;
        proxy_http_version 1.1;
        
        # 基础代理头
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Forwarded-Host \$server_name;
        
        # 连接设置
        proxy_buffering off;
        proxy_buffer_size 4k;
        proxy_buffers 8 4k;
        proxy_read_timeout 60s;
        proxy_connect_timeout 60s;
        
        # 保持活动连接
        proxy_set_header Connection "";
        
        # 禁用代理缓冲
        proxy_request_buffering off;
    }
EOF
        
        # 如果启用缓存
        if [ "$ENABLE_CACHE" = true ]; then
            cat >> "$CONFIG_FILE" << EOF
    
    # 静态文件缓存 (HTTPS)
    location ~* \.(jpg|jpeg|png|gif|ico|css|js)$ {
        proxy_pass http://127.0.0.1:$BACKEND_PORT;
        proxy_cache proxy_cache;
        proxy_cache_valid 200 302 1h;
        proxy_cache_valid 404 1m;
        proxy_cache_use_stale error timeout updating http_500 http_502 http_503 http_504;
        add_header X-Cache-Status \$upstream_cache_status;
    }
EOF
        fi
        
        # HTTPS服务器的WebSocket配置
        if [ "$WEBSOCKET" = true ]; then
            cat >> "$CONFIG_FILE" << EOF
    
    # WebSocket支持 (HTTPS)
    location /ws/ {
        proxy_pass http://127.0.0.1:$BACKEND_PORT;
        proxy_http_version 1.1;
        
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "Upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
    
    location ~ ^/(socket\.io|websocket)/ {
        proxy_pass http://127.0.0.1:$BACKEND_PORT;
        proxy_http_version 1.1;
        
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "Upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
EOF
        fi
        
        echo "}" >> "$CONFIG_FILE"
        
        # 添加缓存配置
        if [ "$ENABLE_CACHE" = true ]; then
            cat >> "$CONFIG_FILE" << EOF

# 代理缓存配置
proxy_cache_path /var/cache/nginx levels=1:2 keys_zone=proxy_cache:10m 
                 max_size=1g inactive=60m use_temp_path=off;
EOF
        fi
    fi
    
    # 启用配置
    mkdir -p /etc/nginx/sites-enabled
    ln -sf "$CONFIG_FILE" "/etc/nginx/sites-enabled/${DOMAIN}.conf"
    
    echo -e "\n${GREEN}✅ 配置创建成功${NC}"
    echo -e "${BLUE}配置文件:${NC} $CONFIG_FILE"
    echo -e "${BLUE}域名:${NC} $DOMAIN"
    echo -e "${BLUE}后端服务:${NC} 127.0.0.1:$BACKEND_PORT"
    echo -e "${BLUE}SSL:${NC} $( [ "$SSL_AVAILABLE" = true ] && echo '启用' || echo '未启用' )"
    echo -e "${BLUE}强制HTTPS:${NC} $( [ "$FORCE_HTTPS" = true ] && echo '是' || echo '否' )"
    echo -e "${BLUE}WebSocket:${NC} $( [ "$WEBSOCKET" = true ] && echo '启用' || echo '未启用' )"
    echo -e "${BLUE}缓存:${NC} $( [ "$ENABLE_CACHE" = true ] && echo '启用' || echo '未启用' )"
    
    if [ "$SSL_AVAILABLE" = true ] && [ -f "$CERT_FILE" ]; then
        echo -e "\n${YELLOW}证书路径:${NC}"
        echo -e "  证书: $CERT_FILE"
        echo -e "  密钥: $KEY_FILE"
    fi
}

# 删除站点配置
delete_site() {
    log "INFO" "删除站点配置"
    
    # 列出所有启用的站点
    echo -e "${YELLOW}当前启用的站点:${NC}"
    local i=1
    local sites=()
    
    if ls /etc/nginx/sites-enabled/*.conf 2>/dev/null >/dev/null; then
        for conf in /etc/nginx/sites-enabled/*.conf; do
            local domain=$(basename "$conf" .conf)
            sites+=("$domain")
            echo -e "  ${GREEN}$i.${NC} $domain"
            ((i++))
        done
    else
        echo -e "${RED}没有启用的站点配置${NC}"
        return
    fi
    
    if [ ${#sites[@]} -eq 0 ]; then
        echo -e "${RED}没有站点可删除${NC}"
        return
    fi
    
    echo -ne "\n${CYAN}请选择要删除的站点编号${NC} (1-${#sites[@]}): "
    read choice
    
    if [[ $choice =~ ^[0-9]+$ ]] && [ $choice -ge 1 ] && [ $choice -le ${#sites[@]} ]; then
        local domain=${sites[$((choice-1))]}
        
        echo -e "${YELLOW}确定要删除站点 '$domain' 吗？${NC}"
        echo -ne "${RED}此操作将删除配置文件和符号链接${NC} (y/n): "
        read -n 1 confirm
        echo
        
        if [[ $confirm =~ ^[Yy]$ ]]; then
            # 删除符号链接
            rm -f "/etc/nginx/sites-enabled/${domain}.conf"
            
            # 删除配置文件
            if [ -f "/etc/nginx/sites-available/${domain}.conf" ]; then
                rm -f "/etc/nginx/sites-available/${domain}.conf"
            fi
            
            # 删除日志文件
            rm -f "/var/log/nginx/${domain}"*.log 2>/dev/null
            rm -f "/var/log/nginx/ssl/${domain}"*.log 2>/dev/null
            
            log "INFO" "站点 '$domain' 已删除"
            
            # 建议重载Nginx
            echo -ne "${YELLOW}是否现在重载Nginx？${NC} (y/n): "
            read -n 1 reload
            echo
            if [[ $reload =~ ^[Yy]$ ]]; then
                reload_nginx
            fi
        else
            echo -e "${GREEN}取消删除操作${NC}"
        fi
    else
        echo -e "${RED}无效的选择${NC}"
    fi
}

# 测试并重载Nginx（兼容不同系统）
reload_nginx() {
    log "INFO" "测试Nginx配置..."
    
    if nginx -t 2>&1; then
        log "INFO" "配置测试通过"
        
        echo -e "${YELLOW}重载Nginx...${NC}"
        
        local reloaded=false
        
        # 尝试不同的重载方式
        if nginx -s reload 2>/dev/null; then
            reloaded=true
        elif [ "$OS" = "alpine" ]; then
            if rc-service nginx reload 2>/dev/null; then
                reloaded=true
            fi
        elif [ "$OS" = "debian" ] || [ "$OS" = "ubuntu" ]; then
            if systemctl reload nginx 2>/dev/null; then
                reloaded=true
            fi
        elif systemctl reload nginx 2>/dev/null; then
            reloaded=true
        fi
        
        if [ "$reloaded" = true ]; then
            log "INFO" "Nginx重载成功"
        else
            # 尝试重启
            echo -e "${YELLOW}重载失败，尝试重启...${NC}"
            if [ "$OS" = "alpine" ]; then
                rc-service nginx restart 2>/dev/null && reloaded=true
            elif [ "$OS" = "debian" ] || [ "$OS" = "ubuntu" ]; then
                systemctl restart nginx 2>/dev/null && reloaded=true
            elif systemctl restart nginx 2>/dev/null; then
                reloaded=true
            fi
            
            if [ "$reloaded" = true ]; then
                log "INFO" "Nginx重启成功"
            else
                log "ERROR" "Nginx重载/重启失败，请手动检查"
                return 1
            fi
        fi
        
        # 显示配置摘要
        show_config_summary
        return 0
    else
        log "ERROR" "配置测试失败"
        echo -e "${YELLOW}错误详情:${NC}"
        nginx -t 2>&1 | tail -10
        return 1
    fi
}

# 检查证书状态
check_certificates() {
    log "INFO" "检查证书状态"
    
    echo -e "${BLUE}搜索证书目录...${NC}"
    
    # 检查主要证书目录
    local cert_dirs=(
        "/etc/nginx/ssl"
        "/etc/letsencrypt/live"
        "/root/.acme.sh"
    )
    
    for dir in "${cert_dirs[@]}"; do
        if [ -d "$dir" ]; then
            echo -e "\n${GREEN}目录: $dir${NC}"
            find "$dir" -name "*.pem" -o -name "*.crt" -o -name "*.cer" -o -name "*.key" 2>/dev/null | head -20 | while read file; do
                if [ -f "$file" ]; then
                    local size=$(du -h "$file" 2>/dev/null | cut -f1 || echo "N/A")
                    local perms=$(stat -c "%a %U:%G" "$file" 2>/dev/null || echo "N/A")
                    local type=""
                    
                    if [[ "$file" =~ \.crt$|\.pem$|\.cer$ ]]; then
                        type="证书"
                        echo -e "  📄 $file ($size, $perms)"
                        
                        # 检查证书过期时间
                        local expire_date=$(openssl x509 -enddate -noout -in "$file" 2>/dev/null | cut -d= -f2)
                        if [ -n "$expire_date" ]; then
                            echo -e "    过期时间: $expire_date"
                        fi
                    elif [[ "$file" =~ \.key$ ]]; then
                        type="密钥"
                        echo -e "  🔑 $file ($size, $perms)"
                    fi
                fi
            done
        fi
    done
    
    # 显示目录结构
    echo -e "\n${BLUE}Nginx SSL目录结构:${NC}"
    if [ -d "/etc/nginx/ssl" ]; then
        echo -e "${GREEN}有效证书文件:${NC}"
        local count=0
        find /etc/nginx/ssl -type f \( -name "*.pem" -o -name "*.crt" -o -name "*.cer" \) 2>/dev/null | \
        while read file; do
            if [ -s "$file" ] && [ -r "$file" ]; then
                local expire_date=$(openssl x509 -enddate -noout -in "$file" 2>/dev/null | cut -d= -f2 2>/dev/null)
                if [ -n "$expire_date" ]; then
                    count=$((count+1))
                    local size=$(du -h "$file" 2>/dev/null | cut -f1 || echo "N/A")
                    echo "  $count. 📄 $file"
                    echo "     大小: $size, 过期: $expire_date"
                fi
            fi
        done
        
        echo -e "\n${GREEN}密钥文件:${NC}"
        find /etc/nginx/ssl -type f -name "*.key" 2>/dev/null | \
        while read file; do
            if [ -s "$file" ] && [ -r "$file" ]; then
                local size=$(du -h "$file" 2>/dev/null | cut -f1 || echo "N/A")
                local perms=$(stat -c "%a" "$file" 2>/dev/null || echo "N/A")
                echo "  🔑 $file ($size, 权限:$perms)"
            fi
        done
        
        echo -e "\n${GREEN}目录结构:${NC}"
        echo "/etc/nginx/ssl/"
        ls -la /etc/nginx/ssl/ 2>/dev/null | tail -n +2 || echo "无法列出目录"
    else
        echo -e "${YELLOW}/etc/nginx/ssl/ 目录不存在${NC}"
        echo -e "${YELLOW}创建证书目录...${NC}"
        mkdir -p /etc/nginx/ssl/{certs,private}