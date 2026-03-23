#!/usr/bin/env bash
set -e

cd /opt/tgbot/bot

git pull

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

. .venv/bin/activate
pip install -r requirements.txt
python -m compileall app

cp tgbot.service /etc/systemd/system/tgbot.service
systemctl daemon-reload
systemctl enable tgbot
systemctl restart tgbot
systemctl status tgbot --no-pager
journalctl -u tgbot -n 50 --no-pager
