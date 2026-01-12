#!/bin/sh

# 检查 root 权限
if [ "$(id -u)" -ne 0 ]; then
    echo "错误: 必须以 root 权限运行！"
    exit 1
fi

# 1. 环境检查与安装
install_ufw() {
    if [ -f /etc/alpine-release ]; then
        apk add ufw
        modprobe ip_tables iptable_filter ip6table_filter
        rc-update add ufw default
    elif [ -f /etc/debian_version ]; then
        apt update && apt install -y ufw
    fi
    
    # 确保 ufw 配置文件中启用了 IPv6
    if [ -f /etc/default/ufw ]; then
        sed -i 's/IPV6=no/IPV6=yes/' /etc/default/ufw
    fi
}

# 2. 初始化核心规则
init_ufw() {
    echo "[*] 初始化策略: 默认拒绝所有入站 (IPv4/IPv6)..."
    ufw --force reset
    ufw default deny incoming
    ufw default allow outgoing
    
    # 默认开放核心端口 (双栈)
    for p in 80 443; do
        ufw allow "$p"/tcp
    done
    
    # SSH 222 端口限流防爆破
    ufw limit 222/tcp
    
    ufw --force enable
}

# 3. 核心处理函数 (支持批量与双栈)
manage_port() {
    action=$1
    input_list=$2  # 端口或 IP:端口
    
    # 转换逗号为内容循环
    items=$(echo "$input_list" | tr ',' ' ')
    
    for item in $items; do
        case $action in
            "allow")
                # 如果包含冒号且不是纯端口，判断为 IPv6 地址操作
                if echo "$item" | grep -q ":"; then
                    ufw allow from "$item"
                    echo "  [+] 已允许来自 IPv6 地址的访问: $item"
                else
                    ufw allow "$item"
                    echo "  [+] 已开放端口 (IPv4/IPv6): $item"
                fi
                ;;
            "deny")
                ufw deny "$item"
                echo "  [-] 已阻断端口/IP: $item"
                ;;
            "delete")
                # ufw delete 会自动匹配并删除对应的 v4 和 v6 规则
                # 循环两次确保尝试删除所有关联规则
                ufw delete allow "$item" >/dev/null 2>&1
                ufw delete deny "$item" >/dev/null 2>&1
                ufw delete limit "$item" >/dev/null 2>&1
                echo "  [x] 已完全清除 $item 的所有规则 (v4/v6)"
                ;;
        esac
    done
}

# --- 主交互界面 ---

if ! command -v ufw > /dev/null; then
    install_ufw
    init_ufw
fi

while true; do
    echo "=========================================="
    echo "      UFW 双栈管理工具 (IPv4 & IPv6)"
    echo "  默认开启: 80, 443, 222(Limit)"
    echo "=========================================="
    echo "1) 开放 (支持端口如 '80' 或 IPv6地址)"
    echo "2) 阻止 (Deny)"
    echo "3) 删除 (同时删除 v4/v6 规则)"
    echo "4) 查看当前详细规则 (Status)"
    echo "5) 仅针对特定 IPv6 开放端口"
    echo "q) 退出"
    read -p "选择操作: " opt

    case $opt in
        1)
            read -p "输入端口号 (如 8080) 或 IP: " val
            manage_port "allow" "$val"
            ;;
        2)
            read -p "输入要阻止的端口或 IP: " val
            manage_port "deny" "$val"
            ;;
        3)
            read -p "输入要删除规则的端口或 IP: " val
            manage_port "delete" "$val"
            ;;
        4)
            ufw status numbered
            ;;
        5)
            read -p "输入 IPv6 地址: " ip6
            read -p "输入端口号: " p6
            ufw allow from "$ip6" to any port "$p6"
            echo "  [+] 已绑定: 只有 $ip6 可以访问端口 $p6"
            ;;
        q) break ;;
    esac
done