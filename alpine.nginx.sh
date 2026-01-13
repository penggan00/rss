#!/bin/sh

echo "🚀 修正版纯IPv6 Nginx反代配置"
echo "============================"

# 检查证书
if [ ! -f "/etc/nginx/ssl/215155.xyz.crt" ] || [ ! -f "/etc/nginx/ssl/215155.xyz.key" ]; then
    echo "❌ 证书不存在: /etc/nginx/ssl/215155.xyz.crt"
    exit 1
fi

# 输入配置
echo ""
read -p "子域名 (如: nz): " subdomain
read -p "端口 (如: 52774): " port

domain="215155.xyz"
full_domain="${subdomain}.${domain}"

# 停止并清理
echo "停止Nginx..."
pkill nginx 2>/dev/null
sleep 2
echo "清理配置..."
rm -f /etc/nginx/conf.d/* /etc/nginx/sites-enabled/* /etc/nginx/sites-available/*

# 创建Nginx主配置
echo "创建Nginx配置..."
cat > /etc/nginx/nginx.conf << 'EOF'
user nginx;
worker_processes auto;
error_log /var/log/nginx/error.log;
pid /var/run/nginx.pid;

events {
    worker_connections 1024;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    access_log /var/log/nginx/access.log;
    sendfile on;
    tcp_nopush on;
    tcp_nodelay on;
    keepalive_timeout 65;
    client_max_body_size 100M;
    include /etc/nginx/conf.d/*.conf;
}
EOF

# 创建反代配置
echo "创建反代配置..."
cat > "/etc/nginx/conf.d/${subdomain}.conf" << EOF
server {
    listen [::]:${port} ssl;
    http2 on;
    server_name ${full_domain};
    
    ssl_certificate /etc/nginx/ssl/215155.xyz.crt;
    ssl_certificate_key /etc/nginx/ssl/215155.xyz.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    
    location / {
        proxy_pass http://[::1]:${port};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
        
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
EOF

# 创建日志目录
mkdir -p /var/log/nginx

# 测试配置
echo ""
echo "测试Nginx配置..."
if nginx -t; then
    echo "✅ 配置测试通过"
    
    # 启动Nginx
    echo "启动Nginx..."
    nginx
    
    # 检查状态
    sleep 2
    if pgrep nginx >/dev/null; then
        echo "✅ Nginx已启动"
        echo ""
        echo "🎉 配置完成！"
        echo "==============="
        echo "域名: ${full_domain}"
        echo "端口: ${port}"
        echo "访问: https://${full_domain}:${port}"
        echo "配置文件: /etc/nginx/conf.d/${subdomain}.conf"
    else
        echo "❌ Nginx启动失败"
        echo "查看错误: tail -20 /var/log/nginx/error.log"
    fi
else
    echo "❌ 配置测试失败"
    echo "请检查配置文件"
fi