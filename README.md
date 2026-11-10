# Adaptive Brightness Webcam

<p align="center">
  <img src="assets/adaptive-brightness-poster.png" alt="Adaptive Brightness Webcam" width="100%">
</p>

<p align="center">
  <strong>Comfortable screen brightness, automatically tuned to your room.</strong><br>
  A lightweight Windows app that uses short webcam light samples to adjust display brightness.
</p>

<p align="center">
  <a href="https://github.com/Arman-Kohan/adaptive-brightness-webcam/releases"><img src="https://img.shields.io/github/v/release/Arman-Kohan/adaptive-brightness-webcam?display_name=tag&style=flat-square&color=0A6CFF" alt="Latest release"></a>
  <a href="https://github.com/Arman-Kohan/adaptive-brightness-webcam/actions/workflows/build-check.yml"><img src="https://img.shields.io/github/actions/workflow/status/Arman-Kohan/adaptive-brightness-webcam/build-check.yml?branch=main&style=flat-square&label=build" alt="Build status"></a>
  <a href="https://github.com/Arman-Kohan/adaptive-brightness-webcam/blob/main/LICENSE"><img src="https://img.shields.io/github/license/Arman-Kohan/adaptive-brightness-webcam?style=flat-square" alt="MIT License"></a>
  <a href="https://github.com/Arman-Kohan/adaptive-brightness-webcam"><img src="https://img.shields.io/github/stars/Arman-Kohan/adaptive-brightness-webcam?style=flat-square" alt="GitHub stars"></a>
</p>

<p align="center">
  <a href="README.fa.md">راهنمای فارسی</a>
  ·
  <a href="https://github.com/Arman-Kohan/adaptive-brightness-webcam/releases">Download</a>
  ·
  <a href="https://github.com/Arman-Kohan/adaptive-brightness-webcam/issues">Suggest an improvement</a>
</p>

## Why Adaptive Brightness Webcam?

Most automatic brightness tools either depend on a dedicated light sensor or react too aggressively to noisy camera readings. This project takes a calmer approach: it samples the webcam briefly, analyzes the scene, filters unstable measurements, and changes brightness only when the result is meaningful.

The result is a small, privacy-conscious utility that is especially useful on Windows laptops with built-in displays.

## Highlights

- **Automatic brightness control** based on short webcam samples.
- **Two sampling modes:** keep a predictable fixed interval or use Smart Adaptive mode.
- **Privacy first:** frames are processed in memory; no images or videos are saved or uploaded.
- **Stable transitions:** median sampling, exposure calibration, smoothing, deadband, hysteresis, and gradual brightness ramps reduce flicker.
- **Scene-aware measurement:** analyzes the center 80% of the frame, luminance percentiles, contrast, highlights, and dim-scene behavior.
- **Simple desktop UI:** test the camera, start or stop automation, and see the latest light estimate and target brightness.
- **System tray support:** keep automation running quietly in the background.
- **Windows startup option:** launch automatically when you sign in.
- **Power-aware behavior:** checks are spaced out further on battery to reduce webcam wakeups.
- **Light and dark appearance:** follow the Windows theme or switch automatically between day and night.
- **Portable release:** download and run the Windows executable without installing Python.

## Download and run

1. Open the [latest Releases](https://github.com/Arman-Kohan/adaptive-brightness-webcam/releases).
2. Download `AdaptiveBrightnessWebcam.exe`, or download `AdaptiveBrightnessWebcam-Windows.zip` for the portable package.
3. Extract the ZIP if necessary and run `AdaptiveBrightnessWebcam.exe`.
4. Choose the webcam index. `Camera 0` is usually the built-in webcam.
5. Click **Test camera**, choose a sampling mode, and click **Start automation**.

No Python installation, administrator privileges, or installer is required for the packaged release.

### Recommended first setup

For a comfortable starting point:

- Keep the webcam uncovered and pointed toward the room.
- Avoid pointing it directly at a lamp or a bright window.
- New installations start with **Smart Adaptive**, using a 60-second maximum by default for faster response during changes and fewer camera wakeups when the room is stable. **Fixed Interval** remains available with its existing 120-second default; advanced Smart mode users can raise its maximum to 120 seconds.
- Use **Test camera** before starting automation.
- If you use an external monitor, verify that it supports Windows brightness control.

## How it works

For each check, the app:

1. Opens the selected webcam through OpenCV and DirectShow.
2. Lets the camera warm up briefly so auto-exposure can settle.
3. Captures five valid samples and uses the median measurement.
4. Analyzes the center 80% of the frame using luminance percentiles (`p25`, `p50`, `p75`, `p90`).
5. Accounts for scene contrast, near-white highlights, supported exposure values, and dim-scene patterns.
6. Maps the result through a configurable comfort curve.
7. Applies smoothing and hysteresis to avoid flicker.
8. Sends the target to Windows with `powercfg`; larger changes are ramped gradually.
9. Releases the webcam immediately after sampling.

In **Smart Adaptive** mode, `AdaptiveSamplingController` keeps a short history of
valid observations, estimates camera noise with a rolling median/MAD, and filters
the signal with an EMA. It requires consecutive stable observations before
increasing the interval, grows it progressively toward the configured maximum
(60 seconds by default), and quickly reduces it toward the configured minimum
after a sustained or rapid change. After a rapid change it remains at the
minimum for several confirming samples before growing again.
Invalid samples never enter the history. Fixed Interval mode bypasses this
controller and retains the existing scheduling behavior.

The displayed camera value is a relative, camera-derived luminance estimate—not a physical lux measurement. Different cameras, framing, exposure, and white balance can produce different values in the same room.

## Settings

| Setting | What it does |
|---|---|
| **Camera** | Selects the webcam index used for sampling. |
| **Sampling mode** | **Fixed Interval** preserves a user-selected schedule. **Smart Adaptive** adjusts between configurable 2–60 second limits by default; 120 seconds remains available as an advanced upper limit. |
| **Check interval** | Fixed Interval schedule. Minimum: 30 seconds. The default is 120 seconds. This control is shown only in Fixed Interval mode. |
| **Smart Mode Limits** | Smart Adaptive minimum and maximum intervals. Defaults: 2 seconds minimum, 60 seconds maximum, and a 5-second initial interval. These controls are shown only in Smart Adaptive mode. |
| **Appearance** | **Auto** follows day/night; **Windows** follows the system theme. |
| **Launch with Windows** | Starts the app automatically when you sign in. |
| **Close to tray** | Hides the window while leaving the app running in the system tray. |

When running on battery, Fixed Interval mode automatically increases the effective wait between checks. Smart Adaptive mode uses its configured interval bounds directly so a sudden change is not delayed by a second, hidden multiplier. Smart Adaptive also shows the current interval, stability state, trend, and whether the next wait is increasing or decreasing.

## Privacy and compatibility

- The webcam is active only during a short warm-up and sampling window. The camera privacy indicator may turn on during that time.
- The DirectShow backend may not honor OpenCV's optional read-timeout property; capture is released after each sample, but a device driver that blocks inside `read()` can still delay shutdown until the driver returns.
- Frames are processed in memory and are not saved, uploaded, or transmitted by this application.
- Diagnostic logs are written to `%USERPROFILE%\.adaptive-brightness-webcam.log`.
- Brightness control works best with internal laptop displays.
- Some external monitors, docks, virtual displays, and vendor-specific drivers do not expose brightness control through Windows.
- The app requires Windows 10/11, a camera permission, a valid webcam index, and a display that accepts the Windows `powercfg` brightness commands.

## Troubleshooting

### The camera test fails

Try camera index `1` or `2`, check Windows camera permissions, and close other apps that may be using the webcam. The selected camera must be available through the DirectShow backend.

### Brightness does not change

Many external monitors do not accept software brightness commands from Windows. Check the display's hardware controls or vendor software, then inspect `%USERPROFILE%\.adaptive-brightness-webcam.log` for `powercfg` errors.

### The response is too slow or too bright

Reduce the fixed interval, make sure the camera is facing the room, and adjust `BrightnessConfig` in `adaptive_brightness.py`. Fixed Interval battery mode intentionally adds extra delay; Smart Adaptive uses its configured bounds.

## Manual webcam validation

These checks require a real Windows webcam and display. They are not part of the
automated test suite, and passing them depends on camera exposure behavior,
lighting, and whether the display accepts Windows brightness commands.

1. Select the webcam, click **Test camera**, and confirm that a numeric ambient
   value and target brightness are shown. Check that the camera privacy indicator
   turns off after the short sample.
2. Select **Smart Adaptive**, keep the default 2–60 second limits, start
   automation, and leave the room stable for at least 10 minutes. Confirm that
   the diagnostic interval grows toward 60 seconds and the camera is active only
   during samples.
3. Move from a dark room into a bright room, then repeat from bright to dark.
   Confirm that the next reading changes the target and that the displayed
   interval falls to 2 seconds for confirmation samples before growing again.
4. Turn room lights off and on several times. Confirm meaningful brightness
   changes occur without rapid oscillation from camera noise.
5. During gradual daylight change, record the displayed ambient value, trend,
   stability state, and interval every few minutes. Confirm the values move
   smoothly rather than jumping on every exposure adjustment.
6. Pass a hand or object briefly in front of the camera. Confirm that a
   short-lived shadow may be missed when the camera is sleeping, but if sampled
   it does not leave the controller stuck at the minimum interval.
7. Disconnect or disable the camera while automation is running, then restore
   it. Confirm the app reports a camera warning, remains responsive, and resumes
   sampling after the device becomes available.
8. While running, switch between **Fixed Interval** and **Smart Adaptive**.
   The current run stops explicitly; start automation again to apply the newly
   selected mode. Verify Fixed Interval retains its configured value and Smart
   Adaptive starts with its configured limits.
9. Repeat the stable-lighting check on AC power and battery power. Fixed
   Interval applies its existing battery delay; Smart Adaptive uses its own
   configured bounds without an additional battery multiplier.

## For developers

### Requirements

- Windows 10 or Windows 11
- Python 3.10+ recommended
- A webcam supported by OpenCV

### Run from source

```powershell
python -m pip install -r requirements.txt
python app.py
```

Run the controller without the graphical interface in Fixed Interval mode:

```powershell
python adaptive_brightness.py --camera 0 --mode fixed --interval 120
```

Run the controller with adaptive sampling:

```powershell
python adaptive_brightness.py --camera 0 --mode smart --minimum-interval 2 --maximum-interval 60
```

The CLI defaults to Smart Adaptive mode. Use `--mode fixed` explicitly when
you want the fixed schedule and its 120-second default.

Take one sample and exit:

```powershell
python adaptive_brightness.py --camera 0 --once
```

Run the tests:

```powershell
python -m unittest discover -s tests -p "test_*.py"
```

Build the Windows executable:

```powershell
.\build.ps1
```

The build produces `dist\AdaptiveBrightnessWebcam.exe`. The release workflow packages the executable and portable ZIP whenever a `v*` tag is pushed.

## Contributing and feedback

Found a bug, have a calibration idea, or want support for another display or camera setup? Please [open an issue](https://github.com/Arman-Kohan/adaptive-brightness-webcam/issues) with:

- Windows version and display type
- Camera model or camera index
- What you expected and what happened
- Relevant log output, with any private information removed

If this project is useful to you, consider leaving a **star** on GitHub. It helps the project become more visible and motivates future improvements.

## License

Released under the [MIT License](LICENSE).
