#!/bin/bash

# ================= Nginx反向代理管理器 =================
# 功能：配置反向代理，自动使用已有证书
# 依赖：Nginx、证书管理器创建的证书
# ===================================================

NGINX_CONF_DIR="/etc/nginx/conf.d"
SSL_DIR="/etc/nginx/ssl"
INSTALL_DIR="/opt/nginx-proxy"
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
        echo "证书文件不存在:"
        echo "  $SSL_DIR/$DOMAIN.crt"
        echo "  $SSL_DIR/$DOMAIN.key"
        echo "请先使用证书管理器申请证书"
        return 1
    fi
}

# 初始化 Nginx 目录结构
init_nginx_dirs() {
    mkdir -p "$NGINX_CONF_DIR"
    mkdir -p "$SSL_DIR"
    mkdir -p "$NGINX_LOG_DIR"
    mkdir -p /var/lib/nginx/logs 2>/dev/null
    mkdir -p /run/nginx 2>/dev/null
}

# 初始化 Nginx 配置
init_nginx() {
    echo -e "${YELLOW}>>> 初始化 Nginx 配置...${NC}"
    
    init_nginx_dirs
    
    # 生成默认的 fallback 证书（防止直接IP访问）
    if [ ! -f "$SSL_DIR/fallback.key" ]; then
        echo -e "${YELLOW}生成默认证书...${NC}"
        openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
            -keyout "$SSL_DIR/fallback.key" \
            -out "$SSL_DIR/fallback.crt" \
            -subj "/CN=Invalid" 2>/dev/null
    fi
    
    # 创建日志目录和文件
    touch "$NGINX_LOG_DIR/access.log" 2>/dev/null
    touch "$NGINX_LOG_DIR/error.log" 2>/dev/null
    
    # 检查 Nginx 用户
    if grep -q "^user" /etc/nginx/nginx.conf 2>/dev/null; then
        NGINX_USER=$(grep "^user" /etc/nginx/nginx.conf | head -1 | awk '{print $2}' | tr -d ';')
    else
        # 根据系统设置默认用户
        if [ -f /etc/alpine-release ]; then
            NGINX_USER="nginx"
        else
            NGINX_USER="www-data"
        fi
    fi
    
    # 创建主配置文件（如果不存在或需要修复）
    if [ ! -f /etc/nginx/nginx.conf ] || ! grep -q "http {" /etc/nginx/nginx.conf; then
        echo -e "${YELLOW}创建 Nginx 主配置文件...${NC}"
        cat > /etc/nginx/nginx.conf <<EOF
user $NGINX_USER;
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

    access_log $NGINX_LOG_DIR/access.log;
    error_log $NGINX_LOG_DIR/error.log;

    include $NGINX_CONF_DIR/*.conf;
    
    # 禁止直接IP访问
    server {
        listen 80 default_server;
        listen 443 ssl default_server;
        server_name _;
        
        ssl_certificate $SSL_DIR/fallback.crt;
        ssl_certificate_key $SSL_DIR/fallback.key;
        
        return 444;
    }
}
EOF
    fi
    
    # 修复权限
    chown -R $NGINX_USER:$NGINX_USER "$NGINX_LOG_DIR" 2>/dev/null
    chown -R $NGINX_USER:$NGINX_USER /var/lib/nginx 2>/dev/null
    
    reload_nginx
    echo -e "${GREEN}>>> Nginx 初始化完成${NC}"
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
    
    # 创建 Nginx 配置
    echo -e "${YELLOW}创建配置: $CONF_FILE${NC}"
    
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
    access_log $NGINX_LOG_DIR/${FULL_DOMAIN}_access.log;
    error_log $NGINX_LOG_DIR/${FULL_DOMAIN}_error.log;
}
EOF
    
    echo -e "${GREEN}>>> Nginx 配置已创建: $CONF_FILE${NC}"
    
    # 配置防火墙
    configure_firewall "$PORT"
    
    # 测试配置并重载
    if nginx -t 2>/dev/null; then
        reload_nginx
        echo -e "${GREEN}>>> 配置成功!${NC}"
        echo ""
        echo -e "${GREEN}========================================${NC}"
        echo -e "${GREEN}      反向代理配置成功                  ${NC}"
        echo -e "${GREEN}========================================${NC}"
        echo ""
        echo "访问地址: https://$FULL_DOMAIN"
        echo "后端地址: http://127.0.0.1:$PORT"
        echo "配置文件: $CONF_FILE"
        echo ""
        echo -e "${YELLOW}注意:${NC}"
        echo "1. 请确保后端服务正在运行"
        echo "2. 端口 $PORT 已被防火墙保护，仅允许本地访问"
        echo "3. 如果需要从外部访问后端，请关闭防火墙相应规则"
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
    
    # 对于 IPv6，使用 ::1（但有些系统可能不支持）
    if iptables -I INPUT -s ::1 -p tcp --dport "$PORT" -j ACCEPT 2>/dev/null; then
        echo "IPv6 规则添加成功"
    else
        echo "跳过 IPv6 规则（可能不支持）"
    fi
    
    echo -e "${GREEN}端口 $PORT 已加锁，仅允许本地访问${NC}"
    
    # 尝试保存规则
    if command -v netfilter-persistent &> /dev/null; then
        netfilter-persistent save 2>/dev/null
    elif [ -f /etc/alpine-release ]; then
        rc-service iptables save 2>/dev/null
    else
        echo "无法自动保存 iptables 规则，重启后需要重新配置"
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
    echo "域名: $(grep "server_name" "$CONF_FILE" | head -1 | awk '{print $2}' | sed 's/;//' 2>/dev/null || echo '未知')"
    echo "后端: $(grep "proxy_pass" "$CONF_FILE" | head -1 | awk '{print $2}' | sed 's/;//' 2>/dev/null || echo '未知')"
    echo ""
    
    # 确认删除
    read -p "确认删除此配置? (y/N): " CONFIRM
    if [ "$CONFIRM" = "y" ] || [ "$CONFIRM" = "Y" ]; then
        # 尝试提取端口号解锁防火墙
        PORT=$(grep -o "proxy_pass.*:[0-9]\+" "$CONF_FILE" | grep -o "[0-9]\+" | head -1)
        
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
        echo ""
        echo "配置文件: $(basename "$CONF")"
        echo "域名: $(grep "server_name" "$CONF" | head -1 | awk '{print $2}' | sed 's/;//' 2>/dev/null || echo '未知')"
        echo "后端: $(grep "proxy_pass" "$CONF" | head -1 | awk '{print $2}' | sed 's/;//' 2>/dev/null || echo '未知')"
        
        # 检查证书
        CERT_FILE=$(grep "ssl_certificate" "$CONF" | head -1 | awk '{print $2}' | sed 's/;//' 2>/dev/null)
        if [ -n "$CERT_FILE" ] && [ -f "$CERT_FILE" ]; then
            echo "证书: 有效 ($CERT_FILE)"
        else
            echo "证书: ${RED}无效或不存在${NC}"
        fi
    done
}

# 重载 Nginx
reload_nginx() {
    echo -e "${YELLOW}>>> 检查 Nginx 配置...${NC}"
    
    if ! nginx -t 2>/dev/null; then
        echo -e "${RED}Nginx 配置测试失败${NC}"
        echo "错误信息:"
        nginx -t 2>&1
        return 1
    fi
    
    echo -e "${YELLOW}>>> 重载 Nginx...${NC}"
    
    # 检查 Nginx 是否运行
    if pgrep nginx >/dev/null 2>&1; then
        if nginx -s reload 2>/dev/null; then
            echo -e "${GREEN}Nginx 重载成功${NC}"
        else
            echo -e "${RED}Nginx 重载失败，尝试重启...${NC}"
            pkill nginx 2>/dev/null
            sleep 1
            nginx 2>/dev/null && echo -e "${GREEN}Nginx 启动成功${NC}" || echo -e "${RED}Nginx 启动失败${NC}"
        fi
    else
        echo -e "${YELLOW}Nginx 未运行，正在启动...${NC}"
        nginx 2>/dev/null && echo -e "${GREEN}Nginx 启动成功${NC}" || echo -e "${RED}Nginx 启动失败${NC}"
    fi
}

# 检查 Nginx 状态
check_nginx_status() {
    if pgrep nginx >/dev/null 2>&1; then
        echo -e "${GREEN}Nginx 正在运行${NC}"
    else
        echo -e "${RED}Nginx 未运行${NC}"
    fi
}

# 主菜单
main_menu() {
    check_cert_manager
    init_nginx_dirs
    
    echo -e "${YELLOW}正在检查系统...${NC}"
    check_nginx_status
    
    while true; do
        echo -e "\n${YELLOW}===== Nginx反向代理管理器 =====${NC}"
        echo "1. 添加反向代理"
        echo "2. 移除反向代理"
        echo "3. 查看当前配置"
        echo "4. 重载 Nginx"
        echo "5. 初始化 Nginx (修复配置)"
        echo "6. 检查 Nginx 状态"
        echo "0. 退出"
        
        read -p "请选择: " OPT
        
        case $OPT in
            1) add_proxy ;;
            2) remove_proxy ;;
            3) list_proxies ;;
            4) reload_nginx ;;
            5) init_nginx ;;
            6) check_nginx_status ;;
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