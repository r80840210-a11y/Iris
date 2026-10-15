# MisuGram Iris Bot v3 — expanded modules

Python 3.11+, aiogram 3.x, SQLite, polling, custom Bot API server.

## Included commands
- Core: `/start`, `/help`, `/id`
- Profile/economy: `/profile`, `/balance`, `/bio`, `/daily`, `/pay`
- Group: `/rules`, `/setrules`, `/setwelcome`, `/welcomeon`, `/welcomeoff`, `/warnlimit`
- Moderation: `/warn`, `/warnings`, `/unwarn`, `/mute`, `/unmute`, `/ban`, `/unban`, `/kick`, `/purge`, `/modlog`
- Clans: `/clancreate`, `/clans`, `/clanjoin`, `/clanleave`, `/myclan`
- Marriage: `/marry` (reply), `/marry_yes`, `/marry_no`, `/my_marriage`, `/divorce`, `/marriages`
- Entertainment: `/choose`, `/random`, `/roll`, `/joke`, `/compliment`, `/ship` (reply)
- Reputation: `/rep`, `/rep_plus`, `/rep_minus` (reply)
- Notes: `/note name | text`, `/notes`, `/noteget name`, `/notedel name`

## Local run
Copy `.env.example` to `.env`, set `BOT_TOKEN`, then run:
```bash
pip install -r requirements.txt
python bot.py
```

## Render
Use a Background Worker or Blueprint. Build: `pip install -r requirements.txt`; Start: `python bot.py`. Add `BOT_TOKEN` as an environment variable. Polling does not need an inbound port. `render.yaml` uses a persistent disk for SQLite.

## Important
This is an independent implementation of selected Iris-like features, not a full clone of every command on the reference page. The official command list includes many more modules (moderator ranks/access rules, chat networks, triggers, statistics, anti-spam/scam databases, VIP, bookmarks, timers, catalog, exchange, inline mode, business bot, reports, giveaways, and more). Those need separate implementation and compatibility testing. The marriage and entertainment commands here are custom, simple versions.
The API server must support the Bot API methods used; otherwise affected actions will return errors.


## Дополнительно в v5
- `/stats`, `/top` — статистика сообщений и топ активности в группе.
- `/bookmark` ответом на сообщение, `/bookmarks`, `/bookmarkdel НОМЕР` — личные закладки в чате.
- `/timer СЕКУНДЫ текст` — напоминание (пока бот работает; при перезапуске таймеры сбрасываются).
- `/поддержать`, `/пожатьруку`, `/танцевать` — дополнительные шуточные действия.
- Развлекательные команды без ставок и азартных игр.


## v6 — меню команд и улучшенная модерация
- `/help`, `/commands`, `/команды`, `/списоккоманд` открывают меню с отдельными разделами команд.
- Мут с длительностью: `/mute 30m причина`, `/mute 1h причина`, `/mute 2d причина`. Поддерживаются минуты, часы, дни и недели на русском/английском.
- Временный или постоянный бан: `/ban 1d причина` или `/ban причина` (навсегда).
- `/softban причина` — удалить участника баном с немедленным разбаном.
- `/unban USER_ID` и `/unmute` снимают соответствующие ограничения.
- Бот должен быть администратором группы с правами блокировать участников и ограничивать участников. Совместимость конкретного MisuGram Bot API зависит от поддерживаемых методов.
