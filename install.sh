#!/usr/bin/env bash
# Instalador para Linux y macOS. En Windows usa install.bat
set -euo pipefail
cd "$(dirname "$0")"

echo
echo "  Instalando lyricsync"
echo "  ===================="
echo

if ! command -v python3 >/dev/null 2>&1; then
    echo "  [X] No se encuentra python3. Instalalo con el gestor de paquetes de tu sistema."
    exit 1
fi
echo "  $(python3 --version) encontrado."
echo

[ -d .venv ] || { echo "  Creando el entorno virtual..."; python3 -m venv .venv; }

echo "  Instalando las dependencias basicas..."
.venv/bin/python -m pip install --upgrade pip --quiet
.venv/bin/python -m pip install -e . --quiet

echo
echo "  Listo. Ya puedes usar la aplicacion con ./lyricsync.sh"
echo
echo "  ---------------------------------------------------------------"
echo "  Opcional: alineacion con GPU"
echo
echo "  Permite sincronizar canciones cuya letra NO esta ya sincronizada"
echo "  en las bases de datos. Descarga varios GB y necesita ffmpeg."
echo "  ---------------------------------------------------------------"
echo
read -r -p "  Instalar tambien la parte de GPU? (s/N): " gpu

if [[ "${gpu,,}" == "s" ]]; then
    echo
    echo "  Descargando PyTorch con CUDA... esto tarda un rato."
    if .venv/bin/python -m pip install -r requirements-gpu.txt \
       && .venv/bin/python -m pip install -r requirements-align.txt \
       && .venv/bin/python -m pip install -r requirements-translate.txt; then
        echo
        echo "  Parte de GPU instalada. Comprobando..."
        .venv/bin/python -m lyricsync doctor
        command -v ffmpeg >/dev/null 2>&1 || {
            echo
            echo "  [!] Falta ffmpeg, que esta parte necesita."
            echo "      Debian/Ubuntu:  sudo apt install ffmpeg"
            echo "      macOS:          brew install ffmpeg"
        }
    else
        echo
        echo "  [!] Fallo la instalacion de la parte de GPU."
        echo "      La aplicacion funciona igual sin ella."
    fi
fi

echo
echo "  Instalacion terminada. Ejecuta ./lyricsync.sh para abrir la aplicacion."
echo
