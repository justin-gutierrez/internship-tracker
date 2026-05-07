"""CLI: scan, refresh, export."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import click
from dotenv import load_dotenv
from googleapiclient.errors import HttpError
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn

from tracker import db as dbmod
from tracker.classifier import process_email
from tracker.db import META_HISTORY_KEY
from tracker.exporter import export_xlsx
from tracker.gmail_client import (
    build_service,
    get_credentials,
    get_message_full,
    get_profile_history_id,
    list_history_message_ids,
    list_message_ids,
)

console = Console()


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )


def _load_paths(ctx: click.Context, credentials: Path | None, token: Path | None) -> tuple[Path, Path]:
    root = Path.cwd()
    cred = credentials or Path(os.environ.get("CREDENTIALS_PATH", root / "credentials.json"))
    tok = token or Path(os.environ.get("TOKEN_PATH", root / "token.json"))
    return cred, tok


@click.group()
@click.option("-v", "--verbose", is_flag=True)
@click.pass_context
def main(ctx: click.Context, verbose: bool) -> None:
    """Internship Application Tracker — Gmail + Ollama -> SQLite + Excel."""
    load_dotenv()
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    _setup_logging(verbose)


@main.command("scan")
@click.option("--max-emails", type=int, default=None, help="Max messages to fetch (default from env)")
@click.option("--lookback-days", type=int, default=None, help="Gmail query newer_than:Xd (default from env)")
@click.option("--output", type=click.Path(path_type=Path), default=None)
@click.option("--db", "db_path", type=click.Path(path_type=Path), default=None)
@click.option("--credentials", type=click.Path(path_type=Path), default=None)
@click.option("--token", type=click.Path(path_type=Path), default=None)
@click.option("--dry-run", is_flag=True, help="Gmail + prefilter only; no Ollama calls")
@click.pass_context
def scan_command(
    ctx: click.Context,
    max_emails: int | None,
    lookback_days: int | None,
    output: Path | None,
    db_path: Path | None,
    credentials: Path | None,
    token: Path | None,
    dry_run: bool,
) -> None:
    """Full scan of recent Gmail messages."""
    cred_path, token_path = _load_paths(ctx, credentials, token)
    max_n = max_emails if max_emails is not None else int(os.environ.get("MAX_EMAILS", "1000"))
    lookback = lookback_days if lookback_days is not None else int(os.environ.get("LOOKBACK_DAYS", "365"))
    out = output or Path(os.environ.get("OUTPUT_PATH", "data/applications.xlsx"))
    dbp = db_path or Path(os.environ.get("DB_PATH", "data/tracker.db"))
    ollama_url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
    ollama_model = os.environ.get("OLLAMA_MODEL", "llama3.1:8b")

    creds = get_credentials(cred_path, token_path)
    service = build_service(creds)

    conn = dbmod.connect(dbp)
    dbmod.init_schema(conn)

    q = f"newer_than:{lookback}d" if lookback > 0 else None
    ids = list(list_message_ids(service, q, max_n))
    console.print(f"[bold]Scan[/bold]: {len(ids)} message ids (query={q!r}, max={max_n})")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Processing", total=len(ids))
        for mid in ids:
            try:
                email = get_message_full(service, mid)
            except HttpError as e:
                logging.warning("Skip message %s: %s", mid, e)
                progress.advance(task)
                continue
            process_email(
                conn,
                email,
                ollama_url=ollama_url,
                ollama_model=ollama_model,
                dry_run=dry_run,
                skip_if_exists=True,
            )
            progress.advance(task)

    try:
        hid = get_profile_history_id(service)
        dbmod.set_meta(conn, META_HISTORY_KEY, hid)
        console.print(f"[green]Saved Gmail historyId[/green] {hid} for incremental refresh.")
    except Exception as e:
        logging.warning("Could not save history id: %s", e)

    export_xlsx(conn, out)
    console.print(f"[bold green]Wrote[/bold green] {out.resolve()}")


@main.command("refresh")
@click.option("--output", type=click.Path(path_type=Path), default=None)
@click.option("--db", "db_path", type=click.Path(path_type=Path), default=None)
@click.option("--credentials", type=click.Path(path_type=Path), default=None)
@click.option("--token", type=click.Path(path_type=Path), default=None)
@click.option("--dry-run", is_flag=True)
@click.pass_context
def refresh_command(
    ctx: click.Context,
    output: Path | None,
    db_path: Path | None,
    credentials: Path | None,
    token: Path | None,
    dry_run: bool,
) -> None:
    """Incremental update using Gmail history API."""
    cred_path, token_path = _load_paths(ctx, credentials, token)
    out = output or Path(os.environ.get("OUTPUT_PATH", "data/applications.xlsx"))
    dbp = db_path or Path(os.environ.get("DB_PATH", "data/tracker.db"))
    ollama_url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
    ollama_model = os.environ.get("OLLAMA_MODEL", "llama3.1:8b")

    conn = dbmod.connect(dbp)
    dbmod.init_schema(conn)
    start_id = dbmod.get_meta(conn, META_HISTORY_KEY)
    if not start_id:
        console.print("[red]No saved history id. Run `tracker scan` first.[/red]")
        sys.exit(1)

    creds = get_credentials(cred_path, token_path)
    service = build_service(creds)

    try:
        new_ids = list_history_message_ids(service, start_id)
    except HttpError:
        console.print(
            "[yellow]History sync failed (history id may be expired). Run `tracker scan` for a full sync.[/yellow]"
        )
        sys.exit(2)

    console.print(f"[bold]Refresh[/bold]: {len(new_ids)} new message(s) since history {start_id}")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Processing", total=len(new_ids))
        for mid in new_ids:
            try:
                email = get_message_full(service, mid)
            except HttpError as e:
                logging.warning("Skip message %s: %s", mid, e)
                progress.advance(task)
                continue
            process_email(
                conn,
                email,
                ollama_url=ollama_url,
                ollama_model=ollama_model,
                dry_run=dry_run,
                skip_if_exists=True,
            )
            progress.advance(task)

    try:
        hid = get_profile_history_id(service)
        dbmod.set_meta(conn, META_HISTORY_KEY, hid)
        console.print(f"[green]Updated Gmail historyId[/green] to {hid}.")
    except Exception as e:
        logging.warning("Could not update history id: %s", e)

    export_xlsx(conn, out)
    console.print(f"[bold green]Wrote[/bold green] {out.resolve()}")


@main.command("export")
@click.option("--output", type=click.Path(path_type=Path), default=None)
@click.option("--db", "db_path", type=click.Path(path_type=Path), default=None)
@click.pass_context
def export_command(ctx: click.Context, output: Path | None, db_path: Path | None) -> None:
    """Write Excel from the local database without calling Gmail or Ollama."""
    out = output or Path(os.environ.get("OUTPUT_PATH", "data/applications.xlsx"))
    dbp = db_path or Path(os.environ.get("DB_PATH", "data/tracker.db"))
    conn = dbmod.connect(dbp)
    dbmod.init_schema(conn)
    export_xlsx(conn, out)
    console.print(f"[bold green]Wrote[/bold green] {out.resolve()}")


if __name__ == "__main__":
    main()
