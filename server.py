import os
import logging
import threading
import telebot
from telebot import types
from flask import Flask, request, jsonify, send_from_directory

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === КОНФИГУРАЦИЯ ===
BOT_TOKEN = "ТВОЙ_ТОКЕН_БОТА"  # <--- ВСТАВЬ СЮДА СВОЙ ТОКЕН!
bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# Хранилища данных
hosts_db = {}
commands_queue = {}
last_reports = {}
UPDATE_DIR = os.path.join(os.getcwd(), "updates")
if not os.path.exists(UPDATE_DIR):
    os.makedirs(UPDATE_DIR)

# === ЛОГИКА БОТА ===

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
    bot.send_message(message.chat.id, "Привет! Используй кнопки для управления ПК.", reply_markup=get_main_keyboard())

@bot.message_handler(func=lambda message: message.text == "🖥️ Статус ПК")
def bot_status(message):
    user_id = str(message.chat.id)
    text = "*Активные устройства:*\n\n"
    found = False
    for hid, info in hosts_db.items():
        if info.get("user_id") == user_id:
            text += f"● `{hid}` — Онлайн\n"
            found = True
    bot.send_message(user_id, text if found else "❌ Нет активных подключений.", parse_mode="Markdown")

@bot.message_handler(func=lambda message: message.text == "📸 Сделать скриншот")
def bot_screenshot(message):
    user_id = str(message.chat.id)
    for hid, info in hosts_db.items():
        if info.get("user_id") == user_id:
            commands_queue.setdefault(hid, []).append({"action": "stream", "data": ""})
            bot.send_message(user_id, "⏳ Команда отправлена.")
            return
    bot.send_message(user_id, "❌ Нет активных ПК.")

@bot.message_handler(func=lambda message: message.text == "📁 Проводник (Диски)")
def bot_explorer(message):
    user_id = str(message.chat.id)
    for hid, info in hosts_db.items():
        if info.get("user_id") == user_id:
            commands_queue.setdefault(hid, []).append({"action": "get_drives", "data": ""})
            bot.send_message(user_id, "⏳ Получаю список дисков...")
            return
    bot.send_message(user_id, "❌ Нет активных ПК.")

@bot.message_handler(func=lambda message: message.text == "🎮 Запуск программ")
def bot_apps(message):
    bot.send_message(message.chat.id, "Выберите программу:", reply_markup=get_apps_keyboard())

@bot.message_handler(func=lambda message: message.text == "🔌 Управление питанием")
def bot_power(message):
    bot.send_message(message.chat.id, "Выберите действие:", reply_markup=get_power_keyboard())

@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    user_id = str(call.message.chat.id)
    target_host = next((hid for hid, info in hosts_db.items() if info.get("user_id") == user_id), None)
    if not target_host:
        bot.answer_callback_query(call.id, "Устройство не найдено.")
        return
    
    if call.data.startswith("power_"):
        commands_queue.setdefault(target_host, []).append({"action": "power", "data": call.data.split("_")[1]})
    elif call.data.startswith("run_"):
        commands_queue.setdefault(target_host, []).append({"action": "run_app", "data": call.data.split("_")[1]})
    elif call.data.startswith("explore_"):
        parts = call.data.split("_")
        action = parts[1]
        data = "_".join(parts[2:]) if len(parts) > 2 else ""
        commands_queue.setdefault(target_host, []).append({"action": f"cd_{action}" if action in ['drive', 'cd'] else "cd_back", "data": data})
        
    bot.answer_callback_query(call.id, "Команда принята")

def run_bot_polling():
    logger.info("Бот запущен!")
    bot.infinity_polling(skip_pending=True)

# === ЛОГИКА СЕРВЕРА ===

@app.route('/poll', methods=['POST'])
def poll():
    data = request.json or {}
    host_id = data.get("host_id")
    user_id = data.get("user_id")
    if not host_id or not user_id: return jsonify({"error": "No ID"}), 400
    
    hosts_db[host_id] = {"user_id": user_id, "current_dir": data.get("current_dir", "")}
    
    if data.get("report"):
        bot.send_message(user_id, f"✅ Отчет от {host_id}:\n{data['report']}")
    if data.get("event_type") == "explorer_ready":
        # Логика отрисовки кнопок для проводника
        markup = types.InlineKeyboardMarkup()
        for item in data.get("items", []):
            markup.add(types.InlineKeyboardButton(item['name'], callback_data=f"explore_cd_{item['name']}"))
        bot.send_message(user_id, "Содержимое папки:", reply_markup=markup)

    cmds = commands_queue.get(host_id, [])
    commands_queue[host_id] = []
    return jsonify({"status": "ok", "commands": cmds})

@app.route('/upload_screenshot', methods=['POST'])
def upload_screenshot():
    host_id = request.args.get("host_id")
    user_id = request.args.get("user_id")
    if user_id and request.data:
        temp_f = f"temp_{host_id}.jpg"
        with open(temp_f, "wb") as f: f.write(request.data)
        with open(temp_f, "rb") as ph: bot.send_photo(user_id, ph)
        os.remove(temp_f)
    return "ok", 200

# === ЗАПУСК ===
if __name__ == "__main__":
    t = threading.Thread(target=run_bot_polling, daemon=True)
    t.start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
