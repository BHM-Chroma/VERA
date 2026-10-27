@echo off
chcp 65001 >nul
title Обновление VERONIKA
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0update.ps1"
if errorlevel 1 (
  echo.
  echo Не получилось обновить. Проверь интернет и попробуй ещё раз.
)
pause
