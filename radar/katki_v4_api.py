"""K1 v4 katkı uçlarını topluluk uygulamasına ekler; varsayılan kapalıdır, ayrı depo ve ayar dosyası gerekir.

Katkıcı uçları v1 hesapsız kutusu gibi yalnız yerel uygulamanın sunucu bağlantısından gelir (tarayıcı çerezi ya da
Origin taşımaz; `X-DevTrellis-Inbox: 1` ve gönderime bağlı yönetim anahtarı `Authorization: Bearer …`). Kişi başına
gönderim sınırı uygulamanın istek sınırlayıcısıyla, gizli anahtarlı adres özeti üzerinden uygulanır (ara katmanda).
Moderatör uçları mevcut oturum, CSRF ve moderatör rolüyle çalışır. Kamu okuması yalnız yayımlanmış kayıtları döndürür.
"""

import re
import threading
from contextlib import asynccontextmanager

from fastapi import Body, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from radar import denetim
from radar.katki_v4_depo import GUNLUK
from radar.katki_v4 import (KatkiHatasi, ajan_gorunumu, ajan_metni, bakis_parametreleri, sozlesme_hatasi,
                            yetenekler)

PREFIX = "/api/community/v4"


def katki_v4_yollarini_ekle(app, depo, ayarlar, session):
    app.state.katki_v4 = depo
    app.state.katki_v4_ayarlari = ayarlar

    def sonuc(islem, *args, **kwargs):
        try:
            return JSONResponse(islem(*args, **kwargs))
        except KatkiHatasi as exc:
            return JSONResponse(exc.govde(), status_code=exc.status, headers=exc.basliklar())

    def yonetim_anahtari(request):
        degerler = request.headers.getlist("authorization")
        if len(degerler) != 1 or not degerler[0].startswith("Bearer "):
            raise KatkiHatasi("Geçerli gönderim yönetim anahtarı gerekli.", 401, code="capability_required")
        return degerler[0][7:]

    def moderator(request, *, mutate=False):
        _, user = session(request, mutate=mutate)
        if user.get("role") != "moderator":
            raise HTTPException(403, "Bu işlem moderatör yetkisi gerektirir.")
        return user

    @app.get("/api/community/capabilities")
    def capabilities():
        # Yapılandırmadaki ayar; moderatörün durdurma anahtarı kamuya açık edilmez (K1 kabulü bulgu 11).
        # Düzeltme turu 2 (kabul Y2): kip kapalıyken de depoda okuma ekli görünür kayıt varsa ekler ilan edilir.
        # 14a: uzantı kipi kapalıyken de `lineage` taşıyan görünür kayıt varsa o ek ilan edilir; `extensions` yalnız açıkken.
        return yetenekler(ayarlar.otomatik_yayin, ayarlar.okuma_eki,
                          okuma_ekli_kayit_var=not ayarlar.okuma_eki and depo.okuma_ekli_kayit_var(),
                          uzanti=ayarlar.uzanti, uzantili_kayit_var=not ayarlar.uzanti and depo.uzantili_kayit_var())

    @app.head("/api/community/sources/{address_key}")
    @app.get("/api/community/sources/{address_key}")
    def source_lookup(address_key: str, request: Request):
        # Sözleşme r1 §6: sunucu yalnız kendi deposunu okur; üçüncü taraf sayfa açmaz, okuma sayacı çalıştırmaz.
        parametreler = bakis_parametreleri(request.query_params.multi_items())
        if parametreler is None or not re.fullmatch(r"[0-9a-f]{64}", address_key):
            return JSONResponse(sozlesme_hatasi("invalid_request"), status_code=400)
        durum, govde = depo.bakis(address_key, *parametreler)
        return JSONResponse(govde, status_code=durum)

    @app.head("/api/community/coverage")
    @app.get("/api/community/coverage")
    def coverage(request: Request):
        if request.query_params:
            return JSONResponse(sozlesme_hatasi("invalid_request"), status_code=400)
        return depo.kapsam()

    @app.head("/api/community/topics")
    @app.get("/api/community/topics")
    def topics(request: Request):
        if request.query_params:
            return JSONResponse(sozlesme_hatasi("invalid_request"), status_code=400)
        return depo.konular()

    @app.post(PREFIX + "/submissions")
    def submit(request: Request, payload: dict = Body(...)):
        # Kapı kaynakları burada getirmez: ağsız denetimler hemen, ağlı denetim arka plan işçisinde (202).
        try:
            govde = depo.gonder(yonetim_anahtari(request), payload, ayarlar=ayarlar, on_denetim=denetim.denetle)
        except KatkiHatasi as exc:
            return JSONResponse(exc.govde(), status_code=exc.status, headers=exc.basliklar())
        return JSONResponse(govde, status_code=200 if govde["duplicate"] else 202)

    @app.get(PREFIX + "/submissions/status")
    def submission_recover(request: Request):
        return sonuc(lambda: depo.bul(yonetim_anahtari(request)))

    @app.get(PREFIX + "/submissions/{submission_id}")
    def submission_status(submission_id: str, request: Request):
        return sonuc(lambda: depo.oku(yonetim_anahtari(request), submission_id))

    @app.post(PREFIX + "/submissions/{submission_id}/withdraw")
    def submission_withdraw(submission_id: str, request: Request, payload: dict = Body(...)):
        return sonuc(lambda: depo.geri_cek(yonetim_anahtari(request), submission_id, payload))

    @app.head(PREFIX + "/records")
    @app.get(PREFIX + "/records")
    def records(offset: int = Query(0, ge=0, le=100000), limit: int = Query(30, ge=1, le=100)):
        return depo.kayitlari_listele(offset=offset, limit=limit)

    def kayit(kimlik, surum, bicim=None):
        durum, govde = depo.kayit_oku(kimlik, surum)
        if durum == 404:
            return JSONResponse({"detail": "Kayıt bulunamadı.", "code": "not_found"}, status_code=404)
        if durum == 200 and surum is not None and bicim is None:
            guncel = depo.guncel_surum(kimlik)
            if guncel is not None and guncel != govde.get("version"):
                # 14a: yerini bırakmış sürüm aynen döner (kayıt gövdesi değişmez); güncel sürüm başlıklarda.
                return JSONResponse(govde, headers={"X-DevTrellis-Superseded-By": str(guncel),
                                                    "Link": f'<{PREFIX}/records/{kimlik}>; rel="latest-version"'})
        if durum != 200 or bicim is None:
            return JSONResponse(govde, status_code=durum)
        if bicim == "agent":
            return JSONResponse(ajan_gorunumu(govde))
        # Sınır her yanıtta rastgele; önbelleğe alınmaz.
        return PlainTextResponse(ajan_metni(govde), headers={"Cache-Control": "no-store"})

    @app.head(PREFIX + "/records/{record_id}")
    @app.get(PREFIX + "/records/{record_id}")
    def record_current(record_id: str):
        return kayit(record_id, None)

    @app.head(PREFIX + "/records/{record_id}/agent")
    @app.get(PREFIX + "/records/{record_id}/agent")
    def record_agent(record_id: str):
        return kayit(record_id, None, "agent")

    @app.get(PREFIX + "/records/{record_id}/agent.txt")
    def record_agent_text(record_id: str):
        return kayit(record_id, None, "text")

    @app.head(PREFIX + "/records/{record_id}/v/{version}")
    @app.get(PREFIX + "/records/{record_id}/v/{version}")
    def record_version(record_id: str, version: int):
        if not 1 <= version <= 2 ** 62:
            return JSONResponse({"detail": "Kayıt bulunamadı.", "code": "not_found"}, status_code=404)
        return kayit(record_id, version)

    @app.get(PREFIX + "/review")
    def review_list(request: Request, status: str = Query("pending_review", max_length=32),
                    offset: int = Query(0, ge=0, le=100000), limit: int = Query(25, ge=1, le=100)):
        moderator(request)
        return sonuc(depo.inceleme_listele, status=status, offset=offset, limit=limit)

    @app.get(PREFIX + "/review/{submission_id}")
    def review_read(submission_id: str, request: Request):
        moderator(request)
        return sonuc(depo.inceleme_oku, submission_id)

    @app.post(PREFIX + "/review/{submission_id}/decision")
    def review_decision(submission_id: str, request: Request, payload: dict = Body(...)):
        user = moderator(request, mutate=True)
        return sonuc(depo.karar, user, submission_id, payload, ayarlar=ayarlar)

    @app.post(PREFIX + "/records/{record_id}/remove")
    def record_remove(record_id: str, request: Request, payload: dict = Body(...)):
        user = moderator(request, mutate=True)
        return sonuc(depo.kaldir, user, record_id, payload)

    @app.get(PREFIX + "/publishing")
    def publishing_state(request: Request):
        moderator(request)
        return depo.durum(ayarlar)

    @app.post(PREFIX + "/publishing/stop")
    def publishing_stop(request: Request, payload: dict = Body(...)):
        user = moderator(request, mutate=True)
        if not isinstance(payload, dict) or payload.keys() - {"reason"}:
            return JSONResponse({"detail": "Yalnız reason alanı gönderilebilir.", "code": "invalid_request"}, status_code=422)
        return sonuc(depo.durdur, f"moderator:{user['id']}", payload.get("reason"), ayarlar)

    @app.post(PREFIX + "/publishing/resume")
    def publishing_resume(request: Request, payload: dict = Body(...)):
        user = moderator(request, mutate=True)
        if payload:
            return JSONResponse({"detail": "Gövde boş nesne olmalı.", "code": "invalid_request"}, status_code=422)
        return sonuc(depo.devam_et, f"moderator:{user['id']}", ayarlar)


def isci_omru(depo, ayarlar, denetci=None):
    """Sunucu açıkken sırayı arka planda işleyen iş parçacığı; istek iş parçacığında kapı çalışmaz."""
    denetci = denetci or denetim.denetle

    @asynccontextmanager
    async def omur(app):
        dur = threading.Event()

        def dongu():
            while not dur.is_set():
                try:
                    depo.isle(ayarlar=ayarlar, denetci=denetci)
                except Exception as exc:
                    # Tek bir turun hatası işçiyi durdurmasın; kayıtlar kirası bitince yeniden alınır. Günlüğe yalnız tür.
                    GUNLUK.error("K1 işçi turu hatayla bitti (%s).", type(exc).__name__)
                dur.wait(ayarlar.isleme_araligi_saniye)

        isci = threading.Thread(target=dongu, name="devtrellis-k1-isci", daemon=True)
        isci.start()
        try:
            yield
        finally:
            dur.set()
            isci.join(timeout=5)

    return omur
