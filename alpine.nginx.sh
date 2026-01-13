#!/bin/sh

echo "创建Nginx反向代理..."
echo ""

# 检查证书是否存在
if [ ! -f "/etc/nginx/ssl/215155.xyz.crt" ]; then
    echo "错误: 证书不存在 /etc/nginx/ssl/215155.xyz.crt"
    exit 1
fi

if [ ! -f "/etc/nginx/ssl/215155.xyz.key" ]; then
    echo "错误: 私钥不存在 /etc/nginx/ssl/215155.xyz.key"
    exit 1
fi

# 输入配置
read -p "子域名 (如: nz): " SUBDOMAIN
read -p "端口 (如: 52774): " PORT

DOMAIN="215155.xyz"
FULL_DOMAIN="${SUBDOMAIN}.${DOMAIN}"
CONFIG_FILE="/etc/nginx/conf.d/${SUBDOMAIN}.conf"

# 创建配置
cat > "$CONFIG_FILE" << EOF
server {
    listen $PORT ssl;
    server_name $FULL_DOMAIN;
    
    ssl_certificate /etc/nginx/ssl/215155.xyz.crt;
    ssl_certificate_key /etc/nginx/ssl/215155.xyz.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    
    location / {
        proxy_pass http://localhost:$PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }
}
EOF

echo "配置已创建: $CONFIG_FILE"

# 测试并重启
if nginx -t; then
    echo "配置测试通过"
    nginx -s reload 2>/dev/null || nginx
    echo "Nginx已重启"
    
    echo ""
    echo "✅ 完成!"
    echo "访问: https://$FULL_DOMAIN:$PORT"
else
    echo "配置测试失败"
    nginx -t
fi