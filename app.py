"""Modern adaptive brightness webcam app with theme and tray support."""
from __future__ import annotations

import ctypes
import logging
import queue
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageTk
except ImportError as exc:
    raise SystemExit("Pillow is required: pip install pillow") from exc

try:
    import pystray
except ImportError:
    pystray = None

import customtkinter as ctk
import adaptive_brightness as engine


APP_NAME = "Adaptive Brightness Webcam"
AUTOSTART_FLAG = "--autostart"
STARTUP_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

ACCENT = "#2E6BE6"
ACCENT_HOVER = "#1F5AD0"
SUCCESS = "#18926B"
WARNING = "#C98A1B"
DANGER = "#D94D5C"

PAGE_BG = ("#F4F6FB", "#171A21")
CARD_BG = ("#FFFFFF", "#1F242E")
CARD_BORDER = ("#E2E7F0", "#2C3340")
SUBTLE_BG = ("#F1F4FA", "#252B37")
FIELD_BG = ("#FBFCFF", "#202634")
TEXT_PRIMARY = ("#18202B", "#F3F6FC")
TEXT_SECONDARY = ("#5D6675", "#9AA4B5")

try:
    LANCZOS = Image.Resampling.LANCZOS
except AttributeError:
    LANCZOS = Image.LANCZOS


def get_auto_theme() -> str:
    return "light" if 6 <= datetime.now().hour < 18 else "dark"


def get_windows_theme() -> str:
    try:
        import winreg

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return "light" if int(value) == 1 else "dark"
    except Exception:
        return get_auto_theme()


def create_app_icon(size: int = 256) -> Image.Image:
    size = max(16, int(size))
    icon = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(icon)
    radius = int(size * 0.23)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=(25, 35, 73, 255))

    for y in range(size):
        color = (
            35 - int(y * 20 / size),
            48 - int(y * 30 / size),
            96 - int(y * 65 / size),
            255,
        )
        draw.line((0, y, size, y), fill=color)

    draw.ellipse(
        (int(size * 0.16), int(size * 0.16), int(size * 0.84), int(size * 0.84)),
        outline=(137, 204, 255, 255),
        width=max(2, int(size * 0.042)),
    )

    moon = Image.new("L", (size, size), 0)
    md = ImageDraw.Draw(moon)
    md.ellipse((int(size * 0.33), int(size * 0.33), int(size * 0.67), int(size * 0.67)), fill=255)
    md.ellipse((int(size * 0.45), int(size * 0.25), int(size * 0.79), int(size * 0.59)), fill=0)

    layer = Image.new("RGBA", (size, size), (255, 206, 112, 255))
    icon.paste(layer, (0, 0), moon)

    for x, y, r in ((0.30, 0.28, 0.016), (0.73, 0.29, 0.013), (0.76, 0.63, 0.015), (0.27, 0.69, 0.012)):
        cx = int(x * size)
        cy = int(y * size)
        rad = max(1, int(r * size))
        draw.ellipse((cx - rad, cy - rad, cx + rad, cy + rad), fill=(224, 236, 255, 210))

    return icon


def save_app_icon() -> Path | None:
    try:
        path = Path(tempfile.gettempdir()) / "adaptive_brightness_webcam.ico"
        create_app_icon(256).save(
            path,
            format="ICO",
            sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
        )
        return path
    except Exception:
        return None


class AutoHideScrollableFrame(ctk.CTkScrollableFrame):
    """
    CTkScrollableFrame that hides the scrollbar when content fits.
    """

    def __init__(self, master=None, **kwargs):
        super().__init__(master, **kwargs)

        self._ah_ready = False
        self._ah_canvas = getattr(self, "_parent_canvas", None)
        self._ah_scrollbar = getattr(self, "_scrollbar", None)
        self._ah_frame = getattr(self, "_frame", None)

        if self._ah_canvas is not None and self._ah_scrollbar is not None:
            self._ah_manager = self._ah_scrollbar.winfo_manager() or "grid"
            self._ah_ready = True

            self._hide_scrollbar()

            self._ah_canvas.bind("<Configure>", self._ah_on_configure, add="+")
            self.bind("<Configure>", self._ah_on_configure, add="+")

            if self._ah_frame is not None:
                self._ah_frame.bind("<Configure>", self._ah_on_configure, add="+")

            self.after(80, self._ah_refresh)
            self.after(500, self._ah_refresh)

    def _ah_on_configure(self, event=None):
        self._ah_refresh()

    def _hide_scrollbar(self):
        try:
            if self._ah_manager == "grid":
                self._ah_scrollbar.grid_remove()
            elif self._ah_manager == "pack":
                self._ah_scrollbar.pack_forget()
            else:
                self._ah_scrollbar.place_forget()
        except Exception:
            pass

    def _show_scrollbar(self):
        try:
            if self._ah_manager == "grid":
                self._ah_scrollbar.grid()
            elif self._ah_manager == "pack":
                self._ah_scrollbar.pack(side="right", fill="y")
            else:
                self._ah_scrollbar.place(relx=1.0, rely=0.0, relheight=1.0, anchor="ne")
        except Exception:
            pass

    def _ah_refresh(self):
        if not self._ah_ready:
            return

        try:
            bbox = self._ah_canvas.bbox("all")
            if not bbox:
                self._hide_scrollbar()
                return

            content_height = bbox[3] - bbox[1]
            canvas_height = self._ah_canvas.winfo_height()

            if content_height > canvas_height + 8:
                self._show_scrollbar()
            else:
                self._hide_scrollbar()
        except Exception:
            pass


class BrightnessApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()

        self.theme_mode_var = ctk.StringVar(value="Windows")
        self.current_theme = get_windows_theme()
        ctk.set_appearance_mode(self.current_theme)

        self.title(APP_NAME)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.geometry("980x700")
        self.minsize(860, 600)
        self.configure(fg_color=PAGE_BG)

        self.app_icon = create_app_icon(256)
        self._window_icon = ImageTk.PhotoImage(self.app_icon.resize((64, 64), LANCZOS))
        self.iconphoto(True, self._window_icon)

        icon_path = save_app_icon()
        if icon_path:
            try:
                self.iconbitmap(str(icon_path))
            except Exception:
                pass

        self.worker_thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.camera_lock = threading.Lock()
        self.brightness_lock = threading.Lock()
        self.events: queue.Queue = queue.Queue()
        self.worker_generation = 0
        self.testing = False
        self.is_running = False
        self.tray_icon = None
        self.tray_thread = None
        self.toast_job = None

        self.camera_var = ctk.StringVar(value="0")
        self.interval_var = ctk.StringVar(value="120")
        self.preset_var = ctk.StringVar(value="120")
        self.startup_var = ctk.BooleanVar(value=self.startup_enabled())
        self.close_to_tray_var = ctk.BooleanVar(value=True)

        self.last_value_var = ctk.StringVar(value="-")
        self.last_target_var = ctk.StringVar(value="-")
        self.last_update_var = ctk.StringVar(value="-")

        self.build_ui()
        self.center_window()

        self.after(150, self.process_events)
        self.after(500, self.apply_theme)
        self.after(8000, self.theme_tick)

        self.start_tray()
        self.ensure_startup_command()

    def center_window(self) -> None:
        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w = min(1120, max(860, int(sw * 0.76), sw - 24))
        h = min(820, max(600, int(sh * 0.82), sh - 90))
        self.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 2 - 16)}")

    def build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=28, pady=(24, 10))
        header.grid_columnconfigure(1, weight=1)

        header_icon = ctk.CTkImage(light_image=self.app_icon, dark_image=self.app_icon, size=(56, 56))
        ctk.CTkLabel(header, image=header_icon, text="").grid(row=0, column=0, rowspan=2, padx=(0, 16))

        ctk.CTkLabel(
            header,
            text="Adaptive Brightness",
            font=("Segoe UI", 24, "bold"),
            text_color=TEXT_PRIMARY,
        ).grid(row=0, column=1, sticky="w")

        ctk.CTkLabel(
            header,
            text="Webcam light sensing with a clean, modern look",
            font=("Segoe UI", 10),
            text_color=TEXT_SECONDARY,
        ).grid(row=1, column=1, sticky="w", pady=(2, 0))

        self.status_card = ctk.CTkFrame(
            self,
            corner_radius=18,
            border_width=1,
            fg_color=CARD_BG,
            border_color=CARD_BORDER,
        )
        self.status_card.grid(row=1, column=0, sticky="ew", padx=28, pady=(8, 10))
        self.build_status(self.status_card)

        scroll = AutoHideScrollableFrame(self, fg_color="transparent")
        scroll.grid(row=2, column=0, sticky="nsew", padx=22, pady=(4, 0))

        content = ctk.CTkFrame(scroll, fg_color="transparent")
        content.pack(fill="both", expand=True)
        content.grid_columnconfigure(0, weight=11)
        content.grid_columnconfigure(1, weight=9)

        settings = ctk.CTkFrame(
            content,
            corner_radius=18,
            border_width=1,
            fg_color=CARD_BG,
            border_color=CARD_BORDER,
        )
        settings.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=(0, 10))

        stats = ctk.CTkFrame(
            content,
            corner_radius=18,
            border_width=1,
            fg_color=CARD_BG,
            border_color=CARD_BORDER,
        )
        stats.grid(row=0, column=1, sticky="nsew", padx=(8, 0), pady=(0, 10))

        self.build_settings(settings)
        self.build_stats(stats)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=3, column=0, sticky="ew", padx=28, pady=(8, 22))
        footer.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            footer,
            text="Privacy first - no images are stored or uploaded",
            font=("Segoe UI", 9),
            text_color=TEXT_SECONDARY,
        ).grid(row=0, column=0, sticky="w")

        self.toast = ctk.CTkLabel(footer, text="", font=("Segoe UI", 9), text_color=ACCENT)
        self.toast.grid(row=0, column=1, sticky="w", padx=16)

        self.test_btn = ctk.CTkButton(
            footer,
            text="Test camera",
            command=self.test_once,
            width=140,
            height=42,
            corner_radius=12,
            fg_color="transparent",
            hover_color=("#E9F1FF", "#24324D"),
            text_color=ACCENT,
            border_width=2,
            border_color=ACCENT,
            font=("Segoe UI", 10, "bold"),
        )
        self.test_btn.grid(row=0, column=2, sticky="e")

        refresh = getattr(scroll, "_ah_refresh", None)
        if callable(refresh):
            self.after(180, refresh)

    def build_status(self, parent) -> None:
        inner = ctk.CTkFrame(parent, fg_color="transparent")
        inner.pack(fill="x", padx=24, pady=20)

        left = ctk.CTkFrame(inner, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True)

        row = ctk.CTkFrame(left, fg_color="transparent")
        row.pack(anchor="w")

        self.status_dot = ctk.CTkLabel(row, text="●", font=("Segoe UI", 13), text_color=TEXT_SECONDARY)
        self.status_dot.pack(side="left", padx=(0, 10))

        self.status_label = ctk.CTkLabel(
            row,
            text="Ready to start",
            font=("Segoe UI", 16, "bold"),
            text_color=TEXT_PRIMARY,
        )
        self.status_label.pack(side="left")

        self.status_sub = ctk.CTkLabel(
            left,
            text="Start automation to adjust brightness automatically.",
            font=("Segoe UI", 9),
            text_color=TEXT_SECONDARY,
        )
        self.status_sub.pack(anchor="w", pady=(5, 0))

        buttons = ctk.CTkFrame(inner, fg_color="transparent")
        buttons.pack(side="right")

        self.start_btn = ctk.CTkButton(
            buttons,
            text="Start automation",
            command=self.start,
            height=44,
            width=180,
            corner_radius=12,
            font=("Segoe UI", 10, "bold"),
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            text_color="#FFFFFF",
        )
        self.start_btn.pack(side="left", padx=(0, 10))

        self.stop_btn = ctk.CTkButton(
            buttons,
            text="Stop",
            command=self.stop,
            height=44,
            width=110,
            corner_radius=12,
            font=("Segoe UI", 10, "bold"),
            fg_color=SUBTLE_BG,
            hover_color=("#E7EBF3", "#313947"),
            text_color=TEXT_PRIMARY,
            border_width=1,
            border_color=CARD_BORDER,
            state="disabled",
        )
        self.stop_btn.pack(side="left")

    def card_header(self, parent, title, subtitle):
        ctk.CTkLabel(parent, text=title, font=("Segoe UI", 16, "bold"), text_color=TEXT_PRIMARY).pack(anchor="w")
        ctk.CTkLabel(parent, text=subtitle, font=("Segoe UI", 9), text_color=TEXT_SECONDARY).pack(anchor="w", pady=(4, 0))

    def build_settings(self, parent) -> None:
        body = ctk.CTkFrame(parent, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=24, pady=22)
        self.card_header(body, "Settings", "Camera, interval, startup, theme, and tray behavior.")

        form = ctk.CTkFrame(body, fg_color="transparent")
        form.pack(fill="both", expand=True, pady=(16, 0))

        ctk.CTkLabel(form, text="CAMERA", font=("Segoe UI", 9, "bold"), text_color=TEXT_SECONDARY).pack(anchor="w")

        row = ctk.CTkFrame(form, fg_color="transparent")
        row.pack(anchor="w", pady=(6, 18))

        ctk.CTkOptionMenu(
            row,
            values=[str(i) for i in range(6)],
            variable=self.camera_var,
            width=120,
            height=40,
            corner_radius=12,
            fg_color=FIELD_BG,
            button_color=ACCENT,
            button_hover_color=ACCENT_HOVER,
            text_color=TEXT_PRIMARY,
            font=("Segoe UI", 10),
        ).pack(side="left")

        ctk.CTkLabel(
            row,
            text="0 is usually the built-in webcam",
            font=("Segoe UI", 9),
            text_color=TEXT_SECONDARY,
        ).pack(side="left", padx=12)

        ctk.CTkLabel(form, text="CHECK INTERVAL", font=("Segoe UI", 9, "bold"), text_color=TEXT_SECONDARY).pack(anchor="w")

        interval = ctk.CTkFrame(form, fg_color="transparent")
        interval.pack(anchor="w", pady=(6, 8))

        ctk.CTkEntry(
            interval,
            textvariable=self.interval_var,
            width=92,
            height=40,
            corner_radius=12,
            fg_color=FIELD_BG,
            border_color=CARD_BORDER,
            text_color=TEXT_PRIMARY,
            font=("Segoe UI", 10),
        ).pack(side="left")

        ctk.CTkLabel(
            interval,
            text="seconds - minimum 30; battery mode adds extra delay",
            font=("Segoe UI", 9),
            text_color=TEXT_SECONDARY,
        ).pack(side="left", padx=12)

        ctk.CTkSegmentedButton(
            form,
            values=["120", "300", "600"],
            variable=self.preset_var,
            command=lambda value: self.interval_var.set(value),
            height=38,
            corner_radius=12,
            fg_color=SUBTLE_BG,
            selected_color=ACCENT,
            selected_hover_color=ACCENT_HOVER,
            text_color=TEXT_PRIMARY,
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(8, 18))

        ctk.CTkLabel(form, text="APP THEME", font=("Segoe UI", 9, "bold"), text_color=TEXT_SECONDARY).pack(anchor="w")

        theme_row = ctk.CTkFrame(form, fg_color="transparent")
        theme_row.pack(anchor="w", pady=(6, 18))

        ctk.CTkSegmentedButton(
            theme_row,
            values=["Auto", "Windows"],
            variable=self.theme_mode_var,
            command=self.on_theme_mode,
            height=40,
            width=220,
            corner_radius=12,
            fg_color=SUBTLE_BG,
            selected_color=ACCENT,
            selected_hover_color=ACCENT_HOVER,
            text_color=TEXT_PRIMARY,
            font=("Segoe UI", 10),
        ).pack(side="left")

        ctk.CTkLabel(
            theme_row,
            text="Auto follows day/night; Windows follows system theme.",
            font=("Segoe UI", 9),
            text_color=TEXT_SECONDARY,
        ).pack(side="left", padx=12)

        ctk.CTkSwitch(
            form,
            text="Launch automatically with Windows",
            variable=self.startup_var,
            command=self.toggle_startup,
            font=("Segoe UI", 10),
            text_color=TEXT_PRIMARY,
            progress_color=ACCENT,
            fg_color=("#D8DFEA", "#3A4150"),
        ).pack(anchor="w", pady=(4, 0))

        ctk.CTkSwitch(
            form,
            text="Close to system tray",
            variable=self.close_to_tray_var,
            font=("Segoe UI", 10),
            text_color=TEXT_PRIMARY,
            progress_color=ACCENT,
            fg_color=("#D8DFEA", "#3A4150"),
        ).pack(anchor="w", pady=(10, 0))

        note = ctk.CTkFrame(body, corner_radius=14, fg_color=SUBTLE_BG)
        note.pack(fill="x", side="bottom", pady=(18, 0))

        ctk.CTkLabel(
            note,
            text="The camera is active only for a brief sample.",
            font=("Segoe UI", 9),
            text_color=TEXT_SECONDARY,
        ).pack(anchor="w", padx=14, pady=12)

    def build_stats(self, parent) -> None:
        body = ctk.CTkFrame(parent, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=24, pady=22)
        self.card_header(body, "Live readings", "Latest light estimate and target brightness.")

        content = ctk.CTkFrame(body, fg_color="transparent")
        content.pack(fill="both", expand=True, pady=(16, 0))

        self.metric_card(content, "AMBIENT LIGHT", self.last_value_var, "camera value")
        target = self.metric_card(content, "TARGET BRIGHTNESS", self.last_target_var, "display level")

        self.progress = ctk.CTkProgressBar(
            target,
            height=12,
            corner_radius=6,
            progress_color=ACCENT,
            fg_color=("#E7ECF6", "#2A3140"),
        )
        self.progress.pack(fill="x", pady=(12, 0))
        self.progress.set(0)

        meta = ctk.CTkFrame(target, fg_color="transparent")
        meta.pack(fill="x", pady=(12, 0))

        ctk.CTkLabel(meta, text="LAST UPDATE", font=("Segoe UI", 9, "bold"), text_color=TEXT_SECONDARY).pack(side="left")
        ctk.CTkLabel(meta, textvariable=self.last_update_var, font=("Segoe UI", 10, "bold"), text_color=TEXT_PRIMARY).pack(side="right")

        tip = ctk.CTkFrame(body, corner_radius=14, fg_color=SUBTLE_BG)
        tip.pack(fill="x", side="bottom", pady=(18, 0))

        ctk.CTkLabel(
            tip,
            text="Keep the webcam uncovered and facing the room for best results.",
            font=("Segoe UI", 9),
            text_color=TEXT_SECONDARY,
            wraplength=300,
            justify="left",
        ).pack(anchor="w", padx=14, pady=12)

    def metric_card(self, parent, title, variable, unit):
        card = ctk.CTkFrame(parent, corner_radius=16, fg_color=SUBTLE_BG)
        card.pack(fill="x", pady=(0, 10))

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=18, pady=16)

        ctk.CTkLabel(inner, text=title, font=("Segoe UI", 9, "bold"), text_color=TEXT_SECONDARY).pack(anchor="w")

        row = ctk.CTkFrame(inner, fg_color="transparent")
        row.pack(fill="x", pady=(7, 0))

        ctk.CTkLabel(row, textvariable=variable, font=("Segoe UI", 24, "bold"), text_color=TEXT_PRIMARY).pack(side="left")
        ctk.CTkLabel(row, text=unit, font=("Segoe UI", 9), text_color=TEXT_SECONDARY).pack(side="left", padx=10, pady=(10, 0))

        return inner

    def show_toast(self, message, mode="info"):
        self.toast.configure(
            text=message,
            text_color={"success": SUCCESS, "error": DANGER, "warning": WARNING}.get(mode, ACCENT),
        )

        if self.toast_job:
            self.after_cancel(self.toast_job)

        self.toast_job = self.after(4200, lambda: self.toast.configure(text=""))

    def set_status(self, text, sub=None, mode="idle"):
        colors = {
            "running": SUCCESS,
            "testing": ACCENT,
            "error": DANGER,
            "warning": WARNING,
            "success": SUCCESS,
            "idle": TEXT_SECONDARY,
        }

        self.status_label.configure(text=text)
        self.status_dot.configure(text_color=colors.get(mode, TEXT_SECONDARY))

        if sub is not None:
            self.status_sub.configure(text=sub)

    def start(self):
        if self.worker_thread and self.worker_thread.is_alive() and self.is_running:
            return

        try:
            camera = int(self.camera_var.get())
            interval = max(30.0, float(self.interval_var.get()))
        except ValueError:
            self.show_toast("Enter a valid camera number and interval.", "error")
            return

        self.stop_event.clear()
        self.worker_generation += 1
        gen = self.worker_generation

        self.worker_thread = threading.Thread(target=self.worker, args=(camera, interval, gen), daemon=True)
        self.worker_thread.start()

        self.is_running = True
        self.set_status("Running", "Monitoring ambient light", "running")
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.show_toast("Automation started", "success")

    def stop(self):
        self.stop_event.set()
        self.worker_generation += 1
        self.is_running = False

        self.set_status("Paused", "Automation stopped")
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")

    def worker(self, camera, interval, generation):
        last_target = None

        while not self.stop_event.is_set() and generation == self.worker_generation:
            try:
                with self.camera_lock:
                    value = engine.sample_camera(camera)

                if value is None:
                    self.events.put(("status", ("Camera unavailable", "Waiting for next check", "warning")))
                else:
                    target = engine.value_to_brightness(value)

                    with self.brightness_lock:
                        if last_target is None:
                            engine.set_brightness(target)
                        elif abs(target - last_target) >= 3:
                            engine.ramp_brightness(
                                last_target,
                                target,
                                lambda: self.stop_event.is_set() or generation != self.worker_generation,
                            )

                    last_target = target
                    self.events.put(("reading", (value, target)))

            except Exception as exc:
                logging.exception("Brightness update failed")
                self.events.put(("status", ("Update failed", str(exc), "error")))

            self.stop_event.wait(self.power_aware_interval(interval))

    @staticmethod
    def power_aware_interval(base_interval):
        """Reduce webcam wakeups while the laptop is running on battery."""
        try:
            class SystemPowerStatus(ctypes.Structure):
                _fields_ = [
                    ("ACLineStatus", ctypes.c_ubyte),
                    ("BatteryFlag", ctypes.c_ubyte),
                    ("BatteryLifePercent", ctypes.c_ubyte),
                    ("Reserved", ctypes.c_ubyte),
                    ("BatteryLifeTime", ctypes.c_ulong),
                    ("BatteryFullLifeTime", ctypes.c_ulong),
                ]

            status = SystemPowerStatus()

            if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
                if status.ACLineStatus == 0:
                    return max(base_interval * 2.5, 300.0)
        except Exception:
            pass

        return base_interval

    def test_once(self):
        if self.testing:
            return

        try:
            camera = int(self.camera_var.get())
        except ValueError:
            self.show_toast("Enter a valid camera number.", "error")
            return

        self.testing = True
        self.test_btn.configure(state="disabled")
        self.set_status("Testing camera", "Capturing a brief sample", "testing")

        threading.Thread(target=self._test_worker, args=(camera,), daemon=True).start()

    def _test_worker(self, camera):
        try:
            with self.camera_lock:
                value = engine.sample_camera(camera)

            if value is None:
                self.events.put(("test_error", "Camera not found. Check camera number and Windows permissions."))
            else:
                self.events.put(("test_done", (value, engine.value_to_brightness(value))))
        except Exception as exc:
            self.events.put(("test_error", str(exc)))

    def process_events(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()

                if kind == "reading":
                    value, target = payload
                    self.last_value_var.set(f"{value:.1f}")
                    self.last_target_var.set(f"{target}%")
                    self.progress.set(target / 100)
                    self.last_update_var.set(datetime.now().strftime("%H:%M:%S"))

                    if self.is_running:
                        self.set_status("Running", "Monitoring ambient light", "running")

                elif kind == "status":
                    self.set_status(*payload)

                elif kind == "test_done":
                    value, target = payload
                    self.last_value_var.set(f"{value:.1f}")
                    self.last_target_var.set(f"{target}%")
                    self.progress.set(target / 100)
                    self.last_update_var.set(datetime.now().strftime("%H:%M:%S"))

                    self.testing = False
                    self.test_btn.configure(state="normal")
                    self.set_status("Test complete", "Camera sample captured", "success")
                    self.show_toast(f"Ambient {value:.1f} -> Target {target}%", "success")

                elif kind == "test_error":
                    self.testing = False
                    self.test_btn.configure(state="normal")
                    self.set_status("Camera error", str(payload), "error")
                    self.show_toast(str(payload), "error")

                elif kind == "tray":
                    action = {
                        "show": self.show_window,
                        "start": self.start,
                        "stop": self.stop,
                        "quit": self.quit_app,
                    }.get(payload)

                    if callable(action):
                        action()

        except queue.Empty:
            pass

        self.after(150, self.process_events)

    def on_theme_mode(self, _value: str) -> None:
        self.apply_theme()

    def detect_theme(self) -> str:
        if self.theme_mode_var.get() == "Auto":
            return get_auto_theme()
        return get_windows_theme()

    def apply_theme(self) -> None:
        theme = self.detect_theme()

        if theme != self.current_theme:
            self.current_theme = theme
            ctk.set_appearance_mode(theme)
            self.configure(fg_color=PAGE_BG)

    def theme_tick(self) -> None:
        self.apply_theme()
        self.after(8000, self.theme_tick)

    def startup_enabled(self):
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_KEY) as key:
                winreg.QueryValueEx(key, APP_NAME)
            return True
        except (ImportError, FileNotFoundError, OSError):
            return False

    @staticmethod
    def startup_command():
        if getattr(sys, "frozen", False):
            return f'"{sys.executable}" {AUTOSTART_FLAG}'

        return f'"{Path(sys.executable).with_name("pythonw.exe")}" "{Path(__file__).resolve()}" {AUTOSTART_FLAG}'

    def ensure_startup_command(self):
        if not self.startup_var.get():
            return

        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, self.startup_command())
        except OSError:
            pass

    def toggle_startup(self):
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_KEY, 0, winreg.KEY_SET_VALUE) as key:
                if self.startup_var.get():
                    winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, self.startup_command())
                else:
                    try:
                        winreg.DeleteValue(key, APP_NAME)
                    except FileNotFoundError:
                        pass

            self.show_toast("Startup setting updated", "success")
        except OSError as exc:
            self.startup_var.set(False)
            self.show_toast(str(exc), "error")

    def start_tray(self):
        if pystray is None:
            return

        menu = pystray.Menu(
            pystray.MenuItem("Show", lambda *_: self.events.put(("tray", "show")), default=True),
            pystray.MenuItem("Start automation", lambda *_: self.events.put(("tray", "start"))),
            pystray.MenuItem("Stop automation", lambda *_: self.events.put(("tray", "stop"))),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda *_: self.events.put(("tray", "quit"))),
        )

        self.tray_icon = pystray.Icon(
            APP_NAME,
            self.app_icon.resize((64, 64), LANCZOS),
            APP_NAME,
            menu,
        )

        self.tray_thread = threading.Thread(target=self.tray_icon.run, daemon=True)
        self.tray_thread.start()

    def launch_autostart(self):
        self.start()
        if self.tray_icon is not None:
            self.withdraw()

    def show_window(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    def quit_app(self):
        self.close_to_tray_var.set(False)
        self.on_close()

    def on_close(self):
        if self.close_to_tray_var.get() and self.tray_icon is not None:
            self.withdraw()
            return

        self.stop_event.set()

        if self.tray_icon:
            try:
                self.tray_icon.stop()
            except Exception:
                pass

        self.destroy()


def main():
    ctk.set_default_color_theme("blue")
    app = BrightnessApp()

    if AUTOSTART_FLAG in sys.argv:
        app.withdraw()
        app.after(400, app.launch_autostart)

    app.mainloop()


if __name__ == "__main__":
    main()
