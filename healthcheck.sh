#!/usr/bin/env bash
set -e

PROJECT_DIR="/opt/tgbot/bot"
ENV_FILE="${PROJECT_DIR}/.env"
critical_fail=0

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
  critical_fail=1
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
      critical_fail=1
    fi
  else
    echo "SKIP: backend is not sqlite (BOT_STORAGE_BACKEND=${backend:-unset})"
  fi
else
  echo "SKIP: .env missing, backend unknown"
fi

echo
echo "=== python bot process ==="
if ps aux | grep -q '[p]ython.*-m app.main'; then
  echo "OK: bot process is alive"
  ps aux | grep '[p]ython.*-m app.main'
else
  echo "ERROR: bot process is not running"
  critical_fail=1
fi

echo
echo "=== bot ping (getMe) ==="
if [ -x "${PROJECT_DIR}/.venv/bin/python" ] && [ -f "${ENV_FILE}" ]; then
  if ! "${PROJECT_DIR}/.venv/bin/python" - << 'PY'
import os
import urllib.request
import urllib.error

env_file = "/opt/tgbot/bot/.env"
token = ""
try:
    with open(env_file, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("BOT_TOKEN="):
                token = line.split("=", 1)[1].strip()
                break
except Exception:
    token = ""

if not token:
    print("ERROR: BOT_TOKEN missing in .env")
    raise SystemExit(1)

url = f"https://api.telegram.org/bot{token}/getMe"
try:
    with urllib.request.urlopen(url, timeout=10) as response:
        payload = response.read().decode("utf-8", errors="ignore")
        if '"ok":true' in payload:
            print("OK: bot ping successful")
            raise SystemExit(0)
        else:
            print("ERROR: bot ping failed")
            raise SystemExit(1)
except urllib.error.URLError as exc:
    print(f"ERROR: bot ping request failed: {exc}")
    raise SystemExit(1)
except Exception as exc:
    print(f"ERROR: bot ping unexpected failure: {exc}")
    raise SystemExit(1)
PY
  then
    critical_fail=1
  fi
else
  echo "ERROR: .venv python or .env missing, ping skipped"
  critical_fail=1
fi

echo
if [ "${critical_fail}" -eq 0 ]; then
  echo "HEALTHCHECK RESULT: OK"
  exit 0
fi
echo "HEALTHCHECK RESULT: CRITICAL FAIL"
exit 1
