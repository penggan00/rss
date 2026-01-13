#!/bin/bash

# ================= Nginx反向代理管理器 (支持 Alpine 和 Debian) =================
# 功能：配置反向代理，自动使用已有证书
# 支持：Alpine Linux 和 Debian 12
# ==============================================================================

NGINX_CONF_DIR="/etc/nginx/conf.d"
SSL_DIR="/etc/nginx/ssl"
DOMAIN_LIST="/opt/cert-manager/config/domains.list"
NGINX_LOG_DIR="/var/log/nginx"

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

# 检测操作系统
detect_os() {
    if [ -f /etc/alpine-release ]; then
        echo "alpine"
    elif [ -f /etc/debian_version ]; then
        echo "debian"
    else
        echo "unknown"
    fi
}

# 获取 Nginx 用户
get_nginx_user() {
    OS=$(detect_os)
    if [ "$OS" = "alpine" ]; then
        echo "nginx"
    else
        echo "www-data"
    fi
}

# 获取 Nginx 组
get_nginx_group() {
    OS=$(detect_os)
    if [ "$OS" = "alpine" ]; then
        echo "nginx"
    else
        echo "www-data"
    fi
}

# 检查证书管理器是否安装
check_cert_manager() {
    if [ ! -f "$DOMAIN_LIST" ]; then
        echo -e "${RED}错误: 证书管理器未安装或未配置证书${NC}"
        echo "请先运行证书申请脚本申请证书"
        exit 1
    fi
}

# 选择域名
select_domain() {
    echo -e "${YELLOW}可用的证书域名:${NC}"
    
    if [ ! -s "$DOMAIN_LIST" ]; then
        echo -e "${RED}暂无证书，请先申请证书${NC}"
        return 1
    fi
    
    cat -n "$DOMAIN_LIST"
    echo ""
    
    read -p "请选择域名编号: " DOMAIN_NUM
    
    if [[ ! "$DOMAIN_NUM" =~ ^[0-9]+$ ]]; then
        echo -e "${RED}请输入数字编号${NC}"
        return 1
    fi
    
    DOMAIN=$(sed -n "${DOMAIN_NUM}p" "$DOMAIN_LIST" 2>/dev/null)
    
    if [ -z "$DOMAIN" ]; then
        echo -e "${RED}无效选择${NC}"
        return 1
    fi
    
    # 检查证书是否存在（检查快捷方式）
    if [ -f "$SSL_DIR/$DOMAIN.crt" ] && [ -f "$SSL_DIR/$DOMAIN.key" ]; then
        echo -e "${GREEN}选择域名: $DOMAIN${NC}"
        return 0
    else
        echo -e "${RED}错误: 找不到 $DOMAIN 的证书文件${NC}"
        return 1
    fi
}

# 初始化 Nginx 目录结构
init_nginx_dirs() {
    NGINX_USER=$(get_nginx_user)
    NGINX_GROUP=$(get_nginx_group)
    
    echo -e "${YELLOW}检测到系统: $(detect_os), 使用用户: $NGINX_USER:$NGINX_GROUP${NC}"
    
    mkdir -p "$NGINX_CONF_DIR"
    mkdir -p "$SSL_DIR"
    mkdir -p "$NGINX_LOG_DIR"
    mkdir -p /var/lib/nginx/logs 2>/dev/null
    mkdir -p /run/nginx 2>/dev/null
    
    # 创建必要的日志文件
    touch "$NGINX_LOG_DIR/access.log" 2>/dev/null
    touch "$NGINX_LOG_DIR/error.log" 2>/dev/null
    
    # 设置正确的权限
    chown -R $NGINX_USER:$NGINX_GROUP "$NGINX_LOG_DIR" 2>/dev/null
    chown -R $NGINX_USER:$NGINX_GROUP /var/lib/nginx 2>/dev/null
    chmod 755 /run/nginx 2>/dev/null
}

# 修复 Nginx 配置文件
fix_nginx_config() {
    NGINX_USER=$(get_nginx_user)
    
    echo -e "${YELLOW}>>> 修复 Nginx 配置文件...${NC}"
    
    # 检查当前配置文件
    if [ -f /etc/nginx/nginx.conf ]; then
        echo -e "${YELLOW}备份原配置文件...${NC}"
        cp /etc/nginx/nginx.conf /etc/nginx/nginx.conf.backup.$(date +%Y%m%d%H%M%S)
    fi
    
    # 创建适合 Alpine 和 Debian 的通用配置
    cat > /etc/nginx/nginx.conf <<EOF
# Nginx 主配置文件 - 适用于 Alpine 和 Debian
user $NGINX_USER;
worker_processes auto;
pid /run/nginx.pid;

events {
    worker_connections 1024;
    use epoll;
    multi_accept on;
}

http {
    # 基础设置
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    
    # 日志
    access_log $NGINX_LOG_DIR/access.log;
    error_log $NGINX_LOG_DIR/error.log;
    
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
    
    # SSL 设置
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-RSA-AES256-GCM-SHA512:DHE-RSA-AES256-GCM-SHA512;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;
    
    # Gzip 压缩
    gzip on;
    gzip_vary on;
    gzip_min_length 1024;
    gzip_proxied expired no-cache no-store private auth;
    gzip_types text/plain text/css text/xml text/javascript application/javascript application/xml+rss application/json;
    
    # 包含其他配置
    include $NGINX_CONF_DIR/*.conf;
    
    # 默认服务器 - 禁止直接IP访问
    server {
        listen 80 default_server;
        listen [::]:80 default_server;
        listen 443 ssl default_server;
        listen [::]:443 ssl default_server;
        server_name _;
        
        # 使用默认证书
        ssl_certificate $SSL_DIR/fallback.crt;
        ssl_certificate_key $SSL_DIR/fallback.key;
        
        # 返回 444 无响应
        return 444;
    }
}
EOF
    
    echo -e "${GREEN}>>> Nginx 配置文件已修复${NC}"
}

# 初始化 Nginx
init_nginx() {
    echo -e "${YELLOW}>>> 初始化 Nginx...${NC}"
    
    init_nginx_dirs
    
    # 生成默认的 fallback 证书（防止直接IP访问）
    if [ ! -f "$SSL_DIR/fallback.key" ]; then
        echo -e "${YELLOW}生成默认证书...${NC}"
        openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
            -keyout "$SSL_DIR/fallback.key" \
            -out "$SSL_DIR/fallback.crt" \
            -subj "/CN=Invalid" 2>/dev/null
        chmod 600 "$SSL_DIR/fallback.key"
    fi
    
    # 修复 Nginx 配置
    fix_nginx_config
    
    # 检查 Nginx 配置
    if nginx -t 2>/dev/null; then
        echo -e "${GREEN}>>> Nginx 配置检查通过${NC}"
        
        # 启动 Nginx（如果未运行）
        if ! pgrep nginx >/dev/null 2>&1; then
            echo -e "${YELLOW}启动 Nginx...${NC}"
            
            OS=$(detect_os)
            if [ "$OS" = "alpine" ]; then
                rc-service nginx start 2>/dev/null || nginx
            else
                systemctl start nginx 2>/dev/null || nginx
            fi
            
            sleep 2
            
            if pgrep nginx >/dev/null 2>&1; then
                echo -e "${GREEN}>>> Nginx 启动成功${NC}"
            else
                echo -e "${RED}>>> Nginx 启动失败，请检查错误日志${NC}"
            fi
        else
            echo -e "${GREEN}>>> Nginx 已在运行${NC}"
        fi
    else
        echo -e "${RED}>>> Nginx 配置检查失败${NC}"
        nginx -t 2>&1
        return 1
    fi
}

# 添加反向代理
add_proxy() {
    echo -e "${YELLOW}=== 添加反向代理 ===${NC}"
    
    # 选择域名
    if ! select_domain; then
        return 1
    fi
    
    echo ""
    echo "当前选择域名: $DOMAIN"
    echo "示例: 输入 'api' 会生成 api.$DOMAIN"
    echo ""
    
    read -p "请输入子域名前缀 (如 api): " PREFIX
    
    # 清理前缀中的域名部分
    PREFIX=$(echo "$PREFIX" | sed "s/\.$DOMAIN//g" | sed "s/\.$//g")
    
    if [ -z "$PREFIX" ]; then
        echo -e "${RED}前缀不能为空${NC}"
        return 1
    fi
    
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
        echo -e "${YELLOW}配置已存在: $CONF_FILE${NC}"
        read -p "是否覆盖? (y/N): " OVERWRITE
        if [ "$OVERWRITE" != "y" ] && [ "$OVERWRITE" != "Y" ]; then
            return 1
        fi
    fi
    
    # 证书路径（使用快捷方式）
    CERT_FILE="$SSL_DIR/$DOMAIN.crt"
    KEY_FILE="$SSL_DIR/$DOMAIN.key"
    
    # 检查证书文件是否存在
    if [ ! -f "$CERT_FILE" ] || [ ! -f "$KEY_FILE" ]; then
        echo -e "${RED}证书文件不存在:${NC}"
        echo "  $CERT_FILE"
        echo "  $KEY_FILE"
        echo "请先确保证书申请成功"
        return 1
    fi
    
    # 创建 Nginx 配置
    echo -e "${YELLOW}创建配置: $CONF_FILE${NC}"
    
    cat > "$CONF_FILE" <<EOF
# 反向代理配置
# 生成时间: $(date)
# 域名: $FULL_DOMAIN
# 后端: http://127.0.0.1:$PORT

server {
    listen 80;
    listen [::]:80;
    server_name $FULL_DOMAIN;
    
    # 重定向到 HTTPS
    return 301 https://\$host\$request_uri;
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name $FULL_DOMAIN;

    # SSL 证书
    ssl_certificate $CERT_FILE;
    ssl_certificate_key $KEY_FILE;
    
    # SSL 优化
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;
    
    # HSTS (可选)
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    
    # 安全头
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    
    # 根目录
    root /var/www/html;
    index index.html;
    
    # 反代配置
    location / {
        proxy_pass http://127.0.0.1:$PORT;
        
        # 基本头部
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Forwarded-Host \$host;
        
        # 超时设置
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
        
        # 缓冲
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
    
    # 访问日志
    access_log $NGINX_LOG_DIR/${FULL_DOMAIN}_access.log;
    error_log $NGINX_LOG_DIR/${FULL_DOMAIN}_error.log;
}
EOF
    
    echo -e "${GREEN}>>> Nginx 配置已创建: $CONF_FILE${NC}"
    
    # 配置防火墙（根据系统）
    configure_firewall "$PORT"
    
    # 测试配置
    echo -e "${YELLOW}>>> 测试 Nginx 配置...${NC}"
    if nginx -t 2>/dev/null; then
        echo -e "${GREEN}>>> Nginx 配置测试通过${NC}"
        
        # 重载 Nginx
        reload_nginx
        
        echo -e "${GREEN}>>> 配置成功!${NC}"
        echo ""
        echo -e "${GREEN}========================================${NC}"
        echo -e "${GREEN}      反向代理配置成功                  ${NC}"
        echo -e "${GREEN}========================================${NC}"
        echo ""
        echo "域名: https://$FULL_DOMAIN"
        echo "后端: http://127.0.0.1:$PORT"
        echo "配置文件: $CONF_FILE"
        echo ""
        echo -e "${YELLOW}注意:${NC}"
        echo "1. 请确保后端服务正在端口 $PORT 上运行"
        echo "2. 防火墙已配置为仅允许本地访问端口 $PORT"
        echo "3. 如果需要外部访问，请调整防火墙规则"
    else
        echo -e "${RED}Nginx 配置测试失败${NC}"
        echo "错误信息:"
        nginx -t 2>&1
        echo ""
        echo -e "${YELLOW}正在删除有问题的配置...${NC}"
        rm -f "$CONF_FILE"
        return 1
    fi
}

# 配置防火墙（支持 Alpine 和 Debian）
configure_firewall() {
    local PORT=$1
    OS=$(detect_os)
    
    echo -e "${YELLOW}>>> 配置防火墙 (系统: $OS)...${NC}"
    
    if [ "$OS" = "alpine" ]; then
        # Alpine 使用 iptables
        if command -v iptables &> /dev/null; then
            # 检查规则是否已存在
            if iptables -C INPUT -p tcp --dport "$PORT" -j DROP 2>/dev/null; then
                echo "端口 $PORT 已被封锁，跳过"
                return
            fi
            
            # 添加规则
            iptables -I INPUT -p tcp --dport "$PORT" -j DROP
            iptables -I INPUT -s 127.0.0.1 -p tcp --dport "$PORT" -j ACCEPT
            
            echo -e "${GREEN}端口 $PORT 已加锁 (iptables)${NC}"
            
            # 保存规则
            if command -v rc-service &> /dev/null; then
                rc-service iptables save 2>/dev/null && echo "规则已保存"
            fi
        else
            echo "未安装 iptables，跳过防火墙配置"
        fi
    elif [ "$OS" = "debian" ]; then
        # Debian 使用 ufw 或 iptables
        if command -v ufw &> /dev/null; then
            echo "检测到 ufw，配置防火墙规则..."
            
            # 检查规则是否已存在
            if ufw status | grep -q "$PORT/tcp.*DENY"; then
                echo "端口 $PORT 已被封锁，跳过"
                return
            fi
            
            # 添加规则
            ufw deny "$PORT/tcp"
            ufw allow from 127.0.0.1 to any port "$PORT"
            
            echo -e "${GREEN}端口 $PORT 已加锁 (ufw)${NC}"
        elif command -v iptables &> /dev/null; then
            echo "使用 iptables 配置防火墙..."
            
            if iptables -C INPUT -p tcp --dport "$PORT" -j DROP 2>/dev/null; then
                echo "端口 $PORT 已被封锁，跳过"
                return
            fi
            
            iptables -I INPUT -p tcp --dport "$PORT" -j DROP
            iptables -I INPUT -s 127.0.0.1 -p tcp --dport "$PORT" -j ACCEPT
            
            echo -e "${GREEN}端口 $PORT 已加锁 (iptables)${NC}"
            
            # 保存规则
            if command -v netfilter-persistent &> /dev/null; then
                netfilter-persistent save 2>/dev/null && echo "规则已保存"
            fi
        else
            echo "未检测到防火墙工具，跳过配置"
        fi
    else
        echo "未知系统，跳过防火墙配置"
    fi
}

# 移除反向代理
remove_proxy() {
    echo -e "${YELLOW}=== 移除反向代理 ===${NC}"
    
    if [ ! -d "$NGINX_CONF_DIR" ] || [ -z "$(ls -A $NGINX_CONF_DIR/*.conf 2>/dev/null)" ]; then
        echo -e "${RED}暂无反向代理配置${NC}"
        return
    fi
    
    echo -e "${YELLOW}当前配置:${NC}"
    ls -1 "$NGINX_CONF_DIR"/*.conf 2>/dev/null | xargs -n1 basename | nl
    
    read -p "请选择要删除的配置编号: " CONF_NUM
    
    if [[ ! "$CONF_NUM" =~ ^[0-9]+$ ]]; then
        echo -e "${RED}请输入数字编号${NC}"
        return
    fi
    
    CONF_NAME=$(ls -1 "$NGINX_CONF_DIR"/*.conf 2>/dev/null | sed -n "${CONF_NUM}p" | xargs basename 2>/dev/null)
    
    if [ -z "$CONF_NAME" ]; then
        echo -e "${RED}无效选择${NC}"
        return
    fi
    
    CONF_FILE="$NGINX_CONF_DIR/$CONF_NAME"
    
    if [ ! -f "$CONF_FILE" ]; then
        echo -e "${RED}配置文件不存在${NC}"
        return
    fi
    
    # 显示配置信息
    echo ""
    echo -e "${YELLOW}配置信息:${NC}"
    echo "文件名: $CONF_NAME"
    DOMAIN_NAME=$(grep "server_name" "$CONF_FILE" | head -1 | awk '{print $2}' | sed 's/;//' 2>/dev/null || echo '未知')
    BACKEND=$(grep "proxy_pass" "$CONF_FILE" | head -1 | awk '{print $2}' | sed 's/;//' 2>/dev/null || echo '未知')
    echo "域名: $DOMAIN_NAME"
    echo "后端: $BACKEND"
    echo ""
    
    # 确认删除
    read -p "确认删除此配置? (y/N): " CONFIRM
    if [ "$CONFIRM" = "y" ] || [ "$CONFIRM" = "Y" ]; then
        # 尝试提取端口号解锁防火墙
        PORT=$(grep -o "proxy_pass.*:[0-9]\+" "$CONF_FILE" | grep -o "[0-9]\+" | head -1)
        
        rm "$CONF_FILE"
        echo -e "${GREEN}配置已删除${NC}"
        
        # 解锁防火墙
        if [ -n "$PORT" ]; then
            echo -e "${YELLOW}解锁端口 $PORT ...${NC}"
            OS=$(detect_os)
            
            if [ "$OS" = "alpine" ] && command -v iptables &> /dev/null; then
                iptables -D INPUT -p tcp --dport "$PORT" -j DROP 2>/dev/null
                iptables -D INPUT -s 127.0.0.1 -p tcp --dport "$PORT" -j ACCEPT 2>/dev/null
            elif [ "$OS" = "debian" ] && command -v ufw &> /dev/null; then
                ufw delete deny "$PORT/tcp" 2>/dev/null
                ufw delete allow from 127.0.0.1 to any port "$PORT" 2>/dev/null
            fi
        fi
        
        reload_nginx
    else
        echo "取消删除"
    fi
}

# 查看配置
list_proxies() {
    echo -e "${YELLOW}=== 当前反向代理配置 ===${NC}"
    
    if [ ! -d "$NGINX_CONF_DIR" ] || [ -z "$(ls -A $NGINX_CONF_DIR/*.conf 2>/dev/null)" ]; then
        echo "暂无配置"
        return
    fi
    
    CONFIGS=$(ls "$NGINX_CONF_DIR"/*.conf 2>/dev/null)
    
    for CONF in $CONFIGS; do
        CONF_NAME=$(basename "$CONF")
        DOMAIN_NAME=$(grep "server_name" "$CONF" | head -1 | awk '{print $2}' | sed 's/;//' 2>/dev/null || echo '未知')
        BACKEND=$(grep "proxy_pass" "$CONF" | head -1 | awk '{print $2}' | sed 's/;//' 2>/dev/null || echo '未知')
        
        echo ""
        echo "配置文件: $CONF_NAME"
        echo "域名: $DOMAIN_NAME"
        echo "后端: $BACKEND"
        
        # 检查证书
        CERT_FILE=$(grep "ssl_certificate" "$CONF" | head -1 | awk '{print $2}' | sed 's/;//' 2>/dev/null)
        if [ -n "$CERT_FILE" ] && [ -f "$CERT_FILE" ]; then
            echo "证书: 有效"
        else
            echo "证书: ${RED}无效或不存在${NC}"
        fi
    done
}

# 重载 Nginx
reload_nginx() {
    echo -e "${YELLOW}>>> 重载 Nginx...${NC}"
    
    OS=$(detect_os)
    
    if [ "$OS" = "alpine" ]; then
        if rc-service nginx reload 2>/dev/null; then
            echo -e "${GREEN}Nginx 重载成功${NC}"
        else
            echo -e "${YELLOW}尝试使用 nginx -s reload...${NC}"
            nginx -s reload 2>/dev/null && echo -e "${GREEN}Nginx 重载成功${NC}" || echo -e "${RED}Nginx 重载失败${NC}"
        fi
    else
        if systemctl reload nginx 2>/dev/null; then
            echo -e "${GREEN}Nginx 重载成功${NC}"
        else
            echo -e "${YELLOW}尝试使用 nginx -s reload...${NC}"
            nginx -s reload 2>/dev/null && echo -e "${GREEN}Nginx 重载成功${NC}" || echo -e "${RED}Nginx 重载失败${NC}"
        fi
    fi
}

# 检查 Nginx 状态
check_nginx_status() {
    OS=$(detect_os)
    
    echo -e "${YELLOW}检查 Nginx 状态 (系统: $OS)...${NC}"
    
    if pgrep nginx >/dev/null 2>&1; then
        echo -e "${GREEN}Nginx 正在运行${NC}"
        
        # 检查配置文件
        if nginx -t 2>/dev/null; then
            echo -e "${GREEN}Nginx 配置检查通过${NC}"
        else
            echo -e "${RED}Nginx 配置检查失败${NC}"
            nginx -t 2>&1
        fi
    else
        echo -e "${RED}Nginx 未运行${NC}"
    fi
}

# 主菜单
main_menu() {
    # 检查证书
    if [ ! -f "$DOMAIN_LIST" ]; then
        echo -e "${RED}错误: 未找到证书管理器配置${NC}"
        echo "请先运行证书申请脚本"
        exit 1
    fi
    
    # 显示系统信息
    OS=$(detect_os)
    echo -e "${YELLOW}检测到系统: $OS${NC}"
    
    # 初始化目录
    init_nginx_dirs
    
    while true; do
        echo -e "\n${YELLOW}===== Nginx反向代理管理器 ($OS) =====${NC}"
        echo "1. 添加反向代理"
        echo "2. 移除反向代理"
        echo "3. 查看当前配置"
        echo "4. 重载 Nginx"
        echo "5. 初始化/修复 Nginx 配置"
        echo "6. 检查 Nginx 状态"
        echo "7. 测试 Nginx 配置"
        echo "0. 退出"
        
        read -p "请选择: " OPT
        
        case $OPT in
            1) add_proxy ;;
            2) remove_proxy ;;
            3) list_proxies ;;
            4) reload_nginx ;;
            5) init_nginx ;;
            6) check_nginx_status ;;
            7) 
                echo -e "${YELLOW}测试 Nginx 配置...${NC}"
                if nginx -t 2>/dev/null; then
                    echo -e "${GREEN}配置检查通过${NC}"
                else
                    nginx -t 2>&1
                fi
                ;;
            0) 
                echo "再见！"
                exit 0
                ;;
            *) echo -e "${RED}无效选项${NC}" ;;
        esac
    done
}

# 运行主函数
main_menu