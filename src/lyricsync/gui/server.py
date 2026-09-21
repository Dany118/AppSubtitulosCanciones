"""The local web application.

Deliberately built on the standard library. The player needs HTTP Range
support so the browser can seek inside an MP3, which none of the obvious web
frameworks give for free, and avoiding extra dependencies keeps this from
colliding with the versions the ML extra pins.

Binds to localhost only. The audio and per-track endpoints address files by
index into the scanned library, so no path outside it is ever reachable.
"""

from __future__ import annotations

import json
import mimetypes
import re
import subprocess
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from ..lrc import format_lrc
from ..models import Lyrics, Status
from ..pipeline import Config, Pipeline, find_tracks
from ..resources import resource_dir
from ..store import Store
from ..tags import clear_lyrics, describe_lyric_frames, read_embedded_lyrics, read_marker
from .jobs import JobRunner
from .state import AppConfig, apply_edits, load_track, save_track

STATIC_DIR = resource_dir("gui", "static")
MAX_BODY_BYTES = 4 * 1024 * 1024
_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")

CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                 ".js": "text/javascript; charset=utf-8"}


class AppServer(ThreadingHTTPServer):
    """Holds the scanned library so handlers never touch arbitrary paths."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        tracks: list[Path],
        config: AppConfig,
        cache_path: Path | None = None,
    ) -> None:
        super().__init__(address, AppHandler)
        self.tracks = list(tracks)
        self.folder = tracks[0].parent if tracks else None
        self.config = config
        self.cache_path = cache_path
        self.jobs = JobRunner()
        # Parsed lyrics per track, so an edit round-trip keeps word timings
        # without re-reading and re-parsing on every request.
        self._lyrics: dict[int, Lyrics | None] = {}
        self._lock = threading.Lock()

    def set_tracks(self, tracks: list[Path], folder: Path | None = None) -> None:
        with self._lock:
            self.tracks = list(tracks)
            self.folder = folder
            self._lyrics.clear()

    def lyrics_for(self, index: int, *, reload: bool = False) -> Lyrics | None:
        with self._lock:
            if reload or index not in self._lyrics:
                _, lyrics = load_track(self.tracks[index])
                self._lyrics[index] = lyrics
            return self._lyrics[index]

    def set_lyrics(self, index: int, lyrics: Lyrics) -> None:
        with self._lock:
            self._lyrics[index] = lyrics

    def invalidate(self) -> None:
        with self._lock:
            self._lyrics.clear()

    def store(self) -> Store | None:
        return Store(self.cache_path) if self.cache_path else None


def _lookup(table: dict[str, str] | None, path: Path) -> str | None:
    """Find a path in a run-log map, tolerating a non-canonical key.

    Runs recorded from the CLI may have been logged relative to whatever
    directory it ran in, so a direct hit is tried before paying for resolve().
    """
    if not table:
        return None
    hit = table.get(str(path))
    if hit is not None:
        return hit
    try:
        return table.get(str(path.resolve()))
    except OSError:
        return None


def pick_folder() -> str | None:
    """Open the operating system's folder chooser.

    Run in a short-lived subprocess: tkinter has main-thread requirements that
    would otherwise constrain the server, and a failure here (no display, no
    tkinter) must degrade to typing a path rather than break the app.
    """
    code = (
        "import tkinter, tkinter.filedialog as fd;"
        "r = tkinter.Tk(); r.withdraw(); r.attributes('-topmost', True);"
        "print(fd.askdirectory() or '')"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=300
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    chosen = result.stdout.strip()
    return chosen or None


class AppHandler(BaseHTTPRequestHandler):
    server_version = "lyricsync"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------- plumbing

    def log_message(self, format: str, *args: object) -> None:
        """Silence per-request noise; the CLI prints what matters."""

    def _send_json(self, payload: object, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status: int, message: str) -> None:
        self._send_json({"error": message}, status=status)

    def _track_index(self, raw: str) -> int | None:
        try:
            index = int(raw)
        except ValueError:
            return None
        return index if 0 <= index < len(self.server.tracks) else None

    def _read_body(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None
        if length <= 0 or length > MAX_BODY_BYTES:
            return None
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _indices(self, body: dict) -> list[int] | None:
        """Validate a list of track indices, defaulting to the whole library."""
        raw = body.get("indices")
        if raw is None:
            return list(range(len(self.server.tracks)))
        if not isinstance(raw, list):
            return None
        indices = []
        for value in raw:
            if not isinstance(value, int) or not 0 <= value < len(self.server.tracks):
                return None
            indices.append(value)
        return indices

    # ----------------------------------------------------------------- GET

    def do_GET(self) -> None:
        path = unquote(urlparse(self.path).path)

        if path in ("/", "/index.html"):
            self._serve_static("index.html")
            return
        if path in ("/app.css", "/app.js"):
            self._serve_static(path.lstrip("/"))
            return
        if path == "/api/library":
            self._serve_library()
            return
        if path == "/api/history":
            self._serve_history()
            return

        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "api":
            # Route on the section first: a job id is not a track index, and
            # resolving it as one would answer "unknown track" for every poll.
            section, ident = parts[1], parts[2]

            if section == "jobs":
                job = self.server.jobs.get(ident)
                if job is None:
                    self._send_error_json(HTTPStatus.NOT_FOUND, "unknown job")
                else:
                    self._send_json(job.snapshot())
                return

            if section in ("track", "audio", "inspect"):
                index = self._track_index(ident)
                if index is None:
                    self._send_error_json(HTTPStatus.NOT_FOUND, "unknown track")
                    return
                if section == "track":
                    self._serve_track(index)
                elif section == "audio":
                    self._serve_audio(self.server.tracks[index])
                else:
                    self._serve_inspect(index)
                return

        self._send_error_json(HTTPStatus.NOT_FOUND, "not found")

    def do_HEAD(self) -> None:
        self.do_GET()

    def _serve_static(self, name: str) -> None:
        file_path = STATIC_DIR / name
        if not file_path.is_file():
            self._send_error_json(HTTPStatus.NOT_FOUND, "missing asset")
            return
        body = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", CONTENT_TYPES.get(file_path.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _track_summary(
        self,
        index: int,
        statuses: dict[str, str] | None = None,
        messages: dict[str, str] | None = None,
    ) -> dict:
        path = self.server.tracks[index]
        lyrics = self.server.lyrics_for(index)
        try:
            meta, _ = load_track(path)
            title, artist, duration = meta.title, meta.artist, meta.duration
        except Exception:  # noqa: BLE001 - a broken file still belongs in the list
            title = artist = duration = None
        return {
            "index": index,
            "name": path.name,
            "title": title,
            "artist": artist,
            "duration": duration,
            "lines": len(lyrics.timed_lines()) if lyrics else 0,
            "hasLyrics": bool(lyrics),
            "wordLevel": bool(lyrics and lyrics.word_level),
            "marker": read_marker(path),
            "lastStatus": _lookup(statuses, path),
            "lastMessage": _lookup(messages, path) or "",
        }

    def _serve_library(self) -> None:
        # Read the run log once for the whole listing rather than per track.
        store = self.server.store()
        statuses = store.latest_statuses() if store else {}
        messages = store.latest_messages() if store else {}
        self._send_json({
            "folder": str(self.server.folder) if self.server.folder else None,
            "tracks": [
                self._track_summary(i, statuses, messages)
                for i in range(len(self.server.tracks))
            ],
        })

    def _serve_track(self, index: int) -> None:
        path = self.server.tracks[index]
        meta, _ = load_track(path)
        lyrics = self.server.lyrics_for(index, reload=True)

        self._send_json({
            "index": index,
            "name": path.name,
            "title": meta.title,
            "artist": meta.artist,
            "duration": meta.duration,
            "source": lyrics.source.value if lyrics else None,
            "wordLevel": bool(lyrics and lyrics.word_level),
            "lines": [
                {
                    "text": line.text,
                    "start": round(line.start, 3) if line.start is not None else None,
                    "end": round(line.end, 3) if line.end is not None else None,
                }
                for line in (lyrics.lines if lyrics else [])
            ],
        })

    def _serve_inspect(self, index: int) -> None:
        path = self.server.tracks[index]
        text = read_embedded_lyrics(path)
        self._send_json({
            "name": path.name,
            "path": str(path),
            "frames": describe_lyric_frames(path),
            "marker": read_marker(path),
            "sidecar": path.with_suffix(".lrc").exists(),
            "words": path.with_suffix(".words.json").exists(),
            "embedded": text,
        })

    def _serve_history(self) -> None:
        store = self.server.store()
        if store is None:
            self._send_json({"runs": []})
            return
        self._send_json({"runs": [
            {
                "path": row["path"],
                "name": Path(row["path"]).name,
                "status": row["status"],
                "source": row["source"],
                "message": row["message"],
                "at": row["ran_at"],
            }
            for row in store.history(100)
        ]})

    def _serve_audio(self, path: Path) -> None:
        """Stream the file, honouring Range so the player can seek."""
        try:
            size = path.stat().st_size
        except OSError:
            self._send_error_json(HTTPStatus.NOT_FOUND, "audio file missing")
            return

        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        start, end = 0, size - 1
        partial = False

        header = self.headers.get("Range")
        if header:
            match = _RANGE_RE.match(header.strip())
            if not match:
                self._send_range_error(size)
                return

            raw_start, raw_end = match.group(1), match.group(2)
            if raw_start:
                start = int(raw_start)
                if raw_end:
                    end = min(int(raw_end), size - 1)
            elif raw_end:
                # A suffix range: the last N bytes of the file.
                start = max(0, size - int(raw_end))
            if start > end or start >= size:
                self._send_range_error(size)
                return
            partial = True

        length = end - start + 1
        self.send_response(HTTPStatus.PARTIAL_CONTENT if partial else HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()

        if self.command == "HEAD":
            return
        with path.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def _send_range_error(self, size: int) -> None:
        self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
        self.send_header("Content-Range", f"bytes */{size}")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---------------------------------------------------------------- POST

    def do_POST(self) -> None:
        path = unquote(urlparse(self.path).path)
        parts = path.strip("/").split("/")

        routes = {
            ("api", "library"): self._set_library,
            ("api", "browse"): self._browse,
            ("api", "export"): self._export,
        }
        handler = routes.get(tuple(parts))
        if handler is not None:
            handler()
            return

        if len(parts) == 3 and parts[:2] == ["api", "jobs"]:
            self._start_job(parts[2])
            return
        if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "cancel":
            self._send_json({"cancelled": self.server.jobs.cancel(parts[2])})
            return
        if len(parts) == 4 and parts[:2] == ["api", "track"] and parts[3] == "save":
            index = self._track_index(parts[2])
            if index is None:
                self._send_error_json(HTTPStatus.NOT_FOUND, "unknown track")
                return
            self._save(index)
            return

        self._send_error_json(HTTPStatus.NOT_FOUND, "not found")

    def _set_library(self) -> None:
        body = self._read_body()
        if body is None or not isinstance(body.get("path"), str):
            self._send_error_json(HTTPStatus.BAD_REQUEST, "'path' is required")
            return

        # Canonical so the paths match what the run log recorded.
        folder = Path(body["path"]).expanduser()
        if folder.exists():
            folder = folder.resolve()
        if not folder.exists():
            self._send_error_json(HTTPStatus.BAD_REQUEST, f"no such folder: {folder}")
            return

        tracks = find_tracks(folder, recursive=bool(body.get("recursive", True)))
        self.server.set_tracks(tracks, folder if folder.is_dir() else folder.parent)
        self._serve_library()

    def _browse(self) -> None:
        chosen = pick_folder()
        if chosen is None:
            self._send_json({"path": None, "available": False})
            return
        self._send_json({"path": chosen, "available": True})

    def _export(self) -> None:
        body = self._read_body()
        if body is None:
            self._send_error_json(HTTPStatus.BAD_REQUEST, "invalid request body")
            return
        index = body.get("index")
        if not isinstance(index, int) or not 0 <= index < len(self.server.tracks):
            self._send_error_json(HTTPStatus.BAD_REQUEST, "unknown track")
            return

        lyrics = self.server.lyrics_for(index)
        if lyrics is None:
            self._send_error_json(HTTPStatus.BAD_REQUEST, "track has no synced lyrics")
            return

        enhanced = bool(body.get("enhanced"))
        if enhanced and not lyrics.word_level:
            self._send_error_json(
                HTTPStatus.BAD_REQUEST,
                "no word-level timings for this track; run sync with alignment first",
            )
            return

        path = self.server.tracks[index]
        destination = path.with_suffix(".lrc")
        destination.write_text(
            format_lrc(lyrics, enhanced=enhanced, decimals=self.server.config.decimals),
            encoding="utf-8",
        )
        self._send_json({"written": str(destination), "enhanced": enhanced,
                         "lines": len(lyrics.timed_lines())})

    def _save(self, index: int) -> None:
        body = self._read_body()
        if body is None:
            self._send_error_json(HTTPStatus.BAD_REQUEST, "invalid request body")
            return

        lyrics = self.server.lyrics_for(index)
        if lyrics is None:
            self._send_error_json(HTTPStatus.BAD_REQUEST, "track has no lyrics to save")
            return

        starts = body.get("starts")
        if not isinstance(starts, list):
            self._send_error_json(HTTPStatus.BAD_REQUEST, "'starts' must be a list")
            return
        try:
            cleaned = [None if s is None else float(s) for s in starts]
        except (TypeError, ValueError):
            self._send_error_json(HTTPStatus.BAD_REQUEST, "'starts' must be numbers or null")
            return

        try:
            edited = apply_edits(lyrics, cleaned)
        except ValueError as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return

        try:
            report = save_track(self.server.tracks[index], edited, self.server.config)
        except OSError as exc:
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, f"could not write: {exc}")
            return

        self.server.set_lyrics(index, edited)
        self._send_json({"saved": True, **report})

    # ----------------------------------------------------------------- jobs

    def _start_job(self, kind: str) -> None:
        if kind not in ("sync", "strip"):
            self._send_error_json(HTTPStatus.NOT_FOUND, "unknown job kind")
            return
        if self.server.jobs.active() is not None:
            self._send_error_json(HTTPStatus.CONFLICT, "a job is already running")
            return

        body = self._read_body()
        if body is None:
            self._send_error_json(HTTPStatus.BAD_REQUEST, "invalid request body")
            return

        indices = self._indices(body)
        if indices is None:
            self._send_error_json(HTTPStatus.BAD_REQUEST, "'indices' must be valid track indices")
            return
        if not indices:
            self._send_error_json(HTTPStatus.BAD_REQUEST, "no tracks selected")
            return

        paths = [self.server.tracks[i] for i in indices]
        worker = self._sync_worker(body) if kind == "sync" else self._strip_worker(body)
        job = self.server.jobs.submit(kind, paths, worker)
        self._send_json(job.snapshot(), status=HTTPStatus.ACCEPTED)

    def _sync_worker(self, body: dict):
        options = body.get("options") or {}
        config = Config(
            device=str(options.get("device", "cuda")),
            separate_vocals=bool(options.get("demucs", True)),
            allow_asr=bool(options.get("asr", False)),
            use_network=not bool(options.get("offline", False)),
            force=bool(options.get("force", False)),
            backup=bool(options.get("backup", False)),
            dry_run=bool(options.get("dryRun", False)),
            enhanced_sidecar=bool(options.get("enhanced", False)),
            lead_in=float(options.get("leadIn", 0.0) or 0.0),
            id3_version=self.server.config.id3_version,
        )
        # One pipeline for the whole job so the alignment model loads once.
        store = self.server.store()
        pipeline = Pipeline(config, store=store)
        server = self.server

        def worker(path: Path) -> dict:
            result = pipeline.process(path)
            if store is not None:
                store.log_run(path, result.status.value,
                              result.source.value if result.source else None,
                              result.log_message)
            server.invalidate()
            return {
                "item": path.name,
                "status": result.status.value,
                "message": result.message,
                "warnings": result.warnings,
                "lines": result.line_count,
                "source": result.source.value if result.source else None,
            }

        return worker

    def _strip_worker(self, body: dict):
        backup = bool((body.get("options") or {}).get("backup", False))
        server = self.server

        def worker(path: Path) -> dict:
            removed = clear_lyrics(path, backup=backup)
            sidecar = path.with_suffix(".lrc")
            if (body.get("options") or {}).get("sidecar") and sidecar.exists():
                sidecar.unlink()
            server.invalidate()
            return {
                "item": path.name,
                "status": Status.OK.value if removed else Status.SKIPPED.value,
                "message": ", ".join(removed) if removed else "nothing to remove",
            }

        return worker


def serve(
    tracks: list[Path],
    *,
    host: str = "127.0.0.1",
    port: int = 8733,
    config: AppConfig | None = None,
    cache_path: Path | None = None,
    open_browser: bool = True,
) -> None:
    """Run the application until interrupted."""
    server = AppServer((host, port), tracks, config or AppConfig(), cache_path)
    url = f"http://{host}:{server.server_address[1]}/"
    print(f"lyricsync running at {url}  ({len(tracks)} track(s) loaded, Ctrl-C to stop)")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
