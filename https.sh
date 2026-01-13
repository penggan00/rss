#!/bin/bash

# ================= 证书管理器 =================
# 功能：申请、更新、查看泛域名证书
# 依赖：acme.sh
# ============================================

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
    mkdir -p "$CONFIG_DIR" "$LOG_DIR"
    
    # 创建证书存储目录（Nginx会从这里读取）
    mkdir -p "/etc/nginx/ssl/certs"
    mkdir -p "/etc/nginx/ssl/private"
}

# 安装和配置 acme.sh
install_acme() {
    if [ ! -d "$ACME_DIR" ]; then
        echo -e "${YELLOW}>>> 正在安装 acme.sh...${NC}"
        git clone https://github.com/acmesh-official/acme.sh.git "$ACME_DIR"
        cd "$ACME_DIR"
        
        # 询问邮箱（用于Let's Encrypt通知）
        if [ ! -f "$CONFIG_DIR/email" ]; then
            read -p "请输入邮箱用于证书通知: " LE_EMAIL
            echo "$LE_EMAIL" > "$CONFIG_DIR/email"
        else
            LE_EMAIL=$(cat "$CONFIG_DIR/email")
        fi
        
        ./acme.sh --install --home "$ACME_DIR" --accountemail "$LE_EMAIL"
        
        # 设置默认使用 Let's Encrypt，避免 ZeroSSL 需要额外注册
        ./acme.sh --set-default-ca --server letsencrypt
        
        cd "$CERT_HOME"
    else
        # 确保使用 Let's Encrypt
        cd "$ACME_DIR"
        ./acme.sh --set-default-ca --server letsencrypt 2>/dev/null || true
        cd "$CERT_HOME"
    fi
}

# 检查系统
check_deps() {
    if ! command -v curl &> /dev/null; then
        apt-get update && apt-get install -y curl git openssl || \
        apk update && apk add curl git openssl
    fi
}

# 申请新证书
issue_cert() {
    echo -e "${YELLOW}=== 申请新证书 ===${NC}"
    
    read -p "请输入主域名 (例如 example.com): " DOMAIN
    
    echo "请在 Cloudflare 创建 API Token，权限需要："
    echo "  - Zone.Zone:Read"
    echo "  - Zone.DNS:Edit"
    echo "模板选择: Edit zone DNS (模板)"
    echo "区域资源: 选择你的域名 $DOMAIN"
    echo ""
    read -p "请输入 Cloudflare API Token: " CF_TOKEN
    
    # 验证 Token
    echo -e "${YELLOW}>>> 验证 API Token...${NC}"
    API_RESPONSE=$(curl -s -X GET "https://api.cloudflare.com/client/v4/zones?name=$DOMAIN" \
        -H "Authorization: Bearer $CF_TOKEN" \
        -H "Content-Type: application/json")
    
    if ! echo "$API_RESPONSE" | grep -q "success\":true"; then
        echo -e "${RED}API Token 验证失败${NC}"
        echo "响应: $API_RESPONSE"
        return 1
    fi
    
    ZONE_ID=$(echo "$API_RESPONSE" | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
    echo -e "${GREEN}Token 验证成功，Zone ID: $ZONE_ID${NC}"
    
    # 保存配置
    echo "DOMAIN=$DOMAIN" > "$CONFIG_DIR/${DOMAIN}.env"
    echo "CF_TOKEN=$CF_TOKEN" >> "$CONFIG_DIR/${DOMAIN}.env"
    echo "ZONE_ID=$ZONE_ID" >> "$CONFIG_DIR/${DOMAIN}.env"
    echo "CERT_DIR=/etc/nginx/ssl/certs/$DOMAIN" >> "$CONFIG_DIR/${DOMAIN}.env"
    echo "KEY_DIR=/etc/nginx/ssl/private/$DOMAIN" >> "$CONFIG_DIR/${DOMAIN}.env"
    
    # 创建证书目录
    mkdir -p "/etc/nginx/ssl/certs/$DOMAIN"
    mkdir -p "/etc/nginx/ssl/private/$DOMAIN"
    
    # 导出环境变量
    export CF_Token="$CF_TOKEN"
    
    echo -e "${YELLOW}>>> 开始申请泛域名证书 *.$DOMAIN ...${NC}"
    
    # 申请证书（强制使用 Let's Encrypt）
    "$ACME_DIR"/acme.sh --issue --server letsencrypt --dns dns_cf \
        -d "$DOMAIN" -d "*.$DOMAIN" \
        --log "$LOG_DIR/acme-$(date +%Y%m%d-%H%M%S).log" \
        --force
    
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}>>> 证书申请成功!${NC}"
        
        # 安装证书到指定位置
        "$ACME_DIR"/acme.sh --install-cert -d "$DOMAIN" \
            --cert-file "/etc/nginx/ssl/certs/$DOMAIN/cert.pem" \
            --key-file "/etc/nginx/ssl/private/$DOMAIN/key.pem" \
            --fullchain-file "/etc/nginx/ssl/certs/$DOMAIN/fullchain.pem" \
            --ca-file "/etc/nginx/ssl/certs/$DOMAIN/ca.pem" \
            --reloadcmd "echo '证书已更新，请重启Nginx服务'"
        
        # 创建符号链接（方便Nginx使用）
        ln -sf "/etc/nginx/ssl/certs/$DOMAIN/fullchain.pem" "/etc/nginx/ssl/$DOMAIN.crt"
        ln -sf "/etc/nginx/ssl/private/$DOMAIN/key.pem" "/etc/nginx/ssl/$DOMAIN.key"
        
        echo -e "${GREEN}证书文件位置:${NC}"
        echo "  公钥: /etc/nginx/ssl/certs/$DOMAIN/fullchain.pem"
        echo "  私钥: /etc/nginx/ssl/private/$DOMAIN/key.pem"
        echo "  快捷方式: /etc/nginx/ssl/$DOMAIN.crt (-> fullchain.pem)"
        echo "  快捷方式: /etc/nginx/ssl/$DOMAIN.key (-> key.pem)"
        
        # 添加到域名列表
        echo "$DOMAIN" >> "$CONFIG_DIR/domains.list"
        sort -u "$CONFIG_DIR/domains.list" -o "$CONFIG_DIR/domains.list"
        
        return 0
    else
        echo -e "${RED}证书申请失败，请查看日志: $LOG_DIR/${NC}"
        
        # 提供调试建议
        echo -e "${YELLOW}调试建议:${NC}"
        echo "1. 检查 Cloudflare API Token 权限"
        echo "2. 手动测试:"
        echo "   export CF_Token=\"$CF_TOKEN\""
        echo "   $ACME_DIR/acme.sh --issue --server letsencrypt --dns dns_cf -d \"$DOMAIN\" -d \"*.$DOMAIN\" --debug"
        
        return 1
    fi
}

# 更新证书
renew_cert() {
    echo -e "${YELLOW}=== 更新证书 ===${NC}"
    
    if [ ! -f "$CONFIG_DIR/domains.list" ]; then
        echo -e "${RED}没有找到证书配置${NC}"
        return 1
    fi
    
    echo "可更新的域名:"
    cat "$CONFIG_DIR/domains.list"
    echo ""
    
    read -p "请输入要更新的域名 (或输入 'all' 更新所有): " RENEW_DOMAIN
    
    if [ "$RENEW_DOMAIN" = "all" ]; then
        while read -r DOMAIN; do
            echo -e "\n${YELLOW}>>> 更新 $DOMAIN ...${NC}"
            renew_single_cert "$DOMAIN"
        done < "$CONFIG_DIR/domains.list"
    else
        renew_single_cert "$RENEW_DOMAIN"
    fi
}

renew_single_cert() {
    local DOMAIN=$1
    
    if [ ! -f "$CONFIG_DIR/${DOMAIN}.env" ]; then
        echo -e "${RED}找不到域名 $DOMAIN 的配置${NC}"
        return 1
    fi
    
    source "$CONFIG_DIR/${DOMAIN}.env"
    export CF_Token="$CF_TOKEN"
    
    echo -e "${YELLOW}正在更新 $DOMAIN ...${NC}"
    "$ACME_DIR"/acme.sh --renew -d "$DOMAIN" --force --server letsencrypt
}

# 查看证书
list_certs() {
    echo -e "${YELLOW}=== 已管理的证书 ===${NC}"
    
    if [ ! -f "$CONFIG_DIR/domains.list" ] || [ ! -s "$CONFIG_DIR/domains.list" ]; then
        echo "暂无证书"
        return
    fi
    
    echo -e "${GREEN}域名列表:${NC}"
    cat "$CONFIG_DIR/domains.list"
    
    echo -e "\n${GREEN}证书详情:${NC}"
    while read -r DOMAIN; do
        echo ""
        echo "域名: $DOMAIN"
        
        CERT_FILE="/etc/nginx/ssl/certs/$DOMAIN/fullchain.pem"
        if [ -f "$CERT_FILE" ]; then
            echo "证书文件: $CERT_FILE"
            echo "有效期: $(openssl x509 -in "$CERT_FILE" -noout -dates 2>/dev/null | grep notAfter | cut -d= -f2)"
            
            # 检查证书签发者
            ISSUER=$(openssl x509 -in "$CERT_FILE" -noout -issuer 2>/dev/null)
            if echo "$ISSUER" | grep -q "ZeroSSL"; then
                echo "签发者: ${YELLOW}ZeroSSL${NC}"
            elif echo "$ISSUER" | grep -q "Let's Encrypt"; then
                echo "签发者: ${GREEN}Let's Encrypt${NC}"
            else
                echo "签发者: $ISSUER"
            fi
        else
            echo "${RED}证书文件不存在${NC}"
        fi
        
        ENV_FILE="$CONFIG_DIR/${DOMAIN}.env"
        if [ -f "$ENV_FILE" ]; then
            echo "配置: $ENV_FILE"
        fi
    done < "$CONFIG_DIR/domains.list"
}

# 切换 CA 提供商
switch_ca() {
    echo -e "${YELLOW}=== 切换证书颁发机构 ===${NC}"
    echo "1. Let's Encrypt (推荐，无需额外注册)"
    echo "2. ZeroSSL (需要注册邮箱)"
    echo "3. BuyPass (需要注册)"
    echo "4. Google Public CA (需要注册)"
    
    read -p "请选择: " CA_OPT
    
    case $CA_OPT in
        1)
            "$ACME_DIR"/acme.sh --set-default-ca --server letsencrypt
            echo -e "${GREEN}已切换到 Let's Encrypt${NC}"
            ;;
        2)
            echo -e "${YELLOW}注意: ZeroSSL 需要先注册账户${NC}"
            "$ACME_DIR"/acme.sh --set-default-ca --server zerossl
            echo "请运行: $ACME_DIR/acme.sh --register-account -m 你的邮箱"
            ;;
        3)
            "$ACME_DIR"/acme.sh --set-default-ca --server buypass
            echo -e "${GREEN}已切换到 BuyPass${NC}"
            ;;
        4)
            "$ACME_DIR"/acme.sh --set-default-ca --server google
            echo -e "${GREEN}已切换到 Google Public CA${NC}"
            ;;
        *)
            echo -e "${RED}无效选择${NC}"
            ;;
    esac
}

# 主菜单
main_menu() {
    init_dirs
    check_deps
    install_acme
    
    while true; do
        echo -e "\n${YELLOW}===== 证书管理器 =====${NC}"
        echo "1. 申请新证书"
        echo "2. 更新证书"
        echo "3. 查看证书"
        echo "4. 设置通知邮箱"
        echo "5. 切换 CA 提供商"
        echo "0. 退出"
        
        read -p "请选择: " OPT
        
        case $OPT in
            1) issue_cert ;;
            2) renew_cert ;;
            3) list_certs ;;
            4) 
                read -p "请输入新邮箱: " NEW_EMAIL
                echo "$NEW_EMAIL" > "$CONFIG_DIR/email"
                echo -e "${GREEN}邮箱已更新${NC}"
                # 更新 acme.sh 账户邮箱
                "$ACME_DIR"/acme.sh --update-account --accountemail "$NEW_EMAIL"
                ;;
            5) switch_ca ;;
            0) exit 0 ;;
            *) echo -e "${RED}无效选项${NC}" ;;
        esac
    done
}

main_menu