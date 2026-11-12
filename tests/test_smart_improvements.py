"""Tests for the Smart Adaptive improvement pass (2026-11).

Covered here:
* the recalibrated ambient -> brightness curve (170.9 -> ~57%),
* persistent settings in app_config (save/load across restarts),
* the CameraSession lifecycle (one open/capture/release per cycle),
* the BrightnessChangeGate anti-flicker rules,
* autonomous adaptive scheduling (internal bounds, stability backoff,
  rapid-change response, brightness-error feedback),
* the Smart worker end to end with a mocked camera session.
"""
import dataclasses
import json

import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import adaptive_brightness as engine


class CalibrationTests(unittest.TestCase):
    def test_reference_point_170_9_maps_to_57(self):
        self.assertEqual(engine.value_to_brightness(170.9), 57)

    def test_calibration_table(self):
        expected = {
            0.0: 10,
            20.0: 14,
            40.0: 21,
            60.0: 26,
            80.0: 32,
            100.0: 38,
            120.0: 43,
            139.6: 49,
            150.0: 51,
            170.9: 57,
            200.0: 65,
            240.0: 75,
            255.0: 75,
        }
        for ambient, brightness in expected.items():
            with self.subTest(ambient=ambient):
                self.assertEqual(engine.value_to_brightness(ambient), brightness)

    def test_curve_is_monotonic_across_full_range(self):
        values = [engine.value_to_brightness(float(v)) for v in range(256)]
        self.assertEqual(values, sorted(values))

    def test_curve_has_no_abrupt_jumps(self):
        values = [engine.value_to_brightness(float(v)) for v in range(256)]
        steps = [abs(b - a) for a, b in zip(values, values[1:])]
        self.assertLessEqual(max(steps), 1)

    def test_curve_neighbourhood_of_reference_point_is_smooth(self):
        neighbourhood = [
            engine.value_to_brightness(170.9 + offset)
            for offset in (-10.0, -5.0, -1.0, 0.0, 1.0, 5.0, 10.0)
        ]
        self.assertEqual(neighbourhood, sorted(neighbourhood))
        self.assertLessEqual(neighbourhood[-1] - neighbourhood[0], 6)

    def test_plain_controller_reading_at_reference_point(self):
        measurement = engine.AmbientMeasurement(
            170.9, 170.9, 170.9, 170.9, 0.0, 0.0, 170.9
        )
        self.assertEqual(engine.BrightnessController().update(measurement), 57)

    def test_typical_scene_at_reference_point_stays_comfortable(self):
        measurement = engine.AmbientMeasurement(
            value=170.9, p50=165.0, p75=178.0, p90=200.0,
            scene_spread=35.0, highlight_ratio=0.02, lower_quartile=150.0,
        )
        target = engine.BrightnessController().update(measurement)
        self.assertGreaterEqual(target, 55)
        self.assertLessEqual(target, 60)

    def test_high_contrast_scene_no_longer_reaches_old_67(self):
        measurement = engine.AmbientMeasurement(
            value=170.9, p50=150.0, p75=185.0, p90=215.0,
            scene_spread=65.0, highlight_ratio=0.06, lower_quartile=130.0,
        )
        target = engine.BrightnessController().update(measurement)
        self.assertLessEqual(target, 67)
        self.assertGreaterEqual(target, 57)


class ConfigPersistenceTests(unittest.TestCase):
    def setUp(self):
        import app_config

        self.app_config = app_config
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "nested" / "settings.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_then_load_simulates_restart(self):
        saved = self.app_config.AppSettings(
            camera_index=2,
            sampling_mode=self.app_config.FIXED_INTERVAL_MODE,
            fixed_interval=180.0,
            theme_mode="Auto",
            close_to_tray=False,
        )
        self.assertTrue(self.app_config.save_settings(saved, self.path))
        loaded = self.app_config.load_settings(self.path)
        self.assertEqual(loaded, saved)

    def test_missing_file_yields_defaults(self):
        loaded = self.app_config.load_settings(self.path)
        self.assertEqual(loaded, self.app_config.AppSettings())

    def test_corrupted_file_yields_defaults_and_is_not_rewritten(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{ not json !!", encoding="utf-8")
        loaded = self.app_config.load_settings(self.path)
        self.assertEqual(loaded, self.app_config.AppSettings())
        # Loading must never overwrite anything on disk.
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{ not json !!")

    def test_non_object_json_yields_defaults(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("[1, 2, 3]", encoding="utf-8")
        self.assertEqual(
            self.app_config.load_settings(self.path), self.app_config.AppSettings()
        )

    def test_legacy_smart_interval_keys_are_ignored(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "camera_index": 1,
                    "sampling_mode": "Smart Adaptive",
                    "minimum_interval": 3,
                    "maximum_interval": 90,
                }
            ),
            encoding="utf-8",
        )
        loaded = self.app_config.load_settings(self.path)
        self.assertEqual(loaded.camera_index, 1)
        self.assertEqual(loaded.sampling_mode, "Smart Adaptive")
        self.assertFalse(hasattr(loaded, "minimum_interval"))
        self.assertFalse(hasattr(loaded, "maximum_interval"))

    def test_invalid_values_fall_back_per_field(self):
        settings = self.app_config.settings_from_dict(
            {
                "camera_index": -4,
                "sampling_mode": "Banana",
                "fixed_interval": "abc",
                "theme_mode": "Neon",
                "close_to_tray": "yes",
            }
        )
        self.assertEqual(settings, self.app_config.AppSettings())

    def test_partially_valid_file_keeps_valid_fields(self):
        settings = self.app_config.settings_from_dict(
            {"camera_index": 3, "fixed_interval": 5}
        )
        self.assertEqual(settings.camera_index, 3)
        # 5s is below the fixed-mode minimum, so it is clamped, and the
        # remaining fields keep their defaults.
        self.assertEqual(settings.fixed_interval, 30.0)
        self.assertEqual(settings.sampling_mode, "Smart Adaptive")

    def test_saved_preferences_are_not_overwritten_by_defaults_on_load(self):
        saved = self.app_config.AppSettings(camera_index=4, fixed_interval=240.0)
        self.app_config.save_settings(saved, self.path)
        first = self.app_config.load_settings(self.path)
        second = self.app_config.load_settings(self.path)
        self.assertEqual(first.camera_index, 4)
        self.assertEqual(second.fixed_interval, 240.0)

    def test_save_is_atomic_and_leaves_no_temp_file(self):
        self.assertTrue(
            self.app_config.save_settings(self.app_config.AppSettings(), self.path)
        )
        self.assertTrue(self.path.exists())
        self.assertFalse(self.path.with_suffix(".tmp").exists())

    def test_default_path_is_in_per_user_app_data(self):
        path = self.app_config.default_config_path()
        self.assertEqual(path.name, "settings.json")
        self.assertEqual(path.parent.name, "Adaptive Brightness Webcam")


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class CameraSessionTests(unittest.TestCase):
    class ValidFrame:
        shape = (2, 2, 3)
        size = 12

    def make_camera(self, opened=True, fail_reads=False):
        outer = self

        class FakeCamera:
            def __init__(self):
                self.read_count = 0
                self.release_count = 0

            def isOpened(self):
                return opened

            def get(self, _prop):
                return 0.0

            def set(self, _prop, _value):
                return True

            def read(self):
                self.read_count += 1
                if fail_reads:
                    return False, None
                return True, outer.ValidFrame()

            def release(self):
                self.release_count += 1

        return FakeCamera()

    def measurement(self, value):
        return engine.AmbientMeasurement(value, 0.0, 0.0, 0.0, 0.0, 0.0)

    def test_session_opens_camera_once_for_many_samples(self):
        camera = self.make_camera()
        values = iter([100.0] * 15)
        with mock.patch.object(
            engine.cv2, "VideoCapture", return_value=camera
        ) as capture, mock.patch.object(
            engine, "ambient_measurement",
            side_effect=lambda _f: self.measurement(next(values)),
        ), mock.patch.object(engine.time, "sleep"):
            session = engine.CameraSession(0)
            results = [session.sample() for _ in range(3)]
            self.assertEqual(results, [100.0, 100.0, 100.0])
            self.assertEqual(capture.call_count, 1)
            self.assertEqual(session.open_count, 1)
            # Warm-up happens only on the single open: 8 + 5 + 5 + 5.
            self.assertEqual(camera.read_count, 8 + 5 * 3)
            self.assertEqual(camera.release_count, 0)
            session.close()
            self.assertEqual(camera.release_count, 1)

    def test_close_is_idempotent(self):
        camera = self.make_camera()
        values = iter([100.0] * 5)
        with mock.patch.object(
            engine.cv2, "VideoCapture", return_value=camera
        ), mock.patch.object(
            engine, "ambient_measurement",
            side_effect=lambda _f: self.measurement(next(values)),
        ), mock.patch.object(engine.time, "sleep"):
            session = engine.CameraSession(0)
            session.sample()
            session.close()
            session.close()
        self.assertEqual(camera.release_count, 1)
        self.assertFalse(session.is_open)

    def test_failed_open_is_rate_limited_by_cooldown(self):
        clock = FakeClock()
        camera = self.make_camera(opened=False)
        with mock.patch.object(
            engine.cv2, "VideoCapture", return_value=camera
        ) as capture, mock.patch.object(engine.time, "sleep"):
            session = engine.CameraSession(0, clock=clock, reopen_cooldown=10.0)
            self.assertIsNone(session.sample())
            self.assertIsNone(session.sample())
            self.assertEqual(capture.call_count, 1)
            clock.now += 11.0
            self.assertIsNone(session.sample())
            self.assertEqual(capture.call_count, 2)

    def test_dead_device_invalidates_session_and_recovers_after_cooldown(self):
        clock = FakeClock()
        state = {"failing": False}
        cameras = []

        outer = self

        class FlakyCamera:
            def __init__(self):
                self.read_count = 0
                self.release_count = 0
                cameras.append(self)

            def isOpened(self):
                return True

            def get(self, _prop):
                return 0.0

            def set(self, _prop, _value):
                return True

            def read(self):
                self.read_count += 1
                if state["failing"]:
                    return False, None
                return True, outer.ValidFrame()

            def release(self):
                self.release_count += 1

        values = iter([100.0] * 20)
        with mock.patch.object(
            engine.cv2, "VideoCapture", side_effect=lambda *_a, **_k: FlakyCamera()
        ) as capture, mock.patch.object(
            engine, "ambient_measurement",
            side_effect=lambda _f: self.measurement(next(values)),
        ), mock.patch.object(engine.time, "sleep"):
            session = engine.CameraSession(0, clock=clock, reopen_cooldown=10.0)
            self.assertEqual(session.sample(), 100.0)
            state["failing"] = True
            self.assertIsNone(session.sample())
            self.assertFalse(session.is_open)
            self.assertEqual(cameras[0].release_count, 1)
            # Cooldown prevents an immediate reopen storm.
            self.assertIsNone(session.sample())
            self.assertEqual(capture.call_count, 1)
            state["failing"] = False
            clock.now += 11.0
            self.assertEqual(session.sample(), 100.0)
            self.assertEqual(capture.call_count, 2)
            session.close()

    def test_dark_scene_is_not_a_device_failure(self):
        camera = self.make_camera()
        with mock.patch.object(
            engine.cv2, "VideoCapture", return_value=camera
        ), mock.patch.object(
            engine, "ambient_measurement",
            side_effect=lambda _f: self.measurement(0.5),
        ), mock.patch.object(engine.time, "sleep"):
            session = engine.CameraSession(0)
            self.assertIsNone(session.sample())
            self.assertTrue(session.is_open)
            session.close()


class BrightnessGateTests(unittest.TestCase):
    def make_gate(self):
        return engine.BrightnessChangeGate()

    def establish(self, gate, target=50):
        applied = None
        for index in range(6):
            result = gate.observe(target, applied, now=float(index * 30))
            if result is not None:
                applied = result
        return applied

    def test_startup_stabilization_blocks_early_changes(self):
        gate = self.make_gate()
        results = [gate.observe(50, None, now=float(i * 10)) for i in range(3)]
        self.assertEqual(results, [None, None, None])
        self.assertEqual(gate.observe(50, None, now=40.0), 50)

    def test_insignificant_fluctuations_never_apply(self):
        gate = self.make_gate()
        applied = self.establish(gate)
        self.assertEqual(applied, 50)
        for index, jitter in enumerate((51, 49, 52, 48, 50, 53, 47)):
            self.assertIsNone(
                gate.observe(jitter, applied, now=300.0 + index * 30)
            )

    def test_moderate_change_needs_confirmation(self):
        gate = self.make_gate()
        applied = self.establish(gate)
        self.assertIsNone(gate.observe(58, applied, now=300.0))
        self.assertEqual(gate.observe(58, applied, now=330.0), 58)

    def test_inconsistent_proposals_reset_confirmation(self):
        gate = self.make_gate()
        applied = self.establish(gate)
        self.assertIsNone(gate.observe(58, applied, now=300.0))
        self.assertIsNone(gate.observe(42, applied, now=330.0))
        self.assertIsNone(gate.observe(58, applied, now=360.0))
        self.assertEqual(gate.observe(58, applied, now=390.0), 58)

    def test_large_change_applies_immediately_but_step_is_limited(self):
        gate = self.make_gate()
        applied = self.establish(gate)
        # +40 requested: applied in one gated step of at most max_step.
        self.assertEqual(gate.observe(90, applied, now=300.0), 70)

    def test_rate_limit_blocks_rapid_successive_moderate_changes(self):
        gate = self.make_gate()
        applied = self.establish(gate)
        self.assertEqual(gate.observe(90, applied, now=300.0), 70)
        applied = 70
        self.assertIsNone(gate.observe(78, applied, now=305.0))
        self.assertIsNone(gate.observe(78, applied, now=310.0))
        # After the rate-limit window, confirmation applies the change.
        self.assertIsNone(gate.observe(78, applied, now=330.0))
        self.assertEqual(gate.observe(78, applied, now=360.0), 78)

    def test_none_proposal_is_ignored(self):
        gate = self.make_gate()
        self.assertIsNone(gate.observe(None, None, now=0.0))
        self.assertEqual(gate.observations, 0)


class AdaptiveSchedulingV2Tests(unittest.TestCase):
    def test_interval_bounds_are_not_user_configurable(self):
        fields = {field.name for field in dataclasses.fields(engine.AdaptiveSamplingConfig)}
        self.assertNotIn("minimum_interval", fields)
        self.assertNotIn("maximum_interval", fields)
        self.assertNotIn("initial_interval", fields)
        with self.assertRaises(TypeError):
            engine.AdaptiveSamplingConfig(minimum_interval=3.0)  # type: ignore[call-arg]

    def test_stable_environment_efficiency_over_simulated_hour(self):
        controller = engine.AdaptiveSamplingController()
        timestamp = 0.0
        samples = 0
        while timestamp < 3600.0:
            decision = controller.update(100.0, timestamp=timestamp)
            timestamp += decision.interval
            samples += 1
        # A fixed 5-second schedule would take 720 samples; autonomous
        # backoff needs only a few dozen while remaining responsive.
        self.assertLess(samples, 60)
        self.assertEqual(controller.current_interval, engine.SMART_MAX_INTERVAL)

    def test_single_noisy_sample_does_not_collapse_grown_interval(self):
        controller = engine.AdaptiveSamplingController()
        timestamp = 0.0
        while controller.current_interval < engine.SMART_MAX_INTERVAL:
            decision = controller.update(100.0, timestamp=timestamp)
            timestamp += decision.interval
        timestamp += controller.current_interval
        decision = controller.update(106.0, timestamp=timestamp)
        self.assertGreater(decision.interval, engine.SMART_MIN_INTERVAL)
        self.assertEqual(decision.state, "highly stable")

    def test_recovery_after_change_grows_interval_again(self):
        controller = engine.AdaptiveSamplingController()
        timestamp = 0.0
        for _ in range(6):
            decision = controller.update(100.0, timestamp=timestamp)
            timestamp += decision.interval
        decision = controller.update(220.0, timestamp=timestamp)
        self.assertEqual(decision.interval, engine.SMART_MIN_INTERVAL)
        intervals = []
        for _ in range(25):
            timestamp += controller.current_interval
            decision = controller.update(220.0, timestamp=timestamp)
            intervals.append(decision.interval)
        self.assertEqual(intervals[:3], [engine.SMART_MIN_INTERVAL] * 3)
        self.assertEqual(intervals, sorted(intervals))
        self.assertEqual(intervals[-1], engine.SMART_MAX_INTERVAL)

    def test_large_brightness_error_shortens_interval(self):
        controller = engine.AdaptiveSamplingController()
        timestamp = 0.0
        while controller.current_interval < engine.SMART_MAX_INTERVAL:
            decision = controller.update(100.0, timestamp=timestamp)
            timestamp += decision.interval
        timestamp += controller.current_interval
        decision = controller.update(
            101.0, timestamp=timestamp, brightness_error=15.0
        )
        self.assertLess(decision.interval, engine.SMART_MAX_INTERVAL)
        self.assertEqual(decision.state, "moderately changing")

    def test_small_brightness_error_does_not_block_stability(self):
        controller = engine.AdaptiveSamplingController()
        decision = None
        for index in range(16):
            decision = controller.update(
                100.0, timestamp=float(index * 10), brightness_error=2.0
            )
        self.assertGreater(decision.interval, engine.SMART_INITIAL_INTERVAL)


class SmartWorkerIntegrationTests(unittest.TestCase):
    @staticmethod
    def make_owner():
        import app

        owner = type("SmartWorkerOwner", (), {})()
        owner.stop_event = threading.Event()
        owner.worker_generation = 1
        owner.worker_thread = threading.current_thread()
        owner.camera_lock = threading.Lock()
        owner.brightness_lock = threading.Lock()
        owner.events = mock.Mock()
        owner.power_aware_interval = lambda _interval: 0
        owner.worker = app.BrightnessApp.worker.__get__(owner, type(owner))
        return owner

    def test_smart_worker_stabilizes_then_applies_without_persistent_camera(self):
        import app

        owner = self.make_owner()
        # Ambient sequence: stable 100 (target 38), tiny jitter, then a
        # jump to 220. The gate must ignore the first three proposals,
        # apply 38 once, ignore jitter, and ramp on the jump. Capture is
        # intermittent: sample_camera runs once per cycle and no
        # persistent CameraSession is ever created by the worker.
        ambient_values = iter([100.0, 100.0, 100.0, 100.0, 101.0, 99.0, 220.0])

        def sample(_camera, **_kwargs):
            try:
                return next(ambient_values)
            except StopIteration:
                owner.stop_event.set()
                return 220.0

        adaptive = mock.Mock()
        adaptive.current_interval = 0.0
        adaptive.update.return_value = engine.AdaptiveSamplingDecision(
            interval=0.0, state="relatively stable", trend=0.0,
            stability_score=1.0, filtered_value=100.0,
        )

        with mock.patch.object(
            app.engine, "sample_camera", side_effect=sample
        ) as sample_camera, mock.patch.object(
            app.engine, "CameraSession"
        ) as session_class, mock.patch.object(
            app.engine, "AdaptiveSamplingController", return_value=adaptive
        ), mock.patch.object(
            app.engine, "set_brightness"
        ) as set_brightness, mock.patch.object(
            app.engine, "ramp_brightness", return_value=True
        ) as ramp:
            owner.worker(
                0, 120.0, 1, owner.stop_event,
                app.SMART_ADAPTIVE_MODE, engine.AdaptiveSamplingConfig(),
            )

        session_class.assert_not_called()
        self.assertGreaterEqual(sample_camera.call_count, 7)
        set_brightness.assert_called_once_with(38)
        ramp.assert_called_once()
        self.assertEqual(ramp.call_args.args[:2], (38, 58))

    def test_smart_worker_camera_failure_keeps_loop_alive_until_stop(self):
        import app

        owner = self.make_owner()
        calls = {"count": 0}

        def failing_sample(_camera, **_kwargs):
            calls["count"] += 1
            if calls["count"] >= 3:
                owner.stop_event.set()
            return None

        adaptive = mock.Mock()
        adaptive.current_interval = 0.0

        with mock.patch.object(
            app.engine, "sample_camera", side_effect=failing_sample
        ), mock.patch.object(
            app.engine, "AdaptiveSamplingController", return_value=adaptive
        ), mock.patch.object(
            app.engine, "set_brightness"
        ) as set_brightness, mock.patch.object(
            app.engine, "ramp_brightness"
        ) as ramp:
            owner.worker(
                0, 120.0, 1, owner.stop_event,
                app.SMART_ADAPTIVE_MODE, engine.AdaptiveSamplingConfig(),
            )

        self.assertEqual(calls["count"], 3)
        set_brightness.assert_not_called()
        ramp.assert_not_called()


class IntermittentCaptureTests(unittest.TestCase):
    """Each measurement cycle: exactly one open, warm-up, capture, release."""

    class ValidFrame:
        shape = (2, 2, 3)
        size = 12

    def make_factory(self, fail_after=None):
        created = []
        outer = self

        class FakeCamera:
            def __init__(self):
                self.read_count = 0
                self.release_count = 0
                created.append(self)

            def isOpened(self):
                return True

            def get(self, _prop):
                return 0.0

            def set(self, _prop, _value):
                return True

            def read(self):
                self.read_count += 1
                if fail_after is not None and self.read_count > fail_after:
                    return False, None
                return True, outer.ValidFrame()

            def release(self):
                self.release_count += 1

        return created, FakeCamera

    def test_each_cycle_opens_and_releases_exactly_once(self):
        created, factory = self.make_factory()
        values = iter([100.0] * 10)
        with mock.patch.object(
            engine.cv2, "VideoCapture", side_effect=lambda *_a, **_k: factory()
        ) as capture, mock.patch.object(
            engine, "ambient_measurement",
            side_effect=lambda _f: engine.AmbientMeasurement(
                next(values), 0.0, 0.0, 0.0, 0.0, 0.0
            ),
        ), mock.patch.object(engine.time, "sleep"):
            first = engine.sample_camera(0)
            # Between cycles no camera object is alive: the first one
            # was already released before the second cycle begins.
            self.assertEqual(created[0].release_count, 1)
            second = engine.sample_camera(0)

        self.assertEqual((first, second), (100.0, 100.0))
        self.assertEqual(capture.call_count, 2)
        self.assertEqual(len(created), 2)
        for camera in created:
            self.assertEqual(camera.release_count, 1)
            self.assertEqual(
                camera.read_count,
                engine.CameraSession.WARMUP_FRAMES + engine.MEASUREMENT_SAMPLE_COUNT,
            )

    def test_warmup_frames_are_discarded_not_measured(self):
        # Frames carry their exposure level: 220 during warm-up reads
        # (unstable exposure), 100 afterwards. Only the five
        # post-warm-up frames may influence the measurement.
        outer = self

        class TaggedCamera:
            def __init__(self):
                self.read_count = 0
                self.release_count = 0

            def isOpened(self):
                return True

            def get(self, _prop):
                return 0.0

            def set(self, _prop, _value):
                return True

            def read(self):
                self.read_count += 1
                frame = outer.ValidFrame()
                frame.level = (
                    220.0
                    if self.read_count <= engine.CameraSession.WARMUP_FRAMES
                    else 100.0
                )
                return True, frame

            def release(self):
                self.release_count += 1

        with mock.patch.object(
            engine.cv2, "VideoCapture", side_effect=lambda *_a, **_k: TaggedCamera()
        ), mock.patch.object(
            engine, "ambient_measurement",
            side_effect=lambda f: engine.AmbientMeasurement(
                f.level, 0.0, 0.0, 0.0, 0.0, 0.0
            ),
        ) as measure, mock.patch.object(engine.time, "sleep"):
            result = engine.sample_camera(0)
        self.assertEqual(result, 100.0)
        self.assertEqual(measure.call_count, engine.MEASUREMENT_SAMPLE_COUNT)

    def test_mid_cycle_failure_still_releases_camera(self):
        created, factory = self.make_factory(fail_after=8)
        with mock.patch.object(
            engine.cv2, "VideoCapture", side_effect=lambda *_a, **_k: factory()
        ), mock.patch.object(
            engine, "ambient_measurement",
            side_effect=lambda _f: engine.AmbientMeasurement(
                100.0, 0.0, 0.0, 0.0, 0.0, 0.0
            ),
        ), mock.patch.object(engine.time, "sleep"):
            result = engine.sample_camera(0)
        self.assertIsNone(result)
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].release_count, 1)

    def test_scheduling_with_intermittent_capture_limits_activations(self):
        # Drive sample cycles from the adaptive scheduler over a
        # simulated stable hour: activations must stay sparse, and the
        # camera must be closed again after every single one.
        created, factory = self.make_factory()
        values = iter([100.0] * 500)
        controller = engine.AdaptiveSamplingController()
        timestamp = 0.0
        activations = 0
        with mock.patch.object(
            engine.cv2, "VideoCapture", side_effect=lambda *_a, **_k: factory()
        ), mock.patch.object(
            engine, "ambient_measurement",
            side_effect=lambda _f: engine.AmbientMeasurement(
                next(values), 0.0, 0.0, 0.0, 0.0, 0.0
            ),
        ), mock.patch.object(engine.time, "sleep"):
            while timestamp < 3600.0:
                value = engine.sample_camera(0)
                self.assertEqual(value, 100.0)
                activations += 1
                decision = controller.update(value, timestamp=timestamp)
                timestamp += decision.interval
        self.assertLess(activations, 60)
        self.assertEqual(len(created), activations)
        self.assertTrue(all(c.release_count == 1 for c in created))


class PipelineCalibrationTests(unittest.TestCase):
    """Regression tests for the FULL effective-brightness pipeline.

    The v1.1.0 bug: the isolated curve mapped 173 -> 58%, but a real
    bright-room scene reached 71-72% because _effective_ambient added
    up to +54 of scene boost, and its bright-scene gating preserved
    the full boost exactly when the scene was already bright. These
    tests exercise controller.update() (boost, dim logic, cap,
    filtering, hysteresis) rather than value_to_brightness() alone.
    """

    BRIGHT_SCENES_AT_173 = [
        engine.AmbientMeasurement(173.0, 173.0, 173.0, 173.0, 0.0, 0.0, 173.0),
        engine.AmbientMeasurement(173.0, 140.0, 190.0, 230.0, 90.0, 0.08, 110.0),
        engine.AmbientMeasurement(173.0, 150.0, 185.0, 215.0, 65.0, 0.06, 130.0),
        engine.AmbientMeasurement(173.0, 135.0, 195.0, 240.0, 105.0, 0.12, 100.0),
        engine.AmbientMeasurement(173.0, 160.0, 180.0, 195.0, 35.0, 0.02, 145.0),
    ]

    def test_user_scenario_bright_room_at_173(self):
        # The scene measured on the user's laptop (bright room, window
        # highlights) produced 71% before the fix.
        scene = self.BRIGHT_SCENES_AT_173[1]
        target = engine.BrightnessController().update(scene)
        self.assertGreaterEqual(target, 59)
        self.assertLessEqual(target, 63)

    def test_all_scene_shapes_at_173_stay_in_comfort_band(self):
        for scene in self.BRIGHT_SCENES_AT_173:
            with self.subTest(scene=scene):
                target = engine.BrightnessController().update(scene)
                self.assertGreaterEqual(target, 56)
                self.assertLessEqual(target, 63)

    def test_pipeline_never_exceeds_curve_plus_allowance(self):
        scene_shapes = [
            lambda v: engine.AmbientMeasurement(v, v, v, v, 0.0, 0.0, v),
            lambda v: engine.AmbientMeasurement(
                v, v * 0.8, v * 1.1, min(255.0, v * 1.35),
                min(255.0, v * 1.35) - v * 0.8, 0.09, v * 0.62,
            ),
            lambda v: engine.AmbientMeasurement(
                v, v * 0.72, v * 1.05, min(255.0, v * 1.4),
                min(255.0, v * 1.4) - v * 0.72, 0.03, v * 0.55,
            ),
        ]
        for value in (30.0, 60.0, 90.0, 120.0, 145.0, 160.0, 173.0, 190.0, 210.0, 235.0):
            for make in scene_shapes:
                with self.subTest(value=value):
                    target = engine.BrightnessController().update(make(value))
                    ceiling = engine.value_to_brightness(
                        value
                    ) + engine.scene_boost_allowance(value)
                    self.assertLessEqual(target, ceiling + 1)

    def test_exposure_compensation_cannot_bypass_the_cap(self):
        calibration = engine.ExposureCalibration()
        calibration.adjust(86.5, engine.CameraProperties(exposure=-6.0))
        adjusted = calibration.adjust(86.5, engine.CameraProperties(exposure=-7.0))
        self.assertEqual(adjusted, 173.0)
        scene = engine.AmbientMeasurement(
            adjusted, 140.0, 190.0, 230.0, 90.0, 0.08, 110.0
        )
        target = engine.BrightnessController().update(scene)
        self.assertLessEqual(target, 63)

    def test_dark_and_midrange_levels_are_preserved(self):
        # Plain scalar readings (no scene context) follow the bare
        # calibrated curve exactly; the cap must not distort them.
        self.assertEqual(engine.BrightnessController().update(40.0), 21)
        self.assertEqual(engine.BrightnessController().update(100.0), 38)
        self.assertEqual(engine.BrightnessController().update(240.0), 75)

    def test_boost_allowance_tapers_to_zero_in_bright_range(self):
        self.assertEqual(
            engine.scene_boost_allowance(100.0),
            engine.DEFAULT_BRIGHTNESS_CONFIG.boost_cap_points,
        )
        self.assertEqual(engine.scene_boost_allowance(220.0), 0.0)
        mid = engine.scene_boost_allowance(173.0)
        self.assertGreater(mid, 0.0)
        self.assertLess(mid, engine.DEFAULT_BRIGHTNESS_CONFIG.boost_cap_points)


if __name__ == "__main__":
    unittest.main()
