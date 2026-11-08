"""İnsanlı paylaşım denemesi için canlıya bağlanmayan ayrı bir yerel pilot hazırlar."""

import argparse
import html
import json
import os
import secrets
import socket
import sqlite3
import stat
import sys
import threading
import time
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / ".deps"))

OWNER = {"application": "devtrellis-r0-human-pilot", "schema": 1}
MARKER = "pilot-owner.json"
COMMUNITY_PORT = 18794
REVIEW_PORT = 18795
STATIC_FILES = frozenset({"contribution-review.html", "contribution-review.js", "contribution-review.css",
                          "contribution-submissions.html", "contribution-submissions.js", "ui-text.js",
                          "confirm-dialog.js", "confirm-dialog.css"})


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def safe_directory(value, *, create=False):
    directory = Path(os.path.abspath(value))
    for part in (directory, *directory.parents):
        if not part.exists():
            continue
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Pilot dizini sembolik bağ veya junction içeremez.")
    if create:
        directory.mkdir(parents=True, exist_ok=False)
        write_new(directory / MARKER, OWNER)
    if not directory.is_dir() or json.loads((directory / MARKER).read_text(encoding="utf-8")) != OWNER:
        raise ValueError("Bu dizin R0 pilotuna ait değil.")
    for child in directory.rglob("*"):
        info = child.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400 or (child.is_file() and info.st_nlink != 1):
            raise ValueError("Pilot dosyaları bağ veya paylaşılan sabit bağlantı içeremez.")
    return directory


def write_new(path, content):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(content, ensure_ascii=False, indent=2) + "\n")


def example_packet():
    from mcp_topluluk import contribution_packet
    return contribution_packet(
        title="Synthetic local contribution-flow exercise",
        summary="A disposable local exercise of preview, keep local, submission, moderation and withdrawal. No research or benchmark claim is made.",
        body=("## Scope\nThis is a synthetic document for a local contribution-flow exercise. "
              "It contains no personal question, account information, conversation or provider output.\n\n"
              "## Observation plan\nA human first keeps one preview local, then chooses whether to submit a "
              "separate preview to the local pilot. A human moderator may publish the exact version before "
              "the contributor withdraws it.\n\n## Limitations\nThe example.org link is a placeholder, "
              "not evidence. This exercise does not establish research quality, model accuracy or production acceptance."),
        sources=[{"title": "Reserved example placeholder; not evidence", "url": "https://example.org/"}],
        model="unknown", provider="unknown", license="CC-BY-4.0", topic="software", language="en")


def prepare(directory):
    directory = safe_directory(directory, create=True)
    (directory / "evidence").mkdir()
    write_new(directory / "draft.json", example_packet())
    from radar.topluluk_depo import ToplulukDeposu
    ToplulukDeposu(directory / "community.sqlite3")
    write_new(directory / "prepared.json", {"prepared_at": stamp(), "human_test": "not_started",
              "public_service": "not_contacted", "moderator": "not_created"})
    return directory


class LocalTransport:
    """Yalnız atanmış pilot portuna izin verir; ortam vekilini ve DNS'i kullanmaz."""
    def __init__(self, port):
        import httpx
        self.port = port
        self.transport = httpx.HTTPTransport(retries=0)
        self.lock = threading.RLock()
        self.calls = {"requests": 0, "write_requests": 0, "submission_requests": 0, "blocked": 0}

    def handle_request(self, request):
        import httpx
        url = request.url
        if (url.scheme != "http" or url.host != "127.0.0.1" or url.port != self.port
                or url.username or url.password or not url.path.startswith("/api/community/")):
            with self.lock:
                self.calls["blocked"] += 1
            raise httpx.ConnectError("R0 pilotu dış ağ hedefini engelledi.", request=request)
        with self.lock:
            self.calls["requests"] += 1
            if request.method not in {"GET", "HEAD"}:
                self.calls["write_requests"] += 1
            if request.method == "POST" and url.path == "/api/community/inbox":
                self.calls["submission_requests"] += 1
        return self.transport.handle_request(request)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        # ToplulukBaglantisi her çağrıda bir istemci açar; taşımanın sahibi pilottur.
        pass

    def close(self):
        self.transport.close()


def read_rows(path, sql):
    if not path.is_file():
        return []
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(sql)]


def state(directory, transport, actions, review_origin, community_origin):
    entries = read_rows(directory / "community.sqlite3", "SELECT id,status,version,latest_revision,published_revision,created_at,updated_at FROM community_entries ORDER BY id")
    deliveries = read_rows(directory / "research.sqlite3", "SELECT local_id,remote_id,created_at FROM community_deliveries ORDER BY local_id")
    moderators = read_rows(directory / "community.sqlite3", "SELECT COUNT(*) AS amount FROM community_accounts WHERE role='moderator' AND active=1")
    with transport.lock:
        network = dict(transport.calls)
    return {"at": stamp(), "mode": "isolated_local_human_pilot", "process_id": os.getpid(),
            "community_url": community_origin, "review_url": review_origin,
            "network": network, "actions": list(actions), "entries": entries,
            "local_deliveries": deliveries, "active_moderators": moderators[0]["amount"],
            "human_acceptance": "pending_manual_observation"}


def review_app(directory, review_origin, community_origin, transport, actions):
    from fastapi import FastAPI, Request
    from starlette.responses import HTMLResponse, JSONResponse, Response
    from radar.depo import Depo
    from radar.platform_api import router_olustur
    from radar.topluluk_baglantisi import ToplulukBaglantisi

    nonce = secrets.token_urlsafe(32)
    depot = Depo(directory / "research.sqlite3", sources=[])
    connection = ToplulukBaglantisi(depot, transport=transport)
    packet = json.loads((directory / "draft.json").read_text(encoding="utf-8"))
    previews = [connection.onizle({"packet": packet}) for _ in range(2)]
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    authority = review_origin.removeprefix("http://")

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        path = request.url.path
        if (request.headers.get("host") != authority
                or request.headers.get("origin", review_origin) != review_origin
                or request.headers.get("sec-fetch-site") == "cross-site"):
            return JSONResponse({"detail": "Yalnız bu pilotun yerel adresi kullanılabilir."}, status_code=403)
        allowed = (path in {"/", "/r0/status", "/api/health"}
                   or path.removeprefix("/static/") in STATIC_FILES and path.startswith("/static/")
                   or path.startswith("/api/platform/community/"))
        if not allowed or "/source-memory" in path:
            return JSONResponse({"detail": "Bu yol R0 pilotunda kapalı."}, status_code=404)
        if request.method not in {"GET", "HEAD"}:
            if (not secrets.compare_digest(request.headers.get("x-radar-csrf", ""), nonce)
                    or request.headers.get("content-type", "").split(";", 1)[0] != "application/json"):
                return JSONResponse({"detail": "Yerel inceleme oturumu gerekli."}, status_code=403)
            if request.headers.get("transfer-encoding") or not request.headers.get("content-length", "").isdigit():
                return JSONResponse({"detail": "Sabit gövde uzunluğu gerekli."}, status_code=411)
            if int(request.headers["content-length"]) > 32768:
                return JSONResponse({"detail": "Gövde sınırı aşıldı."}, status_code=413)
        response = await call_next(request)
        if request.method == "POST" and response.status_code == 200:
            operation = path.rsplit("/", 1)[-1]
            if operation in {"discard", "send", "withdraw"}:
                actions.append({"operation": operation, "at": stamp(), "via": "review_api"})
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        return response

    @app.get("/")
    def landing():
        links = "".join(f'<li><a href="/static/contribution-review.html?preview={html.escape(item["preview_id"])}">Deneme {index + 1}: {label}</a></li>'
                        for index, (item, label) in enumerate(zip(previews, ("Yerelde tut", "İncelemeye gönder"))))
        return HTMLResponse('<!doctype html><html lang="tr"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                            '<title>DevTrellis — yalnız yerel pilot</title><main><h1>Yalnız yerel pilot</h1>'
                            '<p>Canlı siteye bağlanmaz. Bu bir yerel akış denemesidir; araştırma kalitesi veya performans kabulü değildir. Karar düğmelerini yalnız Kullanıcı kullanır.</p>'
                            f'<p>Gönderim adresi: {community_origin}</p><ol>{links}</ol>'
                            '<p>Önizlemeler 30 dakika geçerlidir. Süre dolarsa pilotu yeniden başlatın.</p>'
                            '<p><a href="/static/contribution-submissions.html?lang=tr">Gönderimlerim</a></p>'
                            f'<p><a href="{community_origin}">Pilot topluluk ve moderatör girişi</a></p>'
                            '<p><a href="/r0/status">Gizli bilgi içermeyen durum kaydı</a></p></main></html>')

    @app.get("/api/platform/community/session")
    def session():
        return {"csrf_token": nonce, "destination": community_origin, "search_views": ["full", "summary"],
                "search_modes": ["all", "ranked"], "source_memory": {"adapter_version": 1, "remote_enabled": False}}

    @app.get("/api/health")
    def health():
        return {"product": "DevTrellis", "mode": "r0-local-pilot", "automatic_research": False, "manual_ai": False}

    @app.get("/r0/status")
    def status():
        return state(directory, transport, actions, review_origin, community_origin)

    @app.get("/static/{name}")
    def static(name: str):
        if name not in STATIC_FILES:
            return Response(status_code=404)
        data = (ROOT / "radar" / "static" / name).read_text(encoding="utf-8")
        # Sadece pilot yanıtındaki hedef değişir; depodaki ve kurulu dosyalar değişmez.
        data = data.replace("https://devtrellis.org", community_origin)
        media = "text/html" if name.endswith(".html") else "text/css" if name.endswith(".css") else "application/javascript"
        return Response(data, media_type=media)

    app.include_router(router_olustur(depot, nonce, community=connection))
    return app


@contextmanager
def running(directory, community_port=COMMUNITY_PORT, review_port=REVIEW_PORT):
    import uvicorn
    from radar import topluluk_baglantisi
    from radar.topluluk_api import uygulama

    directory = safe_directory(directory)
    if community_port == review_port and community_port != 0:
        raise ValueError("Topluluk ve inceleme portları ayrı olmalı.")
    sockets, servers, threads = [], [], []
    old_destination = topluluk_baglantisi.COMMUNITY_URL
    transport = None
    try:
        for port in (community_port, review_port):
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sockets.append(listener)
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(("127.0.0.1", port))
            listener.listen(128)
        community_origin = "http://127.0.0.1:" + str(sockets[0].getsockname()[1])
        review_origin = "http://127.0.0.1:" + str(sockets[1].getsockname()[1])
        # Değişiklik yalnız bu ayrı pilot sürecinin belleğindedir; canlıya geri düşüş yoktur.
        topluluk_baglantisi.COMMUNITY_URL = community_origin
        transport = LocalTransport(sockets[0].getsockname()[1])
        actions = []
        apps = [uygulama(directory / "community.sqlite3", public_url=community_origin, local_pilot=True,
                         feed_db_path=directory / "feed.sqlite3", sources=[]),
                review_app(directory, review_origin, community_origin, transport, actions)]
        for app, listener in zip(apps, sockets):
            server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=listener.getsockname()[1],
                    access_log=False, log_level="warning", proxy_headers=False, server_header=False,
                    ws="none", timeout_graceful_shutdown=2))
            worker = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
            servers.append(server)
            threads.append(worker)
            worker.start()
        deadline = time.monotonic() + 15
        while not all(server.started for server in servers):
            if time.monotonic() > deadline or not all(worker.is_alive() for worker in threads):
                raise RuntimeError("R0 pilotu başlatılamadı.")
            time.sleep(0.02)
        yield {"community": community_origin, "review": review_origin, "transport": transport, "actions": actions}
    finally:
        for server in servers:
            server.should_exit = True
        for worker in threads:
            worker.join(5)
        for listener in sockets:
            listener.close()
        if transport is not None:
            transport.close()
        topluluk_baglantisi.COMMUNITY_URL = old_destination
        if any(worker.is_alive() for worker in threads):
            raise RuntimeError("Başlatılan pilot süreci tam kapanmadı; yeniden başlatmadan önce süreç kaydını kontrol edin.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Canlı hizmete bağlanmayan insanlı R0 yerel denemesi")
    parser.add_argument("--directory", required=True, type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("prepare")
    start = sub.add_parser("start")
    start.add_argument("--community-port", type=int, default=COMMUNITY_PORT)
    start.add_argument("--review-port", type=int, default=REVIEW_PORT)
    for name in ("moderator-add", "moderator-reset"):
        sub.add_parser(name)
    observe = sub.add_parser("snapshot")
    observe.add_argument("--phase", choices=["before", "after-keep", "after-share", "after-publish", "after-withdraw"], required=True)
    observe.add_argument("--review-port", type=int, default=REVIEW_PORT)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            print("Pilot hazır: " + str(prepare(args.directory)))
        else:
            directory = safe_directory(args.directory)
            if args.command in {"moderator-add", "moderator-reset"}:
                from topluluk import main as community_cli
                command = ["--db", str(directory / "community.sqlite3")]
                command += (["account-add", "r0-reviewer", "--display-name", "Local pilot reviewer", "--role", "moderator"]
                            if args.command == "moderator-add" else ["password-reset", "r0-reviewer"])
                return community_cli(command)
            if args.command == "snapshot":
                import http.client
                client = http.client.HTTPConnection("127.0.0.1", args.review_port, timeout=5)
                try:
                    client.request("GET", "/r0/status")
                    response = client.getresponse()
                    if response.status != 200:
                        raise ValueError("Pilot durum kaydı alınamadı.")
                    value = json.loads(response.read(1024 * 1024))
                finally:
                    client.close()
                if value.get("mode") != "isolated_local_human_pilot":
                    raise ValueError("Bağlanılan hizmet R0 pilotu değil.")
                value["phase"] = args.phase
                target = directory / "evidence" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ-") + args.phase + ".json")
                write_new(target, value)
                print(str(target))
            elif args.command == "start":
                with running(directory, args.community_port, args.review_port) as pilot:
                    print("YALNIZ YEREL PİLOT — canlı bağlantı yok.", flush=True)
                    print("İnceleme: " + pilot["review"], flush=True)
                    print("Topluluk: " + pilot["community"], flush=True)
                    print("Kapatmak için bu terminalde Ctrl+C. Süreç: " + str(os.getpid()), flush=True)
                    while True:
                        time.sleep(1)
        return 0
    except KeyboardInterrupt:
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
