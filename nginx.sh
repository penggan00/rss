#!/bin/bash

# ================= 配置区域 =================
INSTALL_DIR="/opt/nginx-auto"
NGINX_CONF_DIR="/etc/nginx/conf.d"
SSL_DIR="/etc/nginx/ssl"
ACME_HOME="$INSTALL_DIR/acme.sh"
CONFIG_FILE="$INSTALL_DIR/config.env"
# ===========================================

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

# ----------------------------------------------------
# 1. 环境检测与依赖安装
# ----------------------------------------------------
check_sys() {
    if [ -f /etc/debian_version ]; then
        OS="debian"
        # Debian 12 默认没有 iptables-persistent，需安装以保存规则
        if ! command -v iptables &> /dev/null; then
            apt-get update
            apt-get install -y nginx curl socat git openssl iptables iptables-persistent netfilter-persistent
        fi
        # 确保 Nginx 启动
        systemctl enable nginx
        systemctl start nginx
    elif [ -f /etc/alpine-release ]; then
        OS="alpine"
        # Alpine 需要 iptables 服务
        apk update
        apk add nginx curl socat git openssl iptables ip6tables
        mkdir -p /etc/nginx/conf.d
        rc-update add nginx default
        rc-service nginx start
        # 启用 iptables 服务以便保存
        rc-update add iptables default
        rc-service iptables start
    else
        echo -e "${RED}仅支持 Debian 12 或 Alpine Linux${NC}"
        exit 1
    fi
}

# ----------------------------------------------------
# 2. 安装 acme.sh
# ----------------------------------------------------
install_acme() {
    if [ ! -d "$ACME_HOME" ]; then
        echo -e "${YELLOW}>>> 正在安装 acme.sh...${NC}"
        mkdir -p "$INSTALL_DIR"
        
        # 这里的邮箱是用于注册 Let's Encrypt 账户的，不是 CF 的
        read -p "请输入一个邮箱用于接收证书过期通知 (任意邮箱): " LE_EMAIL
        
        git clone https://github.com/acmesh-official/acme.sh.git "$ACME_HOME"
        cd "$ACME_HOME"
        ./acme.sh --install --home "$ACME_HOME" --accountemail "$LE_EMAIL"
        cd "$INSTALL_DIR"
    fi
}

# ----------------------------------------------------
# 3. 初始化 Nginx (拒绝 IP 直接访问 80/443)
# ----------------------------------------------------
init_nginx() {
    echo -e "${YELLOW}>>> 初始化 Nginx 全局配置...${NC}"
    mkdir -p "$SSL_DIR"
    mkdir -p "$NGINX_CONF_DIR"

    # 生成假证书用于默认的 default_server (防止报错)
    if [ ! -f "$SSL_DIR/fallback.key" ]; then
        openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
        -keyout "$SSL_DIR/fallback.key" \
        -out "$SSL_DIR/fallback.crt" \
        -subj "/CN=Invalid" 2>/dev/null
    fi

    # 写入主配置文件 (覆盖)
    cat > /etc/nginx/nginx.conf <<EOF
user $([ "$OS" = "alpine" ] && echo "nginx" || echo "www-data");
worker_processes auto;
pid /run/nginx.pid;
include /etc/nginx/modules-enabled/*.conf;

events {
    worker_connections 1024;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    sendfile on;
    keepalive_timeout 65;
    client_max_body_size 100m;

    # 日志
    access_log /var/log/nginx/access.log;
    error_log /var/log/nginx/error.log;

    include $NGINX_CONF_DIR/*.conf;
    
    # 兼容配置
    include /etc/nginx/http.d/*.conf; 

    # --- 安全核心：禁止直接通过 IP 访问 80/443 ---
    server {
        listen 80 default_server;
        listen 443 ssl default_server;
        server_name _;
        
        ssl_certificate $SSL_DIR/fallback.crt;
        ssl_certificate_key $SSL_DIR/fallback.key;
        
        # 返回 444 代表无响应，直接切断连接
        return 444; 
    }
}
EOF

    # 清理默认配置
    rm -f /etc/nginx/conf.d/default.conf
    rm -f /etc/nginx/sites-enabled/default
    rm -f /etc/nginx/http.d/default.conf

    reload_nginx
}

reload_nginx() {
    if [ "$OS" = "alpine" ]; then
        rc-service nginx reload
    else
        systemctl reload nginx
    fi
}

# ----------------------------------------------------
# 4. 申请泛域名证书 (DNS API)
# ----------------------------------------------------
issue_cert() {
    echo -e "${YELLOW}--- 申请证书设置 ---${NC}"
    read -p "请输入主域名 (例如 example.com): " DOMAIN
    read -p "请输入 Cloudflare API Token (无需 Account ID): " CF_TOKEN
    
    export CF_Token="$CF_TOKEN"
    
    echo -e "${YELLOW}>>> 开始申请泛域名证书 *.$DOMAIN ...${NC}"
    
    "$ACME_HOME"/acme.sh --issue --server letsencrypt --dns dns_cf \
        -d "$DOMAIN" -d "*.$DOMAIN"
        
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}>>> 证书申请成功!${NC}"
        # 安装证书
        "$ACME_HOME"/acme.sh --install-cert -d "$DOMAIN" \
            --key-file       "$SSL_DIR/live.key"  \
            --fullchain-file "$SSL_DIR/live.cer" \
            --reloadcmd     "if [ -f /etc/alpine-release ]; then rc-service nginx reload; else systemctl reload nginx; fi"
            
        # 记录配置
        echo "DOMAIN=$DOMAIN" > "$CONFIG_FILE"
        
        # 将默认站点的证书也替换为真证书 (为了好看)
        sed -i "s|$SSL_DIR/fallback.key|$SSL_DIR/live.key|g" /etc/nginx/nginx.conf
        sed -i "s|$SSL_DIR/fallback.crt|$SSL_DIR/live.cer|g" /etc/nginx/nginx.conf
        reload_nginx
    else
        echo -e "${RED}证书申请失败，请检查 API Token 是否正确且有 DNS 编辑权限。${NC}"
    fi
}

# ----------------------------------------------------
# 5. 端口防火墙锁 (关键功能)
# ----------------------------------------------------
lock_port() {
    local PORT=$1
    echo -e "${YELLOW}>>> 正在配置防火墙，禁止外部 IP 访问端口 $PORT ...${NC}"
    
    # 逻辑：
    # 1. 允许 本地回环(127.0.0.1) 访问该端口 (Nginx 转发需要)
    # 2. 禁止 所有其他来源 访问该端口
    # 3. 避免重复添加规则

    # 检查是否已存在 DROP 规则
    iptables -C INPUT -p tcp --dport "$PORT" -j DROP 2>/dev/null
    if [ $? -eq 0 ]; then
        echo "端口 $PORT 已被封锁，跳过防火墙设置。"
        return
    fi

    # 插入规则：优先允许 localhost
    iptables -I INPUT -p tcp --dport "$PORT" -j DROP
    iptables -I INPUT -s 127.0.0.1 -p tcp --dport "$PORT" -j ACCEPT
    
    echo -e "${GREEN}端口 $PORT 已加锁。外部无法直接访问，只能通过域名。${NC}"
    
    # 保存规则
    save_iptables
}

unlock_port() {
    local PORT=$1
    # 删除规则 (尝试删除，不报错)
    iptables -D INPUT -s 127.0.0.1 -p tcp --dport "$PORT" -j ACCEPT 2>/dev/null
    iptables -D INPUT -p tcp --dport "$PORT" -j DROP 2>/dev/null
    save_iptables
}

save_iptables() {
    if [ "$OS" = "alpine" ]; then
        rc-service iptables save
    else
        netfilter-persistent save
    fi
}

# ----------------------------------------------------
# 6. 添加反向代理
# ----------------------------------------------------
add_proxy() {
    if [ ! -f "$CONFIG_FILE" ]; then
        echo -e "${RED}请先执行步骤 1 申请证书！${NC}"
        return
    fi
    source "$CONFIG_FILE"

    echo -e "${YELLOW}--- 添加反向代理 ---${NC}"
    read -p "请输入子域名前缀 (如 api -> api.$DOMAIN): " PREFIX
    read -p "请输入后端端口号 (例如 52655): " PORT
    
    # 校验端口是否为数字
    if ! [[ "$PORT" =~ ^[0-9]+$ ]]; then
        echo -e "${RED}端口必须是数字！${NC}"
        return
    fi

    FULL_DOMAIN="$PREFIX.$DOMAIN"
    CONF_FILE="$NGINX_CONF_DIR/$FULL_DOMAIN.conf"

    cat > "$CONF_FILE" <<EOF
server {
    listen 80;
    server_name $FULL_DOMAIN;
    return 301 https://\$host\$request_uri;
}

server {
    listen 443 ssl;
    server_name $FULL_DOMAIN;

    ssl_certificate $SSL_DIR/live.cer;
    ssl_certificate_key $SSL_DIR/live.key;
    
    # 生产级加密套件
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    location / {
        proxy_pass http://127.0.0.1:$PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        
        # WebSocket 支持
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
EOF
    # 执行端口加锁
    lock_port "$PORT"

    reload_nginx
    echo -e "${GREEN}>>> 成功！${NC}"
    echo -e "1. 域名访问: https://$FULL_DOMAIN (正常)"
    echo -e "2. IP直接访问: http://IP:$PORT (已被防火墙拦截)"
}

# ----------------------------------------------------
# 7. 删除反向代理
# ----------------------------------------------------
del_proxy() {
    echo -e "${YELLOW}当前配置:${NC}"
    ls "$NGINX_CONF_DIR" | grep ".conf"
    
    read -p "请输入要删除的前缀 (例如 api): " PREFIX
    source "$CONFIG_FILE" 2>/dev/null
    FULL_DOMAIN="$PREFIX.$DOMAIN"
    CONF_FILE="$NGINX_CONF_DIR/$FULL_DOMAIN.conf"

    if [ -f "$CONF_FILE" ]; then
        # 尝试提取端口以解锁防火墙
        PORT=$(grep "proxy_pass" "$CONF_FILE" | grep -oE '[0-9]+' | tail -1)
        
        rm "$CONF_FILE"
        echo -e "${GREEN}已删除 Nginx 配置${NC}"
        
        if [ ! -z "$PORT" ]; then
            echo -e "${YELLOW}正在解锁端口 $PORT ...${NC}"
            unlock_port "$PORT"
        fi
        
        reload_nginx
    else
        echo -e "${RED}找不到该配置${NC}"
    fi
}

# ----------------------------------------------------
# 主菜单
# ----------------------------------------------------
check_sys
install_acme

while true; do
    echo -e "\n${YELLOW}===== 生产级 Nginx 管理系统 =====${NC}"
    if [ -f "$CONFIG_FILE" ]; then
        source "$CONFIG_FILE"
        echo -e "当前主域名: ${GREEN}$DOMAIN${NC}"
    fi
    echo "1. 申请/重置泛域名证书 (初始配置)"
    echo "2. 添加反代 (自动屏蔽 IP:端口访问)"
    echo "3. 删除反代 (自动解封端口)"
    echo "0. 退出"
    
    read -p "请选择: " OPT
    case $OPT in
        1) issue_cert ;;
        2) add_proxy ;;
        3) del_proxy ;;
        0) exit 0 ;;
        *) echo "无效选项" ;;
    esac
done