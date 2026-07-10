import asyncio
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, UploadFile, File
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import BufferedInputFile

TOKEN = "8889355917:AAEPQcy1eLySvKBQgkOM23tANZoIpR3VU34"
bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- МУЛЬТИХОСТ СТРУКТУРА ДАННЫХ ---
active_hosts = {}      # { host_id: {owner_id, status, commands, current_dir, items} }
command_results = {}   # { host_id: {type, data} }
user_sessions = {}     # { user_id: {current_host, path, page} }

def add_command(host_id: str, action: str, data: str = ""):
    if host_id in active_hosts:
        active_hosts[host_id]["commands"].append({"action": action, "data": data})

@asynccontextmanager
async def lifespan(app: FastAPI):
    await bot.delete_webhook(drop_pending_updates=True)
    polling_task = asyncio.create_task(dp.start_polling(bot))
    print("[SERVER] Облачный сервер запущен. Бот активен.")
    yield
    polling_task.cancel()
    await bot.session.close()

app = FastAPI(lifespan=lifespan)

@app.get("/")
async def root():
    return {"status": "working", "features": ["multihost", "streaming", "file_manager"]}

# --- API ДЛЯ АГЕНТА ПК ---
@app.post("/poll")
async def poll(request: Request):
    payload = await request.json()
    host_id = payload.get("host_id")
    user_id = payload.get("user_id")
    
    if not host_id or not user_id:
        return {"commands": []}
        
    if host_id not in active_hosts:
        active_hosts[host_id] = {
            "owner_id": user_id, "status": "online", "commands": [], "current_dir": "root", "items": []
        }
        
    active_hosts[host_id]["status"] = payload.get("status", "online")
    active_hosts[host_id]["current_dir"] = payload.get("current_dir", "root")
    active_hosts[host_id]["items"] = payload.get("items", [])
    
    if payload.get("result_type") and payload.get("result_type") != "none":
        command_results[host_id] = {"type": payload["result_type"], "data": payload.get("result_data", "")}
    
    cmds = active_hosts[host_id]["commands"]
    active_hosts[host_id]["commands"] = []
    return {"commands": cmds}

@app.post("/upload_screenshot")
async def upload_screenshot(host_id: str, user_id: int, file: UploadFile = File(...)):
    """Пункт 3: Прием покадрового стрима экрана от ПК"""
    try:
        file_bytes = await file.read()
        tg_file = BufferedInputFile(file_bytes, filename="stream.jpg")
        await bot.send_photo(chat_id=user_id, photo=tg_file, caption=f"📺 Live-экран ПК [{host_id}]")
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "details": str(e)}

# --- ТЕЛЕГРАМ КЛАВИАТУРЫ ---
def get_main_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="💻 Статус и Стрим", callback_data="status_pc")
    builder.button(text="📁 Управление Файлами", callback_data="explorer_root")
    builder.button(text="🎮 Команды & Игры", callback_data="apps_menu")
    builder.button(text="🔌 Питание ПК", callback_data="power_menu")
    builder.adjust(2)
    return builder.as_markup()

# --- ОБРАБОТЧИКИ ТЕЛЕГРАМ КОМАНД ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    uid = message.chat.id
    # Регистрируем сессию и создаем дефолтный хост-ID для этого юзера
    hid = f"pc_{uid}"
    user_sessions[uid] = {"current_host": hid, "path": "root", "page": 0}
    if hid not in active_hosts:
        active_hosts[hid] = {"owner_id": uid, "status": "offline", "commands": [], "current_dir": "root", "items": []}
        
    await message.answer(
        f"🤖 **Панель управления ПК активирована**\n\n"
        f"ID для твоего ПК-агента:\n`{uid}`\n\n"
        f"Используй этот ID в настройках программы на ПК.",
        parse_mode="Markdown", reply_markup=get_main_keyboard()
    )

@dp.callback_query(F.data == "main_menu")
async def main_menu(callback: types.CallbackQuery):
    await callback.message.edit_text("💻 Главное меню управления ПК:", reply_markup=get_main_keyboard())

@dp.callback_query(F.data == "status_pc")
async def status_pc(callback: types.CallbackQuery):
    uid = callback.message.chat.id
    hid = user_sessions.get(uid, {}).get("current_host", f"pc_{uid}")
    
    builder = InlineKeyboardBuilder()
    builder.button(text="📺 Включить Live-стрим (3 кадра)", callback_data="start_stream")
    builder.button(text="⬅️ Меню", callback_data="main_menu")
    builder.adjust(1)
    
    status = active_hosts.get(hid, {}).get("status", "offline")
    await callback.message.answer(f"⚙️ **Текущий хост:** `{hid}`\nСтатус: **{status}**", parse_mode="Markdown", reply_markup=builder.as_markup())

@dp.callback_query(F.data == "start_stream")
async def start_stream(callback: types.CallbackQuery):
    uid = callback.message.chat.id
    hid = user_sessions.get(uid, {}).get("current_host", f"pc_{uid}")
    add_command(hid, "stream", "3")  # Команда агенту выслать 3 кадра
    await callback.answer("Запрос потока экрана отправлен...")

# --- МЕНЮ КОМАНД (Пункт 4) ---
@dp.callback_query(F.data == "apps_menu")
async def apps_menu(callback: types.CallbackQuery):
    builder = InlineKeyboardBuilder()
    builder.button(text="🌐 Открыть Браузер", callback_data="app_cmd:browser")
    builder.button(text="🎵 Включить Музыку", callback_data="app_cmd:music")
    builder.button(text="🎮 Открыть Steam", callback_data="app_cmd:steam")
    builder.button(text="⚙️ Диспетчер задач", callback_data="app_cmd:taskmgr")
    builder.button(text="❌ Закрыть Игру (Процесс)", callback_data="kill_process_prompt")
    builder.button(text="⬅️ Меню", callback_data="main_menu")
    builder.adjust(2)
    await callback.message.edit_text("🎮 **Управление приложениями хоста:**", parse_mode="Markdown", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("app_cmd:"))
async def app_cmd_handler(callback: types.CallbackQuery):
    uid = callback.message.chat.id
    hid = user_sessions.get(uid, {}).get("current_host", f"pc_{uid}")
    cmd = callback.data.split("app_cmd:")[1]
    add_command(hid, "run_app", cmd)
    await callback.answer(f"Команда на запуск {cmd} отправлена.")

@dp.callback_query(F.data == "kill_process_prompt")
async def kill_prompt(callback: types.CallbackQuery):
    await callback.message.answer("Чтобы закрыть процесс, отправь текстовое сообщение в формате: `kill:название_программы.exe` (например `kill:fortnite.exe`)")
    await callback.answer()

@dp.message(F.text.startswith("kill:"))
async def kill_process_msg(message: types.Message):
    uid = message.chat.id
    hid = user_sessions.get(uid, {}).get("current_host", f"pc_{uid}")
    proc_name = message.text.split("kill:")[1].strip()
    add_command(hid, "kill_proc", proc_name)
    await message.answer(f"☠️ Сигнал на уничтожение процесса `{proc_name}` отправлен на ПК.")

# --- УПРАВЛЕНИЕ ФАЙЛАМИ (Пункт 5) ---
@dp.callback_query(F.data == "explorer_root")
async def explorer_root(callback: types.CallbackQuery):
    await callback.message.answer("Функция проводника активна. Для создания или удаления файлов отправляйте текстовые триггеры:\n• `del:путь_к_файлу` — удалить\n• `mkdir:путь_к_папке` — создать папку.")
    await callback.answer()

@dp.message(F.text.startswith("del:"))
async def delete_file_msg(message: types.Message):
    uid = message.chat.id
    hid = user_sessions.get(uid, {}).get("current_host", f"pc_{uid}")
    path = message.text.split("del:")[1].strip()
    add_command(hid, "delete_file", path)
    await message.answer(f"🗑 Запрос на удаление объекта `{path}` отправлен.")

# --- ПИТАНИЕ ПК ---
@dp.callback_query(F.data == "power_menu")
async def power_menu(callback: types.CallbackQuery):
    builder = InlineKeyboardBuilder()
    builder.button(text="🛑 Выключение", callback_data="p_cmd:shutdown")
    builder.button(text="🔄 Перезагрузка", callback_data="p_cmd:restart")
    builder.button(text="⬅️ Назад", callback_data="main_menu")
    builder.adjust(1)
    await callback.message.edit_text("🔌 Команды питания:", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("p_cmd:"))
async def power_cmd(callback: types.CallbackQuery):
    uid = callback.message.chat.id
    hid = user_sessions.get(uid, {}).get("current_host", f"pc_{uid}")
    action = callback.data.split("p_cmd:")[1]
    add_command(hid, "power", action)
    await callback.answer("Команда питания улетела на ПК.")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
