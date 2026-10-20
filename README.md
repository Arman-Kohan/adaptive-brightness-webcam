# Adaptive Brightness Webcam

Windows 10/11 app that adjusts display brightness from short webcam light samples.

## Features

- Modern Windows 11-style desktop interface.
- Automatic light and dark theme by time of day.
- System tray controls.
- Optional launch at Windows sign-in.
- No camera images are saved or uploaded.

## Development

```powershell
python -m pip install -r requirements.txt
python app.py
```

## Build the Windows executable

```powershell
.\build.ps1
```

The standalone executable is created at `dist\\AdaptiveBrightnessWebcam.exe`.
The build includes CustomTkinter, Pillow, pystray, OpenCV, and a generated app icon.

To create an installer, compile `installer.iss` with Inno Setup.

## Privacy and limitations

The camera is activated only briefly for each sample. No image or video is stored or sent anywhere. The camera privacy LED may turn on during sampling. Brightness control works best with an internal laptop display; external monitors may not support `powercfg` brightness control.

---

# نسخه فارسی

نرم‌افزار ویندوزی برای تنظیم خودکار روشنایی نمایشگر بر اساس نور محیط که با استفاده از نمونه‌برداری کوتاه از وب‌کم کار می‌کند.

## امکانات

- رابط کاربری مدرن با سبک Windows 11
- حالت روشن و تاریک خودکار بر اساس ساعت روز
- کنترل از طریق System Tray
- امکان اجرای خودکار همراه با ورود به ویندوز
- ذخیره یا آپلود نشدن تصاویر و ویدئوهای وب‌کم

## اجرای نسخه توسعه‌ای

ابتدا وابستگی‌ها را نصب کنید:

```powershell
python -m pip install -r requirements.txt
```

سپس برنامه را اجرا کنید:

```powershell
python app.py
```

## ساخت فایل اجرایی ویندوز

```powershell
.\build.ps1
```

فایل اجرایی مستقل در مسیر زیر ساخته می‌شود:

```
dist\\AdaptiveBrightnessWebcam.exe
```

برای ساخت فایل نصب، فایل `installer.iss` را با Inno Setup کامپایل کنید.

## حریم خصوصی و محدودیت‌ها

وب‌کم فقط برای چند لحظه و هنگام نمونه‌برداری فعال می‌شود. هیچ تصویر یا ویدئویی ذخیره یا ارسال نمی‌شود. ممکن است چراغ حریم خصوصی وب‌کم هنگام نمونه‌برداری روشن شود.

کنترل روشنایی روی نمایشگر داخلی لپ‌تاپ بهترین عملکرد را دارد؛ نمایشگرهای خارجی ممکن است از کنترل روشنایی با `powercfg` پشتیبانی نکنند.

## مجوز

این پروژه تحت مجوز MIT منتشر شده است.
