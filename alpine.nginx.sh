#!/bin/sh

echo "🚀 完整的Nginx反代配置"
echo "===================="

# 停止Nginx
pkill nginx 2>/dev/null
sleep 2

# 创建完整的Nginx配置
cat > /etc/nginx/nginx.conf << 'EOF'
user nginx;
worker_processes auto;
error_log /var/log/nginx/error.log warn;
pid /var/run/nginx.pid;

events {
    worker_connections 1024;
    use epoll;
    multi_accept on;
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
    types_hash_max_size 2048;
    
    # 启用HTTP/2
    http2 on;
    
    # Gzip压缩
    gzip on;
    gzip_vary on;
    gzip_proxied any;
    gzip_comp_level 6;
    gzip_types text/plain text/css text/xml text/javascript 
               application/json application/javascript application/xml+rss 
               application/atom+xml image/svg+xml;
    
    # 上游服务器配置
    upstream backend_nz {
        server [::1]:52774;
        keepalive 32;
    }
    
    # HTTP服务器（80端口）- 重定向到HTTPS
    server {
        # IPv4和IPv6监听80端口
        listen 80;
        listen [::]:80;
        
        server_name nz.215155.xyz;
        
        # 将所有HTTP请求重定向到HTTPS
        return 301 https://$server_name$request_uri;
    }
    
    # HTTPS服务器（443端口）- 主要配置
    server {
        # IPv4和IPv6监听443端口
        listen 443 ssl;
        listen [::]:443 ssl;
        
        server_name nz.215155.xyz;
        
        # SSL证书配置
        ssl_certificate /etc/nginx/ssl/215155.xyz.crt;
        ssl_certificate_key /etc/nginx/ssl/215155.xyz.key;
        
        # SSL安全配置
        ssl_protocols TLSv1.2 TLSv1.3;
        ssl_ciphers ECDHE-RSA-AES256-GCM-SHA512:DHE-RSA-AES256-GCM-SHA512;
        ssl_prefer_server_ciphers off;
        ssl_session_cache shared:SSL:10m;
        ssl_session_timeout 10m;
        
        # HSTS（强制HTTPS）
        add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
        
        # 安全头
        add_header X-Frame-Options SAMEORIGIN;
        add_header X-Content-Type-Options nosniff;
        add_header X-XSS-Protection "1; mode=block";
        add_header Referrer-Policy "strict-origin-when-cross-origin";
        
        # 文件上传大小限制
        client_max_body_size 100M;
        
        # 根目录访问
        location / {
            # 代理到后端服务器
            proxy_pass http://backend_nz;
            
            # 基础代理头
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            
            # 连接设置
            proxy_redirect off;
            proxy_buffering off;
            
            # 超时设置
            proxy_connect_timeout 60s;
            proxy_send_timeout 60s;
            proxy_read_timeout 60s;
            
            # WebSocket支持
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
            
            # 启用keepalive
            proxy_set_header Connection "";
            proxy_http_version 1.1;
            proxy_set_header Keep-Alive "";
            proxy_set_header Proxy-Connection "keep-alive";
        }
        
        # 健康检查端点
        location /nginx-health {
            access_log off;
            return 200 "healthy\n";
            add_header Content-Type text/plain;
        }
        
        # 禁止访问隐藏文件
        location ~ /\. {
            deny all;
            access_log off;
            log_not_found off;
        }
        
        # 静态文件缓存
        location ~* \.(jpg|jpeg|png|gif|ico|css|js|svg|woff|woff2|ttf|eot)$ {
            expires 1y;
            add_header Cache-Control "public, immutable";
            access_log off;
        }
    }
    
    # 默认服务器 - 拒绝所有非法访问
    server {
        listen 80 default_server;
        listen [::]:80 default_server;
        listen 443 ssl default_server;
        listen [::]:443 ssl default_server;
        
        ssl_certificate /etc/nginx/ssl/default.crt;
        ssl_certificate_key /etc/nginx/ssl/default.key;
        
        server_name _;
        
        return 444;
    }
}
EOF

# 创建默认证书（用于默认服务器）
mkdir -p /etc/nginx/ssl
if [ ! -f /etc/nginx/ssl/default.crt ]; then
    echo "创建默认证书..."
    openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
        -keyout /etc/nginx/ssl/default.key \
        -out /etc/nginx/ssl/default.crt \
        -subj "/CN=default" 2>/dev/null
fi

# 确保你的证书存在
if [ ! -f /etc/nginx/ssl/215155.xyz.crt ]; then
    echo "错误: 找不到证书 /etc/nginx/ssl/215155.xyz.crt"
    exit 1
fi

if [ ! -f /etc/nginx/ssl/215155.xyz.key ]; then
    echo "错误: 找不到私钥 /etc/nginx/ssl/215155.xyz.key"
    exit 1
fi

# 创建日志目录
mkdir -p /var/log/nginx

# 测试配置
echo "测试Nginx配置..."
if nginx -t; then
    echo "✅ 配置测试通过"
    
    # 启动Nginx
    echo "启动Nginx..."
    nginx
    
    # 检查是否启动成功
    sleep 2
    if pgrep nginx >/dev/null; then
        echo ""
        echo "🎉 Nginx配置成功！"
        echo "================="
        echo ""
        echo "📡 访问地址:"
        echo "  HTTPS: https://nz.215155.xyz"
        echo "  HTTP: http://nz.215155.xyz (自动跳转到HTTPS)"
        echo ""
        echo "🔧 配置详情:"
        echo "  监听端口: 80 (HTTP), 443 (HTTPS)"
        echo "  IPv4/IPv6: 双栈支持"
        echo "  代理目标: http://[::1]:52774"
        echo "  WebSocket: 已启用"
        echo "  HTTP/2: 已启用"
        echo ""
        echo "📋 日志文件:"
        echo "  错误日志: /var/log/nginx/error.log"
        echo "  访问日志: /var/log/nginx/access.log"
        echo ""
        echo "🛠️ 管理命令:"
        echo "  重启: nginx -s reload"
        echo "  停止: nginx -s stop"
        echo "  测试: nginx -t"
        echo ""
        echo "🔍 验证配置:"
        echo "  1. 检查监听端口: netstat -tlnp | grep nginx"
        echo "  2. 查看实时日志: tail -f /var/log/nginx/access.log"
        echo "  3. 测试HTTPS: curl -I https://nz.215155.xyz"
    else
        echo "❌ Nginx启动失败"
        echo "查看错误日志: tail -20 /var/log/nginx/error.log"
    fi
else
    echo "❌ 配置测试失败"
    nginx -t 2>&1
fi