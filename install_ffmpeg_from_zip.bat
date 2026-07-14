@echo off
setlocal

cd /d "%~dp0"

if "%~1"=="" (
    echo Usage:
    echo   Drag and drop an FFmpeg zip file onto this bat file.
    echo.
    echo Example target files after install:
    echo   tools\ffmpeg\bin\ffmpeg.exe
    echo   tools\ffmpeg\bin\ffprobe.exe
    pause
    exit /b 1
)

set "ZIP_PATH=%~1"
set "TARGET_BIN=%CD%\tools\ffmpeg\bin"
set "EXTRACT_DIR=%CD%\tmp\ffmpeg_manual_extract"

if not exist "%ZIP_PATH%" (
    echo Zip file was not found:
    echo %ZIP_PATH%
    pause
    exit /b 1
)

echo Installing FFmpeg from:
echo %ZIP_PATH%
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ErrorActionPreference = 'Stop';" ^
    "$root = (Resolve-Path '.').Path;" ^
    "$zip = '%ZIP_PATH%';" ^
    "$target = Join-Path $root 'tools\ffmpeg\bin';" ^
    "$extract = Join-Path $root 'tmp\ffmpeg_manual_extract';" ^
    "New-Item -ItemType Directory -Force -Path $target | Out-Null;" ^
    "if (Test-Path $extract) { Remove-Item -LiteralPath $extract -Recurse -Force };" ^
    "Expand-Archive -LiteralPath $zip -DestinationPath $extract -Force;" ^
    "$ffmpeg = Get-ChildItem -LiteralPath $extract -Recurse -Filter 'ffmpeg.exe' | Select-Object -First 1;" ^
    "$ffprobe = Get-ChildItem -LiteralPath $extract -Recurse -Filter 'ffprobe.exe' | Select-Object -First 1;" ^
    "if (-not $ffmpeg -or -not $ffprobe) { throw 'ffmpeg.exe or ffprobe.exe was not found in the zip file.' };" ^
    "$sourceBin = $ffmpeg.Directory.FullName;" ^
    "Copy-Item -Path (Join-Path $sourceBin '*') -Destination $target -Recurse -Force;" ^
    "Remove-Item -LiteralPath $extract -Recurse -Force;" ^
    "Write-Host 'Installed FFmpeg to:' $target;"

if errorlevel 1 (
    echo Failed to install FFmpeg from zip.
    pause
    exit /b 1
)

"%TARGET_BIN%\ffmpeg.exe" -version
"%TARGET_BIN%\ffprobe.exe" -version

echo.
echo Done. You can now run start_app.bat.
pause
