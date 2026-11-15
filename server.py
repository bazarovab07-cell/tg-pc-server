import os
import io
import time
import logging
import re
import uuid
import threading
import telebot
from telebot import types
from flask import Flask, request, jsonify, send_from_directory

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

def get_target_host(user_id: str):
    return next((hid for hid, info in hosts_db.items() if info.get("user_id") == str(user_id) and (time.time() - info.get("last_seen", 0)) < 60), None)

def add_command(host_id: str, action: str, data: str = ""):
    q = commands_queue.setdefault(host_id, [])
    if len(q) < 25:
        q.append({"action": action, "data": data})

def get_main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    if RENDER_URL:
        markup.add(types.KeyboardButton("📱 Открыть приложение экосистемы", web_app=types.WebAppInfo(url=RENDER_URL)))
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
        types.InlineKeyboardButton("🔄 Обновить трек", callback_data="vol_refresh")
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

        target_host = get_target_host(user_id)
        if not target_host:
            bot.answer_callback_query(call.id, "❌ ПК отключен или офлайн.")
            return

        data = call.data
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

        bot.answer_callback_query(call.id)

# --- Flask Routes: Mobile App & REST API ---
MOBILE_APP_HTML = '<!DOCTYPE html>\n<html lang="ru">\n<head>\n  <meta charset="UTF-8">\n  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">\n  <title>⚡ PC Ecosystem Remote</title>\n  <meta name="apple-mobile-web-app-capable" content="yes">\n  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">\n  <meta name="theme-color" content="#09090b">\n  <script src="https://telegram.org/js/telegram-web-app.js"></script>\n  <style>\n    :root {\n      --bg-base: #09090b;\n      --bg-surface: #141417;\n      --bg-card: rgba(26, 26, 31, 0.75);\n      --bg-card-hover: rgba(36, 36, 44, 0.85);\n      --accent: #3b82f6;\n      --accent-glow: rgba(59, 130, 246, 0.4);\n      --accent-gradient: linear-gradient(135deg, #3b82f6 0%, #1d4ed8 100%);\n      --text-main: #f4f4f5;\n      --text-muted: #a1a1aa;\n      --border-color: rgba(255, 255, 255, 0.08);\n      --success: #22c55e;\n      --danger: #ef4444;\n      --warning: #f59e0b;\n      --radius: 16px;\n      --safe-bottom: env(safe-area-inset-bottom, 16px);\n    }\n\n    body.theme-purple {\n      --bg-base: #0a0412;\n      --bg-surface: #130a21;\n      --bg-card: rgba(29, 14, 51, 0.75);\n      --bg-card-hover: rgba(43, 21, 77, 0.85);\n      --accent: #a855f7;\n      --accent-glow: rgba(168, 85, 247, 0.45);\n      --accent-gradient: linear-gradient(135deg, #ec4899 0%, #a855f7 100%);\n    }\n\n    body.theme-matrix {\n      --bg-base: #040d07;\n      --bg-surface: #0a180f;\n      --bg-card: rgba(12, 33, 20, 0.75);\n      --bg-card-hover: rgba(18, 51, 31, 0.85);\n      --accent: #22c55e;\n      --accent-glow: rgba(34, 197, 94, 0.45);\n      --accent-gradient: linear-gradient(135deg, #22c55e 0%, #15803d 100%);\n    }\n\n    body.theme-amber {\n      --bg-base: #0d0a04;\n      --bg-surface: #18140a;\n      --bg-card: rgba(36, 29, 12, 0.75);\n      --bg-card-hover: rgba(54, 43, 18, 0.85);\n      --accent: #f59e0b;\n      --accent-glow: rgba(245, 158, 11, 0.45);\n      --accent-gradient: linear-gradient(135deg, #f59e0b 0%, #b45309 100%);\n    }\n\n    body.theme-crimson {\n      --bg-base: #0d0407;\n      --bg-surface: #18090e;\n      --bg-card: rgba(36, 12, 20, 0.75);\n      --bg-card-hover: rgba(56, 19, 31, 0.85);\n      --accent: #f43f5e;\n      --accent-glow: rgba(244, 63, 94, 0.45);\n      --accent-gradient: linear-gradient(135deg, #f43f5e 0%, #be123c 100%);\n    }\n\n    * {\n      box-sizing: border-box;\n      margin: 0;\n      padding: 0;\n      -webkit-tap-highlight-color: transparent;\n      user-select: none;\n      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;\n    }\n\n    body {\n      background-color: var(--bg-base);\n      color: var(--text-main);\n      min-height: 100vh;\n      display: flex;\n      flex-direction: column;\n      overflow-x: hidden;\n      padding-bottom: calc(75px + var(--safe-bottom));\n      transition: background-color 0.3s ease;\n    }\n\n    .top-bar {\n      position: sticky;\n      top: 0;\n      z-index: 50;\n      background: rgba(9, 9, 11, 0.85);\n      backdrop-filter: blur(16px);\n      -webkit-backdrop-filter: blur(16px);\n      border-bottom: 1px solid var(--border-color);\n      padding: 14px 18px;\n      display: flex;\n      align-items: center;\n      justify-content: space-between;\n    }\n\n    .brand-logo {\n      font-size: 20px;\n      background: var(--accent-gradient);\n      -webkit-background-clip: text;\n      -webkit-text-fill-color: transparent;\n      font-weight: 900;\n      letter-spacing: 0.5px;\n    }\n\n    .status-badge {\n      display: flex;\n      align-items: center;\n      gap: 6px;\n      background: rgba(255, 255, 255, 0.05);\n      padding: 6px 12px;\n      border-radius: 20px;\n      font-size: 12px;\n      font-weight: 600;\n      border: 1px solid var(--border-color);\n    }\n\n    .status-dot {\n      width: 8px;\n      height: 8px;\n      border-radius: 50%;\n      background: var(--danger);\n      box-shadow: 0 0 8px var(--danger);\n      transition: all 0.3s ease;\n    }\n\n    .status-dot.online {\n      background: var(--success);\n      box-shadow: 0 0 10px var(--success);\n    }\n\n    .view-container {\n      flex: 1;\n      padding: 16px;\n      max-width: 580px;\n      margin: 0 auto;\n      width: 100%;\n    }\n\n    .view-page {\n      display: none;\n      animation: fadeIn 0.25s ease forwards;\n    }\n\n    .view-page.active {\n      display: block;\n    }\n\n    @keyframes fadeIn {\n      from { opacity: 0; transform: translateY(6px); }\n      to { opacity: 1; transform: translateY(0); }\n    }\n\n    .card {\n      background: var(--bg-card);\n      backdrop-filter: blur(14px);\n      -webkit-backdrop-filter: blur(14px);\n      border: 1px solid var(--border-color);\n      border-radius: var(--radius);\n      padding: 18px;\n      margin-bottom: 14px;\n      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);\n    }\n\n    .card-title {\n      font-size: 13px;\n      font-weight: 700;\n      text-transform: uppercase;\n      letter-spacing: 0.8px;\n      color: var(--text-muted);\n      margin-bottom: 12px;\n      display: flex;\n      align-items: center;\n      justify-content: space-between;\n    }\n\n    .metric-grid {\n      display: grid;\n      grid-template-columns: repeat(2, 1fr);\n      gap: 10px;\n    }\n\n    .metric-box {\n      background: rgba(255, 255, 255, 0.03);\n      border: 1px solid var(--border-color);\n      border-radius: 12px;\n      padding: 12px;\n    }\n\n    .metric-label {\n      font-size: 11px;\n      color: var(--text-muted);\n      margin-bottom: 4px;\n    }\n\n    .metric-value {\n      font-size: 18px;\n      font-weight: 700;\n      color: var(--text-main);\n    }\n\n    .progress-bar-bg {\n      width: 100%;\n      height: 6px;\n      background: rgba(255, 255, 255, 0.08);\n      border-radius: 3px;\n      margin-top: 6px;\n      overflow: hidden;\n    }\n\n    .progress-bar-fill {\n      height: 100%;\n      width: 0%;\n      background: var(--accent-gradient);\n      border-radius: 3px;\n      transition: width 0.4s ease;\n    }\n\n    .media-card {\n      background: linear-gradient(135deg, rgba(255, 255, 255, 0.04) 0%, rgba(255, 255, 255, 0.01) 100%), var(--bg-card);\n      border: 1px solid var(--accent-glow);\n    }\n\n    .track-info {\n      display: flex;\n      align-items: center;\n      gap: 14px;\n      margin-bottom: 14px;\n    }\n\n    .track-art {\n      width: 50px;\n      height: 50px;\n      border-radius: 12px;\n      background: var(--accent-gradient);\n      display: flex;\n      align-items: center;\n      justify-content: center;\n      font-size: 24px;\n      box-shadow: 0 4px 16px var(--accent-glow);\n    }\n\n    .track-details {\n      flex: 1;\n      overflow: hidden;\n    }\n\n    .track-title {\n      font-size: 15px;\n      font-weight: 700;\n      white-space: nowrap;\n      overflow: hidden;\n      text-overflow: ellipsis;\n    }\n\n    .track-status {\n      font-size: 12px;\n      color: var(--accent);\n      margin-top: 2px;\n    }\n\n    .media-buttons {\n      display: flex;\n      align-items: center;\n      justify-content: space-between;\n      gap: 8px;\n    }\n\n    .btn {\n      background: rgba(255, 255, 255, 0.06);\n      border: 1px solid var(--border-color);\n      color: var(--text-main);\n      border-radius: 12px;\n      padding: 12px 14px;\n      font-size: 14px;\n      font-weight: 600;\n      cursor: pointer;\n      display: inline-flex;\n      align-items: center;\n      justify-content: center;\n      gap: 8px;\n      transition: all 0.15s ease;\n      touch-action: manipulation;\n    }\n\n    .btn:active {\n      transform: scale(0.96);\n      background: rgba(255, 255, 255, 0.12);\n    }\n\n    .btn-primary {\n      background: var(--accent-gradient);\n      border: none;\n      color: #fff;\n      box-shadow: 0 4px 16px var(--accent-glow);\n    }\n\n    .btn-circle {\n      width: 44px;\n      height: 44px;\n      border-radius: 50%;\n      padding: 0;\n      font-size: 18px;\n    }\n\n    .btn-circle.large {\n      width: 54px;\n      height: 54px;\n      font-size: 22px;\n    }\n\n    .btn-danger {\n      background: rgba(239, 68, 68, 0.15);\n      border-color: rgba(239, 68, 68, 0.3);\n      color: #fca5a5;\n    }\n\n    .btn-full {\n      width: 100%;\n    }\n\n    .action-grid {\n      display: grid;\n      grid-template-columns: repeat(3, 1fr);\n      gap: 10px;\n    }\n\n    .action-btn {\n      display: flex;\n      flex-direction: column;\n      align-items: center;\n      justify-content: center;\n      gap: 6px;\n      padding: 14px 6px;\n      background: rgba(255, 255, 255, 0.03);\n      border: 1px solid var(--border-color);\n      border-radius: 12px;\n      color: var(--text-main);\n      font-size: 11px;\n      font-weight: 600;\n      cursor: pointer;\n    }\n\n    .action-btn:active {\n      transform: scale(0.95);\n      background: rgba(255, 255, 255, 0.09);\n    }\n\n    .action-icon {\n      font-size: 24px;\n    }\n\n    .airswipe-container {\n      display: flex;\n      flex-direction: column;\n      align-items: center;\n      justify-content: center;\n      min-height: 400px;\n    }\n\n    .airswipe-card {\n      width: 100%;\n      max-width: 340px;\n      background: var(--bg-card);\n      border: 2px dashed var(--accent);\n      border-radius: 24px;\n      padding: 30px 20px;\n      display: flex;\n      flex-direction: column;\n      align-items: center;\n      text-align: center;\n      box-shadow: 0 10px 30px var(--accent-glow);\n      cursor: pointer;\n      transition: transform 0.25s cubic-bezier(0.175, 0.885, 0.32, 1.275), opacity 0.25s ease;\n      touch-action: none;\n    }\n\n    .airswipe-icon-box {\n      width: 80px;\n      height: 80px;\n      border-radius: 50%;\n      background: var(--accent-gradient);\n      display: flex;\n      align-items: center;\n      justify-content: center;\n      font-size: 38px;\n      margin-bottom: 16px;\n      box-shadow: 0 6px 20px var(--accent-glow);\n      animation: pulse 2s infinite ease-in-out;\n    }\n\n    @keyframes pulse {\n      0%, 100% { transform: scale(1); }\n      50% { transform: scale(1.06); }\n    }\n\n    .airswipe-title {\n      font-size: 18px;\n      font-weight: 700;\n      margin-bottom: 6px;\n    }\n\n    .airswipe-desc {\n      font-size: 13px;\n      color: var(--text-muted);\n      line-height: 1.4;\n      margin-bottom: 20px;\n    }\n\n    .swipe-indicator {\n      display: flex;\n      flex-direction: column;\n      align-items: center;\n      gap: 4px;\n      color: var(--accent);\n      font-size: 12px;\n      font-weight: 700;\n      letter-spacing: 0.5px;\n      animation: floatUp 1.4s infinite ease-in-out;\n    }\n\n    @keyframes floatUp {\n      0%, 100% { transform: translateY(0); opacity: 0.8; }\n      50% { transform: translateY(-8px); opacity: 1; }\n    }\n\n    .touchpad-surface {\n      width: 100%;\n      height: 300px;\n      background: rgba(255, 255, 255, 0.02);\n      border: 1px solid var(--border-color);\n      border-radius: var(--radius);\n      display: flex;\n      align-items: center;\n      justify-content: center;\n      flex-direction: column;\n      color: var(--text-muted);\n      font-size: 13px;\n      position: relative;\n      touch-action: none;\n      cursor: crosshair;\n    }\n\n    .touchpad-buttons {\n      display: grid;\n      grid-template-columns: 1fr 1fr;\n      gap: 10px;\n      margin-top: 10px;\n    }\n\n    .task-item {\n      display: flex;\n      align-items: center;\n      justify-content: space-between;\n      padding: 12px 14px;\n      background: rgba(255, 255, 255, 0.02);\n      border: 1px solid var(--border-color);\n      border-radius: 12px;\n      margin-bottom: 8px;\n    }\n\n    .task-info {\n      overflow: hidden;\n      padding-right: 10px;\n    }\n\n    .task-title {\n      font-size: 14px;\n      font-weight: 600;\n      white-space: nowrap;\n      overflow: hidden;\n      text-overflow: ellipsis;\n    }\n\n    .task-exe {\n      font-size: 11px;\n      color: var(--text-muted);\n    }\n\n    .theme-grid {\n      display: grid;\n      grid-template-columns: repeat(2, 1fr);\n      gap: 10px;\n    }\n\n    .theme-btn {\n      padding: 14px;\n      border-radius: 14px;\n      border: 2px solid transparent;\n      background: rgba(255, 255, 255, 0.04);\n      color: var(--text-main);\n      font-weight: 600;\n      display: flex;\n      align-items: center;\n      gap: 10px;\n      cursor: pointer;\n    }\n\n    .theme-btn.active {\n      border-color: var(--accent);\n      background: rgba(255, 255, 255, 0.08);\n      box-shadow: 0 4px 14px var(--accent-glow);\n    }\n\n    .theme-color-preview {\n      width: 20px;\n      height: 20px;\n      border-radius: 50%;\n    }\n\n    .bottom-nav {\n      position: fixed;\n      bottom: 0;\n      left: 0;\n      right: 0;\n      z-index: 100;\n      background: rgba(14, 14, 18, 0.9);\n      backdrop-filter: blur(20px);\n      -webkit-backdrop-filter: blur(20px);\n      border-top: 1px solid var(--border-color);\n      display: flex;\n      justify-content: space-around;\n      padding: 8px 10px calc(8px + var(--safe-bottom));\n    }\n\n    .nav-item {\n      display: flex;\n      flex-direction: column;\n      align-items: center;\n      gap: 4px;\n      color: var(--text-muted);\n      font-size: 11px;\n      font-weight: 600;\n      cursor: pointer;\n      padding: 6px 12px;\n      border-radius: 12px;\n      transition: all 0.2s ease;\n    }\n\n    .nav-item.active {\n      color: var(--accent);\n      transform: translateY(-2px);\n    }\n\n    .nav-icon {\n      font-size: 20px;\n    }\n\n    .toast {\n      position: fixed;\n      top: 20px;\n      left: 50%;\n      transform: translateX(-50%) translateY(-100px);\n      background: rgba(24, 24, 27, 0.95);\n      backdrop-filter: blur(12px);\n      border: 1px solid var(--accent);\n      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.5), 0 0 16px var(--accent-glow);\n      color: #fff;\n      padding: 12px 20px;\n      border-radius: 30px;\n      font-size: 13px;\n      font-weight: 600;\n      z-index: 1000;\n      transition: transform 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275);\n      display: flex;\n      align-items: center;\n      gap: 8px;\n    }\n\n    .toast.show {\n      transform: translateX(-50%) translateY(0);\n    }\n  </style>\n</head>\n<body>\n  <header class="top-bar">\n    <div class="brand"><span class="brand-logo">⚡ ECOSYSTEM</span></div>\n    <div class="status-badge"><span class="status-dot" id="statusDot"></span><span id="hostName">Ожидание...</span></div>\n  </header>\n\n  <div class="toast" id="toast"><span id="toastIcon">✨</span><span id="toastMsg">Готово</span></div>\n\n  <main class="view-container">\n    <!-- 1. Главная (Dashboard) -->\n    <section class="view-page active" id="viewDashboard">\n      <div class="card media-card">\n        <div class="track-info">\n          <div class="track-art" id="mediaArt">🎵</div>\n          <div class="track-details">\n            <div class="track-title" id="trackTitle">Ничего не играет</div>\n            <div class="track-status" id="trackStatus">Медиаплеер Windows</div>\n          </div>\n        </div>\n        <div class="media-buttons">\n          <button class="btn btn-circle" onclick="sendCmd(\'volume\', \'prev\')">⏮️</button>\n          <button class="btn btn-circle large btn-primary" onclick="sendCmd(\'volume\', \'play_pause\')">⏯️</button>\n          <button class="btn btn-circle" onclick="sendCmd(\'volume\', \'next\')">⏭️</button>\n          <button class="btn btn-circle" onclick="sendCmd(\'volume\', \'down\')">🔉</button>\n          <button class="btn btn-circle" onclick="sendCmd(\'volume\', \'up\')">🔊</button>\n          <button class="btn btn-circle" onclick="sendCmd(\'volume\', \'mute\')">🔇</button>\n        </div>\n      </div>\n\n      <div class="card">\n        <div class="card-title"><span>📊 Состояние компьютера</span><span id="uptimeText" style="font-weight:400; text-transform:none;">--:--</span></div>\n        <div class="metric-grid">\n          <div class="metric-box">\n            <div class="metric-label">Оперативная память</div>\n            <div class="metric-value" id="ramValue">0%</div>\n            <div class="progress-bar-bg"><div class="progress-bar-fill" id="ramProgress"></div></div>\n          </div>\n          <div class="metric-box">\n            <div class="metric-label">Пинг с сервером</div>\n            <div class="metric-value" id="pingValue">-- мс</div>\n          </div>\n        </div>\n      </div>\n\n      <div class="card">\n        <div class="card-title">⚡ Быстрое управление</div>\n        <div class="action-grid">\n          <div class="action-btn" onclick="sendCmd(\'quick_action\', \'lock\')"><span class="action-icon">🔒</span><span>Блок экрана</span></div>\n          <div class="action-btn" onclick="sendCmd(\'quick_action\', \'minimize_all\')"><span class="action-icon">🪟</span><span>Свернуть</span></div>\n          <div class="action-btn" onclick="fetchScreenshot()"><span class="action-icon">📸</span><span>Скриншот</span></div>\n          <div class="action-btn" onclick="sendCmd(\'run_app\', \'steam\')"><span class="action-icon">🎮</span><span>Steam</span></div>\n          <div class="action-btn" onclick="sendCmd(\'run_app\', \'browser\')"><span class="action-icon">🌐</span><span>Браузер</span></div>\n          <div class="action-btn" onclick="sendCmd(\'power\', \'sleep\')"><span class="action-icon">💤</span><span>Сон</span></div>\n        </div>\n      </div>\n\n      <div class="card" id="screenCard" style="display:none;">\n        <div class="card-title"><span>📸 Экран ПК</span><button class="btn" style="padding:4px 10px; font-size:11px;" onclick="fetchScreenshot()">Обновить</button></div>\n        <img id="screenImg" src="" style="width:100%; border-radius:10px; border:1px solid var(--border-color); display:block;" alt="Экран">\n      </div>\n    </section>\n\n    <!-- 2. AirSwipe -->\n    <section class="view-page" id="viewAirSwipe">\n      <div class="airswipe-container">\n        <input type="file" id="airFileInput" style="display: none;" onchange="handleFileSelect(event)">\n        <div class="airswipe-card" id="airCard" onclick="triggerFilePicker()">\n          <div class="airswipe-icon-box" id="airIcon">🚀</div>\n          <div class="airswipe-title" id="airCardTitle">AirSwipe на ПК</div>\n          <div class="airswipe-desc" id="airCardDesc">Нажмите, чтобы выбрать файл, затем <b>смахните вверх</b> для мгновенной отправки на компьютер!</div>\n          <div class="swipe-indicator" id="swipeInd" style="display:none;"><span>▲ СВАЙП ВВЕРХ ДЛЯ ОТПРАВКИ ▲</span></div>\n        </div>\n      </div>\n    </section>\n\n    <!-- 3. Тачпад -->\n    <section class="view-page" id="viewTouchpad">\n      <div class="card">\n        <div class="card-title">🖱️ Виртуальный тачпад</div>\n        <div class="touchpad-surface" id="touchpad">\n          <span>Водите пальцем здесь для движения мыши ПК</span>\n          <span style="font-size:11px; margin-top:6px; opacity:0.7;">Тап = ЛКМ | Удержание = ПКМ</span>\n        </div>\n        <div class="touchpad-buttons">\n          <button class="btn btn-full" onclick="sendCmd(\'mouse_click\', \'left\')">ЛКМ</button>\n          <button class="btn btn-full" onclick="sendCmd(\'mouse_click\', \'right\')">ПКМ</button>\n        </div>\n      </div>\n      <div class="card">\n        <div class="card-title">⌨️ Ввод текста на ПК</div>\n        <div style="display:flex; gap:8px;">\n          <input type="text" id="typeInput" placeholder="Напишите текст..." style="flex:1; background:rgba(255,255,255,0.05); border:1px solid var(--border-color); border-radius:10px; padding:10px 14px; color:#fff; font-size:14px; outline:none;">\n          <button class="btn btn-primary" onclick="sendTypedText()">Отправить</button>\n        </div>\n      </div>\n    </section>\n\n    <!-- 4. Окна -->\n    <section class="view-page" id="viewTasks">\n      <div class="card">\n        <div class="card-title"><span>🖥️ Открытые приложения</span><button class="btn" style="padding:4px 10px; font-size:11px;" onclick="loadTasks()">🔄 Обновить</button></div>\n        <div id="tasksList"><div style="text-align:center; padding:20px; color:var(--text-muted);">Загрузка окон...</div></div>\n      </div>\n    </section>\n\n    <!-- 5. Темы -->\n    <section class="view-page" id="viewSettings">\n      <div class="card">\n        <div class="card-title">🎨 Выбор оформления (Темы)</div>\n        <div class="theme-grid">\n          <div class="theme-btn active" onclick="setTheme(\'default\', this)"><span class="theme-color-preview" style="background:#3b82f6;"></span><span>Cyber Blue</span></div>\n          <div class="theme-btn" onclick="setTheme(\'purple\', this)"><span class="theme-color-preview" style="background:#a855f7;"></span><span>Synthwave</span></div>\n          <div class="theme-btn" onclick="setTheme(\'matrix\', this)"><span class="theme-color-preview" style="background:#22c55e;"></span><span>Matrix Green</span></div>\n          <div class="theme-btn" onclick="setTheme(\'amber\', this)"><span class="theme-color-preview" style="background:#f59e0b;"></span><span>Sunset Gold</span></div>\n          <div class="theme-btn" onclick="setTheme(\'crimson\', this)"><span class="theme-color-preview" style="background:#f43f5e;"></span><span>Crimson Red</span></div>\n        </div>\n      </div>\n      <div class="card">\n        <div class="card-title">🔑 Подключение к ПК</div>\n        <div style="margin-bottom:12px;">\n          <label style="font-size:12px; color:var(--text-muted); display:block; margin-bottom:6px;">Ваш Telegram ID:</label>\n          <input type="text" id="userIdInput" style="width:100%; background:rgba(255,255,255,0.05); border:1px solid var(--border-color); border-radius:10px; padding:12px 14px; color:#fff; font-size:15px; font-weight:600; outline:none;">\n        </div>\n        <button class="btn btn-primary btn-full" onclick="saveUserId()">Сохранить и подключиться</button>\n      </div>\n      <div class="card" style="text-align:center; color:var(--text-muted); font-size:12px;">\n        <p>⚡ <b>PC Ecosystem Mobile v2.5</b></p>\n        <p style="margin-top:4px;">Управляйте компьютером из любого города мира.</p>\n      </div>\n    </section>\n  </main>\n\n  <nav class="bottom-nav">\n    <div class="nav-item active" onclick="switchTab(\'viewDashboard\', this)"><span class="nav-icon">📊</span><span>Главная</span></div>\n    <div class="nav-item" onclick="switchTab(\'viewAirSwipe\', this)"><span class="nav-icon">🚀</span><span>AirSwipe</span></div>\n    <div class="nav-item" onclick="switchTab(\'viewTouchpad\', this)"><span class="nav-icon">🖱️</span><span>Тачпад</span></div>\n    <div class="nav-item" onclick="switchTab(\'viewTasks\', this)"><span class="nav-icon">🖥️</span><span>Окна</span></div>\n    <div class="nav-item" onclick="switchTab(\'viewSettings\', this)"><span class="nav-icon">⚙️</span><span>Темы</span></div>\n  </nav>\n\n  <script>\n    const tg = window.Telegram ? window.Telegram.WebApp : null;\n    if (tg) { tg.expand(); tg.ready(); }\n\n    let userId = localStorage.getItem(\'pc_user_id\') || \'\';\n    if (!userId && tg && tg.initDataUnsafe && tg.initDataUnsafe.user) {\n      userId = String(tg.initDataUnsafe.user.id);\n      localStorage.setItem(\'pc_user_id\', userId);\n    }\n    document.getElementById(\'userIdInput\').value = userId;\n    let selectedFile = null;\n\n    function switchTab(viewId, el) {\n      document.querySelectorAll(\'.view-page\').forEach(p => p.classList.remove(\'active\'));\n      document.querySelectorAll(\'.nav-item\').forEach(n => n.classList.remove(\'active\'));\n      document.getElementById(viewId).classList.add(\'active\');\n      el.classList.add(\'active\');\n      if (viewId === \'viewTasks\') loadTasks();\n      if (viewId === \'viewDashboard\') fetchStatus();\n    }\n\n    function setTheme(themeName, el) {\n      document.body.className = \'\';\n      if (themeName !== \'default\') document.body.classList.add(\'theme-\' + themeName);\n      localStorage.setItem(\'pc_theme\', themeName);\n      document.querySelectorAll(\'.theme-btn\').forEach(b => b.classList.remove(\'active\'));\n      if (el) el.classList.add(\'active\');\n      showToast(\'🎨 Тема \' + themeName + \' применена!\');\n    }\n\n    const savedTheme = localStorage.getItem(\'pc_theme\') || \'default\';\n    if (savedTheme !== \'default\') {\n      document.body.classList.add(\'theme-\' + savedTheme);\n      document.querySelectorAll(\'.theme-btn\').forEach(b => {\n        if (b.innerText.toLowerCase().includes(savedTheme)) b.classList.add(\'active\');\n      });\n    }\n\n    function showToast(msg, icon = \'✨\') {\n      const toast = document.getElementById(\'toast\');\n      document.getElementById(\'toastIcon\').innerText = icon;\n      document.getElementById(\'toastMsg\').innerText = msg;\n      toast.classList.add(\'show\');\n      if (navigator.vibrate) navigator.vibrate(20);\n      setTimeout(() => toast.classList.remove(\'show\'), 2600);\n    }\n\n    function saveUserId() {\n      const val = document.getElementById(\'userIdInput\').value.trim();\n      if (!val || !/^\\d+$/.test(val)) { alert(\'Введите корректный Telegram ID\'); return; }\n      userId = val;\n      localStorage.setItem(\'pc_user_id\', userId);\n      showToast(\'ID сохранён: \' + userId);\n      fetchStatus();\n    }\n\n    async function sendCmd(action, data = \'\') {\n      if (!userId) { showToast(\'Укажите ваш Telegram ID в Настройках!\', \'⚠️\'); return; }\n      if (navigator.vibrate) navigator.vibrate(30);\n      try {\n        const res = await fetch(\'/api/command\', {\n          method: \'POST\',\n          headers: { \'Content-Type\': \'application/json\' },\n          body: JSON.stringify({ user_id: userId, action, data })\n        });\n        const json = await res.json();\n        if (json.status === \'ok\') showToast(json.message || \'Команда отправлена!\');\n        else showToast(json.error || \'Ошибка\', \'⚠️\');\n      } catch (e) { showToast(\'Сбой сети\', \'⚠️\'); }\n    }\n\n    async function fetchStatus() {\n      if (!userId) return;\n      const t0 = Date.now();\n      try {\n        const res = await fetch(\'/api/status?user_id=\' + userId);\n        const ping = Date.now() - t0;\n        document.getElementById(\'pingValue\').innerText = ping + \' мс\';\n        if (res.ok) {\n          const data = await res.json();\n          const dot = document.getElementById(\'statusDot\');\n          const hName = document.getElementById(\'hostName\');\n          if (data.online) {\n            dot.classList.add(\'online\');\n            hName.innerText = data.host_id || \'ПК Онлайн\';\n          } else {\n            dot.classList.remove(\'online\');\n            hName.innerText = \'Офлайн\';\n          }\n          if (data.media_info) {\n            document.getElementById(\'trackTitle\').innerText = data.media_info;\n            document.getElementById(\'trackStatus\').innerText = \'▶ Сейчас играет\';\n          } else {\n            document.getElementById(\'trackTitle\').innerText = \'Ничего не играет\';\n            document.getElementById(\'trackStatus\').innerText = \'Медиаплеер Windows\';\n          }\n          if (data.metrics) {\n            const ram = data.metrics.ram_load || 0;\n            document.getElementById(\'ramValue\').innerText = ram + \'% (\' + data.metrics.ram_used + \' GB)\';\n            document.getElementById(\'ramProgress\').style.width = ram + \'%\';\n            document.getElementById(\'uptimeText\').innerText = \'⏱️ \' + (data.metrics.uptime || \'\');\n          }\n        }\n      } catch (e) {}\n    }\n\n    setInterval(fetchStatus, 2500);\n    fetchStatus();\n\n    async function fetchScreenshot() {\n      showToast(\'Запрос скриншота...\', \'📸\');\n      await sendCmd(\'stream\');\n      setTimeout(async () => {\n        try {\n          const res = await fetch(\'/api/screenshot?user_id=\' + userId + \'&t=\' + Date.now());\n          if (res.ok) {\n            const blob = await res.blob();\n            document.getElementById(\'screenImg\').src = URL.createObjectURL(blob);\n            document.getElementById(\'screenCard\').style.display = \'block\';\n            showToast(\'Скриншот получен!\', \'📸\');\n          }\n        } catch(e) {}\n      }, 1500);\n    }\n\n    function triggerFilePicker() { document.getElementById(\'airFileInput\').click(); }\n\n    function handleFileSelect(e) {\n      if (e.target.files && e.target.files[0]) {\n        selectedFile = e.target.files[0];\n        document.getElementById(\'airCardTitle\').innerText = selectedFile.name;\n        const sizeMb = (selectedFile.size / (1024 * 1024)).toFixed(1);\n        document.getElementById(\'airCardDesc\').innerHTML = \'Размер: <b>\' + sizeMb + \' MB</b><br>Смахните карточку <b>вверх</b>!\';\n        document.getElementById(\'airIcon\').innerText = \'📦\';\n        document.getElementById(\'swipeInd\').style.display = \'flex\';\n        showToast(\'Файл готов! Смахните вверх\', \'🚀\');\n      }\n    }\n\n    const airCard = document.getElementById(\'airCard\');\n    let touchStartY = 0, touchDiffY = 0;\n\n    airCard.addEventListener(\'touchstart\', (e) => {\n      if (!selectedFile) return;\n      touchStartY = e.touches[0].clientY;\n      airCard.style.transition = \'none\';\n    }, { passive: true });\n\n    airCard.addEventListener(\'touchmove\', (e) => {\n      if (!selectedFile) return;\n      touchDiffY = e.touches[0].clientY - touchStartY;\n      if (touchDiffY < 0) {\n        airCard.style.transform = \'translateY(\' + touchDiffY + \'px) scale(\' + (1 - Math.abs(touchDiffY)/1000) + \')\';\n      }\n    }, { passive: true });\n\n    airCard.addEventListener(\'touchend\', () => {\n      if (!selectedFile) return;\n      airCard.style.transition = \'transform 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275), opacity 0.3s ease\';\n      if (touchDiffY < -80) {\n        airCard.style.transform = \'translateY(-400px) scale(0.6)\';\n        airCard.style.opacity = \'0\';\n        performAirSwipeUpload();\n      } else {\n        airCard.style.transform = \'translateY(0) scale(1)\';\n      }\n      touchDiffY = 0;\n    });\n\n    async function performAirSwipeUpload() {\n      showToast(\'🚀 Отправка на ПК...\', \'📡\');\n      if (navigator.vibrate) navigator.vibrate([40, 80, 40]);\n      const fd = new FormData();\n      fd.append(\'file\', selectedFile);\n      fd.append(\'user_id\', userId);\n      try {\n        const res = await fetch(\'/api/upload_file\', { method: \'POST\', body: fd });\n        const data = await res.json();\n        if (data.status === \'ok\') {\n          showToast(\'🎉 Файл отправлен на ПК в Downloads!\', \'✅\');\n          if (navigator.vibrate) navigator.vibrate(100);\n        } else {\n          showToast(data.error || \'Ошибка отправки\', \'⚠️\');\n        }\n      } catch (e) { showToast(\'Сбой сети\', \'⚠️\'); }\n      setTimeout(() => {\n        selectedFile = null;\n        document.getElementById(\'airCardTitle\').innerText = \'AirSwipe на ПК\';\n        document.getElementById(\'airCardDesc\').innerText = \'Нажмите, чтобы выбрать файл, затем смахните вверх для отправки!\';\n        document.getElementById(\'airIcon\').innerText = \'🚀\';\n        document.getElementById(\'swipeInd\').style.display = \'none\';\n        airCard.style.transform = \'translateY(0) scale(1)\';\n        airCard.style.opacity = \'1\';\n      }, 1000);\n    }\n\n    const touchpad = document.getElementById(\'touchpad\');\n    let tpLastX = 0, tpLastY = 0, tpMoveTimer = null;\n\n    touchpad.addEventListener(\'touchstart\', (e) => {\n      tpLastX = e.touches[0].clientX;\n      tpLastY = e.touches[0].clientY;\n    }, { passive: true });\n\n    touchpad.addEventListener(\'touchmove\', (e) => {\n      const cx = e.touches[0].clientX, cy = e.touches[0].clientY;\n      const dx = Math.round((cx - tpLastX) * 1.8);\n      const dy = Math.round((cy - tpLastY) * 1.8);\n      tpLastX = cx; tpLastY = cy;\n      if (!tpMoveTimer) {\n        tpMoveTimer = setTimeout(() => {\n          sendCmd(\'mouse_move\', dx + \',\' + dy);\n          tpMoveTimer = null;\n        }, 35);\n      }\n    }, { passive: true });\n\n    function sendTypedText() {\n      const inp = document.getElementById(\'typeInput\');\n      const val = inp.value.trim();\n      if (val) {\n        sendCmd(\'type_text\', val);\n        inp.value = \'\';\n        showToast(\'Текст напечатан на ПК!\');\n      }\n    }\n\n    async function loadTasks() {\n      const listEl = document.getElementById(\'tasksList\');\n      listEl.innerHTML = \'<div style="text-align:center; padding:20px; color:var(--text-muted);">Запрос списка окон...</div>\';\n      await sendCmd(\'get_tasks\', \'windowed\');\n      setTimeout(async () => {\n        try {\n          const res = await fetch(\'/api/tasks?user_id=\' + userId);\n          const tasks = await res.json();\n          if (!tasks || tasks.length === 0) {\n            listEl.innerHTML = \'<div style="text-align:center; padding:20px; color:var(--text-muted);">Открытые программы не найдены.</div>\';\n            return;\n          }\n          listEl.innerHTML = \'\';\n          tasks.forEach(t => {\n            const div = document.createElement(\'div\');\n            div.className = \'task-item\';\n            div.innerHTML = \'<div class="task-info"><div class="task-title">\' + (t.display || t.name) + \'</div><div class="task-exe">\' + t.name + \'</div></div><button class="btn btn-danger" style="padding:6px 12px; font-size:12px;" onclick="sendCmd(\\\'kill_task\\\', \\\'\' + t.name + \'\\\')">Закрыть</button>\';\n            listEl.appendChild(div);\n          });\n        } catch(e) {\n          listEl.innerHTML = \'<div style="text-align:center; padding:20px; color:var(--danger);">Ошибка загрузки списка.</div>\';\n        }\n      }, 1200);\n    }\n  </script>\n</body>\n</html>'

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
    user_id = sanitize_id(request.args.get("user_id"))
    if not user_id:
        return jsonify({"error": "user_id required"}), 400
    target_host = get_target_host(user_id)
    online = bool(target_host)
    host_info = hosts_db.get(target_host, {}) if target_host else {}
    return jsonify({
        "online": online,
        "host_id": target_host or "",
        "media_info": host_info.get("media_info", ""),
        "metrics": latest_metrics.get(user_id, {})
    }), 200

@app.route('/api/command', methods=['POST'])
def api_command():
    data = request.json or {}
    user_id = sanitize_id(data.get("user_id"))
    action = data.get("action", "").strip()
    action_data = str(data.get("data", "")).strip()

    if not user_id or not action:
        return jsonify({"status": "error", "error": "Неверные параметры"}), 400

    target_host = get_target_host(user_id)
    if not target_host:
        return jsonify({"status": "error", "error": "Ваш ПК сейчас офлайн"}), 400

    add_command(target_host, action, action_data)
    return jsonify({"status": "ok", "message": f"Команда {action} отправлена!"}), 200

@app.route('/api/screenshot', methods=['GET'])
def api_get_screenshot():
    user_id = sanitize_id(request.args.get("user_id"))
    img_bytes = latest_screenshots.get(user_id)
    if img_bytes:
        return img_bytes, 200, {"Content-Type": "image/jpeg", "Cache-Control": "no-cache"}
    return "No screenshot", 404

@app.route('/api/tasks', methods=['GET'])
def api_get_tasks():
    user_id = sanitize_id(request.args.get("user_id"))
    return jsonify(latest_tasks.get(user_id, [])), 200

@app.route('/api/upload_file', methods=['POST'])
def api_upload_file():
    user_id = sanitize_id(request.form.get("user_id"))
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
    user_id = sanitize_id(data.get("user_id"))
    
    if not host_id or not user_id or not user_id.isdigit(): 
        return jsonify({"error": "Invalid parameters"}), 400
    
    if ALLOWED_USERS and user_id not in ALLOWED_USERS:
        return jsonify({"error": "User not allowed"}), 403

    prev_info = hosts_db.get(host_id, {})
    hosts_db[host_id] = {
        "user_id": user_id, 
        "status": "online", 
        "last_seen": time.time(),
        "media_info": data.get("media_info") or prev_info.get("media_info", "")
    }
    ui_msg_id = user_ui_messages.get(user_id)

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
        latest_metrics[user_id] = metrics
        if bot:
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
            if ui_msg_id:
                try: bot.edit_message_text(text, user_id, ui_msg_id, parse_mode="Markdown")
                except: bot.send_message(user_id, text, parse_mode="Markdown")
            else:
                bot.send_message(user_id, text, parse_mode="Markdown")

    elif data.get("event_type") == "tasks_ready":
        tasks = data.get("items", [])
        latest_tasks[user_id] = tasks
        if bot:
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

            if ui_msg_id:
                try: bot.edit_message_text(text, user_id, ui_msg_id, reply_markup=markup, parse_mode="Markdown")
                except: bot.send_message(user_id, text, reply_markup=markup, parse_mode="Markdown")
            else:
                bot.send_message(user_id, text, reply_markup=markup, parse_mode="Markdown")

    elif data.get("report") and bot:
        rep = data.get("report")
        if not rep.startswith("Медиа:"):
            msg = bot.send_message(user_id, f"💬 {rep}")
            delete_message_after(user_id, msg.message_id, delay=3.0)

    cmds = commands_queue.get(host_id, [])
    commands_queue[host_id] = []
    return jsonify({"status": "ok", "commands": cmds}), 200

@app.route('/upload_screenshot', methods=['POST'])
def upload_screenshot():
    if not verify_agent_auth(request):
        return "Unauthorized", 401

    host_id = sanitize_id(request.args.get("host_id"))
    user_id = sanitize_id(request.args.get("user_id"))
    
    if not host_id or not user_id or (ALLOWED_USERS and user_id not in ALLOWED_USERS):
        return "Forbidden", 403

    if request.data:
        latest_screenshots[user_id] = request.data
        ui_msg_id = user_ui_messages.get(user_id)
        if ui_msg_id and bot:
            try: bot.delete_message(user_id, ui_msg_id)
            except: pass
            
        if bot:
            image_stream = io.BytesIO(request.data)
            image_stream.name = f"screenshot_{host_id}.jpg"
            bot.send_photo(user_id, image_stream, caption=f"📸 Скриншот экрана: `{host_id}`", parse_mode="Markdown")
        return "OK", 200
    return "Error", 400

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, threaded=True)
