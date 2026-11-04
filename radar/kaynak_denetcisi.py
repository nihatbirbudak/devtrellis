"""Bağımsız sorgu örneklerini mevcut havuzla karşılaştırır; kaçırma aşamasını ve ölçümün sınırını açıkça kaydeder."""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import timedelta
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from radar.depo import temiz_url
from radar.kaynak_havuzu import alan_adi_gecerli
from radar.konular import KONULAR, konu
from radar.toplayici import CollectionError


DENETIM_SORGULARI = {topic: konu(topic)["audit_queries"] for topic in KONULAR}
KAPSAM_NOTU = ("Bağımsız sorgu örneklemi; ilgisi henüz teyitsiz. Bu sonuçlar bütün internetin "
              "kapsamını veya gerçekten önemli yayınların kaçırılma oranını ölçmez.")


def kamusal_adres(value):
    """Kimlik bilgisi ve yerel hedef içermeyen HTTPS adresini doğrular; DNS ağ katmanında denetlenir."""
    if not isinstance(value, str) or len(value) > 4096 or "\\" in value or any(ord(c) <= 32 or ord(c) == 127 for c in value):
        raise ValueError("Kaynak adresi geçersiz.")
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or parsed.port not in (None, 443) or parsed.username is not None
            or parsed.password is not None or not alan_adi_gecerli(parsed.hostname or "")
            or parsed.fragment or any(key.lower() in {"token", "access_token", "api_key", "apikey", "password", "secret", "authorization", "signature"}
                                      for key, _ in parse_qsl(parsed.query, keep_blank_values=True))):
        raise ValueError("Yalnız kimlik bilgisi içermeyen kamusal HTTPS kaynağı kullanılabilir.")
    return temiz_url(urlunsplit(("https", parsed.hostname, parsed.path or "/", parsed.query, "")))


def yayin_kimlikleri(url):
    """DOI, arXiv sürümleri ve izleme parametreleri için aynı yayını eşleştiren kimlikler üretir."""
    try:
        normalized = temiz_url(url)
        parsed = urlsplit(normalized)
    except (TypeError, ValueError):
        return set()
    identities = {"url:" + normalized.rstrip("/")}
    if parsed.hostname in {"doi.org", "dx.doi.org", "www.doi.org"}:
        doi = unquote(parsed.path.lstrip("/")).lower()
        if re.fullmatch(r"10\.\d{4,9}/[^\s\x00-\x1f]+", doi):
            identities.add("doi:" + doi)
    if parsed.hostname in {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}:
        match = re.fullmatch(r"/(?:abs|pdf|html)/(\d{4}\.\d{4,5})(?:v\d+)?(?:\.pdf)?/?", parsed.path)
        if match:
            identities.add("doi:10.48550/arxiv." + match[1])
    return identities


def kamusal_metin(value, limit=500):
    """Yanıt başlıklarını tek satırlık, sınırlı düz metin olarak saklar."""
    if not isinstance(value, str):
        return ""
    return " ".join(re.sub(r"<[^>]*>", " ", value[:10000]).split())[:limit]


class KaynakDenetcisi:
    def __init__(self, depo):
        self.depo = depo
        with depo.baglanti(write=True) as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS source_coverage_samples (
                run_id TEXT NOT NULL, topic TEXT NOT NULL, identity TEXT NOT NULL,
                observed_at TEXT NOT NULL, detail TEXT NOT NULL,
                PRIMARY KEY(run_id,topic,identity))""")

    def anlik_havuz(self):
        """Yeni aday eklenmeden önce aynı okuma anındaki kaynakları, görevleri ve geri bildirimleri alır."""
        with self.depo.baglanti() as connection:
            connection.execute("BEGIN")
            sources = [json.loads(row[0]) for row in connection.execute("SELECT config FROM sources")]
            items = [dict(row) for row in connection.execute("SELECT id,url,feedback,revision FROM items")]
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            tasks = []
            if "workflow_tasks" in tables:
                tasks = [dict(row) for row in connection.execute("SELECT * FROM workflow_tasks ORDER BY created_at DESC,rowid DESC")]
            identities = {}
            if "kesif_identities" in tables:
                identities = dict(connection.execute("SELECT identity,item_id FROM kesif_identities"))
        by_id = {item["id"]: item for item in items}
        for item in items:
            for identity in yayin_kimlikleri(item["url"]):
                identities[identity] = item["id"]
        current_tasks = {}
        for task in tasks:
            if task["item_id"] not in by_id or task["source_revision"] != by_id[task["item_id"]]["revision"] or task.get("request_id"):
                continue
            current_tasks.setdefault(task["item_id"], []).append(task)
        hosts = {host for source in sources if source.get("kind") != "discovery" for host in source.get("item_hosts", [])}
        return {"items": by_id, "identities": identities, "tasks": current_tasks, "hosts": hosts}

    def siniflandir(self, work, snapshot):
        identities = yayin_kimlikleri(work["url"])
        if work.get("doi"):
            identities |= yayin_kimlikleri(work["doi"])
        item_id = next((snapshot["identities"][identity] for identity in sorted(identities)
                        if identity in snapshot["identities"]), None)
        if item_id is None:
            host = urlsplit(work.get("landing_url") or work["url"]).hostname
            stage = "unknown" if not host or host in {"doi.org", "dx.doi.org", "openalex.org"} else "not_collected" if host in snapshot["hosts"] else "missing_source"
            return stage, None, "Örnek yayın başlangıç havuzunda bulunamadı; ilgi uygunluğu ayrıca değerlendirilmelidir."
        item, tasks = snapshot["items"][item_id], snapshot["tasks"].get(item_id, [])
        if item["feedback"] == "gereksiz" or any(task.get("decision") == "dismiss" for task in tasks):
            return "rejected", item_id, "Kayıt kullanıcı tarafından uygun görülmemiş; otomatik kaçırma sayılmaz."
        for task in tasks:
            try:
                result = json.loads(task.get("result_json") or "{}")
            except (ValueError, TypeError):
                result = {}
            if task.get("status") == "ready" and ((task.get("kind") == "interest" and result.get("fit") == "low")
                    or (task.get("kind") == "review" and result.get("research_decision") == "dismiss")):
                return "rejected", item_id, "İlgi elemesi kaydı uygun görmemiş; kararın doğruluğu ayrıca incelenebilir."
        if any(task.get("status") == "ready" for task in tasks) or item["feedback"] == "yararli":
            return "available", item_id, "Kayıt havuzda ve değerlendirilmiş."
        return "not_examined", item_id, "Kayıt havuzda; güncel tamamlanmış değerlendirme yok. Bu durum yararsızlık sayılmaz."

    def calistir(self, *, run_id, fetcher, now, topics, week_index=0):
        snapshot = self.anlik_havuz()
        report = {"observed_at": now.isoformat(), "method": "independent_query_sample", "note": KAPSAM_NOTU,
                  "samples": [], "queries": [], "errors": [], "counts": {}, "topics_checked": []}
        for topic in topics:
            query = DENETIM_SORGULARI[topic][week_index % 2]
            url = "https://api.openalex.org/works?" + urlencode({
                "search": query, "per-page": 5,
                "filter": "from_publication_date:" + (now - timedelta(days=30)).date().isoformat(),
                "select": "id,doi,title,publication_date,primary_location"})
            try:
                response = fetcher(url, ["api.openalex.org"])
                if response.status != 200:
                    raise CollectionError(f"Bağımsız sorgu okunamadı (HTTP {response.status}).")
                payload = json.loads(response.body)
                if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
                    raise CollectionError("Bağımsız dizin beklenen yayın listesini döndürmedi.")
                report["queries"].append({"topic": topic, "query": query, "provider": "openalex", "order": "relevance", "recent_days": 30})
                report["topics_checked"].append(topic)
                for row in payload["results"][:5]:
                    if not isinstance(row, dict):
                        continue
                    location = row.get("primary_location") or {}
                    landing = location.get("landing_page_url") if isinstance(location, dict) else None
                    try:
                        work_url = kamusal_adres(row.get("doi") or landing or row.get("id"))
                    except (ValueError, TypeError):
                        continue
                    title = kamusal_metin(row.get("title"))
                    if len(title) < 8:
                        continue
                    try:
                        landing = kamusal_adres(landing) if landing else None
                    except (ValueError, TypeError):
                        landing = None
                    work = {"topic": topic, "title": title, "url": work_url, "doi": row.get("doi"),
                            "landing_url": landing, "published_at": row.get("publication_date"),
                            "query": query, "provider": "openalex", "relevance": "unconfirmed"}
                    stage, item_id, reason = self.siniflandir(work, snapshot)
                    work.update(stage=stage, item_id=item_id, reason=reason)
                    identity = sorted(yayin_kimlikleri(work_url))[0]
                    if any(sample["topic"] == topic and sample["identity"] == identity for sample in report["samples"]):
                        continue
                    work["identity"] = identity
                    with self.depo.baglanti(write=True) as connection:
                        connection.execute("INSERT OR IGNORE INTO source_coverage_samples VALUES(?,?,?,?,?)",
                                           (run_id, topic, identity, now.isoformat(), json.dumps(work, ensure_ascii=False)))
                    report["samples"].append(work)
            except Exception as exc:
                message = str(exc)[:300] if isinstance(exc, CollectionError) else "Bağımsız sorgu tamamlanamadı; erişim veya süre sınırı olabilir."
                report["errors"].append({"topic": topic, "message": message})
        report["counts"] = dict(Counter(sample["stage"] for sample in report["samples"]))
        report["sample_count"] = len(report["samples"])
        report["status"] = "completed" if len(report["topics_checked"]) == len(topics) else "partial" if report["topics_checked"] else "failed"
        return report
