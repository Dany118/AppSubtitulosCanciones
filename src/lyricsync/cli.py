"""Command line interface."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from . import __version__
from .lrc import BILINGUAL_STYLES, format_lrc
from .models import LyricLine, Lyrics, ProcessResult, Source, Status, Word
from .pipeline import Config, Pipeline, find_tracks
from .store import Store
from .tags import clear_lyrics, describe_lyric_frames, is_synced_text, read_embedded_lyrics, read_meta

app = typer.Typer(add_completion=False, help="Embed time-synced lyrics into MP3 files.")
console = Console()

STATUS_STYLE = {
    Status.OK: "green",
    Status.REVIEW: "yellow",
    Status.SKIPPED: "dim",
    Status.FAILED: "red",
}

DEFAULT_CACHE = Path.home() / ".cache" / "lyricsync" / "cache.db"


@app.command()
def sync(
    target: Path = typer.Argument(..., exists=True, help="An MP3 file or a folder of them."),
    device: str = typer.Option("cuda", help="Torch device for alignment: cuda or cpu."),
    asr: bool = typer.Option(False, "--asr", help="Transcribe when no lyrics can be found."),
    demucs: bool = typer.Option(True, "--demucs/--no-demucs", help="Isolate vocals before aligning (much more accurate)."),
    enhanced: bool = typer.Option(False, "--enhanced", help="Write a word-level .lrc sidecar (Poweramp, Salt Player)."),
    sidecar: bool = typer.Option(True, "--sidecar/--no-sidecar", help="Write a .lrc file next to the MP3."),
    embed: bool = typer.Option(False, "--embed", help="Also write the lyrics into the MP3 tags. Off by default: players prefer the .lrc."),
    translate: bool = typer.Option(False, "--translate", help="Add a translation beside each original line."),
    language: str = typer.Option("es", "--lang", help="Target language for --translate."),
    bilingual: str = typer.Option("inline", "--bilingual", help="How to lay out a translation: inline or stacked."),
    lead_in: float = typer.Option(0.0, "--lead-in", help="Show each line this many seconds early."),
    id3: int = typer.Option(3, "--id3", help="ID3 version to save: 3 (widest Android support) or 4."),
    offline: bool = typer.Option(False, "--offline", help="Do not query LRCLIB; use local files only."),
    force: bool = typer.Option(False, "--force", help="Reprocess files already marked by lyricsync."),
    backup: bool = typer.Option(False, "--backup", help="Keep a .bak copy of each original."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report what would happen, write nothing."),
    recursive: bool = typer.Option(True, "--recursive/--no-recursive"),
    limit: int = typer.Option(0, "--limit", help="Stop after N files (0 = no limit)."),
    cache: Path = typer.Option(DEFAULT_CACHE, "--cache", help="SQLite cache location."),
) -> None:
    """Find, time and embed lyrics for one track or a whole library."""
    tracks = find_tracks(target, recursive=recursive)
    if limit > 0:
        tracks = tracks[:limit]
    if not tracks:
        console.print("[red]No MP3 files found.[/red]")
        raise typer.Exit(1)

    if bilingual not in BILINGUAL_STYLES:
        console.print(f"[red]--bilingual must be one of: {', '.join(BILINGUAL_STYLES)}[/red]")
        raise typer.Exit(2)

    config = Config(
        device=device,
        id3_version=id3,
        write_sidecar=sidecar,
        sidecar_only=not embed,
        translate=translate,
        target_language=language,
        bilingual=bilingual,
        enhanced_sidecar=enhanced,
        lead_in=lead_in,
        separate_vocals=demucs,
        allow_asr=asr,
        force=force,
        backup=backup,
        dry_run=dry_run,
        use_network=not offline,
    )
    store = Store(cache)
    pipeline = Pipeline(config, store=store)

    results: list[ProcessResult] = []
    with console.status("[bold]Processing...") as status_line:
        for index, path in enumerate(tracks, start=1):
            status_line.update(f"[bold]({index}/{len(tracks)})[/bold] {path.name}")
            result = pipeline.process(path)
            results.append(result)
            store.log_run(path, result.status.value,
                          result.source.value if result.source else None, result.log_message)
            _print_result(result)

    _print_summary(results)
    if any(r.status is Status.FAILED for r in results):
        raise typer.Exit(1)


def _print_result(result: ProcessResult) -> None:
    style = STATUS_STYLE[result.status]
    detail = result.message
    if result.status in (Status.OK, Status.REVIEW) and not detail:
        detail = f"{result.line_count} lines via {result.source.value if result.source else '?'}"
        if result.word_level:
            detail += " (word-level)"
    console.print(
        f"[{style}]{result.status.value:<7}[/{style}] "
        f"{escape(result.path.name)} [dim]{escape(detail)}[/dim]"
    )
    for warning in result.warnings:
        console.print(f"          [yellow]! {escape(warning)}[/yellow]")


def _print_summary(results: list[ProcessResult]) -> None:
    table = Table(title="Summary", show_header=True)
    table.add_column("Status")
    table.add_column("Count", justify="right")
    for status in Status:
        count = sum(1 for r in results if r.status is status)
        if count:
            table.add_row(f"[{STATUS_STYLE[status]}]{status.value}[/{STATUS_STYLE[status]}]", str(count))
    console.print(table)

    review = [r for r in results if r.status is Status.REVIEW]
    if review:
        console.print("\n[yellow]Worth checking by ear:[/yellow]")
        for result in review:
            console.print(f"  {result.path}")


@app.command()
def inspect(target: Path = typer.Argument(..., exists=True)) -> None:
    """Show the lyrics-related tags currently on a file."""
    meta = read_meta(target)
    label = escape(meta.query_label)
    console.print(f"[bold]{label}[/bold]  [dim]{meta.duration:.0f}s[/dim]" if meta.duration else label)

    frames = describe_lyric_frames(target)
    console.print(f"lyric frames: {', '.join(frames) if frames else '[dim]none[/dim]'}")

    text = read_embedded_lyrics(target)
    if not text:
        console.print("[dim]no embedded lyrics[/dim]")
        return
    console.print(f"synced: {'[green]yes[/green]' if is_synced_text(text) else '[yellow]no (plain text)[/yellow]'}")
    preview = text.splitlines()[:6]
    for line in preview:
        console.print(f"  [dim]{escape(line)}[/dim]")
    total = len(text.splitlines())
    if total > len(preview):
        console.print(f"  [dim]... {total - len(preview)} more lines[/dim]")


@app.command()
def strip(
    target: Path = typer.Argument(..., exists=True),
    recursive: bool = typer.Option(True, "--recursive/--no-recursive"),
    backup: bool = typer.Option(False, "--backup"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Remove all embedded lyrics, leaving the rest of the tags alone."""
    tracks = find_tracks(target, recursive=recursive)
    if not tracks:
        console.print("[red]No MP3 files found.[/red]")
        raise typer.Exit(1)

    if not yes and not typer.confirm(f"Remove lyrics from {len(tracks)} file(s)?"):
        raise typer.Abort()

    for path in tracks:
        removed = clear_lyrics(path, backup=backup)
        detail = escape(", ".join(removed)) if removed else "[dim]nothing to remove[/dim]"
        console.print(f"{escape(path.name)}: {detail}")


@app.command()
def export(
    target: Path = typer.Argument(..., exists=True, help="The MP3 whose .words.json to re-render."),
    enhanced: bool = typer.Option(True, "--enhanced/--plain"),
    lead_in: float = typer.Option(0.0, "--lead-in"),
    output: Path = typer.Option(None, "--output", "-o"),
) -> None:
    """Re-render an .lrc from saved word timings, without touching the audio.

    Word timings are the expensive part; keeping them means switching between
    line-level and word-level output later costs nothing.
    """
    words_path = target.with_suffix(".words.json")
    if not words_path.exists():
        console.print(f"[red]No word timings at {words_path.name}. Run sync first.[/red]")
        raise typer.Exit(1)

    payload = json.loads(words_path.read_text(encoding="utf-8"))
    lines = [
        LyricLine(
            text=entry["text"],
            start=entry.get("start"),
            end=entry.get("end"),
            words=[Word(text, start, end) for text, start, end in entry.get("words", [])],
        )
        for entry in payload["lines"]
    ]
    lyrics = Lyrics(lines=lines, source=Source(payload.get("source", "local-lrc")))

    destination = output or target.with_suffix(".lrc")
    destination.write_text(format_lrc(lyrics, enhanced=enhanced, lead_in=lead_in), encoding="utf-8")
    console.print(f"[green]Wrote[/green] {destination} ({len(lines)} lines)")


@app.command()
def gui(
    target: Path = typer.Argument(None, help="Folder to open at startup. Optional: you can pick one in the app."),
    port: int = typer.Option(8733, "--port"),
    host: str = typer.Option("127.0.0.1", "--host", help="Keep this on localhost unless you know why."),
    recursive: bool = typer.Option(True, "--recursive/--no-recursive"),
    enhanced: bool = typer.Option(False, "--enhanced", help="Save sidecars with word-level tags."),
    backup: bool = typer.Option(False, "--backup"),
    id3: int = typer.Option(3, "--id3"),
    open_browser: bool = typer.Option(True, "--open/--no-open"),
    cache: Path = typer.Option(DEFAULT_CACHE, "--cache"),
) -> None:
    """Open the graphical app in your browser.

    Browse a folder, sync lyrics, fix the timings by ear, inspect tags and
    review past runs -- everything the other commands do, in one window.
    """
    from .gui import serve
    from .gui.state import AppConfig

    tracks = find_tracks(target, recursive=recursive) if target else []
    if target and not tracks:
        console.print(f"[yellow]No MP3 files in {target}; you can pick another folder in the app.[/yellow]")

    serve(
        tracks,
        host=host,
        port=port,
        config=AppConfig(id3_version=id3, enhanced_sidecar=enhanced, backup=backup),
        cache_path=cache,
        open_browser=open_browser,
    )


@app.command()
def review(
    target: Path = typer.Argument(..., exists=True, help="An MP3 file or a folder of them."),
    port: int = typer.Option(8733, "--port"),
    only_review: bool = typer.Option(
        False, "--only-review", help="Only tracks whose last run was flagged for review."
    ),
    cache: Path = typer.Option(DEFAULT_CACHE, "--cache"),
) -> None:
    """Open the app on a folder, optionally limited to tracks needing review."""
    from .gui import serve
    from .gui.state import AppConfig

    tracks = find_tracks(target)
    if only_review:
        flagged = _flagged_for_review(cache)
        tracks = [p for p in tracks if str(p) in flagged]
        if not tracks:
            console.print("[green]Nothing is flagged for review.[/green]")
            raise typer.Exit(0)
    if not tracks:
        console.print("[red]No MP3 files found.[/red]")
        raise typer.Exit(1)

    serve(tracks, port=port, config=AppConfig(), cache_path=cache)


def _flagged_for_review(cache: Path) -> set[str]:
    """Paths whose most recent run ended in the review state."""
    if not cache.exists():
        return set()
    return {
        path
        for path, status in Store(cache).latest_statuses().items()
        if status == Status.REVIEW.value
    }


@app.command()
def history(
    limit: int = typer.Option(20, "--limit"),
    cache: Path = typer.Option(DEFAULT_CACHE, "--cache"),
) -> None:
    """Show the most recent processing runs."""
    if not cache.exists():
        console.print("[dim]No run history yet.[/dim]")
        return
    table = Table(show_header=True)
    for column in ("Status", "Source", "File", "Note"):
        table.add_column(column)
    for row in Store(cache).history(limit):
        table.add_row(
            row["status"],
            row["source"] or "-",
            escape(Path(row["path"]).name),
            escape(row["message"] or ""),
        )
    console.print(table)


@app.command()
def version() -> None:
    """Print the version."""
    console.print(f"lyricsync {__version__}")


if __name__ == "__main__":
    app()
