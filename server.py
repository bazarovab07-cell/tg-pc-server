import os
import logging
import base64
import json
import urllib.request  # Встроенная библиотека, работает ВСЕГДА и без установки
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command

# Настройка подробного логирования
logging.basicConfig(level=logging.INFO)

TOKEN = "8889355917:AAEPQcy1eLySvKBQgkOM23tANZoIpR3VU34"
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL", "").strip()

if not RENDER_URL or "YOUR_DOMAIN" in RENDER_URL:
    RENDER_URL = "https://tg-pc-server.onrender.com"

bot = Bot(token=TOKEN)
dp = Dispatcher()

active_hosts = {}   # host_id -> data
user_sessions = {}  # uid -> data
last_processed_reports = {}  # host_id -> last_report_text

# --- ОТПРАВКА ЧЕРЕЗ ВСТРОЕННЫЙ URLLIB (БЕЗ ВНЕШНИХ БИБЛИОТЕК) ---
def send_tg_message(chat_id, text, parse_mode="Markdown"):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    data = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": parse_mode}).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            response.read()
    except Exception as e:
        logging.error(f"Ошибка прямой отправки сообщения: {e}")

def send_tg_document(chat_id, file_name, file_base64, caption=""):
    url = f"https://api.telegram.org/bot{TOKEN}/sendDocument"
    try:
        file_data = base64.b64decode(file_base64)
        boundary = '----WebKitFormBoundary7MA4YWxkTrZu0gW'
        
        # Формируем multipart/form-data вручную
        body = []
        body.append(f'--{boundary}'.encode('utf-8'))
        body.append(f'Content-Disposition: form-data; name="chat_id"'.encode('utf-8'))
        body.append(''.encode('utf-8'))
        body.append(str(chat_id).encode('utf-8'))
        
        body.append(f'--{boundary}'.encode('utf-8'))
        body.append(f'Content-Disposition: form-data; name="caption"'.encode('utf-8'))
        body.append(''.encode('utf-8'))
        body.append(caption.encode('utf-8'))
        
        body.append(f'--{boundary}'.encode('utf-8'))
        body.append(f'Content-Disposition: form-data; name="document"; filename="{file_name}"'.encode('utf-8'))
        body.append('Content-Type: application/octet-stream'.encode('utf-8'))
        body.append(''.encode('utf-8'))
        body.append(file_data)
        
        body.append(f'--{boundary}--'.encode('utf-8'))
        body.append(''.encode('utf-8'))
        
        payload = b'\r\n'.join(body)
        
        headers = {
            'Content-Type': f'multipart/form-data; boundary={boundary}',
            'Content-Length': str(len(payload))
        }
        
        req = urllib.request.Request(url, data=payload, headers=headers, method='POST')
        with urllib.request.urlopen(req, timeout=20) as response:
            response.read()
    except Exception as e:
        logging.error(f"Ошибка прямой отправки файла: {e}")

def send_tg_photo(chat_id, photo_base64, caption=""):
    url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
    try:
        photo_data = base64.b64decode(photo_base64)
        boundary = '----WebKitFormBoundary7MA4YWxkTrZu0gW'
        
        body = []
        body.append(f'--{boundary}'.encode('utf-8'))
        body.append(f'Content-Disposition: form-data; name="chat_id"'.encode('utf-8'))
        body.append(''.encode('utf-8'))
        body.append(str(chat_id).encode('utf-8'))
        
        body.append(f'--{boundary}'.encode('utf-8'))
        body.append(f'Content-Disposition: form-data; name="caption"'.encode('utf-8'))
        body.append(''.encode('utf-8'))
        body.append(caption.encode('utf-8'))
        
        body.append(f'--{boundary}'.encode('utf-8'))
        body.append(f'Content-Disposition: form-data; name="photo"; filename="screenshot.png"'.encode('utf-8'))
        body.append('Content-Type: image/png'.encode('utf-8'))
        body.append(''.encode('utf-8'))
        body.append(photo_data)
        
        body.append(f'--{boundary}--'.encode('utf-8'))
        body.append(''.encode('utf-8'))
        
        payload = b'\r\n'.join(body)
        
        headers = {
            'Content-Type': f'multipart/form-data; boundary={boundary}',
            'Content-Length': str(len(payload))
        }
        
        req = urllib.request.Request(url, data=payload, headers=headers, method='POST')
        with urllib.request.urlopen(req, timeout=20) as response:
            response.read()
    except Exception as e:
        logging.error(f"Ошибка прямой отправки фото: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    base_url = RENDER_URL.rstrip("/")
    webhook_url = f"{base_url}/webhook"
    logging.info(f"--- УСТАНОВКА ВЕБХУКА TG --- URL: {webhook_url}")
    try:
        await bot.set_webhook(url=webhook_url, drop_pending_updates=True)
    except Exception as e:
        logging.error(f"Критическая ошибка вебхука: {e}")
    yield
    try:
        await bot.delete_webhook()
    except Exception:
        pass

app = FastAPI(lifespan=lifespan)

def get_file_manager_keyboard(items, current_dir):
    keyboard = []
    keyboard.append([InlineKeyboardButton(text="📁 .. (Вверх)", callback_data="cd_..")])
    
    folders = [x for x in items if x.get("type") == "folder"]
    files = [x for x in items if x.get("type") == "file"]
    
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

@app.post("/poll")
async def poll(request: Request):
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})
        
    host_id = payload.get("host_id")
    uid_raw = payload.get("uid")
    
    if not host_id or not uid_raw: 
        return {"commands": [], "clear_report": False}
    
    try:
        uid = int(uid_raw)
    except ValueError:
        return {"commands": [], "clear_report": False}
    
    if host_id not in active_hosts:
        active_hosts[host_id] = {"owner_id": uid, "status": "online", "commands": []}
    
    active_hosts[host_id]["current_dir"] = payload.get("current_dir", "C:\\")
    active_hosts[host_id]["items"] = payload.get("items", [])
    
    if uid not in user_sessions:
        user_sessions[uid] = {}
    user_sessions[uid]["current_host"] = host_id
    
    last_msg_id = user_sessions[uid].get("last_msg_id")
    
    report = payload.get("report", "")
    should_clear_report = False
    
    if report:
        text_str = str(report).strip()
        if text_str != last_processed_reports.get(host_id, ""):
            last_processed_reports[host_id] = text_str  
            should_clear_report = True                  
            
            if text_str.startswith("SUCCESS:"):
                msg = text_str.replace("SUCCESS:", "").strip()
                send_tg_message(uid, f"✅ **Успешно:** {msg}")
            elif text_str.startswith("ERROR:"):
                msg = text_str.replace("ERROR:", "").strip()
                send_tg_message(uid, f"❌ **Ошибка:** {msg}")
        else:
            should_clear_report = True

    if "processes" in payload:
        proc_list = payload.get("processes", [])
        text = "📊 **Список активных процессов:**\n\n" + "\n".join([f"🔹 `{p}`" for p in proc_list[:40]])
        send_tg_message(uid, text)
        
    if "clipboard" in payload:
        clip_text = payload.get("clipboard", "")
        send_tg_message(uid, f"📋 **Буфер обмена ПК:**\n`{clip_text}`")

    if "file_data" in payload and "file_name" in payload:
        send_tg_document(uid, payload.get("file_name"), payload.get("file_data"), f"📥 Файл скачан: `{payload.get('file_name')}`")

    if "screenshot_data" in payload:
        send_tg_photo(uid, payload.get("screenshot_data"), "📸 Скриншот экрана ПК")

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

    commands = active_hosts[host_id].get("commands", [])
    active_hosts[host_id]["commands"] = [] 
    
    return {"commands": commands, "clear_report": should_clear_report}


# --- БЛОК ХЭНДЛЕРОВ AIOGRAM ---

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    uid = message.from_user.id
    user_sessions[uid] = {"state": None, "last_msg_id": None}
    
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

@dp.callback_query()
async def handle_callbacks(callback: types.CallbackQuery):
    uid = callback.from_user.id
    session = user_sessions.get(uid, {})
    host_id = session.get("current_host")
    
    if not host_id or host_id not in active_hosts:
        await callback.answer("❌ Активный ПК не найден.", show_alert=True)
        return

    data = callback.data
    user_sessions[uid]["last_msg_id"] = callback.message.message_id

    if data.startswith("cd_"):
        active_hosts[host_id]["commands"].append({"type": "cd", "param": data.replace("cd_", "")})
        await callback.answer()
        
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
        active_hosts[host_id]["commands"].append({"type": "run_file", "param": data.replace("run_", "")})
        await callback.answer("Запуск...")
        
    elif data.startswith("download_"):
        active_hosts[host_id]["commands"].append({"type": "download_file", "param": data.replace("download_", "")})
        await callback.answer("Скачивание...")

    elif data.startswith("delete_"):
        active_hosts[host_id]["commands"].append({"type": "delete_file", "param": data.replace("delete_", "")})
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
        await bot.send_message(chat_id=uid, text="⌨️ Введите консольную команду (CMD):")
        await callback.answer()

    elif data == "pc_shutdown":
        active_hosts[host_id]["commands"].append({"type": "shutdown"})
        await callback.answer("Команда отправлена!", show_alert=True)

@dp.message(AwaitingCMD())
async def handle_text_messages(message: types.Message):
    uid = message.from_user.id
    session = user_sessions.get(uid, {})
    host_id = session.get("current_host")
    state = session.get("state")
    
    if state == "awaiting_cmd" and host_id in active_hosts:
        active_hosts[host_id]["commands"].append({"type": "cmd_exec", "param": message.text})
        user_sessions[uid]["state"] = None
        await message.answer(f"⏳ Команда `{message.text}` отправлена...")

@app.post("/webhook")
async def webhook_endpoint(request: Request):
    try:
        json_data = await request.json()
        update = types.Update.model_validate(json_data, context={"bot": bot})
        await dp.feed_update(bot, update)
        return {"status": "ok"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("server:app", host="0.0.0.0", port=port)
