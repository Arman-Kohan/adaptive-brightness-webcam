"""Modern adaptive brightness webcam app with iOS-style Liquid Glass UI."""
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
    from PIL import Image, ImageDraw, ImageFilter, ImageTk
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

# ------------------------------------------------------------
# iOS-style theme tokens
# ------------------------------------------------------------
ACCENT = "#0A84FF"
ACCENT_HOVER = "#0069D0"
SUCCESS = "#30D158"
WARNING = "#FF9F0A"
DANGER = "#FF453A"

PAGE_BG = ("#F2F2F7", "#08080C")
CARD_BG = ("#FFFFFF", "#1C1C1F")
CARD_BORDER = ("#E6E6EB", "#2D2D33")

SUBTLE_BG = ("#F8F8FC", "#242429")
SUBTLE_HOVER = ("#EDEDF2", "#2F2F36")
FIELD_BG = ("#FFFFFF", "#2E2E35")

TEXT_PRIMARY = ("#151518", "#F5F5F7")
TEXT_SECONDARY = ("#7D7D84", "#9B9BA1")

TRACK_OFF = ("#E4E4E9", "#3A3A3D")

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

    # ------------------------------------------------------------
    # iOS Liquid Glass helpers
    # ------------------------------------------------------------
    def enable_ios_glass_background(self):
        self._bg_label = ctk.CTkLabel(self, text="")
        self._bg_label.place(x=0, y=0, relwidth=1, relheight=1)
        self._bg_label.lower()

        self._bg_job = None
        self._bg_size = None

        self.bind("<Configure>", self._on_window_configure, add="+")
        self._refresh_glass_background()

    def _on_window_configure(self, event=None):
        self._schedule_glass_refresh()

    def _schedule_glass_refresh(self):
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return

        if getattr(self, "_bg_job", None):
            try:
                self.after_cancel(self._bg_job)
            except Exception:
                pass

        self._bg_job = self.after(160, self._refresh_glass_background)

    def _refresh_glass_background(self):
        try:
            if not self.winfo_exists() or not hasattr(self, "_bg_label"):
                return
        except Exception:
            return

        w = max(900, self.winfo_width())
        h = max(640, self.winfo_height())
        key = (w, h, self.current_theme)

        if getattr(self, "_bg_size", None) == key:
            return

        self._bg_size = key
        image = self.create_ios_wallpaper(w, h, self.current_theme)
        self._bg_photo = ImageTk.PhotoImage(image)

        self._bg_label.configure(image=self._bg_photo)
        self._bg_label.lower()

    def create_ios_wallpaper(self, width, height, theme):
        width = max(320, int(width))
        height = max(320, int(height))

        # Render at a smaller size for performance, then upscale.
        rw = min(width, 1600)
        rh = min(height, 1200)

        dark = theme == "dark"

        top = (17, 19, 30) if dark else (247, 248, 252)
        bottom = (5, 7, 12) if dark else (221, 226, 239)

        base = Image.new("RGBA", (rw, rh), top + (255,))
        draw = ImageDraw.Draw(base)

        for y in range(rh):
            t = y / max(1, rh - 1)
            color = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
            draw.line((0, y, rw, y), fill=color + (255,))

        overlay = Image.new("RGBA", (rw, rh), (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)

        if dark:
            blobs = (
                (-0.14, 0.00, 0.42, (38, 96, 240, 74)),
                (0.92, -0.10, 0.34, (80, 200, 255, 50)),
                (0.18, 1.02, 0.56, (110, 86, 240, 58)),
                (1.08, 0.84, 0.34, (255, 69, 58, 36)),
            )
        else:
            blobs = (
                (-0.14, -0.04, 0.46, (10, 132, 255, 46)),
                (0.96, -0.12, 0.38, (120, 210, 255, 42)),
                (0.12, 1.08, 0.58, (175, 82, 222, 34)),
                (1.10, 0.78, 0.32, (255, 159, 10, 24)),
            )

        for cx, cy, r, fill in blobs:
            x0 = int((cx - r) * rw)
            y0 = int((cy - r) * rh)
            x1 = int((cx + r) * rw)
            y1 = int((cy + r) * rh)
            od.ellipse((x0, y0, x1, y1), fill=fill)

        overlay = overlay.filter(ImageFilter.GaussianBlur(radius=max(34, min(rw, rh) // 8)))
        base.paste(overlay, (0, 0), overlay)

        if (rw, rh) != (width, height):
            base = base.resize((width, height), LANCZOS)

        return base.convert("RGB")

    # ------------------------------------------------------------
    # iOS-style widgets
    # ------------------------------------------------------------
    def ios_button(self, parent, text, command=None, kind="primary", width=170, height=48, **kwargs):
        styles = {
            "primary": {
                "fg_color": ACCENT,
                "hover_color": ACCENT_HOVER,
                "text_color": "#FFFFFF",
                "border_width": 0,
            },
            "secondary": {
                "fg_color": SUBTLE_BG,
                "hover_color": SUBTLE_HOVER,
                "text_color": TEXT_PRIMARY,
                "border_width": 1,
                "border_color": CARD_BORDER,
            },
            "outline": {
                "fg_color": "transparent",
                "hover_color": ("#EAF3FF", "#1D2735"),
                "text_color": ACCENT,
                "border_width": 2,
                "border_color": ACCENT,
            },
        }

        style = styles.get(kind, styles["secondary"]).copy()
        style.update(kwargs)

        return ctk.CTkButton(
            parent,
            text=text,
            command=command,
            width=width,
            height=height,
            corner_radius=16,
            font=("Segoe UI", 11, "bold"),
            **style,
        )

    def ios_group(self, parent):
        group = ctk.CTkFrame(
            parent,
            corner_radius=22,
            fg_color=SUBTLE_BG,
            border_width=1,
            border_color=CARD_BORDER,
        )
        group.pack(fill="x", pady=(0, 14))

        inner = ctk.CTkFrame(group, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=18, pady=16)
        return inner

    def ios_section_title(self, parent, text):
        ctk.CTkLabel(
            parent,
            text=text.upper(),
            font=("Segoe UI", 9, "bold"),
            text_color=TEXT_SECONDARY,
        ).pack(anchor="w", pady=(0, 10))

    def ios_hint(self, parent, text):
        ctk.CTkLabel(
            parent,
            text=text,
            font=("Segoe UI", 10),
            text_color=TEXT_SECONDARY,
            wraplength=520,
            justify="left",
        ).pack(anchor="w", pady=(8, 0))

    def ios_switch_row(self, parent, title, subtitle, variable, command=None):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(0, 12))

        left = ctk.CTkFrame(row, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(
            left,
            text=title,
            font=("Segoe UI", 11, "bold"),
            text_color=TEXT_PRIMARY,
        ).pack(anchor="w")

        if subtitle:
            ctk.CTkLabel(
                left,
                text=subtitle,
                font=("Segoe UI", 10),
                text_color=TEXT_SECONDARY,
            ).pack(anchor="w", pady=(3, 0))

        ctk.CTkSwitch(
            row,
            text="",
            variable=variable,
            command=command,
            width=56,
            height=32,
            corner_radius=18,
            progress_color=SUCCESS,
            fg_color=TRACK_OFF,
            button_color="#FFFFFF",
            button_hover_color="#E9E9EE",
        ).pack(side="right", pady=(2, 0))

        return row

    # ------------------------------------------------------------
    # UI
    # ------------------------------------------------------------
    def build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=30, pady=(26, 14))
        header.grid_columnconfigure(1, weight=1)

        self._header_icon = ctk.CTkImage(
            light_image=self.app_icon,
            dark_image=self.app_icon,
            size=(58, 58),
        )

        icon_badge = ctk.CTkFrame(
            header,
            width=80,
            height=80,
            corner_radius=26,
            fg_color=CARD_BG,
            border_width=1,
            border_color=CARD_BORDER,
        )
        icon_badge.grid(row=0, column=0, rowspan=2, padx=(0, 18))
        icon_badge.grid_propagate(False)

        ctk.CTkLabel(
            icon_badge,
            image=self._header_icon,
            text="",
        ).place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(
            header,
            text="Adaptive Brightness",
            font=("Segoe UI", 27, "bold"),
            text_color=TEXT_PRIMARY,
        ).grid(row=0, column=1, sticky="w")

        ctk.CTkLabel(
            header,
            text="Webcam ambient sensing with an iOS-style UI",
            font=("Segoe UI", 10),
            text_color=TEXT_SECONDARY,
        ).grid(row=1, column=1, sticky="w", pady=(4, 0))

        # Status card
        self.status_card = ctk.CTkFrame(
            self,
            corner_radius=28,
            border_width=1,
            fg_color=CARD_BG,
            border_color=CARD_BORDER,
        )
        self.status_card.grid(row=1, column=0, sticky="ew", padx=30, pady=(8, 14))
        self.build_status(self.status_card)

        # Scrollable content
        try:
            scroll = AutoHideScrollableFrame(
                self,
                fg_color="transparent",
                scrollbar_fg_color="transparent",
                scrollbar_button_color=CARD_BORDER,
                scrollbar_button_hover_color=(ACCENT, ACCENT),
            )
        except TypeError:
            scroll = AutoHideScrollableFrame(self, fg_color="transparent")

        scroll.grid(row=2, column=0, sticky="nsew", padx=24, pady=(0, 0))

        content = ctk.CTkFrame(scroll, fg_color="transparent")
        content.pack(fill="both", expand=True)
        content.grid_columnconfigure(0, weight=11)
        content.grid_columnconfigure(1, weight=9)

        settings = ctk.CTkFrame(
            content,
            corner_radius=28,
            border_width=1,
            fg_color=CARD_BG,
            border_color=CARD_BORDER,
        )
        settings.grid(row=0, column=0, sticky="nsew", padx=(0, 10), pady=(0, 14))

        stats = ctk.CTkFrame(
            content,
            corner_radius=28,
            border_width=1,
            fg_color=CARD_BG,
            border_color=CARD_BORDER,
        )
        stats.grid(row=0, column=1, sticky="nsew", padx=(10, 0), pady=(0, 14))

        self.build_settings(settings)
        self.build_stats(stats)

        # Footer
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=3, column=0, sticky="ew", padx=30, pady=(10, 26))
        footer.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            footer,
            text="Privacy first — no images are stored or uploaded",
            font=("Segoe UI", 10),
            text_color=TEXT_SECONDARY,
        ).grid(row=0, column=0, sticky="w")

        self.toast = ctk.CTkLabel(
            footer,
            text="",
            font=("Segoe UI", 10, "bold"),
            text_color=ACCENT,
        )
        self.toast.grid(row=0, column=1, sticky="w", padx=18)

        self.test_btn = self.ios_button(
            footer,
            "Test camera",
            self.test_once,
            kind="outline",
            width=150,
            height=46,
        )
        self.test_btn.grid(row=0, column=2, sticky="e")

        # Liquid glass background
        if not getattr(self, "_ios_bg_enabled", False):
            self._ios_bg_enabled = True
            self.enable_ios_glass_background()

        refresh = getattr(scroll, "_ah_refresh", None)
        if callable(refresh):
            self.after(180, refresh)

    def build_status(self, parent) -> None:
        inner = ctk.CTkFrame(parent, fg_color="transparent")
        inner.pack(fill="x", padx=26, pady=24)

        left = ctk.CTkFrame(inner, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True)

        row = ctk.CTkFrame(left, fg_color="transparent")
        row.pack(anchor="w")

        self.status_dot = ctk.CTkLabel(
            row,
            text="●",
            font=("Segoe UI", 16),
            text_color=TEXT_SECONDARY,
        )
        self.status_dot.pack(side="left", padx=(0, 10))

        self.status_label = ctk.CTkLabel(
            row,
            text="Ready to start",
            font=("Segoe UI", 17, "bold"),
            text_color=TEXT_PRIMARY,
        )
        self.status_label.pack(side="left")

        self.status_sub = ctk.CTkLabel(
            left,
            text="Start automation to adjust brightness automatically.",
            font=("Segoe UI", 10),
            text_color=TEXT_SECONDARY,
        )
        self.status_sub.pack(anchor="w", pady=(6, 0))

        buttons = ctk.CTkFrame(inner, fg_color="transparent")
        buttons.pack(side="right")

        self.start_btn = self.ios_button(
            buttons,
            "Start automation",
            self.start,
            kind="primary",
            width=190,
            height=48,
        )
        self.start_btn.pack(side="left", padx=(0, 10))

        self.stop_btn = self.ios_button(
            buttons,
            "Stop",
            self.stop,
            kind="secondary",
            width=105,
            height=48,
        )
        self.stop_btn.configure(state="disabled")
        self.stop_btn.pack(side="left")

    def card_header(self, parent, title, subtitle):
        ctk.CTkLabel(
            parent,
            text=title,
            font=("Segoe UI", 18, "bold"),
            text_color=TEXT_PRIMARY,
        ).pack(anchor="w")

        ctk.CTkLabel(
            parent,
            text=subtitle,
            font=("Segoe UI", 10),
            text_color=TEXT_SECONDARY,
        ).pack(anchor="w", pady=(5, 0))

    def build_settings(self, parent) -> None:
        body = ctk.CTkFrame(parent, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=24, pady=24)

        self.card_header(
            body,
            "Settings",
            "Camera, interval, appearance, startup, and tray behavior.",
        )

        # Camera
        camera_group = self.ios_group(body)
        self.ios_section_title(camera_group, "Camera")

        cam_row = ctk.CTkFrame(camera_group, fg_color="transparent")
        cam_row.pack(fill="x")

        ctk.CTkLabel(
            cam_row,
            text="Source",
            font=("Segoe UI", 11, "bold"),
            text_color=TEXT_PRIMARY,
        ).pack(side="left")

        ctk.CTkOptionMenu(
            cam_row,
            values=[str(i) for i in range(6)],
            variable=self.camera_var,
            width=110,
            height=40,
            corner_radius=14,
            fg_color=FIELD_BG,
            button_color=ACCENT,
            button_hover_color=ACCENT_HOVER,
            text_color=TEXT_PRIMARY,
            font=("Segoe UI", 10),
        ).pack(side="right")

        self.ios_hint(
            camera_group,
            "0 is usually the built-in webcam. The camera only wakes for a short sample.",
        )

        # Interval
        interval_group = self.ios_group(body)
        self.ios_section_title(interval_group, "Check interval")

        interval_row = ctk.CTkFrame(interval_group, fg_color="transparent")
        interval_row.pack(fill="x")

        ctk.CTkLabel(
            interval_row,
            text="Seconds",
            font=("Segoe UI", 11, "bold"),
            text_color=TEXT_PRIMARY,
        ).pack(side="left")

        ctk.CTkEntry(
            interval_row,
            textvariable=self.interval_var,
            width=88,
            height=42,
            corner_radius=14,
            fg_color=FIELD_BG,
            border_color=CARD_BORDER,
            text_color=TEXT_PRIMARY,
            font=("Segoe UI", 12, "bold"),
            justify="center",
        ).pack(side="right")

        ctk.CTkSegmentedButton(
            interval_group,
            values=["120", "300", "600"],
            variable=self.preset_var,
            command=lambda value: self.interval_var.set(value),
            height=42,
            corner_radius=14,
            fg_color=FIELD_BG,
            selected_color=ACCENT,
            selected_hover_color=ACCENT_HOVER,
            text_color=TEXT_PRIMARY,
            font=("Segoe UI", 10, "bold"),
        ).pack(fill="x", pady=(12, 0))

        self.ios_hint(
            interval_group,
            "Minimum 30 seconds. Battery mode automatically adds extra delay.",
        )

        # Appearance
        appearance_group = self.ios_group(body)
        self.ios_section_title(appearance_group, "Appearance")

        theme_row = ctk.CTkFrame(appearance_group, fg_color="transparent")
        theme_row.pack(fill="x")

        ctk.CTkLabel(
            theme_row,
            text="App theme",
            font=("Segoe UI", 11, "bold"),
            text_color=TEXT_PRIMARY,
        ).pack(side="left")

        ctk.CTkSegmentedButton(
            theme_row,
            values=["Auto", "Windows"],
            variable=self.theme_mode_var,
            command=self.on_theme_mode,
            width=200,
            height=40,
            corner_radius=14,
            fg_color=FIELD_BG,
            selected_color=ACCENT,
            selected_hover_color=ACCENT_HOVER,
            text_color=TEXT_PRIMARY,
            font=("Segoe UI", 10, "bold"),
        ).pack(side="right")

        self.ios_hint(
            appearance_group,
            "Auto follows day/night. Windows follows the system theme.",
        )

        # Behavior
        behavior_group = self.ios_group(body)
        self.ios_section_title(behavior_group, "Behavior")

        self.ios_switch_row(
            behavior_group,
            "Launch with Windows",
            "Start automatically when you sign in",
            self.startup_var,
            self.toggle_startup,
        )

        self.ios_switch_row(
            behavior_group,
            "Close to tray",
            "Keep running quietly in the system tray",
            self.close_to_tray_var,
        )

        note = ctk.CTkFrame(
            body,
            corner_radius=18,
            fg_color=SUBTLE_BG,
            border_width=1,
            border_color=CARD_BORDER,
        )
        note.pack(fill="x", side="bottom", pady=(4, 0))

        ctk.CTkLabel(
            note,
            text="The camera is active only for a brief sample. No frames are saved.",
            font=("Segoe UI", 10),
            text_color=TEXT_SECONDARY,
            wraplength=480,
            justify="left",
        ).pack(anchor="w", padx=16, pady=12)

    def build_stats(self, parent) -> None:
        body = ctk.CTkFrame(parent, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=24, pady=24)

        self.card_header(
            body,
            "Live readings",
            "Latest light estimate and target brightness.",
        )

        content = ctk.CTkFrame(body, fg_color="transparent")
        content.pack(fill="both", expand=True, pady=(18, 0))

        self.metric_card(content, "AMBIENT LIGHT", self.last_value_var, "camera value")
        target = self.metric_card(content, "TARGET BRIGHTNESS", self.last_target_var, "display level")

        self.progress = ctk.CTkProgressBar(
            target,
            height=12,
            corner_radius=6,
            progress_color=ACCENT,
            fg_color=("#E7ECF6", "#2A3140"),
        )
        self.progress.pack(fill="x", pady=(14, 0))
        self.progress.set(0)

        meta = ctk.CTkFrame(target, fg_color="transparent")
        meta.pack(fill="x", pady=(12, 0))

        ctk.CTkLabel(
            meta,
            text="LAST UPDATE",
            font=("Segoe UI", 9, "bold"),
            text_color=TEXT_SECONDARY,
        ).pack(side="left")

        ctk.CTkLabel(
            meta,
            textvariable=self.last_update_var,
            font=("Segoe UI", 12, "bold"),
            text_color=TEXT_PRIMARY,
        ).pack(side="right")

        tip = ctk.CTkFrame(
            body,
            corner_radius=18,
            fg_color=SUBTLE_BG,
            border_width=1,
            border_color=CARD_BORDER,
        )
        tip.pack(fill="x", side="bottom", pady=(10, 0))

        ctk.CTkLabel(
            tip,
            text="Keep the webcam uncovered and facing the room for best results.",
            font=("Segoe UI", 10),
            text_color=TEXT_SECONDARY,
            wraplength=320,
            justify="left",
        ).pack(anchor="w", padx=16, pady=12)

    def metric_card(self, parent, title, variable, unit):
        card = ctk.CTkFrame(
            parent,
            corner_radius=22,
            fg_color=SUBTLE_BG,
            border_width=1,
            border_color=CARD_BORDER,
        )
        card.pack(fill="x", pady=(0, 12))

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=20, pady=18)

        ctk.CTkLabel(
            inner,
            text=title,
            font=("Segoe UI", 9, "bold"),
            text_color=TEXT_SECONDARY,
        ).pack(anchor="w")

        row = ctk.CTkFrame(inner, fg_color="transparent")
        row.pack(fill="x", pady=(8, 0))

        ctk.CTkLabel(
            row,
            textvariable=variable,
            font=("Segoe UI", 30, "bold"),
            text_color=TEXT_PRIMARY,
        ).pack(side="left")

        ctk.CTkLabel(
            row,
            text=unit,
            font=("Segoe UI", 10),
            text_color=TEXT_SECONDARY,
        ).pack(side="left", padx=12, pady=(12, 0))

        return inner

    def show_toast(self, message, mode="info"):
        color = {
            "success": SUCCESS,
            "error": DANGER,
            "warning": WARNING,
        }.get(mode, ACCENT)

        self.toast.configure(text=message, text_color=color)

        if self.toast_job:
            try:
                self.after_cancel(self.toast_job)
            except Exception:
                pass

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

    # ------------------------------------------------------------
    # App logic
    # ------------------------------------------------------------
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
            self._refresh_glass_background()

    def theme_tick(self) -> None:
        self.apply_theme()
        self.after(8000, self.theme_tick)

    # ------------------------------------------------------------
    # Startup / tray
    # ------------------------------------------------------------
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
