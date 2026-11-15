import os
import io
import time
import logging
import re
import uuid
import threading
import telebot
from telebot import types
from flask import Flask, request, jsonify, send_from_directory, redirect

# Настройка логирования
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger('gunicorn.error') if 'gunicorn' in os.environ.get('SERVER_SOFTWARE', '') else logging.getLogger(__name__)

# --- Конфигурация из переменных окружения ---
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    logger.error("КРИТИЧЕСКАЯ ОШИБКА: Переменная окружения BOT_TOKEN не установлена!")

RENDER_URL = os.environ.get("RENDER_URL", "https://tg-pc-server.onrender.com").strip().rstrip("/")
AGENT_SECRET = os.environ.get("AGENT_SECRET", "").strip()
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "").strip()

raw_allowed_users = os.environ.get("ALLOWED_USERS", "").strip()
ALLOWED_USERS = set(filter(None, [u.strip() for u in raw_allowed_users.split(",")])) if raw_allowed_users else set()

EXE_FILE = "agent.exe"
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads_temp")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

bot = telebot.TeleBot(BOT_TOKEN, threaded=False) if BOT_TOKEN else None
app = Flask(__name__, template_folder="templates", static_folder="static")

# Базы данных в памяти
hosts_db = {}
commands_queue = {}
user_ui_messages = {}
media_ui_messages = {}
latest_screenshots = {}
latest_tasks = {}
latest_metrics = {}
latest_audio_devices = {}
latest_app_volumes = {}
audio_devices_lookup = {}

def delete_message_after(chat_id, message_id, delay=3.0):
    """Удаляет временное сервисное сообщение через delay секунд."""
    def _delete():
        time.sleep(delay)
        try:
            bot.delete_message(chat_id, message_id)
        except Exception:
            pass
    threading.Thread(target=_delete, daemon=True).start()

# --- Вспомогательные функции безопасности ---

def is_authorized_user(user_id: str) -> bool:
    if ALLOWED_USERS and str(user_id) not in ALLOWED_USERS:
        return False
    return True

def sanitize_id(identifier: str) -> str:
    if not identifier:
        return ""
    return re.sub(r"[^a-zA-Z0-9_\-\.]", "", str(identifier))[:64]

def verify_agent_auth(req) -> bool:
    if not AGENT_SECRET:
        return True
    auth_header = req.headers.get("X-Agent-Secret", "").strip()
    return auth_header == AGENT_SECRET

def get_target_host(identifier: str = ""):
    now = time.time()
    clean_id = sanitize_id(identifier)
    
    # 1. Поиск по точному имени ПК (host_id)
    if clean_id and clean_id in hosts_db and (now - hosts_db[clean_id].get("last_seen", 0)) < 70:
        return clean_id
        
    # 2. Поиск по user_id или host_id в базе
    if clean_id:
        for hid, info in hosts_db.items():
            if (info.get("user_id") == clean_id or hid == clean_id) and (now - info.get("last_seen", 0)) < 70:
                return hid
                
    # 3. Авто-подключение: если ID не указан или опечатка, но в сети ровно 1 ПК
    active_hosts = [hid for hid, info in hosts_db.items() if (now - info.get("last_seen", 0)) < 70]
    if len(active_hosts) == 1:
        return active_hosts[0]
        
    return None

def add_command(host_id: str, action: str, data: str = ""):
    q = commands_queue.setdefault(host_id, [])
    # Слияние команд движения мыши для плавности и защиты от переполнения очереди
    if action == "mouse_move" and q and q[-1].get("action") == "mouse_move":
        try:
            dx1, dy1 = [int(v) for v in q[-1].get("data", "0,0").split(",")]
            dx2, dy2 = [int(v) for v in str(data).split(",")]
            q[-1]["data"] = f"{dx1 + dx2},{dy1 + dy2}"
            return
        except Exception:
            pass
    if len(q) < 40:
        q.append({"action": action, "data": data})

def get_main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    if RENDER_URL:
        markup.add(types.KeyboardButton("📱 Открыть приложение экосистемы", web_app=types.WebAppInfo(url=RENDER_URL)))
    markup.add(types.KeyboardButton("📥 Установить приложения (ПК и Телефон)"))
    markup.add(types.KeyboardButton("📊 Мониторинг ПК"), types.KeyboardButton("📸 Скриншот экрана"))
    markup.add(types.KeyboardButton("📁 Проводник (Диски)"), types.KeyboardButton("⚙️ Диспетчер задач"))
    markup.add(types.KeyboardButton("🎵 Мультимедиа и Звук"), types.KeyboardButton("🎮 Быстрый запуск"))
    markup.add(types.KeyboardButton("🔒 Блокировка и Окна"), types.KeyboardButton("🔌 Питание ПК"))
    return markup

def get_media_markup():
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("🔉 Тише (-6%)", callback_data="vol_down"),
        types.InlineKeyboardButton("🔇 Mute", callback_data="vol_mute"),
        types.InlineKeyboardButton("🔊 Громче (+6%)", callback_data="vol_up")
    )
    markup.row(
        types.InlineKeyboardButton("⏮️ Предыдущий", callback_data="vol_prev"),
        types.InlineKeyboardButton("⏯️ Пауза / Плей", callback_data="vol_play_pause"),
        types.InlineKeyboardButton("⏭️ Следующий", callback_data="vol_next")
    )
    markup.row(
        types.InlineKeyboardButton("🔊 Устройства вывода", callback_data="btn_audio_devices"),
        types.InlineKeyboardButton("🎚️ Микшер приложений", callback_data="btn_app_volumes")
    )
    markup.row(
        types.InlineKeyboardButton("🔄 Обновить статус", callback_data="vol_refresh")
    )
    return markup

# --- Telegram Bot Handlers ---

if bot:
    @bot.message_handler(commands=['start', 'help'])
    def send_welcome(message):
        user_id = str(message.chat.id)
        if not is_authorized_user(user_id):
            bot.send_message(user_id, "⛔ Доступ к боту ограничен.")
            return

        target_host = get_target_host(user_id)
        status_text = f"🟢 ПК онлайн: `{target_host}`" if target_host else "🔴 Ваш ПК сейчас не подключён."
        
        help_text = (
            f"👋 *Добро пожаловать в PC Remote Ecosystem!*\n\n"
            f"🆔 *Ваш Telegram ID:* `{user_id}`\n"
            f"🖥 *Статус:* {status_text}\n\n"
            f"📱 Нажмите кнопку ниже, чтобы открыть *мобильное приложение экосистемы* с AirSwipe, тачпадом, медиаплеером и выбором тем!"
        )
        bot.send_message(user_id, help_text, reply_markup=get_main_keyboard(), parse_mode="Markdown")

    @bot.message_handler(func=lambda message: message.text in [
        "📥 Установить приложения (ПК и Телефон)",
        "📥 Установить приложения",
        "📥 Установить",
        "Установить",
        "/install",
        "📥 Скачать приложения (ПК и Телефон)",
        "📥 Скачать приложения",
        "📥 Скачать APK на телефон",
        "📱 Скачать APK",
        "💻 Скачать агент на ПК",
        "📥 Скачать",
        "/download"
    ])
    def handle_download_apps(message):
        user_id = str(message.chat.id)
        if not is_authorized_user(user_id):
            return

        apk_url = "https://github.com/bazarovab07-cell/tg-pc-server/releases/download/v1.0-apk/TG_PC_Ecosystem.apk"
        exe_url = "https://github.com/bazarovab07-cell/tg-pc-server/raw/main/agent.exe"

        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton("📱 Установить APK на телефон (Android)", url=apk_url),
            types.InlineKeyboardButton("💻 Установить Агент на ПК (agent.exe)", url=exe_url),
            types.InlineKeyboardButton("📤 Прислать APK файлом в этот чат", callback_data="dl_send_apk"),
            types.InlineKeyboardButton("📤 Прислать EXE файлом в этот чат", callback_data="dl_send_exe")
        )

        caption = (
            "📦 *Центр установки приложений экосистемы PC Remote*\n\n"
            "Здесь вы можете скачать и установить приложения для компьютера и смартфона:\n\n"
            "📱 *1. На телефон (Android APK):*\n"
            "• Нативное приложение-пульт с тачпадом, AirSwipe свайпом файлов, плеером, системным мониторингом и выбором тем оформления.\n"
            f"• [Скачать TG_PC_Ecosystem.apk]({apk_url})\n\n"
            "💻 *2. На компьютер (Windows Agent):*\n"
            "• Высокоскоростной клиентский агент (`agent.exe`). Запустите его на ПК для связи с телефоном и Telegram.\n"
            f"• [Скачать agent.exe для Windows]({exe_url})\n\n"
            "✨ *Используйте кнопки ниже для перехода по ссылке или мгновенной отправки файла прямо в этот диалог!*"
        )
        bot.send_message(user_id, caption, reply_markup=markup, parse_mode="Markdown")

    @bot.message_handler(func=lambda message: message.text in ["📊 Мониторинг ПК", "📸 Скриншот экрана", "📸 Сделать скриншот", "📁 Проводник (Диски)", "⚙️ Диспетчер задач"])
    def handle_main_commands(message):
        user_id = str(message.chat.id)
        if not is_authorized_user(user_id):
            return

        target_host = get_target_host(user_id)
        if not target_host:
            bot.send_message(user_id, "❌ Твой ПК не подключен или находится офлайн. Запустите агент на компьютере.")
            return

        if message.text == "⚙️ Диспетчер задач":
            markup = types.InlineKeyboardMarkup()
            markup.add(
                types.InlineKeyboardButton("🖥 Открытые окна", callback_data="tasks_windowed"),
                types.InlineKeyboardButton("⚙️ Фоновые процессы", callback_data="tasks_background")
            )
            msg = bot.send_message(user_id, "⚙️ *Диспетчер задач*\nКакие процессы показать?", reply_markup=markup, parse_mode="Markdown")
            user_ui_messages[user_id] = msg.message_id
            return

        status_msg = bot.send_message(user_id, "⏳ Выполняю запрос на ПК...")
        user_ui_messages[user_id] = status_msg.message_id

        if message.text == "📊 Мониторинг ПК":
            add_command(target_host, "get_sysinfo")
        elif message.text in ["📸 Скриншот экрана", "📸 Сделать скриншот"]:
            add_command(target_host, "stream")
        elif message.text == "📁 Проводник (Диски)":
            add_command(target_host, "get_drives")

    @bot.message_handler(func=lambda message: message.text in ["🎵 Мультимедиа и Звук", "🎮 Быстрый запуск", "🎮 Запуск программ", "🔒 Блокировка и Окна", "🔌 Питание ПК", "🔌 Управление питанием"])
    def handle_menu_panels(message):
        user_id = str(message.chat.id)
        target_host = get_target_host(user_id)
        if not target_host:
            bot.send_message(user_id, "❌ Твой ПК не подключен или находится офлайн. Запустите агент на компьютере.")
            return

        markup = types.InlineKeyboardMarkup()

        if message.text == "🎵 Мультимедиа и Звук":
            cur_track = hosts_db.get(target_host, {}).get("media_info") or "Трек не обнаружен или плеер на паузе"
            text = f"🎵 *Управление звуком и медиаплеером:*\n\n🎧 *Сейчас играет:*\n▶️ `{cur_track}`"
            msg = bot.send_message(user_id, text, reply_markup=get_media_markup(), parse_mode="Markdown")
            media_ui_messages[user_id] = msg.message_id

        elif message.text in ["🎮 Быстрый запуск", "🎮 Запуск программ"]:
            markup.row(
                types.InlineKeyboardButton("🎮 Steam", callback_data="app_steam"),
                types.InlineKeyboardButton("💬 Discord", callback_data="app_discord"),
                types.InlineKeyboardButton("🌐 Браузер", callback_data="app_browser")
            )
            markup.row(
                types.InlineKeyboardButton("✈️ Telegram", callback_data="app_telegram"),
                types.InlineKeyboardButton("🧮 Калькулятор", callback_data="app_calc"),
                types.InlineKeyboardButton("📈 Диспетчер задач", callback_data="app_taskmgr")
            )
            bot.send_message(user_id, "🎮 *Быстрый запуск приложений:*", reply_markup=markup, parse_mode="Markdown")

        elif message.text == "🔒 Блокировка и Окна":
            markup.row(
                types.InlineKeyboardButton("🔒 Заблокировать экран", callback_data="act_lock"),
                types.InlineKeyboardButton("🪟 Свернуть все окна", callback_data="act_minimize_all")
            )
            markup.row(
                types.InlineKeyboardButton("📋 Прочитать буфер", callback_data="act_clip_get")
            )
            bot.send_message(user_id, "🔒 *Быстрые действия Windows:*", reply_markup=markup, parse_mode="Markdown")

        elif message.text in ["🔌 Питание ПК", "🔌 Управление питанием"]:
            markup.row(
                types.InlineKeyboardButton("💤 Спящий режим", callback_data="pow_sleep"),
                types.InlineKeyboardButton("🔄 Перезагрузка", callback_data="pow_restart"),
                types.InlineKeyboardButton("🛑 Выключение", callback_data="pow_shutdown")
            )
            markup.row(
                types.InlineKeyboardButton("❌ Отмена выключения", callback_data="pow_cancel")
            )
            bot.send_message(user_id, "🔌 *Управление питанием компьютера:*", reply_markup=markup, parse_mode="Markdown")

    @bot.callback_query_handler(func=lambda call: True)
    def handle_callbacks(call):
        user_id = str(call.message.chat.id)
        if not is_authorized_user(user_id):
            bot.answer_callback_query(call.id, "Доступ запрещен.")
            return

        data = call.data

        # Обработка скачивания приложений (не требует онлайн-статуса ПК)
        if data == "dl_send_apk":
            bot.answer_callback_query(call.id, "Отправляю APK файл...")
            apk_url = "https://github.com/bazarovab07-cell/tg-pc-server/releases/download/v1.0-apk/TG_PC_Ecosystem.apk"
            try:
                bot.send_document(
                    user_id,
                    apk_url,
                    caption="📱 *TG_PC_Ecosystem.apk*\nУстановите на ваш Android телефон для дистанционного управления ПК.",
                    parse_mode="Markdown"
                )
            except Exception:
                bot.send_message(user_id, f"🔗 Ссылка на скачивание Android APK:\n{apk_url}")
            return

        elif data == "dl_send_exe":
            bot.answer_callback_query(call.id, "Отправляю agent.exe...")
            exe_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent.exe")
            exe_url = "https://github.com/bazarovab07-cell/tg-pc-server/raw/main/agent.exe"
            if os.path.exists(exe_path):
                try:
                    with open(exe_path, "rb") as f:
                        bot.send_document(
                            user_id,
                            f,
                            caption="💻 *agent.exe (Клиентский агент)*\nЗапустите на вашем компьютере для подключения к экосистеме.",
                            parse_mode="Markdown"
                        )
                    return
                except Exception:
                    pass
            bot.send_message(user_id, f"🔗 Ссылка на скачивание agent.exe для ПК:\n{exe_url}")
            return

        target_host = get_target_host(user_id)
        if not target_host:
            bot.answer_callback_query(call.id, "❌ ПК отключен или офлайн.")
            return
        if data.startswith("tasks_") or data.startswith("explore_") or data.startswith("kill_task_"):
            user_ui_messages[user_id] = call.message.message_id

        if data.startswith("tasks_") and data != "tasks_menu_back":
            task_type = data.replace("tasks_", "")
            add_command(target_host, "get_tasks", task_type)
            type_name = "открытых окон" if task_type == "windowed" else "фоновых служб"
            bot.edit_message_text(f"⏳ Сбор данных {type_name}...", user_id, call.message.message_id)

        elif data == "tasks_menu_back":
            markup = types.InlineKeyboardMarkup()
            markup.add(
                types.InlineKeyboardButton("🖥 Открытые окна", callback_data="tasks_windowed"),
                types.InlineKeyboardButton("⚙️ Фоновые процессы", callback_data="tasks_background")
            )
            bot.edit_message_text("⚙️ *Диспетчер задач*\nКакие процессы показать?", user_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

        elif data.startswith("kill_task_"):
            task_name = data.replace("kill_task_", "")[:45]
            add_command(target_host, "kill_task", task_name)
            bot.answer_callback_query(call.id, f"Закрытие {task_name}...")
            bot.edit_message_text(f"⏳ Закрываю `{task_name}`...", user_id, call.message.message_id, parse_mode="Markdown")

        elif data.startswith("explore_drive_"):
            drive = data.replace("explore_drive_", "")[:10]
            add_command(target_host, "set_drive", drive)
            bot.edit_message_text(f"⏳ Чтение диска {drive}...", user_id, call.message.message_id)
            
        elif data.startswith("explore_cd_"):
            folder = data.replace("explore_cd_", "")[:80]
            add_command(target_host, "cd_into", folder)
            bot.edit_message_text(f"⏳ Открытие `{folder}`...", user_id, call.message.message_id, parse_mode="Markdown")
            
        elif data == "explore_back_":
            add_command(target_host, "cd_back", "")
            bot.edit_message_text("⏳ Переход назад...", user_id, call.message.message_id)

        elif data.startswith("vol_"):
            vol_act = data.replace("vol_", "")
            add_command(target_host, "volume", vol_act)
            media_ui_messages[user_id] = call.message.message_id
            track = hosts_db.get(target_host, {}).get("media_info")
            if track and vol_act in ["next", "prev", "play_pause", "refresh"]:
                bot.answer_callback_query(call.id, f"▶️ {track[:45]}")
            else:
                bot.answer_callback_query(call.id, f"Медиа: {vol_act}")

        elif data.startswith("app_"):
            app_name = data.replace("app_", "")
            add_command(target_host, "run_app", app_name)
            bot.answer_callback_query(call.id, f"🚀 Запуск: {app_name}")

        elif data == "act_lock":
            add_command(target_host, "quick_action", "lock")
            bot.answer_callback_query(call.id, "🔒 Блокировка экрана...")
        elif data == "act_minimize_all":
            add_command(target_host, "quick_action", "minimize_all")
            bot.answer_callback_query(call.id, "🪟 Окна свернуты.")
        elif data == "act_clip_get":
            add_command(target_host, "quick_action", "clipboard_get")
            bot.answer_callback_query(call.id, "📋 Чтение буфера обмена...")

        elif data.startswith("pow_"):
            pow_act = data.replace("pow_", "")
            add_command(target_host, "power", pow_act)
            bot.answer_callback_query(call.id, f"🔌 Питание: {pow_act}")

        elif data in ["btn_audio_devices", "refresh_audio_devs"]:
            add_command(target_host, "get_audio_devices")
            msg = bot.send_message(user_id, "⏳ Опрашиваю устройства вывода звука Windows...")
            user_ui_messages[user_id] = msg.message_id
            bot.answer_callback_query(call.id, "🔊 Поиск аудиоустройств...")

        elif data.startswith("auddev_"):
            lookup_key = data.replace("auddev_", "")
            target_dev = audio_devices_lookup.get(lookup_key, lookup_key)
            add_command(target_host, "set_audio_device", target_dev)
            bot.answer_callback_query(call.id, "Переключение аудиоустройства...")
            bot.edit_message_text("⏳ Переключаю аудиоустройство...", user_id, call.message.message_id)

        elif data in ["btn_app_volumes", "refresh_app_vols"]:
            add_command(target_host, "get_app_volumes")
            msg = bot.send_message(user_id, "⏳ Запрашиваю микшер приложений Windows...")
            user_ui_messages[user_id] = msg.message_id
            bot.answer_callback_query(call.id, "🎚️ Опрос микшера громкости...")

        elif data.startswith("avol_"):
            parts = data.split("_")
            if len(parts) >= 3:
                pid = parts[1]
                direction = parts[2]
                apps = latest_app_volumes.get(target_host, [])
                cur_vol = 100
                for a in apps:
                    if str(a.get("pid")) == str(pid):
                        cur_vol = a.get("volume", 100)
                        break
                new_vol = min(100, cur_vol + 10) if direction == "up" else max(0, cur_vol - 10)
                add_command(target_host, "set_app_volume", f"{pid}|{new_vol}")
                bot.answer_callback_query(call.id, f"Громкость: {new_vol}%")
                add_command(target_host, "get_app_volumes")

        elif data.startswith("amute_"):
            pid = data.replace("amute_", "")
            apps = latest_app_volumes.get(target_host, [])
            cur_muted = False
            for a in apps:
                if str(a.get("pid")) == str(pid):
                    cur_muted = a.get("muted", False)
                    break
            new_muted = "0" if cur_muted else "1"
            add_command(target_host, "mute_app", f"{pid}|{new_muted}")
            bot.answer_callback_query(call.id, "Звук переключен")
            add_command(target_host, "get_app_volumes")

        bot.answer_callback_query(call.id)

# --- Flask Routes: Mobile App & REST API ---

MOBILE_APP_HTML = '<!DOCTYPE html>\n<html lang="ru">\n<head>\n  <meta charset="UTF-8">\n  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">\n  <title>⚡ PC Ecosystem Remote</title>\n  <meta name="apple-mobile-web-app-capable" content="yes">\n  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">\n  <meta name="theme-color" content="#09090b">\n  <script src="https://telegram.org/js/telegram-web-app.js"></script>\n  <style>\n    :root { --bg-base: #09090b; --accent: #3b82f6; --text-main: #f4f4f5; }\n    body { background: #09090b; color: #fff; font-family: sans-serif; display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100vh; text-align: center; }\n  </style>\n</head>\n<body>\n  <h2>⚡ PC Ecosystem WebApp</h2>\n  <p>Загрузка шаблона...</p>\n</body>\n</html>'

@app.route('/', methods=['GET'])
@app.route('/app', methods=['GET'])
def serve_app():
    """Отдаёт красивое мобильное веб-приложение для телефона / Telegram WebApp."""
    template_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "index.html")
    if os.path.exists(template_path):
        try:
            with open(template_path, "r", encoding="utf-8") as f:
                return f.read(), 200, {"Content-Type": "text/html; charset=utf-8"}
        except Exception:
            pass
    return MOBILE_APP_HTML, 200, {"Content-Type": "text/html; charset=utf-8"}

@app.route('/download/apk', methods=['GET'])
def download_apk_redirect():
    """Перенаправляет на прямое скачивание Android APK файла."""
    return redirect("https://github.com/bazarovab07-cell/tg-pc-server/releases/download/v1.0-apk/TG_PC_Ecosystem.apk")

@app.route('/download/pc', methods=['GET'])
@app.route('/download/agent.exe', methods=['GET'])
def download_pc_exe():
    """Скачивание agent.exe для ПК."""
    exe_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent.exe")
    if os.path.exists(exe_path):
        return send_from_directory(os.path.dirname(os.path.abspath(__file__)), "agent.exe", as_attachment=True)
    return redirect("https://github.com/bazarovab07-cell/tg-pc-server/raw/main/agent.exe")

@app.route(f'/{BOT_TOKEN}', methods=['POST'])
def telegram_webhook():
    if not BOT_TOKEN:
        return 'Not configured', 500
    if WEBHOOK_SECRET:
        token = request.headers.get('X-Telegram-Bot-Api-Secret-Token', '')
        if token != WEBHOOK_SECRET:
            return 'Unauthorized', 401
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return '', 200
    return 'Forbidden', 403

@app.route('/api/status', methods=['GET'])
def api_status():
    user_id = sanitize_id(request.args.get("user_id") or request.args.get("device_id") or request.args.get("host_id"))
    target_host = get_target_host(user_id)
    online = bool(target_host)
    host_info = hosts_db.get(target_host, {}) if target_host else {}
    metrics = latest_metrics.get(target_host) or (latest_metrics.get(user_id) if user_id else {}) or {}
    return jsonify({
        "online": online,
        "host_id": target_host or "",
        "media_info": host_info.get("media_info", ""),
        "metrics": metrics
    }), 200

@app.route('/api/devices', methods=['GET'])
def api_devices():
    now = time.time()
    devices = []
    for hid, info in hosts_db.items():
        is_online = (now - info.get("last_seen", 0)) < 70
        devices.append({
            "host_id": hid,
            "name": hid,
            "online": is_online,
            "last_seen": int(info.get("last_seen", 0)),
            "media_info": info.get("media_info", ""),
            "user_id": info.get("user_id", "")
        })
    return jsonify(devices), 200

@app.route('/api/command', methods=['POST'])
def api_command():
    data = request.json or {}
    user_id = sanitize_id(data.get("user_id") or data.get("device_id"))
    action = data.get("action", "").strip()
    action_data = str(data.get("data", "")).strip()

    if not action:
        return jsonify({"status": "error", "error": "Неверные параметры"}), 400

    target_host = get_target_host(user_id)
    if not target_host:
        return jsonify({"status": "error", "error": "Ваш ПК сейчас офлайн"}), 400

    add_command(target_host, action, action_data)
    return jsonify({"status": "ok", "message": f"Команда {action} отправлена!"}), 200

@app.route('/api/screenshot', methods=['GET'])
def api_get_screenshot():
    user_id = sanitize_id(request.args.get("user_id") or request.args.get("device_id"))
    target_host = get_target_host(user_id)
    img_bytes = latest_screenshots.get(target_host) or (latest_screenshots.get(user_id) if user_id else None)
    if img_bytes:
        return img_bytes, 200, {"Content-Type": "image/jpeg", "Cache-Control": "no-cache"}
    return "No screenshot", 404

@app.route('/api/tasks', methods=['GET'])
def api_get_tasks():
    user_id = sanitize_id(request.args.get("user_id") or request.args.get("device_id"))
    target_host = get_target_host(user_id)
    tasks = latest_tasks.get(target_host) or (latest_tasks.get(user_id) if user_id else [])
    return jsonify(tasks), 200

@app.route('/api/audio_devices', methods=['GET'])
def api_get_audio_devices():
    user_id = sanitize_id(request.args.get("user_id") or request.args.get("device_id"))
    target_host = get_target_host(user_id)
    devs = latest_audio_devices.get(target_host) or (latest_audio_devices.get(user_id) if user_id else [])
    return jsonify(devs), 200

@app.route('/api/set_audio_device', methods=['POST'])
def api_set_audio_device():
    data = request.json or {}
    user_id = sanitize_id(data.get("user_id") or data.get("device_id"))
    device_id = str(data.get("device_id", "")).strip()
    target_host = get_target_host(user_id)
    if not target_host:
        return jsonify({"status": "error", "error": "ПК не в сети"}), 400
    if not device_id:
        return jsonify({"status": "error", "error": "device_id не указан"}), 400
    add_command(target_host, "set_audio_device", device_id)
    return jsonify({"status": "ok", "message": "Запрос на смену аудиоустройства отправлен"}), 200

@app.route('/api/app_volumes', methods=['GET'])
def api_get_app_volumes():
    user_id = sanitize_id(request.args.get("user_id") or request.args.get("device_id"))
    target_host = get_target_host(user_id)
    apps = latest_app_volumes.get(target_host) or (latest_app_volumes.get(user_id) if user_id else [])
    return jsonify(apps), 200

@app.route('/api/set_app_volume', methods=['POST'])
def api_set_app_volume():
    data = request.json or {}
    user_id = sanitize_id(data.get("user_id") or data.get("device_id"))
    target_host = get_target_host(user_id)
    if not target_host:
        return jsonify({"status": "error", "error": "ПК не в сети"}), 400
    pid = str(data.get("pid", "")).strip()
    vol = int(data.get("volume", 100))
    add_command(target_host, "set_app_volume", f"{pid}|{vol}")
    return jsonify({"status": "ok", "message": "Громкость изменена"}), 200

@app.route('/api/mute_app', methods=['POST'])
def api_mute_app():
    data = request.json or {}
    user_id = sanitize_id(data.get("user_id") or data.get("device_id"))
    target_host = get_target_host(user_id)
    if not target_host:
        return jsonify({"status": "error", "error": "ПК не в сети"}), 400
    pid = str(data.get("pid", "")).strip()
    mute = "1" if data.get("mute") else "0"
    add_command(target_host, "mute_app", f"{pid}|{mute}")
    return jsonify({"status": "ok", "message": "Звук переключен"}), 200

@app.route('/api/upload_file', methods=['POST'])
def api_upload_file():
    user_id = sanitize_id(request.form.get("user_id") or request.form.get("device_id"))
    target_host = get_target_host(user_id)
    if not target_host:
        return jsonify({"status": "error", "error": "ПК не подключён или офлайн"}), 400

    if 'file' not in request.files:
        return jsonify({"status": "error", "error": "Файл не выбран"}), 400

    f = request.files['file']
    filename = os.path.basename(f.filename) if f.filename else "file.bin"
    file_id = f"{int(time.time())}_{uuid.uuid4().hex[:6]}"
    stored_name = f"{file_id}_{filename}"
    save_path = os.path.join(UPLOAD_FOLDER, stored_name)
    f.save(save_path)

    add_command(target_host, "receive_file", f"{file_id}|{filename}")
    return jsonify({"status": "ok", "message": f"Файл {filename} отправлен на ПК!"}), 200

@app.route('/api/download_uploaded_file', methods=['GET'])
def api_download_uploaded_file():
    file_id = sanitize_id(request.args.get("file_id"))
    if not file_id:
        return "Bad Request", 400
    for fn in os.listdir(UPLOAD_FOLDER):
        if fn.startswith(file_id + "_"):
            return send_from_directory(UPLOAD_FOLDER, fn, as_attachment=True)
    return "Not Found", 404

# --- Agent Poll & Communication ---

@app.route('/poll', methods=['POST'])
def poll():
    if not verify_agent_auth(request):
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json or {}
    host_id = sanitize_id(data.get("host_id"))
    user_id = sanitize_id(data.get("user_id")) or host_id
    
    if not host_id: 
        return jsonify({"error": "Host ID required"}), 400
    
    if ALLOWED_USERS and user_id in ALLOWED_USERS:
        pass
    elif ALLOWED_USERS and host_id not in ALLOWED_USERS and user_id not in ALLOWED_USERS:
        return jsonify({"error": "User not allowed"}), 403

    prev_info = hosts_db.get(host_id, {})
    hosts_db[host_id] = {
        "user_id": user_id, 
        "status": "online", 
        "last_seen": time.time(),
        "media_info": data.get("media_info") or prev_info.get("media_info", "")
    }

    # Регулярный сбор метрик из любого опроса
    if data.get("metrics"):
        latest_metrics[host_id] = data["metrics"]
        if user_id:
            latest_metrics[user_id] = data["metrics"]

    cur_track = data.get("media_info")
    if cur_track:
        old_track = prev_info.get("media_info", "")
        media_msg_id = media_ui_messages.get(user_id)
        if media_msg_id and bot and (cur_track != old_track or data.get("event_type") == "media_refresh"):
            try:
                text = f"🎵 *Управление звуком и медиаплеером:*\n\n🎧 *Сейчас играет:*\n▶️ `{cur_track}`"
                bot.edit_message_text(text, user_id, media_msg_id, reply_markup=get_media_markup(), parse_mode="Markdown")
            except Exception:
                pass

    if data.get("event_type") == "sysinfo_ready":
        metrics = data.get("items", {})
        latest_metrics[host_id] = metrics
        if user_id:
            latest_metrics[user_id] = metrics
        if bot and user_id and user_id.isdigit():
            ram_load = metrics.get("ram_load", 0)
            ram_used = metrics.get("ram_used", 0)
            ram_total = metrics.get("ram_total", 0)
            uptime = metrics.get("uptime", "N/A")
            drives = metrics.get("drives", [])
            drives_text = "\n".join([f"  • `{d}`" for d in drives]) if drives else "  • Данные отсутствуют"

            text = (
                f"📊 *Системный мониторинг: `{host_id}`*\n\n"
                f"⏱️ *Время работы ПК:* `{uptime}`\n"
                f"🧠 *Оперативная память:* `{ram_load}%` ({ram_used} GB / {ram_total} GB)\n"
                f"💾 *Диски ПК:*\n{drives_text}\n"
            )
            ui_msg_id = user_ui_messages.get(user_id)
            try:
                if ui_msg_id:
                    bot.edit_message_text(text, user_id, ui_msg_id, parse_mode="Markdown")
                else:
                    bot.send_message(user_id, text, parse_mode="Markdown")
            except Exception:
                pass

    elif data.get("event_type") == "tasks_ready":
        tasks = data.get("items", [])
        latest_tasks[host_id] = tasks
        if user_id:
            latest_tasks[user_id] = tasks
        if bot and user_id and user_id.isdigit():
            markup = types.InlineKeyboardMarkup(row_width=1)
            if not tasks:
                text = "🖥 *Открытые приложения не найдены.*\nВсе окна свернуты или закрыты."
            else:
                text = "🖥 *Открытые приложения пользователя:*\nНажмите кнопку для закрытия нужного окна:\n\n"
                for task in tasks[:15]:
                    display_name = task.get("display", task.get("name", ""))[:45]
                    proc_name = task.get("name", "")[:35]
                    text += f"• `{display_name}`\n"
                    clean_btn = f"❌ Закрыть {display_name[:24]}"
                    markup.add(types.InlineKeyboardButton(clean_btn, callback_data=f"kill_task_{proc_name}"))
            markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="tasks_menu_back"))

            ui_msg_id = user_ui_messages.get(user_id)
            try:
                if ui_msg_id:
                    bot.edit_message_text(text, user_id, ui_msg_id, reply_markup=markup, parse_mode="Markdown")
                else:
                    bot.send_message(user_id, text, reply_markup=markup, parse_mode="Markdown")
            except Exception:
                pass

    elif data.get("event_type") == "audio_devices_ready":
        devs = data.get("items") or []
        latest_audio_devices[host_id] = devs
        if user_id:
            latest_audio_devices[user_id] = devs
        if bot and user_id and user_id.isdigit():
            markup = types.InlineKeyboardMarkup(row_width=1)
            for idx, d in enumerate(devs[:12]):
                d_id = d.get("id", "")
                d_name = d.get("name", "Аудиоустройство")
                is_def = d.get("is_default", False)
                key = f"{host_id[:6]}_{idx}"
                audio_devices_lookup[key] = d_id
                label = f"✓ {d_name}" if is_def else f"🔈 {d_name}"
                markup.add(types.InlineKeyboardButton(label, callback_data=f"auddev_{key}"))
            markup.add(types.InlineKeyboardButton("🔄 Обновить список", callback_data="refresh_audio_devs"))
            text = "🔊 *Выберите устройство аудиовывода Windows:*"
            ui_msg_id = user_ui_messages.get(user_id)
            try:
                if ui_msg_id:
                    bot.edit_message_text(text, user_id, ui_msg_id, reply_markup=markup, parse_mode="Markdown")
                else:
                    bot.send_message(user_id, text, reply_markup=markup, parse_mode="Markdown")
            except Exception:
                pass

    elif data.get("event_type") == "app_volumes_ready":
        apps = data.get("items") or []
        latest_app_volumes[host_id] = apps
        if user_id:
            latest_app_volumes[user_id] = apps
        if bot and user_id and user_id.isdigit():
            markup = types.InlineKeyboardMarkup()
            if not apps:
                text = "🎚️ *Активных приложений со звуком не обнаружено.*"
                markup.add(types.InlineKeyboardButton("🔄 Обновить", callback_data="refresh_app_vols"))
            else:
                text = "🎚️ *Микшер громкости приложений Windows:*\nНажимайте ➖ / ➕ для изменения громкости:"
                for a in apps[:8]:
                    pid = a.get("pid", 0)
                    name = a.get("name", "App")
                    vol = a.get("volume", 100)
                    muted = a.get("muted", False)
                    mute_icon = "🔇" if muted else "🔊"
                    markup.row(
                        types.InlineKeyboardButton(f"{name}: {vol}%", callback_data="noop"),
                        types.InlineKeyboardButton("➖", callback_data=f"avol_{pid}_down"),
                        types.InlineKeyboardButton("➕", callback_data=f"avol_{pid}_up"),
                        types.InlineKeyboardButton(mute_icon, callback_data=f"amute_{pid}")
                    )
                markup.add(types.InlineKeyboardButton("🔄 Обновить список", callback_data="refresh_app_vols"))
            ui_msg_id = user_ui_messages.get(user_id)
            try:
                if ui_msg_id:
                    bot.edit_message_text(text, user_id, ui_msg_id, reply_markup=markup, parse_mode="Markdown")
                else:
                    bot.send_message(user_id, text, reply_markup=markup, parse_mode="Markdown")
            except Exception:
                pass

    elif data.get("report") and bot and user_id and user_id.isdigit():
        rep = data.get("report")
        if not rep.startswith("Медиа:"):
            try:
                msg = bot.send_message(user_id, f"💬 {rep}")
                delete_message_after(user_id, msg.message_id, delay=3.0)
            except Exception:
                pass

    cmds = commands_queue.get(host_id, [])
    commands_queue[host_id] = []
    return jsonify({"status": "ok", "commands": cmds}), 200

@app.route('/upload_screenshot', methods=['POST'])
def upload_screenshot():
    if not verify_agent_auth(request):
        return "Unauthorized", 401

    host_id = sanitize_id(request.args.get("host_id"))
    user_id = sanitize_id(request.args.get("user_id")) or host_id
    
    if not host_id:
        return "Forbidden", 403

    if request.data:
        latest_screenshots[host_id] = request.data
        if user_id:
            latest_screenshots[user_id] = request.data
            
        ui_msg_id = user_ui_messages.get(user_id)
        if ui_msg_id and bot and user_id.isdigit():
            try: bot.delete_message(user_id, ui_msg_id)
            except Exception: pass
            
        if bot and user_id.isdigit():
            try:
                image_stream = io.BytesIO(request.data)
                image_stream.name = f"screenshot_{host_id}.jpg"
                bot.send_photo(user_id, image_stream, caption=f"📸 Скриншот экрана: `{host_id}`", parse_mode="Markdown")
            except Exception:
                pass
        return "OK", 200
    return "Error", 400

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, threaded=True)
