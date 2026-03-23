#!/usr/bin/env bash
set -euo pipefail

if [[ "$EUID" -ne 0 ]]; then
  echo "ERROR: run this script as root (sudo)."
  exit 1
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "${script_dir}/../.." && pwd)"
instances_root="/opt/clients"

if [[ ! -d "${instances_root}" ]]; then
  echo "No instances directory found at ${instances_root}"
  exit 0
fi

if ! command -v rsync >/dev/null 2>&1; then
  echo "ERROR: rsync is required."
  exit 1
fi

if ! command -v systemctl >/dev/null 2>&1; then
  echo "ERROR: systemctl is required."
  exit 1
fi

if [[ ! -f "${project_root}/requirements.txt" ]]; then
  echo "ERROR: requirements.txt not found in ${project_root}"
  exit 1
fi

updated_count=0
failed_count=0
skipped_count=0
updated_instances=()
failed_instances=()
skipped_instances=()

shopt -s nullglob
instance_dirs=("${instances_root}"/*)
shopt -u nullglob

if (( ${#instance_dirs[@]} == 0 )); then
  echo "No instances found in ${instances_root}"
  exit 0
fi

for instance_path in "${instance_dirs[@]}"; do
  if [[ ! -d "${instance_path}" ]]; then
    continue
  fi

  instance_slug="$(basename "${instance_path}")"
  service_name="tgbot-${instance_slug}"

  if [[ ! -f "${instance_path}/requirements.txt" ]]; then
    echo "[SKIP] ${instance_slug}: requirements.txt not found."
    skipped_count=$((skipped_count + 1))
    skipped_instances+=("${instance_slug}")
    continue
  fi

  if [[ ! -x "${instance_path}/.venv/bin/python" ]]; then
    echo "[SKIP] ${instance_slug}: .venv not found."
    skipped_count=$((skipped_count + 1))
    skipped_instances+=("${instance_slug}")
    continue
  fi

  echo "[INFO] Updating ${instance_slug}..."

  if rsync -a --delete \
      --exclude=".git/" \
      --exclude=".venv/" \
      --exclude=".env" \
      --exclude="logs/" \
      --exclude="backups/" \
      --exclude="data/" \
      --exclude="__pycache__/" \
      --exclude="*.pyc" \
      "${project_root}/" "${instance_path}/" \
    && "${instance_path}/.venv/bin/pip" install --disable-pip-version-check -r "${instance_path}/requirements.txt" \
    && systemctl restart "${service_name}"; then
    updated_count=$((updated_count + 1))
    updated_instances+=("${instance_slug}")
    echo "[OK] ${instance_slug}: updated and restarted."
  else
    failed_count=$((failed_count + 1))
    failed_instances+=("${instance_slug}")
    echo "[FAIL] ${instance_slug}: update failed."
  fi
done

echo
echo "Update summary:"
echo "Updated: ${updated_count}"
echo "Skipped: ${skipped_count}"
echo "Failed: ${failed_count}"
echo "Total processed: $((updated_count + skipped_count + failed_count))"

if (( ${#updated_instances[@]} > 0 )); then
  echo "Updated instances: ${updated_instances[*]}"
fi
if (( ${#skipped_instances[@]} > 0 )); then
  echo "Skipped instances: ${skipped_instances[*]}"
fi
if (( ${#failed_instances[@]} > 0 )); then
  echo "Failed instances: ${failed_instances[*]}"
fi
