import asyncio
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import BufferedInputFile

TOKEN = "8889355917:AAEPQcy1eLySvKBQgkOM23tANZoIpR3VU34"
bot = Bot(token=TOKEN)
dp = Dispatcher()

active_hosts = {}      
user_sessions = {}     

def add_command(host_id: str, action: str, data: str = ""):
    host_id = str(host_id).strip().lower()
    if host_id not in active_hosts:
        active_hosts[host_id] = {"owner_id": None, "status": "online", "commands": [], "current_dir": "C:\\", "items": []}
    if "commands" not in active_hosts[host_id]:
        active_hosts[host_id]["commands"] = []
    active_hosts[host_id]["commands"].append({"action": action, "data": data})

@asynccontextmanager
async def lifespan(app: FastAPI):
    await bot.delete_webhook(drop_pending_updates=True)
    polling_task = asyncio.create_task(dp.start_polling(bot))
    yield
    polling_task.cancel()
    await bot.session.close()

app = FastAPI(lifespan=lifespan)

@app.get("/")
async def root(): return {"status": "working"}

@app.post("/poll")
async def poll(request: Request):
    try: payload = await request.json()
    except: return {"commands": []}
    
    host_id = str(payload.get("host_id", "")).strip().lower()
    uid = int(payload.get("user_id", 0))
    if not host_id or not uid: return {"commands": []}
    
    if host_id not in active_hosts:
        active_hosts[host_id] = {"owner_id": uid, "status": "online", "commands": []}
    
    active_hosts[host_id]["current_dir"] = payload.get("current_dir", "C:\\")
    active_hosts[host_id]["items"] = payload.get("items", [])
    
    report = payload.get("report", "")
    if report:
        if report.startswith("SUCCESS:"):
            msg = report.replace("SUCCESS:", "")
            await bot.send_message(chat_id=uid, text=f"✅ **Выполнено успешно:**\n{msg}", parse_mode="Markdown")
        elif report.startswith("ERROR:"):
            msg = report.replace("ERROR:", "")
            await bot.send_message(chat_id=uid, text=f"❌ **Не выполнено (Ошибка):**\n{msg}", parse_mode="Markdown")
            
    status_data = payload.get("status", "online")
    if status_data.startswith("PROCESSES_LIST:"):
        raw_procs = status_data.replace("PROCESSES_LIST:\n", "").strip()
        
        builder = InlineKeyboardBuilder()
        # Каждая строка — это "имя.exe|память"
        lines = raw_procs.split("\n") if raw_procs else []
        
        text_msg = "📋 **Активные процессы на ПК:**\n Нажми кнопку под сообщением, чтобы убить нужный процесс.\n\n"
        
        for idx, line in enumerate(lines):
            if "|" in line:
                name, mem = line.split("|")
                text_msg += f"{idx+1}. 🔹 `{name}` ({mem})\n"
                # Добавляем инлайн-кнопку для завершения (обрезаем если слишком длинное имя)
                builder.button(text=f"❌ {name}", callback_data=f"kill_proc:{name}")
        
        builder.button(text="🔄 Обновить список", callback_data="get_proc_list")
        builder.button(text="📱 Главное меню", callback_data="main_menu")
        builder.adjust(2) # Кнопки процессов будут идти по 2 в ряд
        
        await bot.send_message(chat_id=uid, text=text_msg, parse_mode="Markdown", reply_markup=builder.as_markup())
        
    elif status_data.startswith("CLIPBOARD_FROM_PC:"):
        raw_text = status_data.split("CLIPBOARD_FROM_PC:")[1]
        await bot.send_message(chat_id=uid, text=f"📋 **Скопировано на ПК:**\n`{raw_text}`", parse_mode="Markdown")
    
    cmds = active_hosts[host_id].get("commands", [])
    active_hosts[host_id]["commands"] = [] 
    return {"commands": cmds}

@app.post("/upload_screenshot")
async def upload_screenshot(request: Request, host_id: str, user_id: int):
    try:
        file_bytes = await request.body()
        if not file_bytes: return {"status": "error"}
        tg_file = BufferedInputFile(file_bytes, filename="stream.jpg")
        await bot.send_photo(chat_id=int(user_id), photo=tg_file, caption=f"📸 Скриншот экрана ПК [{host_id}] ✅")
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "details": str(e)}

def get_user_host(uid: int) -> str:
    return str(user_sessions.get(uid, {}).get("current_host", f"host_{uid}")).strip().lower()

def get_main_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🖥 Выбрать ПК", callback_data="my_hosts")
    builder.button(text="📸 Скриншот экрана", callback_data="start_stream")
    builder.button(text="📁 Проводник файлов", callback_data="explorer_view")
    builder.button(text="🎮 Процессы и Приложения", callback_data="apps_menu")
    builder.button(text="🔌 Питание", callback_data="power_menu")
    builder.adjust(1, 2, 2)
    return builder.as_markup()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    uid = message.chat.id
    user_sessions[uid] = {"current_host": f"host_{uid}"}
    await message.answer(f"🤖 Управление ПК готово.\nТекущий хост: `{get_user_host(uid)}`", parse_mode="Markdown", reply_markup=get_main_keyboard())

@dp.callback_query(F.data == "main_menu")
async def main_menu(callback: types.CallbackQuery):
    await callback.message.edit_text("💻 Главное меню управления:", reply_markup=get_main_keyboard())

@dp.callback_query(F.data == "apps_menu")
async def apps_menu(callback: types.CallbackQuery):
    builder = InlineKeyboardBuilder()
    builder.button(text="📋 Список процессов", callback_data="get_proc_list")
    builder.button(text="🌐 Открыть Браузер", callback_data="app_cmd:browser")
    builder.button(text="🎮 Запустить Steam", callback_data="app_cmd:steam")
    builder.button(text="⬅️ Назад", callback_data="main_menu")
    builder.adjust(1, 2, 1)
    await callback.message.edit_text("🎮 **Управление процессами:**", reply_markup=builder.as_markup())

@dp.callback_query(F.data == "get_proc_list")
async def get_proc_list(callback: types.CallbackQuery):
    add_command(get_user_host(callback.message.chat.id), "list_dir_or_proc", "processes")
    await callback.answer("Запрос списка процессов отправлен.")

# Обработка клика по кнопке процесса
@dp.callback_query(F.data.startswith("kill_proc:"))
async def kill_proc_handler(callback: types.CallbackQuery):
    proc_name = callback.data.split("kill_proc:")[1]
    add_command(get_user_host(callback.message.chat.id), "kill_smart", proc_name)
    await callback.answer(f"Убиваем {proc_name}...")

@dp.callback_query(F.data == "explorer_view")
async def explorer_view(callback: types.CallbackQuery):
    uid = callback.message.chat.id
    hid = get_user_host(uid)
    host_data = active_hosts.get(hid, {})
    current_dir = host_data.get("current_dir", "C:\\")
    items = host_data.get("items", [])
    
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Назад (Вверх)", callback_data="cd_back")
    
    for item in items[:15]:
        name = item.get("name")
        icon = "📁" if item.get("is_dir") else "📄"
        builder.button(text=f"{icon} {name}", callback_data=f"click_item:{name}")
        
    builder.button(text="🔄 Обновить папку", callback_data="refresh_dir")
    builder.button(text="📱 Главное меню", callback_data="main_menu")
    builder.adjust(1)
    
    await callback.message.edit_text(f"📁 **Текущий путь:** `{current_dir}`\nПоказано первых 15 элементов:", parse_mode="Markdown", reply_markup=builder.as_markup())

@dp.callback_query(F.data == "refresh_dir")
async def refresh_dir(callback: types.CallbackQuery):
    add_command(get_user_host(callback.message.chat.id), "list_dir_or_proc", "dir")
    await callback.answer("Обновляю файлы...")
    await asyncio.sleep(1)
    await explorer_view(callback)

@dp.callback_query(F.data.startswith("click_item:"))
async def click_item(callback: types.CallbackQuery):
    item_name = callback.data.split("click_item:")[1]
    add_command(get_user_host(callback.message.chat.id), "cd_into", item_name)
    await callback.answer(f"Входим в {item_name}")
    await asyncio.sleep(1) 
    await explorer_view(callback)

@dp.callback_query(F.data == "cd_back")
async def cd_back(callback: types.CallbackQuery):
    add_command(get_user_host(callback.message.chat.id), "cd_back", "")
    await callback.answer("Назад")
    await asyncio.sleep(1)
    await explorer_view(callback)

@dp.callback_query(F.data == "start_stream")
async def start_stream(callback: types.CallbackQuery):
    add_command(get_user_host(callback.message.chat.id), "stream", "1")
    await callback.answer("Скриншот запрошен.")

@dp.callback_query(F.data.startswith("app_cmd:"))
async def app_cmd_handler(callback: types.CallbackQuery):
    cmd = callback.data.split("app_cmd:")[1]
    add_command(get_user_host(callback.message.chat.id), "run_app", cmd)
    await callback.answer("Запуск...")

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
    action = callback.data.split("p_cmd:")[1]
    add_command(get_user_host(callback.message.chat.id), "power", action)
    await callback.answer("Команда отправлена.")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
