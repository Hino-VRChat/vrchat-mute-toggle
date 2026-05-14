"""
VRChat ミュートトグル (OSC) - GUI + タスクトレイ版
ショートカットキーでVRChatのミュートをトグルする。
最小化でタスクトレイに格納。

前提:
- VRChat側でOSCを有効にしておくこと (Action Menu > Options > OSC > Enabled)
- VRChat側で「Toggle Voice」をONにしておくこと
- pip install python-osc keyboard pystray Pillow
"""

import tkinter as tk
from tkinter import font as tkfont
from pythonosc import udp_client
from pythonosc import dispatcher as osc_dispatcher
from pythonosc import osc_server
import subprocess
import keyboard
import json
import os
import sys
import time
import threading
import queue
from PIL import Image, ImageDraw
import pystray

# --- 設定ファイル ---
if getattr(sys, "frozen", False):
    CONFIG_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(CONFIG_DIR, "vrchat_mute_config.json")

DEFAULT_CONFIG = {
    "hotkey_display": "Right Ctrl",
    "hotkey_names": ["right ctrl"],
    "osc_ip": "127.0.0.1",
    "osc_port": 9000,
}


def get_key_display_name(name: str) -> str:
    """キー名から表示用の名前を取得"""
    # keyboardライブラリのname: "right ctrl", "left ctrl", "shift", "a", etc.
    parts = name.split(" ")
    return " ".join(p.capitalize() for p in parts)


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            for k, v in DEFAULT_CONFIG.items():
                if k not in cfg:
                    cfg[k] = v
            # 旧形式(hotkey_scancodes)からの移行
            if "hotkey_names" not in cfg and "hotkey_scancodes" in cfg:
                cfg["hotkey_names"] = DEFAULT_CONFIG["hotkey_names"]
                cfg["hotkey_display"] = DEFAULT_CONFIG["hotkey_display"]
            return cfg
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


# --- トレイアイコン画像生成 ---
def create_tray_icon(muted: bool) -> Image.Image:
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    if muted:
        draw.ellipse([4, 4, 60, 60], fill="#e94560", outline="#ffffff", width=2)
        draw.line([16, 16, 48, 48], fill="#ffffff", width=4)
    else:
        draw.ellipse([4, 4, 60, 60], fill="#53d8fb", outline="#ffffff", width=2)
        draw.rounded_rectangle([24, 14, 40, 38], radius=6, fill="#ffffff")
        draw.arc([18, 28, 46, 52], start=0, end=180, fill="#ffffff", width=3)
        draw.line([32, 52, 32, 58], fill="#ffffff", width=3)
    return img


# --- メインアプリ ---
class VRChatMuteApp:
    VRCHAT_OSC_LISTEN_PORT = 9001  # VRChatからのOSC出力を受信するポート

    def __init__(self):
        self.config = load_config()
        self.mute_state = True  # VRChat検出後に更新
        self.vrchat_running = False
        self.tray_icon = None
        self.osc_server = None

        # ホットキー状態（event.nameベースで判定）
        self.hotkey_names = frozenset(self.config["hotkey_names"])
        self.pressed_names = set()
        self.hotkey_triggered = False
        self._hook_handle = None  # keyboard.hookの戻り値

        # 記録モード
        self.recording = False
        self.recorded_names = {}  # name -> display_name

        # OSCクライアント（送信用）
        self.osc_client = udp_client.SimpleUDPClient(
            self.config["osc_ip"], self.config["osc_port"]
        )
        # OSC送信キュー（直列化）
        self.osc_queue = queue.Queue()
        self.osc_worker_thread = threading.Thread(target=self._osc_worker, daemon=True)
        self.osc_worker_thread.start()

        # VRChat検出 + OSCリスナー起動
        self._check_vrchat_and_start_listener()

        # GUI
        self.root = tk.Tk()
        self.root.title("VRChat Mute Toggle")
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)
        self.root.configure(bg="#1a1a2e")

        win_w, win_h = 320, 240
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = screen_w - win_w - 40
        y = screen_h - win_h - 80
        self.root.geometry(f"{win_w}x{win_h}+{x}+{y}")

        self.font_title = tkfont.Font(family="Segoe UI", size=12, weight="bold")
        self.font_status = tkfont.Font(family="Segoe UI", size=22, weight="bold")
        self.font_label = tkfont.Font(family="Segoe UI", size=11)
        self.font_hotkey = tkfont.Font(family="Consolas", size=11, weight="bold")
        self.font_btn = tkfont.Font(family="Segoe UI", size=11)
        self.font_footer = tkfont.Font(family="Segoe UI", size=11)

        self._build_ui()

        # グローバルキーフック
        self._install_hook()

        self.root.bind("<Unmap>", self._on_minimize)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # 定期的にVRChatプロセスを確認（30秒ごと）
        self._periodic_vrchat_check()
        # キーフックの自動再登録（60秒ごと）
        self._periodic_hook_reinstall()

        self._create_tray_icon()


    def _build_ui(self):
        tk.Label(
            self.root, text="VRChat Mute Toggle",
            font=self.font_title, fg="#e0e0e0", bg="#1a1a2e",
        ).pack(pady=(12, 4))

        self.status_frame = tk.Frame(
            self.root, bg="#16213e", bd=0,
            highlightthickness=1, highlightbackground="#e94560",
        )
        self.status_frame.pack(padx=16, pady=(4, 8), fill="x")

        self.status_label = tk.Label(
            self.status_frame, text="🔇 ミュート",
            font=self.font_status, fg="#e94560", bg="#16213e", pady=8,
        )
        self.status_label.pack()

        hotkey_frame = tk.Frame(self.root, bg="#1a1a2e")
        hotkey_frame.pack(padx=16, fill="x")

        tk.Label(
            hotkey_frame, text="ショートカット:",
            font=self.font_label, fg="#a0a0a0", bg="#1a1a2e",
        ).pack(side="left")

        self.hotkey_display = tk.Label(
            hotkey_frame, text=self.config["hotkey_display"],
            font=self.font_hotkey, fg="#53d8fb", bg="#1a1a2e",
        )
        self.hotkey_display.pack(side="left", padx=(6, 0))

        self.change_btn = tk.Button(
            hotkey_frame, text="変更", font=self.font_btn,
            fg="#e0e0e0", bg="#0f3460",
            activebackground="#e94560", activeforeground="#ffffff",
            bd=0, padx=10, pady=2, cursor="hand2",
            command=self._start_recording,
        )
        self.change_btn.pack(side="right")

        # フッター（VRChat状態 + OSC情報）
        footer_frame = tk.Frame(self.root, bg="#1a1a2e")
        footer_frame.pack(side="bottom", fill="x", pady=(0, 6))

        self.vrchat_status_label = tk.Label(
            footer_frame,
            text="● VRChat 検出" if self.vrchat_running else "○ VRChat 未検出",
            font=self.font_footer,
            fg="#4ecca3" if self.vrchat_running else "#666666",
            bg="#1a1a2e",
        )
        self.vrchat_status_label.pack(side="left", padx=(16, 0))

        tk.Label(
            footer_frame, text="最小化でトレイ格納",
            font=self.font_footer,
            fg="#a0a0a0", bg="#1a1a2e",
        ).pack(side="right", padx=(0, 16))

    # --- キーフック管理 ---
    def _install_hook(self):
        """キーフックを（再）登録する"""
        try:
            keyboard.unhook_all()
        except Exception:
            pass
        self.pressed_names.clear()
        self.hotkey_triggered = False
        self._hook_handle = keyboard.hook(self._on_key_event)

    def _periodic_hook_reinstall(self):
        """60秒ごとにキーフックを再登録。Windowsが応答遅延フックを自動解除する対策"""
        self._install_hook()
        self.root.after(60000, self._periodic_hook_reinstall)

    # --- キーイベント処理 ---
    def _on_key_event(self, event):
        """全キーイベントのハンドラ（event.nameベース）"""
        name = event.name
        if not name:
            return

        if event.event_type == keyboard.KEY_DOWN:
            self.pressed_names.add(name)

            if self.recording:
                # 記録モード中
                if name == "esc":
                    self.root.after(0, self._finish_recording, False)
                    return
                self.recorded_names[name] = get_key_display_name(name)
                display = " + ".join(self.recorded_names.values())
                self.root.after(0, lambda d=display: self.hotkey_display.config(text=d))
            else:
                # 通常モード: ホットキー判定（他キー同時押しでも発火）
                if (not self.hotkey_triggered
                        and self.pressed_names >= self.hotkey_names):
                    self.hotkey_triggered = True
                    self._toggle_mute()

        elif event.event_type == keyboard.KEY_UP:
            self.pressed_names.discard(name)

            # ホットキーのキーが離されたらリセット（再トリガー可能に）
            if name in self.hotkey_names:
                self.hotkey_triggered = False

            if self.recording and len(self.recorded_names) > 0:
                if len(self.pressed_names) == 0:
                    self.root.after(0, self._finish_recording, True)

    # --- ミュートトグル ---
    def _update_status_display(self):
        if self.mute_state:
            self.status_label.config(text="🔇 ミュート", fg="#e94560")
            self.status_frame.config(highlightbackground="#e94560")
        else:
            self.status_label.config(text="🔊 通話中", fg="#53d8fb")
            self.status_frame.config(highlightbackground="#53d8fb")
        self._update_tray_icon()

    def _toggle_mute(self):
        self.mute_state = not self.mute_state
        self.root.after(0, self._update_status_display)
        self.osc_queue.put(True)

    def _osc_worker(self):
        """OSC送信ワーカー。キューから順番に処理し、各トグル間にクールダウンを入れる"""
        while True:
            try:
                self.osc_queue.get(timeout=1)
            except queue.Empty:
                continue
            try:
                self.osc_client.send_message("/input/Voice", 1)
                time.sleep(0.05)
                self.osc_client.send_message("/input/Voice", 0)
                time.sleep(0.05)  # VRChat側の処理待ち
            except Exception:
                # ソケットエラー等 → クライアント再生成
                try:
                    self.osc_client = udp_client.SimpleUDPClient(
                        self.config["osc_ip"], self.config["osc_port"]
                    )
                except Exception:
                    pass
            finally:
                self.osc_queue.task_done()

    # --- VRChat検出 + OSCリスナー ---
    @staticmethod
    def _is_vrchat_running() -> bool:
        """VRChat.exeが起動中か確認"""
        try:
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq VRChat.exe", "/NH"],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return "VRChat.exe" in result.stdout
        except Exception:
            return False

    def _check_vrchat_and_start_listener(self):
        """VRChat検出とOSCリスナー起動"""
        self.vrchat_running = self._is_vrchat_running()
        if self.vrchat_running:
            # VRChatのデフォルトはミュートオフ
            self.mute_state = False
            self._start_osc_listener()

    def _start_osc_listener(self):
        """VRChatからのOSC出力（ポート9001）を受信するサーバーを起動"""
        if self.osc_server is not None:
            return
        try:
            disp = osc_dispatcher.Dispatcher()
            disp.map("/avatar/parameters/MuteSelf", self._on_mute_self)
            self.osc_server = osc_server.ThreadingOSCUDPServer(
                ("127.0.0.1", self.VRCHAT_OSC_LISTEN_PORT), disp
            )
            server_thread = threading.Thread(
                target=self.osc_server.serve_forever, daemon=True
            )
            server_thread.start()
        except Exception:
            # ポート9001が既に使用中の場合は静かに失敗
            self.osc_server = None

    def _on_mute_self(self, address, *args):
        """VRChatからMuteSelfパラメータを受信した時のコールバック"""
        if args:
            is_muted = bool(args[0])
            self.mute_state = is_muted
            self.root.after(0, self._update_status_display)

    def _periodic_vrchat_check(self):
        """30秒ごとにVRChatのプロセスを確認"""
        def check():
            was_running = self.vrchat_running
            self.vrchat_running = self._is_vrchat_running()

            # 状態が変わった場合
            if self.vrchat_running and not was_running:
                self._start_osc_listener()
            elif not self.vrchat_running and was_running:
                if self.osc_server is not None:
                    self.osc_server.shutdown()
                    self.osc_server = None

            self.root.after(0, self._update_vrchat_status)

        threading.Thread(target=check, daemon=True).start()
        self.root.after(30000, self._periodic_vrchat_check)

    def _update_vrchat_status(self):
        """VRChat接続状態の表示を更新"""
        if self.vrchat_running:
            self.vrchat_status_label.config(text="● VRChat 検出", fg="#4ecca3")
        else:
            self.vrchat_status_label.config(text="○ VRChat 未検出", fg="#666666")

    # --- ホットキー変更 ---
    def _start_recording(self):
        if self.recording:
            return
        self.recording = True
        self.recorded_names = {}
        self.hotkey_display.config(text="キーを押してください...", fg="#ffcc00")
        self.change_btn.config(text="ESCで取消", state="disabled")

    def _finish_recording(self, confirmed: bool):
        self.recording = False
        self.change_btn.config(text="変更", state="normal")

        if confirmed and len(self.recorded_names) > 0:
            names = list(self.recorded_names.keys())
            display = " + ".join(self.recorded_names.values())
            self.hotkey_names = frozenset(names)
            self.config["hotkey_names"] = names
            self.config["hotkey_display"] = display
            save_config(self.config)
            self.hotkey_display.config(text=display, fg="#53d8fb")
        else:
            self.hotkey_display.config(text=self.config["hotkey_display"], fg="#53d8fb")

        self.recorded_names = {}

    # --- タスクトレイ ---
    def _on_minimize(self, event=None):
        if self.root.state() == "iconic":
            self.root.withdraw()

    def _create_tray_icon(self):
        if self.tray_icon is not None:
            return
        state_str = "ミュート" if self.mute_state else "通話中"
        title = f"VRChat Mute Toggle - {state_str}"
        icon_image = create_tray_icon(self.mute_state)
        menu = pystray.Menu(
            pystray.MenuItem("表示", self._tray_show_window, default=True),
            pystray.MenuItem("終了", self._tray_quit),
        )
        self.tray_icon = pystray.Icon(
            "vrchat_mute", icon_image, title, menu
        )
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def _update_tray_icon(self):
        if self.tray_icon is not None:
            self.tray_icon.icon = create_tray_icon(self.mute_state)
            state_str = "ミュート" if self.mute_state else "通話中"
            self.tray_icon.title = f"VRChat Mute Toggle - {state_str}"

    def _tray_show_window(self, icon=None, item=None):
        self.root.after(0, self._restore_window)

    def _restore_window(self):
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        self.root.focus_force()

    def _tray_quit(self, icon=None, item=None):
        if self.tray_icon is not None:
            self.tray_icon.stop()
            self.tray_icon = None
        self.root.after(0, self._on_close)

    def _on_close(self):
        keyboard.unhook_all()
        if self.osc_server is not None:
            self.osc_server.shutdown()
        if self.tray_icon is not None:
            self.tray_icon.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = VRChatMuteApp()
    app.run()
