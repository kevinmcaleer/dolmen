"""HTTP API behind the build front end.

Every path that arrives from the browser is resolved against the site source and
rejected if it escapes it — the editor is a local tool, but it writes files, so
a traversal bug would be a real one.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from .. import frontmatter
from ..config import SPECIAL_DIRS
from ..permalinks import slugify

if TYPE_CHECKING:
    from starlette.requests import Request

    from ..server import DevSite

ASSETS = Path(__file__).parent / "assets"

#: Files the editor will open. Anything else is treated as a binary asset.
EDITABLE_SUFFIXES = {
    ".md", ".markdown", ".mkdn", ".mkd", ".html", ".htm", ".xml", ".json",
    ".yml", ".yaml", ".css", ".scss", ".js", ".txt", ".svg", ".py",
}

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".svg"}

#: Directories never shown in the tree.
HIDDEN = {".git", ".venv", "venv", "node_modules", "__pycache__", ".ruff_cache",
          ".pytest_cache", ".dolmen-cache"}

#: Largest image accepted by the uploader, before resizing.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

#: Images wider than this are resized on upload.
MAX_IMAGE_WIDTH = 2000


def admin_routes(site: DevSite) -> list[Route]:
    """The routes mounted at `/_dolmen`."""

    def resolve(raw: str) -> Path:
        """Resolve a browser-supplied path inside the site source, or raise."""
        candidate = (site.source / str(raw).lstrip("/")).resolve()
        if candidate != site.source and site.source not in candidate.parents:
            raise ValueError("path is outside the site")
        return candidate

    # -- pages ---------------------------------------------------------------

    async def index(request: Request) -> Response:
        return FileResponse(ASSETS / "index.html", headers={"Cache-Control": "no-store"})

    async def asset(request: Request) -> Response:
        name = request.path_params["path"]
        target = (ASSETS / name).resolve()
        if ASSETS not in target.parents or not target.is_file():
            return Response("Not found", status_code=404)
        return FileResponse(target, headers={"Cache-Control": "no-store"})

    # -- reading the site ----------------------------------------------------

    async def tree(request: Request) -> JSONResponse:
        return JSONResponse({"root": site.source.name, "entries": _walk(site.source)})

    async def meta(request: Request) -> JSONResponse:
        """What the editor needs to offer sensible choices in its forms."""
        from ..config import load_config

        config = load_config(site.source, site.overrides)
        layouts = sorted(
            p.stem for p in (site.source / "_layouts").glob("*.*") if p.is_file()
        )
        return JSONResponse(
            {
                "title": config.get("title", ""),
                "layouts": layouts,
                "collections": sorted(config.collections),
                "baseurl": config.baseurl,
            }
        )

    async def read_file(request: Request) -> JSONResponse:
        raw = request.query_params.get("path", "")
        try:
            path = resolve(raw)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if not path.is_file():
            return JSONResponse({"error": "no such file"}, status_code=404)
        if path.suffix.lower() not in EDITABLE_SUFFIXES:
            return JSONResponse({"error": "not a text file"}, status_code=415)

        text = path.read_text(encoding="utf-8")
        parsed = frontmatter.split(text, path)
        return JSONResponse(
            {
                "path": str(path.relative_to(site.source)),
                "text": text,
                "has_front_matter": parsed.has_front_matter,
                "metadata": _jsonable(parsed.metadata),
                "url": _preview_url(site, path),
            }
        )

    # -- writing -------------------------------------------------------------

    async def write_file(request: Request) -> JSONResponse:
        payload = await request.json()
        try:
            path = resolve(payload.get("path", ""))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if path.suffix.lower() not in EDITABLE_SUFFIXES:
            return JSONResponse({"error": "not a text file"}, status_code=415)

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload.get("text", ""), encoding="utf-8")
        # The watcher rebuilds too, but building here means the response can
        # carry the error straight back to the editor.
        site.build()
        site.reload.publish()
        return JSONResponse({"saved": True, "error": site.last_error})

    async def create(request: Request) -> JSONResponse:
        """Create a document from the editor's 'new page' form."""
        payload = await request.json()
        collection = str(payload.get("collection") or "pages")
        title = str(payload.get("title") or "Untitled").strip()
        layout = str(payload.get("layout") or "").strip()

        from ..permalinks import slugify

        slug = slugify(title) or "untitled"
        metadata: dict[str, Any] = {"title": title}
        if layout:
            metadata["layout"] = layout

        if collection == "posts":
            import datetime as dt

            today = dt.date.today()
            metadata["date"] = today.isoformat()
            relative = Path("_posts") / f"{today.isoformat()}-{slug}.md"
        elif collection == "pages":
            relative = Path(f"{slug}.md")
        else:
            relative = Path(f"_{collection}") / f"{slug}.md"

        path = site.source / relative
        if path.exists():
            return JSONResponse({"error": f"{relative} already exists"}, status_code=409)

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            frontmatter.dump(metadata, f"\nWrite {title} here.\n"), encoding="utf-8"
        )
        site.build()
        site.reload.publish()
        return JSONResponse({"path": str(relative), "url": _preview_url(site, path)})

    async def upload(request: Request) -> JSONResponse:
        """Accept a dragged-in image, resize it, and return the markdown to paste."""
        form = await request.form()
        upload_file = form.get("file")
        if upload_file is None or not hasattr(upload_file, "filename"):
            return JSONResponse({"error": "no file supplied"}, status_code=400)

        filename = Path(str(upload_file.filename)).name
        suffix = Path(filename).suffix.lower()
        if suffix not in IMAGE_SUFFIXES:
            return JSONResponse({"error": f"{suffix} is not an image"}, status_code=415)

        # Slugify the stored name. A dragged-in screenshot is typically called
        # something like "Screenshot 2026-08-28 at 10.25.54.png", and markdown
        # will not parse `![](...)` whose URL contains spaces — the image would
        # render as literal text. Spaces in asset URLs are worth avoiding anyway.
        stem = Path(filename).stem
        safe_name = f"{slugify(stem) or 'image'}{suffix}"

        destination_dir = site.source / "assets" / "img"
        destination_dir.mkdir(parents=True, exist_ok=True)
        target = _unique_path(destination_dir / safe_name)

        size = 0
        with target.open("wb") as handle:
            while chunk := await upload_file.read(1024 * 256):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    handle.close()
                    target.unlink(missing_ok=True)
                    return JSONResponse({"error": "file is too large"}, status_code=413)
                handle.write(chunk)

        width, height = _shrink(target)
        site.build()
        site.reload.publish()

        url = "/" + str(target.relative_to(site.source))
        return JSONResponse(
            {
                "url": url,
                # Alt text keeps the human-readable original; `]` and `[` would
                # otherwise close the alt span early.
                "markdown": f"![{stem.replace('[', '').replace(']', '')}]({url})",
                "width": width,
                "height": height,
            }
        )

    async def problems(request: Request) -> JSONResponse:
        """Everything wrong with the site, for the problems panel."""
        return JSONResponse(site.problems())

    async def rebuild(request: Request) -> JSONResponse:
        result = site.build()
        site.reload.publish()
        return JSONResponse(
            {
                "ok": site.last_error is None,
                "error": site.last_error,
                "documents": result.documents if result else 0,
                "duration": round(result.duration, 3) if result else 0,
                "warnings": result.warnings if result else [],
            }
        )

    async def delete_file(request: Request) -> JSONResponse:
        payload = await request.json()
        try:
            path = resolve(payload.get("path", ""))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if not path.is_file():
            return JSONResponse({"error": "no such file"}, status_code=404)
        path.unlink()
        site.build()
        site.reload.publish()
        return JSONResponse({"deleted": True})

    return [
        Route("/", index),
        Route("/api/tree", tree),
        Route("/api/meta", meta),
        Route("/api/file", read_file),
        Route("/api/file", write_file, methods=["POST"]),
        Route("/api/file", delete_file, methods=["DELETE"]),
        Route("/api/new", create, methods=["POST"]),
        Route("/api/upload", upload, methods=["POST"]),
        Route("/api/build", rebuild, methods=["POST"]),
        Route("/api/problems", problems),
        Route("/assets/{path:path}", asset),
    ]


# -- helpers ----------------------------------------------------------------


def _walk(root: Path) -> list[dict[str, Any]]:
    """A flat, sorted listing of the source tree, build inputs included."""
    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        parts = path.relative_to(root).parts
        if any(part in HIDDEN for part in parts):
            continue
        if parts and parts[0] == "_site":
            continue
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        entries.append(
            {
                "path": str(path.relative_to(root)),
                "name": path.name,
                "dir": str(path.parent.relative_to(root)) if path.parent != root else "",
                "editable": suffix in EDITABLE_SUFFIXES,
                "image": suffix in IMAGE_SUFFIXES,
                "special": bool(parts) and parts[0] in SPECIAL_DIRS,
                "size": path.stat().st_size,
            }
        )
    return entries


def _preview_url(site: DevSite, path: Path) -> str | None:
    """The built URL for a source file, so the preview can jump to it."""
    from ..builder import Builder

    try:
        builder = Builder.from_source(site.source, overrides=site.overrides)
        builder._load_plugins()
        builder._discover()
        builder._apply_defaults()
        builder._assign_urls()
    except Exception:  # noqa: BLE001 - preview URL is best-effort
        return None

    for document in builder.site.documents:
        if document.source == path:
            return document.url
    return None


def _unique_path(path: Path) -> Path:
    """`cover.jpg`, then `cover-1.jpg`, … so an upload never clobbers a file."""
    if not path.exists():
        return path
    for index in range(1, 1000):
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise ValueError("could not find a free filename")


def _shrink(path: Path) -> tuple[int | None, int | None]:
    """Resize an oversized upload in place. SVGs and failures pass through."""
    if path.suffix.lower() == ".svg":
        return None, None
    try:
        from PIL import Image
    except ImportError:
        return None, None

    try:
        with Image.open(path) as image:
            width, height = image.size
            if width <= MAX_IMAGE_WIDTH:
                return width, height
            ratio = MAX_IMAGE_WIDTH / width
            resized = image.resize((MAX_IMAGE_WIDTH, round(height * ratio)))
            backup = path.with_suffix(path.suffix + ".orig")
            shutil.copy2(path, backup)
            resized.save(path)
            backup.unlink(missing_ok=True)
            return resized.size
    except Exception:  # noqa: BLE001 - an unreadable image is still uploaded
        return None, None


def _jsonable(value: Any) -> Any:
    """Front matter can hold dates, which JSON cannot."""
    import datetime as dt

    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    return value
