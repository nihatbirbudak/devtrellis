"""DevTrellis katkılarını dar hesapsız teslim protokolü, güvenli oturum ve moderatör onayıyla sunar."""

import hashlib
import re
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Body, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import FileResponse, JSONResponse

from radar.depo import Depo
from radar.gelistirici_alani import GelistiriciAlani, alanlari_dogrula, guvenli_url, paket_dogrula
from radar.paylasim_gizliligi import kamu_kopyasi
from radar.kaynak_havuzu import kaynaklari_oku
from radar.konular import konulari_listele
from radar.marka import PRODUCT_NAME, VERSION
from radar.topluluk_depo import ToplulukDeposu, ToplulukHatasi
from radar.topluluk_talepleri import TalepDeposu


COOKIE = "devtrellis_session"
BODY_LIMIT = 65536


class IstekSiniri:
    """Tek hizmet sürecinde IP ve işlem türüne göre kısa süreli istek bütçesi tutar."""
    def __init__(self, clock=None):
        self.clock = clock or time.time
        self.buckets = {}
        self.lock = threading.Lock()

    def izin(self, address, category, limit, period=60):
        now = int(self.clock())
        expires = (now // period + 1) * period
        key = (address, category)
        with self.lock:
            if len(self.buckets) > 2048:
                self.buckets = {saved_key: value for saved_key, value in self.buckets.items() if value[0] > now}
            if key not in self.buckets and len(self.buckets) >= 4096:
                return False
            previous_expires, count = self.buckets.get(key, (expires, 0))
            count = count + 1 if previous_expires == expires else 1
            self.buckets[key] = (expires, count)
            return count <= limit


def adresi_dogrula(public_url, local_pilot):
    if not isinstance(public_url, str):
        raise ValueError("Hizmetin tam adresi gerekli.")
    try:
        parsed = urlsplit(public_url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Hizmet adresi geçersiz.") from exc
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ValueError("Hizmet adresi yol, sorgu veya kullanıcı bilgisi içeremez.")
    if local_pilot:
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise ValueError("Yerel pilot yalnız http://127.0.0.1 veya http://localhost adresinde açılır.")
        if port is None:
            raise ValueError("Yerel pilot adresinde port gerekli.")
    else:
        guvenli_url(public_url, required=True)
        if parsed.scheme != "https":
            raise ValueError("Topluluk hizmeti HTTPS gerektirir; HTTP yalnız açık yerel pilot bayrağıyla kullanılabilir.")
    return f"{parsed.scheme}://{parsed.netloc.lower()}"


def uygulama(db_path, *, public_url, local_pilot=False, feed_db_path=None, sources=None, static_dir=None, clock=None):
    origin = adresi_dogrula(public_url, local_pilot)
    authority = urlsplit(origin).netloc
    store = ToplulukDeposu(db_path, clock=clock)
    requests_store = TalepDeposu(db_path, clock=clock)
    feed_path = Path(feed_db_path) if feed_db_path else Path(db_path).with_name("community-feed.sqlite3")
    if feed_path.resolve() == Path(db_path).resolve():
        raise ValueError("Haber deposu ve topluluk deposu ayrı dosyalar olmalı.")
    feed_depo = Depo(feed_path, sources if sources is not None else kaynaklari_oku())
    feed = GelistiriciAlani(feed_depo)
    static = Path(static_dir) if static_dir else Path(__file__).resolve().parent / "static"
    limiter = IstekSiniri(clock)
    app = FastAPI(title=f"{PRODUCT_NAME} Community", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.community = store
    app.state.requests = requests_store
    app.state.feed_depo = feed_depo

    @app.exception_handler(RequestValidationError)
    async def request_validation(request, exc):
        if request.url.path == "/api/community/requests" or request.url.path.startswith("/api/community/requests/"):
            # Framework errors otherwise echo malformed private message bodies and query values.
            return JSONResponse({"detail": "Talep biçimi geçersiz; alanları kontrol edin."}, status_code=422)
        return await request_validation_exception_handler(request, exc)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status if isinstance(exc, ToplulukHatasi) else 422)

    @app.middleware("http")
    async def boundaries(request: Request, call_next):
        if request.headers.get("host", "").lower() != authority:
            return JSONResponse({"detail": "Hizmet adresi eşleşmiyor."}, status_code=403)
        if not local_pilot and request.url.scheme != "https":
            return JSONResponse({"detail": "HTTPS gerekli."}, status_code=400)
        supplied_origin = request.headers.get("origin")
        if supplied_origin is not None and supplied_origin != origin:
            return JSONResponse({"detail": "Başka siteden erişim reddedildi."}, status_code=403)
        inbox_create = request.method == "POST" and request.url.path == "/api/community/inbox"
        inbox_control = ((request.method == "GET" and re.fullmatch(r"/api/community/inbox/[^/]+", request.url.path)) or
                         (request.method == "POST" and re.fullmatch(r"/api/community/inbox/[^/]+/withdraw", request.url.path)))
        inbox_call = inbox_create or inbox_control
        contact_create = request.method == "POST" and request.url.path == "/api/community/requests"
        event_write = request.method == "POST" and re.fullmatch(r"/api/community/cards/[^/]+/events", request.url.path)
        event_client = event_write and "x-devtrellis-client" in request.headers
        if event_client and (request.headers.get("x-devtrellis-client") != "1" or
                "origin" in request.headers or "cookie" in request.headers or
                any(name.startswith("sec-fetch-") for name in request.headers)):
            return JSONResponse({"detail": "İstemci etkileşimi tarayıcı oturumu veya bağlamı taşıyamaz."}, status_code=403)
        if inbox_call:
            # Yalnız yerel sunucu istemcisi: tarayıcı çerezlerini/bağlamını bu istisnaya taşıma.
            if ("origin" in request.headers or "cookie" in request.headers or
                    any(name.startswith("sec-fetch-") for name in request.headers) or
                    request.headers.get("x-devtrellis-inbox") != "1"):
                return JSONResponse({"detail": "Bu teslim yolu yerel uygulamanın sunucu bağlantısını gerektirir."}, status_code=403)
        mutation = request.method not in {"GET", "HEAD", "OPTIONS"}
        category = ("contact" if contact_create else "inbox" if inbox_create else "inbox-control" if inbox_control else
                    "event" if event_write else "login" if request.url.path == "/api/community/login" else "write" if mutation else "read")
        limit = {"contact": 4, "login": 6, "write": 40, "read": 240, "inbox": 4, "inbox-control": 30, "event": 60}[category]
        address = request.client.host if request.client else "unknown"
        if not limiter.izin(address, category, limit):
            return JSONResponse({"detail": "İstek sınırına ulaşıldı; bir dakika sonra tekrar deneyin."},
                                status_code=429, headers={"Retry-After": "60"})
        if inbox_create and not limiter.izin(address, "inbox-daily", 20, period=86400):
            return JSONResponse({"detail": "Günlük gönderim sınırına ulaşıldı; daha sonra tekrar deneyin."},
                                status_code=429, headers={"Retry-After": "86400"})
        if contact_create and (not limiter.izin(address, "contact-daily", 20, period=86400) or
                               not limiter.izin("all", "contact-total", 60)):
            return JSONResponse({"detail": "Talep sınırına ulaşıldı; daha sonra tekrar deneyin."},
                                status_code=429, headers={"Retry-After": "86400"})
        if event_write and (not limiter.izin(address, "event-daily", 2000, period=86400) or
                            not limiter.izin("all", "event-total", 600)):
            return JSONResponse({"detail": "Etkileşim sınırına ulaşıldı; daha sonra tekrar deneyin."},
                                status_code=429, headers={"Retry-After": "60"})
        if mutation:
            if not inbox_call and not event_client and (supplied_origin != origin or request.headers.get("sec-fetch-site") == "cross-site"):
                return JSONResponse({"detail": "Aynı hizmet adresinden gönderim gerekli."}, status_code=403)
            if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                return JSONResponse({"detail": "JSON gövdesi gerekli."}, status_code=415)
            try:
                length = int(request.headers.get("content-length", "-1"))
            except ValueError:
                length = -1
            if length < 0 or request.headers.get("transfer-encoding"):
                return JSONResponse({"detail": "İstek uzunluğu gerekli."}, status_code=411)
            if contact_create and length > 16384:
                return JSONResponse({"detail": "Talep en fazla 16 KiB olabilir."}, status_code=413)
            if length > BODY_LIMIT:
                return JSONResponse({"detail": "İstek en fazla 64 KiB olabilir."}, status_code=413)
            body = await request.body()
            if len(body) != length:
                return JSONResponse({"detail": "İstek uzunluğu tutarsız."}, status_code=400)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = ("default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        if not local_pilot:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    def csrf(token):
        return hashlib.sha256(("devtrellis-csrf:" + token).encode("utf-8")).hexdigest()

    def session(request, *, authenticated=True, mutate=False):
        token = request.cookies.get(COOKIE)
        state = store.oturum_oku(token)
        if state is None or (authenticated and state["user"] is None):
            raise HTTPException(401, "Oturum açın.")
        if mutate and not secrets.compare_digest(request.headers.get("x-ufuk-csrf", "").encode(), csrf(token).encode()):
            raise HTTPException(403, "Sayfayı yenileyip tekrar deneyin.")
        return token, state["user"]

    def write_cookie(response, token):
        response.set_cookie(COOKIE, token, max_age=28800, httponly=True, secure=not local_pilot, samesite="strict", path="/")

    def session_view(token, user):
        return {"authenticated": user is not None, "user": user, "csrf_token": csrf(token)}

    def observer(request):
        state = store.oturum_oku(request.cookies.get(COOKIE))
        return state["user"] if state else None

    def inbox_token(request):
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "Geçerli gönderim yönetim anahtarı gerekli.")
        token = authorization[7:]
        store.inbox_token_izi(token)
        return token

    @app.get("/")
    def home():
        return FileResponse(static / "community.html")

    @app.get("/about")
    @app.get("/guidelines")
    @app.get("/contribute")
    @app.get("/support")
    def community_pages():
        return FileResponse(static / "community-pages.html")

    @app.get("/requests")
    def request_management():
        # Sayfa kabuğu veri içermez; bütün talep okumaları moderatör yetkisi gerektirir.
        return FileResponse(static / "community-requests.html")

    @app.get("/static/community-pages.js")
    def community_pages_script():
        return FileResponse(static / "community-pages.js", media_type="text/javascript")

    @app.get("/static/community-pages.css")
    def community_pages_styles():
        return FileResponse(static / "community-pages.css", media_type="text/css")

    @app.get("/static/community-requests.js")
    def community_requests_script():
        return FileResponse(static / "community-requests.js", media_type="text/javascript")

    @app.get("/static/community-requests.css")
    def community_requests_styles():
        return FileResponse(static / "community-requests.css", media_type="text/css")

    @app.get("/community.css")
    @app.get("/static/community.css")
    def stylesheet():
        return FileResponse(static / "community.css", media_type="text/css")

    @app.get("/static/platform.css")
    def shared_stylesheet():
        return FileResponse(static / "platform.css", media_type="text/css")

    @app.get("/community.js")
    @app.get("/static/community.js")
    def script():
        return FileResponse(static / "community.js", media_type="text/javascript")

    @app.get("/static/ui-text.js")
    def shared_text():
        return FileResponse(static / "ui-text.js", media_type="text/javascript")

    @app.get("/static/ufuk.svg")
    def icon():
        return FileResponse(static / "ufuk.svg", media_type="image/svg+xml")

    @app.get("/api/health")
    def health():
        return {"product": PRODUCT_NAME, "version": VERSION, "mode": "community", "status": "ok"}

    @app.get("/api/topics")
    def topics():
        return konulari_listele()

    @app.get("/api/community/session")
    def read_session(request: Request, response: Response):
        token = request.cookies.get(COOKIE)
        state = store.oturum_oku(token)
        if state is None:
            token = store.oturum_ac()
            state = {"user": None}
            write_cookie(response, token)
        return session_view(token, state["user"])

    @app.post("/api/community/login")
    def login(request: Request, response: Response, payload: dict = Body(...)):
        old_token, previous_user = session(request, authenticated=False, mutate=True)
        alanlari_dogrula(payload, {"username", "password"}, {"username", "password"})
        user = store.kimlik_dogrula(payload["username"], payload["password"])
        token = store.oturum_ac(user["id"], old_token)
        write_cookie(response, token)
        return session_view(token, user)

    @app.post("/api/community/logout")
    def logout(request: Request, response: Response, payload: dict = Body(...)):
        alanlari_dogrula(payload, set())
        token, user = session(request, authenticated=False, mutate=True)
        store.oturum_kapat(token)
        response.delete_cookie(COOKIE, path="/", secure=not local_pilot, httponly=True, samesite="strict")
        return {"authenticated": False, "user": None, "csrf_token": None}

    def moderator(request, *, mutate=False):
        _, user = session(request, mutate=mutate)
        if user.get("role") != "moderator":
            raise HTTPException(403, "Bu işlem moderatör yetkisi gerektirir.")
        return user

    @app.post("/api/community/requests")
    def create_request(request: Request, response: Response, payload: dict = Body(...)):
        session(request, authenticated=False, mutate=True)
        result = requests_store.gonder(payload)
        response.status_code = 200 if result["duplicate"] else 201
        return result

    @app.get("/api/community/requests")
    def list_requests(request: Request, status: str = "", offset: int = Query(0, ge=0, le=100000),
                      limit: int = Query(25, ge=1, le=100)):
        moderator(request)
        return requests_store.listele(status=status, offset=offset, limit=limit)

    @app.get("/api/community/requests/{request_id}")
    def read_request(request_id: str, request: Request):
        moderator(request)
        return requests_store.oku(request_id)

    @app.post("/api/community/requests/{request_id}/status")
    def update_request_status(request_id: str, request: Request, payload: dict = Body(...)):
        moderator(request, mutate=True)
        alanlari_dogrula(payload, {"status"}, {"status"})
        return requests_store.durum_degistir(request_id, payload["status"])

    @app.get("/api/community/cards")
    def cards(request: Request, query: str = Query("", max_length=200), kind: str = "",
              offset: int = Query(0, ge=0, le=9223372036854775807), limit: int = Query(30, ge=1, le=100)):
        return store.listele(query=query, kind=kind, offset=offset, limit=limit, observer=observer(request))

    @app.get("/api/community/cards/{entry_id}")
    def card(entry_id: str, request: Request, version: int | None = Query(None, ge=1, le=9223372036854775807)):
        return store.yayini_oku(entry_id, observer(request), version=version)

    @app.get("/api/community/cards/{entry_id}/packet")
    def packet(entry_id: str, version: int | None = Query(None, ge=1, le=9223372036854775807)):
        result = store.yayini_oku(entry_id, version=version)
        return JSONResponse(result["packet"], headers={"Content-Disposition": f'attachment; filename="devtrellis-{result["id"]}-v{result["version"]}.json"'})

    @app.post("/api/community/cards/{entry_id}/events")
    def event(entry_id: str, request: Request, payload: dict = Body(...)):
        if request.headers.get("x-devtrellis-client") != "1":
            session(request, authenticated=False, mutate=True)
        return store.etkilesim_ekle(entry_id, payload)

    @app.get("/api/community/mine")
    def mine(request: Request, query: str = Query("", max_length=200),
             offset: int = Query(0, ge=0, le=9223372036854775807), limit: int = Query(100, ge=1, le=100)):
        token, user = session(request)
        return store.listele(user=user, query=query, offset=offset, limit=limit)

    @app.post("/api/community/submissions", status_code=201)
    def submit(request: Request, payload: dict = Body(...)):
        token, user = session(request, mutate=True)
        return store.gonder(user, payload)

    @app.post("/api/community/inbox")
    def inbox_submit(request: Request, response: Response, payload: dict = Body(...)):
        token = inbox_token(request)
        try:
            original = paket_dogrula(payload.get("packet"))
            public_copy, _ = kamu_kopyasi(original)
            if public_copy != original:
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            # Kullanıcının incelediği kopyayı sunucuda sessizce değiştirme veya özel metni hataya taşıma.
            raise HTTPException(422, "Katkı anonim kamu kopyası olmalı; güncel yerel önizlemeyi inceleyip tekrar gönderin.") from None
        result = store.inbox_gonder(token, payload)
        response.status_code = 200 if result["duplicate"] else 201
        return result

    @app.get("/api/community/inbox/status")
    def inbox_recover(request: Request):
        return store.inbox_bul(inbox_token(request))

    @app.get("/api/community/inbox/{entry_id}")
    def inbox_status(entry_id: str, request: Request):
        return store.inbox_oku(inbox_token(request), entry_id)

    @app.post("/api/community/inbox/{entry_id}/withdraw")
    def inbox_withdraw(entry_id: str, request: Request, payload: dict = Body(...)):
        return store.inbox_geri_cek(inbox_token(request), entry_id, payload)

    @app.post("/api/community/submissions/{entry_id}")
    def update(entry_id: str, request: Request, payload: dict = Body(...)):
        token, user = session(request, mutate=True)
        return store.gonder(user, payload, entry_id)

    @app.post("/api/community/submissions/{entry_id}/withdraw")
    def withdraw(entry_id: str, request: Request, payload: dict = Body(...)):
        token, user = session(request, mutate=True)
        return store.geri_cek(user, entry_id, payload)

    @app.get("/api/community/review")
    def review(request: Request, query: str = Query("", max_length=200),
               offset: int = Query(0, ge=0, le=9223372036854775807), limit: int = Query(100, ge=1, le=100)):
        token, user = session(request)
        return store.listele(user=user, review=True, query=query, offset=offset, limit=limit)

    @app.post("/api/community/submissions/{entry_id}/decision")
    def decision(entry_id: str, request: Request, payload: dict = Body(...)):
        token, user = session(request, mutate=True)
        return store.karar(user, entry_id, payload)

    @app.post("/api/community/submissions/{entry_id}/hide")
    def hide(entry_id: str, request: Request, payload: dict = Body(...)):
        token, user = session(request, mutate=True)
        return store.yayindan_kaldir(user, entry_id, payload)

    @app.get("/api/community/feed")
    def news(query: str = Query("", max_length=200), interest: str = Query("", max_length=40),
             offset: int = Query(0, ge=0, le=9223372036854775807), limit: int = Query(30, ge=1, le=100)):
        return {**feed.akis(query=query, interest=interest, offset=offset, limit=limit),
                "coverage": feed.gorunum()["coverage"]}

    return app
