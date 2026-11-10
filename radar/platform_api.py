"""Yerel geliştirici alanını ve dosyayla paylaşım önizlemesini HTTP üzerinden sunar."""

import re
import secrets

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response

from radar.kaynak_hafizasi_istemci import KaynakHafizasiIstemcisi, KaynakHafizasiIstemciHatasi
from radar.gelistirici_alani import GelistiriciAlani, PlatformSurumCatismasi
from radar.topluluk_baglantisi import COMMUNITY_URL, ToplulukBaglantisi, ToplulukBaglantiHatasi, sozlesme_hatasi

ADRES_ANAHTARI = re.compile(r"^[0-9a-f]{64}$")
BAKIS_PARAMETRESI = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,79}$")


def router_olustur(depo, csrf_token, workflow=None, community=None, source_memory=None):
    area = GelistiriciAlani(depo)
    community = community if community is not None else ToplulukBaglantisi(depo, workflow)
    source_memory = source_memory if source_memory is not None else KaynakHafizasiIstemcisi()
    router = APIRouter(prefix="/api/platform")

    def mutation_guard(request: Request):
        supplied = request.headers.get("x-radar-csrf", "")
        if not secrets.compare_digest(supplied.encode(), csrf_token.encode()):
            raise HTTPException(403, "Sayfayı yenileyip tekrar deneyin.")

    def execute(operation, *args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except PlatformSurumCatismasi as exc:
            raise HTTPException(409, str(exc)) from exc
        except (ToplulukBaglantiHatasi, KaynakHafizasiIstemciHatasi) as exc:
            # Kaldırılmış/geri çekilmiş kayıt (uzak 410) ve var olmayan kimlik ya da sürüm (uzak 404) genel 502 değil,
            # aynı durumla iletilir (alpha.12 kabulü düşük 5; alpha.13a D4).
            durum = getattr(exc, "durum", None)
            raise HTTPException(durum if durum in (404, 410) else 502, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, "İstenen kayıt bulunamadı.") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    guarded = [Depends(mutation_guard)]

    @router.get("/community/session")
    def community_session():
        return {"csrf_token": csrf_token, "destination": COMMUNITY_URL,
                "search_views": ["full", "summary"], "search_modes": ["all", "ranked"],
                "source_memory": {"adapter_version": 1, "remote_enabled": "unknown"}}

    def source_query_guard(request):
        if request.query_params:
            raise HTTPException(422, "Kaynak yolu ek sorgu veya özel bağlam kabul etmez.")

    @router.get("/community/source-memory")
    def source_catalog(request: Request):
        source_query_guard(request)
        return execute(source_memory.katalog)

    @router.get("/community/source-memory/{source_id}")
    def source_read(source_id: str, request: Request):
        source_query_guard(request)
        return execute(source_memory.oku, source_id)

    @router.post("/community/source-memory/{source_id}/check", dependencies=guarded)
    def source_check(source_id: str, request: Request, payload: dict = Body(...)):
        source_query_guard(request)
        if payload.keys() - {"max_age_seconds", "freshness_required"}:
            raise HTTPException(422, "Kaynak kontrolü özel bağlam veya serbest URL kabul etmez.")
        return execute(source_memory.kontrol_et, source_id,
                       max_age_seconds=payload.get("max_age_seconds", 3600),
                       freshness_required=payload.get("freshness_required", True))

    @router.post("/community/search", dependencies=guarded)
    def community_search(payload: dict = Body(...)):
        return execute(community.ara, payload)

    # K1-V4 sözleşme uçları: topluluğun yanıtı değiştirilmeden geçer (200/404/410 gövdesi, 304, sözleşme hata gövdesi).
    def contract_passthrough(path, request, params=None):
        status, body, headers = community.sozlesme_oku(path, params=params, etag=request.headers.get("if-none-match"))
        if status == 304:
            return Response(status_code=304, headers=headers)
        return JSONResponse(body, status_code=status, headers=headers)

    def contract_query(request, allowed=()):
        params = {}
        for name, value in request.query_params.multi_items():
            if name not in allowed or name in params or not BAKIS_PARAMETRESI.fullmatch(value):
                return None
            params[name] = value
        return params

    @router.get("/community/coverage")
    def community_coverage(request: Request):
        if contract_query(request) is None:
            return JSONResponse(sozlesme_hatasi("invalid_request"), status_code=400)
        return contract_passthrough("/api/community/coverage", request)

    @router.get("/community/topics")
    def community_topics(request: Request):
        if contract_query(request) is None:
            return JSONResponse(sozlesme_hatasi("invalid_request"), status_code=400)
        return contract_passthrough("/api/community/topics", request)

    @router.get("/community/sources/{address_key}")
    def community_source(address_key: str, request: Request):
        params = contract_query(request, allowed=("product", "version"))
        if params is None or not ADRES_ANAHTARI.fullmatch(address_key):
            return JSONResponse(sozlesme_hatasi("invalid_request"), status_code=400)
        return contract_passthrough("/api/community/sources/" + address_key, request, params=params)

    @router.get("/community/cards/{entry_id}")
    def community_card(entry_id: str, version: int | None = Query(default=None, ge=1, le=9223372036854775807)):
        return execute(community.oku, entry_id, version)

    @router.get("/community/v4-records/{entry_id}")
    def community_v4_record(entry_id: str, version: int | None = Query(default=None, ge=1, le=9223372036854775807)):
        # v4 olduğu bilinen kayıt (arama/bulma sonucu): v1 ucuna gidilmeden okunur (alpha.12 kabulü düşük 4).
        return execute(community.v4_oku, entry_id, version)

    @router.post("/community/cards/{entry_id}/read", dependencies=guarded)
    def community_mcp_read(entry_id: str, payload: dict = Body(...)):
        if payload.keys() - {"version"}:
            raise HTTPException(422, "Yalnız seçilen araştırma okunur.")
        return execute(community.mcp_oku, entry_id, payload.get("version"))

    @router.post("/community/cards/{entry_id}/use", dependencies=guarded)
    def community_use(entry_id: str, payload: dict = Body(...)):
        return execute(community.kullanimi_bildir, entry_id, payload)

    @router.post("/community/cards/{entry_id}/import", dependencies=guarded)
    def community_import(entry_id: str, payload: dict = Body(...)):
        if payload:
            raise HTTPException(422, "Yalnız seçilen yayımlanmış kayıt alınır.")
        return execute(community.ice_aktar, entry_id)

    @router.post("/community/preview", dependencies=guarded)
    def community_preview(payload: dict = Body(...)):
        return execute(community.onizle, payload)

    @router.get("/community/review/{preview_id}", dependencies=guarded)
    def community_review(preview_id: str):
        return JSONResponse(execute(community.inceleme_ac, preview_id), headers={"Cache-Control": "no-store"})

    @router.post("/community/send", dependencies=guarded)
    def community_send(payload: dict = Body(...)):
        return execute(community.gonder, payload)

    @router.post("/community/discard", dependencies=guarded)
    def community_discard(payload: dict = Body(...)):
        return execute(community.vazgec, payload)

    @router.get("/community/submissions")
    def community_submissions(offset: int = Query(default=0, ge=0, le=9223372036854775807),
                              limit: int = Query(default=25, ge=1, le=100)):
        return execute(community.listele, offset=offset, limit=limit)

    @router.post("/community/submissions/{local_id}/refresh", dependencies=guarded)
    def community_refresh(local_id: str, payload: dict = Body(...)):
        if payload:
            raise HTTPException(422, "Yalnız seçilen gönderinin durumu okunur.")
        return execute(community.yonet, local_id)

    @router.post("/community/submissions/{local_id}/withdraw", dependencies=guarded)
    def community_withdraw(local_id: str, payload: dict = Body(...)):
        return execute(community.yonet, local_id, payload)

    @router.get("")
    def snapshot():
        return {**area.gorunum(), "csrf_token": csrf_token}

    @router.get("/feed")
    def feed(query: str = Query(default="", max_length=200), interest: str = Query(default="", max_length=40),
             offset: int = Query(default=0, ge=0, le=9223372036854775807), limit: int = Query(default=30, ge=1, le=100)):
        return execute(area.akis, query=query, interest=interest, offset=offset, limit=limit)

    @router.post("/settings", dependencies=guarded)
    def settings(payload: dict = Body(...)):
        return execute(area.ayarlari_kaydet, payload)

    @router.post("/entries", dependencies=guarded, status_code=201)
    def create(payload: dict = Body(...)):
        return execute(area.olustur, payload)

    @router.post("/research/{task_id}/draft", dependencies=guarded)
    def research_draft(task_id: str, payload: dict = Body(...)):
        if payload:
            raise HTTPException(422, "Taslak raporun kaydedilmiş içeriğinden oluşturulur.")
        if workflow is None:
            raise HTTPException(503, "Araştırma hizmeti hazır değil.")
        task = execute(workflow.oku, task_id)
        return execute(area.arastirmadan_taslak, task)

    @router.post("/entries/{entry_id}", dependencies=guarded)
    def update(entry_id: str, payload: dict = Body(...)):
        return execute(area.guncelle, entry_id, payload)

    @router.post("/entries/{entry_id}/archive", dependencies=guarded)
    def archive(entry_id: str, payload: dict = Body(...)):
        return execute(area.arsivle, entry_id, payload)

    @router.post("/entries/{entry_id}/preview", dependencies=guarded)
    def preview(entry_id: str, payload: dict = Body(...)):
        return execute(area.onizle, entry_id, payload)

    @router.get("/entries/{entry_id}/download")
    def download(entry_id: str, expected_version: int = Query(ge=1, le=9223372036854775807)):
        result = execute(area.onizle, entry_id, {"expected_version": expected_version})
        packet = result["packet"]
        return JSONResponse(packet, headers={"Content-Disposition": f'attachment; filename="devtrellis-{packet["kind"]}.json"'})

    @router.post("/import/preview", dependencies=guarded)
    def import_preview(payload: dict = Body(...)):
        return execute(area.ice_aktarma_onizle, payload)

    @router.post("/import", dependencies=guarded)
    def import_entry(payload: dict = Body(...)):
        return execute(area.ice_aktar, payload)

    return router
