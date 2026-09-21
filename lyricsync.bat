@echo off
setlocal

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo   La aplicacion no esta instalada todavia.
    echo   Ejecuta primero install.bat
    echo.
    pause
    exit /b 1
)

echo.
echo   Abriendo lyricsync en el navegador...
echo   Deja esta ventana abierta. Para cerrar la aplicacion, pulsa Ctrl+C.
echo.

.venv\Scripts\python.exe -m lyricsync gui %*
