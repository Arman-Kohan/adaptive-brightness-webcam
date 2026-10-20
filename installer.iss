[Setup]
AppName=Adaptive Brightness Webcam
AppVersion=1.0.0
DefaultDirName={autopf}\Adaptive Brightness Webcam
DefaultGroupName=Adaptive Brightness Webcam
OutputDir=dist\installer
OutputBaseFilename=AdaptiveBrightnessWebcam-Setup
SetupIconFile=app_icon.ico
Compression=lzma
SolidCompression=yes
PrivilegesRequired=lowest

[Files]
Source: "dist\AdaptiveBrightnessWebcam.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Adaptive Brightness Webcam"; Filename: "{app}\AdaptiveBrightnessWebcam.exe"
Name: "{autodesktop}\Adaptive Brightness Webcam"; Filename: "{app}\AdaptiveBrightnessWebcam.exe"

[Run]
Filename: "{app}\AdaptiveBrightnessWebcam.exe"; Description: "Ø§Ø¬Ø±Ø§ÛŒ Adaptive Brightness Webcam"; Flags: nowait postinstall skipifsilent
