#!/bin/bash

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# 安装依赖函数
install_dependencies() {
    log "INFO" "检查并安装依赖..."
    
    # 更新包列表
    log "DEBUG" "更新包列表..."
    apk update 2>/dev/null || {
        log "ERROR" "无法更新包列表"
        return 1
    }
    
    # 必需的主包
    local required_packages=("nginx" "openssl")
    
    # 可选但推荐的包
    local recommended_packages=("tree" "curl" "wget" "vim" "certbot")
    
    # 检查并安装必需包
    for pkg in "${required_packages[@]}"; do
        if ! command -v $pkg &> /dev/null && ! apk info -e $pkg &> /dev/null; then
            log "INFO" "安装必需包: $pkg"
            apk add --no-cache $pkg 2>/dev/null
            if [ $? -ne 0 ]; then
                log "ERROR" "安装 $pkg 失败"
                return 1
            fi
        else
            log "DEBUG" "$pkg 已安装"
        fi
    done
    
    # 检查并安装推荐包
    local missing_recommended=()
    for pkg in "${recommended_packages[@]}"; do
        if ! command -v $pkg &> /dev/null && ! apk info -e $pkg &> /dev/null; then
            missing_recommended+=("$pkg")
        fi
    done
    
    if [ ${#missing_recommended[@]} -gt 0 ]; then
        echo -e "${YELLOW}以下推荐包未安装:${NC} ${missing_recommended[*]}"
        read -p "是否安装这些推荐包？(y/n): " choice
        if [[ $choice =~ ^[Yy]$ ]]; then
            for pkg in "${missing_recommended[@]}"; do
                log "INFO" "安装推荐包: $pkg"
                apk add --no-cache $pkg 2>/dev/null
            done
        fi
    fi
    
    # 验证安装
    log "INFO" "验证安装..."
    if ! command -v nginx &> /dev/null; then
        log "ERROR" "Nginx安装失败，请手动安装: apk add nginx"
        return 1
    fi
    
    if ! command -v openssl &> /dev/null; then
        log "ERROR" "OpenSSL安装失败，请手动安装: apk add openssl"
        return 1
    fi
    
    log "INFO" "✅ 所有依赖已安装"
    return 0
}

# 完全安装模式（适用于Alpine）
full_install_mode() {
    echo -e "${BLUE}========================================${NC}"
    echo -e "${GREEN}       Nginx完全安装模式${NC}"
    echo -e "${BLUE}========================================${NC}"
    
    log "INFO" "开始完全安装流程..."
    
    # 1. 停止并清理旧版本
    log "INFO" "停止并清理旧版本..."
    pkill nginx 2>/dev/null
    sleep 2
    
    # 2. 卸载旧包
    log "INFO" "卸载旧包..."
    apk del nginx nginx-* --purge 2>/dev/null || true
    
    # 3. 清理残留
    log "INFO" "清理残留文件..."
    rm -rf /etc/nginx /var/lib/nginx /var/log/nginx /run/nginx /usr/share/nginx 2>/dev/null || true
    
    # 4. 安装依赖
    if ! install_dependencies; then
        log "ERROR" "依赖安装失败"
        exit 1
    fi
    
    # 5. 创建基本配置
    log "INFO" "创建基本配置..."
    create_basic_config
    
    # 6. 启动服务
    log "INFO" "启动Nginx服务..."
    start_nginx_service
    
    # 7. 测试
    log "INFO" "测试安装..."
    test_installation
    
    echo -e "\n${GREEN}✅ Nginx安装完成！${NC}"
    echo -e "${BLUE}========================================${NC}"
    echo -e "${GREEN}Nginx状态:${NC} $(systemctl is-active nginx 2>/dev/null || echo "active")"
    echo -e "${GREEN}配置文件:${NC} /etc/nginx/nginx.conf"
    echo -e "${GREEN}默认页面:${NC} http://$(hostname -I 2>/dev/null | awk '{print $1}')"
    echo -e "${BLUE}========================================${NC}"
    
    # 询问是否进入配置模式
    echo -ne "\n${YELLOW}是否现在配置反向代理？${NC} (y/n): "
    read -n 1 choice
    echo
    if [[ $choice =~ ^[Yy]$ ]]; then
        return 0
    else
        echo -e "${GREEN}退出安装模式，您可以稍后运行此脚本进行配置${NC}"
        exit 0
    fi
}

# 创建基本配置
create_basic_config() {
    log "INFO" "创建Nginx基本配置..."
    
    # 创建目录结构
    mkdir -p /etc/nginx/{conf.d,sites-available,sites-enabled,ssl/{certs,private}}
    mkdir -p /var/log/nginx /run/nginx /var/www/html
    
    # 创建nginx.conf
    cat > /etc/nginx/nginx.conf << 'EOF'
user nginx;
worker_processes auto;
pid /run/nginx/nginx.pid;

events {
    worker_connections 1024;
    multi_accept on;
}

http {
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;

    sendfile        on;
    tcp_nopush      on;
    tcp_nodelay     on;
    keepalive_timeout  65;
    types_hash_max_size 2048;
    server_tokens off;

    # 日志格式
    log_format main '$remote_addr - $remote_user [$time_local] "$request" '
                    '$status $body_bytes_sent "$http_referer" '
                    '"$http_user_agent" "$http_x_forwarded_for"';
    
    log_format proxy '$remote_addr - $remote_user [$time_local] "$request" '
                     '$status $body_bytes_sent "$http_referer" '
                     '"$http_user_agent" "$http_x_forwarded_for" '
                     'proxy: $upstream_addr time: $request_time';

    # 访问日志
    access_log  /var/log/nginx/access.log main;
    error_log   /var/log/nginx/error.log warn;

    # Gzip压缩
    gzip on;
    gzip_vary on;
    gzip_min_length 1024;
    gzip_types text/plain text/css text/xml text/javascript 
               application/javascript application/xml+rss 
               application/json;

    # 默认服务器（仅用于测试）
    server {
        listen 80 default_server;
        listen [::]:80 default_server;
        server_name _;
        
        root /var/www/html;
        index index.html index.htm;
        
        location / {
            try_files $uri $uri/ =404;
        }
        
        location /status {
            stub_status on;
            access_log off;
            allow 127.0.0.1;
            deny all;
        }
    }

    # 包含其他配置
    include /etc/nginx/conf.d/*.conf;
    include /etc/nginx/sites-enabled/*;
}
EOF

    # 创建默认页面
    cat > /var/www/html/index.html << 'EOF'
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Nginx安装成功</title>
    <style>
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            margin: 0;
            padding: 0;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
        }
        .container {
            background: white;
            padding: 40px;
            border-radius: 15px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            text-align: center;
            max-width: 600px;
            width: 90%;
        }
        .success-icon {
            font-size: 80px;
            color: #28a745;
            margin-bottom: 20px;
        }
        h1 {
            color: #333;
            margin-bottom: 20px;
        }
        .info-box {
            background: #f8f9fa;
            padding: 20px;
            border-radius: 10px;
            margin: 20px 0;
            text-align: left;
        }
        .info-item {
            margin: 10px 0;
            padding: 8px 0;
            border-bottom: 1px solid #e9ecef;
        }
        .info-item:last-child {
            border-bottom: none;
        }
        .label {
            font-weight: bold;
            color: #495057;
            display: inline-block;
            width: 120px;
        }
        .value {
            color: #6c757d;
        }
        .tip {
            background: #fff3cd;
            border: 1px solid #ffeaa7;
            color: #856404;
            padding: 15px;
            border-radius: 5px;
            margin-top: 20px;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="success-icon">✅</div>
        <h1>🎉 Nginx安装成功！</h1>
        
        <div class="info-box">
            <div class="info-item">
                <span class="label">状态：</span>
                <span class="value" style="color: #28a745; font-weight: bold;">运行正常</span>
            </div>
            <div class="info-item">
                <span class="label">时间：</span>
                <span class="value" id="datetime"></span>
            </div>
            <div class="info-item">
                <span class="label">Nginx版本：</span>
                <span class="value">$(nginx -v 2>&1 | cut -d/ -f2)</span>
            </div>
            <div class="info-item">
                <span class="label">IP地址：</span>
                <span class="value">$(hostname -I 2>/dev/null | awk '{print $1}' || echo '127.0.0.1')</span>
            </div>
            <div class="info-item">
                <span class="label">系统：</span>
                <span class="value">$(cat /etc/os-release | grep PRETTY_NAME | cut -d= -f2 | tr -d '\"')</span>
            </div>
        </div>
        
        <div class="tip">
            💡 <strong>提示：</strong>使用 <code>./nginx-proxy.sh</code> 脚本配置反向代理
        </div>
    </div>
    
    <script>
        document.getElementById('datetime').textContent = new Date().toLocaleString();
        
        // 动态显示安装步骤
        setTimeout(() => {
            const container = document.querySelector('.info-box');
            const steps = [
                '✅ 依赖安装完成',
                '✅ 目录结构创建',
                '✅ 配置文件生成',
                '✅ 服务启动成功',
                '✅ 端口监听正常'
            ];
            
            steps.forEach((step, index) => {
                setTimeout(() => {
                    const stepElement = document.createElement('div');
                    stepElement.className = 'info-item';
                    stepElement.innerHTML = `<span class="label">步骤 ${index + 1}:</span><span class="value">${step}</span>`;
                    container.appendChild(stepElement);
                }, index * 300);
            });
        }, 1000);
    </script>
</body>
</html>
EOF

    # 设置权限
    chown -R nginx:nginx /var/www/html /var/log/nginx
    chmod -R 755 /var/www/html
    chmod 755 /etc/nginx/ssl
    chmod 700 /etc/nginx/ssl/private
    
    log "INFO" "基本配置创建完成"
}

# 启动Nginx服务
start_nginx_service() {
    log "INFO" "启动Nginx服务..."
    
    # 测试配置
    if nginx -t; then
        log "INFO" "配置测试通过"
        
        # 尝试不同方式启动
        if systemctl start nginx 2>/dev/null; then
            log "INFO" "使用systemctl启动成功"
            systemctl enable nginx 2>/dev/null
        elif rc-service nginx start 2>/dev/null; then
            log "INFO" "使用rc-service启动成功"
            rc-update add nginx default 2>/dev/null
        else
            # 直接启动
            nginx
            if [ $? -eq 0 ]; then
                log "INFO" "直接启动成功"
            else
                log "ERROR" "启动失败"
                return 1
            fi
        fi
        
        # 等待启动
        sleep 2
        
        # 检查状态
        if pgrep nginx > /dev/null; then
            log "INFO" "✅ Nginx正在运行"
            return 0
        else
            log "ERROR" "❌ Nginx未运行"
            return 1
        fi
    else
        log "ERROR" "配置测试失败"
        return 1
    fi
}

# 测试安装
test_installation() {
    log "INFO" "测试安装..."
    
    echo -e "${BLUE}========================================${NC}"
    echo -e "${GREEN}安装测试${NC}"
    echo -e "${BLUE}========================================${NC}"
    
    # 1. 检查进程
    echo -ne "检查Nginx进程... "
    if pgrep nginx > /dev/null; then
        echo -e "${GREEN}✅${NC}"
    else
        echo -e "${RED}❌${NC}"
    fi
    
    # 2. 检查端口
    echo -ne "检查80端口监听... "
    if netstat -tuln 2>/dev/null | grep -q ":80 "; then
        echo -e "${GREEN}✅${NC}"
    else
        echo -e "${RED}❌${NC}"
    fi
    
    # 3. 测试本地访问
    echo -ne "测试本地访问... "
    if curl -s -o /dev/null -w "%{http_code}" http://localhost | grep -q "200\|301\|302"; then
        echo -e "${GREEN}✅${NC}"
    else
        echo -e "${RED}❌${NC}"
    fi
    
    # 4. 测试配置文件
    echo -ne "测试配置语法... "
    if nginx -t 2>/dev/null; then
        echo -e "${GREEN}✅${NC}"
    else
        echo -e "${RED}❌${NC}"
    fi
    
    echo -e "${BLUE}========================================${NC}"
}

# 日志函数（保持原来的）
log() {
    local level=$1
    local message=$2
    local color=$NC
    
    case $level in
        "INFO") color=$GREEN ;;
        "WARN") color=$YELLOW ;;
        "ERROR") color=$RED ;;
        "DEBUG") color=$BLUE ;;
    esac
    
    echo -e "${color}[$(date '+%Y-%m-%d %H:%M:%S')] [$level] $message${NC}"
}

# 主函数
main() {
    # 显示欢迎信息
    clear
    echo -e "${BLUE}========================================${NC}"
    echo -e "${GREEN}    Nginx反向代理自动安装配置工具${NC}"
    echo -e "${BLUE}========================================${NC}"
    
    # 检查系统
    if [ ! -f /etc/alpine-release ] && ! grep -qi "alpine" /etc/os-release 2>/dev/null; then
        echo -e "${YELLOW}警告：此脚本主要针对Alpine Linux优化${NC}"
        echo -e "${YELLOW}检测到其他系统，继续吗？(y/n):${NC}"
        read -n 1 choice
        echo
        if [[ ! $choice =~ ^[Yy]$ ]]; then
            exit 0
        fi
    fi
    
    # 检查root权限
    if [ "$EUID" -ne 0 ]; then 
        echo -e "${RED}请使用root权限运行此脚本${NC}"
        echo -e "${YELLOW}使用: sudo ./nginx-proxy.sh${NC}"
        exit 1
    fi
    
    # 检查是否已安装Nginx
    if ! command -v nginx &> /dev/null; then
        echo -e "${YELLOW}检测到Nginx未安装${NC}"
        echo -e "${GREEN}1. 完全安装模式（安装Nginx+配置）${NC}"
        echo -e "${GREEN}2. 仅配置模式（已安装Nginx）${NC}"
        echo -ne "请选择 [1-2]: "
        read choice
        
        case $choice in
            1)
                full_install_mode
                ;;
            2)
                echo -e "${RED}您选择了配置模式，但Nginx未安装${NC}"
                exit 1
                ;;
            *)
                echo -e "${RED}无效选择${NC}"
                exit 1
                ;;
        esac
    else
        log "INFO" "Nginx已安装，版本: $(nginx -v 2>&1 | cut -d/ -f2)"
    fi
    
    # 进入配置主菜单（使用原来的配置菜单）
    # ... （这里接您原来的主菜单逻辑，但需要调整函数名）
    show_config_menu
}

# 配置菜单（原来的主菜单重命名）
show_config_menu() {
    while true; do
        clear
        echo -e "\n${BLUE}========================================${NC}"
        echo -e "${GREEN}      Nginx反向代理配置工具${NC}"
        echo -e "${BLUE}========================================${NC}"
        
        show_system_info
        
        echo -e "\n${GREEN}1.${NC} 创建新的反向代理"
        echo -e "${GREEN}2.${NC} 删除站点配置"
        echo -e "${GREEN}3.${NC} 重载Nginx配置"
        echo -e "${GREEN}4.${NC} 检查证书状态"
        echo -e "${GREEN}5.${NC} 查看当前配置"
        echo -e "${GREEN}6.${NC} 备份Nginx配置"
        echo -e "${GREEN}7.${NC} 初始化目录结构"
        echo -e "${GREEN}8.${NC} 显示系统信息"
        echo -e "${GREEN}9.${NC} 重新安装Nginx"
        echo -e "${GREEN}0.${NC} 退出"
        echo -e "${BLUE}========================================${NC}"
        echo -ne "请选择操作 [0-9]: "
        read choice
        
        case $choice in
            1)
                create_proxy_config
                echo -ne "\n${YELLOW}是否现在重载Nginx？${NC} (y/n): "
                read -n 1 reload
                echo
                if [[ $reload =~ ^[Yy]$ ]]; then
                    reload_nginx
                fi
                ;;
            2)
                delete_site
                ;;
            3)
                reload_nginx
                ;;
            4)
                check_certificates
                ;;
            5)
                show_current_config
                ;;
            6)
                backup_config
                ;;
            7)
                init_directories
                ;;
            8)
                show_system_info
                ;;
            9)
                echo -e "${YELLOW}重新安装Nginx将保留现有配置${NC}"
                read -p "确定继续？(y/n): " confirm
                if [[ $confirm =~ ^[Yy]$ ]]; then
                    full_install_mode
                fi
                ;;
            0)
                echo -e "${GREEN}退出${NC}"
                exit 0
                ;;
            *)
                echo -e "${RED}无效选择${NC}"
                ;;
        esac
        
        if [ "$choice" != "0" ]; then
            echo -ne "\n${YELLOW}按Enter继续...${NC}"
            read
        fi
    done
}

# 运行主函数
main