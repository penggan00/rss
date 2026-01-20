#!/bin/bash

# ================= 一键SSL证书申请脚本 =================
# 用法：bash -c "$(curl -fsSL https://raw.githubusercontent.com/penggan00/rss/main/https.sh)" 邮箱 域名 API_Token
# 示例：bash -c "$(curl -fsSL https://raw.githubusercontent.com/penggan00/rss/main/https.sh)" admin@example.com example.com your_cf_token
# ======================================================

# 配置
INSTALL_DIR="/opt/cert-manager"
ACME_DIR="$INSTALL_DIR/acme.sh"
CONFIG_DIR="$INSTALL_DIR/config"
LOG_DIR="$INSTALL_DIR/logs"

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# 显示帮助
show_help() {
    echo -e "${YELLOW}一键SSL证书申请脚本${NC}"
    echo ""
    echo "用法："
    echo "  bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/penggan00/rss/main/https.sh)\" [选项]"
    echo ""
    echo "选项："
    echo "  -e, --email    邮箱地址 (用于证书通知)"
    echo "  -d, --domain   主域名 (例如: example.com)"
    echo "  -t, --token    Cloudflare API Token"
    echo "  -h, --help     显示帮助信息"
    echo ""
    echo "示例："
    echo "  bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/penggan00/rss/main/https.sh)\" -e admin@example.com -d example.com -t your_token"
    echo "  bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/penggan00/rss/main/https.sh)\" --email admin@example.com --domain example.com --token your_token"
    echo ""
    echo "注意："
    echo "  1. Cloudflare API Token 需要 DNS 编辑权限"
    echo "  2. 域名必须在 Cloudflare 管理"
    echo ""
    echo "支持的系统："
    echo "  ✅ Debian 9/10/11/12"
    echo "  ✅ Ubuntu 18.04/20.04/22.04"
    echo "  ✅ Alpine Linux"
    echo "  ✅ CentOS/RHEL 7/8/9"
}

# 检测操作系统
detect_os() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        OS=$ID
        VERSION=$VERSION_ID
    elif [ -f /etc/debian_version ]; then
        OS="debian"
        VERSION=$(cat /etc/debian_version)
    elif [ -f /etc/alpine-release ]; then
        OS="alpine"
        VERSION=$(cat /etc/alpine-release)
    else
        OS=$(uname -s)
        VERSION=$(uname -r)
    fi
    
    echo "检测到系统: $OS $VERSION"
}

# 解析命令行参数
parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            -e|--email)
                EMAIL="$2"
                shift 2
                ;;
            -d|--domain)
                DOMAIN="$2"
                shift 2
                ;;
            -t|--token)
                CF_TOKEN="$2"
                shift 2
                ;;
            -h|--help)
                show_help
                exit 0
                ;;
            *)
                # 如果没有参数标识，按顺序解析
                if [[ -z "$EMAIL" ]]; then
                    EMAIL="$1"
                elif [[ -z "$DOMAIN" ]]; then
                    DOMAIN="$1"
                elif [[ -z "$CF_TOKEN" ]]; then
                    CF_TOKEN="$1"
                fi
                shift
                ;;
        esac
    done
}

# 检查必需参数
check_params() {
    if [[ -z "$EMAIL" ]]; then
        read -p "请输入邮箱地址: " EMAIL
    fi
    
    if [[ -z "$DOMAIN" ]]; then
        read -p "请输入主域名 (例如 example.com): " DOMAIN
    fi
    
    if [[ -z "$CF_TOKEN" ]]; then
        echo "请在 Cloudflare 创建 API Token，权限需要："
        echo "  - Zone.Zone:Read"
        echo "  - Zone.DNS:Edit"
        echo "模板选择: Edit zone DNS (模板)"
        echo "区域资源: 选择你的域名 $DOMAIN"
        echo ""
        read -p "请输入 Cloudflare API Token: " CF_TOKEN
    fi
}

# 检查 Root 权限
check_root() {
    if [ "$(id -u)" -ne 0 ]; then
        echo -e "${RED}错误: 必须使用 root 权限运行此脚本${NC}"
        exit 1
    fi
}

# 安装依赖
install_deps() {
    echo -e "${YELLOW}>>> 安装依赖...${NC}"
    
    # 检测操作系统并安装相应依赖
    detect_os
    
    case $OS in
        debian|ubuntu)
            echo "检测到 Debian/Ubuntu 系统"
            apt-get update
            apt-get install -y curl git openssl nginx cron
            ;;
        alpine)
            echo "检测到 Alpine Linux 系统"
            apk update
            apk add curl git openssl nginx busybox-initscripts busybox-suid
            ;;
        centos|rhel|fedora)
            echo "检测到 CentOS/RHEL/Fedora 系统"
            yum install -y curl git openssl nginx crontabs
            ;;
        *)
            echo "未知系统，尝试安装基础依赖..."
            # 尝试通用安装
            if command -v apt-get &> /dev/null; then
                apt-get update && apt-get install -y curl git openssl nginx cron
            elif command -v yum &> /dev/null; then
                yum install -y curl git openssl nginx crontabs
            elif command -v apk &> /dev/null; then
                apk update && apk add curl git openssl nginx busybox-initscripts busybox-suid
            else
                echo -e "${RED}无法安装依赖，请手动安装 curl, git, openssl, nginx${NC}"
                exit 1
            fi
            ;;
    esac
    
    echo -e "${GREEN}>>> 依赖安装完成${NC}"
}

# 安装 acme.sh
install_acme() {
    echo -e "${YELLOW}>>> 安装 acme.sh...${NC}"
    
    mkdir -p "$INSTALL_DIR" "$CONFIG_DIR" "$LOG_DIR"
    
    if [ ! -d "$ACME_DIR" ]; then
        rm -rf "$ACME_DIR"
        git clone https://github.com/acmesh-official/acme.sh.git "$ACME_DIR"
    fi
    
    cd "$ACME_DIR"
    
    # 安装 acme.sh
    ./acme.sh --install --home "$ACME_DIR" --accountemail "$EMAIL"
    
    # 强制使用 Let's Encrypt
    ./acme.sh --set-default-ca --server letsencrypt
    
    echo -e "${GREEN}>>> acme.sh 安装完成${NC}"
    
    # ==================== 设置自动续期 ====================
    echo -e "${YELLOW}>>> 设置自动续期...${NC}"
    
    # 创建续期脚本
    cat > "/opt/cert-manager/renew-all.sh" << 'EOF'
#!/bin/bash

# 设置日志文件
LOG_FILE="/opt/cert-manager/logs/renewal-$(date +%Y%m%d-%H%M%S).log"

echo "===== 证书续期检查开始 $(date) =====" >> "$LOG_FILE"
echo "系统: $(uname -a)" >> "$LOG_FILE"

# 加载所有域名的配置
if [ -f "/opt/cert-manager/config/domains.list" ]; then
    while read DOMAIN; do
        if [ -n "$DOMAIN" ]; then
            CONFIG_FILE="/opt/cert-manager/config/${DOMAIN}.env"
            if [ -f "$CONFIG_FILE" ]; then
                # 加载域名特定的配置
                . "$CONFIG_FILE"
                
                echo "检查域名: $DOMAIN" >> "$LOG_FILE"
                
                # 运行 acme.sh 续期检查
                cd /opt/cert-manager/acme.sh
                export CF_Token="$CF_TOKEN"
                
                # 检查并续期证书（过期前30天自动续期）
                ./acme.sh --renew -d "$DOMAIN" --days 30 --home "/opt/cert-manager/acme.sh" 2>&1 >> "$LOG_FILE"
                
                RENEW_RESULT=$?
                if [ $RENEW_RESULT -eq 0 ]; then
                    echo "✅ $DOMAIN 证书续期成功" >> "$LOG_FILE"
                    
                    # 重新安装证书到 Nginx
                    ./acme.sh --install-cert -d "$DOMAIN" \
                        --key-file "/etc/nginx/ssl/private/$DOMAIN/key.pem" \
                        --fullchain-file "/etc/nginx/ssl/certs/$DOMAIN/fullchain.pem" \
                        --reloadcmd "systemctl reload nginx 2>/dev/null || nginx -s reload 2>/dev/null || rc-service nginx restart 2>/dev/null" \
                        --force 2>&1 >> "$LOG_FILE"
                else
                    echo "⚠️  $DOMAIN 证书尚未需要续期" >> "$LOG_FILE"
                fi
            fi
        fi
    done < "/opt/cert-manager/config/domains.list"
fi

echo "===== 证书续期检查结束 $(date) =====" >> "$LOG_FILE"

# 清理30天前的日志
find /opt/cert-manager/logs -name "renewal-*.log" -mtime +30 -delete 2>/dev/null || true
EOF
    
    # 设置执行权限
    chmod +x "/opt/cert-manager/renew-all.sh"
    
    # 设置定时任务
    setup_cron_job
}

# 设置定时任务（根据系统类型）
setup_cron_job() {
    echo -e "${YELLOW}>>> 配置定时任务...${NC}"
    
    detect_os
    
    # 根据系统类型设置定时任务
    case $OS in
        debian|ubuntu|centos|rhel|fedora)
            # 使用 cron
            echo "0 2 * * * /opt/cert-manager/renew-all.sh" >> /etc/crontab
            systemctl restart cron 2>/dev/null || systemctl restart crond 2>/dev/null || true
            echo "已添加到 /etc/crontab"
            ;;
        alpine)
            # Alpine 使用 crond
            echo "0 2 * * * /opt/cert-manager/renew-all.sh" >> /etc/crontabs/root
            rc-service crond restart 2>/dev/null || true
            echo "已添加到 /etc/crontabs/root"
            ;;
        *)
            # 通用方法
            (crontab -l 2>/dev/null; echo "0 2 * * * /opt/cert-manager/renew-all.sh") | crontab -
            echo "已添加到用户 crontab"
            ;;
    esac
    
    echo -e "${GREEN}>>> 已设置自动续期${NC}"
    echo -e "  - 每天凌晨2点自动检查"
    echo -e "  - 证书过期前30天自动续期"
    echo -e "  - 续期日志: /opt/cert-manager/logs/renewal-*.log"
}

# 验证 API Token
verify_token() {
    echo -e "${YELLOW}>>> 验证 API Token...${NC}"
    
    # 获取 Zone ID
    API_RESPONSE=$(curl -s -X GET "https://api.cloudflare.com/client/v4/zones?name=$DOMAIN" \
        -H "Authorization: Bearer $CF_TOKEN" \
        -H "Content-Type: application/json")
    
    if ! echo "$API_RESPONSE" | grep -q "success\":true"; then
        echo -e "${RED}API Token 验证失败${NC}"
        echo "响应: $API_RESPONSE"
        return 1
    fi
    
    ZONE_ID=$(echo "$API_RESPONSE" | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
    echo -e "${GREEN}>>> Token 验证成功，Zone ID: $ZONE_ID${NC}"
    
    # 保存配置
    mkdir -p "$CONFIG_DIR"
    cat > "$CONFIG_DIR/${DOMAIN}.env" <<EOF
EMAIL=$EMAIL
DOMAIN=$DOMAIN
CF_TOKEN=$CF_TOKEN
ZONE_ID=$ZONE_ID
CERT_DIR=/etc/nginx/ssl/certs/$DOMAIN
KEY_DIR=/etc/nginx/ssl/private/$DOMAIN
EOF
    
    echo "$DOMAIN" >> "$CONFIG_DIR/domains.list"
    sort -u "$CONFIG_DIR/domains.list" -o "$CONFIG_DIR/domains.list"
    
    return 0
}

# 申请证书
issue_certificate() {
    echo -e "${YELLOW}>>> 开始申请泛域名证书 *.$DOMAIN ...${NC}"
    
    # 设置环境变量
    export CF_Token="$CF_TOKEN"
    
    cd "$ACME_DIR"
    
    # 申请证书
    LOG_FILE="$LOG_DIR/acme-$(date +%Y%m%d-%H%M%S).log"
    
    ./acme.sh --issue --server letsencrypt --dns dns_cf \
        -d "$DOMAIN" -d "*.$DOMAIN" \
        --log "$LOG_FILE" \
        --force
    
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}>>> 证书申请成功!${NC}"
        return 0
    else
        echo -e "${RED}>>> 证书申请失败${NC}"
        echo "请查看日志文件: $LOG_FILE"
        return 1
    fi
}

# 安装证书到 Nginx
install_certificate() {
    echo -e "${YELLOW}>>> 安装证书到 Nginx...${NC}"
    
    # 创建证书目录
    mkdir -p "/etc/nginx/ssl/certs/$DOMAIN"
    mkdir -p "/etc/nginx/ssl/private/$DOMAIN"
    mkdir -p "/etc/nginx/ssl"
    
    cd "$ACME_DIR"
    
    # 安装证书
    ./acme.sh --install-cert -d "$DOMAIN" \
        --key-file "/etc/nginx/ssl/private/$DOMAIN/key.pem" \
        --fullchain-file "/etc/nginx/ssl/certs/$DOMAIN/fullchain.pem" \
        --cert-file "/etc/nginx/ssl/certs/$DOMAIN/cert.pem" \
        --ca-file "/etc/nginx/ssl/certs/$DOMAIN/ca.pem" \
        --reloadcmd "systemctl reload nginx 2>/dev/null || nginx -s reload 2>/dev/null || rc-service nginx restart 2>/dev/null || true"
    
    # 设置权限
    chmod 644 "/etc/nginx/ssl/certs/$DOMAIN"/*.pem
    chmod 600 "/etc/nginx/ssl/private/$DOMAIN/key.pem"
    
    # 创建符号链接
    ln -sf "/etc/nginx/ssl/certs/$DOMAIN/fullchain.pem" "/etc/nginx/ssl/$DOMAIN.crt"
    ln -sf "/etc/nginx/ssl/private/$DOMAIN/key.pem" "/etc/nginx/ssl/$DOMAIN.key"
    
    ln -sf "/etc/nginx/ssl/certs/$DOMAIN/fullchain.pem" "/etc/ssl/certs/$DOMAIN.crt"
    ln -sf "/etc/nginx/ssl/private/$DOMAIN/key.pem" "/etc/ssl/private/$DOMAIN.key"
    
    ln -sf "/etc/nginx/ssl/certs/$DOMAIN/fullchain.pem" "/etc/ssl/certs/$DOMAIN.pem"
    ln -sf "/etc/nginx/ssl/private/$DOMAIN/key.pem" "/etc/ssl/private/$DOMAIN.pem.key"
    
    mkdir -p "/etc/pki/tls/certs" "/etc/pki/tls/private"
    ln -sf "/etc/nginx/ssl/certs/$DOMAIN/fullchain.pem" "/etc/pki/tls/certs/$DOMAIN.crt"
    ln -sf "/etc/nginx/ssl/private/$DOMAIN/key.pem" "/etc/pki/tls/private/$DOMAIN.key"
    
    echo -e "${GREEN}>>> 证书安装完成${NC}"
}

# 检查证书续期状态
check_renewal_status() {
    echo -e "${YELLOW}>>> 检查证书续期状态...${NC}"
    
    # 检查定时任务
    echo "当前定时任务:"
    detect_os
    
    case $OS in
        debian|ubuntu|centos|rhel|fedora)
            cat /etc/crontab 2>/dev/null | grep renew-all || echo "没有找到定时任务"
            ;;
        alpine)
            cat /etc/crontabs/root 2>/dev/null | grep renew-all || echo "没有找到定时任务"
            ;;
        *)
            crontab -l 2>/dev/null | grep renew-all || echo "没有找到定时任务"
            ;;
    esac
    
    # 检查证书过期时间
    if [ -f "/etc/nginx/ssl/certs/$DOMAIN/cert.pem" ]; then
        echo ""
        echo "证书过期时间:"
        openssl x509 -in "/etc/nginx/ssl/certs/$DOMAIN/cert.pem" -noout -dates
        
        # 计算剩余天数
        expiry_date=$(openssl x509 -in "/etc/nginx/ssl/certs/$DOMAIN/cert.pem" -noout -enddate | cut -d= -f2)
        expiry_epoch=$(date -d "$expiry_date" +%s 2>/dev/null || date -j -f "%b %d %T %Y %Z" "$expiry_date" +%s 2>/dev/null)
        current_epoch=$(date +%s)
        days_left=$(( (expiry_epoch - current_epoch) / 86400 ))
        
        echo ""
        echo "证书剩余天数: $days_left 天"
        
        if [ $days_left -le 30 ]; then
            echo "⚠️  证书将在 $days_left 天后过期，将在过期前自动续期"
        else
            echo "✅ 证书状态良好，还有 $days_left 天过期"
        fi
    fi
    
    # 检查续期脚本是否存在
    if [ -f "/opt/cert-manager/renew-all.sh" ]; then
        echo ""
        echo "✅ 续期脚本已安装: /opt/cert-manager/renew-all.sh"
    else
        echo ""
        echo "❌ 续期脚本未找到"
    fi
    
    # 检查定时服务状态
    if command -v systemctl &> /dev/null; then
        if systemctl is-active cron &>/dev/null || systemctl is-active crond &>/dev/null; then
            echo "✅ 定时服务正在运行"
        else
            echo "❌ 定时服务未运行"
        fi
    elif command -v rc-service &> /dev/null; then
        if rc-service crond status &>/dev/null; then
            echo "✅ crond 服务正在运行"
        else
            echo "❌ crond 服务未运行"
        fi
    fi
}

# 配置 Nginx 默认站点
configure_nginx() {
    echo -e "${YELLOW}>>> 配置 Nginx...${NC}"
    
    # 创建 Nginx 配置目录
    mkdir -p /etc/nginx/conf.d
    
    # 生成默认的 fallback 证书
    if [ ! -f "/etc/nginx/ssl/fallback.key" ]; then
        openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
            -keyout "/etc/nginx/ssl/fallback.key" \
            -out "/etc/nginx/ssl/fallback.crt" \
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
    
    # 启动或重启 Nginx
    detect_os
    
    case $OS in
        debian|ubuntu|centos|rhel|fedora)
            systemctl restart nginx 2>/dev/null || nginx -s reload 2>/dev/null || nginx
            ;;
        alpine)
            rc-service nginx restart 2>/dev/null || nginx -s reload 2>/dev/null || nginx
            ;;
        *)
            nginx -s reload 2>/dev/null || nginx
            ;;
    esac
    
    echo -e "${GREEN}>>> Nginx 配置完成${NC}"
}

# 显示成功信息
show_success() {
    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}        SSL 证书申请成功！              ${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo ""
    echo "域名: $DOMAIN"
    echo "泛域名: *.$DOMAIN"
    echo ""
    echo "证书文件位置:"
    echo "  公钥: /etc/nginx/ssl/certs/$DOMAIN/fullchain.pem"
    echo "  私钥: /etc/nginx/ssl/private/$DOMAIN/key.pem"
    echo "  快捷方式: /etc/nginx/ssl/$DOMAIN.crt"
    echo "  快捷方式: /etc/nginx/ssl/$DOMAIN.key"
    echo ""
    echo "自动续期设置:"
    echo "  ✅ 已设置自动续期"
    echo "  ✅ 每天凌晨2点检查证书"
    echo "  ✅ 证书过期前30天自动续期"
    echo "  ✅ 续期后自动重载 Nginx"
    echo "  ✅ 续期脚本: /opt/cert-manager/renew-all.sh"
    echo ""
    echo "续期日志位置:"
    echo "  /opt/cert-manager/logs/renewal-*.log"
    echo ""
    echo "手动续期命令:"
    echo "  cd $ACME_DIR && export CF_Token=\"$CF_TOKEN\" && ./acme.sh --renew -d \"$DOMAIN\" --days 30"
    echo ""
    echo "查看续期状态:"
    echo "  cat /etc/crontab"
    echo "  tail -f /opt/cert-manager/logs/renewal-*.log"
    echo ""
    echo "支持的系统:"
    echo "  ✅ Debian/Ubuntu"
    echo "  ✅ Alpine Linux"
    echo "  ✅ CentOS/RHEL"
    echo ""
}

# 主函数
main() {
    echo -e "${YELLOW}========================================${NC}"
    echo -e "${YELLOW}        SSL 证书一键申请工具            ${NC}"
    echo -e "${YELLOW}========================================${NC}"
    echo ""
    
    # 解析参数
    parse_args "$@"
    
    # 检查参数
    check_params
    
    # 显示配置信息
    echo ""
    echo -e "${YELLOW}配置信息:${NC}"
    echo "  邮箱: $EMAIL"
    echo "  域名: $DOMAIN"
    echo "  Token: ${CF_TOKEN:0:10}..."
    echo ""
    
    # 检查 root 权限
    check_root
    
    # 安装依赖
    install_deps
    
    # 安装 acme.sh（包含自动续期设置）
    install_acme
    
    # 验证 API Token
    if ! verify_token; then
        exit 1
    fi
    
    # 申请证书
    if ! issue_certificate; then
        exit 1
    fi
    
    # 安装证书
    install_certificate
    
    # 配置 Nginx
    configure_nginx
    
    # 检查续期状态
    check_renewal_status
    
    # 显示成功信息
    show_success
}

# 运行主函数
main "$@"