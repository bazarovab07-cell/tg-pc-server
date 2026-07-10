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

# --- МУЛЬТИХОСТ СТРУКТУРА ДАННЫХ ДЛЯ ВСЕХ ПОЛЬЗОВАТЕЛЕЙ ---
# active_hosts динамически наполняется от любого ПК, где запущен агент
active_hosts = {}      # { "host_ID": {"owner_id": 123, "status": "online", "commands": [], "current_dir": "...", "items": []} }
user_sessions = {}     # { user_id: {"current_host": "host_ID"} }

def add_command(host_id: str, action: str, data: str = ""):
    if host_id in active_hosts:
        active_hosts[host_id]["commands"].append({"action": action, "data": data})

@asynccontextmanager
async def lifespan(app: FastAPI):
    await bot.delete_webhook(drop_pending_updates=True)
    polling_task = asyncio.create_task(dp.start_polling(bot))
    print("[SERVER] Облачный сервер мультихоста запущен.")
    yield
    polling_task.cancel()
    await bot.session.close()

app = FastAPI(lifespan=lifespan)

@app.get("/")
async def root():
    return {"status": "working", "mode": "public_multi_user"}

# --- API ДЛЯ АГЕНТОВ (СЮДА СТУЧАТСЯ ВСЕ ПК) ---
@app.post("/poll")
async def poll(request: Request):
    payload = await request.json()
    host_id = payload.get("host_id")    # Уникальный ID, который ввел пользователь на ПК
    user_id = payload.get("user_id")    # ID владельца из Telegram
    
    if not host_id or not user_id:
        return {"commands": []}
        
    # Если ПК подключился впервые, регистрируем его в системе облака
    if host_id not in active_hosts:
        active_hosts[host_id] = {
            "owner_id": int(user_id),
            "status": "online",
            "commands": [],
            "current_dir": "root",
            "items": []
        }
        
    # Обновляем текущие параметры конкретной машины
    active_hosts[host_id]["status"] = payload.get("status", "online")
    active_hosts[host_id]["current_dir"] = payload.get("current_dir", "root")
    active_hosts[host_id]["items"] = payload.get("items", [])
    
    # Отдаем команды, предназначенные только для этого хоста
    cmds = active_hosts[host_id]["commands"]
    active_hosts[host_id]["commands"] = []
    return {"commands": cmds}

@app.post("/upload_screenshot")
async def upload_screenshot(host_id: str, user_id: int, file: UploadFile = File(...)):
    try:
        file_bytes = await file.read()
        tg_file = BufferedInputFile(file_bytes, filename="stream.jpg")
        # Отправляем скриншот именно тому юзеру, который владеет этим ПК
        await bot.send_photo(chat_id=user_id, photo=tg_file, caption=f"📺 Экран твоего ПК [{host_id}]")
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "details": str(e)}

# --- ДИНАМИЧЕСКИЕ КЛАВИАТУРЫ ---
def get_main_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="💻 Мои Компьютеры", callback_data="my_hosts")
    builder.button(text="📺 Статус и Стрим", callback_data="status_pc")
    builder.button(text="📁 Файловый менеджер", callback_data="explorer_root")
    builder.button(text="🎮 Команды & Процессы", callback_data="apps_menu")
    builder.button(text="🔌 Питание", callback_data="power_menu")
    builder.adjust(1, 2, 2)
    return builder.as_markup()

# --- ОБРАБОТЧИКИ ДЛЯ ВСЕХ ПОЛЬЗОВАТЕЛЕЙ ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    uid = message.chat.id
    default_hid = f"host_{uid}"
    
    # Запоминаем текущий управляемый хост для пользователя
    user_sessions[uid] = {"current_host": default_hid}
    
    await message.answer(
        f"🤖 **Добро пожаловать в пульт управления ПК!**\n\n"
        f"Этим ботом может пользоваться любой человек. Чтобы подключить свой компьютер:\n\n"
        f"1. Скачай файл агента на ПК.\n"
        f"2. Укажи в настройках файла следующие данные:\n"
        f"• Telegram ID: `{uid}`\n"
        f"• Идентификатор хоста (HOST_ID): `host_{uid}`\n\n"
        f"После запуска агента на ПК, меню ниже станет активным.",
        parse_mode="Markdown", reply_markup=get_main_keyboard()
    )

@dp.callback_query(F.data == "main_menu")
async def main_menu(callback: types.CallbackQuery):
    await callback.message.edit_text("💻 Главное меню управления:", reply_markup=get_main_keyboard())

# Просмотр списка своих ПК (если у пользователя их несколько)
@dp.callback_query(F.data == "my_hosts")
async def my_hosts(callback: types.CallbackQuery):
    uid = callback.message.chat.id
    builder = InlineKeyboardBuilder()
    
    # Находим в памяти все хосты, принадлежащие этому пользователю
    user_hosts = [hid for hid, data in active_hosts.items() if data.get("owner_id") == uid]
    
    if not user_hosts:
        await callback.message.answer("ℹ️ У тебя пока нет активных подключенных ПК. Запусти агент на компьютере!")
        await callback.answer()
        return
        
    for hid in user_hosts:
        status = active_hosts[hid]["status"]
        builder.button(text=f"🖥 {hid} ({status})", callback_data=f"select_host:{hid}")
        
    builder.button(text="⬅️ Меню", callback_data="main_menu")
    builder.adjust(1)
    await callback.message.edit_text("🎯 **Выбери ПК для управления:**", parse_mode="Markdown", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("select_host:"))
async def select_host(callback: types.CallbackQuery):
    uid = callback.message.chat.id
    target_host = callback.data.split("select_host:")[1]
    user_sessions[uid] = {"current_host": target_host}
    await callback.answer(f"Переключено на {target_host}")
    await callback.message.edit_text(f"Успешно выбран хост: `{target_host}`. Теперь все команды будут идти на него.", reply_markup=get_main_keyboard(), parse_mode="Markdown")

# --- ЛОГИКА ОТПРАВКИ КОМАНД С УЧЕТОМ СЕССИИ ПОЛЬЗОВАТЕЛЯ ---
def get_user_host(uid: int) -> str:
    return user_sessions.get(uid, {}).get("current_host", f"host_{uid}")

@dp.callback_query(F.data == "status_pc")
async def status_pc(callback: types.CallbackQuery):
    uid = callback.message.chat.id
    hid = get_user_host(uid)
    
    builder = InlineKeyboardBuilder()
    builder.button(text="📺 Получить Live-кадр экрана", callback_data="start_stream")
    builder.button(text="⬅️ Меню", callback_data="main_menu")
    builder.adjust(1)
    
    status = active_hosts.get(hid, {}).get("status", "offline")
    await callback.message.answer(f"⚙️ **Текущий целевой хост:** `{hid}`\nСтатус связи: **{status}**", parse_mode="Markdown", reply_markup=builder.as_markup())

@dp.callback_query(F.data == "start_stream")
async def start_stream(callback: types.CallbackQuery):
    uid = callback.message.chat.id
    hid = get_user_host(uid)
    add_command(hid, "stream", "1") # Запрос 1 кадра экрана
    await callback.answer("Запрос отправлен...")

@dp.callback_query(F.data == "apps_menu")
async def apps_menu(callback: types.CallbackQuery):
    builder = InlineKeyboardBuilder()
    builder.button(text="🌐 Открыть Браузер", callback_data="app_cmd:browser")
    builder.button(text="🎵 Включить Музыку", callback_data="app_cmd:music")
    builder.button(text="🎮 Открыть Steam", callback_data="app_cmd:steam")
    builder.button(text="⚙️ Диспетчер задач", callback_data="app_cmd:taskmgr")
    builder.button(text="❌ Закрыть процесс", callback_data="kill_process_prompt")
    builder.button(text="⬅️ Назад", callback_data="main_menu")
    builder.adjust(2)
    await callback.message.edit_text("🎮 **Управление приложениями:**", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("app_cmd:"))
async def app_cmd_handler(callback: types.CallbackQuery):
    uid = callback.message.chat.id
    hid = get_user_host(uid)
    cmd = callback.data.split("app_cmd:")[1]
    add_command(hid, "run_app", cmd)
    await callback.answer("Команда передана.")

@dp.callback_query(F.data == "kill_process_prompt")
async def kill_prompt(callback: types.CallbackQuery):
    await callback.message.answer("Отправь сообщение в формате:\n`kill:имя_процесса.exe` (например `kill:opera.exe`)")
    await callback.answer()

@dp.message(F.text.startswith("kill:"))
async def kill_process_msg(message: types.Message):
    uid = message.chat.id
    hid = get_user_host(uid)
    proc_name = message.text.split("kill:")[1].strip()
    add_command(hid, "kill_proc", proc_name)
    await message.answer(f"☠️ Сигнал на закрытие процесса `{proc_name}` отправлен.")

@dp.callback_query(F.data == "explorer_root")
async def explorer_root(callback: types.CallbackQuery):
    await callback.message.answer("Для управления файлами отправляй текстовые команды:\n• `del:путь` — удалить файл\n• `mkdir:путь` — создать папку")
    await callback.answer()

@dp.message(F.text.startswith("del:"))
async def delete_file_msg(message: types.Message):
    uid = message.chat.id
    hid = get_user_host(uid)
    path = message.text.split("del:")[1].strip()
    add_command(hid, "delete_file", path)
    await message.answer(f"🗑 Запрос на удаление отправлен.")

@dp.callback_query(F.data == "power_menu")
async def power_menu(callback: types.CallbackQuery):
    builder = InlineKeyboardBuilder()
    builder.button(text="🛑 Выключение ПК", callback_data="p_cmd:shutdown")
    builder.button(text="🔄 Перезагрузка ПК", callback_data="p_cmd:restart")
    builder.button(text="⬅️ Назад", callback_data="main_menu")
    builder.adjust(1)
    await callback.message.edit_text("🔌 Питание выбранного хоста:", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("p_cmd:"))
async def power_cmd(callback: types.CallbackQuery):
    uid = callback.message.chat.id
    hid = get_user_host(uid)
    action = callback.data.split("p_cmd:")[1]
    add_command(hid, "power", action)
    await callback.answer("Команда питания отправлена.")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
