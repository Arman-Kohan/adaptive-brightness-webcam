"""Persistent user settings for Adaptive Brightness Webcam.

Settings live in a JSON file under the per-user application data
directory (``%APPDATA%\\Adaptive Brightness Webcam\\settings.json`` on
Windows), so they survive application restarts and Windows reboots.
Earlier versions kept every setting only in memory, which is why
values such as the sampling mode silently reset to defaults on every
launch.

Loading is defensive by design:

* a missing file simply yields the defaults;
* a corrupted or unreadable file yields the defaults and is left in
  place (the next save replaces it atomically);
* unknown keys -- including the Smart Mode minimum/maximum interval
  keys written by older versions -- are ignored, never resurrected;
* each known value is validated and falls back to its own default
  independently, so one bad value cannot discard the rest;
* loading never writes anything, so valid user preferences can never
  be overwritten by defaults during startup.

Saving is atomic (write to a temporary file, then replace), so a
crash or power loss mid-save cannot leave a half-written file behind.
"""
from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import asdict, dataclass
from pathlib import Path

APP_DIR_NAME = "Adaptive Brightness Webcam"
SETTINGS_FILE_NAME = "settings.json"
CONFIG_VERSION = 2

FIXED_INTERVAL_MODE = "Fixed Interval"
SMART_ADAPTIVE_MODE = "Smart Adaptive"
SAMPLING_MODES = (FIXED_INTERVAL_MODE, SMART_ADAPTIVE_MODE)
THEME_MODES = ("Auto", "Windows")

DEFAULT_CAMERA_INDEX = 0
DEFAULT_FIXED_INTERVAL = 120.0
MIN_FIXED_INTERVAL = 30.0
MAX_FIXED_INTERVAL = 3600.0


def default_config_path() -> Path:
    """Return the per-user settings file location for this platform."""
    appdata = os.environ.get("APPDATA")
    if appdata:
        base = Path(appdata)
    elif os.name == "nt":
        base = Path.home() / "AppData" / "Roaming"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / APP_DIR_NAME / SETTINGS_FILE_NAME


@dataclass
class AppSettings:
    """User preferences that persist across restarts.

    Note there are deliberately no Smart Mode interval fields: Smart
    Adaptive schedules measurements autonomously, so there is nothing
    interval-related left to persist for it.
    """

    camera_index: int = DEFAULT_CAMERA_INDEX
    sampling_mode: str = SMART_ADAPTIVE_MODE
    fixed_interval: float = DEFAULT_FIXED_INTERVAL
    theme_mode: str = "Windows"
    close_to_tray: bool = True


def _clean_camera_index(value: object) -> int:
    try:
        index = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_CAMERA_INDEX
    return index if 0 <= index <= 15 else DEFAULT_CAMERA_INDEX


def _clean_fixed_interval(value: object) -> float:
    try:
        interval = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_FIXED_INTERVAL
    if not math.isfinite(interval):
        return DEFAULT_FIXED_INTERVAL
    return max(MIN_FIXED_INTERVAL, min(MAX_FIXED_INTERVAL, interval))


def settings_from_dict(data: object) -> AppSettings:
    """Build validated settings from decoded JSON, ignoring bad data."""
    if not isinstance(data, dict):
        return AppSettings()
    mode = data.get("sampling_mode")
    theme = data.get("theme_mode")
    close_to_tray = data.get("close_to_tray")
    return AppSettings(
        camera_index=_clean_camera_index(data.get("camera_index")),
        sampling_mode=mode if mode in SAMPLING_MODES else SMART_ADAPTIVE_MODE,
        fixed_interval=_clean_fixed_interval(data.get("fixed_interval")),
        theme_mode=theme if theme in THEME_MODES else "Windows",
        close_to_tray=close_to_tray if isinstance(close_to_tray, bool) else True,
    )


def load_settings(path: Path | None = None) -> AppSettings:
    """Load settings from disk; never raises and never writes."""
    target = Path(path) if path is not None else default_config_path()
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return AppSettings()
    except OSError:
        logging.warning("Could not read settings file %s", target, exc_info=True)
        return AppSettings()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        logging.warning("Settings file %s is corrupted; using defaults", target)
        return AppSettings()
    return settings_from_dict(data)


def save_settings(settings: AppSettings, path: Path | None = None) -> bool:
    """Persist settings atomically. Returns True on success.

    The values are re-validated on the way out so a bad in-memory state
    can never write an invalid file.
    """
    target = Path(path) if path is not None else default_config_path()
    clean = settings_from_dict(asdict(settings))
    payload = {"version": CONFIG_VERSION, **asdict(clean)}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, target)
        return True
    except OSError:
        logging.warning("Could not save settings file %s", target, exc_info=True)
        return False
