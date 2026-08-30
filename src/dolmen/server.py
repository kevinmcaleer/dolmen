"""The development server: serve `_site/`, rebuild on change, reload the browser.

Three moving parts:

* a **watcher** task that rebuilds when anything in the source changes (ignoring
  the output directory, or the build would retrigger itself forever);
* a **reload channel** — server-sent events rather than websockets, because SSE
  needs no extra dependency and reconnects on its own;
* a **file server** that injects the reload client into every HTML response, so
  no page in the site has to know the dev server exists.

The build front end is mounted alongside at `/_dolmen/`, and is only ever part
of this server — it is never written into the output. The published site is
static files and nothing else.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import mimetypes
import threading
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)
from starlette.routing import Mount, Route
from watchfiles import awatch

from .builder import Builder, BuildResult
from .config import load_config
from .exceptions import StaticError

#: Injected before </body> of every HTML page the dev server returns.
LIVE_RELOAD_SNIPPET = """
<script>
(() => {
  const KEY = "dolmen:scroll:" + location.pathname;

  // Put the reader back where they were. A rebuild that scrolls a long page to
  // the top is worse than no live reload at all.
  const saved = sessionStorage.getItem(KEY);
  if (saved !== null) {
    sessionStorage.removeItem(KEY);
    addEventListener("load", () => window.scrollTo(0, Number(saved)));
  }

  // A stylesheet change needs no reload — re-request it and the page repaints
  // with the scroll position, form state and open details all intact.
  const reloadStyles = () => {
    for (const link of document.querySelectorAll('link[rel="stylesheet"]')) {
      const url = new URL(link.href, location.href);
      url.searchParams.set("_dolmen", Date.now());
      link.href = url.href;
    }
  };

  let current = null;
  const source = new EventSource("/_dolmen/events");
  source.addEventListener("build", (event) => {
    let payload;
    try { payload = JSON.parse(event.data); } catch { payload = { version: event.data }; }
    if (current !== null && payload.version !== current) {
      if (payload.cssOnly) {
        reloadStyles();
      } else {
        sessionStorage.setItem(KEY, String(window.scrollY));
        location.reload();
      }
    }
    current = payload.version;
  });
})();
</script>
"""


#: Extensions that only affect presentation, so the browser can re-request the
#: stylesheet instead of reloading and losing the reader's place.
STYLE_SUFFIXES = (".css", ".scss", ".sass")


@dataclass
class ReloadChannel:
    """Fan-out of build notifications to every connected browser."""

    version: int = 0
    _subscribers: set[asyncio.Queue[dict[str, Any]]] = field(default_factory=set)

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers.add(queue)
        queue.put_nowait({"version": self.version, "cssOnly": False})
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    def publish(self, *, css_only: bool = False) -> None:
        self.version += 1
        message = {"version": self.version, "cssOnly": css_only}
        for queue in self._subscribers:
            queue.put_nowait(message)


class DevSite:
    """Owns the config, the current build, and rebuilding on demand."""

    def __init__(self, source: Path, overrides: dict[str, Any] | None = None) -> None:
        self.source = Path(source).resolve()
        self.overrides = overrides or {}
        self.reload = ReloadChannel()
        self.last_result: BuildResult | None = None
        self.last_error: str | None = None
        #: The Site from the last successful build, kept so the problems panel
        #: can validate without paying for another build.
        self.last_site: Any = None
        self.last_index: Any = None
        self._lock = threading.Lock()

    @property
    def destination(self) -> Path:
        return load_config(self.source, self.overrides).destination

    def render_preview(self, path: Path, text: str) -> tuple[str, str | None]:
        """Render `text` as if it were the contents of `path`.

        Runs the real pipeline — front matter, Liquid, markdown, the layout
        chain — against the last build's site model, so the page looks exactly
        as it will once saved. Nothing is written; the site model is restored
        afterwards, or a preview would leak into the next build.
        """
        from . import frontmatter
        from .config import load_config
        from .markdown import MarkdownRenderer
        from .templating import Templating

        if self.last_site is None:
            return "", "nothing has been built yet"

        # Documents hold resolved paths, and on macOS /var and /private/var are
        # the same directory by two names.
        path = Path(path).resolve()
        document = next(
            (d for d in self.last_site.documents if d.source.resolve() == path), None
        )
        if document is None:
            return "", None if not text else "that file is not a document in this site"

        config = load_config(self.source, self.overrides)
        parsed = frontmatter.split(text, path)

        original = (document.metadata, document.body, document.content)
        try:
            document.metadata = {**original[0], **parsed.metadata}
            document.body = parsed.content

            markdown = MarkdownRenderer(
                link_resolver=lambda target: _preview_resolver(self.last_site, target)
            )
            templating = Templating(
                self.source,
                markdown=markdown,
                baseurl=config.baseurl,
                url=config.url,
            )
            context = {
                "site": self.last_site.to_template_dict(),
                "page": document.to_template_dict(),
                "content": "",
            }
            body = templating.render_string(
                document.body, context, name=str(path)
            )
            if document.is_markdown:
                body = markdown.render(body)
            document.content = body
            context["page"] = document.to_template_dict()

            layout = document.layout
            html = (
                templating.render_layout(layout, body, context, name=str(path))
                if layout
                else body
            )
            return html, None
        except StaticError as exc:
            return "", str(exc)
        except Exception as exc:  # noqa: BLE001 - a preview must never kill the server
            return "", f"{type(exc).__name__}: {exc}"
        finally:
            document.metadata, document.body, document.content = original

    def problems(self) -> dict[str, Any]:
        """Validate the last build, for the front end's problems panel."""
        from .config import load_config
        from .validate import Report, validate

        if self.last_site is None:
            return Report().to_dict()
        config = load_config(self.source, self.overrides)
        warnings = self.last_result.warnings if self.last_result else []
        return validate(
            self.last_site,
            config,
            build_warnings=warnings,
            link_index=self.last_index,
        ).to_dict()

    def build(self) -> BuildResult | None:
        """Rebuild, capturing errors so a bad save never kills the server."""
        with self._lock:
            try:
                builder = Builder.from_source(self.source, overrides=self.overrides)
                self.last_result = builder.build()
                self.last_site = builder.site
                self.last_index = builder.link_index
                self.last_error = None
            except StaticError as exc:
                self.last_error = str(exc)
                return None
            except Exception as exc:  # noqa: BLE001 - a crash must not stop the server
                self.last_error = f"{type(exc).__name__}: {exc}"
                return None
        return self.last_result


def _preview_resolver(site: Any, target: str) -> str | None:
    """Resolve a wiki link during preview, without rebuilding the index."""
    from .links import anchor_for, split_target

    page, heading = split_target(target)
    document = site.find_by_title(page)
    if document is None:
        return None
    return f"{document.url}#{anchor_for(heading)}" if heading else document.url


def create_app(
    site: DevSite,
    *,
    live_reload: bool = True,
    admin: bool = True,
    on_ready: Callable[[], None] | None = None,
) -> Starlette:
    """Build the Starlette app serving the site, the reload channel and the admin."""

    async def events(request: Request) -> StreamingResponse:
        queue = site.reload.subscribe()

        async def stream() -> Any:
            try:
                while True:
                    message = await queue.get()
                    yield f"event: build\ndata: {json.dumps(message)}\n\n"
            except asyncio.CancelledError:  # client went away
                raise
            finally:
                site.reload.unsubscribe(queue)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    async def status(request: Request) -> JSONResponse:
        result = site.last_result
        return JSONResponse(
            {
                "ok": site.last_error is None,
                "error": site.last_error,
                "documents": result.documents if result else 0,
                "static_files": result.static_files if result else 0,
                "duration": round(result.duration, 3) if result else 0,
                "warnings": result.warnings if result else [],
                "version": site.reload.version,
            }
        )

    async def serve_file(request: Request) -> Response:
        return _serve_path(site, request.path_params.get("path", ""), live_reload=live_reload)

    routes = [
        Route("/_dolmen/events", events),
        Route("/_dolmen/api/status", status),
    ]

    if admin:
        from .admin.app import admin_routes

        routes.append(Mount("/_dolmen", routes=admin_routes(site)))

    routes.append(Route("/{path:path}", serve_file))

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> Any:
        stop = asyncio.Event()
        watcher = asyncio.create_task(_watch(site, stop)) if live_reload else None
        if on_ready is not None:
            on_ready()
        try:
            yield
        finally:
            stop.set()
            if watcher is not None:
                watcher.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await watcher

    return Starlette(routes=routes, lifespan=lifespan)


def _serve_path(site: DevSite, path: str, *, live_reload: bool) -> Response:
    """Resolve a URL to a file in the output directory."""
    root = site.destination

    if site.last_error is not None:
        return HTMLResponse(_error_page(site.last_error), status_code=500)

    candidate = (root / path.lstrip("/")).resolve()
    # Never serve outside the output directory, whatever the URL contains.
    if not str(candidate).startswith(str(root)):
        return Response("Forbidden", status_code=403)

    if candidate.is_dir():
        candidate = candidate / "index.html"
    if not candidate.is_file():
        # A permalink without a trailing slash still finds its index.html.
        alternative = candidate.with_suffix(".html")
        if alternative.is_file():
            candidate = alternative
        else:
            return HTMLResponse(_not_found_page(path, root), status_code=404)

    media_type, _ = mimetypes.guess_type(candidate.name)
    if live_reload and (media_type == "text/html" or candidate.suffix == ".html"):
        html = candidate.read_text(encoding="utf-8")
        html = _inject(html, LIVE_RELOAD_SNIPPET)
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    return FileResponse(candidate, headers={"Cache-Control": "no-store"})


def _inject(html: str, snippet: str) -> str:
    lowered = html.lower()
    index = lowered.rfind("</body>")
    if index == -1:
        return html + snippet
    return html[:index] + snippet + html[index:]


def _error_page(message: str) -> str:
    from html import escape

    return f"""<!DOCTYPE html><meta charset="utf-8"><title>Build failed</title>
<style>
 body {{ font: 15px/1.6 ui-monospace, SFMono-Regular, Menlo, monospace;
        background:#1b1d23; color:#e7e9ee; margin:0; padding:3rem; }}
 h1 {{ font-size:1rem; color:#ff7b72; margin:0 0 1rem; letter-spacing:.04em;
       text-transform:uppercase; }}
 pre {{ background:#0d1117; padding:1.25rem; border-radius:.5rem;
        border-left:3px solid #ff7b72; overflow-x:auto; white-space:pre-wrap; }}
 p {{ color:#98a1b0; }}
</style>
<h1>Build failed</h1><pre>{escape(message)}</pre>
<p>Fix the error and save — the page reloads itself.</p>
{LIVE_RELOAD_SNIPPET}"""


def _not_found_page(path: str, root: Path) -> str:
    from html import escape

    custom = root / "404.html"
    if custom.is_file():
        return _inject(custom.read_text(encoding="utf-8"), LIVE_RELOAD_SNIPPET)
    return f"""<!DOCTYPE html><meta charset="utf-8"><title>404</title>
<style>body{{font:15px/1.6 system-ui,sans-serif;margin:0;padding:3rem;color:#16181d}}
code{{background:#eef0f4;padding:.15em .4em;border-radius:.25rem}}</style>
<h1>404</h1><p>Nothing built at <code>/{escape(path)}</code>.</p>
<p><a href="/_dolmen/">Open the build front end</a></p>
{LIVE_RELOAD_SNIPPET}"""


async def _watch(site: DevSite, stop: asyncio.Event) -> None:
    """Rebuild whenever the source changes, ignoring the output directory."""
    destination = str(site.destination)

    def ignore(change: Any, path: str) -> bool:
        return (
            path.startswith(destination)
            or "/.git/" in path
            or "/__pycache__/" in path
            or path.endswith(("~", ".swp", ".tmp"))
        )

    async for changes in awatch(
        site.source, watch_filter=lambda c, p: not ignore(c, p), stop_event=stop
    ):
        changed = [path for _, path in changes]
        css_only = bool(changed) and all(
            path.lower().endswith(STYLE_SUFFIXES) for path in changed
        )
        result = site.build()
        if result is not None:
            print(
                f"  rebuilt {result.documents} document(s) in {result.duration:.2f}s",
                flush=True,
            )
        else:
            print(f"  build failed: {site.last_error}", flush=True)
        site.reload.publish(css_only=css_only and site.last_error is None)


def serve(
    *,
    source: Path,
    host: str = "127.0.0.1",
    port: int = 4000,
    overrides: dict[str, Any] | None = None,
    live_reload: bool = True,
    admin: bool = True,
    open_browser: bool = False,
) -> None:
    """Build, then serve until interrupted."""
    site = DevSite(Path(source), overrides)
    result = site.build()

    if result is None:
        print(f"build failed: {site.last_error}", flush=True)
        print("serving anyway — fix the error and it will rebuild", flush=True)
    else:
        print(f"built {result.documents} document(s) in {result.duration:.2f}s", flush=True)
        for warning in result.warnings:
            print(f"  warning: {warning}", flush=True)

    def announce() -> None:
        url = f"http://{host}:{port}/"
        print(f"serving {site.destination} at {url}", flush=True)
        if admin:
            print(f"build front end at {url}_dolmen/", flush=True)
        if open_browser:
            webbrowser.open(url)

    app = create_app(site, live_reload=live_reload, admin=admin, on_ready=announce)
    uvicorn.run(app, host=host, port=port, log_level="warning")
