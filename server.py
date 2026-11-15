import os
import io
import time
import logging
import re
import threading
import telebot
from telebot import types
from flask import Flask, request, jsonify

# Настройка логирования
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger('gunicorn.error') if 'gunicorn' in os.environ.get('SERVER_SOFTWARE', '') else logging.getLogger(__name__)

# --- Конфигурация из переменных окружения (БЕЗ хардкода токенов) ---
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    logger.error("КРИТИЧЕСКАЯ ОШИБКА: Переменная окружения BOT_TOKEN не установлена!")

RENDER_URL = os.environ.get("RENDER_URL", "").strip().rstrip("/")
AGENT_SECRET = os.environ.get("AGENT_SECRET", "").strip()
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "").strip()

# Белый список пользователей Telegram (ID через запятую: "123456789,987654321")
raw_allowed_users = os.environ.get("ALLOWED_USERS", "").strip()
ALLOWED_USERS = set(filter(None, [u.strip() for u in raw_allowed_users.split(",")])) if raw_allowed_users else set()

EXE_FILE = "agent.exe"

bot = telebot.TeleBot(BOT_TOKEN, threaded=False) if BOT_TOKEN else None
app = Flask(__name__)

# Базы данных в памяти
hosts_db = {}
commands_queue = {}
user_ui_messages = {}

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
    """Бот открыт для всех пользователей: каждый управляет своим ПК по своему Telegram ID."""
    return True

def sanitize_id(identifier: str) -> str:
    """Очищает строковые идентификаторы от потенциально опасных символов."""
    if not identifier:
        return ""
    return re.sub(r"[^a-zA-Z0-9_\-\.]", "", str(identifier))[:64]

def verify_agent_auth(req) -> bool:
    """Проверяет токен авторизации между клиентом Agent.py и сервером."""
    if not AGENT_SECRET:
        return True
    auth_header = req.headers.get("X-Agent-Secret", "").strip()
    return auth_header == AGENT_SECRET

def get_target_host(user_id: str):
    """Находит активный хост для данного пользователя."""
    return next((hid for hid, info in hosts_db.items() if info.get("user_id") == str(user_id) and (time.time() - info.get("last_seen", 0)) < 60), None)

def add_command(host_id: str, action: str, data: str = ""):
    """Безопасно добавляет команду в очередь хоста с лимитом размера очереди."""
    q = commands_queue.setdefault(host_id, [])
    if len(q) < 15:
        q.append({"action": action, "data": data})

def get_main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(types.KeyboardButton("📊 Мониторинг ПК"), types.KeyboardButton("📸 Скриншот экрана"))
    markup.add(types.KeyboardButton("📁 Проводник (Диски)"), types.KeyboardButton("⚙️ Диспетчер задач"))
    markup.add(types.KeyboardButton("🎵 Мультимедиа и Звук"), types.KeyboardButton("🎮 Быстрый запуск"))
    markup.add(types.KeyboardButton("🔒 Блокировка и Окна"), types.KeyboardButton("🔌 Питание ПК"))
    markup.add(types.KeyboardButton("🖥️ Статус сети"))
    return markup

# --- Обработчики Telegram бота ---

if bot:
    @bot.message_handler(commands=['start'])
    def send_welcome(message):
        user_id = str(message.chat.id)
        
        if not is_authorized_user(user_id):
            logger.warning(f"Неавторизованная попытка доступа от ID: {user_id}")
            bot.send_message(user_id, "⛔ Доступ запрещен. Бот работает в приватном режиме.")
            return

        welcome_text = (
            f"👋 *Добро пожаловать в центр управления ПК!*\n\n"
            f"🔑 Твой личный Telegram ID: `{user_id}`\n"
            f"*(нажми на число выше, чтобы скопировать его в буфер)*\n\n"
            f"🚀 *Инструкция подключения:*\n"
            f"1. Открой приложение `Agent.py` на компьютере.\n"
            f"2. Вставь скопированный ID и нажми **«Запустить Агент»**.\n"
            f"3. Все функции станут доступны мгновенно в этом чате!"
        )
        
        bot.send_message(user_id, welcome_text, reply_markup=get_main_keyboard(), parse_mode="Markdown")

        if os.path.exists(EXE_FILE):
            try:
                with open(EXE_FILE, "rb") as exe_data:
                    bot.send_document(user_id, exe_data, caption="💻 Клиентский агент для твоего ПК")
            except Exception as e:
                logger.error(f"Ошибка при отправке agent.exe: {e}")

    @bot.message_handler(func=lambda message: message.text in ["🖥️ Статус сети", "🖥️ Статус ПК"])
    def bot_status(message):
        user_id = str(message.chat.id)
        if not is_authorized_user(user_id):
            return

        text = "🖥️ *Подключенные устройства:*\n\n"
        found = False
        now = time.time()
        for hid, info in list(hosts_db.items()):
            if info.get("user_id") == user_id:
                diff = int(now - info.get("last_seen", 0))
                is_online = diff < 25
                status_icon = f"Онлайн ✅ ({diff}с назад)" if is_online else "Офлайн ❌"
                text += f"● `{hid}` — {status_icon}\n"
                found = True
        bot.send_message(user_id, text if found else "❌ Нет активных подключений. Запустите агент на ПК.", parse_mode="Markdown")

    @bot.message_handler(func=lambda message: message.text in ["📊 Мониторинг ПК", "📸 Скриншот экрана", "📸 Сделать скриншот", "📁 Проводник (Диски)", "⚙️ Диспетчер задач"])
    def handle_direct_commands(message):
        user_id = str(message.chat.id)

        target_host = get_target_host(user_id)
        if not target_host:
            bot.send_message(user_id, "❌ Твой ПК не подключен или находится офлайн. Запустите agent.exe на компьютере.")
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
            bot.send_message(user_id, "❌ Твой ПК не подключен или находится офлайн. Запустите agent.exe на компьютере.")
            return

        markup = types.InlineKeyboardMarkup()

        if message.text == "🎵 Мультимедиа и Звук":
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
            bot.send_message(user_id, "🎵 *Управление звуком и медиаплеером:*", reply_markup=markup, parse_mode="Markdown")

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
        # Сохраняем ID сообщения только для проводника и диспетчера задач (где обновляется внутреннее содержимое меню)
        if data.startswith("tasks_") or data.startswith("explore_") or data.startswith("kill_task_"):
            user_ui_messages[user_id] = call.message.message_id

        # 1. Диспетчер задач
        if data.startswith("tasks_") and data != "tasks_menu_back":
            task_type = data.replace("tasks_", "")
            add_command(target_host, "get_tasks", task_type)
            type_name = "оконных программ" if task_type == "windowed" else "фоновых служб"
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

        # 2. Проводник
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

        # 3. Мультимедиа и Звук
        elif data.startswith("vol_"):
            vol_act = data.replace("vol_", "")
            add_command(target_host, "volume", vol_act)
            vol_labels = {
                "up": "🔊 Громкость +6%",
                "down": "🔉 Громкость -6%",
                "mute": "🔇 Звук переключен",
                "play_pause": "⏯️ Пауза / Воспроизведение",
                "next": "⏭️ Следующий трек",
                "prev": "⏮️ Предыдущий трек"
            }
            bot.answer_callback_query(call.id, vol_labels.get(vol_act, f"Медиа: {vol_act}"))

        # 4. Запуск программ
        elif data.startswith("app_"):
            app_name = data.replace("app_", "")
            add_command(target_host, "run_app", app_name)
            bot.answer_callback_query(call.id, f"🚀 Запуск: {app_name}")

        # 5. Быстрые действия
        elif data == "act_lock":
            add_command(target_host, "quick_action", "lock")
            bot.answer_callback_query(call.id, "🔒 Блокировка экрана...")
        elif data == "act_minimize_all":
            add_command(target_host, "quick_action", "minimize_all")
            bot.answer_callback_query(call.id, "🪟 Сворачивание окон...")
        elif data == "act_clip_get":
            add_command(target_host, "quick_action", "clipboard_get")
            bot.answer_callback_query(call.id, "📋 Чтение буфера обмена...")

        # 6. Питание
        elif data.startswith("pow_"):
            pow_act = data.replace("pow_", "")
            add_command(target_host, "power", pow_act)
            bot.answer_callback_query(call.id, f"🔌 Питание: {pow_act}")

        bot.answer_callback_query(call.id)

# --- Flask Webhook и API ---

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

@app.route('/', methods=['GET'])
def index():
    if bot and RENDER_URL and BOT_TOKEN:
        try:
            bot.remove_webhook()
            kwargs = {"url": f"{RENDER_URL}/{BOT_TOKEN}"}
            if WEBHOOK_SECRET:
                kwargs["secret_token"] = WEBHOOK_SECRET
            bot.set_webhook(**kwargs)
            logger.info("Webhook успешно настроен.")
        except Exception as e:
            logger.error(f"Ошибка настройки Webhook: {e}")
    return "<h1>Сервер управления ПК активен (Secure & Enhanced Mode)</h1>", 200

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

    hosts_db[host_id] = {"user_id": user_id, "status": "online", "last_seen": time.time()}
    ui_msg_id = user_ui_messages.get(user_id)

    # 1. Отчет о мониторинге системы
    if data.get("event_type") == "sysinfo_ready" and bot:
        metrics = data.get("items", {})
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
            f"💽 *Диски ПК:*\n{drives_text}\n"
        )
        if ui_msg_id:
            try: bot.edit_message_text(text, user_id, ui_msg_id, parse_mode="Markdown")
            except: bot.send_message(user_id, text, parse_mode="Markdown")
        else:
            bot.send_message(user_id, text, parse_mode="Markdown")

    # 2. Диспетчер задач
    elif data.get("event_type") == "tasks_ready" and bot:
        markup = types.InlineKeyboardMarkup(row_width=1)
        tasks = data.get("items", [])
        
        text = "⚙️ *Список активных процессов:*\nНажмите кнопку для завершения:\n\n"
        for task in tasks[:15]:
            display_name = task.get("display", task.get("name", ""))[:40]
            proc_name = task.get("name", "")[:35]
            text += f"• `{display_name}`\n"
            markup.add(types.InlineKeyboardButton(f"❌ Завершить {proc_name}", callback_data=f"kill_task_{proc_name}"))
            
        markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="tasks_menu_back"))

        if ui_msg_id:
            try: bot.edit_message_text(text, user_id, ui_msg_id, reply_markup=markup, parse_mode="Markdown")
            except: bot.send_message(user_id, text, reply_markup=markup, parse_mode="Markdown")
        else:
            bot.send_message(user_id, text, reply_markup=markup, parse_mode="Markdown")

    # 3. Проводник
    elif data.get("event_type") == "explorer_ready" and bot:
        markup = types.InlineKeyboardMarkup(row_width=1)
        items = data.get("items", [])
        current_dir = str(data.get("current_dir", ""))[:120]
        
        if not current_dir:
            text = "📁 *Проводник:* Выберите диск компьютера:"
            for item in items[:8]:
                markup.add(types.InlineKeyboardButton(f"💽 {item['name']}", callback_data=f"explore_drive_{item['name']}"))
        else:
            text = f"📁 *Папка:* `{current_dir}`"
            markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="explore_back_"))
            for item in items[:12]:
                if item.get("is_dir"):
                    folder_name = item['name'][:30]
                    markup.add(types.InlineKeyboardButton(f"📁 {folder_name}", callback_data=f"explore_cd_{folder_name}"))
                    
        if ui_msg_id:
            try: bot.edit_message_text(text, user_id, ui_msg_id, reply_markup=markup, parse_mode="Markdown")
            except: bot.send_message(user_id, text, reply_markup=markup, parse_mode="Markdown")
        else:
            bot.send_message(user_id, text, reply_markup=markup, parse_mode="Markdown")

    # 4. Текстовые отчеты
    elif data.get("report") and bot:
        safe_report = str(data['report'])[:350]
        # Для звука, медиа, запуска программ и быстрых действий отправляем статус отдельным сообщением и удаляем через 3 секунды
        is_transient = any(k in safe_report.lower() for k in [
            "громкость", "звук", "медиа", "трек", "запущено", "запущен", "заблокирован", "свернуты", "буфер"
        ])
        
        if is_transient:
            try:
                temp_msg = bot.send_message(user_id, f"💬 `{host_id}`:\n{safe_report}", parse_mode="Markdown")
                delete_message_after(user_id, temp_msg.message_id, delay=3.0)
            except Exception as e:
                logger.error(f"Ошибка отправки временного отчета: {e}")
        else:
            text = f"💬 `{host_id}`:\n{safe_report}"
            if ui_msg_id:
                try: bot.edit_message_text(text, user_id, ui_msg_id, parse_mode="Markdown")
                except: bot.send_message(user_id, text, parse_mode="Markdown")
            else:
                bot.send_message(user_id, text, parse_mode="Markdown")

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

    ui_msg_id = user_ui_messages.get(user_id)
    if ui_msg_id and bot:
        try: bot.delete_message(user_id, ui_msg_id)
        except: pass
        
    if request.data and bot:
        image_stream = io.BytesIO(request.data)
        image_stream.name = f"screenshot_{host_id}.jpg"
        bot.send_photo(user_id, image_stream, caption=f"📸 Скриншот: `{host_id}`", parse_mode="Markdown")
        return "OK", 200
    return "Error", 400

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, threaded=True)
