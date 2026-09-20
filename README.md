# lyricsync

Incrusta letras **sincronizadas en el tiempo** dentro de archivos MP3, para leerlas
en el móvil mientras suena la canción.

Pensado para practicar *reading* en inglés: la letra aparece en el reproductor
justo cuando se canta.

---

## Cómo funciona

El problema no es escribir la etiqueta, es **conseguir los tiempos correctos**.
El pipeline prueba tres estrategias, de la más fiable a la menos:

| # | Estrategia | Cuándo se usa | Precisión |
|---|-----------|---------------|-----------|
| 1 | **LRC ya sincronizado** (`.lrc` local, LRCLIB, o ya embebido) | La letra con tiempos ya existe | La del original |
| 2 | **Alineación forzada** (Demucs + MMS_FA) | Tenemos la letra en texto, faltan los tiempos | Alta — el modelo *sabe* qué se canta, solo decide *cuándo* |
| 3 | **ASR** (Whisper) | No hay letra por ningún lado | Baja — siempre marcado para revisión |

La estrategia 2 es la clave: al darle el texto al modelo, no tiene que adivinar
las palabras, solo localizarlas. Por eso el pipeline busca la letra real antes
de recurrir a transcribir.

**Aislar la voz antes de alinear** es la mayor ganancia de precisión de todo el
sistema. Con la instrumental encima el alineador se desvía cientos de
milisegundos; sobre una voz limpia baja a decenas.

### Formato de salida

- **`USLT`** con el texto LRC dentro → es el frame que los reproductores Android
  leen de verdad (el `SYLT` "oficial" casi nadie lo soporta; se escribe igual por
  si acaso).
- **`.lrc` hermano** junto al MP3 → el formato más universal y el más fácil de
  corregir a mano.
- **`.words.json`** → los tiempos por palabra. Calcularlos es lo caro; guardarlos
  significa que cambiar entre salida por línea y por palabra después no cuesta nada.
- ID3v2.3 por defecto (mejor compatibilidad con Android que v2.4).

**BlackPlayer EX** muestra la letra línea a línea, que es la salida por defecto.
Si algún día cambias a Poweramp o Salt Player, `lyricsync export --enhanced`
regenera el `.lrc` con resaltado palabra por palabra sin reprocesar el audio.

---

## Instalación

Requiere **Python 3.10+** y **ffmpeg** en el PATH.

```bash
git clone https://github.com/Dany118/AppSubtitulosCanciones
cd AppSubtitulosCanciones
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
```

Eso ya te da la estrategia 1 (sin ML, sin GPU).

Para las estrategias 2 y 3, instala PyTorch con la rueda CUDA que corresponda a
tu driver y luego el extra:

```bash
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124
pip install -e ".[align]"
```

---

## Uso

```bash
# Una canción
lyricsync sync "cancion.mp3"

# Ver qué tiene ya embebido, sin tocar nada
lyricsync inspect "cancion.mp3"

# Toda una carpeta, probando primero sin escribir
lyricsync sync ~/Musica --dry-run
lyricsync sync ~/Musica --backup

# Sin red: solo .lrc/.txt que ya tengas al lado del MP3
lyricsync sync ~/Musica --offline

# Transcribir las que no aparezcan en ninguna base de datos
lyricsync sync ~/Musica --asr

# Quitar toda la letra embebida
lyricsync strip "cancion.mp3"

# Regenerar el .lrc con resaltado por palabra
lyricsync export "cancion.mp3" --enhanced
```

### Opciones útiles

| Opción | Para qué |
|--------|----------|
| `--lead-in 0.3` | Muestra cada línea 0,3 s antes de cantarse. Ayuda a leer a tiempo. |
| `--no-demucs` | Salta el aislado de voz. Mucho más rápido, bastante menos preciso. |
| `--device cpu` | Si no quieres usar la GPU. |
| `--force` | Reprocesa archivos ya marcados por lyricsync. |
| `--dry-run` | Informa de lo que pasaría, sin escribir nada. |
| `--limit 10` | Prueba con las 10 primeras antes de lanzar el lote entero. |

---

## Seguridad de los archivos

- Toda escritura pasa por una copia temporal y termina en `os.replace`, así que
  un lote interrumpido **nunca** deja un MP3 a medio escribir.
- Los frames de audio no se tocan: solo se reescriben las etiquetas.
- Los tiempos se **validan antes** de escribir. Si la letra va desordenada, se
  sale de la duración de la pista, o se desvía del inicio real de la voz, el
  archivo se marca para revisión y no se embebe nada.
- Se limpia la letra antigua de todos los sitios donde se esconde: `USLT` (puede
  haber varias copias en distintos idiomas), `SYLT`, `TXXX:LYRICS` y los bloques
  legacy **Lyrics3 v1/v2** del final del archivo. Las demás etiquetas no se tocan.
- Cada archivo procesado queda marcado (`TXXX:LYRICSYNC`), así que volver a
  lanzar un lote no repite trabajo.

---

## Estructura

```
src/lyricsync/
  models.py       Estructuras compartidas (Word, LyricLine, Lyrics, TrackMeta)
  lrc.py          Parseo y escritura de LRC, estándar y enhanced
  tags.py         Lectura, limpieza y escritura de ID3 (escritura atómica)
  audio.py        Decodificación con ffmpeg, Demucs, detección de inicio vocal
  text.py         Normalización: una versión para mostrar, otra para alinear
  validate.py     Comprobaciones de los tiempos antes de escribir
  store.py        Caché SQLite y registro de ejecuciones
  pipeline.py     El orquestador y la cascada de estrategias
  cli.py          Interfaz de línea de comandos
  providers/      De dónde sale la letra (LRCLIB, archivos locales, embebida)
  align/          Cómo se sincroniza (alineación forzada, ASR)
```

## Desarrollo

```bash
pip install -e ".[dev]"
pytest
```

Los tests construyen un MP3 sintético a partir de cabeceras MPEG y simulan las
respuestas HTTP, así que la suite no necesita ffmpeg, ni GPU, ni red.

## Estado

- **Fase 1** — letra sincronizada desde LRCLIB y archivos locales. ✅
- **Fase 2** — alineación forzada con tiempos por palabra, aislado de voz,
  validación y ASR de reserva. ✅ (requiere el extra `[align]`)
- **Fase 3** — lotes con caché e idempotencia. ✅ Pendiente: revisor web para
  ajustar el offset a oído.

## Nota

La herramienta obtiene letras de LRCLIB y las incrusta en tus propios archivos,
para uso personal. Las letras son propiedad de sus autores.
