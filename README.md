# Adaptive Brightness Webcam

راهنمای فارسی: [README.fa.md](README.fa.md)

Smooth Windows display brightness adjustment for laptops without a usable ambient-light sensor.

The program briefly samples the built-in webcam, maps the scene brightness to a comfortable display range, and applies changes gradually. It does not keep the camera open continuously and launches no console windows.

## Important privacy note

This uses a webcam as a light sensor. The camera privacy LED may turn on briefly during sampling. Many laptops hard-wire that LED to camera use; this project does not bypass or disable it. For completely silent light sensing, use a real ambient-light sensor or an external USB sensor.

## Requirements

- Windows 10 or 11
- Python 3.10+
- A webcam
- `powercfg` access to display brightness

## Install and test

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python adaptive_brightness.py --camera 0 --once
```

If camera `0` is not the correct device, try `--camera 1`.

## Run continuously

```powershell
pythonw adaptive_brightness.py --camera 0 --interval 60
```

To start it automatically after sign-in, add a shortcut to the command above in `shell:startup`, or create a per-user Scheduled Task. Use `pythonw.exe` to avoid a console window.

## Tuning

- `--interval 60`: seconds between samples.
- `value_to_brightness`: adjust the comfortable minimum/maximum range.
- `ramp_brightness`: controls how gradually brightness changes.

## License

MIT.
