"""DevTrellis katkılarını dar hesapsız teslim protokolü, güvenli oturum ve moderatör onayıyla sunar."""

import hashlib
import html
import hmac
import ipaddress
import os
import stat
import re
import secrets
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import Body, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response as RawResponse

from radar.arastirma_ozeti import arama_ozeti
from radar.depo import Depo
from radar.gelistirici_alani import GelistiriciAlani, alanlari_dogrula, guvenli_url, paket_dogrula
from radar.paylasim_gizliligi import kamu_kopyasi
from radar.kaynak_havuzu import kaynaklari_oku
from radar.konular import konulari_listele
from radar.marka import PRODUCT_NAME, VERSION
from radar.ozel_dosya_izinleri import ozel_dosya_izni
from radar.topluluk_depo import ToplulukDeposu, ToplulukHatasi
from radar.topluluk_talepleri import TalepDeposu
from radar.topluluk_sayfalari import atom, kisa_ad, markdown_rapor, rapor_yolu, sayfa, sitemap, yayindaki_arastirmalar


COOKIE = "devtrellis_session"
BODY_LIMIT = 65536


def _rate_path(path):
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or (getattr(info, "st_file_attributes", 0) &
                                         getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
            raise ValueError("İstek sınırı yolu bağlantı olamaz.")


def _rate_store(connection, *, allow_empty=False):
    app_id = connection.execute("PRAGMA application_id").fetchone()[0]
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    if allow_empty and not tables and app_id == 0 and version == 0:
        return False
    if app_id != 0x44545231 or version != 1 or tables != {"request_limits"}:
        raise ValueError("İstek sınırı için ayrı, desteklenen özel veritabanı gerekli.")
    return True


class IstekSiniri:
    """Adresleri kalıcı kipte gizli anahtarlı özetle sınırlı süre saklar."""
    def __init__(self, clock=None, *, db_path=None, key=None):
        self.clock = clock or time.time
        self.db_path = Path(os.path.abspath(db_path)) if db_path else None
        if self.db_path and (not isinstance(key, (bytes, bytearray)) or len(key) < 32):
            raise ValueError("Kalıcı istek sınırı için en az 32 bayt gizli anahtar gerekli.")
        if self.db_path:
            _rate_path(self.db_path)
            allowed = {self.db_path.name, *(self.db_path.name + suffix for suffix in ("-journal", "-wal", "-shm"))}
            if self.db_path.parent.exists():
                for child in self.db_path.parent.iterdir():
                    try:
                        info = child.lstat()
                    except FileNotFoundError:
                        continue
                    _rate_path(child)
                    if child.name not in allowed or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise ValueError("İstek sınırı deposu yalnız kendisine ayrılmış dizinde tutulmalı.")
            if self.db_path.exists() and self.db_path.stat().st_size:
                with closing(sqlite3.connect(self.db_path.as_uri() + "?mode=ro", uri=True)) as connection:
                    _rate_store(connection)
            self.db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            ozel_dosya_izni(self.db_path.parent, directory=True)
            try:
                descriptor = os.open(self.db_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass
            else:
                os.close(descriptor)
            ozel_dosya_izni(self.db_path)
            with closing(sqlite3.connect(self.db_path)) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                if not _rate_store(connection, allow_empty=True):
                    connection.execute("CREATE TABLE request_limits (address_hash TEXT NOT NULL,category TEXT NOT NULL,expires INTEGER NOT NULL,count INTEGER NOT NULL,PRIMARY KEY(address_hash,category))")
                    connection.execute("PRAGMA application_id=1146376753")
                    connection.execute("PRAGMA user_version=1")
        self.key = key
        self.buckets = {}
        self.lock = threading.Lock()

    def izin(self, address, category, limit, period=60):
        now = int(self.clock())
        expires = (now // period + 1) * period
        if self.db_path:
            digest = hmac.new(self.key, str(address).encode("utf-8"), hashlib.sha256).hexdigest()
            with self.lock, closing(sqlite3.connect(self.db_path, timeout=10)) as connection, connection:
                connection.execute("PRAGMA secure_delete=ON")
                connection.execute("BEGIN IMMEDIATE")
                _rate_store(connection)
                connection.execute("DELETE FROM request_limits WHERE expires<=?", (now,))
                saved = connection.execute("SELECT expires,count FROM request_limits WHERE address_hash=? AND category=?", (digest, category)).fetchone()
                if saved is None and connection.execute("SELECT COUNT(*) FROM request_limits").fetchone()[0] >= 20000:
                    return False
                count = saved[1] + 1 if saved and saved[0] == expires else 1
                connection.execute("INSERT INTO request_limits VALUES(?,?,?,?) ON CONFLICT(address_hash,category) DO UPDATE SET expires=excluded.expires,count=excluded.count", (digest, category, expires, count))
                return count <= limit
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


def k1_depo_yolunu_dogrula(path, other_paths):
    """K1 v4 deposu öteki depolardan ayrı dosyada ve ayrı dizinde olmalı (yedek ve izin ayrı tutulur)."""
    selected = Path(path).absolute()
    for other in other_paths:
        if other is None:
            continue
        other = Path(other)
        if selected.resolve() == other.resolve() or selected.parent.resolve() == other.parent.resolve():
            raise ValueError("K1 v4 deposuna öteki depolardan ayrı özel bir dizin ayırın.")
    return selected


def uygulama(db_path, *, public_url, local_pilot=False, feed_db_path=None, sources=None, static_dir=None, clock=None,
             source_memory=None, objections_db_path=None, trusted_proxy_token=None, rate_db_path=None, rate_key=None,
             k1_db_path=None, k1_settings=None, k1_worker=False, k1_gate=None):
    origin = adresi_dogrula(public_url, local_pilot)
    if (k1_db_path is None) != (k1_settings is None):
        raise ValueError("K1 v4 katkı yolu için ayrı depo ve ayar dosyası birlikte verilmeli.")
    authority = urlsplit(origin).netloc
    feed_path = Path(feed_db_path) if feed_db_path else Path(db_path).with_name("community-feed.sqlite3")
    if feed_path.resolve() == Path(db_path).resolve():
        raise ValueError("Haber deposu ve topluluk deposu ayrı dosyalar olmalı.")
    if source_memory is not None:
        from radar.kaynak_hafizasi import KaynakHafizasi
        from radar.kaynak_hafizasi_api import ayri_depolari_dogrula
        if not isinstance(source_memory, KaynakHafizasi):
            raise ValueError("Kaynak hafızası hizmeti geçersiz.")
        ayri_depolari_dogrula(source_memory.path, db_path, feed_path)
    if objections_db_path is not None:
        from radar.arastirma_itiraz_api import itiraz_depo_yolunu_dogrula
        objections_db_path = itiraz_depo_yolunu_dogrula(objections_db_path, db_path, feed_path,
                                                       source_memory.path if source_memory is not None else None)
    store = ToplulukDeposu(db_path, clock=clock)
    requests_store = TalepDeposu(db_path, clock=clock)
    feed_depo = Depo(feed_path, sources if sources is not None else kaynaklari_oku())
    feed = GelistiriciAlani(feed_depo)
    static = Path(static_dir) if static_dir else Path(__file__).resolve().parent / "static"

    def asset_url(name):
        selected = static / name
        if not re.fullmatch(r"[a-z0-9-]+\.(?:js|css|svg)", name) or not selected.is_file():
            raise HTTPException(404, "Durağan dosya bulunamadı.")
        digest = hashlib.sha256(selected.read_bytes()).hexdigest()[:16]
        return f"/assets/{digest}/{name}"

    def static_document(name):
        markup = (static / name).read_text(encoding="utf-8")
        return re.sub(r'(?<=")/static/([a-z0-9-]+\.(?:js|css|svg))(?=")',
                      lambda match: asset_url(match[1]), markup)
    if rate_db_path is not None:
        other_stores = {Path(db_path).resolve(), feed_path.resolve()}
        if source_memory is not None:
            other_stores.add(Path(source_memory.path).resolve())
        if objections_db_path is not None:
            other_stores.add(Path(objections_db_path).resolve())
        if Path(rate_db_path).resolve() in other_stores:
            raise ValueError("İstek sınırı deposu ayrı dosya olmalı.")
    limiter = IstekSiniri(clock, db_path=rate_db_path, key=rate_key)
    k1_store = lifespan = None
    if k1_db_path is not None:
        from radar.katki_v4_api import isci_omru
        from radar.katki_v4_depo import KatkiDeposu
        k1_db_path = k1_depo_yolunu_dogrula(k1_db_path, (db_path, feed_path, objections_db_path, rate_db_path,
                                                         source_memory.path if source_memory is not None else None))
        k1_store = KatkiDeposu(k1_db_path, clock=clock, origin=origin)
        if k1_worker:
            lifespan = isci_omru(k1_store, k1_settings, k1_gate)
    app = FastAPI(title=f"{PRODUCT_NAME} Community", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.community = store
    app.state.requests = requests_store
    app.state.feed_depo = feed_depo

    @app.exception_handler(RequestValidationError)
    async def request_validation(request, exc):
        if request.url.path == "/api/community/objections" or request.url.path.startswith("/api/community/objections/"):
            return JSONResponse({"detail": "İtiraz isteği biçimi geçersiz."}, status_code=422)
        if request.url.path == "/api/community/source-memory" or request.url.path.startswith("/api/community/source-memory/"):
            return JSONResponse({"detail": "Kaynak kontrol isteği biçimi geçersiz."}, status_code=422)
        if request.url.path.startswith("/api/community/v4/"):
            # Katkı adayının özel metni hata yanıtına yansıtılmaz.
            return JSONResponse({"detail": "K1 isteğinin biçimi geçersiz.", "code": "invalid_request"}, status_code=422)
        if request.url.path == "/api/community/requests" or request.url.path.startswith("/api/community/requests/"):
            # Framework errors otherwise echo malformed private message bodies and query values.
            return JSONResponse({"detail": "Talep biçimi geçersiz; alanları kontrol edin."}, status_code=422)
        return await request_validation_exception_handler(request, exc)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status if isinstance(exc, ToplulukHatasi) else 422)

    @app.middleware("http")
    async def boundaries(request: Request, call_next):
        def reject(content, *, status_code, headers=None):
            safe_headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                            "Referrer-Policy": "no-referrer"}
            safe_headers.update(headers or {})
            return JSONResponse(content, status_code=status_code, headers=safe_headers)
        peer = request.client.host if request.client else ""
        proxy_header = request.headers.get("x-devtrellis-proxy-token", "")
        trusted = (not local_pilot and trusted_proxy_token and peer in {"127.0.0.1", "::1"} and
                   secrets.compare_digest(proxy_header.encode("utf-8"), trusted_proxy_token.encode("utf-8")))
        if proxy_header and not trusted:
            return reject({"detail": "Güvenilmeyen yönlendirme başlığı."}, status_code=403)
        if trusted:
            if any(len(request.headers.getlist(name)) != 1 for name in
                   ("x-devtrellis-proxy-token", "x-devtrellis-client-ip", "x-forwarded-proto")):
                return reject({"detail": "Vekil başlıkları tek değer taşımalı."}, status_code=403)
            client_address = request.headers.get("x-devtrellis-client-ip", "")
            try:
                if not client_address or "," in client_address or str(ipaddress.ip_address(client_address)) != client_address:
                    raise ValueError()
            except ValueError:
                return reject({"detail": "Vekil istemci adresi geçersiz."}, status_code=403)
            if request.headers.get("x-forwarded-proto") != "https":
                return reject({"detail": "Vekil HTTPS doğrulaması eksik."}, status_code=403)
            request.scope["client"] = (client_address, request.client.port)
            request.scope["scheme"] = "https"
        # Standart iletilmiş başlıklar hiçbir durumda kimlik kaynağı değildir.
        # Belirteç yoksa gerçek soket adresi ve ASGI şeması korunur; HTTPS uydurulmaz.
        ignored = {b"x-forwarded-for", b"x-real-ip", b"cf-connecting-ip", b"forwarded",
                   b"x-forwarded-proto", b"x-devtrellis-client-ip", b"x-devtrellis-proxy-token"}
        request.scope["headers"] = [(key, value) for key, value in request.scope["headers"] if key.lower() not in ignored]
        if hasattr(request, "_headers"):
            del request._headers
        if request.headers.get("host", "").lower() != authority:
            return reject({"detail": "Hizmet adresi eşleşmiyor."}, status_code=403)
        if not local_pilot and request.url.scheme != "https":
            return reject({"detail": "HTTPS gerekli."}, status_code=400)
        supplied_origin = request.headers.get("origin")
        if supplied_origin is not None and supplied_origin != origin:
            return reject({"detail": "Başka siteden erişim reddedildi."}, status_code=403)
        inbox_create = request.method == "POST" and request.url.path == "/api/community/inbox"
        inbox_control = ((request.method == "GET" and re.fullmatch(r"/api/community/inbox/[^/]+", request.url.path)) or
                         (request.method == "POST" and re.fullmatch(r"/api/community/inbox/[^/]+/withdraw", request.url.path)))
        # K1 v4 katkı uçları da yalnız yerel uygulamanın sunucu bağlantısıyla çağrılır (hesapsız kutu gibi).
        v4_create = k1_store is not None and request.method == "POST" and request.url.path == "/api/community/v4/submissions"
        v4_control = k1_store is not None and (
            (request.method == "GET" and re.fullmatch(r"/api/community/v4/submissions/[^/]+", request.url.path)) or
            (request.method == "POST" and re.fullmatch(r"/api/community/v4/submissions/[^/]+/withdraw", request.url.path)))
        inbox_call = inbox_create or inbox_control or v4_create or v4_control
        contact_create = request.method == "POST" and request.url.path == "/api/community/requests"
        event_write = request.method == "POST" and re.fullmatch(r"/api/community/cards/[^/]+/events", request.url.path)
        event_client = event_write and "x-devtrellis-client" in request.headers
        browser_view = event_write and "x-devtrellis-view" in request.headers
        if browser_view and (request.headers.getlist("x-devtrellis-view") != ["1"] or event_client or
                supplied_origin != origin or request.headers.get("sec-fetch-site") not in {None, "same-origin"} or
                any(name in request.headers for name in ("cookie", "authorization", "x-ufuk-csrf", "x-devtrellis-csrf"))):
            return reject({"detail": "Görüntüleme yalnız aynı hizmetten, çerezsiz ve hesap bağlamı olmadan kaydedilir."}, status_code=403)
        if event_client and (request.headers.get("x-devtrellis-client") != "1" or
                "origin" in request.headers or "cookie" in request.headers or
                any(name.startswith("sec-fetch-") for name in request.headers)):
            return reject({"detail": "İstemci etkileşimi tarayıcı oturumu veya bağlamı taşıyamaz."}, status_code=403)
        if inbox_call:
            # Yalnız yerel sunucu istemcisi: tarayıcı çerezlerini/bağlamını bu istisnaya taşıma.
            if ("origin" in request.headers or "cookie" in request.headers or
                    any(name.startswith("sec-fetch-") for name in request.headers) or
                    request.headers.get("x-devtrellis-inbox") != "1"):
                return reject({"detail": "Bu teslim yolu yerel uygulamanın sunucu bağlantısını gerektirir."}, status_code=403)
        mutation = request.method not in {"GET", "HEAD", "OPTIONS"}
        category = ("contact" if contact_create else "inbox" if inbox_create else "inbox-control" if inbox_control else
                    "v4-submit" if v4_create else "v4-control" if v4_control else
                    "event" if event_write else "login" if request.url.path == "/api/community/login" else "write" if mutation else "read")
        limit = {"contact": 4, "login": 6, "write": 40, "read": 240, "inbox": 4, "inbox-control": 30, "event": 60,
                 "v4-submit": k1_settings.kisi_dakika if k1_store is not None else 0, "v4-control": 30}[category]
        address = request.client.host if request.client else "unknown"
        if not limiter.izin(address, category, limit):
            return reject({"detail": "İstek sınırına ulaşıldı; bir dakika sonra tekrar deneyin."},
                                status_code=429, headers={"Retry-After": "60"})
        # Kişi başına sınır: anonim kimlik, kalıcı kipte gizli anahtarlı özeti tutulan istemci adresidir (ham adres saklanmaz).
        # Günlük kişi sınırı ve bütün katkıcıların saatlik toplamı ayar dosyasından gelir.
        if v4_create and not limiter.izin(address, "v4-submit-daily", k1_settings.kisi_gun, period=86400):
            return reject({"detail": "Günlük gönderim sınırına ulaşıldı; daha sonra tekrar deneyin.", "code": "rate_limited"},
                          status_code=429, headers={"Retry-After": "86400"})
        if v4_create and not limiter.izin("all", "v4-submit-total", k1_settings.tum_saat, period=3600):
            return reject({"detail": "Topluluk gönderim sınırına ulaşıldı; daha sonra tekrar deneyin.", "code": "rate_limited"},
                          status_code=429, headers={"Retry-After": "3600"})
        if inbox_create and not limiter.izin(address, "inbox-daily", 20, period=86400):
            return reject({"detail": "Günlük gönderim sınırına ulaşıldı; daha sonra tekrar deneyin."},
                                status_code=429, headers={"Retry-After": "86400"})
        if contact_create and (not limiter.izin(address, "contact-daily", 20, period=86400) or
                               not limiter.izin("all", "contact-total", 60)):
            return reject({"detail": "Talep sınırına ulaşıldı; daha sonra tekrar deneyin."},
                                status_code=429, headers={"Retry-After": "86400"})
        if event_write and (not limiter.izin(address, "event-daily", 2000, period=86400) or
                            not limiter.izin("all", "event-total", 600)):
            return reject({"detail": "Etkileşim sınırına ulaşıldı; daha sonra tekrar deneyin."},
                                status_code=429, headers={"Retry-After": "60"})
        if mutation:
            if not inbox_call and not event_client and (supplied_origin != origin or request.headers.get("sec-fetch-site") == "cross-site"):
                return reject({"detail": "Aynı hizmet adresinden gönderim gerekli."}, status_code=403)
            if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                return reject({"detail": "JSON gövdesi gerekli."}, status_code=415)
            try:
                length = int(request.headers.get("content-length", "-1"))
            except ValueError:
                length = -1
            if length < 0 or request.headers.get("transfer-encoding"):
                return reject({"detail": "İstek uzunluğu gerekli."}, status_code=411)
            if contact_create and length > 16384:
                return reject({"detail": "Talep en fazla 16 KiB olabilir."}, status_code=413)
            if length > BODY_LIMIT:
                return reject({"detail": "İstek en fazla 64 KiB olabilir."}, status_code=413)
            body = await request.body()
            if len(body) != length:
                return reject({"detail": "İstek uzunluğu tutarsız."}, status_code=400)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        public_read = request.method in {"GET", "HEAD"} and (
            request.url.path.startswith("/r/") or request.url.path in {"/sitemap.xml", "/feed.atom", "/llms.txt"} or
            request.url.path in {"/api/topics", "/api/community/cards", "/api/community/feed"} or
            re.fullmatch(r"/api/community/cards/[a-f0-9]{32}", request.url.path) or
            (k1_store is not None and (request.url.path in {"/api/community/capabilities", "/api/community/v4/records",
                                                            "/api/community/coverage", "/api/community/topics"} or
                                       re.fullmatch(r"/api/community/sources/[0-9a-f]{64}", request.url.path) or
                                       re.fullmatch(r"/api/community/v4/records/[a-f0-9]{32}(?:/v/[0-9]+|/agent)?", request.url.path))))
        if public_read:
            # Aynı URL moderatör alanları veya farklı dil döndürebilir.
            response.headers.append("Vary", "Cookie, Authorization, Accept-Language")
        anonymous_public = (public_read and response.status_code == 200 and
                            "cookie" not in request.headers and "authorization" not in request.headers and
                            "set-cookie" not in response.headers)
        response.headers["Cache-Control"] = ("public, max-age=31536000, immutable" if request.url.path.startswith("/assets/") and response.status_code == 200
            else "public, max-age=0, must-revalidate" if anonymous_public else "no-store")
        response.headers["Content-Security-Policy"] = ("default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        if not local_pilot:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        if anonymous_public and int(response.headers.get("content-length", "0")) <= 8 * 1024 * 1024:
            body = b"".join([chunk async for chunk in response.body_iterator])
            etag = 'W/"' + hashlib.sha256(body).hexdigest() + '"'
            response.headers["ETag"] = etag
            supplied = [value.strip().removeprefix("W/") for value in request.headers.get("if-none-match", "").split(",")]
            if "*" in supplied or etag.removeprefix("W/") in supplied:
                headers = dict(response.headers)
                headers.pop("content-length", None)
                headers.pop("content-type", None)
                return RawResponse(status_code=304, headers=headers, background=response.background)
            return RawResponse(content=b"" if request.method == "HEAD" else body,
                               status_code=response.status_code, headers=dict(response.headers), background=response.background)
        return response

    def csrf(token):
        return hashlib.sha256(("devtrellis-csrf:" + token).encode("utf-8")).hexdigest()

    def session(request, *, authenticated=True, mutate=False):
        token = request.cookies.get(COOKIE)
        state = store.oturum_oku(token)
        if state is None or (authenticated and state["user"] is None):
            raise HTTPException(401, "Oturum açın.")
        if mutate:
            current = request.headers.getlist("x-devtrellis-csrf")
            legacy = request.headers.getlist("x-ufuk-csrf")
            if len(current) > 1 or len(legacy) > 1 or (current and legacy and current != legacy):
                raise HTTPException(403, "Sayfayı yenileyip tekrar deneyin.")
            provided = (current or legacy or [""])[0]
            if not secrets.compare_digest(provided.encode(), csrf(token).encode()):
                raise HTTPException(403, "Sayfayı yenileyip tekrar deneyin.")
        return token, state["user"]

    if source_memory is not None:
        from radar.kaynak_hafizasi_api import kaynak_hafizasi_yollarini_ekle
        kaynak_hafizasi_yollarini_ekle(app, source_memory, session, limiter)

    if objections_db_path is not None:
        from radar.arastirma_itiraz_api import itiraz_yollarini_ekle
        itiraz_yollarini_ekle(app, objections_db_path, store, session, limiter, clock=clock)

    if k1_store is not None:
        from radar.katki_v4_api import katki_v4_yollarini_ekle
        katki_v4_yollarini_ekle(app, k1_store, k1_settings, session)

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
        return HTMLResponse(static_document("community.html"))

    @app.get("/assets/{digest}/{name}")
    def hashed_asset(digest: str, name: str):
        if digest != asset_url(name).split("/")[2]:
            raise HTTPException(404, "Durağan dosya bulunamadı.")
        media = "text/javascript" if name.endswith(".js") else "text/css" if name.endswith(".css") else "image/svg+xml"
        return FileResponse(static / name, media_type=media)

    def report_language(request):
        selected = request.query_params.get("lang")
        if selected in {"en", "tr"}:
            return selected
        return "tr" if request.headers.get("accept-language", "").lower().startswith("tr") else "en"

    def report(entry_id, version=None):
        card = public_corrections(store.yayini_oku(entry_id, version=version))
        if card["packet"]["kind"] != "research":
            raise HTTPException(404, "Araştırma bulunamadı.")
        return card

    @app.head("/r/{entry_id}.md")
    @app.get("/r/{entry_id}.md")
    def report_markdown(entry_id: str, version: int | None = Query(None, ge=1)):
        card = report(entry_id, version)
        return PlainTextResponse(markdown_rapor(card, origin), media_type="text/markdown; charset=utf-8")

    @app.head("/r/{entry_id}/v/{version}")
    @app.get("/r/{entry_id}/v/{version}")
    def report_version(entry_id: str, version: int, request: Request):
        if version < 1:
            raise HTTPException(404, "Sürüm bulunamadı.")
        if k1_store is not None and re.fullmatch(r"[0-9a-f]{32}", entry_id) and store.yayindaki_surum_var_mi(entry_id) is False:
            # Arama listesindeki v4 kaydının bağlantısı: v4 kayıtlarının HTML rapor sayfası yok, kamu kaydına yönlendirilir.
            if k1_store.kayit_oku(entry_id, version)[0] == 200:
                return RedirectResponse(f"/api/community/v4/records/{entry_id}/v/{version}", status_code=303)
        return HTMLResponse(sayfa(report(entry_id, version), origin, report_language(request), objections_enabled=objections_db_path is not None).replace(
            "/static/community-report.css", asset_url("community-report.css")).replace("/static/report-language.js", asset_url("report-language.js")))

    @app.head("/r/{entry_id}/{slug}")
    @app.get("/r/{entry_id}/{slug}")
    def report_current(entry_id: str, slug: str, request: Request):
        card = report(entry_id)
        if slug != kisa_ad(card["packet"]["title"]):
            return RedirectResponse(rapor_yolu(card) + ("?lang=" + report_language(request)), status_code=308)
        return HTMLResponse(sayfa(card, origin, report_language(request), objections_enabled=objections_db_path is not None).replace(
            "/static/community-report.css", asset_url("community-report.css")).replace("/static/report-language.js", asset_url("report-language.js")))

    @app.head("/sitemap.xml")
    @app.get("/sitemap.xml")
    def report_sitemap():
        return RawResponse(sitemap(list(yayindaki_arastirmalar(store)), origin), media_type="application/xml")

    @app.head("/feed.atom")
    @app.get("/feed.atom")
    def report_atom():
        return RawResponse(atom(list(yayindaki_arastirmalar(store)), origin), media_type="application/atom+xml")

    @app.head("/llms.txt")
    @app.get("/llms.txt")
    def report_llms():
        cards = list(yayindaki_arastirmalar(store))
        lines = ["# DevTrellis", "",
                 "Public, source-quoted research. Records published through the machine gate are not human-reviewed: a machine "
                 "found their quotes in the cited pages and did not check the claims. Read the exact version and sources before "
                 "citing.", ""]
        lines.extend(f'- [{card["packet"]["title"]}]({origin}{rapor_yolu(card, exact=True)})' for card in cards)
        return PlainTextResponse("\n".join(lines) + "\n")

    @app.get("/report-guide")
    def report_guide(request: Request):
        language = report_language(request)
        tr = language == "tr"
        return HTMLResponse(f'<!doctype html><html lang="{language}"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{"Rapor nasıl okunur" if tr else "How to read a report"} — DevTrellis</title><link rel="stylesheet" href="{asset_url("community-report.css")}"><script src="{asset_url("report-language.js")}" defer></script><main><a href="/">DevTrellis</a><p aria-label="Language"><a href="/report-guide?lang=en" lang="en" hreflang="en" data-report-language="en">English</a> · <a href="/report-guide?lang=tr" lang="tr" hreflang="tr" data-report-language="tr">Türkçe</a></p><h1>{"Rapor nasıl okunur" if tr else "How to read a report"}</h1><p>{"Raporun kapsamını, iddia dayanağını, son kontrol tarihini ve eksik kanıtları ayrı değerlendirin. Model adı veya sayaç doğruluk puanı değildir. Yalnız katkıcının yayımlamayı seçtiği kopyalar kamuya açıktır; makine denetimiyle yayımlanan kayıtlar bir insan tarafından incelenmedi; kayıt bunu açıkça yazar. Kaynakların lisansı rapor lisansından ayrıdır." if tr else "Check the report scope, claim evidence, last checked date and gaps separately. Model names and counters are not accuracy scores. Only copies their contributor chose to publish are public; records published through the machine gate were not reviewed by a person and say so. Source licenses remain separate from the report license."}</p><p><a href="/guidelines">{"Topluluk kuralları" if tr else "Community guidelines"}</a></p></main></html>')

    @app.get("/objections/new/{entry_id}/{version}")
    def objection_form(entry_id: str, version: int, request: Request):
        if objections_db_path is None:
            raise HTTPException(404, "İtiraz yolu kapalı.")
        card = report(entry_id, version)
        language = report_language(request)
        tr = language == "tr"
        esc = lambda value: html.escape(str(value), quote=True)
        labels = ({"title": "İddiaya itiraz et", "claim": "İddia veya kısa alıntı", "reason": "Gerekçe",
                   "source": "Kamu kaynağı (isteğe bağlı)", "source_title": "Kaynak başlığı", "source_url": "HTTPS bağlantısı",
                   "consent": "Bu özel itirazı inceleme kuyruğuna göndermeyi onaylıyorum.", "preview": "Önizle",
                   "send": "İtirazı gönder", "edit": "Düzenle", "privacy": "Özel metin yalnız moderatörce incelenir; kabul edilen düzeltme özeti ayrıca kamuya açılabilir. Gizli bilgi eklemeyin."} if tr else
                  {"title": "Dispute a claim", "claim": "Claim or short quote", "reason": "Reason",
                   "source": "Public source (optional)", "source_title": "Source title", "source_url": "HTTPS link",
                   "consent": "I agree to send this private objection for review.", "preview": "Preview",
                   "send": "Send objection", "edit": "Edit", "privacy": "Only moderators review the private text; an accepted correction summary may be public. Do not include secrets."})
        return HTMLResponse(f'''<!doctype html><html lang="{language}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{labels["title"]} — DevTrellis</title><link rel="stylesheet" href="{asset_url("community-report.css")}"><link rel="stylesheet" href="{asset_url("confirm-dialog.css")}"><script src="{asset_url("confirm-dialog.js")}" defer></script><script src="{asset_url("objection-form.js")}" defer></script></head><body><main data-research-id="{esc(entry_id)}" data-version="{version}"><a href="/r/{esc(entry_id)}/v/{version}?lang={language}">DevTrellis · {esc(card["packet"]["title"])}</a><h1>{labels["title"]}</h1><p>{labels["privacy"]}</p><form id="objection-form"><label>{labels["claim"]}<textarea name="claim" maxlength="800" required></textarea></label><label>{labels["reason"]}<textarea name="reason" maxlength="2000" required></textarea></label><fieldset><legend>{labels["source"]}</legend><label>{labels["source_title"]}<input name="source_title" maxlength="160"></label><label>{labels["source_url"]}<input name="source_url" type="url" pattern="https://.*"></label></fieldset><label><input name="consent" type="checkbox" required> {labels["consent"]}</label><button type="submit">{labels["preview"]}</button></form><section id="objection-preview" hidden><h2>{labels["preview"]}</h2><pre id="objection-preview-text"></pre><button id="objection-edit" type="button">{labels["edit"]}</button><button id="objection-send" type="button">{labels["send"]}</button></section><p id="objection-status" role="status" aria-live="polite"></p></main></body></html>''')

    @app.get("/objections/review")
    def objection_review(request: Request):
        if objections_db_path is None:
            raise HTTPException(404, "İtiraz yolu kapalı.")
        language = report_language(request)
        tr = language == "tr"
        title = "Özel itiraz kuyruğu" if tr else "Private objection queue"
        return HTMLResponse(f'''<!doctype html><html lang="{language}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title} — DevTrellis</title><link rel="stylesheet" href="{asset_url("community-report.css")}"><link rel="stylesheet" href="{asset_url("confirm-dialog.css")}"><script src="{asset_url("confirm-dialog.js")}" defer></script><script src="{asset_url("objection-review.js")}" defer></script></head><body><main><a href="/">DevTrellis</a><h1>{title}</h1><p>{"Yalnız moderatörler özel metni okuyabilir. Kabul için yayımlı düzeltici sürümü önce hazırlayın." if tr else "Only moderators may read private text. Prepare a published correcting version before acceptance."}</p><p id="objection-status" role="status"></p><div id="objection-list"></div><section id="objection-detail" hidden></section></main></body></html>''')

    @app.get("/static/community-report.css")
    def report_styles():
        return FileResponse(static / "community-report.css", media_type="text/css")

    @app.get("/about")
    @app.get("/guidelines")
    @app.get("/contribute")
    @app.get("/support")
    def community_pages():
        return HTMLResponse(static_document("community-pages.html"))

    @app.get("/requests")
    def request_management():
        # Sayfa kabuğu veri içermez; bütün talep okumaları moderatör yetkisi gerektirir.
        return HTMLResponse(static_document("community-requests.html"))

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

    @app.head("/api/topics")
    @app.get("/api/topics")
    def topics():
        return konulari_listele()

    @app.get("/api/community/session")
    def read_session(request: Request, response: Response):
        token = request.cookies.get(COOKIE)
        state = store.oturum_oku(token)
        if state is None:
            return {"authenticated": False, "user": None, "csrf_token": None}
        return session_view(token, state["user"])

    @app.post("/api/community/session")
    def open_session(request: Request, response: Response, payload: dict = Body(...)):
        alanlari_dogrula(payload, set())
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

    def public_corrections(card):
        # v4 kayıtlarının düzeltme defteri kendi `corrections` alanındadır; v1 itiraz deposu onları tanımaz.
        if objections_db_path is None or card["packet"]["kind"] != "research" or card.get("record_type") == "v4":
            return card
        corrections = app.state.objections.kamusal_ozetler(card["id"], card["version"], offset=0, limit=3)
        return {**card, "public_corrections": {key: corrections[key] for key in ("total", "offset", "limit", "items")}}

    @app.head("/api/community/cards")
    @app.get("/api/community/cards")
    def cards(request: Request, query: str = Query("", max_length=200), kind: str = "",
              offset: int = Query(0, ge=0, le=9223372036854775807), limit: int = Query(30, ge=1, le=100),
              view: Literal["full", "summary"] = "full", search_mode: Literal["all", "ranked"] = "all"):
        # K1 açıkken yayındaki v4 kayıtları kamu araştırma listesine ve aramasına aynı sıralamayla katılır (K1 ikinci dilim).
        result = store.listele(query=query, kind=kind, offset=offset, limit=limit, observer=observer(request), search_mode=search_mode,
                               ek_arama=k1_store.arama_kaynagi() if k1_store is not None else None)
        if objections_db_path is not None:
            result = {**result, "items": [public_corrections(item) for item in result["items"]]}
        return arama_ozeti(result) if view == "summary" else result

    @app.head("/api/community/cards/{entry_id}")
    @app.get("/api/community/cards/{entry_id}")
    def card(entry_id: str, request: Request, version: int | None = Query(None, ge=1, le=9223372036854775807)):
        return public_corrections(store.yayini_oku(entry_id, observer(request), version=version))

    @app.get("/api/community/cards/{entry_id}/packet")
    def packet(entry_id: str, version: int | None = Query(None, ge=1, le=9223372036854775807)):
        result = store.yayini_oku(entry_id, version=version)
        return JSONResponse(result["packet"], headers={"Content-Disposition": f'attachment; filename="devtrellis-{result["id"]}-v{result["version"]}.json"'})

    @app.post("/api/community/cards/{entry_id}/events")
    def event(entry_id: str, request: Request, payload: dict = Body(...)):
        if request.headers.get("x-devtrellis-view") == "1":
            if payload.get("kind") != "web_view":
                raise HTTPException(403, "Çerezsiz görüntüleme yolu yalnız web görüntülemesi kabul eder.")
        elif request.headers.get("x-devtrellis-client") != "1":
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

    @app.head("/api/community/feed")
    @app.get("/api/community/feed")
    def news(query: str = Query("", max_length=200), interest: str = Query("", max_length=40),
             offset: int = Query(0, ge=0, le=9223372036854775807), limit: int = Query(30, ge=1, le=100)):
        return {**feed.akis(query=query, interest=interest, offset=offset, limit=limit),
                "coverage": feed.gorunum()["coverage"]}

    return app
