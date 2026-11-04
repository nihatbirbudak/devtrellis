"""Gazeteyi ve yerel kullanıcı seçimlerini yalnız aynı bilgisayarda sunar."""

import json
import secrets
import sqlite3
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from math import ceil

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from radar.baslangic import kartlari_ekle
from radar.bakim import Bakim
from radar.calisma_masasi import CalismaMasasi, SurumCatismasi
from radar.depo import Depo, simdi, tarih
from radar.hizmet import Toplama
from radar.katalog import kartlari_listele
from radar.kullanim_rehberi import rehber_html
from radar.kaynak_havuzu import kaynaklari_oku
from radar.kaynak_yonetimi import KaynakYonetimi, KaynakSurumCatismasi, YenidenDenemeBekliyor
from radar.kisisel import KisiselAlan
from radar.okuma import OkumaIsleri, OkumaMesgul, OkumaSiniri, OkumaHazirDegil
from radar.toplayici import kaynaklari_yetkilendir
from radar.zeka import ZekaIsleri, ZekaMesgul, ZekaSiniri, ZekaHazirDegil
from radar.arastirma_akisi import ArastirmaAkisi
from radar.kesif import Kesif, KesifMesgul
from radar.arastirma_talepleri import ArastirmaTalepleri
from radar.platform_api import router_olustur
from radar.marka import PRODUCT_NAME, VERSION
from radar.konular import KONULAR, konulari_listele
from radar.kaynak_ogrenme import KaynakOgrenme, KaynakOgrenmeCatismasi
from radar.kaynak_arastirmacisi import (KaynakArastirmacisi, KaynakArastirmaciMesgul,
                                      KaynakArastirmaciSiniri, KaynakAdayCatismasi)

ROOT = Path(__file__).resolve().parent
Konu = Literal[*KONULAR]


class Tercih(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: Literal["yararli", "gereksiz", "sonra", "yok"]
    note: str = Field(default="", max_length=500)


class Tarama(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str | None = Field(default=None, max_length=80)


class Secim(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IncelemeNotu(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str = Field(max_length=2000)
    status: Literal["bekliyor", "tamamlandi", "iptal"]
    expected_version: int = Field(ge=0, strict=True)
    source_revision: int = Field(ge=1, strict=True)


class SurumluSecim(Secim):
    expected_version: int = Field(ge=0, strict=True)


class KisiselAyarlar(SurumluSecim):
    topic_weights: dict[str, int]
    daily_limit: int = Field(ge=4, le=20, strict=True)
    keywords: list[str] = Field(max_length=20)
    muted_keywords: list[str] = Field(max_length=20)
    model_config = ConfigDict(extra="forbid", strict=True)


class Okundu(Secim):
    read: bool = Field(strict=True)


class TopluOkundu(Okundu):
    item_ids: list[str] = Field(min_length=1, max_length=100)


class Koleksiyon(Secim):
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=300)


class KoleksiyonDuzenleme(Koleksiyon, SurumluSecim):
    pass


class Arsivleme(SurumluSecim):
    archived: bool = Field(strict=True)


class KoleksiyonUyeleri(SurumluSecim):
    item_ids: list[str] = Field(min_length=1, max_length=100)
    remove: bool = Field(default=False, strict=True)


class KayitliArama(Secim):
    name: str = Field(min_length=1, max_length=60)
    query: str = Field(default="", max_length=200)
    topic: str | None = Field(default=None, max_length=20)
    source_id: str | None = Field(default=None, max_length=64)
    collection_id: str | None = Field(default=None, max_length=128)
    read_status: Literal["all", "read", "unread"] = "all"
    sort: Literal["relevance", "newest", "oldest"] = "relevance"


class YeniKaynak(Secim):
    name: str = Field(min_length=2, max_length=200)
    fetch_url: str = Field(max_length=4096)
    kind: Literal["rss", "atom"]
    topic: Konu
    publisher: str = Field(min_length=2, max_length=200)
    branches: list[str] = Field(min_length=1, max_length=10)
    interval_hours: int = Field(default=48, ge=24, le=720, strict=True)
    language: str = Field(default="bilinmiyor", max_length=20)
    source_type: str = Field(default="Teknik yayın", max_length=40)


class KaynakAyarlari(SurumluSecim):
    enabled: bool = Field(strict=True)
    interval_hours: int = Field(ge=24, le=720, strict=True)


class ZekaIstegi(Secim):
    kind: Literal["summary", "research"] = "summary"


class SaglayiciAyarlari(SurumluSecim):
    deep_provider: Literal["claude", "codex", "nvidia"]
    codex_model: str | None = Field(default=None, min_length=1, max_length=100)
    output_language: Literal["tr", "en"]


class AkisAyarlari(SurumluSecim):
    enabled: bool = Field(strict=True)
    daily_hour: int = Field(ge=0, le=23, strict=True)
    screen_limit: int = Field(ge=1, le=24, strict=True)
    auto_deep_limit: int = Field(ge=0, le=5, strict=True)
    auto_threshold: int = Field(ge=80, le=100, strict=True)


class ArastirmaKarari(SurumluSecim):
    decision: Literal['develop', 'watch', 'dismiss']


class KesifIstegi(Secim):
    topic: Konu | None = None
    query: str | None = Field(default=None, min_length=3, max_length=500)


class TalepButcesi(Secim):
    max_candidates: int = Field(default=10, ge=1, le=20, strict=True)
    max_deep_tasks: Literal[1] = 1


class ArastirmaTalebi(Secim):
    question: str = Field(min_length=5, max_length=500)
    topic: Konu
    need: str = Field(default='', max_length=300)
    source_url: str = Field(default='', max_length=2048)
    claim_text: str = Field(default='', max_length=1000)
    context_text: str = Field(default='', max_length=6000)
    scope: list[str] = Field(default_factory=list, max_length=5)
    budget: TalepButcesi = Field(default_factory=TalepButcesi)
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r'^[A-Za-z0-9_-]+$')


class KaynakOgrenmeAyarlari(SurumluSecim):
    enabled: bool = Field(strict=True)


class KaynakKoruma(SurumluSecim):
    protected: bool = Field(strict=True)


class KaynakAdayKarari(SurumluSecim):
    decision: Literal['trial', 'dismiss']


def uygulama(db_path=None, sources=None, seed=True, collector=None, port=8781, automatic_collection=False,
             automatic_backup=False, article_fetcher=None, ai_provider=None, credential_provider=None,
             automatic_workflow=False, discovery_fetcher=None, source_scout_fetcher=None):
    sources = sources if sources is not None else kaynaklari_oku()
    depo = Depo(db_path or ROOT.parent / "data" / "radar.sqlite3", sources)
    if seed:
        kartlari_ekle(depo)
    collection = Toplama(depo, collector)
    desk = CalismaMasasi(depo)
    backup = Bakim(depo)
    personal = KisiselAlan(depo)
    source_manager = KaynakYonetimi(depo)
    # Testlerin küçük kaynak örnekleri ağ izni vermez; kayıtlı kamusal katalog doğrulanır.
    kaynaklari_yetkilendir(kaynaklari_oku() + source_manager.ozel_kaynaklar())
    normal_limit = 80 if automatic_workflow or ai_provider is None else 20
    reading = OkumaIsleri(depo, fetcher=article_fetcher, daily_limit=normal_limit)
    try:
        intelligence = ZekaIsleri(depo, provider=ai_provider, credential_provider=credential_provider,
                                 daily_limit=normal_limit)
    except BaseException:
        reading.kapan()
        raise
    try:
        workflow = ArastirmaAkisi(depo, reading, intelligence, collection, automatic=automatic_workflow)
    except BaseException:
        intelligence.kapan()
        reading.kapan()
        raise
    research_requests = None
    try:
        discovery = Kesif(depo, fetcher=discovery_fetcher)
        try:
            research_requests = ArastirmaTalepleri(depo, workflow)
            source_learning = KaynakOgrenme(depo)
            source_scout = KaynakArastirmacisi(depo, source_learning, fetcher=source_scout_fetcher)
        except BaseException:
            if research_requests is not None:
                research_requests.kapan()
            discovery.kapan()
            raise
    except BaseException:
        workflow.kapan()
        intelligence.kapan()
        reading.kapan()
        raise
    nonce = secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifetime(app):
        try:
            if automatic_workflow:
                workflow.baslat()
                research_requests.zamanli_calistir(discovery)
            if automatic_collection:
                threading.Thread(target=collection.zamanli_calistir, daemon=True).start()
                threading.Thread(target=discovery.zamanli_calistir, daemon=True).start()
                threading.Thread(target=source_scout.zamanli_calistir, daemon=True).start()
            if automatic_backup:
                backup.baslat()
            yield
        finally:
            source_scout.kapan()
            research_requests.kapan()
            discovery.kapan()
            workflow.kapan()
            intelligence.kapan()
            reading.kapan()
            collection.kapan()
            backup.kapan()

    app = FastAPI(title=PRODUCT_NAME, version=VERSION, docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifetime)
    app.include_router(router_olustur(depo, nonce, workflow))
    app.state.depo = depo
    app.state.collection = collection
    app.state.backup = backup
    app.state.personal = personal
    app.state.source_manager = source_manager
    app.state.reading = reading
    app.state.ai = intelligence
    app.state.workflow = workflow
    app.state.discovery = discovery
    app.state.research_requests = research_requests
    app.state.source_learning = source_learning
    app.state.source_scout = source_scout

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"detail": "İstenen kayıt bulunamadı."}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        code, headers = 422, {}
        if isinstance(exc, (SurumCatismasi, KaynakSurumCatismasi, OkumaMesgul, ZekaMesgul, KesifMesgul,
                            KaynakOgrenmeCatismasi, KaynakArastirmaciMesgul, KaynakAdayCatismasi)):
            code = 409
        elif isinstance(exc, (OkumaSiniri, ZekaSiniri, YenidenDenemeBekliyor, KaynakArastirmaciSiniri)):
            code = 429
            retry = getattr(exc, "retry_after_seconds", 0)
            available = tarih(getattr(exc, "available_at", None) or getattr(exc, 'next_due_at', None))
            if available:
                retry = max(retry, ceil((available - tarih(simdi())).total_seconds()))
            headers["Retry-After"] = str(max(1, retry))
        elif isinstance(exc, (ZekaHazirDegil, OkumaHazirDegil)):
            code = 503
        return JSONResponse({"detail": str(exc)}, status_code=code, headers=headers)

    @app.middleware("http")
    async def sinir(request: Request, call_next):
        host = request.headers.get("host", "").lower()
        if host not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
            return JSONResponse({"detail": "DevTrellis yalnız yerel adresten açılabilir."}, status_code=403)
        origin = request.headers.get("origin")
        if origin and origin not in {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}:
            return JSONResponse({"detail": "Başka siteden erişim reddedildi."}, status_code=403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "Başka siteden erişim reddedildi."}, status_code=403)
        if request.method not in {"GET", "HEAD"}:
            if not secrets.compare_digest(request.headers.get("x-radar-csrf", "").encode(), nonce.encode()):
                return JSONResponse({"detail": "Sayfayı yenileyip tekrar deneyin."}, status_code=403)
            if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                return JSONResponse({"detail": "JSON gövdesi gerekli."}, status_code=415)
            try:
                size = int(request.headers.get("content-length", "-1"))
            except ValueError:
                size = -1
            if size < 0 or request.headers.get("transfer-encoding"):
                return JSONResponse({"detail": "İstek uzunluğu gerekli."}, status_code=411)
            body_limit = 16384 if request.url.path.startswith("/api/jobs/") and request.url.path.endswith("/note") else 4096
            if request.url.path == '/api/research-requests':
                body_limit = 8192
            if (request.url.path in {"/api/personal/settings", "/api/read/batch", "/api/sources"}
                    or request.url.path.startswith(("/api/collections/", "/api/platform/"))):
                body_limit = 32768
            if size > body_limit:
                return JSONResponse({"detail": "İstek çok büyük."}, status_code=413)
            body = await request.body()
            if len(body) != size:
                return JSONResponse({"detail": "İstek uzunluğu tutarsız."}, status_code=400)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = ("default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    def home():
        return FileResponse(ROOT / "static" / "index.html")

    @app.get("/platform")
    def platform_home():
        return FileResponse(ROOT / "static" / "platform.html")

    @app.get("/guide", response_class=HTMLResponse)
    def guide(language: Literal["en", "tr"] = "en", document: Literal["research", "installation"] = "research"):
        return rehber_html(language, document)

    @app.get("/api/health")
    def health():
        return {"application": "gelisme-radari", "product": PRODUCT_NAME, "status": "ok", "mode": "local", "version": VERSION,
                "automatic_research": automatic_workflow and workflow.ayarlar()['enabled'], "paid_models": None, "manual_ai": True,
                "provider_configured": intelligence.durum()["configured"]}

    @app.get("/api/topics")
    def topics_get():
        return konulari_listele()

    @app.get("/api/providers")
    def provider_settings():
        return intelligence.saglayici_ayarlari()

    @app.post("/api/providers")
    def save_provider_settings(payload: SaglayiciAyarlari):
        return intelligence.saglayici_ayarlarini_yaz(payload.model_dump())

    @app.get("/api/state")
    def state():
        view = depo.gorunum()
        workspace = personal.snapshot()
        ai_state = intelligence.durum()
        return {**view, "csrf_token": nonce, "collection": collection.gorunum(), "backup": backup.gorunum(),
                "personal": workspace, "reading": reading.durum(), "ai": ai_state, "workflow": workflow.gorunum(),
                "discovery": discovery.gorunum(), "research_requests": {"requests": research_requests.listele()},
                "source_learning": source_learning.gorunum(), "source_scout": source_scout.gorunum(),
                "settings": {"mode": "local", "automatic_research": automatic_workflow and workflow.ayarlar()['enabled'], "paid_models": None,
                             "manual_ai": True, "provider_configured": ai_state["configured"],
                             "telegram": False, "source_limit": len(view['sources']), "scan_batch_limit": 12,
                             "daily_card_limit": workspace["settings"]["daily_limit"],
                             "automatic_collection": automatic_collection, "automatic_backup": automatic_backup}}

    @app.get('/api/workflow')
    def workflow_state():
        return workflow.gorunum()

    @app.get('/api/source-learning')
    def source_learning_state():
        return {'learning': source_learning.gorunum(), 'scout': source_scout.gorunum()}

    @app.post('/api/source-learning/settings')
    def source_learning_settings(payload: KaynakOgrenmeAyarlari):
        return source_learning.ayarlari_kaydet(**payload.model_dump())

    @app.post('/api/source-learning/sources/{source_id}/protect')
    def source_learning_protect(source_id: str, payload: KaynakKoruma):
        return source_learning.koru(source_id, **payload.model_dump())

    @app.post('/api/source-learning/changes/{change_id}/undo')
    def source_learning_undo(change_id: str, payload: SurumluSecim):
        return source_learning.geri_al(change_id, expected_version=payload.expected_version)

    @app.post('/api/source-learning/scout/start', status_code=202)
    def source_scout_start(payload: Secim):
        if not source_scout.baslat():
            raise HTTPException(409, 'Kaynak araştırması zaten sürüyor.')
        return source_scout.gorunum()

    @app.post('/api/source-learning/candidates/{candidate_id}/decision')
    def source_candidate_decision(candidate_id: str, payload: KaynakAdayKarari):
        return source_scout.aday_karari(candidate_id, **payload.model_dump())

    @app.get('/api/discovery')
    def discovery_state():
        return discovery.gorunum()

    @app.post('/api/discovery', status_code=202)
    def discovery_start(payload: KesifIstegi):
        if not discovery.baslat(topic=payload.topic, query=payload.query):
            raise HTTPException(409, 'Bir kaynak keşfi zaten sürüyor; sonuçlanmasını bekleyin.')
        return discovery.gorunum()

    @app.get('/api/research-requests')
    def request_list():
        return {'requests': research_requests.listele()}

    @app.post('/api/research-requests', status_code=202)
    def request_create(payload: ArastirmaTalebi):
        result = research_requests.olustur(**payload.model_dump())
        workflow.baslat()
        research_requests.zamanli_calistir(discovery)
        return result

    @app.get('/api/research-requests/{request_id}')
    def request_get(request_id: str):
        return research_requests.oku(request_id)

    @app.post('/api/research-requests/{request_id}/cancel')
    def request_cancel(request_id: str, payload: SurumluSecim):
        return research_requests.iptal(request_id, expected_version=payload.expected_version)

    @app.post('/api/workflow/run', status_code=202)
    def workflow_run(payload: Secim):
        return workflow.gunu_iste()

    @app.post('/api/workflow/settings')
    def workflow_settings(payload: AkisAyarlari):
        return workflow.ayarlari_yaz(payload.model_dump(exclude={'expected_version'}), payload.expected_version)

    @app.post('/api/workflow/items/{item_id}/research', status_code=202)
    def workflow_research(item_id: str, payload: Secim):
        task = workflow.kuyruga_al(item_id)
        workflow.baslat()
        return task

    @app.get('/api/workflow/tasks/{task_id}')
    def workflow_task(task_id: str):
        return workflow.oku(task_id)

    @app.post('/api/workflow/tasks/{task_id}/retry', status_code=202)
    def workflow_retry(task_id: str, payload: SurumluSecim):
        task = workflow.tekrar(task_id, expected_version=payload.expected_version)
        workflow.baslat()
        return task

    @app.post('/api/workflow/tasks/{task_id}/seen')
    def workflow_seen(task_id: str, payload: Secim):
        return workflow.goruldu(task_id)

    @app.post('/api/workflow/tasks/{task_id}/decision')
    def workflow_decision(task_id: str, payload: ArastirmaKarari):
        return workflow.karar(task_id, payload.decision, expected_version=payload.expected_version)

    @app.get('/api/workflow/tasks/{task_id}/download')
    def workflow_download(task_id: str):
        return Response(workflow.dosya(task_id), media_type='text/plain; charset=utf-8',
                        headers={'Content-Disposition': 'attachment; filename="ufuk-arastirma.txt"'})

    @app.get('/api/workflow/tasks/{task_id}/handoff')
    def workflow_handoff(task_id: str):
        return Response(workflow.dosya(task_id, handoff=True), media_type='text/plain; charset=utf-8',
                        headers={'Content-Disposition': 'attachment; filename="ufuk-gelistirme-devri.txt"'})

    @app.get("/api/items")
    def items(query: str = Query(default="", max_length=200), topic: str = "all", source: str = Query(default="", max_length=64), include_archive: bool = True,
              limit: int = Query(default=100, ge=1, le=100), offset: int = Query(default=0, ge=0, le=1000000),
              collection_id: str = Query(default="", max_length=128),
              read_status: Literal["all", "read", "unread"] = "all",
              sort: Literal["relevance", "newest", "oldest"] = "relevance"):
        try:
            return kartlari_listele(depo, query=query, topic=topic, source=source, include_archive=include_archive,
                                   limit=limit, offset=offset, collection_id=collection_id, read_status=read_status, sort=sort)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.post("/api/items/{item_id}/feedback")
    def feedback(item_id: str, payload: Tercih):
        try:
            depo.geri_bildirim(item_id, payload.value, payload.note)
        except KeyError:
            raise HTTPException(404, "Kart bulunamadı.") from None
        return {"ok": True}

    @app.post("/api/items/{item_id}/research")
    def research(item_id: str, payload: Secim):
        try:
            job = depo.arastirma_sec(item_id)
            if automatic_workflow:
                workflow.kuyruga_al(item_id)
                workflow.baslat()
            return job
        except KeyError:
            raise HTTPException(404, "Kart bulunamadı.") from None

    @app.post("/api/collect", status_code=202)
    def collect(payload: Tarama):
        try:
            if not collection.baslat(payload.source_id):
                raise HTTPException(409, "Bir tarama zaten sürüyor.")
        except KeyError:
            raise HTTPException(404, "Kaynak bulunamadı.") from None
        return {"ok": True, "message": "Vadesi gelen kaynaklar kontrol ediliyor."}

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str):
        try:
            return desk.oku(job_id)
        except KeyError:
            raise HTTPException(404, "İnceleme bulunamadı.") from None

    @app.get("/api/personal")
    def personal_snapshot():
        return personal.snapshot()

    @app.post("/api/personal/settings")
    def personal_settings(payload: KisiselAyarlar):
        return personal.ayarlar_kaydet(payload.model_dump(exclude={"expected_version"}),
                                      expected_version=payload.expected_version)

    @app.post("/api/items/{item_id}/read")
    def mark_read(item_id: str, payload: Okundu):
        return personal.okundu_yaz(item_id, read=payload.read)

    @app.post("/api/read/batch")
    def mark_read_batch(payload: TopluOkundu):
        return {"items": personal.toplu_okundu_yaz(payload.item_ids, read=payload.read)}

    @app.post("/api/collections", status_code=201)
    def collection_create(payload: Koleksiyon):
        return personal.koleksiyon_olustur(**payload.model_dump())

    @app.post("/api/collections/{collection_id}")
    def collection_edit(collection_id: str, payload: KoleksiyonDuzenleme):
        return personal.koleksiyon_duzenle(collection_id, **payload.model_dump())

    @app.post("/api/collections/{collection_id}/archive")
    def collection_archive(collection_id: str, payload: Arsivleme):
        return personal.koleksiyon_arsivle(collection_id, **payload.model_dump())

    @app.post("/api/collections/{collection_id}/items")
    def collection_members(collection_id: str, payload: KoleksiyonUyeleri):
        operation = personal.koleksiyondan_cikar if payload.remove else personal.koleksiyona_ekle
        return operation(collection_id, payload.item_ids, expected_version=payload.expected_version)

    @app.post("/api/searches", status_code=201)
    def search_create(payload: KayitliArama):
        return personal.arama_kaydet(**payload.model_dump())

    @app.post("/api/searches/{search_id}/archive")
    def search_archive(search_id: str, payload: Arsivleme):
        return personal.arama_arsivle(search_id, **payload.model_dump())

    @app.post("/api/sources", status_code=201)
    def source_create(payload: YeniKaynak):
        source = source_manager.ekle(**payload.model_dump())
        kaynaklari_yetkilendir(source_manager.ozel_kaynaklar())
        return source

    @app.post("/api/sources/{source_id}")
    def source_edit(source_id: str, payload: KaynakAyarlari):
        return source_manager.kaydet(source_id, **payload.model_dump())

    @app.post("/api/sources/{source_id}/retry", status_code=202)
    def source_retry(source_id: str, payload: SurumluSecim):
        source_manager.yeniden_dene(source_id, expected_version=payload.expected_version)
        started = collection.baslat(source_id)
        return {"source": source_manager.oku(source_id), "started": started, "queued": not started}

    @app.get("/api/items/{item_id}/article")
    def article_get(item_id: str):
        return reading.oku(item_id)

    @app.post("/api/items/{item_id}/article", status_code=202)
    def article_start(item_id: str, payload: Secim):
        return reading.baslat(item_id)

    @app.get("/api/items/{item_id}/article/download")
    def article_download(item_id: str):
        saved = reading.oku(item_id)
        if not saved.get("body"):
            raise HTTPException(404, "İndirilebilecek kayıtlı makale metni yok.")
        lines = [saved.get("title") or saved["source_title"],
                 "Kaynak: " + (saved.get("source_url") or saved["current_url"]),
                 "Okunma zamanı: " + (saved.get("fetched_at") or "Bilinmiyor")]
        if saved.get("url") and saved["url"] != saved.get("source_url"):
            lines.append("Erişilen adres: " + saved["url"])
        if saved["stale"]:
            lines.append("Uyarı: Kaynak değişti; bu kayıt eski bir sürüme aittir.")
        if saved.get("truncated"):
            lines.append("Not: Kaynak metni uzunluk sınırı nedeniyle kısaltılmıştır.")
        content = "\n".join([*lines, "", saved["body"], ""])
        return Response(content, media_type="text/plain; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="ufuk-makale.txt"'})

    @app.get("/api/items/{item_id}/ai")
    def ai_get(item_id: str, kind: Literal["summary", "research"] = "summary"):
        return intelligence.oku(item_id, kind=kind)

    @app.post("/api/items/{item_id}/ai", status_code=202)
    def ai_start(item_id: str, payload: ZekaIstegi):
        return intelligence.baslat(item_id, kind=payload.kind)

    @app.get("/api/items/{item_id}/ai/download")
    def ai_download(item_id: str, kind: Literal["summary", "research"] = "summary"):
        saved = intelligence.oku(item_id, kind=kind)
        if not saved.get("result") or not saved.get("text"):
            raise HTTPException(404, "İndirilebilecek kayıtlı yapay zekâ sonucu yok.")
        label, filename = ("Türkçe yapay zekâ özeti", "ufuk-ozet.txt") if kind == "summary" else (
            "Yapay zekâ kaynak karşılaştırması", "ufuk-arastirma.txt")
        lines = [label, "Üretim zamanı: " + (saved.get("generated_at") or "Bilinmiyor"),
                 "Model: " + saved["model"]]
        if saved["stale"]:
            lines.append("Uyarı: Kaynak değişti; bu kayıt eski bir sürüme aittir.")
        lines.extend(["", saved["text"], "", "Kullanılan kaynaklar"])
        for source in saved["sources"]:
            lines.extend([source["title"], "Kaynak: " + source["url"],
                          "Yayıncı: " + source["publisher"], ""])
        return Response("\n".join(lines), media_type="text/plain; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @app.post("/api/jobs/{job_id}/note")
    def job_note(job_id: str, payload: IncelemeNotu):
        try:
            return desk.kaydet(job_id, **payload.model_dump())
        except KeyError:
            raise HTTPException(404, "İnceleme bulunamadı.") from None
        except SurumCatismasi as exc:
            raise HTTPException(409, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.get("/api/jobs/{job_id}/export")
    def job_export(job_id: str):
        try:
            content = desk.dosya(job_id)
        except KeyError:
            raise HTTPException(404, "İnceleme bulunamadı.") from None
        return Response(content, media_type="text/plain; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="ufuk-inceleme.txt"'})

    @app.get("/api/items/{item_id}/revisions")
    def revisions(item_id: str):
        return {"revisions": depo.revizyonlar(item_id)}

    @app.get("/api/backup")
    def backup_status():
        return backup.gorunum()

    @app.post("/api/backup")
    def backup_create(payload: Secim):
        try:
            if not backup.yedekle():
                raise HTTPException(409, "Bir yedekleme zaten sürüyor.")
        except (OSError, RuntimeError, sqlite3.Error):
            raise HTTPException(503, "Yedek hazırlanamadı. Önceki başarılı yedek korunuyor; disk alanını kontrol edin.") from None
        return backup.gorunum()

    @app.get("/api/backup/download")
    def backup_download():
        try:
            path = backup.dosya()
        except FileNotFoundError:
            raise HTTPException(404, "Henüz yedek yok. Önce yedek oluşturun.") from None
        return FileResponse(path, media_type="application/octet-stream", filename="ufuk-yedek.sqlite3")

    @app.get("/api/export")
    def export():
        return Response(json.dumps(depo.disa_aktar(), ensure_ascii=False, indent=2),
                        media_type="application/json", headers={"Content-Disposition": 'attachment; filename="ufuk-koleksiyon.json"'})

    @app.get("/api/sources/export")
    def source_export():
        return Response(json.dumps({"exported_at": simdi(), "sources": depo.kaynaklar()}, ensure_ascii=False, indent=2),
                        media_type="application/json", headers={"Content-Disposition": 'attachment; filename="ufuk-kaynak-kutuphanesi.json"'})

    @app.get("/api/sources/{source_id}")
    def source_get(source_id: str):
        return source_manager.oku(source_id)

    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    return app
