@echo off
setlocal

echo.
echo   Instalando lyricsync
echo   ====================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo   [X] No se encuentra Python.
    echo.
    echo   Instalalo desde https://www.python.org/downloads/
    echo   IMPORTANTE: marca la casilla "Add Python to PATH" al instalarlo.
    echo.
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo   Python %PYVER% encontrado.
echo.

if not exist ".venv" (
    echo   Creando el entorno virtual...
    python -m venv .venv
    if errorlevel 1 (
        echo   [X] No se pudo crear el entorno virtual.
        pause
        exit /b 1
    )
)

echo   Instalando las dependencias basicas...
call .venv\Scripts\python.exe -m pip install --upgrade pip --quiet
call .venv\Scripts\python.exe -m pip install -e . --quiet
if errorlevel 1 (
    echo   [X] Fallo la instalacion.
    pause
    exit /b 1
)

echo.
echo   Listo. Ya puedes usar la aplicacion con lyricsync.bat
echo.
echo   ---------------------------------------------------------------
echo   Opcional: alineacion con GPU
echo.
echo   Permite sincronizar canciones cuya letra NO esta ya sincronizada
echo   en las bases de datos. Descarga varios GB y necesita ffmpeg.
echo   ---------------------------------------------------------------
echo.

set /p GPU="  Instalar tambien la parte de GPU? (s/N): "
if /i not "%GPU%"=="s" goto :done

echo.
echo   Descargando PyTorch con CUDA... esto tarda un rato.
call .venv\Scripts\python.exe -m pip install -r requirements-gpu.txt
call .venv\Scripts\python.exe -m pip install -r requirements-align.txt
call .venv\Scripts\python.exe -m pip install -r requirements-translate.txt
if errorlevel 1 (
    echo.
    echo   [!] Fallo la instalacion de la parte de GPU.
    echo       La aplicacion funciona igual sin ella.
) else (
    echo.
    echo   Parte de GPU instalada. Comprobando...
    call .venv\Scripts\python.exe -m lyricsync doctor
    where ffmpeg >nul 2>&1
    if errorlevel 1 (
        echo.
        echo   [!] Falta ffmpeg, que esta parte necesita. Instalalo con:
        echo          winget install Gyan.FFmpeg
        echo       y despues cierra y vuelve a abrir esta ventana.
    )
)

:done
echo.
echo   Instalacion terminada. Ejecuta lyricsync.bat para abrir la aplicacion.
echo.
pause
