import os
import logging
import telebot
from telebot import types
from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('gunicorn.error')

BOT_TOKEN = "8889355917:AAEPQcy1eLySvKBQgkOM23tANZoIpR3VU34"  # <--- ВСТАВЬ СВОЙ ТОКЕН
RENDER_URL = "https://tg-pc-server.onrender.com"  # <--- ВСТАВЬ URL БЕЗ /

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

hosts_db = {}
commands_queue = {}

# Хранилище ID сообщений для каждого пользователя, чтобы обновлять их интерфейс
user_ui_messages = {}

# === ЛОГИКА TELEGRAM БОТА ===
def get_main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(types.KeyboardButton("🖥️ Статус ПК"), types.KeyboardButton("📸 Сделать скриншот"))
    markup.add(types.KeyboardButton("📁 Проводник (Диски)"), types.KeyboardButton("⚙️ Диспетчер задач"))
    markup.add(types.KeyboardButton("🎮 Запуск программ"), types.KeyboardButton("🔌 Управление питанием"))
    return markup

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.send_message(message.chat.id, "Привет! Бот готов к работе.", reply_markup=get_main_keyboard())

@bot.message_handler(func=lambda message: message.text == "🖥️ Статус ПК")
def bot_status(message):
    user_id = str(message.chat.id)
    text = "*Твои устройства:*\n\n"
    found = False
    for hid, info in hosts_db.items():
        if info.get("user_id") == user_id:
            text += f"● `{hid}` — Онлайн\n"
            found = True
    bot.send_message(user_id, text if found else "❌ Нет active подключений.", parse_mode="Markdown")

@bot.message_handler(func=lambda message: message.text in ["📸 Сделать скриншот", "📁 Проводник (Диски)", "⚙️ Диспетчер задач"])
def handle_menu_commands(message):
    user_id = str(message.chat.id)
    target_host = next((hid for hid, info in hosts_db.items() if info.get("user_id") == user_id), None)
    
    if not target_host:
        bot.send_message(user_id, "❌ Твой ПК не найден в сети.")
        return

    # Отправляем базовое сообщение, которое потом будем редактировать
    status_msg = bot.send_message(user_id, "⏳ Запрос отправлен на ПК, ожидание данных...")
    user_ui_messages[user_id] = status_msg.message_id

    if message.text == "📸 Сделать скриншот":
        commands_queue.setdefault(target_host, []).append({"action": "stream", "data": ""})
    elif message.text == "📁 Проводник (Диски)":
        commands_queue.setdefault(target_host, []).append({"action": "get_drives", "data": ""})
    elif message.text == "⚙️ Диспетчер задач":
        commands_queue.setdefault(target_host, []).append({"action": "get_tasks", "data": ""})

@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    user_id = str(call.message.chat.id)
    target_host = next((hid for hid, info in hosts_db.items() if info.get("user_id") == user_id), None)
    
    if not target_host:
        bot.answer_callback_query(call.id, "❌ ПК не найден.")
        return

    # Запоминаем ID сообщения, в котором кликнули кнопку, чтобы обновить его
    user_ui_messages[user_id] = call.message.message_id

    if call.data.startswith("kill_task_"):
        task_name = call.data.replace("kill_task_", "")
        commands_queue.setdefault(target_host, []).append({"action": "kill_task", "data": task_name})
        bot.answer_callback_query(call.id, f"Убиваю процесс {task_name}...")
        
        # Сразу меняем текст на статус ожидания
        bot.edit_message_text("⏳ Запрос на закрытие процесса отправлен, обновляю список...", user_id, call.message.message_id)
        
    bot.answer_callback_query(call.id)

# === ВЕБХУК ТЕЛЕГРАМА ===
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
    return "<h1>Сервер и Редактируемый Вебхук Активны!</h1>", 200

# === ПОДКЛЮЧЕНИЕ АГЕНТА ===
@app.route('/poll', methods=['POST'])
def poll():
    data = request.json or {}
    host_id = data.get("host_id")
    user_id = str(data.get("user_id", ""))
    
    if not host_id or not user_id: 
        return jsonify({"error": "Missing params"}), 400
    
    hosts_db[host_id] = {"user_id": user_id, "status": "online"}
    
    ui_msg_id = user_ui_messages.get(user_id)

    # 1. Если прилетел список задач (Диспетчер задач)
    if data.get("event_type") == "tasks_ready":
        markup = types.InlineKeyboardMarkup(row_width=1)
        tasks = data.get("items", [])
        
        text = "⚙️ *Диспетчер задач*\nНажми кнопку, чтобы принудительно закрыть процесс:\n\n"
        for task in tasks[:15]:
            text += f"• `{task['name']}`\n"
            markup.add(types.InlineKeyboardButton(f"❌ Закрыть {task['name']}", callback_data=f"kill_task_{task['name']}"))
            
        if ui_msg_id:
            try: bot.edit_message_text(text, user_id, ui_msg_id, reply_markup=markup, parse_mode="Markdown")
            except: bot.send_message(user_id, text, reply_markup=markup, parse_mode="Markdown")
        else:
            bot.send_message(user_id, text, reply_markup=markup, parse_mode="Markdown")

    # 2. Если прилетел список дисков или папок (Проводник)
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

    # 3. Пересылка обычных текстовых отчетов (например, об успешном закрытии процесса)
    elif data.get("report"):
        text = f"✅ `{host_id}`: {data['report']}"
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
    
    # Скриншоты нельзя запихнуть в текстовое изменение, поэтому старое статусное сообщение
    # мы просто удаляем, чтобы чат оставался чистым, и шлем готовую картинку.
    ui_msg_id = user_ui_messages.get(user_id)
    if ui_msg_id:
        try: bot.delete_message(user_id, ui_msg_id)
        except: pass
        
    if request.data:
        temp = f"temp_{host_id}.jpg"
        with open(temp, "wb") as f: f.write(request.data)
        with open(temp, "rb") as ph: bot.send_photo(user_id, ph, caption=f"📸 Скриншот ПК: {host_id}")
        os.remove(temp)
        return "OK", 200
    return "Error", 400

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
