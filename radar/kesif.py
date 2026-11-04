"""Kamusal akademik dizinlerde konu sorgusu çalıştırır, gerçek sonuçları ve keşif geçmişini yerelde saklar."""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, unquote, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from radar.depo import tarih, temiz_url, yayin_tarihi
from radar.hizmet import TaramaKilidi
from radar.kaynak_havuzu import KONULAR, alan_adi_gecerli, katalogu_dogrula
from radar.konular import konu, konu_etiketleri
from radar.toplayici import CollectionError, kamusal_veri_oku, kaynaklari_yetkilendir


HAFTALIK_SAAT = 168
KONU_SINIRI = 20
TOPLAM_SURE = 300
KAYNAKCA_SINIRI = 3
SAGLAYICILAR = ("openalex", "semantic_scholar", "crossref")
API_HOSTLARI = ["api.openalex.org", "api.semanticscholar.org", "api.crossref.org"]
MADDE_HOSTLARI = ["doi.org", "openalex.org", "www.semanticscholar.org", "semanticscholar.org", "arxiv.org"]
KONU_ADLARI = konu_etiketleri()
SORGULAR = {topic: konu(topic)["query"] for topic in KONULAR}


def sorguyu_genislet(query):
    """Bilinen Türkçe konu terimlerini açık bir sözlükle arama terimlerine çevirir; genel konuya sessizce dönmez."""
    normalized = "".join(char for char in unicodedata.normalize("NFKD", query.lower().replace("ı", "i")) if not unicodedata.combining(char))
    dictionary = (("arastir", "deep research agents"), ("kaynak", "evidence citation"),
                  ("gelistir", "evaluation improvement"), ("hafiza", "memory"), ("bellek", "memory"),
                  ("sesli", "voice assistant"), ("otomasyon", "home automation"),
                  ("sera", "greenhouse"), ("sulama", "irrigation"), ("iklim", "climate control"),
                  ("robot", "robotics"), ("algila", "perception"), ("goruntu", "computer vision"),
                  ("video", "video"), ("ses", "audio speech"), ("kurgu", "editing"),
                  ("alinti", "citation"), ("dogrula", "verification"), ("ajan", "agents"))
    terms = []
    for token, english in dictionary:
        if re.search(r"\b" + re.escape(token), normalized):
            terms.extend(english.split())
    if not terms or not any(token in normalized for token in ("arastir", "kaynak", "gelistir", "hafiza", "bellek", "sesli", "otomasyon", "sera", "sulama", "iklim", "algila", "goruntu", "kurgu", "alinti", "dogrula", "ajan")):
        return query
    return " ".join(dict.fromkeys(terms))[:300]


class KesifMesgul(ValueError):
    """Aynı depoda başka keşif işi çalışırken yeni iş başlatılamaz."""


def tablolari_hazirla(connection):
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS kesif_runs (
            run_id TEXT PRIMARY KEY, status TEXT NOT NULL, topic TEXT, query TEXT,
            started_at TEXT NOT NULL, finished_at TEXT, result TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS kesif_matches (
            run_id TEXT NOT NULL REFERENCES kesif_runs(run_id), item_id TEXT NOT NULL REFERENCES items(id),
            provider TEXT NOT NULL, topic TEXT NOT NULL, query TEXT NOT NULL, identity TEXT NOT NULL,
            category TEXT NOT NULL, metadata TEXT NOT NULL,
            PRIMARY KEY(run_id,item_id,topic));
        CREATE TABLE IF NOT EXISTS kesif_identities (
            identity TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES items(id));
        CREATE TABLE IF NOT EXISTS kesif_schedule (
            id INTEGER PRIMARY KEY CHECK(id=1), next_due_at TEXT NOT NULL, last_run_id TEXT);
    """)


def kesif_kaynaklari():
    """Sorgu dizinlerini günlük besleme taramasından ayrı, görünür kaynaklar olarak tanımlar."""
    return [{"id": "kesif_" + topic, "name": KONU_ADLARI[topic] + " · akademik keşif", "topic": topic,
             "url": "https://openalex.org/", "fetch_url": "https://api.openalex.org/works?" + urlencode({"search": query}),
             "kind": "discovery", "interval_hours": HAFTALIK_SAAT, "enabled": True, "language": "bilinmiyor",
             "publisher": "OpenAlex / Semantic Scholar / Crossref", "source_type": "Araştırma",
             "branches": [KONU_ADLARI[topic], "Akademik yayınlar"], "benefit": "Konu sorularına ilişkin yayınları bulur; sonuçlar ayrıca değerlendirilmelidir.",
             "fetch_hosts": list(API_HOSTLARI), "item_hosts": list(MADDE_HOSTLARI),
             "discovered_from": "https://help.openalex.org/api/"}
            for topic, query in SORGULAR.items()]


def _metin(value, limit):
    if not isinstance(value, str):
        return ""
    soup = BeautifulSoup(value[:40000], "html.parser")
    for node in soup(["script", "style", "noscript"]):
        node.decompose()
    return " ".join(soup.get_text(" ", strip=True).split())[:limit]


def _adres(value):
    if not isinstance(value, str) or not value or len(value) > 4096:
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or parsed.port not in (None, 443) or parsed.username is not None
                or parsed.password is not None or not alan_adi_gecerli(parsed.hostname or "")
                or any(ord(char) <= 32 or ord(char) == 127 for char in value) or "\\" in value):
            return None
        return temiz_url(urlunsplit(parsed._replace(fragment="")))
    except (ValueError, TypeError):
        return None


def _doi(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    if value.lower().startswith(("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/")):
        value = unquote(urlsplit(value).path.lstrip("/"))
    if len(value) > 400 or not re.fullmatch(r"10\.\d{4,9}/[^\s\x00-\x1f]+", value, re.I) or "\\" in value:
        return None
    return value.lower()


def _kimlikler(url, doi=None):
    found = _doi(doi) or _doi(url)
    parsed = urlsplit(url)
    if not found and parsed.hostname in {"arxiv.org", "export.arxiv.org", "www.arxiv.org"}:
        match = re.fullmatch(r"/(?:abs|html|pdf)/(\d{4}\.\d{4,5})(?:v\d+)?(?:\.pdf)?/?", parsed.path)
        if match:
            found = "10.48550/arxiv." + match[1]
    return (["doi:" + found] if found else []) + ["url:" + temiz_url(url)]


def _ozet_dizini(value):
    if not isinstance(value, dict) or len(value) > 6000:
        return ""
    words = {}
    for word, positions in value.items():
        if not isinstance(word, str) or not isinstance(positions, list):
            continue
        for position in positions[:100]:
            if type(position) is int and 0 <= position < 6000:
                words[position] = word[:100]
    return _metin(" ".join(words[index] for index in sorted(words)), 4000)


def _tarih_parcalari(value):
    if not isinstance(value, dict):
        return None
    parts = value.get("date-parts")
    try:
        # Yalnız yıl verilmişse ay ve gün uydurulmaz.
        values = parts[0]
        if len(values) < 3 or any(type(part) is not int for part in values[:3]):
            return None
        return datetime(*values[:3]).date().isoformat()
    except (TypeError, ValueError, IndexError):
        return None


def _yayinlar(provider, payload):
    """Dizinlerin kamusal metadata alanlarını ortak, sınırlı kayıt biçimine dönüştürür."""
    if not isinstance(payload, dict):
        raise ValueError("Dizin beklenen JSON nesnesini döndürmedi.")
    if provider == "openalex":
        rows = payload.get("results")
    elif provider == "semantic_scholar":
        rows = payload.get("data")
    else:
        message = payload.get("message")
        rows = message.get("items") if isinstance(message, dict) else None
    if not isinstance(rows, list):
        raise ValueError("Dizin beklenen yayın listesini döndürmedi.")
    result = []
    for raw in rows[:KONU_SINIRI]:
        if not isinstance(raw, dict):
            continue
        landing = None
        if provider == "openalex":
            title, abstract, doi = raw.get("title"), _ozet_dizini(raw.get("abstract_inverted_index")), _doi(raw.get("doi"))
            location = raw.get("primary_location") or {}
            landing = _adres(location.get("landing_page_url")) if isinstance(location, dict) else None
            url = landing or _adres(raw.get("id"))
            published = yayin_tarihi(raw.get("publication_date"))
            publisher = _metin((location.get("source") or {}).get("display_name"), 150) if isinstance(location, dict) and isinstance(location.get("source"), dict) else ""
        elif provider == "semantic_scholar":
            external = raw.get("externalIds") or {}
            doi = _doi(external.get("DOI")) if isinstance(external, dict) else None
            title, abstract = raw.get("title"), _metin(raw.get("abstract"), 4000)
            url = _adres(raw.get("url"))
            published, publisher = yayin_tarihi(raw.get("publicationDate")), ""
        else:
            titles = raw.get("title")
            title = titles[0] if isinstance(titles, list) and titles else ""
            abstract, doi = _metin(raw.get("abstract"), 4000), _doi(raw.get("DOI"))
            resource = raw.get("resource") or {}
            primary = resource.get("primary") or {} if isinstance(resource, dict) else {}
            landing = _adres(primary.get("URL")) if isinstance(primary, dict) else None
            url = _adres(raw.get("URL")) or landing
            published = _tarih_parcalari(raw.get("published")) or _tarih_parcalari(raw.get("published-online"))
            publisher = _metin(raw.get("publisher"), 150)
        title = _metin(title, 500)
        if doi:
            url = "https://doi.org/" + quote(doi, safe="/():;._-")
        if not url or len(title) < 8:
            continue
        references = raw.get("referenced_works", []) if provider == "openalex" else []
        references = [value for value in references[:100] if isinstance(value, str) and re.fullmatch(r"https://openalex.org/W\d{1,20}", value)] if isinstance(references, list) else []
        result.append({"title": title, "url": url, "doi": doi, "excerpt": abstract, "published_at": published,
                       "landing_url": landing, "publisher": publisher, "referenced_works": references})
    return result


def _sorgu_adresi(provider, query, mode, now):
    since = (now - timedelta(days=30)).date().isoformat()
    limit = KONU_SINIRI if mode == "mixed" else KONU_SINIRI // 2
    if provider == "openalex":
        params = {"search": query, "per-page": limit, "page": 1,
                  "select": "id,doi,title,publication_date,abstract_inverted_index,primary_location,referenced_works"}
        if mode == "recent":
            params.update(filter="from_publication_date:" + since, sort="publication_date:desc")
        return "https://api.openalex.org/works?" + urlencode(params)
    if provider == "semantic_scholar":
        params = {"query": query, "limit": limit, "offset": 0,
                  "fields": "paperId,title,abstract,url,externalIds,publicationDate"}
        if mode == "recent":
            params["publicationDateOrYear"] = since + ":"
        return "https://api.semanticscholar.org/graph/v1/paper/search?" + urlencode(params)
    params = {"query.bibliographic": query, "rows": limit, "offset": 0,
              "select": "DOI,title,abstract,URL,published,published-online,publisher,resource"}
    if mode == "recent":
        params.update(filter="from-pub-date:" + since, sort="published", order="desc")
    return "https://api.crossref.org/works?" + urlencode(params)


class Kesif:
    def __init__(self, depo, *, fetcher=None, clock=None):
        self.depo, self.fetcher = depo, fetcher or kamusal_veri_oku
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.stop, self.lock, self.thread = threading.Event(), threading.Lock(), None
        self.process_lock = TaramaKilidi(depo.path.with_name(depo.path.name + ".kesif"))
        self.active_id = None
        self.schedule_error = None
        with depo.baglanti() as connection:
            tablolari_hazirla(connection)
            connection.execute("INSERT OR IGNORE INTO kesif_schedule VALUES(1,?,NULL)", (self._now().isoformat(),))
            for source in kesif_kaynaklari():
                connection.execute("INSERT OR IGNORE INTO sources(id,config) VALUES(?,?)",
                                   (source["id"], json.dumps(source, ensure_ascii=False)))
        configs = [source for source in depo.kaynaklar() if source.get("kind") == "discovery"]
        kaynaklari_yetkilendir(configs)
        # Başka süreç çalışıyorsa onun durumunu yarım kalmış diye değiştirme.
        if self.process_lock.al():
            try:
                with depo.baglanti(write=True) as connection:
                    for row in connection.execute("SELECT * FROM kesif_runs WHERE status='running'").fetchall():
                        record = json.loads(row["result"])
                        record.update(status="interrupted", finished_at=self._now().isoformat())
                        record["errors"].append({"provider": None, "topic": record["topic"], "message": "Uygulama kapanırken keşif yarım kaldı."})
                        connection.execute("UPDATE kesif_runs SET status=?,finished_at=?,result=? WHERE run_id=?",
                                           (record["status"], record["finished_at"], json.dumps(record, ensure_ascii=False), row["run_id"]))
            finally:
                self.process_lock.birak()

    def _now(self):
        value = self.clock()
        if isinstance(value, str):
            value = tarih(value)
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("Keşif saati saat dilimi taşımalı.")
        return value.astimezone(timezone.utc)

    def gorunum(self):
        with self.depo.baglanti() as connection:
            last = connection.execute("SELECT result FROM kesif_runs ORDER BY started_at DESC,rowid DESC LIMIT 1").fetchone()
            schedule = connection.execute("SELECT * FROM kesif_schedule WHERE id=1").fetchone()
            running = connection.execute("SELECT 1 FROM kesif_runs WHERE status='running'").fetchone() is not None
        return {"running": running, "last_run": json.loads(last[0]) if last else None,
                "schedule_error": self.schedule_error,
                "next_due_at": schedule["next_due_at"], "weekly_interval_hours": HAFTALIK_SAAT,
                "queries": dict(SORGULAR), "providers": list(SAGLAYICILAR), "max_items_per_topic": KONU_SINIRI,
                "max_reference_lookups": KAYNAKCA_SINIRI,
                "coverage": "Sorguyla bulunan sınırlı sonuçlar; bütün literatürün tarandığı anlamına gelmez."}

    def oku(self, run_id):
        with self.depo.baglanti() as connection:
            row = connection.execute("SELECT result FROM kesif_runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return json.loads(row[0])

    def _hazirla(self, topic, query, preserve_query=False):
        if type(preserve_query) is not bool:
            raise ValueError("Sorguyu koruma seçeneği doğru/yanlış olmalı.")
        if topic is not None and topic not in KONULAR:
            raise ValueError("Keşif konusu geçersiz.")
        if query is not None:
            if not isinstance(query, str) or not 3 <= len(query.strip()) <= 500 or any(ord(char) < 32 for char in query):
                raise ValueError("Keşif sorusu 3–500 karakterlik düz metin olmalı.")
            query, topic = query.strip(), topic or "ai"
        if self.stop.is_set():
            raise KesifMesgul("Keşif hizmeti kapanıyor.")
        if self.active_id or not self.process_lock.al():
            raise KesifMesgul("Başka bir keşif çalışıyor; tamamlanınca yeniden deneyin.")
        record = {"run_id": uuid.uuid4().hex, "status": "running", "topic": topic, "query": query,
                  "item_ids": [], "added": 0, "duplicates": 0, "errors": [], "searches": [], "relations": [],
                  "started_at": self._now().isoformat(), "finished_at": None}
        if preserve_query:
            record['preserve_query'] = True
        try:
            with self.depo.baglanti(write=True) as connection:
                connection.execute("INSERT INTO kesif_runs VALUES(?,?,?,?,?,?,?)",
                                   (record["run_id"], "running", topic, query, record["started_at"], None, json.dumps(record, ensure_ascii=False)))
            self.active_id = record["run_id"]
        except BaseException:
            self.process_lock.birak()
            raise
        return record

    def baslat(self, topic=None, query=None, *, preserve_query=False):
        with self.lock:
            record = self._hazirla(topic, query, preserve_query)
            try:
                self.thread = threading.Thread(target=self._yurut, args=(record,), daemon=True)
                self.thread.start()
            except BaseException:
                record.update(status="failed", finished_at=self._now().isoformat())
                self._sakla(record)
                self.active_id = None
                self.process_lock.birak()
                raise
        # İşçi aynı nesneyi değiştirse de çağırana başka işin sonucu verilmez.
        return {"started": True, "run_id": record["run_id"], "status": "running", "topic": record["topic"],
                "query": record["query"], "item_ids": []}

    def calistir(self, topic=None, query=None, *, preserve_query=False):
        with self.lock:
            record = self._hazirla(topic, query, preserve_query)
        self._yurut(record)
        return self.oku(record["run_id"])

    def _sakla(self, record):
        with self.depo.baglanti(write=True) as connection:
            connection.execute("UPDATE kesif_runs SET status=?,finished_at=?,result=? WHERE run_id=?",
                               (record["status"], record["finished_at"], json.dumps(record, ensure_ascii=False), record["run_id"]))

    def _bul(self, topic, query, mode, record, deadline):
        for provider in SAGLAYICILAR:
            if self.stop.is_set() or time.monotonic() >= deadline:
                return None
            try:
                url = _sorgu_adresi(provider, query, mode, self._now())
                options = {"deadline": deadline} if self.fetcher is kamusal_veri_oku else {}
                response = self.fetcher(url, API_HOSTLARI, stop_event=self.stop, **options)
                if response.status != 200:
                    raise CollectionError(f"Akademik dizin isteği tamamlanamadı (HTTP {response.status}).")
                works = _yayinlar(provider, json.loads(response.body))
                record["searches"].append({"provider": provider, "topic": topic, "query": query,
                                           "original_query": record["query"], "mode": mode, "count": len(works)})
                return provider, works
            except Exception as exc:
                # Yanıt gövdesi, başlıklar ve sorgunun kendisi hata metnine eklenmez.
                message = str(exc)[:400] if isinstance(exc, CollectionError) else "Akademik dizin yanıtı işlenemedi."
                record["errors"].append({"provider": provider, "topic": topic, "message": message})
                self._sakla(record)
            if self.stop.wait(1.0):
                return None
        return None

    def _kaydet(self, topic, query, provider, work, record):
        source_id = "kesif_" + topic
        identities = _kimlikler(work["url"], work["doi"])
        with self.depo.baglanti() as connection:
            item_id = next((row[0] for identity in identities if (row := connection.execute(
                "SELECT item_id FROM kesif_identities WHERE identity=?", (identity,)).fetchone())), None)
            if item_id is None:
                # Önceden beslemeden gelmiş DOI/URL kaydı da yeni kart sayılmaz.
                for old in connection.execute("SELECT id,url FROM items"):
                    if set(_kimlikler(old["url"])) & set(identities):
                        item_id = old["id"]
                        break
            row = connection.execute("SELECT config FROM sources WHERE id=?", (source_id,)).fetchone()
        source = json.loads(row[0])
        # Yalnız dizinin verdiği açık yayın adresleri eklenir. İndirme sırasında
        # robots, HTTPS sertifikası, genel IP ve DNS sabitlemesi yine zorunludur.
        for url in (work["url"], work.get("landing_url")):
            host = urlsplit(url).hostname if url else None
            if host and host not in source["item_hosts"] and len(source["item_hosts"]) < 128:
                source["item_hosts"].append(host)
        katalogu_dogrula([source])
        kaynaklari_yetkilendir([source])
        with self.depo.baglanti(write=True) as connection:
            connection.execute("UPDATE sources SET config=? WHERE id=?", (json.dumps(source, ensure_ascii=False), source_id))
        published = work["published_at"]
        age = (self._now().date() - datetime.fromisoformat(published).date()).days if published else None
        category = "recent" if age is not None and 0 <= age <= 14 else "foundation" if age is not None and age > 14 else "undated"
        if item_id is None:
            counts = self.depo.kaydet(source_id, [{"external_id": identities[0], "url": work["url"], "title": work["title"],
                                                "excerpt": work["excerpt"], "published_at": published, "language": "bilinmiyor",
                                                "evidence_type": "Akademik dizin · " + {"recent": "yakın tarihli yayın", "foundation": "geçmiş yayın", "undated": "yayın tarihi belirsiz"}[category]}])
            record["added"] += counts["added"]
            with self.depo.baglanti() as connection:
                item_id = connection.execute("SELECT item_id FROM source_entries WHERE source_id=? AND external_id=?", (source_id, identities[0])).fetchone()[0]
        else:
            record["duplicates"] += 1
        with self.depo.baglanti(write=True) as connection:
            for identity in identities:
                connection.execute("INSERT OR IGNORE INTO kesif_identities VALUES(?,?)", (identity, item_id))
            connection.execute("INSERT OR IGNORE INTO source_entries VALUES(?,?,?)", (source_id, identities[0], item_id))
            connection.execute("INSERT OR IGNORE INTO kesif_matches VALUES(?,?,?,?,?,?,?,?)",
                               (record["run_id"], item_id, provider, topic, query, identities[0], category, json.dumps(work, ensure_ascii=False)))
        if item_id not in record["item_ids"]:
            record["item_ids"].append(item_id)
        self._sakla(record)
        return item_id

    def _kaynakca(self, candidates, record, deadline):
        """Dizinin kaynakça bağlantılarından en çok üç yayını ayrıca okur; bunu sorgu sonucu saymaz."""
        seen = set()
        attempted = 0
        for topic, query, parent, references in candidates:
            for reference in references:
                if attempted >= KAYNAKCA_SINIRI or self.stop.is_set() or time.monotonic() >= deadline:
                    return
                if reference in seen:
                    continue
                seen.add(reference)
                attempted += 1
                try:
                    identifier = reference.rsplit("/", 1)[-1]
                    url = "https://api.openalex.org/works/" + identifier
                    options = {"deadline": deadline} if self.fetcher is kamusal_veri_oku else {}
                    response = self.fetcher(url, API_HOSTLARI, stop_event=self.stop, **options)
                    if response.status != 200:
                        raise CollectionError(f"Kaynakça yayını okunamadı (HTTP {response.status}).")
                    works = _yayinlar("openalex", {"results": [json.loads(response.body)]})
                    if not works:
                        raise CollectionError("Kaynakça yayını geçerli başlık ve adres taşımıyor.")
                    item_id = self._kaydet(topic, query, "openalex", works[0], record)
                    record["relations"].append({"parent_item_id": parent, "item_id": item_id, "relation": "reference", "provider": "openalex"})
                    self._sakla(record)
                except Exception as exc:
                    message = str(exc)[:400] if isinstance(exc, CollectionError) else "Kaynakça yayını işlenemedi."
                    record["errors"].append({"provider": "openalex", "topic": topic, "stage": "reference", "message": message})

    def _yurut(self, record):
        completed = failed = 0
        deadline = time.monotonic() + TOPLAM_SURE
        references = []
        try:
            topics = [record["topic"]] if record["topic"] else list(SORGULAR)
            for topic in topics:
                sources = {source["id"]: source for source in self.depo.kaynaklar()}
                if not sources["kesif_" + topic].get("enabled", True):
                    failed += 1
                    record["errors"].append({"provider": None, "topic": topic, "message": "Bu konunun akademik keşif kaynağı kapalı."})
                    continue
                query = (record["query"] if record.get('preserve_query') else sorguyu_genislet(record["query"])) if record["query"] else SORGULAR[topic]
                topic_completed = 0
                for mode in (["mixed"] if record["query"] else ["foundation", "recent"]):
                    found = self._bul(topic, query, mode, record, deadline)
                    if found is None:
                        failed += 1
                        continue
                    provider, works = found
                    completed += 1
                    topic_completed += 1
                    for work in works[:KONU_SINIRI if mode == "mixed" else KONU_SINIRI // 2]:
                        if self.stop.is_set():
                            break
                        item_id = self._kaydet(topic, query, provider, work, record)
                        if provider == "openalex" and work["referenced_works"]:
                            references.append((topic, query, item_id, work["referenced_works"]))
                self.depo.tarama_sonucu("kesif_" + topic, error=None if topic_completed else "Akademik sorgu sağlayıcılarından sonuç alınamadı.")
            self._kaynakca(references, record, deadline)
            record["status"] = "interrupted" if self.stop.is_set() else "partial" if failed and completed else "failed" if failed else "completed"
            if time.monotonic() >= deadline:
                record["errors"].append({"provider": None, "topic": record["topic"], "message": "Keşif için ayrılan toplam süre doldu."})
        except Exception:
            record["status"] = "partial" if record["item_ids"] else "failed"
            record["errors"].append({"provider": None, "topic": record["topic"], "message": "Keşif tamamlanamadı; yerel kayıt veya dizin yanıtı denetlenmeli."})
        finally:
            try:
                record["finished_at"] = self._now().isoformat()
                self._sakla(record)
                if record["query"] is None and record["topic"] is None:
                    with self.depo.baglanti(write=True) as connection:
                        connection.execute("UPDATE kesif_schedule SET next_due_at=?,last_run_id=? WHERE id=1",
                                           ((self._now() + timedelta(hours=HAFTALIK_SAAT)).isoformat(), record["run_id"]))
            finally:
                with self.lock:
                    self.active_id = None
                    self.process_lock.birak()

    def zamanli_calistir(self):
        """Uygulama açıkken haftalık keşfi başlatır; çağıran ayrı iş parçacığı kullanır."""
        while not self.stop.is_set():
            try:
                due = tarih(self.gorunum()["next_due_at"])
                if due is None or due <= self._now():
                    self.baslat()
                self.schedule_error = None
            except KesifMesgul:
                pass
            except Exception:
                self.schedule_error = "Haftalık keşif başlatılamadı; sonraki kontrolde yeniden denenecek."
            if self.stop.wait(900):
                break

    def kapan(self):
        self.stop.set()
        if self.thread and self.thread.is_alive() and self.thread is not threading.current_thread():
            self.thread.join(timeout=40)
