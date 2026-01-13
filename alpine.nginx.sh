#!/bin/sh

echo "🚀 纯IPv6 Nginx反向代理配置"
echo "========================="

# 检查证书
if [ ! -f "/etc/nginx/ssl/215155.xyz.crt" ] || [ ! -f "/etc/nginx/ssl/215155.xyz.key" ]; then
    echo "❌ 证书不存在: /etc/nginx/ssl/215155.xyz.crt 或 /etc/nginx/ssl/215155.xyz.key"
    exit 1
fi

# 输入配置
echo ""
read -p "子域名 (如: nz): " subdomain
read -p "端口 (如: 52774): " port

domain="215155.xyz"
full_domain="${subdomain}.${domain}"

# 停止并清理旧配置
echo "停止Nginx..."
pkill nginx 2>/dev/null
sleep 2

echo "清理旧配置..."
rm -rf /etc/nginx/sites-enabled/* /etc/nginx/sites-available/* /etc/nginx/conf.d/*

# 创建纯IPv6 Nginx配置
echo "创建Nginx主配置..."
cat > /etc/nginx/nginx.conf << 'EOF'
user nginx;
worker_processes auto;
error_log /var/log/nginx/error.log;
pid /var/run/nginx.pid;

events {
    worker_connections 1024;
    use epoll;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    
    log_format main '$remote_addr - $remote_user [$time_local] "$request" '
                    '$status $body_bytes_sent "$http_referer" '
                    '"$http_user_agent" "$http_x_forwarded_for"';
    
    access_log /var/log/nginx/access.log main;
    
    sendfile on;
    tcp_nopush on;
    tcp_nodelay on;
    keepalive_timeout 65;
    
    # 启用gzip压缩
    gzip on;
    gzip_vary on;
    gzip_proxied any;
    gzip_comp_level 6;
    gzip_types text/plain text/css text/xml text/javascript 
               application/json application/javascript application/xml+rss 
               application/atom+xml image/svg+xml;
    
    # 包含所有配置文件
    include /etc/nginx/conf.d/*.conf;
}
EOF

# 创建纯IPv6反向代理配置（支持WebSocket）
echo "创建反向代理配置..."
cat > "/etc/nginx/conf.d/${subdomain}.conf" << EOF
server {
    # 监听IPv6，支持SSL和HTTP/2
    listen [::]:${port} ssl http2;
    
    # 服务器名称
    server_name ${full_domain};
    
    # SSL证书
    ssl_certificate /etc/nginx/ssl/215155.xyz.crt;
    ssl_certificate_key /etc/nginx/ssl/215155.xyz.key;
    
    # SSL优化
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-RSA-AES256-GCM-SHA512:DHE-RSA-AES256-GCM-SHA512;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;
    
    # 安全头
    add_header X-Frame-Options SAMEORIGIN;
    add_header X-Content-Type-Options nosniff;
    add_header X-XSS-Protection "1; mode=block";
    
    # 文件上传大小限制
    client_max_body_size 100M;
    
    # 代理设置
    location / {
        # 上游服务器
        proxy_pass http://[::1]:${port};
        
        # 基础代理头
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        
        # 超时设置
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
        
        # WebSocket支持
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        
        # 缓冲设置
        proxy_buffering off;
        proxy_request_buffering off;
    }
    
    # 健康检查端点
    location /nginx-health {
        access_log off;
        return 200 "healthy\n";
        add_header Content-Type text/plain;
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
    
    # 检查是否启动成功
    sleep 2
    if pgrep nginx >/dev/null; then
        echo "✅ Nginx已成功启动"
        
        # 显示配置信息
        echo ""
        echo "🎉 配置完成！"
        echo "==============="
        echo "域名: ${full_domain}"
        echo "端口: ${port}"
        echo "协议: HTTPS + HTTP/2"
        echo "IPv6: 已启用"
        echo "WebSocket: 已启用"
        echo "访问地址: https://${full_domain}:${port}"
        echo ""
        echo "📁 配置文件: /etc/nginx/conf.d/${subdomain}.conf"
        echo "📋 错误日志: /var/log/nginx/error.log"
        echo "📋 访问日志: /var/log/nginx/access.log"
        echo ""
        echo "🔧 常用命令:"
        echo "  重启Nginx: nginx -s reload"
        echo "  停止Nginx: nginx -s stop"
        echo "  测试配置: nginx -t"
        echo "  查看日志: tail -f /var/log/nginx/error.log"
    else
        echo "❌ Nginx启动失败"
        echo "请检查错误日志: tail -20 /var/log/nginx/error.log"
    fi
else
    echo "❌ 配置测试失败"
    echo "错误信息:"
    nginx -t 2>&1
    echo ""
    echo "请检查配置文件:"
    cat "/etc/nginx/conf.d/${subdomain}.conf"
fi