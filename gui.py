import json
import os
import pathlib
import queue
import socket
import sys
import webbrowser

import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk

import server as file_server

APP_NAME = "File Server"
CONFIG_DIR = pathlib.Path(os.environ.get("APPDATA") or pathlib.Path.home()) / "DFileServer"
CONFIG_PATH = CONFIG_DIR / "config.json"


def ensure_std_streams():
    # Windowed builds have no console: stdout/stderr are None, and any library
    # writing to them (http.server logging, traceback hooks) would crash.
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        stream = open(CONFIG_DIR / "gui.log", "a", encoding="utf-8", buffering=1)
    except OSError:
        stream = open(os.devnull, "w")
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream


ensure_std_streams()

try:
    import pystray
    from PIL import Image, ImageDraw, ImageTk

    TRAY_AVAILABLE = True
except Exception:
    TRAY_AVAILABLE = False

DEFAULTS = {
    "root": file_server.DEFAULT_ROOT,
    "host": "0.0.0.0",
    "port": "8000",
    "minimize_to_tray": True,
}


def load_config():
    config = dict(DEFAULTS)
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if isinstance(data, dict):
        for key in DEFAULTS:
            if key in data:
                config[key] = data[key]
    if not pathlib.Path(str(config["root"])).is_dir():
        config["root"] = str(pathlib.Path.home())
    try:
        port = int(config["port"])
        config["port"] = str(port if 1 <= port <= 65535 else 8000)
    except (TypeError, ValueError):
        config["port"] = "8000"
    return config


def save_config(config):
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def local_ip():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def make_tray_image():
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((2, 2, 62, 62), radius=14, fill=(37, 99, 235, 255))
    draw.rounded_rectangle((13, 22, 51, 49), radius=5, fill=(255, 255, 255, 255))
    draw.polygon([(13, 20), (28, 20), (33, 25), (13, 25)], fill=(255, 255, 255, 255))
    return image


def shorten_path(path, limit=64):
    text = str(path)
    if len(text) <= limit:
        return text
    head = limit // 2 - 1
    return f"{text[:head]}…{text[-head:]}"


class ControlPanel:
    def __init__(self, window):
        self.window = window
        self.config = load_config()
        self.actions = queue.Queue()
        self.icon = None
        self.window_icon = None
        self.tray_hint_shown = False

        self.server = file_server.FileServer(
            self.config["root"],
            host=self.config["host"],
            port=int(self.config["port"]),
        )

        self.root_var = tk.StringVar(value=str(self.server.root))
        self.host_var = tk.StringVar(value=self.server.host)
        self.port_var = tk.StringVar(value=str(self.server.port))
        self.tray_var = tk.BooleanVar(value=bool(self.config["minimize_to_tray"]) and TRAY_AVAILABLE)

        self._build_ui()
        self._build_tray()
        self._refresh()
        self._center_window()

        self.window.protocol("WM_DELETE_WINDOW", self._on_close)
        self.window.bind("<Unmap>", self._on_unmap)
        self.window.after(120, self._poll_actions)

    def _build_ui(self):
        base_font = tkfont.nametofont("TkDefaultFont")
        bold_font = base_font.copy()
        bold_font.configure(size=10, weight="bold")
        self.bold_font = bold_font

        outer = ttk.Frame(self.window, padding=18)
        outer.grid(row=0, column=0, sticky="nsew")
        outer.columnconfigure(0, weight=1)

        folder = ttk.LabelFrame(outer, text="服务根目录", padding=12)
        folder.grid(row=0, column=0, sticky="ew")
        folder.columnconfigure(0, weight=1)
        self.root_entry = ttk.Entry(folder, textvariable=self.root_var)
        self.root_entry.grid(row=0, column=0, sticky="ew")
        browse_button = ttk.Button(folder, text="浏览…", command=self.browse_root)
        browse_button.grid(row=0, column=1, padx=(8, 0))
        self.apply_button = ttk.Button(folder, text="应用", width=6, command=self.apply_root)
        self.apply_button.grid(row=0, column=2, padx=(6, 0))
        ttk.Label(
            folder,
            text="服务运行中切换目录会立即生效，无需重启。",
            foreground="#6b7280",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 0))

        network = ttk.LabelFrame(outer, text="监听设置", padding=12)
        network.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        network.columnconfigure(1, weight=1)
        ttk.Label(network, text="主机").grid(row=0, column=0, sticky="w")
        self.host_entry = ttk.Entry(network, textvariable=self.host_var)
        self.host_entry.grid(row=0, column=1, sticky="ew", padx=(8, 16))
        ttk.Label(network, text="端口").grid(row=0, column=2, sticky="w")
        self.port_entry = ttk.Entry(network, textvariable=self.port_var, width=8)
        self.port_entry.grid(row=0, column=3, sticky="w", padx=(8, 0))
        ttk.Label(
            network,
            text="0.0.0.0 允许局域网访问；127.0.0.1 仅本机可用。",
            foreground="#6b7280",
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(8, 0))

        status = ttk.LabelFrame(outer, text="服务状态", padding=12)
        status.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        status.columnconfigure(0, weight=1)
        self.status_label = ttk.Label(status, text="已停止", font=bold_font)
        self.status_label.grid(row=0, column=0, sticky="w")
        self.url_label = ttk.Label(status, text="—", foreground="#0a58ca", cursor="hand2")
        self.url_label.grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.url_label.bind("<Button-1>", lambda event: self.open_browser())
        self.root_label = ttk.Label(status, text="", foreground="#4b5563")
        self.root_label.grid(row=2, column=0, sticky="w", pady=(6, 0))

        buttons = ttk.Frame(outer)
        buttons.grid(row=3, column=0, sticky="ew", pady=(14, 0))
        buttons.columnconfigure(2, weight=1)
        self.toggle_button = ttk.Button(buttons, text="启动服务", width=12, command=self.toggle_server)
        self.toggle_button.grid(row=0, column=0)
        self.open_button = ttk.Button(buttons, text="打开浏览器", command=self.open_browser)
        self.open_button.grid(row=0, column=1, padx=(8, 0))
        self.hide_button = ttk.Button(buttons, text="最小化到托盘", command=self.hide_to_tray)
        self.hide_button.grid(row=0, column=2, sticky="e")
        if not TRAY_AVAILABLE:
            self.hide_button.state(["disabled"])

        tray_text = "关闭/最小化窗口时收起到托盘"
        if not TRAY_AVAILABLE:
            tray_text = "托盘不可用（需要 pystray 与 Pillow）"
        self.tray_check = ttk.Checkbutton(outer, text=tray_text, variable=self.tray_var, command=self._on_tray_toggle)
        self.tray_check.grid(row=4, column=0, sticky="w", pady=(10, 0))
        if not TRAY_AVAILABLE:
            self.tray_check.state(["disabled"])

    def _build_tray(self):
        if not TRAY_AVAILABLE:
            return
        try:
            image = make_tray_image()
            menu = pystray.Menu(
                pystray.MenuItem("显示窗口", lambda icon, item: self._post("show"), default=True),
                pystray.MenuItem(
                    "启动服务",
                    lambda icon, item: self._post("start"),
                    enabled=lambda item: not self.server.running,
                ),
                pystray.MenuItem(
                    "停止服务",
                    lambda icon, item: self._post("stop"),
                    enabled=lambda item: self.server.running,
                ),
                pystray.MenuItem(
                    "打开浏览器",
                    lambda icon, item: self._post("open"),
                    enabled=lambda item: self.server.running,
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出", lambda icon, item: self._post("exit")),
            )
            self.icon = pystray.Icon("file-server", image, APP_NAME, menu)
            self.icon.run_detached()
        except Exception:
            self.icon = None
            self.hide_button.state(["disabled"])
            return
        try:
            self.window_icon = ImageTk.PhotoImage(image)
            self.window.iconphoto(True, self.window_icon)
        except Exception:
            self.window_icon = None

    def _center_window(self):
        self.window.update_idletasks()
        width = max(self.window.winfo_reqwidth(), 520)
        height = self.window.winfo_reqheight()
        x = max((self.window.winfo_screenwidth() - width) // 2, 0)
        y = max((self.window.winfo_screenheight() - height) // 3, 0)
        self.window.geometry(f"{width}x{height}+{x}+{y}")

    def _poll_actions(self):
        while True:
            try:
                action = self.actions.get_nowait()
            except queue.Empty:
                break
            if action == "show":
                self._show_window()
            elif action == "start":
                self.start_server()
            elif action == "stop":
                self.stop_server()
            elif action == "open":
                self.open_browser()
            elif action == "exit":
                self._quit(confirm=False)
                return
        self.window.after(120, self._poll_actions)

    def _post(self, action):
        self.actions.put(action)

    def _refresh(self):
        running = self.server.running
        if running:
            url = self._display_url()
            self.status_label.configure(text="● 运行中", foreground="#15803d")
            self.url_label.configure(text=f"{url}   点击打开")
            self.toggle_button.configure(text="停止服务")
            self.open_button.state(["!disabled"])
            self.apply_button.state(["!disabled"])
            self.host_entry.state(["disabled"])
            self.port_entry.state(["disabled"])
        else:
            self.status_label.configure(text="○ 已停止", foreground="#b91c1c")
            self.url_label.configure(text="—")
            self.toggle_button.configure(text="启动服务")
            self.open_button.state(["disabled"])
            self.apply_button.state(["disabled"])
            self.host_entry.state(["!disabled"])
            self.port_entry.state(["!disabled"])
        self.root_label.configure(text=f"当前根目录：{shorten_path(self.server.root)}")
        if self.icon is not None:
            try:
                self.icon.update_menu()
            except Exception:
                pass

    def _display_url(self):
        address = self.server.address
        if address is None:
            return None
        host, port = address[0], address[1]
        if host in ("0.0.0.0", "", "::"):
            host = local_ip()
        elif host == "localhost":
            host = "127.0.0.1"
        return f"http://{host}:{port}"

    def _save(self):
        self.config = {
            "root": self.root_var.get().strip(),
            "host": self.host_var.get().strip() or "0.0.0.0",
            "port": self.port_var.get().strip() or "8000",
            "minimize_to_tray": bool(self.tray_var.get()),
        }
        save_config(self.config)

    def start_server(self):
        raw_root = self.root_var.get().strip().strip('"')
        if not raw_root:
            messagebox.showwarning("缺少根目录", "请选择要共享的目录。", parent=self.window)
            return
        if not pathlib.Path(raw_root).is_dir():
            messagebox.showwarning("目录不存在", f"找不到目录：\n{raw_root}", parent=self.window)
            return
        raw_host = self.host_var.get().strip() or "0.0.0.0"
        try:
            port = int(self.port_var.get().strip())
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            messagebox.showwarning("端口无效", "请输入 1 - 65535 之间的端口号。", parent=self.window)
            return

        try:
            self.server.set_root(raw_root)
            self.server.host = raw_host
            self.server.port = port
            self.server.start()
        except OSError as exc:
            messagebox.showerror(
                "启动失败",
                f"无法监听 {raw_host}:{port}。\n端口可能已被占用。\n\n{exc}",
                parent=self.window,
            )
            self._refresh()
            return

        self.root_var.set(str(self.server.root))
        self._save()
        self._refresh()

    def stop_server(self):
        self.server.stop()
        self._refresh()

    def toggle_server(self):
        if self.server.running:
            self.stop_server()
        else:
            self.start_server()

    def browse_root(self):
        initial = self.root_var.get().strip() or str(pathlib.Path.home())
        chosen = filedialog.askdirectory(
            parent=self.window,
            title="选择服务根目录",
            initialdir=initial,
            mustexist=True,
        )
        if not chosen:
            return
        self.root_var.set(str(pathlib.Path(chosen)))
        if self.server.running:
            self.apply_root()
        else:
            self._save()

    def apply_root(self):
        raw_root = self.root_var.get().strip().strip('"')
        if not pathlib.Path(raw_root).is_dir():
            messagebox.showwarning("目录不存在", f"找不到目录：\n{raw_root}", parent=self.window)
            return
        self.server.set_root(raw_root)
        self.root_var.set(str(self.server.root))
        self._save()
        self._refresh()

    def open_browser(self):
        if not self.server.running:
            return
        url = self._display_url()
        if url:
            webbrowser.open(url)

    def hide_to_tray(self):
        if self.icon is None:
            return
        self.window.withdraw()
        if not self.tray_hint_shown:
            self.tray_hint_shown = True
            try:
                self.icon.notify("服务在后台运行，双击托盘图标可重新打开窗口。", APP_NAME)
            except Exception:
                pass

    def _show_window(self):
        self.window.deiconify()
        self.window.lift()
        self.window.attributes("-topmost", True)
        self.window.after(50, lambda: self.window.attributes("-topmost", False))
        self.window.focus_force()

    def _on_tray_toggle(self):
        if self.tray_var.get() and self.icon is None and TRAY_AVAILABLE:
            self._build_tray()
            if self.icon is None:
                self.tray_var.set(False)
            else:
                self.hide_button.state(["!disabled"])
        self._save()

    def _on_unmap(self, event):
        if event.widget is not self.window:
            return
        if self.window.state() == "iconic" and self.tray_var.get() and self.icon is not None:
            self.hide_to_tray()

    def _on_close(self):
        if self.tray_var.get() and self.icon is not None:
            self.hide_to_tray()
            return
        self._quit(confirm=True)

    def _quit(self, confirm):
        if confirm and self.server.running:
            if not messagebox.askyesno(
                "退出",
                "服务正在运行，退出后服务将停止。\n确定要退出吗？",
                parent=self.window,
            ):
                return False
        self._save()
        try:
            self.server.stop()
        except Exception:
            pass
        icon, self.icon = self.icon, None
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                pass
        self.window.destroy()
        return True


def main():
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

    window = tk.Tk()
    window.title("File Server 控制台")
    window.resizable(False, False)
    try:
        tkfont.nametofont("TkDefaultFont").configure(family="Microsoft YaHei UI", size=10)
    except tk.TclError:
        pass
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass

    ControlPanel(window)
    window.mainloop()


if __name__ == "__main__":
    main()