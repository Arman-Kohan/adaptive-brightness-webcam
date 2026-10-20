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

Licensed under the MIT License.
