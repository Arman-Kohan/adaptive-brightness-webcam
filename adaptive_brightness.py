"""Adaptive Windows display brightness using a short webcam light sample."""
from __future__ import annotations
import argparse
from collections import deque
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
DEFAULT_FIXED_INTERVAL = 120.0

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


@dataclass(frozen=True)
class AdaptiveSamplingConfig:
    """Bounds and sensitivity settings for smart sampling."""

    initial_interval: float = 5.0
    minimum_interval: float = 2.0
    maximum_interval: float = 60.0
    history_size: int = 12
    ema_alpha: float = 0.35
    sudden_change_alpha: float = 0.82
    noise_floor: float = 2.0
    stable_delta: float = 4.0
    significant_delta: float = 12.0
    rapid_delta: float = 28.0
    stable_samples: int = 4
    highly_stable_samples: int = 8
    increase_factor: float = 1.35
    decrease_factor: float = 0.45

    def __post_init__(self):
        if not (
            0.0 < self.minimum_interval <= self.initial_interval
            <= self.maximum_interval
        ):
            raise ValueError("adaptive intervals must satisfy minimum <= initial <= maximum")
        if self.history_size < 4:
            raise ValueError("history_size must be at least 4")
        if not 0.0 < self.ema_alpha <= 1.0:
            raise ValueError("ema_alpha must be in (0, 1]")
        if not 0.0 < self.sudden_change_alpha <= 1.0:
            raise ValueError("sudden_change_alpha must be in (0, 1]")
        if self.stable_samples < 2 or self.highly_stable_samples < self.stable_samples:
            raise ValueError("stability sample thresholds are invalid")


@dataclass(frozen=True)
class AdaptiveSamplingDecision:
    """Observable result of one adaptive-sampling observation."""

    interval: float
    state: str
    trend: float
    stability_score: float
    filtered_value: float | None
    valid: bool = True
    direction: str = "holding"


class AdaptiveSamplingController:
    """Filter ambient observations and choose the next smart sampling interval.

    The controller uses an EMA for responsiveness, a rolling median/MAD estimate
    for camera noise, and consecutive evidence before increasing the interval.
    Invalid observations never enter the history and therefore cannot distort
    stability or brightness decisions.
    """

    STATES = ("unknown", "rapidly changing", "moderately changing", "relatively stable", "highly stable")

    def __init__(
        self,
        config: AdaptiveSamplingConfig = AdaptiveSamplingConfig(),
        clock=time.monotonic,
    ):
        self.config = config
        self.clock = clock
        self.reset()

    def reset(self):
        self.current_interval = self.config.initial_interval
        self.history = deque(maxlen=self.config.history_size)
        self.filtered_value: float | None = None
        self.previous_filtered: float | None = None
        self.previous_timestamp: float | None = None
        self.stable_count = 0
        self.state = "unknown"

    def _decision(self, valid=True, trend=0.0, score=0.0, direction="holding"):
        return AdaptiveSamplingDecision(
            interval=self.current_interval,
            state=self.state,
            trend=trend,
            stability_score=score,
            filtered_value=self.filtered_value,
            valid=valid,
            direction=direction,
        )

    def _robust_trend(self) -> float:
        slopes = []
        history = list(self.history)
        for index, (left_time, left_value) in enumerate(history[:-1]):
            for right_time, right_value in history[index + 1:]:
                elapsed = right_time - left_time
                if elapsed > 0.0:
                    slopes.append((right_value - left_value) / elapsed)
        return float(np.median(slopes)) if slopes else 0.0

    def update(self, observation: float | AmbientMeasurement, timestamp: float | None = None):
        raw = observation.value if isinstance(observation, AmbientMeasurement) else observation
        try:
            raw = float(raw)
        except (TypeError, ValueError):
            return self._decision(valid=False)
        if not _validate_ambient(raw):
            return self._decision(valid=False)

        now = self.clock() if timestamp is None else float(timestamp)
        if not math.isfinite(now):
            now = self.clock()
        self.history.append((now, raw))

        if self.filtered_value is None:
            self.filtered_value = raw
            self.previous_timestamp = now
            self.state = "unknown"
            self.stable_count = 0
            return self._decision(score=0.0)

        self.previous_filtered = self.filtered_value
        raw_delta = abs(raw - self.filtered_value)
        alpha = (
            self.config.sudden_change_alpha
            if raw_delta >= self.config.rapid_delta
            else self.config.ema_alpha
        )
        self.filtered_value += alpha * (raw - self.filtered_value)

        previous_timestamp = (
            self.previous_timestamp
            if self.previous_timestamp is not None
            else now
        )
        elapsed = max(0.001, now - previous_timestamp)
        trend = (self.filtered_value - self.previous_filtered) / elapsed
        deltas = [
            abs(current - previous)
            for (_, previous), (_, current) in zip(self.history, list(self.history)[1:])
        ]
        median_delta = float(np.median(deltas)) if deltas else raw_delta
        median_value = float(np.median([value for _, value in self.history]))
        deviations = [abs(value - median_value) for _, value in self.history]
        mad = float(np.median(deviations)) if deviations else 0.0
        noise = max(self.config.noise_floor, 1.4826 * mad)
        history_trend = self._robust_trend()
        robust_delta = max(0.0, raw_delta - noise)
        relative_delta = robust_delta / max(abs(self.previous_filtered), 20.0)
        rapid = robust_delta >= self.config.rapid_delta or abs(trend) >= 8.0
        significant = (
            robust_delta >= max(self.config.significant_delta, noise * 2.5)
            or relative_delta >= 0.18
            or abs(trend) >= 3.0
        )
        enough_evidence = len(self.history) >= 3
        stable = (
            enough_evidence
            and robust_delta <= max(self.config.stable_delta, noise * 1.5)
            and median_delta <= max(self.config.stable_delta, noise * 1.5)
            and abs(history_trend) * min(self.current_interval, 60.0) < self.config.stable_delta
        )

        if rapid:
            self.state = "rapidly changing"
            self.stable_count = 0
            self.current_interval = self.config.minimum_interval
            direction = "decreasing"
        elif len(self.history) < 3:
            self.state = "unknown"
            self.stable_count = 0
            direction = "holding"
        elif significant:
            self.state = "moderately changing"
            self.stable_count = 0
            self.current_interval = max(
                self.config.minimum_interval,
                self.current_interval * 0.70,
            )
            direction = "decreasing"
        elif stable:
            self.stable_count += 1
            self.state = (
                "highly stable"
                if self.stable_count >= self.config.highly_stable_samples
                else "relatively stable"
            )
            if self.stable_count >= self.config.stable_samples:
                target = self.config.maximum_interval
                growth = self.config.increase_factor
                if self.stable_count < self.config.highly_stable_samples:
                    target = min(target, self.config.maximum_interval * 0.65)
                next_interval = min(target, self.current_interval * growth)
                direction = "increasing" if next_interval > self.current_interval else "holding"
                self.current_interval = next_interval
            else:
                direction = "holding"
        else:
            self.state = "moderately changing"
            self.stable_count = 0
            direction = "holding"

        self.current_interval = _clamp(
            self.current_interval,
            self.config.minimum_interval,
            self.config.maximum_interval,
        )
        self.previous_timestamp = now
        if len(self.history) < 3:
            stability_score = 0.0
        else:
            stability_score = _clamp(
                1.0
                - max(
                    robust_delta / max(self.config.significant_delta, 1.0),
                    max(0.0, median_delta - noise)
                    / max(self.config.significant_delta, 1.0),
                    abs(history_trend) * min(self.current_interval, 60.0)
                    / max(self.config.significant_delta, 1.0),
                ),
                0.0,
                1.0,
            )
        return self._decision(
            trend=trend,
            score=stability_score,
            direction=direction,
        )


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
    read_timeout: float | None = 12.0,
) -> float | AmbientMeasurement | None:
    camera = None
    try:
        camera = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if not camera.isOpened():
            logging.warning("Camera %s could not be opened", index)
            return None
        if read_timeout is not None and read_timeout > 0.0:
            try:
                timeout_property = getattr(cv2, "CAP_PROP_READ_TIMEOUT_MSEC", None)
                if timeout_property is not None:
                    camera.set(timeout_property, float(read_timeout) * 1000.0)
            except (AttributeError, TypeError, ValueError):
                logging.debug("Camera %s does not expose a read timeout", index)

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
    parser.add_argument("--mode", choices=("fixed", "smart"), default="smart")
    parser.add_argument("--interval", type=float, default=DEFAULT_FIXED_INTERVAL)
    parser.add_argument("--minimum-interval", type=float, default=2.0)
    parser.add_argument("--maximum-interval", type=float, default=60.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    if args.camera < 0:
        parser.error("--camera must be a non-negative integer.")
    if args.mode == "fixed" and (
        not math.isfinite(args.interval) or args.interval < 30.0
    ):
        parser.error("--interval must be a finite number of at least 30 seconds.")
    if (
        not math.isfinite(args.minimum_interval)
        or not math.isfinite(args.maximum_interval)
        or args.minimum_interval < 0.5
        or args.minimum_interval > args.maximum_interval
    ):
        parser.error("adaptive interval bounds must be finite and ordered.")

    controller = BrightnessController()
    calibration = ExposureCalibration()
    adaptive = AdaptiveSamplingController(
        AdaptiveSamplingConfig(
            minimum_interval=args.minimum_interval,
            maximum_interval=args.maximum_interval,
            initial_interval=max(
                args.minimum_interval,
                min(5.0, args.maximum_interval),
            ),
        )
    )
    last_target = None
    while True:
        value = sample_camera(args.camera, calibration, detailed=True)
        if value is None:
            logging.warning("Camera sample unavailable")
            wait_interval = adaptive.current_interval if args.mode == "smart" else args.interval
        else:
            decision = adaptive.update(value) if args.mode == "smart" else None
            target = controller.update(value)
            if target is None:
                wait_interval = decision.interval if decision else args.interval
                logging.warning("Ambient value did not produce a brightness target")
            else:
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
                wait_interval = (
                    decision.interval if decision else args.interval
                )
        if args.once:
            return
        time.sleep(wait_interval)

if __name__ == "__main__":
    main()
