# 优化配置（参考Komari项目的最佳实践）
DOMAIN="nz.215155.xyz"
PORT="25774"
CONFIG="/etc/nginx/sites-available/${DOMAIN}.conf"

cat > "$CONFIG" << EOF
# 反向代理配置: $DOMAIN -> 127.0.0.1:$PORT
# 生成时间: $(date)
# 参考Komari项目最佳实践

# HTTP重定向到HTTPS
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;
    
    # 安全头部
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    
    # 强制HTTPS重定向
    return 301 https://\$server_name\$request_uri;
}

# HTTPS服务器配置
server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name $DOMAIN;
    
    # 使用父域名的证书（通配符证书）
    ssl_certificate /etc/nginx/ssl/certs/215155.xyz/fullchain.pem;
    ssl_certificate_key /etc/nginx/ssl/private/215155.xyz/key.pem;
    
    # SSL优化
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers 'ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384';
    ssl_prefer_server_ciphers off;
    
    # 安全头部
    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    
    # 访问日志
    access_log /var/log/nginx/${DOMAIN}_ssl_access.log;
    error_log /var/log/nginx/${DOMAIN}_ssl_error.log;
    
    # 允许大文件上传（参考Komari）
    client_max_body_size 50M;
    
    # 主要代理设置
    location / {
        proxy_pass http://127.0.0.1:$PORT;
        
        # Komari要求的核心设置
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "Upgrade";
        
        # 基础代理头
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        
        # Komari建议：禁用代理缓冲
        proxy_buffering off;
        
        # 连接设置
        proxy_buffer_size 4k;
        proxy_buffers 8 4k;
        proxy_read_timeout 60s;
        proxy_connect_timeout 60s;
        
        # 保持活动连接
        proxy_set_header Connection "";
    }
}
EOF

# 测试配置
nginx -t && nginx -s reload

echo "✅ 配置已优化并更新"
echo "访问: https://nz.215155.xyz"
echo "配置特点:"
echo "1. 参考Komari项目最佳实践"
echo "2. 启用WebSocket支持 (Upgrade/Connection头部)"
echo "3. 禁用代理缓冲 (proxy_buffering off)"
echo "4. 支持大文件上传 (50M)"
echo "5. HTTP/1.1协议"