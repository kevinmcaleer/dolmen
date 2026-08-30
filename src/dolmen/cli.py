"""The `dolmen` command."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import click

from . import __version__
from .builder import Builder
from .exceptions import StaticError
from .scaffold import create_site


def _fail(exc: Exception) -> None:
    click.secho(f"error: {exc}", fg="red", err=True)
    sys.exit(1)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="dolmen")
def main() -> None:
    """dolmen — a Jekyll-compatible static site generator written in Python."""


@main.command()
@click.option(
    "--source", "-s", default=".", type=click.Path(file_okay=False, path_type=Path),
    help="Site source directory.",
)
@click.option(
    "--destination", "-d", type=click.Path(file_okay=False, path_type=Path),
    help="Where to write the site (default: the config's destination, or _site).",
)
@click.option("--drafts", is_flag=True, help="Include documents in _drafts.")
@click.option("--strict", is_flag=True, help="Treat warnings as errors.")
@click.option("--quiet", "-q", is_flag=True, help="Only print errors.")
def build(
    source: Path, destination: Path | None, drafts: bool, strict: bool, quiet: bool
) -> None:
    """Build the site once."""
    overrides = {}
    if destination is not None:
        overrides["destination"] = str(destination)
    if drafts:
        overrides["show_drafts"] = True

    try:
        builder = Builder.from_source(source, overrides=overrides, strict=strict)
        result = builder.build()
    except StaticError as exc:
        _fail(exc)
        return

    for warning in result.warnings:
        click.secho(f"warning: {warning}", fg="yellow", err=True)

    if not quiet:
        click.secho(
            f"built {result.documents} document(s) and copied {result.static_files} file(s) "
            f"to {result.output_dir} in {result.duration:.2f}s",
            fg="green",
        )


@main.command()
@click.option(
    "--source", "-s", default=".", type=click.Path(file_okay=False, path_type=Path),
    help="Site source directory.",
)
@click.option("--host", default="127.0.0.1", help="Address to bind.")
@click.option("--port", "-p", default=4000, type=int, help="Port to bind.")
@click.option("--drafts", is_flag=True, help="Include documents in _drafts.")
@click.option("--no-reload", is_flag=True, help="Disable rebuild-on-change and live reload.")
@click.option("--admin/--no-admin", default=True, help="Serve the build front end at /_dolmen/.")
@click.option("--open", "open_browser", is_flag=True, help="Open a browser once serving.")
def serve(
    source: Path,
    host: str,
    port: int,
    drafts: bool,
    no_reload: bool,
    admin: bool,
    open_browser: bool,
) -> None:
    """Build, serve, and rebuild on change."""
    from .server import serve as run_server  # imported late: pulls in uvicorn

    overrides = {"show_drafts": True} if drafts else {}
    try:
        run_server(
            source=source,
            host=host,
            port=port,
            overrides=overrides,
            live_reload=not no_reload,
            admin=admin,
            open_browser=open_browser,
        )
    except StaticError as exc:
        _fail(exc)


@main.command()
@click.argument("path", type=click.Path(path_type=Path))
@click.option("--title", default=None, help="Site title (default: the directory name).")
@click.option("--force", is_flag=True, help="Write into a non-empty directory.")
def new(path: Path, title: str | None, force: bool) -> None:
    """Create a new site at PATH."""
    try:
        create_site(path, title=title, force=force)
    except StaticError as exc:
        _fail(exc)
        return
    click.secho(f"created a new site in {path}", fg="green")
    click.echo(f"  cd {path} && dolmen serve --open")


@main.command()
@click.option(
    "--source", "-s", default=".", type=click.Path(file_okay=False, path_type=Path),
    help="Site source directory.",
)
def clean(source: Path) -> None:
    """Delete the build output directory."""
    from .config import load_config

    try:
        destination = load_config(source).destination
    except StaticError as exc:
        _fail(exc)
        return
    if destination.exists():
        shutil.rmtree(destination)
        click.secho(f"removed {destination}", fg="green")
    else:
        click.echo(f"nothing to remove at {destination}")


@main.command("doctor")
@click.option(
    "--source", "-s", default=".", type=click.Path(file_okay=False, path_type=Path),
    help="Site source directory.",
)
@click.option(
    "--strict", is_flag=True, help="Exit non-zero if there are warnings, not just errors."
)
def doctor(source: Path, strict: bool) -> None:
    """Build the site and report every problem found.

    The same checks the front end's problems panel runs: broken links and
    images, unresolved wiki links, missing layouts and includes, absent front
    matter, and code fences tagged with a language nothing can highlight.
    """
    from .validate import validate

    try:
        builder = Builder.from_source(source)
        result = builder.build()
    except StaticError as exc:
        _fail(exc)
        return

    report = validate(
        builder.site,
        builder.config,
        build_warnings=result.warnings,
        link_index=builder.link_index,
    )

    if not report.total:
        click.secho("no problems found", fg="green")
        return

    colours = {"error": "red", "warning": "yellow", "info": "cyan"}
    for problem in report.sorted():
        location = problem.file or ""
        if problem.line:
            location = f"{location}:{problem.line}"
        click.secho(f"{problem.severity}: {problem.title}", fg=colours[problem.severity], bold=True)
        if location:
            click.echo(f"  {location}")
        click.echo(f"  {problem.message}")
        click.secho(f"  why: {problem.why}", dim=True)
        click.echo()

    summary = ", ".join(
        f"{count} {label}"
        for count, label in (
            (report.errors, "error(s)"),
            (report.warnings, "warning(s)"),
            (report.infos, "info"),
        )
        if count
    )
    click.secho(summary, fg="yellow")

    if report.errors or (strict and report.warnings):
        sys.exit(1)


if __name__ == "__main__":
    main()
