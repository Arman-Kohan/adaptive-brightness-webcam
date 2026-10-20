# Adaptive Brightness Webcam

A Windows 10/11 app that adjusts display brightness automatically using short webcam light samples.

یک برنامه برای ویندوز ۱۰ و ۱۱ که با نمونه‌برداری کوتاه از نور محیط توسط وب‌کم، روشنایی نمایشگر را به‌صورت خودکار تنظیم می‌کند.

## For regular users | برای کاربران عادی

### English

1. Open the [Releases page](https://github.com/Arman-Kohan/adaptive-brightness-webcam/releases).
2. Download `AdaptiveBrightnessWebcam-Windows.zip` or `AdaptiveBrightnessWebcam.exe`.
3. If you downloaded the ZIP, extract it first.
4. Double-click `AdaptiveBrightnessWebcam.exe`.
5. Choose your camera and click **Test camera**, then click **Start automation**.

No Python installation is required. The ZIP version is portable; it does not need an installer.

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

وب‌کم فقط هنگام نمونه‌برداری و برای چند لحظه فعال می‌شود و ممکن است چراغ حریم خصوصی آن روشن شود. کنترل روشنایی روی نمایشگر داخلی لپ‌تاپ بهترین عملکرد را دارد و بعضی نمایشگرهای خارجی از آن پشتیبانی نمی‌کنند.

## For developers | برای توسعه‌دهندگان

```powershell
python -m pip install -r requirements.txt
python app.py
```

To build the Windows executable:

برای ساخت فایل اجرایی ویندوز:

```powershell
.\build.ps1
```

The project is licensed under the MIT License.

این پروژه تحت مجوز MIT منتشر شده است.
