"""Modern adaptive brightness webcam app with unified night-sky UI."""
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


# ------------------------------------------------------------
# Unified night-sky design tokens
# ------------------------------------------------------------
ACCENT = ("#3E6DFF", "#5B8CFF")
ACCENT_HOVER = ("#2F59D6", "#4978EA")
ACCENT_TEXT = ("#2F59D6", "#9DB9FF")

SUCCESS = ("#17A56B", "#3DDC84")
WARNING = ("#B36B00", "#FFC76B")
DANGER = ("#D64545", "#FF6B6B")

# This is the key fix:
# App background and card background are unified, so no extra
# rectangle is visible behind rounded cards.
APP_BG = ("#FFFFFF", "#0F1626")
PAGE_BG = APP_BG
CARD_BG = APP_BG

CARD_BORDER = ("#D8E1F6", "#273452")

FIELD_BG = ("#F1F5FF", "#0B1222")
FIELD_BORDER = ("#CBD8F4", "#2B3C63")

TEXT_PRIMARY = ("#111A2C", "#F4F7FF")
TEXT_SECONDARY = ("#5C6B8A", "#94A3C4")

SEG_BG = FIELD_BG
SEG_HOVER = ("#E4EBFF", "#141E38")

TRACK_OFF = ("#D6E0F3", "#33415E")

STOP_BG = ("#FDECEC", "#2A1520")
STOP_HOVER = ("#FADADA", "#381D29")
STOP_TEXT = ("#C53030", "#FF8A8A")
STOP_BORDER = ("#F0B9B9", "#552430")

OUTLINE_HOVER = ("#E9F0FF", "#18274A")

SUBTLE_BG = FIELD_BG


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
        self.configure(fg_color=APP_BG)

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
    # UI helpers
    # ------------------------------------------------------------
    def make_frame(self, parent, bg_color=APP_BG, **kwargs):
        """
        Create a CTkFrame with an explicit bg_color so the rectangular
        canvas behind rounded corners matches the app surface.
        """
        try:
            return ctk.CTkFrame(parent, bg_color=bg_color, **kwargs)
        except TypeError:
            return ctk.CTkFrame(parent, **kwargs)

    def ui_button(self, parent, text, command=None, kind="primary", width=170, height=48, **kwargs):
        styles = {
            "primary": {
                "fg_color": ACCENT,
                "hover_color": ACCENT_HOVER,
                "text_color": "#FFFFFF",
                "border_width": 0,
            },
            "danger_soft": {
                "fg_color": STOP_BG,
                "hover_color": STOP_HOVER,
                "text_color": STOP_TEXT,
                "border_width": 1,
                "border_color": STOP_BORDER,
            },
            "outline": {
                "fg_color": "transparent",
                "hover_color": OUTLINE_HOVER,
                "text_color": ACCENT_TEXT,
                "border_width": 1,
                "border_color": ACCENT,
            },
        }

        style = styles.get(kind, styles["primary"]).copy()
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

        ctk.CTkSwitch(
            row,
            text="",
            variable=variable,
            command=command,
            width=54,
            height=30,
            progress_color=SUCCESS,
            fg_color=TRACK_OFF,
            button_color="#FFFFFF",
            button_hover_color="#E8EEFF",
        ).pack(side="right", pady=(2, 0))

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

            btn = ctk.CTkButton(
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

        try:
            variable.trace_add("write", refresh)
        except AttributeError:
            try:
                variable.trace("w", refresh)
            except Exception:
                pass

        refresh()
        return frame

    # ------------------------------------------------------------
    # UI
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
            corner_radius=26,
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
                )
            except TypeError:
                scroll = AutoHideScrollableFrame(self, fg_color=APP_BG)

        # No outer padding; padding goes inside.
        scroll.grid(row=2, column=0, sticky="nsew", padx=0, pady=0)

        content = self.make_frame(scroll, bg_color=APP_BG, fg_color="transparent")
        content.pack(fill="both", expand=True)

        inner = self.make_frame(content, bg_color=APP_BG, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=30, pady=(8, 16))
        inner.grid_columnconfigure(0, weight=11)
        inner.grid_columnconfigure(1, weight=9)

        settings = self.make_frame(
            inner,
            bg_color=APP_BG,
            corner_radius=26,
            border_width=1,
            fg_color=CARD_BG,
            border_color=CARD_BORDER,
        )
        settings.grid(row=0, column=0, sticky="nsew", padx=(0, 12), pady=(0, 14))

        stats = self.make_frame(
            inner,
            bg_color=APP_BG,
            corner_radius=26,
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
            width=200,
            height=50,
        )
        self.start_btn.pack(side="left", padx=(0, 12))

        self.stop_btn = self.ui_button(
            buttons,
            "Stop",
            self.stop,
            kind="danger_soft",
            width=110,
            height=50,
            state="disabled",
        )
        self.stop_btn.pack(side="left")

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
        cam_menu = ctk.CTkOptionMenu(
            cam_row,
            values=[str(i) for i in range(6)],
            variable=self.camera_var,
            width=130,
            height=42,
            corner_radius=14,
            fg_color=FIELD_BG,
            button_color=ACCENT,
            button_hover_color=ACCENT_HOVER,
            text_color=TEXT_PRIMARY,
            font=("Segoe UI", 10),
        )
        cam_menu.pack(side="right")

        try:
            cam_menu.configure(
                dropdown_fg_color=CARD_BG,
                dropdown_button_color=ACCENT,
                dropdown_button_hover_color=ACCENT_HOVER,
                dropdown_text_color=TEXT_PRIMARY,
            )
        except Exception:
            pass

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

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=20, pady=18)

        ctk.CTkLabel(
            inner,
            text=title.upper(),
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

            # Switch the actual CustomTkinter theme near the middle of the fade.
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
