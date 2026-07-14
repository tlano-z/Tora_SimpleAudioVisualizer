@echo off
setlocal

cd /d "%~dp0"

set "FFMPEG_BIN=%CD%\tools\ffmpeg\bin"
set "FFMPEG_EXE=%FFMPEG_BIN%\ffmpeg.exe"
set "FFPROBE_EXE=%FFMPEG_BIN%\ffprobe.exe"
set "FFMPEG_URL=https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
set "FFMPEG_PAGE=https://www.gyan.dev/ffmpeg/builds/"
set "DOWNLOAD=%CD%\tmp\ffmpeg-release-essentials.zip"
set "EXTRACT_DIR=%CD%\tmp\ffmpeg_extract"

if exist "%FFMPEG_EXE%" if exist "%FFPROBE_EXE%" (
    echo FFmpeg is already installed locally:
    echo %FFMPEG_BIN%
    "%FFMPEG_EXE%" -version
    pause
    exit /b 0
)

echo Installing portable FFmpeg into this project...
echo Target: %FFMPEG_BIN%
echo Source page: %FFMPEG_PAGE%
echo Download URL: %FFMPEG_URL%
echo.
echo This downloads the release essentials ZIP, which is enough for this app.
echo File size is about 100 MB.
echo.

if not exist "%CD%\tmp" mkdir "%CD%\tmp"
if not exist "%CD%\tools\ffmpeg\bin" mkdir "%CD%\tools\ffmpeg\bin"

if exist "%DOWNLOAD%" del /f /q "%DOWNLOAD%"

echo Downloading FFmpeg...
curl.exe -L --fail --retry 3 --retry-delay 3 --connect-timeout 20 --progress-bar -o "%DOWNLOAD%" "%FFMPEG_URL%"
if errorlevel 1 (
    echo.
    echo Download failed.
    echo You can manually download the ZIP from:
    echo %FFMPEG_PAGE%
    echo.
    echo Then drag and drop the downloaded ZIP onto install_ffmpeg_from_zip.bat.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ErrorActionPreference = 'Stop';" ^
    "$root = (Resolve-Path '.').Path;" ^
    "$tmp = Join-Path $root 'tmp';" ^
    "$download = Join-Path $tmp 'ffmpeg-release-essentials.zip';" ^
    "$extract = Join-Path $tmp 'ffmpeg_extract';" ^
    "$target = Join-Path $root 'tools\ffmpeg\bin';" ^
    "New-Item -ItemType Directory -Force -Path $tmp, $target | Out-Null;" ^
    "if (Test-Path $extract) { Remove-Item -LiteralPath $extract -Recurse -Force };" ^
    "Expand-Archive -LiteralPath $download -DestinationPath $extract -Force;" ^
    "$ffmpeg = Get-ChildItem -LiteralPath $extract -Recurse -Filter 'ffmpeg.exe' | Select-Object -First 1;" ^
    "$ffprobe = Get-ChildItem -LiteralPath $extract -Recurse -Filter 'ffprobe.exe' | Select-Object -First 1;" ^
    "if (-not $ffmpeg -or -not $ffprobe) { throw 'ffmpeg.exe or ffprobe.exe was not found in the downloaded archive.' };" ^
    "$sourceBin = $ffmpeg.Directory.FullName;" ^
    "Copy-Item -Path (Join-Path $sourceBin '*') -Destination $target -Recurse -Force;" ^
    "Remove-Item -LiteralPath $extract -Recurse -Force;" ^
    "Remove-Item -LiteralPath $download -Force;" ^
    "Write-Host 'Local FFmpeg setup completed.'"

if errorlevel 1 (
    echo Failed to install FFmpeg.
    pause
    exit /b 1
)

"%FFMPEG_EXE%" -version
pause
