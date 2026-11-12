"""Adaptive Windows display brightness using a short webcam light sample."""
from __future__ import annotations
import argparse
from collections import deque
import logging
import math
import subprocess
import threading
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
    """Calibration and comfort settings for the ambient-light controller.

    Calibration note (2026-11): the previous power curve (exponent 1.75)
    combined with the old scene boost (up to +110) pushed a measured
    ambient value of about 170.9 to roughly 67% brightness, which users
    experience as unnecessarily bright for that lighting. The curve is now
    compressive (exponent 0.92, a Weber-Fechner-style response: responsive
    in dim light, gentle in bright light) and the scene boost is capped
    lower, so the bare mapping sends 170.9 -> 57% and boosted real-world
    scenes stay close to that target instead of overshooting it. The
    mapping stays smooth and monotonic: neighbouring ambient values never
    differ by more than about 1 brightness point per unit.
    """

    ambient_min: float = 8.0
    ambient_max: float = 240.0
    brightness_min: int = 10
    brightness_max: int = 75
    curve_exponent: float = 0.92
    ambient_deadband: float = 3.0
    smoothing_alpha: float = 0.35
    sudden_change_alpha: float = 0.8
    sudden_change_threshold: float = 45.0
    brightness_hysteresis: int = 2
    scene_boost_max: float = 65.0
    # Hard comfort ceiling for ALL ambient-domain boosts combined
    # (scene boost, dim-scene boost, exposure-driven inflation). The
    # pipeline may never produce an effective ambient whose brightness
    # exceeds the calibrated curve value for the measured ambient by
    # more than this allowance. The allowance is expressed in
    # brightness points: full in dim/mid light (where scene
    # compensation is legitimate), then smoothstepped to zero between
    # the normalized start/end points, because an already-bright
    # measurement needs no upward compensation. See
    # BrightnessController.update and scene_boost_allowance().
    boost_cap_points: float = 15.0
    boost_cap_start: float = 0.55
    boost_cap_end: float = 0.78
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


# Internal safety bounds for Smart Adaptive scheduling. These are
# deliberately NOT user-configurable: Smart Adaptive decides on its own
# when the next measurement is needed. The minimum keeps camera and CPU
# usage calm even while light is changing rapidly; the maximum bounds
# the worst-case latency for detecting a sudden change that happens
# just after a sample. Earlier versions exposed minimum/maximum
# intervals in the GUI and CLI; those settings no longer exist and any
# values stored by old versions are ignored.
SMART_MIN_INTERVAL = 5.0
SMART_MAX_INTERVAL = 120.0
SMART_INITIAL_INTERVAL = 8.0


@dataclass(frozen=True)
class AdaptiveSamplingConfig:
    """Sensitivity settings for autonomous smart sampling.

    Only the signal-processing sensitivity is configurable here. The
    interval bounds themselves are internal constants
    (``SMART_MIN_INTERVAL`` / ``SMART_MAX_INTERVAL`` /
    ``SMART_INITIAL_INTERVAL``) and are exposed read-only below for
    diagnostics and backwards-compatible readers.
    """

    history_size: int = 12
    ema_alpha: float = 0.35
    sudden_change_alpha: float = 0.82
    noise_floor: float = 2.0
    stable_delta: float = 4.0
    significant_delta: float = 12.0
    rapid_delta: float = 28.0
    stable_samples: int = 4
    highly_stable_samples: int = 8
    increase_factor: float = 1.4
    decrease_factor: float = 0.6
    rapid_hold_samples: int = 3

    @property
    def minimum_interval(self) -> float:
        return SMART_MIN_INTERVAL

    @property
    def maximum_interval(self) -> float:
        return SMART_MAX_INTERVAL

    @property
    def initial_interval(self) -> float:
        return SMART_INITIAL_INTERVAL

    def __post_init__(self):
        if self.history_size < 4:
            raise ValueError("history_size must be at least 4")
        if not 0.0 < self.ema_alpha <= 1.0:
            raise ValueError("ema_alpha must be in (0, 1]")
        if not 0.0 < self.sudden_change_alpha <= 1.0:
            raise ValueError("sudden_change_alpha must be in (0, 1]")
        if self.stable_samples < 2 or self.highly_stable_samples < self.stable_samples:
            raise ValueError("stability sample thresholds are invalid")
        if self.increase_factor <= 1.0:
            raise ValueError("increase_factor must be greater than 1")
        if not 0.0 < self.decrease_factor < 1.0:
            raise ValueError("decrease_factor must be in (0, 1)")
        if self.rapid_hold_samples < 0:
            raise ValueError("rapid_hold_samples must be non-negative")


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
    """Autonomously schedule ambient-light measurements.

    Algorithm
    ---------
    Each valid observation updates three signal estimates:

    * an exponential moving average (EMA) of the ambient value, with a
      faster alpha after large jumps so real changes are tracked
      promptly while camera noise is smoothed;
    * a rolling median/MAD estimate of camera noise, so the stability
      thresholds adapt to the actual sensor instead of assuming a
      fixed noise level;
    * a robust (Theil-Sen) trend in units/second, which distinguishes
      a genuine brightening/darkening drift from uncorrelated noise.

    From these, every observation is classified:

    * *rapid* -- the noise-adjusted jump or trend is large. The next
      interval drops straight to ``SMART_MIN_INTERVAL`` and is held
      there for ``rapid_hold_samples`` confirmation samples, so a real
      change is re-measured promptly until the new level is confirmed.
    * *significant* -- a meaningful but not dramatic change, or the
      caller reports that the current screen brightness is far from
      the desired one (``brightness_error``). The interval shrinks
      geometrically (``decrease_factor``).
    * *stable* -- noise-adjusted deltas, the median step and the
      projected drift over the next interval are all small, and the
      brightness error (if reported) is settled. Only *consecutive*
      stable samples grow the interval, geometrically
      (``increase_factor``), and early growth is capped until the
      environment has proven highly stable. A single noisy sample
      therefore cannot collapse or explode the schedule, and the
      interval cannot oscillate: growth requires sustained evidence,
      shrinkage is immediate but bounded below.

    The interval is always clamped to the internal safety bounds
    [``SMART_MIN_INTERVAL``, ``SMART_MAX_INTERVAL``]. A sudden change
    that occurs just after a sample is therefore detected at most
    ``SMART_MAX_INTERVAL`` seconds later, and the rapid path then
    re-checks within ``SMART_MIN_INTERVAL`` seconds.

    Invalid observations never enter the history and therefore cannot
    distort stability or brightness decisions.
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
        self.current_interval = SMART_INITIAL_INTERVAL
        self.history = deque(maxlen=self.config.history_size)
        self.filtered_value: float | None = None
        self.previous_filtered: float | None = None
        self.previous_timestamp: float | None = None
        self.stable_count = 0
        self.state = "unknown"
        self._hold_remaining = 0

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

    def update(
        self,
        observation: float | AmbientMeasurement,
        timestamp: float | None = None,
        brightness_error: float | None = None,
    ):
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

        if brightness_error is not None and math.isfinite(brightness_error):
            error_significant = abs(brightness_error) >= 8.0
            error_settled = abs(brightness_error) <= 3.0
        else:
            error_significant = False
            error_settled = True

        rapid = robust_delta >= self.config.rapid_delta or abs(trend) >= 8.0
        significant = (
            robust_delta >= max(self.config.significant_delta, noise * 2.5)
            or relative_delta >= 0.18
            or abs(trend) >= 3.0
            or error_significant
        )
        enough_evidence = len(self.history) >= 3
        stable = (
            enough_evidence
            and error_settled
            and robust_delta <= max(self.config.stable_delta, noise * 1.5)
            and median_delta <= max(self.config.stable_delta, noise * 1.5)
            and abs(history_trend) * min(self.current_interval, 60.0) < self.config.stable_delta
        )

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

        if rapid:
            self.state = "rapidly changing"
            self.stable_count = 0
            self._hold_remaining = self.config.rapid_hold_samples
            self.current_interval = SMART_MIN_INTERVAL
            direction = "decreasing"
        elif self._hold_remaining > 0:
            # Confirmation period after a rapid change: stay at the
            # minimum interval until the new level has been re-measured
            # a few times, even if individual samples already look calm.
            self._hold_remaining -= 1
            self.stable_count = 0
            if self.state != "rapidly changing":
                self.state = "moderately changing"
            self.current_interval = SMART_MIN_INTERVAL
            direction = "holding"
        elif len(self.history) < 3:
            self.state = "unknown"
            self.stable_count = 0
            direction = "holding"
        elif significant:
            self.state = "moderately changing"
            self.stable_count = 0
            next_interval = max(
                SMART_MIN_INTERVAL,
                self.current_interval * self.config.decrease_factor,
            )
            direction = "decreasing" if next_interval < self.current_interval else "holding"
            self.current_interval = next_interval
        elif stable:
            self.stable_count += 1
            self.state = (
                "highly stable"
                if self.stable_count >= self.config.highly_stable_samples
                else "relatively stable"
            )
            if self.stable_count >= self.config.stable_samples:
                target = SMART_MAX_INTERVAL
                if self.stable_count < self.config.highly_stable_samples:
                    # Grow cautiously until stability is well proven.
                    target = min(target, SMART_MAX_INTERVAL * 0.65)
                next_interval = min(target, self.current_interval * self.config.increase_factor)
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
            SMART_MIN_INTERVAL,
            SMART_MAX_INTERVAL,
        )
        self.previous_timestamp = now
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


def brightness_to_value(
    brightness: float,
    config: BrightnessConfig = DEFAULT_BRIGHTNESS_CONFIG,
) -> float:
    """Invert :func:`value_to_brightness` (exact for the power curve)."""
    if config.brightness_max <= config.brightness_min:
        raise ValueError("brightness_max must be greater than brightness_min")
    if config.ambient_max <= config.ambient_min:
        raise ValueError("ambient_max must be greater than ambient_min")
    if config.curve_exponent <= 0.0 or not math.isfinite(config.curve_exponent):
        raise ValueError("curve_exponent must be a positive finite number")
    fraction = _clamp(
        (brightness - config.brightness_min)
        / (config.brightness_max - config.brightness_min),
        0.0,
        1.0,
    )
    return config.ambient_min + (fraction ** (1.0 / config.curve_exponent)) * (
        config.ambient_max - config.ambient_min
    )


def scene_boost_allowance(
    value: float,
    config: BrightnessConfig = DEFAULT_BRIGHTNESS_CONFIG,
) -> float:
    """Brightness points of total boost allowed at a measured ambient.

    Full allowance at and below ``boost_cap_start`` of the ambient
    range, smoothstepped to zero at ``boost_cap_end``. This is the
    comfort ceiling that keeps scene/exposure compensation from
    pushing an already-bright measurement far past its calibrated
    target.
    """
    normalized = _clamp(
        (value - config.ambient_min) / (config.ambient_max - config.ambient_min),
        0.0,
        1.0,
    )
    start, end = config.boost_cap_start, config.boost_cap_end
    if normalized <= start:
        return config.boost_cap_points
    if normalized >= end or end <= start:
        return 0.0
    t = (normalized - start) / (end - start)
    smooth = t * t * (3.0 - 2.0 * t)
    return config.boost_cap_points * (1.0 - smooth)


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
        # Comfort ceiling, applied downstream of EVERY ambient-domain
        # modifier (scene boost, dim-scene boost and trend penalty;
        # exposure compensation is already inside the measured value).
        # Whatever the scene logic computed, the effective ambient may
        # not map to a brightness more than scene_boost_allowance()
        # points above the calibrated curve for the measured value.
        # Without this, a bright, high-contrast room at a measured 173
        # received the full +54 scene boost (its "bright scene" gating
        # preserves the boost precisely when the scene is already
        # bright) and produced 71-72% instead of the calibrated ~58%.
        base_brightness = value_to_brightness(measurement.value, self.config)
        cap_value = brightness_to_value(
            base_brightness + scene_boost_allowance(measurement.value, self.config),
            self.config,
        )
        value = min(value, cap_value)
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

@dataclass(frozen=True)
class BrightnessGateConfig:
    """Anti-flicker rules for applying Smart Adaptive brightness changes."""

    stabilization_samples: int = 3
    min_change: int = 4
    confirmation_samples: int = 2
    large_change: int = 12
    max_step: int = 20
    min_seconds_between_changes: float = 20.0

    def __post_init__(self):
        if self.stabilization_samples < 0:
            raise ValueError("stabilization_samples must be non-negative")
        if self.min_change < 1:
            raise ValueError("min_change must be at least 1")
        if self.confirmation_samples < 1:
            raise ValueError("confirmation_samples must be at least 1")
        if self.large_change < self.min_change:
            raise ValueError("large_change must be at least min_change")
        if self.max_step < self.min_change:
            raise ValueError("max_step must be at least min_change")
        if self.min_seconds_between_changes < 0.0:
            raise ValueError("min_seconds_between_changes must be non-negative")


class BrightnessChangeGate:
    """Decide whether a proposed brightness may actually be applied.

    Smart Adaptive used to apply the first measurement immediately and
    then react to every later proposal, so sensor noise near a curve
    boundary made the screen visibly hunt up and down. The gate adds
    the missing discipline:

    * a startup stabilization period: the first
      ``stabilization_samples`` proposals are observed, never applied;
    * a deadband: proposals closer than ``min_change`` points to the
      currently applied brightness are ignored;
    * confirmation: moderate changes must be proposed consistently by
      ``confirmation_samples`` consecutive measurements before they are
      applied, so a single noisy sample changes nothing;
    * rate limiting: after a change, further moderate changes wait at
      least ``min_seconds_between_changes``;
    * magnitude limiting: one application moves at most ``max_step``
      points (the existing ramp then smooths the transition itself).

    Large changes (at least ``large_change`` points, e.g. walking into
    a bright room) bypass confirmation and rate limiting so the
    display stays responsive when it matters.
    """

    def __init__(
        self,
        config: BrightnessGateConfig = BrightnessGateConfig(),
        clock=time.monotonic,
    ):
        self.config = config
        self.clock = clock
        self.reset()

    def reset(self):
        self.observations = 0
        self._pending_target: int | None = None
        self._pending_count = 0
        self._last_change_at: float | None = None

    def observe(
        self,
        proposed: int | None,
        applied: int | None,
        now: float | None = None,
    ) -> int | None:
        """Return the brightness to apply now, or None to hold."""
        if proposed is None:
            return None
        proposed = int(proposed)
        if now is None:
            now = self.clock()
        self.observations += 1
        if self.observations <= self.config.stabilization_samples:
            return None
        if applied is None:
            self._last_change_at = now
            self._pending_target = None
            self._pending_count = 0
            return proposed

        delta = proposed - applied
        if abs(delta) < self.config.min_change:
            self._pending_target = None
            self._pending_count = 0
            return None

        step = max(-self.config.max_step, min(self.config.max_step, delta))
        target = applied + step

        if abs(delta) >= self.config.large_change:
            self._pending_target = None
            self._pending_count = 0
            self._last_change_at = now
            return target

        if (
            self._last_change_at is not None
            and now - self._last_change_at
            < self.config.min_seconds_between_changes
        ):
            return None

        if (
            self._pending_target is not None
            and abs(proposed - self._pending_target) <= 2
        ):
            self._pending_count += 1
        else:
            self._pending_target = proposed
            self._pending_count = 1
        if self._pending_count < self.config.confirmation_samples:
            return None

        self._pending_target = None
        self._pending_count = 0
        self._last_change_at = now
        return target


class CameraSession:
    """One controlled camera lifecycle: open, warm up, sample, release.

    The application creates one session per measurement cycle (via
    :func:`sample_camera`) and closes it immediately after the cycle,
    so the webcam LED is off while the scheduler waits for the next
    check. Within a cycle the session opens the device exactly once,
    discards ``WARMUP_FRAMES`` frames while auto-exposure settles (the
    first measurement after opening is therefore never taken from an
    unstabilized sensor), captures ``MEASUREMENT_SAMPLE_COUNT`` frames,
    and is released exactly once by :meth:`close`.

    Failure handling is deliberately conservative: if frames stop
    arriving, the session invalidates itself and refuses to reopen
    until a cooldown has passed, so a disconnected or busy camera
    cannot cause a reopen storm or GUI instability. A dark scene
    (frames arrive but measure near-black) is *not* a device failure
    and keeps the session open.
    """

    WARMUP_FRAMES = 8
    REOPEN_COOLDOWN = 10.0

    def __init__(
        self,
        index: int,
        calibration: ExposureCalibration | None = None,
        *,
        clock=time.monotonic,
        read_timeout: float | None = 12.0,
        reopen_cooldown: float = REOPEN_COOLDOWN,
    ):
        self.index = index
        self.calibration = calibration
        self.clock = clock
        self.read_timeout = read_timeout
        self.reopen_cooldown = reopen_cooldown
        self.open_count = 0
        self.consecutive_failures = 0
        self._camera = None
        self._properties: CameraProperties | None = None
        self._lock = threading.Lock()
        self._next_open_at = 0.0

    @property
    def is_open(self) -> bool:
        return self._camera is not None

    def __enter__(self) -> "CameraSession":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def _ensure_open_locked(self) -> bool:
        if self._camera is not None:
            return True
        if self.clock() < self._next_open_at:
            return False
        camera = None
        try:
            camera = cv2.VideoCapture(self.index, cv2.CAP_DSHOW)
            if not camera.isOpened():
                logging.warning("Camera %s could not be opened", self.index)
                try:
                    camera.release()
                except Exception:
                    pass
                self.consecutive_failures += 1
                self._next_open_at = self.clock() + self.reopen_cooldown
                return False
        except Exception:
            logging.exception("Camera %s failed to open", self.index)
            self.consecutive_failures += 1
            self._next_open_at = self.clock() + self.reopen_cooldown
            return False

        self._camera = camera
        self.open_count += 1
        if self.read_timeout is not None and self.read_timeout > 0.0:
            try:
                timeout_property = getattr(cv2, "CAP_PROP_READ_TIMEOUT_MSEC", None)
                if timeout_property is not None:
                    camera.set(timeout_property, float(self.read_timeout) * 1000.0)
            except (AttributeError, TypeError, ValueError):
                logging.debug("Camera %s does not expose a read timeout", self.index)

        # Warm up once per (re)open so auto-exposure can settle.
        for _ in range(self.WARMUP_FRAMES):
            try:
                camera.read()
            except Exception:
                logging.warning(
                    "Camera %s failed during warm-up", self.index, exc_info=True
                )
            time.sleep(0.08)

        self._properties = _camera_properties(camera)
        properties = self._properties
        logging.info(
            "Camera %s properties: auto_exposure=%s exposure=%s gain=%s "
            "auto_white_balance=%s white_balance=%s",
            self.index,
            properties.auto_exposure,
            properties.exposure,
            properties.gain,
            properties.auto_white_balance,
            properties.white_balance,
        )
        return True

    def _invalidate_locked(self) -> None:
        camera, self._camera = self._camera, None
        self._properties = None
        if camera is not None:
            try:
                camera.release()
            except Exception:
                logging.warning(
                    "Camera %s failed to release", self.index, exc_info=True
                )
        self.consecutive_failures += 1
        self._next_open_at = self.clock() + self.reopen_cooldown

    def sample_detailed(self) -> AmbientMeasurement | None:
        """Take one median-of-samples measurement from the open camera."""
        with self._lock:
            if not self._ensure_open_locked():
                return None
            camera = self._camera
            properties = self._properties
            samples: list[AmbientMeasurement] = []
            successful_reads = 0
            for _ in range(MEASUREMENT_SAMPLE_COUNT):
                try:
                    ok, frame = camera.read()
                    if ok and _valid_capture_frame(frame):
                        successful_reads += 1
                        measurement = ambient_measurement(frame)
                        if self.calibration is not None and properties is not None:
                            measurement = AmbientMeasurement(
                                value=self.calibration.adjust(
                                    measurement.value, properties
                                ),
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
                        logging.warning(
                            "Camera %s returned an invalid frame", self.index
                        )
                except Exception:
                    logging.warning(
                        "Camera %s failed during sampling",
                        self.index,
                        exc_info=True,
                    )
                time.sleep(0.08)

            if successful_reads == 0:
                # The device stopped delivering frames: drop it and let
                # the cooldown govern any reopen attempt.
                logging.warning(
                    "Camera %s delivered no frames; session invalidated",
                    self.index,
                )
                self._invalidate_locked()
                return None
            if len(samples) < 2:
                logging.warning(
                    "Camera %s did not provide enough valid frames", self.index
                )
                return None
            self.consecutive_failures = 0
            samples.sort(key=lambda item: item.value)
            measurement = samples[len(samples) // 2]
            logging.info(
                "Camera %s measurement: value=%.1f p25=%s p50=%.1f p75=%.1f "
                "p90=%.1f spread=%.1f highlights=%.3f exposure=%s",
                self.index,
                measurement.value,
                measurement.lower_quartile,
                measurement.p50,
                measurement.p75,
                measurement.p90,
                measurement.scene_spread,
                measurement.highlight_ratio,
                properties.exposure if properties is not None else None,
            )
            return measurement

    def sample(self) -> float | None:
        measurement = self.sample_detailed()
        return measurement.value if measurement is not None else None

    def close(self) -> None:
        """Release the camera exactly once; safe to call repeatedly."""
        with self._lock:
            camera, self._camera = self._camera, None
            self._properties = None
            if camera is not None:
                try:
                    camera.release()
                except Exception:
                    logging.warning(
                        "Camera %s failed to release", self.index, exc_info=True
                    )


def sample_camera(
    index: int,
    calibration: ExposureCalibration | None = None,
    detailed: bool = False,
    read_timeout: float | None = 12.0,
) -> float | AmbientMeasurement | None:
    """One-shot sample for the camera test button and one-off CLI runs.

    Continuous Smart Adaptive runs use :class:`CameraSession` directly
    so the camera is not reopened for every measurement.
    """
    session = CameraSession(index, calibration, read_timeout=read_timeout)
    try:
        measurement = session.sample_detailed()
    finally:
        session.close()
    if measurement is None:
        return None
    return measurement if detailed else measurement.value


def main() -> None:
    parser = argparse.ArgumentParser(description="Adaptive brightness controller")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--mode", choices=("fixed", "smart"), default="smart")
    parser.add_argument("--interval", type=float, default=DEFAULT_FIXED_INTERVAL)
    # Deprecated: Smart Adaptive no longer takes user-configured interval
    # bounds. The flags are still accepted so old scripts and shortcuts
    # keep working, but their values are ignored.
    parser.add_argument("--minimum-interval", type=float, default=None)
    parser.add_argument("--maximum-interval", type=float, default=None)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    if args.camera < 0:
        parser.error("--camera must be a non-negative integer.")
    if args.mode == "fixed" and (
        not math.isfinite(args.interval) or args.interval < 30.0
    ):
        parser.error("--interval must be a finite number of at least 30 seconds.")
    if args.minimum_interval is not None or args.maximum_interval is not None:
        logging.warning(
            "--minimum-interval/--maximum-interval are deprecated and ignored; "
            "Smart Adaptive schedules measurements autonomously."
        )

    controller = BrightnessController()
    calibration = ExposureCalibration()
    adaptive = (
        AdaptiveSamplingController() if args.mode == "smart" else None
    )
    gate = BrightnessChangeGate() if args.mode == "smart" else None
    last_target = None
    while True:
        # Intermittent capture: one open/warm-up/capture/release
        # cycle per measurement, so the camera LED is off while
        # waiting for the next scheduled check.
        value = sample_camera(args.camera, calibration, detailed=True)
        if value is None:
            logging.warning("Camera sample unavailable")
            wait_interval = adaptive.current_interval if adaptive else args.interval
        else:
            target = controller.update(value)
            brightness_error = (
                target - last_target
                if target is not None and last_target is not None
                else None
            )
            decision = (
                adaptive.update(value, brightness_error=brightness_error)
                if adaptive
                else None
            )
            if target is None:
                wait_interval = decision.interval if decision else args.interval
                logging.warning("Ambient value did not produce a brightness target")
            else:
                logging.info("ambient=%.1f target=%s last=%s", value, target, last_target)
                # In smart mode the gate enforces the stabilization
                # period, deadband, confirmation and rate limits;
                # --once and fixed mode keep the direct behaviour.
                apply_target = target
                if gate is not None and not args.once:
                    apply_target = gate.observe(target, last_target)
                if apply_target is not None and (
                    last_target is None or apply_target != last_target
                ):
                    try:
                        if last_target is None:
                            set_brightness(apply_target)
                        else:
                            ramp_brightness(last_target, apply_target)
                        last_target = apply_target
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
