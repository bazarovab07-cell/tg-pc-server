import os
import logging
import telebot
from telebot import types
from flask import Flask, request, jsonify, send_from_directory

# Настройка логирования для панели Render
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('gunicorn.error')

# === КОНФИГУРАЦИЯ ===
BOT_TOKEN = "8889355917:AAEPQcy1eLySvKBQgkOM23tANZoIpR3VU34"  # <--- ВСТАВЬ СЮДА СВОЙ ТОКЕН!
# Сюда вставь чистый URL твоего приложения на Render (без косой черты на конце)
# Пример: "https://tg-pc-server.onrender.com"
RENDER_URL = "https://tg-pc-server.onrender.com"  

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# Хранилища данных в памяти сервера
hosts_db = {}
commands_queue = {}

UPDATE_DIR = os.path.join(os.getcwd(), "updates")
if not os.path.exists(UPDATE_DIR):
    os.makedirs(UPDATE_DIR)

# === ОБРАБОТКА ВЕБХУКА TELEGRAM ===

@app.route(f'/{BOT_TOKEN}', methods=['POST'])
def telegram_webhook():
    """Сюда Telegram будет присылать все сообщения пользователей"""
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return '', 200
    else:
        return 'Forbidden', 403

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
    bot.send_message(user_id, text if found else "❌ Нет active подключений. Запусти Агент на ПК.", parse_mode="Markdown")

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
        
    elif call.data.startswith("explore_"):
        parts = call.data.split("_")
        action = parts[1]
        data = "_".join(parts[2:]) if len(parts) > 2 else ""
        
        if action == "drive":
            commands_queue.setdefault(target_host, []).append({"action": "set_drive", "data": data})
        elif action == "cd":
            commands_queue.setdefault(target_host, []).append({"action": "cd_into", "data": data})
        elif action == "back":
            commands_queue.setdefault(target_host, []).append({"action": "cd_back", "data": ""})
            
        bot.answer_callback_query(call.id, "⏳ Запрос на переход отправлен...")
        
    bot.answer_callback_query(call.id)


# === ЛОГИКА ВЕБ-СЕРВЕРА FLASK ===

@app.route('/', methods=['GET'])
def index():
    # Принудительно устанавливаем вебхук при заходе на главную страницу для проверки
    webhook_url = f"{RENDER_URL}/{BOT_TOKEN}"
    logger.info(f"Установка вебхука на URL: {webhook_url}")
    bot.remove_webhook()
    success = bot.set_webhook(url=webhook_url)
    if success:
        return f"<h1>Server is Live!</h1><p>Вебхук успешно привязан к Telegram!</p>", 200
    else:
        return f"<h1>Ошибка!</h1><p>Не удалось установить вебхук.</p>", 500

@app.route('/poll', methods=['POST'])
def poll():
    data = request.json or {}
    host_id = data.get("host_id")
    user_id = data.get("user_id")
    
    if not host_id or not user_id: 
        return jsonify({"error": "Missing host_id or user_id"}), 400
    
    hosts_db[host_id] = {
        "user_id": user_id, 
        "current_dir": data.get("current_dir", ""),
        "status": data.get("status", "online")
    }
    
    if data.get("report"):
        bot.send_message(user_id, f"✅ Отчет от ПК `{host_id}`:\n\n{data['report']}", parse_mode="Markdown")
        
    if data.get("event_type") == "explorer_ready":
        markup = types.InlineKeyboardMarkup(row_width=1)
        items = data.get("items", [])
        current_dir = data.get("current_dir", "")
        
        if not current_dir:
            text = "Выберите диск компьютера:"
            for item in items:
                markup.add(types.InlineKeyboardButton(f"💽 {item['name']}", callback_data=f"explore_drive_{item['name']}"))
        else:
            text = f"Папка: `{current_dir}`\n\nВыберите элемент:"
            markup.add(types.InlineKeyboardButton("⬅️ Назад (Вверх)", callback_data="explore_back_"))
            
            for item in items[:10]:
                if item.get("is_dir"):
                    markup.add(types.InlineKeyboardButton(f"📁 {item['name']}", callback_data=f"explore_cd_{item['name']}"))
                    
        bot.send_message(user_id, text, reply_markup=markup, parse_mode="Markdown")

    cmds = commands_queue.get(host_id, [])
    commands_queue[host_id] = []
    
    return jsonify({
        "status": "ok", 
        "commands": cmds,
        "update_version": 2
    }), 200

@app.route('/upload_screenshot', methods=['POST'])
def upload_screenshot():
    host_id = request.args.get("host_id")
    user_id = request.args.get("user_id")
    
    if user_id and request.data:
        temp_filename = f"temp_{host_id}.jpg"
        with open(temp_filename, "wb") as f: 
            f.write(request.data)
            
        with open(temp_filename, "rb") as ph: 
            bot.send_photo(user_id, ph, caption=f"📸 Скриншот экрана ПК ({host_id})")
            
        os.remove(temp_filename)
        return "Screenshot uploaded", 200
    return "Invalid data", 400

@app.route('/get_update', methods=['GET'])
def get_update():
    filename = "Agent_update.exe"
    if os.path.exists(os.path.join(UPDATE_DIR, filename)):
        return send_from_directory(UPDATE_DIR, filename, as_attachment=True)
    return "No update file found", 404

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
