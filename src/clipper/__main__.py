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
    captions: bool = typer.Option(False, "--captions", help="Also run Marlin visual captioning (slower, useful for visual-driven content)"),
    no_transcribe: bool = typer.Option(False, "--no-transcribe", help="Skip Whisper transcription"),
) -> None:
    """Build an index for VIDEO. Default = transcript-only.

    Pass --captions to also run Marlin visual captioning (helpful for
    silent screencasts, sports, music videos, etc.).
    """
    def _report(message, current, total):
        console.print(f"  [dim]{current}/{total}[/]  [cyan]{message}[/]")

    if not captions and no_transcribe:
        console.print("[red]nothing to index — drop --no-transcribe or pass --captions[/]")
        raise typer.Exit(1)

    console.print(f"[bold]indexing[/] {video}")
    t0 = time.time()
    result = pipeline.index_video(
        str(video), window=window, overlap=overlap, reencode=reencode,
        caption=captions, transcribe=not no_transcribe, progress=_report,
    )
    elapsed = time.time() - t0
    n_trans = len(result.get("transcript", {}).get("segments", [])) if result.get("transcript") else 0
    console.print(
        f"[green]done[/] in {elapsed:.1f}s — "
        f"{len(result['chunks'])} chunks, {len(result['events'])} events, "
        f"{n_trans} transcript segments"
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
def serve(
    host: str = typer.Option("0.0.0.0", help="Bind address (use 127.0.0.1 to keep local)"),
    port: int = typer.Option(8000, help="Port"),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code change (dev only)"),
) -> None:
    """Start the FastAPI server."""
    import uvicorn

    console.print(f"[bold]starting clipper server[/] on http://{host}:{port}")
    uvicorn.run(
        "clipper.server:app",
        host=host, port=port, reload=reload,
        log_level="info",
    )


@app.command()
def dev(
    api_port: int = typer.Option(8765, help="FastAPI port"),
    web_port: int = typer.Option(5173, help="Vite dev port"),
    no_open: bool = typer.Option(False, "--no-open", help="Don't auto-open the browser"),
) -> None:
    """Start API + Vite dev server together (Ctrl-C kills both)."""
    import os
    import shutil
    import signal
    import subprocess
    import sys

    web_dir = Path(__file__).resolve().parents[2] / "web"
    if not (web_dir / "node_modules").exists():
        console.print("[yellow]web/node_modules missing — running `npm install`[/]")
        npm = shutil.which("npm")
        if not npm:
            console.print("[red]npm not found on PATH[/]")
            raise typer.Exit(1)
        subprocess.run([npm, "install"], cwd=web_dir, check=True)

    npx = shutil.which("npx")
    if not npx:
        console.print("[red]npx not found on PATH[/]")
        raise typer.Exit(1)

    console.print(f"[bold]starting clipper dev[/]")
    console.print(f"  [blue]api[/] http://127.0.0.1:{api_port}")
    console.print(f"  [green]web[/] http://127.0.0.1:{web_port}  (proxies /api + /media to :{api_port})")

    vite_args = [npx, "vite", "--port", str(web_port), "--strictPort"]
    if not no_open:
        vite_args.append("--open")
    vite_env = {**os.environ, "VITE_API_TARGET": f"http://127.0.0.1:{api_port}"}
    vite = subprocess.Popen(vite_args, cwd=web_dir, env=vite_env)

    def _shutdown(*_: object) -> None:
        if vite.poll() is None:
            vite.terminate()
        sys.exit(0)
    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        import uvicorn
        uvicorn.run(
            "clipper.server:app",
            host="127.0.0.1", port=api_port,
            log_level="info",
        )
    finally:
        if vite.poll() is None:
            vite.terminate()
            try: vite.wait(timeout=5)
            except subprocess.TimeoutExpired: vite.kill()


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
