#!/bin/bash

# ============================================================================
# SSH安全防护与内网访问控制脚本 v4.0
# 支持 Debian 12+ / Alpine
# 作者：AI Assistant
# ============================================================================

# ============================================================================
# 1. 初始化设置
# ============================================================================

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'
NC='\033[0m' # No Color
BOLD='\033[1m'
UNDERLINE='\033[4m'

# 配置常量
readonly SSH_PORT=222
readonly DEFAULT_PORTS="80 443 222"
readonly CONFIG_DIR="/etc/ssh222-security"
readonly LOG_FILE="/var/log/ssh222-security.log"
readonly CONFIG_FILE="$CONFIG_DIR/config"
readonly PORTS_FILE="$CONFIG_DIR/ports.conf"
readonly NETWORK_FILE="$CONFIG_DIR/network.conf"
readonly BACKUP_DIR="$CONFIG_DIR/backup"
readonly RULES_DIR="$CONFIG_DIR/rules"
readonly LOCK_FILE="/var/run/ssh222-security.lock"
readonly VERSION="4.0"

# 全局变量（通过配置文件加载）
OS_TYPE=""
LOCAL_IP=""
NETWORK_SEGMENT=""
ALLOW_LOCAL_NETWORK="yes"
ALLOWED_PORTS=""
DRY_RUN=false
STATE_FILE=""

# ============================================================================
# 2. 工具函数
# ============================================================================

# 日志记录（分级）
log() {
    local level="$1"
    local message="$2"
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    local color=""
    
    case "$level" in
        "INFO") color="$GREEN" ;;
        "WARN") color="$YELLOW" ;;
        "ERROR") color="$RED" ;;
        "DEBUG") color="$CYAN" ;;
        *) color="$WHITE" ;;
    esac
    
    local log_entry="[$timestamp] [$level] $message"
    echo -e "${color}${log_entry}${NC}" | tee -a "$LOG_FILE"
}

# 带错误处理的命令执行
run_cmd() {
    local cmd="$1"
    local description="$2"
    
    log "DEBUG" "执行: $cmd"
    
    if $DRY_RUN; then
        log "INFO" "[DRY-RUN] $description"
        return 0
    fi
    
    if eval "$cmd" >> "$LOG_FILE" 2>&1; then
        log "INFO" "✓ $description"
        return 0
    else
        log "ERROR" "✗ $description 失败"
        return 1
    fi
}

# 安全执行命令，失败则退出
safe_run() {
    local cmd="$1"
    local error_msg="$2"
    
    if ! run_cmd "$cmd" "执行命令"; then
        log "ERROR" "$error_msg"
        exit 1
    fi
}

# 暂停等待用户输入
pause() {
    echo -e "\n${YELLOW}按 Enter 键继续...${NC}"
    read -r
}

# 显示横幅
show_banner() {
    clear
    echo -e "${GREEN}╔══════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║${WHITE}            SSH安全防护与内网访问控制脚本 v${VERSION}             ${GREEN}║${NC}"
    echo -e "${GREEN}║${CYAN}                   支持 Debian 12+ / Alpine                     ${GREEN}║${NC}"
    echo -e "${GREEN}╚══════════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

# 检查root权限
check_root() {
    if [[ $EUID -ne 0 ]]; then
        log "ERROR" "此脚本必须以root权限运行"
        exit 1
    fi
}

# 文件锁管理
acquire_lock() {
    if [ -f "$LOCK_FILE" ]; then
        local pid=$(cat "$LOCK_FILE" 2>/dev/null)
        if kill -0 "$pid" 2>/dev/null; then
            log "ERROR" "脚本已在运行 (PID: $pid)"
            exit 1
        else
            rm -f "$LOCK_FILE"
        fi
    fi
    echo $$ > "$LOCK_FILE"
    trap 'release_lock' EXIT INT TERM
}

release_lock() {
    rm -f "$LOCK_FILE" 2>/dev/null
}

# ============================================================================
# 3. 配置管理
# ============================================================================

# 检测系统类型
detect_os() {
    if [ -f /etc/alpine-release ]; then
        echo "alpine"
    elif [ -f /etc/debian_version ]; then
        local debian_version=$(cat /etc/debian_version | cut -d. -f1)
        if [ "$debian_version" -ge 12 ]; then
            echo "debian"
        else
            log "ERROR" "需要Debian 12或更高版本"
            exit 1
        fi
    else
        log "ERROR" "不支持的操作系统"
        exit 1
    fi
}

# 检测网络信息
detect_network() {
    # 获取本机IP
    LOCAL_IP=$(ip route get 1 2>/dev/null | awk '{print $NF;exit}' || \
               hostname -I 2>/dev/null | awk '{print $1}' || \
               echo "127.0.0.1")
    
    # 自动检测内网网段
    local ip_parts=($(echo "$LOCAL_IP" | tr '.' ' '))
    if [ ${#ip_parts[@]} -eq 4 ]; then
        # 常见内网网段
        if [ "${ip_parts[0]}" = "10" ]; then
            NETWORK_SEGMENT="10.0.0.0/8"
        elif [ "${ip_parts[0]}" = "172" ] && [ "${ip_parts[1]}" -ge 16 ] && [ "${ip_parts[1]}" -le 31 ]; then
            NETWORK_SEGMENT="172.16.0.0/12"
        elif [ "${ip_parts[0]}" = "192" ] && [ "${ip_parts[1]}" = "168" ]; then
            NETWORK_SEGMENT="192.168.0.0/16"
        else
            # 使用/24子网
            NETWORK_SEGMENT="${ip_parts[0]}.${ip_parts[1]}.${ip_parts[2]}.0/24"
        fi
    else
        NETWORK_SEGMENT="192.168.0.0/16 10.0.0.0/8 172.16.0.0/12"
    fi
    
    log "INFO" "检测到网络: IP=$LOCAL_IP, 网段=$NETWORK_SEGMENT"
}

# 加载配置
load_config() {
    # 创建配置目录
    mkdir -p "$CONFIG_DIR" "$BACKUP_DIR" "$RULES_DIR"
    
    # 设置OS_TYPE
    OS_TYPE=$(detect_os)
    
    # 加载主配置
    if [ -f "$CONFIG_FILE" ]; then
        source "$CONFIG_FILE"
    fi
    
    # 加载端口配置
    if [ -f "$PORTS_FILE" ]; then
        source "$PORTS_FILE"
    else
        ALLOWED_PORTS="$DEFAULT_PORTS"
        save_ports_config
    fi
    
    # 加载网络配置
    if [ -f "$NETWORK_FILE" ]; then
        source "$NETWORK_FILE"
    else
        detect_network
        save_network_config
    fi
    
    # 设置默认值
    : ${ALLOW_LOCAL_NETWORK:="yes"}
    
    log "INFO" "配置加载完成"
}

# 保存主配置
save_main_config() {
    cat > "$CONFIG_FILE" << EOF
# SSH Security Configuration
OS_TYPE="$OS_TYPE"
SSH_PORT="$SSH_PORT"
CONFIG_VERSION="$VERSION"
LAST_UPDATED="$(date '+%Y-%m-%d %H:%M:%S')"
EOF
    log "DEBUG" "主配置已保存"
}

# 保存端口配置
save_ports_config() {
    cat > "$PORTS_FILE" << EOF
# Allowed ports for public access
ALLOWED_PORTS="$ALLOWED_PORTS"
SSH_PORT="$SSH_PORT"
EOF
    log "DEBUG" "端口配置已保存"
}

# 保存网络配置
save_network_config() {
    cat > "$NETWORK_FILE" << EOF
# Network configuration
LOCAL_IP="$LOCAL_IP"
NETWORK_SEGMENT="$NETWORK_SEGMENT"
ALLOW_LOCAL_NETWORK="$ALLOW_LOCAL_NETWORK"
EOF
    log "DEBUG" "网络配置已保存"
}

# 保存状态（用于回滚）
save_state() {
    STATE_FILE="$BACKUP_DIR/state_$(date +%s).tar"
    
    # 保存关键文件
    local files_to_backup=(
        "/etc/ssh/sshd_config"
        "/etc/iptables/rules.v4"
        "/etc/iptables/rules.v6"
    )
    
    tar cf "$STATE_FILE" "${files_to_backup[@]}" 2>/dev/null
    log "DEBUG" "状态已保存到: $STATE_FILE"
}

# 恢复状态
restore_state() {
    if [ -f "$STATE_FILE" ]; then
        log "INFO" "正在恢复状态..."
        tar xf "$STATE_FILE" -C / 2>/dev/null
        apply_firewall_rules
        restart_ssh_service
        log "INFO" "状态已恢复"
    fi
}

# ============================================================================
# 4. 系统兼容性检查
# ============================================================================

check_system_compatibility() {
    log "INFO" "检查系统兼容性..."
    
    # 检查必需命令
    local required_cmds=("iptables" "ip6tables" "awk" "sed" "grep" "curl" "tar")
    for cmd in "${required_cmds[@]}"; do
        if ! command -v "$cmd" >/dev/null 2>&1; then
            log "WARN" "命令 $cmd 未找到，将尝试安装"
        fi
    done
    
    # 检查系统类型
    case "$OS_TYPE" in
        debian)
            local version=$(lsb_release -rs 2>/dev/null | cut -d. -f1 || echo "0")
            if [ "$version" -lt 12 ]; then
                log "ERROR" "需要Debian 12或更高版本"
                return 1
            fi
            ;;
        alpine)
            local version=$(cat /etc/alpine-release 2>/dev/null | cut -d. -f1 || echo "0")
            if [ "$version" -lt 3 ]; then
                log "ERROR" "需要Alpine 3.x或更高版本"
                return 1
            fi
            ;;
    esac
    
    log "INFO" "系统兼容性检查通过"
    return 0
}

# ============================================================================
# 5. 依赖安装
# ============================================================================

install_dependencies() {
    log "INFO" "正在安装系统依赖..."
    
    case "$OS_TYPE" in
        alpine)
            run_cmd "apk update" "更新包列表"
            run_cmd "apk add --no-cache iptables ip6tables iptables-persistent ipset fail2ban rsyslog" "安装必需软件包"
            run_cmd "rc-update add iptables default" "设置iptables开机自启"
            run_cmd "rc-update add ip6tables default" "设置ip6tables开机自启"
            run_cmd "rc-update add fail2ban default" "设置fail2ban开机自启"
            run_cmd "rc-service iptables start" "启动iptables服务"
            run_cmd "rc-service fail2ban start" "启动fail2ban服务"
            ;;
        debian)
            run_cmd "apt-get update" "更新包列表"
            run_cmd "apt-get install -y iptables-persistent netfilter-persistent fail2ban ipset" "安装必需软件包"
            run_cmd "systemctl enable netfilter-persistent" "设置netfilter-persistent开机自启"
            run_cmd "systemctl enable fail2ban" "设置fail2ban开机自启"
            ;;
    esac
    
    log "INFO" "依赖安装完成"
}

# ============================================================================
# 6. SSH服务配置
# ============================================================================

configure_ssh() {
    log "INFO" "正在配置SSH服务..."
    
    # 备份原始配置
    local backup_file="$BACKUP_DIR/sshd_config.backup_$(date +%Y%m%d_%H%M%S)"
    run_cmd "cp /etc/ssh/sshd_config '$backup_file'" "备份SSH配置"
    
    # 使用模板生成新配置
    cat > /tmp/sshd_config.new << EOF
# ============================================================================
# SSH Configuration - Generated by ssh222-security v$VERSION
# ============================================================================

# Port configuration
Port $SSH_PORT

# Security settings
PermitRootLogin no
PasswordAuthentication yes
PubkeyAuthentication yes
MaxAuthTries 3
ClientAliveInterval 300
ClientAliveCountMax 2
LoginGraceTime 1m
X11Forwarding no
AllowTcpForwarding yes
PermitTunnel yes
AllowAgentForwarding yes

# Authentication
ChallengeResponseAuthentication no
UsePAM yes
PrintMotd no
TCPKeepAlive yes

# Performance
UseDNS no
Compression yes

# Logging
SyslogFacility AUTH
LogLevel INFO

# Restrict access (optional)
# AllowUsers yourusername
# AllowGroups sshusers
EOF
    
    # 应用新配置
    run_cmd "cp /tmp/sshd_config.new /etc/ssh/sshd_config" "应用SSH配置"
    run_cmd "rm -f /tmp/sshd_config.new" "清理临时文件"
    
    # 重启SSH服务
    restart_ssh_service
    
    log "INFO" "SSH配置完成"
}

restart_ssh_service() {
    log "INFO" "重启SSH服务..."
    
    case "$OS_TYPE" in
        alpine)
            run_cmd "rc-service sshd restart" "重启SSH服务"
            ;;
        debian)
            run_cmd "systemctl restart ssh" "重启SSH服务"
            ;;
    esac
    
    # 验证服务状态
    sleep 2
    if check_ssh_service; then
        log "INFO" "SSH服务运行正常"
    else
        log "ERROR" "SSH服务启动失败"
        return 1
    fi
}

check_ssh_service() {
    case "$OS_TYPE" in
        alpine)
            rc-service sshd status >/dev/null 2>&1
            ;;
        debian)
            systemctl is-active ssh >/dev/null 2>&1
            ;;
    esac
}

# ============================================================================
# 7. 防火墙管理
# ============================================================================

# 创建防火墙链
setup_firewall_chains() {
    log "INFO" "设置防火墙链..."
    
    # 清空现有规则（保留计数器）
    run_cmd "iptables -F" "清空IPv4规则"
    run_cmd "iptables -X" "删除IPv4自定义链"
    run_cmd "iptables -Z" "清空IPv4计数器"
    run_cmd "ip6tables -F" "清空IPv6规则"
    run_cmd "ip6tables -X" "删除IPv6自定义链"
    run_cmd "ip6tables -Z" "清空IPv6计数器"
    
    # 创建自定义链
    run_cmd "iptables -N SSH222_INPUT" "创建SSH222_INPUT链"
    run_cmd "iptables -N SSH222_LOCAL" "创建SSH222_LOCAL链"
    run_cmd "ip6tables -N SSH222_INPUT" "创建IPv6 SSH222_INPUT链"
    run_cmd "ip6tables -N SSH222_LOCAL" "创建IPv6 SSH222_LOCAL链"
    
    # 设置默认策略
    run_cmd "iptables -P INPUT DROP" "设置IPv4 INPUT默认策略为DROP"
    run_cmd "iptables -P FORWARD DROP" "设置IPv4 FORWARD默认策略为DROP"
    run_cmd "iptables -P OUTPUT ACCEPT" "设置IPv4 OUTPUT默认策略为ACCEPT"
    run_cmd "ip6tables -P INPUT DROP" "设置IPv6 INPUT默认策略为DROP"
    run_cmd "ip6tables -P FORWARD DROP" "设置IPv6 FORWARD默认策略为DROP"
    run_cmd "ip6tables -P OUTPUT ACCEPT" "设置IPv6 OUTPUT默认策略为ACCEPT"
    
    # 将流量引导到自定义链
    run_cmd "iptables -A INPUT -j SSH222_INPUT" "引导IPv4流量到SSH222_INPUT链"
    run_cmd "ip6tables -A INPUT -j SSH222_INPUT" "引导IPv6流量到SSH222_INPUT链"
    
    log "INFO" "防火墙链设置完成"
}

# 配置防火墙规则
configure_firewall_rules() {
    log "INFO" "配置防火墙规则..."
    
    # 基本规则
    add_basic_rules
    
    # 内网访问规则
    if [ "$ALLOW_LOCAL_NETWORK" = "yes" ]; then
        add_local_network_rules
    fi
    
    # 公网端口规则
    add_public_port_rules
    
    # 最终默认规则
    add_final_rules
    
    log "INFO" "防火墙规则配置完成"
}

add_basic_rules() {
    # 允许本地回环
    run_cmd "iptables -A SSH222_INPUT -i lo -j ACCEPT" "允许IPv4本地回环"
    run_cmd "ip6tables -A SSH222_INPUT -i lo -j ACCEPT" "允许IPv6本地回环"
    
    # 允许已建立的连接
    run_cmd "iptables -A SSH222_INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT" "允许IPv4已建立连接"
    run_cmd "ip6tables -A SSH222_INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT" "允许IPv6已建立连接"
    
    # 允许ICMP（ping）
    run_cmd "iptables -A SSH222_INPUT -p icmp -j ACCEPT" "允许IPv4 ICMP"
    run_cmd "ip6tables -A SSH222_INPUT -p ipv6-icmp -j ACCEPT" "允许IPv6 ICMP"
}

add_local_network_rules() {
    log "INFO" "添加内网访问规则..."
    
    # 添加内网网段到本地链
    for network in $NETWORK_SEGMENT; do
        run_cmd "iptables -A SSH222_LOCAL -s $network -j ACCEPT" "允许IPv4内网网段: $network"
    done
    
    # 允许Docker网络（如果存在）
    if ip link show docker0 >/dev/null 2>&1; then
        run_cmd "iptables -A SSH222_LOCAL -s 172.17.0.0/16 -j ACCEPT" "允许Docker网络"
        run_cmd "iptables -A SSH222_LOCAL -s 172.18.0.0/16 -j ACCEPT" "允许Docker网络"
    fi
    
    # 将本地链添加到INPUT链
    run_cmd "iptables -A SSH222_INPUT -j SSH222_LOCAL" "应用内网规则"
    run_cmd "ip6tables -A SSH222_INPUT -j SSH222_LOCAL" "应用IPv6内网规则"
}

add_public_port_rules() {
    log "INFO" "添加公网端口规则..."
    
    for port in $ALLOWED_PORTS; do
        run_cmd "iptables -A SSH222_INPUT -p tcp --dport $port -j ACCEPT" "允许IPv4 TCP端口: $port"
        run_cmd "ip6tables -A SSH222_INPUT -p tcp --dport $port -j ACCEPT" "允许IPv6 TCP端口: $port"
    done
}

add_final_rules() {
    # 记录被拒绝的连接（可选）
    run_cmd "iptables -A SSH222_INPUT -m limit --limit 5/min -j LOG --log-prefix \"SSH222-DROP: \" --log-level 7" "记录被拒绝的连接"
    run_cmd "ip6tables -A SSH222_INPUT -m limit --limit 5/min -j LOG --log-prefix \"SSH222-DROP: \" --log-level 7" "记录IPv6被拒绝的连接"
    
    # 最终拒绝规则
    run_cmd "iptables -A SSH222_INPUT -j DROP" "拒绝其他IPv4连接"
    run_cmd "ip6tables -A SSH222_INPUT -j DROP" "拒绝其他IPv6连接"
}

# 应用防火墙规则
apply_firewall_rules() {
    log "INFO" "应用防火墙规则..."
    
    # 保存当前规则（用于备份）
    local backup_file="$RULES_DIR/iptables.backup_$(date +%Y%m%d_%H%M%S)"
    run_cmd "iptables-save > '$backup_file.v4'" "备份IPv4规则"
    run_cmd "ip6tables-save > '$backup_file.v6'" "备份IPv6规则"
    
    # 设置链和规则
    setup_firewall_chains
    configure_firewall_rules
    
    # 持久化保存规则
    save_firewall_rules
    
    # 验证规则
    if validate_firewall_rules; then
        log "INFO" "防火墙规则应用成功"
        return 0
    else
        log "ERROR" "防火墙规则验证失败"
        return 1
    fi
}

# 保存防火墙规则
save_firewall_rules() {
    log "INFO" "保存防火墙规则..."
    
    case "$OS_TYPE" in
        alpine)
            run_cmd "iptables-save > /etc/iptables/rules.v4" "保存IPv4规则"
            run_cmd "ip6tables-save > /etc/iptables/rules.v6" "保存IPv6规则"
            run_cmd "rc-service iptables save" "保存iptables规则"
            ;;
        debian)
            run_cmd "iptables-save > /etc/iptables/rules.v4" "保存IPv4规则"
            run_cmd "ip6tables-save > /etc/iptables/rules.v6" "保存IPv6规则"
            run_cmd "netfilter-persistent save" "保存持久化规则"
            ;;
    esac
}

# 验证防火墙规则
validate_firewall_rules() {
    log "INFO" "验证防火墙规则..."
    
    local errors=0
    
    # 检查链是否存在
    if ! iptables -L SSH222_INPUT >/dev/null 2>&1; then
        log "ERROR" "SSH222_INPUT链不存在"
        errors=$((errors + 1))
    fi
    
    # 检查端口规则
    for port in $ALLOWED_PORTS; do
        if ! iptables -L SSH222_INPUT -n | grep -q "dpt:$port.*ACCEPT"; then
            log "WARN" "端口 $port 的规则可能未生效"
            errors=$((errors + 1))
        fi
    done
    
    # 检查内网规则
    if [ "$ALLOW_LOCAL_NETWORK" = "yes" ]; then
        if ! iptables -L SSH222_LOCAL -n | grep -q "ACCEPT"; then
            log "WARN" "内网规则可能未生效"
            errors=$((errors + 1))
        fi
    fi
    
    if [ $errors -eq 0 ]; then
        log "INFO" "防火墙规则验证通过"
        return 0
    else
        log "ERROR" "发现 $errors 个问题"
        return 1
    fi
}

# ============================================================================
# 8. Fail2Ban配置
# ============================================================================

configure_fail2ban() {
    log "INFO" "配置Fail2Ban防暴力破解..."
    
    # 创建自定义jail配置
    cat > /etc/fail2ban/jail.d/sshd-custom.conf << EOF
[sshd-$SSH_PORT]
enabled = true
port = $SSH_PORT
filter = sshd
logpath = /var/log/auth.log
maxretry = 3
bantime = 3600
findtime = 600
ignoreip = 127.0.0.1/8 ::1 $NETWORK_SEGMENT

[sshd-$SSH_PORT-ddos]
enabled = true
port = $SSH_PORT
filter = sshd-ddos
logpath = /var/log/auth.log
maxretry = 5
bantime = 7200
findtime = 300
ignoreip = 127.0.0.1/8 ::1 $NETWORK_SEGMENT
EOF
    
    # 重启Fail2Ban服务
    restart_fail2ban_service
    
    log "INFO" "Fail2Ban配置完成"
}

restart_fail2ban_service() {
    log "INFO" "重启Fail2Ban服务..."
    
    case "$OS_TYPE" in
        alpine)
            run_cmd "rc-service fail2ban restart" "重启Fail2Ban"
            ;;
        debian)
            run_cmd "systemctl restart fail2ban" "重启Fail2Ban"
            ;;
    esac
}

# ============================================================================
# 9. 验证和测试
# ============================================================================

validate_port_open() {
    local port="$1"
    local source="${2:-127.0.0.1}"
    
    log "DEBUG" "验证端口 $port 在 $source 是否可访问"
    
    if timeout 1 bash -c "cat < /dev/null > /dev/tcp/$source/$port" 2>/dev/null; then
        log "INFO" "端口 $port 在 $source 可访问"
        return 0
    else
        log "WARN" "端口 $port 在 $source 不可访问"
        return 1
    fi
}

test_connections() {
    log "INFO" "开始连接测试..."
    
    echo -e "\n${CYAN}测试本机访问：${NC}"
    for port in $ALLOWED_PORTS; do
        if validate_port_open "$port" "127.0.0.1"; then
            echo -e "  端口 $port: ${GREEN}✓ 可访问${NC}"
        else
            echo -e "  端口 $port: ${RED}✗ 不可访问${NC}"
        fi
    done
    
    echo -e "\n${CYAN}测试内网访问：${NC}"
    if [ -n "$LOCAL_IP" ] && [ "$LOCAL_IP" != "127.0.0.1" ]; then
        for port in $ALLOWED_PORTS; do
            if validate_port_open "$port" "$LOCAL_IP"; then
                echo -e "  端口 $port: ${GREEN}✓ 可访问${NC}"
            else
                echo -e "  端口 $port: ${YELLOW}⚠ 可能受限${NC}"
            fi
        done
    else
        echo "  无法获取内网IP"
    fi
    
    echo -e "\n${CYAN}测试命令：${NC}"
    echo "  从内网测试SSH: ssh -p $SSH_PORT $LOCAL_IP"
    echo "  测试Web服务: curl -I http://$LOCAL_IP"
    echo "  查看防火墙: iptables -L SSH222_INPUT -n --line-numbers"
}

monitor_firewall_performance() {
    echo -e "\n${CYAN}防火墙性能统计：${NC}"
    
    # 规则数量
    local rule_count=$(iptables -L -n | wc -l)
    echo "  规则总数: $rule_count"
    
    # 链规则统计
    echo -e "\n${YELLOW}各链规则数：${NC}"
    for chain in INPUT FORWARD OUTPUT SSH222_INPUT SSH222_LOCAL; do
        local count=$(iptables -L "$chain" -n 2>/dev/null | grep -c "^ACCEPT\|^DROP\|^REJECT")
        [ $count -gt 0 ] && echo "  $chain: $count 条规则"
    done
    
    # 连接跟踪（如果启用）
    if [ -f /proc/net/nf_conntrack ]; then
        local conn_count=$(wc -l < /proc/net/nf_conntrack 2>/dev/null || echo "0")
        echo "  当前连接数: $conn_count"
    fi
}

# ============================================================================
# 10. 端口管理
# ============================================================================

open_public_ports() {
    local ports="$1"
    
    if [ -z "$ports" ]; then
        echo -e "${YELLOW}请输入要开放的端口（多个端口用空格分隔）：${NC}"
        read -p "端口: " ports
        
        if [ -z "$ports" ]; then
            log "ERROR" "未输入端口号"
            return 1
        fi
    fi
    
    # 验证端口格式
    local valid_ports=""
    local invalid_ports=""
    
    for port in $ports; do
        if [[ "$port" =~ ^[0-9]+$ ]] && [ "$port" -ge 1 ] && [ "$port" -le 65535 ]; then
            if [[ ! " $ALLOWED_PORTS " =~ " $port " ]]; then
                valid_ports="$valid_ports $port"
            else
                log "WARN" "端口 $port 已经开放"
            fi
        else
            invalid_ports="$invalid_ports $port"
        fi
    done
    
    if [ -n "$invalid_ports" ]; then
        log "ERROR" "无效的端口: $invalid_ports"
    fi
    
    if [ -n "$valid_ports" ]; then
        # 确认操作
        echo -e "\n${YELLOW}即将开放端口：$valid_ports${NC}"
        read -p "确认操作？(y/N): " confirm
        
        if [[ "$confirm" =~ ^[Yy]$ ]]; then
            # 添加到允许的端口列表
            ALLOWED_PORTS="$ALLOWED_PORTS $valid_ports"
            save_ports_config
            
            # 重新配置防火墙
            apply_firewall_rules
            
            log "INFO" "端口开放成功: $valid_ports"
            echo -e "${GREEN}✓ 端口开放完成${NC}"
        else
            log "INFO" "操作已取消"
        fi
    fi
}

delete_public_ports() {
    local ports="$1"
    
    if [ -z "$ports" ]; then
        echo -e "${YELLOW}当前开放的端口：${GREEN}$ALLOWED_PORTS${NC}"
        echo -e "${YELLOW}请输入要删除的端口（多个端口用空格分隔）：${NC}"
        read -p "端口: " ports
        
        if [ -z "$ports" ]; then
            log "ERROR" "未输入端口号"
            return 1
        fi
    fi
    
    # 检查是否包含SSH端口
    local ssh_port_warning=false
    for port in $ports; do
        if [ "$port" = "$SSH_PORT" ]; then
            ssh_port_warning=true
            break
        fi
    done
    
    if $ssh_port_warning; then
        echo -e "${RED}警告：您正在尝试删除SSH端口（$SSH_PORT）！${NC}"
        echo -e "${RED}这将导致无法远程连接服务器！${NC}"
        read -p "确定要继续吗？(输入 'CONFIRM' 确认): " confirm
        if [ "$confirm" != "CONFIRM" ]; then
            log "INFO" "操作已取消"
            return 1
        fi
    fi
    
    # 从列表中删除端口
    local new_ports=""
    local deleted_ports=""
    
    for port in $ALLOWED_PORTS; do
        if [[ " $ports " =~ " $port " ]]; then
            deleted_ports="$deleted_ports $port"
        else
            new_ports="$new_ports $port"
        fi
    done
    
    if [ -n "$deleted_ports" ]; then
        # 更新配置
        ALLOWED_PORTS=$(echo $new_ports | sed 's/^ *//;s/ *$//')
        save_ports_config
        
        # 重新配置防火墙
        apply_firewall_rules
        
        log "INFO" "端口删除成功: $deleted_ports"
        echo -e "${GREEN}✓ 端口删除完成${NC}"
        echo -e "${YELLOW}当前开放端口：${GREEN}$ALLOWED_PORTS${NC}"
    else
        log "WARN" "未找到要删除的端口"
    fi
}

manage_local_access() {
    echo -e "\n${CYAN}内网访问管理${NC}"
    echo "当前设置："
    echo "  内网访问: $([ "$ALLOW_LOCAL_NETWORK" = "yes" ] && echo "${GREEN}允许${NC}" || echo "${RED}限制${NC}")"
    echo "  内网网段: $NETWORK_SEGMENT"
    echo ""
    
    echo "选择操作："
    echo "  1. 允许内网访问所有端口（推荐）"
    echo "  2. 限制内网访问（与公网相同规则）"
    echo "  3. 自定义内网网段"
    echo "  4. 查看当前规则"
    
    read -p "请选择 [1-4]: " choice
    
    case $choice in
        1)
            ALLOW_LOCAL_NETWORK="yes"
            save_network_config
            apply_firewall_rules
            echo -e "${GREEN}✓ 已允许内网访问所有端口${NC}"
            ;;
        2)
            echo -e "${YELLOW}警告：这可能导致内网服务无法访问！${NC}"
            read -p "确定要限制内网访问吗？(输入 'YES' 确认): " confirm
            if [ "$confirm" = "YES" ]; then
                ALLOW_LOCAL_NETWORK="no"
                save_network_config
                apply_firewall_rules
                echo -e "${YELLOW}✓ 内网访问已限制${NC}"
            fi
            ;;
        3)
            echo -e "${YELLOW}当前内网网段：$NETWORK_SEGMENT${NC}"
            echo "示例：192.168.1.0/24 10.0.0.0/8（多个网段用空格分隔）"
            read -p "请输入新的内网网段: " custom_segment
            
            if [ -n "$custom_segment" ]; then
                NETWORK_SEGMENT="$custom_segment"
                save_network_config
                apply_firewall_rules
                echo -e "${GREEN}✓ 内网网段已更新${NC}"
            fi
            ;;
        4)
            echo -e "\n${CYAN}当前内网规则：${NC}"
            iptables -L SSH222_LOCAL -n --line-numbers
            ;;
        *)
            echo -e "${RED}无效选择${NC}"
            ;;
    esac
}

# ============================================================================
# 11. 主安装流程
# ============================================================================

initialize_installation() {
    show_banner
    
    echo -e "${CYAN}正在执行初始化安装...${NC}"
    echo -e "${YELLOW}此操作将：${NC}"
    echo -e "  1. 检查系统兼容性"
    echo -e "  2. 安装必要依赖"
    echo -e "  3. 修改SSH端口为222"
    echo -e "  4. 配置防火墙（允许内网+只开放公网80,443,222端口）"
    echo -e "  5. 配置Fail2Ban防暴力破解"
    echo -e "  6. 设置开机自启"
    echo ""
    
    echo -e "${GREEN}访问策略：${NC}"
    echo -e "  ✓ 允许localhost访问所有服务"
    echo -e "  ✓ 允许内网其他主机访问所有服务"
    echo -e "  ✓ 公网只允许访问80,443,222端口"
    echo ""
    
    read -p "是否继续？(y/N): " confirm
    if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
        echo -e "${YELLOW}安装已取消${NC}"
        pause
        return
    fi
    
    # 保存当前状态（用于回滚）
    save_state
    
    # 执行安装步骤
    if perform_installation; then
        show_success_message
    else
        log "ERROR" "安装失败，正在回滚..."
        restore_state
        echo -e "${RED}安装失败，已回滚到之前的状态${NC}"
    fi
    
    pause
}

perform_installation() {
    log "INFO" "开始执行安装..."
    
    # 1. 检查兼容性
    if ! check_system_compatibility; then
        return 1
    fi
    
    # 2. 安装依赖
    if ! install_dependencies; then
        log "ERROR" "依赖安装失败"
        return 1
    fi
    
    # 3. 配置SSH
    if ! configure_ssh; then
        log "ERROR" "SSH配置失败"
        return 1
    fi
    
    # 4. 配置防火墙
    if ! apply_firewall_rules; then
        log "ERROR" "防火墙配置失败"
        return 1
    fi
    
    # 5. 配置Fail2Ban
    if ! configure_fail2ban; then
        log "ERROR" "Fail2Ban配置失败"
        return 1
    fi
    
    # 6. 设置开机自启
    setup_autostart
    
    log "INFO" "安装完成"
    return 0
}

setup_autostart() {
    log "INFO" "设置开机自启..."
    
    # 创建系统服务
    cat > /etc/systemd/system/ssh222-security.service << EOF
[Unit]
Description=SSH Security Firewall Service
After=network.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/bash -c 'iptables-restore < /etc/iptables/rules.v4; ip6tables-restore < /etc/iptables/rules.v6'
ExecStop=/bin/bash -c 'iptables -F; iptables -X; iptables -P INPUT ACCEPT; ip6tables -F; ip6tables -X; ip6tables -P INPUT ACCEPT'

[Install]
WantedBy=multi-user.target
EOF
    
    case "$OS_TYPE" in
        alpine)
            # Alpine使用OpenRC
            cat > /etc/init.d/ssh222-security << 'EOF'
#!/sbin/openrc-run
name="ssh222-security"
description="SSH Security Firewall"

depend() {
    need net
    after firewall
}

start() {
    ebegin "Loading SSH Security firewall rules"
    iptables-restore < /etc/iptables/rules.v4 2>/dev/null
    ip6tables-restore < /etc/iptables/rules.v6 2>/dev/null
    eend $?
}

stop() {
    ebegin "Stopping SSH Security firewall"
    iptables -F
    iptables -X
    iptables -P INPUT ACCEPT
    ip6tables -F
    ip6tables -X
    ip6tables -P INPUT ACCEPT
    eend $?
}
EOF
            chmod +x /etc/init.d/ssh222-security
            rc-update add ssh222-security default
            ;;
        debian)
            # Debian使用systemd
            run_cmd "systemctl daemon-reload" "重载systemd配置"
            run_cmd "systemctl enable ssh222-security" "启用ssh222-security服务"
            run_cmd "systemctl start ssh222-security" "启动ssh222-security服务"
            ;;
    esac
    
    log "INFO" "开机自启设置完成"
}

show_success_message() {
    echo -e "\n${GREEN}════════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}             初始化安装完成！              ${NC}"
    echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
    echo ""
    echo -e "${YELLOW}重要信息：${NC}"
    echo -e "  SSH端口已改为: ${BOLD}${SSH_PORT}${NC}"
    echo -e "  公网开放端口: ${BOLD}80 443 222${NC}"
    echo -e "  内网访问: ${BOLD}允许所有端口${NC}"
    echo ""
    echo -e "${GREEN}连接方式：${NC}"
    echo -e "  从公网: ${BOLD}ssh -p 222 username@你的公网IP${NC}"
    echo -e "  从内网: ${BOLD}ssh username@$LOCAL_IP${NC}"
    echo ""
    echo -e "${CYAN}管理命令：${NC}"
    echo -e "  查看状态: ${BOLD}$0 status${NC}"
    echo -e "  开放端口: ${BOLD}$0 open-port 端口号${NC}"
    echo -e "  删除端口: ${BOLD}$0 delete-port 端口号${NC}"
    echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
}

# ============================================================================
# 12. 卸载功能
# ============================================================================

uninstall() {
    show_banner
    
    echo -e "${RED}════════════════════════════════════════════════════════════${NC}"
    echo -e "${RED}                    警告：卸载操作                    ${NC}"
    echo -e "${RED}════════════════════════════════════════════════════════════${NC}"
    echo ""
    echo -e "${RED}此操作将：${NC}"
    echo -e "  1. 移除所有防火墙规则"
    echo -e "  2. 恢复SSH默认配置"
    echo -e "  3. 删除所有配置文件"
    echo -e "  4. 停止相关服务"
    echo ""
    echo -e "${YELLOW}注意：卸载后系统将恢复到安装前的状态${NC}"
    echo ""
    
    read -p "输入 'UNINSTALL' 确认卸载: " confirm
    if [ "$confirm" != "UNINSTALL" ]; then
        echo -e "${YELLOW}卸载已取消${NC}"
        return
    fi
    
    log "INFO" "开始卸载..."
    
    # 1. 恢复防火墙规则
    echo -e "${YELLOW}[1/4] 恢复防火墙规则...${NC}"
    iptables -F
    iptables -X
    iptables -P INPUT ACCEPT
    iptables -P FORWARD ACCEPT
    iptables -P OUTPUT ACCEPT
    ip6tables -F
    ip6tables -X
    ip6tables -P INPUT ACCEPT
    ip6tables -P FORWARD ACCEPT
    ip6tables -P OUTPUT ACCEPT
    
    # 2. 恢复SSH配置
    echo -e "${YELLOW}[2/4] 恢复SSH配置...${NC}"
    local latest_backup=$(ls -t "$BACKUP_DIR"/sshd_config.backup_* 2>/dev/null | head -1)
    if [ -f "$latest_backup" ]; then
        cp "$latest_backup" /etc/ssh/sshd_config
        restart_ssh_service
    fi
    
    # 3. 停止和禁用服务
    echo -e "${YELLOW}[3/4] 停止服务...${NC}"
    case "$OS_TYPE" in
        alpine)
            rc-service ssh222-security stop 2>/dev/null
            rc-update del ssh222-security 2>/dev/null
            rm -f /etc/init.d/ssh222-security
            ;;
        debian)
            systemctl stop ssh222-security 2>/dev/null
            systemctl disable ssh222-security 2>/dev/null
            rm -f /etc/systemd/system/ssh222-security.service
            systemctl daemon-reload
            ;;
    esac
    
    # 4. 删除配置文件
    echo -e "${YELLOW}[4/4] 删除配置文件...${NC}"
    rm -rf "$CONFIG_DIR"
    rm -f "$LOCK_FILE"
    
    echo -e "\n${GREEN}✓ 卸载完成${NC}"
    echo -e "${YELLOW}系统已恢复到安装前的状态${NC}"
    
    log "INFO" "卸载完成"
}

# ============================================================================
# 13. 状态显示
# ============================================================================

show_status() {
    show_banner
    
    echo -e "${CYAN}════════════════════════════════════════════════════════════${NC}"
    echo -e "${CYAN}                    系统状态信息                          ${NC}"
    echo -e "${CYAN}════════════════════════════════════════════════════════════${NC}"
    echo ""
    
    # 系统信息
    echo -e "${GREEN}[系统信息]${NC}"
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        echo -e "  系统: $PRETTY_NAME"
    fi
    echo -e "  脚本版本: v$VERSION"
    echo -e "  配置版本: $(grep CONFIG_VERSION "$CONFIG_FILE" 2>/dev/null | cut -d= -f2 || echo "未知")"
    
    # SSH状态
    echo -e "\n${GREEN}[SSH服务]${NC}"
    if check_ssh_service; then
        echo -e "  状态: ${GREEN}运行中${NC}"
    else
        echo -e "  状态: ${RED}未运行${NC}"
    fi
    echo -e "  端口: $SSH_PORT"
    
    # 防火墙状态
    echo -e "\n${GREEN}[防火墙]${NC}"
    if iptables -L SSH222_INPUT >/dev/null 2>&1; then
        echo -e "  状态: ${GREEN}已启用${NC}"
        echo -e "  策略: $(iptables -L INPUT -n | grep 'policy' | awk '{print $4}')"
        
        # 统计规则
        local input_rules=$(iptables -L SSH222_INPUT -n | grep -c "^ACCEPT\|^DROP\|^REJECT")
        local local_rules=$(iptables -L SSH222_LOCAL -n | grep -c "^ACCEPT\|^DROP\|^REJECT")
        echo -e "  规则数: SSH222_INPUT($input_rules), SSH222_LOCAL($local_rules)"
    else
        echo -e "  状态: ${YELLOW}未配置${NC}"
    fi
    
    # 端口状态
    echo -e "\n${GREEN}[端口配置]${NC}"
    echo -e "  公网开放: $ALLOWED_PORTS"
    echo -e "  内网访问: $([ "$ALLOW_LOCAL_NETWORK" = "yes" ] && echo "${GREEN}允许${NC}" || echo "${RED}限制${NC}")"
    
    # Fail2Ban状态
    echo -e "\n${GREEN}[Fail2Ban]${NC}"
    if command -v fail2ban-client >/dev/null; then
        if fail2ban-client status sshd-$SSH_PORT 2>/dev/null | grep -q "Status"; then
            echo -e "  状态: ${GREEN}已启用${NC}"
            local banned=$(fail2ban-client status sshd-$SSH_PORT 2>/dev/null | grep "Currently banned" | cut -d: -f2 | tr -d '[:space:]')
            echo -e "  被禁IP数: ${banned:-0}"
        else
            echo -e "  状态: ${YELLOW}未运行${NC}"
        fi
    else
        echo -e "  状态: ${RED}未安装${NC}"
    fi
    
    # 网络信息
    echo -e "\n${GREEN}[网络信息]${NC}"
    echo -e "  本机IP: $LOCAL_IP"
    echo -e "  内网网段: $NETWORK_SEGMENT"
    
    # 服务状态
    echo -e "\n${GREEN}[服务状态]${NC}"
    case "$OS_TYPE" in
        alpine)
            rc-service ssh222-security status 2>/dev/null | grep -q "started" && \
                echo -e "  ssh222-security: ${GREEN}运行中${NC}" || \
                echo -e "  ssh222-security: ${YELLOW}未运行${NC}"
            ;;
        debian)
            systemctl is-active ssh222-security >/dev/null 2>&1 && \
                echo -e "  ssh222-security: ${GREEN}运行中${NC}" || \
                echo -e "  ssh222-security: ${YELLOW}未运行${NC}"
            ;;
    esac
    
    echo ""
    echo -e "${CYAN}════════════════════════════════════════════════════════════${NC}"
    
    # 性能统计
    read -p "查看详细统计？(y/N): " show_stats
    if [[ "$show_stats" =~ ^[Yy]$ ]]; then
        monitor_firewall_performance
    fi
    
    pause
}

# ============================================================================
# 14. 主菜单
# ============================================================================

show_menu() {
    show_banner
    
    echo -e "${CYAN}════════════════════════════════════════════════════════════${NC}"
    echo -e "${CYAN}                    主菜单                           ${NC}"
    echo -e "${CYAN}════════════════════════════════════════════════════════════${NC}"
    echo ""
    echo -e "${GREEN}[1]${NC} 查看系统状态"
    echo -e "${GREEN}[2]${NC} 查看端口访问策略"
    echo -e "${GREEN}[3]${NC} 开放公网端口（内网始终可访问）"
    echo -e "${GREEN}[4]${NC} 删除公网端口"
    echo -e "${GREEN}[5]${NC} 管理内网访问设置"
    echo -e "${GREEN}[6]${NC} 初始化安装（推荐新VPS）"
    echo -e "${GREEN}[7]${NC} 测试连接"
    echo -e "${GREEN}[8]${NC} 查看安装日志"
    echo -e "${GREEN}[9]${NC} 查看防火墙统计"
    echo -e "${RED}[0]${NC} 卸载脚本"
    echo -e "${RED}[q]${NC} 退出脚本"
    echo ""
    echo -e "${CYAN}════════════════════════════════════════════════════════════${NC}"
    echo ""
    read -p "请选择操作 [0-9/q]: " choice
}

# ============================================================================
# 15. 主程序
# ============================================================================

main_interactive() {
    check_root
    acquire_lock
    load_config
    
    while true; do
        show_menu
        
        case $choice in
            1) show_status ;;
            2) show_open_ports ;;
            3) open_public_ports "" ;;
            4) delete_public_ports "" ;;
            5) manage_local_access ;;
            6) initialize_installation ;;
            7) test_connections ;;
            8) 
                echo -e "\n${CYAN}最近日志：${NC}"
                tail -20 "$LOG_FILE"
                pause
                ;;
            9) monitor_firewall_performance; pause ;;
            0) uninstall; pause ;;
            q|Q) 
                echo -e "\n${GREEN}感谢使用，再见！${NC}"
                break
                ;;
            *) 
                echo -e "${RED}无效选择，请重新输入${NC}"
                sleep 1
                ;;
        esac
    done
    
    release_lock
}

# CLI模式
main_cli() {
    check_root
    acquire_lock
    load_config
    
    case "$1" in
        install)
            initialize_installation
            ;;
        status)
            show_status
            ;;
        open-port)
            open_public_ports "$2"
            ;;
        delete-port)
            delete_public_ports "$2"
            ;;
        test)
            test_connections
            ;;
        uninstall)
            uninstall
            ;;
        help|--help|-h)
            echo "用法: $0 [命令]"
            echo "命令:"
            echo "  install               初始化安装"
            echo "  status                查看状态"
            echo "  open-port <端口>      开放端口"
            echo "  delete-port <端口>    删除端口"
            echo "  test                  测试连接"
            echo "  uninstall             卸载"
            echo "  help                  显示帮助"
            ;;
        *)
            echo -e "${RED}未知命令: $1${NC}"
            echo "使用: $0 help 查看可用命令"
            ;;
    esac
    
    release_lock
}

# ============================================================================
# 16. 脚本入口
# ============================================================================

# 如果带参数运行，使用CLI模式，否则使用交互模式
if [ $# -gt 0 ]; then
    main_cli "$@"
else
    main_interactive
fi