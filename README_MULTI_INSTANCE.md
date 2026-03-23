# Multi-instance deployment

Этот документ описывает запуск множества изолированных клиентов на одном сервере по схеме:
**1 клиент = 1 отдельный bot instance**.

## Подготовка сервера

- Установите системные зависимости: `git`, `python3`, `python3-venv`, `python3-pip`, `rsync`, `systemd`.
- Разместите основной репозиторий (шаблон кода) в удобном пути, например: `/opt/tgbot/bot`.
- Все клиентские инстансы будут создаваться в: `/opt/clients/<instance_slug>`.

## Создать нового клиента одной командой

Из корня проекта выполните:

`sudo bash scripts/instances/create_instance.sh <instance_slug> <BOT_TOKEN> <ADMIN_IDS> "<SERVICES>"`

Пример:

`sudo bash scripts/instances/create_instance.sh alina 123456:ABCDEF "11111111,22222222" "Маникюр,Педикюр"`

Если вы уже работаете под root, можно без `sudo`:

`bash scripts/instances/create_instance.sh alina <TOKEN> <ADMIN_IDS> "Маникюр,Педикюр"`

Что делает команда:
- копирует код в `/opt/clients/<instance_slug>` без `.git`, `.venv`, `.env`, `logs`, `backups`, `data`;
- создает `.venv`, `data/`, `logs/`, `backups/`;
- генерирует индивидуальный `.env`;
- устанавливает зависимости;
- создает и запускает systemd unit `tgbot-<instance_slug>`.

## Логи конкретного клиента

- Онлайн-логи: `sudo journalctl -u tgbot-<instance_slug> -f`
- Последние 200 строк: `sudo journalctl -u tgbot-<instance_slug> -n 200 --no-pager`

## Остановить/запустить конкретного клиента

- Остановить: `sudo systemctl stop tgbot-<instance_slug>`
- Запустить: `sudo systemctl start tgbot-<instance_slug>`
- Перезапустить: `sudo systemctl restart tgbot-<instance_slug>`
- Статус: `sudo systemctl status tgbot-<instance_slug> --no-pager`

## Удалить клиента

1. `sudo systemctl stop tgbot-<instance_slug>`
2. `sudo systemctl disable tgbot-<instance_slug>`
3. `sudo rm -f /etc/systemd/system/tgbot-<instance_slug>.service`
4. `sudo systemctl daemon-reload`
5. `sudo rm -rf /opt/clients/<instance_slug>`

## Обновить всех клиентов

Из корня проекта:

`sudo bash scripts/instances/update_all_instances.sh`

Скрипт синхронизирует код из шаблонного репозитория в каждый `/opt/clients/<instance_slug>`, выполняет `pip install -r requirements.txt` в локальном `.venv` и делает `systemctl restart`.

## Как не сломать чужие `.env`

- Не редактируйте `.env` в шаблонном репозитории для клиентских настроек.
- Каждый клиент хранит свой `.env` только в `/opt/clients/<instance_slug>/.env`.
- Скрипт обновления **не перезаписывает** `.env` (`rsync --exclude ".env"`).
- Для изменения токена/админов/услуг редактируйте только `.env` нужного инстанса и затем делайте `sudo systemctl restart tgbot-<instance_slug>`.
