"""Adaptive Windows display brightness using a short webcam light sample."""
from __future__ import annotations

import argparse
import logging
import subprocess
import time
from pathlib import Path

import cv2

try:
    import numpy as np
except ImportError:
    np = None


BRIGHTNESS_GUID = "aded5e82-b909-4619-9949-f5d71dac0bcb"
LOG_FILE = Path.home() / ".adaptive-brightness-webcam.log"

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


# ------------------------------------------------------------
# Calibration
# ------------------------------------------------------------
# If the result is still too dark, increase AMBIENT_PERCENTILE to 50 or 55.
# If it is too bright in dark rooms, decrease AMBIENT_PERCENTILE to 40 or 35.
AMBIENT_PERCENTILE = 45

# Piecewise calibration.
# Each tuple is: (camera_ambient_value, target_brightness_percent)
#
# Current tuning:
# - ~120  -> around 12%
# - ~132  -> around 16%
# - ~153  -> around 41%
# - ~155  -> around 45%
# - ~165  -> around 58%
# - ~180  -> around 70%
CALIBRATION_POINTS = (
    (0.0, 5),
    (70.0, 7),
    (100.0, 9),
    (115.0, 11),
    (130.0, 14),
    (140.0, 22),
    (150.0, 36),
    (155.0, 45),
    (165.0, 58),
    (180.0, 70),
    (210.0, 85),
    (255.0, 100),
)


def set_brightness(percent: int) -> None:
    percent = max(0, min(100, int(percent)))
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    for mode in ("SETACVALUEINDEX", "SETDCVALUEINDEX"):
        subprocess.run(
            [
                "powercfg",
                f"/{mode}",
                "SCHEME_CURRENT",
                "SUB_VIDEO",
                BRIGHTNESS_GUID,
                str(percent),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=no_window,
        )

    subprocess.run(
        ["powercfg", "/S", "SCHEME_CURRENT"],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=no_window,
    )


def ramp_brightness(start: int, end: int, cancel_check=None) -> None:
    """
    Smoothly move brightness from start to end.

    cancel_check is an optional callable. If it returns True,
    the ramp is cancelled.
    """
    if start == end:
        return

    step = 2 if end > start else -2
    value = start

    while (step > 0 and value < end) or (step < 0 and value > end):
        if cancel_check is not None and cancel_check():
            return

        value += step
        value = max(0, min(100, value))

        if (step > 0 and value > end) or (step < 0 and value < end):
            value = end

        set_brightness(value)
        time.sleep(0.12)


def ambient_value(frame) -> float:
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    height, width = hsv.shape[:2]

    # Use only the V channel and crop edges.
    crop = hsv[
        height // 10 : height * 9 // 10,
        width // 10 : width * 9 // 10,
        2,
    ]

    if crop.size == 0:
        return 0.0

    # Percentile keeps the estimate robust against small bright spots/noise.
    if np is not None:
        return float(np.percentile(crop, AMBIENT_PERCENTILE))

    return float(cv2.mean(crop)[0])


def _interp_brightness(value: float) -> int:
    points = CALIBRATION_POINTS

    if value <= points[0][0]:
        return int(points[0][1])

    if value >= points[-1][0]:
        return int(points[-1][1])

    for i in range(len(points) - 1):
        x0, y0 = points[i]
        x1, y1 = points[i + 1]

        if value <= x1:
            if x1 <= x0:
                return int(y1)

            t = (value - x0) / (x1 - x0)
            return int(round(y0 + t * (y1 - y0)))

    return int(points[-1][1])


def value_to_brightness(value: float) -> int:
    """
    Convert camera ambient value to display brightness percent.

    This uses piecewise calibration instead of a single gamma curve,
    so both dark rooms and normally lit rooms behave more predictably.
    """
    value = max(0.0, min(255.0, float(value)))
    return _interp_brightness(value)


def sample_camera(index: int) -> float | None:
    camera = cv2.VideoCapture(index, cv2.CAP_DSHOW)

    if not camera.isOpened():
        return None

    try:
        # Warm up the camera so auto-exposure settles.
        for _ in range(8):
            camera.read()
            time.sleep(0.08)

        samples = []

        for _ in range(3):
            ok, frame = camera.read()
            if ok and frame is not None:
                value = ambient_value(frame)
                if value > 1.0:
                    samples.append(value)
            time.sleep(0.08)

        if len(samples) < 2:
            return None

        samples.sort()
        return samples[len(samples) // 2]

    finally:
        camera.release()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--interval", type=float, default=60.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    last_target = None

    while True:
        value = sample_camera(args.camera)

        if value is None:
            logging.warning("Camera sample unavailable")
        else:
            target = value_to_brightness(value)
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
