import asyncio
from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ТОКЕН ТВОЕГО БОТА
TOKEN = "8889355917:AAFYsn7EZdO9ljP4vNbJR09m4Tbom_oG1_A"

bot = Bot(token=TOKEN)
dp = Dispatcher()
app = FastAPI()

# База данных в оперативной памяти сервера
user_sessions = {}
command_queues = {}
command_results = {}

class AgentReport(BaseModel):
    user_id: int
    status: str
    current_dir: str = "root"
    items: list = []
    result_type: str = "none"
    result_data: str = ""

# --- HTTP API ДЛЯ СВЯЗИ С АГЕНТОМ (.EXE) ---
@app.post("/poll")
async def poll_commands(report: AgentReport):
    user_id = report.user_id
    
    if user_id not in user_sessions:
        user_sessions[user_id] = {"path": "root", "page": 0}
    
    user_sessions[user_id]["status"] = "online"
    user_sessions[user_id]["items"] = report.items
    if report.current_dir != "stay":
        user_sessions[user_id]["path"] = report.current_dir

    if report.result_type != "none":
        command_results[user_id] = {"type": report.result_type, "data": report.result_data}

    queue = command_queues.get(user_id, [])
    command_queues[user_id] = [] 
    return {"commands": queue}


# --- ЛОГИКА ТЕЛЕГРАМ БОТА ---

def get_help_text():
    return (
        "❓ **Справка по мультихост-платформе управления ПК**\n\n"
        "🔒 **Доступ:** Бот автоматически привязывается к вашему Telegram ID. Управление идет только вашим ПК.\n\n"
        "📁 **Проводник:**\n"
        "• Кнопка *Проводник* открывает диски C, D или Рабочий стол вашего удаленного ПК.\n"
        "• Кнопки *Назад* и *Вперед* листают списки папок постранично.\n"
        "• Клик по файлу генерирует сообщение с его полным путем.\n\n"
        "🚀 **Запуск и Закрытие:**\n"
        "• Отправьте полный путь к файлу/ярлыку обычным текстом, чтобы открыть его на вашем ПК.\n"
        "• Напишите `/close имя_программы` (например, `/close notepad`), чтобы принудительно убить процесс на вашем ПК.\n\n"
        "📷 **Дополнительно:**\n"
        "• Кнопки скриншота, статуса системы и очистки корзины работают индивидуально для каждого онлайн-ПК."
    )

def get_main_menu():
    builder = InlineKeyboardBuilder()
    builder.button(text="🔋 Питание", callback_data="menu_power")
    builder.button(text="📊 Статус ПК", callback_data="status_pc")
    builder.button(text="📁 Проводник", callback_data="open_explorer")
    builder.button(text="📷 Сделать скриншот", callback_data="make_screenshot")
    builder.button(text="🗑️ Очистить Корзину", callback_data="clear_trash")
    builder.button(text="❓ Помощь", callback_data="help_menu")
    builder.adjust(2, 1, 1, 1, 1)
    return builder.as_markup()

def get_power_menu():
    builder = InlineKeyboardBuilder()
    builder.button(text="🛑 Выключить ПК", callback_data="power_shutdown")
    builder.button(text="🔄 Перезагрузить", callback_data="power_restart")
    builder.button(text="🌙 Режим сна", callback_data="power_sleep")
    builder.button(text="⬅️ Назад", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()

def get_explorer_keyboard(chat_id: int):
    builder = InlineKeyboardBuilder()
    session = user_sessions.get(chat_id, {"path": "root", "page": 0, "items": []})
    current_path = session["path"]
    page = session["page"]
    items_on_page = session.get("items", [])
    
    if current_path == "root":
        builder.button(text="CD: Диск C:\\", callback_data="exp_root_C")
        builder.button(text="CD: Диск D:\\", callback_data="exp_root_D")
        builder.button(text="🖥️ Рабочий стол", callback_data="exp_root_desk")
        builder.button(text="⬅️ Главное меню", callback_data="main_menu")
        builder.adjust(2, 1, 1)
        return builder.as_markup()

    builder.button(text="⬅️ Назад (Вверх)", callback_data="exp_up")
    
    for idx, item in enumerate(items_on_page):
        icon = "📁" if item["is_dir"] else "📄"
        builder.button(text=f"{icon} {item['name']}", callback_data=f"exp_click:{idx}")
        
    builder.adjust(1)
    
    nav_buttons = []
    if page > 0:
        nav_buttons.append(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="exp_page_prev"))
    if len(items_on_page) == 10:
        nav_buttons.append(types.InlineKeyboardButton(text="Вперед ➡️", callback_data="exp_page_next"))
        
    if nav_buttons:
        builder.row(*nav_buttons)
        
    return builder.as_markup()

def add_command(user_id: int, action: str, data: str = ""):
    if user_id not in command_queues:
        command_queues[user_id] = []
    command_queues[user_id].append({"action": action, "data": data})

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    uid = message.chat.id
    user_sessions[uid] = {"path": "root", "page": 0, "status": "offline", "items": []}
    await message.answer(f"👋 Добро пожаловать!\nВаш уникальный ID для настройки Агента на ПК: `{uid}`\n\nВведите этот ID в файл config.txt рядом с программой.", parse_mode="Markdown", reply_markup=get_main_menu())

@dp.message(Command("help"))
@dp.callback_query(F.data == "help_menu")
async def show_help(event: types.Message or types.CallbackQuery):
    text = get_help_text()
    builder = InlineKeyboardBuilder().button(text="⬅️ Главное меню", callback_data="main_menu")
    if isinstance(event, types.Message):
        await event.answer(text, reply_markup=builder.as_markup(), parse_mode="Markdown")
    else:
        await event.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")
        await event.answer()

@dp.callback_query(F.data == "main_menu")
async def back_to_menu(callback: types.CallbackQuery):
    await callback.message.edit_text("Выберите действие:", reply_markup=get_main_menu())
    await callback.answer()

@dp.callback_query(F.data == "open_explorer")
async def open_explorer(callback: types.CallbackQuery):
    uid = callback.message.chat.id
    user_sessions[uid] = {"path": "root", "page": 0, "items": []}
    await callback.message.edit_text("📂 Выберите раздел проводника:", reply_markup=get_explorer_keyboard(uid))
    await callback.answer()

@dp.callback_query(F.data.startswith("exp_root_"))
async def root_clicks(callback: types.CallbackQuery):
    uid = callback.message.chat.id
    choice = callback.data.split("exp_root_")[1]
    add_command(uid, "browse", choice)
    await callback.answer("Запрос отправлен на ПК...")
    await asyncio.sleep(1.5)
    await callback.message.edit_text(f"📁 Путь: `{user_sessions[uid]['path']}`", reply_markup=get_explorer_keyboard(uid), parse_mode="Markdown")

@dp.callback_query(F.data == "exp_up")
async def explorer_up(callback: types.CallbackQuery):
    uid = callback.message.chat.id
    add_command(uid, "up")
    await callback.answer("Поднимаемся выше...")
    await asyncio.sleep(1.5)
    await callback.message.edit_text(f"📁 Путь: `{user_sessions[uid]['path']}`", reply_markup=get_explorer_keyboard(uid), parse_mode="Markdown")

@dp.callback_query(F.data.startswith("exp_page_"))
async def explorer_pages(callback: types.CallbackQuery):
    uid = callback.message.chat.id
    direction = callback.data.split("exp_page_")[1]
    if direction == "next": user_sessions[uid]["page"] += 1
    else: user_sessions[uid]["page"] = max(0, user_sessions[uid]["page"] - 1)
    
    add_command(uid, "page", str(user_sessions[uid]["page"]))
    await callback.answer("Листаем...")
    await asyncio.sleep(1.5)
    await callback.message.edit_text(f"📁 Путь: `{user_sessions[uid]['path']}`", reply_markup=get_explorer_keyboard(uid), parse_mode="Markdown")

@dp.callback_query(F.data.startswith("exp_click:"))
async def explorer_click(callback: types.CallbackQuery):
    uid = callback.message.chat.id
    idx = callback.data.split("exp_click:")[1]
    add_command(uid, "click", idx)
    await callback.answer("Обработка клика...")
    await asyncio.sleep(1.5)
    
    if uid in command_results:
        res = command_results.pop(uid)
        await callback.message.answer(f"📍 **Путь готов к копированию:**\n\n`{res['data']}`\n\nℹ️ Отправьте его сообщением боту для запуска.", parse_mode="Markdown")
    else:
        await callback.message.edit_text(f"📁 Путь: `{user_sessions[uid]['path']}`", reply_markup=get_explorer_keyboard(uid), parse_mode="Markdown")

@dp.message()
async def text_commands(message: types.Message):
    uid = message.chat.id
    text = message.text.strip()
    
    if text.lower().startswith("/close "):
        proc = text[7:].strip()
        add_command(uid, "kill", proc)
        await message.answer(f"⏳ Отправлена команда на закрытие процесса `{proc}`")
    elif text.startswith("http"):
        add_command(uid, "download", text)
        await message.answer("📥 Ссылка передана вашему ПК на скачивание.")
    else:
        add_command(uid, "run", text)
        await message.answer(f"🚀 Передана команда на запуск: `{text}`", parse_mode="Markdown")

@dp.callback_query(F.data == "menu_power")
async def menu_power(callback: types.CallbackQuery):
    await callback.message.edit_text("Питание компьютера пользователя:", reply_markup=get_power_menu())

@dp.callback_query(F.data.startswith("power_"))
async def handle_power(callback: types.CallbackQuery):
    add_command(callback.message.chat.id, "power", callback.data.split("_")[1])
    await callback.answer("Команда изменения питания отправлена.")

@dp.callback_query(F.data == "clear_trash")
async def clear_trash(callback: types.CallbackQuery):
    add_command(callback.message.chat.id, "clear_trash")
    await callback.answer("Запрос на очистку корзины отправлен.")

@dp.callback_query(F.data == "status_pc")
async def status_pc(callback: types.CallbackQuery):
    uid = callback.message.chat.id
    add_command(uid, "status")
    await callback.answer("Запрос статуса железа...")
    await asyncio.sleep(1.5)
    if uid in command_results:
        res = command_results.pop(uid)
        await callback.message.answer(res["data"], parse_mode="Markdown")

@dp.callback_query(F.data == "make_screenshot")
async def make_screenshot(callback: types.CallbackQuery):
    uid = callback.message.chat.id
    add_command(uid, "screenshot")
    await callback.answer("Запрос скриншота...")

async def run_bot():
    await dp.start_polling(bot)

async def main():
    import os
    port = int(os.environ.get("PORT", 8000))

    # Запускаем бота фоновой задачей
    asyncio.create_task(run_bot())

    # Настраиваем и запускаем веб-сервер uvicorn
    config = uvicorn.Config(app, host="0.0.0.0", port=port, loop="asyncio")
    server = uvicorn.Server(config)
    await server.serve()

if __name__ == "__main__":
    asyncio.run(main())
