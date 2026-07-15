import os
import logging
import threading
import telebot
from telebot import types
from flask import Flask, request, jsonify, send_from_directory

# Настройка логирования для отслеживания ошибок в консоли Render
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === ИНИЦИАЛИЗАЦИЯ ===
# Замени на свой реальный токен бота
BOT_TOKEN = "ТВОЙ_ТОКЕН_БОТА"  
bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# Директория, где будет лежать файл обновления (создается автоматически)
UPDATE_DIR = os.path.join(os.getcwd(), "updates")
if not os.path.exists(UPDATE_DIR):
    os.makedirs(UPDATE_DIR)

# === ГЛОБАЛЬНАЯ БАЗА ДАННЫХ В ПАМЯТИ ===
# Хранит данные о подключенных хостах (ПК)
# Структура: { "host_desktop_123": { "user_id": "123456", "status": "online", "current_dir": "C:\\", "items": [...] } }
hosts_db = {}

# Очередь команд для каждого хоста
# Структура: { "host_desktop_123": [ {"action": "stream"}, ... ] }
commands_queue = {}

# Последние полученные отчеты от агентов
last_reports = {}

# Текущая версия обновления на сервере (меняй на 3, 4 и т.д., когда заливаешь новый exe)
LATEST_VERSION = 2  

# Флаг для контроля запуска потока бота
bot_started = False

# === ЛОГИКА TELEGRAM БОТА ===

# Генерация главного меню с кнопками управления
def get_main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    btn_status = types.KeyboardButton("🖥️ Статус ПК")
    btn_screenshot = types.KeyboardButton("📸 Сделать скриншот")
    btn_explorer = types.KeyboardButton("📁 Проводник (Диски)")
    btn_apps = types.KeyboardButton("🎮 Запуск программ")
    btn_power = types.KeyboardButton("🔌 Управление питанием")
    markup.add(btn_status, btn_screenshot, btn_explorer, btn_apps, btn_power)
    return markup

# Меню управления питанием
def get_power_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    btn_reboot = types.InlineKeyboardButton("🔄 Перезагрузка", callback_data="power_restart")
    btn_shutdown = types.InlineKeyboardButton("🛑 Выключение", callback_data="power_shutdown")
    markup.add(btn_reboot, btn_shutdown)
    return markup

# Меню запуска приложений
def get_apps_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    btn_steam = types.InlineKeyboardButton("🎮 Запустить Steam", callback_data="run_steam")
    btn_discord = types.InlineKeyboardButton("💬 Запустить Discord", callback_data="run_discord")
    btn_browser = types.InlineKeyboardButton("🌐 Открыть Браузер", callback_data="run_browser")
    markup.add(btn_steam, btn_discord, btn_browser)
    return markup

# Команда /start
@bot.message_handler(commands=['start'])
def send_welcome(message):
    chat_id = str(message.chat.id)
    welcome_text = (
        f"Привет! Твой Telegram ID: `{chat_id}`\n\n"
        f"1. Скопируй этот ID и введи его в окне Агента на твоем ПК.\n"
        f"2. После подключения ты сможешь управлять компьютером кнопками ниже!"
    )
    bot.send_message(chat_id, welcome_text, parse_mode="Markdown", reply_markup=get_main_keyboard())

# Кнопка: Статус ПК
@bot.message_handler(func=lambda message: message.text == "🖥️ Статус ПК")
def bot_status(message):
    user_id = str(message.chat.id)
    active_hosts = []
    
    for hid, info in hosts_db.items():
        if info.get("user_id") == user_id:
            active_hosts.append(f"● *{hid}* — `Онлайн`\n📍 Папка: `{info.get('current_dir') or 'Не определена'}`")
            
    if active_hosts:
        response = "*Подключенные устройства:*\n\n" + "\n\n".join(active_hosts)
    else:
        response = "❌ Нет активных подключений. Убедись, что Агент запущен на ПК и в нем введен правильный ID."
        
    bot.send_message(user_id, response, parse_mode="Markdown")

# Кнопка: Сделать скриншот
@bot.message_handler(func=lambda message: message.text == "📸 Сделать скриншот")
def bot_screenshot(message):
    user_id = str(message.chat.id)
    target_host = None
    
    for hid, info in hosts_db.items():
        if info.get("user_id") == user_id:
            target_host = hid
            break
            
    if not target_host:
        bot.send_message(user_id, "❌ Нет активных ПК для отправки команды.")
        return
        
    if target_host not in commands_queue:
        commands_queue[target_host] = []
        
    commands_queue[target_host].append({"action": "stream", "data": ""})
    bot.send_message(user_id, "⏳ Запрос отправлен. Ожидаем скриншот с ПК...")

# Кнопка: Проводник (Диски)
@bot.message_handler(func=lambda message: message.text == "📁 Проводник (Диски)")
def bot_explorer(message):
    user_id = str(message.chat.id)
    target_host = None
    
    for hid, info in hosts_db.items():
        if info.get("user_id") == user_id:
            target_host = hid
            break
            
    if not target_host:
        bot.send_message(user_id, "❌ Нет активных ПК для работы с проводником.")
        return
        
    if target_host not in commands_queue:
        commands_queue[target_host] = []
        
    commands_queue[target_host].append({"action": "get_drives", "data": ""})
    bot.send_message(user_id, "⏳ Загружаю список дисков компьютера...")

# Кнопка: Запуск программ
@bot.message_handler(func=lambda message: message.text == "🎮 Запуск программ")
def bot_apps(message):
    user_id = str(message.chat.id)
    bot.send_message(user_id, "Выберите программу для запуска на ПК:", reply_markup=get_apps_keyboard())

# Кнопка: Управление питанием
@bot.message_handler(func=lambda message: message.text == "🔌 Управление питанием")
def bot_power(message):
    user_id = str(message.chat.id)
    bot.send_message(user_id, "⚠️ Выберите действие с питанием ПК:", reply_markup=get_power_keyboard())

# Обработка нажатий Inline-кнопок (Callback)
@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    user_id = str(call.message.chat.id)
    target_host = None
    
    for hid, info in hosts_db.items():
        if info.get("user_id") == user_id:
            target_host = hid
            break
            
    if not target_host:
        bot.answer_callback_query(call.id, "❌ Устройство не найдено.")
        return

    if target_host not in commands_queue:
        commands_queue[target_host] = []

    # Обработка питания
    if call.data.startswith("power_"):
        action = call.data.split("_")[1] # restart или shutdown
        commands_queue[target_host].append({"action": "power", "data": action})
        bot.edit_message_text(f"🚀 Команда отправлена: Питание -> {action}", call.message.chat.id, call.message.message_id)
        
    # Обработка запуска софта
    elif call.data.startswith("run_"):
        app_name = call.data.split("_")[1] # steam, discord, browser
        commands_queue[target_host].append({"action": "run_app", "data": app_name})
        bot.edit_message_text(f"🚀 Команда отправлена: Запуск {app_name.capitalize()}", call.message.chat.id, call.message.message_id)
        
    # Обработка навигации файлового менеджера
    elif call.data.startswith("explore_"):
        parts = call.data.split("_")
        action = parts[1] # drive, cd, back
        path_data = "_".join(parts[2:]) # Данные пути
        
        if action == "drive":
            commands_queue[target_host].append({"action": "set_drive", "data": path_data})
        elif action == "cd":
            commands_queue[target_host].append({"action": "cd_into", "data": path_data})
        elif action == "back":
            commands_queue[target_host].append({"action": "cd_back", "data": ""})
            
        bot.answer_callback_query(call.id, "⏳ Перехожу...")

    bot.answer_callback_query(call.id)

# Функция бесконечного цикла чтения сообщений бота
def run_bot_polling():
    logger.info("Запуск Telegram-бота в фоновом потоке...")
    try:
        bot.infinity_polling(skip_pending=True)
    except Exception as e:
        logger.error(f"Критическая ошибка в потоке бота: {e}")


# === ЛОГИКА ВЕБ-СЕРВЕРА FLASK ===

# Инициализация фонового потока при первом сетевом обращении
@app.before_request
def start_bot_thread():
    global bot_started
    if not bot_started:
        bot_started = True
        t = threading.Thread(target=run_bot_polling, daemon=True)
        t.start()

# Главная страница (для проверки работы сервера в браузере)
@app.route('/', methods=['GET'])
def index():
    return "<h1>Server is Live!</h1><p>Telegram Bot and Agent backend are running successfully.</p>", 200

# Главный роут опроса и обмена данными с агентом
@app.route('/poll', methods=['POST'])
def poll():
    data = request.json or {}
    host_id = data.get("host_id")
    user_id = data.get("user_id")
    status = data.get("status")
    current_dir = data.get("current_dir", "")
    items = data.get("items", [])
    report = data.get("report", "")
    event_type = data.get("event_type", "")

    if not host_id or not user_id:
        return jsonify({"error": "Missing parameters"}), 400

    # Записываем или обновляем данные устройства в базе
    hosts_db[host_id] = {
        "user_id": user_id,
        "status": status,
        "current_dir": current_dir,
        "items": items
    }

    # Если агент прислал отчет о выполнении команды
    if report:
        last_reports[host_id] = report
        # Если команда требовала обновления проводника, генерируем инлайн-кнопки
        if event_type == "explorer_ready":
            send_explorer_markup(user_id, current_dir, items)
        else:
            bot.send_message(user_id, f"✅ Отчет с ПК ({host_id}):\n`{report}`", parse_mode="Markdown")

    # Собираем команды, накопившиеся для этого хоста
    cmds_to_send = commands_queue.get(host_id, [])
    # Очищаем очередь, так как мы их сейчас отдадим агенту
    commands_queue[host_id] = []

    # Возвращаем ответ агенту вместе со списком команд и версией автообновления
    return jsonify({
        "status": "ok",
        "commands": cmds_to_send,
        "update_version": LATEST_VERSION
    }), 200

# Вспомогательная функция отрисовки файлов/дисков в чате Telegram
def send_explorer_markup(user_id, current_dir, items):
    markup = types.InlineKeyboardMarkup(row_width=1)
    
    if not current_dir:
        # Отрисовка списка дисков
        text = "Выберите системный диск:"
        for item in items:
            btn_name = f"💽 {item['name']}"
            markup.add(types.InlineKeyboardButton(btn_name, callback_data=f"explore_drive_{item['name']}"))
    else:
        # Отрисовка папок внутри проводника
        text = f"Папка: `{current_dir}`\n\nВыберите подпапку для перехода:"
        markup.add(types.InlineKeyboardButton("⬅️ Назад (Вверх)", callback_data="explore_back_"))
        
        # Выводим первые 10 папок во избежание ограничений лимитов кнопок Telegram
        folder_count = 0
        for item in items:
            if item.get("is_dir") and folder_count < 10:
                btn_name = f"📁 {item['name']}"
                markup.add(types.InlineKeyboardButton(btn_name, callback_data=f"explore_cd_{item['name']}"))
                folder_count += 1
                
    bot.send_message(user_id, text, reply_markup=markup, parse_mode="Markdown")

# Получение скриншота от агента
@app.route('/upload_screenshot', methods=['POST'])
def upload_screenshot():
    host_id = request.args.get("host_id")
    user_id = request.args.get("user_id")
    
    if not user_id:
        return "Missing user_id", 400
        
    image_data = request.data
    if image_data:
        # Временное сохранение скриншота для отправки ботом
        temp_filename = f"temp_screen_{host_id}.jpg"
        with open(temp_filename, "wb") as f:
            f.write(image_data)
            
        with open(temp_filename, "rb") as photo:
            bot.send_photo(user_id, photo, caption=f"📸 Снимок экрана с ПК ({host_id})")
            
        os.remove(temp_filename)
        return "Success", 200
        
    return "No image data received", 400

# Маршрут для раздачи файлов автообновления
@app.route('/get_update', methods=['GET'])
def get_update():
    # Ищем скомпилированный Agent_update.exe в папке updates
    filename = "Agent_update.exe"
    if os.path.exists(os.path.join(UPDATE_DIR, filename)):
        return send_from_directory(UPDATE_DIR, filename, as_attachment=True)
    else:
        # Если нет специального файла обновления, отдаем обычный Agent.exe из папки билда
        return "No update file found on server", 404


if __name__ == "__main__":
    # Настройка порта для Render
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
