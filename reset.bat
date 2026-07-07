@echo off
setlocal

cd /d "%~dp0"
python "%~dp0reset.py"
exit /b %errorlevel%
