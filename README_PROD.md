# Production Deployment

## Первый деплой
1. Скопируйте проект на сервер в `/opt/tgbot/bot`.
2. Создайте серверный `.env` в `/opt/tgbot/bot/.env` (файл не хранится в git).
3. Выполните:
   - `sudo bash /opt/tgbot/bot/bootstrap_server.sh`
   - `sudo bash /opt/tgbot/bot/deploy.sh`

## Обновление
- `cd /opt/tgbot/bot && sudo bash ./deploy.sh`

## Логи
- `sudo journalctl -u tgbot -f`
- `sudo journalctl -u tgbot -n 200 --no-pager`

## Проверка статуса
- `sudo systemctl status tgbot --no-pager`
- `sudo bash /opt/tgbot/bot/healthcheck.sh`

## Перезапуск
- `sudo systemctl restart tgbot`

## Восстановление после неудачного деплоя
1. `cd /opt/tgbot/bot`
2. `git reflog --date=iso`
3. `git checkout <stable_commit_sha>`
4. `sudo bash ./deploy.sh`

## Что не коммитить
- `.env`
- файлы с секретами и приватными токенами
