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

        self.assertEqual(engine.value_to_brightness(dark_room), 34)
        self.assertGreater(bright_room, dark_room)
        self.assertGreaterEqual(engine.value_to_brightness(bright_room), 60)
        self.assertLessEqual(engine.value_to_brightness(bright_room), 70)

    def test_sample_camera_uses_five_measurements_and_median(self):
        frames = [(True, self.ValidFrame()) for _ in range(13)]
        measurements = iter([100.0, 100.0, 12.0, 100.0, 100.0])
        camera = self.make_camera(frames)
        with mock.patch.object(engine.cv2, "VideoCapture", return_value=camera), \
             mock.patch.object(engine, "ambient_value", side_effect=lambda _frame: next(measurements)), \
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
                 mock.patch.object(engine, "ambient_value", side_effect=lambda _frame: next(measurements)), \
                 mock.patch.object(engine.time, "sleep"):
                return engine.sample_camera(0)

        stable_low = sample([40.0] * engine.MEASUREMENT_SAMPLE_COUNT)
        stable_high = sample([140.0] * engine.MEASUREMENT_SAMPLE_COUNT)

        self.assertEqual(stable_low, 40.0)
        self.assertEqual(stable_high, 140.0)
        self.assertLess(engine.value_to_brightness(stable_low), engine.value_to_brightness(stable_high))

    def test_brightness_curve_targets_comfortable_indoor_level(self):
        self.assertEqual(engine.value_to_brightness(139.6), 34)
        self.assertLess(engine.value_to_brightness(139.6), 44)

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
        self.assertEqual(engine.value_to_brightness(54.0), 14)
        self.assertEqual(normal, 12)
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
             mock.patch.object(engine, "ambient_value", side_effect=lambda _frame: next(measurements)), \
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
            ["--interval", "nan", "--once"],
            ["--interval", "inf", "--once"],
            ["--interval", "29", "--once"],
        ]
        for argv in cases:
            with self.subTest(argv=argv):
                with mock.patch.object(sys, "argv", ["adaptive_brightness.py", *argv]):
                    with self.assertRaises(SystemExit) as result:
                        engine.main()
                self.assertEqual(result.exception.code, 2)


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


if __name__ == "__main__":
    unittest.main()
