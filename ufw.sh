#!/bin/bash

# 交互式UFW管理脚本
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
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
    if command -v ufw &> /dev/null; then
        return 0
    fi
    
    case $OS in
        "debian"|"ubuntu")
            apt-get update && apt-get install -y ufw
            ;;
        "alpine")
            apk update && apk add ufw
            ;;
        *)
            exit 1
            ;;
    esac
}

check_ufw_status() {
    ufw status | grep -q "Status: active"
}

view_rules() {
    clear
    echo "=== 当前防火墙规则 ==="
    ufw status numbered
    echo "===================="
    echo ""
}

open_port() {
    echo "输入端口（多个用空格分隔）:"
    read -r ports
    
    for port in $ports; do
        if [[ $port =~ ^[0-9]+$ ]]; then
            ufw allow $port
        fi
    done
}

close_port() {
    echo "输入要关闭的端口:"
    read -r ports
    
    for port in $ports; do
        if [[ $port =~ ^[0-9]+$ ]]; then
            # 删除所有相关规则
            while ufw status | grep -q " $port/"; do
                rule_num=$(ufw status numbered | grep " $port/" | head -1 | sed -n 's/.*\[\([0-9]*\)\].*/\1/p')
                [ -n "$rule_num" ] && echo "y" | ufw delete $rule_num
            done
        fi
    done
}

whitelist_ip() {
    echo "输入IP（多个用空格分隔）:"
    read -r ips
    
    for ip in $ips; do
        if [[ $ip =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+(/[0-9]+)?$ ]] || [[ $ip =~ ^([0-9a-fA-F:]+)(/[0-9]+)?$ ]]; then
            ufw allow from $ip
        fi
    done
}

blacklist_ip() {
    echo "输入IP（多个用空格分隔）:"
    read -r ips
    
    for ip in $ips; do
        if [[ $ip =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+(/[0-9]+)?$ ]] || [[ $ip =~ ^([0-9a-fA-F:]+)(/[0-9]+)?$ ]]; then
            ufw deny from $ip
        fi
    done
}

reset_rules() {
    echo "重置规则？ (y/N):"
    read -r confirm
    [[ $confirm != "y" && $confirm != "Y" ]] && return
    
    ufw --force disable
    ufw --force reset
    ufw default deny incoming
    ufw default allow outgoing
    
    for port in 80 443 222; do
        ufw allow $port
    done
    
    echo "y" | ufw enable
}

show_menu() {
    clear
    echo "=== UFW管理 ==="
    echo "1) 开放端口"
    echo "2) 关闭端口"
    echo "3) 查看规则"
    echo "4) 白名单IP"
    echo "5) 黑名单IP"
    echo "6) 重置规则"
    echo "q) 退出"
    echo ""
}

main() {
    [ "$EUID" -ne 0 ] && echo "请使用sudo运行" && exit 1
    
    detect_os
    install_ufw
    
    while true; do
        show_menu
        read -r -p "选择: " choice
        
        case $choice in
            1) open_port ;;
            2) close_port ;;
            3) view_rules ;;
            4) whitelist_ip ;;
            5) blacklist_ip ;;
            6) reset_rules ;;
            q) exit 0 ;;
            *) ;;
        esac
        
        read -r -p "按回车继续..."
    done
}

main