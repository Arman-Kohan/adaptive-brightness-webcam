# Adaptive Brightness Webcam

<p align="center">
  <img src="assets/adaptive-brightness-poster.png" alt="Adaptive Brightness Webcam" width="100%">
</p>

<p align="center">
  <strong>Comfortable screen brightness, automatically tuned to your room.</strong><br>
  A lightweight Windows app that samples ambient light through your webcam and adjusts your laptop's display brightness to match — verified on the actual display, every time.
</p>

<p align="center">
  <a href="https://github.com/Arman-Kohan/adaptive-brightness-webcam/releases/tag/v1.1.0"><img src="https://img.shields.io/badge/release-v1.1.0-blue" alt="Release v1.1.0"></a>
  <a href="https://github.com/Arman-Kohan/adaptive-brightness-webcam/actions/workflows/build-check.yml"><img src="https://github.com/Arman-Kohan/adaptive-brightness-webcam/actions/workflows/build-check.yml/badge.svg" alt="Build status"></a>
  <img src="https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-0078D6" alt="Windows 10 | 11">
  <img src="https://img.shields.io/badge/python-3.11%2B-3776AB" alt="Python 3.11+">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License"></a>
</p>

<p align="center">
  <a href="README.fa.md">🇮🇷 این راهنما به فارسی</a>
</p>

---

## Overview

Most laptops adjust brightness only by hand. Adaptive Brightness Webcam watches the light around you through the built-in webcam and keeps the screen at a comfortable level as your room changes — lights on or off, daylight fading, a lamp switched on in the evening.

It is designed to be **calm and trustworthy**:

- The webcam wakes only for a split-second measurement and turns off again — it is never held open.
- Brightness changes are filtered, confirmed, and ramped, so the screen never hunts or flickers.
- Every brightness change is **applied through WMI and read back from the display**, so what the app reports is what the panel actually shows.

## Key features

- **Smart Adaptive mode** — fully autonomous measurement scheduling: frequent checks while your lighting is changing, sparse checks (up to every 2 minutes) in a stable room. No interval settings to tune.
- **Intermittent camera capture** — one controlled cycle per check: open, discard warm-up frames while auto-exposure settles, capture, release. The camera LED is off between checks.
- **Verified Windows brightness control** — WMI `WmiSetBrightness` (the same mechanism as the Windows brightness slider) with immediate readback verification, retries with backoff, and honest *requested / applied / verified* state tracking.
- **Field-calibrated comfort curve** — tuned over several rounds of real-world testing on actual hardware, with scene compensation that is capped so bright rooms cannot overshoot.
- **Anti-flicker gating** — stabilization period after Start, a deadband for insignificant changes, confirmation for moderate changes, and rate/step limits.
- **Persistent settings** — camera, mode, interval, theme, and tray preferences survive restarts and Windows reboots.
- **System tray** — runs quietly in the tray with quick controls; optional launch with Windows.
- **Private by design** — frames are processed in memory only. Nothing is recorded, saved, or sent anywhere. No account, no network, no telemetry.

## Download and installation

1. Go to the [**v1.1.0 release**](https://github.com/Arman-Kohan/adaptive-brightness-webcam/releases/tag/v1.1.0).
2. Download either:
   - `AdaptiveBrightnessWebcam.exe` — the standalone application, or
   - `AdaptiveBrightnessWebcam-Windows.zip` — the same executable with this README and the app icon.
3. Run `AdaptiveBrightnessWebcam.exe`. There is nothing to install: no Python, no dependencies, no administrator rights.

> **Windows SmartScreen:** the executable is not code-signed, so Windows may show a warning on first run. Choose **More info → Run anyway**. This is expected for unsigned open-source builds; you can also build the executable yourself from source (see [Development](#development)).

Upgrading from an older version: just replace the old `.exe` with the new one. Your settings are kept (see below). If the new file lives in a different folder and you use *Launch with Windows*, toggle that option off and on once so Windows points at the new location.

## First run

1. **Select your camera** — `0` is usually the built-in webcam.
2. Click **Test camera**. You should see an ambient-light reading and the brightness it maps to.
3. Leave the mode on **Smart Adaptive** and click **Start automation**.

Your settings are saved automatically to `%APPDATA%\Adaptive Brightness Webcam\settings.json` and restored on the next launch — including after a Windows restart. Corrupted or outdated settings files are handled gracefully: invalid values fall back to defaults field by field, and your valid choices are never overwritten at startup.

## How it works

Each measurement cycle:

1. Opens the webcam and discards the first frames while auto-exposure settles, so an unstabilized sensor is never trusted.
2. Captures 5 frames and summarizes each with robust statistics (median, percentiles, highlights, dark fraction) that resist glare and outliers.
3. Compensates for camera auto-exposure changes, then converts the ambient estimate to a target brightness with the calibration curve.
4. Applies bounded scene compensation for high-contrast rooms (for example, a bright window behind a darker desk).
5. Smooths the result (EMA), suppresses insignificant changes (deadband and hysteresis), and passes it through the anti-flicker gate.
6. Applies the change to the display through WMI — larger changes are ramped gradually — and **reads the panel back to verify** the new level actually took effect.
7. Releases the webcam immediately. The camera stays off until the next scheduled check.

### Smart Adaptive scheduling

Smart Adaptive decides *when* to measure on its own. It tracks a smoothed ambient signal, a robust noise estimate, and a trend estimate, and combines them with feedback from how far the current brightness is from the target:

| Room behavior | Scheduling response |
| --- | --- |
| Rapid, large light change | Interval drops toward the 5-second floor and stays fast until the change is confirmed over several samples |
| Gradual change (sunset, dimming lamp) | Interval shrinks moderately (×0.6) |
| Stable light | Interval grows (×1.4) up to a 2-minute ceiling — about 40 camera activations per hour instead of 720 at a fixed 5-second pace |

Intervals are bounded internally between **5 and 120 seconds** and are not user-configurable — there are no minimum/maximum settings to get wrong. In a stable room the trade-off is deliberate: a sudden light change can take up to ~2 minutes to notice, in exchange for a camera that is almost never on. On battery power, *Fixed Interval* mode additionally stretches its interval (×2.5, minimum 5 minutes).

### Calibration

The ambient-to-brightness curve covers the measured range 8–240 and maps to 10–75% brightness. It was calibrated against comfort data points collected on real hardware over several releases:

| Ambient reading | 20 | 40 | 100 | 150 | 177.8 | 200 | 240 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Screen brightness | 12% | 15% | 30% | 45% | 54% | 61% | 75% |

Two safeguards keep the *effective* target faithful to this curve:

- **Scene compensation is capped.** High-contrast scenes may raise the effective ambient above the raw measurement, but the total boost from all modifiers (scene boost, dim-scene boost, exposure compensation) is limited in brightness points — up to 15 points in dim/mid light, tapering to **zero** at bright levels, where an already-bright measurement needs no help.
- **Verification closes the loop.** A target is only reported as applied when the display readback confirms it (see below).

<details>
<summary>Anti-flicker gate details</summary>

In Smart Adaptive mode every proposed brightness also passes `BrightnessChangeGate`:

- The first 3 samples after Start are stabilization-only; nothing is applied.
- Changes smaller than 4 points are ignored (deadband).
- Moderate changes need 2 consecutive confirming samples.
- Successive changes are rate-limited (20 s) and step-limited (20 points).
- Large changes (≥ 12 points, e.g. lights switched on) apply immediately.
</details>

### Windows brightness control and verification

Brightness is set through **WMI** — `WmiMonitorBrightnessMethods.WmiSetBrightness`, the same interface the Windows brightness slider uses for internal laptop displays — and read back through `WmiMonitorBrightness.CurrentBrightness` in the same operation.

The application layer (`BrightnessApplier`) keeps three states separate: **requested**, **applied** (API accepted), and **verified** (readback matches). Behavior:

- Results are reported as `verified`, `applied_unverified` (API accepted but the machine offers no readback), or `failed` — never silently assumed.
- Transient failures and readback mismatches are retried up to 3 times with backoff (0.3 s, 0.6 s).
- All applications are serialized on a single lock, and newer requests supersede stale in-flight ones, so rapid measurements cannot produce conflicting commands and ramps abort instead of overriding.
- If the WMI brightness classes do not exist on a machine, the legacy `powercfg` power-plan method is used as a fallback and its results are labeled unverified.
- The GUI progress indicator reflects the **applied** brightness, and if a change cannot be applied, the app says so ("Brightness not applied") instead of showing a value the screen does not have.

Every attempt is written to the diagnostic log as one structured line, for example:

```
brightness_apply target=54 previous=48 method=wmi attempt=1 status=accepted readback=54 result=verified
```

## Fixed Mode vs. Smart Adaptive

| | Fixed Interval | Smart Adaptive |
| --- | --- | --- |
| Measurement timing | Every interval you set (30 s or more) | Autonomous, 5–120 s based on lighting stability |
| Camera use | One brief capture per interval | One brief capture per self-scheduled check |
| Anti-flicker gate | No | Yes (stabilization, deadband, confirmation, rate/step limits) |
| Best for | Predictable, simple behavior | Set-and-forget everyday use |
| Battery behavior | Interval stretches automatically on battery | Already sparse; unchanged |

## System requirements

- Windows 10 or Windows 11 (64-bit).
- A laptop with an internal display that exposes the standard WMI brightness controls (most do) and a working webcam with camera permission granted.
- The standalone executable needs nothing else. Running from source needs Python 3.11+ (see [Development](#development)).

External monitors usually do **not** accept WMI brightness commands; the app targets the internal laptop display.

## Troubleshooting

**The screen brightness does not change.**
Check the diagnostic log (below) for `brightness_apply … result=` lines: `verified` means the panel confirmed the change. If you see `failed` or repeated readback mismatches, run the hardware diagnostic (next section). Some vendor utilities and GPU "adaptive brightness" features can fight external control — try disabling those. Also make sure no other brightness tool is running.

**Windows says the app is unrecognized (SmartScreen).**
Expected for unsigned builds: **More info → Run anyway**, or build from source.

**The camera light turns on briefly now and then.**
That is the measurement cycle working as designed — see [Privacy and camera behavior](#privacy-and-camera-behavior). In a stable Smart Adaptive room it happens at most every 2 minutes.

**Wrong camera or "Camera unavailable".**
Try the next camera index (0, 1, 2…), close other apps that may hold the webcam (Teams, Zoom, Camera), and check Windows *Settings → Privacy → Camera* allows desktop apps.

**Settings reset after restart.**
Settings live in `%APPDATA%\Adaptive Brightness Webcam\settings.json`. If the file was deleted or the app is run under a different Windows user, defaults return. Note that very old versions stored nothing at all — set your preferences once with this version and they will persist.

## Diagnostics and logs

- **Log file:** `%USERPROFILE%\.adaptive-brightness-webcam.log` — includes sampling decisions and one structured `brightness_apply` line per brightness attempt (target, previous value, method, attempt number, API status, readback, final result).
- **Hardware diagnostic:** from a source checkout, run:

  ```powershell
  python brightness_diagnostic.py --yes
  ```

  It reads the panel through WMI, applies three test values through the same verified path the app uses, reads the panel back after each step, prints PASS/FAIL per step, and restores your original brightness. Exit code 0 means every step was verified on the hardware.
- **Live state:** the GUI status line shows the current target and, when it differs, the actually applied brightness.

## Privacy and camera behavior

- Frames are analyzed **in memory only**. No image or video is ever saved, uploaded, or sent over the network. The app makes no network connections at all.
- The webcam is opened only for the brief measurement cycle (well under a second of capture plus warm-up) and released immediately afterward. Between checks the camera is fully off.
- **The camera LED lights during capture because of hardware design**: on virtually all laptops the indicator is wired to the sensor's power as a privacy feature. The app neither can nor tries to disable it — the short, infrequent activations in Smart Adaptive mode are how it stays unobtrusive.

## Development

```powershell
git clone https://github.com/Arman-Kohan/adaptive-brightness-webcam.git
cd adaptive-brightness-webcam
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Run the test suite (127 tests):

```powershell
python -m unittest discover -s tests -p "test_*.py"
```

Build the standalone executable:

```powershell
.\build.ps1   # produces dist\AdaptiveBrightnessWebcam.exe
```

An optional Inno Setup script (`installer.iss`) is included if you prefer a traditional installer.

### Project layout

| File | Role |
| --- | --- |
| `app.py` | GUI, system tray, background worker, event flow |
| `adaptive_brightness.py` | Engine: sampling, calibration, controller, scheduler, gate, camera session, verified brightness application |
| `app_config.py` | Persistent settings (`%APPDATA%`) with validation and graceful recovery |
| `brightness_diagnostic.py` | On-hardware brightness verification tool |
| `build.ps1` | PyInstaller build (one-file, windowed, icon) |
| `tests/` | Unit and integration tests for the engine, GUI logic, scheduling, calibration pipeline, and brightness application |

## Known limitations

- The executable is unsigned, so SmartScreen warns on first run.
- Designed for a single internal laptop display; external monitors generally ignore WMI brightness.
- In a very stable room, Smart Adaptive may take up to ~2 minutes to react to a sudden light change (the price of sparse camera use).
- The camera LED necessarily lights during each brief measurement.
- Laptops whose firmware does not expose WMI brightness fall back to the legacy `powercfg` method, whose results cannot be verified.

## License and acknowledgments

Released under the [MIT License](LICENSE).

Built with Python, OpenCV, CustomTkinter, Pillow, and pystray. Thanks to everyone who reported issues and tested builds on real hardware — the calibration curve and the WMI verification layer both exist because of that feedback.
