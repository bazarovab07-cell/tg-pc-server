import time
import os
import sys
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

# === КОНСТАНТЫ ОБНОВЛЕНИЯ ===
CURRENT_VERSION = 1  # При сборке нового .exe увеличивай эту цифру на 1
# ============================

CONFIG_FILE = "config.txt"
USER_ID = 0
HOST_ID = ""
SERVER_URL = "https://tg-pc-server.onrender.com"

last_clipboard = ""
status_buffer = "online"
report_buffer = "" 

START_DIR = os.path.abspath(os.environ.get('SystemDrive', 'C:\\'))
os.chdir(START_DIR)

SMART_APPS = {
    "steam": [os.path.expandvars(r"%ProgramFiles(x86)%\Steam\steam.exe"), "steam://"],
    "discord": [os.path.expandvars(r"%LocalAppData%\Discord\Update.exe"), "discord://"],
    "browser": ["chrome", "msedge"]
}

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
        except: 
            pass
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

root = None
id_entry = None
status_label = None

def setup_gui():
    global root, id_entry, status_label
    root = tk.Tk()
    root.title("PC Remote Agent")
    root.geometry("450x280")
    root.resizable(False, False)
    
    # Тёмная палитра
    BG_DARK = "#121212"
    BG_CARD = "#1E1E1E"
    TEXT_LIGHT = "#E0E0E0"
    TEXT_MUTED = "#A0A0A0"
    ACCENT_BLUE = "#007ACC"
    
    root.configure(bg=BG_DARK)
    root.protocol("WM_DELETE_WINDOW", hide_window)
    
    title_frame = tk.Frame(root, bg=BG_DARK)
    title_frame.pack(pady=15)
    
    title_label = tk.Label(
        title_frame, 
        text="PC REMOTE AGENT", 
        font=("Segoe UI", 16, "bold"), 
        fg=ACCENT_BLUE, 
        bg=BG_DARK
    )
    title_label.pack()
    
    subtitle = tk.Label(
        title_frame, 
        text="Удалённое управление компьютером", 
        font=("Segoe UI", 9), 
        fg=TEXT_MUTED, 
        bg=BG_DARK
    )
    subtitle.pack(pady=2)
    
    status_card = tk.Frame(root, bg=BG_CARD, bd=0, highlightbackground="#2D2D2D", highlightthickness=1)
    status_card.pack(fill="x", padx=30, pady=10, ipady=8)
    
    status_label = tk.Label(
        status_card, 
        text="Проверка конфигурации...", 
        font=("Segoe UI", 10), 
        fg="orange", 
        bg=BG_CARD,
        justify="center"
    )
    status_label.pack(expand=True, fill="both")

    if not load_config():
        status_label.config(text="⚠️ Требуется первоначальная настройка ID!", fg="#FF3333")
        
        setup_frame = tk.Frame(root, bg=BG_DARK)
        setup_frame.pack(pady=5)
        
        id_entry = tk.Entry(
            setup_frame, 
            font=("Segoe UI", 12), 
            justify="center", 
            width=18, 
            bg="#2D2D2D", 
            fg=TEXT_LIGHT, 
            insertbackground=TEXT_LIGHT,
            bd=0,
            highlightthickness=1,
            highlightbackground="#444444",
            highlightcolor=ACCENT_BLUE
        )
        id_entry.pack(side="left", padx=5, ipady=3)
        id_entry.insert(0, "Твой Telegram ID")
        
        id_entry.bind("<FocusIn>", lambda args: id_entry.delete('0', 'end') if id_entry.get() == "Твой Telegram ID" else None)
        
        btn = tk.Button(
            setup_frame, 
            text="Запустить", 
            command=on_save_clicked, 
            bg=ACCENT_BLUE, 
            fg="white", 
            activebackground="#005A9E",
            activeforeground="white",
            font=("Segoe UI", 10, "bold"),
            bd=0,
            padx=15,
            pady=3,
            cursor="hand2"
        )
        btn.pack(side="left", padx=5)
    else:
        show_active_status()
        
    btn_hide = tk.Button(
        root, 
        text="Свернуть в трей", 
        command=hide_window,
        bg="#252526",
        fg=TEXT_MUTED,
        activebackground="#2D2D30",
        activeforeground=TEXT_LIGHT,
        font=("Segoe UI", 9, "bold"),
        bd=0,
        pady=6,
        cursor="hand2"
    )
    btn_hide.pack(side="bottom", fill="x", padx=30, pady=15)
    
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
    status_label.config(
        text=f"🟢 Агент запущен и готов к работе\nID устройства: host_{USER_ID}", 
        fg="#4CAF50", 
        font=("Segoe UI", 11, "bold")
    )

def hide_window(): 
    if root:
        root.withdraw() 

def show_window(): 
    if root: 
        root.deiconify() 

def get_processes_list():
    procs = []
    seen_names = set()
    for proc in psutil.process_iter(['name', 'memory_info']):
        try:
            name = proc.info['name']
            if name not in seen_names and name.lower() != "system":
                procs.append((name, proc.info['memory_info'].rss))
                seen_names.add(name)
        except: 
            pass
    procs = sorted(procs, key=lambda x: x[1], reverse=True)[:10]
    return "\n".join([f"{name}|{round(mem / 1024 / 1024, 1)} MB" for name, mem in procs])

def get_directory_items():
    items = []
    try:
        current_path = os.getcwd()
        for entry in os.scandir(current_path):
            try:
                if not entry.name.startswith('$') and not entry.name.startswith('.'):
                    items.append({"name": entry.name, "is_dir": entry.is_dir()})
            except: 
                pass
    except Exception as e:
        items.append({"name": f"Ошибка доступа: {str(e)}", "is_dir": False})
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
                report_buffer = "SUCCESS:Скриншот успешно отправлен."
            else: 
                report_buffer = "ERROR:Не удалось создать файл скриншота."
                
        elif action == "list_dir_or_proc":
            if data == "processes":
                status_buffer = f"PROCESSES_LIST:\n{get_processes_list()}"
                report_buffer = "SUCCESS:Список процессов успешно загружен."
            elif data == "dir":
                status_buffer = "online"
                report_buffer = "SUCCESS:Список файлов обновлен."

        elif action == "get_clipboard_forced":
            try: 
                clip_text = pyperclip.paste()
            except: 
                clip_text = ""
            status_buffer = f"CLIPBOARD_FROM_PC:{clip_text}"
            report_buffer = "SUCCESS:Текст из буфера успешно прочитан."

        elif action == "kill_smart":
            target = data.lower()
            killed = False
            for proc in psutil.process_iter(['name']):
                try:
                    if proc.info['name'].lower() == target:
                        proc.kill()
                        killed = True
                except: 
                    pass
            if killed: 
                report_buffer = f"SUCCESS:Процесс {data} жестко завершен."
            else: 
                report_buffer = f"ERROR:Не удалось закрыть {data}."
            
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
                    except: 
                        pass
            else:
                try: 
                    webbrowser.open(data)
                    opened = True
                except: 
                    pass
            
            if opened: 
                report_buffer = f"SUCCESS:Приложение {data} запущено."
            else: 
                report_buffer = f"ERROR:Не удалось запустить {data}."

        elif action == "cd_into":
            target_path = os.path.abspath(os.path.join(os.getcwd(), data))
            if os.path.isdir(target_path): 
                os.chdir(target_path)
                report_buffer = f"SUCCESS:Переход в папку `{data}`"
            else: 
                report_buffer = f"ERROR:`{data}` не является папкой."
                
        elif action == "cd_back":
            current_path = os.getcwd()
            parent_path = os.path.abspath(os.path.join(current_path, ".."))
            if current_path == parent_path: 
                report_buffer = "ERROR:Вы в корне диска."
            else:
                os.chdir(parent_path)
                report_buffer = f"SUCCESS:Переход выше выполнен."
                
        elif action == "power":
            if data == "shutdown":
                report_buffer = "SUCCESS:Компьютер выключается..."
                os.system("shutdown /s /t 5")
            elif data == "restart":
                report_buffer = "SUCCESS:Компьютер перезагружается..."
                os.system("shutdown /r /t 5")
                
    except Exception as e:
        report_buffer = f"ERROR:Сбой при выполнении: {str(e)}"

# === ФУНКЦИЯ СКАЧИВАНИЯ И НАКАТЫВАНИЯ ОБНОВЛЕНИЯ ===
def perform_update():
    try:
        update_url = f"{SERVER_URL}/get_update"
        response = requests.get(update_url, stream=True, timeout=60)
        
        if response.status_code == 200:
            temp_exe = "update_temp.exe"
            with open(temp_exe, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            
            current_exe = os.path.abspath(sys.executable)
            
            # Скрипт замены занятого .exe
            bat_content = f"""@echo off
timeout /t 2 /nobreak > nul
del "{current_exe}"
rename "{os.path.abspath(temp_exe)}" "{os.path.basename(current_exe)}"
start "" "{current_exe}"
del "%~f0"
"""
            with open("updater.bat", "w", encoding="cp866") as bat_file:
                bat_file.write(bat_content)
                
            subprocess.Popen("updater.bat", shell=True)
            exit_agent()
    except Exception as e:
        print(f"Ошибка автообновления: {e}")

def agent_loop():
    global last_clipboard, status_buffer, report_buffer
    while USER_ID == 0: 
        time.sleep(1)
        
    while True:
        try:
            try: 
                current_clip = pyperclip.paste()
            except: 
                current_clip = ""
                
            send_status = status_buffer
            if current_clip and current_clip != last_clipboard and not status_buffer.startswith("CLIPBOARD_FROM_PC:"):
                last_clipboard = current_clip
            
            if status_buffer.startswith("PROCESSES_LIST:") or status_buffer.startswith("CLIPBOARD_FROM_PC:"):
                status_buffer = "online"

            payload = {
                "host_id": HOST_ID, 
                "user_id": str(USER_ID),
                "status": send_status, 
                "current_dir": os.getcwd(),
                "items": get_directory_items(),
                "report": report_buffer
            }
            report_buffer = ""

            response = requests.post(f"{SERVER_URL}/poll", json=payload, timeout=10)
            if response.status_code == 200:
                data = response.json()
                
                # Проверяем, есть ли на сервере более свежая версия
                server_version = data.get("update_version", 0)
                if server_version > CURRENT_VERSION:
                    threading.Thread(target=perform_update, daemon=True).start()
                    continue

                for cmd in data.get("commands", []):
                    execute_command(cmd)
        except: 
            pass
        time.sleep(2)

if __name__ == "__main__":
    if load_config(): 
        threading.Thread(target=agent_loop, daemon=True).start()
    threading.Thread(target=create_tray_icon, daemon=True).start()
    setup_gui()
