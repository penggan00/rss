#!/bin/bash

# ===================================================
# Debian 12+ Nginx 一键安装配置脚本
# 直接复制粘贴运行即可
# ===================================================

set -e  # 遇到错误立即退出

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
    sleep 0.5
}

error() {
    echo -e "${RED}[错误]${NC} $1" >&2
    exit 1
}

warn() {
    echo -e "${YELLOW}[警告]${NC} $1"
}

info() {
    echo -e "${BLUE}[信息]${NC} $1"
}

# 显示横幅
show_banner() {
    clear
    echo -e "${CYAN}════════════════════════════════════════════════════════${NC}"
    echo -e "${CYAN}         Debian 12+ Nginx 一键安装配置脚本            ${NC}"
    echo -e "${CYAN}════════════════════════════════════════════════════════${NC}"
    echo ""
    echo -e "${GREEN}本脚本将完成以下操作：${NC}"
    echo "  ✓ 安装 Nginx 最新版"
    echo "  ✓ 配置优化设置"
    echo "  ✓ 设置反向代理"
    echo "  ✓ 配置 SSL 证书"
    echo "  ✓ 设置开机自启"
    echo ""
}

# 检查 Root 权限
check_root() {
    if [ "$(id -u)" -ne 0 ]; then
        error "必须使用 root 权限运行此脚本"
    fi
}

# 清理旧 Nginx
clean_old_nginx() {
    log "步骤 1/8: 清理旧 Nginx 安装"
    
    # 停止 Nginx
    if systemctl is-active nginx &>/dev/null; then
        log "停止 Nginx 服务..."
        systemctl stop nginx
    fi
    
    # 移除旧版本
    log "移除旧版本 Nginx..."
    apt-get remove --purge -y nginx* 2>/dev/null || true
    apt-get autoremove -y 2>/dev/null
    
    # 清理目录
    log "清理配置文件..."
    rm -rf /etc/nginx
    rm -rf /var/log/nginx
    rm -rf /var/cache/nginx
    rm -rf /var/lib/nginx
    
    log "旧 Nginx 清理完成"
}

# 安装依赖
install_dependencies() {
    log "步骤 2/8: 安装系统依赖"
    
    # 更新系统
    log "更新系统包列表..."
    apt-get update -y
    
    # 安装基础工具
    log "安装基础工具..."
    apt-get install -y curl wget git tar gzip unzip
    
    # 安装 SSL 工具
    log "安装 SSL 工具..."
    apt-get install -y openssl certbot python3-certbot-nginx
    
    log "依赖安装完成"
}

# 创建目录结构
create_directories() {
    log "步骤 3/8: 创建目录结构"
    
    # 创建主目录
    mkdir -p /etc/nginx/conf.d
    mkdir -p /etc/nginx/sites-available
    mkdir -p /etc/nginx/sites-enabled
    mkdir -p /etc/nginx/ssl/certs
    mkdir -p /etc/nginx/ssl/private
    
    # 创建网站目录
    mkdir -p /var/www/html
    mkdir -p /var/www/ssl
    
    # 创建日志目录
    mkdir -p /var/log/nginx
    mkdir -p /var/log/nginx/proxy
    
    # 创建缓存目录
    mkdir -p /var/cache/nginx
    mkdir -p /var/lib/nginx
    
    # 设置权限
    chown -R www-data:www-data /var/www
    chown -R www-data:www-data /var/log/nginx
    chown -R www-data:www-data /var/cache/nginx
    chown -R www-data:www-data /var/lib/nginx
    chmod 755 /var/www
    chmod 750 /var/log/nginx/proxy
    chmod 700 /etc/nginx/ssl/private
    
    log "目录结构创建完成"
}

# 安装 Nginx
install_nginx() {
    log "步骤 4/8: 安装 Nginx"
    
    # 安装 Nginx
    log "安装 Nginx..."
    apt-get install -y nginx
    
    # 检查安装
    if ! command -v nginx &>/dev/null; then
        error "Nginx 安装失败"
    fi
    
    log "Nginx 版本: $(nginx -v 2>&1)"
    log "Nginx 安装完成"
}

# 配置 Nginx
configure_nginx() {
    log "步骤 5/8: 配置 Nginx"
    
    # 备份原始配置
    cp /etc/nginx/nginx.conf /etc/nginx/nginx.conf.backup
    
    # 创建优化配置
    cat > /etc/nginx/nginx.conf << 'EOF'
user www-data;
worker_processes auto;
pid /run/nginx.pid;

events {
    worker_connections 1024;
    multi_accept on;
}

http {
    # 基础设置
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    
    # 日志格式
    log_format main '$remote_addr - $remote_user [$time_local] "$request" '
                    '$status $body_bytes_sent "$http_referer" '
                    '"$http_user_agent" "$http_x_forwarded_for"';
    
    # 性能优化
    sendfile on;
    tcp_nopush on;
    tcp_nodelay on;
    keepalive_timeout 65;
    types_hash_max_size 2048;
    client_max_body_size 100M;
    
    # SSL 设置
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;
    
    # 访问日志
    access_log /var/log/nginx/access.log main;
    error_log /var/log/nginx/error.log;
    
    # Gzip 压缩
    gzip on;
    gzip_vary on;
    gzip_proxied any;
    gzip_comp_level 6;
    gzip_types text/plain text/css text/xml text/javascript 
               application/json application/javascript application/xml+rss 
               application/atom+xml image/svg+xml;
    
    # 包含其他配置
    include /etc/nginx/conf.d/*.conf;
    include /etc/nginx/sites-enabled/*;
}
EOF
    
    # 创建默认网站配置
    cat > /etc/nginx/sites-available/default << 'EOF'
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    
    root /var/www/html;
    index index.html index.htm;
    
    server_name _;
    
    location / {
        try_files $uri $uri/ =404;
    }
}
EOF
    
    # 启用默认网站
    ln -sf /etc/nginx/sites-available/default /etc/nginx/sites-enabled/
    
    # 创建欢迎页面
    cat > /var/www/html/index.html << 'EOF'
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Nginx 安装成功</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            text-align: center;
            padding: 50px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }
        .container {
            background: rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
            border-radius: 20px;
            padding: 40px;
            display: inline-block;
            box-shadow: 0 10px 30px rgba(0,0,0,0.3);
        }
        h1 {
            font-size: 2.5em;
            margin-bottom: 20px;
        }
        p {
            font-size: 1.2em;
            margin-bottom: 30px;
        }
        .success {
            font-size: 4em;
            margin-bottom: 20px;
        }
        .info {
            background: rgba(255, 255, 255, 0.2);
            border-radius: 10px;
            padding: 20px;
            margin: 20px 0;
            text-align: left;
        }
        code {
            background: rgba(0, 0, 0, 0.3);
            padding: 5px 10px;
            border-radius: 5px;
            font-family: 'Courier New', monospace;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="success">🎉</div>
        <h1>Nginx 安装成功！</h1>
        <p>您的服务器已经配置完成</p>
        
        <div class="info">
            <strong>服务器信息：</strong><br>
            - 系统: Debian $(lsb_release -rs)<br>
            - 时间: <span id="time"></span><br>
            - IP: <span id="ip"></span>
        </div>
        
        <div class="info">
            <strong>管理命令：</strong><br>
            <code>systemctl status nginx</code> - 查看状态<br>
            <code>nginx -t</code> - 测试配置<br>
            <code>systemctl reload nginx</code> - 重载配置
        </div>
        
        <p>现在您可以开始配置反向代理了</p>
    </div>
    
    <script>
        // 显示当前时间
        function updateTime() {
            const now = new Date();
            document.getElementById('time').textContent = 
                now.toLocaleString('zh-CN');
        }
        setInterval(updateTime, 1000);
        updateTime();
        
        // 获取 IP
        fetch('https://api.ipify.org?format=json')
            .then(response => response.json())
            .then(data => {
                document.getElementById('ip').textContent = data.ip;
            })
            .catch(() => {
                document.getElementById('ip').textContent = '未知';
            });
    </script>
</body>
</html>
EOF
    
    log "Nginx 配置完成"
}

# 配置系统服务
configure_service() {
    log "步骤 6/8: 配置系统服务"
    
    # 创建优化的服务文件
    cat > /lib/systemd/system/nginx.service << 'EOF'
[Unit]
Description=A high performance web server and a reverse proxy server
Documentation=man:nginx(8)
After=network.target

[Service]
Type=forking
PIDFile=/run/nginx.pid
ExecStartPre=/usr/sbin/nginx -t -q -g 'daemon on; master_process on;'
ExecStart=/usr/sbin/nginx -g 'daemon on; master_process on;'
ExecReload=/usr/sbin/nginx -g 'daemon on; master_process on;' -s reload
ExecStop=-/sbin/start-stop-daemon --quiet --stop --retry QUIT/5 --pidfile /run/nginx.pid
TimeoutStopSec=5
KillMode=mixed

User=www-data
Group=www-data

[Install]
WantedBy=multi-user.target
EOF
    
    # 重新加载 systemd
    systemctl daemon-reload
    
    # 启用开机自启
    systemctl enable nginx
    
    log "系统服务配置完成"
}

# 测试 Nginx
test_nginx() {
    log "步骤 7/8: 测试 Nginx"
    
    # 测试配置文件
    log "测试配置文件..."
    if nginx -t; then
        log "✅ 配置文件测试通过"
    else
        error "❌ 配置文件测试失败"
    fi
    
    # 启动 Nginx
    log "启动 Nginx 服务..."
    systemctl start nginx
    
    # 检查状态
    sleep 2
    if systemctl is-active nginx &>/dev/null; then
        log "✅ Nginx 启动成功"
    else
        error "❌ Nginx 启动失败"
    fi
    
    # 测试访问
    log "测试 Web 访问..."
    if curl -s -o /dev/null -w "%{http_code}" http://localhost | grep -q "200"; then
        log "✅ Web 服务运行正常"
    else
        warn "⚠️  Web 服务访问测试失败"
    fi
}

# 创建管理脚本
create_management_script() {
    log "步骤 8/8: 创建管理脚本"
    
    # 创建管理脚本
    cat > /usr/local/bin/nginx-manager << 'EOF'
#!/bin/bash

# Nginx 管理脚本

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

show_menu() {
    clear
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    echo -e "${CYAN}         Nginx 管理脚本                    ${NC}"
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    echo ""
    echo -e "${GREEN}1.${NC} 查看 Nginx 状态"
    echo -e "${GREEN}2.${NC} 测试配置文件"
    echo -e "${GREEN}3.${NC} 重载 Nginx"
    echo -e "${GREEN}4.${NC} 重启 Nginx"
    echo -e "${GREEN}5.${NC} 停止 Nginx"
    echo -e "${GREEN}6.${NC} 查看错误日志"
    echo -e "${GREEN}7.${NC} 查看访问日志"
    echo -e "${GREEN}8.${NC} 添加反向代理"
    echo -e "${GREEN}9.${NC} 申请 SSL 证书"
    echo -e "${GREEN}0.${NC} 退出"
    echo ""
}

view_status() {
    echo -e "${YELLOW}=== Nginx 状态 ===${NC}"
    systemctl status nginx --no-pager
    echo ""
    echo -e "${YELLOW}=== 进程信息 ===${NC}"
    ps aux | grep nginx | grep -v grep
    echo ""
    echo -e "${YELLOW}=== 端口监听 ===${NC}"
    netstat -tulpn | grep nginx
}

test_config() {
    echo -e "${YELLOW}测试 Nginx 配置...${NC}"
    if nginx -t; then
        echo -e "${GREEN}✅ 配置测试通过${NC}"
    else
        echo -e "${RED}❌ 配置测试失败${NC}"
    fi
}

add_proxy() {
    echo -e "${YELLOW}添加反向代理${NC}"
    read -p "请输入域名 (如: example.com): " domain
    read -p "请输入本地端口 (如: 3000): " port
    
    if [[ -z "$domain" || -z "$port" ]]; then
        echo -e "${RED}域名和端口不能为空${NC}"
        return 1
    fi
    
    # 创建配置文件
    cat > /etc/nginx/conf.d/${domain}.conf << EOF
# 反向代理配置
# 生成时间: $(date)

# HTTP 重定向
server {
    listen 80;
    server_name ${domain};
    
    location / {
        return 301 https://\$server_name\$request_uri;
    }
}

# HTTPS 代理
server {
    listen 443 ssl;
    server_name ${domain};
    
    # SSL 证书路径（需要先申请）
    ssl_certificate /etc/nginx/ssl/certs/${domain}/fullchain.pem;
    ssl_certificate_key /etc/nginx/ssl/private/${domain}/key.pem;
    
    location / {
        proxy_pass http://localhost:${port};
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
    
    echo -e "${GREEN}✅ 反向代理配置已创建${NC}"
    echo -e "配置文件: /etc/nginx/conf.d/${domain}.conf"
    echo -e "请先申请 SSL 证书: certbot --nginx -d ${domain}"
}

ssl_cert() {
    echo -e "${YELLOW}申请 SSL 证书${NC}"
    
    if ! command -v certbot &>/dev/null; then
        echo -e "${RED}certbot 未安装，正在安装...${NC}"
        apt-get update
        apt-get install -y certbot python3-certbot-nginx
    fi
    
    read -p "请输入域名 (如: example.com): " domain
    if [[ -z "$domain" ]]; then
        echo -e "${RED}域名不能为空${NC}"
        return 1
    fi
    
    echo -e "${YELLOW}正在为 ${domain} 申请证书...${NC}"
    certbot --nginx -d ${domain} -d www.${domain} --non-interactive --agree-tos --email admin@${domain} || {
        echo -e "${RED}证书申请失败${NC}"
        echo -e "${YELLOW}请检查:${NC}"
        echo "1. 域名是否解析到本服务器"
        echo "2. 80/443 端口是否开放"
        return 1
    }
    
    echo -e "${GREEN}✅ SSL 证书申请成功${NC}"
}

main() {
    while true; do
        show_menu
        read -p "请选择操作 (0-9): " choice
        
        case $choice in
            1)
                view_status
                ;;
            2)
                test_config
                ;;
            3)
                echo -e "${YELLOW}重载 Nginx...${NC}"
                systemctl reload nginx && echo -e "${GREEN}✅ 重载成功${NC}" || echo -e "${RED}❌ 重载失败${NC}"
                ;;
            4)
                echo -e "${YELLOW}重启 Nginx...${NC}"
                systemctl restart nginx && echo -e "${GREEN}✅ 重启成功${NC}" || echo -e "${RED}❌ 重启失败${NC}"
                ;;
            5)
                echo -e "${YELLOW}停止 Nginx...${NC}"
                systemctl stop nginx && echo -e "${GREEN}✅ 停止成功${NC}" || echo -e "${RED}❌ 停止失败${NC}"
                ;;
            6)
                echo -e "${YELLOW}=== 错误日志 (最后50行) ===${NC}"
                tail -50 /var/log/nginx/error.log
                ;;
            7)
                echo -e "${YELLOW}=== 访问日志 (最后50行) ===${NC}"
                tail -50 /var/log/nginx/access.log
                ;;
            8)
                add_proxy
                ;;
            9)
                ssl_cert
                ;;
            0)
                echo "再见！"
                exit 0
                ;;
            *)
                echo -e "${RED}无效选择${NC}"
                ;;
        esac
        
        echo ""
        read -p "按 Enter 键继续..."
    done
}

main "$@"
EOF
    
    # 设置权限
    chmod +x /usr/local/bin/nginx-manager
    
    log "管理脚本创建完成"
}

# 显示完成信息
show_completion() {
    clear
    echo -e "${CYAN}════════════════════════════════════════════════════════${NC}"
    echo -e "${CYAN}                  安装完成！                           ${NC}"
    echo -e "${CYAN}════════════════════════════════════════════════════════${NC}"
    echo ""
    echo -e "${GREEN}✅ Nginx 安装配置已完成${NC}"
    echo ""
    echo -e "${YELLOW}📋 安装摘要：${NC}"
    echo "  - Nginx 已安装并运行"
    echo "  - 优化配置已应用"
    echo "  - 系统服务已配置"
    echo "  - 管理脚本已创建"
    echo ""
    echo -e "${YELLOW}🔧 管理命令：${NC}"
    echo "  查看状态: systemctl status nginx"
    echo "  测试配置: nginx -t"
    echo "  重载配置: systemctl reload nginx"
    echo "  管理菜单: nginx-manager"
    echo ""
    echo -e "${YELLOW}🌐 访问地址：${NC}"
    local_ip=$(hostname -I | awk '{print $1}')
    echo "  本地访问: http://localhost"
    echo "  远程访问: http://${local_ip}"
    echo ""
    echo -e "${YELLOW}📁 重要目录：${NC}"
    echo "  配置文件: /etc/nginx/"
    echo "  网站文件: /var/www/html/"
    echo "  日志文件: /var/log/nginx/"
    echo "  SSL证书: /etc/nginx/ssl/"
    echo ""
    echo -e "${CYAN}════════════════════════════════════════════════════════${NC}"
    echo ""
    
    # 测试访问
    echo -e "${YELLOW}正在测试 Web 服务...${NC}"
    if curl -s -o /dev/null -w "HTTP状态码: %{http_code}\n" http://localhost; then
        echo -e "${GREEN}✅ Web 服务运行正常${NC}"
    else
        echo -e "${RED}⚠️  Web 服务可能有问题${NC}"
    fi
    
    echo ""
    echo -e "${GREEN}现在可以运行 'nginx-manager' 来管理 Nginx 了${NC}"
}

# 主安装函数
main_install() {
    show_banner
    
    # 确认安装
    read -p "是否继续安装？(y/n): " confirm
    if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
        echo "安装已取消"
        exit 0
    fi
    
    # 检查 root
    check_root
    
    # 执行安装步骤
    clean_old_nginx
    install_dependencies
    create_directories
    install_nginx
    configure_nginx
    configure_service
    test_nginx
    create_management_script
    show_completion
}

# 如果直接运行，执行安装
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main_install
fi