@echo off
setlocal
title Tora_SimpleAudioVisualizer

cd /d "%~dp0"

set "STREAMLIT_BROWSER_GATHER_USAGE_STATS=false"
set "STREAMLIT_SERVER_HEADLESS=false"
if exist "%CD%\tools\ffmpeg\bin\ffmpeg.exe" set "PATH=%CD%\tools\ffmpeg\bin;%PATH%"
set "VENV_PY=%CD%\.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
    echo Creating local virtual environment...
    py -3 -m venv .venv
    if errorlevel 1 (
        echo Failed to create .venv with py launcher. Trying python...
        python -m venv .venv
        if errorlevel 1 (
            echo Failed to create the virtual environment.
            pause
            exit /b 1
        )
    )
)

echo Checking Python packages in .venv...
"%VENV_PY%" -c "import streamlit, numpy, cv2, PIL" >nul 2>nul
if errorlevel 1 (
    echo Installing required packages into .venv...
    "%VENV_PY%" -m pip install --upgrade pip
    if errorlevel 1 (
        echo Failed to upgrade pip.
        pause
        exit /b 1
    )

    "%VENV_PY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Failed to install requirements.
        pause
        exit /b 1
    )
)

echo Starting Tora_SimpleAudioVisualizer...
echo URL: http://localhost:8501
"%VENV_PY%" -m streamlit run app.py --server.address localhost --server.headless false --browser.gatherUsageStats false

pause
