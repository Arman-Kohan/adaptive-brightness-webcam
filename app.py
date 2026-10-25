"""Modern adaptive brightness webcam app with iOS 27-style UI."""
from __future__ import annotations

import ctypes
import logging
import os
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

# ------------------------------------------------------------
# iOS 27-style design tokens
# ------------------------------------------------------------
ACCENT = ("#0A6CFF", "#5B8CFF")
ACCENT_HOVER = ("#0859DB", "#4978EA")
ACCENT_TEXT = ("#0859DB", "#9DB9FF")
SUCCESS = ("#188A55", "#3DDC84")
WARNING = ("#B36B00", "#FFC76B")
DANGER = ("#E24C4B", "#FF6961")
DANGER_HOVER = ("#C43D3C", "#E85C58")
APP_BG = ("#F6F8FE", "#0A111F")
PAGE_BG = APP_BG
CARD_BG = APP_BG
CARD_BORDER = ("#D8E2F5", "#243250")
FIELD_BG = ("#EDF2FB", "#0B1322")
FIELD_BORDER = ("#C9D6EF", "#2B3C63")
TEXT_PRIMARY = ("#0F1828", "#F4F7FF")
TEXT_SECONDARY = ("#5B6B8A", "#93A3C6")
SEG_BG = FIELD_BG
SEG_HOVER = ("#E5EDFF", "#182541")
TRACK_OFF = ("#A9B7D2", "#33415E")
DISABLED_BG = ("#E6EAF3", "#1B2438")
DISABLED_TEXT = ("#9AA7BF", "#5F6C88")
DISABLED_BORDER = ("#D3DBEA", "#2B3854")
OUTLINE_HOVER = ("#E9F0FF", "#18274A")
SUBTLE_BG = FIELD_BG

try:
    LANCZOS = Image.Resampling.LANCZOS
except AttributeError:
    LANCZOS = Image.LANCZOS

_ICON_MASTER = None


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


def enable_windows_enhancements() -> None:
    """Improve Windows taskbar behavior and DPI rendering."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_NAME)
    except Exception:
        pass


# ------------------------------------------------------------
# High-quality app icon
# ------------------------------------------------------------
def _lerp_color(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _build_icon_master(size: int = 1024) -> Image.Image:
    """
    Build a clean, high-contrast master icon.

    Design choices for taskbar sharpness:
     - larger ring
     - larger moon
     - no tiny stars
     - strong dark lens behind ring/moon
    """
    size = max(256, int(size))

    gradient = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(gradient)

    top = (24, 62, 168)
    mid = (13, 29, 86)
    bottom = (6, 11, 34)

    for y in range(size):
        t = y / max(1, size - 1)
        if t < 0.55:
            color = _lerp_color(top, mid, t / 0.55)
        else:
            color = _lerp_color(mid, bottom, (t - 0.55) / 0.45)
        draw.line((0, y, size - 1, y), fill=color + (255,))

    mask = Image.new("L", (size, size), 0)
    mask_draw = ImageDraw.Draw(mask)
    radius = int(size * 0.223)

    try:
        mask_draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    except AttributeError:
        mask_draw.rectangle((0, 0, size - 1, size - 1), fill=255)

    icon = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    icon.paste(gradient, (0, 0), mask)
    draw = ImageDraw.Draw(icon)

    # Dark lens for contrast.
    lens_inset = int(size * 0.145)
    draw.ellipse(
        (lens_inset, lens_inset, size - lens_inset, size - lens_inset),
        fill=(7, 13, 34, 255),
    )

    # Bright ring.
    ring_inset = int(size * 0.155)
    draw.ellipse(
        (ring_inset, ring_inset, size - ring_inset, size - ring_inset),
        outline=(150, 210, 255, 255),
        width=max(3, int(size * 0.055)),
    )

    # Crescent moon.
    moon_mask = Image.new("L", (size, size), 0)
    md = ImageDraw.Draw(moon_mask)
    md.ellipse(
        (
            int(size * 0.32),
            int(size * 0.32),
            int(size * 0.70),
            int(size * 0.70),
        ),
        fill=255,
    )
    md.ellipse(
        (
            int(size * 0.47),
            int(size * 0.22),
            int(size * 0.86),
            int(size * 0.60),
        ),
        fill=0,
    )

    moon_layer = Image.new("RGBA", (size, size), (255, 205, 105, 255))
    icon.paste(moon_layer, (0, 0), moon_mask)

    # Small highlight dot.
    draw = ImageDraw.Draw(icon)
    draw.ellipse(
        (
            int(size * 0.255),
            int(size * 0.245),
            int(size * 0.295),
            int(size * 0.285),
        ),
        fill=(215, 238, 255, 205),
    )

    return icon


def _get_icon_master(size: int = 1024) -> Image.Image:
    global _ICON_MASTER
    if _ICON_MASTER is None:
        _ICON_MASTER = _build_icon_master(size)
    return _ICON_MASTER


def create_app_icon(size: int = 256) -> Image.Image:
    size = max(16, int(size))
    return _get_icon_master(1024).resize((size, size), LANCZOS)


def save_app_icon() -> Path | None:
    try:
        path = Path(tempfile.gettempdir()) / f"adaptive_brightness_webcam_{os.getpid()}.ico"
        _get_icon_master(1024).save(
            path,
            format="ICO",
            sizes=[
                (16, 16),
                (20, 20),
                (24, 24),
                (32, 32),
                (40, 40),
                (48, 48),
                (64, 64),
                (96, 96),
                (128, 128),
                (256, 256),
            ],
        )
        return path
    except Exception:
        return None


class AutoHideScrollableFrame(ctk.CTkScrollableFrame):
    """CTkScrollableFrame that hides the scrollbar when content fits."""

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
        self.configure(fg_color=APP_BG)

        # Icons
        self.app_icon = create_app_icon(512)
        self._icon_path = save_app_icon()
        self._icon_fallback_images = []
        self._apply_window_icon()

        # App state
        self.worker_thread = None
        self.stop_event = threading.Event()
        self.camera_lock = threading.Lock()
        self.brightness_lock = threading.Lock()
        self.events: queue.Queue = queue.Queue()
        self.worker_generation = 0
        self.testing = False
        self.is_running = False
        self.tray_icon = None
        self.tray_thread = None
        self.tray_toggle_item = None
        self.toast_job = None
        self.camera_chips = []

        # New: stop work while Windows is asleep.
        self.power_watcher = engine.PowerWatcher()

        # Settings variables
        self.camera_var = ctk.StringVar(value="0")
        self.interval_var = ctk.StringVar(value="120")
        self.preset_var = ctk.StringVar(value="120")
        self.startup_var = ctk.BooleanVar(value=self.startup_enabled())
        self.close_to_tray_var = ctk.BooleanVar(value=True)

        # Stats variables
        self.last_value_var = ctk.StringVar(value="-")
        self.last_target_var = ctk.StringVar(value="-")
        self.last_update_var = ctk.StringVar(value="-")

        self.build_ui()
        self.center_window()

        self.after(150, self.process_events)
        self.after(500, self.apply_theme)
        self.after(8000, self.theme_tick)

        # Some Windows taskbars only pick the icon reliably after mapping.
        self.after(250, self._apply_window_icon)
        self.after(1200, self._apply_window_icon)

        self.start_tray()
        self.ensure_startup_command()

    def center_window(self) -> None:
        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w = min(1120, max(860, int(sw * 0.76), sw - 24))
        h = min(820, max(600, int(sh * 0.82), sh - 90))
        self.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 2 - 16)}")

    def _apply_window_icon(self) -> None:
        icon_ok = False

        if getattr(self, "_icon_path", None):
            try:
                self.update_idletasks()
                self.iconbitmap(str(self._icon_path))
                icon_ok = True
            except Exception:
                icon_ok = False

            try:
                self.iconbitmap(bitmap=str(self._icon_path), default=True)
            except Exception:
                pass

        if not icon_ok:
            if not self._icon_fallback_images:
                for size in (16, 24, 32, 48, 64, 128, 256):
                    try:
                        self._icon_fallback_images.append(
                            ImageTk.PhotoImage(create_app_icon(size))
                        )
                    except Exception:
                        pass

            if self._icon_fallback_images:
                try:
                    self.iconphoto(True, *self._icon_fallback_images)
                except Exception:
                    pass

    # ------------------------------------------------------------
    # Safe widget helpers
    # ------------------------------------------------------------
    def _trace(self, variable, callback):
        try:
            variable.trace_add("write", callback)
        except AttributeError:
            try:
                variable.trace("w", callback)
            except Exception:
                pass

    def make_frame(self, parent, bg_color=APP_BG, **kwargs):
        """Create a CTkFrame with explicit bg_color for clean rounded corners."""
        try:
            return ctk.CTkFrame(parent, bg_color=bg_color, **kwargs)
        except TypeError:
            return ctk.CTkFrame(parent, **kwargs)

    def _make_ctk_button(self, parent, **kwargs):
        try:
            return ctk.CTkButton(parent, **kwargs)
        except TypeError:
            for key in ("text_color_disabled", "border_color", "corner_radius"):
                kwargs.pop(key, None)
            return ctk.CTkButton(parent, **kwargs)

    def _safe_configure(self, widget, **kwargs):
        try:
            widget.configure(**kwargs)
        except Exception:
            for key in ("text_color_disabled", "border_color", "corner_radius"):
                kwargs.pop(key, None)
            try:
                widget.configure(**kwargs)
            except Exception:
                pass

    def ui_button(self, parent, text, command=None, kind="primary", width=170, height=48, **kwargs):
        styles = {
            "primary": {
                "fg_color": ACCENT,
                "hover_color": ACCENT_HOVER,
                "text_color": "#FFFFFF",
                "border_width": 0,
                "text_color_disabled": "#FFFFFF",
            },
            "danger_filled": {
                "fg_color": DANGER,
                "hover_color": DANGER_HOVER,
                "text_color": "#FFFFFF",
                "border_width": 0,
                "text_color_disabled": "#FFFFFF",
            },
            "outline": {
                "fg_color": "transparent",
                "hover_color": OUTLINE_HOVER,
                "text_color": ACCENT_TEXT,
                "border_width": 1,
                "border_color": ACCENT,
                "text_color_disabled": ACCENT_TEXT,
            },
        }

        style = styles.get(kind, styles["primary"]).copy()
        style.update(kwargs)

        base = dict(
            text=text,
            command=command,
            width=width,
            height=height,
            corner_radius=20,
            font=("Segoe UI", 11, "bold"),
        )
        base.update(style)

        return self._make_ctk_button(parent, **base)

    def make_switch(self, parent, variable, command=None):
        base = dict(
            text="",
            variable=variable,
            command=command,
            width=54,
            height=30,
            corner_radius=17,
            progress_color=SUCCESS,
            fg_color=TRACK_OFF,
            button_color=("#FFFFFF", "#F5F8FF"),
            button_hover_color=("#E8EEFF", "#DCE6FF"),
        )

        try:
            return ctk.CTkSwitch(
                parent,
                border_width=1,
                border_color=("#98A9CB", "#46587F"),
                **base,
            )
        except TypeError:
            try:
                return ctk.CTkSwitch(parent, **base)
            except TypeError:
                base.pop("corner_radius", None)
                return ctk.CTkSwitch(parent, **base)

    def ios_section_title(self, parent, text):
        ctk.CTkLabel(
            parent,
            text=text.upper(),
            font=("Segoe UI", 9, "bold"),
            text_color=TEXT_SECONDARY,
        ).pack(anchor="w", pady=(0, 8))

    def ios_hint(self, parent, text):
        ctk.CTkLabel(
            parent,
            text=text,
            font=("Segoe UI", 9),
            text_color=TEXT_SECONDARY,
            wraplength=520,
            justify="left",
        ).pack(anchor="w", pady=(6, 0))

    def ios_divider(self, parent):
        self.make_frame(
            parent,
            bg_color=APP_BG,
            height=1,
            fg_color=CARD_BORDER,
        ).pack(fill="x", pady=18)

    def ios_row(self, parent, label=None):
        row = self.make_frame(parent, bg_color=APP_BG, fg_color="transparent")
        row.pack(fill="x", pady=(0, 8))
        if label:
            ctk.CTkLabel(
                row,
                text=label,
                font=("Segoe UI", 11, "bold"),
                text_color=TEXT_PRIMARY,
            ).pack(side="left")
        return row

    def ios_switch_row(self, parent, title, subtitle, variable, command=None):
        row = self.make_frame(parent, bg_color=APP_BG, fg_color="transparent")
        row.pack(fill="x", pady=(0, 12))

        left = self.make_frame(row, bg_color=APP_BG, fg_color="transparent")
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
                font=("Segoe UI", 9),
                text_color=TEXT_SECONDARY,
            ).pack(anchor="w", pady=(2, 0))

        self.make_switch(row, variable, command).pack(side="right", pady=(2, 0))
        return row

    def ios_segmented(self, parent, values, variable, command=None, width=None, height=40):
        frame = self.make_frame(
            parent,
            bg_color=APP_BG,
            corner_radius=14,
            fg_color=SEG_BG,
            border_width=1,
            border_color=CARD_BORDER,
        )

        if width is not None:
            frame.configure(width=width, height=height + 6)
            frame.pack_propagate(False)

        frame.grid_rowconfigure(0, weight=1)
        buttons = []

        def select(value):
            if variable.get() != value:
                variable.set(value)
            if callable(command):
                command(value)
            refresh()

        def refresh(*_args):
            current = variable.get()
            for btn, value in buttons:
                if value == current:
                    btn.configure(
                        fg_color=ACCENT,
                        hover_color=ACCENT_HOVER,
                        text_color="#FFFFFF",
                    )
                else:
                    btn.configure(
                        fg_color="transparent",
                        hover_color=SEG_HOVER,
                        text_color=TEXT_SECONDARY,
                    )

        for i, value in enumerate(values):
            frame.grid_columnconfigure(i, weight=1)
            btn = self._make_ctk_button(
                frame,
                text=value,
                height=height - 6,
                corner_radius=11,
                fg_color="transparent",
                hover_color=SEG_HOVER,
                text_color=TEXT_SECONDARY,
                font=("Segoe UI", 10, "bold"),
                command=lambda v=value: select(v),
            )
            btn.grid(row=0, column=i, sticky="nsew", padx=3, pady=3)
            buttons.append((btn, value))

        self._trace(variable, refresh)
        refresh()
        return frame

    # ------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------
    def build_ui(self) -> None:
        self.configure(fg_color=APP_BG)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # Header
        header = self.make_frame(self, bg_color=APP_BG, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=30, pady=(28, 14))
        header.grid_columnconfigure(1, weight=1)

        header_icon = ctk.CTkImage(
            light_image=self.app_icon,
            dark_image=self.app_icon,
            size=(64, 64),
        )
        self._header_icon = header_icon

        ctk.CTkLabel(
            header,
            image=header_icon,
            text="",
        ).grid(row=0, column=0, rowspan=2, padx=(0, 18))

        ctk.CTkLabel(
            header,
            text="Adaptive Brightness",
            font=("Segoe UI", 28, "bold"),
            text_color=TEXT_PRIMARY,
        ).grid(row=0, column=1, sticky="w")

        ctk.CTkLabel(
            header,
            text="Night-sky ambient sensing, tuned for your display",
            font=("Segoe UI", 10),
            text_color=TEXT_SECONDARY,
        ).grid(row=1, column=1, sticky="w", pady=(4, 0))

        # Status card
        self.status_card = self.make_frame(
            self,
            bg_color=APP_BG,
            corner_radius=30,
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
                fg_color=APP_BG,
                corner_radius=0,
                bg_color=APP_BG,
                scrollbar_fg_color="transparent",
                scrollbar_button_color=CARD_BORDER,
                scrollbar_button_hover_color=ACCENT,
            )
        except TypeError:
            try:
                scroll = AutoHideScrollableFrame(
                    self,
                    fg_color=APP_BG,
                    corner_radius=0,
                    bg_color=APP_BG,
                )
            except TypeError:
                scroll = AutoHideScrollableFrame(self, fg_color=APP_BG)

        scroll.grid(row=2, column=0, sticky="nsew", padx=0, pady=0)

        content = self.make_frame(scroll, bg_color=APP_BG, fg_color="transparent")
        content.pack(fill="both", expand=True)

        inner = self.make_frame(content, bg_color=APP_BG, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=30, pady=(8, 16))

        inner.grid_columnconfigure(0, weight=11)
        inner.grid_columnconfigure(1, weight=9)
        inner.grid_rowconfigure(0, weight=1)

        settings = self.make_frame(
            inner,
            bg_color=APP_BG,
            corner_radius=30,
            border_width=1,
            fg_color=CARD_BG,
            border_color=CARD_BORDER,
        )
        settings.grid(row=0, column=0, sticky="nsew", padx=(0, 12), pady=(0, 14))

        stats = self.make_frame(
            inner,
            bg_color=APP_BG,
            corner_radius=30,
            border_width=1,
            fg_color=CARD_BG,
            border_color=CARD_BORDER,
        )
        stats.grid(row=0, column=1, sticky="nsew", padx=(12, 0), pady=(0, 14))

        self.build_settings(settings)
        self.build_stats(stats)

        # Footer
        footer = self.make_frame(self, bg_color=APP_BG, fg_color="transparent")
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
            text_color=ACCENT_TEXT,
        )
        self.toast.grid(row=0, column=1, sticky="w", padx=18)

        self.test_btn = self.ui_button(
            footer,
            "Test camera",
            self.test_once,
            kind="outline",
            width=160,
            height=46,
        )
        self.test_btn.grid(row=0, column=2, sticky="e")

        refresh = getattr(scroll, "_ah_refresh", None)
        if callable(refresh):
            self.after(180, refresh)

    def build_status(self, parent) -> None:
        inner = self.make_frame(parent, bg_color=APP_BG, fg_color="transparent")
        inner.pack(fill="x", padx=28, pady=26)

        left = self.make_frame(inner, bg_color=APP_BG, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True)

        row = self.make_frame(left, bg_color=APP_BG, fg_color="transparent")
        row.pack(anchor="w")

        self.status_dot = ctk.CTkLabel(
            row,
            text="●",
            font=("Segoe UI", 18),
            text_color=TEXT_SECONDARY,
        )
        self.status_dot.pack(side="left", padx=(0, 12))

        self.status_label = ctk.CTkLabel(
            row,
            text="Ready to start",
            font=("Segoe UI", 18, "bold"),
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

        buttons = self.make_frame(inner, bg_color=APP_BG, fg_color="transparent")
        buttons.pack(side="right")

        self.start_btn = self.ui_button(
            buttons,
            "Start automation",
            self.start,
            kind="primary",
            width=210,
            height=52,
        )
        self.start_btn.pack(side="left", padx=(0, 12))

        self.stop_btn = self.ui_button(
            buttons,
            "Stop",
            self.stop,
            kind="danger_filled",
            width=115,
            height=52,
            state="disabled",
        )
        self.stop_btn.pack(side="left")

        self.refresh_run_controls()

    def refresh_run_controls(self):
        """Make enabled/disabled state of Start and Stop visually obvious."""
        if not hasattr(self, "start_btn") or not hasattr(self, "stop_btn"):
            return

        if self.is_running:
            # Start becomes disabled gray.
            self._safe_configure(
                self.start_btn,
                state="disabled",
                fg_color=DISABLED_BG,
                hover_color=DISABLED_BG,
                text_color=DISABLED_TEXT,
                text_color_disabled=DISABLED_TEXT,
                border_width=1,
                border_color=DISABLED_BORDER,
            )

            # Stop becomes active red.
            self._safe_configure(
                self.stop_btn,
                state="normal",
                fg_color=DANGER,
                hover_color=DANGER_HOVER,
                text_color="#FFFFFF",
                text_color_disabled="#FFFFFF",
                border_width=0,
                border_color=DANGER,
            )
        else:
            # Start becomes active blue.
            self._safe_configure(
                self.start_btn,
                state="normal",
                fg_color=ACCENT,
                hover_color=ACCENT_HOVER,
                text_color="#FFFFFF",
                text_color_disabled="#FFFFFF",
                border_width=0,
                border_color=ACCENT,
            )

            # Stop becomes disabled gray.
            self._safe_configure(
                self.stop_btn,
                state="disabled",
                fg_color=DISABLED_BG,
                hover_color=DISABLED_BG,
                text_color=DISABLED_TEXT,
                text_color_disabled=DISABLED_TEXT,
                border_width=1,
                border_color=DISABLED_BORDER,
            )

    def card_header(self, parent, title, subtitle):
        ctk.CTkLabel(
            parent,
            text=title,
            font=("Segoe UI", 19, "bold"),
            text_color=TEXT_PRIMARY,
        ).pack(anchor="w")

        ctk.CTkLabel(
            parent,
            text=subtitle,
            font=("Segoe UI", 10),
            text_color=TEXT_SECONDARY,
        ).pack(anchor="w", pady=(5, 0))

    def build_settings(self, parent) -> None:
        body = self.make_frame(parent, bg_color=APP_BG, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=26, pady=26)

        self.card_header(
            body,
            "Settings",
            "Camera, interval, appearance, startup, and tray behavior.",
        )

        container = self.make_frame(body, bg_color=APP_BG, fg_color="transparent")
        container.pack(fill="both", expand=True, pady=(20, 0))

        # Camera
        self.ios_section_title(container, "Camera")

        cam_row = self.ios_row(container, "Webcam source")
        self.camera_value_label = ctk.CTkLabel(
            cam_row,
            text=f"Camera {self.camera_var.get()}",
            font=("Segoe UI", 10, "bold"),
            text_color=ACCENT_TEXT,
        )
        self.camera_value_label.pack(side="right")

        self._trace(self.camera_var, self._update_camera_badge)
        self.build_camera_chips(container)

        self.ios_hint(
            container,
            "0 is usually the built-in webcam. The camera only wakes for a short sample.",
        )

        self.ios_divider(container)

        # Interval
        self.ios_section_title(container, "Check interval")

        interval_row = self.ios_row(container, "Seconds")
        ctk.CTkEntry(
            interval_row,
            textvariable=self.interval_var,
            width=92,
            height=42,
            corner_radius=14,
            fg_color=FIELD_BG,
            border_color=FIELD_BORDER,
            border_width=1,
            text_color=TEXT_PRIMARY,
            font=("Segoe UI", 12, "bold"),
            justify="center",
        ).pack(side="right")

        preset_seg = self.ios_segmented(
            container,
            ["120", "300", "600"],
            self.preset_var,
            command=lambda value: self.interval_var.set(value),
        )
        preset_seg.pack(fill="x", pady=(10, 0))

        def _sync_preset(*_args):
            value = self.interval_var.get().strip()
            if value in ("120", "300", "600") and self.preset_var.get() != value:
                self.preset_var.set(value)

        self._trace(self.interval_var, _sync_preset)

        self.ios_hint(
            container,
            "Minimum 30 seconds. Battery mode automatically adds extra delay.",
        )

        self.ios_divider(container)

        # Appearance
        self.ios_section_title(container, "Appearance")

        theme_row = self.ios_row(container, "App theme")
        theme_seg = self.ios_segmented(
            theme_row,
            ["Auto", "Windows"],
            self.theme_mode_var,
            command=self.on_theme_mode,
            width=230,
            height=40,
        )
        theme_seg.pack(side="right")

        self.ios_hint(
            container,
            "Auto follows day/night. Windows follows the system theme.",
        )

        self.ios_divider(container)

        # Behavior
        self.ios_section_title(container, "Behavior")

        self.ios_switch_row(
            container,
            "Launch with Windows",
            "Start automatically when you sign in",
            self.startup_var,
            self.toggle_startup,
        )

        self.ios_switch_row(
            container,
            "Close to tray",
            "Keep running quietly in the system tray",
            self.close_to_tray_var,
        )

        note = self.make_frame(
            body,
            bg_color=APP_BG,
            corner_radius=16,
            fg_color=FIELD_BG,
            border_width=1,
            border_color=FIELD_BORDER,
        )
        note.pack(fill="x", side="bottom", pady=(18, 0))

        ctk.CTkLabel(
            note,
            text="The camera is active only for a brief sample. No frames are saved.",
            font=("Segoe UI", 10),
            text_color=TEXT_SECONDARY,
            wraplength=480,
            justify="left",
        ).pack(anchor="w", padx=16, pady=12)

    def _update_camera_badge(self, *_args):
        try:
            self.camera_value_label.configure(text=f"Camera {self.camera_var.get()}")
        except Exception:
            pass

    def build_camera_chips(self, parent):
        """iOS-like selectable camera grid instead of a dropdown."""
        grid = self.make_frame(parent, bg_color=APP_BG, fg_color="transparent")
        grid.pack(fill="x", pady=(10, 0))

        for col in range(3):
            grid.grid_columnconfigure(col, weight=1)

        self.camera_chips = []

        for index in range(6):
            row, col = divmod(index, 3)
            chip = self._make_ctk_button(
                grid,
                text=f"Camera {index}",
                height=50,
                corner_radius=16,
                font=("Segoe UI", 10, "bold"),
                command=lambda i=index: self.camera_var.set(str(i)),
            )
            chip.grid(row=row, column=col, sticky="nsew", padx=4, pady=4)
            self.camera_chips.append((chip, str(index)))

        def refresh(*_args):
            current = self.camera_var.get()
            for chip, value in self.camera_chips:
                selected = value == current
                if selected:
                    chip.configure(
                        fg_color=ACCENT,
                        hover_color=ACCENT_HOVER,
                        text_color="#FFFFFF",
                        border_width=0,
                        border_color=ACCENT,
                        text_color_disabled="#FFFFFF",
                    )
                else:
                    chip.configure(
                        fg_color=FIELD_BG,
                        hover_color=SEG_HOVER,
                        text_color=TEXT_PRIMARY,
                        border_width=1,
                        border_color=FIELD_BORDER,
                        text_color_disabled=TEXT_PRIMARY,
                    )

        self._trace(self.camera_var, refresh)
        refresh()

    def build_stats(self, parent) -> None:
        body = self.make_frame(parent, bg_color=APP_BG, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=26, pady=26)

        self.card_header(
            body,
            "Live readings",
            "Latest light estimate and target brightness.",
        )

        content = self.make_frame(body, bg_color=APP_BG, fg_color="transparent")
        content.pack(fill="both", expand=True, pady=(20, 0))

        self.metric_card(content, "Ambient light", self.last_value_var, "camera value")
        target = self.metric_card(content, "Target brightness", self.last_target_var, "display level")

        self.progress = ctk.CTkProgressBar(
            target,
            height=10,
            corner_radius=5,
            progress_color=ACCENT,
            fg_color=("#E7ECF6", "#2A3140"),
        )
        self.progress.pack(fill="x", pady=(14, 0))
        self.progress.set(0)

        meta = self.make_frame(target, bg_color=FIELD_BG, fg_color="transparent")
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

        tip = self.make_frame(
            body,
            bg_color=APP_BG,
            corner_radius=16,
            fg_color=FIELD_BG,
            border_width=1,
            border_color=FIELD_BORDER,
        )
        tip.pack(fill="x", side="bottom", pady=(18, 0))

        ctk.CTkLabel(
            tip,
            text="Keep the webcam uncovered and facing the room for best results.",
            font=("Segoe UI", 10),
            text_color=TEXT_SECONDARY,
            wraplength=320,
            justify="left",
        ).pack(anchor="w", padx=16, pady=12)

    def metric_card(self, parent, title, variable, unit):
        card = self.make_frame(
            parent,
            bg_color=APP_BG,
            corner_radius=20,
            fg_color=FIELD_BG,
            border_width=1,
            border_color=FIELD_BORDER,
        )
        card.pack(fill="x", pady=(0, 12))

        inner = self.make_frame(card, bg_color=FIELD_BG, fg_color="transparent")
        inner.pack(fill="x", padx=20, pady=18)

        ctk.CTkLabel(
            inner,
            text=title.upper(),
            font=("Segoe UI", 9, "bold"),
            text_color=TEXT_SECONDARY,
        ).pack(anchor="w")

        row = self.make_frame(inner, bg_color=FIELD_BG, fg_color="transparent")
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
        colors = {
            "success": SUCCESS,
            "error": DANGER,
            "warning": WARNING,
            "info": ACCENT_TEXT,
        }
        self.toast.configure(text=message, text_color=colors.get(mode, ACCENT_TEXT))

        if self.toast_job:
            try:
                self.after_cancel(self.toast_job)
            except Exception:
                pass

        self.toast_job = self.after(4200, lambda: self.toast.configure(text=""))

    def set_status(self, text, sub=None, mode="idle"):
        colors = {
            "running": SUCCESS,
            "testing": ACCENT_TEXT,
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
    # Theme transition
    # ------------------------------------------------------------
    def _hex_to_rgb(self, value):
        value = str(value).lstrip("#")
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))

    def _rgb_to_hex(self, rgb):
        r, g, b = [max(0, min(255, int(c))) for c in rgb]
        return f"#{r:02x}{g:02x}{b:02x}"

    def _mix_color(self, color_a, color_b, t):
        rgb_a = self._hex_to_rgb(color_a)
        rgb_b = self._hex_to_rgb(color_b)
        mixed = tuple(rgb_a[i] + (rgb_b[i] - rgb_a[i]) * t for i in range(3))
        return self._rgb_to_hex(mixed)

    def _animate_theme(self, old_theme, new_theme, steps=8, interval=20):
        if getattr(self, "_theme_anim_job", None):
            try:
                self.after_cancel(self._theme_anim_job)
            except Exception:
                pass

        if getattr(self, "_theme_overlay", None):
            try:
                self._theme_overlay.destroy()
            except Exception:
                pass

        old_color = APP_BG[1] if old_theme == "dark" else APP_BG[0]
        new_color = APP_BG[1] if new_theme == "dark" else APP_BG[0]

        overlay = ctk.CTkFrame(
            self,
            fg_color=old_color,
            corner_radius=0,
            border_width=0,
        )

        try:
            overlay.configure(bg_color=old_color)
        except Exception:
            pass

        overlay.place(x=0, y=0, relwidth=1, relheight=1)
        overlay.lift()
        self._theme_overlay = overlay

        state = {"switched": False}

        def step(i):
            try:
                if not self.winfo_exists():
                    return
            except Exception:
                return

            t = i / max(1, steps)

            try:
                overlay.configure(fg_color=self._mix_color(old_color, new_color, t))
            except Exception:
                pass

            if not state["switched"] and i >= steps // 2:
                state["switched"] = True
                try:
                    ctk.set_appearance_mode(new_theme)
                    self.configure(fg_color=APP_BG)
                except Exception:
                    pass

            if i >= steps:
                try:
                    overlay.destroy()
                except Exception:
                    pass
                self._theme_overlay = None
                self._theme_anim_job = None
                return

            self._theme_anim_job = self.after(interval, lambda: step(i + 1))

        step(0)

    def apply_theme(self) -> None:
        theme = self.detect_theme()
        if theme == self.current_theme:
            return

        old_theme = self.current_theme
        self.current_theme = theme
        self._animate_theme(old_theme, theme)

    def theme_tick(self) -> None:
        self.apply_theme()
        self.after(8000, self.theme_tick)

    def on_theme_mode(self, _value: str) -> None:
        self.apply_theme()

    def detect_theme(self) -> str:
        if self.theme_mode_var.get() == "Auto":
            return get_auto_theme()
        return get_windows_theme()

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

        self.worker_thread = threading.Thread(
            target=self.worker,
            args=(camera, interval, gen),
            daemon=True,
        )
        self.worker_thread.start()

        self.is_running = True
        self.set_status("Running", "Monitoring ambient light", "running")
        self.refresh_run_controls()
        self.update_tray_state()
        self.show_toast("Automation started", "success")

    def stop(self):
        self.stop_event.set()
        self.worker_generation += 1
        self.is_running = False

        self.set_status("Paused", "Automation stopped")
        self.refresh_run_controls()
        self.update_tray_state()

    def toggle_running(self):
        if self.is_running:
            self.stop()
        else:
            self.start()

    def worker(self, camera, interval, generation):
        last_target = None

        while not self.stop_event.is_set() and generation == self.worker_generation:
            # Do not sample the camera or touch brightness while Windows is asleep.
            if self.power_watcher.suspended.is_set():
                self.events.put(
                    ("status", ("System sleeping", "Waiting for resume", "idle"))
                )

                while self.power_watcher.suspended.is_set() and not self.stop_event.is_set():
                    self.stop_event.wait(2)

                if self.stop_event.is_set() or generation != self.worker_generation:
                    break

                # Give the camera and display pipeline a moment after resume.
                self.stop_event.wait(3)
                continue

            try:
                with self.camera_lock:
                    value = engine.sample_camera(camera)

                if value is None:
                    self.events.put(
                        ("status", ("Camera unavailable", "Waiting for next check", "warning"))
                    )
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
                self.events.put(
                    ("test_error", "Camera not found. Check camera number and Windows permissions.")
                )
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
                        "toggle": self.toggle_running,
                        "quit": self.quit_app,
                    }.get(payload)

                    if callable(action):
                        action()

        except queue.Empty:
            pass

        self.after(150, self.process_events)

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

        return (
            f'"{Path(sys.executable).with_name("pythonw.exe")}" '
            f'"{Path(__file__).resolve()}" {AUTOSTART_FLAG}'
        )

    def ensure_startup_command(self):
        if not self.startup_var.get():
            return

        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                STARTUP_KEY,
                0,
                winreg.KEY_SET_VALUE,
            ) as key:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, self.startup_command())
        except OSError:
            pass

    def toggle_startup(self):
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                STARTUP_KEY,
                0,
                winreg.KEY_SET_VALUE,
            ) as key:
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

        automation_item = None
        tray_action = lambda *_: self.events.put(("tray", "toggle"))

        try:
            automation_item = pystray.MenuItem(
                "Automation",
                tray_action,
                checked=bool(self.is_running),
            )
        except TypeError:
            automation_item = pystray.MenuItem("Automation", tray_action)
        except Exception:
            automation_item = None

        items = [
            pystray.MenuItem("Show", lambda *_: self.events.put(("tray", "show")), default=True),
        ]

        if automation_item is not None:
            items.append(automation_item)

        items.append(pystray.Menu.SEPARATOR)
        items.append(pystray.MenuItem("Quit", lambda *_: self.events.put(("tray", "quit"))))

        menu = pystray.Menu(*items)

        self.tray_toggle_item = automation_item
        self.tray_icon = pystray.Icon(
            APP_NAME,
            create_app_icon(64),
            APP_NAME,
            menu,
        )

        self.tray_thread = threading.Thread(target=self.tray_icon.run, daemon=True)
        self.tray_thread.start()

        self.after(300, self.update_tray_state)

    def update_tray_state(self):
        if self.tray_toggle_item is None:
            return

        try:
            self.tray_toggle_item.checked = bool(self.is_running)
        except Exception:
            pass

        if self.tray_icon is not None:
            try:
                self.tray_icon.update_menu()
            except Exception:
                pass

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

        try:
            self.power_watcher.close()
        except Exception:
            pass

        self.destroy()


def main():
    enable_windows_enhancements()
    ctk.set_default_color_theme("blue")

    app = BrightnessApp()

    if AUTOSTART_FLAG in sys.argv:
        app.withdraw()
        app.after(400, app.launch_autostart)

    app.mainloop()


if __name__ == "__main__":
    main()
