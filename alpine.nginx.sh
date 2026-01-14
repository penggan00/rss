#!/bin/bash

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 域名验证函数（更宽松）
validate_domain() {
    local domain=$1
    
    # 空值检查
    if [ -z "$domain" ]; then
        echo -e "${RED}错误: 域名不能为空${NC}"
        return 1
    fi
    
    # 长度检查
    if [ ${#domain} -gt 255 ]; then
        echo -e "${RED}错误: 域名太长${NC}"
        return 1
    fi
    
    # 简单格式检查
    if [[ "$domain" =~ ^[a-zA-Z0-9]([a-zA-Z0-9\-\.]{0,253}[a-zA-Z0-9])?$ ]] && [[ "$domain" =~ \..+ ]]; then
        # 额外检查：不能以点号开头或结尾，不能有连续点号
        if [[ "$domain" =~ ^\. ]] || [[ "$domain" =~ \.$ ]] || [[ "$domain" =~ \.\. ]]; then
            echo -e "${RED}错误: 域名格式不正确 (不能以点号开头/结尾或有连续点号)${NC}"
            return 1
        fi
        
        # 检查标签长度
        local IFS="."
        local labels=($domain)
        for label in "${labels[@]}"; do
            if [ ${#label} -gt 63 ]; then
                echo -e "${RED}错误: 域名标签 '$label' 太长 (超过63个字符)${NC}"
                return 1
            fi
            
            if [[ ! "$label" =~ ^[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?$ ]]; then
                echo -e "${RED}错误: 域名标签 '$label' 包含无效字符${NC}"
                return 1
            fi
        done
        
        echo -e "${GREEN}✅ 域名格式验证通过${NC}"
        return 0
    else
        echo -e "${YELLOW}⚠️  域名格式看起来不标准，但将继续处理${NC}"
        # 仍然接受，因为可能是本地域名或其他特殊格式
        return 0
    fi
}

# 查找证书
find_certificates() {
    local domain=$1
    
    # 清理域名（去掉协议部分）
    local clean_domain=${domain#*//}
    clean_domain=${clean_domain%%/*}
    
    # 可能的证书路径
    local cert_paths=(
        "/etc/nginx/ssl/certs/${clean_domain}/fullchain.pem"
        "/etc/nginx/ssl/${clean_domain}.crt"
        "/etc/ssl/certs/${clean_domain}/fullchain.pem"
        "/etc/letsencrypt/live/${clean_domain}/fullchain.pem"
        "/root/.acme.sh/${clean_domain}/fullchain.cer"
        "/root/.acme.sh/${clean_domain}_ecc/fullchain.cer"
    )
    
    local key_paths=(
        "/etc/nginx/ssl/private/${clean_domain}/key.pem"
        "/etc/nginx/ssl/${clean_domain}.key"
        "/etc/ssl/private/${clean_domain}/key.pem"
        "/etc/letsencrypt/live/${clean_domain}/privkey.pem"
        "/root/.acme.sh/${clean_domain}/${clean_domain}.key"
        "/root/.acme.sh/${clean_domain}_ecc/${clean_domain}.key"
    )
    
    # 查找证书文件
    for cert in "${cert_paths[@]}"; do
        if [ -f "$cert" ]; then
            CERT_FILE="$cert"
            echo -e "${GREEN}找到证书: $cert${NC}"
            break
        fi
    done
    
    # 查找密钥文件
    for key in "${key_paths[@]}"; do
        if [ -f "$key" ]; then
            KEY_FILE="$key"
            echo -e "${GREEN}找到密钥: $key${NC}"
            break
        fi
    done
    
    if [ -n "$CERT_FILE" ] && [ -n "$KEY_FILE" ]; then
        return 0
    else
        # 尝试通配符证书
        local wildcard_domain="*.${clean_domain#*.}"
        cert_paths=(
            "/etc/nginx/ssl/certs/${wildcard_domain}/fullchain.pem"
            "/etc/nginx/ssl/${wildcard_domain}.crt"
            "/root/.acme.sh/${wildcard_domain}/fullchain.cer"
        )
        
        key_paths=(
            "/etc/nginx/ssl/private/${wildcard_domain}/key.pem"
            "/etc/nginx/ssl/${wildcard_domain}.key"
            "/root/.acme.sh/${wildcard_domain}/${wildcard_domain}.key"
        )
        
        for cert in "${cert_paths[@]}"; do
            if [ -f "$cert" ]; then
                CERT_FILE="$cert"
                echo -e "${GREEN}找到通配符证书: $cert${NC}"
                break
            fi
        done
        
        for key in "${key_paths[@]}"; do
            if [ -f "$key" ]; then
                KEY_FILE="$key"
                echo -e "${GREEN}找到通配符密钥: $key${NC}"
                break
            fi
        done
        
        if [ -n "$CERT_FILE" ] && [ -n "$KEY_FILE" ]; then
            return 0
        fi
    fi
    
    return 1
}

# 创建反向代理配置
create_proxy_config() {
    echo -e "${YELLOW}>>> 创建反向代理配置${NC}"
    
    # 获取用户输入
    while true; do
        echo -n "请输入域名 (例如: api.example.com 或 nz.215155.xyz): "
        read DOMAIN
        
        # 允许用户跳过验证
        if [ -n "$DOMAIN" ]; then
            echo -e "${YELLOW}使用域名: $DOMAIN${NC}"
            break
        else
            echo -e "${RED}错误: 域名不能为空${NC}"
        fi
    done
    
    # 验证端口
    while true; do
        echo -n "请输入后端服务端口 (例如: 3000): "
        read BACKEND_PORT
        
        if [[ "$BACKEND_PORT" =~ ^[0-9]+$ ]] && [ "$BACKEND_PORT" -ge 1 ] && [ "$BACKEND_PORT" -le 65535 ]; then
            break
        else
            echo -e "${RED}错误: 端口号必须是1-65535之间的数字${NC}"
        fi
    done
    
    echo -n "是否启用WebSocket支持？(y/n): "
    read -n 1 WS_CHOICE
    echo
    [[ $WS_CHOICE =~ ^[Yy]$ ]] && WEBSOCKET=true || WEBSOCKET=false
    
    echo -n "是否强制HTTPS？(y/n): "
    read -n 1 HTTPS_CHOICE
    echo
    [[ $HTTPS_CHOICE =~ ^[Yy]$ ]] && FORCE_HTTPS=true || FORCE_HTTPS=false
    
    # 查找证书
    echo -e "${YELLOW}正在查找证书...${NC}"
    if find_certificates "$DOMAIN"; then
        SSL_AVAILABLE=true
        echo -e "${GREEN}✅ 找到SSL证书${NC}"
        echo -e "证书文件: $CERT_FILE"
        echo -e "密钥文件: $KEY_FILE"
    else
        SSL_AVAILABLE=false
        echo -e "${YELLOW}⚠️  未找到SSL证书，将使用HTTP模式${NC}"
        
        if [ "$FORCE_HTTPS" = true ]; then
            echo -e "${YELLOW}警告: 选择了强制HTTPS但未找到证书，将使用HTTP${NC}"
            FORCE_HTTPS=false
        fi
    fi
    
    # 配置文件名
    CONFIG_FILE="/etc/nginx/sites-available/${DOMAIN}.conf"
    
    echo -e "${YELLOW}生成配置文件: $CONFIG_FILE${NC}"
    
    # 生成配置
    cat > "$CONFIG_FILE" << EOF
# 反向代理配置: $DOMAIN -> 127.0.0.1:$BACKEND_PORT
# 生成时间: $(date)
# 证书: $( [ "$SSL_AVAILABLE" = true ] && echo "已配置" || echo "未配置" )

# HTTP服务器 - 用于重定向或直接服务
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;
    
    # 安全头部
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    
    # 访问日志
    access_log /var/log/nginx/${DOMAIN}_access.log;
    error_log /var/log/nginx/${DOMAIN}_error.log;
EOF

    # 如果有证书且强制HTTPS，添加重定向
    if [ "$SSL_AVAILABLE" = true ] && [ "$FORCE_HTTPS" = true ]; then
        cat >> "$CONFIG_FILE" << EOF
    
    # 强制HTTPS重定向
    return 301 https://\$server_name\$request_uri;
}
EOF
    else
        # HTTP直接代理
        cat >> "$CONFIG_FILE" << EOF
    
    # 代理设置
    location / {
        proxy_pass http://127.0.0.1:$BACKEND_PORT;
        proxy_http_version 1.1;
        
        # 基础代理头
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Forwarded-Host \$server_name;
        
        # 连接设置
        proxy_buffering off;
        proxy_buffer_size 4k;
        proxy_buffers 8 4k;
        proxy_read_timeout 60s;
        proxy_connect_timeout 60s;
        
        # 保持活动连接
        proxy_set_header Connection "";
    }
EOF
        
        # 如果启用WebSocket，添加配置
        if [ "$WEBSOCKET" = true ]; then
            cat >> "$CONFIG_FILE" << EOF
    
    # WebSocket支持
    location /ws/ {
        proxy_pass http://127.0.0.1:$BACKEND_PORT;
        proxy_http_version 1.1;
        
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "Upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        
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
        
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
EOF
        fi
        
        echo "}" >> "$CONFIG_FILE"
    fi
    
    # 如果有证书，添加HTTPS服务器配置
    if [ "$SSL_AVAILABLE" = true ]; then
        cat >> "$CONFIG_FILE" << EOF

# HTTPS服务器配置
server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name $DOMAIN;
    
    # SSL证书
    ssl_certificate $CERT_FILE;
    ssl_certificate_key $KEY_FILE;
    
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
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    
    # 访问日志
    access_log /var/log/nginx/${DOMAIN}_ssl_access.log;
    error_log /var/log/nginx/${DOMAIN}_ssl_error.log;
    
    # 代理设置
    location / {
        proxy_pass http://127.0.0.1:$BACKEND_PORT;
        proxy_http_version 1.1;
        
        # 基础代理头
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Forwarded-Host \$server_name;
        
        # 连接设置
        proxy_buffering off;
        proxy_buffer_size 4k;
        proxy_buffers 8 4k;
        proxy_read_timeout 60s;
        proxy_connect_timeout 60s;
        
        # 保持活动连接
        proxy_set_header Connection "";
    }
EOF
        
        # HTTPS服务器的WebSocket配置
        if [ "$WEBSOCKET" = true ]; then
            cat >> "$CONFIG_FILE" << EOF
    
    # WebSocket支持 (HTTPS)
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
    fi
    
    # 启用配置
    mkdir -p /etc/nginx/sites-enabled
    ln -sf "$CONFIG_FILE" "/etc/nginx/sites-enabled/${DOMAIN}.conf"
    
    echo -e "\n${GREEN}✅ 配置创建成功${NC}"
    echo -e "${BLUE}配置文件:${NC} $CONFIG_FILE"
    echo -e "${BLUE}域名:${NC} $DOMAIN"
    echo -e "${BLUE}后端服务:${NC} 127.0.0.1:$BACKEND_PORT"
    echo -e "${BLUE}SSL:${NC} $( [ "$SSL_AVAILABLE" = true ] && echo '启用' || echo '未启用' )"
    echo -e "${BLUE}强制HTTPS:${NC} $( [ "$FORCE_HTTPS" = true ] && echo '是' || echo '否' )"
    echo -e "${BLUE}WebSocket:${NC} $( [ "$WEBSOCKET" = true ] && echo '启用' || echo '未启用' )"
    
    if [ "$SSL_AVAILABLE" = false ]; then
        echo -e "\n${YELLOW}提示: 证书路径应为:${NC}"
        echo -e "  /etc/nginx/ssl/certs/$DOMAIN/fullchain.pem"
        echo -e "  /etc/nginx/ssl/private/$DOMAIN/key.pem"
    fi
}

# 测试并重载Nginx
reload_nginx() {
    echo -e "${YELLOW}>>> 测试Nginx配置...${NC}"
    
    if nginx -t 2>&1; then
        echo -e "${GREEN}✅ 配置测试通过${NC}"
        
        echo -e "${YELLOW}重载Nginx...${NC}"
        nginx -s reload 2>/dev/null || rc-service nginx reload 2>/dev/null
        
        if [ $? -eq 0 ]; then
            echo -e "${GREEN}✅ Nginx重载成功${NC}"
            
            # 显示配置摘要
            echo -e "\n${BLUE}================ 配置摘要 ================${NC}"
            echo -e "${GREEN}当前启用的代理:${NC}"
            ls -1 /etc/nginx/sites-enabled/*.conf 2>/dev/null | while read conf; do
                domain=$(grep "server_name" "$conf" | head -1 | awk '{print $2}' | tr -d ';')
                echo "  - $domain"
            done
            
            echo -e "\n${GREEN}监听端口:${NC}"
            netstat -tulpn 2>/dev/null | grep -E ":80\>|:443\>" | awk '{print "  " $4}'
            
            echo -e "${BLUE}========================================${NC}"
        else
            echo -e "${RED}❌ Nginx重载失败${NC}"
        fi
    else
        echo -e "${RED}❌ 配置测试失败${NC}"
        echo -e "${YELLOW}错误详情:${NC}"
        nginx -t 2>&1 | tail -10
    fi
}

# 检查证书状态
check_certificates() {
    echo -e "${YELLOW}>>> 检查证书状态${NC}"
    
    echo -e "${BLUE}搜索证书目录...${NC}"
    
    # 查找所有证书
    find /etc/nginx/ssl -name "*.pem" -o -name "*.crt" -o -name "*.key" 2>/dev/null | while read file; do
        if [ -f "$file" ]; then
            size=$(du -h "$file" | cut -f1)
            perms=$(stat -c "%a %U:%G" "$file")
            echo -e "  $file ($size, $perms)"
        fi
    done
    
    # 显示目录结构
    echo -e "\n${BLUE}证书目录结构:${NC}"
    if [ -d "/etc/nginx/ssl" ]; then
        tree /etc/nginx/ssl 2>/dev/null || ls -la /etc/nginx/ssl/
    else
        echo "  /etc/nginx/ssl/ 目录不存在"
        echo -e "${YELLOW}创建证书目录...${NC}"
        mkdir -p /etc/nginx/ssl/{certs,private}
    fi
}

# 主菜单
show_menu() {
    echo -e "\n${BLUE}========== Nginx反向代理配置 ==========${NC}"
    echo -e "${GREEN}1.${NC} 创建新的反向代理"
    echo -e "${GREEN}2.${NC} 重载Nginx配置"
    echo -e "${GREEN}3.${NC} 检查证书状态"
    echo -e "${GREEN}4.${NC} 查看当前配置"
    echo -e "${GREEN}5.${NC} 退出"
    echo -e "${BLUE}========================================${NC}"
    echo -n "请选择操作 [1-5]: "
}

# 查看当前配置
show_current_config() {
    echo -e "${YELLOW}>>> 当前Nginx配置${NC}"
    
    echo -e "${BLUE}启用的站点:${NC}"
    if ls /etc/nginx/sites-enabled/*.conf 2>/dev/null >/dev/null; then
        for conf in /etc/nginx/sites-enabled/*.conf; do
            echo -e "\n${GREEN}配置文件: $(basename $conf)${NC}"
            echo "域名: $(grep -h "server_name" "$conf" | head -1 | awk '{print $2}' | tr -d ';')"
            echo "端口: $(grep -h "listen" "$conf" | grep -v "listen \[::\]" | head -1 | awk '{print $2}' | tr -d ';')"
            echo "后端: $(grep -h "proxy_pass" "$conf" | head -1 | awk '{print $2}' | tr -d ';')"
        done
    else
        echo "  没有启用的配置"
    fi
    
    echo -e "\n${BLUE}Nginx状态:${NC}"
    if pgrep nginx > /dev/null; then
        echo -e "${GREEN}✅ Nginx正在运行${NC}"
    else
        echo -e "${RED}❌ Nginx未运行${NC}"
    fi
}

# 主函数
main() {
    # 检查root权限
    if [ "$EUID" -ne 0 ]; then 
        echo -e "${RED}请使用root权限运行此脚本${NC}"
        exit 1
    fi
    
    # 检查Nginx是否安装
    if ! command -v nginx &> /dev/null; then
        echo -e "${RED}Nginx未安装，请先安装Nginx${NC}"
        exit 1
    fi
    
    echo -e "${BLUE}========================================${NC}"
    echo -e "${GREEN}  Nginx反向代理配置工具${NC}"
    echo -e "${BLUE}========================================${NC}"
    echo -e "系统: $(cat /etc/os-release | grep PRETTY_NAME | cut -d= -f2 | tr -d '\"')"
    echo -e "Nginx版本: $(nginx -v 2>&1 | cut -d/ -f2)"
    echo -e "IP地址: $(hostname -I 2>/dev/null | awk '{print $1}')"
    echo -e "${BLUE}========================================${NC}"
    
    while true; do
        show_menu
        read choice
        
        case $choice in
            1)
                create_proxy_config
                echo -e "\n${YELLOW}是否现在重载Nginx？(y/n):${NC}"
                read -n 1 reload
                echo
                if [[ $reload =~ ^[Yy]$ ]]; then
                    reload_nginx
                fi
                ;;
            2)
                reload_nginx
                ;;
            3)
                check_certificates
                ;;
            4)
                show_current_config
                ;;
            5)
                echo -e "${GREEN}退出${NC}"
                exit 0
                ;;
            *)
                echo -e "${RED}无效选择${NC}"
                ;;
        esac
        
        echo -e "\n${YELLOW}按Enter继续...${NC}"
        read
    done
}

# 运行主函数
main