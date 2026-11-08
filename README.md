# Adaptive Brightness Webcam

<p align="center">
  <img src="assets/adaptive-brightness-poster.png" alt="Adaptive Brightness Webcam — automatic Windows brightness adjustment using your webcam" width="100%">
</p>

A Windows 10/11 app that adjusts display brightness automatically using short webcam light samples.

یک برنامه برای ویندوز ۱۰ و ۱۱ که با نمونه‌برداری کوتاه از نور محیط توسط وب‌کم، روشنایی نمایشگر را به‌صورت خودکار تنظیم می‌کند.

## For regular users | برای کاربران عادی

### English

1. Open the [Releases page](https://github.com/Arman-Kohan/adaptive-brightness-webcam/releases).
2. Download `AdaptiveBrightnessWebcam.exe`.
3. Double-click `AdaptiveBrightnessWebcam.exe`.
4. Choose your camera and click **Test camera**, then click **Start automation**.

No Python installation is required. The executable is portable and does not need an installer.

### فارسی

۱. وارد [صفحه Releases](https://github.com/Arman-Kohan/adaptive-brightness-webcam/releases) شوید.
۲. فایل `AdaptiveBrightnessWebcam-Windows.zip` یا `AdaptiveBrightnessWebcam.exe` را دانلود کنید.
۳. اگر فایل ZIP را دانلود کردید، ابتدا آن را Extract کنید.
۴. روی فایل `AdaptiveBrightnessWebcam.exe` دوبار کلیک کنید.
۵. دوربین را انتخاب کنید، روی **Test camera** و سپس روی **Start automation** کلیک کنید.

برای اجرای برنامه نیازی به نصب Python نیست. نسخه ZIP قابل‌حمل است و به نصب‌کننده نیاز ندارد.

## Features | امکانات

- Modern Windows 11-style interface | رابط کاربری مدرن با سبک Windows 11
- Automatic light and dark theme | حالت روشن و تاریک خودکار
- System tray controls | کنترل از System Tray
- Optional launch with Windows | اجرای اختیاری همراه با ورود به ویندوز
- No images or videos are stored or uploaded | هیچ تصویر یا ویدئویی ذخیره یا آپلود نمی‌شود

## Privacy and limitations | حریم خصوصی و محدودیت‌ها

The webcam is active only briefly during each sample. The camera privacy light may turn on. Brightness control works best with an internal laptop display; some external monitors do not support it.

### Brightness calibration

The controller measures robust luminance percentiles from the center 80% of each
webcam sample instead of using the mean HSV value alone. This reduces sensitivity
to saturated highlights, dark edges, and scene composition. The capture backend's
exposure, gain, and white-balance properties are recorded; when DirectShow
reports a valid log-scale exposure value, relative exposure changes are compensated
before the brightness curve is applied. Unsupported or ambiguous camera controls
are left unchanged rather than guessed.

The corrected measurement is mapped through a configurable power curve rather
than a linear scale. The default curve uses ambient values 8–240, display
brightness 10–75%, and an exponent of 1.75; this intentionally keeps ordinary
indoor light comfortable. For example, an ambient value near 139.6 maps to
approximately 34%. A raw reading can be lower in a brighter room when the webcam
changes exposure, so readings should be interpreted together with the capture
properties rather than as physical lux values.

The controller also retains scene context from the luminance distribution. A
wide gap between the 50th and 90th percentiles, together with a small proportion
of near-white pixels, indicates a brighter indoor scene even when the camera's
single scalar value is similar to a darker scene. This scene boost is bounded
and filtered with the same deadband, smoothing, and hysteresis rules; it is not
a hardcoded mapping for any one room or reading. Each GUI sample logs the raw
percentiles, scene spread, highlight ratio, filtered value, and final target so
calibration can be checked against the actual camera backend.

`BrightnessConfig` in `adaptive_brightness.py` contains the tuning points:

- Increase `brightness_max` to allow brighter output in daylight.
- Increase `curve_exponent` to keep indoor brightness lower while preserving the
  high-light response; decrease it for a more sensitive response.
- Adjust `ambient_min` and `ambient_max` for a camera whose exposure range differs.
- `ambient_deadband` ignores small camera fluctuations.
- `smoothing_alpha` controls normal response speed; `sudden_change_alpha` controls
  large transitions. `brightness_hysteresis` prevents one- or two-point flicker.
- `scene_boost_max`, `scene_spread_start`, and `scene_spread_end` bound the
  additional response for a high-contrast bright indoor scene.
- `bright_scene_response` and `highlight_response_span` reduce that response
  smoothly when the camera reports a bright, clipped scene; this prevents
  highlight-heavy frames from being interpreted as unlimited room illumination.
- The lower luminance quartile (`p25`) is retained as a darkness indicator.
  When `p25` is below `dim_scene_lower_quartile` (default `110`) and the
  median is below `dim_scene_median` (default `132`) and the
  highlight ratio is below `dim_scene_highlight_ratio` (default `0.05`), the
  controller treats the scene as dim rather than assuming that a larger
  camera-derived scalar means a brighter room. `dim_scene_boost` (default
  `60`) provides a bounded initial correction, while
  `sunset_trend_penalty` (default `20` brightness-equivalent units) reduces
  the target when successive dim-scene samples rise, as happens when a
  webcam's auto-exposure response makes a room appear numerically brighter
  while it is actually getting darker.
- A median at or above the dim-scene threshold prevents a low-highlight,
  adequately illuminated room from inheriting the dim-scene correction.
  Exposure-calibrated measurements preserve `p25`, so this classification is
  also available on the DirectShow path.
- `illuminated_scene_median_start` and `illuminated_scene_median_end`
  (defaults `130` and `137`) provide a smooth median-based confidence ramp
  for adequately illuminated indoor scenes even when there are no clipped
  highlights.
- These sunset safeguards are stateful and only activate when the detailed
  measurement includes `p25`; legacy scalar callers retain the normal
  mapping.

The controller uses median sampling, an ambient deadband, adaptive temporal
smoothing, and brightness hysteresis. Invalid or out-of-range camera values are
ignored, and the webcam is still opened only for each short sample.

وب‌کم فقط هنگام نمونه‌برداری و برای چند لحظه فعال می‌شود و ممکن است چراغ حریم خصوصی آن روشن شود. کنترل روشنایی روی نمایشگر داخلی لپ‌تاپ بهترین عملکرد را دارد و بعضی نمایشگرهای خارجی از آن پشتیبانی نمی‌کنند.

## For developers | برای توسعه‌دهندگان

```powershell
python -m pip install -r requirements.txt
python app.py
```

To build the Windows executable:

برای ساخت فایل اجرایی ویندوز:

```powershell
.\\build.ps1
```

The project is licensed under the MIT License.

این پروژه تحت مجوز MIT منتشر شده است.
