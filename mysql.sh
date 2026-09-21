#!/bin/bash

# 自动定位脚本所在目录，读取同目录下的 .env
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"

if [ -f "$ENV_FILE" ]; then
    export $(grep -v '^#' "$ENV_FILE" | xargs)
fi

# 解析 DATABASE_URL
URL="${DATABASE_URL#mysql://}"
USERPASS="${URL%%@*}"
REST="${URL#*@}"

DB_USER="${USERPASS%%:*}"
DB_PASS="${USERPASS#*:}"

HOSTPORT="${REST%%/*}"
DB_HOST="${HOSTPORT%%:*}"
DB_PORT="${HOSTPORT#*:}"

DBNAME="${REST#*/}"
DBNAME="${DBNAME%%\?*}"

# 执行一次读查询，成功即静默退出
mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" -p"$DB_PASS" \
      --ssl-mode=REQUIRED "$DBNAME" \
      -e "SELECT 1;" >/dev/null 2>&1

exit 0