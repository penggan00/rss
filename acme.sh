#!/bin/bash

# ================= 修复版证书管理器 =================
# 强制使用 Let's Encrypt，避免 ZeroSSL 注册问题
# =================================================

CERT_HOME="/opt/cert-manager"
ACME_DIR="$CERT_HOME/acme.sh"
CONFIG_DIR="$CERT_HOME/config"
LOG_DIR="$CERT_HOME/logs"

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

# 初始化目录
init_dirs() {
    echo -e "${YELLOW}>>> 初始化目录...${NC}"
    mkdir -p "$CONFIG_DIR" "$LOG_DIR" "$CERT_HOME"
    mkdir -p "/etc/nginx/ssl/certs"
    mkdir -p "/etc/nginx/ssl/private"
}

# 安装 acme.sh（强制使用 Let's Encrypt）
install_acme() {
    echo -e "${YELLOW}>>> 安装 acme.sh...${NC}"
    
    # 安装依赖
    if command -v apt-get &> /dev/null; then
        apt-get update && apt-get install -y curl git openssl
    elif command -v apk &> /dev/null; then
        apk update && apk add curl git openssl
    fi
    
    # 删除旧版本
    rm -rf "$ACME_DIR"
    
    # 克隆最新版本
    git clone https://github.com/acmesh-official/acme.sh.git "$ACME_DIR"
    
    # 询问邮箱
    if [ ! -f "$CONFIG_DIR/email" ]; then
        read -p "请输入邮箱用于证书通知: " LE_EMAIL
        echo "$LE_EMAIL" > "$CONFIG_DIR/email"
    else
        LE_EMAIL=$(cat "$CONFIG_DIR/email")
    fi
    
    echo -e "${YELLOW}>>> 安装 acme.sh 并配置 Let's Encrypt...${NC}"
    
    # 重要：安装时直接指定使用 Let's Encrypt
    cd "$ACME_DIR"
    
    # 方法1：直接编辑默认配置
    cat > /tmp/force-letsencrypt.sh <<'INNER_EOF'
#!/bin/bash
# 强制使用 Let's Encrypt 的安装脚本

# 先正常安装
./acme.sh --install --home "$ACME_DIR" --accountemail "$LE_EMAIL"

# 立即切换到 Let's Encrypt
./acme.sh --set-default-ca --server letsencrypt

# 创建配置文件确保始终使用 Let's Encrypt
cat > ~/.acme.sh/account.conf <<'CONF_EOF'
AUTO_UPGRADE="1"
DEFAULT_ACME_SERVER="letsencrypt"
CONF_EOF

echo "已强制配置为使用 Let's Encrypt"
INNER_EOF
    
    chmod +x /tmp/force-letsencrypt.sh
    ACME_DIR="$ACME_DIR" LE_EMAIL="$LE_EMAIL" /tmp/force-letsencrypt.sh
    
    cd "$CERT_HOME"
    
    echo -e "${GREEN}>>> acme.sh 安装完成，已配置为使用 Let's Encrypt${NC}"
}

# 申请证书（强制使用 Let's Encrypt）
issue_cert() {
    echo -e "${YELLOW}=== 申请新证书 ===${NC}"
    
    read -p "请输入主域名 (例如 example.com): " DOMAIN
    
    echo -e "${YELLOW}Cloudflare API Token 权限要求:${NC}"
    echo "1. 登录 Cloudflare 控制台"
    echo "2. 进入 My Profile > API Tokens"
    echo "3. 创建令牌：使用 'Edit zone DNS' 模板"
    echo "4. 权限需要: Zone.Zone:Read, Zone.DNS:Edit"
    echo "5. 区域资源: 选择域名 $DOMAIN"
    echo ""
    read -p "请输入 Cloudflare API Token: " CF_TOKEN
    
    # 验证 Token
    echo -e "${YELLOW}>>> 验证 API Token...${NC}"
    API_RESPONSE=$(curl -s -X GET "https://api.cloudflare.com/client/v4/zones?name=$DOMAIN" \
        -H "Authorization: Bearer $CF_TOKEN" \
        -H "Content-Type: application/json" \
        -w "\nHTTP_STATUS:%{http_code}")
    
    if ! echo "$API_RESPONSE" | grep -q "success\":true"; then
        echo -e "${RED}API Token 验证失败${NC}"
        echo "请检查:"
        echo "1. Token 是否正确"
        echo "2. Token 是否有 DNS 编辑权限"
        echo "3. 域名是否在 Cloudflare 账户中"
        return 1
    fi
    
    ZONE_ID=$(echo "$API_RESPONSE" | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
    echo -e "${GREEN}✓ Token 验证成功${NC}"
    echo -e "${GREEN}✓ Zone ID: $ZONE_ID${NC}"
    
    # 保存配置
    cat > "$CONFIG_DIR/${DOMAIN}.env" <<CONF
DOMAIN=$DOMAIN
CF_TOKEN=$CF_TOKEN
ZONE_ID=$ZONE_ID
CERT_DIR=/etc/nginx/ssl/certs/$DOMAIN
KEY_DIR=/etc/nginx/ssl/private/$DOMAIN
CONF
    
    # 创建证书目录
    mkdir -p "/etc/nginx/ssl/certs/$DOMAIN"
    mkdir -p "/etc/nginx/ssl/private/$DOMAIN"
    
    # 导出环境变量
    export CF_Token="$CF_TOKEN"
    
    echo -e "${YELLOW}>>> 开始申请泛域名证书...${NC}"
    echo "域名: $DOMAIN"
    echo "泛域名: *.$DOMAIN"
    
    # 手动设置使用 Let's Encrypt，避免任何 ZeroSSL 问题
    export DEFAULT_ACME_SERVER="letsencrypt"
    
    # 使用详细的调试模式
    cd "$ACME_DIR"
    
    LOG_FILE="$LOG_DIR/acme-$(date +%Y%m%d-%H%M%S).log"
    echo -e "${YELLOW}>>> 详细日志: $LOG_FILE${NC}"
    
    # 执行申请（强制使用 Let's Encrypt）
    ./acme.sh --issue --server letsencrypt --dns dns_cf \
        -d "$DOMAIN" -d "*.$DOMAIN" \
        --debug 2 --log "$LOG_FILE" \
        --force
    
    RESULT=$?
    
    if [ $RESULT -eq 0 ]; then
        echo -e "${GREEN}>>> ✓ 证书申请成功!${NC}"
        
        # 安装证书
        echo -e "${YELLOW}>>> 安装证书到 Nginx 目录...${NC}"
        ./acme.sh --install-cert -d "$DOMAIN" \
            --cert-file "/etc/nginx/ssl/certs/$DOMAIN/cert.pem" \
            --key-file "/etc/nginx/ssl/private/$DOMAIN/key.pem" \
            --fullchain-file "/etc/nginx/ssl/certs/$DOMAIN/fullchain.pem" \
            --reloadcmd "echo '证书安装完成'"
        
        # 创建符号链接
        ln -sf "/etc/nginx/ssl/certs/$DOMAIN/fullchain.pem" "/etc/nginx/ssl/$DOMAIN.crt" 2>/dev/null
        ln -sf "/etc/nginx/ssl/private/$DOMAIN/key.pem" "/etc/nginx/ssl/$DOMAIN.key" 2>/dev/null
        
        echo -e "${GREEN}>>> 证书文件位置:${NC}"
        ls -la "/etc/nginx/ssl/certs/$DOMAIN/"
        ls -la "/etc/nginx/ssl/private/$DOMAIN/"
        
        # 添加到域名列表
        echo "$DOMAIN" >> "$CONFIG_DIR/domains.list"
        sort -u "$CONFIG_DIR/domains.list" -o "$CONFIG_DIR/domains.list"
        
        echo -e "${GREEN}>>> 证书申请完成！现在可以使用 nginx-proxy 配置反向代理了。${NC}"
        return 0
    else
        echo -e "${RED}>>> ✗ 证书申请失败${NC}"
        echo "请查看日志文件: $LOG_FILE"
        echo "或尝试手动调试:"
        echo "cd $ACME_DIR"
        echo "export CF_Token=\"YOUR_TOKEN\""
        echo "./acme.sh --issue --server letsencrypt --dns dns_cf -d \"$DOMAIN\" -d \"*.$DOMAIN\" --debug"
        return 1
    fi
}

# 查看证书
list_certs() {
    echo -e "${YELLOW}=== 证书状态 ===${NC}"
    
    if [ -f "$CONFIG_DIR/domains.list" ]; then
        echo "已配置域名:"
        cat "$CONFIG_DIR/domains.list"
    else
        echo "暂无证书配置"
    fi
    
    echo ""
    echo "证书文件检查:"
    if [ -d "/etc/nginx/ssl/certs" ]; then
        find "/etc/nginx/ssl/certs" -name "*.pem" -type f | while read -r cert; do
            echo ""
            echo "证书: $cert"
            if [ -f "$cert" ]; then
                VALID_TO=$(openssl x509 -in "$cert" -noout -enddate 2>/dev/null | cut -d= -f2)
                ISSUER=$(openssl x509 -in "$cert" -noout -issuer 2>/dev/null | cut -d= -f2-)
                echo "有效期至: $VALID_TO"
                echo "颁发者: $ISSUER"
            fi
        done
    fi
}

# 手动切换 CA
force_switch_ca() {
    echo -e "${YELLOW}>>> 强制切换到 Let's Encrypt...${NC}"
    cd "$ACME_DIR"
    
    # 方法1：直接修改配置文件
    cat > ~/.acme.sh/account.conf <<'ACCOUNT_CONF'
AUTO_UPGRADE="1"
DEFAULT_ACME_SERVER="letsencrypt"
ACCOUNT_CONF
    
    # 方法2：执行切换命令
    ./acme.sh --set-default-ca --server letsencrypt
    
    echo -e "${GREEN}>>> 已强制切换到 Let's Encrypt${NC}"
    
    # 验证
    CURRENT_CA=$(./acme.sh --info | grep "SERVER" | head -1)
    echo "当前CA: $CURRENT_CA"
}

# 主菜单
main_menu() {
    init_dirs
    
    while true; do
        echo -e "\n${YELLOW}===== 证书管理器（修复版）=====${NC}"
        echo "1. 安装/重装 acme.sh（强制 Let's Encrypt）"
        echo "2. 申请新证书"
        echo "3. 查看证书状态"
        echo "4. 强制切换到 Let's Encrypt"
        echo "5. 测试 API Token"
        echo "0. 退出"
        
        read -p "请选择: " OPT
        
        case $OPT in
            1) install_acme ;;
            2) issue_cert ;;
            3) list_certs ;;
            4) force_switch_ca ;;
            5)
                read -p "输入域名: " TEST_DOMAIN
                read -p "输入 API Token: " TEST_TOKEN
                echo "测试 Token..."
                curl -s -X GET "https://api.cloudflare.com/client/v4/zones?name=$TEST_DOMAIN" \
                    -H "Authorization: Bearer $TEST_TOKEN" \
                    -H "Content-Type: application/json" | jq .
                ;;
            0) exit 0 ;;
            *) echo -e "${RED}无效选项${NC}" ;;
        esac
    done
}

main_menu