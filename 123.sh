# 完全清理并重新安装的完整脚本
bash -c "$(cat << 'EOF'
# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}>>> 开始修复Nginx...${NC}"

# 1. 停止Nginx
echo -e "${YELLOW}停止Nginx进程...${NC}"
pkill nginx 2>/dev/null
sleep 2

# 2. 卸载所有相关包
echo -e "${YELLOW}卸载Nginx包...${NC}"
apk del nginx nginx-* --purge 2>/dev/null

# 3. 清理残留文件
echo -e "${YELLOW}清理残留文件...${NC}"
rm -rf /etc/nginx /var/lib/nginx /var/log/nginx /run/nginx /usr/share/nginx

# 4. 更新并重新安装
echo -e "${YELLOW}更新包列表并重新安装...${NC}"
apk update
apk add nginx

# 5. 检查安装的文件
echo -e "${YELLOW}检查安装的文件...${NC}"
apk info -L nginx | grep -E "mime.types|nginx.conf"

# 6. 查找mime.types的实际位置
echo -e "${YELLOW}查找mime.types文件...${NC}"
MIME_TYPES=$(find / -name "mime.types" 2>/dev/null | head -1)
if [ -z "$MIME_TYPES" ]; then
    echo -e "${RED}未找到mime.types文件，尝试手动创建${NC}"
    
    # 如果没有找到，创建一个基本的mime.types
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

# 7. 创建目录结构
echo -e "${YELLOW}创建目录结构...${NC}"
mkdir -p /etc/nginx/{conf.d,sites-available,sites-enabled,ssl}
mkdir -p /var/log/nginx /run/nginx /var/www/html /var/lib/nginx/logs
mkdir -p $(dirname "$MIME_TYPES")

# 8. 创建正确的nginx配置
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

    # 日志
    access_log  /var/log/nginx/access.log;
    error_log   /var/log/nginx/error.log;

    # Gzip压缩
    gzip on;
    gzip_vary on;
    gzip_min_length 1024;
    gzip_types text/plain text/css text/xml text/javascript 
               application/javascript application/xml+rss 
               application/json;

    # 包含其他配置
    include /etc/nginx/conf.d/*.conf;
    include /etc/nginx/sites-enabled/*;
}
EOC

echo -e "${GREEN}配置已写入 /etc/nginx/nginx.conf${NC}"

# 9. 创建默认网页
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

# 10. 设置权限
echo -e "${YELLOW}设置文件权限...${NC}"
chown -R nginx:nginx /var/www/html /var/log/nginx /var/lib/nginx
chmod 755 /var/www/html

# 11. 测试并启动
echo -e "${YELLOW}测试配置...${NC}"
if nginx -t; then
    echo -e "${GREEN}✅ 配置测试通过${NC}"
    
    echo -e "${YELLOW}启动Nginx...${NC}"
    nginx
    
    sleep 2
    
    if pgrep nginx > /dev/null; then
        echo -e "${GREEN}✅ Nginx启动成功${NC}"
        
        # 显示状态
        echo -e "${YELLOW}运行状态：${NC}"
        echo "进程："
        ps aux | grep nginx | grep -v grep
        
        echo -e "\n监听端口："
        (netstat -tulpn 2>/dev/null || ss -tulpn 2>/dev/null) | grep nginx || echo "  等待端口监听..."
        
        echo -e "\n${GREEN}🎉 修复完成！${NC}"
        echo "访问测试： curl -I http://localhost"
    else
        echo -e "${RED}❌ Nginx启动失败${NC}"
        echo "查看错误： tail -f /var/log/nginx/error.log"
    fi
else
    echo -e "${RED}❌ 配置测试失败${NC}"
    nginx -t 2>&1
fi
EOF
)"