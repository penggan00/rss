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
    if command -v apt-get &> /dev/null; then
        apt-get update && apt-get install -y curl git openssl nginx
    elif command -v apk &> /dev/null; then
        apk update && apk add curl git openssl nginx
    fi
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
    mkdir -p "/ygkkkca/"
    
    cd "$ACME_DIR"
    
    # 安装证书
    ./acme.sh --install-cert -d "$DOMAIN" \
        --key-file "/etc/nginx/ssl/private/$DOMAIN/key.pem" \
        --fullchain-file "/etc/nginx/ssl/certs/$DOMAIN/fullchain.pem" \
        --cert-file "/etc/nginx/ssl/certs/$DOMAIN/cert.pem" \
        --ca-file "/etc/nginx/ssl/certs/$DOMAIN/ca.pem" \
        --reloadcmd "echo '证书安装完成'"
    
    # 设置权限
    chmod 644 "/etc/nginx/ssl/certs/$DOMAIN"/*.pem
    chmod 600 "/etc/nginx/ssl/private/$DOMAIN/key.pem"
    
    # 创建符号链接
    
    # 1. Nginx专用链接
    ln -sf "/etc/nginx/ssl/certs/$DOMAIN/fullchain.pem" "/etc/nginx/ssl/$DOMAIN.crt"
    ln -sf "/etc/nginx/ssl/private/$DOMAIN/key.pem" "/etc/nginx/ssl/$DOMAIN.key"
    
    # 2. 系统标准位置链接
    ln -sf "/etc/nginx/ssl/certs/$DOMAIN/fullchain.pem" "/etc/ssl/certs/$DOMAIN.crt"
    ln -sf "/etc/nginx/ssl/private/$DOMAIN/key.pem" "/etc/ssl/private/$DOMAIN.key"
    
    # 3. 系统标准PEM格式链接（兼容性）
    ln -sf "/etc/nginx/ssl/certs/$DOMAIN/fullchain.pem" "/etc/ssl/certs/$DOMAIN.pem"
    ln -sf "/etc/nginx/ssl/private/$DOMAIN/key.pem" "/etc/ssl/private/$DOMAIN.pem.key"
    
    # 4. PKI标准路径
    mkdir -p "/etc/pki/tls/certs"
    mkdir -p "/etc/pki/tls/private"
    ln -sf "/etc/nginx/ssl/certs/$DOMAIN/fullchain.pem" "/etc/pki/tls/certs/$DOMAIN.crt"
    ln -sf "/etc/nginx/ssl/private/$DOMAIN/key.pem" "/etc/pki/tls/private/$DOMAIN.key"
    
    # 5. 创建到你指定目录的软链接
   # echo -e "${YELLOW}>>> 创建到指定目录的软链接...${NC}"
  #  mkdir -p "/root/ygkkkca"
    
    # 公钥链接到指定路径
   # ln -sf "/etc/nginx/ssl/certs/$DOMAIN/fullchain.pem" "/root/ygkkkca/cert.crt"
    
    # 私钥链接到指定路径
   # ln -sf "/etc/nginx/ssl/private/$DOMAIN/key.pem" "/root/ygkkkca/private.key"
    
    echo -e "${GREEN}>>> 证书安装完成${NC}"
    
    # 显示重要的链接信息
    echo ""
    echo -e "${YELLOW}证书链接位置：${NC}"
    echo "1. 你指定的路径："
   # echo "   公钥: /root/ygkkkca/cert.crt"
   # echo "   私钥: /root/ygkkkca/private.key"
   # echo ""
    echo "2. 系统标准路径："
    echo "   公钥: /etc/ssl/certs/$DOMAIN.crt"
    echo "   私钥: /etc/ssl/private/$DOMAIN.key"
    echo ""
    echo "3. Nginx专用路径："
    echo "   公钥: /etc/nginx/ssl/$DOMAIN.crt"
    echo "   私钥: /etc/nginx/ssl/$DOMAIN.key"
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
    if command -v systemctl &> /dev/null && systemctl is-active nginx &>/dev/null; then
        systemctl restart nginx
    elif command -v rc-service &> /dev/null && rc-service nginx status &>/dev/null; then
        rc-service nginx restart
    else
        pkill nginx 2>/dev/null
        nginx
    fi
    
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
    echo "现在可以使用证书配置反向代理了！"
    echo ""
    echo "下次更新证书命令:"
    echo "  cd $ACME_DIR && export CF_Token=\"$CF_TOKEN\" && ./acme.sh --renew -d \"$DOMAIN\" --force"
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
    
    # 安装 acme.sh
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
    
    # 显示成功信息
    show_success
}

# 运行主函数
main "$@"