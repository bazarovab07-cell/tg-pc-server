import os
import asyncio
import logging
import base64
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.filters import Command

# Настройка логирования
logging.basicConfig(level=logging.INFO)

TOKEN = "8889355917:AAEPQcy1eLySvKBQgkOM23tANZoIpR3VU34"
# Рендер автоматически выдает этот URL. Если тестируешь локально, используй ngrok
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL", "https://YOUR_DOMAIN.onrender.com")

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Глобальные хранилища данных
active_hosts = {}   # host_id -> data
user_sessions = {}  # uid -> data
last_processed_reports = {}  # host_id -> last_report_text

@asynccontextmanager
async def lifespan(app: FastAPI):
    webhook_url = f"{RENDER_URL}/webhook"
    logging.info(f"Установка вебхука: {webhook_url}")
    await bot.set_webhook(url=webhook_url, drop_pending_updates=True)
    yield
    logging.info("Удаление вебхука...")
    await bot.delete_webhook()

app = FastAPI(lifespan=lifespan)

# Генерирует файловый менеджер в виде инлайн-кнопок
def get_file_manager_keyboard(items, current_dir):
    keyboard = []
    keyboard.append([InlineKeyboardButton(text="📁 .. (Вверх)", callback_data="cd_..")])
    
    folders = [x for x in items if x.get("type") == "folder"]
    files = [x for x in items if x.get("type") == "file"]
    
    # Ограничиваем вывод, чтобы кнопки не уперлись в лимиты Telegram (макс 100 кнопок)
    for folder in folders[:30]:
        name = folder.get("name", "")
        keyboard.append([InlineKeyboardButton(text=f"📁 {name}", callback_data=f"cd_{name}")])
        
    for file in files[:30]:
        name = file.get("name", "")
        size = file.get("size", "0 KB")
        keyboard.append([InlineKeyboardButton(text=f"📄 {name} ({size})", callback_data=f"file_{name}")])
        
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
        return {"commands": [], "clear_report": False}
    
    uid = int(uid)
    
    # Регистрация или обновление хоста в сети
    if host_id not in active_hosts:
        active_hosts[host_id] = {"owner_id": uid, "status": "online", "commands": []}
    
    active_hosts[host_id]["current_dir"] = payload.get("current_dir", "C:\\")
    active_hosts[host_id]["items"] = payload.get("items", [])
    
    if uid not in user_sessions:
        user_sessions[uid] = {}
    user_sessions[uid]["current_host"] = host_id
    
    last_msg_id = user_sessions[uid].get("last_msg_id")
    
    # === БЛОК АВТОУДАЛЕНИЯ УВЕДОМЛЕНИЙ ===
    report = payload.get("report", "")
    should_clear_report = False
    
    if report:
        text_str = str(report).strip()
        last_report = last_processed_reports.get(host_id, "")
        
        if text_str != last_report:
            last_processed_reports[host_id] = text_str  
            should_clear_report = True                  
            
            async def send_and_delete_report(chat_id, report_text):
                try:
                    status_msg = None
                    if report_text.startswith("SUCCESS:"):
                        msg = report_text.replace("SUCCESS:", "").strip()
                        status_msg = await bot.send_message(chat_id=chat_id, text=f"✅ **Успешно:** {msg}", parse_mode="Markdown")
                    elif report_text.startswith("ERROR:"):
                        msg = report_text.replace("ERROR:", "").strip()
                        status_msg = await bot.send_message(chat_id=chat_id, text=f"❌ **Ошибка:** {msg}", parse_mode="Markdown")
                    
                    if status_msg:
                        await asyncio.sleep(5)
                        try:
                            await bot.delete_message(chat_id=chat_id, message_id=status_msg.message_id)
                        except Exception:
                            pass
                except Exception as e:
                    logging.error(f"Ошибка удаления сообщения: {e}")

            asyncio.create_task(send_and_delete_report(uid, text_str))
        else:
            should_clear_report = True
    # === КОНЕЦ БЛОКА АВТОУДАЛЕНИЯ ===

    # Обработка процессов
    if "processes" in payload:
        proc_list = payload.get("processes", [])
        text = "📊 **Список активных процессов:**\n\n" + "\n".join([f"🔹 `{p}`" for p in proc_list[:40]])
        await bot.send_message(chat_id=uid, text=text, parse_mode="Markdown")
        
    # Обработка буфера обмена ПК
    if "clipboard" in payload:
        clip_text = payload.get("clipboard", "")
        await bot.send_message(chat_id=uid, text=f"📋 **Буфер обмена ПК:**\n`{clip_text}`", parse_mode="Markdown")

    # Прием файлов
    if "file_data" in payload and "file_name" in payload:
        file_name = payload.get("file_name")
        raw_data = base64.b64decode(payload.get("file_data"))
        input_file = BufferedInputFile(raw_data, filename=file_name)
        await bot.send_document(chat_id=uid, document=input_file, caption=f"📥 Файл скачан: `{file_name}`", parse_mode="Markdown")

    # Прием скриншота
    if "screenshot_data" in payload:
        raw_data = base64.b64decode(payload.get("screenshot_data"))
        input_file = BufferedInputFile(raw_data, filename="screenshot.png")
        await bot.send_photo(chat_id=uid, photo=input_file, caption="📸 Скриншот экрана ПК")

    # Обновляем основную панель интерфейса
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

    # Отдаем команды агенту
    commands = active_hosts[host_id].get("commands", [])
    active_hosts[host_id]["commands"] = [] 
    
    return {"commands": commands, "clear_report": should_clear_report}

# Хэндлер на команду /start
@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    uid = message.from_user.id
    user_sessions[uid] = {"state": None, "last_msg_id": None}
    
    # Ищем, нет ли уже онлайн-хоста для этого юзера
    target_host = None
    for hid, data in active_hosts.items():
        if data.get("owner_id") == uid:
            target_host = hid
            break
            
    if target_host:
        user_sessions[uid]["current_host"] = target_host
        kb = get_file_manager_keyboard(active_hosts[target_host]["items"], active_hosts[target_host]["current_dir"])
        msg = await message.answer(
            text=f"💻 **Хост найден в сети:** `{target_host}`\n📁 **Путь:** `{active_hosts[target_host]['current_dir']}`",
            reply_markup=kb,
            parse_mode="Markdown"
        )
        user_sessions[uid]["last_msg_id"] = msg.message_id
    else:
        await message.answer("👋 Привет! Твой ПК пока оффлайн. Запусти скрипт-агент на компьютере.")

# Хэндлер для отправки консольных команд
@dp.message()
async def handle_text_messages(message: types.Message):
    uid = message.from_user.id
    session = user_sessions.get(uid, {})
    host_id = session.get("current_host")
    state = session.get("state")
    
    if not host_id or host_id not in active_hosts:
        await message.answer("❌ Нет активного подключения к ПК. Запусти агент.")
        return

    if state == "awaiting_cmd":
        active_hosts[host_id]["commands"].append({"type": "cmd_exec", "param": message.text})
        user_sessions[uid]["state"] = None
        await message.answer(f"⏳ Команда `{message.text}` отправлена на исполнение...")
        return

# Обработчик нажатий на инлайн-кнопки
@dp.callback_query()
async def handle_callbacks(callback: types.CallbackQuery):
    uid = callback.from_user.id
    session = user_sessions.get(uid, {})
    host_id = session.get("current_host")
    
    if not host_id or host_id not in active_hosts:
        await callback.answer("❌ Активный ПК не найден или оффлайн.", show_alert=True)
        return

    data = callback.data
    user_sessions[uid]["last_msg_id"] = callback.message.message_id

    if data.startswith("cd_"):
        folder_name = data.replace("cd_", "")
        active_hosts[host_id]["commands"].append({"type": "cd", "param": folder_name})
        await callback.answer("Смена папки...")
        
    elif data.startswith("file_"):
        file_name = data.replace("file_", "")
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

    elif data.startswith("run_"):
        f_name = data.replace("run_", "")
        active_hosts[host_id]["commands"].append({"type": "run_file", "param": f_name})
        await callback.answer("Запуск файла...")
        
    elif data.startswith("download_"):
        f_name = data.replace("download_", "")
        active_hosts[host_id]["commands"].append({"type": "download_file", "param": f_name})
        await callback.answer("Скачивание...")

    elif data.startswith("delete_"):
        f_name = data.replace("delete_", "")
        active_hosts[host_id]["commands"].append({"type": "delete_file", "param": f_name})
        await callback.answer("Удаление...")

    elif data == "back_to_manager":
        kb = get_file_manager_keyboard(active_hosts[host_id]["items"], active_hosts[host_id]["current_dir"])
        await bot.edit_message_text(
            chat_id=uid,
            message_id=callback.message.message_id,
            text=f"💻 **Хост:** `{host_id}`\n📁 **Текущий путь:** `{active_hosts[host_id]['current_dir']}`",
            reply_markup=kb,
            parse_mode="Markdown"
        )
        await callback.answer()
        
    elif data == "show_processes":
        active_hosts[host_id]["commands"].append({"type": "get_processes"})
        await callback.answer("Запрос процессов...")
        
    elif data == "get_clipboard":
        active_hosts[host_id]["commands"].append({"type": "get_clipboard"})
        await callback.answer("Чтение буфера...")

    elif data == "take_screenshot":
        active_hosts[host_id]["commands"].append({"type": "screenshot"})
        await callback.answer("Снимок экрана...")

    elif data == "run_cmd":
        user_sessions[uid]["state"] = "awaiting_cmd"
        await bot.send_message(chat_id=uid, text="⌨️ Введите консольную команду (CMD), которую нужно выполнить на ПК:")
        await callback.answer()

    elif data == "pc_shutdown":
        active_hosts[host_id]["commands"].append({"type": "shutdown"})
        await callback.answer("Команда на выключение отправлена!", show_alert=True)

# Точка входа для вебхуков Telegram
@app.post("/webhook")
async def webhook_endpoint(request: Request):
    try:
        json_data = await request.json()
        update = types.Update.model_validate(json_data, context={"bot": bot})
        await dp.feed_update(bot, update)
        return {"status": "ok"}
    except Exception as e:
        logging.error(f"Ошибка в эндпоинте вебхука: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})
