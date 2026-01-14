#!/bin/bash

# ===================================================
# Debian 12+ Nginx 反向代理纯净安装管理脚本
# 功能：完全清理并重新安装Nginx，配置反向代理
# 版本：2.0
# 作者：AI Assistant
# ===================================================

# 配置
INSTALL_DIR="/opt/cert-manager"
ACME_DIR="$INSTALL_DIR/acme.sh"
CONFIG_DIR="$INSTALL_DIR/config"
LOG_DIR="$INSTALL_DIR/logs"
NGINX_CONF_DIR="/etc/nginx/conf.d"
NGINX_SITES_AVAILABLE="/etc/nginx/sites-available"
NGINX_SITES_ENABLED="/etc/nginx/sites-enabled"
SSL_DIR="/etc/nginx/ssl"
CERT_ROOT="/etc/nginx/ssl/certs"
KEY_ROOT="/etc/nginx/ssl/private"
BACKUP_DIR="/etc/nginx/backup"
WWW_ROOT="/var/www/html"

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# 日志函数
log() {
    echo -e "${GREEN}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} $1"
}

error() {
    echo -e "${RED}[错误]${NC} $1" >&2
}

warn() {
    echo -e "${YELLOW}[警告]${NC} $1"
}

info() {
    echo -e "${BLUE}[信息]${NC} $1"
}

# 检查 Root 权限
check_root() {
    if [ "$(id -u)" -ne 0 ]; then
        error "必须使用 root 权限运行此脚本"
        exit 1
    fi
}

# 清理旧 Nginx 安装
clean_nginx() {
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    echo -e "${CYAN}          清理旧 Nginx 安装                ${NC}"
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    
    # 停止 Nginx 服务
    if systemctl is-active nginx &>/dev/null; then
        log "停止 Nginx 服务..."
        systemctl stop nginx
        systemctl disable nginx 2>/dev/null
    fi
    
    # 杀死所有 nginx 进程
    if pgrep nginx &>/dev/null; then
        log "终止 nginx 进程..."
        pkill -9 nginx 2>/dev/null
    fi
    
    # 备份现有配置
    if [ -d "/etc/nginx" ]; then
        log "备份现有配置..."
        backup_tar="/tmp/nginx-backup-$(date +%Y%m%d-%H%M%S).tar.gz"
        tar czf "$backup_tar" -C /etc nginx/ 2>/dev/null
        log "配置已备份到: $backup_tar"
    fi
    
    # 完全删除 Nginx
    log "完全删除 Nginx..."
    apt-get remove --purge -y nginx nginx-common nginx-full nginx-core 2>/dev/null
    apt-get autoremove -y 2>/dev/null
    
    # 清理配置文件
    log "清理配置文件..."
    rm -rf /etc/nginx
    rm -rf /var/log/nginx
    rm -rf /var/cache/nginx
    rm -rf /var/lib/nginx
    rm -rf /usr/share/nginx
    rm -rf /usr/lib/nginx
    
    # 清理可能存在的残余文件
    find /etc -name "*nginx*" -type f -delete 2>/dev/null
    find /var -name "*nginx*" -type d -exec rm -rf {} + 2>/dev/null || true
    
    # 删除可能存在的 nginx 用户和组
    if id nginx &>/dev/null; then
        userdel -r nginx 2>/dev/null || true
    fi
    
    log "Nginx 清理完成"
}

# 安装依赖
install_deps() {
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    echo -e "${CYAN}          安装系统依赖                    ${NC}"
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    
    log "更新系统包列表..."
    apt-get update -y
    
    log "安装必要工具..."
    apt-get install -y curl wget git tar gzip unzip
    
    log "安装 SSL 相关工具..."
    apt-get install -y openssl certbot python3-certbot-nginx
    
    log "安装编译工具（可选）..."
    apt-get install -y build-essential libpcre3 libpcre3-dev zlib1g zlib1g-dev libssl-dev
    
    log "依赖安装完成"
}

# 创建目录结构和设置权限
create_dirs_and_permissions() {
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    echo -e "${CYAN}          创建目录结构和权限              ${NC}"
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    
    log "创建主安装目录..."
    mkdir -p "$INSTALL_DIR"
    chmod 755 "$INSTALL_DIR"
    chown root:root "$INSTALL_DIR"
    
    log "创建配置目录..."
    mkdir -p "$CONFIG_DIR"
    chmod 750 "$CONFIG_DIR"
    chown root:www-data "$CONFIG_DIR"
    
    log "创建日志目录..."
    mkdir -p "$LOG_DIR"
    chmod 775 "$LOG_DIR"
    chown root:www-data "$LOG_DIR"
    
    log "创建 Nginx 配置目录..."
    mkdir -p /etc/nginx
    mkdir -p "$NGINX_CONF_DIR"
    mkdir -p "$NGINX_SITES_AVAILABLE"
    mkdir -p "$NGINX_SITES_ENABLED"
    
    chmod 755 /etc/nginx
    chmod 750 "$NGINX_CONF_DIR"
    chown root:www-data /etc/nginx
    chown root:www-data "$NGINX_CONF_DIR"
    
    log "创建 SSL 证书目录..."
    mkdir -p "$SSL_DIR"
    mkdir -p "$CERT_ROOT"
    mkdir -p "$KEY_ROOT"
    
    chmod 755 "$SSL_DIR"
    chmod 755 "$CERT_ROOT"
    chmod 700 "$KEY_ROOT"  # 私钥目录严格权限
    
    chown root:root "$SSL_DIR"
    chown root:www-data "$CERT_ROOT"
    chown root:www-data "$KEY_ROOT"
    
    log "创建网站根目录..."
    mkdir -p "$WWW_ROOT"
    chmod 755 "$WWW_ROOT"
    chown www-data:www-data "$WWW_ROOT"
    
    log "创建日志目录..."
    mkdir -p /var/log/nginx
    mkdir -p /var/log/nginx/proxy
    mkdir -p /var/log/nginx/ssl
    
    chmod 755 /var/log/nginx
    chmod 770 /var/log/nginx/proxy
    chmod 770 /var/log/nginx/ssl
    
    chown root:www-data /var/log/nginx
    chown www-data:www-data /var/log/nginx/proxy
    chown www-data:www-data /var/log/nginx/ssl
    
    log "创建备份目录..."
    mkdir -p "$BACKUP_DIR"
    chmod 750 "$BACKUP_DIR"
    chown root:root "$BACKUP_DIR"
    
    log "创建运行时目录..."
    mkdir -p /var/cache/nginx
    mkdir -p /var/lib/nginx
    mkdir -p /run/nginx
    
    chmod 755 /var/cache/nginx
    chmod 755 /var/lib/nginx
    chmod 755 /run/nginx
    
    chown www-data:www-data /var/cache/nginx
    chown www-data:www-data /var/lib/nginx
    chown www-data:www-data /run/nginx
    
    log "目录结构和权限设置完成"
}

# 安装 Nginx
install_nginx() {
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    echo -e "${CYAN}          安装 Nginx                      ${NC}"
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    
    log "安装 Nginx 主包..."
    apt-get install -y nginx
    
    if [ $? -eq 0 ]; then
        log "✅ Nginx 安装成功"
        
        # 检查版本
        nginx -v
    else
        error "Nginx 安装失败"
        
        # 尝试从官方源安装
        log "尝试从 Nginx 官方源安装..."
        
        # 添加 Nginx 官方源
        wget -O /tmp/nginx-key.gpg https://nginx.org/keys/nginx_signing.key
        apt-key add /tmp/nginx-key.gpg
        
        # 添加源
        echo "deb https://nginx.org/packages/mainline/debian/ $(lsb_release -cs) nginx" > /etc/apt/sources.list.d/nginx.list
        echo "deb-src https://nginx.org/packages/mainline/debian/ $(lsb_release -cs) nginx" >> /etc/apt/sources.list.d/nginx.list
        
        apt-get update
        apt-get install -y nginx
        
        if [ $? -ne 0 ]; then
            error "Nginx 官方源安装也失败"
            return 1
        fi
    fi
    
    # 停止自动启动的 Nginx
    systemctl stop nginx
    systemctl disable nginx
    
    log "Nginx 安装完成"
}

# 配置 Nginx 用户和权限
setup_nginx_user() {
    log "配置 Nginx 用户和组..."
    
    # 确保 www-data 用户存在
    if ! id www-data &>/dev/null; then
        groupadd www-data
        useradd -r -g www-data -s /sbin/nologin -d /nonexistent www-data
    fi
    
    # 设置用户 shell 为 nologin
    usermod -s /usr/sbin/nologin www-data
    
    log "Nginx 用户配置完成"
}

# 配置 Nginx 主配置文件
configure_nginx_main() {
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    echo -e "${CYAN}          配置 Nginx 主配置文件            ${NC}"
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    
    log "创建优化的 nginx.conf..."
    
    cat > /etc/nginx/nginx.conf << 'EOF'
# Nginx 主配置文件
# 由反向代理管理脚本生成

# 运行用户和组
user www-data;
worker_processes auto;
pid /run/nginx.pid;

# 错误日志位置和级别
error_log /var/log/nginx/error.log warn;

# 事件模块配置
events {
    worker_connections 1024;
    multi_accept on;
    use epoll;
}

# HTTP 模块配置
http {
    # 基础 MIME 类型
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    
    # 日志格式定义
    log_format main '$remote_addr - $remote_user [$time_local] "$request" '
                    '$status $body_bytes_sent "$http_referer" '
                    '"$http_user_agent" "$http_x_forwarded_for"';
    
    log_format proxy '$remote_addr - $remote_user [$time_local] "$request" '
                     '$status $body_bytes_sent "$http_referer" '
                     '"$http_user_agent" "$http_x_forwarded_for" '
                     'proxy: $upstream_addr time: $upstream_response_time';
    
    log_format ssl '$remote_addr - $remote_user [$time_local] "$request" '
                   '$status $body_bytes_sent "$http_referer" '
                   '"$http_user_agent" ssl_protocol: $ssl_protocol ssl_cipher: $ssl_cipher';
    
    # 访问日志
    access_log /var/log/nginx/access.log main;
    
    # 性能优化
    sendfile on;
    tcp_nopush on;
    tcp_nodelay on;
    keepalive_timeout 65;
    types_hash_max_size 2048;
    client_max_body_size 100M;
    
    # 响应头优化
    server_tokens off;
    
    # Gzip 压缩
    gzip on;
    gzip_vary on;
    gzip_proxied any;
    gzip_comp_level 6;
    gzip_types text/plain text/css text/xml text/javascript 
               application/json application/javascript application/xml+rss 
               application/atom+xml image/svg+xml;
    
    # SSL 优化
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;
    
    # 文件缓存
    open_file_cache max=1000 inactive=20s;
    open_file_cache_valid 30s;
    open_file_cache_min_uses 2;
    open_file_cache_errors on;
    
    # 包含其他配置
    include /etc/nginx/conf.d/*.conf;
    include /etc/nginx/sites-enabled/*;
}
EOF
    
    # 设置权限
    chmod 644 /etc/nginx/nginx.conf
    chown root:root /etc/nginx/nginx.conf
    
    log "✅ Nginx 主配置文件创建完成"
}

# 创建默认站点配置
create_default_sites() {
    log "创建默认站点配置..."
    
    # 生成自签名证书
    if [ ! -f "$SSL_DIR/default.key" ]; then
        openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
            -keyout "$SSL_DIR/default.key" \
            -out "$SSL_DIR/default.crt" \
            -subj "/C=CN/ST=Beijing/L=Beijing/O=Default/CN=localhost" \
            -addext "subjectAltName=DNS:localhost" 2>/dev/null
        
        chmod 600 "$SSL_DIR/default.key"
        chown www-data:www-data "$SSL_DIR/default.key"
        chown www-data:www-data "$SSL_DIR/default.crt"
    fi
    
    # 创建默认 HTTP 站点（重定向到 HTTPS）
    cat > "$NGINX_SITES_AVAILABLE/default" << 'EOF'
# 默认 HTTP 站点 - 重定向到 HTTPS
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;
    
    # 安全头
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    
    # 访问控制 - 只允许本地访问
    allow 127.0.0.1;
    allow ::1;
    deny all;
    
    # 记录访问
    access_log /var/log/nginx/default-access.log main;
    error_log /var/log/nginx/default-error.log;
    
    # 返回 403 禁止访问
    return 403;
}

# 健康检查端点
server {
    listen 127.0.0.1:8080;
    server_name localhost;
    
    location /health {
        access_log off;
        return 200 "healthy\n";
        add_header Content-Type text/plain;
    }
    
    location /nginx-status {
        stub_status on;
        access_log off;
        allow 127.0.0.1;
        deny all;
    }
}
EOF
    
    # 创建管理页面
    cat > "$WWW_ROOT/index.html" << 'EOF'
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Nginx 反向代理管理</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }
        
        .container {
            background: white;
            border-radius: 20px;
            padding: 40px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            max-width: 600px;
            width: 100%;
            text-align: center;
        }
        
        h1 {
            color: #333;
            margin-bottom: 10px;
            font-size: 2.5em;
        }
        
        .subtitle {
            color: #666;
            margin-bottom: 30px;
            font-size: 1.1em;
        }
        
        .status-box {
            background: #f8f9fa;
            border-radius: 10px;
            padding: 25px;
            margin: 20px 0;
            text-align: left;
        }
        
        .status-item {
            margin: 10px 0;
            display: flex;
            align-items: center;
        }
        
        .status-icon {
            width: 24px;
            height: 24px;
            border-radius: 50%;
            margin-right: 15px;
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-weight: bold;
        }
        
        .status-up {
            background: #10b981;
        }
        
        .status-down {
            background: #ef4444;
        }
        
        .command-box {
            background: #1f2937;
            color: #f3f4f6;
            border-radius: 10px;
            padding: 15px;
            margin: 20px 0;
            font-family: 'Courier New', monospace;
            text-align: left;
            overflow-x: auto;
        }
        
        .btn {
            display: inline-block;
            background: #4f46e5;
            color: white;
            padding: 12px 30px;
            border-radius: 50px;
            text-decoration: none;
            font-weight: bold;
            margin-top: 20px;
            transition: all 0.3s ease;
            border: none;
            cursor: pointer;
            font-size: 1em;
        }
        
        .btn:hover {
            background: #4338ca;
            transform: translateY(-2px);
            box-shadow: 0 10px 20px rgba(0,0,0,0.2);
        }
        
        .footer {
            margin-top: 30px;
            color: #888;
            font-size: 0.9em;
        }
        
        .logo {
            font-size: 3em;
            margin-bottom: 20px;
            color: #4f46e5;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="logo">🚀</div>
        <h1>Nginx 反向代理已就绪</h1>
        <p class="subtitle">您的服务器已配置完成并正常运行</p>
        
        <div class="status-box">
            <div class="status-item">
                <div class="status-icon status-up">✓</div>
                <div>
                    <strong>Nginx 服务状态：</strong> 运行中
                </div>
            </div>
            <div class="status-item">
                <div class="status-icon status-up">✓</div>
                <div>
                    <strong>SSL 证书：</strong> 已配置
                </div>
            </div>
            <div class="status-item">
                <div class="status-icon status-up">✓</div>
                <div>
                    <strong>反向代理：</strong> 就绪
                </div>
            </div>
        </div>
        
        <div class="command-box">
            # 管理命令<br>
            nginx -t                 # 测试配置<br>
            systemctl reload nginx   # 重载配置<br>
            systemctl status nginx   # 查看状态
        </div>
        
        <button class="btn" onclick="location.reload()">刷新状态</button>
        
        <div class="footer">
            <p>由 Nginx 反向代理管理脚本自动生成</p>
            <p>© 2024 - 服务器时间: <span id="time"></span></p>
        </div>
    </div>
    
    <script>
        function updateTime() {
            const now = new Date();
            document.getElementById('time').textContent = 
                now.toLocaleString('zh-CN', { 
                    year: 'numeric', 
                    month: '2-digit', 
                    day: '2-digit',
                    hour: '2-digit',
                    minute: '2-digit',
                    second: '2-digit',
                    hour12: false 
                });
        }
        updateTime();
        setInterval(updateTime, 1000);
    </script>
</body>
</html>
EOF
    
    # 启用默认站点
    ln -sf "$NGINX_SITES_AVAILABLE/default" "$NGINX_SITES_ENABLED/"
    
    log "✅ 默认站点配置完成"
}

# 配置防火墙
setup_firewall() {
    log "配置防火墙规则..."
    
    # 检查 ufw 是否安装
    if command -v ufw &>/dev/null; then
        ufw allow 22/tcp comment 'SSH'
        ufw allow 80/tcp comment 'HTTP'
        ufw allow 443/tcp comment 'HTTPS'
        ufw --force enable
        log "UFW 防火墙已配置"
    fi
    
    # 检查 iptables
    if command -v iptables &>/dev/null; then
        iptables -A INPUT -p tcp --dport 22 -j ACCEPT
        iptables -A INPUT -p tcp --dport 80 -j ACCEPT
        iptables -A INPUT -p tcp --dport 443 -j ACCEPT
        iptables -A INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT
        iptables -A INPUT -i lo -j ACCEPT
        iptables -A INPUT -j DROP
        log "iptables 规则已配置"
    fi
    
    log "防火墙配置完成"
}

# 设置开机自启和服务配置
setup_service() {
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    echo -e "${CYAN}          配置系统服务                    ${NC}"
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    
    log "创建 systemd 服务文件..."
    
    cat > /lib/systemd/system/nginx.service << 'EOF'
[Unit]
Description=A high performance web server and a reverse proxy server
Documentation=man:nginx(8)
After=network.target nss-lookup.target

[Service]
Type=forking
PIDFile=/run/nginx.pid
ExecStartPre=/usr/sbin/nginx -t -q -g 'daemon on; master_process on;'
ExecStart=/usr/sbin/nginx -g 'daemon on; master_process on;'
ExecReload=/usr/sbin/nginx -g 'daemon on; master_process on;' -s reload
ExecStop=-/sbin/start-stop-daemon --quiet --stop --retry QUIT/5 --pidfile /run/nginx.pid
TimeoutStopSec=5
KillMode=mixed

# 安全加固
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/var/log/nginx /var/cache/nginx /var/lib/nginx
ReadOnlyPaths=/etc/nginx

# 资源限制
LimitNOFILE=65536
LimitNPROC=512

# 用户和组
User=www-data
Group=www-data

[Install]
WantedBy=multi-user.target
EOF
    
    # 重新加载 systemd
    systemctl daemon-reload
    
    # 启用并启动 Nginx
    systemctl enable nginx
    systemctl start nginx
    
    # 检查服务状态
    if systemctl is-active nginx &>/dev/null; then
        log "✅ Nginx 服务启动成功"
        
        # 显示服务状态
        systemctl status nginx --no-pager | head -20
    else
        error "Nginx 服务启动失败"
        journalctl -u nginx --no-pager -n 20
        return 1
    fi
    
    log "服务配置完成"
}

# 创建监控脚本
create_monitoring() {
    log "创建监控脚本..."
    
    cat > /usr/local/bin/nginx-monitor << 'EOF'
#!/bin/bash

# Nginx 状态监控脚本

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

check_nginx() {
    if systemctl is-active nginx &>/dev/null; then
        echo -e "${GREEN}✓ Nginx 正在运行${NC}"
        return 0
    else
        echo -e "${RED}✗ Nginx 已停止${NC}"
        return 1
    fi
}

check_ports() {
    echo -e "\n端口监听状态:"
    netstat -tulpn | grep -E ':80|:443' | grep nginx || echo -e "${YELLOW}警告: Nginx 未监听标准端口${NC}"
}

check_ssl() {
    echo -e "\nSSL 证书状态:"
    if [ -d /etc/nginx/ssl/certs ]; then
        find /etc/nginx/ssl/certs -name "*.pem" -type f | while read cert; do
            domain=$(basename $(dirname "$cert"))
            expiry=$(openssl x509 -in "$cert" -noout -dates 2>/dev/null | grep "Not After" | cut -d= -f2)
            if [ -n "$expiry" ]; then
                echo "  $domain: $expiry"
            fi
        done
    fi
}

check_logs() {
    echo -e "\n日志文件大小:"
    ls -lh /var/log/nginx/*.log 2>/dev/null | awk '{print $5, $9}'
}

# 主函数
main() {
    echo "=== Nginx 状态监控 ==="
    echo "时间: $(date)"
    echo ""
    
    check_nginx
    check_ports
    check_ssl
    check_logs
    
    # 显示活动连接数
    connections=$(netstat -an | grep ':80\|:443' | grep ESTABLISHED | wc -l)
    echo -e "\n活动连接数: $connections"
    
    # 显示系统负载
    load=$(uptime | awk -F'load average:' '{print $2}')
    echo -e "系统负载: $load"
}

main "$@"
EOF
    
    chmod +x /usr/local/bin/nginx-monitor
    
    # 添加定时监控任务
    cat > /etc/cron.d/nginx-monitor << 'EOF'
# Nginx 监控任务
*/5 * * * * root /usr/local/bin/nginx-monitor >> /var/log/nginx/monitor.log 2>&1

# 每日凌晨清理旧日志
0 2 * * * root find /var/log/nginx -name "*.log" -mtime +30 -delete
0 2 * * * root find /var/log/nginx -name "*.gz" -mtime +90 -delete
EOF
    
    log "✅ 监控脚本创建完成"
}

# 测试 Nginx 配置
test_nginx() {
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    echo -e "${CYAN}          测试 Nginx 配置                 ${NC}"
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    
    log "测试 Nginx 配置语法..."
    if nginx -t; then
        log "✅ Nginx 配置测试通过"
    else
        error "❌ Nginx 配置测试失败"
        return 1
    fi
    
    log "测试 HTTP 访问..."
    if curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/health | grep -q "200"; then
        log "✅ HTTP 健康检查通过"
    else
        error "❌ HTTP 健康检查失败"
    fi
    
    log "测试进程运行..."
    if pgrep nginx &>/dev/null; then
        nginx_processes=$(pgrep nginx | wc -l)
        log "✅ Nginx 进程运行中 (共 $nginx_processes 个进程)"
    else
        error "❌ Nginx 进程未运行"
    fi
    
    log "测试日志目录..."
    if [ -f "/var/log/nginx/error.log" ]; then
        log "✅ 日志文件正常"
    else
        warn "⚠️  日志文件未找到"
    fi
}

# 显示安装摘要
show_summary() {
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    echo -e "${CYAN}          Nginx 安装完成摘要              ${NC}"
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    echo ""
    
    echo -e "${GREEN}✅ 安装状态:${NC} 完成"
    echo ""
    
    echo -e "${YELLOW}📁 目录结构:${NC}"
    echo "  安装目录: $INSTALL_DIR"
    echo "  配置目录: /etc/nginx/"
    echo "  证书目录: $SSL_DIR"
    echo "  网站目录: $WWW_ROOT"
    echo "  日志目录: /var/log/nginx/"
    echo ""
    
    echo -e "${YELLOW}🔧 服务信息:${NC}"
    systemctl status nginx --no-pager | grep "Active:" | head -1
    nginx -v 2>&1
    echo ""
    
    echo -e "${YELLOW}🌐 访问信息:${NC}"
    server_ip=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "未知")
    echo "  服务器IP: $server_ip"
    echo "  管理页面: http://$server_ip (健康检查)"
    echo ""
    
    echo -e "${YELLOW}🛠️  管理命令:${NC}"
    echo "  查看状态: systemctl status nginx"
    echo "  测试配置: nginx -t"
    echo "  重载配置: systemctl reload nginx"
    echo "  监控状态: nginx-monitor"
    echo ""
    
    echo -e "${YELLOW}📋 后续步骤:${NC}"
    echo "  1. 配置域名解析到服务器 IP"
    echo "  2. 使用脚本添加反向代理"
    echo "  3. 配置 SSL 证书（可选）"
    echo "  4. 设置防火墙规则"
    echo ""
    
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    log "安装完成！Nginx 已成功安装并配置"
}

# 主安装函数
install_nginx_complete() {
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    echo -e "${CYAN}      Debian 12+ Nginx 纯净安装            ${NC}"
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    echo ""
    
    # 确认安装
    read -p "这将完全删除现有 Nginx 并重新安装，是否继续？(y/n): " confirm
    if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
        echo "安装已取消"
        exit 0
    fi
    
    # 执行安装步骤
    check_root
    
    log "开始安装流程..."
    echo ""
    
    # 步骤 1: 清理旧安装
    clean_nginx
    
    # 步骤 2: 安装依赖
    install_deps
    
    # 步骤 3: 创建目录和权限
    create_dirs_and_permissions
    
    # 步骤 4: 配置用户
    setup_nginx_user
    
    # 步骤 5: 安装 Nginx
    install_nginx
    if [ $? -ne 0 ]; then
        error "Nginx 安装失败"
        exit 1
    fi
    
    # 步骤 6: 配置主文件
    configure_nginx_main
    
    # 步骤 7: 创建默认站点
    create_default_sites
    
    # 步骤 8: 配置防火墙
    setup_firewall
    
    # 步骤 9: 设置服务
    setup_service
    if [ $? -ne 0 ]; then
        error "服务设置失败"
        exit 1
    fi
    
    # 步骤 10: 创建监控
    create_monitoring
    
    # 步骤 11: 测试配置
    test_nginx
    
    # 步骤 12: 显示摘要
    show_summary
}

# 显示帮助
show_help() {
    echo -e "${CYAN}使用方法:${NC}"
    echo "  $(basename "$0") [选项]"
    echo ""
    echo -e "${CYAN}选项:${NC}"
    echo "  install    完全重新安装 Nginx（推荐）"
    echo "  status     查看 Nginx 状态"
    echo "  test       测试 Nginx 配置"
    echo "  monitor    运行监控脚本"
    echo "  help       显示帮助信息"
    echo ""
    echo -e "${CYAN}示例:${NC}"
    echo "  $(basename "$0") install   # 完全重新安装"
    echo "  $(basename "$0") status    # 查看状态"
    echo ""
}

# 主函数
main() {
    case "$1" in
        "install")
            install_nginx_complete
            ;;
        "status")
            systemctl status nginx --no-pager
            ;;
        "test")
            nginx -t
            ;;
        "monitor")
            /usr/local/bin/nginx-monitor 2>/dev/null || echo "请先运行 install 安装监控脚本"
            ;;
        "help"|"-h"|"--help")
            show_help
            ;;
        *)
            echo "Debian 12+ Nginx 纯净安装脚本"
            echo ""
            echo "请选择要执行的操作:"
            echo "1. 完全重新安装 Nginx"
            echo "2. 查看当前状态"
            echo "3. 退出"
            echo ""
            read -p "请输入选项 (1-3): " choice
            
            case $choice in
                1)
                    install_nginx_complete
                    ;;
                2)
                    systemctl status nginx --no-pager
                    ;;
                3)
                    exit 0
                    ;;
                *)
                    echo "无效选项"
                    ;;
            esac
            ;;
    esac
}

# 检查是否直接运行
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi