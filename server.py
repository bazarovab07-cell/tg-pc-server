import asyncio
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

# --- НАСТРОЙКИ ---
TOKEN = "8889355917:AAEPQcy1eLySvKBQgkOM23tANZoIpR3VU34"

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Хранилище состояний
active_hosts = {}
command_results = {}
user_sessions = {}

def add_command(user_id: int, action: str, data: str = ""):
    if user_id not in active_hosts:
        active_hosts[user_id] = {"commands": [], "status": "offline", "current_dir": "root", "items": []}
    active_hosts[user_id]["commands"].append({"action": action, "data": data})

# --- МЕХАНИЗМ ЗАПУСКА БОТА ВНУТРИ FASTAPI (БЕЗ КОНФЛИКТОВ) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Действия при старте сервера:
    # Очищаем старые зависшие нажатия кнопок перед стартом
    await bot.delete_webhook(drop_pending_updates=True)
    print("[START] Очередь обновлений Telegram очищена.")
    
    # Запускаем фоновую задачу опроса Telegram
    polling_task = asyncio.create_task(dp.start_polling(bot))
    print("[START] Бот успешно запущен в фоне параллельно с FastAPI!")
    
    yield  # Здесь сервер принимает запросы
    
    # Действия при остановке сервера:
    print("[STOP] Останавливаем бота...")
    polling_task.cancel()
    await bot.session.close()

# Инициализируем FastAPI с привязкой жизненного цикла
app = FastAPI(lifespan=lifespan)


# --- РОУТ ДЛЯ КРОН-ПИНГАТОРА (ЧТОБЫ НЕ БЫЛО 404 ОШИБКИ) ---
@app.get("/")
async def root():
    return {"status": "working", "message": "Бот-пульт успешно функционирует!"}


# --- API ДЛЯ АГЕНТА (ПОЛЛИНГ) ---
@app.post("/poll")
async def poll(request: Request):
    payload = await request.json()
    uid = payload.get("user_id")
    
    if not uid:
        return {"commands": []}
        
    if uid not in active_hosts:
        active_hosts[uid] = {"commands": [], "status": "offline", "current_dir": "root", "items": []}
        
    active_hosts[uid]["status"] = payload.get("status", "online")
    active_hosts[uid]["current_dir"] = payload.get("current_dir", "root")
    active_hosts[uid]["items"] = payload.get("items", [])
    
    res_type = payload.get("result_type", "none")
    res_data = payload.get("result_data", "")
    
    if res_type != "none":
        command_results[uid] = {"type": res_type, "data": res_data}
        
    cmds = active_hosts[uid]["commands"]
    active_hosts[uid]["commands"] = []
    
    return {"commands": cmds}

# --- КЛАВИАТУРЫ ТЕЛЕГРАМ ---
def get_main_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="💻 Статус ПК", callback_data="status_pc")
    builder.button(text="📁 Проводник", callback_data="explorer_root")
    builder.button(text="📷 Скриншот", callback_data="make_screenshot")
    builder.button(text="🔌 Питание", callback_data="power_menu")
    builder.adjust(2)
    return builder.as_markup()

def get_explorer_keyboard(uid: int):
    builder = InlineKeyboardBuilder()
    host = active_hosts.get(uid, {"current_dir": "root", "items": []})
    current_dir = host["current_dir"]
    items = host["items"]
    
    if current_dir == "root":
        builder.button(text="Диск C:\\", callback_data="exp_browse:C")
        builder.button(text="Диск D:\\", callback_data="exp_browse:D")
        builder.button(text="Рабочий стол", callback_data="exp_browse:desk")
        builder.adjust(1)
    else:
        session = user_sessions.get(uid, {"path": "root", "page": 0})
        page = session["page"]
        
        for idx, item in enumerate(items):
            icon = "📁 " if item["is_dir"] else "📄 "
            builder.button(text=f"{icon}{item['name']}", callback_data=f"exp_click:{idx}")
            
        builder.adjust(1)
        
        nav_buttons = []
        if page > 0:
            nav_buttons.append(types.InlineKeyboardButton(text="⬅️ Пред.", callback_data=f"exp_page:{page-1}"))
        nav_buttons.append(types.InlineKeyboardButton(text="🔼 Вверх", callback_data="exp_up"))
        if len(items) == 10:
            nav_buttons.append(types.InlineKeyboardButton(text="След. ➡️", callback_data=f"exp_page:{page+1}"))
            
        builder.row(*nav_buttons)
        builder.row(types.InlineKeyboardButton(text="🗑️ Очистить корзину", callback_data="clear_trash"))
        builder.row(types.InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu"))
        
    return builder.as_markup()

# --- ОБРАБОТЧИКИ ТЕЛЕГРАМ КОМАНД ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    uid = message.chat.id
    user_sessions[uid] = {"path": "root", "page": 0, "last_selected_file": ""}
    await message.answer(
        f"🤖 Привет! Это панель управления твоим ПК.\nТвой ID для файла конфигурации: `{uid}`", 
        parse_mode="Markdown", 
        reply_markup=get_main_keyboard()
    )

@dp.callback_query(F.data == "main_menu")
async def main_menu(callback: types.CallbackQuery):
    await callback.message.edit_text("💻 Главное меню управления ПК:", reply_markup=get_main_keyboard())

@dp.callback_query(F.data == "status_pc")
async def status_pc(callback: types.CallbackQuery):
    uid = callback.message.chat.id
    add_command(uid, "status")
    await callback.answer("Запрос отправлен, ожидаем ПК...")
    
    resolved = False
    for _ in range(25):
        await asyncio.sleep(0.2)
        if uid in command_results:
            res = command_results.pop(uid)
            await callback.message.answer(res["data"], parse_mode="Markdown")
            resolved = True
            break
            
    if not resolved:
        await callback.message.answer("⚠️ ПК не ответил вовремя. Убедись, что Агент запущен на компьютере.")

@dp.callback_query(F.data == "explorer_root")
async def explorer_root(callback: types.CallbackQuery):
    uid = callback.message.chat.id
    user_sessions[uid] = {"path": "root", "page": 0, "last_selected_file": ""}
    await callback.message.edit_text("📁 Выберите системный каталог:", reply_markup=get_explorer_keyboard(uid))

@dp.callback_query(F.data.startswith("exp_browse:"))
async def explorer_browse(callback: types.CallbackQuery):
    uid = callback.message.chat.id
    target = callback.data.split("exp_browse:")[1]
    user_sessions[uid]["path"] = target
    user_sessions[uid]["page"] = 0
    add_command(uid, "browse", target)
    await callback.answer("Открываю...")
    await asyncio.sleep(2.0)
    await callback.message.edit_text(f"📁 Путь: `{target}`", reply_markup=get_explorer_keyboard(uid), parse_mode="Markdown")

@dp.callback_query(F.data == "exp_up")
async def explorer_up(callback: types.CallbackQuery):
    uid = callback.message.chat.id
    add_command(uid, "up")
    await callback.answer("Перемещаюсь на уровень вверх...")
    await asyncio.sleep(1.5)
    await callback.message.edit_text("📁 Загрузка папки...", reply_markup=get_explorer_keyboard(uid))

@dp.callback_query(F.data.startswith("exp_page:"))
async def explorer_page(callback: types.CallbackQuery):
    uid = callback.message.chat.id
    page = int(callback.data.split("exp_page:")[1])
    user_sessions[uid]["page"] = page
    add_command(uid, "page", str(page))
    await callback.answer("Перелистываю...")
    await asyncio.sleep(1.5)
    await callback.message.edit_text("📁 Загрузка страницы...", reply_markup=get_explorer_keyboard(uid))

@dp.callback_query(F.data.startswith("exp_click:"))
async def explorer_click(callback: types.CallbackQuery):
    uid = callback.message.chat.id
    idx = callback.data.split("exp_click:")[1]
    add_command(uid, "click", idx)
    await callback.answer("Обработка клика...")
    
    resolved = False
    for _ in range(25):
        await asyncio.sleep(0.2)
        if uid in command_results:
            res = command_results.pop(uid)
            resolved = True
            
            builder = InlineKeyboardBuilder()
            builder.button(text="📥 Скачать этот файл в Telegram", callback_data="send_file_to_me")
            builder.button(text="⬅️ Главное меню", callback_data="main_menu")
            builder.adjust(1)
            
            user_sessions[uid]["last_selected_file"] = res['data']
            await callback.message.answer(
                f"📍 **Выбран файл:**\n\n`{res['data']}`\n\n"
                f"ℹ️ Нажмите кнопку ниже, чтобы Агент упаковал и отправил этот файл в чат.", 
                parse_mode="Markdown", 
                reply_markup=builder.as_markup()
            )
            break
            
    if not resolved:
        await callback.message.edit_text("📁 Обновление папки...", reply_markup=get_explorer_keyboard(uid))

@dp.callback_query(F.data == "send_file_to_me")
async def send_file_callback(callback: types.CallbackQuery):
    uid = callback.message.chat.id
    file_path = user_sessions.get(uid, {}).get("last_selected_file", "")
    
    if file_path:
        add_command(uid, "upload_file", file_path)
        await callback.answer("Запрос отправлен Агенту...")
        await callback.message.answer("⏳ Агент готовит отправку файла. Крупные файлы (до 50МБ) могут загружаться чуть дольше...")
    else:
        await callback.answer("❌ Ошибка: Файл не выбран в текущей сессии", show_alert=True)

@dp.callback_query(F.data == "make_screenshot")
async def make_screenshot(callback: types.CallbackQuery):
    uid = callback.message.chat.id
    add_command(uid, "screenshot")
    await callback.answer("Запрос снимка...")
    
    for _ in range(25):
        await asyncio.sleep(0.2)
        if uid in command_results:
            command_results.pop(uid)
            await callback.message.answer("📸 Снимок экрана успешно сформирован хостом!")
            break

@dp.callback_query(F.data == "power_menu")
async def power_menu(callback: types.CallbackQuery):
    builder = InlineKeyboardBuilder()
    builder.button(text="🛑 Выключение", callback_data="p_cmd:shutdown")
    builder.button(text="🔄 Перезагрузка", callback_data="p_cmd:restart")
    builder.button(text="🌙 Спящий режим", callback_data="p_cmd:sleep")
    builder.button(text="⬅️ Главное меню", callback_data="main_menu")
    builder.adjust(1)
    await callback.message.edit_text("🔌 Выберите команду питания для хоста:", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("p_cmd:"))
async def power_command(callback: types.CallbackQuery):
    uid = callback.message.chat.id
    p_action = callback.data.split("p_cmd:")[1]
    add_command(uid, "power", p_action)
    await callback.answer("Команда питания отправлена!")
    await callback.message.answer(f"✅ Сигнал `{p_action}` успешно отправлен на ПК.")

@dp.callback_query(F.data == "clear_trash")
async def clear_trash(callback: types.CallbackQuery):
    uid = callback.message.chat.id
    add_command(uid, "clear_trash")
    await callback.answer("Корзина очищается...")


# --- СТАНДАРТНЫЙ ЗАПУСК UVICORN С ПОРТОМ RENDER ---
if __name__ == "__main__":
    import uvicorn
    
    port = int(os.environ.get("PORT", 8000))
    # Запускаем uvicorn. Механизм lifespan автоматически запустит внутри себя aiogram
    uvicorn.run(app, host="0.0.0.0", port=port)
