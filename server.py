import os
import time
import threading
from flask import Flask, request, jsonify, send_file
import telebot
from telebot import types

# Инициализация Flask приложения
app = Flask(__name__)

# Токен твоего Telegram бота
TOKEN = "8889355917:AAEPQcy1eLySvKBQgkOM23tANZoIpR3VU34"
bot = telebot.TeleBot(TOKEN)

# === НАСТРОЙКИ ОБНОВЛЕНИЯ И АДМИНИСТРИРОВАНИЯ ===
ADMIN_ID = 7779752480  # <=== ВСТАВЬ СЮДА СВОЙ TELEGRAM ID
UPDATE_FILE_PATH = "latest_update.exe"
current_update_version = 0  # Счётчик версий
# ===============================================

# Хранилище команд и состояний хостов
hosts_data = {}

# Функция для удаления отчетов в фоновом режиме через 5 секунд
def send_and_delete_msg(chat_id, text, delay=5):
    try:
        msg = bot.send_message(chat_id, text)
        def delayed_delete():
            time.sleep(delay)
            try:
                bot.delete_message(chat_id, msg.message_id)
            except:
                pass
        threading.Thread(target=delayed_delete, daemon=True).start()
    except Exception as e:
        print(f"Ошибка отправки/удаления сообщения: {e}")

# ================= TELEGRAM BOT LOGIC =================

# Главное меню
def get_main_keyboard():
    keyboard = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    btn_status = types.KeyboardButton("🖥️ Статус ПК")
    btn_screenshot = types.KeyboardButton("📸 Сделать Скриншот")
    btn_explorer = types.KeyboardButton("📁 Проводник")
    btn_processes = types.KeyboardButton("⚙️ Процессы")
    btn_apps = types.KeyboardButton("🚀 Запуск ПО")
    btn_power = types.KeyboardButton("🔌 Питание")
    keyboard.add(btn_status, btn_screenshot, btn_explorer, btn_processes, btn_apps, btn_power)
    return keyboard

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.send_message(
        message.chat.id, 
        "Привет! Я твой личный пульт управления ПК через Telegram.\nИспользуй кнопки меню для отправки команд.", 
        reply_markup=get_main_keyboard()
    )

# Обработчик загрузки обновления (доступно только для ADMIN_ID)
@bot.message_handler(content_types=['document'])
def handle_update_document(message):
    global current_update_version
    if message.from_user.id != ADMIN_ID:
        # Если прислал обычный пользователь, игнорируем или пишем отказ
        return 
    
    if message.document.file_name.endswith('.exe'):
        bot.send_message(message.chat.id, "🔄 Скачиваю файл обновления на сервер...")
        try:
            file_info = bot.get_file(message.document.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            
            with open(UPDATE_FILE_PATH, 'wb') as new_file:
                new_file.write(downloaded_file)
            
            current_update_version += 1
            bot.send_message(
                message.chat.id, 
                f"✅ Обновление успешно зарегистрировано!\nАктуальная версия теперь: v{current_update_version}\nВсе подключенные агенты обновятся автоматически."
            )
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Ошибка во время сохранения обновления: {e}")

# Обработка текстовых команд меню
@bot.message_handler(func=lambda message: True)
def handle_text_commands(message):
    chat_id = str(message.chat.id)
    host_id = f"host_{chat_id}"
    text = message.text

    # Проверяем, зарегистрирован ли агент для этого пользователя
    if host_id not in hosts_data:
        hosts_data[host_id] = {"pending_commands": [], "items": [], "current_dir": ""}

    if text == "🖥️ Статус ПК":
        last_seen = hosts_data[host_id].get("last_seen", 0)
        status_text = "🟢 Онлайн" if time.time() - last_seen < 10 else "🔴 Офлайн"
        cur_dir = hosts_data[host_id].get("current_dir", "Неизвестно")
        bot.send_message(message.chat.id, f"Статус: {status_text}\nТекущая директория: `{cur_dir}`", parse_mode="Markdown")

    elif text == "📸 Сделать Скриншот":
        hosts_data[host_id]["pending_commands"].append({"action": "stream", "data": ""})
        bot.send_message(message.chat.id, "⏳ Запрос на скриншот отправлен...")

    elif text == "📁 Проводник":
        # Сначала отправляем запрос на обновление списка файлов
        hosts_data[host_id]["pending_commands"].append({"action": "list_dir_or_proc", "data": "dir"})
        show_explorer(message.chat.id, host_id)

    elif text == "⚙️ Процессы":
        hosts_data[host_id]["pending_commands"].append({"action": "list_dir_or_proc", "data": "processes"})
        bot.send_message(message.chat.id, "⏳ Запрашиваю список процессов...")

    elif text == "🚀 Запуск ПО":
        keyboard = types.InlineKeyboardMarkup(row_width=2)
        btn_steam = types.InlineKeyboardButton("🎮 Steam", callback_data="run:steam")
        btn_ds = types.InlineKeyboardButton("💬 Discord", callback_data="run:discord")
        btn_chrome = types.InlineKeyboardButton("🌐 Браузер", callback_data="run:browser")
        keyboard.add(btn_steam, btn_ds, btn_chrome)
        bot.send_message(message.chat.id, "Выберите приложение для запуска:", reply_markup=keyboard)

    elif text == "🔌 Питание":
        keyboard = types.InlineKeyboardMarkup(row_width=2)
        btn_restart = types.InlineKeyboardButton("🔄 Перезагрузка", callback_data="power:restart")
        btn_shutdown = types.InlineKeyboardButton("🛑 Выключение", callback_data="power:shutdown")
        keyboard.add(btn_restart, btn_shutdown)
        bot.send_message(message.chat.id, "Выберите действие питания:", reply_markup=keyboard)

# Отрисовка встроенного файлового менеджера (Проводника)
def show_explorer(chat_id, host_id):
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    
    # Кнопка назад в начало списка
    btn_back = types.InlineKeyboardButton("⬅️ Назад (Вверх)", callback_data="dir:back")
    keyboard.add(btn_back)
    
    # Добавляем папки и файлы из кэша
    items = hosts_data[host_id].get("items", [])
    for item in items:
        name = item["name"]
        icon = "📁 " if item["is_dir"] else "📄 "
        callback_action = f"dir:into:{name}" if item["is_dir"] else "dir:noop"
        
        # Telegram ограничивает размер callback_data до 64 байт
        if len(callback_action.encode('utf-8')) < 64:
            keyboard.add(types.InlineKeyboardButton(f"{icon}{name}", callback_data=callback_action))
            
    bot.send_message(chat_id, f"📁 Проводник\nПуть: `{hosts_data[host_id].get('current_dir', '')}`", 
                     reply_markup=keyboard, parse_mode="Markdown")

# Обработчик инлайн-кнопок
@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    chat_id = str(call.message.chat.id)
    host_id = f"host_{chat_id}"
    data = call.data

    if host_id not in hosts_data:
        hosts_data[host_id] = {"pending_commands": [], "items": []}

    # Кнопки Проводника
    if data.startswith("dir:"):
        parts = data.split(":")
        if parts[1] == "back":
            hosts_data[host_id]["pending_commands"].append({"action": "cd_back", "data": ""})
            bot.answer_callback_query(call.id, "Переход на уровень вверх...")
        elif parts[1] == "into":
            folder_name = ":".join(parts[2:])
            hosts_data[host_id]["pending_commands"].append({"action": "cd_into", "data": folder_name})
            bot.answer_callback_query(call.id, f"Вход в {folder_name}...")
        elif parts[1] == "noop":
            bot.answer_callback_query(call.id, "Это файл, открыть его нельзя.")

    # Кнопки запуска приложений
    elif data.startswith("run:"):
        app_name = data.split(":")[1]
        hosts_data[host_id]["pending_commands"].append({"action": "run_app", "data": app_name})
        bot.answer_callback_query(call.id, f"Запуск {app_name}...")

    # Кнопки питания
    elif data.startswith("power:"):
        action = data.split(":")[1]
        hosts_data[host_id]["pending_commands"].append({"action": "power", "data": action})
        bot.answer_callback_query(call.id, "Команда отправлена.")

# ================= API ENDPOINTS FOR CLIENT (AGENT) =================

# API эндпоинт для скачивания файла обновления агентами
@app.route('/get_update', methods=['GET'])
def get_update():
    if os.path.exists(UPDATE_FILE_PATH):
        return send_file(UPDATE_FILE_PATH, as_attachment=True, download_name="RemoteAgent_new.exe")
    return "No update file found", 404

# Основной поллинг от клиентов
@app.route('/poll', methods=['POST'])
def poll():
    global current_update_version
    payload = request.json
    if not payload:
        return jsonify({"commands": [], "update_version": current_update_version}), 400
        
    host_id = payload.get("host_id")
    user_id = payload.get("user_id")
    status = payload.get("status")
    current_dir = payload.get("current_dir")
    items = payload.get("items", [])
    report = payload.get("report", "")

    if not host_id or not user_id:
        return jsonify({"commands": [], "update_version": current_update_version}), 400

    # Обновляем кэш состояния хоста
    if host_id not in hosts_data:
         hosts_data[host_id] = {"pending_commands": []}
         
    hosts_data[host_id].update({
        "user_id": user_id,
        "status": status,
        "current_dir": current_dir,
        "items": items,
        "last_seen": time.time()
    })

    # Если агент прислал отчет о выполнении
    if report:
        if report.startswith("SUCCESS:"):
            msg_text = f"✅ Успешно: {report.split(':', 1)[1]}"
            send_and_delete_msg(user_id, msg_text, delay=5)  # Удаление отчета через 5 секунд
        elif report.startswith("ERROR:"):
            # Ошибки не удаляем, чтобы пользователь их точно заметил
            bot.send_message(user_id, f"❌ Ошибка: {report.split(':', 1)[1]}")

    # Забираем команды для этого агента
    commands = hosts_data[host_id].get("pending_commands", [])
    hosts_data[host_id]["pending_commands"] = []

    return jsonify({
        "commands": commands, 
        "update_version": current_update_version
    })

# Загрузка скриншота
@app.route('/upload_screenshot', methods=['POST'])
def upload_screenshot():
    host_id = request.args.get("host_id")
    user_id = request.args.get("user_id")
    if not host_id or not user_id:
        return "Missing parameters", 400
        
    try:
        photo_data = request.data
        filename = f"temp_{host_id}.jpg"
        with open(filename, "wb") as f:
            f.write(photo_data)
            
        with open(filename, "rb") as f:
            bot.send_photo(user_id, f, caption="📸 Скриншот экрана ПК ✅")
            
        os.remove(filename)
        return "OK"
    except Exception as e:
        return str(e), 500

# Поллинг телеграм-бота в отдельном потоке
def run_tg_polling():
    while True:
        try:
            bot.polling(none_stop=True, interval=0)
        except Exception as e:
            time.sleep(3)

if __name__ == "__main__":
    threading.Thread(target=run_tg_polling, daemon=True).start()
    
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
