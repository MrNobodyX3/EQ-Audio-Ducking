@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 goto use_py
where python >nul 2>nul
if %errorlevel%==0 goto use_python

echo Python 3 was not found. Install Python 3 and try again.
pause
exit /b 1

:use_py
py -3 -B app.py
goto finished

:use_python
python -B app.py

:finished
if errorlevel 1 (
    echo.
    echo The app exited with an error. Check that Python 3 and NumPy are installed.
    pause
)
