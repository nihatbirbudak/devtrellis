"""Kaynak hafızasının açıkça seçilen yapılandırmasını ve sınırlı topluluk HTTP yollarını kurar."""

import json
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Body, HTTPException, Request

from radar import toplayici
from radar.kaynak_hafizasi import EXTRACTOR_VERSION, REPRESENTATION, KaynakHafizasi, KaynakTanimi


PREFIX = "/api/community/source-memory"
CONFIG_LIMIT = 32768
CONFIG_ERROR = "Kaynak hafızası yapılandırması geçersiz; küçük kamu kaynak listesini ve alanlarını kontrol edin."
REQUEST_ERROR = "Kaynak kontrol isteği geçersiz; yalnız kontrol yaşı ve güncellik gereksinimi kabul edilir."


def tekil_alanlar(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(CONFIG_ERROR)
        result[key] = value
    return result


def yapilandirmayi_oku(path):
    """Sınırlı yerel dosyadan açık kamu kataloğunu okur; ağ isteği veya depo oluşturmaz."""
    try:
        with Path(path).open("rb") as handle:
            raw = handle.read(CONFIG_LIMIT + 1)
        if len(raw) > CONFIG_LIMIT:
            raise ValueError(CONFIG_ERROR)
        config = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=tekil_alanlar)
        if (not isinstance(config, dict) or set(config) != {"schema_version", "daily_limit", "sources"}
                or type(config["schema_version"]) is not int or config["schema_version"] != 1
                or type(config["daily_limit"]) is not int or not 1 <= config["daily_limit"] <= 1000
                or not isinstance(config["sources"], list) or not 1 <= len(config["sources"]) <= 20):
            raise ValueError(CONFIG_ERROR)
        sources = []
        for value in config["sources"]:
            if (not isinstance(value, dict) or not {"id", "url", "language"} <= value.keys()
                    or value.keys() - {"id", "url", "language", "representation", "extractor_version"}):
                raise ValueError(CONFIG_ERROR)
            source = KaynakTanimi(value["id"], value["url"], value["language"],
                                 value.get("representation", REPRESENTATION), value.get("extractor_version", EXTRACTOR_VERSION))
            if urlsplit(source.url).hostname not in toplayici.APPROVED_FETCH_HOSTS:
                raise ValueError(CONFIG_ERROR)
            sources.append(source)
        if len({source.id for source in sources}) != len(sources) or len({source.key for source in sources}) != len(sources):
            raise ValueError(CONFIG_ERROR)
        return sources, config["daily_limit"]
    except (OSError, UnicodeError, ValueError, TypeError, KeyError):
        raise ValueError(CONFIG_ERROR) from None


def ayri_depolari_dogrula(memory_path, community_path, feed_path):
    """Kaynak hafızasının var olan topluluk/haber deposunu kullanmasını engeller."""
    memory = Path(memory_path).resolve()
    for existing in (Path(community_path).resolve(), Path(feed_path).resolve()):
        if memory == existing or (memory.exists() and existing.exists() and memory.samefile(existing)):
            raise ValueError("Kaynak hafızası, topluluk ve haber depolarından ayrı bir dosya olmalı.")


def kaynak_hafizasini_hazirla(config_path, memory_path, community_path, feed_path, *, clock=None):
    """İki seçenek birlikte verilmedikçe yeni depo ve kaynak hafızası oluşturmaz."""
    if config_path is None and memory_path is None:
        return None
    if config_path is None or memory_path is None:
        raise ValueError("--source-memory-config ve --source-memory-db birlikte verilmeli.")
    ayri_depolari_dogrula(memory_path, community_path, feed_path)
    sources, daily_limit = yapilandirmayi_oku(config_path)
    return KaynakHafizasi(memory_path, sources, daily_limit=daily_limit, clock=clock)


def kaynak_hafizasi_yollarini_ekle(app, memory, session, limiter):
    """Kimlik bilgisi taşımayan okumayı ve aynı siteden açık kontrol isteğini ekler."""
    app.state.source_memory = memory

    def query_yok(request):
        if request.query_params:
            raise HTTPException(422, REQUEST_ERROR)

    def kaynak_var(source_id):
        if source_id not in memory.sources:
            raise HTTPException(404, "Kaynak bu küçük kamu pilotunda tanımlı değil.")

    @app.get(PREFIX)
    def catalog(request: Request):
        query_yok(request)
        return {"sources": [{"id": source.id, "url": source.url, "language": source.language,
                             "representation": source.representation, "extractor_version": source.extractor_version}
                            for source in memory.sources.values()], "model_calls": 0}

    @app.get(PREFIX + "/{source_id}")
    def read(source_id: str, request: Request):
        query_yok(request)
        kaynak_var(source_id)
        return memory.oku(source_id)

    @app.post(PREFIX + "/{source_id}/check")
    def check(source_id: str, request: Request, payload: dict = Body(...)):
        session(request, authenticated=False, mutate=True)
        query_yok(request)
        kaynak_var(source_id)
        if not isinstance(payload, dict) or payload.keys() - {"max_age_seconds", "freshness_required"}:
            raise HTTPException(422, REQUEST_ERROR)
        maximum_age = payload.get("max_age_seconds", 3600)
        required = payload.get("freshness_required", True)
        if type(maximum_age) is not int or not 60 <= maximum_age <= 86400 or type(required) is not bool:
            raise HTTPException(422, REQUEST_ERROR)
        address = request.client.host if request.client else "unknown"
        if not limiter.izin(address, "source-memory-check", 6):
            raise HTTPException(429, "Kaynak kontrol sınırına ulaşıldı; daha sonra tekrar deneyin.", headers={"Retry-After": "60"})
        return memory.kontrol_et(source_id, max_age_seconds=maximum_age, freshness_required=required)
