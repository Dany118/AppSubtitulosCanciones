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

- **`.lrc` hermano** junto al MP3 → el formato más universal, el más fácil de
  corregir a mano, y el que los reproductores prefieren cuando existe. **Es la
  salida por defecto, y no toca el MP3 en absoluto.**
- **`USLT`** con el texto LRC dentro, solo si lo pides (`--embed`, o
  desmarcando *Solo generar el .lrc*) → es el frame que los reproductores
  Android leen cuando no hay `.lrc` (el `SYLT` "oficial" casi nadie lo
  soporta; se escribe igual por si acaso).
- **`.words.json`** → los tiempos por palabra. Calcularlos es lo caro; guardarlos
  significa que cambiar entre salida por línea y por palabra después no cuesta nada.
- ID3v2.3 por defecto (mejor compatibilidad con Android que v2.4).

**BlackPlayer EX** muestra la letra línea a línea, que es la salida por defecto.
Si algún día cambias a Poweramp o Salt Player, `lyricsync export --enhanced`
regenera el `.lrc` con resaltado palabra por palabra sin reprocesar el audio.

---

## Instalación

### Windows (lo más sencillo)

1. Instala **Python 3.10 o superior** desde [python.org](https://www.python.org/downloads/).
   Marca la casilla *"Add Python to PATH"* durante la instalación.
2. Descarga este repositorio y descomprímelo.
3. Doble clic en **`install.bat`**.
4. Doble clic en **`lyricsync.bat`** para abrir la aplicación.

El instalador te preguntará si quieres añadir también la parte de GPU
(alineación forzada y transcripción). Puedes decir que no y añadirla después.

### Linux y macOS

```bash
git clone https://github.com/Dany118/AppSubtitulosCanciones
cd AppSubtitulosCanciones
./install.sh
./lyricsync.sh
```

### A mano

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt    # o: pip install -e .
lyricsync gui
```

### Archivos de dependencias

| Archivo | Qué instala |
|---------|-------------|
| `requirements.txt` | El núcleo: interfaz, etiquetas, búsqueda de letras ya sincronizadas. Sin GPU. |
| `requirements-align.txt` | PyTorch, Demucs y Whisper para la alineación forzada. Varios GB, requiere ffmpeg. |
| `requirements-dev.txt` | Tests y construcción del ejecutable. |

### Ejecutable autónomo

Para tener un `.exe` que funcione en un PC **sin Python instalado**:

```
build_exe.bat          (Windows)
pyinstaller lyricsync.spec   (cualquier sistema)
```

Deja un único archivo en `dist/`. Al ejecutarlo abre la interfaz directamente.

Incluye la interfaz completa y la búsqueda de letras ya sincronizadas, que es
la ruta que resuelve la mayoría de una biblioteca en inglés. **No incluye la
alineación con GPU**: empaquetar PyTorch, CUDA y los modelos convertiría una
descarga de ~25 MB en varios gigabytes, y un ejecutable congelado no tiene pip
para descargar modelos después. Para esa parte usa la instalación normal.

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

# Abrir la aplicación gráfica (todo lo anterior, en una ventana)
lyricsync gui
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

## Traducción: inglés y español a la vez

Pensado para leer: cada línea original acompañada de su traducción.

```bash
lyricsync sync ~/Musica --translate
lyricsync sync ~/Musica --translate --bilingual stacked
```

En la interfaz es la casilla **Traducción al español**, con un desplegable de
formato al lado.

LRC no tiene un campo nativo para un segundo idioma, así que hay dos
convenciones y cada reproductor se lleva mejor con una:

| Formato | Cómo queda | Cuándo usarlo |
|---------|-----------|---------------|
| `inline` (misma línea) | `[00:12.34]the original line / la línea traducida` | Funciona en cualquier reproductor, porque es una línea de texto normal |
| `stacked` (línea aparte) | dos líneas con la misma marca de tiempo | Se lee mejor en el móvil, si tu reproductor muestra líneas consecutivas |

**Prueba las dos en tu reproductor.** Cuál se ve mejor no lo puede decidir el
código.

### De dónde sale la traducción

1. **Un archivo tuyo**, `cancion.es.txt`, una línea por línea de letra. Si
   existe, gana siempre — así puedes corregir a mano y la siguiente ejecución
   no te lo pisa.
2. **Un modelo local** (Helsinki-NLP opus-mt). Sin clave de API y sin conexión
   después de la primera descarga:

```bash
pip install -r requirements-translate.txt
```

Las traducciones se cachean por línea, no por canción: un estribillo se traduce
una sola vez, y si se repite en otra canción tampoco se recalcula.

### Cómo saber si una canción está realmente traducida

Tres sitios, de más rápido a más detallado:

- **Biblioteca** — junto al estado aparece un chip `ES` si lleva traducción, o
  *sin traducir* si no.
- **Revisar** — la ves. Y la barra de estado dice cuántas líneas están
  traducidas: `traducida (es): 24/24 líneas`.
- **Inspeccionar** — la respuesta exacta: `sí — 24 de 24 líneas (es, misma
  línea)`, y si hay un `.es.txt` tuyo al lado.

Si una canción sale como `18/24`, la traducción está incompleta: el resto
aparecerá solo en inglés.

### El archivo se puede volver a leer

El `.lrc` guarda en su cabecera qué idioma y qué formato se usó (`[tr:]` y
`[trsep:]`), así que al reprocesar la canción el programa vuelve a separar las
dos lenguas en vez de tratar la línea mezclada como si fuera el original. Sin
eso, cada ejecución le añadiría la traducción otra vez.


---

## La aplicación gráfica

Todo lo que hacen los comandos, en una ventana:

```bash
lyricsync gui                 # elige la carpeta dentro de la app
lyricsync gui ~/Musica        # o ábrela ya cargada
```

Se abre en el navegador (en `127.0.0.1`, solo tu máquina). Cuatro pestañas:

**Biblioteca** — la lista de pistas con su estado: *sincronizada*, *por palabra*,
*sin letra*, o **revisar** en ámbar cuando el validador no quedó convencido de
los tiempos (pasa el ratón por encima y te dice por qué). Marcas las que quieras y pulsas **Sincronizar** o **Limpiar letra**. En
**Opciones** están todos los modificadores de la CLI: aislar voz, transcribir,
sin red, reprocesar, copia `.bak`, simulacro, dispositivo y adelanto.

El trabajo corre en segundo plano con barra de progreso y un registro en vivo
línea a línea, y se puede **cancelar**: se detiene tras la pista en curso, sin
dejar ningún archivo a medias.

**Revisar** — el reproductor con la letra scrolleando. Si la pista está
traducida, cada línea muestra el original arriba y la traducción debajo. Las
teclas de siempre (`←` `→` mueven toda la letra, `T` remarca una línea suelta,
`Ctrl+S` guarda). Ajustar los tiempos **conserva la traducción y su formato**.

**Inspeccionar** — qué frames tiene el MP3 ahora mismo, si hay sidecar, si hay
tiempos por palabra, y la letra embebida tal cual.

**Historial** — las últimas ejecuciones con su resultado.

El botón **Examinar…** abre el selector de carpetas del sistema. Si no está
disponible, se escribe la ruta a mano y la app lo dice en vez de fallar.


---

## El revisor, en detalle

Lo que convierte "casi en tiempo" en "en tiempo". Abre un reproductor local en
el navegador con la letra scrolleando, y la corriges a oído:

Está en la pestaña **Revisar** de la app. También se abre directo sobre una
carpeta:

```bash
lyricsync review ~/Musica

# Solo las que el validador marcó para revisar
lyricsync review ~/Musica --only-review
```

| Tecla | Acción |
|-------|--------|
| `espacio` | Reproducir / pausar |
| `←` `→` | Mueve **toda** la letra 100 ms (con `Shift`, 10 ms) |
| `↑` `↓` | Selecciona línea |
| `T` | Fija el inicio de la línea seleccionada **al momento actual** |
| `Ctrl+S` | Guardar |

Hacer clic en una línea salta a ese punto de la canción, así que puedes
comprobar un verso concreto al instante.

El flujo normal son dos pasos: mueves todo con las flechas hasta que la primera
estrofa encaja (eso arregla la mayoría de los desfases, que son constantes), y
si alguna línea suelta sigue descuadrada, la seleccionas y la remarcas con `T`.

Al guardar se reescribe el MP3 y el `.lrc`. **Los tiempos por palabra
sobreviven**: cada palabra se desplaza junto con su línea, así que corregir el
offset no destruye el trabajo de la alineación forzada.

En el revisor la validación avisa pero **no bloquea**: estás escuchando la
canción, tu criterio vale más que la heurística.

El servidor escucha solo en `127.0.0.1` y únicamente sirve los archivos de la
carpeta que le indicas.

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
  gui/            Aplicación web local (servidor stdlib con soporte de Range)
```

## Desarrollo

```bash
pip install -r requirements-dev.txt
pytest
```

Los tests construyen un MP3 sintético a partir de cabeceras MPEG y simulan las
respuestas HTTP, así que la suite no necesita ffmpeg, ni GPU, ni red. Los del
servidor levantan uno real en un puerto efímero.

## Estado

- **Fase 1** — letra sincronizada desde LRCLIB y archivos locales. ✅
- **Fase 2** — alineación forzada con tiempos por palabra, aislado de voz,
  validación y ASR de reserva. ✅ (requiere el extra `[align]`)
- **Fase 3** — lotes con caché e idempotencia, y aplicación gráfica con
  biblioteca, sincronización en segundo plano, revisor, inspector e historial. ✅

## Nota

La herramienta obtiene letras de LRCLIB y las incrusta en tus propios archivos,
para uso personal. Las letras son propiedad de sus autores.
