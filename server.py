import os
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.filters import Command

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Твой рабочий токен подставлен напрямую
TOKEN = "8889355917:AAEPQcy1eLySvKBQgkOM23tANZoIpR3VU34"
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL", "https://YOUR_DOMAIN.onrender.com")

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Глобальные хранилища данных
active_hosts = {}   # host_id -> data
user_sessions = {}  # uid -> data

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Установка вебхука при запуске сервера
    webhook_url = f"{RENDER_URL}/webhook"
    logging.info(f"Установка вебхука: {webhook_url}")
    await bot.set_webhook(url=webhook_url)
    yield
    # Удаление вебхука при остановке
    logging.info("Удаление вебхука...")
    await bot.delete_webhook()

app = FastAPI(lifespan=lifespan)

# Генерирует файловый менеджер в виде инлайн-кнопок
def get_file_manager_keyboard(items, current_dir):
    keyboard = []
    
    # Кнопка подняться вверх по директории
    keyboard.append([InlineKeyboardButton(text="📁 .. (Вверх)", callback_data="cd_..")])
    
    # Сортируем: сначала папки, потом файлы
    folders = [x for x in items if x.get("type") == "folder"]
    files = [x for x in items if x.get("type") == "file"]
    
    for folder in folders:
        name = folder.get("name", "")
        keyboard.append([InlineKeyboardButton(text=f"📁 {name}", callback_data=f"cd_{name}")])
        
    for file in files:
        name = file.get("name", "")
        size = file.get("size", "0 KB")
        keyboard.append([InlineKeyboardButton(text=f"📄 {name} ({size})", callback_data=f"file_{name}")])
        
    # Дополнительные системные утилиты панели управления (Сетки кнопок по 2 в ряд)
    keyboard.append([
        InlineKeyboardButton(text="⚙️ Процессы", callback_data="show_processes"),
        InlineKeyboardButton(text="📋 Буфер", callback_data="get_clipboard")
    ])
    keyboard.append([
        InlineKeyboardButton(text="📸 Скриншот", callback_data="take_screenshot"),
        InlineKeyboardButton(text="💻 CMD Команда", callback_data="run_cmd")
    ])
    keyboard.append([
        InlineKeyboardButton(text="🔴 Выключить ПК", callback_data="pc_shutdown")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# Эндпоинт связи с агентом на ПК (Poll)
@app.post("/poll")
async def poll(request: Request):
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})
        
    host_id = payload.get("host_id")
    uid = payload.get("uid")
    
    if not host_id or not uid: 
        return {"commands": []}
    
    # Регистрация или обновление хоста в сети
    if host_id not in active_hosts:
        active_hosts[host_id] = {"owner_id": uid, "status": "online", "commands": []}
    
    active_hosts[host_id]["current_dir"] = payload.get("current_dir", "C:\\")
    active_hosts[host_id]["items"] = payload.get("items", [])
    
    # Привязываем сессию пользователя к активному ПК
    if uid not in user_sessions:
        user_sessions[uid] = {}
    user_sessions[uid]["current_host"] = host_id
    
    last_msg_id = user_sessions[uid].get("last_msg_id")
    
    # === НАЧАЛО БЛОКА АВТОУДАЛЕНИЯ УВЕДОМЛЕНИЙ НА 5 СЕКУНД ===
    report = payload.get("report", "")
    if report:  # Убрали обязательное требование last_msg_id, чтобы не блокировать отправку
        async def send_and_delete_report(chat_id, report_text):
            try:
                status_msg = None
                # Приводим к строке и очищаем от пробелов, чтобы избежать ошибок формата
                text_str = str(report_text).strip()
                
                if text_str.startswith("SUCCESS:"):
                    msg = text_str.replace("SUCCESS:", "").strip()
                    status_msg = await bot.send_message(chat_id=chat_id, text=f"✅ **Успешно:** {msg}", parse_mode="Markdown")
                elif text_str.startswith("ERROR:"):
                    msg = text_str.replace("ERROR:", "").strip()
                    status_msg = await bot.send_message(chat_id=chat_id, text=f"❌ **Ошибка:** {msg}", parse_mode="Markdown")
                
                if status_msg:
                    # Ждем ровно 5 секунд
                    await asyncio.sleep(5)
                    try:
                        await bot.delete_message(chat_id=chat_id, message_id=status_msg.message_id)
                        logging.info(f"Сообщение {status_msg.message_id} успешно удалено через 5 сек.")
                    except Exception as del_err:
                        logging.error(f"Не удалось удалить сообщение: {del_err}")
            except Exception as e:
                logging.error(f"Ошибка в таске автоудаления: {e}")

        # Запускаем фоновую задачу
        asyncio.create_task(send_and_delete_report(int(uid), report))
    # === КОНЕЦ БЛОКА АВТОУДАЛЕНИЯ ===

    # Обработка специфических тяжелых данных от агента (Список процессов)
    if "processes" in payload:
        proc_list = payload.get("processes", [])
        text = "📊 **Список активных процессов:**\n\n" + "\n".join([f"🔹 `{p}`" for p in proc_list[:40]])
        await bot.send_message(chat_id=uid, text=text, parse_mode="Markdown")
        
    # Обработка содержимого буфера обмена ПК
    if "clipboard" in payload:
        clip_text = payload.get("clipboard", "")
        await bot.send_message(chat_id=uid, text=f"📋 **Буфер обмена ПК:**\n`{clip_text}`", parse_mode="Markdown")

    # Прием файлов (скачивание файла с ПК на телефон)
    if "file_data" in payload and "file_name" in payload:
        import base64
        file_name = payload.get("file_name")
        raw_data = base64.b64decode(payload.get("file_data"))
        input_file = BufferedInputFile(raw_data, filename=file_name)
        await bot.send_document(chat_id=uid, document=input_file, caption=f"📥 Файл скачан: `{file_name}`", parse_mode="Markdown")

    # Прием скриншота с ПК
    if "screenshot_data" in payload:
        import base64
        raw_data = base64.b64decode(payload.get("screenshot_data"))
        input_file = BufferedInputFile(raw_data, filename="screenshot.png")
        await bot.send_photo(chat_id=uid, photo=input_file, caption="📸 Скриншот экрана ПК")

    # Обновляем основную панель интерфейса, если изменилась папка или список файлов
    if "items" in payload or "current_dir" in payload:
        if last_msg_id:
            try:
                kb = get_file_manager_keyboard(active_hosts[host_id]["items"], active_hosts[host_id]["current_dir"])
                await bot.edit_message_text(
                    chat_id=uid,
                    message_id=last_msg_id,
                    text=f"💻 **Хост:** `{host_id}`\n📁 **Текущий путь:** `{active_hosts[host_id]['current_dir']}`",
                    reply_markup=kb,
                    parse_mode="Markdown"
                )
            except Exception:
                pass

    # Забираем команды, которые накопились для отправки на агент
    commands = active_hosts[host_id].get("commands", [])
    active_hosts[host_id]["commands"] = [] 
    
    return {"commands": commands}

# Хэндлер на команду /start
@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    uid = message.from_user.id
    user_sessions[uid] = {}
    await message.answer("👋 Привет! Ожидаю подключения твоего PC-агента. Как только он сделает первый запрос, здесь появится панель управления.")

# Хэндлер для отправки консольных команд на ПК
@dp.message()
async def handle_text_messages(message: types.Message):
    uid = message.from_user.id
    host_id = user_sessions.get(uid, {}).get("current_host")
    state = user_sessions.get(uid, {}).get("state")
    
    if not host_id or host_id not in active_hosts:
        await message.answer("❌ Нет активного подключения к ПК. Запусти агент.")
        return

    # Если пользователь вводил команду для CMD
    if state == "awaiting_cmd":
        active_hosts[host_id]["commands"].append({"type": "cmd_exec", "param": message.text})
        user_sessions[uid]["state"] = None
        await message.answer(f"⏳ Команда `{message.text}` отправлена на исполнение...")
        return

# Обработчик нажатий на кнопки клавиатуры (Инлайн)
@dp.callback_query()
async def handle_callbacks(callback: types.CallbackQuery):
    uid = callback.from_user.id
    host_id = user_sessions.get(uid, {}).get("current_host")
    
    if not host_id or host_id not in active_hosts:
        await callback.answer("❌ Активный ПК не найден или оффлайн.", show_alert=True)
        return

    data = callback.data
    user_sessions[uid]["last_msg_id"] = callback.message.message_id

    # Логика навигации по папкам
    if data.startswith("cd_"):
        folder_name = data.replace("cd_", "")
        active_hosts[host_id]["commands"].append({"type": "cd", "param": folder_name})
        await callback.answer("Запрос на смену папки отправлен...")
        
    # Логика взаимодействия с файлами
    elif data.startswith("file_"):
        file_name = data.replace("file_", "")
        # Создаем меню действий для конкретного файла (Запустить/Скачать/Удалить)
        file_kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🚀 Запустить", callback_data=f"run_{file_name}"),
                InlineKeyboardButton(text="📥 Скачать", callback_data=f"download_{file_name}")
            ],
            [
                InlineKeyboardButton(text="❌ Удалить", callback_data=f"delete_{file_name}"),
                InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_manager")
            ]
        ])
        await bot.edit_message_text(
            chat_id=uid,
            message_id=callback.message.message_id,
            text=f"📄 Выбран файл: `{file_name}`\nЧто необходимо сделать?",
            reply_markup=file_kb,
            parse_mode="Markdown"
        )
        await callback.answer()

    # Действия над файлами
    elif data.startswith("run_"):
        f_name = data.replace("run_", "")
        active_hosts[host_id]["commands"].append({"type": "run_file", "param": f_name})
        await callback.answer("Запрос на запуск отправлен...")
        
    elif data.startswith("download_"):
        f_name = data.replace("download_", "")
        active_hosts[host_id]["commands"].append({"type": "download_file", "param": f_name})
        await callback.answer("Скачиваю файл...")

    elif data.startswith("delete_"):
        f_name = data.replace("delete_", "")
        active_hosts[host_id]["commands"].append({"type": "delete_file", "param": f_name})
        await callback.answer("Удаляю файл...")

    elif data == "back_to_manager":
        # Просто форсируем обновление менеджер-панели данными из памяти
        kb = get_file_manager_keyboard(active_hosts[host_id]["items"], active_hosts[host_id]["current_dir"])
        await bot.edit_message_text(
            chat_id=uid,
            message_id=callback.message.message_id,
            text=f"💻 **Хост:** `{host_id}`\n📁 **Текущий путь:** `{active_hosts[host_id]['current_dir']}`",
            reply_markup=kb,
            parse_mode="Markdown"
        )
        await callback.answer()
        
    # Запрос процессов
    elif data == "show_processes":
        active_hosts[host_id]["commands"].append({"type": "get_processes"})
        await callback.answer("Запрашиваю список процессов...")
        
    # Запрос буфера обмена
    elif data == "get_clipboard":
        active_hosts[host_id]["commands"].append({"type": "get_clipboard"})
        await callback.answer("Читаю буфер обмена...")

    # Скриншот экрана
    elif data == "take_screenshot":
        active_hosts[host_id]["commands"].append({"type": "screenshot"})
        await callback.answer("Делаю снимок экрана...")

    # Переключение в режим ввода консольной команды
    elif data == "run_cmd":
        user_sessions[uid]["state"] = "awaiting_cmd"
        await bot.send_message(chat_id=uid, text="⌨️ Введите консольную команду (CMD), которую нужно выполнить на ПК:")
        await callback.answer()

    # Выключение компьютера
    elif data == "pc_shutdown":
        active_hosts[host_id]["commands"].append({"type": "shutdown"})
        await callback.answer("Команда на выключение ПК отправлена!", show_alert=True)

# Роут для вебхука Telegram
@app.post("/webhook")
async def webhook_endpoint(request: Request):
    update = types.Update.model_validate(await request.json(), context={"bot": bot})
    await dp.feed_update(bot, update)
    return {"status": "ok"}
