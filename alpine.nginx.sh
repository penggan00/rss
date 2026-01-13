#!/bin/bash

# ================= Alpine Nginx 反向代理管理器 (IPv6支持) =================
# 专为 Alpine Linux 设计，支持 IPv6 优先监听
# =========================================================================

NGINX_CONF_DIR="/etc/nginx/conf.d"
SSL_DIR="/etc/nginx/ssl"
DOMAIN_LIST="/opt/cert-manager/config/domains.list"

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# 检查 Root 权限
if [ "$(id -u)" -ne 0 ]; then
    echo -e "${RED}错误: 必须使用 root 权限${NC}"
    exit 1
fi

# 确保是 Alpine
if [ ! -f /etc/alpine-release ]; then
    echo -e "${RED}错误: 仅支持 Alpine Linux${NC}"
    exit 1
fi

# 检查系统是否支持 IPv6
check_ipv6() {
    if ip -6 addr show 2>/dev/null | grep -q "inet6" && [ -f /proc/net/if_inet6 ]; then
        echo -e "${GREEN}检测到 IPv6 支持${NC}"
        return 0
    else
        echo -e "${YELLOW}未检测到 IPv6 支持${NC}"
        return 1
    fi
}

# 修复 Nginx 配置（强制）
fix_nginx_now() {
    echo -e "${YELLOW}>>> 修复 Nginx 配置...${NC}"
    
    # 确保目录存在
    mkdir -p "$NGINX_CONF_DIR" "$SSL_DIR" /var/log/nginx /run/nginx
    
    # 检查 IPv6 支持
    HAS_IPV6=$(check_ipv6 && echo "yes" || echo "no")
    
    # 创建 Nginx 配置（IPv6 优先）
    cat > /etc/nginx/nginx.conf <<EOF
user nginx nginx;
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
    
    # 日志
    access_log /var/log/nginx/access.log;
    error_log /var/log/nginx/error.log;
    
    # 性能优化
    sendfile on;
    tcp_nopush on;
    tcp_nodelay on;
    keepalive_timeout 65;
    types_hash_max_size 2048;
    server_tokens off;
    
    # 限制
    client_max_body_size 100m;
    client_body_timeout 12;
    client_header_timeout 12;
    send_timeout 10;
    
    # 包含其他配置
    include $NGINX_CONF_DIR/*.conf;
    
    # 默认服务器 - 禁止直接IP访问
    # IPv6 优先监听
    server {
        listen [::]:80 default_server ipv6only=on;
        listen 80 default_server;
        server_name _;
        
        return 444;
    }
    
    server {
        listen [::]:443 ssl default_server ipv6only=on;
        listen 443 ssl default_server;
        server_name _;
        
        ssl_certificate $SSL_DIR/fallback.crt;
        ssl_certificate_key $SSL_DIR/fallback.key;
        
        return 444;
    }
}
EOF
    
    # 创建 fallback 证书
    if [ ! -f "$SSL_DIR/fallback.key" ]; then
        echo -e "${YELLOW}生成默认证书...${NC}"
        openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
            -keyout "$SSL_DIR/fallback.key" \
            -out "$SSL_DIR/fallback.crt" \
            -subj "/CN=Invalid" 2>/dev/null
        chmod 600 "$SSL_DIR/fallback.key"
    fi
    
    # 测试配置
    echo -e "${YELLOW}测试 Nginx 配置...${NC}"
    nginx -t 2>&1
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}Nginx 配置正常${NC}"
        
        # 显示监听信息
        echo -e "${YELLOW}Nginx 监听配置:${NC}"
        echo "IPv6 优先: 启用"
        echo "默认服务器: 已配置 IPv6 和 IPv4"
        return 0
    else
        echo -e "${RED}Nginx 配置错误${NC}"
        return 1
    fi
}

# 启动/重载 Nginx
reload_nginx() {
    echo -e "${YELLOW}>>> 重载 Nginx...${NC}"
    
    # 先测试配置
    if ! nginx -t 2>/dev/null; then
        echo -e "${RED}配置错误，无法重载${NC}"
        nginx -t 2>&1
        return 1
    fi
    
    if pgrep nginx >/dev/null; then
        if nginx -s reload 2>/dev/null; then
            echo -e "${GREEN}Nginx 重载成功${NC}"
            
            # 显示监听状态
            sleep 1
            echo -e "${YELLOW}当前监听端口:${NC}"
            if command -v ss >/dev/null; then
                ss -tulpn | grep nginx
            else
                netstat -tulpn | grep nginx
            fi
            return 0
        else
            echo -e "${RED}重载失败，尝试重启...${NC}"
            pkill nginx 2>/dev/null
            sleep 1
            if nginx; then
                echo -e "${GREEN}Nginx 启动成功${NC}"
                echo -e "${YELLOW}当前监听端口:${NC}"
                if command -v ss >/dev/null; then
                    ss -tulpn | grep nginx
                else
                    netstat -tulpn | grep nginx
                fi
                return 0
            else
                echo -e "${RED}启动失败${NC}"
                return 1
            fi
        fi
    else
        if nginx; then
            echo -e "${GREEN}Nginx 启动成功${NC}"
            echo -e "${YELLOW}当前监听端口:${NC}"
            if command -v ss >/dev/null; then
                ss -tulpn | grep nginx
            else
                netstat -tulpn | grep nginx
            fi
            return 0
        else
            echo -e "${RED}启动失败${NC}"
            return 1
        fi
    fi
}

# 测试 Nginx 配置
test_nginx_config() {
    echo -e "${YELLOW}>>> 测试 Nginx 配置...${NC}"
    nginx -t 2>&1
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}配置测试通过${NC}"
        return 0
    else
        echo -e "${RED}配置测试失败${NC}"
        return 1
    fi
}

# 添加反向代理（IPv6支持）
add_proxy() {
    echo -e "${YELLOW}=== 添加反向代理 (IPv6支持) ===${NC}"
    
    # 检查证书
    if [ ! -f "$DOMAIN_LIST" ] || [ ! -s "$DOMAIN_LIST" ]; then
        echo -e "${RED}没有可用证书${NC}"
        return 1
    fi
    
    # 选择域名
    echo "可用域名:"
    cat -n "$DOMAIN_LIST"
    echo ""
    
    read -p "选择域名编号: " NUM
    DOMAIN=$(sed -n "${NUM}p" "$DOMAIN_LIST" 2>/dev/null)
    
    if [ -z "$DOMAIN" ]; then
        echo -e "${RED}无效选择${NC}"
        return 1
    fi
    
    # 检查证书文件
    CERT="$SSL_DIR/$DOMAIN.crt"
    KEY="$SSL_DIR/$DOMAIN.key"
    
    if [ ! -f "$CERT" ] || [ ! -f "$KEY" ]; then
        echo -e "${RED}证书文件不存在:${NC}"
        echo "  $CERT"
        echo "  $KEY"
        return 1
    fi
    
    echo -e "${GREEN}使用域名: $DOMAIN${NC}"
    
    # 获取子域名
    read -p "子域名前缀 (如 api): " PREFIX
    PREFIX=$(echo "$PREFIX" | sed 's/\..*//g')
    
    if [ -z "$PREFIX" ]; then
        echo -e "${RED}前缀不能为空${NC}"
        return 1
    fi
    
    # 获取端口
    read -p "后端端口: " PORT
    if ! [[ "$PORT" =~ ^[0-9]+$ ]] || [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then
        echo -e "${RED}无效端口${NC}"
        return 1
    fi
    
    # 创建配置
    FULL_DOMAIN="$PREFIX.$DOMAIN"
    CONF_FILE="$NGINX_CONF_DIR/$FULL_DOMAIN.conf"
    
    # 检查 IPv6 支持
    if check_ipv6; then
        HAS_IPV6="yes"
        echo -e "${GREEN}检测到 IPv6 支持${NC}"
    else
        HAS_IPV6="no"
        echo -e "${YELLOW}未检测到 IPv6 支持${NC}"
    fi
    
    # 创建配置文件
    echo -e "${YELLOW}创建配置文件: $CONF_FILE${NC}"
    
    if [ "$HAS_IPV6" = "yes" ]; then
        # 有 IPv6 支持的配置
        cat > "$CONF_FILE" <<EOF
# 反向代理: $FULL_DOMAIN -> 127.0.0.1:$PORT
# 生成时间: $(date)
# IPv6 支持: 已启用

# HTTP 服务器 - IPv6 优先
server {
    listen [::]:80 ipv6only=on;
    listen 80;
    server_name $FULL_DOMAIN;
    
    # 记录访问日志
    access_log /var/log/nginx/${FULL_DOMAIN}_access.log;
    error_log /var/log/nginx/${FULL_DOMAIN}_error.log;
    
    # 重定向到 HTTPS
    return 301 https://\$host\$request_uri;
}

# HTTPS 服务器 - IPv6 优先
server {
    listen [::]:443 ssl http2 ipv6only=on;
    listen 443 ssl http2;
    server_name $FULL_DOMAIN;
    
    # SSL 证书
    ssl_certificate $CERT;
    ssl_certificate_key $KEY;
    
    # SSL 配置
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-RSA-AES256-GCM-SHA512:DHE-RSA-AES256-GCM-SHA512;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;
    
    # 安全头
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    
    # 日志
    access_log /var/log/nginx/${FULL_DOMAIN}_ssl_access.log;
    error_log /var/log/nginx/${FULL_DOMAIN}_ssl_error.log;
    
    # 反向代理配置
    location / {
        proxy_pass http://127.0.0.1:$PORT;
        
        # 基础头部
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Forwarded-Host \$host;
        
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
    
    # 禁止访问隐藏文件
    location ~ /\. {
        deny all;
        access_log off;
        log_not_found off;
    }
}
EOF
    else
        # 无 IPv6 支持的配置
        cat > "$CONF_FILE" <<EOF
# 反向代理: $FULL_DOMAIN -> 127.0.0.1:$PORT
# 生成时间: $(date)
# IPv6 支持: 未启用

# HTTP 服务器
server {
    listen 80;
    server_name $FULL_DOMAIN;
    
    # 记录访问日志
    access_log /var/log/nginx/${FULL_DOMAIN}_access.log;
    error_log /var/log/nginx/${FULL_DOMAIN}_error.log;
    
    # 重定向到 HTTPS
    return 301 https://\$host\$request_uri;
}

# HTTPS 服务器
server {
    listen 443 ssl http2;
    server_name $FULL_DOMAIN;
    
    # SSL 证书
    ssl_certificate $CERT;
    ssl_certificate_key $KEY;
    
    # SSL 配置
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-RSA-AES256-GCM-SHA512:DHE-RSA-AES256-GCM-SHA512;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;
    
    # 安全头
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    
    # 日志
    access_log /var/log/nginx/${FULL_DOMAIN}_ssl_access.log;
    error_log /var/log/nginx/${FULL_DOMAIN}_ssl_error.log;
    
    # 反向代理配置
    location / {
        proxy_pass http://127.0.0.1:$PORT;
        
        # 基础头部
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Forwarded-Host \$host;
        
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
    
    # 禁止访问隐藏文件
    location ~ /\. {
        deny all;
        access_log off;
        log_not_found off;
    }
}
EOF
    fi
    
    echo -e "${GREEN}配置文件: $CONF_FILE${NC}"
    echo -e "${YELLOW}IPv6 支持: $HAS_IPV6${NC}"
    
    # 配置防火墙
    echo -e "${YELLOW}配置防火墙...${NC}"
    if command -v iptables &>/dev/null; then
        # 清理旧规则
        iptables -D INPUT -p tcp --dport $PORT -j DROP 2>/dev/null
        iptables -D INPUT -s 127.0.0.1 -p tcp --dport $PORT -j ACCEPT 2>/dev/null
        
        # 添加新规则
        iptables -I INPUT -p tcp --dport $PORT -j DROP
        iptables -I INPUT -s 127.0.0.1 -p tcp --dport $PORT -j ACCEPT
        
        echo -e "${GREEN}端口 $PORT 已锁定 (IPv4)${NC}"
        
        # 如果支持 IPv6，也配置 IPv6 防火墙
        if [ "$HAS_IPV6" = "yes" ] && command -v ip6tables &>/dev/null; then
            ip6tables -D INPUT -p tcp --dport $PORT -j DROP 2>/dev/null
            ip6tables -D INPUT -s ::1 -p tcp --dport $PORT -j ACCEPT 2>/dev/null
            
            ip6tables -I INPUT -p tcp --dport $PORT -j DROP
            ip6tables -I INPUT -s ::1 -p tcp --dport $PORT -j ACCEPT
            
            echo -e "${GREEN}端口 $PORT 已锁定 (IPv6)${NC}"
        fi
    else
        echo "未安装 iptables，跳过防火墙配置"
    fi
    
    # 测试配置
    echo -e "${YELLOW}测试 Nginx 配置...${NC}"
    if test_nginx_config; then
        echo -e "${GREEN}配置测试通过${NC}"
        
        # 重载 Nginx
        if reload_nginx; then
            echo -e "${GREEN}配置成功!${NC}"
            echo ""
            echo -e "${GREEN}========================================${NC}"
            echo -e "${GREEN}      IPv6 优先反向代理配置成功        ${NC}"
            echo -e "${GREEN}========================================${NC}"
            echo ""
            echo "域名: $FULL_DOMAIN"
            echo "后端: http://127.0.0.1:$PORT"
            echo "配置文件: $CONF_FILE"
            echo ""
            if [ "$HAS_IPV6" = "yes" ]; then
                echo -e "${YELLOW}IPv6 配置:${NC}"
                echo "HTTP: 监听 [::]:80 和 0.0.0.0:80"
                echo "HTTPS: 监听 [::]:443 和 0.0.0.0:443"
                echo ""
                echo -e "${YELLOW}访问方式:${NC}"
                echo "IPv6: https://[$FULL_DOMAIN]"
                echo "IPv4: https://$FULL_DOMAIN"
            else
                echo -e "${YELLOW}仅支持 IPv4:${NC}"
                echo "HTTP: 监听 0.0.0.0:80"
                echo "HTTPS: 监听 0.0.0.0:443"
                echo ""
                echo -e "${YELLOW}访问方式:${NC}"
                echo "https://$FULL_DOMAIN"
            fi
            echo ""
            echo -e "${YELLOW}防火墙状态:${NC}"
            echo "端口 $PORT: 已封锁（仅允许本地访问）"
            return 0
        else
            echo -e "${RED}Nginx 重载失败${NC}"
            rm -f "$CONF_FILE"
            return 1
        fi
    else
        echo -e "${RED}配置测试失败，删除配置文件${NC}"
        rm -f "$CONF_FILE"
        return 1
    fi
}

# 查看配置
list_configs() {
    echo -e "${YELLOW}=== 当前配置 ===${NC}"
    
    if [ ! -d "$NGINX_CONF_DIR" ] || [ -z "$(ls $NGINX_CONF_DIR/*.conf 2>/dev/null)" ]; then
        echo "暂无配置"
        return
    fi
    
    for conf in "$NGINX_CONF_DIR"/*.conf; do
        CONF_NAME=$(basename "$conf")
        DOMAIN_NAME=$(grep 'server_name' "$conf" | head -1 | awk '{print $2}' | sed 's/;//')
        PORT=$(grep 'proxy_pass' "$conf" | grep -o '[0-9]\+' | head -1)
        
        echo ""
        echo "文件: $CONF_NAME"
        echo "域名: $DOMAIN_NAME"
        echo "后端端口: $PORT"
        
        # 检查 IPv6 配置
        if grep -q "listen \[::\]" "$conf"; then
            echo "IPv6: ${GREEN}已启用${NC}"
        else
            echo "IPv6: ${RED}未启用${NC}"
        fi
    done
}

# 删除配置
delete_config() {
    echo -e "${YELLOW}=== 删除配置 ===${NC}"
    
    list_configs
    
    echo ""
    read -p "输入要删除的完整域名 (如 api.example.com): " DOMAIN_TO_DELETE
    
    if [ -z "$DOMAIN_TO_DELETE" ]; then
        return
    fi
    
    CONF_FILE="$NGINX_CONF_DIR/$DOMAIN_TO_DELETE.conf"
    
    if [ -f "$CONF_FILE" ]; then
        echo "找到配置: $CONF_FILE"
        read -p "确认删除? (y/N): " CONFIRM
        
        if [ "$CONFIRM" = "y" ] || [ "$CONFIRM" = "Y" ]; then
            # 获取端口解锁防火墙
            PORT=$(grep 'proxy_pass' "$CONF_FILE" | grep -o '[0-9]\+' | head -1)
            
            rm "$CONF_FILE"
            echo -e "${GREEN}配置已删除${NC}"
            
            # 解锁防火墙
            if [ -n "$PORT" ] && command -v iptables &>/dev/null; then
                echo -e "${YELLOW}解锁端口 $PORT ...${NC}"
                # IPv4
                iptables -D INPUT -p tcp --dport $PORT -j DROP 2>/dev/null
                iptables -D INPUT -s 127.0.0.1 -p tcp --dport $PORT -j ACCEPT 2>/dev/null
                
                # IPv6
                if command -v ip6tables &>/dev/null; then
                    ip6tables -D INPUT -p tcp --dport $PORT -j DROP 2>/dev/null
                    ip6tables -D INPUT -s ::1 -p tcp --dport $PORT -j ACCEPT 2>/dev/null
                fi
                
                echo "防火墙规则已清除"
            fi
            
            reload_nginx
        fi
    else
        echo -e "${RED}配置文件不存在${NC}"
    fi
}

# 检查 Nginx 状态
check_nginx_status() {
    echo -e "${YELLOW}=== Nginx 状态检查 ===${NC}"
    
    # 检查进程
    if pgrep nginx >/dev/null; then
        echo -e "${GREEN}Nginx 进程: 运行中${NC}"
        
        # 检查配置
        echo -e "${YELLOW}配置检查:${NC}"
        test_nginx_config
        
        # 显示监听端口
        echo -e "${YELLOW}监听端口:${NC}"
        if command -v ss >/dev/null; then
            echo "IPv4 和 IPv6 监听:"
            ss -tulpn | grep nginx
        else
            echo "IPv4 和 IPv6 监听:"
            netstat -tulpn | grep nginx
        fi
    else
        echo -e "${RED}Nginx 进程: 未运行${NC}"
    fi
    
    # 检查 IPv6 支持
    echo ""
    echo -e "${YELLOW}系统 IPv6 状态:${NC}"
    if check_ipv6; then
        echo -e "${GREEN}IPv6 地址:${NC}"
        ip -6 addr show 2>/dev/null | grep inet6 | grep -v "::1" | head -2
    else
        echo -e "${RED}系统不支持 IPv6 或未启用${NC}"
    fi
}

# 主菜单
main() {
    echo -e "${YELLOW}=== Alpine Nginx 管理器 (IPv6 支持) ==="
    echo "检测到系统: Alpine Linux"
    
    # 检查 IPv6
    if check_ipv6; then
        echo -e "${GREEN}IPv6 支持: 已启用${NC}"
    else
        echo -e "${YELLOW}IPv6 支持: 未检测到${NC}"
    fi
    
    echo "=====================================${NC}"
    
    # 先修复配置
    fix_nginx_now
    
    while true; do
        echo ""
        echo -e "${YELLOW}===== Alpine Nginx 管理器 ====="
        echo "1. 添加反向代理 (IPv6优先)"
        echo "2. 查看配置"
        echo "3. 删除配置"
        echo "4. 重载 Nginx"
        echo "5. 修复 Nginx 配置"
        echo "6. 检查 Nginx 状态"
        echo "7. 测试 Nginx 配置"
        echo "0. 退出"
        echo -e "==================================${NC}"
        
        read -p "选择: " OPT
        
        case $OPT in
            1) add_proxy ;;
            2) list_configs ;;
            3) delete_config ;;
            4) reload_nginx ;;
            5) fix_nginx_now ;;
            6) check_nginx_status ;;
            7) test_nginx_config ;;
            0) 
                echo "再见！"
                exit 0
                ;;
            *) echo -e "${RED}无效选项${NC}" ;;
        esac
    done
}

# 运行
main