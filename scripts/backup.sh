#!/usr/bin/env bash
set -e

PROJECT_DIR="/opt/tgbot/bot"
SOURCE_DB="${PROJECT_DIR}/data/bot2.sqlite3"
BACKUP_DIR="${PROJECT_DIR}/backups"

mkdir -p "${BACKUP_DIR}"

if [ ! -f "${SOURCE_DB}" ]; then
  echo "ERROR: sqlite file not found: ${SOURCE_DB}"
  exit 1
fi

timestamp="$(date +%Y%m%d_%H%M)"
target="${BACKUP_DIR}/bot_${timestamp}.sqlite3"
cp "${SOURCE_DB}" "${target}"
echo "Backup created: ${target}"

ls -1t "${BACKUP_DIR}"/bot_*.sqlite3 2>/dev/null | awk 'NR>20' | xargs -r rm -f
echo "Old backups cleanup done (max 20 files)"
