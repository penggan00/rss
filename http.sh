#!/bin/bash

# ================= 简化版SSL证书申请脚本 =================
# 用法：bash -c "$(curl -fsSL https://raw.githubusercontent.com/penggan00/rss/main/https-simple.sh)" 邮箱 域名 API_Token
# 示例：bash -c "$(curl -fsSL https://raw.githubusercontent.com/penggan00/rss/main/https-simple.sh)" admin@example.com example.com your_cf_token
# ========================================================

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# 显示帮助
show_help() {
    echo -e "${GREEN}简化版SSL证书申请脚本${NC}"
    echo ""
    echo "用法:"
    echo "  bash -c \"\$(curl -fsSL URL)\" 邮箱 域名 Token"
    echo ""
    echo "示例:"
    echo "  bash -c \"\$(curl -fsSL URL)\" admin@example.com example.com your_token"
    echo ""
    echo "注意:"
    echo "  Cloudflare Token需要 DNS 编辑权限"
}

# 检查参数
if [ $# -ne 3 ]; then
    show_help
    exit 1
fi

EMAIL="$1"
DOMAIN="$2"
CF_TOKEN="$3"

echo -e "${GREEN}================================${NC}"
echo -e "${GREEN}    SSL证书申请开始             ${NC}"
echo -e "${GREEN}================================${NC}"
echo ""
echo "域名: $DOMAIN"
echo "邮箱: $EMAIL"
echo ""

# 1. 安装acme.sh
echo -e "${YELLOW}[1/4] 安装acme.sh...${NC}"
curl https://get.acme.sh | sh -s email="$EMAIL"
source ~/.bashrc
~/.acme.sh/acme.sh --set-default-ca --server letsencrypt

# 2. 申请泛域名证书
echo -e "${YELLOW}[2/4] 申请泛域名证书...${NC}"
export CF_Token="$CF_TOKEN"
~/.acme.sh/acme.sh --issue --dns dns_cf -d "$DOMAIN" -d "*.$DOMAIN" --force

# 3. 安装证书到标准位置
echo -e "${YELLOW}[3/4] 安装证书...${NC}"

# 创建证书目录
mkdir -p /etc/nginx/ssl/certs
mkdir -p /etc/nginx/ssl/private

# 安装证书
~/.acme.sh/acme.sh --install-cert -d "$DOMAIN" \
    --key-file /etc/nginx/ssl/private/"$DOMAIN".key \
    --fullchain-file /etc/nginx/ssl/certs/"$DOMAIN".crt \
    --reloadcmd "echo '证书已安装'"

# 4. 显示证书路径
echo -e "${YELLOW}[4/4] 完成！${NC}"
echo ""
echo -e "${GREEN}================================${NC}"
echo -e "${GREEN}       证书申请成功！           ${NC}"
echo -e "${GREEN}================================${NC}"
echo ""
echo -e "${YELLOW}证书路径:${NC}"
echo "公钥(crt): /etc/nginx/ssl/certs/$DOMAIN.crt"
echo "私钥(key): /etc/nginx/ssl/private/$DOMAIN.key"
echo ""
echo -e "${YELLOW}快捷路径:${NC}"
echo "ln -sf /etc/nginx/ssl/certs/$DOMAIN.crt /etc/nginx/ssl/$DOMAIN.crt"
echo "ln -sf /etc/nginx/ssl/private/$DOMAIN.key /etc/nginx/ssl/$DOMAIN.key"
echo ""
echo -e "${YELLOW}验证证书:${NC}"
echo "openssl x509 -in /etc/nginx/ssl/certs/$DOMAIN.crt -text -noout | head -20"
echo ""
echo -e "${YELLOW}下次续期:${NC}"
echo "~/.acme.sh/acme.sh --renew -d \"$DOMAIN\" --force"
echo ""