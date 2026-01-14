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
                apk del nginx nginx-mod-* --purge 2>/dev/null
                rm -rf /etc/nginx /var/lib/nginx /var/log/nginx /run/nginx
            else
                apt-get remove --purge nginx* -y
                apt-get autoremove -y
                rm -rf /etc/nginx /var/lib/nginx /var/log/nginx /run/nginx
            fi
            echo -e "${GREEN}>>> Nginx已卸载${NC}"
            return 1
        else
            echo -e "${GREEN}使用现有Nginx安装${NC}"
            return 0
        fi
    fi
    
    return 1
}

# 清理冲突配置文件
cleanup_conflicts() {
    echo -e "${YELLOW}>>> 清理冲突配置文件...${NC}"
    
    # 删除可能导致冲突的默认配置
    rm -f /etc/nginx/conf.d/*.conf 2>/dev/null
    rm -f /etc/nginx/modules-enabled/* 2>/dev/null
    
    # 检查并删除包含stream指令的配置文件
    find /etc/nginx -name "*.conf" -type f -exec grep -l "stream {" {} \; 2>/dev/null | while read file; do
        echo -e "${YELLOW}删除可能冲突的文件: $file${NC}"
        mv "$file" "$file.backup.$(date +%Y%m%d%H%M%S)"
    done
    
    echo -e "${GREEN}>>> 冲突配置已清理${NC}"
}

# 安装Nginx（智能适配系统）
install_nginx() {
    echo -e "${YELLOW}>>> 安装Nginx...${NC}"
    
    if [ "$SYSTEM" = "alpine" ]; then
        # Alpine安装
        echo -e "${BLUE}正在更新Alpine软件包...${NC}"
        apk update
        
        echo -e "${BLUE}安装Nginx及相关模块...${NC}"
        # 先安装基础nginx
        apk add nginx
        
        # 安装常用模块（不包括可能冲突的stream模块）
        apk add nginx-mod-http-headers-more nginx-mod-http-lua \
                nginx-mod-http-set-misc
        
        # Alpine需要创建运行目录
        mkdir -p /run/nginx
        
    else
        # Debian/Ubuntu安装
        echo -e "${BLUE}正在更新APT包列表...${NC}"
        apt-get update
        
        echo -e "${BLUE}安装Nginx...${NC}"
        apt-get install nginx -y
        
        # 安装常用模块
        apt-get install nginx-common nginx-core -y
    fi
    
    # 验证安装
    if command -v nginx &> /dev/null; then
        echo -e "${GREEN}Nginx版本:${NC}"
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
    
    # 清理可能冲突的配置
    cleanup_conflicts
    
    # 创建必要的目录结构
    mkdir -p /etc/nginx/{sites-available,sites-enabled,ssl,conf.d}
    mkdir -p /var/log/nginx
    mkdir -p /var/www/html
    mkdir -p /run/nginx
    
    # 简化Nginx配置 - 只包含基本HTTP模块
    cat > /etc/nginx/nginx.conf << 'EOF'
user nginx;
worker_processes auto;
pid /run/nginx/nginx.pid;

# 只加载必要的模块
load_module modules/ngx_http_headers_more_filter_module.so;
load_module modules/ngx_http_lua_module.so;
load_module modules/ngx_http_set_misc_module.so;

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
    
    # 创建简单的默认页面
    cat > /var/www/html/index.html << 'EOF'
<!DOCTYPE html>
<html>
<head>
    <title>Nginx Proxy Ready</title>
    <style>
        body { font-family: Arial, sans-serif; text-align: center; padding: 50px; }
        h1 { color: #333; }
        .status { color: green; font-weight: bold; }
        .info { margin: 20px auto; max-width: 600px; text-align: left; }
    </style>
</head>
<body>
    <h1>🚀 Nginx反向代理已就绪</h1>
    <p class="status">状态: 运行正常</p>
    <div class="info">
        <p><strong>服务器时间:</strong> <span id="datetime"></span></p>
        <p><strong>服务器信息:</strong> <span id="serverinfo"></span></p>
    </div>
    <script>
        document.getElementById('datetime').textContent = new Date().toLocaleString();
        document.getElementById('serverinfo').textContent = navigator.userAgent;
    </script>
</body>
</html>
EOF
    
    # 创建空的conf.d目录文件
    touch /etc/nginx/conf.d/default.conf
    
    echo -e "${GREEN}>>> Nginx基础配置完成${NC}"
}

# 启动Nginx服务
start_nginx() {
    echo -e "${YELLOW}>>> 启动Nginx服务...${NC}"
    
    # 先停止可能运行的服务
    if pgrep nginx > /dev/null; then
        echo -e "${YELLOW}停止运行的Nginx进程...${NC}"
        pkill nginx 2>/dev/null
        sleep 2
    fi
    
    # 测试配置
    echo -e "${YELLOW}测试Nginx配置...${NC}"
    if nginx -t; then
        echo -e "${GREEN}配置测试通过${NC}"
        
        # 启动Nginx
        if [ "$SYSTEM" = "alpine" ]; then
            rc-service nginx start 2>/dev/null || nginx
        else
            systemctl start nginx 2>/dev/null || nginx
        fi
        
        sleep 2
        
        # 检查是否启动成功
        if pgrep nginx > /dev/null; then
            echo -e "${GREEN}>>> Nginx启动成功${NC}"
            
            # 显示监听的端口
            echo -e "${BLUE}Nginx监听端口:${NC}"
            netstat -tulpn | grep nginx | awk '{print "  " $4}' || echo "  未检测到监听端口"
            
            return 0
        else
            echo -e "${RED}>>> Nginx启动失败，检查错误日志: /var/log/nginx/error.log${NC}"
            return 1
        fi
    else
        echo -e "${RED}>>> Nginx配置测试失败${NC}"
        echo -e "${YELLOW}请检查配置: /etc/nginx/nginx.conf${NC}"
        return 1
    fi
}

# 设置开机自启
enable_autostart() {
    echo -e "${YELLOW}>>> 设置Nginx开机自启...${NC}"
    
    if [ "$SYSTEM" = "alpine" ]; then
        # Alpine使用openrc
        rc-update add nginx default 2>/dev/null || true
    else
        # Debian使用systemd
        systemctl enable nginx 2>/dev/null || true
    fi
    
    echo -e "${GREEN}>>> 开机自启设置完成${NC}"
}

# 检查Nginx状态
check_nginx_status() {
    echo -e "${YELLOW}>>> Nginx状态检查${NC}"
    
    if command -v nginx &> /dev/null; then
        echo -e "${GREEN}Nginx已安装:${NC}"
        nginx -v 2>&1
    else
        echo -e "${RED}Nginx未安装${NC}"
        return
    fi
    
    if pgrep nginx > /dev/null; then
        echo -e "${GREEN}Nginx进程:${NC}"
        ps aux | grep nginx | grep -v grep
        
        echo -e "${GREEN}监听端口:${NC}"
        netstat -tulpn 2>/dev/null | grep nginx || ss -tulpn 2>/dev/null | grep nginx
        
        echo -e "${GREEN}最近错误日志:${NC}"
        tail -5 /var/log/nginx/error.log 2>/dev/null || echo "  无错误日志"
    else
        echo -e "${RED}Nginx未运行${NC}"
    fi
}

# 创建反向代理配置
create_reverse_proxy() {
    echo -e "${YELLOW}>>> 创建反向代理配置${NC}"
    
    # 获取用户输入
    echo -n "请输入子域名 (例如: api.example.com): "
    read SUBDOMAIN
    
    echo -n "请输入后端服务端口 (例如: 3000): "
    read BACKEND_PORT
    
    echo -n "是否启用WebSocket支持？(y/n): "
    read -n 1 WEBSOCKET_CHOICE
    echo
    [[ $WEBSOCKET_CHOICE =~ ^[Yy]$ ]] && WEBSOCKET=true || WEBSOCKET=false
    
    # 验证输入
    if [ -z "$SUBDOMAIN" ] || [ -z "$BACKEND_PORT" ]; then
        echo -e "${RED}错误: 子域名和端口不能为空${NC}"
        return 1
    fi
    
    # 检查端口是否为数字
    if ! [[ "$BACKEND_PORT" =~ ^[0-9]+$ ]]; then
        echo -e "${RED}错误: 端口必须是数字${NC}"
        return 1
    fi
    
    # 创建SSL证书路径（假设证书已存在）
    SSL_CERT="/etc/nginx/ssl/certs/${SUBDOMAIN}/fullchain.pem"
    SSL_KEY="/etc/nginx/ssl/private/${SUBDOMAIN}/key.pem"
    
    # 检查证书是否存在
    if [ ! -f "$SSL_CERT" ] || [ ! -f "$SSL_KEY" ]; then
        echo -e "${YELLOW}警告: 未找到SSL证书，将使用HTTP模式${NC}"
        echo -e "${YELLOW}证书路径应为:${NC}"
        echo -e "  证书: $SSL_CERT"
        echo -e "  私钥: $SSL_KEY"
        echo -e "${YELLOW}您可以在添加代理后手动配置SSL证书${NC}"
        USE_SSL=false
    else
        USE_SSL=true
        echo -e "${GREEN}找到SSL证书${NC}"
    fi
    
    # 生成Nginx配置
    CONFIG_FILE="/etc/nginx/sites-available/${SUBDOMAIN}.conf"
    
    echo -e "${BLUE}生成配置文件: $CONFIG_FILE${NC}"
    
    # 创建HTTP配置
    cat > "$CONFIG_FILE" << EOF
# 反向代理配置: $SUBDOMAIN -> 127.0.0.1:$BACKEND_PORT
# 生成时间: $(date)
# 配置类型: $( [ "$USE_SSL" = true ] && echo "HTTPS" || echo "HTTP" )

# HTTP服务器配置
server {
    listen 80;
    listen [::]:80;
    server_name $SUBDOMAIN;
    
    # 安全头
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
EOF
    
    # 如果启用SSL，添加重定向
    if [ "$USE_SSL" = true ]; then
        cat >> "$CONFIG_FILE" << EOF
    
    # 强制HTTPS重定向
    return 301 https://\$server_name\$request_uri;
}
EOF
    else
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
    }
}
EOF
    fi
    
    # 如果启用SSL，添加HTTPS配置块
    if [ "$USE_SSL" = true ]; then
        cat >> "$CONFIG_FILE" << EOF

# HTTPS服务器配置
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
        
        # 保持活动连接
        proxy_set_header Connection "";
    }
EOF
    
    # 如果启用WebSocket，添加相关配置
    if [ "$WEBSOCKET" = true ]; then
        cat >> "$CONFIG_FILE" << EOF
    
    # WebSocket支持
    location /ws {
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
    
    # WebSocket代理设置（通用）
    location ~ ^/(socket\.io|websocket|wss?)/ {
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
    
    # 关闭HTTPS服务器块
    echo "}" >> "$CONFIG_FILE"
    
    fi
    
    # 创建符号链接启用站点
    mkdir -p /etc/nginx/sites-enabled
    ln -sf "$CONFIG_FILE" "/etc/nginx/sites-enabled/${SUBDOMAIN}.conf"
    
    echo -e "\n${GREEN}✅ 反向代理配置已创建${NC}"
    echo -e "${BLUE}配置文件:${NC} $CONFIG_FILE"
    echo -e "${BLUE}域名:${NC} $SUBDOMAIN"
    echo -e "${BLUE}后端端口:${NC} 127.0.0.1:$BACKEND_PORT"
    echo -e "${BLUE}SSL:${NC} $( [ "$USE_SSL" = true ] && echo '启用' || echo '未启用' )"
    echo -e "${BLUE}WebSocket:${NC} $( [ "$WEBSOCKET" = true ] && echo '启用' || echo '未启用' )"
    
    if [ "$USE_SSL" = false ]; then
        echo -e "${YELLOW}提示: 如需启用HTTPS，请将证书放置在:${NC}"
        echo -e "  $SSL_CERT"
        echo -e "  $SSL_KEY"
    fi
}

# 重载Nginx配置
reload_nginx() {
    echo -e "${YELLOW}>>> 重载Nginx配置...${NC}"
    
    # 先测试配置
    echo -e "${YELLOW}测试Nginx配置...${NC}"
    if nginx -t 2>&1; then
        echo -e "${GREEN}配置测试通过${NC}"
        
        # 重载配置
        if [ "$SYSTEM" = "alpine" ]; then
            rc-service nginx reload 2>/dev/null || nginx -s reload
        else
            systemctl reload nginx 2>/dev/null || nginx -s reload
        fi
        
        if [ $? -eq 0 ]; then
            echo -e "${GREEN}✅ Nginx配置重载成功${NC}"
            
            # 显示当前配置摘要
            echo -e "\n${BLUE}================ 配置摘要 ================${NC}"
            echo -e "${GREEN}✅ Nginx运行状态:${NC} $(pgrep nginx > /dev/null && echo '运行中' || echo '未运行')"
            
            echo -e "${GREEN}✅ 启用的代理站点:${NC}"
            if [ -d /etc/nginx/sites-enabled ]; then
                ls -1 /etc/nginx/sites-enabled/*.conf 2>/dev/null | while read file; do
                    domain=$(grep -h "server_name" "$file" | head -1 | awk '{print $2}' | tr -d ';')
                    echo "  - $domain"
                done
            else
                echo "  无"
            fi
            
            echo -e "${GREEN}✅ 监听端口:${NC}"
            (netstat -tulpn 2>/dev/null || ss -tulpn 2>/dev/null) | grep -E ":80\>|:443\>" | awk '{print "  " $4}'
            
            echo -e "${BLUE}========================================${NC}"
            return 0
        else
            echo -e "${RED}❌ Nginx重载失败${NC}"
            return 1
        fi
    else
        echo -e "${RED}❌ Nginx配置测试失败${NC}"
        echo -e "${YELLOW}请检查配置错误:${NC}"
        nginx -t 2>&1 | grep -A5 -B5 "error"
        return 1
    fi
}

# 显示当前配置
show_config() {
    echo -e "${YELLOW}>>> 当前Nginx配置状态${NC}"
    
    echo -e "${BLUE}1. Nginx基本信息:${NC}"
    nginx -v 2>&1
    
    echo -e "${BLUE}2. 启用的站点配置:${NC}"
    if [ -d /etc/nginx/sites-enabled ]; then
        for conf in /etc/nginx/sites-enabled/*.conf; do
            if [ -f "$conf" ]; then
                echo -e "\n${GREEN}配置文件: $conf${NC}"
                domain=$(grep "server_name" "$conf" | head -1 | awk '{print $2}' | tr -d ';')
                port=$(grep "listen" "$conf" | head -1 | awk '{print $2}' | tr -d ';')
                echo "  域名: $domain"
                echo "  监听端口: $port"
            fi
        done
    else
        echo "  无启用的站点"
    fi
    
    echo -e "${BLUE}3. 运行状态:${NC}"
    if pgrep nginx > /dev/null; then
        echo -e "  Nginx进程:"
        ps aux | grep nginx | grep -v grep | awk '{print "    PID:" $2 " " $11}'
    else
        echo "  Nginx未运行"
    fi
}

# 备份配置
backup_config() {
    BACKUP_DIR="/etc/nginx/backup_$(date +%Y%m%d_%H%M%S)"
    
    echo -e "${YELLOW}>>> 备份Nginx配置...${NC}"
    
    mkdir -p "$BACKUP_DIR"
    
    # 备份主要配置文件
    cp -r /etc/nginx/nginx.conf "$BACKUP_DIR/"
    cp -r /etc/nginx/sites-available "$BACKUP_DIR/" 2>/dev/null
    cp -r /etc/nginx/sites-enabled "$BACKUP_DIR/" 2>/dev/null
    cp -r /etc/nginx/conf.d "$BACKUP_DIR/" 2>/dev/null
    
    echo -e "${GREEN}✅ 配置已备份到: $BACKUP_DIR${NC}"
    echo -e "备份内容:"
    ls -la "$BACKUP_DIR/"
}

# 修复Nginx配置
fix_nginx_config() {
    echo -e "${YELLOW}>>> 修复Nginx配置...${NC}"
    
    # 1. 清理冲突配置
    cleanup_conflicts
    
    # 2. 创建最小化配置
    cat > /etc/nginx/nginx.conf << 'EOF'
user nginx;
worker_processes auto;
pid /run/nginx/nginx.pid;

events {
    worker_connections 1024;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    
    sendfile on;
    keepalive_timeout 65;
    
    access_log /var/log/nginx/access.log;
    error_log /var/log/nginx/error.log;
    
    include /etc/nginx/conf.d/*.conf;
    include /etc/nginx/sites-enabled/*;
}
EOF
    
    # 3. 确保目录存在
    mkdir -p /etc/nginx/{sites-available,sites-enabled,conf.d}
    mkdir -p /run/nginx
    
    echo -e "${GREEN}✅ 配置已修复，请重启Nginx${NC}"
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
    echo -e "${GREEN}7.${NC} 修复Nginx配置问题"
    echo -e "${GREEN}8.${NC} 退出"
    echo -e "${BLUE}========================================${NC}"
    echo -n "请选择操作 [1-8]: "
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
                echo -e "\n${BLUE}=== 选项1: 初始化安装/重新安装Nginx ===${NC}"
                if check_nginx; then
                    echo -e "${YELLOW}使用现有Nginx安装${NC}"
                else
                    install_nginx
                    configure_nginx
                    start_nginx
                    enable_autostart
                fi
                ;;
            2)
                echo -e "\n${BLUE}=== 选项2: 添加新的反向代理 ===${NC}"
                create_reverse_proxy
                echo -e "\n${YELLOW}是否现在重载Nginx配置使更改生效？(y/n):${NC}"
                read -n 1 reload_choice
                echo
                if [[ $reload_choice =~ ^[Yy]$ ]]; then
                    reload_nginx
                fi
                ;;
            3)
                echo -e "\n${BLUE}=== 选项3: 重载Nginx配置 ===${NC}"
                reload_nginx
                ;;
            4)
                echo -e "\n${BLUE}=== 选项4: 查看Nginx状态 ===${NC}"
                check_nginx_status
                ;;
            5)
                echo -e "\n${BLUE}=== 选项5: 查看当前配置 ===${NC}"
                show_config
                ;;
            6)
                echo -e "\n${BLUE}=== 选项6: 备份当前配置 ===${NC}"
                backup_config
                ;;
            7)
                echo -e "\n${BLUE}=== 选项7: 修复Nginx配置问题 ===${NC}"
                fix_nginx_config
                ;;
            8)
                echo -e "${GREEN}退出脚本${NC}"
                exit 0
                ;;
            *)
                echo -e "${RED}无效选择，请重新输入${NC}"
                ;;
        esac
        
        echo -e "\n${YELLOW}按Enter继续...${NC}"
        read
    done
}

# 脚本开始
clear
echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}   Nginx智能反向代理配置脚本${NC}"
echo -e "${BLUE}========================================${NC}"
echo -e "系统: $(cat /etc/os-release | grep PRETTY_NAME | cut -d= -f2 | tr -d '\"')"
echo -e "内核: $(uname -r)"
echo -e "主机名: $(hostname)"
echo -e "${BLUE}========================================${NC}"

main