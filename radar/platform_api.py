"""Yerel geliştirici alanını ve dosyayla paylaşım önizlemesini HTTP üzerinden sunar."""

import secrets

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from radar.gelistirici_alani import GelistiriciAlani, PlatformSurumCatismasi


def router_olustur(depo, csrf_token, workflow=None):
    area = GelistiriciAlani(depo)
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
        except KeyError as exc:
            raise HTTPException(404, "İstenen kayıt bulunamadı.") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    guarded = [Depends(mutation_guard)]

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
