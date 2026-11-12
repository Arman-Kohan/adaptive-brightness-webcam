import logging
import sys
import threading
import unittest
from unittest import mock

import adaptive_brightness as engine
import numpy as np


class BrightnessEngineTests(unittest.TestCase):
    class ValidFrame:
        shape = (2, 2, 3)
        size = 12

    def make_camera(self, frames, opened=True):
        class FakeCamera:
            def __init__(self):
                self.read_count = 0
                self.released = False

            def isOpened(self):
                return opened

            def read(self):
                frame = frames[self.read_count]
                self.read_count += 1
                return frame

            def release(self):
                self.released = True

        return FakeCamera()

    def test_luminance_metric_tracks_scene_illumination(self):
        dark_frame = np.full((100, 100, 3), 80, dtype=np.uint8)
        bright_frame = np.full((100, 100, 3), 210, dtype=np.uint8)

        self.assertLess(
            engine.ambient_value(dark_frame),
            engine.ambient_value(bright_frame),
        )

    def test_environment_observations_use_exposure_aware_calibration(self):
        calibration = engine.ExposureCalibration()
        dark_room = calibration.adjust(
            139.6,
            engine.CameraProperties(exposure=-8.0, gain=12.0),
        )
        bright_room = calibration.adjust(
            125.4,
            engine.CameraProperties(exposure=-8.8, gain=28.0),
        )

        self.assertEqual(engine.value_to_brightness(dark_room), 49)
        self.assertGreater(bright_room, dark_room)
        self.assertGreaterEqual(engine.value_to_brightness(bright_room), 60)
        self.assertLessEqual(engine.value_to_brightness(bright_room), 70)

    def test_environment_observations_use_scene_context_without_changing_dark_anchor(self):
        dark_room = engine.AmbientMeasurement(
            value=139.6, p50=139.6, p75=145.0, p90=157.0,
            scene_spread=17.4, highlight_ratio=0.01,
            lower_quartile=132.0,
        )
        sunny_room = engine.AmbientMeasurement(
            value=125.4, p50=105.0, p75=170.0, p90=205.0,
            scene_spread=100.0, highlight_ratio=0.09,
            lower_quartile=82.0,
        )
        bright_room = engine.AmbientMeasurement(
            value=124.1, p50=108.0, p75=158.0, p90=205.0,
            scene_spread=80.0, highlight_ratio=0.06,
            lower_quartile=85.0,
        )

        dark_target = engine.BrightnessController().update(dark_room)
        sunny_target = engine.BrightnessController().update(sunny_room)
        bright_target = engine.BrightnessController().update(bright_room)

        self.assertEqual(dark_target, 49)
        self.assertGreaterEqual(sunny_target, 60)
        self.assertLessEqual(sunny_target, 70)
        self.assertGreaterEqual(bright_target, 54)
        self.assertLessEqual(bright_target, 62)

    def test_personalized_bright_scene_calibration_is_smooth_and_bounded(self):
        samples = [
            (145.0, 65, 95.0),
            (132.3, 60, 90.0),
            (123.0, 56, 85.0),
        ]
        targets = []
        for value, expected, spread in samples:
            measurement = engine.AmbientMeasurement(
                value=value,
                p50=value - 34.0,
                p75=value + 10.0,
                p90=value - 34.0 + spread,
                scene_spread=spread,
                highlight_ratio=0.06,
                lower_quartile=value - 50.0,
            )
            target = engine.BrightnessController().update(measurement)
            targets.append(target)
            self.assertLessEqual(abs(target - expected), 3)

        self.assertEqual(targets, sorted(targets, reverse=True))
        self.assertEqual(
            engine.BrightnessController().update(
                engine.AmbientMeasurement(
                    139.6, 139.6, 145.0, 157.0, 17.4, 0.01
                )
            ),
            49,
        )

    def test_controller_target_is_the_brightness_request_sent_to_windows(self):
        measurement = engine.AmbientMeasurement(
            value=123.5, p50=96.0, p75=135.0, p90=201.0,
            scene_spread=105.0, highlight_ratio=0.09,
            lower_quartile=72.0,
        )
        controller_target = engine.BrightnessController().update(measurement)
        with mock.patch.object(engine.subprocess, "run") as run:
            engine.set_brightness(controller_target)
        self.assertEqual(controller_target, 59)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[0][-1], "59")
        self.assertEqual(commands[1][-1], "59")

    def test_dim_sunset_scene_limits_boost_and_interpolates_downward(self):
        sunset = [
            engine.AmbientMeasurement(148.0, 123.0, 164.0, 208.0, 85.0, 0.011, 92.0),
            engine.AmbientMeasurement(152.6, 125.0, 173.0, 213.0, 88.0, 0.026, 94.0),
            engine.AmbientMeasurement(156.0, 129.0, 176.0, 215.0, 86.0, 0.038, 98.0),
        ]
        controller = engine.BrightnessController()
        targets = [controller.update(item) for item in sunset]
        self.assertLessEqual(targets[0], 60)
        self.assertLessEqual(targets[1], 59)
        self.assertLessEqual(targets[2], 56)
        self.assertGreaterEqual(targets[0], targets[1])
        self.assertGreaterEqual(targets[1], targets[2])

    def test_illuminated_sunset_scene_is_distinct_from_dim_sunset_scene(self):
        dim = [
            engine.AmbientMeasurement(148.0, 123.0, 164.0, 208.0, 85.0, 0.011, 92.0),
            engine.AmbientMeasurement(152.6, 125.0, 173.0, 213.0, 88.0, 0.026, 94.0),
            engine.AmbientMeasurement(156.0, 129.0, 176.0, 215.0, 86.0, 0.038, 98.0),
        ]
        illuminated = engine.AmbientMeasurement(
            156.2, 137.0, 173.0, 193.0, 56.0, 0.0, 120.0
        )
        dim_controller = engine.BrightnessController()
        dim_target = [dim_controller.update(item) for item in dim][-1]
        illuminated_controller = engine.BrightnessController()
        illuminated_target = illuminated_controller.update(illuminated)
        self.assertFalse(illuminated_controller.last_dim_scene)
        self.assertLessEqual(dim_target, 56)
        self.assertGreaterEqual(illuminated_target, 57)
        self.assertGreater(illuminated_target, dim_target)

    def test_lights_on_transition_resets_dim_scene_history(self):
        controller = engine.BrightnessController()
        dim = engine.AmbientMeasurement(
            156.0, 129.0, 176.0, 215.0, 86.0, 0.038, 98.0
        )
        illuminated = engine.AmbientMeasurement(
            156.2, 137.0, 173.0, 193.0, 56.0, 0.0, 120.0
        )
        controller.update(dim)
        controller.update(dim)
        target = controller.update(illuminated)
        self.assertFalse(controller.last_dim_scene)
        self.assertEqual(controller.sunset_rising_steps, 0)
        self.assertEqual(controller.last_trend_penalty, 0.0)
        self.assertGreaterEqual(target, 53)

    def test_sample_camera_uses_five_measurements_and_median(self):
        frames = [(True, self.ValidFrame()) for _ in range(13)]
        measurements = iter([100.0, 100.0, 12.0, 100.0, 100.0])
        camera = self.make_camera(frames)
        with mock.patch.object(engine.cv2, "VideoCapture", return_value=camera), \
             mock.patch.object(
                 engine,
                 "ambient_measurement",
                 side_effect=lambda _frame: engine.AmbientMeasurement(
                     next(measurements), 0.0, 0.0, 0.0, 0.0, 0.0
                 ),
             ), \
             mock.patch.object(engine.time, "sleep"):
            result = engine.sample_camera(0)

        self.assertEqual(result, 100.0)
        self.assertEqual(camera.read_count, 8 + engine.MEASUREMENT_SAMPLE_COUNT)

    def test_sample_camera_preserves_stable_response_and_maps_real_change(self):
        def sample(measurements):
            frames = [(True, self.ValidFrame()) for _ in range(8 + len(measurements))]
            measurements = iter(measurements)
            camera = self.make_camera(frames)
            with mock.patch.object(engine.cv2, "VideoCapture", return_value=camera), \
                 mock.patch.object(
                     engine,
                     "ambient_measurement",
                     side_effect=lambda _frame: engine.AmbientMeasurement(
                         next(measurements), 0.0, 0.0, 0.0, 0.0, 0.0
                     ),
                 ), \
                 mock.patch.object(engine.time, "sleep"):
                return engine.sample_camera(0)

        stable_low = sample([40.0] * engine.MEASUREMENT_SAMPLE_COUNT)
        stable_high = sample([140.0] * engine.MEASUREMENT_SAMPLE_COUNT)

        self.assertEqual(stable_low, 40.0)
        self.assertEqual(stable_high, 140.0)
        self.assertLess(engine.value_to_brightness(stable_low), engine.value_to_brightness(stable_high))

    def test_brightness_curve_targets_comfortable_indoor_level(self):
        self.assertEqual(engine.value_to_brightness(139.6), 49)
        # User reference point: ~170.9 ambient should map to ~57%, not 67%.
        self.assertEqual(engine.value_to_brightness(170.9), 57)

    def test_brightness_curve_is_monotonic_and_bounded(self):
        values = [engine.value_to_brightness(value) for value in (0, 20, 60, 120, 180, 240, 255)]
        self.assertEqual(values, sorted(values))
        self.assertGreaterEqual(min(values), engine.DEFAULT_BRIGHTNESS_CONFIG.brightness_min)
        self.assertLessEqual(max(values), engine.DEFAULT_BRIGHTNESS_CONFIG.brightness_max)

    def test_controller_deadband_and_hysteresis_hold_small_changes(self):
        controller = engine.BrightnessController()
        first = controller.update(100.0)
        self.assertEqual(first, engine.value_to_brightness(100.0))
        self.assertEqual(controller.update(101.0), first)
        self.assertEqual(controller.update(102.0), first)

    def test_controller_smooths_normal_changes_and_reacts_to_large_changes(self):
        controller = engine.BrightnessController()
        controller.update(40.0)
        normal = controller.update(80.0)
        self.assertEqual(controller.filtered_ambient, 54.0)
        self.assertEqual(engine.value_to_brightness(54.0), 25)
        self.assertEqual(normal, 25)
        self.assertLessEqual(
            abs(engine.value_to_brightness(54.0) - normal),
            engine.DEFAULT_BRIGHTNESS_CONFIG.brightness_hysteresis,
        )

        sudden = controller.update(240.0)
        self.assertEqual(controller.filtered_ambient, 202.8)
        self.assertEqual(sudden, engine.value_to_brightness(202.8))

    def test_controller_ignores_invalid_measurements(self):
        controller = engine.BrightnessController()
        self.assertIsNone(controller.update(float("nan")))
        self.assertIsNone(controller.update(float("inf")))
        self.assertIsNone(controller.update(-1.0))
        self.assertIsNone(controller.update(256.0))

    def test_sample_camera_uses_median_for_noisy_measurements(self):
        measurements = iter([45.0, 130.0, 52.0, 49.0, 180.0])
        frames = [(True, self.ValidFrame()) for _ in range(8 + engine.MEASUREMENT_SAMPLE_COUNT)]
        camera = self.make_camera(frames)

        with mock.patch.object(engine.cv2, "VideoCapture", return_value=camera), \
             mock.patch.object(
                 engine,
                 "ambient_measurement",
                 side_effect=lambda _frame: engine.AmbientMeasurement(
                     next(measurements), 0.0, 0.0, 0.0, 0.0, 0.0
                 ),
             ), \
             mock.patch.object(engine.time, "sleep"):
            result = engine.sample_camera(0)

        self.assertEqual(result, 52.0)
        self.assertTrue(camera.released)

    def test_sample_camera_returns_none_when_camera_cannot_open(self):
        camera = self.make_camera([], opened=False)

        with mock.patch.object(engine.cv2, "VideoCapture", return_value=camera):
            result = engine.sample_camera(2)

        self.assertIsNone(result)
        self.assertTrue(camera.released)
        self.assertEqual(camera.read_count, 0)

    def test_sample_camera_returns_none_for_failed_or_invalid_reads(self):
        frames = [(False, self.ValidFrame())] * 8 + [
            (False, self.ValidFrame()),
            (True, None),
            (True, self.ValidFrame()),
            (True, type("EmptyFrame", (), {"shape": (0, 0, 3), "size": 0})()),
            (False, self.ValidFrame()),
        ]
        camera = self.make_camera(frames)

        with mock.patch.object(engine.cv2, "VideoCapture", return_value=camera), \
             mock.patch.object(engine.time, "sleep"):
            result = engine.sample_camera(1)

        self.assertIsNone(result)
        self.assertTrue(camera.released)

    def test_sample_camera_handles_read_exception_and_releases_camera(self):
        frames = [(True, self.ValidFrame())] * 8
        camera = self.make_camera(frames)

        def failing_read():
            raise RuntimeError("capture failed")

        camera.read = failing_read
        with mock.patch.object(engine.cv2, "VideoCapture", return_value=camera), \
             mock.patch.object(engine.time, "sleep"):
            result = engine.sample_camera(1)

        self.assertIsNone(result)
        self.assertTrue(camera.released)

    def test_ramp_reports_cancellation(self):
        with mock.patch.object(engine, "set_brightness") as set_brightness:
            cancelled = engine.ramp_brightness(
                50,
                53,
                cancel_check=lambda: True,
            )

        self.assertFalse(cancelled)
        set_brightness.assert_not_called()

    def test_ramp_reports_completion(self):
        with mock.patch.object(engine, "set_brightness") as set_brightness, \
             mock.patch.object(engine.time, "sleep"):
            completed = engine.ramp_brightness(50, 53)

        self.assertTrue(completed)
        self.assertEqual(set_brightness.call_args_list, [mock.call(52), mock.call(53)])

    def test_set_brightness_preserves_command_order_and_arguments(self):
        with mock.patch.object(engine.subprocess, "run") as run:
            engine.set_brightness(42)

        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(
            commands,
            [
                [
                    "powercfg",
                    "/SETACVALUEINDEX",
                    "SCHEME_CURRENT",
                    "SUB_VIDEO",
                    engine.BRIGHTNESS_GUID,
                    "42",
                ],
                [
                    "powercfg",
                    "/SETDCVALUEINDEX",
                    "SCHEME_CURRENT",
                    "SUB_VIDEO",
                    engine.BRIGHTNESS_GUID,
                    "42",
                ],
                ["powercfg", "/S", "SCHEME_CURRENT"],
            ],
        )
        for call in run.call_args_list:
            self.assertTrue(call.kwargs["check"])
            self.assertTrue(call.kwargs["capture_output"])
            self.assertTrue(call.kwargs["text"])

    def test_set_brightness_reports_powercfg_diagnostics(self):
        failure = engine.subprocess.CalledProcessError(
            1,
            ["powercfg", "/SETACVALUEINDEX"],
        )
        failure.stderr = "The parameter is incorrect."
        failure.stdout = ""
        with mock.patch.object(engine.subprocess, "run", side_effect=failure):
            with self.assertLogs(level=logging.ERROR) as logs:
                with self.assertRaisesRegex(
                    RuntimeError, "AC brightness: The parameter is incorrect."
                ):
                    engine.set_brightness(42)

        self.assertIn("powercfg failed during AC brightness", logs.output[0])

    def test_cli_rejects_invalid_camera_and_interval(self):
        cases = [
            ["--camera", "-1", "--once"],
            ["--mode", "fixed", "--interval", "nan", "--once"],
            ["--mode", "fixed", "--interval", "inf", "--once"],
            ["--mode", "fixed", "--interval", "29", "--once"],
        ]
        for argv in cases:
            with self.subTest(argv=argv):
                with mock.patch.object(sys, "argv", ["adaptive_brightness.py", *argv]):
                    with self.assertRaises(SystemExit) as result:
                        engine.main()
                self.assertEqual(result.exception.code, 2)

    def test_cli_smart_mode_does_not_require_fixed_interval(self):
        argv = [
            "adaptive_brightness.py",
            "--mode",
            "smart",
            "--interval",
            "2",
            "--once",
        ]
        with mock.patch.object(sys, "argv", argv), mock.patch.object(
            engine, "sample_camera", return_value=None
        ):
            engine.main()

    def test_cli_fresh_default_uses_smart_mode(self):
        adaptive = mock.Mock(update=mock.Mock(return_value=mock.Mock(interval=5.0)))
        with mock.patch.object(
            sys, "argv", ["adaptive_brightness.py", "--once"]
        ), mock.patch.object(
            engine, "sample_camera", return_value=100.0
        ), mock.patch.object(
            engine, "AdaptiveSamplingController", return_value=adaptive
        ), mock.patch.object(
            engine, "BrightnessController", return_value=mock.Mock(update=lambda value: 40)
        ), mock.patch.object(
            engine, "set_brightness"
        ):
            engine.main()

        adaptive.update.assert_called_once_with(100.0, brightness_error=None)

    def test_cli_explicit_fixed_mode_does_not_use_adaptive_controller(self):
        adaptive = mock.Mock(update=mock.Mock())
        with mock.patch.object(
            sys, "argv", ["adaptive_brightness.py", "--mode", "fixed", "--once"]
        ), mock.patch.object(
            engine, "sample_camera", return_value=100.0
        ), mock.patch.object(
            engine, "AdaptiveSamplingController", return_value=adaptive
        ), mock.patch.object(
            engine, "BrightnessController", return_value=mock.Mock(update=lambda value: 40)
        ), mock.patch.object(
            engine, "set_brightness"
        ):
            engine.main()

        adaptive.update.assert_not_called()


class ConfigurationDefaultTests(unittest.TestCase):
    def test_fresh_configuration_defaults_to_smart_adaptive(self):
        import app

        self.assertEqual(app.resolve_sampling_mode(), app.SMART_ADAPTIVE_MODE)

    def test_explicit_fixed_mode_is_preserved(self):
        import app

        self.assertEqual(
            app.resolve_sampling_mode(app.FIXED_INTERVAL_MODE),
            app.FIXED_INTERVAL_MODE,
        )

    def test_existing_smart_preference_is_preserved(self):
        import app

        self.assertEqual(
            app.resolve_sampling_mode(app.SMART_ADAPTIVE_MODE),
            app.SMART_ADAPTIVE_MODE,
        )

    def test_interval_defaults_remain_mode_specific(self):
        self.assertEqual(engine.DEFAULT_FIXED_INTERVAL, 120.0)
        config = engine.AdaptiveSamplingConfig()
        # Smart bounds are internal constants now, not user settings.
        self.assertEqual(config.minimum_interval, engine.SMART_MIN_INTERVAL)
        self.assertEqual(config.maximum_interval, engine.SMART_MAX_INTERVAL)
        self.assertEqual(config.initial_interval, engine.SMART_INITIAL_INTERVAL)
        self.assertEqual(engine.SMART_MIN_INTERVAL, 5.0)
        self.assertEqual(engine.SMART_MAX_INTERVAL, 120.0)
        self.assertEqual(engine.SMART_INITIAL_INTERVAL, 8.0)

    def test_persisted_settings_default_to_smart_adaptive(self):
        import app_config

        settings = app_config.AppSettings()
        self.assertEqual(settings.sampling_mode, app_config.SMART_ADAPTIVE_MODE)
        self.assertEqual(settings.fixed_interval, 120.0)
        self.assertEqual(settings.camera_index, 0)


class SamplingModeUiTests(unittest.TestCase):
    def test_fixed_mode_shows_only_fixed_settings(self):
        import app

        self.assertEqual(
            app.sampling_mode_visibility(app.FIXED_INTERVAL_MODE),
            (True, False),
        )

    def test_smart_mode_shows_only_smart_settings(self):
        import app

        self.assertEqual(
            app.sampling_mode_visibility(app.SMART_ADAPTIVE_MODE),
            (False, True),
        )

    def test_switching_modes_refreshes_visible_settings_without_resetting_values(self):
        import app

        owner = type("SamplingModeOwner", (), {})()
        owner.mode_var = mock.Mock()
        owner.mode_hint_var = mock.Mock()
        owner.fixed_settings = mock.Mock()
        owner.adaptive_settings = mock.Mock()
        owner.interval_entry = mock.Mock()

        owner.mode_var.get.return_value = app.SMART_ADAPTIVE_MODE
        app.BrightnessApp._refresh_sampling_mode(owner)
        owner.fixed_settings.pack_forget.assert_called_once_with()
        owner.adaptive_settings.pack.assert_called_once_with(fill="x")
        owner.interval_entry.configure.assert_not_called()

        owner.fixed_settings.reset_mock()
        owner.adaptive_settings.reset_mock()
        owner.interval_entry.reset_mock()
        owner.mode_var.get.return_value = app.FIXED_INTERVAL_MODE
        app.BrightnessApp._refresh_sampling_mode(owner)
        owner.adaptive_settings.pack_forget.assert_called_once_with()
        owner.fixed_settings.pack.assert_called_once_with(fill="x")
        owner.interval_entry.configure.assert_called_once_with(state="normal")

    def test_hidden_mode_values_are_not_used_by_start_validation(self):
        import app

        self.assertEqual(
            app.sampling_mode_visibility(app.SMART_ADAPTIVE_MODE),
            (False, True),
        )
        self.assertEqual(
            app.sampling_mode_visibility(app.FIXED_INTERVAL_MODE),
            (True, False),
        )

    def test_mode_help_matches_selected_mode(self):
        import app

        self.assertIn(
            "configured schedule",
            app.sampling_mode_description(app.FIXED_INTERVAL_MODE),
        )
        self.assertIn(
            "automatically",
            app.sampling_mode_description(app.SMART_ADAPTIVE_MODE),
        )
        self.assertNotIn(
            "configured limits",
            app.sampling_mode_description(app.SMART_ADAPTIVE_MODE),
        )


class AdaptiveSamplingTests(unittest.TestCase):
    @staticmethod
    def percentile(values, percentile):
        ordered = sorted(values)
        position = (len(ordered) - 1) * percentile
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        fraction = position - lower
        return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction

    def test_smart_internal_maximum_interval_is_one_hundred_twenty_seconds(self):
        self.assertEqual(
            engine.AdaptiveSamplingConfig().maximum_interval,
            engine.SMART_MAX_INTERVAL,
        )
        self.assertEqual(engine.SMART_MAX_INTERVAL, 120.0)
        self.assertEqual(engine.SMART_MIN_INTERVAL, 5.0)

    def test_stable_environment_progressively_reaches_maximum(self):
        controller = engine.AdaptiveSamplingController()
        intervals = [
            controller.update(100.0, timestamp=float(index)).interval
            for index in range(20)
        ]

        self.assertEqual(intervals[0], engine.SMART_INITIAL_INTERVAL)
        self.assertEqual(intervals, sorted(intervals))
        self.assertEqual(intervals[-1], engine.SMART_MAX_INTERVAL)
        self.assertEqual(controller.state, "highly stable")

    def test_interval_never_exceeds_internal_maximum(self):
        controller = engine.AdaptiveSamplingController()
        timestamp = 0.0
        while controller.current_interval < engine.SMART_MAX_INTERVAL:
            decision = controller.update(100.0, timestamp=timestamp)
            timestamp += decision.interval

        self.assertEqual(decision.interval, engine.SMART_MAX_INTERVAL)
        for _ in range(5):
            decision = controller.update(100.0, timestamp=timestamp)
            timestamp += decision.interval
            self.assertLessEqual(decision.interval, engine.SMART_MAX_INTERVAL)

    def test_sudden_change_quickly_enters_responsive_state(self):
        controller = engine.AdaptiveSamplingController()
        for index in range(10):
            controller.update(80.0, timestamp=float(index))
        before = controller.current_interval

        decision = controller.update(220.0, timestamp=10.0)

        self.assertLess(decision.interval, before)
        self.assertEqual(decision.interval, engine.SMART_MIN_INTERVAL)
        self.assertEqual(decision.state, "rapidly changing")
        self.assertGreater(decision.trend, 0.0)

    def test_noise_does_not_change_interval_or_history_state(self):
        controller = engine.AdaptiveSamplingController()
        for index, value in enumerate((100.0, 101.0, 99.0, 100.5, 98.5)):
            decision = controller.update(value, timestamp=float(index))

        self.assertEqual(decision.state, "relatively stable")
        self.assertGreaterEqual(decision.interval, engine.SMART_INITIAL_INTERVAL)
        self.assertEqual(len(controller.history), 5)

    def test_invalid_measurements_do_not_enter_history(self):
        controller = engine.AdaptiveSamplingController()
        controller.update(100.0, timestamp=0.0)
        before = controller.current_interval

        decision = controller.update(float("nan"), timestamp=1.0)

        self.assertFalse(decision.valid)
        self.assertEqual(len(controller.history), 1)
        self.assertEqual(controller.current_interval, before)
        self.assertEqual(decision.filtered_value, 100.0)

    def test_interval_limits_are_respected_during_change_and_recovery(self):
        controller = engine.AdaptiveSamplingController()
        values = [20.0, 220.0, 20.0, 220.0] * 8
        intervals = [
            controller.update(value, timestamp=float(index)).interval
            for index, value in enumerate(values)
        ]

        self.assertGreaterEqual(min(intervals), engine.SMART_MIN_INTERVAL)
        self.assertLessEqual(max(intervals), engine.SMART_MAX_INTERVAL)

    def test_gradual_transition_is_not_treated_as_camera_failure(self):
        controller = engine.AdaptiveSamplingController()
        decisions = [
            controller.update(60.0 + index * 2.0, timestamp=float(index * 5))
            for index in range(10)
        ]

        self.assertTrue(all(decision.valid for decision in decisions))
        self.assertTrue(all(decision.filtered_value is not None for decision in decisions))
        self.assertNotEqual(decisions[-1].state, "rapidly changing")

    def test_first_sample_has_no_stability_claim(self):
        decision = engine.AdaptiveSamplingController().update(100.0, timestamp=0.0)

        self.assertEqual(decision.state, "unknown")
        self.assertEqual(decision.stability_score, 0.0)

    def test_repeated_transitions_reenter_minimum_interval(self):
        controller = engine.AdaptiveSamplingController()
        for index in range(20):
            controller.update(100.0, timestamp=float(index))

        changed = controller.update(220.0, timestamp=20.0)
        recovered = controller.update(220.0, timestamp=22.0)
        changed_back = controller.update(20.0, timestamp=24.0)

        self.assertEqual(changed.interval, engine.SMART_MIN_INTERVAL)
        self.assertEqual(recovered.interval, engine.SMART_MIN_INTERVAL)
        self.assertEqual(changed_back.interval, engine.SMART_MIN_INTERVAL)
        self.assertEqual(changed_back.state, "rapidly changing")

    def test_stability_score_reflects_noise_after_noise_adaptation(self):
        controller = engine.AdaptiveSamplingController()
        decisions = [
            controller.update(value, timestamp=float(index))
            for index, value in enumerate((100, 112, 94, 108, 97, 103))
        ]

        self.assertLess(decisions[-1].stability_score, 1.0)
        self.assertGreaterEqual(decisions[-1].stability_score, 0.0)

    def test_worst_case_change_immediately_after_max_interval(self):
        controller = engine.AdaptiveSamplingController()
        timestamp = 0.0
        while controller.current_interval < controller.config.maximum_interval:
            decision = controller.update(100.0, timestamp=timestamp)
            timestamp += decision.interval

        event_timestamp = timestamp + 0.001
        detection_timestamp = timestamp + controller.current_interval
        decision = controller.update(220.0, timestamp=detection_timestamp)

        self.assertEqual(controller.current_interval, engine.SMART_MIN_INTERVAL)
        self.assertEqual(decision.state, "rapidly changing")
        self.assertAlmostEqual(
            detection_timestamp - event_timestamp,
            engine.SMART_MAX_INTERVAL - 0.001,
            places=3,
        )

    def test_rapid_change_holds_minimum_interval_for_confirmation_samples(self):
        controller = engine.AdaptiveSamplingController()
        timestamp = 0.0
        while controller.current_interval < controller.config.maximum_interval:
            decision = controller.update(100.0, timestamp=timestamp)
            timestamp += decision.interval

        timestamp += controller.current_interval
        controller.update(220.0, timestamp=timestamp)
        confirmation_intervals = []
        for _ in range(5):
            timestamp += controller.current_interval
            decision = controller.update(220.0, timestamp=timestamp)
            confirmation_intervals.append(decision.interval)

        self.assertEqual(confirmation_intervals, [engine.SMART_MIN_INTERVAL] * 5)

    def test_percentile_latency_calculation_is_deterministic(self):
        latencies = [0.0, 15.0, 30.0, 45.0, 60.0]

        self.assertEqual(self.percentile(latencies, 0.5), 30.0)
        self.assertEqual(self.percentile(latencies, 0.95), 57.0)
        self.assertEqual(self.percentile(latencies, 1.0), 60.0)


class WorkerLifecycleTests(unittest.TestCase):
    @staticmethod
    def make_worker_owner():
        import app

        owner = type("WorkerOwner", (), {})()
        owner.stop_event = threading.Event()
        owner.worker_generation = 1
        owner.worker_thread = threading.current_thread()
        owner.camera_lock = threading.Lock()
        owner.brightness_lock = threading.Lock()
        owner.events = mock.Mock()
        owner.power_aware_interval = lambda _interval: 0
        owner.worker = app.BrightnessApp.worker.__get__(owner, type(owner))
        return owner

    def run_targets(self, targets):
        import app

        owner = self.make_worker_owner()
        owner.stop_event = threading.Event()
        values = iter(targets)

        def sample(_camera, **_kwargs):
            try:
                target = next(values)
            except StopIteration:
                owner.stop_event.set()
                target = targets[-1]
            return target

        with mock.patch.object(app.engine, "sample_camera", side_effect=sample), \
             mock.patch.object(
                 app.engine,
                 "BrightnessController",
                 return_value=mock.Mock(update=lambda value: value),
             ), \
             mock.patch.object(app.engine, "set_brightness") as set_brightness, \
             mock.patch.object(app.engine, "ramp_brightness") as ramp:
            owner.worker(0, 30.0, 1, owner.stop_event)

        return set_brightness, ramp

    def test_gradual_targets_compare_against_last_commanded_target(self):
        set_brightness, ramp = self.run_targets([50, 51, 52, 53])

        set_brightness.assert_called_once_with(50)
        ramp.assert_called_once_with(50, 53, mock.ANY)

    def test_repeated_and_downward_targets_keep_commanded_state(self):
        set_brightness, ramp = self.run_targets([50, 50, 51, 47])

        set_brightness.assert_called_once_with(50)
        ramp.assert_called_once_with(50, 47, mock.ANY)

    def test_command_failure_does_not_update_commanded_target(self):
        import app

        owner = self.make_worker_owner()
        owner.stop_event = threading.Event()
        values = iter([50, 53])

        def sample(_camera, **_kwargs):
            value = next(values)
            return value

        def fail_ramp(*_args):
            owner.stop_event.set()
            raise RuntimeError("brightness command failed")

        with mock.patch.object(app.engine, "sample_camera", side_effect=sample), \
             mock.patch.object(
                 app.engine,
                 "BrightnessController",
                 return_value=mock.Mock(update=lambda value: value),
             ), \
             mock.patch.object(app.engine, "set_brightness") as set_brightness, \
             mock.patch.object(app.engine, "ramp_brightness", side_effect=fail_ramp):
            owner.worker(0, 30.0, 1, owner.stop_event)

        set_brightness.assert_called_once_with(50)

    def test_ramp_cancellation_does_not_update_commanded_target(self):
        import app

        owner = self.make_worker_owner()
        owner.stop_event = threading.Event()
        values = iter([50, 53, 54])

        def sample(_camera, **_kwargs):
            value = next(values)
            return value

        def cancel_ramp(*_args):
            owner.stop_event.set()
            return False

        with mock.patch.object(app.engine, "sample_camera", side_effect=sample), \
             mock.patch.object(
                 app.engine,
                 "BrightnessController",
                 return_value=mock.Mock(update=lambda value: value),
             ), \
             mock.patch.object(app.engine, "set_brightness") as set_brightness, \
             mock.patch.object(app.engine, "ramp_brightness", side_effect=cancel_ramp) as ramp:
            owner.worker(0, 30.0, 1, owner.stop_event)

        set_brightness.assert_called_once_with(50)
        ramp.assert_called_once_with(50, 53, mock.ANY)

    def test_stale_worker_does_not_apply_brightness(self):
        import app

        owner = self.make_worker_owner()
        owner.worker_generation = 2
        with mock.patch.object(
            app.engine, "sample_camera", return_value=100
        ), mock.patch.object(app.engine, "set_brightness") as set_brightness:
            owner.worker(0, 30.0, 1, owner.stop_event)

        set_brightness.assert_not_called()

    def test_generation_change_after_capture_does_not_apply_brightness(self):
        import app

        owner = self.make_worker_owner()

        def capture(_camera, **_kwargs):
            owner.worker_generation = 2
            owner.stop_event.set()
            return 100

        with mock.patch.object(
            app.engine, "sample_camera", side_effect=capture
        ), mock.patch.object(app.engine, "set_brightness") as set_brightness:
            owner.worker(0, 30.0, 1, owner.stop_event)

        set_brightness.assert_not_called()

    def test_worker_stop_event_prevents_second_sample(self):
        import app

        owner = self.make_worker_owner()
        owner.stop_event.set()
        with mock.patch.object(app.engine, "sample_camera") as sample_camera:
            owner.worker(0, 30.0, 1, owner.stop_event)

        sample_camera.assert_not_called()

    def test_capture_failure_does_not_issue_brightness_command(self):
        import app

        owner = self.make_worker_owner()

        def unavailable(_camera, **_kwargs):
            owner.stop_event.set()
            return None

        with mock.patch.object(app.engine, "sample_camera", side_effect=unavailable), \
             mock.patch.object(app.engine, "set_brightness") as set_brightness, \
             mock.patch.object(app.engine, "ramp_brightness") as ramp:
            owner.worker(0, 30.0, 1, owner.stop_event)

        set_brightness.assert_not_called()
        ramp.assert_not_called()

    def test_smart_worker_uses_adaptive_interval_without_battery_multiplier(self):
        import app

        owner = self.make_worker_owner()
        owner.stop_event = threading.Event()
        owner.power_aware_interval = mock.Mock(return_value=999.0)

        session = mock.Mock()

        def sample_detailed():
            owner.stop_event.set()
            return 100.0

        session.sample_detailed.side_effect = sample_detailed

        with mock.patch.object(
                 app.engine, "CameraSession", return_value=session
             ), \
             mock.patch.object(
                 app.engine,
                 "BrightnessController",
                 return_value=mock.Mock(update=lambda value: 30),
             ), \
             mock.patch.object(app.engine, "set_brightness"), \
             mock.patch.object(app.engine, "AdaptiveSamplingController") as adaptive_class:
            adaptive = adaptive_class.return_value
            adaptive.current_interval = 5.0
            adaptive.update.return_value = app.engine.AdaptiveSamplingDecision(
                interval=5.0,
                state="unknown",
                trend=0.0,
                stability_score=0.0,
                filtered_value=100.0,
            )
            owner.worker(
                0,
                120.0,
                1,
                owner.stop_event,
                "Smart Adaptive",
                app.engine.AdaptiveSamplingConfig(),
            )

        owner.power_aware_interval.assert_not_called()


if __name__ == "__main__":
    unittest.main()
