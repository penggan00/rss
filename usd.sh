#!/bin/bash
#(crontab -l 2>/dev/null | grep -v "usd.sh"; echo "10 06,16,23 * * 1-5 /bin/bash /root/rss/usd.sh > /dev/null 2>&1"; echo "10 06 * * 6-7 /bin/bash /root/rss/usd.sh > /dev/null 2>&1") | crontab -
# 检查usd.py进程是否在运行
#!/bin/bash
# usd.sh - 带锁和超时的版本，防止并发和卡死

cat > /root/rss/usd.sh << 'EOF'
#!/bin/bash
# usd.sh - 带锁和超时的版本

LOG=/tmp/usd_trigger.log
LOCK=/tmp/usd.lock
PY=/root/rss/rss_venv/bin/python
SCRIPT=/root/rss/usd.py

echo "$(date '+%F %T') pid=$$ ppid=$PPID start" >> "$LOG"

# ---------- 锁机制 ----------
if [ -e "$LOCK" ]; then
    if [ "$(find "$LOCK" -mmin +30 2>/dev/null)" ]; then
        echo "$(date '+%F %T') lock expired, removing" >> "$LOG"
        rm -f "$LOCK"
    else
        echo "$(date '+%F %T') locked, skip" >> "$LOG"
        exit 0
    fi
fi
touch "$LOCK"
trap 'rm -f "$LOCK"' EXIT

# ---------- 执行，30 分钟超时 ----------
timeout 1800 "$PY" "$SCRIPT"
RC=$?

if [ $RC -eq 124 ]; then
    echo "$(date '+%F %T') timeout, killed" >> "$LOG"
else
    echo "$(date '+%F %T') done rc=$RC" >> "$LOG"
fi
EOF

chmod +x /root/rss/usd.sh