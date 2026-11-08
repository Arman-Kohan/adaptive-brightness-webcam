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
import numpy as np

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
    scene_boost_max: float = 110.0
    scene_spread_start: float = 25.0
    scene_spread_end: float = 95.0
    bright_scene_start: float = 118.0
    bright_scene_end: float = 155.0
    bright_scene_response: float = 0.30
    highlight_response_span: float = 0.06
    dim_scene_lower_quartile: float = 110.0
    dim_scene_median: float = 132.0
    dim_scene_median_span: float = 24.0
    illuminated_scene_median_start: float = 130.0
    illuminated_scene_median_end: float = 137.0
    dim_scene_highlight_ratio: float = 0.05
    dim_scene_boost: float = 60.0
    sunset_trend_penalty: float = 20.0


DEFAULT_BRIGHTNESS_CONFIG = BrightnessConfig()


@dataclass(frozen=True)
class CameraProperties:
    """Best-effort camera controls reported by the active capture backend."""

    auto_exposure: float | None = None
    exposure: float | None = None
    gain: float | None = None
    auto_white_balance: float | None = None
    white_balance: float | None = None


@dataclass(frozen=True)
class AmbientMeasurement:
    """Image-derived illumination estimate and scene diagnostics."""

    value: float
    p50: float
    p75: float
    p90: float
    scene_spread: float
    highlight_ratio: float
    lower_quartile: float | None = None


class ExposureCalibration:
    """Correct relative luminance for comparable DirectShow exposure changes."""

    def __init__(self):
        self.reference_exposure: float | None = None

    def adjust(self, value: float, properties: CameraProperties) -> float:
        exposure = properties.exposure
        if exposure is None or not math.isfinite(exposure) or not -20.0 <= exposure <= 0.0:
            return value

        if self.reference_exposure is None:
            self.reference_exposure = exposure
        factor = 2.0 ** (self.reference_exposure - exposure)

        return _clamp(value * factor, 0.0, 255.0)


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _validate_ambient(value: float) -> bool:
    return math.isfinite(value) and 0.0 <= value <= 255.0


def set_brightness(percent: int) -> None:
    percent = max(10, min(100, int(percent)))
    logging.info("brightness request: %s", percent)
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

def ambient_measurement(frame) -> AmbientMeasurement:
    """Estimate illumination and scene context from robust luminance statistics."""
    if not _valid_capture_frame(frame):
        raise ValueError("frame is empty or invalid")

    height, width = frame.shape[:2]
    crop = frame[
        height // 10 : height * 9 // 10,
        width // 10 : width * 9 // 10,
    ]
    if len(crop.shape) == 2:
        luminance = crop.astype(np.float32)
    elif crop.shape[2] >= 3:
        luminance = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).astype(np.float32)
    else:
        raise ValueError("frame must have one grayscale or at least three color channels")
    pixels = luminance.reshape(-1)
    p25, p50, p75, p90 = np.percentile(pixels, (25, 50, 75, 90))
    pixels_u8 = pixels.astype(np.uint8)
    highlight_ratio = float(np.mean(pixels_u8 >= 235))
    return AmbientMeasurement(
        value=float(0.55 * p50 + 0.30 * p75 + 0.15 * p90),
        p50=float(p50),
        p75=float(p75),
        p90=float(p90),
        scene_spread=float(p90 - p50),
        highlight_ratio=highlight_ratio,
        lower_quartile=float(p25),
    )


def ambient_value(frame) -> float:
    """Return the scalar ambient estimate for backwards-compatible callers."""
    return ambient_measurement(frame).value


def _camera_properties(camera) -> CameraProperties:
    def read(property_id):
        try:
            value = float(camera.get(property_id))
            return value if math.isfinite(value) else None
        except (AttributeError, TypeError, ValueError):
            return None

    return CameraProperties(
        auto_exposure=read(cv2.CAP_PROP_AUTO_EXPOSURE),
        exposure=read(cv2.CAP_PROP_EXPOSURE),
        gain=read(cv2.CAP_PROP_GAIN),
        auto_white_balance=read(cv2.CAP_PROP_AUTO_WB),
        white_balance=read(cv2.CAP_PROP_WB_TEMPERATURE),
    )


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
        self.previous_raw_ambient: float | None = None
        self.sunset_rising_steps = 0
        self.last_effective_ambient: float | None = None
        self.last_dim_scene = False
        self.last_scene_boost = 0.0
        self.last_trend_penalty = 0.0

    def _effective_ambient(self, measurement: AmbientMeasurement) -> float:
        spread_range = self.config.scene_spread_end - self.config.scene_spread_start
        if spread_range <= 0.0:
            return measurement.value
        spread_fraction = _clamp(
            (measurement.scene_spread - self.config.scene_spread_start) / spread_range,
            0.0,
            1.0,
        )
        highlight_fraction = _clamp(measurement.highlight_ratio / 0.12, 0.0, 1.0)
        scene_fraction = spread_fraction * (0.7 + 0.3 * highlight_fraction)
        boost = scene_fraction * self.config.scene_boost_max
        bright_fraction = _clamp(
            (measurement.value - self.config.bright_scene_start)
            / (self.config.bright_scene_end - self.config.bright_scene_start),
            0.0,
            1.0,
        )
        highlight_response = self.config.bright_scene_response
        if self.config.highlight_response_span > 0.0:
            high_highlight_fraction = _clamp(
                (measurement.highlight_ratio - 0.01)
                / self.config.highlight_response_span,
                0.0,
                1.0,
            )
            highlight_response += (
                high_highlight_fraction
                * (1.0 - self.config.bright_scene_response)
            )
        illuminated_fraction = _clamp(
            (measurement.p50 - self.config.illuminated_scene_median_start)
            / (
                self.config.illuminated_scene_median_end
                - self.config.illuminated_scene_median_start
            ),
            0.0,
            1.0,
        )
        highlight_response = max(highlight_response, illuminated_fraction)
        lower_quartile = (
            measurement.lower_quartile
            if measurement.lower_quartile is not None
            else measurement.p50
        )
        feature_median_floor = (
            self.config.dim_scene_median - self.config.dim_scene_median_span / 2.0
        )
        if measurement.p50 < feature_median_floor:
            legacy_darkness_value = (
                measurement.lower_quartile
                if measurement.lower_quartile is not None
                else measurement.p50
            )
            darkness_fraction = _clamp(
                (105.0 - legacy_darkness_value) / 55.0,
                0.0,
                1.0,
            )
        else:
            lower_quartile_darkness = _clamp(
                (self.config.dim_scene_lower_quartile - lower_quartile)
                / self.config.dim_scene_lower_quartile,
                0.0,
                1.0,
            )
            median_darkness = _clamp(
                (self.config.dim_scene_median - measurement.p50)
                / self.config.dim_scene_median_span,
                0.0,
                1.0,
            )
            darkness_fraction = min(lower_quartile_darkness, median_darkness)
        highlight_response *= 1.0 - 0.65 * darkness_fraction
        boost *= 1.0 - bright_fraction * (1.0 - highlight_response)
        self.last_scene_boost = boost
        return _clamp(
            measurement.value + boost,
            0.0,
            255.0,
        )

    def update(self, value: float | AmbientMeasurement) -> int | None:
        raw_value = value.value if isinstance(value, AmbientMeasurement) else value
        if not isinstance(raw_value, (int, float)) or not _validate_ambient(float(raw_value)):
            logging.warning("Ignoring invalid ambient value: %r", raw_value)
            return self.target_brightness
        measurement = (
            value
            if isinstance(value, AmbientMeasurement)
            else AmbientMeasurement(
                value=float(raw_value),
                p50=float(raw_value),
                p75=float(raw_value),
                p90=float(raw_value),
                scene_spread=0.0,
                highlight_ratio=0.0,
            )
        )
        value = self._effective_ambient(measurement)
        lower_quartile = (
            measurement.lower_quartile
            if measurement.lower_quartile is not None
            else measurement.p50
        )
        dim_scene = (
            measurement.lower_quartile is not None
            and lower_quartile <= self.config.dim_scene_lower_quartile
            and measurement.p50 <= self.config.dim_scene_median
            and measurement.highlight_ratio <= self.config.dim_scene_highlight_ratio
        )
        if dim_scene != self.last_dim_scene:
            self.sunset_rising_steps = 0
        if dim_scene:
            if (
                self.previous_raw_ambient is not None
                and raw_value > self.previous_raw_ambient + self.config.ambient_deadband
            ):
                self.sunset_rising_steps += 1
            dim_boost = self.config.dim_scene_boost * _clamp(
                (self.config.dim_scene_lower_quartile - lower_quartile)
                / self.config.dim_scene_lower_quartile,
                0.0,
                1.0,
            )
            self.last_trend_penalty = (
                self.sunset_rising_steps * self.config.sunset_trend_penalty
            )
            value += dim_boost - self.last_trend_penalty
        else:
            self.sunset_rising_steps = 0
            self.last_trend_penalty = 0.0
        scene_transition = self.last_dim_scene and not dim_scene
        self.last_dim_scene = dim_scene
        self.previous_raw_ambient = float(raw_value)
        self.last_effective_ambient = _clamp(value, 0.0, 255.0)
        value = self.last_effective_ambient
        if not _validate_ambient(value):
            logging.warning("Ignoring invalid ambient value: %r", value)
            return self.target_brightness

        if self.filtered_ambient is None or scene_transition:
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

def sample_camera(
    index: int,
    calibration: ExposureCalibration | None = None,
    detailed: bool = False,
) -> float | AmbientMeasurement | None:
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

        properties = _camera_properties(camera)
        logging.info(
            "Camera %s properties: auto_exposure=%s exposure=%s gain=%s "
            "auto_white_balance=%s white_balance=%s",
            index,
            properties.auto_exposure,
            properties.exposure,
            properties.gain,
            properties.auto_white_balance,
            properties.white_balance,
        )

        samples: list[AmbientMeasurement] = []
        for _ in range(MEASUREMENT_SAMPLE_COUNT):
            try:
                ok, frame = camera.read()
                if ok and _valid_capture_frame(frame):
                    measurement = ambient_measurement(frame)
                    if calibration is not None:
                        measurement = AmbientMeasurement(
                            value=calibration.adjust(measurement.value, properties),
                            p50=measurement.p50,
                            p75=measurement.p75,
                            p90=measurement.p90,
                            scene_spread=measurement.scene_spread,
                            highlight_ratio=measurement.highlight_ratio,
                            lower_quartile=measurement.lower_quartile,
                        )
                    if measurement.value > 1.0:
                        samples.append(measurement)
                else:
                    logging.warning("Camera %s returned an invalid frame", index)
            except Exception:
                logging.warning("Camera %s failed during sampling", index, exc_info=True)
            time.sleep(0.08)

        if len(samples) < 2:
            logging.warning("Camera %s did not provide enough valid frames", index)
            return None
        samples.sort(key=lambda item: item.value)
        measurement = samples[len(samples) // 2]
        logging.info(
            "Camera %s measurement: value=%.1f p25=%s p50=%.1f p75=%.1f "
            "p90=%.1f spread=%.1f highlights=%.3f exposure=%s",
            index,
            measurement.value,
            measurement.lower_quartile,
            measurement.p50,
            measurement.p75,
            measurement.p90,
            measurement.scene_spread,
            measurement.highlight_ratio,
            properties.exposure,
        )
        return measurement if detailed else measurement.value
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
    calibration = ExposureCalibration()
    last_target = None
    while True:
        value = sample_camera(args.camera, calibration, detailed=True)
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
