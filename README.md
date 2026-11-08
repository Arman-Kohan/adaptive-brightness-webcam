# Adaptive Brightness Webcam

<p align="center">
  <img src="assets/adaptive-brightness-poster.png" alt="Adaptive Brightness Webcam — automatic Windows brightness adjustment using your webcam" width="100%">
</p>

**Adaptive Brightness Webcam** is a Windows 10/11 desktop application that uses short webcam samples to estimate ambient light and adjust the brightness of the active display automatically. It is designed to keep indoor brightness comfortable while still responding to major changes such as daylight, lights turning on, or sunset.

**Adaptive Brightness Webcam** یک برنامه دسکتاپ برای ویندوز ۱۰ و ۱۱ است که با نمونه‌برداری کوتاه از تصویر وب‌کم، نور محیط را تخمین می‌زند و روشنایی نمایشگر فعال را به‌صورت خودکار تنظیم می‌کند. هدف برنامه حفظ روشنایی مناسب در محیط‌های داخلی و واکنش صحیح به تغییرات بزرگ مانند روشن شدن چراغ‌ها، تغییر نور روز و غروب آفتاب است.

## What it does | قابلیت‌های اصلی

- Samples the webcam briefly instead of keeping it continuously active.
- Estimates luminance from the center 80% of the frame using robust percentiles rather than a single average.
- Compensates for supported DirectShow exposure changes so camera auto-exposure is less likely to distort the result.
- Converts the measurement into a comfortable display-brightness target using a configurable power curve.
- Uses scene context, highlight ratio, lower-quartile darkness, smoothing, a deadband, and hysteresis to reduce flicker and noisy changes.
- Applies large brightness changes gradually instead of jumping abruptly.
- Shows the latest ambient-light estimate, target brightness, progress, and update time in the app.
- Supports multiple camera indices and a one-click camera test.
- Offers configurable check intervals, with presets for **120**, **300**, and **600** seconds; custom values must be at least **30 seconds**.
- Delays checks further while the laptop is running on battery to reduce webcam wakeups and power usage.
- Can follow the Windows theme or switch automatically between a light and dark appearance based on day/night.
- Can launch with Windows and continue running from the system tray.
- Stores no camera frames and uploads no images or videos.

## For regular users | راهنمای کاربران عادی

### English

1. Open the [Releases page](https://github.com/Arman-Kohan/adaptive-brightness-webcam/releases).
2. Download `AdaptiveBrightnessWebcam.exe` for the standalone executable, or `AdaptiveBrightnessWebcam-Windows.zip` for the portable package.
3. If you downloaded the ZIP file, extract it to a folder you can access.
4. Run `AdaptiveBrightnessWebcam.exe`.
5. In **Settings**, choose the correct camera index. `Camera 0` is usually the built-in webcam.
6. Click **Test camera** and confirm that a light value and target brightness are reported.
7. Choose the check interval, then click **Start automation**.

Python is not required for the packaged Windows executable. The portable release does not need an installer or administrator privileges. To stop automation, click **Stop**. If **Close to tray** is enabled, closing the window hides the app while it continues running in the system tray; use the tray menu to show the window, toggle automation, or quit.

### فارسی

۱. وارد [صفحه Releases](https://github.com/Arman-Kohan/adaptive-brightness-webcam/releases) شوید.
۲. برای دریافت فایل مستقل، `AdaptiveBrightnessWebcam.exe` و برای نسخه قابل‌حمل، `AdaptiveBrightnessWebcam-Windows.zip` را دانلود کنید.
۳. اگر فایل ZIP را دانلود کردید، آن را در یک پوشه استخراج کنید.
۴. فایل `AdaptiveBrightnessWebcam.exe` را اجرا کنید.
۵. در بخش **Settings** شماره دوربین درست را انتخاب کنید. در بیشتر لپ‌تاپ‌ها، وب‌کم داخلی `Camera 0` است.
۶. روی **Test camera** کلیک کنید و مطمئن شوید که مقدار نور و روشنایی هدف نمایش داده می‌شود.
۷. فاصله نمونه‌برداری را انتخاب کنید و روی **Start automation** کلیک کنید.

برای اجرای نسخه بسته‌بندی‌شده ویندوز نیازی به نصب Python نیست. نسخه قابل‌حمل به نصب‌کننده یا دسترسی Administrator نیاز ندارد. برای توقف خودکارسازی روی **Stop** کلیک کنید. اگر گزینه **Close to tray** فعال باشد، بستن پنجره فقط آن را مخفی می‌کند و برنامه در System Tray به کار خود ادامه می‌دهد؛ از منوی Tray می‌توانید پنجره را باز کنید، خودکارسازی را روشن یا خاموش کنید، یا برنامه را کاملاً ببندید.

## Settings | تنظیمات

| Setting | English | فارسی |
|---|---|---|
| Camera | Select the webcam index used for sampling. | انتخاب شماره وب‌کم برای نمونه‌برداری |
| Check interval | Set the delay between samples. The minimum is 30 seconds. | تعیین فاصله بین نمونه‌برداری‌ها؛ حداقل ۳۰ ثانیه |
| Appearance | **Auto** follows day/night; **Windows** follows the system theme. | حالت **Auto** بر اساس روز و شب و حالت **Windows** بر اساس تم سیستم عمل می‌کند |
| Launch with Windows | Starts the app when you sign in to Windows. | اجرای برنامه هنگام ورود به ویندوز |
| Close to tray | Hides the window instead of exiting when it is closed. | مخفی کردن پنجره به‌جای خروج کامل از برنامه |

When the laptop is on battery, the app increases the effective interval to reduce camera wakeups. The selected interval remains visible in Settings, while the actual wait is power-aware.

هنگام استفاده از باتری، برنامه برای کاهش دفعات فعال شدن وب‌کم فاصله واقعی بررسی‌ها را بیشتر می‌کند. مقدار انتخاب‌شده در Settings بدون تغییر باقی می‌ماند، اما زمان انتظار اجرا با وضعیت برق سازگار می‌شود.

## How the measurement works | روش اندازه‌گیری

Each check opens the selected camera, allows it to warm up briefly, captures five valid frames, and uses the median measurement. The camera is then released. The image is converted to luminance and the center 80% is analyzed, which reduces the effect of dark borders and objects near the edge of the frame.

The controller considers:

- `p25`, `p50`, `p75`, and `p90` luminance percentiles.
- The spread between the median and the 90th percentile.
- The ratio of near-white highlight pixels.
- Supported DirectShow exposure and gain properties.
- Whether a scene appears dim even when auto-exposure produces a larger raw value.
- A bounded bright-scene boost for high-contrast indoor scenes.

The result passes through exposure calibration, a configurable power curve, temporal smoothing, an ambient deadband, and brightness hysteresis. Targets are sent to Windows through `powercfg`. A change of three or more brightness points is ramped gradually to avoid an abrupt visual transition.

هر بررسی وب‌کم انتخاب‌شده را باز می‌کند، اجازه می‌دهد نوردهی آن برای مدت کوتاهی پایدار شود، پنج فریم معتبر می‌گیرد و از مقدار میانه استفاده می‌کند. سپس وب‌کم آزاد می‌شود. تصویر به روشنایی تبدیل می‌شود و ۸۰ درصد مرکزی آن بررسی می‌شود تا تأثیر لبه‌های تاریک یا اشیای کنار تصویر کمتر شود.

کنترل‌کننده موارد زیر را در نظر می‌گیرد:

- صدک‌های روشنایی `p25`، `p50`، `p75` و `p90`.
- فاصله بین میانه و صدک ۹۰.
- نسبت پیکسل‌های بسیار روشن یا نزدیک به سفید.
- ویژگی‌های نوردهی و Gain در صورت گزارش شدن توسط DirectShow.
- تشخیص صحنه کم‌نور، حتی زمانی که نوردهی خودکار وب‌کم مقدار خام بزرگ‌تری تولید می‌کند.
- افزایش محدود روشنایی برای صحنه‌های داخلی پرکنتراست.

نتیجه پس از کالیبراسیون نوردهی، منحنی توان قابل تنظیم، هموارسازی زمانی، Deadband و Hysteresis به ویندوز ارسال می‌شود. تغییرات سه واحدی یا بیشتر به‌صورت تدریجی اعمال می‌شوند تا روشنایی ناگهان تغییر نکند.

## Calibration and tuning | کالیبراسیون و تنظیم دقیق

The default configuration is intentionally conservative for ordinary indoor lighting:

- Ambient range: `8`–`240`
- Display brightness range: `10%`–`75%`
- Curve exponent: `1.75`
- Ambient deadband: `3`
- Normal smoothing alpha: `0.35`
- Sudden-change smoothing alpha: `0.8`
- Brightness hysteresis: `2`

For example, an ambient value around `139.6` maps to approximately `34%` with the default curve. These values are camera-derived luminance estimates, not physical lux measurements. The same room can produce different raw values with different cameras, framing, exposure, or white balance.

The tuning points are defined in `BrightnessConfig` in `adaptive_brightness.py`. The most useful adjustments are:

- `brightness_max`: raise this to allow brighter output in daylight.
- `curve_exponent`: raise it to keep normal indoor brightness lower; lower it for a faster, more sensitive response.
- `ambient_min` and `ambient_max`: adapt the input range to a camera with a different exposure response.
- `ambient_deadband`, `smoothing_alpha`, `sudden_change_alpha`, and `brightness_hysteresis`: control stability versus response speed.
- `scene_boost_max`, `scene_spread_start`, and `scene_spread_end`: control the bounded high-contrast scene boost.
- `dim_scene_*`, `dim_scene_boost`, and `sunset_trend_penalty`: protect against auto-exposure behavior that can make a darkening room appear numerically brighter.

For reliable results, keep the webcam uncovered and pointed toward the room rather than directly at a lamp or a bright window.

مقادیر پیش‌فرض برای نور معمولی محیط داخلی عمداً محافظه‌کارانه انتخاب شده‌اند:

- بازه نور محیط: `8` تا `240`
- بازه روشنایی نمایشگر: `10%` تا `75%`
- توان منحنی: `1.75`
- Deadband نور محیط: `3`
- ضریب هموارسازی معمولی: `0.35`
- ضریب واکنش به تغییر ناگهانی: `0.8`
- Hysteresis روشنایی: `2`

برای نمونه، مقدار محیطی نزدیک به `139.6` با منحنی پیش‌فرض تقریباً به روشنایی `34%` تبدیل می‌شود. این اعداد تخمین روشنایی بر اساس دوربین هستند و واحد فیزیکی Lux محسوب نمی‌شوند. یک اتاق یکسان با دوربین، کادر، نوردهی یا White Balance متفاوت می‌تواند مقدار خام متفاوتی تولید کند.

نقاط قابل تنظیم در `BrightnessConfig` فایل `adaptive_brightness.py` قرار دارند. مهم‌ترین گزینه‌ها:

- `brightness_max`: برای امکان روشنایی بیشتر در نور روز افزایش دهید.
- `curve_exponent`: برای پایین نگه داشتن روشنایی معمول داخل خانه افزایش دهید؛ برای واکنش حساس‌تر کاهش دهید.
- `ambient_min` و `ambient_max`: برای دوربینی با رفتار نوردهی متفاوت تنظیم کنید.
- `ambient_deadband`، `smoothing_alpha`، `sudden_change_alpha` و `brightness_hysteresis`: تعادل بین پایداری و سرعت واکنش را تعیین می‌کنند.
- `scene_boost_max`، `scene_spread_start` و `scene_spread_end`: افزایش محدود مربوط به صحنه‌های پرکنتراست را کنترل می‌کنند.
- گزینه‌های `dim_scene_*`، به‌همراه `dim_scene_boost` و `sunset_trend_penalty`: اثر نوردهی خودکار را در اتاقی که رو به تاریک شدن است کاهش می‌دهند.

برای نتیجه بهتر، وب‌کم را نپوشانید و آن را به سمت فضای اتاق بگیرید؛ از قرار دادن مستقیم آن مقابل چراغ یا پنجره بسیار روشن خودداری کنید.

## Privacy, compatibility, and limitations | حریم خصوصی، سازگاری و محدودیت‌ها

- The webcam is active only during a short warm-up and sampling window. The privacy indicator may turn on during that time.
- Frames are processed in memory and are not saved, uploaded, or transmitted by this application.
- Diagnostic messages are written to `%USERPROFILE%\.adaptive-brightness-webcam.log`.
- Brightness control uses Windows `powercfg` and works best with an internal laptop display.
- Some external monitors, docks, virtual displays, or vendor-specific display drivers do not expose brightness controls through Windows and may not respond.
- The app requires a working camera permission, a usable webcam index, and a Windows display that accepts the `powercfg` brightness commands.
- If the camera cannot be opened, the app reports an error and waits for the next check rather than changing brightness based on invalid data.

- وب‌کم فقط هنگام گرم شدن کوتاه و نمونه‌برداری فعال است و ممکن است چراغ حریم خصوصی در همان مدت روشن شود.
- فریم‌ها در حافظه پردازش می‌شوند و توسط این برنامه ذخیره، آپلود یا منتقل نمی‌شوند.
- پیام‌های تشخیصی در `%USERPROFILE%\.adaptive-brightness-webcam.log` نوشته می‌شوند.
- کنترل روشنایی با `powercfg` ویندوز انجام می‌شود و روی نمایشگر داخلی لپ‌تاپ بهترین عملکرد را دارد.
- بعضی نمایشگرهای خارجی، Dock، نمایشگرهای مجازی یا درایورهای اختصاصی کنترل روشنایی را از طریق ویندوز ارائه نمی‌کنند و ممکن است پاسخ ندهند.
- برنامه به دسترسی فعال دوربین، شماره دوربین معتبر و نمایشگری نیاز دارد که فرمان‌های روشنایی `powercfg` را بپذیرد.
- اگر دوربین باز نشود، برنامه خطا را گزارش می‌کند و تا بررسی بعدی صبر می‌کند؛ بر اساس داده نامعتبر روشنایی را تغییر نمی‌دهد.

## Troubleshooting | رفع اشکال

### The camera test fails | تست دوربین ناموفق است

Try another camera index such as `1` or `2`, verify Windows camera permissions, and make sure another application is not exclusively using the webcam. The selected camera must be visible to OpenCV through the DirectShow backend.

شماره دوربین دیگری مانند `1` یا `2` را امتحان کنید، مجوز دوربین در ویندوز را بررسی کنید و مطمئن شوید برنامه دیگری وب‌کم را به‌صورت انحصاری استفاده نمی‌کند. دوربین انتخاب‌شده باید از طریق Backend نوع DirectShow برای OpenCV قابل مشاهده باشد.

### Brightness does not change | روشنایی تغییر نمی‌کند

Check whether the active display supports Windows brightness control. Built-in laptop panels usually work best; many external monitors require their own hardware controls or vendor software. Check the log file for `powercfg` diagnostics.

بررسی کنید که نمایشگر فعال از کنترل روشنایی ویندوز پشتیبانی کند. نمایشگر داخلی لپ‌تاپ معمولاً بهترین عملکرد را دارد؛ بسیاری از نمایشگرهای خارجی به کنترل سخت‌افزاری یا نرم‌افزار سازنده نیاز دارند. برای خطاهای `powercfg` فایل لاگ را بررسی کنید.

### The response is too slow or too bright | واکنش کند یا روشنایی زیاد است

Reduce the interval, adjust `curve_exponent` or `brightness_max` in `BrightnessConfig`, and verify that the webcam is facing the room rather than a bright light source. Keep in mind that battery mode intentionally adds delay.

فاصله نمونه‌برداری را کم کنید، `curve_exponent` یا `brightness_max` را در `BrightnessConfig` تنظیم کنید و مطمئن شوید وب‌کم به سمت فضای اتاق است، نه منبع نور شدید. توجه کنید که حالت باتری عمداً تأخیر بیشتری ایجاد می‌کند.

## For developers | برای توسعه‌دهندگان

### Requirements

- Windows 10 or Windows 11 for camera capture and brightness control.
- Python 3.10+ recommended.
- A webcam supported by OpenCV.

### Run from source

```powershell
python -m pip install -r requirements.txt
python app.py
```

To run the controller without the graphical interface:

```powershell
python adaptive_brightness.py --camera 0 --interval 120
```

Use `--once` to take one sample and exit:

```powershell
python adaptive_brightness.py --camera 0 --once
```

The interval must be a finite value of at least 30 seconds, and the camera index must be non-negative.

### Run tests

```powershell
python -m unittest discover -s tests -p "test_*.py"
```

### Build the Windows executable

```powershell
.\build.ps1
```

The build script installs the dependencies, generates `app_icon.ico`, and creates:

```text
dist\AdaptiveBrightnessWebcam.exe
```

The GitHub Actions release workflow builds the executable when a `v*` tag is pushed and publishes both the executable and `AdaptiveBrightnessWebcam-Windows.zip`. The optional Inno Setup definition is in `installer.iss`.

### ساخت از کد منبع

برای نصب وابستگی‌ها و اجرای رابط گرافیکی:

```powershell
python -m pip install -r requirements.txt
python app.py
```

برای اجرای موتور بدون رابط گرافیکی:

```powershell
python adaptive_brightness.py --camera 0 --interval 120
```

با گزینه `--once` فقط یک‌بار نمونه‌برداری کنید و خارج شوید:

```powershell
python adaptive_brightness.py --camera 0 --once
```

فاصله باید یک عدد محدود و حداقل ۳۰ ثانیه باشد و شماره دوربین نباید منفی باشد.

برای اجرای تست‌ها:

```powershell
python -m unittest discover -s tests -p "test_*.py"
```

برای ساخت فایل اجرایی ویندوز:

```powershell
.\build.ps1
```

اسکریپت ساخت وابستگی‌ها را نصب می‌کند، فایل `app_icon.ico` را می‌سازد و فایل زیر را تولید می‌کند:

```text
dist\AdaptiveBrightnessWebcam.exe
```

Workflow انتشار GitHub با Push شدن Tagهایی مانند `v1.0.0` فایل اجرایی را می‌سازد و هم فایل اجرایی و هم `AdaptiveBrightnessWebcam-Windows.zip` را منتشر می‌کند. تعریف اختیاری Inno Setup در فایل `installer.iss` قرار دارد.

## License | مجوز

This project is released under the [MIT License](LICENSE).

این پروژه تحت مجوز [MIT](LICENSE) منتشر شده است.
