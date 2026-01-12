#!/bin/bash

# 交互式UFW管理脚本
# 支持Debian 12 和 Alpine
# 作者: 助手

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 系统检测
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

# 检查并安装UFW
install_ufw() {
    echo -e "${BLUE}[INFO]${NC} 检查UFW安装状态..."
    
    if command -v ufw &> /dev/null; then
        echo -e "${GREEN}[SUCCESS]${NC} UFW已安装"
        return 0
    fi
    
    echo -e "${YELLOW}[WARNING]${NC} UFW未安装，正在安装..."
    
    case $OS in
        "debian"|"ubuntu")
            apt-get update
            apt-get install -y ufw
            ;;
        "alpine")
            apk update
            apk add ufw
            ;;
        *)
            echo -e "${RED}[ERROR]${NC} 不支持的操作系统: $OS"
            exit 1
            ;;
    esac
    
    if command -v ufw &> /dev/null; then
        echo -e "${GREEN}[SUCCESS]${NC} UFW安装成功"
        return 0
    else
        echo -e "${RED}[ERROR]${NC} UFW安装失败"
        exit 1
    fi
}

# 检查UFW状态
check_ufw_status() {
    if ufw status | grep -q "Status: active"; then
        return 0
    else
        return 1
    fi
}

# 启用UFW
enable_ufw() {
    echo -e "${BLUE}[INFO]${NC} 启用UFW..."
    
    # 设置默认策略
    ufw default deny incoming
    ufw default allow outgoing
    
    # 启用UFW
    echo "y" | ufw enable
    
    if check_ufw_status; then
        echo -e "${GREEN}[SUCCESS]${NC} UFW已启用"
    else
        echo -e "${RED}[ERROR]${NC} UFW启用失败"
    fi
}

# 禁用UFW
disable_ufw() {
    echo -e "${BLUE}[INFO]${NC} 禁用UFW..."
    
    ufw disable
    
    if ! check_ufw_status; then
        echo -e "${GREEN}[SUCCESS]${NC} UFW已禁用"
    else
        echo -e "${RED}[ERROR]${NC} UFW禁用失败"
    fi
}

# 查看防火墙规则
view_rules() {
    echo -e "${BLUE}[INFO]${NC} 当前防火墙规则:"
    echo "========================================"
    ufw status verbose
    echo "========================================"
    echo -e "\n${BLUE}[INFO]${NC} 详细规则列表:"
    echo "========================================"
    ufw status numbered
    echo "========================================"
}

# 开放端口
open_port() {
    echo -e "${BLUE}[INFO]${NC} 开放端口"
    echo "请输入要开放的端口（多个端口用空格分隔，如：80 443 222）:"
    read -r ports
    
    if [ -z "$ports" ]; then
        echo -e "${YELLOW}[WARNING]${NC} 未输入端口"
        return
    fi
    
    for port in $ports; do
        if [[ $port =~ ^[0-9]+$ ]]; then
            echo -e "${BLUE}[INFO]${NC} 开放端口 $port (TCP/UDP IPv4/IPv6)..."
            
            # 开放TCP和UDP
            ufw allow $port/tcp
            ufw allow $port/udp
            
            echo -e "${GREEN}[SUCCESS]${NC} 端口 $port (TCP/UDP) 已开放"
        else
            echo -e "${RED}[ERROR]${NC} 无效的端口号: $port"
        fi
    done
}

# 关闭/删除特定端口
close_port() {
    echo -e "${BLUE}[INFO]${NC} 关闭/删除端口"
    echo "请输入要关闭的端口（多个端口用空格分隔）:"
    read -r ports
    
    if [ -z "$ports" ]; then
        echo -e "${YELLOW}[WARNING]${NC} 未输入端口"
        return
    fi
    
    for port in $ports; do
        if [[ $port =~ ^[0-9]+$ ]]; then
            echo -e "${BLUE}[INFO]${NC} 关闭端口 $port..."
            
            # 删除TCP规则
            if ufw status | grep -q "$port/tcp"; then
                echo -e "${BLUE}[INFO]${NC} 删除TCP规则..."
                ufw delete allow $port/tcp
                echo -e "${GREEN}[SUCCESS]${NC} 端口 $port/tcp 已关闭"
            fi
            
            # 删除UDP规则
            if ufw status | grep -q "$port/udp"; then
                echo -e "${BLUE}[INFO]${NC} 删除UDP规则..."
                ufw delete allow $port/udp
                echo -e "${GREEN}[SUCCESS]${NC} 端口 $port/udp 已关闭"
            fi
            
            # 尝试删除可能存在的旧规则格式
            if ufw status | grep -q " $port "; then
                echo -e "${BLUE}[INFO]${NC} 删除旧格式规则..."
                ufw delete allow $port
                echo -e "${GREEN}[SUCCESS]${NC} 端口 $port 旧格式规则已删除"
            fi
            
        else
            echo -e "${RED}[ERROR]${NC} 无效的端口号: $port"
        fi
    done
}

# 删除规则（按编号）
delete_rule() {
    view_rules
    
    echo -e "\n${BLUE}[INFO]${NC} 删除规则"
    echo "请输入要删除的规则编号（多个编号用空格分隔）:"
    read -r rule_numbers
    
    if [ -z "$rule_numbers" ]; then
        echo -e "${YELLOW}[WARNING]${NC} 未输入规则编号"
        return
    fi
    
    # 反转规则编号顺序，避免删除时编号变化
    sorted_numbers=$(echo $rule_numbers | tr ' ' '\n' | sort -rn)
    
    for number in $sorted_numbers; do
        if [[ $number =~ ^[0-9]+$ ]]; then
            echo -e "${BLUE}[INFO]${NC} 删除规则 #$number..."
            
            # 获取规则详细信息
            rule_info=$(ufw status numbered | grep "^\[$number\]")
            echo -e "${YELLOW}[INFO]${NC} 正在删除: $rule_info"
            
            echo "y" | ufw delete $number
            echo -e "${GREEN}[SUCCESS]${NC} 规则 #$number 已删除"
        else
            echo -e "${RED}[ERROR]${NC} 无效的规则编号: $number"
        fi
    done
}

# 白名单IP
whitelist_ip() {
    echo -e "${BLUE}[INFO]${NC} 添加IP到白名单"
    echo "请输入IP地址或CIDR（多个用空格分隔，如：192.168.1.1 10.0.0.0/24）:"
    read -r ips
    
    if [ -z "$ips" ]; then
        echo -e "${YELLOW}[WARNING]${NC} 未输入IP地址"
        return
    fi
    
    for ip in $ips; do
        if [[ $ip =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+(/[0-9]+)?$ ]] || [[ $ip =~ ^([0-9a-fA-F:]+)(/[0-9]+)?$ ]]; then
            echo -e "${BLUE}[INFO]${NC} 添加白名单IP: $ip..."
            ufw allow from $ip
            echo -e "${GREEN}[SUCCESS]${NC} IP $ip 已添加到白名单"
        else
            echo -e "${RED}[ERROR]${NC} 无效的IP地址格式: $ip"
        fi
    done
}

# 黑名单IP
blacklist_ip() {
    echo -e "${BLUE}[INFO]${NC} 添加IP到黑名单"
    echo "请输入IP地址或CIDR（多个用空格分隔）:"
    read -r ips
    
    if [ -z "$ips" ]; then
        echo -e "${YELLOW}[WARNING]${NC} 未输入IP地址"
        return
    fi
    
    for ip in $ips; do
        if [[ $ip =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+(/[0-9]+)?$ ]] || [[ $ip =~ ^([0-9a-fA-F:]+)(/[0-9]+)?$ ]]; then
            echo -e "${BLUE}[INFO]${NC} 添加黑名单IP: $ip..."
            ufw deny from $ip
            echo -e "${GREEN}[SUCCESS]${NC} IP $ip 已添加到黑名单"
        else
            echo -e "${RED}[ERROR]${NC} 无效的IP地址格式: $ip"
        fi
    done
}

# 删除IP规则
delete_ip_rule() {
    echo -e "${BLUE}[INFO]${NC} 删除IP规则"
    echo "请输入要删除的IP地址或CIDR:"
    read -r ip
    
    if [ -z "$ip" ]; then
        echo -e "${YELLOW}[WARNING]${NC} 未输入IP地址"
        return
    fi
    
    # 查找相关的规则
    echo -e "${BLUE}[INFO]${NC} 查找与 $ip 相关的规则..."
    
    # 查找白名单规则
    if ufw status | grep -q "ALLOW.*$ip"; then
        echo -e "${YELLOW}[INFO]${NC} 找到白名单规则，正在删除..."
        ufw delete allow from $ip
        echo -e "${GREEN}[SUCCESS]${NC} $ip 白名单规则已删除"
    fi
    
    # 查找黑名单规则
    if ufw status | grep -q "DENY.*$ip"; then
        echo -e "${YELLOW}[INFO]${NC} 找到黑名单规则，正在删除..."
        ufw delete deny from $ip
        echo -e "${GREEN}[SUCCESS]${NC} $ip 黑名单规则已删除"
    fi
    
    if ! ufw status | grep -q "$ip"; then
        echo -e "${GREEN}[SUCCESS]${NC} 所有与 $ip 相关的规则已删除"
    else
        echo -e "${YELLOW}[WARNING]${NC} 可能还有与 $ip 相关的规则存在"
        echo -e "${BLUE}[INFO]${NC} 请手动检查: ufw status | grep '$ip'"
    fi
}

# 重置全部规则并设置默认
reset_rules() {
    echo -e "${YELLOW}[WARNING]${NC} 即将重置所有防火墙规则！"
    echo -e "${YELLOW}[WARNING]${NC} 这将关闭所有端口，然后只开放80, 443, 222端口"
    echo "是否继续？ (y/N):"
    read -r confirm
    
    if [[ $confirm != "y" && $confirm != "Y" ]]; then
        echo -e "${BLUE}[INFO]${NC} 操作已取消"
        return
    fi
    
    echo -e "${BLUE}[INFO]${NC} 重置所有规则..."
    
    # 禁用UFW
    ufw --force disable
    
    # 重置规则
    ufw --force reset
    
    # 设置默认策略
    ufw default deny incoming
    ufw default allow outgoing
    
    # 开放默认端口
    echo -e "${BLUE}[INFO]${NC} 开放默认端口 (80, 443, 222)..."
    
    for port in 80 443 222; do
        ufw allow $port/tcp
        ufw allow $port/udp
    done
    
    # 启用UFW
    echo "y" | ufw enable
    
    echo -e "${GREEN}[SUCCESS]${NC} 防火墙规则已重置"
    echo -e "${GREEN}[INFO]${NC} 当前仅开放端口: 80, 443, 222"
}

# 显示菜单
show_menu() {
    clear
    echo -e "${BLUE}========================================"
    echo "       UFW防火墙管理脚本"
    echo "       支持Debian 12和Alpine"
    echo "========================================${NC}"
    echo ""
    echo -e "操作系统: ${GREEN}$OS${NC}"
    echo -e "UFW状态: $(if check_ufw_status; then echo -e "${GREEN}启用${NC}"; else echo -e "${RED}禁用${NC}"; fi)"
    echo ""
    echo "请选择操作:"
    echo -e "  ${GREEN}1${NC}) 开放端口（自动开放TCP/UDP）"
    echo -e "  ${GREEN}2${NC}) 关闭防火墙"
    echo -e "  ${GREEN}3${NC}) 关闭/删除端口（删除TCP和UDP）"
    echo -e "  ${GREEN}4${NC}) 删除规则（按编号）"
    echo -e "  ${GREEN}5${NC}) 查看规则"
    echo -e "  ${GREEN}6${NC}) 添加IP到白名单"
    echo -e "  ${GREEN}7${NC}) 添加IP到黑名单"
    echo -e "  ${GREEN}8${NC}) 删除IP规则"
    echo -e "  ${GREEN}9${NC}) 重置全部规则（默认只开放80,443,222）"
    echo -e "  ${GREEN}10${NC}) 启用防火墙"
    echo -e "  ${GREEN}11${NC}) 退出"
    echo ""
}

# 主函数
main() {
    # 检测系统
    detect_os
    
    # 检查并安装UFW
    install_ufw
    
    # 主循环
    while true; do
        show_menu
        
        read -r -p "请输入选项 [1-11]: " choice
        
        case $choice in
            1)
                open_port
                ;;
            2)
                disable_ufw
                ;;
            3)
                close_port
                ;;
            4)
                delete_rule
                ;;
            5)
                view_rules
                ;;
            6)
                whitelist_ip
                ;;
            7)
                blacklist_ip
                ;;
            8)
                delete_ip_rule
                ;;
            9)
                reset_rules
                ;;
            10)
                enable_ufw
                ;;
            11)
                echo -e "${BLUE}[INFO]${NC} 再见！"
                exit 0
                ;;
            *)
                echo -e "${RED}[ERROR]${NC} 无效选项"
                ;;
        esac
        
        echo ""
        echo