#!/bin/sh

# 兼容性处理：Alpine默认使用ash，不支持[[ ]]，改为使用 [ ]
# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

detect_os() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        OS=$ID
    elif [ -f /etc/alpine-release ]; then
        OS="alpine"
    else
        OS=$(uname -s)
    fi
}

install_ufw() {
    if command -v ufw > /dev/null 2>&1; then
        return 0
    fi
    echo "${YELLOW}[*] 正在安装 UFW...${NC}"
    case $OS in
        "debian"|"ubuntu")
            apt-get update && apt-get install -y ufw ;;
        "alpine")
            apk update && apk add ufw
            # Alpine需要手动加载内核模块并确保配置文件开启IPv6
            modprobe ip_tables iptable_filter ip6table_filter
            [ -f /etc/default/ufw ] && sed -i 's/IPV6=no/IPV6=yes/' /etc/default/ufw
            rc-update add ufw default ;;
    esac
}

open_port() {
    echo "输入端口（多个用空格分隔，支持 80 或 80/udp）:"
    read -r ports
    for port in $ports; do
        # 只要输入了端口，UFW默认会同时应用到 v4 和 v6
        ufw allow "$port"
        echo "${GREEN}[+] 端口 $port 已开放 (v4/v6)${NC}"
    done
}

delete_port() {
    echo "输入要彻底删除的端口 (将清理所有关联协议和 v4/v6):"
    read -r user_ports
    for p in $user_ports; do
        # 技巧：倒序获取编号，确保删除前面的不影响后面的编号
        # 匹配逻辑：匹配端口号边界，确保不误删 8080 (当你想删 80 时)
        rules=$(ufw status numbered | grep -iE "\s$p(/|\s)" | awk -F"[][]" '{print $2}' | sort -rn)
        
        if [ -z "$rules" ]; then
            echo "${RED}[!] 未找到端口 $p 的相关规则${NC}"
        else
            for rule_num in $rules; do
                # 使用 --force 避免交互确认
                ufw --force delete "$rule_num"
            done
            echo "${GREEN}[x] 端口 $p 的所有 IPv4/IPv6 及 TCP/UDP 规则已清理${NC}"
        fi
    done
}

# IP匹配正则优化，兼容 sh 语法
whitelist_ip() {
    echo "输入IP或网段:"
    read -r ips
    for ip in $ips; do
        ufw allow from "$ip"
        echo "${GREEN}[+] IP $ip 已加入白名单${NC}"
    done
}

reset_rules() {
    echo "${RED}确认重置并重新初始化？(y/N):${NC}"
    read -r confirm
    if [ "$confirm" = "y" ] || [ "$confirm" = "Y" ]; then
        ufw --force reset
        ufw default deny incoming
        ufw default allow outgoing
        # 默认开放你要求的三个端口
        ufw allow 80/tcp
        ufw allow 443/tcp
        ufw limit 222/tcp
        echo "y" | ufw enable
        echo "${GREEN}[OK] 防火墙已重置。默认只开启 80, 443, 222 (防爆破)${NC}"
    fi
}

# ... 其他 show_menu 和 main 函数保持逻辑，但将 [[ ]] 换成 [ ] ...

show_menu() {
    echo "${BLUE}=== UFW 增强管理工具 (支持 v4/v6) ===${NC}"
    echo "1) 开放端口 (同时 v4/v6)"
    echo "2) 删除端口 (全协议清理)"
    echo "3) 查看规则"
    echo "4) 白名单 IP"
    echo "5) 黑名单 IP"
    echo "6) 初始化/重置 (80,443,222)"
    echo "q) 退出"
}

main() {
    if [ "$(id -u)" -ne 0 ]; then
        echo "请使用 root 运行"
        exit 1
    fi
    detect_os
    install_ufw
    while true; do
        show_menu
        read -r choice
        case $choice in
            1) open_port ;;
            2) delete_port ;;
            3) ufw status numbered ;;
            4) whitelist_ip ;;
            5) blacklist_ip ;;
            6) reset_rules ;;
            q) exit 0 ;;
        esac
        echo "按回车继续..."
        read -r tmp
    done
}
main