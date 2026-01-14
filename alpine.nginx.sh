#!/bin/bash

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 系统检测
detect_system() {
    if [ -f /etc/alpine-release ]; then
        SYSTEM="alpine"
        echo -e "${BLUE}检测到系统: Alpine Linux${NC}"
    elif [ -f /etc/debian_version ]; then
        SYSTEM="debian"
        echo -e "${BLUE}检测到系统: Debian/Ubuntu${NC}"
    else
        echo -e "${RED}不支持的系统！脚本仅支持Alpine和Debian系统${NC}"
        exit 1
    fi
}

# 检查并清除现有Nginx
check_nginx() {
    echo -e "${YELLOW}>>> 检查现有Nginx安装...${NC}"
    
    if command -v nginx &> /dev/null; then
        echo -e "${YELLOW}检测到已安装的Nginx${NC}"
        nginx -v
        
        read -p "是否清除现有Nginx并重新安装？(y/n): " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            echo -e "${YELLOW}>>> 正在卸载Nginx...${NC}"
            
            if [ "$SYSTEM" = "alpine" ]; then
                apk del nginx nginx-mod-http-* --purge 2>/dev/null
                rm -rf /etc/nginx /var/lib/nginx /var/log/nginx
            else
                apt-get remove --purge nginx* -y
                apt-get autoremove -y
                rm -rf /etc/nginx /var/lib/nginx /var/log/nginx
            fi
        else
            echo -e "${GREEN}使用现有Nginx安装${NC}"
            return 0
        fi
    fi
    
    return 1
}

# 安装Nginx（智能适配系统）
install_nginx() {
    echo -e "${YELLOW}>>> 安装Nginx...${NC}"
    
    if [ "$SYSTEM" = "alpine" ]; then
        # Alpine安装
        echo -e "${BLUE}正在更新Alpine软件包...${NC}"
        apk update
        
        echo -e "${BLUE}安装Nginx及相关模块...${NC}"
        apk add nginx nginx-mod-http-headers-more nginx-mod-http-lua \
                nginx-mod-http-set-misc nginx-mod-stream
        
        # Alpine需要创建运行目录
        mkdir -p /run/nginx
        
    else
        # Debian/Ubuntu安装
        echo -e "${BLUE}正在更新APT包列表...${NC}"
        apt-get update
        
        echo -e "${BLUE}安装Nginx...${NC}"
        apt-get install nginx -y
        
        # 安装常用模块
        apt-get install nginx-extras -y 2>/dev/null || echo "nginx-extras不可用，使用标准模块"
    fi
    
    # 验证安装
    if command -v nginx &> /dev/null; then
        nginx -v
        echo -e "${GREEN}>>> Nginx安装成功${NC}"
        return 0
    else
        echo -e "${RED}>>> Nginx安装失败${NC}"
        return 1
    fi
}

# 配置Nginx基础设置
configure_nginx() {
    echo -e "${YELLOW}>>> 配置Nginx...${NC}"
    
    # 备份原配置
    if [ -f /etc/nginx/nginx.conf ]; then
        cp /etc/nginx/nginx.conf /etc/nginx/nginx.conf.backup.$(date +%Y%m%d%H%M%S)
    fi
    
    # 创建必要的目录结构
    mkdir -p /etc/nginx/{sites-available,sites-enabled,ssl,conf.d}
    mkdir -p /var/log/nginx
    mkdir -p /var/www/html
    
    # 优化Nginx配置
    cat > /etc/nginx/nginx.conf << 'EOF'
user nginx;
worker_processes auto;
pid /run/nginx/nginx.pid;

events {
    worker_connections 1024;
    multi_accept on;
    use epoll;
}

http {
    # 基础设置
    sendfile on;
    tcp_nopush on;
    tcp_nodelay on;
    keepalive_timeout 65;
    types_hash_max_size 2048;
    server_tokens off;
    
    # MIME类型
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    
    # 日志格式
    log_format main '$remote_addr - $remote_user [$time_local] "$request" '
                    '$status $body_bytes_sent "$http_referer" '
                    '"$http_user_agent" "$http_x_forwarded_for"';
    
    access_log /var/log/nginx/access.log main;
    error_log /var/log/nginx/error.log warn;
    
    # Gzip压缩
    gzip on;
    gzip_vary on;
    gzip_min_length 1024;
    gzip_types text/plain text/css text/xml text/javascript 
               application/javascript application/xml+rss 
               application/json image/svg+xml;
    
    # 限制
    client_max_body_size 100M;
    client_body_timeout 12;
    client_header_timeout 12;
    reset_timedout_connection on;
    
    # 包含其他配置
    include /etc/nginx/conf.d/*.conf;
    include /etc/nginx/sites-enabled/*;
}
EOF
    
    # 创建默认页面
    cat > /var/www/html/index.html << 'EOF'
<!DOCTYPE html>
<html>
<head>
    <title>Nginx Proxy Ready</title>
    <style>
        body { font-family: Arial, sans-serif; text-align: center; padding: 50px; }
        h1 { color: #333; }
        .status { color: green; font-weight: bold; }
    </style>
</head>
<body>
    <h1>🚀 Nginx反向代理已就绪</h1>
    <p class="status">状态: 运行正常</p>
    <p>服务器时间: <span id="datetime"></span></p>
    <script>
        document.getElementById('datetime').textContent = new Date().toLocaleString();
    </script>
</body>
</html>
EOF
    
    echo -e "${GREEN}>>> Nginx基础配置完成${NC}"
}

# 设置开机自启
enable_autostart() {
    echo -e "${YELLOW}>>> 设置Nginx开机自启...${NC}"
    
    if [ "$SYSTEM" = "alpine" ]; then
        # Alpine使用openrc
        rc-update add nginx default 2>/dev/null
        rc-service nginx start
    else
        # Debian使用systemd
        systemctl enable nginx
        systemctl start nginx
    fi
    
    # 检查运行状态
    if pgrep nginx > /dev/null; then
        echo -e "${GREEN}>>> Nginx已启动并设置开机自启${NC}"
    else
        echo -e "${YELLOW}>>> Nginx未运行，尝试手动启动...${NC}"
        nginx
    fi
}

# 创建反向代理配置
create_reverse_proxy() {
    echo -e "${YELLOW}>>> 创建反向代理配置${NC}"
    
    # 获取用户输入
    read -p "请输入子域名 (例如: api.example.com): " SUBDOMAIN
    read -p "请输入后端服务端口 (例如: 3000): " BACKEND_PORT
    read -p "是否启用WebSocket支持？(y/n): " -n 1 -r
    echo
    [[ $REPLY =~ ^[Yy]$ ]] && WEBSOCKET=true || WEBSOCKET=false
    
    # 验证输入
    if [ -z "$SUBDOMAIN" ] || [ -z "$BACKEND_PORT" ]; then
        echo -e "${RED}错误: 子域名和端口不能为空${NC}"
        return 1
    fi
    
    # 创建SSL证书路径（假设证书已存在）
    SSL_CERT="/etc/nginx/ssl/certs/${SUBDOMAIN}/fullchain.pem"
    SSL_KEY="/etc/nginx/ssl/private/${SUBDOMAIN}/key.pem"
    
    # 检查证书是否存在
    if [ ! -f "$SSL_CERT" ] || [ ! -f "$SSL_KEY" ]; then
        echo -e "${YELLOW}警告: 未找到SSL证书，将使用HTTP模式${NC}"
        echo -e "${YELLOW}证书路径应为:${NC}"
        echo -e "证书: $SSL_CERT"
        echo -e "私钥: $SSL_KEY"
        USE_SSL=false
    else
        USE_SSL=true
        echo -e "${GREEN}找到SSL证书${NC}"
    fi
    
    # 生成Nginx配置
    CONFIG_FILE="/etc/nginx/sites-available/${SUBDOMAIN}.conf"
    
    cat > "$CONFIG_FILE" << EOF
# 反向代理配置: $SUBDOMAIN -> 127.0.0.1:$BACKEND_PORT
# 生成时间: $(date)

# HTTP重定向到HTTPS（如果启用SSL）
server {
    listen 80;
    listen [::]:80;
    server_name $SUBDOMAIN;
    
    # 安全头
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    
    # 强制HTTPS（如果启用SSL）
    $([ "$USE_SSL" = true ] && echo 'return 301 https://\$server_name\$request_uri;')
    
    # 如果未启用SSL，直接代理
    $([ "$USE_SSL" != true ] && echo "location / {
        proxy_pass http://127.0.0.1:$BACKEND_PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }")
}
EOF
    
    # 如果启用SSL，添加HTTPS配置块
    if [ "$USE_SSL" = true ]; then
        cat >> "$CONFIG_FILE" << EOF

# HTTPS服务器
server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name $SUBDOMAIN;
    
    # SSL证书
    ssl_certificate $SSL_CERT;
    ssl_certificate_key $SSL_KEY;
    
    # SSL优化
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers 'ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384';
    ssl_prefer_server_ciphers off;
    
    # 安全头
    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    
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
        
        # 禁用缓存（可根据需要调整）
        proxy_no_cache 1;
        proxy_cache_bypass 1;
        
        # 保持活动连接
        proxy_set_header Connection "";
    }
EOF
    
    # 如果启用WebSocket，添加相关配置
    if [ "$WEBSOCKET" = true ]; then
        cat >> "$CONFIG_FILE" << EOF
    
    # WebSocket支持
    location /websocket {
        proxy_pass http://127.0.0.1:$BACKEND_PORT;
        proxy_http_version 1.1;
        
        # WebSocket特定头部
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "Upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        
        # 连接超时设置
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
EOF
    fi
    
    # 关闭HTTPS服务器块
    echo "}" >> "$CONFIG_FILE"
    
    fi
    
    # 创建符号链接启用站点
    ln -sf "$CONFIG_FILE" "/etc/nginx/sites-enabled/${SUBDOMAIN}.conf"
    
    echo -e "${GREEN}>>> 反向代理配置已创建:${NC}"
    echo -e "${BLUE}配置文件:${NC} $CONFIG_FILE"
    echo -e "${BLUE}域名:${NC} $SUBDOMAIN"
    echo -e "${BLUE}后端端口:${NC} $BACKEND_PORT"
    echo -e "${BLUE}SSL:${NC} $USE_SSL"
    echo -e "${BLUE}WebSocket:${NC} $WEBSOCKET"
}

# 测试并重载Nginx
reload_nginx() {
    echo -e "${YELLOW}>>> 测试Nginx配置...${NC}"
    
    if nginx -t; then
        echo -e "${GREEN}>>> 配置测试通过${NC}"
        
        echo -e "${YELLOW}>>> 重载Nginx配置...${NC}"
        if [ "$SYSTEM" = "alpine" ]; then
            rc-service nginx reload 2>/dev/null || nginx -s reload
        else
            systemctl reload nginx 2>/dev/null || nginx -s reload
        fi
        
        if [ $? -eq 0 ]; then
            echo -e "${GREEN}>>> Nginx配置重载成功${NC}"
            
            # 显示配置摘要
            echo -e "\n${BLUE}================ 配置摘要 ================${NC}"
            echo -e "${GREEN}✅ Nginx运行状态:${NC} $(pgrep nginx > /dev/null && echo '运行中' || echo '未运行')"
            echo -e "${GREEN}✅ 监听端口:${NC}"
            netstat -tulpn | grep nginx | grep -E ':(80|443)' | awk '{print "  " $4}'
            echo -e "${GREEN}✅ 启用的站点:${NC}"
            ls -1 /etc/nginx/sites-enabled/ 2>/dev/null || echo "  无"
            echo -e "${BLUE}========================================${NC}"
        else
            echo -e "${RED}>>> Nginx重载失败${NC}"
        fi
    else
        echo -e "${RED}>>> Nginx配置测试失败，请检查配置${NC}"
        return 1
    fi
}

# 显示菜单
show_menu() {
    echo -e "\n${BLUE}========== Nginx智能反向代理管理 ==========${NC}"
    echo -e "${GREEN}1.${NC} 初始化安装/重新安装Nginx"
    echo -e "${GREEN}2.${NC} 添加新的反向代理"
    echo -e "${GREEN}3.${NC} 重载Nginx配置"
    echo -e "${GREEN}4.${NC} 查看Nginx状态"
    echo -e "${GREEN}5.${NC} 查看当前配置"
    echo -e "${GREEN}6.${NC} 备份当前配置"
    echo -e "${GREEN}7.${NC} 退出"
    echo -e "${BLUE}========================================${NC}"
    echo -n "请选择操作 [1-7]: "
}

# 主函数
main() {
    # 检查root权限
    if [ "$EUID" -ne 0 ]; then 
        echo -e "${RED}请使用root权限运行此脚本${NC}"
        exit 1
    fi
    
    # 检测系统
    detect_system
    
    while true; do
        show_menu
        read choice
        
        case $choice in
            1)
                check_nginx
                install_nginx
                configure_nginx
                enable_autostart
                ;;
            2)
                create_reverse_proxy
                reload_nginx
                ;;
            3)
                reload_nginx
                ;;
            4)
                echo -e "${YELLOW}>>> Nginx状态:${NC}"
                if [ "$SYSTEM" = "alpine" ]; then
                    rc-service nginx status 2>/dev/null || ps aux | grep nginx
                else
                    systemctl status nginx --no-pager -l
                fi
                ;;
            5)
                echo -e "${YELLOW}>>> 当前启用的代理配置:${NC}"
                grep -r "server_name" /etc/nginx/sites-enabled/ 2>/dev/null || echo "未找到配置"
                echo -e "\n${YELLOW}>>> 监听端口:${NC}"
                netstat -tulpn | grep nginx
                ;;
            6)
                BACKUP_DIR="/etc/nginx/backup_$(date +%Y%m%d_%H%M%S)"
                mkdir -p "$BACKUP_DIR"
                cp -r /etc/nginx/* "$BACKUP_DIR/"
                echo -e "${GREEN}>>> 配置已备份到: $BACKUP_DIR${NC}"
                ;;
            7)
                echo -e "${GREEN}退出脚本${NC}"
                exit 0
                ;;
            *)
                echo -e "${RED}无效选择，请重新输入${NC}"
                ;;
        esac
        
        echo -e "\n按Enter继续..."
        read
    done
}

# 脚本开始
clear
echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}   Nginx智能反向代理配置脚本${NC}"
echo -e "${BLUE}========================================${NC}"
echo -e "系统: $(uname -a)"
echo -e "主机名: $(hostname)"
echo -e "IP地址: $(hostname -I 2>/dev/null || ip addr show | grep -oP 'inet \K[\d.]+' | grep -v '127.0.0.1' | head -1)"
echo -e "${BLUE}========================================${NC}"

main