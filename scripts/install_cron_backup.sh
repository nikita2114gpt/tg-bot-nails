#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/opt/tgbot/bot"
BACKUP_SCRIPT="${PROJECT_DIR}/scripts/backup.sh"
CRON_SCHEDULE="${CRON_SCHEDULE:-0 3 * * *}"
CRON_LOG="${CRON_LOG:-/var/log/tgbot-backup.log}"
CRON_MARKER="# tgbot-daily-backup"
CRON_COMMAND="${CRON_SCHEDULE} bash ${BACKUP_SCRIPT} >> ${CRON_LOG} 2>&1 ${CRON_MARKER}"

if [ ! -f "${BACKUP_SCRIPT}" ]; then
  echo "ERROR: backup script not found: ${BACKUP_SCRIPT}"
  exit 1
fi

tmp_cron="$(mktemp)"
crontab -l 2>/dev/null | sed "/${CRON_MARKER//\//\\/}/d" > "${tmp_cron}" || true
echo "${CRON_COMMAND}" >> "${tmp_cron}"
crontab "${tmp_cron}"
rm -f "${tmp_cron}"

echo "OK: cron backup installed/updated"
echo "Schedule: ${CRON_SCHEDULE}"
echo "Script: ${BACKUP_SCRIPT}"
echo "Log: ${CRON_LOG}"
