#!/usr/bin/env bash
# Abre la aplicacion. En Windows usa lyricsync.bat
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
    echo
    echo "  La aplicacion no esta instalada todavia."
    echo "  Ejecuta primero ./install.sh"
    echo
    exit 1
fi

echo
echo "  Abriendo lyricsync en el navegador..."
echo "  Deja esta ventana abierta. Para cerrar la aplicacion, pulsa Ctrl+C."
echo

exec .venv/bin/python -m lyricsync gui "$@"
