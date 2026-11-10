$ErrorActionPreference = "Stop"
$python = (Get-Command python -ErrorAction Stop).Source
& $python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    throw "Dependency installation failed with exit code $LASTEXITCODE."
}
& $python -c "import app; app.create_app_icon(256).save('app_icon.ico', format='ICO', sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])"
if ($LASTEXITCODE -ne 0) {
    throw "Application icon generation failed with exit code $LASTEXITCODE."
}
& $python -m PyInstaller --noconfirm --clean --onefile --windowed --icon=app_icon.ico --name AdaptiveBrightnessWebcam app.py
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE."
}
if (-not (Test-Path "dist\AdaptiveBrightnessWebcam.exe")) {
    throw "Expected executable was not created."
}
Write-Host "Build complete: dist\AdaptiveBrightnessWebcam.exe"
