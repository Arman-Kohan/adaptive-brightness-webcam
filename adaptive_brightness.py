"""Adaptive Windows display brightness using a short webcam light sample."""
from __future__ import annotations
import argparse
import logging
import math
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import cv2

BRIGHTNESS_GUID = "aded5e82-b909-4619-9949-f5d71dac0bcb"
MEASUREMENT_SAMPLE_COUNT = 5
LOG_FILE = Path.home() / ".adaptive-brightness-webcam.log"

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


@dataclass(frozen=True)
class BrightnessConfig:
    """Calibration and comfort settings for the ambient-light controller."""

    ambient_min: float = 8.0
    ambient_max: float = 240.0
    brightness_min: int = 10
    brightness_max: int = 75
    curve_exponent: float = 1.75
    ambient_deadband: float = 3.0
    smoothing_alpha: float = 0.35
    sudden_change_alpha: float = 0.8
    sudden_change_threshold: float = 45.0
    brightness_hysteresis: int = 2


DEFAULT_BRIGHTNESS_CONFIG = BrightnessConfig()


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _validate_ambient(value: float) -> bool:
    return math.isfinite(value) and 0.0 <= value <= 255.0


def set_brightness(percent: int) -> None:
    percent = max(10, min(100, int(percent)))
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    commands = [
        (
            "AC brightness",
            [
                "powercfg",
                "/SETACVALUEINDEX",
                "SCHEME_CURRENT",
                "SUB_VIDEO",
                BRIGHTNESS_GUID,
                str(percent),
            ],
        ),
        (
            "battery brightness",
            [
                "powercfg",
                "/SETDCVALUEINDEX",
                "SCHEME_CURRENT",
                "SUB_VIDEO",
                BRIGHTNESS_GUID,
                str(percent),
            ],
        ),
        ("power scheme refresh", ["powercfg", "/S", "SCHEME_CURRENT"]),
    ]

    for operation, command in commands:
        try:
            subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                creationflags=no_window,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("Windows powercfg command was not found.") from exc
        except subprocess.CalledProcessError as exc:
            diagnostic = (exc.stderr or exc.stdout or "").strip()
            detail = f": {diagnostic}" if diagnostic else ""
            logging.error(
                "powercfg failed during %s: returncode=%s stderr=%r stdout=%r",
                operation,
                exc.returncode,
                exc.stderr,
                exc.stdout,
            )
            raise RuntimeError(
                f"Windows brightness control failed during {operation}{detail}"
            ) from exc

def ramp_brightness(start: int, end: int, cancel_check=None) -> bool:
    """
    Smoothly move brightness from start to end.
    cancel_check is an optional callable. If it returns True,
    the ramp is cancelled.
    Returns True when the end target was reached, or False when cancelled.
    """
    if start == end:
        return True
    step = 2 if end > start else -2
    value = start
    while (step > 0 and value < end) or (step < 0 and value > end):
        if cancel_check is not None and cancel_check():
            return False
        value += step
        value = max(10, min(100, value))
        if (step > 0 and value > end) or (step < 0 and value < end):
            value = end
        set_brightness(value)
        time.sleep(0.12)
    return True

def ambient_value(frame) -> float:
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    height, width = hsv.shape[:2]
    crop = hsv[
        height // 10 : height * 9 // 10,
        width // 10 : width * 9 // 10,
    ]
    return float(cv2.mean(crop)[2])


def value_to_brightness(
    value: float,
    config: BrightnessConfig = DEFAULT_BRIGHTNESS_CONFIG,
) -> int:
    """Map camera value to comfortable display brightness using a power curve."""
    if not _validate_ambient(value):
        raise ValueError(f"ambient value must be finite and in [0, 255], got {value!r}")
    if config.ambient_max <= config.ambient_min:
        raise ValueError("ambient_max must be greater than ambient_min")
    if config.brightness_max < config.brightness_min:
        raise ValueError("brightness_max must be at least brightness_min")
    if config.curve_exponent <= 0.0 or not math.isfinite(config.curve_exponent):
        raise ValueError("curve_exponent must be a positive finite number")

    normalized = _clamp(
        (value - config.ambient_min)
        / (config.ambient_max - config.ambient_min),
        0.0,
        1.0,
    )
    curved = normalized**config.curve_exponent
    brightness = config.brightness_min + (
        curved * (config.brightness_max - config.brightness_min)
    )
    return int(round(_clamp(brightness, config.brightness_min, config.brightness_max)))


class BrightnessController:
    """Stateful filtering and hysteresis around the configurable brightness curve."""

    def __init__(self, config: BrightnessConfig = DEFAULT_BRIGHTNESS_CONFIG):
        self.config = config
        self.filtered_ambient: float | None = None
        self.target_brightness: int | None = None

    def update(self, value: float) -> int | None:
        if not _validate_ambient(value):
            logging.warning("Ignoring invalid ambient value: %r", value)
            return self.target_brightness

        if self.filtered_ambient is None:
            self.filtered_ambient = value
        else:
            delta = value - self.filtered_ambient
            if abs(delta) >= self.config.ambient_deadband:
                alpha = (
                    self.config.sudden_change_alpha
                    if abs(delta) >= self.config.sudden_change_threshold
                    else self.config.smoothing_alpha
                )
                self.filtered_ambient += alpha * delta

        proposed = value_to_brightness(self.filtered_ambient, self.config)
        if (
            self.target_brightness is not None
            and abs(proposed - self.target_brightness)
            <= self.config.brightness_hysteresis
        ):
            return self.target_brightness

        self.target_brightness = proposed
        return proposed

def _valid_capture_frame(frame) -> bool:
    try:
        shape = getattr(frame, "shape", None)
        if shape is None or len(shape) < 2:
            return False
        if any(int(dimension) <= 0 for dimension in shape[:2]):
            return False
        return int(getattr(frame, "size", 0)) > 0
    except (TypeError, ValueError):
        return False

def sample_camera(index: int) -> float | None:
    camera = None
    try:
        camera = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if not camera.isOpened():
            logging.warning("Camera %s could not be opened", index)
            return None

        # Warm up the camera so auto-exposure settles.
        for _ in range(8):
            try:
                camera.read()
            except Exception:
                logging.warning("Camera %s failed during warm-up", index, exc_info=True)
            time.sleep(0.08)

        samples = []
        for _ in range(MEASUREMENT_SAMPLE_COUNT):
            try:
                ok, frame = camera.read()
                if ok and _valid_capture_frame(frame):
                    value = ambient_value(frame)
                    if value > 1.0:
                        samples.append(value)
                else:
                    logging.warning("Camera %s returned an invalid frame", index)
            except Exception:
                logging.warning("Camera %s failed during sampling", index, exc_info=True)
            time.sleep(0.08)

        if len(samples) < 2:
            logging.warning("Camera %s did not provide enough valid frames", index)
            return None
        samples.sort()
        return samples[len(samples) // 2]
    except Exception:
        logging.exception("Camera %s sampling failed", index)
        return None
    finally:
        if camera is not None:
            try:
                camera.release()
            except Exception:
                logging.warning("Camera %s failed to release", index, exc_info=True)

def main() -> None:
    parser = argparse.ArgumentParser(description="Adaptive brightness controller")
    parser.add_argument("--camera", type=int, default=0)
    # Changed default interval from 60.0 to 120.0 (2 minutes)
    parser.add_argument("--interval", type=float, default=120.0) 
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    if args.camera < 0:
        parser.error("--camera must be a non-negative integer.")
    if not math.isfinite(args.interval) or args.interval < 30.0:
        parser.error("--interval must be a finite number of at least 30 seconds.")
    
    controller = BrightnessController()
    last_target = None
    while True:
        value = sample_camera(args.camera)
        if value is None:
            logging.warning("Camera sample unavailable")
        else:
            target = controller.update(value)
            if target is None:
                logging.warning("Ambient value did not produce a brightness target")
                if args.once:
                    return
                time.sleep(args.interval)
                continue
            logging.info("ambient=%.1f target=%s last=%s", value, target, last_target)
            if last_target is None or abs(target - last_target) >= 3:
                try:
                    if last_target is None:
                        set_brightness(target)
                    else:
                        ramp_brightness(last_target, target)
                    last_target = target
                except Exception:
                    logging.exception("Brightness update failed")
        if args.once:
            return
        time.sleep(args.interval)

if __name__ == "__main__":
    main()
