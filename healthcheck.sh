#!/usr/bin/env bash
set -e

PROJECT_DIR="/opt/tgbot/bot"
ENV_FILE="${PROJECT_DIR}/.env"

echo "=== systemd status ==="
systemctl status tgbot --no-pager || true

echo
echo "=== journalctl last 50 ==="
journalctl -u tgbot -n 50 --no-pager || true

echo
echo "=== env file check ==="
if [ -f "${ENV_FILE}" ]; then
  echo "OK: ${ENV_FILE} exists"
else
  echo "ERROR: ${ENV_FILE} is missing"
fi

echo
echo "=== sqlite file check ==="
if [ -f "${ENV_FILE}" ]; then
  backend="$(sed -n 's/^BOT_STORAGE_BACKEND=//p' "${ENV_FILE}" | tail -n 1 | tr -d '\r' | tr '[:upper:]' '[:lower:]')"
  if [ "${backend}" = "sqlite" ]; then
    db_path="$(sed -n 's/^SQLITE_DB_PATH=//p' "${ENV_FILE}" | tail -n 1 | tr -d '\r')"
    if [ -z "${db_path}" ]; then
      db_path="data/bot2.sqlite3"
    fi
    if [[ "${db_path}" = /* ]]; then
      resolved_db_path="${db_path}"
    else
      resolved_db_path="${PROJECT_DIR}/${db_path}"
    fi

    if [ -f "${resolved_db_path}" ]; then
      echo "OK: sqlite DB exists: ${resolved_db_path}"
    else
      echo "ERROR: sqlite DB missing: ${resolved_db_path}"
    fi
  else
    echo "SKIP: backend is not sqlite (BOT_STORAGE_BACKEND=${backend:-unset})"
  fi
else
  echo "SKIP: .env missing, backend unknown"
fi

echo
echo "=== python bot process ==="
ps aux | grep '[p]ython.*-m app.main' || true
