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

`BrightnessConfig` in `adaptive_brightness.py` contains the tuning points:

- Increase `brightness_max` to allow brighter output in daylight.
- Increase `curve_exponent` to keep indoor brightness lower while preserving the
  high-light response; decrease it for a more sensitive response.
- Adjust `ambient_min` and `ambient_max` for a camera whose exposure range differs.
- `ambient_deadband` ignores small camera fluctuations.
- `smoothing_alpha` controls normal response speed; `sudden_change_alpha` controls
  large transitions. `brightness_hysteresis` prevents one- or two-point flicker.

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
