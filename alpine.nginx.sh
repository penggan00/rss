#!/bin/bash

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

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

# 检查依赖
check_dependencies() {
    local deps=("nginx" "openssl" "tree")
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
            apk update
            for dep in "${missing[@]}"; do
                apk add "$dep"
            done
        fi
    fi
}

# 初始化目录结构
init_directories() {
    log "INFO" "初始化目录结构..."
    
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
    chown -R nginx:nginx /etc/nginx/ssl/private
    chmod 700 /etc/nginx/ssl/private
    chmod 644 /etc/nginx/ssl/certs/*
    2>/dev/null
    
    log "INFO" "目录结构初始化完成"
}

# 域名验证函数（简化版，兼容Alpine ash）
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
    
    # 可能的证书路径
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
            KEY_FILE="${cert/fullchain./privkey.}"
            KEY_FILE="${KEY_FILE/fullchain./}"
            KEY_FILE="${KEY_FILE/.cer/.key}"
            KEY_FILE="${KEY_FILE/.crt/.key}"
            KEY_FILE="${KEY_FILE/.pem/.key}"
            
            # 尝试找到对应的key文件
            if [ ! -f "$KEY_FILE" ]; then
                KEY_FILE=""
                for key in "${key_paths[@]}"; do
                    if [ -f "$key" ]; then
                        KEY_FILE="$key"
                        break
                    fi
                done
            fi
            
            if [ -n "$KEY_FILE" ]; then
                log "INFO" "SSL证书配置成功"
                return 0
            fi
        fi
    done
    
    # 如果没找到，尝试查找父域名或通配符证书
    if [[ "$clean_domain" == *.* ]]; then
        # 尝试父域名
        local parent_domain="${clean_domain#*.}"
        local parent_certs=(
            "/root/.acme.sh/${parent_domain}_ecc/fullchain.cer"
            "/root/.acme.sh/${parent_domain}/fullchain.cer"
        )
        
        for cert in "${parent_certs[@]}"; do
            if [ -f "$cert" ]; then
                CERT_FILE="$cert"
                KEY_FILE="${cert/fullchain./privkey.}"
                KEY_FILE="${KEY_FILE/fullchain./}"
                KEY_FILE="${KEY_FILE/.cer/.key}"
                
                if [ -f "$KEY_FILE" ]; then
                    log "INFO" "使用父域名SSL证书"
                    return 0
                fi
            fi
        done
        
        # 尝试通配符证书
        local wildcard_domain="*.${clean_domain#*.}"
        local wildcard_certs=(
            "/root/.acme.sh/${wildcard_domain}_ecc/fullchain.cer"
            "/root/.acme.sh/${wildcard_domain}/fullchain.cer"
        )
        
        for cert in "${wildcard_certs[@]}"; do
            if [ -f "$cert" ]; then
                CERT_FILE="$cert"
                KEY_FILE="${cert/fullchain./privkey.}"
                KEY_FILE="${KEY_FILE/fullchain./}"
                KEY_FILE="${KEY_FILE/.cer/.key}"
                
                if [ -f "$KEY_FILE" ]; then
                    log "INFO" "使用通配符SSL证书"
                    return 0
                fi
            fi
        done
    fi
    
    log "WARN" "未找到SSL证书"
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
        
        # 设置权限
        chmod 644 "$CERT_FILE"
        chmod 600 "$KEY_FILE"
        chown nginx:nginx "$KEY_FILE"
        
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

# 测试并重载Nginx
reload_nginx() {
    log "INFO" "测试Nginx配置..."
    
    if nginx -t 2>&1; then
        log "INFO" "配置测试通过"
        
        echo -e "${YELLOW}重载Nginx...${NC}"
        
        # 尝试不同的重载方式
        if nginx -s reload 2>/dev/null; then
            log "INFO" "Nginx重载成功"
        elif rc-service nginx reload 2>/dev/null; then
            log "INFO" "Nginx重载成功"
        elif systemctl reload nginx 2>/dev/null; then
            log "INFO" "Nginx重载成功"
        else
            # 尝试重启
            echo -e "${YELLOW}重载失败，尝试重启...${NC}"
            if systemctl restart nginx 2>/dev/null || rc-service nginx restart 2>/dev/null; then
                log "INFO" "Nginx重启成功"
            else
                log "ERROR" "Nginx重载/重启失败"
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
                    local size=$(du -h "$file" | cut -f1)
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
        if command -v tree &> /dev/null; then
            tree /etc/nginx/ssl -L 3 2>/dev/null || ls -laR /etc/nginx/ssl/
        else
            ls -la /etc/nginx/ssl/
        fi
    else
        echo -e "${YELLOW}/etc/nginx/ssl/ 目录不存在${NC}"
        echo -e "${YELLOW}创建证书目录...${NC}"
        mkdir -p /etc/nginx/ssl/{certs,private}
        chmod 750 /etc/nginx/ssl/private
        chmod 755 /etc/nginx/ssl/certs
    fi
}

# 显示配置摘要
show_config_summary() {
    echo -e "\n${BLUE}================ 配置摘要 ================${NC}"
    
    # 显示启用的站点
    echo -e "${GREEN}当前启用的代理:${NC}"
    if ls /etc/nginx/sites-enabled/*.conf 2>/dev/null >/dev/null; then
        for conf in /etc/nginx/sites-enabled/*.conf; do
            local domain=$(grep "server_name" "$conf" | head -1 | awk '{print $2}' | tr -d ';')
            local port=$(grep "listen" "$conf" | grep -v "listen \[::\]" | head -1 | awk '{print $2}' | tr -d ';')
            local backend=$(grep "proxy_pass" "$conf" | head -1 | awk '{print $2}' | tr -d ';')
            echo -e "  🌐 $domain (端口: $port) -> $backend"
        done
    else
        echo -e "  没有启用的配置"
    fi
    
    # 显示监听端口
    echo -e "\n${GREEN}监听端口:${NC}"
    if command -v netstat &> /dev/null; then
        netstat -tulpn 2>/dev/null | grep -E ":80\>|:443\>" | awk '{print "  " $4}'
    elif command -v ss &> /dev/null; then
        ss -tulpn 2>/dev/null | grep -E ":80\>|:443\>" | awk '{print "  " $5}'
    else
        echo "  无法获取端口信息"
    fi
    
    # 显示Nginx状态
    echo -e "\n${GREEN}Nginx状态:${NC}"
    if pgrep nginx > /dev/null; then
        echo -e "  ✅ 正在运行"
        echo -e "  主进程PID: $(cat /run/nginx/nginx.pid 2>/dev/null || pgrep -o nginx)"
    else
        echo -e "  ❌ 未运行"
    fi
    
    echo -e "${BLUE}========================================${NC}"
}

# 查看当前配置
show_current_config() {
    echo -e "${YELLOW}>>> 当前Nginx配置${NC}"
    
    # 检查Nginx主配置
    echo -e "${BLUE}Nginx主配置:${NC}"
    if [ -f "/etc/nginx/nginx.conf" ]; then
        echo -e "  路径: /etc/nginx/nginx.conf"
        echo -e "  大小: $(du -h /etc/nginx/nginx.conf | cut -f1)"
    else
        echo -e "  ❌ 主配置文件不存在"
    fi
    
    # 显示启用的站点
    echo -e "\n${BLUE}启用的站点配置:${NC}"
    if ls /etc/nginx/sites-enabled/*.conf 2>/dev/null >/dev/null; then
        for conf in /etc/nginx/sites-enabled/*.conf; do
            echo -e "\n${GREEN}配置文件: $(basename "$conf")${NC}"
            echo "  路径: $conf"
            echo "  大小: $(du -h "$conf" | cut -f1)"
            echo "  修改时间: $(stat -c "%y" "$conf" 2>/dev/null | cut -d'.' -f1)"
            
            # 提取关键信息
            local domain=$(grep -h "server_name" "$conf" | head -1 | awk '{print $2}' | tr -d ';')
            local port=$(grep -h "listen" "$conf" | grep -v "listen \[::\]" | head -1 | awk '{print $2}' | tr -d ';' | cut -d' ' -f1)
            local backend=$(grep -h "proxy_pass" "$conf" | head -1 | awk '{print $2}' | tr -d ';')
            local ssl=$(grep -h "ssl_certificate" "$conf" | head -1 | awk '{print $2}' | tr -d ';')
            
            echo "  域名: $domain"
            echo "  端口: $port"
            echo "  后端: $backend"
            
            if [ -n "$ssl" ]; then
                echo "  SSL证书: $ssl"
                if [ -f "$ssl" ]; then
                    echo -e "  ✅ 证书文件存在"
                else
                    echo -e "  ❌ 证书文件不存在"
                fi
            fi
        done
    else
        echo "  没有启用的配置"
    fi
    
    # 显示可用配置
    echo -e "\n${BLUE}可用的站点配置:${NC}"
    if ls /etc/nginx/sites-available/*.conf 2>/dev/null >/dev/null; then
        for conf in /etc/nginx/sites-available/*.conf; do
            local enabled="❌"
            if [ -L "/etc/nginx/sites-enabled/$(basename "$conf")" ]; then
                enabled="✅"
            fi
            echo "  $enabled $(basename "$conf")"
        done
    else
        echo "  没有可用的配置"
    fi
}

# 显示系统信息
show_system_info() {
    echo -e "\n${BLUE}========== 系统信息 ==========${NC}"
    
    # OS信息
    if [ -f /etc/os-release ]; then
        echo -e "${GREEN}操作系统:${NC}"
        grep PRETTY_NAME /etc/os-release | cut -d= -f2 | tr -d '\"'
    fi
    
    # Nginx信息
    echo -e "${GREEN}Nginx版本:${NC}"
    nginx -v 2>&1
    
    # 内存信息
    echo -e "${GREEN}内存使用:${NC}"
    free -h | awk 'NR==2{printf "总: %s, 已用: %s, 可用: %s\n", $2, $3, $7}'
    
    # 磁盘信息
    echo -e "${GREEN}磁盘空间:${NC}"
    df -h / | awk 'NR==2{printf "总: %s, 已用: %s, 可用: %s\n", $2, $3, $4}'
    
    # IP地址
    echo -e "${GREEN}IP地址:${NC}"
    hostname -I 2>/dev/null | awk '{print "  " $1}' || ip addr show | grep -oP 'inet \K[\d.]+' | grep -v '127.0.0.1' | head -3
    
    echo -e "${BLUE}===============================${NC}"
}

# 备份配置
backup_config() {
    local backup_dir="/var/backups/nginx/$(date +%Y%m%d_%H%M%S)"
    
    log "INFO" "备份Nginx配置到 $backup_dir"
    
    mkdir -p "$backup_dir"
    
    # 备份配置文件
    cp -r /etc/nginx/nginx.conf "$backup_dir/" 2>/dev/null
    cp -r /etc/nginx/sites-available "$backup_dir/" 2>/dev/null
    cp -r /etc/nginx/sites-enabled "$backup_dir/" 2>/dev/null
    cp -r /etc/nginx/ssl "$backup_dir/" 2>/dev/null
    
    # 备份日志文件
    tar -czf "$backup_dir/logs.tar.gz" /var/log/nginx/*.log 2>/dev/null
    
    # 创建备份信息文件
    cat > "$backup_dir/backup.info" << EOF
备份时间: $(date)
备份目录: $backup_dir
备份内容:
- Nginx主配置
- 站点可用配置
- 站点启用配置
- SSL证书
- 日志文件

文件列表:
$(find "$backup_dir" -type f | sed 's|^|  |')
EOF
    
    echo -e "${GREEN}✅ 备份完成${NC}"
    echo -e "备份位置: $backup_dir"
    echo -e "备份大小: $(du -sh "$backup_dir" | cut -f1)"
}

# 主菜单
show_menu() {
    clear
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${GREEN}      Nginx反向代理配置工具${NC}"
    echo -e "${BLUE}========================================${NC}"
    
    show_system_info
    
    echo -e "\n${GREEN}1.${NC} 创建新的反向代理"
    echo -e "${GREEN}2.${NC} 删除站点配置"
    echo -e "${GREEN}3.${NC} 重载Nginx配置"
    echo -e "${GREEN}4.${NC} 检查证书状态"
    echo -e "${GREEN}5.${NC} 查看当前配置"
    echo -e "${GREEN}6.${NC} 备份Nginx配置"
    echo -e "${GREEN}7.${NC} 初始化目录结构"
    echo -e "${GREEN}8.${NC} 显示系统信息"
    echo -e "${GREEN}9.${NC} 退出"
    echo -e "${BLUE}========================================${NC}"
    echo -ne "请选择操作 [1-9]: "
}

# 主函数
main() {
    # 检查root权限
    if [ "$EUID" -ne 0 ]; then 
        echo -e "${RED}请使用root权限运行此脚本${NC}"
        exit 1
    fi
    
    # 检查Nginx是否安装
    if ! command -v nginx &> /dev/null; then
        echo -e "${RED}Nginx未安装，请先安装Nginx${NC}"
        echo -e "${YELLOW}安装命令: apk add nginx${NC}"
        exit 1
    fi
    
    # 检查依赖
    check_dependencies
    
    # 初始化目录
    init_directories
    
    while true; do
        show_menu
        read choice
        
        case $choice in
            1)
                create_proxy_config
                echo -ne "\n${YELLOW}是否现在重载Nginx？${NC} (y/n): "
                read -n 1 reload
                echo
                if [[ $reload =~ ^[Yy]$ ]]; then
                    reload_nginx
                fi
                ;;
            2)
                delete_site
                ;;
            3)
                reload_nginx
                ;;
            4)
                check_certificates
                ;;
            5)
                show_current_config
                ;;
            6)
                backup_config
                ;;
            7)
                init_directories
                ;;
            8)
                show_system_info
                ;;
            9)
                echo -e "${GREEN}退出${NC}"
                exit 0
                ;;
            *)
                echo -e "${RED}无效选择${NC}"
                ;;
        esac
        
        if [ "$choice" != "9" ]; then
            echo -ne "\n${YELLOW}按Enter继续...${NC}"
            read
        fi
    done
}

# 运行主函数
main