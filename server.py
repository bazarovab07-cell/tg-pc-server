import os
import logging
import telebot
from telebot import types
from flask import Flask, request, jsonify, send_from_directory

# Включаем логирование, чтобы видеть каждую ошибку в консоли Render
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('gunicorn.error')

# === КОНФИГУРАЦИЯ ===
BOT_TOKEN = "ТВОЙ_ТОКЕН_БОТА"  # <--- ВСТАВЬ СЮДА СВОЙ ТОКЕН!
bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# Хранилища в памяти
hosts_db = {}
commands_queue = {}

UPDATE_DIR = os.path.join(os.getcwd(), "updates")
if not os.path.exists(UPDATE_DIR):
    os.makedirs(UPDATE_DIR)

# === ЛОГИКА TELEGRAM БОТА ===

def get_main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(types.KeyboardButton("🖥️ Статус ПК"), types.KeyboardButton("📸 Сделать скриншот"))
    markup.add(types.KeyboardButton("📁 Проводник (Диски)"), types.KeyboardButton("🎮 Запуск программ"))
    markup.add(types.KeyboardButton("🔌 Управление питанием"))
    return markup

def get_power_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(types.InlineKeyboardButton("🔄 Перезагрузка", callback_data="power_restart"),
               types.InlineKeyboardButton("🛑 Выключение", callback_data="power_shutdown"))
    return markup

def get_apps_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(types.InlineKeyboardButton("🎮 Steam", callback_data="run_steam"),
               types.InlineKeyboardButton("💬 Discord", callback_data="run_discord"),
               types.InlineKeyboardButton("🌐 Браузер", callback_data="run_browser"))
    return markup

@bot.message_handler(commands=['start'])
def send_welcome(message):
    logger.info(f"Получена команда /start от {message.chat.id}")
    bot.send_message(
        message.chat.id, 
        f"Привет! Твой Telegram ID: `{message.chat.id}`\nИспользуй кнопки ниже для управления ПК.", 
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown"
    )

@bot.message_handler(func=lambda message: message.text == "🖥️ Статус ПК")
def bot_status(message):
    user_id = str(message.chat.id)
    text = "*Активные устройства:*\n\n"
    found = False
    for hid, info in hosts_db.items():
        if info.get("user_id") == user_id:
            text += f"● `{hid}` — Онлайн\n📍 Папка: `{info.get('current_dir', 'Не определена')}`\n"
            found = True
    bot.send_message(user_id, text if found else "❌ Нет активных подключений. Запусти Агент на ПК.", parse_mode="Markdown")

@bot.message_handler(func=lambda message: message.text == "📸 Сделать скриншот")
def bot_screenshot(message):
    user_id = str(message.chat.id)
    for hid, info in hosts_db.items():
        if info.get("user_id") == user_id:
            commands_queue.setdefault(hid, []).append({"action": "stream", "data": ""})
            bot.send_message(user_id, "⏳ Запрос на скриншот отправлен агенту...")
            return
    bot.send_message(user_id, "❌ Нет активных ПК для этой команды.")

@bot.message_handler(func=lambda message: message.text == "📁 Проводник (Диски)")
def bot_explorer(message):
    user_id = str(message.chat.id)
    for hid, info in hosts_db.items():
        if info.get("user_id") == user_id:
            commands_queue.setdefault(hid, []).append({"action": "get_drives", "data": ""})
            bot.send_message(user_id, "⏳ Запрашиваю список дисков компьютера...")
            return
    bot.send_message(user_id, "❌ Нет активных ПК.")

@bot.message_handler(func=lambda message: message.text == "🎮 Запуск программ")
def bot_apps(message):
    bot.send_message(message.chat.id, "Выберите программу для запуска на ПК:", reply_markup=get_apps_keyboard())

@bot.message_handler(func=lambda message: message.text == "🔌 Управление питанием")
def bot_power(message):
    bot.send_message(message.chat.id, "Выберите действие с питанием:", reply_markup=get_power_keyboard())

@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    user_id = str(call.message.chat.id)
    target_host = next((hid for hid, info in hosts_db.items() if info.get("user_id") == user_id), None)
    
    if not target_host:
        bot.answer_callback_query(call.id, "❌ Устройство не найдено.")
        return
    
    if call.data.startswith("power_"):
        action = call.data.split("_")[1]
        commands_queue.setdefault(target_host, []).append({"action": "power", "data": action})
        bot.edit_message_text(f"🚀 Отправлена команда: Питание -> {action}", call.message.chat.id, call.message.message_id)
        
    elif call.data.startswith("run_"):
        app_name = call.data.split("_")[1]
        commands_queue.setdefault(target_host, []).append({"action": "run_app", "data": app_name})
        bot.edit_message_text(f"🚀 Отправлена команда: Запуск {app_name.capitalize()}", call.message.chat.id, call.message.message_id)
        
    bot.answer_callback_query(call.id)


# === ЛОГИКА ВЕБ-СЕРВЕРА FLASK ===

@app.route('/', methods=['GET'])
def index():
    return "<h1>Server is Live!</h1>", 200

@app.route('/poll', methods=['POST'])
def poll():
    data = request.json or {}
    host_id = data.get("host_id")
    user_id = data.get("user_id")
    
    if not host_id or not user_id: 
        return jsonify({"error": "Missing parameters"}), 400
    
    # Запоминаем, что агент онлайн
    hosts_db[host_id] = {
        "user_id": user_id, 
        "current_dir": data.get("current_dir", ""),
        "status": data.get("status", "online")
    }
    
    # Магия: каждый раз, когда агент стучится на сервер, 
    # мы проверяем, не написал ли пользователь что-то боту в Telegram.
    # Это работает БЕЗ ПОТОКОВ и никогда не зависнет на Render!
    try:
        updates = bot.get_updates(offset=(bot.last_update_id + 1), timeout=1, allowed_updates=["message", "callback_query"])
        if updates:
            bot.process_new_updates(updates)
    except Exception as e:
        logger.error(f"Ошибка проверки сообщений бота: {e}")

    # Отдаем команды агенту
    cmds = commands_queue.get(host_id, [])
    commands_queue[host_id] = []
    
    return jsonify({"status": "ok", "commands": cmds}), 200

@app.route('/upload_screenshot', methods=['POST'])
def upload_screenshot():
    host_id = request.args.get("host_id")
    user_id = request.args.get("user_id")
    if user_id and request.data:
        temp_filename = f"temp_{host_id}.jpg"
        with open(temp_filename, "wb") as f: f.write(request.data)
        with open(temp_filename, "rb") as ph: bot.send_photo(user_id, ph)
        os.remove(temp_filename)
        return "OK", 200
    return "Invalid data", 400

# Запуск Flask
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
