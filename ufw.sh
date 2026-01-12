#!/bin/sh

# 檢查 root 權限
if [ "$(id -u)" -ne 0 ]; then
    echo "錯誤: 必須以 root 權限執行！"
    exit 1
fi

# 1. 環境檢查與自動配置
install_and_init() {
    if [ -f /etc/alpine-release ]; then
        apk add ufw
        modprobe ip_tables iptable_filter ip6table_filter
        rc-update add ufw default
    elif [ -f /etc/debian_version ]; then
        apt update && apt install -y ufw
    fi
    
    # 強制開啟 IPv6 支持
    if [ -f /etc/default/ufw ]; then
        sed -i 's/IPV6=no/IPV6=yes/' /etc/default/ufw
    fi

    echo "[*] 正在初始化嚴格策略 (默認拒絕入站)..."
    ufw --force reset
    ufw default deny incoming
    ufw default allow outgoing
    
    # 預設開放 80, 443, 222 (雙棧)
    ufw allow 80/tcp
    ufw allow 443/tcp
    ufw limit 222/tcp  # 222 端口防爆破
    
    ufw --force enable
    echo "[OK] 初始化完成。"
}

# 2. 核心清理函數 (重點：同時刪除 TCP/UDP/v4/v6)
clean_port_rule() {
    local target=$1
    # 嘗試刪除多種可能的組合，確保清空
    ufw delete allow "$target" >/dev/null 2>&1
    ufw delete deny "$target" >/dev/null 2>&1
    ufw delete limit "$target" >/dev/null 2>&1
    ufw delete allow "$target/tcp" >/dev/null 2>&1
    ufw delete allow "$target/udp" >/dev/null 2>&1
    ufw delete deny "$target/tcp" >/dev/null 2>&1
    ufw delete deny "$target/udp" >/dev/null 2>&1
}

# 3. 處理函數
manage_port() {
    action=$1
    input=$2
    
    # 處理逗號分隔並去除空格
    items=$(echo "$input" | tr ',' ' ')
    
    for item in $items; do
        case $action in
            "allow")
                # 默認開放 TCP/UDP 雙協議
                ufw allow "$item"
                echo "  [+] 已開放 (TCP/UDP/v4/v6): $item"
                ;;
            "deny")
                ufw deny "$item"
                echo "  [-] 已阻止: $item"
                ;;
            "delete")
                # 執行深度清理
                clean_port_rule "$item"
                echo "  [x] 已徹底清理 $item (含 TCP/UDP/v4/v6)"
                ;;
        esac
    done
}

# --- 菜單界面 ---

if ! command -v ufw > /dev/null; then
    install_and_init
fi

while true; do
    echo ""
    echo "=========================================="
    echo "      UFW 深度管理工具 (Debian/Alpine)"
    echo "  策略: 默認拒絕入站 | 預設: 80,443,222"
    echo "=========================================="
    echo "1) 批量開放端口 (TCP/UDP)"
    echo "2) 批量阻止端口"
    echo "3) 批量刪除規則 (自動清理 TCP/UDP/v4/v6)"
    echo "4) 查看詳細規則 (帶編號)"
    echo "5) 按編號刪除 (精確刪除特定規則)"
    echo "6) 添加 IP 白名单 (最高優先級)"
    echo "q) 退出"
    read -p "請選擇操作: " opt

    case $opt in
        1)
            read -p "請輸入端口: " ps
            manage_port "allow" "$ps"
            ;;
        2)
            read -p "請輸入端口: " ps
            manage_port "deny" "$ps"
            ;;
        3)
            read -p "請輸入端口: " ps
            manage_port "delete" "$ps"
            ;;
        4)
            ufw status numbered
            ;;
        5)
            ufw status numbered
            read -p "請輸入要刪除的規則編號: " num
            ufw --force delete "$num"
            ;;
        6)
            read -p "請輸入白名單 IP: " wip
            ufw insert 1 allow from "$wip"
            echo "  [OK] IP $wip 已加入最高優先級白名單。"
            ;;
        q) break ;;
    esac
done