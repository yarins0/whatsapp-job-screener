@echo off
cd /d "%~dp0"
call venv\Scripts\activate.bat
python start.py
rem Only hold the window open if start.py exited with an error, so a clean
rem shutdown closes the window instead of waiting on a keypress.
if errorlevel 1 pause
