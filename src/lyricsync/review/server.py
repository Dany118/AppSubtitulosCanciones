"""A small local HTTP server for reviewing lyric timings by ear.

Deliberately built on the standard library. The reviewer needs HTTP Range
support so the browser can seek inside an MP3, which none of the obvious web
frameworks give for free, and avoiding extra dependencies keeps this from
colliding with the versions the ML extra pins.

Binds to localhost only and serves nothing outside the scanned track list.
"""

from __future__ import annotations

import json
import mimetypes
import re
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from ..models import Lyrics
from .state import ReviewConfig, apply_edits, load_track, save_track

STATIC_DIR = Path(__file__).parent / "static"
MAX_BODY_BYTES = 4 * 1024 * 1024
_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


class ReviewServer(ThreadingHTTPServer):
    """Holds the track list so handlers never touch arbitrary paths."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], tracks: list[Path], config: ReviewConfig) -> None:
        super().__init__(address, ReviewHandler)
        self.tracks = tracks
        self.config = config
        # Cache parsed lyrics per track so an edit round-trip keeps word
        # timings without re-reading and re-parsing on every request.
        self._lyrics: dict[int, Lyrics | None] = {}
        self._lock = threading.Lock()

    def lyrics_for(self, index: int, *, reload: bool = False) -> Lyrics | None:
        with self._lock:
            if reload or index not in self._lyrics:
                _, lyrics = load_track(self.tracks[index])
                self._lyrics[index] = lyrics
            return self._lyrics[index]

    def set_lyrics(self, index: int, lyrics: Lyrics) -> None:
        with self._lock:
            self._lyrics[index] = lyrics


class ReviewHandler(BaseHTTPRequestHandler):
    server_version = "lyricsync-review"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------- plumbing

    def log_message(self, format: str, *args: object) -> None:
        """Silence the per-request noise; the CLI prints what matters."""

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
        """Resolve a path segment to a valid track index, or None."""
        try:
            index = int(raw)
        except ValueError:
            return None
        return index if 0 <= index < len(self.server.tracks) else None

    # ----------------------------------------------------------------- GET

    def do_GET(self) -> None:
        path = unquote(urlparse(self.path).path)

        if path in ("/", "/index.html"):
            self._serve_static("index.html")
            return
        if path == "/api/tracks":
            self._serve_track_list()
            return

        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "track":
            index = self._track_index(parts[2])
            if index is None:
                self._send_error_json(HTTPStatus.NOT_FOUND, "unknown track")
                return
            self._serve_track(index)
            return
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "audio":
            index = self._track_index(parts[2])
            if index is None:
                self._send_error_json(HTTPStatus.NOT_FOUND, "unknown track")
                return
            self._serve_audio(self.server.tracks[index])
            return

        self._send_error_json(HTTPStatus.NOT_FOUND, "not found")

    def _serve_static(self, name: str) -> None:
        file_path = STATIC_DIR / name
        if not file_path.is_file():
            self._send_error_json(HTTPStatus.NOT_FOUND, "missing asset")
            return
        body = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_track_list(self) -> None:
        payload = []
        for index, path in enumerate(self.server.tracks):
            lyrics = self.server.lyrics_for(index)
            payload.append({
                "index": index,
                "name": path.name,
                "lines": len(lyrics.timed_lines()) if lyrics else 0,
                "hasLyrics": bool(lyrics),
                "wordLevel": bool(lyrics and lyrics.word_level),
            })
        self._send_json({"tracks": payload})

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
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
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
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
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

    def do_HEAD(self) -> None:
        self.do_GET()

    # ---------------------------------------------------------------- POST

    def do_POST(self) -> None:
        path = unquote(urlparse(self.path).path)
        parts = path.strip("/").split("/")

        if len(parts) == 4 and parts[0] == "api" and parts[1] == "track" and parts[3] == "save":
            index = self._track_index(parts[2])
            if index is None:
                self._send_error_json(HTTPStatus.NOT_FOUND, "unknown track")
                return
            self._save(index)
            return

        self._send_error_json(HTTPStatus.NOT_FOUND, "not found")

    def _read_body(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None
        if length <= 0 or length > MAX_BODY_BYTES:
            return None
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

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

        path = self.server.tracks[index]
        try:
            report = save_track(path, edited, self.server.config)
        except OSError as exc:
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, f"could not write: {exc}")
            return

        self.server.set_lyrics(index, edited)
        self._send_json({"saved": True, **report})


def serve(
    tracks: list[Path],
    *,
    host: str = "127.0.0.1",
    port: int = 8733,
    config: ReviewConfig | None = None,
    open_browser: bool = True,
) -> None:
    """Run the reviewer until interrupted."""
    server = ReviewServer((host, port), tracks, config or ReviewConfig())
    url = f"http://{host}:{server.server_address[1]}/"
    print(f"Reviewing {len(tracks)} track(s) at {url}  (Ctrl-C to stop)")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
