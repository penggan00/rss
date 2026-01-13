#!/bin/bash

# ================= Nginx反向代理管理器 =================
# 功能：配置反向代理，自动使用已有证书
# 依赖：Nginx、证书管理器创建的证书
# ===================================================

NGINX_CONF_DIR="/etc/nginx/conf.d"
SSL_DIR="/etc/nginx/ssl"
INSTALL_DIR="/opt/nginx-proxy"
CONFIG_FILE="$INSTALL_DIR/config.env"
DOMAIN_LIST="/opt/cert-manager/config/domains.list"

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# 检查 Root 权限
if [ "$(id -u)" -ne 0 ]; then
    echo -e "${RED}错误: 必须使用 root 权限运行此脚本${NC}"
    exit 1
fi

# 检查证书管理器是否安装
check_cert_manager() {
    if [ ! -f "$DOMAIN_LIST" ]; then
        echo -e "${RED}错误: 证书管理器未安装或未配置证书${NC}"
        echo "请先运行 cert-manager.sh 申请证书"
        exit 1
    fi
}

# 选择域名
select_domain() {
    echo -e "${YELLOW}可用的证书域名:${NC}"
    cat "$DOMAIN_LIST" | nl
    
    read -p "请选择域名编号: " DOMAIN_NUM
    DOMAIN=$(sed -n "${DOMAIN_NUM}p" "$DOMAIN_LIST")
    
    if [ -z "$DOMAIN" ]; then
        echo -e "${RED}无效选择${NC}"
        return 1
    fi
    
    # 检查证书是否存在
    CERT_FILE="$SSL_DIR/certs/$DOMAIN/fullchain.pem"
    KEY_FILE="$SSL_DIR/private/$DOMAIN/key.pem"
    
    if [ ! -f "$CERT_FILE" ] || [ ! -f "$KEY_FILE" ]; then
        echo -e "${RED}错误: 找不到 $DOMAIN 的证书文件${NC}"
        echo "请先使用证书管理器申请证书"
        return 1
    fi
    
    echo -e "${GREEN}选择域名: $DOMAIN${NC}"
    return 0
}

# 初始化 Nginx
init_nginx() {
    echo -e "${YELLOW}>>> 初始化 Nginx 配置...${NC}"
    
    mkdir -p "$NGINX_CONF_DIR"
    mkdir -p "$SSL_DIR"
    
    # 生成默认的 fallback 证书（防止直接IP访问）
    if [ ! -f "$SSL_DIR/fallback.key" ]; then
        openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
            -keyout "$SSL_DIR/fallback.key" \
            -out "$SSL_DIR/fallback.crt" \
            -subj "/CN=Invalid" 2>/dev/null
    fi
    
    # 创建主配置文件（如果不存在）
    if [ ! -f /etc/nginx/nginx.conf ]; then
        cat > /etc/nginx/nginx.conf <<'EOF'
user www-data;
worker_processes auto;
pid /run/nginx.pid;

events {
    worker_connections 1024;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    sendfile on;
    keepalive_timeout 65;
    client_max_body_size 100m;

    access_log /var/log/nginx/access.log;
    error_log /var/log/nginx/error.log;

    include /etc/nginx/conf.d/*.conf;
    
    # 禁止直接IP访问
    server {
        listen 80 default_server;
        listen 443 ssl default_server;
        server_name _;
        
        ssl_certificate /etc/nginx/ssl/fallback.crt;
        ssl_certificate_key /etc/nginx/ssl/fallback.key;
        
        return 444;
    }
}
EOF
    fi
    
    reload_nginx
}

# 添加反向代理
add_proxy() {
    echo -e "${YELLOW}=== 添加反向代理 ===${NC}"
    
    # 选择域名
    select_domain || return 1
    
    read -p "请输入子域名前缀 (如 api -> api.$DOMAIN): " PREFIX
    read -p "请输入后端端口号 (例如 52655): " PORT
    
    # 校验端口
    if ! [[ "$PORT" =~ ^[0-9]+$ ]] || [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then
        echo -e "${RED}无效端口号${NC}"
        return 1
    fi
    
    FULL_DOMAIN="$PREFIX.$DOMAIN"
    CONF_FILE="$NGINX_CONF_DIR/$FULL_DOMAIN.conf"
    
    # 检查是否已存在配置
    if [ -f "$CONF_FILE" ]; then
        read -p "配置已存在，是否覆盖? (y/N): " OVERWRITE
        if [ "$OVERWRITE" != "y" ] && [ "$OVERWRITE" != "Y" ]; then
            return 1
        fi
    fi
    
    # 证书路径
    CERT_FILE="$SSL_DIR/certs/$DOMAIN/fullchain.pem"
    KEY_FILE="$SSL_DIR/private/$DOMAIN/key.pem"
    
    # 创建Nginx配置
    cat > "$CONF_FILE" <<EOF
# 自动生成于 $(date)
# 域名: $FULL_DOMAIN
# 后端: 127.0.0.1:$PORT

server {
    listen 80;
    server_name $FULL_DOMAIN;
    return 301 https://\$host\$request_uri;
}

server {
    listen 443 ssl http2;
    server_name $FULL_DOMAIN;

    # 使用证书管理器提供的证书
    ssl_certificate $CERT_FILE;
    ssl_certificate_key $KEY_FILE;
    
    # 安全配置
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-RSA-AES256-GCM-SHA512:DHE-RSA-AES256-GCM-SHA512;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;
    
    # HSTS
    add_header Strict-Transport-Security "max-age=63072000" always;
    
    # 反代配置
    location / {
        proxy_pass http://127.0.0.1:$PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        
        # 超时设置
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
        
        # WebSocket 支持
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
    }
    
    # 禁止访问敏感文件
    location ~ /\.(?!well-known) {
        deny all;
    }
    
    # 访问日志
    access_log /var/log/nginx/${FULL_DOMAIN}_access.log;
    error_log /var/log/nginx/${FULL_DOMAIN}_error.log;
}
EOF
    
    echo -e "${GREEN}>>> Nginx 配置已创建: $CONF_FILE${NC}"
    
    # 配置防火墙（可选）
    configure_firewall "$PORT"
    
    # 测试配置并重载
    if nginx -t; then
        reload_nginx
        echo -e "${GREEN}>>> 配置成功!${NC}"
        echo -e "域名: https://$FULL_DOMAIN"
        echo -e "后端: http://127.0.0.1:$PORT"
    else
        echo -e "${RED}Nginx 配置测试失败，请检查配置${NC}"
        rm -f "$CONF_FILE"
    fi
}

# 配置防火墙
configure_firewall() {
    local PORT=$1
    
    echo -e "${YELLOW}>>> 配置防火墙...${NC}"
    
    # 检查是否安装了iptables
    if ! command -v iptables &> /dev/null; then
        echo "未安装iptables，跳过防火墙配置"
        return
    fi
    
    # 检查规则是否已存在
    if iptables -C INPUT -p tcp --dport "$PORT" -j DROP 2>/dev/null; then
        echo "端口 $PORT 已被封锁，跳过"
        return
    fi
    
    # 添加规则：允许本地访问，禁止外部访问
    iptables -I INPUT -p tcp --dport "$PORT" -j DROP
    iptables -I INPUT -s 127.0.0.1 -p tcp --dport "$PORT" -j ACCEPT
    iptables -I INPUT -s ::1 -p tcp --dport "$PORT" -j ACCEPT
    
    echo -e "${GREEN}端口 $PORT 已加锁，仅允许本地访问${NC}"
    
    # 尝试保存规则
    if command -v netfilter-persistent &> /dev/null; then
        netfilter-persistent save
    elif [ -f /etc/alpine-release ]; then
        rc-service iptables save
    fi
}

# 移除反向代理
remove_proxy() {
    echo -e "${YELLOW}=== 移除反向代理 ===${NC}"
    
    if [ ! -d "$NGINX_CONF_DIR" ]; then
        echo -e "${RED}配置目录不存在${NC}"
        return
    fi
    
    # 列出所有配置
    echo -e "${YELLOW}当前配置:${NC}"
    ls -1 "$NGINX_CONF_DIR"/*.conf 2>/dev/null | xargs -n1 basename
    
    read -p "请输入要删除的配置文件全名: " CONF_NAME
    
    CONF_FILE="$NGINX_CONF_DIR/$CONF_NAME"
    
    if [ ! -f "$CONF_FILE" ]; then
        echo -e "${RED}配置文件不存在${NC}"
        return
    fi
    
    # 尝试提取端口号解锁防火墙
    PORT=$(grep -o "proxy_pass.*:[0-9]\+" "$CONF_FILE" | grep -o "[0-9]\+" | head -1)
    
    # 确认删除
    read -p "确认删除 $CONF_NAME? (y/N): " CONFIRM
    if [ "$CONFIRM" = "y" ] || [ "$CONFIRM" = "Y" ]; then
        rm "$CONF_FILE"
        echo -e "${GREEN}配置已删除${NC}"
        
        # 如果找到端口，解锁防火墙
        if [ -n "$PORT" ]; then
            echo -e "${YELLOW}解锁端口 $PORT ...${NC}"
            iptables -D INPUT -p tcp --dport "$PORT" -j DROP 2>/dev/null
            iptables -D INPUT -s 127.0.0.1 -p tcp --dport "$PORT" -j ACCEPT 2>/dev/null
            iptables -D INPUT -s ::1 -p tcp --dport "$PORT" -j ACCEPT 2>/dev/null
        fi
        
        reload_nginx
    fi
}

# 查看配置
list_proxies() {
    echo -e "${YELLOW}=== 当前反向代理配置 ===${NC}"
    
    if [ ! -d "$NGINX_CONF_DIR" ]; then
        echo "配置目录不存在"
        return
    fi
    
    CONFIGS=$(ls "$NGINX_CONF_DIR"/*.conf 2>/dev/null)
    
    if [ -z "$CONFIGS" ]; then
        echo "暂无配置"
        return
    fi
    
    for CONF in $CONFIGS; do
        echo ""
        echo "配置文件: $(basename "$CONF")"
        echo "域名: $(grep "server_name" "$CONF" | head -1 | awk '{print $2}' | sed 's/;//')"
        echo "后端: $(grep "proxy_pass" "$CONF" | head -1 | awk '{print $2}' | sed 's/;//')"
        echo "证书: $(grep "ssl_certificate" "$CONF" | head -1 | awk '{print $2}' | sed 's/;//')"
    done
}

# 重载 Nginx
reload_nginx() {
    echo -e "${YELLOW}>>> 重载 Nginx 配置...${NC}"
    
    if nginx -t; then
        if systemctl is-active nginx &>/dev/null; then
            systemctl reload nginx
        elif rc-service nginx status &>/dev/null; then
            rc-service nginx reload
        else
            nginx -s reload
        fi
        echo -e "${GREEN}Nginx 重载成功${NC}"
    else
        echo -e "${RED}Nginx 配置测试失败，请检查错误${NC}"
    fi
}

# 主菜单
main_menu() {
    check_cert_manager
    init_nginx
    
    while true; do
        echo -e "\n${YELLOW}===== Nginx反向代理管理器 =====${NC}"
        echo "1. 添加反向代理"
        echo "2. 移除反向代理"
        echo "3. 查看当前配置"
        echo "4. 重载 Nginx"
        echo "5. 初始化 Nginx (首次使用)"
        echo "0. 退出"
        
        read -p "请选择: " OPT
        
        case $OPT in
            1) add_proxy ;;
            2) remove_proxy ;;
            3) list_proxies ;;
            4) reload_nginx ;;
            5) init_nginx ;;
            0) exit 0 ;;
            *) echo -e "${RED}无效选项${NC}" ;;
        esac
    done
}

main_menu