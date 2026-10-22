# Контекст проекта: русский бот для MisuGram (@iris_cm_bot)

## Что это
Русскоязычный Telegram-бот модерации для кастомного сервера MisuGram (клиент OwpenGram).
Bot API: http://2.26.51.20:8088 — это НЕ api.telegram.org, а самописный сервер (типа owpengram-server на Go).

Бот: @iris_cm_bot, id 1780243243, отображаемое имя «Iris | Чат-менеджер».
Владелец: @DarkBro, id 1780243210. Тестовая группа: -1000000000022 (бот в ней есть).
Токен лежит в .env — никуда не копировать.

## Файлы (C:\Users\domer\OneDrive\Desktop\telegram_group)
- bot.py и bot_ru.py — полностью русские версии (были китайские, переведены целиком), идентичны.
- .env — BOT_TOKEN, ADMIN_IDS=1780243210,1780243202, SPAM_KEYWORDS, API_URL=http://2.26.51.20:8088, CREATOR_USERNAME=DarkBro.
- start_bot.bat — запуск с автоперезапуском, лог пишется в bot.log (UTF-8).
- Данные бота: ranks.json (должности), users.json (статистика), recent.json, messages.json (кэш сообщений),
  rules.json (правила чатов), creator.json (id создателя), keywords.json, groups.json, group_titles.json,
  whitelist.json, banned_users.json, antispam_settings.json, violation_counts.json.
- README.md — русская документация.
- Мусор от старых версий: bot.zip, original.txt.
- Рабочая папка агента: C:\Users\domer\OneDrive\Документы\deepseek-harness\default-workspace\tr_work
  (там все скрипты-патчи patch_*.py, тесты test_*.py и бэкапы backup_before_*).

## Что уже сделано
1. Полный перевод бота с китайского на русский: bot.py, README.md, комментарии .env, banned_users.json.
   Проверено: иероглифов 0, код не менялся (переводились только строки и комментарии), файлы компилируются.
2. Подключение к MisuGram: Application.builder().token(...).base_url(API_URL + '/bot');
   поставлен python-telegram-bot[job-queue]; исправлено падение «'NoneType' has no attribute 'run_repeating'».
3. Права: создатель определяется по нику из .env и запоминается в creator.json (работает и в личке);
   is_admin = ADMIN_IDS ∪ создатель ∪ должность >= 4.
4. Новые команды:
   /mute, /unmute, /ban, /unban — срок словами (от «1 секунды» до «10 лет», а также «1ч30м», «1.5 часа»,
   «10» = 10 минут, «навсегда»), в личку участнику уходит сообщение, при разбане — ссылка;
   /admin_add @ник 1-5, /admin_remove @ник 0-5, /admins (шапка «СОЗДАТЕЛЬ БОТА @DarkBro», далее
   Владелец / Администратор / Ст.Модератор / Модератор / Мл.Модератор);
   /commands (полный список), /id (свой ID и ник), /правила (+ /rules) — правила чата, задаёт владелец/админ.
5. Развлечения и профили — работают со слэшем и без него, в том числе ответом на сообщение:
   /убить, /поцеловать, /трахнуть, /выебать, /обнять, /шипперим, /кто_я, /кто_ты.
   Формат: «эмодзи | кто_написал что_сделал кому_ответили», имена — кликабельные ссылки (tg://user?id=...).
   Карточка профиля: статус в чате, ранг вида «🔥 [6] Ранг: Создатель бота», репутация,
   первое появление, последний актив, активность «д|н|м|весь».
6. Определение адресата ответа: обычный ответ → external_reply → отправитель-канал → @ник / имя / ID
   (с транслитерацией «@Мехура» → @mehura и терпимостью к опечаткам) → @бота → предыдущий писавший.
   Сервер присылает reply_to_message БЕЗ автора, поэтому автор ищется по id сообщения в messages.json.
7. Данные не теряются: users.json и остальные файлы сохраняются после каждого сообщения и при остановке
   бота (post_shutdown). Логи httpx отключены, чтобы токен не попадал в лог.
8. group_titles.json — механизм псевдонимов названий групп (очищен, чтобы показывались точные названия).
   Из /help убрана строка «Внимание: … ваш ID», убраны /jy и /jc, из /commands убран блок «Для персонала».

## Главное ограничение (проверено прямыми запросами к Bot API MisuGram)
НЕ реализованы методы: restrictChatMember (мут), banChatMember, unbanChatMember, getChat, getChatMember,
getChatAdministrators, getChatMemberCount, createChatInviteLink, exportChatInviteLink, leaveChat,
pinChatMessage, setChatPermissions, sendChatAction, forwardMessage — сервер отвечает METHOD_NOT_FOUND
или рвёт соединение.
В группах не работают editMessageText и deleteMessage (ответ CHAT_ID_INVALID, хотя в личке те же вызовы
проходят) — значит удаление спама и меню с редактированием в группах невозможны.
Работают: sendMessage, setMyCommands, getMe/getUpdates, answerCallbackQuery, sendPhoto, getFile,
а в личке — editMessageText и deleteMessage.
Итог: мут и бан на этом сервере невозможны в принципе; бот теперь отвечает на /mute и /ban понятным
текстом: «Сервер MisuGram не реализует этот метод Bot API, поэтому мут и бан тут невозможны…».

## Как запускать
Закрыть окно бота и запустить start_bot.bat. Логи — в bot.log.
Правки файлов на рабочем столе требуют разрешения (песочница DSH), бэкапы складываются в tr_work.

## Осталось по желанию
- заменить китайские спам-слова в keywords.json и .env на русские;
- удалить original.txt и bot.zip;
- вернуть псевдонимы групп в group_titles.json, если понадобится.
