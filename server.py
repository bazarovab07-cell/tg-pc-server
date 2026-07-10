import time
import os
import subprocess
import webbrowser
import pystray
import pyperclip
import psutil
from PIL import Image, ImageDraw
import requests
import threading
import tkinter as tk
from tkinter import messagebox

# Конфигурационные файлы
CONFIG_FILE = "config.txt"
USER_ID = 0
HOST_ID = ""
SERVER_URL = "https://tg-pc-server.onrender.com"

last_clipboard = ""
status_buffer = "online"
report_buffer = "" 

# Инициализируем стартовый путь как корень системного диска (например, C:\)
# Это гарантирует, что ты увидишь файлы самого ПК, а не папку запуска скрипта
START_DIR = os.path.abspath(os.environ.get('SystemDrive', 'C:\\'))
os.chdir(START_DIR)

SMART_APPS = {
    "steam": [os.path.expandvars(r"%ProgramFiles(x86)%\Steam\steam.exe"), "steam://"],
    "discord": [os.path.expandvars(r"%LocalAppData%\Discord\Update.exe"), "discord://"],
    "browser": ["chrome", "msedge"]
}

# --- ЛОГИКА ТГ-ID КОНФИГУРАЦИИ ---
def load_config():
    global USER_ID, HOST_ID
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                uid = int(f.read().strip())
                if uid > 0:
                    USER_ID = uid
                    HOST_ID = f"host_{USER_ID}"
                    return True
        except: pass
    return False

def save_config(uid_str):
    global USER_ID, HOST_ID
    try:
        uid = int(uid_str.strip())
        with open(CONFIG_FILE, "w") as f:
            f.write(str(uid))
        USER_ID = uid
        HOST_ID = f"host_{USER_ID}"
        return True
    except:
        return False

# --- СИСТЕМНЫЙ ТРЕЙ ---
icon_tray = None

def create_tray_icon():
    global icon_tray
    image = Image.new('RGB', (64, 64), color=(0, 0, 0))
    dc = ImageDraw.Draw(image)
    dc.rectangle([16, 16, 48, 48], fill=(0, 128, 255))
    
    menu = pystray.Menu(
        pystray.MenuItem("Открыть окно", lambda: show_window()),
        pystray.MenuItem("Выход", lambda: exit_agent())
    )
    icon_tray = pystray.Icon("PC-Agent", image, "PC Agent", menu=menu)
    icon_tray.run()

def exit_agent():
    if icon_tray:
        icon_tray.stop()
    os._exit(0)

# --- GUI ОКНО (TKINTER) ---
root = None
id_entry = None
status_label = None

def setup_gui():
    global root, id_entry, status_label
    root = tk.Tk()
    root.title("PC Remote Agent Status")
    root.geometry("400x250")
    root.resizable(False, False)
    
    root.protocol("WM_DELETE_WINDOW", hide_window)
    
    title_label = tk.Label(root, text="PC Remote Control Agent", font=("Arial", 14, "bold"))
    title_label.pack(pady=10)
    
    status_label = tk.Label(root, text="Проверка конфигурации...", font=("Arial", 10), fg="orange")
    status_label.pack(pady=5)

    if not load_config():
        status_label.config(text="⚠️ Требуется первоначальная настройка ID!", fg="red")
        
        lbl = tk.Label(root, text="Введите ваш Telegram ID:")
        lbl.pack(pady=5)
        
        id_entry = tk.Entry(root, font=("Arial", 12), justify="center", width=20)
        id_entry.pack(pady=5)
        
        btn = tk.Button(root, text="Сохранить и Запустить", command=on_save_clicked, bg="#0080FF", fg="white", font=("Arial", 10, "bold"))
        btn.pack(pady=10)
    else:
        show_active_status()
        
    btn_hide = tk.Button(root, text="Свернуть в трей", command=hide_window)
    btn_hide.pack(side="bottom", pady=15)
    
    root.mainloop()

def on_save_clicked():
    uid_input = id_entry.get()
    if save_config(uid_input):
        messagebox.showinfo("Успех", "Telegram ID успешно сохранен! Агент запущен.")
        for widget in root.winfo_children():
            if isinstance(widget, (tk.Entry, tk.Button, tk.Label)) and widget != status_label and widget.cget("text") != "Свернуть в трей":
                if "PC Remote" not in str(widget.cget("text")):
                    widget.destroy()
        
        show_active_status()
        threading.Thread(target=agent_loop, daemon=True).start()
    else:
        messagebox.showerror("Ошибка", "Неверный формат Telegram ID. Введите только цифры!")

def show_active_status():
    status_label.config(text=f"✅ Агент запущен и работает\nТекущий хост: host_{USER_ID}", fg="green", font=("Arial", 11, "bold"))

def hide_window():
    root.withdraw() 

def show_window():
    if root:
        root.deiconify() 

# --- СИСТЕМНЫЕ ФУНКЦИИ И ЦИКЛ ---
def get_processes_list():
    procs = []
    seen_names = set()
    for proc in psutil.process_iter(['name', 'memory_info']):
        try:
            name = proc.info['name']
            if name not in seen_names and name.lower() != "system":
                procs.append((name, proc.info['memory_info'].rss))
                seen_names.add(name)
        except: pass
    procs = sorted(procs, key=lambda x: x[1], reverse=True)[:10]
    return "\n".join([f"{name}|{round(mem / 1024 / 1024, 1)} MB" for name, mem in procs])

def get_directory_items():
    items = []
    try:
        # Получаем абсолютный текущий путь на ПК пользователя
        current_path = os.getcwd()
        for entry in os.scandir(current_path):
            try:
                # Пропускаем скрытые и системные файлы, чтобы не захламлять ТГ
                if not entry.name.startswith('$') and not entry.name.startswith('.'):
                    items.append({"name": entry.name, "is_dir": entry.is_dir()})
            except: pass
    except Exception as e:
        items.append({"name": f"Ошибка доступа: {str(e)}", "is_dir": False})
    
    # Сортируем: сначала папки, потом файлы
    return sorted(items, key=lambda x: not x['is_dir'])[:15]

def execute_command(cmd_dict):
    global status_buffer, report_buffer
    action = cmd_dict.get("action")
    data = cmd_dict.get("data", "")
    
    try:
        if action == "stream":
            import mss
            with mss.mss() as sct:
                sct.shot(output="stream.jpg")
            if os.path.exists("stream.jpg"):
                with open("stream.jpg", "rb") as f:
                    requests.post(f"{SERVER_URL}/upload_screenshot?host_id={HOST_ID}&user_id={USER_ID}", data=f.read(), timeout=15)
                os.remove("stream.jpg")
                report_buffer = "SUCCESS:Скриншот успешно отправлен на телефон."
            else:
                report_buffer = "ERROR:Не удалось создать файл скриншота."
                
        elif action == "list_dir_or_proc":
            if data == "processes":
                status_buffer = f"PROCESSES_LIST:\n{get_processes_list()}"
                report_buffer = "SUCCESS:Список процессов успешно загружен."
            elif data == "dir":
                status_buffer = "online"
                report_buffer = "SUCCESS:Список файлов обновлен."

        elif action == "kill_smart":
            target = data.lower()
            killed = False
            for proc in psutil.process_iter(['name']):
                try:
                    if proc.info['name'].lower() == target:
                        proc.kill()
                        killed = True
                except: pass
            if killed: report_buffer = f"SUCCESS:Процесс `{data}` жестко завершен кнопкой."
            else: report_buffer = f"ERROR:Не удалось закрыть `{data}`, возможно он уже закрыт."
            
        elif action == "run_app":
            opened = False
            if data in SMART_APPS:
                paths = SMART_APPS[data]
                for p in paths:
                    try:
                        if "://" in p or p in ["chrome", "msedge"]: 
                            webbrowser.open(p)
                            opened = True
                        elif os.path.exists(p): 
                            subprocess.Popen(p)
                            opened = True
                        break
                    except: pass
            else:
                try: 
                    webbrowser.open(data)
                    opened = True
                except: pass
            
            if opened: report_buffer = f"SUCCESS:Приложение `{data}` запущено."
            else: report_buffer = f"ERROR:Не удалось запустить `{data}`."

        elif action == "cd_into":
            # Вычисляем полный путь для перехода на ПК
            target_path = os.path.abspath(os.path.join(os.getcwd(), data))
            if os.path.isdir(target_path): 
                os.chdir(target_path)
                report_buffer = f"SUCCESS:Переход в папку `{data}`"
            else: 
                report_buffer = f"ERROR:`{data}` не является папкой или к ней нет доступа."
                
        elif action == "cd_back":
            current_path = os.getcwd()
            parent_path = os.path.abspath(os.path.join(current_path, ".."))
            
            if current_path == parent_path:
                report_buffer = "ERROR:Вы уже в корневом каталоге диска."
            else:
                os.chdir(parent_path)
                report_buffer = f"SUCCESS:Вы перешли выше. Текущий путь: {parent_path}"
                
        elif action == "power":
            if data == "shutdown":
                report_buffer = "SUCCESS:Компьютер выключается..."
                os.system("shutdown /s /t 5")
            elif data == "restart":
                report_buffer = "SUCCESS:Компьютер перезагружается..."
                os.system("shutdown /r /t 5")
                
        elif action == "update_clipboard":
            pyperclip.copy(data)
            report_buffer = "SUCCESS:Текст помещен в буфер обмена ПК."
            
    except Exception as e:
        report_buffer = f"ERROR:Сбой при выполнении {action}: {str(e)}"

def agent_loop():
    global last_clipboard, status_buffer, report_buffer
    while USER_ID == 0:
        time.sleep(1)
        
    while True:
        try:
            try: current_clip = pyperclip.paste()
            except: current_clip = ""
                
            send_status = status_buffer
            if current_clip and current_clip != last_clipboard:
                send_status = f"CLIPBOARD_FROM_PC:{current_clip}"
                last_clipboard = current_clip
            
            if status_buffer.startswith("PROCESSES_LIST:"):
                status_buffer = "online"

            payload = {
                "host_id": HOST_ID, "user_id": str(USER_ID),
                "status": send_status, "current_dir": os.getcwd(),
                "items": get_directory_items(),
                "report": report_buffer
            }
            report_buffer = ""

            response = requests.post(f"{SERVER_URL}/poll", json=payload, timeout=10)
            if response.status_code == 200:
                for cmd in response.json().get("commands", []):
                    execute_command(cmd)
        except: pass
        time.sleep(2)

if __name__ == "__main__":
    if load_config():
        threading.Thread(target=agent_loop, daemon=True).start()
        
    threading.Thread(target=create_tray_icon, daemon=True).start()
    setup_gui()
