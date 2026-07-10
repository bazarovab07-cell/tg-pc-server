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
    if host_id in active_hosts:
        if "commands" not in active_hosts[host_id]:
            active_hosts[host_id]["commands"] = []
        active_hosts[host_id]["commands"].append({"action": action, "data": data})
        print(f"[SERVER] Добавлена команда {action} для {host_id}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    await bot.delete_webhook(drop_pending_updates=True)
    polling_task = asyncio.create_task(dp.start_polling(bot))
    print("[SERVER] Облачный сервер запущен.")
    yield
    polling_task.cancel()
    await bot.session.close()

app = FastAPI(lifespan=lifespan)

@app.get("/")
async def root():
    return {"status": "working", "mode": "no_multipart_compat"}

@app.post("/poll")
async def poll(request: Request):
    payload = await request.json()
    host_id = payload.get("host_id")    
    user_id = payload.get("user_id")    
    
    if not host_id or not user_id:
        return {"commands": []}
        
    # Регистрируем или обновляем хост
    if host_id not in active_hosts:
        active_hosts[host_id] = {
            "owner_id": int(user_id), "status": "online", "commands": [], "current_dir": "root", "items": []
        }
    
    # Автоматически привязываем пользователя к его хосту, если привязка слетела
    uid = int(user_id)
    if uid not in user_sessions or user_sessions[uid].get("current_host") != host_id:
        user_sessions[uid] = {"current_host": host_id}
        
    status_data = payload.get("status", "online")
    
    # Обработка буфера обмена С КОМПЬЮТЕРА НА ТЕЛЕФОН
    if status_data.startswith("CLIPBOARD_FROM_PC:"):
        raw_text = status_data.split("CLIPBOARD_FROM_PC:")[1]
        await bot.send_message(chat_id=uid, text=f"📋 **Скопировано на ПК:**\n`{raw_text}`", parse_mode="Markdown")
        active_hosts[host_id]["status"] = "online"
    else:
        active_hosts[host_id]["status"] = status_data
        
    active_hosts[host_id]["current_dir"] = payload.get("current_dir", "root")
    active_hosts[host_id]["items"] = payload.get("items", [])
    
    cmds = active_hosts[host_id].get("commands", [])
    active_hosts[host_id]["commands"] = [] # Очищаем массив после выдачи
    return {"commands": cmds}

@app.post("/upload_screenshot")
async def upload_screenshot(request: Request, host_id: str, user_id: int):
    try:
        file_bytes = await request.body()
        if not file_bytes:
            return {"status": "error", "details": "Empty body"}
            
        tg_file = BufferedInputFile(file_bytes, filename="stream.jpg")
        await bot.send_photo(chat_id=user_id, photo=tg_file, caption=f"📺 Экран ПК [{host_id}]")
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "details": str(e)}

def get_user_host(uid: int) -> str:
    # Возвращает текущий привязанный хост или дефолтный по маске
    if uid in user_sessions and user_sessions[uid].get("current_host"):
        return user_sessions[uid]["current_host"]
    return f"host_{uid}"

def get_main_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🖥 Выбрать/Сменить ПК", callback_data="my_hosts")
    builder.button(text="📺 Статус и Стрим экрана", callback_data="status_pc")
    builder.button(text="📁 Управление Файлами", callback_data="explorer_root")
    builder.button(text="🎮 Команды & Процессы", callback_data="apps_menu")
    builder.button(text="🔌 Управление Питанием", callback_data="power_menu")
    builder.adjust(1, 2, 2)
    return builder.as_markup()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    uid = message.chat.id
    default_hid = f"host_{uid}"
    if uid not in user_sessions:
        user_sessions[uid] = {"current_host": default_hid}
    
    await message.answer(
        f"🤖 **Система управления ПК готова к работе!**\n\n"
        f"Ваш ID: `{uid}`\n"
        f"Текущий выбранный ПК: `{get_user_host(uid)}`",
        parse_mode="Markdown", reply_markup=get_main_keyboard()
    )

@dp.callback_query(F.data == "main_menu")
async def main_menu(callback: types.CallbackQuery):
    await callback.message.edit_text("💻 Главное меню управления:", reply_markup=get_main_keyboard())

@dp.callback_query(F.data == "my_hosts")
async def my_hosts(callback: types.CallbackQuery):
    uid = callback.message.chat.id
    builder = InlineKeyboardBuilder()
    user_hosts = [hid for hid, data in active_hosts.items() if data.get("owner_id") == uid]
    
    if not user_hosts:
        await callback.message.answer("ℹ️ Нет активных устройств в сети. Запустите агент.")
        await callback.answer()
        return
        
    for hid in user_hosts:
        status = active_hosts[hid]["status"]
        builder.button(text=f"🖥 {hid} ({status})", callback_data=f"select_host:{hid}")
        
    builder.button(text="⬅️ В меню", callback_data="main_menu")
    builder.adjust(1)
    await callback.message.edit_text("🎯 **Выберите компьютер:**", parse_mode="Markdown", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("select_host:"))
async def select_host(callback: types.CallbackQuery):
    uid = callback.message.chat.id
    target_host = callback.data.split("select_host:")[1]
    user_sessions[uid] = {"current_host": target_host}
    await callback.answer(f"Выбран {target_host}")
    await callback.message.edit_text(f"Подключено к: `{target_host}`.", reply_markup=get_main_keyboard(), parse_mode="Markdown")

@dp.callback_query(F.data == "status_pc")
async def status_pc(callback: types.CallbackQuery):
    uid = callback.message.chat.id
    hid = get_user_host(uid)
    
    builder = InlineKeyboardBuilder()
    builder.button(text="📸 Сделать снимок экрана", callback_data="start_stream")
    builder.button(text="⬅️ Меню", callback_data="main_menu")
    builder.adjust(1)
    
    status = active_hosts.get(hid, {}).get("status", "offline")
    await callback.message.answer(f"⚙️ **Устройство:** `{hid}`\nСтатус: **{status}**", parse_mode="Markdown", reply_markup=builder.as_markup())

@dp.callback_query(F.data == "start_stream")
async def start_stream(callback: types.CallbackQuery):
    uid = callback.message.chat.id
    hid = get_user_host(uid)
    add_command(hid, "stream", "1")
    await callback.answer("Запрос скриншота отправлен.")

@dp.callback_query(F.data == "apps_menu")
async def apps_menu(callback: types.CallbackQuery):
    builder = InlineKeyboardBuilder()
    builder.button(text="🌐 Открыть Браузер", callback_data="app_cmd:browser")
    builder.button(text="🎵 Включить Музыку", callback_data="app_cmd:music")
    builder.button(text="🎮 Открыть Steam", callback_data="app_cmd:steam")
    builder.button(text="⚙️ Диспетчер задач", callback_data="app_cmd:taskmgr")
    builder.button(text="❌ Завершить процесс", callback_data="kill_process_prompt")
    builder.button(text="⬅️ Назад", callback_data="main_menu")
    builder.adjust(2)
    await callback.message.edit_text("🎮 **Управление приложениями:**", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("app_cmd:"))
async def app_cmd_handler(callback: types.CallbackQuery):
    uid = callback.message.chat.id
    hid = get_user_host(uid)
    cmd = callback.data.split("app_cmd:")[1]
    add_command(hid, "run_app", cmd)
    await callback.answer("Команда отправлена.")

@dp.callback_query(F.data == "kill_process_prompt")
async def kill_prompt(callback: types.CallbackQuery):
    await callback.message.answer("Чтобы завершить процесс, пришлите:\n`kill:имя_файла.exe`")
    await callback.answer()

@dp.message(F.text.startswith("kill:"))
async def kill_process_msg(message: types.Message):
    uid = message.chat.id
    hid = get_user_host(uid)
    proc_name = message.text.split("kill:")[1].strip()
    add_command(hid, "kill_proc", proc_name)
    await message.answer(f"☠️ Команда на уничтожение процесса `{proc_name}` передана.")

@dp.callback_query(F.data == "explorer_root")
async def explorer_root(callback: types.CallbackQuery):
    await callback.message.answer("Управление файлами:\n• `del:путь` — удалить\n• `mkdir:путь` — создать папку")
    await callback.answer()

@dp.message(F.text.startswith("del:"))
async def delete_file_msg(message: types.Message):
    uid = message.chat.id
    hid = get_user_host(uid)
    path = message.text.split("del:")[1].strip()
    add_command(hid, "delete_file", path)
    await message.answer(f"🗑 Запрос на удаление объекта отправлен.")

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
    hid = get_user_host(uid)
    action = callback.data.split("p_cmd:")[1]
    add_command(hid, "power", action)
    await callback.answer("Сигнал питания отправлен.")

# --- СИНХРОНИЗАЦИЯ БУФЕРА ОБМЕНА ИЗ ТЕЛЕФОНА НА ПК ---
@dp.message(F.text & ~F.text.startswith("/") & ~F.text.startswith("kill:") & ~F.text.startswith("del:"))
async def handle_text_for_clipboard(message: types.Message):
    uid = message.chat.id
    hid = get_user_host(uid)
    add_command(hid, "update_clipboard", message.text)
    await message.answer(f"📋 Текст отправлен в буфер обмена устройства `{hid}`!")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
