# DropGram Iris

Telegram-бот для управления группами, модерации и развлечений.

## Render
- Build Command: `pip install -r requirements.txt`
- Start Command: `python bot.py`
- Для Web Service бот поднимает HTTP health endpoint на `0.0.0.0:$PORT`.
- Environment: `BOT_TOKEN` обязателен; `API_URL` задавайте только если используете собственный совместимый Telegram Bot API Server. Для обычного Telegram Bot API уберите `API_URL` из окружения, чтобы использовался стандартный endpoint.
- Для сохранения SQLite добавьте Persistent Disk в `/var/data` и установите `DB_PATH=/var/data/dropgram.sqlite3`.

## Команды
Команды модерации: `/mute`, `/unmute`, `/ban`, `/warn`, `/unwarn`, `/kick`, `/softban`.
Можно ответить на сообщение или указать известного боту пользователя по `@username`/ID. Пример: `/mute @username 30м флуд`.
Развлечения поддерживают ответ на сообщение и `@username`, например `/обнять @username`, `/пнуть @username`, `/шип @username`.
Команда `/смс` удаляет сообщение, на которое ответили, и само сообщение с командой; бот должен иметь право удалять сообщения.

Telegram Bot API не позволяет боту искать любого участника группы по произвольному username. Поэтому `@username` должен быть уже замечен ботом в сообщении/команде в группе. Если пользователь ещё не писал при работающем боте, попросите его написать сообщение.
