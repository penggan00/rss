#!/bin/bash

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m'

log() {
    local level=$1
    local message=$2
    local color=$NC
    
    case $level in
        "INFO") color=$GREEN ;;
        "WARN") color=$YELLOW ;;
        "ERROR") color=$RED ;;
        "DEBUG") color=$BLUE ;;
    esac
    
    echo -e "${color}[$(date '+%Y-%m-%d %H:%M:%S')] [$level] $message${NC}"
}

check_dependencies() {
    local deps=("nginx" "openssl" "tree")
    local missing=()
    
    for dep in "${deps[@]}"; do
        if ! command -v $dep &> /dev/null; then
            missing+=("$dep")
        fi
    done
    
    if [ ${#missing[@]} -gt 0 ]; then
        log "WARN" "缺少以下依赖: ${missing[*]}"
        read -p "是否安装缺失的依赖？(y/n): " choice
        if [[ $choice =~ ^[Yy]$ ]]; then
            apk update
            for dep in "${missing[@]}"; do
                apk add "$dep"
            done
        fi
    fi
}

init_directories() {
    log "INFO" "初始化目录结构..."
    
    local dirs=(
        "/etc/nginx/ssl/certs"
        "/etc/nginx/ssl/private"
        "/etc/nginx/sites-available"
        "/etc/nginx/sites-enabled"
        "/var/log/nginx/ssl"
        "/var/www/html"
    )
    
    for dir in "${dirs[@]}"; do
        if [ ! -d "$dir" ]; then
            mkdir -p "$dir"
            chmod 750 "$dir"
            log "DEBUG" "创建目录: $dir"
        fi
    done
    
    chown -R nginx:nginx /etc/nginx/ssl/private
    chmod 700 /etc/nginx/ssl/private
    chmod 644 /etc/nginx/ssl/certs/*
    2>/dev/null
    
    log "INFO" "目录结构初始化完成"
}

validate_domain() {
    local domain=$1
    
    if [ -z "$domain" ]; then
        echo -e "${RED}错误: 域名不能为空${NC}"
        return 1
    fi
    
    if [ ${#domain} -gt 255 ]; then
        echo -e "${RED}错误: 域名太长${NC}"
        return 1
    fi
    
    if [[ "$domain" != *.* ]]; then
        echo -e "${YELLOW}警告: 域名缺少点号，但将继续处理${NC}"
        return 0
    fi
    
    if [[ "$domain" == .* ]] || [[ "$domain" == *. ]]; then
        echo -e "${RED}错误: 域名不能以点号开头或结尾${NC}"
        return 1
    fi
    
    if [[ "$domain" == *..* ]]; then
        echo -e "${RED}错误: 域名不能有连续点号${NC}"
        return 1
    fi
    
    echo -e "${GREEN}✅ 域名格式验证通过${NC}"
    return 0
}

find_certificates() {
    local domain="${1#*//}"
    domain="${domain%%/*}"
    
    local cert_file="/etc/nginx/ssl/${domain}.crt"
    local key_file="/etc/nginx/ssl/${domain}.key"
    
    if { [ -f "$cert_file" ] || [ -L "$cert_file" ]; } && \
       { [ -f "$key_file" ] || [ -L "$key_file" ]; }; then
        echo "$cert_file:$key_file"
        return 0
    else
        return 1
    fi
}

generate_self_signed_cert() {
    local domain=$1
    local cert_dir="/etc/nginx/ssl/certs/${domain}"
    local key_dir="/etc/nginx/ssl/private/${domain}"
    
    mkdir -p "$cert_dir" "$key_dir"
    
    log "INFO" "为 $domain 生成自签名证书..."
    
    openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
        -keyout "${key_dir}/key.pem" \
        -out "${cert_dir}/fullchain.pem" \
        -subj "/C=CN/ST=Beijing/L=Beijing/O=Development/CN=${domain}" \
        2>/dev/null
    
    if [ $? -eq 0 ]; then
        CERT_FILE="${cert_dir}/fullchain.pem"
        KEY_FILE="${key_dir}/key.pem"
        
        chmod 644 "$CERT_FILE"
        chmod 600 "$KEY_FILE"
        chown nginx:nginx "$KEY_FILE"
        
        log "INFO" "自签名证书生成成功"
        return 0
    else
        log "ERROR" "自签名证书生成失败"
        return 1
    fi
}

create_proxy_config() {
    log "INFO" "开始创建反向代理配置"
    
    while true; do
        echo -ne "${CYAN}请输入域名${NC} (例如: api.example.com): "
        read DOMAIN
        
        if [ -n "$DOMAIN" ]; then
            validate_domain "$DOMAIN" && break
        else
            echo -e "${RED}错误: 域名不能为空${NC}"
        fi
    done
    
    while true; do
        echo -ne "${CYAN}请输入后端服务端口${NC} (例如: 3000): "
        read BACKEND_PORT
        
        if [[ "$BACKEND_PORT" =~ ^[0-9]+$ ]] && [ "$BACKEND_PORT" -ge 1 ] && [ "$BACKEND_PORT" -le 65535 ]; then
            break
        else
            echo -e "${RED}错误: 端口号必须是1-65535之间的数字${NC}"
        fi
    done
    
    echo -ne "${CYAN}是否启用WebSocket支持？${NC} (y/n): "
    read -n 1 WS_CHOICE
    echo
    [[ $WS_CHOICE =~ ^[Yy]$ ]] && WEBSOCKET=true || WEBSOCKET=false
    
    echo -ne "${CYAN}是否启用缓存？${NC} (y/n): "
    read -n 1 CACHE_CHOICE
    echo
    [[ $CACHE_CHOICE =~ ^[Yy]$ ]] && ENABLE_CACHE=true || ENABLE_CACHE=false
    
    FORCE_HTTPS=true
    
    log "INFO" "正在查找证书..."
    SSL_AVAILABLE=false
    
    if find_certificates "$DOMAIN"; then
        SSL_AVAILABLE=true
        log "INFO" "找到SSL证书"
    else
        log "WARN" "未找到SSL证书"
        echo -ne "${YELLOW}是否生成自签名证书？${NC} (y/n): "
        read -n 1 CERT_CHOICE
        echo
        
        if [[ $CERT_CHOICE =~ ^[Yy]$ ]]; then
            if generate_self_signed_cert "$DOMAIN"; then
                SSL_AVAILABLE=true
                log "INFO" "已生成自签名证书"
            fi
        fi
    fi
    
    if [ "$SSL_AVAILABLE" = false ]; then
        log "ERROR" "必须使用HTTPS，但未找到证书且未生成自签名证书"
        return 1
    fi
    
    CONFIG_FILE="/etc/nginx/sites-available/${DOMAIN}.conf"
    
    log "INFO" "生成配置文件: $CONFIG_FILE"
    
    cat > "$CONFIG_FILE" << EOF
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;
    
    return 301 https://\$server_name\$request_uri;
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name $DOMAIN;
    
    ssl_certificate $CERT_FILE;
    ssl_certificate_key $KEY_FILE;
    
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers 'ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384';
    ssl_prefer_server_ciphers off;
    
    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    
    access_log /var/log/nginx/ssl/${DOMAIN}_access.log;
    error_log /var/log/nginx/ssl/${DOMAIN}_error.log warn;
    
    location / {
        proxy_pass http://127.0.0.1:$BACKEND_PORT;
        proxy_http_version 1.1;
        
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Forwarded-Host \$server_name;
        
        proxy_buffering off;
        proxy_buffer_size 4k;
        proxy_buffers 8 4k;
        proxy_read_timeout 60s;
        proxy_connect_timeout 60s;
        
        proxy_set_header Connection "";
        
        proxy_request_buffering off;
    }
EOF
    
    if [ "$ENABLE_CACHE" = true ]; then
        cat >> "$CONFIG_FILE" << EOF
    
    location ~* \.(jpg|jpeg|png|gif|ico|css|js)$ {
        proxy_pass http://127.0.0.1:$BACKEND_PORT;
        proxy_cache proxy_cache;
        proxy_cache_valid 200 302 1h;
        proxy_cache_valid 404 1m;
        proxy_cache_use_stale error timeout updating http_500 http_502 http_503 http_504;
        add_header X-Cache-Status \$upstream_cache_status;
    }
EOF
    fi
    
    if [ "$WEBSOCKET" = true ]; then
        cat >> "$CONFIG_FILE" << EOF
    
    location /ws/ {
        proxy_pass http://127.0.0.1:$BACKEND_PORT;
        proxy_http_version 1.1;
        
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "Upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
    
    location ~ ^/(socket\.io|websocket)/ {
        proxy_pass http://127.0.0.1:$BACKEND_PORT;
        proxy_http_version 1.1;
        
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "Upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
EOF
    fi
    
    echo "}" >> "$CONFIG_FILE"
    
    if [ "$ENABLE_CACHE" = true ]; then
        cat >> "$CONFIG_FILE" << EOF

proxy_cache_path /var/cache/nginx levels=1:2 keys_zone=proxy_cache:10m 
                 max_size=1g inactive=60m use_temp_path=off;
EOF
    fi
    
    mkdir -p /etc/nginx/sites-enabled
    ln -sf "$CONFIG_FILE" "/etc/nginx/sites-enabled/${DOMAIN}.conf"
    
    echo -e "\n${GREEN}✅ 配置创建成功${NC}"
    echo -e "${BLUE}配置文件:${NC} $CONFIG_FILE"
    echo -e "${BLUE}域名:${NC} $DOMAIN"
    echo -e "${BLUE}后端服务:${NC} 127.0.0.1:$BACKEND_PORT"
    echo -e "${BLUE}SSL:${NC} $( [ "$SSL_AVAILABLE" = true ] && echo '启用' || echo '未启用' )"
    echo -e "${BLUE}强制HTTPS:${NC} 是"
    echo -e "${BLUE}WebSocket:${NC} $( [ "$WEBSOCKET" = true ] && echo '启用' || echo '未启用' )"
    echo -e "${BLUE}缓存:${NC} $( [ "$ENABLE_CACHE" = true ] && echo '启用' || echo '未启用' )"
    
    if [ "$SSL_AVAILABLE" = true ] && [ -f "$CERT_FILE" ]; then
        echo -e "\n${YELLOW}证书路径:${NC}"
        echo -e "  证书: $CERT_FILE"
        echo -e "  密钥: $KEY_FILE"
    fi
}

delete_site() {
    log "INFO" "删除站点配置"
    
    echo -e "${YELLOW}当前启用的站点:${NC}"
    local i=1
    local sites=()
    
    if ls /etc/nginx/sites-enabled/*.conf 2>/dev/null >/dev/null; then
        for conf in /etc/nginx/sites-enabled/*.conf; do
            local domain=$(basename "$conf" .conf)
            sites+=("$domain")
            echo -e "  ${GREEN}$i.${NC} $domain"
            ((i++))
        done
    else
        echo -e "${RED}没有启用的站点配置${NC}"
        return
    fi
    
    if [ ${#sites[@]} -eq 0 ]; then
        echo -e "${RED}没有站点可删除${NC}"
        return
    fi
    
    echo -ne "\n${CYAN}请选择要删除的站点编号${NC} (1-${#sites[@]}): "
    read choice
    
    if [[ $choice =~ ^[0-9]+$ ]] && [ $choice -ge 1 ] && [ $choice -le ${#sites[@]} ]; then
        local domain=${sites[$((choice-1))]}
        
        echo -e "${YELLOW}确定要删除站点 '$domain' 吗？${NC}"
        echo -ne "${RED}此操作将删除配置文件和符号链接${NC} (y/n): "
        read -n 1 confirm
        echo
        
        if [[ $confirm =~ ^[Yy]$ ]]; then
            rm -f "/etc/nginx/sites-enabled/${domain}.conf"
            
            if [ -f "/etc/nginx/sites-available/${domain}.conf" ]; then
                rm -f "/etc/nginx/sites-available/${domain}.conf"
            fi
            
            rm -f "/var/log/nginx/${domain}"*.log 2>/dev/null
            rm -f "/var/log/nginx/ssl/${domain}"*.log 2>/dev/null
            
            log "INFO" "站点 '$domain' 已删除"
            
            echo -ne "${YELLOW}是否现在重载Nginx？${NC} (y/n): "
            read -n 1 reload
            echo
            if [[ $reload =~ ^[Yy]$ ]]; then
                reload_nginx
            fi
        else
            echo -e "${GREEN}取消删除操作${NC}"
        fi
    else
        echo -e "${RED}无效的选择${NC}"
    fi
}

reload_nginx() {
    log "INFO" "测试Nginx配置..."
    
    if nginx -t 2>&1; then
        log "INFO" "配置测试通过"
        
        echo -e "${YELLOW}重载Nginx...${NC}"
        
        if nginx -s reload 2>/dev/null; then
            log "INFO" "Nginx重载成功"
        elif rc-service nginx reload 2>/dev/null; then
            log "INFO" "Nginx重载成功"
        elif systemctl reload nginx 2>/dev/null; then
            log "INFO" "Nginx重载成功"
        else
            echo -e "${YELLOW}重载失败，尝试重启...${NC}"
            if systemctl restart nginx 2>/dev/null || rc-service nginx restart 2>/dev/null; then
                log "INFO" "Nginx重启成功"
            else
                log "ERROR" "Nginx重载/重启失败"
                return 1
            fi
        fi
        
        show_config_summary
        return 0
    else
        log "ERROR" "配置测试失败"
        echo -e "${YELLOW}错误详情:${NC}"
        nginx -t 2>&1 | tail -10
        return 1
    fi
}

check_certificates() {
    log "INFO" "检查证书状态"
    
    echo -e "${BLUE}搜索证书目录...${NC}"
    
    local cert_dirs=(
        "/etc/nginx/ssl"
        "/etc/letsencrypt/live"
        "/root/.acme.sh"
    )
    
    for dir in "${cert_dirs[@]}"; do
        if [ -d "$dir" ]; then
            echo -e "\n${GREEN}目录: $dir${NC}"
            find "$dir" -name "*.pem" -o -name "*.crt" -o -name "*.cer" -o -name "*.key" 2>/dev/null | head -20 | while read file; do
                if [ -f "$file" ]; then
                    local size=$(du -h "$file" | cut -f1)
                    local perms=$(stat -c "%a %U:%G" "$file" 2>/dev/null || echo "N/A")
                    
                    if [[ "$file" =~ \.crt$|\.pem$|\.cer$ ]]; then
                        echo -e "  📄 $file ($size, $perms)"
                        
                        local expire_date=$(openssl x509 -enddate -noout -in "$file" 2>/dev/null | cut -d= -f2)
                        if [ -n "$expire_date" ]; then
                            echo -e "    过期时间: $expire_date"
                        fi
                    elif [[ "$file" =~ \.key$ ]]; then
                        echo -e "  🔑 $file ($size, $perms)"
                    fi
                fi
            done
        fi
    done
    
    echo -e "\n${BLUE}Nginx SSL目录结构:${NC}"
    if [ -d "/etc/nginx/ssl" ]; then
        if command -v tree &> /dev/null; then
            tree /etc/nginx/ssl -L 3 2>/dev/null || ls -laR /etc/nginx/ssl/
        else
            ls -la /etc/nginx/ssl/
        fi
    else
        echo -e "${YELLOW}/etc/nginx/ssl/ 目录不存在${NC}"
        mkdir -p /etc/nginx/ssl/{certs,private}
        chmod 750 /etc/nginx/ssl/private
        chmod 755 /etc/nginx/ssl/certs
    fi
}

init_nginx() {
    log "INFO" "开始初始化Nginx"
    
    echo -e "${YELLOW}警告：此操作将完全清理并重新安装Nginx${NC}"
    echo -e "${RED}所有自定义配置将被删除！${NC}"
    echo -ne "是否继续？(y/n): "
    read -n 1 confirm
    echo
    
    if [[ ! $confirm =~ ^[Yy]$ ]]; then
        echo -e "${GREEN}操作已取消${NC}"
        return
    fi
    
    bash -c "$(cat << 'EOF'
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}>>> 开始修复Nginx...${NC}"

echo -e "${YELLOW}停止Nginx进程...${NC}"
pkill nginx 2>/dev/null
sleep 2

echo -e "${YELLOW}卸载Nginx包...${NC}"
apk del nginx nginx-* --purge 2>/dev/null

echo -e "${YELLOW}清理残留文件...${NC}"
rm -rf /etc/nginx /var/lib/nginx /var/log/nginx /run/nginx /usr/share/nginx

echo -e "${YELLOW}更新包列表并重新安装...${NC}"
apk update
apk add nginx

echo -e "${YELLOW}检查安装的文件...${NC}"
apk info -L nginx | grep -E "mime.types|nginx.conf"

echo -e "${YELLOW}查找mime.types文件...${NC}"
MIME_TYPES=$(find / -name "mime.types" 2>/dev/null | head -1)
if [ -z "$MIME_TYPES" ]; then
    echo -e "${RED}未找到mime.types文件，尝试手动创建${NC}"
    
    mkdir -p /usr/share/nginx
    cat > /usr/share/nginx/mime.types << 'EOC'
types {
    text/html                                        html htm shtml;
    text/css                                         css;
    text/xml                                         xml;
    image/gif                                        gif;
    image/jpeg                                       jpeg jpg;
    application/javascript                           js;
    application/atom+xml                             atom;
    application/rss+xml                              rss;

    text/mathml                                      mml;
    text/plain                                       txt;
    text/vnd.sun.j2me.app-descriptor                 jad;
    text/vnd.wap.wml                                 wml;
    text/x-component                                 htc;

    image/png                                        png;
    image/svg+xml                                    svg svgz;
    image/tiff                                       tif tiff;
    image/vnd.wap.wbmp                               wbmp;
    image/webp                                       webp;
    image/x-icon                                     ico;
    image/x-jng                                      jng;
    image/x-ms-bmp                                   bmp;

    font/woff                                        woff;
    font/woff2                                       woff2;

    application/java-archive                         jar war ear;
    application/json                                 json;
    application/mac-binhex40                         hqx;
    application/msword                               doc;
    application/pdf                                  pdf;
    application/postscript                           ps eps ai;
    application/rtf                                  rtf;
    application/vnd.apple.mpegurl                    m3u8;
    application/vnd.google-earth.kml+xml             kml;
    application/vnd.google-earth.kmz                 kmz;
    application/vnd.ms-excel                         xls;
    application/vnd.ms-fontobject                    eot;
    application/vnd.ms-powerpoint                    ppt;
    application/vnd.oasis.opendocument.graphics      odg;
    application/vnd.oasis.opendocument.presentation  odp;
    application/vnd.oasis.opendocument.spreadsheet   ods;
    application/vnd.oasis.opendocument.text          odt;
    application/vnd.openxmlformats-officedocument.presentationml.presentation pptx;
    application/vnd.openxmlformats-officedocument.spreadsheetml.sheet         xlsx;
    application/vnd.openxmlformats-officedocument.wordprocessingml.document   docx;
    application/vnd.wap.wmlc                        wmlc;
    application/x-7z-compressed                     7z;
    application/x-cocoa                             cco;
    application/x-java-archive-diff                  jardiff;
    application/x-java-jnlp-file                     jnlp;
    application/x-makeself                           run;
    application/x-perl                               pl pm;
    application/x-pilot                              prc pdb;
    application/x-rar-compressed                     rar;
    application/x-redhat-package-manager             rpm;
    application/x-sea                                sea;
    application/x-shockwave-flash                    swf;
    application/x-stuffit                            sit;
    application/x-tcl                                tcl tk;
    application/x-x509-ca-cert                       der pem crt;
    application/x-xpinstall                          xpi;
    application/xhtml+xml                            xhtml;
    application/xspf+xml                             xspf;
    application/zip                                  zip;

    application/octet-stream                         bin exe dll;
    application/octet-stream                         deb;
    application/octet-stream                         dmg;
    application/octet-stream                         iso img;
    application/octet-stream                         msi msp msm;

    audio/midi                                       mid midi kar;
    audio/mpeg                                       mp3;
    audio/ogg                                        ogg;
    audio/x-m4a                                      m4a;
    audio/x-realaudio                                ra;

    video/3gpp                                       3gpp 3gp;
    video/mp2t                                       ts;
    video/mp4                                        mp4;
    video/mpeg                                       mpeg mpg;
    video/quicktime                                  mov;
    video/webm                                       webm;
    video/x-flv                                      flv;
    video/x-m4v                                      m4v;
    video/x-mng                                      mng;
    video/x-ms-asf                                   asx asf;
    video/x-ms-wmv                                   wmv;
    video/x-msvideo                                  avi;
}
EOC
    MIME_TYPES="/usr/share/nginx/mime.types"
    echo -e "${GREEN}已创建基本的mime.types文件${NC}"
else
    echo -e "${GREEN}找到mime.types: $MIME_TYPES${NC}"
fi

echo -e "${YELLOW}创建目录结构...${NC}"
mkdir -p /etc/nginx/{conf.d,sites-available,sites-enabled,ssl}
mkdir -p /var/log/nginx /run/nginx /var/www/html /var/lib/nginx/logs
mkdir -p $(dirname "$MIME_TYPES")

echo -e "${YELLOW}创建nginx配置...${NC}"
cat > /etc/nginx/nginx.conf << EOC
user nginx;
worker_processes auto;
pid /run/nginx/nginx.pid;

events {
    worker_connections 1024;
    multi_accept on;
}

http {
    include       $MIME_TYPES;
    default_type  application/octet-stream;

    sendfile        on;
    tcp_nopush      on;
    tcp_nodelay     on;
    keepalive_timeout  65;
    types_hash_max_size 2048;
    server_tokens off;

    access_log  /var/log/nginx/access.log;
    error_log   /var/log/nginx/error.log;

    gzip on;
    gzip_vary on;
    gzip_min_length 1024;
    gzip_types text/plain text/css text/xml text/javascript 
               application/javascript application/xml+rss 
               application/json;

    include /etc/nginx/conf.d/*.conf;
    include /etc/nginx/sites-enabled/*;
}
EOC

echo -e "${GREEN}配置已写入 /etc/nginx/nginx.conf${NC}"

echo -e "${YELLOW}创建默认网页...${NC}"
cat > /var/www/html/index.html << 'EOC'
<!DOCTYPE html>
<html>
<head>
    <title>Nginx修复成功</title>
    <style>
        body { font-family: Arial, sans-serif; text-align: center; padding: 50px; }
        .success { color: #28a745; font-weight: bold; }
        .info { margin: 20px 0; padding: 15px; background: #f8f9fa; border-radius: 5px; }
    </style>
</head>
<body>
    <h1>✅ Nginx修复成功</h1>
    <div class="info">
        <p><strong>状态：</strong> <span class="success">运行正常</span></p>
        <p><strong>时间：</strong> <span id="datetime"></span></p>
        <p><strong>Nginx版本：</strong> $(nginx -v 2>&1 | cut -d/ -f2)</p>
    </div>
    <script>
        document.getElementById('datetime').textContent = new Date().toLocaleString();
    </script>
</body>
</html>
EOC

echo -e "${YELLOW}设置文件权限...${NC}"
chown -R nginx:nginx /var/www/html /var/log/nginx /var/lib/nginx
chmod 755 /var/www/html

echo -e "${YELLOW}启动Nginx服务...${NC}"
rc-service nginx start
rc-update add nginx default

echo "alias docker-compose='docker compose'" >> ~/.profile
source ~/.profile
echo -e "${GREEN}别名设置完成${NC}"
EOF
)"
    
    log "INFO" "Nginx初始化完成"
}

show_config_summary() {
    echo -e "\n${BLUE}================ 配置摘要 ================${NC}"
    
    echo -e "${GREEN}当前启用的代理:${NC}"
    if ls /etc/nginx/sites-enabled/*.conf 2>/dev/null >/dev/null; then
        for conf in /etc/nginx/sites-enabled/*.conf; do
            local domain=$(grep "server_name" "$conf" | head -1 | awk '{print $2}' | tr -d ';')
            local port=$(grep "listen" "$conf" | grep -v "listen \[::\]" | head -1 | awk '{print $2}' | tr -d ';')
            local backend=$(grep "proxy_pass" "$conf" | head -1 | awk '{print $2}' | tr -d ';')
            echo -e "  🌐 $domain (端口: $port) -> $backend"
        done
    else
        echo -e "  没有启用的配置"
    fi
    
    echo -e "\n${GREEN}监听端口:${NC}"
    if command -v netstat &> /dev/null; then
        netstat -tulpn 2>/dev/null | grep -E ":80\>|:443\>" | awk '{print "  " $4}'
    elif command -v ss &> /dev/null; then
        ss -tulpn 2>/dev/null | grep -E ":80\>|:443\>" | awk '{print "  " $5}'
    else
        echo "  无法获取端口信息"
    fi
    
    echo -e "\n${GREEN}Nginx状态:${NC}"
    if pgrep nginx > /dev/null; then
        echo -e "  ✅ 正在运行"
        echo -e "  主进程PID: $(cat /run/nginx/nginx.pid 2>/dev/null || pgrep -o nginx)"
    else
        echo -e "  ❌ 未运行"
    fi
    
    echo -e "${BLUE}========================================${NC}"
}

show_current_config() {
    echo -e "${YELLOW}>>> 当前Nginx配置${NC}"
    
    echo -e "${BLUE}Nginx主配置:${NC}"
    if [ -f "/etc/nginx/nginx.conf" ]; then
        echo -e "  路径: /etc/nginx/nginx.conf"
        echo -e "  大小: $(du -h /etc/nginx/nginx.conf | cut -f1)"
    else
        echo -e "  ❌ 主配置文件不存在"
    fi
    
    echo -e "\n${BLUE}启用的站点配置:${NC}"
    if ls /etc/nginx/sites-enabled/*.conf 2>/dev/null >/dev/null; then
        for conf in /etc/nginx/sites-enabled/*.conf; do
            echo -e "\n${GREEN}配置文件: $(basename "$conf")${NC}"
            echo "  路径: $conf"
            echo "  大小: $(du -h "$conf" | cut -f1)"
            echo "  修改时间: $(stat -c "%y" "$conf" 2>/dev/null | cut -d'.' -f1)"
            
            local domain=$(grep -h "server_name" "$conf" | head -1 | awk '{print $2}' | tr -d ';')
            local port=$(grep -h "listen" "$conf" | grep -v "listen \[::\]" | head -1 | awk '{print $2}' | tr -d ';' | cut -d' ' -f1)
            local backend=$(grep -h "proxy_pass" "$conf" | head -1 | awk '{print $2}' | tr -d ';')
            local ssl=$(grep -h "ssl_certificate" "$conf" | head -1 | awk '{print $2}' | tr -d ';')
            
            echo "  域名: $domain"
            echo "  端口: $port"
            echo "  后端: $backend"
            
            if [ -n "$ssl" ]; then
                echo "  SSL证书: $ssl"
                if [ -f "$ssl" ]; then
                    echo -e "  ✅ 证书文件存在"
                else
                    echo -e "  ❌ 证书文件不存在"
                fi
            fi
        done
    else
        echo "  没有启用的配置"
    fi
    
    echo -e "\n${BLUE}可用的站点配置:${NC}"
    if ls /etc/nginx/sites-available/*.conf 2>/dev/null >/dev/null; then
        for conf in /etc/nginx/sites-available/*.conf; do
            local enabled="❌"
            if [ -L "/etc/nginx/sites-enabled/$(basename "$conf")" ]; then
                enabled="✅"
            fi
            echo "  $enabled $(basename "$conf")"
        done
    else
        echo "  没有可用的配置"
    fi
}

show_system_info() {
    echo -e "\n${BLUE}========== 系统信息 ==========${NC}"
    
    if [ -f /etc/os-release ]; then
        echo -e "${GREEN}操作系统:${NC}"
        grep PRETTY_NAME /etc/os-release | cut -d= -f2 | tr -d '\"'
    fi
    
    echo -e "${GREEN}Nginx版本:${NC}"
    nginx -v 2>&1
    
    echo -e "${GREEN}内存使用:${NC}"
    free -h | awk 'NR==2{printf "总: %s, 已用: %s, 可用: %s\n", $2, $3, $7}'
    
    echo -e "${GREEN}磁盘空间:${NC}"
    df -h / | awk 'NR==2{printf "总: %s, 已用: %s, 可用: %s\n", $2, $3, $4}'
    
    echo -e "${GREEN}IP地址:${NC}"
    hostname -I 2>/dev/null | awk '{print "  " $1}' || ip addr show | grep -oP 'inet \K[\d.]+' | grep -v '127.0.0.1' | head -3
    
    echo -e "${BLUE}===============================${NC}"
}

backup_config() {
    local backup_dir="/var/backups/nginx/$(date +%Y%m%d_%H%M%S)"
    
    log "INFO" "备份Nginx配置到 $backup_dir"
    
    mkdir -p "$backup_dir"
    
    cp -r /etc/nginx/nginx.conf "$backup_dir/" 2>/dev/null
    cp -r /etc/nginx/sites-available "$backup_dir/" 2>/dev/null
    cp -r /etc/nginx/sites-enabled "$backup_dir/" 2>/dev/null
    cp -r /etc/nginx/ssl "$backup_dir/" 2>/dev/null
    
    tar -czf "$backup_dir/logs.tar.gz" /var/log/nginx/*.log 2>/dev/null
    
    cat > "$backup_dir/backup.info" << EOF
备份时间: $(date)
备份目录: $backup_dir
备份内容:
- Nginx主配置
- 站点可用配置
- 站点启用配置
- SSL证书
- 日志文件

文件列表:
$(find "$backup_dir" -type f | sed 's|^|  |')
EOF
    
    echo -e "${GREEN}✅ 备份完成${NC}"
    echo -e "备份位置: $backup_dir"
    echo -e "备份大小: $(du -sh "$backup_dir" | cut -f1)"
}

show_menu() {
    clear
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${GREEN}      Nginx反向代理配置工具${NC}"
    echo -e "${BLUE}========================================${NC}"
    
    show_system_info
    
    echo -e "\n${GREEN}1.${NC} 创建新的反向代理"
    echo -e "${GREEN}2.${NC} 删除站点配置"
    echo -e "${GREEN}3.${NC} 重载Nginx配置"
    echo -e "${GREEN}4.${NC} 检查证书状态"
    echo -e "${GREEN}5.${NC} 查看当前配置"
    echo -e "${GREEN}6.${NC} 备份Nginx配置"
    echo -e "${GREEN}7.${NC} 初始化目录结构"
    echo -e "${GREEN}8.${NC} 初始化Nginx（完全清理重装）"
    echo -e "${GREEN}9.${NC} 显示系统信息"
    echo -e "${GREEN}10.${NC} 退出"
    echo -e "${BLUE}========================================${NC}"
    echo -ne "请选择操作 [1-10]: "
}

main() {
    if [ "$EUID" -ne 0 ]; then 
        echo -e "${RED}请使用root权限运行此脚本${NC}"
        exit 1
    fi
    
    if ! command -v nginx &> /dev/null; then
        echo -e "${RED}Nginx未安装，请先安装Nginx${NC}"
        echo -e "${YELLOW}安装命令: apk add nginx${NC}"
        exit 1
    fi
    
    check_dependencies
    
    init_directories
    
    while true; do
        show_menu
        read choice
        
        case $choice in
            1)
                create_proxy_config
                echo -ne "\n${YELLOW}是否现在重载Nginx？${NC} (y/n): "
                read -n 1 reload
                echo
                if [[ $reload =~ ^[Yy]$ ]]; then
                    reload_nginx
                fi
                ;;
            2)
                delete_site
                ;;
            3)
                reload_nginx
                ;;
            4)
                check_certificates
                ;;
            5)
                show_current_config
                ;;
            6)
                backup_config
                ;;
            7)
                init_directories
                ;;
            8)
                init_nginx
                ;;
            9)
                show_system_info
                ;;
            10)
                echo -e "${GREEN}退出${NC}"
                exit 0
                ;;
            *)
                echo -e "${RED}无效选择${NC}"
                ;;
        esac
        
        if [ "$choice" != "10" ]; then
            echo -ne "\n${YELLOW}按Enter继续...${NC}"
            read
        fi
    done
}

main