@echo off
setlocal
chcp 65001 >nul

:: 1. Проверка прав Администратора и автоматический UAC-запрос
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [ИНФО] Запрос прав Администратора...
    powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Start-Process cmd.exe -ArgumentList '/k cd /d ""%~dp0"" && powershell.exe -NoProfile -ExecutionPolicy Bypass -File ""%~dp0make-boot-usb.ps1""' -Verb RunAs"
    exit /b
)

cd /d "%~dp0"

:: 2. Запуск основного PowerShell скрипта
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0make-boot-usb.ps1"

if %errorlevel% neq 0 (
    echo.
    echo ======================================================================
    echo  Работа мастера завершена.
    echo ======================================================================
    pause
)
