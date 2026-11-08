"""MCP oturumuna ait, araştırma çalıştırmayan dar kapsamlı yerel HTTP köprüsü."""

import asyncio
import json
import os
import secrets
import socket
import stat
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.responses import JSONResponse

from radar.depo import Depo
from radar.platform_api import router_olustur


START_TIMEOUT = 15.0
STOP_TIMEOUT = 5.0
OWNER_FILE = ".devtrellis-mcp-data.json"
OWNER = {"application": "devtrellis-mcp", "schema": 1}
REVIEW_FILES = frozenset({"/static/contribution-review.html", "/static/contribution-review.js",
                         "/static/contribution-review.css", "/static/ui-text.js",
                         "/static/contribution-submissions.html", "/static/contribution-submissions.js",
                         "/static/confirm-dialog.js", "/static/confirm-dialog.css"})
LOG_CONFIG = {
    "version": 1, "disable_existing_loggers": False,
    "handlers": {"mcp_stderr": {"class": "logging.StreamHandler", "stream": "ext://sys.stderr"}},
    "loggers": {"uvicorn": {"handlers": ["mcp_stderr"], "level": "WARNING", "propagate": False},
                "uvicorn.error": {"handlers": [], "level": "WARNING", "propagate": True},
                "uvicorn.access": {"handlers": [], "level": "WARNING", "propagate": False}},
}


def _linked(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0)
                                            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _data_directory(value):
    directory = Path(os.path.abspath(Path(value).expanduser()))
    # Explicit data-dir must never silently attach to an existing personal database.
    for candidate in (directory, *directory.parents):
        if candidate.exists() and _linked(candidate):
            raise ValueError("MCP data directory must not use a symbolic link or junction.")
    directory.mkdir(parents=True, exist_ok=True)
    marker = directory / OWNER_FILE
    if not marker.exists():
        if any(directory.iterdir()):
            if not marker.exists():
                raise ValueError("Choose an empty MCP data directory; existing personal data is not reused.")
        try:
            with marker.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(OWNER, sort_keys=True) + "\n")
        except FileExistsError:
            pass  # Another MCP process just initialized this same dedicated directory.
    if _linked(marker) or marker.stat().st_size > 256:
        raise ValueError("Invalid MCP data-directory ownership record.")
    for attempt in range(11):
        try:
            content = marker.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ValueError("Invalid MCP data-directory ownership record.") from exc
        if content or attempt == 10:
            break
        time.sleep(0.01)  # An exclusively created marker may still be getting its first write.
    if content != json.dumps(OWNER, sort_keys=True) + "\n":
        raise ValueError("Invalid MCP data-directory ownership record.")
    if any(_linked(child) for child in directory.iterdir()):
        raise ValueError("MCP data files must not be symbolic links or junctions.")
    return directory


def _application(directory, port):
    """Build only the community API; no research services, account probes or schedulers."""
    nonce = secrets.token_urlsafe(32)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.depo = Depo(directory / "research.sqlite3", sources=[])
    app.include_router(router_olustur(app.state.depo, nonce, workflow=None))
    app.mount("/static", StaticFiles(directory=Path(__file__).resolve().parent / "static"))

    @app.get("/api/health")
    def health():
        return {"product": "DevTrellis", "status": "ok", "mode": "mcp",
                "automatic_research": False, "manual_ai": False}

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        origin = request.headers.get("origin")
        error = None
        if (request.headers.get("host", "").lower() not in allowed_hosts
                or (origin and origin not in {"http://" + host for host in allowed_hosts})
                or request.headers.get("sec-fetch-site") == "cross-site"):
            error = (403, "Only this local origin can access the MCP bridge.")
        elif request.method not in {"GET", "HEAD"}:
            if not secrets.compare_digest(request.headers.get("x-radar-csrf", "").encode(), nonce.encode()):
                error = (403, "Refresh the local review before continuing.")
            elif request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                error = (415, "A JSON request body is required.")
            else:
                try:
                    size = int(request.headers.get("content-length", "-1"))
                except ValueError:
                    size = -1
                if size < 0 or request.headers.get("transfer-encoding"):
                    error = (411, "A fixed request length is required.")
                elif size > 32768:
                    error = (413, "The request is too large.")
                elif len(await request.body()) != size:
                    error = (400, "The request length is inconsistent.")
        response = JSONResponse({"detail": error[1]}, status_code=error[0]) if error else await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = ("default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        return response

    return app


class _CommunityOnly:
    def __init__(self, app, port):
        self.app, self.port = app, port

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] == "http" and (path.startswith("/api/platform/community/")
                                         or path in REVIEW_FILES or path == "/api/health"):
            return await self.app(scope, receive, send)
        if scope["type"] == "http":
            response = JSONResponse({"detail": "This endpoint is unavailable in the MCP bridge."},
                                    status_code=404, headers={"Cache-Control": "no-store"})
            return await response(scope, receive, send)
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        raise RuntimeError("Unsupported MCP bridge protocol.")


@contextmanager
def yerel_kopru(data_dir):
    """Yield an owned loopback origin, then stop only that server and release its locks."""
    directory = _data_directory(data_dir)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server = None
    worker = None
    finished = threading.Event()
    failures = []
    async_owner = {}
    try:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        port = listener.getsockname()[1]
        app = _application(directory, port)
        # Own the lifespan even if Uvicorn fails before its normal startup handshake.
        async def serve():
            nonlocal server
            async with app.router.lifespan_context(app):
                # Keep stdio stdout exclusively available for MCP protocol frames.
                config = uvicorn.Config(_CommunityOnly(app, port), host="127.0.0.1", port=port,
                                        log_config=LOG_CONFIG, access_log=False, lifespan="off", ws="none",
                                        loop="asyncio", interface="asgi3", timeout_graceful_shutdown=2,
                                        proxy_headers=False, server_header=False)
                server = uvicorn.Server(config)
                async_owner.update(loop=asyncio.get_running_loop(), task=asyncio.current_task())
                await server.serve(sockets=[listener])

        def run():
            try:
                asyncio.run(serve())
            except BaseException as exc:
                failures.append(exc)
            finally:
                finished.set()

        worker = threading.Thread(target=run, name="devtrellis-mcp-loopback", daemon=True)
        try:
            worker.start()
        except BaseException:
            # No worker means nothing entered the lifespan that owns constructor locks.
            async def close_unstarted():
                async with app.router.lifespan_context(app):
                    pass
            asyncio.run(close_unstarted())
            raise
        deadline = time.monotonic() + START_TIMEOUT
        while server is None or not server.started:
            if finished.wait(0.01):
                raise RuntimeError("Could not start the local DevTrellis MCP bridge.") from (failures[0] if failures else None)
            if time.monotonic() >= deadline:
                raise RuntimeError("Timed out starting the local DevTrellis MCP bridge.")
        yield f"http://127.0.0.1:{port}"
    finally:
        if server is not None:
            server.should_exit = True
        if worker is not None and worker.ident is not None:
            worker.join(STOP_TIMEOUT)
            if worker.is_alive():
                if server is not None:
                    server.force_exit = True
                listener.close()
                if async_owner and not async_owner["loop"].is_closed():
                    async_owner["loop"].call_soon_threadsafe(async_owner["task"].cancel)
                worker.join(1.0)
        listener.close()
        if worker is not None and worker.is_alive():
            raise RuntimeError("The owned MCP bridge did not stop within its shutdown limit.")
