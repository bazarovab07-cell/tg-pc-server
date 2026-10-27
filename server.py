import os
import io
import time
import logging
import telebot
from telebot import types
from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('gunicorn.error')

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8889355917:AAEPQcy1eLySvKBQgkOM23tANZoIpR3VU34")
RENDER_URL = os.environ.get("RENDER_URL", "https://tg-pc-server.onrender.com")
EXE_FILE = "agent.exe"

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

hosts_db = {}
commands_queue = {}
user_ui_messages = {}

def get_main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(types.KeyboardButton("🖥️ Статус ПК"), types.KeyboardButton("📸 Сделать скриншот"))
    markup.add(types.KeyboardButton("📁 Проводник (Диски)"), types.KeyboardButton("⚙️ Диспетчер задач"))
    markup.add(types.KeyboardButton("🎮 Запуск программ"), types.KeyboardButton("🔌 Управление питанием"))
    return markup

@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = str(message.chat.id)
    
    welcome_text = (
        f"👋 *Добро пожаловать в панель управления ПК!*\n\n"
        f"Твой личный Telegram ID: `{user_id}`\n"
        f"*(нажми на число выше, чтобы скопировать его в буфер)*\n\n"
        f"📋 *Быстрый старт:*\n"
        f"1. Скачай прикрепленный файл `agent.exe` ниже.\n"
        f"2. Запусти его на своем компьютере.\n"
        f"3. Вставь скопированный ID в поле ввода и нажми «Запустить Агент».\n"
        f"4. Программа автоматически свернется в системный трей."
    )
    
    bot.send_message(user_id, welcome_text, reply_markup=get_main_keyboard(), parse_mode="Markdown")

    if os.path.exists(EXE_FILE):
        try:
            with open(EXE_FILE, "rb") as exe_data:
                bot.send_document(user_id, exe_data, caption="💻 Клиентский агент для твоего ПК")
        except Exception as e:
            logger.error(f"Ошибка при отправке agent.exe: {e}")
            bot.send_message(user_id, "⚠️ Не удалось отправить файл агента.")
    else:
        bot.send_message(
            user_id,
            "ℹ️ Файл `agent.exe` еще не загружен в репозиторий сервера. Соберите его на ПК и загрузите рядом с `server.py`.",
            parse_mode="Markdown"
        )

@bot.message_handler(func=lambda message: message.text == "🖥️ Статус ПК")
def bot_status(message):
    user_id = str(message.chat.id)
    text = "*Подключенные устройства:*\n\n"
    found = False
    for hid, info in hosts_db.items():
        if info.get("user_id") == user_id:
            text += f"● `{hid}` — Онлайн ✅\n"
            found = True
    bot.send_message(user_id, text if found else "❌ Нет активных подключений. Запустите агент на ПК.", parse_mode="Markdown")

@bot.message_handler(func=lambda message: message.text in ["📸 Сделать скриншот", "📁 Проводник (Диски)", "⚙️ Диспетчер задач"])
def handle_menu_commands(message):
    user_id = str(message.chat.id)
    target_host = next((hid for hid, info in hosts_db.items() if info.get("user_id") == user_id), None)
    
    if not target_host:
        bot.send_message(user_id, "❌ Твой ПК не подключен к сети.")
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

    if message.text == "📸 Сделать скриншот":
        commands_queue.setdefault(target_host, []).append({"action": "stream", "data": ""})
    elif message.text == "📁 Проводник (Диски)":
        commands_queue.setdefault(target_host, []).append({"action": "get_drives", "data": ""})

@bot.message_handler(func=lambda message: message.text in ["🎮 Запуск программ", "🔌 Управление питанием"])
def handle_submenu_commands(message):
    user_id = str(message.chat.id)
    markup = types.InlineKeyboardMarkup(row_width=2)
    
    if message.text == "🎮 Запуск программ":
        text = "🎮 *Быстрый запуск:*"
        markup.add(
            types.InlineKeyboardButton("Steam", callback_data="run_app_steam"),
            types.InlineKeyboardButton("Discord", callback_data="run_app_discord"),
            types.InlineKeyboardButton("Браузер", callback_data="run_app_browser")
        )
    else:
        text = "🔌 *Управление питанием:*"
        markup.add(
            types.InlineKeyboardButton("🔄 Перезагрузка", callback_data="power_restart"),
            types.InlineKeyboardButton("🛑 Выключение", callback_data="power_shutdown")
        )
        
    bot.send_message(user_id, text, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    user_id = str(call.message.chat.id)
    target_host = next((hid for hid, info in hosts_db.items() if info.get("user_id") == user_id), None)
    
    if not target_host:
        bot.answer_callback_query(call.id, "❌ ПК отключен.")
        return

    user_ui_messages[user_id] = call.message.message_id

    if call.data.startswith("tasks_") and call.data != "tasks_menu_back":
        task_type = call.data.replace("tasks_", "")
        commands_queue.setdefault(target_host, []).append({"action": "get_tasks", "data": task_type})
        type_name = "оконных программ" if task_type == "windowed" else "фоновых служб"
        bot.edit_message_text(f"⏳ Сбор данных {type_name}...", user_id, call.message.message_id)

    elif call.data == "tasks_menu_back":
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("🖥 Открытые окна", callback_data="tasks_windowed"),
            types.InlineKeyboardButton("⚙️ Фоновые процессы", callback_data="tasks_background")
        )
        bot.edit_message_text("⚙️ *Диспетчер задач*\nКакие процессы показать?", user_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    elif call.data.startswith("kill_task_"):
        task_name = call.data.replace("kill_task_", "")
        commands_queue.setdefault(target_host, []).append({"action": "kill_task", "data": task_name})
        bot.answer_callback_query(call.id, f"Закрытие {task_name}...")
        bot.edit_message_text(f"⏳ Закрываю `{task_name}`...", user_id, call.message.message_id, parse_mode="Markdown")
        
    elif call.data.startswith("explore_drive_"):
        drive = call.data.replace("explore_drive_", "")
        commands_queue.setdefault(target_host, []).append({"action": "set_drive", "data": drive})
        bot.edit_message_text(f"⏳ Чтение диска {drive}...", user_id, call.message.message_id)
        
    elif call.data.startswith("explore_cd_"):
        folder = call.data.replace("explore_cd_", "")
        commands_queue.setdefault(target_host, []).append({"action": "cd_into", "data": folder})
        bot.edit_message_text(f"⏳ Открытие `{folder}`...", user_id, call.message.message_id, parse_mode="Markdown")
        
    elif call.data == "explore_back_":
        commands_queue.setdefault(target_host, []).append({"action": "cd_back", "data": ""})
        bot.edit_message_text("⏳ Переход назад...", user_id, call.message.message_id)

    elif call.data.startswith("run_app_"):
        app_name = call.data.replace("run_app_", "")
        commands_queue.setdefault(target_host, []).append({"action": "run_app", "data": app_name})
        bot.answer_callback_query(call.id, f"Запуск {app_name}...")

    elif call.data.startswith("power_"):
        action = call.data.replace("power_", "")
        commands_queue.setdefault(target_host, []).append({"action": "power", "data": action})
        bot.answer_callback_query(call.id, f"Команда питания отправлена: {action}")
        
    bot.answer_callback_query(call.id)

@app.route(f'/{BOT_TOKEN}', methods=['POST'])
def telegram_webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return '', 200
    return 'Forbidden', 403

@app.route('/', methods=['GET'])
def index():
    bot.remove_webhook()
    bot.set_webhook(url=f"{RENDER_URL}/{BOT_TOKEN}")
    return "<h1>Сервер управления ПК активен!</h1>", 200

@app.route('/poll', methods=['POST'])
def poll():
    data = request.json or {}
    host_id = data.get("host_id")
    user_id = str(data.get("user_id", ""))
    
    if not host_id or not user_id: 
        return jsonify({"error": "Missing params"}), 400
    
    hosts_db[host_id] = {"user_id": user_id, "status": "online", "last_seen": time.time()}
    ui_msg_id = user_ui_messages.get(user_id)

    if data.get("event_type") == "tasks_ready":
        markup = types.InlineKeyboardMarkup(row_width=1)
        tasks = data.get("items", [])
        
        text = "⚙️ *Список процессов:*\nНажмите кнопку для завершения:\n\n"
        for task in tasks[:15]:
            display_name = task.get("display", task["name"])
            text += f"• `{display_name}`\n"
            markup.add(types.InlineKeyboardButton(f"❌ Закрыть {task['name']}", callback_data=f"kill_task_{task['name']}"))
            
        markup.add(types.InlineKeyboardButton("⬅️ Назад к выбору", callback_data="tasks_menu_back"))

        if ui_msg_id:
            try: bot.edit_message_text(text, user_id, ui_msg_id, reply_markup=markup, parse_mode="Markdown")
            except: bot.send_message(user_id, text, reply_markup=markup, parse_mode="Markdown")
        else:
            bot.send_message(user_id, text, reply_markup=markup, parse_mode="Markdown")

    elif data.get("event_type") == "explorer_ready":
        markup = types.InlineKeyboardMarkup(row_width=1)
        items = data.get("items", [])
        current_dir = data.get("current_dir", "")
        
        if not current_dir:
            text = "📁 *Проводник:* Выберите диск компьютера:"
            for item in items:
                markup.add(types.InlineKeyboardButton(f"💽 {item['name']}", callback_data=f"explore_drive_{item['name']}"))
        else:
            text = f"📁 *Папка:* `{current_dir}`"
            markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="explore_back_"))
            for item in items[:10]:
                if item.get("is_dir"):
                    markup.add(types.InlineKeyboardButton(f"📁 {item['name']}", callback_data=f"explore_cd_{item['name']}"))
                    
        if ui_msg_id:
            try: bot.edit_message_text(text, user_id, ui_msg_id, reply_markup=markup, parse_mode="Markdown")
            except: bot.send_message(user_id, text, reply_markup=markup, parse_mode="Markdown")
        else:
            bot.send_message(user_id, text, reply_markup=markup, parse_mode="Markdown")

    elif data.get("report"):
        text = f"💬 `{host_id}`: {data['report']}"
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
    host_id = request.args.get("host_id")
    user_id = request.args.get("user_id")
    ui_msg_id = user_ui_messages.get(user_id)
    
    if ui_msg_id:
        try: bot.delete_message(user_id, ui_msg_id)
        except: pass
        
    if request.data:
        image_stream = io.BytesIO(request.data)
        image_stream.name = f"screenshot_{host_id}.jpg"
        bot.send_photo(user_id, image_stream, caption=f"📸 Скриншот: `{host_id}`", parse_mode="Markdown")
        return "OK", 200
    return "Error", 400

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, threaded=True)
