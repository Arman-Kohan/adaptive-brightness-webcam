"""Adaptive Windows display brightness using a short webcam light sample."""
from __future__ import annotations

import argparse
import logging
import subprocess
import threading
import time
from pathlib import Path

import cv2

BRIGHTNESS_GUID = "aded5e82-b909-4619-9949-f5d71dac0bcb"
LOG_FILE = Path.home() / ".adaptive-brightness-webcam.log"

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


class PowerWatcher:
    """Watches Windows suspend/resume so we can skip work while asleep."""

    PBT_APMSUSPEND = 0x0004
    PBT_RESUME_EVENTS = (0x0006, 0x0007, 0x000A, 0x0012)

    def __init__(self) -> None:
        self.suspended = threading.Event()
        self._handle = None
        self._callback_ref = None
        self._register()

    def _callback(self, context, event_type, setting):
        if event_type == self.PBT_APMSUSPEND:
            self.suspended.set()
        elif event_type in self.PBT_RESUME_EVENTS:
            self.suspended.clear()
        return 0

    def _register(self) -> None:
        try:
            import ctypes

            powrprof = ctypes.windll.powrprof
            if not hasattr(powrprof, "PowerRegisterSuspendResumeNotification"):
                return

            callback_type = ctypes.WINFUNCTYPE(
                ctypes.c_ulong,
                ctypes.c_void_p,
                ctypes.c_ulong,
                ctypes.c_void_p,
            )
            self._callback_ref = callback_type(self._callback)
            handle = ctypes.c_void_p()
            status = powrprof.PowerRegisterSuspendResumeNotification(
                ctypes.c_ulong(2),  # DEVICE_NOTIFY_CALLBACK
                self._callback_ref,
                ctypes.byref(handle),
            )
            if status == 0:
                self._handle = handle
        except Exception:
            self._handle = None

    def close(self) -> None:
        if self._handle is not None:
            try:
                import ctypes

                ctypes.windll.powrprof.PowerUnregisterSuspendResumeNotification(
                    self._handle
                )
            except Exception:
                pass
            self._handle = None


def set_brightness(percent: int) -> None:
    percent = max(10, min(100, int(percent)))
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
        value = max(10, min(100, value))
        if (step > 0 and value > end) or (step < 0 and value < end):
            value = end
        set_brightness(value)
        time.sleep(0.12)


def ambient_value(frame) -> float:
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    height, width = hsv.shape[:2]
    crop = hsv[
        height // 10 : height * 9 // 10,
        width // 10 : width * 9 // 10,
    ]
    return float(cv2.mean(crop)[2])


def value_to_brightness(value: float) -> int:
    low, high = 20.0, 185.0
    normalized = max(0.0, min(1.0, (value - low) / (high - low)))
    return round(15 + normalized * 55)


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

    watcher = PowerWatcher()
    last_target = None

    try:
        while True:
            if watcher.suspended.is_set():
                if args.once:
                    logging.info("System suspended; skipping one-shot sample")
                    return

                while watcher.suspended.is_set():
                    time.sleep(2)

                # Give the camera and display pipeline a moment after resume.
                time.sleep(3)
                continue

            value = sample_camera(args.camera)
            if value is None:
                logging.warning("Camera sample unavailable")
            else:
                target = value_to_brightness(value)
                logging.info(
                    "ambient=%.1f target=%s last=%s",
                    value,
                    target,
                    last_target,
                )

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
    finally:
        watcher.close()


if __name__ == "__main__":
    main()
