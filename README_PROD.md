# Production Deployment

## Первый деплой
1. Скопируйте проект на сервер в `/opt/tgbot/bot`.
2. Создайте серверный `.env` в `/opt/tgbot/bot/.env` (файл не хранится в git).
3. Выполните:
   - `sudo bash /opt/tgbot/bot/bootstrap_server.sh`
   - `sudo bash /opt/tgbot/bot/deploy.sh`

## Обновление
- `cd /opt/tgbot/bot && sudo bash ./deploy.sh`

## Ежедневные команды
- `cd /opt/tgbot/bot && sudo systemctl status tgbot --no-pager`
- `sudo journalctl -u tgbot -n 100 --no-pager`
- `sudo bash /opt/tgbot/bot/healthcheck.sh`
- `ls -1t /opt/tgbot/bot/backups | head -n 20`

## Логи
- `sudo journalctl -u tgbot -f`
- `sudo journalctl -u tgbot -n 200 --no-pager`

## Проверка статуса
- `sudo systemctl status tgbot --no-pager`
- `sudo bash /opt/tgbot/bot/healthcheck.sh`

## Перезапуск
- `sudo systemctl restart tgbot`

## Что делать если бот перестал отвечать
- `sudo systemctl status tgbot --no-pager`
- `sudo journalctl -u tgbot -n 200 --no-pager`
- `sudo systemctl restart tgbot`
- `sudo bash /opt/tgbot/bot/healthcheck.sh`

## Как сделать backup вручную
- `sudo bash /opt/tgbot/bot/scripts/backup.sh`
- `ls -1t /opt/tgbot/bot/backups | head -n 20`
- `sudo bash /opt/tgbot/bot/scripts/install_cron_backup.sh`

## Как восстановить backup
- `ls -1t /opt/tgbot/bot/backups/bot_*.sqlite3 | head -n 5`
- `sudo systemctl stop tgbot`
- `sudo cp /opt/tgbot/bot/backups/<backup_file>.sqlite3 /opt/tgbot/bot/data/bot2.sqlite3`
- `sudo chown root:root /opt/tgbot/bot/data/bot2.sqlite3`
- `sudo systemctl start tgbot`
- `sudo bash /opt/tgbot/bot/healthcheck.sh`

## Как переустановить service unit
- `cd /opt/tgbot/bot`
- `sudo cp tgbot.service /etc/systemd/system/tgbot.service`
- `sudo systemctl daemon-reload`
- `sudo systemctl enable tgbot`
- `sudo systemctl restart tgbot`
- `sudo systemctl status tgbot --no-pager`

## Восстановление после неудачного деплоя
1. `cd /opt/tgbot/bot`
2. `git reflog --date=iso`
3. `git checkout <stable_commit_sha>`
4. `sudo bash ./deploy.sh`

## Что не коммитить
- `.env`
- файлы с секретами и приватными токенами
