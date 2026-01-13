#!/bin/bash

# ================= 修复 Nginx IPv6 监听 =================
# 解决 Nginx 只监听 IPv4 的问题
# =====================================================

echo "=== 修复 Nginx IPv6 监听问题 ==="

# 1. 检查当前监听状态
echo "1. 当前监听状态:"
ss -tlnp | grep -E ':(80|443)'

# 2. 备份原配置
echo "2. 备份原配置..."
cp /etc/nginx/nginx.conf /etc/nginx/nginx.conf.backup.$(date +%Y%m%d%H%M%S)

# 3. 创建正确的 Nginx 主配置
echo "3. 创建新的 Nginx 配置..."
cat > /etc/nginx/nginx.conf <<'EOF'
user nginx nginx;
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
}
EOF

# 4. 创建默认服务器配置（监听 IPv6）
echo "4. 创建默认服务器配置..."
cat > /etc/nginx/conf.d/default_server.conf <<'EOF'
# 默认服务器 - 禁止直接IP访问
# 同时监听 IPv4 和 IPv6
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;
    return 444;
}

server {
    listen 443 ssl default_server;
    listen [::]:443 ssl default_server;
    server_name _;
    
    ssl_certificate /etc/nginx/ssl/fallback.crt;
    ssl_certificate_key /etc/nginx/ssl/fallback.key;
    
    # 兼容性 SSL 配置
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;
    
    return 444;
}
EOF

# 5. 修复你的反向代理配置
echo "5. 修复反向代理配置..."
cat > /etc/nginx/conf.d/nz.215155.xyz.conf <<'EOF'
# 反向代理配置
# 域名: nz.215155.xyz
# 后端: 127.0.0.1:25774
# 监听 IPv6

# HTTP - 重定向到 HTTPS
server {
    listen 80;
    listen [::]:80;
    server_name nz.215155.xyz;
    
    access_log /var/log/nginx/nz.215155.xyz_access.log;
    error_log /var/log/nginx/nz.215155.xyz_error.log;
    
    return 301 https://$host$request_uri;
}

# HTTPS - 主要配置
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name nz.215155.xyz;
    
    # SSL 证书
    ssl_certificate /etc/nginx/ssl/215155.xyz.crt;
    ssl_certificate_key /etc/nginx/ssl/215155.xyz.key;
    
    # SSL 配置（兼容 Alpine）
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;
    
    # 安全头
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    
    # 日志
    access_log /var/log/nginx/nz.215155.xyz_ssl_access.log;
    error_log /var/log/nginx/nz.215155.xyz_ssl_error.log;
    
    # 反向代理
    location / {
        proxy_pass http://127.0.0.1:25774;
        
        # 基础头部
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
        
        # 超时设置
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
        
        # WebSocket 支持
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
    
    # 禁止访问隐藏文件
    location ~ /\. {
        deny all;
        access_log off;
        log_not_found off;
    }
}
EOF

# 6. 检查证书文件
echo "6. 检查证书文件..."
if [ ! -f "/etc/nginx/ssl/215155.xyz.crt" ] || [ ! -f "/etc/nginx/ssl/215155.xyz.key" ]; then
    echo "错误: 证书文件不存在"
    echo "请确保证书文件存在:"
    echo "  /etc/nginx/ssl/215155.xyz.crt"
    echo "  /etc/nginx/ssl/215155.xyz.key"
    exit 1
else
    echo "✅ 证书文件存在"
fi

# 7. 创建 fallback 证书（如果不存在）
if [ ! -f "/etc/nginx/ssl/fallback.crt" ]; then
    echo "创建 fallback 证书..."
    mkdir -p /etc/nginx/ssl
    openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
        -keyout /etc/nginx/ssl/fallback.key \
        -out /etc/nginx/ssl/fallback.crt \
        -subj "/CN=Invalid" 2>/dev/null
fi

# 8. 测试 Nginx 配置
echo "7. 测试 Nginx 配置..."
nginx -t 2>&1
if [ $? -eq 0 ]; then
    echo "✅ Nginx 配置测试通过"
else
    echo "❌ Nginx 配置测试失败"
    exit 1
fi

# 9. 重载 Nginx
echo "8. 重载 Nginx..."
nginx -s reload 2>/dev/null || nginx

# 10. 检查监听状态
echo "9. 检查新的监听状态..."
sleep 2
echo "IPv4 监听:"
ss -tlnp | grep -E '0.0.0.0:(80|443)'
echo ""
echo "IPv6 监听:"
ss -tlnp | grep -E ':::(80|443)'

# 11. 测试连接
echo "10. 测试连接..."
echo "测试 komari 服务 (本地):"
if curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:25774 2>/dev/null | grep -q "200"; then
    echo "✅ komari 服务正常 (HTTP 200)"
else
    echo "❌ komari 服务异常"
fi

echo ""
echo "测试 Nginx HTTP 重定向:"
curl -s -I http://nz.215155.xyz 2>/dev/null | grep -i "location\|HTTP"

echo ""
echo "测试 Nginx HTTPS 连接:"
if curl -s -k -o /dev/null -w "%{http_code}" https://nz.215155.xyz 2>/dev/null | grep -q "200"; then
    echo "✅ Nginx HTTPS 连接成功 (HTTP 200)"
else
    echo "❌ Nginx HTTPS 连接失败"
fi

# 12. 获取服务器 IPv6 地址
echo ""
echo "11. 服务器 IPv6 地址:"
ip -6 addr show | grep inet6 | grep -v "::1" | head -2

echo ""
echo "=== 修复完成 ==="
echo "现在应该可以通过以下方式访问:"
echo "1. IPv4: https://nz.215155.xyz"
echo "2. IPv6: https://[你的IPv6地址]"
echo ""
echo "要获取准确的 IPv6 地址，运行: ip -6 addr show"