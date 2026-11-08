"""İtiraz deposunu açık seçimle, özel fiş ve mevcut tarayıcı oturum sınırlarıyla sunar.

Varsayılan kapalıdır; ayrı ve özel bir dizin/SQLite dosyası gerekir. Mevcut
topluluk backup/restore komutu bu dosyayı kapsamaz: dağıtımda ayrı izinli yedek,
geri yükleme ve yedek saklama kararı olmadan etkinleştirilmemelidir. Depo özel
metni 90 günde temizler; yedek kopyaları otomatik temizlenmez. Kamuya onaylanan
düzeltme izi ve tekrar teslimi önleyen küçük kayıtlar kalır.

İstemci fişi yerelde secrets.token_urlsafe(32) eşdeğeriyle üretir ve saklar.
Fiş yalnız X-DevTrellis-Objection-Receipt başlığındadır; URL, sorgu, yanıt,
HTML, telemetri veya günlüğe eklenmez. HTTP erişim günlüğü CLI'da kapalıdır;
ters vekil de başlık/gövde kaydetmemelidir. Fişi bilen aynı özel kaydı yönetir.

POST gönderim yalnız özel pending kaydıdır. İnsan önizleme/onay arayüzünün
sorumluluğu çağırandadır; consent=true bir modelin insan izni aldığına kanıt
değildir. MCP aracı veya sunucu istemcisi istisnası sağlanmaz. Anonim oturum,
Origin ve CSRF yine gerekir. Kamu yayını ayrı, doğrulanmış moderatör kararıdır.
"""

from pathlib import Path

from fastapi import Body, HTTPException, Query, Request, Response

from radar.arastirma_itirazlari import ItirazDeposu, ItirazHatasi
from radar.gelistirici_alani import alanlari_dogrula
from radar.topluluk_depo import ToplulukHatasi


PREFIX = "/api/community/objections"
RECEIPT_HEADER = "X-DevTrellis-Objection-Receipt"
BODY_LIMIT = 16384


def itiraz_depo_yolunu_dogrula(path, community_path, feed_path, source_memory_path=None):
    """Yol/samefile çakışmasını DB veya dizin oluşturmadan reddeder."""
    if path is None:
        return None
    selected = Path(path).absolute()
    try:
        for other in (community_path, feed_path, source_memory_path):
            if other is None:
                continue
            other = Path(other)
            if selected.resolve() == other.resolve() or (selected.exists() and other.exists() and selected.samefile(other)):
                raise ItirazHatasi("İtiraz, topluluk, haber ve kaynak hafızası depoları ayrı dosyalarda olmalı.", 409)
            if selected.parent.resolve() == other.parent.resolve():
                raise ItirazHatasi("İtiraz deposuna diğer depolardan ayrı özel bir dizin ayırın.", 409)
    except OSError:
        raise ItirazHatasi("İtiraz deposunun ayrı yolu doğrulanamadı.", 503) from None
    return selected


def itiraz_yollarini_ekle(app, path, community, session, limiter, *, clock=None):
    """Yalnız uygulamanın doğruladığı oturumu kullanır; istemci user alanını kabul etmez."""
    objections = ItirazDeposu(path, published_report=lambda identifier, version:
                             community.yayini_oku(identifier, version=version), clock=clock)
    app.state.objections = objections

    def query_fields(request, allowed=()):
        pairs = list(request.query_params.multi_items())
        if any(key not in allowed for key, _ in pairs) or len(pairs) != len({key for key, _ in pairs}):
            raise HTTPException(422, "İtiraz isteğinin sorgu alanları geçersiz.")

    def fields(payload, allowed=(), required=()):
        try:
            alanlari_dogrula(payload, set(allowed), set(required))
        except ValueError:
            raise HTTPException(422, "İtiraz isteğinin alanları geçersiz.") from None

    def owner(request):
        query_fields(request)
        session(request, authenticated=False, mutate=True)
        if int(request.headers.get("content-length", "0")) > BODY_LIMIT:
            raise HTTPException(413, "İtiraz isteği en fazla 16 KiB olabilir.")
        receipts = request.headers.getlist(RECEIPT_HEADER)
        if len(receipts) != 1:
            raise HTTPException(401, "Başlıkta tek bir özel itiraz fişi gerekli.")
        return receipts[0]

    def moderator(request, *, mutate=False, allowed=()):
        query_fields(request, allowed)
        _, user = session(request, mutate=mutate)
        # Rol ve etkin hesap her istekte mevcut sunucu oturumundan okunur.
        if user.get("role") != "moderator":
            raise HTTPException(403, "Bu işlem moderatör yetkisi gerektirir.")
        return user

    def invoke(method, *args, **kwargs):
        try:
            return method(*args, **kwargs)
        except ToplulukHatasi as exc:
            # Depo hataları özel metin/fiş taşımayan sabit mesajlardır.
            raise HTTPException(exc.status, str(exc)) from None
        except (ValueError, OSError):
            raise HTTPException(503, "İtiraz işlemi tamamlanamadı; daha sonra deneyin.") from None

    @app.post(PREFIX)
    def submit(request: Request, response: Response, payload: dict = Body(...)):
        receipt = owner(request)
        address = request.client.host if request.client else "unknown"
        if not limiter.izin(address, "objection-create", 4):
            raise HTTPException(429, "İtiraz gönderim sınırına ulaşıldı.", headers={"Retry-After": "60"})
        if not limiter.izin(address, "objection-daily", 20, period=86400):
            raise HTTPException(429, "Günlük itiraz gönderim sınırına ulaşıldı.", headers={"Retry-After": "86400"})
        result = invoke(objections.gonder, receipt, payload)
        response.status_code = 200 if result["duplicate"] else 201
        return result

    @app.post(PREFIX + "/read")
    def read(request: Request, payload: dict = Body(...)):
        receipt = owner(request)
        fields(payload)
        return invoke(objections.oku, receipt)

    @app.post(PREFIX + "/withdraw")
    def withdraw(request: Request, payload: dict = Body(...)):
        receipt = owner(request)
        fields(payload, {"expected_revision"}, {"expected_revision"})
        return invoke(objections.geri_cek, receipt, payload["expected_revision"])

    @app.get(PREFIX + "/review")
    def review_list(request: Request, status: str = Query("", max_length=32),
                    offset: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=100)):
        user = moderator(request, allowed={"status", "offset", "limit"})
        return invoke(objections.inceleme_listele, user, status=status, offset=offset, limit=limit)

    @app.get(PREFIX + "/review/{objection_id}")
    def review_read(objection_id: str, request: Request):
        user = moderator(request)
        return invoke(objections.inceleme_oku, user, objection_id)

    @app.post(PREFIX + "/review/{objection_id}/decision")
    def decision(objection_id: str, request: Request, payload: dict = Body(...)):
        user = moderator(request, mutate=True)
        return invoke(objections.karar, user, objection_id, payload)

    @app.get(PREFIX + "/corrections/{research_id}/{version}")
    def corrections(research_id: str, version: int, request: Request,
                    offset: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=100)):
        query_fields(request, {"offset", "limit"})
        return invoke(objections.kamusal_ozetler, research_id, version, offset=offset, limit=limit)
