#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 4 ]]; then
  echo "Usage: bash scripts/instances/create_instance.sh <instance_slug> <bot_token> <admin_ids> <services>"
  exit 1
fi

instance_slug="$1"
bot_token="$2"
admin_ids="$3"
services="$4"

if [[ ! "$instance_slug" =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]*$ ]]; then
  echo "ERROR: instance_slug contains unsupported characters."
  exit 1
fi

if [[ "$EUID" -ne 0 ]]; then
  echo "ERROR: run this script as root (sudo)."
  exit 1
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "${script_dir}/../.." && pwd)"
template_path="${project_root}/templates/systemd.instance.service.tpl"

instance_root="/opt/clients"
instance_path="${instance_root}/${instance_slug}"
service_name="tgbot-${instance_slug}"
unit_path="/etc/systemd/system/${service_name}.service"

if [[ ! -f "${project_root}/requirements.txt" ]]; then
  echo "ERROR: requirements.txt not found in ${project_root}"
  exit 1
fi

if [[ ! -f "${template_path}" ]]; then
  echo "ERROR: systemd template not found at ${template_path}"
  exit 1
fi

if [[ -e "${instance_path}" ]]; then
  echo "ERROR: instance path already exists: ${instance_path}"
  exit 1
fi

if ! command -v rsync >/dev/null 2>&1; then
  echo "ERROR: rsync is required."
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 is required."
  exit 1
fi

if ! command -v systemctl >/dev/null 2>&1; then
  echo "ERROR: systemctl is required."
  exit 1
fi

mkdir -p "${instance_root}"
mkdir -p "${instance_path}"

rsync -a \
  --exclude=".git/" \
  --exclude=".venv/" \
  --exclude=".env" \
  --exclude="logs/" \
  --exclude="backups/" \
  --exclude="data/" \
  --exclude="__pycache__/" \
  --exclude="*.pyc" \
  "${project_root}/" "${instance_path}/"

python3 -m venv "${instance_path}/.venv"
mkdir -p "${instance_path}/data" "${instance_path}/logs" "${instance_path}/backups"

cat > "${instance_path}/.env" <<EOF
BOT_TOKEN=${bot_token}
ADMIN_IDS=${admin_ids}
SERVICES=${services}
BOT_STORAGE_BACKEND=sqlite
SQLITE_DB_PATH=data/bot.sqlite3
ALLOW_MULTIPLE_ACTIVE_BOOKINGS=0
LOG_LEVEL=INFO
SERVICE_NAME=${service_name}
BOT_LOG_DIR=logs
BOT_RUNTIME_LOCK_FILE=/tmp/${service_name}.lock
EOF
chmod 600 "${instance_path}/.env"

"${instance_path}/.venv/bin/pip" install --disable-pip-version-check -r "${instance_path}/requirements.txt"

sed \
  -e "s|__INSTANCE_SLUG__|${instance_slug}|g" \
  -e "s|__INSTANCE_PATH__|${instance_path}|g" \
  "${template_path}" > "${unit_path}"

systemctl daemon-reload
systemctl enable "${service_name}"
systemctl start "${service_name}"

echo
echo "Service status:"
systemctl status "${service_name}" --no-pager
echo
echo "Instance path: ${instance_path}"
echo "Logs command: journalctl -u ${service_name} -f"
