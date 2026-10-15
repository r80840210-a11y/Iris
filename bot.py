import asyncio
import logging
import os
import re
import random
import ast
import operator
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, ChatPermissions, CallbackQuery, ChatMemberUpdated
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramAPIError
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("dropgram-iris")

TOKEN = os.getenv("BOT_TOKEN", "").strip()
API_URL = os.getenv("API_URL", "http://67.211.221.152:8888").strip().rstrip("/")
DB_PATH = os.getenv("DB_PATH", "dropgram.sqlite3")
START_BALANCE = int(os.getenv("START_BALANCE", "100"))
dp = Dispatcher()

SCHEMA = """
CREATE TABLE IF NOT EXISTS known_groups(
 chat_id INTEGER PRIMARY KEY, title TEXT NOT NULL, added_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users(
 user_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT,
 balance INTEGER NOT NULL DEFAULT 100, bio TEXT DEFAULT '',
 created_at TEXT NOT NULL, last_daily TEXT
);
CREATE TABLE IF NOT EXISTS chat_settings(
 chat_id INTEGER PRIMARY KEY, welcome TEXT DEFAULT '', rules TEXT DEFAULT '',
 warn_limit INTEGER NOT NULL DEFAULT 3, welcome_enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS warnings(
 id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL,
 user_id INTEGER NOT NULL, moderator_id INTEGER NOT NULL,
 reason TEXT DEFAULT '', created_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS moderation_log(
 id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL,
 user_id INTEGER NOT NULL, moderator_id INTEGER NOT NULL,
 action TEXT NOT NULL, reason TEXT DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS transfers(
 id INTEGER PRIMARY KEY AUTOINCREMENT, sender_id INTEGER NOT NULL,
 receiver_id INTEGER NOT NULL, amount INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS clans(
 id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL,
 owner_id INTEGER NOT NULL, name TEXT NOT NULL, description TEXT DEFAULT '',
 UNIQUE(chat_id, name)
);
CREATE TABLE IF NOT EXISTS clan_members(
 chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, clan_id INTEGER NOT NULL,
 PRIMARY KEY(chat_id, user_id)
);
CREATE TABLE IF NOT EXISTS marriages(
 id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL,
 user1 INTEGER NOT NULL, user2 INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reputation(
 chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, score INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(chat_id,user_id)
);
CREATE TABLE IF NOT EXISTS notes(
 chat_id INTEGER NOT NULL, name TEXT NOT NULL, body TEXT NOT NULL,
 author_id INTEGER NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(chat_id,name)
);
CREATE TABLE IF NOT EXISTS message_stats(
 chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, messages INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(chat_id,user_id)
);
CREATE TABLE IF NOT EXISTS message_authors(
 chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
 PRIMARY KEY(chat_id,message_id)
);
CREATE TABLE IF NOT EXISTS bookmarks(
 id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
 body TEXT NOT NULL, created_at TEXT NOT NULL
);

"""

def now():
    return datetime.now(timezone.utc).isoformat()

def safe(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def mention(user):
    return f'<a href="tg://user?id={user.id}">{safe(user.full_name or "Пользователь")}</a>'

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        for statement in SCHEMA.split(";"):
            if statement.strip():
                await db.execute(statement)
        await db.commit()

async def ensure_user(user):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        INSERT INTO users(user_id, username, full_name, balance, created_at)
        VALUES(?,?,?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, full_name=excluded.full_name
        """, (user.id, user.username, user.full_name, START_BALANCE, now()))
        await db.commit()

async def balance_of(uid):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
        row = await cur.fetchone()
        return row[0] if row else START_BALANCE

async def ensure_chat(chat_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO chat_settings(chat_id) VALUES(?)", (chat_id,))
        await db.commit()

async def setting(chat_id, key, default=""):
    await ensure_chat(chat_id)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(f"SELECT {key} FROM chat_settings WHERE chat_id=?", (chat_id,))
        row = await cur.fetchone()
        return row[0] if row else default

async def admin(message):
    if message.chat.type == "private" or not message.from_user:
        return False
    try:
        member = await message.bot.get_chat_member(message.chat.id, message.from_user.id)
        return member.status in ("creator", "administrator")
    except TelegramAPIError:
        return False

async def target_from_message(message):
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user
    parts = (message.text or "").split()
    if len(parts) > 1 and parts[1].isdigit():
        try:
            return await message.bot.get_chat_member(message.chat.id, int(parts[1]))
        except TelegramAPIError:
            return None
    return None

async def log_action(chat_id, uid, mid, action, reason=""):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO moderation_log(chat_id,user_id,moderator_id,action,reason,created_at) VALUES(?,?,?,?,?,?)",
                         (chat_id, uid, mid, action, reason, now()))
        await db.commit()

async def author_from_cache(chat_id, message_id):
    if not message_id:
        return None
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT u.user_id, u.username, u.full_name FROM message_authors a "
            "JOIN users u ON u.user_id=a.user_id WHERE a.chat_id=? AND a.message_id=?",
            (chat_id, message_id))
        row = await cur.fetchone()
    if row:
        return SimpleNamespace(id=int(row[0]), username=row[1], full_name=row[2] or f"ID {row[0]}", is_bot=False)
    return None

async def remember_author(message):
    user = getattr(message, "from_user", None)
    if user is None or getattr(user, "is_bot", False):
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO message_authors(chat_id,message_id,user_id) VALUES(?,?,?)",
                         (message.chat.id, message.message_id, user.id))
        await db.commit()

async def resolve_target(message):
    """Resolve a target consistently: replied user, @username, or numeric Telegram ID."""
    replied = getattr(message, "reply_to_message", None)
    if replied is not None:
        # Normal reply is always the most reliable way to identify a group member.
        replied_user = getattr(replied, "from_user", None)
        if replied_user is not None and not getattr(replied_user, "is_bot", False):
            return replied_user
        # Некоторые Bot API-серверы не отдают from_user внутри reply_to_message.
        # Тогда берём автора из кэша, который заполняется при каждом входящем сообщении.
        cached = await author_from_cache(message.chat.id, getattr(replied, "message_id", None))
        if cached is not None and not getattr(cached, "is_bot", False):
            return cached
        # Anonymous admin/channel posts do not expose a personal Telegram user ID.
        if getattr(replied, "sender_chat", None) is not None:
            log.warning("Target reply is an anonymous admin/channel post; no personal user ID available")
        else:
            log.warning("Could not resolve reply author. reply_to_message=%s", replied.model_dump_json(exclude_none=True) if hasattr(replied, "model_dump_json") else replied)

    text = (getattr(message, "text", None) or "").strip()
    # Drop the command and optional @BotName suffix, keep remaining arguments.
    pieces = text.split(maxsplit=1)
    if len(pieces) < 2:
        return None
    token = pieces[1].strip().split(maxsplit=1)[0].strip(" ,;.!?()[]{}")

    if token.startswith("@"):
        username = token[1:].casefold()
        if not username:
            return None
        # First use the local cache populated from messages and group events.
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                "SELECT user_id, username, full_name FROM users WHERE lower(username)=? LIMIT 1",
                (username,),
            )
            row = await cur.fetchone()
        if row:
            uid, uname, full_name = row
            if int(uid) != int(message.from_user.id):
                return SimpleNamespace(id=int(uid), username=uname, full_name=full_name or uname, is_bot=False)
        return None

    if token.isdigit():
        uid = int(token)
        if uid == int(message.from_user.id):
            return message.from_user
        # get_chat_member returns a ChatMember wrapper, so return its .user.
        try:
            member = await message.bot.get_chat_member(message.chat.id, uid)
            user = getattr(member, "user", None)
            if user is not None and not getattr(user, "is_bot", False):
                return user
        except TelegramAPIError as exc:
            log.info("Could not resolve target ID %s via get_chat_member: %s", uid, exc)
        # Fall back to the known-user cache when Telegram lookup is unavailable.
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                "SELECT user_id, username, full_name FROM users WHERE user_id=? LIMIT 1", (uid,)
            )
            row = await cur.fetchone()
        if row:
            return SimpleNamespace(id=int(row[0]), username=row[1], full_name=row[2] or f"ID {uid}", is_bot=False)
    return None

def command_tail(message, target=None):
    """Arguments after command and optional explicit target, for reason/duration parsing."""
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 2:
        return ""
    if target is not None and not (message.reply_to_message and message.reply_to_message.from_user):
        return parts[2] if len(parts) > 2 else ""
    return (message.text or "").partition(" ")[2].strip()

def start_menu(bot_username=""):
    kb = InlineKeyboardBuilder()
    if bot_username:
        kb.button(text="➕ Добавить в группу", url=f"https://t.me/{bot_username}?startgroup=true")
    kb.button(text="👥 Мои группы", callback_data="my_groups")
    kb.button(text="📚 Команды", callback_data="show_commands")
    kb.adjust(1)
    return kb.as_markup()

@dp.message.outer_middleware()
async def remember_chat_users(handler, event, data):
    """Remember users from all incoming group messages, not only /start."""
    user = getattr(event, "from_user", None)
    if user is not None and not getattr(user, "is_bot", False):
        try:
            await ensure_user(user)
            await remember_author(event)
        except Exception:
            log.exception("Could not remember user %s", user.id)
    replied = getattr(event, "reply_to_message", None)
    ru = getattr(replied, "from_user", None) if replied else None
    if ru is not None and not getattr(ru, "is_bot", False):
        try:
            await ensure_user(ru)
            await remember_author(replied)
        except Exception:
            log.exception("Could not remember replied user")
    return await handler(event, data)


@dp.message(CommandStart())
async def start(m: Message):
    await ensure_user(m.from_user)
    me = await m.bot.get_me()
    text = ("👋 <b>Привет! Я DropGram Iris — менеджер чатов.</b>\n\n"
            "Помогаю управлять группой: модерация, предупреждения, настройки, развлечения, репутация и экономика.\n\n"
            "➕ Добавь меня в группу, выдай нужные права администратора, а затем используй /help.\n"
            "Выбери действие ниже:")
    await m.answer(text, parse_mode="HTML", reply_markup=start_menu(me.username or ""))

@dp.my_chat_member()
async def track_group(event: ChatMemberUpdated):
    chat = event.chat
    if chat.type not in ("group", "supergroup"):
        return
    new_status = event.new_chat_member.status
    async with aiosqlite.connect(DB_PATH) as db:
        if new_status in ("member", "administrator"):
            await db.execute("INSERT INTO known_groups(chat_id,title,added_at) VALUES(?,?,?) ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title", (chat.id, chat.title or "Группа", now()))
        elif new_status in ("left", "kicked"):
            await db.execute("DELETE FROM known_groups WHERE chat_id=?", (chat.id,))
        await db.commit()

@dp.callback_query(F.data == "my_groups")
async def my_groups(c: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT title,chat_id FROM known_groups ORDER BY title LIMIT 50")
        rows = await cur.fetchall()
    if not rows:
        text = "Пока не вижу групп, где я состою. Добавь меня через кнопку «Добавить в группу» и проверь, что я не удалён из неё."
    else:
        text = "👥 <b>Группы, где состоит бот:</b>\n" + "\n".join(f"• {safe(title)} — <code>{gid}</code>" for title,gid in rows)
    await c.message.answer(text, parse_mode="HTML")
    await c.answer()

@dp.callback_query(F.data == "show_commands")
async def show_commands(c: CallbackQuery):
    await c.message.answer("Открой список команд: /help")
    await c.answer()

COMMAND_SECTIONS = {
    "main": ("📌 Основные", "<b>Основные команды</b>\n/start — запуск бота\n/help или /команды — меню команд\n/id — ID пользователя и чата\n/profile — профиль\n/balance — баланс\n/bio ТЕКСТ — описание профиля\n/daily — ежедневный бонус\n/pay СУММА (ответом) — перевести монеты"),
    "moderation": ("🛡 Модерация", "<b>Модерация</b> (в большинстве случаев ответом на сообщение)\n/warn [причина] — предупреждение\n/warnings — активные предупреждения\n/unwarn — снять последнее предупреждение\n/mute 30m причина — мут на 30 минут\n/mute 1h причина — мут на 1 час\n/mute 2d причина — мут на 2 дня\n/unmute — снять мут\n/ban [срок] [причина] — бан навсегда или на срок\nПример: /ban 1d спам\n/unban USER_ID — разбанить по ID\n/softban [причина] — удалить и сразу снять бан\n/kick — исключить без постоянного бана\n/purge ЧИСЛО (ответом) — удалить сообщения\n/modlog — журнал действий\n/setrules ТЕКСТ — установить правила\n/warnlimit ЧИСЛО — лимит предупреждений"),
    "fun": ("🎭 Развлечения", "<b>Развлечения</b> (ответом на сообщение или через @username)\n/обнять — обнять\n/пнуть или /шлепнуть — шуточное мультяшное действие\n/датьпять — дать пять\n/погладить — дружеское действие\n/поддержать — поддержать\n/пожатьруку — рукопожатие\n/танцевать — танец\n/флирт — лёгкий подкат\n/шип — совместимость\n/выбери ВАРИАНТ1 | ВАРИАНТ2 — выбор\n/рандом — случайное число\n/кубик — кубик\n/анекдот — шутка\n/комплимент — комплимент"),
    "social": ("💞 Отношения", "<b>Отношения и сообщество</b>\n/брак — предложить брак (ответом на сообщение)\nКнопки «Согласиться» и «Отказаться» — ответ на предложение\n/мой_брак — посмотреть свой статус\n/развод — развестись\n/браки — список браков\n/rep — репутация (ответом)\n/реп_плюс — повысить репутацию\n/реп_минус — понизить репутацию\n/клансоздать НАЗВАНИЕ — создать клан\n/кланы — список кланов\n/кланвступить ID — вступить\n/кланвыйти — выйти\n/мойклан — мой клан"),
    "tools": ("🧰 Инструменты", "<b>Инструменты чата</b>\n/rules — правила чата\n/setwelcome ТЕКСТ — задать приветствие\n/welcomeon и /welcomeoff — приветствие вкл./выкл.\n/note НАЗВАНИЕ | ТЕКСТ — сохранить заметку (админ)\n/заметки — список заметок\n/заметкапоказать НАЗВАНИЕ — показать заметку\n/заметкаудалить НАЗВАНИЕ — удалить заметку (админ)\n/stats — статистика чата\n/top — топ активности\n/bookmark (ответом) — сохранить сообщение\n/bookmarks — мои закладки\n/bookmarkdel НОМЕР — удалить закладку\n/timer СЕКУНДЫ ТЕКСТ — напоминание")
}

def command_menu():
    kb = InlineKeyboardBuilder()
    for key in COMMAND_SECTIONS:
        kb.button(text=COMMAND_SECTIONS[key][0], callback_data=f"cmds:{key}")
    kb.adjust(2)
    return kb.as_markup()

@dp.message(Command("help", "commands", "команды", "списоккоманд"))
async def help_cmd(m: Message):
    await m.answer(
        "<b>📚 DropGram Iris — список команд</b>\nВыбери раздел ниже. Команды модерации доступны только администраторам группы, если не указано иное.",
        reply_markup=command_menu(),
        parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("cmds:"))
async def command_section(c: CallbackQuery):
    key = c.data.split(":", 1)[1]
    section = COMMAND_SECTIONS.get(key)
    if not section:
        await c.answer("Раздел не найден.", show_alert=True)
        return
    kb = InlineKeyboardBuilder()
    for k, (label, _) in COMMAND_SECTIONS.items():
        kb.button(text=label, callback_data=f"cmds:{k}")
    kb.adjust(2)
    await c.message.edit_text(section[1], reply_markup=kb.as_markup(), parse_mode="HTML")
    await c.answer()

@dp.message(Command("debug"))
async def debug_cmd(m):
    r = m.reply_to_message
    if not r:
        return await m.answer("Ответь командой /debug на нужное сообщение.")
    raw = r.model_dump_json(exclude_none=True, indent=1) if hasattr(r, "model_dump_json") else str(r)
    await m.answer(f"<pre>{safe(raw[:3500])}</pre>", parse_mode="HTML")

@dp.message(Command("id"))
async def id_cmd(m):
    target = await resolve_target(m) or m.from_user
    await m.answer(f"👤 ID: <code>{target.id}</code>\n💬 Chat ID: <code>{m.chat.id}</code>", parse_mode="HTML")

@dp.message(Command("profile", "профиль"))
async def profile(m):
    u = await resolve_target(m) or m.from_user
    await ensure_user(u)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT balance,bio,created_at FROM users WHERE user_id=?", (u.id,))
        bal, bio, created = await cur.fetchone()
        cur = await db.execute("SELECT COUNT(*) FROM warnings WHERE user_id=? AND chat_id=? AND active=1", (u.id,m.chat.id))
        warns = (await cur.fetchone())[0]
    await m.answer(f"👤 <b>Профиль</b>\nИмя: {mention(u)}\nID: <code>{u.id}</code>\n"
                   f"💰 Монеты: {bal}\n⚠️ Активные предупреждения в этом чате: {warns}\n"
                   f"📝 {safe(bio) or 'Описание не задано'}", parse_mode="HTML")

@dp.message(Command("balance", "баланс"))
async def balance_cmd(m):
    await ensure_user(m.from_user)
    await m.answer(f"💰 Баланс: <b>{await balance_of(m.from_user.id)}</b> монет", parse_mode="HTML")

@dp.message(Command("bio"))
async def bio_cmd(m):
    text = (m.text or "").partition(" ")[2].strip()
    if not text:
        return await m.answer("Использование: /bio текст описания")
    await ensure_user(m.from_user)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET bio=? WHERE user_id=?", (text[:300], m.from_user.id))
        await db.commit()
    await m.answer("✅ Описание профиля сохранено.")

@dp.message(Command("daily"))
async def daily(m):
    await ensure_user(m.from_user)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute("SELECT balance,last_daily FROM users WHERE user_id=?", (m.from_user.id,))
        bal, last = await cur.fetchone()
        today = datetime.now(timezone.utc).date().isoformat()
        if last == today:
            await db.rollback()
            return await m.answer("🎁 Ежедневный бонус уже получен. Попробуй завтра.")
        amount = 50
        await db.execute("UPDATE users SET balance=balance+?,last_daily=? WHERE user_id=?", (amount,today,m.from_user.id))
        await db.commit()
    await m.answer(f"🎁 Ты получил {amount} монет! Баланс: {bal+amount}.")

@dp.message(Command("pay"))
async def pay(m):
    receiver = await resolve_target(m) if m.reply_to_message else None
    if not receiver:
        return await m.answer("Ответь на сообщение пользователя: /pay 25")
    parts = (m.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        return await m.answer("Формат: /pay 25 ответом на сообщение")
    amount = int(parts[1]); sender=m.from_user
    if amount < 1 or amount > 1000000 or sender.id == receiver.id or receiver.is_bot:
        return await m.answer("Проверь сумму и получателя.")
    await ensure_user(sender); await ensure_user(receiver)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute("SELECT balance FROM users WHERE user_id=?", (sender.id,))
        bal = (await cur.fetchone())[0]
        if bal < amount:
            await db.rollback()
            return await m.answer("Недостаточно монет.")
        await db.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (amount,sender.id))
        await db.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (amount,receiver.id))
        await db.execute("INSERT INTO transfers(sender_id,receiver_id,amount,created_at) VALUES(?,?,?,?)", (sender.id,receiver.id,amount,now()))
        await db.commit()
    await m.answer(f"✅ Переведено {amount} монет пользователю {mention(receiver)}.", parse_mode="HTML")

@dp.message(Command("rules"))
async def rules(m):
    text = await setting(m.chat.id, "rules")
    await m.answer(text or "Правила ещё не заданы.")

@dp.message(Command("setrules"))
async def setrules(m):
    if not await admin(m): return await m.answer("Только администратор группы может менять правила.")
    value=(m.text or "").partition(" ")[2].strip()
    if not value: return await m.answer("Использование: /setrules текст")
    await ensure_chat(m.chat.id)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE chat_settings SET rules=? WHERE chat_id=?", (value,m.chat.id)); await db.commit()
    await m.answer("✅ Правила сохранены.")

@dp.message(Command("setwelcome"))
async def setwelcome(m):
    if not await admin(m): return await m.answer("Только администратор группы может менять приветствие.")
    value=(m.text or "").partition(" ")[2].strip()
    if not value: return await m.answer("Использование: /setwelcome Добро пожаловать, {name}!")
    await ensure_chat(m.chat.id)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE chat_settings SET welcome=? WHERE chat_id=?", (value,m.chat.id)); await db.commit()
    await m.answer("✅ Приветствие сохранено. Переменная {name} подставляет имя.")

async def toggle_welcome(m, enabled):
    if not await admin(m): return await m.answer("Только администратор группы.")
    await ensure_chat(m.chat.id)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE chat_settings SET welcome_enabled=? WHERE chat_id=?", (int(enabled),m.chat.id)); await db.commit()
    await m.answer("Приветствия включены." if enabled else "Приветствия выключены.")

@dp.message(Command("welcomeon"))
async def welcomeon(m): await toggle_welcome(m, True)
@dp.message(Command("welcomeoff"))
async def welcomeoff(m): await toggle_welcome(m, False)

@dp.message(F.new_chat_members)
async def greet(m):
    if not await setting(m.chat.id, "welcome_enabled", 1): return
    template = await setting(m.chat.id, "welcome")
    for u in m.new_chat_members:
        await ensure_user(u)
        if u.id == m.bot.id: continue
        text = template or "Добро пожаловать, {name}! 👋"
        await m.answer(text.replace("{name}", safe(u.full_name)).replace("{id}", str(u.id)))

@dp.message(Command("warnlimit"))
async def warnlimit(m):
    if not await admin(m): return await m.answer("Только администратор группы.")
    parts=(m.text or "").split()
    if len(parts)!=2 or not parts[1].isdigit() or not 1<=int(parts[1])<=20:
        return await m.answer("Использование: /warnlimit 3 (от 1 до 20)")
    await ensure_chat(m.chat.id)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE chat_settings SET warn_limit=? WHERE chat_id=?", (int(parts[1]),m.chat.id)); await db.commit()
    await m.answer(f"Лимит предупреждений установлен: {parts[1]}.")

@dp.message(Command("warn"))
async def warn(m):
    if not await admin(m): return await m.answer("Только администратор группы.")
    t=await resolve_target(m)
    if not t: return await m.answer("Ответь на сообщение пользователя: /warn причина")
    if t.id==m.from_user.id or t.id==m.bot.id: return await m.answer("Нельзя выдать предупреждение этому пользователю.")
    reason=command_tail(m, t) or "не указана"
    await ensure_user(t)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO warnings(chat_id,user_id,moderator_id,reason,created_at) VALUES(?,?,?,?,?)",(m.chat.id,t.id,m.from_user.id,reason,now()))
        await db.commit()
        cur=await db.execute("SELECT COUNT(*) FROM warnings WHERE chat_id=? AND user_id=? AND active=1",(m.chat.id,t.id)); count=(await cur.fetchone())[0]
    await log_action(m.chat.id,t.id,m.from_user.id,"warn",reason)
    limit=await setting(m.chat.id,"warn_limit",3)
    await m.answer(f"⚠️ {mention(t)}: предупреждение {count}/{limit}. Причина: {safe(reason)}",parse_mode="HTML")
    if count>=limit:
        try:
            await m.bot.ban_chat_member(m.chat.id,t.id)
            await log_action(m.chat.id,t.id,m.from_user.id,"auto-ban","лимит предупреждений")
            await m.answer("🚫 Достигнут лимит предупреждений: пользователь заблокирован.")
        except TelegramAPIError as e:
            await m.answer(f"Лимит достигнут, но API не выполнил бан: {safe(str(e))}")

@dp.message(Command("warnings", "варны"))
async def warnings(m):
    t=await resolve_target(m) or m.from_user
    async with aiosqlite.connect(DB_PATH) as db:
        cur=await db.execute("SELECT id,reason,created_at FROM warnings WHERE chat_id=? AND user_id=? AND active=1 ORDER BY id DESC LIMIT 15",(m.chat.id,t.id))
        rows=await cur.fetchall()
    if not rows: return await m.answer("Активных предупреждений нет.")
    text="\n".join(f"#{r[0]} — {safe(r[1])} ({r[2][:10]})" for r in rows)
    await m.answer(f"⚠️ Предупреждения пользователя {mention(t)}:\n{text}",parse_mode="HTML")

@dp.message(Command("unwarn"))
async def unwarn(m):
    if not await admin(m): return await m.answer("Только администратор группы.")
    t=await resolve_target(m)
    if not t: return await m.answer("Ответь на сообщение пользователя: /unwarn")
    async with aiosqlite.connect(DB_PATH) as db:
        cur=await db.execute("SELECT id FROM warnings WHERE chat_id=? AND user_id=? AND active=1 ORDER BY id DESC LIMIT 1",(m.chat.id,t.id))
        row=await cur.fetchone()
        if row: await db.execute("UPDATE warnings SET active=0 WHERE id=?",(row[0],))
        await db.commit()
    if not row: return await m.answer("У пользователя нет активных предупреждений.")
    await log_action(m.chat.id,t.id,m.from_user.id,"unwarn")
    await m.answer(f"✅ Последнее предупреждение снято с {mention(t)}.",parse_mode="HTML")

def parse_duration(raw):
    """Parse durations like 30m, 30 мин, 1h, 2д, 1 день."""
    value = (raw or "").strip().lower().replace(",", ".")
    match = re.fullmatch(r"(\d+)\s*(m|min|mins|мин|м|h|hr|hour|hours|ч|час|часа|часов|d|day|days|д|день|дня|дней|w|week|weeks|нед|неделя|недели|недель)", value)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2)
    if amount < 1:
        return None
    if unit in {"m", "min", "mins", "мин", "м"}:
        return amount * 60
    if unit in {"h", "hr", "hour", "hours", "ч", "час", "часа", "часов"}:
        return amount * 3600
    if unit in {"d", "day", "days", "д", "день", "дня", "дней"}:
        return amount * 86400
    if unit in {"w", "week", "weeks", "нед", "неделя", "недели", "недель"}:
        return amount * 604800
    return None

def split_duration_reason(text):
    """Return (seconds_or_none, reason) for '30m spam', '30 мин спам', '1 час флуд'."""
    raw = (text or "").strip()
    unit = r"(?:m|min|mins|мин|м|minute|minutes|минута|минуты|минут|h|hr|hour|hours|ч|час|часа|часов|d|day|days|д|день|дня|дней|w|week|weeks|нед|неделя|недели|недель)"
    match = re.match(rf"^(\d+\s*{unit})(?:\s+(.*))?$", raw, flags=re.IGNORECASE)
    if match:
        return parse_duration(match.group(1)), (match.group(2) or "не указана")
    return None, raw or "не указана"

def pretty_duration(seconds):
    if seconds % 604800 == 0: return f"{seconds // 604800} нед."
    if seconds % 86400 == 0: return f"{seconds // 86400} дн."
    if seconds % 3600 == 0: return f"{seconds // 3600} ч."
    return f"{seconds // 60} мин."

@dp.message(Command("mute", "мут"))
async def mute(m):
    if not await admin(m):
        return await m.answer("Только администратор группы может выдавать мут.")
    t = await resolve_target(m)
    if not t:
        return await m.answer("Ответь на сообщение пользователя: /mute 30m причина\nПримеры: /mute 30m спам, /mute 1h флуд, /mute 2d оскорбления")
    raw = command_tail(m, t)
    parsed, reason = split_duration_reason(raw)
    seconds = min(parsed, 366 * 86400) if parsed is not None else 3600
    if parsed is None and raw:
        reason = raw
    until = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    try:
        await m.bot.restrict_chat_member(
            m.chat.id, t.id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=until
        )
        await log_action(m.chat.id, t.id, m.from_user.id, "mute", reason)
        await m.answer(f"🔇 {mention(t)} получил мут на {pretty_duration(seconds)}.\nПричина: {safe(reason)}", parse_mode="HTML")
    except TelegramAPIError as e:
        await m.answer(f"Не удалось выдать мут. Проверь права бота и поддержку метода API.\n{safe(str(e))}")

@dp.message(Command("unmute", "размут"))
async def unmute(m):
    if not await admin(m): return await m.answer("Только администратор группы.")
    t=await resolve_target(m)
    if not t: return await m.answer("Ответь на сообщение: /unmute")
    try:
        await m.bot.restrict_chat_member(m.chat.id,t.id,permissions=ChatPermissions(can_send_messages=True,can_send_audios=True,can_send_documents=True,can_send_photos=True,can_send_videos=True,can_send_video_notes=True,can_send_voice_notes=True,can_send_polls=True,can_send_other_messages=True,can_add_web_page_previews=True))
        await log_action(m.chat.id,t.id,m.from_user.id,"unmute")
        await m.answer(f"🔊 Ограничение снято с {mention(t)}.",parse_mode="HTML")
    except TelegramAPIError as e: await m.answer(f"API не выполнил размут: {safe(str(e))}")

@dp.message(Command("ban", "бан"))
async def ban(m):
    if not await admin(m):
        return await m.answer("Только администратор группы может выдавать бан.")
    t = await resolve_target(m)
    if not t:
        return await m.answer("Ответь на сообщение пользователя:\n/ban причина — навсегда\n/ban 1d причина — на день\n/ban 1h причина — на час")
    raw = command_tail(m, t)
    parsed, reason = split_duration_reason(raw)
    duration = min(parsed, 366 * 86400) if parsed is not None else None
    if parsed is None and raw:
        reason = raw
    try:
        kwargs = {}
        if duration is not None:
            kwargs["until_date"] = datetime.now(timezone.utc) + timedelta(seconds=duration)
        await m.bot.ban_chat_member(m.chat.id, t.id, **kwargs)
        await log_action(m.chat.id, t.id, m.from_user.id, "tempban" if duration else "ban", reason)
        term = f"на {pretty_duration(duration)}" if duration else "навсегда"
        await m.answer(f"🚫 {mention(t)} заблокирован {term}.\nПричина: {safe(reason)}", parse_mode="HTML")
    except TelegramAPIError as e:
        await m.answer(f"Не удалось выдать бан. Проверь права бота и поддержку метода API.\n{safe(str(e))}")

@dp.message(Command("softban"))
async def softban(m):
    if not await admin(m):
        return await m.answer("Только администратор группы.")
    t = await resolve_target(m)
    if not t:
        return await m.answer("Ответь на сообщение: /softban причина")
    reason = command_tail(m, t) or "не указана"
    try:
        await m.bot.ban_chat_member(m.chat.id, t.id)
        await m.bot.unban_chat_member(m.chat.id, t.id, only_if_banned=True)
        await log_action(m.chat.id, t.id, m.from_user.id, "softban", reason)
        await m.answer(f"🧹 {mention(t)} исключён; бан сразу снят. Причина: {safe(reason)}", parse_mode="HTML")
    except TelegramAPIError as e:
        await m.answer(f"Не удалось выполнить softban: {safe(str(e))}")

@dp.message(Command("unban", "разбан"))
async def unban(m):
    if not await admin(m): return await m.answer("Только администратор группы.")
    parts=(m.text or "").split()
    if len(parts)!=2 or not parts[1].isdigit(): return await m.answer("Использование: /unban USER_ID")
    try:
        await m.bot.unban_chat_member(m.chat.id,int(parts[1]),only_if_banned=True)
        await log_action(m.chat.id,int(parts[1]),m.from_user.id,"unban")
        await m.answer("✅ Пользователь разблокирован.")
    except TelegramAPIError as e: await m.answer(f"API не выполнил разбан: {safe(str(e))}")

@dp.message(Command("kick"))
async def kick(m):
    if not await admin(m): return await m.answer("Только администратор группы.")
    t=await resolve_target(m)
    if not t: return await m.answer("Ответь на сообщение: /kick")
    try:
        await m.bot.ban_chat_member(m.chat.id,t.id)
        await m.bot.unban_chat_member(m.chat.id,t.id,only_if_banned=True)
        await log_action(m.chat.id,t.id,m.from_user.id,"kick")
        await m.answer(f"👢 {mention(t)} исключён из группы.",parse_mode="HTML")
    except TelegramAPIError as e: await m.answer(f"API не выполнил кик: {safe(str(e))}")

@dp.message(Command("purge"))
async def purge(m):
    if not await admin(m): return await m.answer("Только администратор группы.")
    parts=(m.text or "").split()
    if len(parts)!=2 or not parts[1].isdigit(): return await m.answer("Использование: /purge 10 (ответом на сообщение)")
    if not m.reply_to_message: return await m.answer("Ответь на первое сообщение для удаления: /purge 10")
    count=min(int(parts[1]),100)
    deleted=0
    for mid in range(m.reply_to_message.message_id, m.reply_to_message.message_id+count):
        try: await m.bot.delete_message(m.chat.id,mid); deleted+=1
        except TelegramAPIError: pass
    try: await m.delete()
    except TelegramAPIError: pass
    await m.answer(f"🧹 Удалено сообщений: {deleted}")

@dp.message(Command("modlog"))
async def modlog(m):
    if not await admin(m): return await m.answer("Только администратор группы.")
    async with aiosqlite.connect(DB_PATH) as db:
        cur=await db.execute("SELECT user_id,moderator_id,action,reason,created_at FROM moderation_log WHERE chat_id=? ORDER BY id DESC LIMIT 15",(m.chat.id,))
        rows=await cur.fetchall()
    if not rows: return await m.answer("Журнал пока пуст.")
    out="\n".join(f"{r[2]} | user {r[0]} | mod {r[1]} | {safe(r[3])} | {r[4][:16]}" for r in rows)
    await m.answer("<b>Журнал модерации</b>\n"+out,parse_mode="HTML")

@dp.message(Command("клансоздать"))
async def clancreate(m):
    name=(m.text or "").partition(" ")[2].strip()
    if m.chat.type=="private": return await m.answer("Кланы создаются в группе.")
    if not name or len(name)>40: return await m.answer("Использование: /клансоздать Название (до 40 символов)")
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT INTO clans(chat_id,owner_id,name) VALUES(?,?,?)",(m.chat.id,m.from_user.id,name))
            await db.commit()
            cur=await db.execute("SELECT last_insert_rowid()")
            clan_id=(await cur.fetchone())[0]
            await db.execute("INSERT OR REPLACE INTO clan_members(chat_id,user_id,clan_id) VALUES(?,?,?)",(m.chat.id,m.from_user.id,clan_id))
            await db.commit()
        await m.answer(f"🏰 Клан «{safe(name)}» создан. ID клана: {clan_id}. Вступить: /кланвступить {clan_id}",parse_mode="HTML")
    except aiosqlite.IntegrityError:
        await m.answer("Клан с таким названием уже существует в этой группе.")

@dp.message(Command("кланы"))
async def clans(m):
    async with aiosqlite.connect(DB_PATH) as db:
        cur=await db.execute("SELECT id,name,owner_id FROM clans WHERE chat_id=? ORDER BY id DESC LIMIT 30",(m.chat.id,))
        rows=await cur.fetchall()
    await m.answer("🏰 <b>Кланы</b>\n"+("\n".join(f"#{r[0]} — {safe(r[1])} (владелец ID {r[2]})" for r in rows) if rows else "Пока нет кланов."),parse_mode="HTML")

@dp.message(Command("кланвступить"))
async def clanjoin(m):
    parts=(m.text or "").split()
    if len(parts)!=2 or not parts[1].isdigit(): return await m.answer("Использование: /кланвступить ID")
    async with aiosqlite.connect(DB_PATH) as db:
        cur=await db.execute("SELECT id FROM clans WHERE id=? AND chat_id=?",(int(parts[1]),m.chat.id))
        row=await cur.fetchone()
        if not row: return await m.answer("Клан не найден в этой группе.")
        await db.execute("INSERT OR REPLACE INTO clan_members(chat_id,user_id,clan_id) VALUES(?,?,?)",(m.chat.id,m.from_user.id,row[0]))
        await db.commit()
    await m.answer("✅ Ты вступил в клан.")

@dp.message(Command("кланвыйти"))
async def clanleave(m):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM clan_members WHERE chat_id=? AND user_id=?",(m.chat.id,m.from_user.id)); await db.commit()
    await m.answer("Ты вышел из клана.")

@dp.message(Command("мойклан"))
async def myclan(m):
    async with aiosqlite.connect(DB_PATH) as db:
        cur=await db.execute("SELECT c.id,c.name,c.owner_id,c.description FROM clans c JOIN clan_members cm ON cm.clan_id=c.id WHERE cm.chat_id=? AND cm.user_id=?",(m.chat.id,m.from_user.id))
        row=await cur.fetchone()
    if not row: return await m.answer("Ты пока не состоишь в клане.")
    await m.answer(f"🏰 <b>{safe(row[1])}</b>\nID: {row[0]}\nВладелец ID: {row[2]}\n{safe(row[3])}",parse_mode="HTML")



# ── Marriage / social module ────────────────────────────────────────────────
async def get_active_marriage(chat_id, uid):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id,user1,user2,created_at FROM marriages WHERE chat_id=? AND status='active' AND (user1=? OR user2=?) ORDER BY id DESC LIMIT 1",
            (chat_id,uid,uid))
        return await cur.fetchone()

@dp.message(Command("marry", "брак", "жениться"))
async def marry(m):
    if m.chat.type == "private":
        return await m.answer("Предложение брака работает в группе.")
    parts = (m.text or "").split(maxsplit=1)
    # Приоритет — пользователь, на сообщение которого ответили. Можно написать просто /брак.
    if len(parts) > 1 and parts[1].strip().casefold() in ("да", "yes", "принять"):
        return await m.answer("Нажми кнопку «Согласиться» в сообщении с предложением брака.")
    if len(parts) > 1 and parts[1].strip().casefold() in ("нет", "no", "отклонить"):
        return await m.answer("Нажми кнопку «Отказаться» в сообщении с предложением брака.")
    b = await resolve_target(m)
    if not b:
        return await m.answer("Не удалось определить участника. Ответь на обычное сообщение человека командой /брак, либо укажи /брак @username или /брак ID. Нельзя определить автора анонимного сообщения администратора или публикации канала.")
    a = m.from_user
    if a.id == b.id or getattr(b, "is_bot", False):
        return await m.answer("Выбери другого участника группы.")
    if await get_active_marriage(m.chat.id, a.id) or await get_active_marriage(m.chat.id, b.id):
        return await m.answer("Один из вас уже состоит в браке.")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id FROM marriages WHERE chat_id=? AND user1=? AND user2=? AND status='pending'", (m.chat.id, a.id, b.id))
        existing = await cur.fetchone()
        if existing:
            return await m.answer("Предложение этому пользователю уже отправлено.")
        cur = await db.execute("INSERT INTO marriages(chat_id,user1,user2,status,created_at) VALUES(?,?,?,'pending',?)", (m.chat.id, a.id, b.id, now()))
        marriage_id = cur.lastrowid
        await db.commit()
    kb = InlineKeyboardBuilder()
    kb.button(text="💍 Согласиться", callback_data=f"marriage:yes:{marriage_id}")
    kb.button(text="❌ Отказаться", callback_data=f"marriage:no:{marriage_id}")
    kb.adjust(2)
    await m.answer(f"💍 {mention(a)} предлагает брак {mention(b)}!\n{mention(b)}, выбери действие кнопкой ниже.", parse_mode="HTML", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("marriage:"))
async def marriage_decision(c: CallbackQuery):
    try:
        _, decision, raw_id = c.data.split(":", 2)
        marriage_id = int(raw_id)
    except (ValueError, AttributeError):
        return await c.answer("Некорректное предложение.", show_alert=True)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id,chat_id,user1,user2,status FROM marriages WHERE id=?", (marriage_id,))
        row = await cur.fetchone()
        if not row or row[1] != c.message.chat.id or row[4] != "pending":
            return await c.answer("Предложение уже недействительно.", show_alert=True)
        if c.from_user.id != row[3]:
            return await c.answer("Эти кнопки предназначены только получателю предложения.", show_alert=True)
        if decision == "yes":
            if await get_active_marriage(row[1], row[2]) or await get_active_marriage(row[1], row[3]):
                await db.execute("UPDATE marriages SET status='declined' WHERE id=?", (marriage_id,))
                await db.commit()
                await c.answer("Один из вас уже состоит в браке.", show_alert=True)
                return
            await db.execute("UPDATE marriages SET status='active' WHERE id=?", (marriage_id,))
            text = "💍 Поздравляем! Предложение принято — вы теперь в браке. Посмотреть статус: /мой_брак"
        elif decision == "no":
            await db.execute("UPDATE marriages SET status='declined' WHERE id=?", (marriage_id,))
            text = "❌ Предложение брака отклонено."
        else:
            return await c.answer("Неизвестное действие.", show_alert=True)
        await db.commit()
    try:
        await c.message.edit_reply_markup(reply_markup=None)
    except TelegramAPIError:
        pass
    await c.message.answer(text)
    await c.answer("Готово")

@dp.message(Command("marry_yes", "брак_да"))
async def marry_yes(m):
    await m.answer("Теперь принимай предложение кнопкой «💍 Согласиться» под сообщением с предложением брака.")

@dp.message(Command("marry_no", "брак_нет"))
async def marry_no(m):
    await m.answer("Теперь отклоняй предложение кнопкой «❌ Отказаться» под сообщением с предложением брака.")

@dp.message(Command("my_marriage", "мой_брак"))
async def my_marriage(m):
    row=await get_active_marriage(m.chat.id,m.from_user.id)
    if not row: return await m.answer("Ты не состоишь в браке в этом чате.")
    partner_id=row[2] if row[1]==m.from_user.id else row[1]
    await m.answer(f"💍 Твой брак активен.\nПартнёр: <code>{partner_id}</code>\nДата: {row[3][:10]}",parse_mode="HTML")

@dp.message(Command("divorce", "развод"))
async def divorce(m):
    row=await get_active_marriage(m.chat.id,m.from_user.id)
    if not row: return await m.answer("Ты не состоишь в браке в этом чате.")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE marriages SET status='divorced' WHERE id=?",(row[0],))
        await db.commit()
    await m.answer("💔 Брак расторгнут.")

@dp.message(Command("marriages", "браки"))
async def marriages(m):
    async with aiosqlite.connect(DB_PATH) as db:
        cur=await db.execute("SELECT user1,user2,created_at FROM marriages WHERE chat_id=? AND status='active' ORDER BY id DESC LIMIT 30",(m.chat.id,))
        rows=await cur.fetchall()
    if not rows: return await m.answer("В этом чате пока нет браков.")
    await m.answer("💍 <b>Браки чата</b>\n"+"\n".join(f"{i}. <code>{a}</code> ❤️ <code>{b}</code> — {dt[:10]}" for i,(a,b,dt) in enumerate(rows,1)),parse_mode="HTML")

# ── Safe entertainment commands (no betting / gambling) ─────────────────────
JOKES = [
    "Почему программист любит тёмную тему? Потому что свет притягивает баги. 🐛",
    "— Ты сделал бэкап? — Конечно! — Где? — В планах. 😅",
    "У компьютера плохое настроение: сегодня он опять завис на своих мыслях.",
    "— Как дела? — Как у Wi-Fi: вроде есть связь, но иногда пропадаю."
]
COMPLIMENTS = [
    "Ты умеешь поднимать настроение окружающим ✨",
    "У тебя отличный вкус и интересные идеи 🌟",
    "С тобой в чате явно веселее 😄",
    "Не забывай: даже маленький шаг — это прогресс 💪"
]

@dp.message(Command("анекдот"))
async def joke(m):
    await m.answer(random.choice(JOKES))

@dp.message(Command("комплимент"))
async def compliment(m):
    target=await resolve_target(m) or m.from_user
    await m.answer(f"🌟 {mention(target)}, {random.choice(COMPLIMENTS)}",parse_mode="HTML")

@dp.message(Command("выбери"))
async def choose(m):
    raw=(m.text or "").partition(" ")[2]
    options=[x.strip() for x in raw.split("|") if x.strip()]
    if len(options)<2: return await m.answer("Пример: /выбери пицца | суши | паста")
    await m.answer("🎯 Мой выбор: "+random.choice(options))

@dp.message(Command("рандом"))
async def random_number(m):
    parts=(m.text or "").split()
    try:
        lo,hi=(int(parts[1]),int(parts[2])) if len(parts)>=3 else (1,100)
        if lo>hi: lo,hi=hi,lo
        if hi-lo>10**9: return await m.answer("Слишком большой диапазон.")
        await m.answer(f"🎲 Случайное число: {random.randint(lo,hi)}")
    except ValueError:
        await m.answer("Формат: /рандом 1 100")

@dp.message(Command("кубик"))
async def roll(m):
    await m.answer(f"🎲 Выпало: {random.randint(1,6)}")

@dp.message(Command("шип"))
async def ship(m):
    b = await resolve_target(m)
    if b:
        a=m.from_user
        if a.id==b.id: return await m.answer("Можно пошипперить двух разных людей 🙂")
        pct=random.randint(0,100)
        await m.answer(f"💞 Совместимость {mention(a)} и {mention(b)}: {pct}% — это просто случайная игра!",parse_mode="HTML")
    else:
        await m.answer("Не удалось определить пользователя. Ответь на обычное сообщение участника командой /шип или используй /шип @username либо /шип ID. Для @username бот должен ранее видеть его сообщение.")


# Короткие мемные RP-команды. Все ответы шуточные и без подробного насилия.
RP_ACTIONS = {
    "hug": ("обнял(а)", "🫂"),
    "kick": ("легонько пнул(а) по-мультяшному", "🦶"),
    "highfive": ("дал(а) пять", "🙌"),
    "pat": ("дружески погладил(а) по голове", "🐾"),
}

async def rp_action(m, action_key):
    target = await resolve_target(m)
    if not target:
        return await m.answer("Не удалось определить пользователя. Ответь на обычное сообщение участника командой /обнять, либо укажи /обнять @username или /обнять ID. Для @username бот должен хотя бы раз увидеть сообщение этого человека; ID должен принадлежать участнику этой группы.")
    if target.id == m.from_user.id:
        return await m.answer("Самому себе так не получится 😄")
    verb, emoji = RP_ACTIONS[action_key]
    actor = mention(m.from_user)
    recipient = mention(target)
    endings = {
        "hug": "и вокруг на секунду стало уютнее ✨",
        "kick": "тот отлетел ровно на один пиксель и вернулся на место 😄",
        "highfive": "звонко — хлоп! ✋",
        "pat": "и выдал(а) ему +1 к настроению 🌟",
    }
    await m.answer(f"{emoji} {actor} {verb} {recipient} — {endings[action_key]}", parse_mode="HTML")

@dp.message(Command("обнять", "hug"))
async def rp_hug(m):
    await rp_action(m, "hug")

@dp.message(Command("пнуть", "пинок", "шлепнуть"))
async def rp_kick(m):
    await rp_action(m, "kick")

@dp.message(Command("датьпять", "дайпять", "highfive"))
async def rp_highfive(m):
    await rp_action(m, "highfive")

@dp.message(Command("погладить", "pat"))
async def rp_pat(m):
    await rp_action(m, "pat")

@dp.message(Command("флирт", "flirt"))
async def rp_flirt(m):
    target = await resolve_target(m)
    if not target:
        return await m.answer("Укажи пользователя: /флирт @username (или ответь на его сообщение). 🙂")
    if target.id == m.from_user.id:
        return await m.answer("С собой лучше просто поделиться комплиментом 😄")
    lines = [
        "попробовал(а) подкатить, но забыл(а) заготовленную фразу 😅",
        "отправил(а) самый уверенный смайлик: 😉",
        "сказал(а): «У тебя классное чувство юмора!» ✨",
        "передал(а) виртуальную розу 🌹",
    ]
    await m.answer(f"😉 {mention(m.from_user)} {random.choice(lines)} для {mention(target)}", parse_mode="HTML")

@dp.message(Command("репутация"))
async def rep(m):
    target=await resolve_target(m) or m.from_user
    async with aiosqlite.connect(DB_PATH) as db:
        cur=await db.execute("SELECT score FROM reputation WHERE chat_id=? AND user_id=?",(m.chat.id,target.id))
        row=await cur.fetchone()
    await m.answer(f"⭐ Репутация {mention(target)}: {row[0] if row else 0}",parse_mode="HTML")

async def rep_change(m, delta):
    target = await resolve_target(m)
    if not target:
        return await m.answer("Укажи пользователя: /репплюс @username (или ответь на его сообщение).")
    if target.id==m.from_user.id: return await m.answer("Нельзя менять собственную репутацию.")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO reputation(chat_id,user_id,score) VALUES(?,?,0)",(m.chat.id,target.id))
        await db.execute("UPDATE reputation SET score=score+? WHERE chat_id=? AND user_id=?",(delta,m.chat.id,target.id))
        await db.commit()
        cur=await db.execute("SELECT score FROM reputation WHERE chat_id=? AND user_id=?",(m.chat.id,target.id))
        score=(await cur.fetchone())[0]
    await m.answer(f"⭐ Репутация {mention(target)} теперь {score}.",parse_mode="HTML")

@dp.message(Command("rep_plus"))
async def rep_plus(m): await rep_change(m,1)
@dp.message(Command("rep_minus"))
async def rep_minus(m): await rep_change(m,-1)

# ── Chat notes ───────────────────────────────────────────────────────────────
@dp.message(Command("заметка"))
async def note(m):
    if not await admin(m): return await m.answer("Заметки может создавать администратор группы.")
    raw=(m.text or "").partition(" ")[2]
    if "|" not in raw: return await m.answer("Формат: /note название | текст")
    name,body=[x.strip() for x in raw.split("|",1)]
    if not name or not body: return await m.answer("Нужно указать название и текст.")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO notes(chat_id,name,body,author_id,created_at) VALUES(?,?,?,?,?) ON CONFLICT(chat_id,name) DO UPDATE SET body=excluded.body,author_id=excluded.author_id,created_at=excluded.created_at",(m.chat.id,name.lower(),body,m.from_user.id,now()))
        await db.commit()
    await m.answer(f"📝 Заметка «{safe(name)}» сохранена.",parse_mode="HTML")

@dp.message(Command("заметки"))
async def notes(m):
    async with aiosqlite.connect(DB_PATH) as db:
        cur=await db.execute("SELECT name FROM notes WHERE chat_id=? ORDER BY name LIMIT 50",(m.chat.id,))
        rows=await cur.fetchall()
    await m.answer("📝 Заметки:\n"+("\n".join("• "+safe(r[0]) for r in rows) if rows else "Пока нет заметок."),parse_mode="HTML")

@dp.message(Command("заметкапоказать"))
async def noteget(m):
    name=(m.text or "").partition(" ")[2].strip().lower()
    if not name: return await m.answer("Формат: /заметкапоказать название")
    async with aiosqlite.connect(DB_PATH) as db:
        cur=await db.execute("SELECT body FROM notes WHERE chat_id=? AND name=?",(m.chat.id,name))
        row=await cur.fetchone()
    await m.answer(row[0] if row else "Заметка не найдена.")

@dp.message(Command("заметкаудалить"))
async def notedel(m):
    if not await admin(m): return await m.answer("Удалять заметки может администратор.")
    name=(m.text or "").partition(" ")[2].strip().lower()
    if not name: return await m.answer("Формат: /заметкаудалить название")
    async with aiosqlite.connect(DB_PATH) as db:
        cur=await db.execute("DELETE FROM notes WHERE chat_id=? AND name=?",(m.chat.id,name))
        await db.commit()
    await m.answer("🗑 Заметка удалена." if cur.rowcount else "Заметка не найдена.")



@dp.message(Command("смс"))
async def delete_replied_message(m: Message):
    if m.chat.type == "private":
        return await m.answer("Команда /смс работает в группе: ответь на сообщение и отправь /смс.")
    if not m.reply_to_message:
        return await m.answer("Ответь на сообщение, которое нужно удалить, командой /смс.")
    if not await admin(m):
        return await m.answer("Удалять чужие сообщения командой /смс может только администратор группы.")
    try:
        await m.bot.delete_message(m.chat.id, m.reply_to_message.message_id)
        await m.bot.delete_message(m.chat.id, m.message_id)
    except TelegramAPIError as e:
        log.warning("Не удалось удалить сообщение: %s", e)
        await m.answer("Не получилось удалить сообщение. Проверь, что у бота есть право удалять сообщения и что сообщение не слишком старое.")

# ── Статистика чата ──────────────────────────────────────────────────────────
@dp.message(F.text & ~F.text.startswith("/"))
async def count_regular_message(m):
    if not m.from_user or m.chat.type == "private":
        return
    # Сохраняем username каждого активного участника, чтобы команды с @username работали без reply.
    await ensure_user(m.from_user)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO message_stats(chat_id,user_id,messages) VALUES(?,?,1) "
            "ON CONFLICT(chat_id,user_id) DO UPDATE SET messages=messages+1",
            (m.chat.id, m.from_user.id)
        )
        await db.commit()

@dp.message(Command("stats", "статистика"))
async def chat_stats(m):
    if m.chat.type == "private":
        return await m.answer("Статистика доступна в группе.")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COALESCE(SUM(messages),0), COUNT(*) FROM message_stats WHERE chat_id=?", (m.chat.id,))
        total, users = await cur.fetchone()
    await m.answer(f"📊 Статистика чата\n💬 Учтено сообщений: {total}\n👥 Активных участников: {users}")

@dp.message(Command("top", "топ"))
async def chat_top(m):
    if m.chat.type == "private":
        return await m.answer("Топ доступен в группе.")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT s.user_id,s.messages,u.full_name FROM message_stats s "
            "LEFT JOIN users u ON u.user_id=s.user_id WHERE s.chat_id=? "
            "ORDER BY s.messages DESC LIMIT 10", (m.chat.id,)
        )
        rows = await cur.fetchall()
    if not rows:
        return await m.answer("Пока нет статистики. Напишите немного сообщений в чате.")
    lines = ["🏆 Топ активности чата:"]
    for i, (uid, count, name) in enumerate(rows, 1):
        label = safe(name or f"ID {uid}")
        lines.append(f"{i}. {label} — {count} сообщ.")
    await m.answer("\n".join(lines), parse_mode="HTML")

# ── Закладки сообщений ───────────────────────────────────────────────────────
@dp.message(Command("bookmark", "закладка"))
async def bookmark(m):
    if not m.reply_to_message:
        return await m.answer("Ответь на сообщение командой /bookmark, чтобы сохранить его.")
    original = m.reply_to_message
    author = original.from_user.full_name if original.from_user else "сообщение"
    body = f"{author}: {original.text or original.caption or '[медиа без подписи]'}"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO bookmarks(chat_id,user_id,body,created_at) VALUES(?,?,?,?)",
            (m.chat.id, m.from_user.id, body[:3500], now())
        )
        await db.commit()
    await m.answer("🔖 Сохранил сообщение в твои закладки. Посмотреть: /bookmarks")

@dp.message(Command("bookmarks", "закладки"))
async def bookmarks(m):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id,body FROM bookmarks WHERE chat_id=? AND user_id=? ORDER BY id DESC LIMIT 10",
            (m.chat.id, m.from_user.id)
        )
        rows = await cur.fetchall()
    if not rows:
        return await m.answer("Закладок пока нет.")
    out = ["🔖 Твои последние закладки:"]
    for bid, body in rows:
        out.append(f"\n#{bid} — {safe(body[:500])}")
    await m.answer("\n".join(out), parse_mode="HTML")

@dp.message(Command("bookmarkdel", "удалитьзакладку"))
async def bookmark_del(m):
    raw = (m.text or "").partition(" ")[2].strip()
    if not raw.isdigit():
        return await m.answer("Формат: /bookmarkdel номер (номер виден в /bookmarks)")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM bookmarks WHERE id=? AND chat_id=? AND user_id=?",
            (int(raw), m.chat.id, m.from_user.id)
        )
        await db.commit()
    await m.answer("🗑 Закладка удалена." if cur.rowcount else "Закладка не найдена.")

# ── Таймер ───────────────────────────────────────────────────────────────────
@dp.message(Command("timer", "таймер"))
async def timer(m):
    raw = (m.text or "").partition(" ")[2].strip()
    parts = raw.split(maxsplit=1)
    if not parts or not parts[0].isdigit():
        return await m.answer("Формат: /timer 30 текст напоминания (секунды, от 1 до 86400)")
    seconds = int(parts[0])
    if seconds < 1 or seconds > 86400:
        return await m.answer("Укажи время от 1 до 86400 секунд.")
    note = parts[1] if len(parts) > 1 else "Время вышло!"
    await m.answer(f"⏳ Таймер запущен на {seconds} сек.")
    async def notify():
        await asyncio.sleep(seconds)
        try:
            await m.answer(f"⏰ Напоминание для {mention(m.from_user)}: {safe(note)}", parse_mode="HTML")
        except TelegramAPIError:
            pass
    asyncio.create_task(notify())

# ── Дополнительные лёгкие RP-команды ─────────────────────────────────────────
@dp.message(Command("поддержать", "support"))
async def rp_support(m):
    target = await resolve_target(m) if m.reply_to_message else None
    if not target:
        return await m.answer("Ответь на сообщение человека командой /поддержать 🙂")
    if target.id == m.from_user.id:
        return await m.answer("Ты тоже заслуживаешь поддержки 💛")
    lines = [
        "напомнил(а), что у тебя всё получится 💛",
        "передал(а) виртуальную чашку какао ☕",
        "отправил(а) +100 к уверенности ✨",
        "сказал(а): «Ты справишься, не сдавайся!» 🌟",
    ]
    await m.answer(f"💛 {mention(m.from_user)} поддержал(а) {mention(target)}: {random.choice(lines)}", parse_mode="HTML")

@dp.message(Command("пожатьруку", "handshake"))
async def rp_handshake(m):
    target = await resolve_target(m) if m.reply_to_message else None
    if not target:
        return await m.answer("Ответь на сообщение человека командой /пожатьруку 🤝")
    await m.answer(f"🤝 {mention(m.from_user)} пожал(а) руку {mention(target)}. Договорились!", parse_mode="HTML")

@dp.message(Command("танцевать", "dance"))
async def rp_dance(m):
    target = await resolve_target(m) if m.reply_to_message else None
    if target:
        await m.answer(f"🕺 {mention(m.from_user)} пригласил(а) {mention(target)} на танец. Музыка пошла! 🎶", parse_mode="HTML")
    else:
        await m.answer(f"🕺 {mention(m.from_user)} устроил(а) танцы прямо в чате! 🎶", parse_mode="HTML")


async def main():
    if not TOKEN:
        raise RuntimeError("Не задан BOT_TOKEN в переменных окружения Render.")

    await init_db()
    session = AiohttpSession(api=TelegramAPIServer.from_base(API_URL))
    bot = Bot(token=TOKEN, session=session)

    # Render Web Service требует, чтобы приложение слушало порт PORT.
    # Этот лёгкий HTTP endpoint также позволяет проверить, что процесс жив.
    from aiohttp import web

    async def health(_request):
        return web.json_response({"status": "ok", "service": "DropGram Iris"})

    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)

    try:
        me = await bot.get_me()
        log.info("Запущен @%s; API=%s", me.username, API_URL)
        await runner.setup()
        port = int(os.getenv("PORT", "10000"))
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        log.info("Health server слушает 0.0.0.0:%s", port)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await runner.cleanup()
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
