"""clipper CLI — `uv run clip <command>`.

Commands:
    index <video>                       Build a caption index for a video.
    search <video> <query> [--fanout]   Search an indexed video.
    cut <video> <start> <end> -o <out>  Cut a clip via ffmpeg.
    show <video>                        Print the cached index summary.
"""

from __future__ import annotations

# Import the package first to bootstrap LD_LIBRARY_PATH + Marlin env vars.
import clipper  # noqa: F401

import time
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from clipper import clipper as clipper_mod
from clipper import pipeline, search

app = typer.Typer(no_args_is_help=True, add_completion=False, pretty_exceptions_show_locals=False)
console = Console()


def _fmt(secs: float) -> str:
    h, rem = divmod(int(secs), 3600)
    m, s = divmod(rem, 60)
    frac = secs - int(secs)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}.{int(frac * 10):d}"
    return f"{m:d}:{s:02d}.{int(frac * 10):d}"


@app.command()
def index(
    video: Path = typer.Argument(..., exists=True, dir_okay=False, resolve_path=True),
    window: float = typer.Option(90.0, help="Chunk window (seconds)"),
    overlap: float = typer.Option(8.0, help="Chunk overlap (seconds)"),
    reencode: bool = typer.Option(False, "--reencode", help="Frame-accurate chunk extraction (slower)"),
) -> None:
    """Build a caption index for VIDEO, write <video>.index.json."""
    def _report(chunk, i, total):
        console.print(
            f"  [dim]chunk {i + 1}/{total}[/]  "
            f"[cyan]{_fmt(chunk.start)} → {_fmt(chunk.end)}[/]"
        )

    console.print(f"[bold]indexing[/] {video}")
    t0 = time.time()
    result = pipeline.index_video(
        str(video), window=window, overlap=overlap,
        reencode=reencode, progress=_report,
    )
    elapsed = time.time() - t0
    console.print(
        f"[green]done[/] in {elapsed:.1f}s — "
        f"{len(result['chunks'])} chunks, {len(result['events'])} events"
    )
    console.print(f"  wrote {pipeline.index_path_for(video)}")


@app.command(name="search")
def search_cmd(
    video: Path = typer.Argument(..., exists=True, dir_okay=False, resolve_path=True),
    query: str = typer.Argument(...),
    fanout: bool = typer.Option(False, "--fanout", help="Run find() on every chunk (slower, more precise)"),
    limit: int = typer.Option(10, help="Max results"),
) -> None:
    """Search an already-indexed video for QUERY."""
    idx = pipeline.load_index(video)
    if idx is None:
        console.print(f"[red]no index for {video}[/] — run `clip index` first")
        raise typer.Exit(1)

    t0 = time.time()
    if fanout:
        hits = search.find_fanout(idx, str(video), query, limit=limit)
        mode = "find fanout"
    else:
        hits = search.text_search(idx, query, limit=limit)
        mode = "text search"
    elapsed = time.time() - t0

    if not hits:
        console.print(f"[yellow]{mode}: no matches in {elapsed:.1f}s[/]")
        return

    table = Table(title=f"{mode} for {query!r}  ({len(hits)} hits in {elapsed:.1f}s)")
    table.add_column("#", justify="right")
    table.add_column("start", justify="right")
    table.add_column("end", justify="right")
    table.add_column("dur", justify="right")
    table.add_column("score", justify="right")
    table.add_column("description")
    for i, h in enumerate(hits, 1):
        table.add_row(
            str(i), _fmt(h.start), _fmt(h.end),
            f"{h.end - h.start:.1f}s", f"{h.score:.1f}",
            h.description,
        )
    console.print(table)


@app.command()
def cut(
    video: Path = typer.Argument(..., exists=True, dir_okay=False, resolve_path=True),
    start: float = typer.Argument(..., help="Start time in seconds"),
    end: float = typer.Argument(..., help="End time in seconds"),
    output: Path = typer.Option(..., "-o", "--output"),
    reencode: bool = typer.Option(False, "--reencode", help="Frame-accurate re-encode (slower)"),
) -> None:
    """Cut a clip from VIDEO between START and END seconds."""
    out = clipper_mod.cut_clip(
        video, start, end, output, stream_copy=not reencode,
    )
    console.print(f"[green]wrote[/] {out} ({out.stat().st_size:,} bytes)")


@app.command()
def show(
    video: Path = typer.Argument(..., exists=True, dir_okay=False, resolve_path=True),
    events_limit: int = typer.Option(20, help="Show at most N events"),
) -> None:
    """Print the cached index for VIDEO."""
    idx = pipeline.load_index(video)
    if idx is None:
        console.print(f"[red]no index for {video}[/]")
        raise typer.Exit(1)
    console.print(f"[bold]{idx['video']}[/]  duration={_fmt(idx['duration'])}  "
                  f"chunks={len(idx['chunks'])}  events={len(idx['events'])}")
    table = Table()
    table.add_column("start", justify="right")
    table.add_column("end", justify="right")
    table.add_column("chunk", justify="right")
    table.add_column("description")
    for ev in idx["events"][:events_limit]:
        table.add_row(
            _fmt(ev["start"]), _fmt(ev["end"]),
            str(ev["chunk_index"]), ev["description"],
        )
    console.print(table)
    if len(idx["events"]) > events_limit:
        console.print(f"  [dim]... and {len(idx['events']) - events_limit} more[/]")


if __name__ == "__main__":
    app()
