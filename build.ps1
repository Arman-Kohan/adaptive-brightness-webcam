$ErrorActionPreference = "Stop"
$python = (Get-Command python -ErrorAction Stop).Source
& $python -m pip install -r requirements.txt
& $python -c "import app; app.create_app_icon(256).save('app_icon.ico', format='ICO', sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])"
& $python -m PyInstaller --noconfirm --clean --onefile --windowed --icon=app_icon.ico --name AdaptiveBrightnessWebcam app.py
Write-Host "Build complete: dist\AdaptiveBrightnessWebcam.exe"
