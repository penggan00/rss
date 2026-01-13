#!/bin/bash

# ================= Alpine Nginx 反向代理管理器 =================
# 专为 Alpine Linux 设计，简化配置
# ==============================================================

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

# 修复 Nginx 配置（强制）
fix_nginx_now() {
    echo -e "${YELLOW}>>> 修复 Nginx 配置...${NC}"
    
    # 确保目录存在
    mkdir -p "$NGINX_CONF_DIR" "$SSL_DIR" /var/log/nginx /run/nginx
    
    # 创建极简 Nginx 配置
    cat > /etc/nginx/nginx.conf <<'EOF'
user nginx nginx;
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
    
    server {
        listen 80 default_server;
        server_name _;
        return 444;
    }
}
EOF
    
    # 创建 fallback 证书
    if [ ! -f "$SSL_DIR/fallback.key" ]; then
        openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
            -keyout "$SSL_DIR/fallback.key" \
            -out "$SSL_DIR/fallback.crt" \
            -subj "/CN=Invalid" 2>/dev/null
    fi
    
    # 测试配置
    if nginx -t 2>/dev/null; then
        echo -e "${GREEN}Nginx 配置正常${NC}"
    else
        echo -e "${RED}Nginx 配置错误:${NC}"
        nginx -t 2>&1
    fi
}

# 启动/重载 Nginx
reload_nginx() {
    if pgrep nginx >/dev/null; then
        if nginx -s reload 2>/dev/null; then
            echo -e "${GREEN}Nginx 重载成功${NC}"
        else
            echo -e "${RED}重载失败，尝试重启...${NC}"
            pkill nginx 2>/dev/null
            sleep 1
            nginx && echo -e "${GREEN}Nginx 启动成功${NC}" || echo -e "${RED}启动失败${NC}"
        fi
    else
        nginx && echo -e "${GREEN}Nginx 启动成功${NC}" || echo -e "${RED}启动失败${NC}"
    fi
}

# 添加反向代理
add_proxy() {
    echo -e "${YELLOW}=== 添加反向代理 ===${NC}"
    
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
    
    cat > "$CONF_FILE" <<EOF
# 反向代理: $FULL_DOMAIN -> 127.0.0.1:$PORT
# 生成时间: $(date)

server {
    listen 80;
    server_name $FULL_DOMAIN;
    return 301 https://\$host\$request_uri;
}

server {
    listen 443 ssl;
    server_name $FULL_DOMAIN;
    
    ssl_certificate $CERT;
    ssl_certificate_key $KEY;
    ssl_protocols TLSv1.2 TLSv1.3;
    
    location / {
        proxy_pass http://127.0.0.1:$PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
EOF
    
    echo -e "${GREEN}配置文件: $CONF_FILE${NC}"
    
    # 配置防火墙
    echo -e "${YELLOW}配置防火墙...${NC}"
    if command -v iptables &>/dev/null; then
        iptables -D INPUT -p tcp --dport $PORT -j DROP 2>/dev/null
        iptables -D INPUT -s 127.0.0.1 -p tcp --dport $PORT -j ACCEPT 2>/dev/null
        
        iptables -I INPUT -p tcp --dport $PORT -j DROP
        iptables -I INPUT -s 127.0.0.1 -p tcp --dport $PORT -j ACCEPT
        echo -e "${GREEN}端口 $PORT 已锁定${NC}"
    fi
    
    # 测试并重载
    if nginx -t 2>/dev/null; then
        reload_nginx
        echo -e "${GREEN}配置成功!${NC}"
        echo "访问: https://$FULL_DOMAIN"
        echo "后端: http://127.0.0.1:$PORT"
    else
        echo -e "${RED}配置错误，删除文件${NC}"
        rm -f "$CONF_FILE"
        nginx -t 2>&1
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
        echo ""
        echo "文件: $(basename "$conf")"
        echo "域名: $(grep 'server_name' "$conf" | head -1 | awk '{print $2}' | sed 's/;//')"
        echo "后端: $(grep 'proxy_pass' "$conf" | head -1 | awk '{print $2}' | sed 's/;//')"
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
            
            if [ -n "$PORT" ] && command -v iptables &>/dev/null; then
                iptables -D INPUT -p tcp --dport $PORT -j DROP 2>/dev/null
                iptables -D INPUT -s 127.0.0.1 -p tcp --dport $PORT -j ACCEPT 2>/dev/null
                echo "防火墙规则已清除"
            fi
            
            reload_nginx
        fi
    else
        echo -e "${RED}配置文件不存在${NC}"
    fi
}

# 主菜单
main() {
    # 先修复配置
    fix_nginx_now
    
    while true; do
        echo ""
        echo -e "${YELLOW}===== Alpine Nginx 管理器 ====="
        echo "1. 添加反向代理"
        echo "2. 查看配置"
        echo "3. 删除配置"
        echo "4. 重载 Nginx"
        echo "5. 修复 Nginx 配置"
        echo "0. 退出"
        echo -e "==================================${NC}"
        
        read -p "选择: " OPT
        
        case $OPT in
            1) add_proxy ;;
            2) list_configs ;;
            3) delete_config ;;
            4) reload_nginx ;;
            5) fix_nginx_now ;;
            0) echo "再见！"; exit 0 ;;
            *) echo -e "${RED}无效选项${NC}" ;;
        esac
    done
}

# 运行
main