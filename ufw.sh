#!/bin/sh

# 檢查 root 權限
if [ "$(id -u)" -ne 0 ]; then
    echo "錯誤: 必須以 root 權限執行！"
    exit 1
fi

# 1. 環境檢查與安裝 ufw
install_ufw() {
    if [ -f /etc/alpine-release ]; then
        echo "[*] 檢測到 Alpine Linux，正在安裝 ufw..."
        apk add ufw
        # 確保 Alpine 內核模組加載
        modprobe ip_tables
        modprobe iptable_filter
        rc-update add ufw default
    elif [ -f /etc/debian_version ]; then
        echo "[*] 檢測到 Debian，正在安裝 ufw..."
        apt update && apt install -y ufw
    else
        echo "不支持的系統，僅限 Debian 或 Alpine"
        exit 1
    fi
}

# 2. 初始化默認規則 (嚴格模式)
init_ufw() {
    echo "[*] 正在初始化安全策略 (默認拒絕所有入站)..."
    
    # 重置所有規則
    ufw --force reset
    
    # 設置默認策略
    ufw default deny incoming
    ufw default allow outgoing
    
    # 僅開放核心端口
    echo "[*] 開放預設端口: 80, 443, 222..."
    ufw allow 80/tcp
    ufw allow 443/tcp
    
    # 對 222 SSH 端口啟用防爆破保護 (limit 模式)
    ufw limit 222/tcp
    
    # 啟用防火牆
    ufw --force enable
    echo "[OK] ufw 已啟動，並設置為開機自啟。"
}

# 3. 端口處理邏輯
manage_port() {
    action=$1
    ports=$2
    
    # 將逗號替換為空格，支持批量處理
    ports_list=$(echo "$ports" | tr ',' ' ')
    
    for port in $ports_list; do
        case $action in
            "allow")
                ufw allow "$port"
                echo "  [+] 已開放端口: $port"
                ;;
            "deny")
                ufw deny "$port"
                echo "  [-] 已阻止端口: $port"
                ;;
            "delete")
                # 刪除已存在的 allow 或 deny 規則
                ufw delete allow "$port" > /dev/null 2>&1
                ufw delete deny "$port" > /dev/null 2>&1
                echo "  [x] 已刪除端口 $port 的所有規則"
                ;;
        esac
    done
}

# --- 執行流程 ---

# 檢查 ufw 是否安裝，若無則初始化
if ! command -v ufw > /dev/null; then
    install_ufw
    init_ufw
fi

while true; do
    echo ""
    echo "=========================================="
    echo "      UFW 交互式管理工具 (Debian/Alpine)"
    echo "  預設狀態: 拒絕所有入站 | 允許 80,443,222"
    echo "=========================================="
    echo "1) 增加開放端口 (Allow)"
    echo "2) 增加阻止端口 (Deny)"
    echo "3) 刪除端口規則 (Delete)"
    echo "4) 查看當前所有規則 (Status)"
    echo "5) 重置並重新初始化 (Reset)"
    echo "q) 退出"
    echo "------------------------------------------"
    read -p "請選擇操作 [1-5/q]: " opt

    case $opt in
        1)
            read -p "請輸入要開放的端口 (多個用逗號分隔): " ps
            manage_port "allow" "$ps"
            ;;
        2)
            read -p "請輸入要阻止的端口 (多個用逗號分隔): " ps
            manage_port "deny" "$ps"
            ;;
        3)
            read -p "請輸入要刪除規則的端口 (多個用逗號分隔): " ps
            manage_port "delete" "$ps"
            ;;
        4)
            ufw status verbose
            ;;
        5)
            init_ufw
            ;;
        q)
            break
            ;;
        *)
            echo "無效選項，請重新選擇。"
            ;;
    esac
done

echo "[*] 操作完成，所有變更已自動持久化保存。"