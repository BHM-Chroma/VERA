@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Создаю виртуальное окружение и ставлю библиотеки...
py -3 -m venv .venv || python -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\pip install -r requirements.txt
if not exist .env copy .env.example .env
echo.
echo Готово. Открой файл .env и впиши свой новый ключ OpenAI (если нужен онлайн-ИИ).
pause
