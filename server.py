import sys
import time
import os
import platform
import subprocess
import threading
import requests
import tkinter as tk
from tkinter import messagebox, ttk
from PIL import Image, ImageDraw
import pystray
from pystray import MenuItem as item

# === КОНФИГУРАЦИЯ СЕРВЕРА ===
SERVER_URL = "https://tg-pc-server.onrender.com"  # <--- ТВОЙ URL НА RENDER
HOST_ID = platform.node()
ID_FILE = "user_id.txt"

# Переменные для работы агента в фоне
current_dir = ""
is_running = False
user_id = ""

# Тематические цвета
THEMES = {
    "Тёмная": {
        "bg": "#1e1e1e", "fg": "#ffffff", "entry_bg": "#2d2d2d", 
        "btn_bg": "#007acc", "btn_fg": "#ffffff", "status_on": "#4caf50", "status_off": "#f44336"
    },
    "Светлая": {
        "bg": "#f5f5f5", "fg": "#333333", "entry_bg": "#ffffff", 
        "btn_bg": "#2196f3", "btn_fg": "#ffffff", "status_on": "#4caf50", "status_off": "#f44336"
    }
}
current_theme = "Тёмная"

def load_user_id():
    if os.path.exists(ID_FILE):
        with open(ID_FILE, "r") as f:
            return f.read().strip()
    return ""

def save_user_id(uid):
    with open(ID_FILE, "w") as f:
        f.write(uid.strip())

# === ОБНОВЛЕННАЯ ФУНКЦИЯ ДИСПЕТЧЕРА ЗАДАЧ (ФИЛЬТРАЦИЯ СИСТЕМНОГО МУСОРА) ===
def get_running_tasks():
    tasks = []
    # Список ключевых слов системных процессов Windows, которые мы гарантированно скрываем
    SYSTEM_IGNORE = {
        "svchost.exe", "conhost.exe", "taskhostw.exe", "explorer.exe", "runtimebroker.exe",
        "smartscreen.exe", "searchhost.exe", "shellexperiencehost.exe", "startmenuexperiencehost.exe",
        "lsass.exe", "services.exe", "wininit.exe", "csrss.exe", "smss.exe", "winlogon.exe",
        "spoolsv.exe", "sihost.exe", "fontdrvhost.exe", "dwm.exe", "ctfmon.exe", "tasklist.exe",
        "dllhost.exe", "audiodg.exe", "wlanext.exe", "conhost.exe", "nvvsvc.exe", "nvxdsync.exe",
        "wmiinv.exe", "wmiprvse.exe", "System", "Idle", "Registry", "Memory Compression"
    }
    
    try:
        if platform.system() == "Windows":
            output = subprocess.check_output('tasklist /NH /FO CSV', shell=True).decode('cp1251', errors='ignore')
            seen = set()
            for line in output.split('\n'):
                if line.strip():
                    parts = line.split(',')
                    name = parts[0].strip('"')
                    name_lower = name.lower()
                    
                    # Проверяем, чтобы процесс был .exe, не был в списке игнорируемых и не содержал служебных слов Windows
                    if (name.endswith('.exe') and 
                        name not in seen and 
                        name not in SYSTEM_IGNORE and 
                        not name_lower.startswith("microsoft") and 
                        not name_lower.startswith("win")):
                        
                        seen.add(name)
                        tasks.append({"name": name})
        else:
            # Для Linux/Mac базовый вывод пользовательских процессов
            output = subprocess.check_output("ps -eo comm=", shell=True).decode('utf-8', errors='ignore')
            seen = set()
            for name in output.split('\n'):
                name = name.strip()
                if name and name not in seen and not name.startswith("[") and name != "ps":
                    seen.add(name)
                    tasks.append({"name": name})
    except Exception as e:
        print(f"Ошибка сбора задач: {e}")
    return tasks

def execute_command(cmd):
    global current_dir, user_id
    action = cmd.get("action")
    data = cmd.get("data")
    
    report = ""
    event_type = None
    items = []

    try:
        if action == "stream":
            import pyautogui
            screenshot_path = "screen.jpg"
            pyautogui.screenshot(screenshot_path)
            with open(screenshot_path, "rb") as f:
                requests.post(f"{SERVER_URL}/upload_screenshot?host_id={HOST_ID}&user_id={user_id}", data=f.read())
            os.remove(screenshot_path)
            return

        elif action == "get_tasks":
            items = get_running_tasks()
            event_type = "tasks_ready"

        elif action == "kill_task":
            if platform.system() == "Windows":
                os.system(f"taskkill /F /IM {data} /T")
            else:
                os.system(f"pkill -f {data}")
            report = f"Процесс {data} успешно закрыт!"

        elif action == "get_drives":
            current_dir = ""
            if platform.system() == "Windows":
                import string
                from ctypes import windll
                drives = []
                bitmask = windll.kernel32.GetLogicalDrives()
                for letter in string.ascii_uppercase:
                    if bitmask & 1:
                        drives.append({"name": f"{letter}:\\", "is_dir": True})
                    bitmask >>= 1
                items = drives
            else:
                items = [{"name": "/", "is_dir": True}]
            event_type = "explorer_ready"

        elif action == "set_drive" or action == "cd_into":
            current_dir = data if action == "set_drive" else os.path.join(current_dir, data)
            items = []
            if os.path.exists(current_dir):
                for file in os.listdir(current_dir):
                    f_path = os.path.join(current_dir, file)
                    items.append({"name": file, "is_dir": os.path.isdir(f_path)})
            event_type = "explorer_ready"

        elif action == "cd_back":
            current_dir = os.path.dirname(current_dir.rstrip(os.sep))
            if not current_dir or current_dir == os.path.dirname(current_dir):
                current_dir = ""
                execute_command({"action": "get_drives", "data": ""})
                return
            items = []
            for file in os.listdir(current_dir):
                f_path = os.path.join(current_dir, file)
                items.append({"name": file, "is_dir": os.path.isdir(f_path)})
            event_type = "explorer_ready"

        elif action == "run_app":
            if data == "steam":
                os.startfile("steam://") if platform.system() == "Windows" else subprocess.Popen(["steam"])
            elif data == "discord":
                subprocess.Popen(["discord"])
            elif data == "browser":
                import webbrowser
                webbrowser.open("https://google.com")
            report = f"Команда на запуск {data} выполнена."

        elif action == "power":
            if data == "restart":
                os.system("shutdown /r /t 1" if platform.system() == "Windows" else "reboot")
            elif data == "shutdown":
                os.system("shutdown /s /t 1" if platform.system() == "Windows" else "poweroff")
            report = f"Команда питания ({data}) отправлена на ОС."

    except Exception as e:
        report = f"Ошибка выполнения команды {action}: {e}"

    payload = {"host_id": HOST_ID, "user_id": user_id, "current_dir": current_dir, "report": report}
    if event_type:
        payload["event_type"] = event_type
        payload["items"] = items
        
    try: requests.post(f"{SERVER_URL}/poll", json=payload)
    except: pass

def network_loop():
    global is_running, user_id
    while is_running:
        try:
            res = requests.post(f"{SERVER_URL}/poll", json={
                "host_id": HOST_ID, "user_id": user_id, "current_dir": current_dir
            }).json()
            
            for cmd in res.get("commands", []):
                execute_command(cmd)
            
            app.status_label.config(text="СТАТУС: ОНЛАЙН", fg=THEMES[current_theme]["status_on"])
        except:
            app.status_label.config(text="СТАТУС: ОШИБКА СЕТИ", fg=THEMES[current_theme]["status_off"])
            
        time.sleep(2)

class DesktopAgentApp:
    def __init__(self, root):
        self.root = root
        self.root.title(f"PC Control Agent ({HOST_ID})")
        self.root.geometry("400x350")
        self.root.resizable(False, False)
        
        self.root.protocol('WM_DELETE_WINDOW', self.hide_window)
        self.icon = None
        self.create_tray_icon()

        self.welcome_label = tk.Label(root, text="ДОБРО ПОЖАЛОВАТЬ В PC AGENT", font=("Arial", 14, "bold"))
        self.welcome_label.pack(pady=15)

        self.id_label = tk.Label(root, text="Введите ваш Telegram ID (из бота):", font=("Arial", 10))
        self.id_label.pack(pady=5)
        self.id_entry = tk.Entry(root, font=("Arial", 12), justify="center", width=25)
        self.id_entry.pack(pady=5)

        saved_id = load_user_id()
        if saved_id:
            self.id_entry.insert(0, saved_id)

        self.theme_label = tk.Label(root, text="Выбор темы оформления:", font=("Arial", 9))
        self.theme_label.pack(pady=(10, 0))

        self.theme_combo = ttk.Combobox(root, values=list(THEMES.keys()), state="readonly", width=15)
        self.theme_combo.set(current_theme)
        self.theme_combo.pack(pady=5)
        self.theme_combo.bind("<<ComboboxSelected>>", lambda e: self.apply_theme(self.theme_combo.get()))

        self.start_btn = tk.Button(root, text="Запустить Агент", font=("Arial", 11, "bold"), width=20, height=1, command=self.start_agent, relief="flat")
        self.start_btn.pack(pady=10)

        self.hide_btn = tk.Button(root, text="Свернуть в трей", font=("Arial", 9), width=20, command=self.hide_window)
        self.hide_btn.pack(pady=2)

        self.stop_btn = tk.Button(root, text="Остановить и Выйти", font=("Arial", 9), width=20, command=self.quit_app, bg="#d32f2f", fg="white")
        self.stop_btn.pack(pady=2)

        self.status_label = tk.Label(root, text="СТАТУС: ОТКЛЮЧЕН", font=("Arial", 10, "bold"))
        self.status_label.pack(side="bottom", fill="x", pady=10)

        self.apply_theme(current_theme)

    def apply_theme(self, theme_name):
        global current_theme
        current_theme = theme_name
        colors = THEMES[theme_name]
        
        self.root.config(bg=colors["bg"])
        self.welcome_label.config(bg=colors["bg"], fg=colors["fg"])
        self.id_label.config(bg=colors["bg"], fg=colors["fg"])
        self.id_entry.config(bg=colors["entry_bg"], fg=colors["fg"], insertbackground=colors["fg"])
        self.theme_label.config(bg=colors["bg"], fg=colors["fg"])
        self.status_label.config(bg=colors["bg"])
        self.hide_btn.config(bg=colors["btn_bg"], fg=colors["btn_fg"])
        
        if not is_running:
            self.start_btn.config(bg=colors["btn_bg"], fg=colors["btn_fg"])
            self.status_label.config(text="СТАТУС: ОТКЛЮЧЕН", fg=colors["status_off"])
        else:
            self.status_label.config(fg=colors["status_on"])

    def create_tray_icon(self):
        image = Image.new('RGB', (64, 64), color='white')
        dc = ImageDraw.Draw(image)
        dc.ellipse((8, 8, 56, 56), fill='#4CAF50')
        
        menu = (
            item('Открыть окно', self.show_window),
            item('Остановить и Выйти', self.quit_app)
        )
        self.icon = pystray.Icon("agent_icon", image, "PC Control Agent", menu)

    def hide_window(self):
        uid = self.id_entry.get().strip()
        if not uid and not is_running:
            messagebox.showwarning("Внимание", "Пожалуйста, введите и запустите Агент с вашим Telegram ID перед сворачиванием.")
            return
        self.root.withdraw()
        threading.Thread(target=self.icon.run, daemon=True).start()

    def show_window(self):
        if self.icon:
            self.icon.stop()
        self.root.after(0, self.root.deiconify)

    def start_agent(self):
        global is_running, user_id
        user_id = self.id_entry.get().strip()
        if not user_id:
            messagebox.showerror("Ошибка", "Введите ваш Telegram ID!")
            return
        
        save_user_id(user_id)
        
        if not is_running:
            is_running = True
            self.start_btn.config(text="Агент запущен", bg="#4caf50", fg="white", state="disabled")
            self.id_entry.config(state="disabled")
            self.theme_combo.config(state="disabled")
            self.status_label.config(text="СТАТУС: ЗАПУСК...", fg="#ff9800")
            threading.Thread(target=network_loop, daemon=True).start()

    def quit_app(self):
        if messagebox.askyesno("Exit", "Вы действительно хотите полностью выключить агента?"):
            global is_running
            is_running = False
            if self.icon:
                self.icon.stop()
            self.root.destroy()
            sys.exit(0)

if __name__ == "__main__":
    root = tk.Tk()
    app = DesktopAgentApp(root)
    root.mainloop()
