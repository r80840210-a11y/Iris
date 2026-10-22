#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import logging
import re
import asyncio
import json
import random
import datetime
import threading
from telegram import (Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions,
                      CallbackQuery, BotCommandScopeDefault, BotCommandScopeAllPrivateChats,
                      BotCommandScopeAllGroupChats)
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram import error as telegram_error
from dotenv import load_dotenv

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# Не светим в логах запросы httpx: в URL виден токен бота
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('apscheduler').setLevel(logging.WARNING)

# Функция безопасного редактирования сообщений
async def safe_edit_message(query, text, reply_markup=None, parse_mode=None):
    """Безопасно редактирует сообщение, а при неудаче пытается отправить новое"""
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except telegram_error.BadRequest as e:
        # игнорировать ошибку «сообщение не изменено»
        if "Message is not modified" in str(e):
            pass
        # Обрабатываем ошибку «сообщение не существует»
        elif "Message to edit not found" in str(e):
            logger.error(f"Не удалось отредактировать сообщение (сообщение не найдено): {e}")
            try:
                await query.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
            except Exception as e2:
                logger.error(f"Отправить сообщение тоже не удалось: {e2}")
        else:
            logger.error(f"Не удалось отредактировать сообщение: {e}")
            try:
                await query.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
            except Exception as e2:
                logger.error(f"Отправить сообщение тоже не удалось: {e2}")
    except Exception as e:
        logger.error(f"При редактировании сообщения произошла неизвестная ошибка: {e}")
        try:
            await query.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        except Exception as e2:
            logger.error(f"Отправить сообщение тоже не удалось: {e2}")

# Загрузка переменных окружения
load_dotenv()

# Путь к файлу хранения ключевых слов
KEYWORDS_FILE = 'keywords.json'
# путь к файлу хранения белого списка
WHITELIST_FILE = 'whitelist.json'
# путь к файлу хранения данных групп
GROUPS_FILE = 'groups.json'
# Путь к файлу псевдонимов названий групп (свои имена для отображения)
GROUP_TITLES_FILE = 'group_titles.json'
# Путь к файлу хранения забаненных пользователей
BANNED_USERS_FILE = 'banned_users.json'
# Путь к файлу хранения настроек антиспама
ANTISPAM_SETTINGS_FILE = 'antispam_settings.json'
# Настройки режима разработчика
DEV_MODE = False
# Путь к файлу счётчика нарушений
VIOLATION_COUNTS_FILE = 'violation_counts.json'
# Файлы дополнительных функций
MARRIAGES_FILE = 'marriages.json'          # браки в чатах
NICKS_FILE = 'nicks.json'                  # ники участников
GREETINGS_FILE = 'greetings.json'          # приветствия чатов
FARM_FILE = 'farm.json'                    # данные фермы/фармы
REPUTATION_FILE = 'reputation.json'        # репутация участников
CHANNEL_SETTINGS_FILE = 'channel_settings.json'  # разрешено ли писать каналам

# История сообщений для обнаружения флуда
MESSAGE_HISTORY = {}
# Порог флуда: максимальное число сообщений в секунду
FLOOD_THRESHOLD = 3
# Окно обнаружения флуда (секунды)
FLOOD_WINDOW = 5

# Настройки антиспама по умолчанию
DEFAULT_ANTISPAM_SETTINGS = {
    "anti_flood": False,           # Защита от флуда
    "block_sensitive": False,      # Блокировать подозрительные аккаунты
    "block_links": False,          # Блокировать ссылки
    "block_channel_mask": False,   # Блокировать сообщения от имени канала
    "block_channel_forward": False, # Блокировать пересылки из каналов
    "block_user_forward": False,   # Блокировать пересылки от пользователей
    "block_group_mention": False,  # Блокировать упоминания @групп
    "block_user_mention": False,   # Блокировать упоминания @пользователей
    "block_eth_address": False,    # Блокировать адреса Ethereum
    "clear_command_msg": False,    # Удалять сообщение с командой
    "block_long_msg": False,       # Блокировать слишком длинные сообщения
    "block_long_name": False,      # Блокировать слишком длинные имена
    "ban_time": 60,                # время мута в минутах
    "delete_notice_time": 5,       # Время удаления сообщения с предупреждением (секунды)
    "punishment_type": "mute"      # тип наказания: warn(предупреждение), mute(мут), kick(исключить из группы), kick_ban(исключить из группы и забанить), kick_reply(исключить только автора ответа)
}

# Загрузить настройки антиспама из файла
def load_antispam_settings():
    """Загрузка настроек антиспама из JSON-файла"""
    if os.path.exists(ANTISPAM_SETTINGS_FILE):
        try:
            with open(ANTISPAM_SETTINGS_FILE, 'r', encoding='utf-8') as file:
                return json.load(file)
        except Exception as e:
            logger.error(f"Не удалось загрузить настройки антиспама: {e}")
    return {}

# Сохранить настройки антиспама в файл
def save_antispam_settings():
    """Сохранение настроек антиспама в JSON-файл"""
    try:
        with open(ANTISPAM_SETTINGS_FILE, 'w', encoding='utf-8') as file:
            json.dump(ANTISPAM_SETTINGS, file, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Не удалось сохранить настройки антиспама: {e}")
        return False

# Загрузить чёрный список пользователей из файла
def load_banned_users():
    """Загрузка чёрного списка пользователей из JSON-файла"""
    if os.path.exists(BANNED_USERS_FILE):
        try:
            with open(BANNED_USERS_FILE, 'r', encoding='utf-8') as file:
                data = json.load(file)
                return data.get('banned_users', {})
        except Exception as e:
            logger.error(f"Не удалось загрузить чёрный список пользователей: {e}")
    return {}

# Сохранить чёрный список пользователей в файл
def save_banned_users(banned_users):
    """Сохранение чёрного списка пользователей в JSON-файл"""
    try:
        with open(BANNED_USERS_FILE, 'w', encoding='utf-8') as file:
            json.dump({'banned_users': banned_users}, file, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Не удалось сохранить чёрный список пользователей: {e}")
        return False

# Загрузить счётчик нарушений из файла
def load_violation_counts():
    """Загрузка счётчика нарушений пользователей из JSON-файла"""
    if os.path.exists(VIOLATION_COUNTS_FILE):
        try:
            with open(VIOLATION_COUNTS_FILE, 'r', encoding='utf-8') as file:
                data = json.load(file)
                return data.get('violation_counts', {})
        except Exception as e:
            logger.error(f"Не удалось загрузить счётчик нарушений: {e}")
    return {}

# Сохранить счётчик нарушений в файл
def save_violation_counts(violation_counts):
    """Сохранение счётчика нарушений в JSON-файл"""
    try:
        with open(VIOLATION_COUNTS_FILE, 'w', encoding='utf-8') as file:
            json.dump({'violation_counts': violation_counts}, file, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Не удалось сохранить счётчик нарушений: {e}")
        return False

# Загрузить спам-слова из файла
def load_keywords():
    """Загрузка спам-слов из JSON-файла"""
    global KEYWORDS
    if os.path.exists(KEYWORDS_FILE):
        try:
            with open(KEYWORDS_FILE, 'r', encoding='utf-8') as file:
                data = json.load(file)
                global_keywords = data.get('keywords', [])
                group_keywords = data.get('group_keywords', {})
                
                # Инициализировать ключевые слова группы
                if group_keywords:
                    KEYWORDS = group_keywords
                
                return global_keywords
        except Exception as e:
            logger.error(f"Не удалось загрузить ключевые слова: {e}")
    return []

# Сохранить спам-слова в файл
def save_keywords(keywords):
    """Сохранение спам-слов в JSON-файл"""
    try:
        with open(KEYWORDS_FILE, 'w', encoding='utf-8') as file:
            data = {
                'keywords': keywords,
                'group_keywords': KEYWORDS
            }
            json.dump(data, file, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Не удалось сохранить ключевые слова: {e}")
        return False

# Загрузить белый список из файла
def load_whitelist():
    """Загрузка ID пользователей белого списка из JSON-файла"""
    if os.path.exists(WHITELIST_FILE):
        try:
            with open(WHITELIST_FILE, 'r', encoding='utf-8') as file:
                data = json.load(file)
                return data.get('whitelist', [])
        except Exception as e:
            logger.error(f"Не удалось загрузить белый список: {e}")
    return []

# Сохранить белый список в файл
def save_whitelist(whitelist):
    """Сохранение ID пользователей из белого списка в JSON-файл"""
    try:
        with open(WHITELIST_FILE, 'w', encoding='utf-8') as file:
            json.dump({'whitelist': whitelist}, file, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Не удалось сохранить белый список: {e}")
        return False

# Загрузить данные групп из файла
def load_groups():
    """Загрузка данных групп из JSON-файла"""
    if os.path.exists(GROUPS_FILE):
        try:
            with open(GROUPS_FILE, 'r', encoding='utf-8') as file:
                data = json.load(file)
                return data.get('groups', {})
        except Exception as e:
            logger.error(f"Не удалось загрузить данные групп: {e}")
    return {}

# Загрузить псевдонимы названий групп из файла
def load_group_titles():
    """Загрузка псевдонимов названий групп из JSON-файла"""
    if os.path.exists(GROUP_TITLES_FILE):
        try:
            with open(GROUP_TITLES_FILE, 'r', encoding='utf-8') as file:
                data = json.load(file)
                if isinstance(data, dict):
                    return {str(k): v for k, v in data.items()}
        except Exception as e:
            logger.error(f"Не удалось загрузить псевдонимы названий групп: {e}")
    return {}

# Сохранить данные групп в файл
def save_groups(groups):
    """Сохранение данных групп в JSON-файл"""
    try:
        with open(GROUPS_FILE, 'w', encoding='utf-8') as file:
            json.dump({'groups': groups}, file, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Не удалось сохранить данные групп: {e}")
        return False

# получить токен бота
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("Отсутствует BOT_TOKEN, укажите его в файле .env")

# хранилище запрещённых слов для групп
KEYWORDS = {}

# получить список спам-слов
env_keywords = os.getenv('SPAM_KEYWORDS', '').split(',')
file_keywords = load_keywords()

# Объединяем ключевые слова из переменных окружения и файла (удаляем пустые строки)
SPAM_KEYWORDS = list(set([kw for kw in env_keywords + file_keywords if kw.strip()]))

# Сохранить изначально объединённые ключевые слова
save_keywords(SPAM_KEYWORDS)

# Загрузка белого списка
WHITELIST = load_whitelist()

# Загрузка данных групп
GROUPS = load_groups()

# Загрузка псевдонимов названий групп (group_titles.json)
GROUP_TITLES = load_group_titles()

# Загрузить настройки антиспама
ANTISPAM_SETTINGS = load_antispam_settings()

# Загрузить список заблокированных пользователей
BANNED_USERS = load_banned_users()

# Загрузка счётчика нарушений
VIOLATION_COUNTS = load_violation_counts()

# Список подозрительных аккаунтов (пока пустой список)
SENSITIVE_USERS = []

# получить список ID администраторов
ADMIN_IDS = os.getenv('ADMIN_IDS', '').split(',')
ADMIN_IDS = [int(admin_id.strip()) for admin_id in ADMIN_IDS if admin_id.strip().isdigit()]

# Сохранить исходное сообщение в файл
def save_original_message(message):
    """Сохранение исходного содержимого сообщения в файл для отладки"""
    try:
        # Преобразует объект сообщения в формат JSON
        message_json = message.to_dict()
        with open('original.txt', 'w', encoding='utf-8') as file:
            json.dump(message_json, file, ensure_ascii=False, indent=2)
        logger.info(f"Исходное сообщение сохранено в original.txt")
        return True
    except Exception as e:
        logger.error(f"Не удалось сохранить исходное сообщение: {e}")
        return False

# Проверка, является ли пользователь администратором
def is_admin(user_id):
    """Администратор: ID из .env, создатель бота или должность Администратор и выше"""
    try:
        if int(user_id) in ADMIN_IDS:
            return True
        if CREATOR_ID and int(user_id) == CREATOR_ID:
            return True
        return rank_of(user_id) >= 4
    except (TypeError, ValueError):
        return False

# Название группы для показа: псевдоним из group_titles.json, иначе название из Telegram
def group_display_title(chat_id, default=None):
    """Вернуть отображаемое название группы"""
    alias = None
    try:
        if os.path.exists(GROUP_TITLES_FILE):
            with open(GROUP_TITLES_FILE, 'r', encoding='utf-8') as file:
                alias = json.load(file).get(str(chat_id))
    except Exception as e:
        logger.error(f"Не удалось прочитать псевдонимы названий групп: {e}")
    if alias is None:
        alias = GROUP_TITLES.get(str(chat_id))
    if alias and str(alias).strip():
        return str(alias)
    return default or f'Группа {chat_id}'

# Переключить режим разработчика
async def toggle_dev_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Переключение режима разработчика"""
    user_id = update.effective_user.id
    
    # Проверка, является ли пользователь администратором
    if not is_admin(user_id):
        await update.message.reply_text('Эту команду может использовать только администратор')
        return
    
    global DEV_MODE
    DEV_MODE = not DEV_MODE
    status = "включён" if DEV_MODE else "выключен"
    
    await update.message.reply_text(f'Режим разработчика {status}.\n\n{"Сейчас бот будет сохранять полученные исходные сообщения в файл original.txt, чтобы было удобнее анализировать структуру сообщений." if DEV_MODE else ""}')

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка команды start: отправляем разные сообщения в зависимости от ситуации"""
    bot_username = context.bot.username
    
    # Проверка, запущен ли бот через кнопку «Добавить в группу»
    if update.message.chat.type != 'private':
        # записать информацию о группе
        chat = update.message.chat
        chat_id = str(chat.id)
        GROUPS[chat_id] = {
            'title': chat.title,
            'type': chat.type,
            'join_date': update.message.date.isoformat()
        }
        save_groups(GROUPS)
        
        # Инициализировать KEYWORDS этой группы (если их нет)
        if chat_id not in KEYWORDS:
            KEYWORDS[chat_id] = []
        
        # Приветственное сообщение в группе
        await update.message.reply_text(
            f'Спасибо за добавление меня в группу!\n'
            f'Я помогу вам управлять группой и фильтровать спам.\n'
            f'Убедитесь, что у меня достаточно прав (удалять сообщения, исключать пользователей и т. д.) для нормальной работы.\n'
            f'Администратор может использовать /help для просмотра всех доступных команд.'
        )
        return
    
    # Приветственное сообщение и кнопка приглашения в личном чате
    keyboard = [
        [
            InlineKeyboardButton("➕ Как добавить меня в группу", callback_data="how_to_add:group")
        ],
        [
            InlineKeyboardButton("➕ Как добавить меня в канал", callback_data="how_to_add:channel")
        ],
        [
            InlineKeyboardButton("Выбрать группу для управления", callback_data="manage_groups")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f'Здравствуйте, добро пожаловать в {bot_username}!\n\n'
        f'{bot_username} — это бот с практичными и эффективными функциями управления группами и тёплой системой общения, это ваш'
        f' помощник для управления группами и чатами.\n\n'
        f'{bot_username} — быстрый и безопасный, гарантирует конфиденциальность. Бесплатно используйте в своих группах и каналах '
        f'{bot_username}.',
        reply_markup=reply_markup
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправка справочной информации"""
    user_id = update.effective_user.id
    is_user_admin = is_admin(user_id)
    
    basic_commands = (
        'Основные команды:\n'
        '/start - запустить бота\n'
        '/help - показать справку\n'
        '/commands - полный список команд\n'
    )
    
    admin_commands = (
        '\nКоманды администратора:\n'
        '/add_keyword ключевое слово - добавить одно спам-слово\n'
        '/bulk_add_keywords ключевое слово1\\nключевое слово2 - массово добавить спам-слова, по одному в строке\n'
        '/remove_keyword ключевое слово - удалить спам-слово\n'
        '/list_keywords - вывести все спам-слова\n'
        '/add_group_keyword ID группы ключевое слово - добавить ключевое слово для конкретной группы\n'
        '/remove_group_keyword ID группы ключевое слово - удалить ключевое слово конкретной группы\n'
        '/list_group_keywords ID группы - вывести ключевые слова конкретной группы\n'
        '/list_admins - вывести ID всех администраторов\n'
        '/add_whitelist ID пользователя - добавить пользователя в белый список\n'
        '/remove_whitelist ID пользователя - удалить пользователя из белого списка\n'
        '/list_whitelist - вывести ID всех пользователей из белого списка\n'
        '/gban ID пользователя [причина] - добавить пользователя в глобальный чёрный список\n'
        '/gunban ID пользователя - удалить пользователя из глобального чёрного списка\n'
        '/admin_add @имя_пользователя 1-5 - выдать должность (5 — Владелец, 1 — Мл.Модератор)\n'
        '/admin_remove @имя_пользователя 0-5 - понизить должность (0 — снять совсем)\n'
        '/admins - список руководства чата\n'
        '/list_banned - вывести ID всех пользователей из чёрного списка\n'
        '/list_violations - вывести записи о нарушениях всех пользователей\n'
        '/clear_violation ID пользователя - очистить записи о нарушениях пользователя\n'
        '/dev_mode - переключить режим разработчика (сохранение исходных сообщений в файл)\n'
    )
    
    group_commands = (
        '\nКоманды управления группой:\n'
        '/mute @имя_пользователя 1 день/1 час и т.д - замутить пользователя на указанное число минут/часов/дней/недель/месяцев/лет (по умолчанию навсегда)\n'
        '/unmute @имя_пользователя - снять мут с пользователя (бот напишет ему в личку)\n'
        '/ban @имя_пользователя 1 день/1 час и т.д - забанить пользователя на указанное число минут/часов/дней/недель/месяцев/лет (по умолчанию навсегда)\n'
        '/unban @имя_пользователя - снять бан с пользователя (бот напишет ему в личку и пришлёт ссылку на чат)\n'
    )

    fun_commands = (
        '\nБраки и развлечения:\n'
        '/marry @юзер или «брак @юзер» — сделать предложение\n'
        '   Бот пришлёт кнопки «💖 Согласиться» / «💔 Отказаться».\n'
        '   В группе editMessageText не работает — бот шлёт новое сообщение.\n'
        '/divorce или «развод» — расторгнуть брак\n'
        '/my_marriage или «мой брак» — карточка вашего брака\n'
        '/marriages или «браки» — список всех пар чата\n'
        '   Годовщины: от Зелёной до Красной свадьбы (18 названий).\n'
        '\n'
        '/nick или «ник» — посмотреть свой ник\n'
        '/nick СЛОВО или «+ник СЛОВО» — установить ник\n'
        '\n'
        '/farm или «ферма» / «фарма» — добыча раз в 4 часа (рандом, звёзды, урожай)\n'
        '\n'
        '/greeting текст или «+Приветствие текст» — задать приветствие чата\n'
        '   «+Приветствие» — посмотреть, «-Приветствие» — удалить\n'
        '   Переменные: {имя} — имя, {ж|м|мн} — пол (муж/жен/мн. число)\n'
        '\n'
        '«+Каналы» — разрешить писать от имени каналов\n'
        '«-Каналы» — запретить (бот попытается удалить сообщение канала)\n'
        '\n'
        '«!скажи текст» — бот опубликует текст от своего имени\n'
        '\n'
        '/stats или «стата» / «стата вся» — топ участников по сообщениям\n'
        '   🥇🥈🥉 + проценты + ваше место, если не в топ-10.\n'
        '\n'
        'Репутация (в ответ на сообщение):\n'
        '   йк, респект, F, уважение, уважуха, увожение, жиза, жыза,\n'
        '   плюс, красавчик, справедливо, meine respektierung → +1\n'
        '\n'
        '/кто_я — своя карточка с цитатой-настроением\n'
        '/кто_ты (ответом) — карточка участника\n'
        '/убить, /поцеловать, /обнять, /трахнуть, /выебать, /шипперим\n'
        '/пожать_руку, /пнуть (в ответ на сообщение)\n'
    )

    mod_commands = (
        '\nКоманды модератора:\n'
        '«-смс» (в ответ) — удалить сообщение (модератор)\n'
        '«!закреп» (в ответ) — закрепить сообщение (админ)\n'
        '«!открепить» — снять закрепление (админ)\n'
        '   Внимание: на DropGram pin/unpin/delete в группе могут не работать.\n'
    )

    additional_info = (
        '\nОписание дополнительных функций:\n'
        '• Управление группами - используйте команду /start в личном чате, затем нажмите кнопку «Выбрать группу для управления»\n'
        '• Настройки антиспама - выберите пункт «Антиспам» в меню управления группой\n'
        '• Настройки наказаний - нажмите кнопку «Наказание» в меню настроек антиспама\n'
        '• Управление запрещёнными словами - выберите пункт «Запрещённые слова» в меню управления группой\n'
        '• Система счётчиков нарушений - после 2 нарушений пользователь получает глобальный бан и не может писать ни в одной группе, управляемой ботом\n'
        '• Режим разработчика - только для администраторов, сохраняет исходные сообщения для анализа структуры новых типов сообщений\n'
    )

    if is_user_admin:
        # Если это администратор, показываем все команды
        # Telegram лимит — 4096 символов на сообщение, разбиваем на части
        part1 = basic_commands + admin_commands + group_commands
        part2 = fun_commands + mod_commands + additional_info
        await update.message.reply_text(part1)
        if len(part2) > 4000:
            # На всякий случай — если part2 тоже длинная
            mid = part2.rfind('\n', 0, 4000)
            if mid == -1:
                mid = 4000
            await update.message.reply_text(part2[:mid])
            await update.message.reply_text(part2[mid:])
        else:
            await update.message.reply_text(part2)
    else:
        # Если это не администратор, показываем основные, групповые и развлекательные команды
        await update.message.reply_text(basic_commands + group_commands + fun_commands)

async def welcome_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Приветствие новых участников или обработка добавления бота в группу"""
    bot = context.bot
    
    for new_member in update.message.new_chat_members:
        # Если новый участник — сам бот
        if new_member.id == bot.id:
            # записать информацию о группе
            chat = update.message.chat
            chat_id = str(chat.id)
            GROUPS[chat_id] = {
                'title': chat.title,
                'type': chat.type,
                'join_date': update.message.date.isoformat()
            }
            save_groups(GROUPS)
            
            # Инициализировать KEYWORDS этой группы (если их нет)
            if chat_id not in KEYWORDS:
                KEYWORDS[chat_id] = []
            
            # Сообщение при добавлении бота в группу
            welcome_msg = await update.message.reply_text(
                f'Спасибо за добавление меня в группу!\n'
                f'Я помогу вам управлять группой и фильтровать спам.\n'
                f'Убедитесь, что у меня достаточно прав (удалять сообщения, исключать пользователей и т. д.) для нормальной работы.\n'
                f'Администратор может использовать /help для просмотра всех доступных команд.'
            )
            # Удалить приветственное сообщение через 5 секунд
            await asyncio.sleep(5)
            await welcome_msg.delete()
        # обычный пользователь присоединился
        elif not new_member.is_bot:
            template = GREETINGS.get(str(chat_id))
            count = len(update.message.new_chat_members or [])
            if template:
                text = _greeting_text(template, new_member, count)
            else:
                text = (f'Добро пожаловать, {new_member.first_name}, в группу! '
                        f'Соблюдайте правила группы и не отправляйте спам.')
            welcome_msg = await update.message.reply_text(text)
            # Удалить приветственное сообщение через 5 секунд
            await asyncio.sleep(5)
            try:
                await welcome_msg.delete()
            except Exception:
                pass

async def add_keyword(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Добавить спам-слово"""
    user_id = update.effective_user.id
    
    # Проверка, является ли пользователь администратором
    if not is_admin(user_id):
        await update.message.reply_text('Эту команду может использовать только администратор')
        return

    if not context.args:
        await update.message.reply_text('Укажите ключевое слово для добавления, например: /add_keyword казино')
        return
    
    keyword = context.args[0]
    if keyword not in SPAM_KEYWORDS:
        SPAM_KEYWORDS.append(keyword)
        # Сохранить обновлённый список ключевых слов
        if save_keywords(SPAM_KEYWORDS):
            await update.message.reply_text(f'Ключевое слово добавлено: {keyword}')
        else:
            await update.message.reply_text(f'Ключевое слово добавлено: {keyword} (но не удалось сохранить)')
    else:
        await update.message.reply_text(f'Ключевое слово {keyword} уже существует')

async def remove_keyword(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Удалить спам-слово"""
    user_id = update.effective_user.id
    
    # Проверка, является ли пользователь администратором
    if not is_admin(user_id):
        await update.message.reply_text('Эту команду может использовать только администратор')
        return

    if not context.args:
        await update.message.reply_text('Укажите ключевое слово для удаления, например: /remove_keyword казино')
        return
    
    keyword = context.args[0]
    if keyword in SPAM_KEYWORDS:
        SPAM_KEYWORDS.remove(keyword)
        # Сохранить обновлённый список ключевых слов
        if save_keywords(SPAM_KEYWORDS):
            await update.message.reply_text(f'Ключевое слово удалено: {keyword}')
        else:
            await update.message.reply_text(f'Ключевое слово удалено: {keyword} (но не удалось сохранить)')
    else:
        await update.message.reply_text(f'Ключевое слово {keyword} не существует')

async def list_keywords(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Вывести все спам-слова"""
    user_id = update.effective_user.id
    
    # Проверка, является ли пользователь администратором
    if not is_admin(user_id):
        await update.message.reply_text('Эту команду может использовать только администратор')
        return
    
    if not SPAM_KEYWORDS or SPAM_KEYWORDS == ['']:
        await update.message.reply_text('Сейчас спам-слова не заданы')
    else:
        keywords_list = ', '.join(SPAM_KEYWORDS)
        await update.message.reply_text(f'Текущий список спам-слов:\n{keywords_list}')

async def cmd_block_channels(update, context):
    """Удалить и предупредить, если сообщение отправлено от имени канала, когда это запрещено.
    В группах DropGram deleteMessage не работает — там только предупреждение."""
    message = update.message
    if message is None:
        return
    chat_id = update.effective_chat.id
    sender = getattr(message, 'sender_chat', None)
    if sender is None or sender.id == chat_id:
        return
    if CHANNELS_ALLOWED.get(str(chat_id), False):
        return
    title = sender.title or f'ID {sender.id}'
    # попробуем удалить; в группах DropGram не работает — но попытаемся
    try:
        await message.delete()
    except Exception as e:
        logger.info(f"Не удалось удалить сообщение канала (возможно, сервер не поддерживает): {e}")
    try:
        await message.reply_text(
            f'⚠️ Каналу «{html_escape(title)}» запрещено писать в этом чате.')
    except Exception as e:
        logger.error(f"Не удалось предупредить о канале: {e}")


async def check_spam(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Проверить, является ли сообщение спамом"""
    # Если режим разработчика включён, записываем все типы сообщений для анализа
    if DEV_MODE and update.message and is_admin(update.message.from_user.id):
        logger.info(f"Получено сообщение, начинаю анализ: {update.message.message_id}")
        save_original_message(update.message)
        await update.message.reply_text("Это сообщение сохранено в файл original.txt для анализа")
    
    # Если это не сообщение, сразу выходим
    if not update.message:
        return
    
    # Если это сообщение самого бота, сразу выходим
    if update.message.from_user.is_bot:
        return
    
    # получить ID отправителя сообщения
    user_id = str(update.message.from_user.id)
    
    # Если отправитель сообщения — администратор, сразу выходим
    if is_admin(int(user_id)):
        return
    
    # Если отправитель сообщения в белом списке, сразу выходим
    if user_id in WHITELIST:
        return
    
    # Если отправитель сообщения в чёрном списке, сразу удаляем сообщение
    if user_id in BANNED_USERS:
        try:
            await update.message.delete()
            logger.info(f"Удалено сообщение пользователя {user_id} из чёрного списка")
        except Exception as e:
            logger.error(f"Не удалось удалить сообщение пользователя из чёрного списка: {e}")
        return
    
    # получить ID группы
    chat_id = str(update.effective_chat.id)
    
    # Если группы нет в списке присоединённых, сразу выходим
    if chat_id not in GROUPS:
        return
    
    # получить настройки антиспама для этой группы
    settings = ANTISPAM_SETTINGS.get(chat_id, DEFAULT_ANTISPAM_SETTINGS.copy())
    
    # Инициализировать переменные
    should_delete = False
    violation_reason = ""
    
    # Текст сообщения (если есть)
    text = update.message.text or ""
    
    # Проверка, является ли аккаунт подозрительным
    if settings.get('block_sensitive', False) and user_id in SENSITIVE_USERS:
        await handle_spam_message(update, context, "От подозрительного аккаунта", settings)
        return
    
    # Проверка, является ли сообщение отправленным от имени канала
    if settings.get('block_channel_mask', False):
        # Проверка поля external_reply (сообщения от имени канала, появившиеся в Telegram 9.5.3+)
        message_dict = update.message.to_dict()
        if 'external_reply' in message_dict:
            external_reply = message_dict.get('external_reply', {})
            origin = external_reply.get('origin', {})
            
            # Если исходный источник сообщения — канал
            if origin.get('type') == 'channel':
                await handle_spam_message(update, context, "Сообщение от имени канала (внешний ответ)", settings)
                return
    
    # Проверка, является ли сообщение пересланным
    if update.message.forward_from_chat:
        # Если это пересылка из канала
        if update.message.forward_from_chat.type == 'channel' and settings.get('block_channel_forward', False):
            await handle_spam_message(update, context, "Пересланный из канала контент", settings)
            return
    
    if update.message.forward_from and settings.get('block_user_forward', False):
        # Если это пересылка от пользователя
        await handle_spam_message(update, context, "Пересланный пользователем контент", settings)
        return
    
    # 1. Проверка флуда (если включена)
    if settings.get('anti_flood', False) and user_id:
        # получить историю сообщений пользователя в этой группе
        if chat_id not in MESSAGE_HISTORY:
            MESSAGE_HISTORY[chat_id] = {}
        
        if user_id not in MESSAGE_HISTORY[chat_id]:
            MESSAGE_HISTORY[chat_id][user_id] = []
        
        # Добавить метку времени текущего сообщения
        current_time = datetime.datetime.now().timestamp()
        MESSAGE_HISTORY[chat_id][user_id].append(current_time)
        
        # Оставляем только записи о сообщениях за последние FLOOD_WINDOW секунд
        MESSAGE_HISTORY[chat_id][user_id] = [
            t for t in MESSAGE_HISTORY[chat_id][user_id] 
            if current_time - t <= FLOOD_WINDOW
        ]
        
        # Проверка превышения порога
        if len(MESSAGE_HISTORY[chat_id][user_id]) >= FLOOD_THRESHOLD:
            # вычислить интервал между последними сообщениями
            intervals = [
                MESSAGE_HISTORY[chat_id][user_id][i] - MESSAGE_HISTORY[chat_id][user_id][i-1]
                for i in range(1, len(MESSAGE_HISTORY[chat_id][user_id]))
            ]
            
            # Если интервал между последовательными сообщениями очень мал (меньше 1 секунды), это считается флудом
            if intervals and sum(intervals) / len(intervals) < 1.0:
                should_delete = True
                violation_reason = "Флуд (слишком много сообщений за короткое время)"
    
    # 2. Проверка ключевых слов
    if text and not should_delete:
        message_text = text.lower()
        
        # Сначала проверяем глобальные ключевые слова
        for keyword in SPAM_KEYWORDS:
            if keyword and keyword.lower() in message_text:
                should_delete = True
                violation_reason = f"Содержит запрещённое слово «{keyword}»"
                break
        
        # Затем проверить ключевые слова конкретной группы
        if not should_delete and chat_id in KEYWORDS:
            for keyword in KEYWORDS[chat_id]:
                if keyword and keyword.lower() in message_text:
                    should_delete = True
                    violation_reason = f"Содержит запрещённое слово группы «{keyword}»"
                    break
    
    # 3. Проверка ссылок (если включена)
    if not should_delete and settings.get('block_links', False) and text:
        # простое регулярное выражение для определения URL
        url_pattern = re.compile(r'https?://\S+|www\.\S+')
        if url_pattern.search(text):
            should_delete = True
            violation_reason = "Содержит ссылку"
    
    # 6. Проверка наличия @ID группы (если включена)
    if not should_delete and settings.get('block_group_mention', False) and text:
        if re.search(r'@[a-zA-Z][a-zA-Z0-9_]*', text):
            should_delete = True
            violation_reason = "Содержит @ID группы"
    
    # 7. Проверка наличия @ID пользователя (если включена)
    if not should_delete and settings.get('block_user_mention', False) and update.message.entities:
        for entity in update.message.entities:
            if entity.type == 'mention':
                should_delete = True
                violation_reason = "Содержит @ID пользователя"
                break
    
    # 8. Проверка наличия адреса Ethereum (если включена)
    if not should_delete and settings.get('block_eth_address', False) and text:
        # Регулярное выражение для поиска адресов Ethereum
        eth_pattern = re.compile(r'0x[a-fA-F0-9]{40}')
        if eth_pattern.search(text):
            should_delete = True
            violation_reason = "Содержит адрес Ethereum"
    
    # 9. Проверка слишком длинного сообщения (если включена)
    if not should_delete and settings.get('block_long_msg', False) and text:
        if len(text) > 2000:  # сообщение длиннее 2000 символов считается слишком длинным
            should_delete = True
            violation_reason = "Сообщение слишком длинное"
    
    # 10. Проверка слишком длинного имени пользователя (если включена)
    if not should_delete and settings.get('block_long_name', False) and update.message.from_user:
        full_name = f"{update.message.from_user.first_name or ''} {update.message.from_user.last_name or ''}".strip()
        if len(full_name) > 50:  # имя длиннее 50 символов считается слишком длинным
            should_delete = True
            violation_reason = "Имя пользователя слишком длинное"
    
    # Если правила нарушены, обрабатываем сообщение
    if should_delete:
        await handle_spam_message(update, context, violation_reason, settings)

async def handle_spam_message(update: Update, context: ContextTypes.DEFAULT_TYPE, reason: str, settings: dict) -> None:
    """Обработка спама"""
    try:
        # получить информацию о пользователе и группе
        chat_id = update.effective_chat.id
        user = update.message.from_user
        user_id = user.id
        user_name = user.first_name
        
        # получить тип наказания
        punishment_type = settings.get('punishment_type', 'mute')
        ban_time = settings.get('ban_time', 60)  # время мута (минуты)
        delete_notice_time = settings.get('delete_notice_time', 5)  # Время удаления предупреждения (секунды)
        
        # Удалить сообщение
        await update.message.delete()
        
        # Обновление счётчика нарушений
        global VIOLATION_COUNTS
        user_id_str = str(user_id)
        
        # Увеличиваем счётчик нарушений
        if user_id_str not in VIOLATION_COUNTS:
            VIOLATION_COUNTS[user_id_str] = 1
        else:
            VIOLATION_COUNTS[user_id_str] += 1
            
        # Сохранить счётчик нарушений
        save_violation_counts(VIOLATION_COUNTS)
        
        # Проверка, достигнут ли лимит нарушений (2 раза)
        if VIOLATION_COUNTS[user_id_str] >= 2 and user_id_str not in BANNED_USERS:
            # Добавить в глобальный чёрный список
            BANNED_USERS[user_id_str] = {
                'added_by': 'system',
                'added_time': datetime.datetime.now().isoformat(),
                'reason': 'Автобан за два нарушения'
            }
            # Сохранить обновлённый чёрный список
            save_banned_users(BANNED_USERS)
            
            punishment_type = 'kick_ban'  # принудительно изменено на исключение и бан
            
        # Выполнение действий в зависимости от типа наказания
        warning_message = ""
        if punishment_type == 'warn':
            # Только отправляем предупреждение
            warning_message = f"⚠️ Сообщение от @{user.username or user_name} удалено. Причина: {reason}. Это нарушение № {VIOLATION_COUNTS[user_id_str]} (за 2 нарушения — глобальный бан)"
        
        elif punishment_type == 'mute':
            # замутить пользователя
            until_date = datetime.datetime.now() + datetime.timedelta(minutes=ban_time)
            await context.bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=user_id,
                permissions=ChatPermissions(
                    can_send_messages=False,
                    can_send_polls=False,
                    can_send_other_messages=False,
                    can_add_web_page_previews=False,
                    can_change_info=False,
                    can_invite_users=False,
                    can_pin_messages=False
                ),
                until_date=until_date
            )
            warning_message = f"⚠️ Сообщение от @{user.username or user_name} удалено. Причина: {reason}. Пользователь замучен на {ban_time} минут. Это нарушение № {VIOLATION_COUNTS[user_id_str]} (за 2 нарушения — глобальный бан)"
        
        elif punishment_type == 'kick':
            # Исключить пользователя
            await context.bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
            # немедленно снять бан, чтобы пользователь мог снова присоединиться по ссылке-приглашению
            await context.bot.unban_chat_member(chat_id=chat_id, user_id=user_id, only_if_banned=True)
            warning_message = f"⚠️ Сообщение от @{user.username or user_name} удалено. Причина: {reason}. Пользователь исключён из группы. Это нарушение № {VIOLATION_COUNTS[user_id_str]} (за 2 нарушения — глобальный бан)"
        
        elif punishment_type == 'kick_ban':
            # Исключить и забанить пользователя
            await context.bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
            if VIOLATION_COUNTS[user_id_str] >= 2:
                warning_message = f"⚠️ Сообщение от @{user.username or user_name} удалено. Причина: {reason}. Пользователь забанен навсегда. Так как счётчик нарушений достиг 2, он добавлен в глобальный чёрный список."
            else:
                warning_message = f"⚠️ Сообщение от @{user.username or user_name} удалено. Причина: {reason}. Пользователь забанен навсегда. Это нарушение № {VIOLATION_COUNTS[user_id_str]} (за 2 нарушения — глобальный бан)"
        
        elif punishment_type == 'kick_reply':
            # Исключать только того, кому ответили (если сообщение является ответом на другое сообщение)
            if update.message.reply_to_message:
                replied_user_id = update.message.reply_to_message.from_user.id
                replied_user_name = update.message.reply_to_message.from_user.first_name
                
                await context.bot.ban_chat_member(chat_id=chat_id, user_id=replied_user_id)
                # немедленно снять бан, чтобы пользователь мог снова присоединиться по ссылке-приглашению
                await context.bot.unban_chat_member(chat_id=chat_id, user_id=replied_user_id, only_if_banned=True)
                
                warning_message = f"⚠️ Сообщение от @{user.username or user_name} удалено. Причина: {reason}. Пользователь, которому ответили, {replied_user_name}, исключён из группы. Это нарушение № {VIOLATION_COUNTS[user_id_str]} (за 2 нарушения — глобальный бан)"
            else:
                warning_message = f"⚠️ Сообщение от @{user.username or user_name} удалено. Причина: {reason}. (исключать некого — сообщение не является ответом) Это нарушение № {VIOLATION_COUNTS[user_id_str]} (за 2 нарушения — глобальный бан)"
        
        # Добавить кнопки действий
        keyboard = [[
            InlineKeyboardButton("Забанить пользователя", callback_data=f"ban_user:{user_id}"),
            InlineKeyboardButton("Снять мут", callback_data=f"unmute_user:{user_id}")
        ]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Отправляем предупреждение с кнопками действий
        if warning_message:
            sent_message = await context.bot.send_message(
                chat_id=chat_id,
                text=warning_message,
                reply_markup=reply_markup
            )
            logger.info(f"Удалено спам-сообщение пользователя {user_id}, причина: {reason}, количество нарушений: {VIOLATION_COUNTS[user_id_str]}")
            
            # Если включено автоматическое удаление предупреждений
            if delete_notice_time > 0:
                # Удалить предупреждение через некоторое время
                await asyncio.sleep(delete_notice_time)
                await sent_message.delete()
                
    except Exception as e:
        logger.error(f"Не удалось обработать спам-сообщение: {e}")
        # Проверка, не связана ли ошибка с исключением бота из группы
        if "bot was kicked" in str(e) or "not enough rights" in str(e):
            logger.info(f"Бот, вероятно, был исключён из группы {chat_id}, удалено из записей")
            if str(chat_id) in GROUPS:
                del GROUPS[str(chat_id)]
                save_groups(GROUPS)

async def list_admins(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Вывести все ID администраторов"""
    user_id = update.effective_user.id
    
    # Проверка, является ли пользователь администратором
    if not is_admin(user_id):
        await update.message.reply_text('Эту команду может использовать только администратор')
        return
    
    if not ADMIN_IDS:
        await update.message.reply_text('Сейчас администраторы не заданы')
    else:
        admins_list = ', '.join(map(str, ADMIN_IDS))
        await update.message.reply_text(f'Текущий список ID администраторов:\n{admins_list}')

async def bulk_add_keywords(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Массовое добавление спам-слов, по одному в строке"""
    user_id = update.effective_user.id
    
    # Проверка, является ли пользователь администратором
    if not is_admin(user_id):
        await update.message.reply_text('Эту команду может использовать только администратор')
        return
    
    # получить текст после команды
    command_text = update.message.text
    # Пропускаем команду и берём остальной текст
    if ' ' in command_text:
        keywords_text = command_text.split(' ', 1)[1]
    else:
        await update.message.reply_text(
            'Добавьте ключевые слова в таком формате, по одному в строке:\n'
            '/bulk_add_keywords ключевое слово1\nключевое слово2\nключевое слово3'
        )
        return
    
    # разделить ключевые слова по строкам
    keywords = [kw.strip() for kw in keywords_text.split('\n') if kw.strip()]
    
    if not keywords:
        await update.message.reply_text('Не указано допустимое ключевое слово')
        return
    
    # записать результат добавления
    added = []
    duplicated = []
    
    # Добавить ключевое слово
    for keyword in keywords:
        if keyword not in SPAM_KEYWORDS:
            SPAM_KEYWORDS.append(keyword)
            added.append(keyword)
        else:
            duplicated.append(keyword)
    
    # Сохранить обновлённый список ключевых слов
    save_result = "✅" if save_keywords(SPAM_KEYWORDS) else "❌ (не удалось сохранить)"
    
    # Формирование сообщения с результатом
    result_message = []
    if added:
        result_message.append(f"✅ Успешно добавлены ключевые слова ({len(added)}): {', '.join(added)}")
    if duplicated:
        result_message.append(f"⚠️ Такие ключевые слова уже существуют ({len(duplicated)}): {', '.join(duplicated)}")
    
    result_message.append(f"Статус сохранения: {save_result}")
    
    if result_message:
        await update.message.reply_text('\n'.join(result_message))
    else:
        await update.message.reply_text('Произошла ошибка при добавлении ключевого слова')

async def add_whitelist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Добавить пользователя в белый список"""
    user_id = update.effective_user.id
    
    # Проверка, является ли пользователь администратором
    if not is_admin(user_id):
        await update.message.reply_text('Эту команду может использовать только администратор')
        return

    if not context.args:
        await update.message.reply_text('Укажите ID пользователя для добавления в белый список, например: /add_whitelist 123456789')
        return
    
    try:
        whitelist_id = int(context.args[0])
        if whitelist_id not in WHITELIST:
            WHITELIST.append(whitelist_id)
            # Сохранить обновлённый белый список
            if save_whitelist(WHITELIST):
                await update.message.reply_text(f'Пользователь {whitelist_id} добавлен в белый список')
            else:
                await update.message.reply_text(f'Пользователь {whitelist_id} добавлен в белый список (но не удалось сохранить)')
        else:
            await update.message.reply_text(f'Пользователь {whitelist_id} уже в белом списке')
    except ValueError:
        await update.message.reply_text('Укажите корректный ID пользователя (только цифры)')

async def remove_whitelist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Убрать пользователя из белого списка"""
    user_id = update.effective_user.id
    
    # Проверка, является ли пользователь администратором
    if not is_admin(user_id):
        await update.message.reply_text('Эту команду может использовать только администратор')
        return

    if not context.args:
        await update.message.reply_text('Укажите ID пользователя, которого нужно убрать из белого списка, например: /remove_whitelist 123456789')
        return
    
    try:
        whitelist_id = int(context.args[0])
        if whitelist_id in WHITELIST:
            WHITELIST.remove(whitelist_id)
            # Сохранить обновлённый белый список
            if save_whitelist(WHITELIST):
                await update.message.reply_text(f'Пользователь {whitelist_id} убран из белого списка')
            else:
                await update.message.reply_text(f'Пользователь {whitelist_id} убран из белого списка (но не удалось сохранить)')
        else:
            await update.message.reply_text(f'Пользователь {whitelist_id} отсутствует в белом списке')
    except ValueError:
        await update.message.reply_text('Укажите корректный ID пользователя (только цифры)')

async def list_whitelist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Вывести все ID пользователей из белого списка"""
    user_id = update.effective_user.id
    
    # Проверка, является ли пользователь администратором
    if not is_admin(user_id):
        await update.message.reply_text('Эту команду может использовать только администратор')
        return
    
    if not WHITELIST:
        await update.message.reply_text('Текущий белый список пуст')
    else:
        whitelist_text = ', '.join(map(str, WHITELIST))
        await update.message.reply_text(f'ID пользователей в текущем белом списке:\n{whitelist_text}')

async def handle_group_management(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка нажатия кнопки управления группой"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    # Консольный лог: кто нажал кнопку управления
    _uname = f"@{query.from_user.username}" if query.from_user.username else user_id
    logger.info(f'🔘 {_uname} нажал кнопку управления: {query.data}')
    
    # Инструкция «как добавить бота» — доступна всем, поэтому до проверки прав
    if query.data and query.data.startswith("how_to_add:"):
        username = context.bot.username
        bot_id = context.bot.id
        what = query.data.split(":", 1)[1]
        if what == "channel":
            text = (
                f'Как добавить меня в канал:\n'
                f'1. Откройте канал → Управление → Администраторы.\n'
                f'2. Нажмите «Добавить администратора» и введите @{username} — или, если по нику не находит, мой ID: {bot_id}.\n'
                f'3. Выдайте права: публикация и удаление сообщений, бан участников.\n\n'
                f'Если клиент пишет «Не удалось добавить пользователя» — вводите именно ID {bot_id}.'
            )
        else:
            text = (
                f'Как добавить меня в группу:\n'
                f'1. Откройте группу → Управление → Участники.\n'
                f'2. Нажмите «Добавить участника» и введите @{username} — или, если по нику не находит, мой ID: {bot_id}.\n'
                f'3. Выдайте мне права администратора: удаление сообщений, мут и бан участников, приглашения.\n\n'
                f'Если клиент пишет «Не удалось добавить пользователя» — вводите именно ID {bot_id}.\n\n'
                f'После этого отправьте здесь /start и выберите группу в меню ниже.'
            )
        try:
            await query.message.reply_text(text)
        except Exception as e:
            logger.error(f"Не удалось отправить инструкцию: {e}")
        return
    
    # Объявляем глобальные переменные
    global GROUPS, ANTISPAM_SETTINGS, SPAM_KEYWORDS, WHITELIST, BANNED_USERS, KEYWORDS
    
    # Проверка, является ли пользователь администратором
    if not is_admin(user_id):
        try:
            await query.edit_message_text('Только администратор может управлять группой')
        except Exception as e:
            logger.error(f"Не удалось отредактировать сообщение: {e}")
            # Если редактирование не удалось, пробуем отправить новое сообщение
            try:
                await query.message.reply_text('Только администратор может управлять группой')
            except Exception as e2:
                logger.error(f"Не удалось отправить сообщение: {e2}")
        return
    
    # Если это callback управления списком групп
    if query.data == "manage_groups":
        try:
            await show_groups_list(query, context)
        except Exception as e:
            logger.error(f"Не удалось показать список групп: {e}")
            try:
                await query.message.reply_text("Не удалось показать список групп, попробуйте ещё раз")
            except Exception:
                pass
    
    # опции управления группой
    elif query.data.startswith("manage_group:"):
        group_id = query.data.split(":", 1)[1]
        await manage_group(query, group_id)
    
    # Просмотр подробной информации о группе
    elif query.data.startswith("group_info:"):
        group_id = query.data.split(":", 1)[1]
        if group_id in GROUPS:
            group_info = GROUPS[group_id]
            title = group_display_title(group_id, group_info.get('title', 'Неизвестная группа'))
            group_type = "группа" if group_info.get('type') == 'supergroup' else "канал" if group_info.get('type') == 'channel' else "групповой чат"
            join_date = group_info.get('join_date', 'Время неизвестно')
            
            # Кнопка возврата
            keyboard = [[InlineKeyboardButton("« Назад", callback_data=f"manage_group:{group_id}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            try:
                await query.edit_message_text(
                    f"Информация о группе:\n\n"
                    f"Название: {title}\n"
                    f"Тип: {group_type}\n"
                    f"ID: {group_id}\n"
                    f"Время вступления: {join_date}",
                    reply_markup=reply_markup
                )
            except Exception as e:
                logger.error(f"Не удалось отредактировать сообщение: {e}")
                try:
                    await query.message.reply_text(
                        f"Информация о группе:\n\n"
                        f"Название: {title}\n"
                        f"Тип: {group_type}\n"
                        f"ID: {group_id}\n"
                        f"Время вступления: {join_date}",
                        reply_markup=reply_markup
                    )
                except Exception:
                    pass
        else:
            try:
                await query.edit_message_text("Группа не найдена")
            except Exception as e:
                logger.error(f"Не удалось отредактировать сообщение: {e}")
                try:
                    await query.message.reply_text("Группа не найдена")
                except Exception:
                    pass
    
    # Настройки приветствия новых участников
    elif query.data.startswith("welcome_settings:"):
        group_id = query.data.split(":", 1)[1]
        if group_id in GROUPS:
            # Кнопка возврата
            keyboard = [[InlineKeyboardButton("« Назад", callback_data=f"manage_group:{group_id}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            try:
                await query.edit_message_text(
                    f"Приветствие новых участников\n\n"
                    f"Текущее приветствие:\n«Добро пожаловать, [имя пользователя], в группу! Соблюдайте правила группы и не отправляйте спам.»\n\n"
                    f"(функция настраиваемого приветствия в разработке)",
                    reply_markup=reply_markup
                )
            except Exception as e:
                logger.error(f"Не удалось отредактировать сообщение: {e}")
                try:
                    await query.message.reply_text(
                        f"Приветствие новых участников\n\n"
                        f"Текущее приветствие:\n«Добро пожаловать, [имя пользователя], в группу! Соблюдайте правила группы и не отправляйте спам.»\n\n"
                        f"(функция настраиваемого приветствия в разработке)",
                        reply_markup=reply_markup
                    )
                except Exception:
                    pass
        else:
            try:
                await query.edit_message_text("Группа не найдена")
            except Exception as e:
                logger.error(f"Не удалось отредактировать сообщение: {e}")
    
    # Настройки антиспама
    elif query.data.startswith("antispam_settings:"):
        group_id = query.data.split(":", 1)[1]
        if group_id in GROUPS:
            # получить настройки антиспама для этой группы
            group_settings = ANTISPAM_SETTINGS.get(group_id, DEFAULT_ANTISPAM_SETTINGS.copy())
            
            # Убедиться, что все параметры настроек присутствуют
            for key in DEFAULT_ANTISPAM_SETTINGS:
                if key not in group_settings:
                    group_settings[key] = DEFAULT_ANTISPAM_SETTINGS[key]
            
            ban_time = group_settings.get("ban_time", 60)
            delete_notice_time = group_settings.get("delete_notice_time", 5)
            
            html_content = (
                f"<b>Антиспам</b>\n\n"
                f"<b>Общее наказание — мут {ban_time} минут</b>\n"
                f"<b>Удалить напоминание через {delete_notice_time} сек.</b>\n\n"
            )
            
            # Проверка состояния защиты от флуда и блокировки подозрительных аккаунтов
            flood_status = "✅" if group_settings.get("anti_flood", False) else "❌"
            sensitive_status = "✅" if group_settings.get("block_sensitive", False) else "❌"
            
            html_content += (
                f"<b>{flood_status} Защита от флуда:</b>\n"
                f"└ Пользователь за короткое время отправляет сообщения в несколько групп\n\n"
                f"<b>{sensitive_status} Блокировка подозрительных аккаунтов:</b>\n"
                f"└ Аккаунты, забаненные во всех группах за многократную отправку спама"
            )
            
            # Создать кнопку функции, отображающую ✅ или ❌ в зависимости от текущего состояния
            keyboard = []
            
            features = [
                ("anti_flood", "Защита от флуда"),
                ("block_sensitive", "Блокировать подозрительные аккаунты"),
                ("block_links", "Блокировать ссылки"),
                ("block_channel_mask", "Блокировать сообщения от имени канала"),
                ("block_channel_forward", "Блокировать пересылки из каналов"),
                ("block_user_forward", "Блокировать пересылки от пользователей"),
                ("block_group_mention", "Блокировать упоминания @групп"),
                ("block_user_mention", "Блокировать упоминания @пользователей"),
                ("block_eth_address", "Блокировать адреса Ethereum"),
                ("clear_command_msg", "Удалять сообщение с командой"),
                ("block_long_msg", "Блокировать слишком длинные сообщения"),
                ("block_long_name", "Блокировать слишком длинные имена")
            ]
            
            # добавить кнопки функций в раскладке по 2 в ряд
            for i in range(0, len(features), 2):
                row = []
                # Добавить первую функциональную кнопку
                key1, name1 = features[i]
                status1 = "✅" if group_settings.get(key1, False) else "❌"
                row.append(InlineKeyboardButton(f"{status1} {name1}", callback_data=f"toggle_antispam:{group_id}:{key1}"))
                
                # Добавить вторую функциональную кнопку (если есть)
                if i + 1 < len(features):
                    key2, name2 = features[i + 1]
                    status2 = "✅" if group_settings.get(key2, False) else "❌"
                    row.append(InlineKeyboardButton(f"{status2} {name2}", callback_data=f"toggle_antispam:{group_id}:{key2}"))
                
                keyboard.append(row)
            
            # Добавить кнопки настроек наказаний и исключений
            keyboard.append([
                InlineKeyboardButton("⚡ Наказание", callback_data=f"punishment_settings:{group_id}"),
                InlineKeyboardButton("⚠️ Исключения", callback_data=f"whitelist_settings:{group_id}")
            ])
            
            # Добавить кнопку очистки запрещённых слов
            keyboard.append([
                InlineKeyboardButton("🗑️ Очистить запрещённые слова", callback_data=f"remove_keywords:{group_id}")
            ])
            
            # Кнопка возврата
            keyboard.append([
                InlineKeyboardButton("« Назад", callback_data=f"manage_group:{group_id}")
            ])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            try:
                await query.edit_message_text(html_content, reply_markup=reply_markup, parse_mode="HTML")
            except telegram_error.BadRequest as e:
                # Проверка, не является ли ошибка следствием того, что сообщение не изменилось
                if "Message is not modified" in str(e):
                    # Если сообщение не изменено, ничего делать не нужно
                    pass
                else:
                    # Если это другая ошибка, записываем в лог и продолжаем
                    logger.error(f"Не удалось отредактировать сообщение: {e}")
        else:
            await query.edit_message_text("Группа не найдена")
    
    # Настройки наказаний
    elif query.data.startswith("punishment_settings:"):
        group_id = query.data.split(":", 1)[1]
        if group_id in GROUPS:
            await render_punishment_settings(query, group_id)
        else:
            await safe_edit_message(query, "Группа не найдена")
    
    # Подсказка для задания времени мута
    elif query.data.startswith("set_ban_time_prompt:"):
        parts = query.data.split(":")
        if len(parts) >= 2:
            group_id = parts[1]
            # Проверка существования группы
            if check_group_exists(group_id):
                # Создать HTML-содержимое
                html_content = f"<b>Выберите длительность мута</b>\n\nВыберите, на сколько минут замутить:"
                
                # Создать кнопки выбора времени
                keyboard = []
                # Добавить варианты времени
                row1 = []
                for minutes in [5, 10, 15]:
                    row1.append(InlineKeyboardButton(f"{minutes} мин.", callback_data=f"set_ban_time:{group_id}:{minutes}"))
                keyboard.append(row1)
                
                row2 = []
                for minutes in [30, 60, 120]:
                    row2.append(InlineKeyboardButton(f"{minutes} мин.", callback_data=f"set_ban_time:{group_id}:{minutes}"))
                keyboard.append(row2)
                
                row3 = []
                for minutes in [360, 720, 1440]:
                    display = f"{minutes//60} ч." if minutes >= 60 else f"{minutes} мин."
                    row3.append(InlineKeyboardButton(display, callback_data=f"set_ban_time:{group_id}:{minutes}"))
                keyboard.append(row3)
                
                # Кнопка возврата
                keyboard.append([
                    InlineKeyboardButton("« Назад", callback_data=f"punishment_settings:{group_id}")
                ])
                
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await safe_edit_message(query, html_content, reply_markup=reply_markup, parse_mode="HTML")
            else:
                await query.answer("Группа не найдена")
        else:
            await query.answer("Неверная команда")

    # Подсказка для задания времени удаления напоминания
    elif query.data.startswith("set_delete_time_prompt:"):
        parts = query.data.split(":")
        if len(parts) >= 2:
            group_id = parts[1]
            # Проверка существования группы
            if check_group_exists(group_id):
                # Создать HTML-содержимое
                html_content = f"<b>Выберите время удаления напоминания</b>\n\nВыберите, через сколько секунд удалять сообщение-напоминание:"
                
                # Создать кнопки выбора времени
                keyboard = []
                # Добавить варианты времени
                row1 = []
                for seconds in [3, 5, 10]:
                    row1.append(InlineKeyboardButton(f"{seconds} сек.", callback_data=f"set_delete_time:{group_id}:{seconds}"))
                keyboard.append(row1)
                
                row2 = []
                for seconds in [15, 30, 60]:
                    row2.append(InlineKeyboardButton(f"{seconds} сек.", callback_data=f"set_delete_time:{group_id}:{seconds}"))
                keyboard.append(row2)
                
                # Кнопка возврата
                keyboard.append([
                    InlineKeyboardButton("« Назад", callback_data=f"punishment_settings:{group_id}")
                ])
                
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await safe_edit_message(query, html_content, reply_markup=reply_markup, parse_mode="HTML")
            else:
                await query.answer("Группа не найдена")
        else:
            await query.answer("Неверная команда")

    # Настройки белого списка (исключения)
    elif query.data.startswith("whitelist_settings:"):
        group_id = query.data.split(":", 1)[1]
        if check_group_exists(group_id):
            # Создать кнопку
            keyboard = [
                [InlineKeyboardButton("Посмотреть пользователей из белого списка", callback_data=f"view_whitelist:{group_id}")],
                [InlineKeyboardButton("Добавить пользователя в белый список", callback_data=f"add_whitelist_prompt:{group_id}")],
                [InlineKeyboardButton("Удалить пользователя из белого списка", callback_data=f"remove_whitelist_prompt:{group_id}")],
                [InlineKeyboardButton("« Назад", callback_data=f"antispam_settings:{group_id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await safe_edit_message(
                query,
                f"Настройки белого списка\n\n"
                f"Сообщения пользователей из белого списка не проверяются и не удаляются, даже если содержат запрещённые слова.\n"
                f"Сейчас всего {len(WHITELIST)} пользователей в белом списке",
                reply_markup=reply_markup
            )
        else:
            await safe_edit_message(query, "Группа не найдена")
    
    # Переключить состояние функции антиспама
    elif query.data.startswith("toggle_antispam:"):
        parts = query.data.split(":", 2)
        if len(parts) != 3:
            await query.answer("Неверное действие")
            return
        
        group_id = parts[1]
        feature_key = parts[2]
        
        if check_group_exists(group_id):
            await toggle_antispam_feature(query, group_id, feature_key)
        else:
            await query.answer("Группа не найдена")
    
    # Задать время мута
    elif query.data.startswith("set_ban_time:"):
        parts = query.data.split(":", 2)
        if len(parts) != 3:
            await query.answer("Неверное действие")
            return
        
        group_id = parts[1]
        try:
            ban_time = int(parts[2])
            
            if check_group_exists(group_id):
                await set_ban_time(query, group_id, ban_time)
            else:
                await query.answer("Группа не найдена")
        except ValueError:
            await query.answer("Неверное время мута")
    
    # Задать время удаления сообщения-напоминания
    elif query.data.startswith("set_delete_time:"):
        parts = query.data.split(":", 2)
        if len(parts) != 3:
            await query.answer("Неверное действие")
            return
        
        group_id = parts[1]
        try:
            delete_time = int(parts[2])
            
            if check_group_exists(group_id):
                await set_delete_time(query, group_id, delete_time)
            else:
                await query.answer("Группа не найдена")
        except ValueError:
            await query.answer("Неверное время удаления")

    # Задать тип наказания
    elif query.data.startswith("set_punishment:"):
        parts = query.data.split(":", 2)
        if len(parts) != 3:
            await query.answer("Неверное действие")
            return
        
        group_id = parts[1]
        punishment_type = parts[2]
        
        if check_group_exists(group_id):
            await set_punishment_type(query, group_id, punishment_type)
        else:
            await query.answer("Группа не найдена")
            
    # Посмотреть пользователей из белого списка
    elif query.data.startswith("view_whitelist:"):
        group_id = query.data.split(":", 1)[1]
        if check_group_exists(group_id):
            # Кнопка возврата
            keyboard = [[InlineKeyboardButton("« Назад", callback_data=f"whitelist_settings:{group_id}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            if not WHITELIST:
                await safe_edit_message(query, 'Текущий белый список пуст', reply_markup=reply_markup)
            else:
                whitelist_text = ', '.join(map(str, WHITELIST))
                await safe_edit_message(query, f'ID пользователей в текущем белом списке:\n{whitelist_text}', reply_markup=reply_markup)
        else:
            await safe_edit_message(query, "Группа не найдена")
    
    # Настройки запрещённых слов
    elif query.data.startswith("keywords_settings:"):
        group_id = query.data.split(":", 1)[1]
        if check_group_exists(group_id):
            # Создать кнопку
            keyboard = [
                [InlineKeyboardButton("Посмотреть все запрещённые слова", callback_data=f"view_keywords:{group_id}")],
                [InlineKeyboardButton("Добавить запрещённое слово", callback_data=f"add_keyword_prompt:{group_id}")],
                [InlineKeyboardButton("« Назад", callback_data=f"manage_group:{group_id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # получить количество запрещённых слов в этой группе
            keyword_count = 0
            if group_id in KEYWORDS:
                keyword_count = len(KEYWORDS[group_id])
            
            await safe_edit_message(
                query,
                f"Запрещённые слова\n\n"
                f"Сейчас всего {keyword_count} запрещённых слов\n"
                f"Сообщения с запрещёнными словами будут автоматически удалены",
                reply_markup=reply_markup
            )
        else:
            await safe_edit_message(query, "Группа не найдена")
            
    # Посмотреть все запрещённые слова
    elif query.data.startswith("view_keywords:"):
        group_id = query.data.split(":", 1)[1]
        if check_group_exists(group_id):
            # Кнопка возврата
            keyboard = [[InlineKeyboardButton("« Назад", callback_data=f"keywords_settings:{group_id}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            if group_id not in KEYWORDS or not KEYWORDS[group_id]:
                await safe_edit_message(query, 'Сейчас запрещённые слова не заданы', reply_markup=reply_markup)
            else:
                keywords_text = ', '.join(KEYWORDS[group_id])
                await safe_edit_message(query, f'Текущий список запрещённых слов:\n{keywords_text}', reply_markup=reply_markup)
        else:
            await safe_edit_message(query, "Группа не найдена")
            
    # ответ на виртуальную операцию
    elif query.data.startswith("dummy_action:"):
        group_id = query.data.split(":", 1)[1]
        # Для функциональных кнопок показываем только уведомление о том, что функция ещё не реализована, но интерфейс не меняем
        await query.answer("Эта функция ещё не реализована")
        return
    
    # Если это callback-запрос обновления списка групп
    elif query.data == "refresh_groups":
        await refresh_groups_list(query, context)
    
    # Если это callback-запрос очистки недействительных групп
    elif query.data == "cleanup_groups":
        await cleanup_invalid_groups(query, context)
    
    # Если это callback-запрос выхода из указанной группы
    elif query.data.startswith("leave_group:"):
        parts = query.data.split(":")
        if len(parts) >= 2:
            group_id = parts[1]
            if check_group_exists(group_id):
                # получить название группы
                group_title = group_display_title(group_id, GROUPS[group_id].get("title", "Неизвестная группа"))
                
                # Создать кнопку подтверждения
                keyboard = [
                    [
                        InlineKeyboardButton("✅ Подтвердить выход", callback_data=f"confirm_leave:{group_id}"),
                        InlineKeyboardButton("❌ Отмена", callback_data=f"manage_group:{group_id}")
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                message = f"Вы уверены, что хотите, чтобы бот покинул группу <b>{group_title}</b>?\n\n<b>Внимание:</b> после выхода удалённо управлять этой группой будет невозможно, придётся снова пригласить бота в группу."
                await safe_edit_message(query, message, reply_markup=reply_markup, parse_mode="HTML")
            else:
                await query.answer("Группа не найдена")
        else:
            await query.answer("Неверная команда")

    # Подтвердить выход из группы
    elif query.data.startswith("confirm_leave:"):
        parts = query.data.split(":")
        if len(parts) >= 2:
            group_id = parts[1]
            await handle_confirm_leave(query, context, group_id)
        else:
            await query.answer("Неверная команда")

    # Удалить все запрещённые слова
    elif query.data.startswith("remove_keywords:"):
        group_id = query.data.split(":", 1)[1]
        if check_group_exists(group_id):
            # подсчитать количество запрещённых слов в этой группе
            keyword_count = 0
            if group_id in KEYWORDS:
                keyword_count = len(KEYWORDS[group_id])
                
            # Создать кнопку подтверждения
            keyboard = [
                [
                    InlineKeyboardButton("✅ Подтвердить очистку", callback_data=f"confirm_remove_keywords:{group_id}"),
                    InlineKeyboardButton("❌ Отмена", callback_data=f"antispam_settings:{group_id}")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await safe_edit_message(
                query,
                f"⚠️ Внимание: вы уверены, что хотите удалить все запрещённые слова?\n\n"
                f"Сейчас всего {keyword_count} запрещённых слов\n"
                f"Это действие удалит все запрещённые слова, и отменить его нельзя.",
                reply_markup=reply_markup
            )
        else:
            await safe_edit_message(query, "Группа не найдена")
    
    # Подтвердить удаление всех запрещённых слов
    elif query.data.startswith("confirm_remove_keywords:"):
        group_id = query.data.split(":", 1)[1]
        if check_group_exists(group_id):
            # Очистить список запрещённых слов
            if group_id in KEYWORDS:
                KEYWORDS[group_id] = []
            # Сохранить обновлённый список ключевых слов
            if save_keywords(KEYWORDS):
                # Кнопка возврата
                keyboard = [[InlineKeyboardButton("« Назад", callback_data=f"antispam_settings:{group_id}")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await safe_edit_message(query, "✅ Все запрещённые слова успешно удалены", reply_markup=reply_markup)
            else:
                # Кнопка возврата
                keyboard = [[InlineKeyboardButton("« Назад", callback_data=f"antispam_settings:{group_id}")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await safe_edit_message(query, "❌ Не удалось удалить запрещённые слова", reply_markup=reply_markup)
        else:
            await safe_edit_message(query, "Группа не найдена")
            
    # Обработка кнопки бана пользователя
    elif query.data.startswith("ban_user:"):
        # Проверка, является ли пользователь администратором
        if not is_admin(user_id):
            await query.answer("Только администратор может выполнить это действие")
            return
        
        target_user_id = query.data.split(":", 1)[1]
        chat_id = query.message.chat.id
        
        # Добавить пользователя в чёрный список
        if target_user_id not in BANNED_USERS:
            BANNED_USERS[target_user_id] = {
                'added_by': user_id,
                'added_time': datetime.datetime.now().isoformat(),
                'reason': 'Бан по предупреждению антиспама'
            }
            
            # Сохранить обновлённый чёрный список
            save_banned_users(BANNED_USERS)
            
            # Пытаемся исключить этого пользователя из текущей группы
            try:
                await context.bot.ban_chat_member(chat_id=chat_id, user_id=int(target_user_id))
                await safe_edit_message(query, f"{query.message.text}\n\n✅ Пользователь {target_user_id} забанен и исключён из группы")
            except Exception as e:
                await safe_edit_message(query, f"{query.message.text}\n\n✅ Пользователь {target_user_id} забанен, но исключить не удалось: {e}")
        else:
            await safe_edit_message(query, f"{query.message.text}\n\n⚠️ Пользователь {target_user_id} уже в чёрном списке")
    
    # Обработка кнопки снятия мута
    elif query.data.startswith("unmute_user:"):
        # Проверка, является ли пользователь администратором
        if not is_admin(user_id):
            await query.answer("Только администратор может выполнить это действие")
            return
        
        target_user_id = query.data.split(":", 1)[1]
        chat_id = query.message.chat.id
        
        # Пытаемся снять мут с пользователя
        try:
            await context.bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=int(target_user_id),
                permissions=ChatPermissions(
                    can_send_messages=True,
                    can_send_polls=True,
                    can_send_other_messages=True,
                    can_add_web_page_previews=True,
                    can_change_info=False,
                    can_invite_users=True,
                    can_pin_messages=False
                )
            )
            await safe_edit_message(query, f"{query.message.text}\n\n✅ С пользователя {target_user_id} снят мут")
        except Exception as e:
            await safe_edit_message(query, f"{query.message.text}\n\n❌ Не удалось снять мут: {e}")

    # настроить время удаления уведомления
    elif query.data.startswith("set_delete_time:"):
        parts = query.data.split(":")
        if len(parts) >= 3:
            group_id = parts[1]
            delete_time = int(parts[2])
            await set_delete_notice_time(query, group_id, delete_time)
        else:
            await query.answer("Неверная команда")

    # Изменение toggle_welcome:
    elif query.data.startswith("toggle_welcome:"):
        parts = query.data.split(":", 2)
        if len(parts) != 3:
            await query.answer("Неверное действие")
            return
        
        group_id = parts[1]
        status = parts[2]
        
        if check_group_exists(group_id):
            if status == "enable":
                GROUPS[group_id]["welcome_enabled"] = True
                await query.answer("Приветствие новых участников включено")
            else:
                GROUPS[group_id]["welcome_enabled"] = False
                await query.answer("Приветствие новых участников выключено")
            
            await save_groups()
            # Исправление: нельзя напрямую присваивать query.data
            # Создать новый объект CallbackQuery
            # а вместо этого заново вызывать callback из welcome_settings
            await query.message.edit_text(
                query.message.text,
                reply_markup=query.message.reply_markup
            )
            # Затем заново сгенерировать callback_data
            new_data = f"welcome_settings:{group_id}"
            # вызвать функцию-обработчик вручную
            query.data = new_data
            await handle_group_management(query)
        else:
            await query.answer("Группа не найдена")

async def show_groups_list(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """показать список групп"""
    try:
        global GROUPS
        keyboard = []
        
        # Проверка, пуст ли GROUPS
        if isinstance(GROUPS, dict):
            is_empty = len(GROUPS) == 0
        else:
            is_empty = not GROUPS
            
        if is_empty:
            message = "Пока не найдено ни одной группы. Убедитесь, что бот добавлен администратором группы."
            
            # Добавить кнопку обновления
            keyboard.append([InlineKeyboardButton("🔄 Обновить список групп", callback_data="refresh_groups")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            if isinstance(query, CallbackQuery):
                await safe_edit_message(query, message, reply_markup=reply_markup)
            else:
                await query.message.reply_text(message, reply_markup=reply_markup)
            return
        
        # Формирование сообщения со списком групп
        message = "Выберите группу для управления:"
        
        # Обработка в соответствии с форматом GROUPS
        if isinstance(GROUPS, dict):
            # GROUPS — это словарь (исходный формат)
            for chat_id, group_info in GROUPS.items():
                title = group_info.get('title', f'Неизвестная группа {chat_id}')
                keyboard.append([InlineKeyboardButton(group_display_title(chat_id, title), callback_data=f"manage_group:{chat_id}")])
        else:
            # GROUPS — это список
            for group in GROUPS:
                try:
                    # Если group — это объект Chat
                    if hasattr(group, 'id') and hasattr(group, 'title'):
                        group_id = group.id
                        group_title = group.title
                    # Если group — это словарь
                    elif isinstance(group, dict) and 'id' in group and 'title' in group:
                        group_id = group['id']
                        group_title = group['title']
                    else:
                        logger.warning(f"Неизвестный формат группы: {type(group)}")
                        continue
                    
                    keyboard.append([InlineKeyboardButton(group_display_title(group_id, group_title), callback_data=f"manage_group:{group_id}")])
                except Exception as e:
                    logger.error(f"Ошибка при обработке кнопки группы: {e}, группа: {group}")
                    continue
        
        # Добавить кнопку обновления
        keyboard.append([InlineKeyboardButton("🔄 Обновить список групп", callback_data="refresh_groups")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Отправка разных сообщений в зависимости от типа объекта query
        if isinstance(query, CallbackQuery):
            await safe_edit_message(query, message, reply_markup=reply_markup)
        else:
            await query.message.reply_text(message, reply_markup=reply_markup)
    except Exception as e:
        error_message = f"Не удалось показать список групп: {e}"
        logger.error(error_message)
        if isinstance(query, CallbackQuery):
            await query.answer(error_message)
        else:
            await query.message.reply_text(error_message)

async def check_groups(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Получить все группы, в которых находится бот"""
    try:
        global GROUPS
        
        # Временные переменные
        original_format = False
        original_groups_data = {"groups": {}}
        
        # Пытаемся прочитать исторические данные групп
        try:
            with open('groups.json', 'r', encoding='utf-8') as f:
                file_content = f.read().strip()
                if not file_content:
                    logger.warning("groups.json — пустой файл")
                else:
                    stored_groups = json.loads(file_content)
                    
                    # Определение исходного формата — есть вложенная структура словаря groups
                    if isinstance(stored_groups, dict) and "groups" in stored_groups:
                        original_format = True
                        original_groups_data = stored_groups
                        # Инициализировать словарь GROUPS исходными данными
                        GROUPS = {}
                        for chat_id, group_info in stored_groups["groups"].items():
                            GROUPS[chat_id] = group_info
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.warning(f"Не удалось прочитать сохранённые данные групп: {e}")
        
        # Храним ID всех полученных групп, чтобы избежать повторов
        group_ids = set(GROUPS.keys()) if isinstance(GROUPS, dict) else set()
        
        # Пытаемся получить актуальный список чатов бота
        try:
            updates = await context.bot.get_updates(offset=-1, timeout=1)
            if updates:
                for update in updates:
                    if update.message and update.message.chat:
                        chat = update.message.chat
                        if chat.type in ['group', 'supergroup']:
                            chat_id_str = str(chat.id)
                            if chat_id_str not in group_ids:
                                # Сохранять сведения о группах в формате словаря
                                if isinstance(GROUPS, dict):
                                    GROUPS[chat_id_str] = {
                                        'title': chat.title,
                                        'type': chat.type,
                                        'join_date': datetime.datetime.now().isoformat()
                                    }
                                    group_ids.add(chat_id_str)
        except Exception as e:
            logger.warning(f"Не удалось получить последнее обновление: {e}")
        
        # Пытаемся получить актуальную информацию о каждой группе
        updated_groups = {}
        for chat_id in list(GROUPS.keys()) if isinstance(GROUPS, dict) else []:
            try:
                chat = await context.bot.get_chat(int(chat_id))
                if chat.type in ['group', 'supergroup']:
                    # Обновление информации о группе с сохранением исходного join_date
                    group_info = GROUPS[chat_id].copy()
                    group_info['title'] = chat.title
                    group_info['type'] = chat.type
                    updated_groups[chat_id] = group_info
            except Exception as e:
                logger.warning(f"Не удалось получить информацию о группе {chat_id}: {e}")
                # Сохранить исходные данные
                updated_groups[chat_id] = GROUPS[chat_id]
        
        # обновить GROUPS
        if updated_groups:
            GROUPS = updated_groups
        
        # Сохранить данные групп
        try:
            # Сохранить исходный формат
            if original_format:
                output_data = {"groups": {}}
                for chat_id, group_info in GROUPS.items():
                    output_data["groups"][chat_id] = group_info
            else:
                output_data = {"groups": GROUPS}
            
            with open('groups.json', 'w', encoding='utf-8') as f:
                json.dump(output_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Не удалось сохранить данные групп: {e}")
        
        logger.info(f"Найдено {len(GROUPS)} групп")
    except Exception as e:
        logger.error(f"Ошибка при проверке группы: {e}")

async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Добавить пользователя в чёрный список"""
    user_id = update.effective_user.id
    
    # Проверка, является ли пользователь администратором
    if not is_admin(user_id):
        await update.message.reply_text('Эту команду может использовать только администратор')
        return

    if not context.args:
        await update.message.reply_text('Укажите ID пользователя для блокировки, например: /ban 123456789')
        return
    
    try:
        ban_user_id = context.args[0]
        
        # Если указано имя пользователя (начинается с @), пытаемся получить ID пользователя
        if ban_user_id.startswith('@'):
            username = ban_user_id[1:]  # Убираем символ @
            await update.message.reply_text(f'Укажите ID пользователя вместо имени {ban_user_id}: чтобы узнать ID, перешлите сообщение пользователя и используйте команду /ban')
            return
        
        ban_user_id = str(ban_user_id)  # Убедиться, что это строка
        
        if ban_user_id in BANNED_USERS:
            await update.message.reply_text(f'Пользователь {ban_user_id} уже в чёрном списке')
            return
        
        # Если это ID пользователя, добавляем в чёрный список
        BANNED_USERS[ban_user_id] = {
            'added_by': user_id,
            'added_time': datetime.datetime.now().isoformat(),
            'reason': ' '.join(context.args[1:]) if len(context.args) > 1 else 'Причина не указана'
        }
        
        # Сохранить обновлённый чёрный список
        if save_banned_users(BANNED_USERS):
            await update.message.reply_text(f'Пользователь {ban_user_id} добавлен в чёрный список')
        else:
            await update.message.reply_text(f'Пользователь {ban_user_id} добавлен в чёрный список (но не удалось сохранить)')
    except Exception as e:
        await update.message.reply_text(f'Не удалось забанить пользователя: {e}')

async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Убрать пользователя из чёрного списка"""
    user_id = update.effective_user.id
    
    # Проверка, является ли пользователь администратором
    if not is_admin(user_id):
        await update.message.reply_text('Эту команду может использовать только администратор')
        return

    if not context.args:
        await update.message.reply_text('Укажите ID пользователя, которого нужно убрать из чёрного списка, например: /unban 123456789')
        return
    
    try:
        ban_user_id = str(context.args[0])
        
        if ban_user_id not in BANNED_USERS:
            await update.message.reply_text(f'Пользователь {ban_user_id} отсутствует в чёрном списке')
            return
        
        # Удалить из чёрного списка
        del BANNED_USERS[ban_user_id]
        
        # Сохранить обновлённый чёрный список
        if save_banned_users(BANNED_USERS):
            await update.message.reply_text(f'Пользователь {ban_user_id} убран из чёрного списка')
        else:
            await update.message.reply_text(f'Пользователь {ban_user_id} убран из чёрного списка (но не удалось сохранить)')
    except Exception as e:
        await update.message.reply_text(f'Не удалось разблокировать пользователя: {e}')

async def list_banned_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Вывести все ID пользователей из чёрного списка"""
    user_id = update.effective_user.id
    
    # Проверка, является ли пользователь администратором
    if not is_admin(user_id):
        await update.message.reply_text('Эту команду может использовать только администратор')
        return
    
    if not BANNED_USERS:
        await update.message.reply_text('Текущий чёрный список пуст')
    else:
        # Формирование сообщения с подробной информацией
        message_lines = ['Пользователи в текущем чёрном списке:']
        for banned_id, info in BANNED_USERS.items():
            reason = info.get('reason', 'Причина не указана')
            added_time = info.get('added_time', 'Время неизвестно')
            message_lines.append(f'ID пользователя: {banned_id}, причина: {reason}, время добавления: {added_time}')
        
        await update.message.reply_text('\n'.join(message_lines))

async def setup_commands(application):
    """Меню команд бота"""
    commands = [
        BotCommand("start", "Запуск бота"),
        BotCommand("help", "Показать справку"),
        BotCommand("add_keyword", "Добавить одно спам-слово"),
        BotCommand("bulk_add_keywords", "Массовое добавление спам-слов, по одному в строке"),
        BotCommand("remove_keyword", "Удалить спам-слово"),
        BotCommand("list_keywords", "Вывести все спам-слова"),
        BotCommand("add_group_keyword", "Добавить ключевые слова для конкретной группы"),
        BotCommand("remove_group_keyword", "Удалить ключевые слова, специфичные для группы"),
        BotCommand("list_group_keywords", "Вывести ключевые слова конкретной группы"),
        BotCommand("list_admins", "Вывести все ID администраторов"),
        BotCommand("add_whitelist", "Добавить пользователя в белый список"),
        BotCommand("remove_whitelist", "Убрать пользователя из белого списка"),
        BotCommand("list_whitelist", "Вывести все ID пользователей из белого списка"),
        BotCommand("mute", "Замутить участника (/mute @ник 1 день)"),
        BotCommand("unmute", "Снять мут с участника (/unmute @ник)"),
        BotCommand("ban", "Забанить участника (/ban @ник 1 день)"),
        BotCommand("unban", "Снять бан с участника (/unban @ник)"),
        BotCommand("gban", "Внести в глобальный чёрный список"),
        BotCommand("gunban", "Убрать из глобального чёрного списка"),
        BotCommand("admin_add", "Выдать должность (/admin_add @ник 1-5)"),
        BotCommand("admin_remove", "Понизить должность (/admin_remove @ник 0-5)"),
        BotCommand("admins", "Список руководства"),
        BotCommand("ship", "Случайная пара (шипперим)"),
        BotCommand("whoami", "Мой профиль (/кто_я)"),
        BotCommand("whois", "Профиль участника (/кто_ты, ответом)"),
        BotCommand("commands", "Полный список всех команд"),
        BotCommand("id", "Показать свой ID и ник"),
        BotCommand("rules", "Правила чата (/rules [текст])"),
        BotCommand("fuck", "Принудить к интиму (ответом)"),
        BotCommand("hardfuck", "Жёсткий интим (ответом)"),
        BotCommand("kill", "Убить (ответом на сообщение)"),
        BotCommand("kiss", "Поцеловать (ответом на сообщение)"),
        BotCommand("hug", "Обнять (ответом на сообщение)"),
        BotCommand("handshake", "Пожать руку (ответом)"),
        BotCommand("kick_fun", "Пнуть (ответом)"),
        BotCommand("marry", "Сделать предложение руки и сердца (/marry @user)"),
        BotCommand("divorce", "Расторгнуть брак (/divorce)"),
        BotCommand("my_marriage", "Моя карточка брака (/my_marriage)"),
        BotCommand("marriages", "Список браков чата (/marriages)"),
        BotCommand("farm", "Ферма: добыча раз в 4 часа (/farm)"),
        BotCommand("nick", "Ник (/nick или /nick СЛОВО)"),
        BotCommand("greeting", "Приветствие чата (+Приветствие / -Приветствие)"),
        BotCommand("channels", "Каналы (+Каналы / -Каналы)"),
        BotCommand("say", "Опубликовать текст от бота (/say текст)"),
        BotCommand("stats", "Топ участников по сообщениям (/stats)"),
        BotCommand("list_banned", "Вывести все ID пользователей из чёрного списка"),
        BotCommand("list_violations", "Вывести записи о нарушениях всех пользователей"),
        BotCommand("clear_violation", "Сбросить нарушения пользователя"),
        BotCommand("dev_mode", "Переключить режим разработчика (только администратор)")
    ]
    
    # Меню команд бота: ставим для всех областей, чтобы список по «/» показывался и в личке, и в группах
    scopes = (BotCommandScopeDefault(), BotCommandScopeAllPrivateChats(), BotCommandScopeAllGroupChats())
    for scope in scopes:
        try:
            await application.bot.set_my_commands(commands, scope=scope)
        except Exception as e:
            if 'SCOPE' in str(e).upper():
                logger.info('Сервер не поддерживает области меню команд — оставляю меню по умолчанию')
                break
            logger.warning(f"Не удалось установить меню команд: {e}")

async def toggle_antispam_feature(query, group_id, feature_key):
    """Переключение состояния функции антиспама с обновлением интерфейса"""
    try:
        global ANTISPAM_SETTINGS
        
        # получить настройки антиспама для этой группы
        if group_id not in ANTISPAM_SETTINGS:
            ANTISPAM_SETTINGS[group_id] = DEFAULT_ANTISPAM_SETTINGS.copy()
        
        # Переключить состояние функции
        current_status = ANTISPAM_SETTINGS[group_id].get(feature_key, False)
        ANTISPAM_SETTINGS[group_id][feature_key] = not current_status
        
        # Сохранить настройки
        save_antispam_settings()
        
        # показать результат операции
        new_status = "включено" if ANTISPAM_SETTINGS[group_id][feature_key] else "выключено"
        feature_names = {
            "anti_flood": "Защита от флуда",
            "block_sensitive": "Блокировать подозрительные аккаунты",
            "block_links": "Блокировать ссылки",
            "block_channel_mask": "Блокировать сообщения от имени канала",
            "block_channel_forward": "Блокировать пересылки из каналов",
            "block_user_forward": "Блокировать пересылки от пользователей",
            "block_group_mention": "Блокировать упоминания @групп",
            "block_user_mention": "Блокировать упоминания @пользователей",
            "block_eth_address": "Блокировать адреса Ethereum",
            "clear_command_msg": "Удалять сообщение с командой",
            "block_long_msg": "Блокировать слишком длинные сообщения",
            "block_long_name": "Блокировать слишком длинные имена"
        }
        feature_name = feature_names.get(feature_key, feature_key)
        
        await query.answer(f"{feature_name}: {new_status}")
        
        # Возврат на страницу настроек антиспама
        await render_antispam_settings(query, group_id)
    except Exception as e:
        # При ошибке хотя бы сообщаем пользователю, что действие выполнено
        await query.answer(f"{feature_name}: {new_status}, но не удалось обновить интерфейс")
        logger.error(f"Не удалось переключить состояние функции: {e}")

async def render_antispam_settings(query, group_id):
    """Отобразить страницу настроек антиспама"""
    try:
        # получить настройки антиспама для этой группы
        group_settings = ANTISPAM_SETTINGS.get(group_id, DEFAULT_ANTISPAM_SETTINGS.copy())
        
        # Убедиться, что все параметры настроек присутствуют
        for key in DEFAULT_ANTISPAM_SETTINGS:
            if key not in group_settings:
                group_settings[key] = DEFAULT_ANTISPAM_SETTINGS[key]
        
        ban_time = group_settings.get("ban_time", 60)
        delete_notice_time = group_settings.get("delete_notice_time", 5)
        
        html_content = (
            f"<b>Антиспам</b>\n\n"
            f"<b>Общее наказание — мут {ban_time} минут</b>\n"
            f"<b>Удалить напоминание через {delete_notice_time} сек.</b>\n\n"
        )
        
        # Проверка состояния защиты от флуда и блокировки подозрительных аккаунтов
        flood_status = "✅" if group_settings.get("anti_flood", False) else "❌"
        sensitive_status = "✅" if group_settings.get("block_sensitive", False) else "❌"
        
        html_content += (
            f"<b>{flood_status} Защита от флуда:</b>\n"
            f"└ Пользователь за короткое время отправляет сообщения в несколько групп\n\n"
            f"<b>{sensitive_status} Блокировка подозрительных аккаунтов:</b>\n"
            f"└ Аккаунты, забаненные во всех группах за многократную отправку спама"
        )
        
        # Создать кнопку функции, отображающую ✅ или ❌ в зависимости от текущего состояния
        keyboard = []
        
        features = [
            ("anti_flood", "Защита от флуда"),
            ("block_sensitive", "Блокировать подозрительные аккаунты"),
            ("block_links", "Блокировать ссылки"),
            ("block_channel_mask", "Блокировать сообщения от имени канала"),
            ("block_channel_forward", "Блокировать пересылки из каналов"),
            ("block_user_forward", "Блокировать пересылки от пользователей"),
            ("block_group_mention", "Блокировать упоминания @групп"),
            ("block_user_mention", "Блокировать упоминания @пользователей"),
            ("block_eth_address", "Блокировать адреса Ethereum"),
            ("clear_command_msg", "Удалять сообщение с командой"),
            ("block_long_msg", "Блокировать слишком длинные сообщения"),
            ("block_long_name", "Блокировать слишком длинные имена")
        ]
        
        # добавить кнопки функций в раскладке по 2 в ряд
        for i in range(0, len(features), 2):
            row = []
            # Добавить первую функциональную кнопку
            key1, name1 = features[i]
            status1 = "✅" if group_settings.get(key1, False) else "❌"
            row.append(InlineKeyboardButton(f"{status1} {name1}", callback_data=f"toggle_antispam:{group_id}:{key1}"))
            
            # Добавить вторую функциональную кнопку (если есть)
            if i + 1 < len(features):
                key2, name2 = features[i + 1]
                status2 = "✅" if group_settings.get(key2, False) else "❌"
                row.append(InlineKeyboardButton(f"{status2} {name2}", callback_data=f"toggle_antispam:{group_id}:{key2}"))
            
            keyboard.append(row)
        
        # Добавить кнопки настроек наказаний и исключений
        keyboard.append([
            InlineKeyboardButton("⚡ Наказание", callback_data=f"punishment_settings:{group_id}"),
            InlineKeyboardButton("⚠️ Исключения", callback_data=f"whitelist_settings:{group_id}")
        ])
        
        # Добавить кнопку очистки запрещённых слов
        keyboard.append([
            InlineKeyboardButton("🗑️ Очистить запрещённые слова", callback_data=f"remove_keywords:{group_id}")
        ])
        
        # Кнопка возврата
        keyboard.append([
            InlineKeyboardButton("« Назад", callback_data=f"manage_group:{group_id}")
        ])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await safe_edit_message(query, html_content, reply_markup=reply_markup, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Не удалось отобразить настройки антиспама: {e}")
        await query.answer("Не удалось обновить интерфейс, попробуйте ещё раз")

async def render_punishment_settings(query, group_id):
    """Отобразить страницу настроек наказаний"""
    try:
        # получить настройки антиспама для этой группы
        group_settings = ANTISPAM_SETTINGS.get(group_id, DEFAULT_ANTISPAM_SETTINGS.copy())
        
        # Создать HTML-содержимое
        ban_time = group_settings.get("ban_time", 60)
        delete_notice_time = group_settings.get("delete_notice_time", 5)
        
        html_content = (
            f"<b>Антиспам</b>\n\n"
            f"<b>Общее наказание — мут {ban_time} минут</b>\n"
            f"<b>Удалить напоминание через {delete_notice_time} сек.</b>\n\n"
        )
        
        # получить текущий тип наказания
        current_punishment = group_settings.get("punishment_type", "mute")
        
        # Создать кнопки настройки наказаний
        keyboard = [
            # кнопки предупреждение/мут/исключение
            [
                InlineKeyboardButton(
                    "✅ Предупреждение" if current_punishment == "warn" else "Предупреждение", 
                    callback_data=f"set_punishment:{group_id}:warn"
                ),
                InlineKeyboardButton(
                    "✅ Мут" if current_punishment == "mute" else "Мут", 
                    callback_data=f"set_punishment:{group_id}:mute"
                ),
                InlineKeyboardButton(
                    "✅ Исключить" if current_punishment == "kick" else "Исключить", 
                    callback_data=f"set_punishment:{group_id}:kick"
                )
            ],
            # Вариант «исключить + бан»
            [
                InlineKeyboardButton(
                    "✅ Исключить + бан" if current_punishment == "kick_ban" else "❌ Исключить + бан", 
                    callback_data=f"set_punishment:{group_id}:kick_ban"
                )
            ],
            # Опция «исключать только того, кому ответили»
            [
                InlineKeyboardButton(
                    "✅ Исключать только того, кому ответили, а не нарушителя" if current_punishment == "kick_reply" else "❌ Исключать только того, кому ответили, а не нарушителя", 
                    callback_data=f"set_punishment:{group_id}:kick_reply"
                )
            ],
            # Кнопка задания длительности мута
            [
                InlineKeyboardButton("⏱ Настроить длительность мута", callback_data=f"set_ban_time_prompt:{group_id}")
            ],
            # Кнопка возврата
            [
                InlineKeyboardButton("« Назад", callback_data=f"antispam_settings:{group_id}")
            ]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await safe_edit_message(query, html_content, reply_markup=reply_markup, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Не удалось отобразить настройки наказаний: {e}")
        await query.answer("Не удалось обновить интерфейс, попробуйте ещё раз")

async def set_ban_time(query, group_id, ban_time):
    """Задать время мута и обновить интерфейс"""
    try:
        # Убедиться, что group_id есть в конфигурации
        if group_id not in ANTISPAM_SETTINGS:
            ANTISPAM_SETTINGS[group_id] = DEFAULT_ANTISPAM_SETTINGS.copy()
        
        # Обновление времени мута
        ANTISPAM_SETTINGS[group_id]["ban_time"] = ban_time
        
        # Сохранить настройки
        save_antispam_settings()
        
        # Обновление пользовательского интерфейса
        await render_punishment_settings(query, group_id)
        
    except Exception as e:
        logger.error(f"Не удалось задать время мута: {e}")
        await query.answer("Не удалось задать время мута, попробуйте ещё раз")

async def set_delete_time(query, group_id, delete_time):
    """Функция-обёртка для задания времени удаления напоминания"""
    await set_delete_notice_time(query, group_id, delete_time)

async def set_punishment_type(query, group_id, punishment_type):
    """Задать тип наказания и обновить интерфейс"""
    try:
        # Убедиться, что group_id есть в конфигурации
        if group_id not in ANTISPAM_SETTINGS:
            ANTISPAM_SETTINGS[group_id] = DEFAULT_ANTISPAM_SETTINGS.copy()
        
        # Обновление типа наказания
        ANTISPAM_SETTINGS[group_id]["punishment_type"] = punishment_type
        
        # Сохранить настройки
        save_antispam_settings()
        
        # Возврат на страницу настроек наказаний
        await render_punishment_settings(query, group_id)
        
    except Exception as e:
        logger.error(f"Не удалось задать тип наказания: {e}")
        await query.answer("Не удалось задать тип наказания, попробуйте ещё раз")

async def refresh_groups_list(query, context):
    """Обновить список групп"""
    await query.answer("Обновляем список групп, подождите...")
    
    # принудительно обновить список групп
    await check_groups(context)
    
    # Показать список групп сразу
    await show_groups_list(query, context)

async def cleanup_invalid_groups(query, context):
    """Очистить недействительные группы"""
    try:
        global GROUPS
        
        # Сначала показать уведомление об очистке
        await safe_edit_message(query, "Проверяем и удаляем недействительные группы, подождите...")
        
        # Временное хранилище активных групп
        valid_groups = {}
        invalid_count = 0
        
        # Проверка каждой группы
        if isinstance(GROUPS, dict):
            for chat_id, group_info in list(GROUPS.items()):
                try:
                    # Пытаемся получить информацию о группе
                    chat = await context.bot.get_chat(int(chat_id))
                    # Проверка, всё ещё находится ли бот в группе
                    member = await context.bot.get_chat_member(int(chat_id), context.bot.id)
                    if member.status not in ['left', 'kicked']:
                        # Обновление информации о группе
                        valid_groups[chat_id] = {
                            'title': chat.title,
                            'type': chat.type,
                            'join_date': group_info.get('join_date', datetime.datetime.now().isoformat())
                        }
                    else:
                        invalid_count += 1
                        logger.info(f"Бот покинул группу {chat_id}")
                except Exception as e:
                    invalid_count += 1
                    logger.warning(f"Не удалось получить информацию о группе {chat_id}: {e}")
        
        # обновить GROUPS
        GROUPS = valid_groups
        
        # Сохранить обновлённый список групп
        try:
            output_data = {"groups": {}}
            for chat_id, group_info in GROUPS.items():
                output_data["groups"][chat_id] = group_info
            
            with open('groups.json', 'w', encoding='utf-8') as f:
                json.dump(output_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Не удалось сохранить данные групп: {e}")
        
        # показать результат очистки
        temp_text = f"Очистка завершена: удалено недействительных групп — {invalid_count}, всего групп сейчас — {len(GROUPS)}."
        await safe_edit_message(query, temp_text)
        
        # показать список групп
        await show_groups_list(query, context)
    except Exception as e:
        logger.error(f"Не удалось очистить недействительные группы: {e}")
        await query.answer("Не удалось очистить недействительные группы, попробуйте ещё раз")

async def mute_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Замутить пользователя на время - /jy @имя_пользователя секунды"""
    chat = update.effective_chat
    sender = update.effective_user
    
    # Проверка нахождения в группе
    if chat.type not in ['group', 'supergroup']:
        await update.message.reply_text('Эту команду можно использовать только в группе')
        return
    
    # Проверка, есть ли у отправившего команду пользователя права администратора
    try:
        sender_member = await context.bot.get_chat_member(chat.id, sender.id)
        if sender_member.status not in ['creator', 'administrator'] and not is_admin(sender.id):
            await update.message.reply_text('Эту команду может использовать только администратор или администратор группы')
            return
    except Exception as e:
        await update.message.reply_text(f'Не удалось проверить права: {e}')
        return
    
    # разобрать параметры команды
    if not context.args or len(context.args) < 1:
        await update.message.reply_text('Использование: /jy @имя_пользователя секунды\nНапример: /jy @имя_пользователя 300')
        return
    
    # получить целевого пользователя
    target_username_or_id = context.args[0]
    target_user = None
    
    # получить время мута (секунды)
    mute_seconds = 300  # по умолчанию 5 минут
    if len(context.args) >= 2:
        try:
            mute_seconds = int(context.args[1])
            if mute_seconds <= 0:
                await update.message.reply_text('Время мута должно быть больше 0 секунд')
                return
        except ValueError:
            await update.message.reply_text('Неверное время, используется время по умолчанию — 5 минут')
            mute_seconds = 300
    
    try:
        # Поиск ID целевого пользователя
        # Способ 1: проверить, является ли сообщение ответом кому-либо
        if update.message.reply_to_message and update.message.reply_to_message.from_user:
            target_user = update.message.reply_to_message.from_user
        
        # Способ 2: проверить, указан ли ID пользователя напрямую
        elif target_username_or_id.isdigit():
            try:
                user_id = int(target_username_or_id)
                chat_member = await context.bot.get_chat_member(chat.id, user_id)
                target_user = chat_member.user
            except Exception as e:
                await update.message.reply_text(f'Не удалось найти пользователя по ID: {e}')
        
        # Способ 3: если указано имя пользователя, попробовать получить пользователя через get_chat_member
        else:
            username = target_username_or_id
            if username.startswith('@'):
                username = username[1:]  # Убираем символ @
            
            try:
                # К сожалению, Telegram API не поддерживает поиск пользователя напрямую по имени пользователя
                # Нам нужно, чтобы пользователь дал более точную информацию
                await update.message.reply_text(
                    f'Найти пользователя напрямую по имени @{username} невозможно.\n'
                    f'Используйте один из следующих способов:\n'
                    f'1. Ответьте на сообщение этого пользователя и используйте команду\n'
                    f'2. Укажите ID пользователя, а не имя пользователя\n'
                    f'3. Перешлите сюда удостоверение личности пользователя, затем ответьте'
                )
                return
            except Exception as e:
                await update.message.reply_text(f'Не удалось найти пользователя: {e}')
                return
        
        if not target_user:
            await update.message.reply_text(f'Пользователь не найден. Ответьте на сообщение этого пользователя или укажите ID пользователя напрямую')
            return
        
        # вычислить время окончания мута
        until_date = datetime.datetime.now() + datetime.timedelta(seconds=mute_seconds)
        
        # выполнить мут
        await context.bot.restrict_chat_member(
            chat_id=chat.id,
            user_id=target_user.id,
            permissions=ChatPermissions(
                can_send_messages=False,
                can_send_polls=False,
                can_send_other_messages=False,
                can_add_web_page_previews=False,
                can_change_info=False,
                can_invite_users=False,
                can_pin_messages=False
            ),
            until_date=until_date
        )
        
        # Форматирование отображения времени
        duration_text = ""
        if mute_seconds < 60:
            duration_text = f"{mute_seconds} сек."
        elif mute_seconds < 3600:
            duration_text = f"{mute_seconds // 60} мин."
        else:
            hours = mute_seconds // 3600
            minutes = (mute_seconds % 3600) // 60
            duration_text = f"{hours} ч."
            if minutes > 0:
                duration_text += f"{minutes} мин."
        
        await update.message.reply_text(f'Пользователь {target_user.first_name} (@{target_user.username or ""}) получил мут {duration_text}')
    except Exception as e:
        await update.message.reply_text(f'Не удалось замутить пользователя: {e}')

async def unmute_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Снять мут с пользователя - /jc @имя_пользователя"""
    chat = update.effective_chat
    sender = update.effective_user
    
    # Проверка нахождения в группе
    if chat.type not in ['group', 'supergroup']:
        await update.message.reply_text('Эту команду можно использовать только в группе')
        return
    
    # Проверка, есть ли у отправившего команду пользователя права администратора
    try:
        sender_member = await context.bot.get_chat_member(chat.id, sender.id)
        if sender_member.status not in ['creator', 'administrator'] and not is_admin(sender.id):
            await update.message.reply_text('Эту команду может использовать только администратор или администратор группы')
            return
    except Exception as e:
        await update.message.reply_text(f'Не удалось проверить права: {e}')
        return
    
    # разобрать параметры команды
    if not context.args or len(context.args) < 1:
        await update.message.reply_text('Использование: /jc @имя_пользователя\nНапример: /jc @имя_пользователя')
        return
    
    # получить целевого пользователя
    target_username_or_id = context.args[0]
    target_user = None
    
    try:
        # Поиск ID целевого пользователя
        # Способ 1: проверить, является ли сообщение ответом кому-либо
        if update.message.reply_to_message and update.message.reply_to_message.from_user:
            target_user = update.message.reply_to_message.from_user
        
        # Способ 2: проверить, указан ли ID пользователя напрямую
        elif target_username_or_id.isdigit():
            try:
                user_id = int(target_username_or_id)
                chat_member = await context.bot.get_chat_member(chat.id, user_id)
                target_user = chat_member.user
            except Exception as e:
                await update.message.reply_text(f'Не удалось найти пользователя по ID: {e}')
        
        # Способ 3: если указано имя пользователя, попробовать получить пользователя через get_chat_member
        else:
            username = target_username_or_id
            if username.startswith('@'):
                username = username[1:]  # Убираем символ @
            
            try:
                # К сожалению, Telegram API не поддерживает поиск пользователя напрямую по имени пользователя
                # Нам нужно, чтобы пользователь дал более точную информацию
                await update.message.reply_text(
                    f'Найти пользователя напрямую по имени @{username} невозможно.\n'
                    f'Используйте один из следующих способов:\n'
                    f'1. Ответьте на сообщение этого пользователя и используйте команду\n'
                    f'2. Укажите ID пользователя, а не имя пользователя\n'
                    f'3. Перешлите сюда удостоверение личности пользователя, затем ответьте'
                )
                return
            except Exception as e:
                await update.message.reply_text(f'Не удалось найти пользователя: {e}')
                return
        
        if not target_user:
            await update.message.reply_text(f'Пользователь не найден. Ответьте на сообщение этого пользователя или укажите ID пользователя напрямую')
            return
        
        # снять мут
        await context.bot.restrict_chat_member(
            chat_id=chat.id,
            user_id=target_user.id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
                can_change_info=False,
                can_invite_users=True,
                can_pin_messages=False
            )
        )
        
        await update.message.reply_text(f'С пользователя {target_user.first_name} (@{target_user.username or ""}) снят мут')
    except Exception as e:
        await update.message.reply_text(f'Не удалось снять мут. {api_error_hint(e)}')

async def set_delete_notice_time(query, group_id, delete_time):
    """Задать время удаления напоминания и обновить интерфейс"""
    try:
        # Убедиться, что group_id есть в конфигурации
        if group_id not in ANTISPAM_SETTINGS:
            ANTISPAM_SETTINGS[group_id] = DEFAULT_ANTISPAM_SETTINGS.copy()
        
        # Обновление времени напоминания об удалении
        ANTISPAM_SETTINGS[group_id]["delete_notice_time"] = delete_time
        
        # Сохранить настройки
        save_antispam_settings()
        
        # Обновление пользовательского интерфейса
        await render_punishment_settings(query, group_id)
        
    except Exception as e:
        logger.error(f"Не удалось настроить время удаления уведомления: {e}")
        await query.answer("Не удалось задать время удаления напоминания, попробуйте ещё раз")

async def manage_group(query, group_id):
    """показать параметры управления группами"""
    try:
        global GROUPS
        
        # Поиск группы
        found = False
        group_title = None
        
        # Проверка, является ли GROUPS словарём или списком
        if isinstance(GROUPS, dict):
            # старый формат, GROUPS — это словарь
            if str(group_id) in GROUPS:
                found = True
                group_info = GROUPS[str(group_id)]
                group_title = group_info.get('title', f'Неизвестная группа {group_id}')
        else:
            # новый формат, GROUPS — это список
            for group in GROUPS:
                # Если group — это объект Chat
                if hasattr(group, 'id') and str(group.id) == str(group_id):
                    found = True
                    group_title = group.title
                    break
                # Если group — это словарь
                elif isinstance(group, dict) and 'id' in group and str(group['id']) == str(group_id):
                    found = True
                    group_title = group.get('title', f'Неизвестная группа {group_id}')
                    break
        
        if not found:
            await query.answer("группа не найдена, пожалуйста, вернитесь и выберите заново")
            return
        
        # опции управления группой - сетка 2x2
        keyboard = [
            [
                InlineKeyboardButton("Информация о группе", callback_data=f"group_info:{group_id}"),
                InlineKeyboardButton("Приветствие", callback_data=f"welcome_settings:{group_id}")
            ],
            [
                InlineKeyboardButton("Антиспам", callback_data=f"antispam_settings:{group_id}"),
                InlineKeyboardButton("Запрещённые слова", callback_data=f"keywords_settings:{group_id}")
            ],
            [
                InlineKeyboardButton("🚪 Покинуть группу", callback_data=f"leave_group:{group_id}")
            ],
            [
                InlineKeyboardButton("« Назад", callback_data="manage_groups")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await safe_edit_message(query, f"Управление группой: {group_display_title(group_id, group_title)}, выберите параметр для изменения", reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Не удалось управлять группой: {e}")
        await query.answer("Не удалось получить опции управления группой, попробуйте ещё раз")

async def handle_confirm_leave(query, context, group_id):
    """Логика подтверждения выхода из группы"""
    global GROUPS
    
    # Поиск группы
    found = False
    found_group_title = None
    
    # Проверка, является ли GROUPS словарём или списком
    if isinstance(GROUPS, dict):
        # старый формат, GROUPS — это словарь
        if str(group_id) in GROUPS:
            found = True
            found_group_title = GROUPS[str(group_id)].get('title', f'Неизвестная группа {group_id}')
    else:
        # новый формат, GROUPS — это список
        for group in GROUPS:
            if hasattr(group, 'id') and str(group.id) == str(group_id):
                found = True
                found_group_title = group.title
                break
            elif isinstance(group, dict) and 'id' in group and str(group['id']) == str(group_id):
                found = True
                found_group_title = group.get('title', f'Неизвестная группа {group_id}')
                break
    
    if found and found_group_title:
        try:
            # Пытаемся выйти из группы
            await context.bot.leave_chat(group_id)
            
            # удалить данные группы
            if isinstance(GROUPS, dict):
                # Если GROUPS — это словарь, удаляем через del
                if str(group_id) in GROUPS:
                    del GROUPS[str(group_id)]
            else:
                # Если GROUPS — это список, фильтруем через списковое включение
                GROUPS = [g for g in GROUPS if not (
                    (hasattr(g, 'id') and str(g.id) == str(group_id)) or 
                    (isinstance(g, dict) and 'id' in g and str(g['id']) == str(group_id))
                )]
            
            # Сохранить обновлённый список групп
            try:
                if isinstance(GROUPS, dict):
                    # Сохранить в исходном формате
                    output_data = {"groups": {}}
                    for chat_id, group_info in GROUPS.items():
                        output_data["groups"][chat_id] = group_info
                else:
                    # Сохранить в формате списка
                    output_data = {"groups": {}}
                    for group in GROUPS:
                        if hasattr(group, 'id') and hasattr(group, 'title'):
                            chat_id = str(group.id)
                            output_data["groups"][chat_id] = {
                                'title': group.title,
                                'type': getattr(group, 'type', 'unknown'),
                                'join_date': datetime.datetime.now().isoformat()
                            }
                
                with open('groups.json', 'w', encoding='utf-8') as f:
                    json.dump(output_data, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.error(f"Не удалось сохранить данные групп: {e}")
            
            # Обновление интерфейса
            message = f"Бот успешно покинул группу <b>{group_display_title(group_id, found_group_title)}</b>"
            await safe_edit_message(query, message, parse_mode="HTML")
            
            # Через 1 секунду возвращаемся к списку групп
            await asyncio.sleep(1)
            await show_groups_list(query, context)
            
        except Exception as e:
            logger.error(f"Не удалось покинуть группу: {e}")
            error_message = f"Не удалось покинуть группу: {e}"
            await safe_edit_message(query, error_message)
    else:
        await query.answer("Группа не найдена")

def check_group_exists(group_id):
    """Проверка существования группы"""
    if isinstance(GROUPS, dict):
        return str(group_id) in GROUPS
    else:
        for group in GROUPS:
            # Если group — это объект Chat
            if hasattr(group, 'id') and str(group.id) == str(group_id):
                return True
            # Если group — это словарь
            elif isinstance(group, dict) and 'id' in group and str(group['id']) == str(group_id):
                return True
        return False

# Добавить ключевые слова для конкретной группы
async def add_group_keyword(query, group_id, keyword):
    """Добавить ключевые слова для конкретной группы"""
    global KEYWORDS
    
    # Убедиться, что ID группы есть в KEYWORDS
    if group_id not in KEYWORDS:
        KEYWORDS[group_id] = []
    
    # Проверка, существует ли уже ключевое слово
    if keyword in KEYWORDS[group_id]:
        await query.answer(f"Ключевое слово «{keyword}» уже существует")
        return False
    
    # Добавить ключевое слово
    KEYWORDS[group_id].append(keyword)
    
    # Сохранить обновлённый список ключевых слов
    success = save_keywords(SPAM_KEYWORDS)
    if success:
        await query.answer(f"Ключевое слово добавлено: '{keyword}'")
    else:
        await query.answer(f"Ключевое слово добавлено: '{keyword}' (но не удалось сохранить)")
    
    return success

# Удалить ключевые слова конкретной группы
async def remove_group_keyword(query, group_id, keyword):
    """Удалить ключевые слова конкретной группы"""
    global KEYWORDS
    
    # Убедиться, что ID группы есть в KEYWORDS
    if group_id not in KEYWORDS:
        await query.answer("В этой группе не задано ни одного ключевого слова")
        return False
    
    # Проверка наличия ключевого слова
    if keyword not in KEYWORDS[group_id]:
        await query.answer(f"Ключевое слово «{keyword}» не существует")
        return False
    
    # Удалить ключевое слово
    KEYWORDS[group_id].remove(keyword)
    
    # Сохранить обновлённый список ключевых слов
    success = save_keywords(SPAM_KEYWORDS)
    if success:
        await query.answer(f"Ключевое слово удалено: '{keyword}'")
    else:
        await query.answer(f"Ключевое слово удалено: '{keyword}' (но не удалось сохранить)")
    
    return success

# Подсказка для добавления ключевого слова
async def add_keyword_prompt(query, group_id):
    """показать подсказку по добавлению ключевого слова"""
    keyboard = [[InlineKeyboardButton("« Назад", callback_data=f"keywords_settings:{group_id}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await safe_edit_message(
        query,
        "Отправьте в личном чате /add_keyword ключевое слово, чтобы добавить глобальное ключевое слово,\n"
        "Или используйте /add_group_keyword ID группы ключевое слово, чтобы добавить ключевое слово для конкретной группы.\n\n"
        "Например: /add_keyword спам\n"
        f"Или: /add_group_keyword {group_id} спам",
        reply_markup=reply_markup
    )

# подсказка об удалении ключевого слова
async def remove_keyword_prompt(query, group_id):
    """показать подсказку по удалению ключевого слова"""
    keyboard = [[InlineKeyboardButton("« Назад", callback_data=f"keywords_settings:{group_id}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await safe_edit_message(
        query,
        "Отправьте в личном чате /remove_keyword ключевое слово, чтобы удалить глобальное ключевое слово,\n"
        "Или используйте /remove_group_keyword ID группы ключевое слово, чтобы удалить ключевое слово для конкретной группы.\n\n"
        "Например: /remove_keyword спам\n"
        f"Или: /remove_group_keyword {group_id} спам",
        reply_markup=reply_markup
    )

# Добавить обработчик команды ключевых слов группы
async def add_group_keyword_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Добавить ключевые слова для конкретной группы"""
    user_id = update.effective_user.id
    
    # Проверка, является ли пользователь администратором
    if not is_admin(user_id):
        await update.message.reply_text('Эту команду может использовать только администратор')
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text('Укажите ID группы и ключевое слово для добавления, например: /add_group_keyword -1001234567890 казино')
        return
    
    group_id = context.args[0]
    keyword = context.args[1]
    
    # Убедиться, что ID группы есть в GROUPS
    if not check_group_exists(group_id):
        await update.message.reply_text(f'группа не найдена: {group_id}')
        return
    
    # Убедиться, что ID группы есть в KEYWORDS
    if group_id not in KEYWORDS:
        KEYWORDS[group_id] = []
    
    # Проверка, существует ли уже ключевое слово
    if keyword in KEYWORDS[group_id]:
        await update.message.reply_text(f'Ключевое слово {keyword} уже существует в группе {group_id}')
        return
    
    # Добавить ключевое слово
    KEYWORDS[group_id].append(keyword)
    
    # Сохранить обновлённый список ключевых слов
    if save_keywords(SPAM_KEYWORDS):
        await update.message.reply_text(f'Ключевое слово {keyword} добавлено в группу {group_id}')
    else:
        await update.message.reply_text(f'Ключевое слово {keyword} добавлено в группу {group_id} (но не удалось сохранить)')

# обработчик команды удаления ключевого слова группы
async def remove_group_keyword_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Удалить ключевые слова, специфичные для группы"""
    user_id = update.effective_user.id
    
    # Проверка, является ли пользователь администратором
    if not is_admin(user_id):
        await update.message.reply_text('Эту команду может использовать только администратор')
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text('Укажите ID группы и ключевое слово для удаления, например: /remove_group_keyword -1001234567890 казино')
        return
    
    group_id = context.args[0]
    keyword = context.args[1]
    
    # Убедиться, что ID группы есть в KEYWORDS
    if group_id not in KEYWORDS:
        await update.message.reply_text(f'В группе {group_id} не задано ни одного ключевого слова')
        return
    
    # Проверка наличия ключевого слова
    if keyword not in KEYWORDS[group_id]:
        await update.message.reply_text(f'Ключевое слово {keyword} не существует в группе {group_id}')
        return
    
    # удалить ключевое слово
    KEYWORDS[group_id].remove(keyword)
    
    # Сохранить обновлённый список ключевых слов
    if save_keywords(SPAM_KEYWORDS):
        await update.message.reply_text(f'Ключевое слово {keyword} удалено из группы {group_id}')
    else:
        await update.message.reply_text(f'Ключевое слово {keyword} удалено из группы {group_id} (но не удалось сохранить)')

# Функция-обработчик команды вывода ключевых слов группы
async def list_group_keywords_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Вывести ключевые слова конкретной группы"""
    user_id = update.effective_user.id
    
    # Проверка, является ли пользователь администратором
    if not is_admin(user_id):
        await update.message.reply_text('Эту команду может использовать только администратор')
        return

    if not context.args:
        await update.message.reply_text('Укажите ID группы, например: /list_group_keywords -1001234567890')
        return
    
    group_id = context.args[0]
    
    # Убедиться, что ID группы есть в KEYWORDS
    if group_id not in KEYWORDS or not KEYWORDS[group_id]:
        await update.message.reply_text(f'В группе {group_id} не задано ни одного ключевого слова')
        return
    
    # Вывести список ключевых слов
    keywords_text = ', '.join(KEYWORDS[group_id])
    await update.message.reply_text(f'Список ключевых слов группы {group_id}:\n{keywords_text}')

# Сбросить нарушения пользователя
async def clear_violation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Сбросить нарушения пользователя"""
    user_id = update.effective_user.id
    
    # Проверка, является ли пользователь администратором
    if not is_admin(user_id):
        await update.message.reply_text('Эту команду может использовать только администратор')
        return

    if not context.args:
        await update.message.reply_text('Укажите ID пользователя, у которого нужно очистить нарушения, например: /clear_violation 123456789')
        return
    
    try:
        target_user_id = str(context.args[0])
        
        # Проверка, есть ли у пользователя записи о нарушениях
        if target_user_id not in VIOLATION_COUNTS:
            await update.message.reply_text(f'У пользователя {target_user_id} нет нарушений')
            return
        
        # Сбросить нарушения
        del VIOLATION_COUNTS[target_user_id]
        
        # Сохранить обновлённые записи о нарушениях
        if save_violation_counts(VIOLATION_COUNTS):
            await update.message.reply_text(f'Счётчик нарушений пользователя {target_user_id} очищен')
        else:
            await update.message.reply_text(f'Счётчик нарушений пользователя {target_user_id} очищен (но не удалось сохранить)')
    except Exception as e:
        await update.message.reply_text(f'Не удалось сбросить нарушения: {e}')

# Вывести все записи о нарушениях
async def list_violations(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Вывести записи о нарушениях всех пользователей"""
    user_id = update.effective_user.id
    
    # Проверка, является ли пользователь администратором
    if not is_admin(user_id):
        await update.message.reply_text('Эту команду может использовать только администратор')
        return
    
    if not VIOLATION_COUNTS:
        await update.message.reply_text('Сейчас нарушений нет')
    else:
        # Формирование сообщения с записями о нарушениях
        violations_list = []
        for v_user_id, count in VIOLATION_COUNTS.items():
            violations_list.append(f'ID пользователя: {v_user_id}, число нарушений: {count}')
        
        message = 'Список нарушений:\n' + '\n'.join(violations_list)
        await update.message.reply_text(message)

# ==================== ДОЛЖНОСТИ, ПРОФИЛИ И РАЗВЛЕКАТЕЛЬНЫЕ КОМАНДЫ ====================
# Добавлено поверх основной логики:
#   /admin_add, /admin_remove, /admins  — система должностей (1-5)
#   /mute, /unmute, /ban, /unban        — наказания со сроком словами («1 день», «2 часа»)
#   /кто_я, /кто_ты                     — карточка профиля участника
#   /убить, /поцеловать, /обнять, /трахнуть, /выебать, /шипперим — команды-развлечения

import random

# Создатель бота — самая высокая ступень. Ник и ID можно переопределить в .env
CREATOR_USERNAME = os.getenv('CREATOR_USERNAME', 'v1nk_molik').lstrip('@')
# Файл с ID создателя: заполняется сам, чтобы его команды работали
CREATOR_FILE = 'creator.json'


def load_creator_id():
    """Загрузить ID создателя бота из файла"""
    if os.path.exists(CREATOR_FILE):
        try:
            with open(CREATOR_FILE, 'r', encoding='utf-8') as file:
                return int(json.load(file).get('id') or 0)
        except Exception as e:
            logger.error(f"Не удалось загрузить ID создателя бота: {e}")
    return 0


def pin_creator(user):
    """Запомнить ID создателя бота (по нику из .env), чтобы ему работали команды"""
    global CREATOR_ID
    if user is None or not is_creator(user):
        return False
    if CREATOR_ID != user.id:
        CREATOR_ID = user.id
        try:
            with open(CREATOR_FILE, 'w', encoding='utf-8') as file:
                json.dump({'id': user.id, 'username': user.username}, file, ensure_ascii=False, indent=2)
            logger.info(f"Создатель бота определён: @{user.username} ({user.id})")
        except Exception as e:
            logger.error(f"Не удалось сохранить ID создателя бота: {e}")
    return True


try:
    CREATOR_ID = int(os.getenv('CREATOR_ID') or 0)
except ValueError:
    CREATOR_ID = 0
CREATOR_ID = CREATOR_ID or load_creator_id()

# Файлы новых данных
RANKS_FILE = 'ranks.json'
USERS_FILE = 'users.json'

# Должности: 5 — старшая, 0 — без должности, 6 — создатель бота
RANK_NAMES = {
    6: 'Создатель бота',
    5: 'Владелец',
    4: 'Администратор',
    3: 'Ст.Модератор',
    2: 'Модератор',
    1: 'Мл.Модератор',
    0: 'Простой участник',
}
RANK_STARS = {5: '✨✨✨✨✨', 4: '✨✨✨✨', 3: '✨✨✨', 2: '✨✨', 1: '✨', 0: '·'}
CREATOR_STAR = '🌌'

# Должности: {user_id: {'rank': 1-5, 'name': ..., 'username': ...}}
RANKS = {}
# Статистика: {chat_id: {user_id: {'name', 'username', 'first_seen', 'last_active', 'days', 'total'}}}
USERS = {}
_USERS_DIRTY = 0
# Браки: {chat_id: {user_id: {'partner_id': int, 'since': iso}}}
MARRIAGES = {}
# Ники: {chat_id: {user_id: nick}}
NICKS = {}
# Приветствия: {chat_id: template}
GREETINGS = {}
# Ферма: {user_id: {'last': iso, 'total': int, 'stars': int, 'yield': int}}
FARM = {}
# Репутация: {user_id: {'given': int, 'received': int}}
REPUTATION = {}
# Разрешено ли каналам писать в чате: {chat_id: bool} — по умолчанию False
CHANNELS_ALLOWED = {}
# Ожидающие предложения руки и сердца: {chat_id: {target_id: proposer_id}}
PENDING_MARRIAGE = {}


def load_ranks():
    """Загрузить должности из JSON-файла"""
    if os.path.exists(RANKS_FILE):
        try:
            with open(RANKS_FILE, 'r', encoding='utf-8') as file:
                data = json.load(file)
                if isinstance(data, dict):
                    return {str(k): v for k, v in data.items()}
        except Exception as e:
            logger.error(f"Не удалось загрузить должности: {e}")
    return {}


def save_ranks():
    """Сохранить должности в JSON-файл"""
    try:
        with open(RANKS_FILE, 'w', encoding='utf-8') as file:
            json.dump(RANKS, file, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Не удалось сохранить должности: {e}")
        return False


def load_users():
    """Загрузить статистику участников из JSON-файла"""
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, 'r', encoding='utf-8') as file:
                data = json.load(file)
                if isinstance(data, dict):
                    return data
        except Exception as e:
            logger.error(f"Не удалось загрузить статистику участников: {e}")
    return {}


def save_users(force=False):
    """Сохранить статистику участников (пишем сразу, чтобы данные не терялись)"""
    global _USERS_DIRTY
    _USERS_DIRTY += 1
    try:
        with open(USERS_FILE, 'w', encoding='utf-8') as file:
            json.dump(USERS, file, ensure_ascii=False, indent=2)
        _USERS_DIRTY = 0
        return True
    except Exception as e:
        logger.error(f"Не удалось сохранить статистику участников: {e}")
        _USERS_DIRTY = 0
        return False


# Последние писавшие в чате: {chat_id: [user_id, ...]}
RECENT_FILE = 'recent.json'
RECENT = {}
# Кэш сообщений {chat_id: [{'id', 'user_id', 'text'}]} — переживает перезапуск бота
MESSAGES_FILE = 'messages.json'


def load_messages():
    """Загрузить кэш сообщений"""
    if os.path.exists(MESSAGES_FILE):
        try:
            with open(MESSAGES_FILE, 'r', encoding='utf-8') as file:
                data = json.load(file)
                if isinstance(data, dict):
                    return data
        except Exception as e:
            logger.error(f"Не удалось загрузить кэш сообщений: {e}")
    return {}


def save_messages():
    """Сохранить кэш сообщений"""
    try:
        with open(MESSAGES_FILE, 'w', encoding='utf-8') as file:
            json.dump(CHAT_MESSAGES, file, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Не удалось сохранить кэш сообщений: {e}")
        return False


def load_recent():
    """Загрузить список последних писавших"""
    if os.path.exists(RECENT_FILE):
        try:
            with open(RECENT_FILE, 'r', encoding='utf-8') as file:
                data = json.load(file)
                if isinstance(data, dict):
                    return data
        except Exception as e:
            logger.error(f"Не удалось загрузить список последних писавших: {e}")
    return {}


def save_recent():
    """Сохранить список последних писавших"""
    try:
        with open(RECENT_FILE, 'w', encoding='utf-8') as file:
            json.dump(RECENT, file, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Не удалось сохранить список последних писавших: {e}")
        return False


def load_marriages():
    """Загрузить браки: {chat_id: {user_id: {partner_id, since}}}"""
    if os.path.exists(MARRIAGES_FILE):
        try:
            with open(MARRIAGES_FILE, 'r', encoding='utf-8') as file:
                data = json.load(file)
                if isinstance(data, dict):
                    return {str(k): {str(uid): dict(v) for uid, v in v.items() if isinstance(v, dict)}
                            for k, v in data.items() if isinstance(v, dict)}
        except Exception as e:
            logger.error(f"Не удалось загрузить браки: {e}")
    return {}


def save_marriages():
    """Сохранить браки в JSON"""
    try:
        with open(MARRIAGES_FILE, 'w', encoding='utf-8') as file:
            json.dump(MARRIAGES, file, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Не удалось сохранить браки: {e}")
        return False


def load_nicks():
    """Загрузить ники: {chat_id: {user_id: nick}}"""
    if os.path.exists(NICKS_FILE):
        try:
            with open(NICKS_FILE, 'r', encoding='utf-8') as file:
                data = json.load(file)
                if isinstance(data, dict):
                    return {str(k): {str(uid): str(v) for uid, v in v.items()}
                            for k, v in data.items() if isinstance(v, dict)}
        except Exception as e:
            logger.error(f"Не удалось загрузить ники: {e}")
    return {}


def save_nicks():
    """Сохранить ники в JSON"""
    try:
        with open(NICKS_FILE, 'w', encoding='utf-8') as file:
            json.dump(NICKS, file, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Не удалось сохранить ники: {e}")
        return False


def load_greetings():
    """Загрузить приветствия чатов: {chat_id: template}"""
    if os.path.exists(GREETINGS_FILE):
        try:
            with open(GREETINGS_FILE, 'r', encoding='utf-8') as file:
                data = json.load(file)
                if isinstance(data, dict):
                    return {str(k): str(v) for k, v in data.items()}
        except Exception as e:
            logger.error(f"Не удалось загрузить приветствия: {e}")
    return {}


def save_greetings():
    """Сохранить приветствия в JSON"""
    try:
        with open(GREETINGS_FILE, 'w', encoding='utf-8') as file:
            json.dump(GREETINGS, file, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Не удалось сохранить приветствия: {e}")
        return False


def load_farm():
    """Загрузить данные фермы: {user_id: {last, total, stars, yield_}}"""
    if os.path.exists(FARM_FILE):
        try:
            with open(FARM_FILE, 'r', encoding='utf-8') as file:
                data = json.load(file)
                if isinstance(data, dict):
                    return {str(uid): dict(info) for uid, info in data.items() if isinstance(info, dict)}
        except Exception as e:
            logger.error(f"Не удалось загрузить ферму: {e}")
    return {}


def save_farm():
    """Сохранить данные фермы"""
    try:
        with open(FARM_FILE, 'w', encoding='utf-8') as file:
            json.dump(FARM, file, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Не удалось сохранить ферму: {e}")
        return False


def load_reputation():
    """Загрузить репутацию: {user_id: {given, received}}"""
    if os.path.exists(REPUTATION_FILE):
        try:
            with open(REPUTATION_FILE, 'r', encoding='utf-8') as file:
                data = json.load(file)
                if isinstance(data, dict):
                    return {str(uid): dict(info) for uid, info in data.items() if isinstance(info, dict)}
        except Exception as e:
            logger.error(f"Не удалось загрузить репутацию: {e}")
    return {}


def save_reputation():
    """Сохранить репутацию"""
    try:
        with open(REPUTATION_FILE, 'w', encoding='utf-8') as file:
            json.dump(REPUTATION, file, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Не удалось сохранить репутацию: {e}")
        return False


def load_channels_allowed():
    """Загрузить настройки каналов: {chat_id: bool}"""
    if os.path.exists(CHANNEL_SETTINGS_FILE):
        try:
            with open(CHANNEL_SETTINGS_FILE, 'r', encoding='utf-8') as file:
                data = json.load(file)
                if isinstance(data, dict):
                    return {str(k): bool(v) for k, v in data.items()}
        except Exception as e:
            logger.error(f"Не удалось загрузить настройки каналов: {e}")
    return {}


def save_channels_allowed():
    """Сохранить настройки каналов"""
    try:
        with open(CHANNEL_SETTINGS_FILE, 'w', encoding='utf-8') as file:
            json.dump(CHANNELS_ALLOWED, file, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Не удалось сохранить настройки каналов: {e}")
        return False


def remember_author(chat_id, user_id):
    """Запомнить, кто писал последним"""
    key = str(chat_id)
    queue = RECENT.get(key)
    if not isinstance(queue, list):
        queue = []
        RECENT[key] = queue
    uid = str(user_id)
    if uid in queue:
        queue.remove(uid)
    queue.append(uid)
    del queue[:-200]


# Последние сообщения чата: нужны, чтобы находить автора по цитате в ответе
CHAT_MESSAGES = {}
MESSAGE_CACHE_LIMIT = 500

# Отдельный словарь message_id → user_id (до 2000 записей на чат).
# Нужен потому, что DropGram НЕ присылает from в reply_to_message,
# и CHAT_MESSAGES (с текстом) ограничен 500 записями.
MESSAGE_AUTHORS = {}  # {chat_id: {message_id_str: user_id_str}}


def remember_message_author(chat_id, message_id, user_id):
    """Запомнить автора сообщения по message_id (отдельно от CHAT_MESSAGES)"""
    if not message_id or user_id is None:
        return
    key = str(chat_id)
    authors = MESSAGE_AUTHORS.setdefault(key, {})
    authors[str(message_id)] = str(user_id)
    if len(authors) > 2000:
        # Удаляем старые записи (по числовому message_id)
        sorted_keys = sorted(authors.keys(), key=lambda k: int(k) if str(k).isdigit() else 0)
        for k in sorted_keys[:1000]:
            del authors[k]


def remember_message(chat_id, message):
    """Запомнить сообщение чата (автор + текст), включая сообщения ботов"""
    if message is None:
        return
    author_id = message.from_user.id if message.from_user is not None else None
    if author_id is None and getattr(message, 'sender_chat', None) is not None:
        author_id = message.sender_chat.id
    if author_id is None:
        return
    # Запоминаем автора в отдельном большом словаре (до 2000 записей)
    remember_message_author(chat_id, message.message_id, author_id)
    queue = CHAT_MESSAGES.setdefault(str(chat_id), [])
    queue.append({
        'id': message.message_id,
        'user_id': author_id,
        'text': (message.text or message.caption or '')[:200],
    })
    del queue[:-MESSAGE_CACHE_LIMIT]
    save_messages()


def find_author_by_text(chat_id, text):
    """Найти автора сообщения по тексту цитаты"""
    needle = ' '.join(str(text).split()).lower()
    if not needle:
        return None
    for item in reversed(CHAT_MESSAGES.get(str(chat_id), [])):
        haystack = ' '.join(str(item.get('text', '')).split()).lower()
        if not haystack:
            continue
        if haystack == needle or needle in haystack or haystack in needle:
            return int(item['user_id'])
    return None


def find_author_by_message_id(chat_id, message_id):
    """Найти автора сообщения по его id (сервер часто не присылает автора в ответе)"""
    if not message_id:
        return None
    # Сначала ищем в большом словаре MESSAGE_AUTHORS (до 2000 записей)
    uid = MESSAGE_AUTHORS.get(str(chat_id), {}).get(str(message_id))
    if uid is not None:
        try:
            return int(uid)
        except (TypeError, ValueError):
            pass
    # Затем в CHAT_MESSAGES (с текстом, до 500 записей)
    for item in reversed(CHAT_MESSAGES.get(str(chat_id), [])):
        if item.get('id') == message_id:
            try:
                return int(item['user_id'])
            except (TypeError, ValueError):
                return None
    return None


TRANSLIT_MAP = str.maketrans({
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e', 'ж': 'zh',
    'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o',
    'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u', 'ф': 'f', 'х': 'h', 'ц': 'ts',
    'ч': 'ch', 'ш': 'sh', 'щ': 'sch', 'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu',
    'я': 'ya',
})


def normalize_nick(text):
    """Привести ник к латинице: @Мехура и @mehura станут одинаковыми"""
    return str(text or '').lstrip('@').strip().lower().translate(TRANSLIT_MAP)


def edit_distance(first, second):
    """Расстояние Левенштейна для похожих ников"""
    if first == second:
        return 0
    previous = list(range(len(second) + 1))
    for i, char_a in enumerate(first, 1):
        current = [i]
        for j, char_b in enumerate(second, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1,
                               previous[j - 1] + (char_a != char_b)))
        previous = current
    return previous[-1]


def user_link(user_id, name=None, username=None):
    """Имя пользователя ссылкой на профиль (нужен parse_mode=HTML)"""
    if name:
        label = html_escape(name)
    elif username:
        label = html_escape('@' + str(username).lstrip('@'))
    else:
        label = f'ID {user_id}'
    return f'<a href="tg://user?id={user_id}">{label}</a>'


def label_for(chat_id, user_id, context=None):
    """Как показать пользователя по его ID"""
    if context is not None and user_id == context.bot.id:
        return f'@{context.bot.username}'
    info = USERS.get(str(chat_id), {}).get(str(user_id), {})
    return user_link(user_id, info.get('name'), info.get('username'))


RANKS = load_ranks()
USERS = load_users()
RECENT = load_recent()
CHAT_MESSAGES = load_messages()
MARRIAGES = load_marriages()
NICKS = load_nicks()
GREETINGS = load_greetings()
FARM = load_farm()
REPUTATION = load_reputation()
CHANNELS_ALLOWED = load_channels_allowed()


def plural_ru(number, one, two, five):
    """Русская форма слова для числа: 1 день, 2 дня, 5 дней"""
    n = abs(int(number)) % 100
    if 11 <= n <= 14:
        return five
    n %= 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return two
    return five


def user_mention(user_id, name=None, username=None):
    """Как показать пользователя в тексте"""
    if username:
        return '@' + str(username).lstrip('@')
    return name or f'ID {user_id}'


def rank_of(user_id):
    """Должность пользователя (0 — без должности)"""
    info = RANKS.get(str(user_id))
    if not isinstance(info, dict):
        return 0
    try:
        return int(info.get('rank', 0))
    except (TypeError, ValueError):
        return 0


def is_creator(user):
    """Проверка, что пользователь — создатель бота"""
    if user is None:
        return False
    if CREATOR_ID and user.id == CREATOR_ID:
        return True
    username = (getattr(user, 'username', None) or '').lstrip('@').lower()
    return bool(username) and username == CREATOR_USERNAME.lower()


def can_manage_ranks(user):
    """Кто может выдавать должности: создатель бота, админы из .env и Владельцы"""
    return is_creator(user) or is_admin(user.id) or rank_of(user.id) >= 5


async def can_moderate(update, context):
    """Кто может использовать /mute, /ban и т.п.: персонал бота или админ группы"""
    user = update.effective_user
    if user is None:
        return False
    if is_creator(user) or is_admin(user.id) or rank_of(user.id) >= 1:
        return True
    try:
        member = await context.bot.get_chat_member(update.effective_chat.id, user.id)
        return member.status in ('creator', 'administrator')
    except Exception as e:
        logger.error(f"Не удалось проверить права пользователя: {e}")
        return False


def set_rank(user_id, level, name=None, username=None):
    """Назначить должность (0 — снять)"""
    key = str(user_id)
    if level <= 0:
        RANKS.pop(key, None)
    else:
        info = RANKS.get(key) if isinstance(RANKS.get(key), dict) else {}
        info['rank'] = level
        if name:
            info['name'] = name
        if username:
            info['username'] = str(username).lstrip('@')
        RANKS[key] = info
    return save_ranks()


def resolve_user_arg(chat_id, text):
    """Найти пользователя по @нику, имени или ID: в этом чате, потом во всех известных"""
    if not text:
        return None, None
    text = text.strip()
    if text.lstrip('-').isdigit():
        uid = str(int(text))
        info = {}
        for known_uid, known_info in iter_known_users(chat_id):
            if known_uid == uid:
                info = known_info
                break
        return int(uid), user_mention(uid, info.get('name'), info.get('username'))
    wanted = text.lstrip('@').strip().lower()
    if not wanted:
        return None, None
    wanted_lat = normalize_nick(wanted)
    fuzzy = []
    for uid, info in iter_known_users(chat_id):
        username = str(info.get('username', '')).strip().lower()
        name = str(info.get('name', '')).strip().lower()
        for value in (username, name):
            if not value:
                continue
            value_lat = normalize_nick(value)
            if value == wanted or value_lat == wanted_lat:
                return int(uid), user_mention(uid, info.get('name'), info.get('username'))
            if value.startswith(wanted) or value_lat.startswith(wanted_lat):
                fuzzy.append((1, int(uid), info))
                break
            if len(wanted_lat) >= 4 and edit_distance(wanted_lat, value_lat) <= 2:
                fuzzy.append((2, int(uid), info))
                break
    if fuzzy:
        fuzzy.sort(key=lambda item: item[0])
        best_score = fuzzy[0][0]
        best = [item for item in fuzzy if item[0] == best_score]
        if len(best) == 1 or best_score == 1:
            uid, info = best[0][1], best[0][2]
            return uid, user_mention(uid, info.get('name'), info.get('username'))
    return None, None


def iter_known_users(chat_id):
    """Все, кого знает бот: сначала участники этого чата, потом остальные"""
    seen = set()
    pools = [USERS.get(str(chat_id), {})]
    pools.extend(other for other in USERS.values() if isinstance(other, dict))
    for pool in pools:
        for uid, info in pool.items():
            if not str(uid).isdigit() or uid in seen or not isinstance(info, dict):
                continue
            seen.add(uid)
            yield uid, info


# Срок: от 1 минуты до 10 лет. Порядок важен — «мес» должен проверяться раньше «м»
DURATION_UNITS = (
    (('месяц', 'мес', 'mth', 'mo'), 30 * 86400),
    (('мин', 'м', 'm'), 60),
    (('час', 'ч', 'h'), 3600),
    (('день', 'дня', 'дней', 'дню', 'дни', 'дн', 'сут', 'д', 'd'), 86400),
    (('недел', 'нед', 'н', 'w'), 7 * 86400),
    (('год', 'года', 'году', 'годов', 'лет', 'г', 'л', 'y', 'yr'), 365 * 86400),
    (('сек', 'с', 's'), 1),
)


def parse_duration(text):
    """Разобрать срок: «1 минута» … «10 лет». None — навсегда"""
    if not text:
        return None
    text = str(text).strip().lower().replace('ё', 'е').replace(',', '.')
    if any(word in text for word in ('навсегда', 'всегда', 'бессрочн', 'перманент', 'forever')):
        return None
    total = 0.0
    found = False
    bare_numbers = []
    for raw_number, word in re.findall(r'(\d+(?:\.\d+)?)\s*([а-яa-z]*)', text):
        amount = float(raw_number)
        if amount <= 0:
            continue
        seconds = None
        for prefixes, unit_seconds in DURATION_UNITS:
            if word.startswith(prefixes):
                seconds = unit_seconds
                break
        if seconds is None:
            bare_numbers.append(amount)
            continue
        total += amount * seconds
        found = True
    if not found and bare_numbers:
        # число без единицы измерения считаем минутами: /mute @ник 10 -> 10 минут
        total = bare_numbers[0] * 60
        found = True
    if not found or total <= 0:
        return None
    return int(total)


def human_duration(seconds):
    """Срок словами: 3600 -> «1 час»"""
    if not seconds:
        return 'навсегда'
    units = (
        ('год', 'года', 'лет', 365 * 86400),
        ('месяц', 'месяца', 'месяцев', 30 * 86400),
        ('неделю', 'недели', 'недель', 7 * 86400),
        ('день', 'дня', 'дней', 86400),
        ('час', 'часа', 'часов', 3600),
        ('минуту', 'минуты', 'минут', 60),
        ('секунду', 'секунды', 'секунд', 1),
    )
    parts = []
    left = int(seconds)
    for one, two, five, size in units:
        if left >= size:
            amount, left = divmod(left, size)
            parts.append(f"{amount} {plural_ru(amount, one, two, five)}")
    return ' '.join(parts) if parts else 'меньше минуты'


def parse_dt(value):
    """Разобрать дату из файла статистики"""
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def human_ago(moment):
    """«5 минут назад», «2 дня назад», «3 месяца назад»"""
    if moment is None:
        return 'неизвестно'
    seconds = int((datetime.datetime.now() - moment).total_seconds())
    if seconds < 60:
        return 'только что'
    if seconds < 3600:
        amount = seconds // 60
        return f"{amount} {plural_ru(amount, 'минуту', 'минуты', 'минут')} назад"
    if seconds < 86400:
        amount = seconds // 3600
        return f"{amount} {plural_ru(amount, 'час', 'часа', 'часов')} назад"
    days = seconds // 86400
    if days < 31:
        return f"{days} {plural_ru(days, 'день', 'дня', 'дней')} назад"
    months = days // 30
    if months < 12:
        return f"{months} {plural_ru(months, 'месяц', 'месяца', 'месяцев')} назад"
    years = days // 365
    return f"{years} {plural_ru(years, 'год', 'года', 'лет')} назад"


def activity_stats(info):
    """Активность: сегодня | за неделю | за месяц | всего"""
    days = info.get('days') if isinstance(info, dict) else None
    if not isinstance(days, dict):
        days = {}
    today = datetime.date.today()
    day_count = 0
    week_count = 0
    month_count = 0
    for day, count in days.items():
        try:
            when = datetime.date.fromisoformat(day)
        except (TypeError, ValueError):
            continue
        try:
            count = int(count)
        except (TypeError, ValueError):
            continue
        if when == today:
            day_count += count
        if 0 <= (today - when).days < 7:
            week_count += count
        if 0 <= (today - when).days < 30:
            month_count += count
    total = info.get('total') if isinstance(info, dict) else 0
    try:
        total = int(total)
    except (TypeError, ValueError):
        total = sum(int(c) for c in days.values() if str(c).isdigit())
    return day_count, week_count, month_count, total


async def target_from_update(update, context):
    """Кого наказываем: автор сообщения, на которое ответили, либо @ник/ID в аргументах"""
    message = update.message
    args = list(context.args or [])
    reply = message.reply_to_message
    if reply is not None and reply.from_user is not None:
        user = reply.from_user
        return user.id, user_mention(user.id, user.first_name, user.username), args
    if not args:
        return None, None, []
    target_id, label = resolve_user_arg(update.effective_chat.id, args[0])
    if target_id is None and args[0].lstrip('-').isdigit():
        target_id = int(args[0])
        label = f'ID {target_id}'
    return target_id, label, args[1:]


async def make_invite_link(context, chat_id):
    """Одноразовая ссылка-приглашение в чат"""
    try:
        link = await context.bot.create_chat_invite_link(chat_id=chat_id, member_limit=1)
        return link.invite_link
    except Exception as e:
        logger.warning(f"Не удалось создать ссылку-приглашение: {e}")
        return None


async def notify_user(context, user_id, text):
    """Написать пользователю в личку (может быть недоступно)"""
    try:
        await context.bot.send_message(chat_id=user_id, text=text)
        return True
    except Exception as e:
        logger.warning(f"Не удалось написать пользователю {user_id}: {e}")
        return False


def api_error_hint(error):
    """Понятное объяснение ошибки Bot API (сервер DropGram урезан)"""
    text = str(error)
    if 'METHOD_NOT_FOUND' in text:
        return ('Сервер DropGram не реализует этот метод Bot API, поэтому мут и бан тут невозможны. '
                'Бот умеет писать сообщения, вести должности, профили и правила.')
    if 'CHAT_ID_INVALID' in text:
        return ('Сервер DropGram не умеет удалять и редактировать сообщения в группах '
                '(в личке работает) — это ограничение сервера.')
    if 'not enough rights' in text.lower() or 'CHAT_ADMIN_REQUIRED' in text:
        return 'У бота нет прав администратора в этом чате — выдай их, и команда сработает.'
    if 'USER_NOT_PARTICIPANT' in text:
        return 'Этот пользователь не состоит в чате.'
    if 'CHAT_WRITE_FORBIDDEN' in text:
        return 'Боту запрещено писать в этот чат — проверь права.'
    return f'Сервер ответил: {text}'


MUTE_PERMISSIONS = dict(
    can_send_messages=False, can_send_polls=False, can_send_other_messages=False,
    can_add_web_page_previews=False, can_change_info=False, can_invite_users=False,
    can_pin_messages=False,
)
UNMUTE_PERMISSIONS = dict(
    can_send_messages=True, can_send_polls=True, can_send_other_messages=True,
    can_add_web_page_previews=True, can_change_info=False, can_invite_users=True,
    can_pin_messages=False,
)


async def cmd_mute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Замутить участника: /mute @ник 1 день (по умолчанию — навсегда)"""
    chat = update.effective_chat
    if chat.type not in ('group', 'supergroup'):
        await update.message.reply_text(
            'Эта команда работает только в группе. Добавь меня в группу (мой ID: '
            f'{context.bot.id}) и выдай права администратора — потом напиши команду уже там.')
        return
    if not await can_moderate(update, context):
        await update.message.reply_text('Недостаточно прав для этой команды')
        return
    target_id, label, rest = await target_from_update(update, context)
    if target_id is None:
        await update.message.reply_text(
            'Не понял, кого мутить. Ответь на сообщение участника или укажи @ник либо ID.')
        return
    seconds = parse_duration(' '.join(rest))
    kwargs = dict(chat_id=chat.id, user_id=target_id,
                  permissions=ChatPermissions(**MUTE_PERMISSIONS))
    if seconds:
        kwargs['until_date'] = datetime.datetime.now() + datetime.timedelta(seconds=seconds)
    try:
        await context.bot.restrict_chat_member(**kwargs)
    except Exception as e:
        await update.message.reply_text(f'Не удалось замутить. {api_error_hint(e)}')
        return
    if seconds:
        await update.message.reply_text(f'🔇 {label} — мут на {human_duration(seconds)}')
    else:
        await update.message.reply_text(f'🔇 {label} — мут навсегда')


async def cmd_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Снять мут: /unmute @ник (участнику придёт сообщение в личку)"""
    chat = update.effective_chat
    if chat.type not in ('group', 'supergroup'):
        await update.message.reply_text(
            'Эта команда работает только в группе. Добавь меня в группу (мой ID: '
            f'{context.bot.id}) и выдай права администратора — потом напиши команду уже там.')
        return
    if not await can_moderate(update, context):
        await update.message.reply_text('Недостаточно прав для этой команды')
        return
    target_id, label, _rest = await target_from_update(update, context)
    if target_id is None:
        await update.message.reply_text(
            'Не понял, с кого снять мут. Ответь на сообщение участника или укажи @ник либо ID.')
        return
    try:
        await context.bot.restrict_chat_member(chat_id=chat.id, user_id=target_id,
                                               permissions=ChatPermissions(**UNMUTE_PERMISSIONS))
    except Exception as e:
        await update.message.reply_text(f'Не удалось снять мут. {api_error_hint(e)}')
        return
    sent = await notify_user(context, target_id,
                             f'🔊 С вас снят мут в чате «{chat.title}».')
    tail = ' (написал ему в личку)' if sent else ' (в личку написать не удалось)'
    await update.message.reply_text(f'🔊 {label} — мут снят{tail}')


async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Забанить участника: /ban @ник 1 день (по умолчанию — навсегда)"""
    chat = update.effective_chat
    if chat.type not in ('group', 'supergroup'):
        await update.message.reply_text(
            'Эта команда работает только в группе. Добавь меня в группу (мой ID: '
            f'{context.bot.id}) и выдай права администратора — потом напиши команду уже там.')
        return
    if not await can_moderate(update, context):
        await update.message.reply_text('Недостаточно прав для этой команды')
        return
    target_id, label, rest = await target_from_update(update, context)
    if target_id is None:
        await update.message.reply_text(
            'Не понял, кого банить. Ответь на сообщение участника или укажи @ник либо ID.')
        return
    seconds = parse_duration(' '.join(rest))
    kwargs = dict(chat_id=chat.id, user_id=target_id)
    if seconds:
        kwargs['until_date'] = datetime.datetime.now() + datetime.timedelta(seconds=seconds)
    try:
        await context.bot.ban_chat_member(**kwargs)
    except Exception as e:
        await update.message.reply_text(f'Не удалось забанить. {api_error_hint(e)}')
        return
    if seconds:
        await update.message.reply_text(f'🚫 {label} — бан на {human_duration(seconds)}')
    else:
        await update.message.reply_text(f'🚫 {label} — бан навсегда')


async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Снять бан: /unban @ник (участнику придёт сообщение со ссылкой на чат)"""
    chat = update.effective_chat
    if chat.type not in ('group', 'supergroup'):
        await update.message.reply_text(
            'Эта команда работает только в группе. Добавь меня в группу (мой ID: '
            f'{context.bot.id}) и выдай права администратора — потом напиши команду уже там.')
        return
    if not await can_moderate(update, context):
        await update.message.reply_text('Недостаточно прав для этой команды')
        return
    target_id, label, _rest = await target_from_update(update, context)
    if target_id is None:
        await update.message.reply_text(
            'Не понял, кого разбанить. Ответь на сообщение участника или укажи @ник либо ID.')
        return
    try:
        await context.bot.unban_chat_member(chat_id=chat.id, user_id=target_id, only_if_banned=True)
        await context.bot.restrict_chat_member(chat_id=chat.id, user_id=target_id,
                                               permissions=ChatPermissions(**UNMUTE_PERMISSIONS))
    except Exception as e:
        await update.message.reply_text(f'Не удалось снять бан. {api_error_hint(e)}')
        return
    link = await make_invite_link(context, chat.id)
    text = f'✅ С вас снят бан в чате «{chat.title}».'
    if link:
        text += f'\nСсылка, чтобы вернуться: {link}'
    sent = await notify_user(context, target_id, text)
    tail = ' (написал ему в личку со ссылкой)' if sent else ' (в личку написать не удалось)'
    await update.message.reply_text(f'✅ {label} — бан снят{tail}')


async def parse_rank_args(update, context):
    """Разобрать аргументы должности: кому и какой уровень.
    Поддерживаем: /admin_add @ник 3, /admin_add 123456789 3, ответом на сообщение.
    """
    message = update.message
    args = list(context.args or [])
    reply = message.reply_to_message
    if reply is not None:
        # Сначала сам объект from_user, потом поиск по message_id (сервер часто не присылает автора)
        user = reply.from_user
        if user is None:
            found_id = find_author_by_message_id(update.effective_chat.id, getattr(reply, 'message_id', None))
            if found_id is not None:
                info = USERS.get(str(update.effective_chat.id), {}).get(str(found_id), {})
                return found_id, user_mention(found_id, info.get('name'), info.get('username')), (args[0] if args else None)
        if user is not None:
            return (user.id, user_mention(user.id, user.first_name, user.username),
                    (args[0] if args else None))
    if len(args) >= 2:
        # первый аргумент — это либо @ник, либо числовой ID; второй — уровень 0-5
        target_id, label = resolve_user_arg(update.effective_chat.id, args[0])
        if target_id is None and args[0].lstrip('-').isdigit():
            target_id, label = int(args[0]), f'ID {args[0]}'
        return target_id, label, args[1]
    if len(args) == 1:
        # цель не указана — попросим @ник
        return None, None, args[0]
    return None, None, None


async def cmd_admin_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Выдать должность: /admin_add @ник 1-5 (или ответом на сообщение)"""
    if not can_manage_ranks(update.effective_user):
        await update.message.reply_text('Выдавать должности может создатель бота, Владелец или админ из .env')
        return
    target_id, label, level_raw = await parse_rank_args(update, context)
    level = int(level_raw) if str(level_raw).isdigit() else None
    if level is None or not 1 <= level <= 5:
        await update.message.reply_text(
            'Формат: /admin_add @ник 1-5 (или ответом на сообщение).\n'
            '5 — Владелец, 4 — Администратор, 3 — Ст.Модератор, 2 — Модератор, 1 — Мл.Модератор.\n'
            'Пример: /admin_add @crazy 4')
        return
    if target_id is None:
        await ask_rank_target(update, context, level)
        return
    info = USERS.get(str(update.effective_chat.id), {}).get(str(target_id), {})
    set_rank(target_id, level, name=info.get('name'), username=info.get('username'))
    await update.message.reply_text(
        f'{RANK_STARS[level]} {label} — новая должность: {RANK_NAMES[level]}')


async def cmd_admin_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Понизить должность: /admin_remove @ник 0-5 (0 — снять совсем)"""
    if not can_manage_ranks(update.effective_user):
        await update.message.reply_text('Менять должности может создатель бота, Владелец или админ из .env')
        return
    target_id, label, level_raw = await parse_rank_args(update, context)
    level = int(level_raw) if str(level_raw).isdigit() else None
    if level is None or not 0 <= level <= 5:
        await update.message.reply_text(
            'Формат: /admin_remove @ник 0-5 (или ответом на сообщение).\n'
            '0 — снять должность полностью, 1-5 — понизить до этой должности.\n'
            'Пример: /admin_remove @crazy 0')
        return
    if target_id is None:
        await ask_rank_target(update, context, level)
        return
    info = USERS.get(str(update.effective_chat.id), {}).get(str(target_id), {})
    set_rank(target_id, level, name=info.get('name'), username=info.get('username'))
    if level == 0:
        await update.message.reply_text(f'· {label} — должность снята')
    else:
        await update.message.reply_text(
            f'{RANK_STARS[level]} {label} — новая должность: {RANK_NAMES[level]}')


async def cmd_admins(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Список руководства: /admins"""
    lines = [f'{CREATOR_STAR} СОЗДАТЕЛЬ БОТА ', f'{CREATOR_STAR} @{CREATOR_USERNAME}', '']
    for level in (5, 4, 3, 2, 1):
        names = []
        for uid, info in RANKS.items():
            if rank_of(uid) != level or not isinstance(info, dict):
                continue
            names.append(user_mention(uid, info.get('name'), info.get('username')))
        names.sort()
        lines.append(f'{RANK_STARS[level]} {RANK_NAMES[level]}')
        lines.append('💥 ' + (', '.join(names) if names else '—'))
        lines.append('')
    await update.message.reply_text('\n'.join(lines).strip())


def html_escape(text):
    """Экранирование текста для parse_mode=HTML"""
    return str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


class SimpleUser:
    """Данные участника, когда объект User от Telegram недоступен"""

    def __init__(self, user_id, name=None, username=None, is_bot=False):
        self.id = user_id
        self.first_name = name or f'ID {user_id}'
        self.last_name = ''
        self.username = username
        self.is_bot = is_bot


async def fetch_user(context, user_id, chat_id):
    """Данные пользователя: сначала из Telegram, потом из статистики бота"""
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        if getattr(member, 'user', None) is not None:
            return member.user
    except Exception as e:
        logger.info(f"Не удалось получить данные пользователя {user_id}: {e}")
    known = USERS.get(str(chat_id), {}).get(str(user_id), {})
    return SimpleUser(user_id, known.get('name'), known.get('username'))


def previous_author(chat_id, author_id=None):
    """Последний, кто писал в чат до нас (кроме нас самих)"""
    for user_id in reversed(RECENT.get(str(chat_id), [])):
        if author_id is not None and str(user_id) == str(author_id):
            continue
        info = USERS.get(str(chat_id), {}).get(str(user_id), {})
        try:
            return int(user_id), user_mention(user_id, info.get('name'), info.get('username'))
        except (TypeError, ValueError):
            continue
    return None, None


async def fun_target(message, chat_id, context=None):
    """Кому адресована команда: ответ, external_reply, @ник или текстовая ссылка"""
    if message is None:
        return None, None
    reply = message.reply_to_message
    if reply is not None:
        if reply.from_user is not None:
            user = reply.from_user
            return user.id, user_link(user.id, user.first_name, user.username)
        sender_chat = getattr(reply, 'sender_chat', None)
        if sender_chat is not None:
            return sender_chat.id, html_escape(sender_chat.title or f'ID {sender_chat.id}')
        # сервер не прислал автора в ответе — ищем по id или тексту сообщения
        found_id = find_author_by_message_id(chat_id, getattr(reply, 'message_id', None))
        if found_id is None:
            found_id = find_author_by_text(
                chat_id, getattr(reply, 'text', None) or getattr(reply, 'caption', None) or '')
        if found_id is not None:
            return found_id, label_for(chat_id, found_id, context)
        # Кэш-мисс: DropGram не присылает from в reply_to_message, и сообщение
        # может отсутствовать в кэше. previous_author тут НЕЛЬЗЯ использовать —
        # он возвращает случайного последнего писавшего, а не автора ответа.
        logger.info(f'fun_target: автор ответа не найден (message_id={getattr(reply, "message_id", None)}, кэш-мисс)')
        return None, None
    external = getattr(message, 'external_reply', None)
    origin = getattr(external, 'origin', None) if external is not None else None
    sender = getattr(origin, 'sender_user', None) if origin is not None else None
    if sender is not None:
        return sender.id, user_link(sender.id, sender.first_name, sender.username)
    for entity in (message.entities or []):
        if entity.type == 'text_mention' and getattr(entity, 'user', None) is not None:
            user = entity.user
            return user.id, user_link(user.id, user.first_name, user.username)
    for arg in (message.text or '').split()[1:]:
        user_id, label = resolve_user_arg(chat_id, arg)
        if user_id:
            return user_id, label_for(chat_id, user_id, context)
    # цель — сам бот (@имя_бота)
    if context is not None:
        bot_username = (getattr(context.bot, 'username', '') or '').lower()
        for arg in (message.text or '').split()[1:]:
            if bot_username and arg.lstrip('@').lower() == bot_username:
                return context.bot.id, f'@{context.bot.username}'
    # сервер не прислал ответ, но мог прислать цитату — ищем автора по тексту
    quote = getattr(message, 'quote', None)
    quote_text = (getattr(quote, 'text', None) or '').strip()
    if quote_text:
        found_id = find_author_by_text(chat_id, quote_text)
        if found_id is not None:
            info = USERS.get(str(chat_id), {}).get(str(found_id), {})
            return found_id, user_mention(found_id, info.get('name'), info.get('username'))
    logger.info(f"Адресат команды не определён: {message.to_dict()}")
    return None, None


def fun_candidates(chat_id, author_id=None, limit=8):
    """Последние писавшие в чате — из них выбираем цель"""
    result = []
    seen = set()
    for user_id in reversed(RECENT.get(str(chat_id), [])):
        if author_id is not None and str(user_id) == str(author_id):
            continue
        if user_id in seen or not str(user_id).isdigit():
            continue
        seen.add(user_id)
        info = USERS.get(str(chat_id), {}).get(user_id, {})
        result.append((int(user_id), user_mention(user_id, info.get('name'), info.get('username'))))
        if len(result) >= limit:
            break
    return result


def reply_diagnostics(message):
    """Что сервер прислал про ответ — для отладки"""
    parts = []
    if getattr(message, 'reply_to_message', None) is not None:
        parts.append('reply_to_message')
    if getattr(message, 'external_reply', None) is not None:
        parts.append('external_reply')
    if getattr(message, 'quote', None) is not None:
        parts.append('quote')
    extra = sorted((getattr(message, 'api_kwargs', None) or {}).keys())
    if extra:
        parts.append('api_kwargs: ' + ', '.join(extra))
    return ', '.join(parts) if parts else 'сервер вообще не прислал данных об ответе'


async def ask_fun_target(update, context, action):
    """Если ответ не передан — просим указать цель текстом"""
    await update.message.reply_text(
        'Не понял, кому это. Ответь на сообщение человека или напиши его @ник, например: '
        f'/{action} @ник')


async def ask_rank_target(update, context, level):
    """Просим указать, кому выдать должность (текстом, без кнопок)"""
    what = 'снять должность' if level == 0 else f'выдать должность: {RANK_NAMES.get(level, level)}'
    await update.message.reply_text(
        f'Не понял, кому {what}. Напиши так:\n'
        f'   /admin_add @ник {level}\n'
        f'   /admin_add 123456789 {level}   (если знаешь числовой ID)\n'
        f'Или ответь этой командой на сообщение участника.\n'
        f'ID можно узнать командой /id (можно ответом на сообщение участника).')


async def cmd_choice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Нажатие кнопки выбора цели"""
    query = update.callback_query
    await query.answer()
    # Консольный лог: кто нажал кнопку выбора цели
    _uname = f"@{query.from_user.username}" if query.from_user.username else query.from_user.id
    logger.info(f'🔘 {_uname} нажал кнопку выбора: {query.data}')
    parts = (query.data or '').split(':')
    if len(parts) != 3:
        return
    kind, first, raw_id = parts
    if first == 'cancel' or not raw_id.isdigit():
        await safe_edit_message(query, 'Отменено')
        return
    chat_id = query.message.chat.id
    user_id = int(raw_id)
    info = USERS.get(str(chat_id), {}).get(raw_id, {})
    label = user_link(user_id, info.get('name'), info.get('username'))
    author = query.from_user
    if kind == 'rank':
        if not can_manage_ranks(author):
            await safe_edit_message(query, 'Недостаточно прав')
            return
        level = int(first)
        set_rank(user_id, level, name=info.get('name'), username=info.get('username'))
        if level == 0:
            await safe_edit_message(query, f'· {label} — должность снята')
        else:
            await safe_edit_message(
                query, f'{RANK_STARS[level]} {label} — новая должность: {RANK_NAMES[level]}')
        return
    if first in ('кто_ты', 'whois'):
        target = await fetch_user(context, user_id, chat_id)
        card = await profile_card(update, context, target)
        await safe_edit_message(query, card, parse_mode='HTML')
        return
    action = FUN_ACTIONS.get(first)
    if action is None:
        await safe_edit_message(query, 'Неизвестная команда')
        return
    emoji, verb = action
    await safe_edit_message(
        query,
        f'{emoji} | {user_link(author.id, author.first_name, author.username)} {verb} {label}',
        parse_mode='HTML')


async def profile_card(update, context, user):
    """Карточка участника для /кто_я и /кто_ты"""
    chat = update.effective_chat
    info = USERS.get(str(chat.id), {}).get(str(user.id), {})
    status_emoji, status_text = '👤', 'Состоит в чате'
    if is_creator(user):
        status_emoji, status_text = '🔥', 'Создатель Бота'
    else:
        try:
            member = await context.bot.get_chat_member(chat.id, user.id)
            if member.status == 'creator':
                status_emoji, status_text = '👑', 'Владелец чата'
            elif member.status == 'administrator':
                status_emoji, status_text = '🛡', 'Админ чата'
            elif member.status in ('left', 'kicked'):
                status_emoji, status_text = '🚪', 'Покинул чат'
        except Exception as e:
            logger.warning(f"Не удалось узнать статус участника: {e}")

    level = 6 if is_creator(user) else rank_of(user.id)
    stars = '🔥' if level == 6 else RANK_STARS.get(level, '·')
    day_count, week_count, month_count, total = activity_stats(info)
    first_seen = parse_dt(info.get('first_seen'))
    last_active = parse_dt(info.get('last_active'))
    first_text = (f"{first_seen.strftime('%d.%m.%Y')} ({human_ago(first_seen)})"
                  if first_seen else 'неизвестно')
    last_text = (f"{last_active.strftime('%d.%m.%Y %H:%M')} ({human_ago(last_active)})"
                 if last_active else 'неизвестно')

    rep = REPUTATION.get(str(user.id), {}) or {}
    rep_received = int(rep.get('received', 0))
    rep_given = int(rep.get('given', 0))
    display_name = html_escape(user_mention(user.id, user.first_name, user.username))
    return (
        f'👤 Это пользователь <b>{display_name}</b>\n'
        f'{status_emoji} {status_text}\n'
        f'\n'
        f'{stars} [{level}] Ранг: {RANK_NAMES.get(level, "Простой участник")}\n'
        f'Репутация: ✨ {rep_received} | ➕ {rep_given}\n'
        f'Первое появление: {first_text}\n'
        f'Последний актив: {last_text}\n'
        f'Актив (д|н|м|весь): {day_count} | {week_count} | {month_count} | {total}'
    )


async def cmd_who_me(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Профиль самого пользователя: /кто_я"""
    card = await profile_card(update, context, update.effective_user)
    quote = random.choice(MOOD_QUOTES)
    await update.message.reply_text(f'💬 {quote}\n\n{card}', parse_mode='HTML')


async def cmd_who_you(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Профиль участника: /кто_ты ответом на сообщение или с @ником"""
    target_id, _label = await fun_target(update.message, update.effective_chat.id, context)
    if target_id is None:
        await ask_fun_target(update, context, 'кто_ты')
        return
    target = await fetch_user(context, target_id, update.effective_chat.id)
    card = await profile_card(update, context, target)
    quote = random.choice(MOOD_QUOTES)
    await update.message.reply_text(f'💬 {quote}\n\n{card}', parse_mode='HTML')


RULES_FILE = 'rules.json'
RULES = {}


def load_rules():
    """Загрузить правила чатов"""
    if os.path.exists(RULES_FILE):
        try:
            with open(RULES_FILE, 'r', encoding='utf-8') as file:
                data = json.load(file)
                if isinstance(data, dict):
                    return {str(k): v for k, v in data.items()}
        except Exception as e:
            logger.error(f"Не удалось загрузить правила чатов: {e}")
    return {}


def save_rules():
    """Сохранить правила чатов"""
    try:
        with open(RULES_FILE, 'w', encoding='utf-8') as file:
            json.dump(RULES, file, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Не удалось сохранить правила чатов: {e}")
        return False


RULES = load_rules()


async def cmd_rules(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Правила чата: /правила — показать, /правила текст — задать (владелец или админ)"""
    chat = update.effective_chat
    text = ' '.join(context.args or []).strip()
    if not text:
        rules = RULES.get(str(chat.id))
        if not rules:
            await update.message.reply_text(
                'Правила для этого чата пока не заданы. Владелец или админ может задать их так:\n'
                '/правила Не спамить, не рекламировать, уважать участников.')
            return
        await update.message.reply_text(f'📜 Правила чата:\n\n{rules}')
        return
    if not await can_moderate(update, context):
        await update.message.reply_text(
            'Задавать правила может владелец чата, администратор или персонал бота.')
        return
    RULES[str(chat.id)] = text
    save_rules()
    await update.message.reply_text('✅ Правила сохранены. Посмотреть: /правила')


# ==================== БРАКИ, НИКИ, ФЕРМА, КАНАЛЫ, ПРИВЕТСТВИЕ, РЕПУТАЦИЯ, !СКАЖИ, !ЗАКРЕП, -СМС ====================

# Кулдаун фермы — 4 часа
FARM_COOLDOWN_SECONDS = 4 * 3600

# Названия годовщин свадеб по числу полных дней
WEDDING_ANNIVERSARIES = (
    (1, 'Зелёная свадьба'),
    (2, 'Бумажная свадьба'),
    (5, 'Деревянная свадьба'),
    (10, 'Оловянная свадьба'),
    (15, 'Стеклянная свадьба'),
    (20, 'Фарфоровая свадьба'),
    (25, 'Серебряная свадьба'),
    (30, 'Жемчужная свадьба'),
    (35, 'Коралловая свадьба'),
    (40, 'Рубиновая свадьба'),
    (45, 'Сапфировая свадьба'),
    (50, 'Золотая свадьба'),
    (60, 'Бриллиантовая свадьба'),
    (70, 'Платиновая свадьба'),
    (75, 'Благодатная свадьба'),
    (80, 'Дубовая свадьба'),
    (90, 'Гранитная свадьба'),
    (100, 'Красная свадьба'),
)

# Синонимы команды брака
MARRIAGE_COMMAND_ALIASES = {'брак', 'marry', 'свадьба', 'жениться', 'поженить'}

# Слова-маркеры уважения (для ответов на чужие сообщения)
REPUTATION_KEYWORDS = {'йк', 'респект', 'f', 'уважение', 'уважуха', 'увожение',
                       'жиза', 'жыза', 'плюс', 'красавчик', 'справедливо',
                       'meine respektierung'}

# Рандомные статусы настроения для карточек профиля
MOOD_QUOTES = (
    'Этот человек сегодня явно в ударе.', 'Тут у нас любитель поговорить.',
    'Тот самый участник, без которого чат был бы тише.', 'Тут у нас душа компании.',
    'Этот человек может удивить.', 'С ним не соскучишься.',
    'Один из тех, кто формирует атмосферу чата.', 'Мастер на все руки.',
    'Тот, кто всегда найдёт, что сказать.', 'Энергия этого человека заряжает.',
)


def _marriage_partner_of(chat_id, user_id):
    """Партнёр user_id в этом чате или None"""
    record = MARRIAGES.get(str(chat_id), {}).get(str(user_id))
    if isinstance(record, dict):
        partner = record.get('partner_id')
        try:
            return int(partner) if partner is not None else None
        except (TypeError, ValueError):
            return None
    return None


def _is_married(chat_id, user_id):
    return _marriage_partner_of(chat_id, user_id) is not None


def _unregister_marriage(chat_id, user_id, partner_id):
    """Убрать брак в обе стороны"""
    MARRIAGES.get(str(chat_id), {}).pop(str(user_id), None)
    MARRIAGES.get(str(chat_id), {}).pop(str(partner_id), None)
    save_marriages()


def _greeting_text(template, user, count):
    """Подставить переменные {имя} и {ж|м|мн} в шаблон приветствия"""
    name = (user.first_name or '')
    if getattr(user, 'last_name', None):
        name = (name + ' ' + user.last_name).strip()
    if not name:
        name = f'@{user.username}' if getattr(user, 'username', None) else f'ID {user.id}'
    gender = 'ж' if count == 1 else ('м' if count == 2 else 'мн')
    return str(template).replace('{имя}', name).replace('{ж|м|мн}', gender)


def _anniversary_name(days):
    """Название годовщины по числу полных дней"""
    name = None
    for d, n in WEDDING_ANNIVERSARIES:
        if days >= d:
            name = n
        else:
            break
    return name or 'Без названия'


def _next_anniversary(days):
    """Следующая годовщина и сколько дней до неё"""
    for d, n in WEDDING_ANNIVERSARIES:
        if d > days:
            return n, d - days
    return '🏆 Столетие вместе', max(365 * 100 - days, 0)


async def _safe_answer(query, text=None, show_alert=False):
    """Ответить на callback, не падая если запрос устарел"""
    try:
        await query.answer(text, show_alert=show_alert)
    except Exception:
        pass


# ----- Брак: предложение, ответ, отказ, развод -----

async def cmd_marry(update, context):
    """Предложение «брак @юзер»; «брак да»/«брак нет» — ответ на предложение"""
    message = update.message
    if message is None or not message.text:
        return
    chat_id = update.effective_chat.id
    author = message.from_user
    parts = message.text.strip().split()
    head = parts[0].lstrip('/').split('@')[0].lower().replace('ё', 'е')

    if head in MARRIAGE_COMMAND_ALIASES and len(parts) == 2 and parts[1].lower() in (
            'да', 'yes', '+', 'нет', 'no', '-'):
        pending = PENDING_MARRIAGE.get(str(chat_id), {})
        proposer_id = pending.get(str(author.id))
        if proposer_id is None:
            await message.reply_text('Вам никто не делал предложение, либо вы уже ответили на него.')
            return
        if parts[1].lower() in ('да', 'yes', '+'):
            proposer_id_int = int(proposer_id)
            since = datetime.datetime.now().isoformat()
            MARRIAGES.setdefault(str(chat_id), {})[str(author.id)] = {
                'partner_id': proposer_id_int, 'since': since}
            MARRIAGES[str(chat_id)][str(proposer_id_int)] = {
                'partner_id': author.id, 'since': since}
            pending.pop(str(author.id), None)
            PENDING_MARRIAGE[str(chat_id)] = pending
            save_marriages()
            chat_users = USERS.get(str(chat_id), {})
            p_info = chat_users.get(str(proposer_id_int), {})
            t_info = chat_users.get(str(author.id), {})
            proposer_link = user_link(proposer_id_int, p_info.get('name'), p_info.get('username'))
            target_link = user_link(author.id, t_info.get('name'), author.username)
            await message.reply_text(
                f'💍 {target_link} принял предложение о браке.\n'
                f'👰👨‍⚖️ С сегодняшнего дня {proposer_link} и {target_link} состоят в браке.',
                parse_mode='HTML')
            return
        pending.pop(str(author.id), None)
        PENDING_MARRIAGE[str(chat_id)] = pending
        chat_users = USERS.get(str(chat_id), {})
        p_info = chat_users.get(str(proposer_id), {})
        target_link = user_link(author.id, author.first_name, author.username)
        proposer_link = user_link(int(proposer_id), p_info.get('name'), p_info.get('username'))
        await message.reply_text(
            f'💔 {proposer_link}, примите соболезнования\n'
            f'💔 {target_link} отклонил ваше предложение о вступлении в брак.',
            parse_mode='HTML')
        return

    # Обычная команда «брак @юзер» — отправляем предложение
    target_id, _label = await fun_target(message, chat_id, context)
    if target_id is None:
        await message.reply_text(
            '💍 Чтобы сделать предложение, ответьте на сообщение участника или укажите @ник: «брак @юзер».')
        return
    if target_id == author.id:
        await message.reply_text('💍 Нельзя жениться на самом себе.')
        return
    if _is_married(chat_id, author.id):
        partner_id = _marriage_partner_of(chat_id, author.id)
        chat_users = USERS.get(str(chat_id), {})
        p_info = chat_users.get(str(partner_id), {})
        partner_link = user_link(partner_id, p_info.get('name'), p_info.get('username'))
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(
            '💔 Подать на развод', callback_data=f'marry:divorce:{author.id}:{partner_id}')]])
        await message.reply_text(
            f'📝 Вы уже состоите в браке с {partner_link}\n'
            f'💬 Сначала разведитесь командой «развод». Но лучше сохраните свои отношения!',
            reply_markup=keyboard, parse_mode='HTML')
        return
    if _is_married(chat_id, target_id):
        await message.reply_text('💍 Этот участник уже состоит в браке.')
        return
    pending = PENDING_MARRIAGE.setdefault(str(chat_id), {})
    pending[str(target_id)] = author.id
    PENDING_MARRIAGE[str(chat_id)] = pending
    author_link = user_link(author.id, author.first_name, author.username)
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton('💖 Согласиться', callback_data=f'marry:yes:{author.id}:{target_id}'),
        InlineKeyboardButton('💔 Отказаться', callback_data=f'marry:no:{author.id}:{target_id}'),
    ]])
    await message.reply_text(
        f'💍 Iris | Чат-менеджер, минуточку внимания\n'
        f'{author_link} сделал вам предложение руки и сердца.\n'
        f'Принять решение можно командами «брак да» / «брак нет»',
        reply_markup=keyboard, parse_mode='HTML')


async def cmd_divorce(update, context):
    """«развод» — расторгнуть брак"""
    message = update.message
    if message is None:
        return
    chat_id = update.effective_chat.id
    author = message.from_user
    partner_id = _marriage_partner_of(chat_id, author.id)
    if partner_id is None:
        await message.reply_text('💔 Вы не состоите в браке.')
        return
    _unregister_marriage(chat_id, author.id, partner_id)
    chat_users = USERS.get(str(chat_id), {})
    p_info = chat_users.get(str(partner_id), {})
    partner_link = user_link(partner_id, p_info.get('name'), p_info.get('username'))
    author_link = user_link(author.id, author.first_name, author.username)
    await message.reply_text(f'💔 {author_link} и {partner_link} больше не в браке.', parse_mode='HTML')


async def cmd_marriage_callback(update, context):
    """Inline-кнопки под предложением: marry:yes/no/divorce"""
    query = update.callback_query
    data = (query.data or '').split(':')
    if len(data) != 4 or data[0] != 'marry':
        return
    _, action, raw_proposer, raw_target = data
    if not raw_proposer.isdigit() or not raw_target.isdigit():
        await _safe_answer(query)
        return
    proposer_id = int(raw_proposer)
    target_id = int(raw_target)
    clicker_id = query.from_user.id
    chat_id = query.message.chat.id if query.message else None
    # Консольный лог: кто нажал кнопку брака
    _uname = f"@{query.from_user.username}" if query.from_user.username else clicker_id
    logger.info(f'🔘 {_uname} нажал кнопку брака: {query.data}')
    if chat_id is None:
        await _safe_answer(query)
        return
    if action == 'divorce':
        partner_id = _marriage_partner_of(chat_id, clicker_id)
        if partner_id == proposer_id:
            _unregister_marriage(chat_id, proposer_id, clicker_id)
            await _safe_answer(query, 'Развод оформлен 💔', show_alert=True)
        else:
            await _safe_answer(query, 'Не получилось оформить развод', show_alert=True)
        return
    if clicker_id != target_id:
        await _safe_answer(query, 'Это предложение не для тебя.', show_alert=True)
        return
    pending = PENDING_MARRIAGE.get(str(chat_id), {})
    if pending.get(str(target_id)) != proposer_id:
        await _safe_answer(query, 'Предложение уже неактуально.', show_alert=True)
        return
    chat_users = USERS.get(str(chat_id), {})
    p_info = chat_users.get(str(proposer_id), {})
    proposer_link = user_link(proposer_id, p_info.get('name'), p_info.get('username'))
    target_link = user_link(target_id, query.from_user.first_name, query.from_user.username)
    if action == 'yes':
        since = datetime.datetime.now().isoformat()
        MARRIAGES.setdefault(str(chat_id), {})[str(target_id)] = {
            'partner_id': proposer_id, 'since': since}
        MARRIAGES[str(chat_id)][str(proposer_id)] = {
            'partner_id': target_id, 'since': since}
        pending.pop(str(target_id), None)
        PENDING_MARRIAGE[str(chat_id)] = pending
        save_marriages()
        await _safe_answer(query, 'Принято! 💍')
        await query.message.reply_text(
            f'💍 {target_link} принял предложение о браке.\n'
            f'👰👨‍⚖️ С сегодняшнего дня {proposer_link} и {target_link} состоят в браке.',
            parse_mode='HTML')
        return
    if action == 'no':
        pending.pop(str(target_id), None)
        PENDING_MARRIAGE[str(chat_id)] = pending
        await _safe_answer(query, 'Отклонено 💔')
        await query.message.reply_text(
            f'💔 {proposer_link}, примите соболезнования\n'
            f'💔 {target_link} отклонил ваше предложение о вступлении в брак.',
            parse_mode='HTML')
        return


async def cmd_my_marriage(update, context):
    """«мой брак» — карточка текущего брака"""
    message = update.message
    if message is None:
        return
    chat_id = update.effective_chat.id
    author = message.from_user
    partner_id = _marriage_partner_of(chat_id, author.id)
    if partner_id is None:
        await message.reply_text(
            '💔 Вы не состоите в браке. Введите команду «брак @юзер», чтобы сделать предложение.')
        return
    record = MARRIAGES.get(str(chat_id), {}).get(str(author.id), {})
    since_dt = parse_dt(record.get('since')) or datetime.datetime.now()
    days = max((datetime.datetime.now() - since_dt).days, 0)
    chat_users = USERS.get(str(chat_id), {})
    p_info = chat_users.get(str(partner_id), {})
    partner_link = user_link(partner_id, p_info.get('name'), p_info.get('username'))
    author_link = user_link(author.id, author.first_name, author.username)
    when_full = 'сегодня' if days == 0 else ('вчера' if days == 1 else f'{days} {plural_ru(days, "день", "дня", "дней")} назад')
    duration = human_duration(int((datetime.datetime.now() - since_dt).total_seconds()))
    title = 'Молодожёны' if days < 365 else ('Зрелый союз' if days < 365 * 5 else 'Старожилы любви')
    text = (
        f'👰👨‍⚖️ Брак между {author_link} и {partner_link}\n'
        f'🗓 Зарегистрирован ❗️ {when_full} ({human_ago(since_dt)})\n'
        f'⏱️ Длится уже {duration}\n'
        f'🫶 {title}\n'
    )
    if update.effective_chat.type != 'private':
        next_name, next_in = _next_anniversary(days)
        await message.reply_text(
            text + f'Счастья молодым!\n\n🌱 Следующая годовщина: {next_name}\n'
            f'⏱️ Через {human_duration(next_in * 86400)}')
        return
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton('🔍 Подробнее', callback_data=f'marriage:details:{author.id}')
    ]])
    await message.reply_text(text, reply_markup=keyboard)


async def cmd_marriage_details(update, context):
    """Кнопка «Подробнее» в карточке «мой брак» (только в ЛС)"""
    query = update.callback_query
    data = (query.data or '').split(':')
    if len(data) != 3 or data[0] != 'marriage' or data[1] != 'details':
        return
    if not data[2].isdigit():
        await _safe_answer(query)
        return
    user_id = int(data[2])
    if query.from_user.id != user_id:
        await _safe_answer(query, 'Это не ваш брак.', show_alert=True)
        return
    chat_id = query.message.chat.id
    record = MARRIAGES.get(str(chat_id), {}).get(str(user_id), {})
    if not record:
        await _safe_answer(query, 'Брак не найден.', show_alert=True)
        return
    partner_id = record.get('partner_id')
    since_dt = parse_dt(record.get('since')) or datetime.datetime.now()
    days = max((datetime.datetime.now() - since_dt).days, 0)
    chat_users = USERS.get(str(chat_id), {})
    p_info = chat_users.get(str(partner_id), {})
    user = await fetch_user(context, user_id, chat_id)
    author_link = user_link(user_id, user.first_name, user.username)
    partner_link = user_link(partner_id, p_info.get('name'), p_info.get('username'))
    when_full = 'сегодня' if days == 0 else ('вчера' if days == 1 else f'{days} {plural_ru(days, "день", "дня", "дней")} назад')
    duration = human_duration(int((datetime.datetime.now() - since_dt).total_seconds()))
    title = 'Молодожёны' if days < 365 else ('Зрелый союз' if days < 365 * 5 else 'Старожилы любви')
    next_name, next_in = _next_anniversary(days)
    text = (
        f'👰👨‍⚖️ Брак между {author_link} и {partner_link}\n'
        f'🗓 Зарегистрирован ❗️ {when_full} ({human_ago(since_dt)})\n'
        f'⏱️ Длится уже {duration}\n'
        f'🫶 {title}\n'
        f'Счастья молодым!\n\n'
        f'🌱 Следующая годовщина: {next_name}\n'
        f'⏱️ Через {human_duration(next_in * 86400)}'
    )
    await _safe_answer(query)
    if update.effective_chat.type != 'private':
        await query.message.reply_text(text)
    else:
        await safe_edit_message(query, text)


async def cmd_marriages_list(update, context):
    """«браки» — список браков чата"""
    message = update.message
    chat_id = update.effective_chat.id
    chat_marriages = MARRIAGES.get(str(chat_id), {})
    pairs = set()
    for uid, info in chat_marriages.items():
        if not isinstance(info, dict):
            continue
        try:
            partner = int(info.get('partner_id'))
        except (TypeError, ValueError):
            continue
        pairs.add(tuple(sorted([uid, str(partner)])))
    if not pairs:
        await message.reply_text(
            '💍 Браков этого чата пока нет. Введите «брак @юзер», чтобы сделать предложение.')
        return
    chat_users = USERS.get(str(chat_id), {})
    lines = ['💍 Браки этого чата\n', '🫶 Молодожёны']
    for idx, (a, b) in enumerate(sorted(pairs), start=1):
        a_info = chat_users.get(a, {})
        b_info = chat_users.get(b, {})
        a_link = user_link(int(a), a_info.get('name'), a_info.get('username'))
        b_link = user_link(int(b), b_info.get('name'), b_info.get('username'))
        record = chat_marriages.get(a, {})
        since_dt = parse_dt(record.get('since')) or datetime.datetime.now()
        duration = human_duration(int((datetime.datetime.now() - since_dt).total_seconds()))
        lines.append(f'{idx}. {a_link} + {b_link} ({duration})')
    await message.reply_text('\n'.join(lines))


# ----- Ферма -----

async def cmd_farm(update, context):
    """«ферма» / «фарма» — добыча с кулдауном 4 часа"""
    message = update.message
    if message is None:
        return
    user_id = str(message.from_user.id)
    info = FARM.get(user_id)
    now = datetime.datetime.now()
    if info:
        last_dt = parse_dt(info.get('last'))
        if last_dt and (now - last_dt).total_seconds() < FARM_COOLDOWN_SECONDS:
            remain = FARM_COOLDOWN_SECONDS - int((now - last_dt).total_seconds())
            await message.reply_text(
                f'❌ НЕЗАЧЁТ! Фармить можно раз в 4 часа. Следующая добыча через {human_duration(remain)}.')
            return
    a = random.randint(50, 200)
    b = random.randint(50, 200)
    gain = (a * b) % 1000
    stars = random.randint(1, 5)
    crop = random.randint(20, 200)
    FARM[user_id] = {
        'last': now.isoformat(),
        'total': int((info or {}).get('total', 0)) + gain,
        'stars': int((info or {}).get('stars', 0)) + stars,
        'yield': int((info or {}).get('yield', 0)) + crop,
    }
    save_farm()
    await message.reply_text(
        f'✅ ЗАЧЁТ! ☢️ +{gain} i¢ = {a}×{b}({a * b % 1000})\n'
        f'✨ Сила звёздности: {FARM[user_id]["stars"]}\n'
        f'⏳ Урожайность: {FARM[user_id]["yield"]}')


# ----- Ники -----

async def cmd_nick(update, context):
    """«ник» — показать, «+ник СЛОВО» — установить"""
    message = update.message
    if message is None:
        return
    chat_id = update.effective_chat.id
    user_id = message.from_user.id
    parts = message.text.split()
    chat_nicks = NICKS.setdefault(str(chat_id), {})
    if len(parts) == 1:
        nick = chat_nicks.get(str(user_id))
        if nick:
            await message.reply_text(f'🗓 Ник: {nick}')
        else:
            await message.reply_text(
                '🗓 Ник пока не установлен. Чтобы поставить ник, напишите «+ник СЛОВО».')
        return
    new_nick = ' '.join(parts[1:]).strip()
    if not new_nick:
        await message.reply_text(
            '🗓 Ник пока не установлен. Чтобы поставить ник, напишите «+ник СЛОВО».')
        return
    chat_nicks[str(user_id)] = new_nick
    save_nicks()
    name = (message.from_user.first_name or '')
    if getattr(message.from_user, 'last_name', None):
        name = (name + ' ' + message.from_user.last_name).strip()
    if not name:
        name = f'@{message.from_user.username}' if message.from_user.username else f'ID {user_id}'
    link = user_link(user_id, name, message.from_user.username)
    await message.reply_text(f'✅ Ник {link} изменён на «{new_nick}»')


# ----- Приветствие -----

async def cmd_greeting(update, context):
    """«+Приветствие текст» — задать; «+Приветствие» — посмотреть; «-Приветствие» — удалить"""
    message = update.message
    if message is None:
        return
    chat_id = update.effective_chat.id
    text = message.text.strip()
    head = text.split()[0].lstrip('/').split('@')[0]
    if head in ('-Приветствие', '-приветствие', '-greeting', '-greetings'):
        if str(chat_id) in GREETINGS:
            GREETINGS.pop(str(chat_id), None)
            save_greetings()
            await message.reply_text('✅ Приветствие чата удалено.')
        else:
            await message.reply_text('🗓 Приветствие чата пока не установлено.')
        return
    parts = text.split(maxsplit=1)
    body = parts[1].strip() if len(parts) > 1 else ''
    if not body:
        current = GREETINGS.get(str(chat_id))
        if current:
            await message.reply_text(
                f'🗓 Текущее приветствие чата:\n\n{current}\n\n'
                'В приветствии можно использовать следующие переменные:\n'
                '{имя} — выводит имя и фамилию пользователя.\n'
                '{ж|м|мн} — выводит текст в зависимости от пола или количества '
                'пользователей: для женского пола, мужского пола и группы людей.')
        else:
            await message.reply_text(
                '🗓 Приветствие чата пока не установлено.\n'
                'Установка приветствия: +Приветствие\n\n'
                'Текст на новой строчке\n'
                'В приветствии можно использовать следующие переменные:\n'
                '{имя} — выводит имя и фамилию пользователя.\n'
                '{ж|м|мн} — выводит текст в зависимости от пола или количества пользователей: '
                'для женского пола, мужского пола и группы людей.')
        return
    if not await can_moderate(update, context):
        await message.reply_text('Задавать приветствие может владелец чата, администратор или персонал бота.')
        return
    GREETINGS[str(chat_id)] = body
    save_greetings()
    await message.reply_text('✅ Приветствие новых пользователей в беседе обновлено.')


# ----- Каналы -----

async def cmd_channels(update, context):
    """«+Каналы» / «-Каналы» — разрешить или запретить писать от имени каналов"""
    message = update.message
    if message is None:
        return
    if not await can_moderate(update, context):
        await message.reply_text('Менять настройки каналов может только администратор.')
        return
    chat_id = update.effective_chat.id
    head = message.text.strip().split()[0].lstrip('/').split('@')[0].lower()
    if head in ('+каналы', '+channels'):
        CHANNELS_ALLOWED[str(chat_id)] = True
        save_channels_allowed()
        await message.reply_text('✅ Писать от имени каналов теперь разрешено.')
        return
    if head in ('-каналы', '-channels'):
        CHANNELS_ALLOWED[str(chat_id)] = False
        save_channels_allowed()
        await message.reply_text('✅ Писать от имени каналов теперь запрещено.')
        return


# ----- !скажи -----

async def cmd_say(update, context):
    """«!скажи текст» — бот публикует текст от своего имени"""
    message = update.message
    if message is None or not message.text:
        return
    text = message.text.split(maxsplit=1)
    body = text[1].strip() if len(text) > 1 else ''
    if not body:
        await message.reply_text('🗓 Использование: «!скажи текст» — я отправлю этот текст от своего имени.')
        return
    who = message.from_user
    mention = user_link(who.id, who.first_name, who.username) if who else 'кто-то'
    await message.reply_text(
        f'🗓 {mention} попросил меня сказать:\n{body}', parse_mode='HTML')


# ----- Репутация (ответы на сообщения ключевыми словами) -----

async def cmd_reputation_handler(update, context):
    """Обработчик коротких ответов-уважения на сообщения"""
    message = update.message
    if message is None or not message.text or message.reply_to_message is None:
        return
    text = message.text.strip().lower().replace('ё', 'е')
    if text not in REPUTATION_KEYWORDS:
        return
    target = message.reply_to_message.from_user
    giver = message.from_user
    if target is None or target.is_bot or giver.id == target.id:
        return
    rec_t = REPUTATION.setdefault(str(target.id), {'given': 0, 'received': 0})
    rec_t['received'] = int(rec_t.get('received', 0)) + 1
    rec_g = REPUTATION.setdefault(str(giver.id), {'given': 0, 'received': 0})
    rec_g['given'] = int(rec_g.get('given', 0)) + 1
    save_reputation()
    target_link = user_link(target.id, target.first_name, target.username)
    giver_link = user_link(giver.id, giver.first_name, giver.username)
    await message.reply_text(
        f'✨ {target_link} теперь имеет репутацию <b>{rec_t["received"]}</b>\n'
        f'➕ {giver_link} оказал(а) уважение <b>{rec_g["given"]}</b> раз(а)',
        parse_mode='HTML')


# ----- Удаление и закрепление -----

async def cmd_delete_msg(update, context):
    """«-смс» (в ответ на сообщение) — удаляет сообщение. Доступно модераторам."""
    message = update.message
    if message is None:
        return
    if message.reply_to_message is None:
        await message.reply_text('🗓 Ответьте этой командой на сообщение, которое хотите удалить.')
        return
    if not await can_moderate(update, context):
        await message.reply_text('Удалять сообщения может только администратор или модератор.')
        return
    try:
        await context.bot.delete_message(chat_id=message.chat_id,
                                          message_id=message.reply_to_message.message_id)
        # Если удалилось — удаляем и само сообщение команды
        try:
            await context.bot.delete_message(chat_id=message.chat_id,
                                              message_id=message.message_id)
        except Exception:
            pass
        await message.reply_text('✅ Сообщение удалено.')
    except Exception as e:
        err = str(e)
        if 'CHAT_ID_INVALID' in err or 'METHOD_NOT_FOUND' in err:
            await message.reply_text(
                '⚠️ Сервер DropGram не поддерживает удаление сообщений в группах.\n'
                'Удалите сообщение вручную или через клиента Telegram.')
        else:
            await message.reply_text(f'⚠️ Не удалось удалить: {e}')


async def cmd_pin_msg(update, context):
    """«!закреп» (в ответ на сообщение) — закрепляет. Доступно администраторам."""
    message = update.message
    if message is None:
        return
    if message.reply_to_message is None:
        await message.reply_text('🗓 Ответьте этой командой на сообщение, которое хотите закрепить.')
        return
    if not await can_moderate(update, context):
        await message.reply_text('Закреплять сообщения может только администратор.')
        return
    try:
        await context.bot.pin_chat_message(message.chat_id, message.reply_to_message.message_id)
        await message.reply_text('✅ Сообщение закреплено.')
    except Exception as e:
        err = str(e)
        if 'METHOD_NOT_FOUND' in err:
            await message.reply_text(
                '⚠️ Сервер DropGram не поддерживает закрепление сообщений.\n'
                'Закрепите сообщение через клиента Telegram (правый клик → Закрепить).')
        else:
            await message.reply_text(f'⚠️ Не удалось закрепить: {e}')


async def cmd_unpin_msg(update, context):
    """«!открепить» — открепляет закреплённое сообщение"""
    message = update.message
    if message is None:
        return
    if not await can_moderate(update, context):
        await message.reply_text('Откреплять сообщения может только администратор.')
        return
    try:
        await context.bot.unpin_chat_message(message.chat_id)
        await message.reply_text('✅ Сообщение откреплено.')
    except Exception as e:
        err = str(e)
        if 'METHOD_NOT_FOUND' in err:
            await message.reply_text(
                '⚠️ Сервер DropGram не поддерживает открепление сообщений.\n'
                'Открепите сообщение через клиента Telegram (правый клик → Открепить).')
        else:
            await message.reply_text(f'⚠️ Не удалось открепить: {e}')


async def cmd_stats(update, context):
    """«/stats» или «стата» / «стата вся» — топ участников по сообщениям"""
    message = update.message
    if message is None:
        return
    chat = update.effective_chat
    if chat is None:
        return
    chat_users = USERS.get(str(chat.id), {})
    if not chat_users:
        await message.reply_text('Статистика пока пуста — ни одного сообщения не зафиксировано.')
        return

    # Сортируем по total (кол-во сообщений) по убыванию
    items = []
    for uid, info in chat_users.items():
        if not isinstance(info, dict):
            continue
        total = int(info.get('total', 0))
        if total <= 0:
            continue
        items.append((uid, info, total))
    items.sort(key=lambda x: x[2], reverse=True)

    overall = sum(t for _, _, t in items)
    top = items[:10]

    medals = ['🥇', '🥈', '🥉']
    lines = [f'📊 <b>Топ участников по сообщениям</b>\n']
    for i, (uid, info, total) in enumerate(top):
        medal = medals[i] if i < 3 else f'{i + 1}.'
        name = info.get('name')
        username = info.get('username')
        link = user_link(uid, name, username)
        pct = (total / overall * 100) if overall else 0
        lines.append(f'{medal} {link} — <b>{total}</b> ({pct:.1f}%)')
    lines.append(f'\nВсего сообщений в чате: <b>{overall}</b>')
    lines.append(f'Активных участников: <b>{len(items)}</b>')

    # Показываем место самого пользователя, если он не в топ-10
    me = message.from_user
    if me is not None:
        my_entry = None
        for i, (uid, info, total) in enumerate(items):
            if str(uid) == str(me.id):
                my_entry = (i + 1, total)
                break
        if my_entry is not None and my_entry[0] > 10:
            lines.append(f'\nВы на {my_entry[0]}-м месте — {my_entry[1]} сообщений.')

    await message.reply_text('\n'.join(lines), parse_mode='HTML')


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показать свой ID и ник: /id (ответом — ID того, кому ответили)"""
    message = update.message
    user = message.from_user
    lines = [f'Ваш ID: <code>{user.id}</code>']
    if user.username:
        lines.append(f'Ваш ник: @{user.username}')
    reply = message.reply_to_message
    if reply is not None:
        found = reply.from_user.id if reply.from_user is not None else None
        if found is None:
            found = find_author_by_message_id(
                update.effective_chat.id, getattr(reply, 'message_id', None))
        if found is not None:
            lines.append(f'ID того, кому ответили: <code>{found}</code>')
    await message.reply_text('\n'.join(lines), parse_mode='HTML')


async def cmd_commands(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Полный список команд: /commands"""
    lines = [
        '📋 ВСЕ КОМАНДЫ\n',
        'Основные:',
        '/start — запустить бота',
        '/help — справка',
        '/commands — этот список',
        '/id — узнать свой ID и ник\n',
        'Управление группой (в группе, можно ответом на сообщение):',
        '/mute @ник 1 день — мут (без срока — навсегда)',
        '/unmute @ник — снять мут',
        '/ban @ник 1 день — бан (без срока — навсегда)',
        '/unban @ник — снять бан (в личку уйдёт ссылка)',
        '/-смс (в ответ) — удалить сообщение (модератор)',
        '/!закреп (в ответ) — закрепить сообщение (админ)',
        '/!открепить — снять закрепление\n',
        'Профиль и развлечения (можно писать без слэша: «кто я», «обнять @ник»):',
        '/кто_я — своя карточка',
        '/кто_ты — карточка участника',
        '/убить, /поцеловать, /обнять, /трахнуть, /выебать',
        '/пожать_руку, /пнуть',
        '/шипперим — случайная пара\n',
        'Брак (без слэша тоже работает):',
        '/marry @юзер — сделать предложение',
        '/divorce — развод',
        '/my_marriage — карточка моего брака',
        '/marriages — список браков чата',
        'Кнопки «💖 Согласиться» / «💔 Отказаться» — в группе бот шлёт новое сообщение.',
        'В группе editMessageText не работает, поэтому «Подробнее» в карточке брака',
        'доступно только в ЛС; в группе бот покажет подробности новым сообщением.\n',
        'Ники, ферма, приветствия, каналы, репутация:',
        '/ник или /ник СЛОВО — ник (в чате)',
        '/ферма или /фарма — добыча раз в 4 часа (рандом)',
        '/+Приветствие текст — задать приветствие чата',
        '/-Приветствие — удалить; /+Приветствие без текста — посмотреть',
        '   переменные: {имя}, {ж|м|мн}',
        '/+Каналы — разрешить писать от имени каналов',
        '/-Каналы — запретить (бот попытается удалить сообщение канала; в группе',
        '   DropGram не поддерживает удаление — будет только предупреждение)',
        '/!скажи текст — бот опубликует текст от своего имени',
        '/stats или «стата» / «стата вся» — топ участников по сообщениям',
        'Репутация (в ответ на сообщение): йк, респект, F, уважение, уважуха,',
        '   увожение, жиза, жыза, плюс, красавчик, справедливо, meine respektierung → +1\n',
        'Должности:',
        '/правила (или /rules) — правила чата; с текстом — задать (владелец/админ)',
        '/admin_add @ник 1-5 — выдать должность (или /admin_add 3 — кнопки выбора цели)',
        '/admin_remove @ник 0-5 — понизить (0 — снять совсем)',
        '/admins — список руководства',
    ]
    await update.message.reply_text('\n'.join(lines))


FUN_ACTIONS = {
    'убить': ('🔪', 'убил'),
    'kill': ('🔪', 'убил'),
    'поцеловать': ('😘', 'поцеловал'),
    'kiss': ('😘', 'поцеловал'),
    'трахнуть': ('❤', 'принудил к интиму'),
    'fuck': ('❤', 'принудил к интиму'),
    'выебать': ('😩', 'принудил к жёсткому интиму'),
    'hardfuck': ('😩', 'принудил к жёсткому интиму'),
    'обнять': ('🤗', 'обнял'),
    'hug': ('🤗', 'обнял'),
    'пожать руку': ('🤝', 'пожал руку'),
    'пожать_руку': ('🤝', 'пожал руку'),
    'handshake': ('🤝', 'пожал руку'),
    'пнуть': ('👞', 'пнул'),
    'kick_fun': ('👞', 'пнул'),
}

# Русские команды Telegram не считает командами, поэтому ловим их обычным фильтром по тексту
FUN_PATTERN = (r'^/?(убить|поцеловать|трахнуть|выебать|обнять|шипперим|кто[ _]я|кто[ _]ты|'
               r'команды|правила|kill|kiss|fuck|hardfuck|hug|ship|whoami|whois|rules|'
               r'пнуть|handshake|kick_fun)'
               r'(@[A-Za-z0-9_]+)?(\s|$)')
# Команда из двух слов — отдельный шаблон
FUN_PATTERN_TWOWORD = (r'^/?пожать[ _]руку(@[A-Za-z0-9_]+)?(\s|$)')


async def _dispatch_extra_text(update, context):
    """Маршрутизация русских команд, которые Telegram не считает командами.
    Сюда попадают: брак / развод / мой брак / браки / ферма / фарма /
    ник / +ник / +Приветствие / -Приветствие / +Каналы / -Каналы /
    !скажи / !закреп / !открепить / -смс.
    """
    message = update.message
    if message is None or not message.text:
        return
    text = message.text.strip()
    head = text.split()[0].lstrip('/').split('@')[0].lower().replace('ё', 'е')
    if head in ('брак', 'marry', 'свадьба', 'жениться', 'поженить'):
        await cmd_marry(update, context)
        return
    if head in ('развод', 'divorce'):
        await cmd_divorce(update, context)
        return
    if head in ('мой_брак', 'мой брак', 'my_marriage', 'мойбрак'):
        await cmd_my_marriage(update, context)
        return
    if head in ('браки', 'marriages'):
        await cmd_marriages_list(update, context)
        return
    if head in ('ферма', 'фарма', 'farm'):
        await cmd_farm(update, context)
        return
    if head in ('ник', 'nick'):
        await cmd_nick(update, context)
        return
    # Head с ведущим + или - нужно смотреть без lower()
    raw_head = text.split()[0].lstrip('/').split('@')[0]
    if raw_head in ('+Приветствие', '+приветствие', '+greeting'):
        await cmd_greeting(update, context)
        return
    if raw_head in ('-Приветствие', '-приветствие', '-greeting'):
        await cmd_greeting(update, context)
        return
    if raw_head in ('+Каналы', '+каналы', '+channels'):
        await cmd_channels(update, context)
        return
    if raw_head in ('-Каналы', '-каналы', '-channels'):
        await cmd_channels(update, context)
        return
    if raw_head in ('!скажи', '!say'):
        await cmd_say(update, context)
        return
    if raw_head in ('!закреп', '!pin'):
        await cmd_pin_msg(update, context)
        return
    if raw_head in ('!открепить', '!unpin'):
        await cmd_unpin_msg(update, context)
        return
    if raw_head in ('-смс', '-msg', '-message'):
        await cmd_delete_msg(update, context)
        return
    if head in ('стата', 'статы', 'stats', 'статистика'):
        await cmd_stats(update, context)
        return


async def cmd_fun(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Развлекательные команды (действуют на того, кому ответили)"""
    message = update.message
    if message is None or not message.text:
        return
    raw = message.text.strip().lower().replace('ё', 'е').lstrip('/')
    if raw.startswith(('кто я', 'кто_я')):
        command = 'кто_я'
    elif raw.startswith(('кто ты', 'кто_ты')):
        command = 'кто_ты'
    elif raw.startswith(('пожать руку', 'пожать_руку')):
        command = 'пожать руку'
    else:
        command = message.text.strip().split()[0].lstrip('/').split('@')[0].lower()
    if command in ('кто_я', 'whoami'):
        await cmd_who_me(update, context)
        return
    if command in ('кто_ты', 'whois'):
        await cmd_who_you(update, context)
        return
    if command in ('шипперим', 'ship'):
        await cmd_shippering(update, context)
        return
    if command in ('команды', 'commands'):
        await cmd_commands(update, context)
        return
    if command in ('правила', 'rules'):
        context.args = message.text.strip().split()[1:]
        await cmd_rules(update, context)
        return
    action = FUN_ACTIONS.get(command)
    if action is None:
        return
    emoji, verb = action
    target_id, target_label = await fun_target(message, update.effective_chat.id, context)
    if target_id is None:
        await ask_fun_target(update, context, command)
        return
    author = message.from_user
    await message.reply_text(
        f'{emoji} | {user_link(author.id, author.first_name, author.username)} {verb} '
        f'{target_label}',
        parse_mode='HTML')


async def cmd_shippering(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Случайная пара из участников чата: /шипперим"""
    chat = update.effective_chat
    users = USERS.get(str(chat.id), {})
    known = set(uid for uid in users if str(uid).isdigit())
    known.update(uid for uid in RECENT.get(str(chat.id), []) if str(uid).isdigit())
    for item in CHAT_MESSAGES.get(str(chat.id), []):
        uid = str(item.get('user_id', ''))
        if uid.isdigit():
            known.add(uid)
    known = sorted(known)
    if len(known) < 2:
        await update.message.reply_text(
            'Пока знаю только тех, кто писал в чате (бот не видит список участников). '
            'Пусть напишут пару сообщений — и они попадут в шипперим 🙈')
        return
    first_id, second_id = random.sample(known, 2)
    first = user_link(first_id, (users.get(first_id) or {}).get('name'),
                      (users.get(first_id) or {}).get('username'))
    second = user_link(second_id, (users.get(second_id) or {}).get('name'),
                       (users.get(second_id) or {}).get('username'))
    await update.message.reply_text(
        f'💞 РАНДОМ ШИППЕРИМ: {first} + {second}. Любите друг друга и берегите. Мур.',
        parse_mode='HTML')


async def track_activity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Учёт первого появления, последнего актива и активности участников"""
    message = update.effective_message
    if message is None:
        return
    chat = message.chat
    if chat is None:
        return
    # создателя бота запоминаем в любом чате, включая личку — иначе не будет прав
    pin_creator(message.from_user)
    if chat.type not in ('group', 'supergroup', 'private'):
        return
    # запоминаем все сообщения, включая сообщения ботов: по ним ищем адресата ответа
    remember_message(chat.id, message)
    if message.from_user is None or message.from_user.is_bot:
        return
    user = message.from_user
    chat_users = USERS.setdefault(str(chat.id), {})
    # Консольный лог: кто что пишет
    _uname = f"@{user.username}" if user.username else user.id
    _cname = getattr(chat, 'title', None) or ('ЛС' if chat.type == 'private' else str(chat.id))
    _snippet = (message.text or '')[:80].replace('\n', ' ')
    logger.info(f'📝 {_uname} в «{_cname}»: {_snippet}')
    info = chat_users.get(str(user.id))
    if not isinstance(info, dict):
        info = {'first_seen': datetime.datetime.now().isoformat(), 'days': {}, 'total': 0}
        chat_users[str(user.id)] = info
    name = f'{user.first_name or ""} {user.last_name or ""}'.strip()
    if name:
        info['name'] = name
    if user.username:
        info['username'] = user.username
    now = datetime.datetime.now()
    info['last_active'] = now.isoformat()
    day = now.date().isoformat()
    days = info.get('days')
    if not isinstance(days, dict):
        days = {}
        info['days'] = days
    days[day] = int(days.get(day, 0)) + 1
    info['total'] = int(info.get('total', 0)) + 1
    pin_creator(user)
    remember_author(chat.id, user.id)
    save_users()
    save_recent()


def main() -> None:
    """Запуск бота"""
    # Создать Application
    # Адрес Bot API — сервер DropGram (НЕ api.telegram.org).
    # «/bot» в конце обязателен: библиотека сама добавляет к нему токен.
    # Адрес можно изменить в .env строкой API_URL=...
    api_base_url = os.getenv('API_URL', 'http://67.211.221.152:8888').rstrip('/')
    if not api_base_url.endswith('/bot'):
        api_base_url += '/bot'
    application = Application.builder().token(BOT_TOKEN).base_url(api_base_url).build()

    # Настройка меню команд (регистрируем функцию, вызываемую при запуске)
    application.post_init = setup_commands
    
    # Добавить обработчик команды
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("add_keyword", add_keyword))
    application.add_handler(CommandHandler("bulk_add_keywords", bulk_add_keywords))
    application.add_handler(CommandHandler("remove_keyword", remove_keyword))
    application.add_handler(CommandHandler("list_keywords", list_keywords))
    application.add_handler(CommandHandler("list_admins", list_admins))
    application.add_handler(CommandHandler("add_whitelist", add_whitelist))
    application.add_handler(CommandHandler("remove_whitelist", remove_whitelist))
    application.add_handler(CommandHandler("list_whitelist", list_whitelist))
    application.add_handler(CommandHandler("gban", ban_user))  # глобальный чёрный список
    application.add_handler(CommandHandler("gunban", unban_user))  # снятие глобального бана
    application.add_handler(CommandHandler("mute", cmd_mute))
    application.add_handler(CommandHandler("unmute", cmd_unmute))
    application.add_handler(CommandHandler("ban", cmd_ban))
    application.add_handler(CommandHandler("unban", cmd_unban))
    application.add_handler(CommandHandler("commands", cmd_commands))
    application.add_handler(CommandHandler("id", cmd_id))
    application.add_handler(CommandHandler("rules", cmd_rules))
    application.add_handler(CommandHandler("admin_add", cmd_admin_add))
    application.add_handler(CommandHandler("admin_remove", cmd_admin_remove))
    application.add_handler(CommandHandler("admins", cmd_admins))
    application.add_handler(CommandHandler("marry", cmd_marry))
    application.add_handler(CommandHandler("divorce", cmd_divorce))
    application.add_handler(CommandHandler("my_marriage", cmd_my_marriage))
    application.add_handler(CommandHandler("marriages", cmd_marriages_list))
    application.add_handler(CommandHandler("farm", cmd_farm))
    application.add_handler(CommandHandler("nick", cmd_nick))
    application.add_handler(CommandHandler("greeting", cmd_greeting))
    application.add_handler(CommandHandler("channels", cmd_channels))
    application.add_handler(CommandHandler("say", cmd_say))
    application.add_handler(CommandHandler("stats", cmd_stats))
    application.add_handler(CommandHandler("delete_msg", cmd_delete_msg))
    application.add_handler(CommandHandler("pin_msg", cmd_pin_msg))
    application.add_handler(CommandHandler("unpin_msg", cmd_unpin_msg))
    application.add_handler(CommandHandler("list_banned", list_banned_users))
    application.add_handler(CommandHandler("list_violations", list_violations))
    application.add_handler(CommandHandler("clear_violation", clear_violation))
    application.add_handler(CommandHandler("dev_mode", toggle_dev_mode))  # Команда режима разработчика
    application.add_handler(CommandHandler("add_group_keyword", add_group_keyword_cmd))  # Добавить команду ключевых слов группы
    application.add_handler(CommandHandler("remove_group_keyword", remove_group_keyword_cmd))  # команда удаления ключевого слова группы
    application.add_handler(CommandHandler("list_group_keywords", list_group_keywords_cmd))  # Команда вывода ключевых слов группы
    
    # Выбор цели кнопками (развлекательные команды, профиль, должности) — до общего обработчика
    application.add_handler(CallbackQueryHandler(cmd_choice_callback, pattern=r"^(fun|rank):"), group=-1)
    
    # Обработка callback-запроса (исключаем marry: и marriage: — у них свои обработчики ниже)
    application.add_handler(CallbackQueryHandler(handle_group_management,
                                                   pattern=r'^(?!marry:|marriage:)'))
    
    # Обработка присоединения новых участников
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_members))
    
    # Учёт активности участников (раньше остальных обработчиков)
    application.add_handler(MessageHandler(filters.ALL, track_activity), group=-2)

    # Блокировка сообщений от имени каналов (если в чате включена +Каналы/-Каналы)
    application.add_handler(MessageHandler(filters.SenderChat() & filters.ChatType.GROUPS,
                                           cmd_block_channels), group=-2)

    # Развлекательные команды и профили (/кто_я, /убить, /шипперим и т.д.)
    application.add_handler(MessageHandler(filters.Regex(FUN_PATTERN), cmd_fun), group=-1)
    application.add_handler(MessageHandler(filters.Regex(FUN_PATTERN_TWOWORD), cmd_fun), group=-1)

    # Команды брака/развода/моя_свадьба/список браков (русские слова — Telegram не считает их командами)
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.Regex(
            r'^/?(брак(\s|$)|развод(\s|$)|мой[ _]брак(\s|$)|браки(\s|$)|'
            r'ферма(\s|$)|фарма(\s|$)|ник(\s|$)|ник\s+\S+|'
            r'\+приветствие(\s|$)|\-приветствие(\s|$)|\+каналы(\s|$)|\-каналы(\s|$)|'
            r'!скажи(\s|$)|!закреп(\s|$)|!открепить(\s|$)|\-смс(\s|$)|'
            r'стата(\s+вся)?(\s|$)|статы(\s|$)|статистика(\s|$)|'
            r'stats?(\s|$))'
        ),
        _dispatch_extra_text), group=-1)

    # Ответы на сообщения ключевыми словами репутации (йк, респект, F, …)
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.Regex(r'^(йк|респект|f|уважение|уважуха|увожение|жиза|жыза|плюс|красавчик|справедливо|meine respektierung)\s*$'),
        cmd_reputation_handler), group=-1)

    # Inline-кнопки брака: marry:yes/no/divorce
    application.add_handler(CallbackQueryHandler(cmd_marriage_callback, pattern=r'^marry:'))

    # Кнопка «Подробнее» в карточке «мой брак»
    application.add_handler(CallbackQueryHandler(cmd_marriage_details, pattern=r'^marriage:details:'))

    # Обрабатываем все текстовые сообщения
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, check_spam))

    # Проверять состояние групп раз в день (нужен JobQueue:
    # pip install "python-telegram-bot[job-queue]")
    if application.job_queue is not None:
        application.job_queue.run_repeating(check_groups, interval=86400, first=10800)
    else:
        logger.warning(
            'JobQueue недоступен: периодическая проверка групп отключена. '
            'Установите: pip install "python-telegram-bot[job-queue]"')

    # Сохранить данные при остановке бота
    async def save_on_shutdown(application):
        save_users(force=True)
        save_ranks()
        save_recent()
        save_messages()
        save_rules()
        save_marriages()
        save_nicks()
        save_greetings()
        save_farm()
        save_reputation()
        save_channels_allowed()
        logger.info('Данные сохранены')

    application.post_shutdown = save_on_shutdown
    
    # --- Веб-сервер для Render (health-check, порт из $PORT) ---
    def _run_health_server():
        port = int(os.getenv('PORT', 10000))
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class HealthHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b'{"status":"ok","bot":"DropGram Bot"}'
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        try:
            HTTPServer(('0.0.0.0', port), HealthHandler).serve_forever()
        except Exception as e:
            logger.warning(f'Health-сервер не запустился (порт {port}): {e}')

    threading.Thread(target=_run_health_server, daemon=True).start()
    logger.info(f'Health-сервер запущен на порту {os.getenv("PORT", 10000)}')

    # Запуск бота
    logger.info("Бот запущен")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main() 