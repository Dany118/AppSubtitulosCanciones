@echo off
setlocal
cd /d "%~dp0"

echo.
echo   Construyendo el ejecutable de lyricsync
echo   ======================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo   [X] Ejecuta primero install.bat
    pause
    exit /b 1
)

call .venv\Scripts\python.exe -m pip install pyinstaller --quiet
call .venv\Scripts\python.exe -m PyInstaller lyricsync.spec --noconfirm --clean
if errorlevel 1 (
    echo   [X] Fallo la construccion.
    pause
    exit /b 1
)

echo.
echo   Listo: dist\lyricsync.exe
echo.
echo   Es autonomo: puedes copiarlo a cualquier PC con Windows sin instalar
echo   Python. Incluye la interfaz y la busqueda de letras ya sincronizadas.
echo   La alineacion con GPU NO va dentro; para eso usa install.bat
echo.
pause
