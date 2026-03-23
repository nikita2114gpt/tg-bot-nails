[Unit]
Description=Telegram bot instance (__INSTANCE_SLUG__)
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=300
StartLimitBurst=10

[Service]
Type=simple
User=root
WorkingDirectory=__INSTANCE_PATH__
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=__INSTANCE_PATH__/.env
ExecStart=__INSTANCE_PATH__/.venv/bin/python -m app.main
Restart=always
RestartSec=3
TimeoutStartSec=60
KillSignal=SIGINT
TimeoutStopSec=30
LimitNOFILE=4096
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
