#!/usr/bin/env bash
set -e

PROJECT_ROOT="/opt/tgbot"
PROJECT_DIR="/opt/tgbot/bot"

if command -v apt-get >/dev/null 2>&1; then
  apt-get update
  apt-get install -y git python3 python3-venv python3-pip
else
  echo "ERROR: apt-get not found. Install git/python3/python3-venv/python3-pip manually."
  exit 1
fi

mkdir -p "${PROJECT_ROOT}"

if [ -d "${PROJECT_DIR}/.git" ]; then
  echo "Project already exists at ${PROJECT_DIR}, keeping existing files."
else
  echo "Project repository not found at ${PROJECT_DIR}."
  echo "Clone your repository into ${PROJECT_DIR} before deploy."
fi

echo
echo "Next steps:"
echo "1) Put your production .env in ${PROJECT_DIR}/.env (if not already there)."
echo "2) Run deploy: cd ${PROJECT_DIR} && bash ./deploy.sh"
