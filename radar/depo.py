"""Haberleri, kaynak revizyonlarını ve kullanıcı seçimlerini tek yerel depoda korur."""

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def simdi():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def tarih(value):
    if not value:
        return None
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if result.tzinfo is None:
            return None
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def yayin_tarihi(value):
    """Kaynağın yalnız gün verdiği tarihi saat veya saat dilimi uydurmadan korur."""
    if isinstance(value, str) and len(value) == 10:
        try:
            return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
        except ValueError:
            return None
    parsed = tarih(value)
    return parsed.isoformat() if parsed else None


def temiz_url(value):
    parts = urlsplit(str(value).strip())
    if parts.scheme not in {"https", "http"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("Kaynak bağlantısı güvenli HTTP adresi olmalı.")
    if len(value) > 4096 or any(ord(char) < 32 for char in value):
        raise ValueError("Kaynak bağlantısı geçersiz.")
    query = [(key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True)
             if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/",
                       urlencode(query), ""))


def kart_gorunumu(raw, names, editorial):
    """Ham kayda yalnız geçerli kaynak revizyonuna ait Türkçe değerlendirmeyi ekler."""
    item = dict(raw)
    item["source_name"] = names.get(item["source_id"], item["source_id"])
    item["is_archive"] = bool(item["is_archive"])
    item["selected"] = bool(item["selected"])
    item.pop("content_hash", None)
    note = editorial.get(item["id"])
    if note and note["source_revision"] == item["revision"]:
        item.update(source_title=item["title"], source_language=item["language"],
                    title=note["title_tr"], summary=note["summary_tr"], benefit=note["benefit_tr"],
                    limitation=note["limitation"], summary_status="editor_ozeti", language="tr",
                    editorial_date=note["created_at"])
    return item


class Depo:
    def __init__(self, path, sources):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.baglanti() as connection:
            connection.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sources (
                    id TEXT PRIMARY KEY, config TEXT NOT NULL,
                    initialized INTEGER NOT NULL DEFAULT 0,
                    last_checked_at TEXT, last_success_at TEXT, last_error TEXT,
                    next_due_at TEXT, failures INTEGER NOT NULL DEFAULT 0,
                    etag TEXT, modified TEXT
                );
                CREATE TABLE IF NOT EXISTS items (
                    id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id),
                    url TEXT NOT NULL UNIQUE, title TEXT NOT NULL, excerpt TEXT NOT NULL,
                    summary TEXT NOT NULL, summary_status TEXT NOT NULL,
                    topic TEXT NOT NULL, benefit TEXT NOT NULL, evidence_type TEXT NOT NULL,
                    language TEXT NOT NULL, published_at TEXT, event_at TEXT, updated_at TEXT,
                    first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1, content_hash TEXT NOT NULL,
                    is_archive INTEGER NOT NULL, score INTEGER NOT NULL,
                    feedback TEXT NOT NULL DEFAULT 'yok', snoozed_until TEXT
                );
                CREATE TABLE IF NOT EXISTS source_entries (
                    source_id TEXT NOT NULL REFERENCES sources(id), external_id TEXT NOT NULL,
                    item_id TEXT NOT NULL REFERENCES items(id),
                    PRIMARY KEY(source_id, external_id)
                );
                CREATE TABLE IF NOT EXISTS revisions (
                    item_id TEXT NOT NULL REFERENCES items(id), revision INTEGER NOT NULL,
                    seen_at TEXT NOT NULL, content TEXT NOT NULL,
                    PRIMARY KEY(item_id, revision)
                );
                CREATE TABLE IF NOT EXISTS feedback_events (
                    id INTEGER PRIMARY KEY, item_id TEXT NOT NULL REFERENCES items(id),
                    value TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL,
                    actor TEXT NOT NULL DEFAULT 'yerel_kullanici'
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, item_id TEXT NOT NULL UNIQUE REFERENCES items(id),
                    status TEXT NOT NULL DEFAULT 'bekliyor', owner TEXT, result TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_notes (
                    job_id TEXT NOT NULL REFERENCES jobs(id), version INTEGER NOT NULL,
                    source_revision INTEGER NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(job_id, version)
                );
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, kind TEXT NOT NULL,
                    entity_id TEXT NOT NULL, detail TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS editorial (
                    item_id TEXT PRIMARY KEY REFERENCES items(id), source_revision INTEGER NOT NULL,
                    title_tr TEXT NOT NULL, summary_tr TEXT NOT NULL, benefit_tr TEXT NOT NULL,
                    limitation TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS items_topic_date ON items(topic,published_at);
            """)
            for source in sources:
                connection.execute("INSERT INTO sources(id,config) VALUES(?,?) ON CONFLICT(id) "
                                   "DO UPDATE SET config=excluded.config",
                                   (source["id"], json.dumps(source, ensure_ascii=False)))
            from radar.kaynak_yonetimi import tablolari_hazirla as kaynak_tablolari
            from radar.kisisel import tablolari_hazirla as kisisel_tablolar
            from radar.zeka import tablolari_hazirla as zeka_tablolari
            kaynak_tablolari(connection)
            kisisel_tablolar(connection)
            zeka_tablolari(connection)

    @contextmanager
    def baglanti(self, write=False):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def kaynaklar(self, connection=None):
        if connection is None:
            with self.baglanti() as opened:
                return self.kaynaklar(opened)
        rows = connection.execute("SELECT s.*, (SELECT COUNT(DISTINCT e.item_id) FROM source_entries e "
                                  "WHERE e.source_id=s.id) AS item_count FROM sources s").fetchall()
        from radar.kaynak_yonetimi import tercihleri_uygula
        sources = [{**json.loads(row["config"]), **{key: row[key] for key in row.keys()
                    if key != "config"}} for row in rows]
        return tercihleri_uygula(sources, connection)

    def kaydet(self, source_id, incoming, editorial=False):
        added = changed = 0
        now = simdi()
        clock = tarih(now)
        with self.baglanti(write=True) as connection:
            row = connection.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
            if row is None:
                raise ValueError("Kaynak bulunamadı.")
            source = json.loads(row["config"])
            for raw in incoming:
                url = temiz_url(raw["url"])
                external_id = str(raw.get("external_id") or url)[:4096]
                title = str(raw.get("title") or "Başlık bulunamadı")[:500]
                excerpt = str(raw.get("excerpt") or "")[:2400]
                published = tarih(raw.get("published_at"))
                published_at = yayin_tarihi(raw.get("published_at"))
                updated_at = yayin_tarihi(raw.get("updated_at"))
                event_at = yayin_tarihi(raw.get("event_at"))
                existing = connection.execute("SELECT i.* FROM items i JOIN source_entries e "
                                              "ON i.id=e.item_id WHERE e.source_id=? AND e.external_id=?",
                                              (source_id, external_id)).fetchone()
                if existing is None:
                    existing = connection.execute("SELECT * FROM items WHERE url=?", (url,)).fetchone()
                if existing and existing["source_id"] == source_id:
                    # Liste veya besleme bu tur tarihi vermese de önceki açık kanıt kaybolmaz.
                    published_at = published_at or existing["published_at"]
                    updated_at = updated_at or existing["updated_at"]
                    event_at = event_at or existing["event_at"]
                snapshot = {"title": title, "excerpt": excerpt, "url": url,
                            "published_at": published_at, "updated_at": updated_at,
                            "event_at": event_at, "source_id": source_id}
                fingerprint = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
                if existing:
                    item_id = existing["id"]
                    # Aynı URL başka kaynakta görüldüyse asıl kaynağın kartını ezme.
                    if existing["source_id"] == source_id and existing["content_hash"] != fingerprint:
                        revision = existing["revision"] + 1
                        connection.execute("UPDATE items SET title=?,excerpt=?,summary=?,summary_status=?,"
                                           "published_at=?,updated_at=?,event_at=?,url=?,language=?,"
                                           "last_seen_at=?,revision=?,content_hash=? "
                                           "WHERE id=?", (title, excerpt, excerpt,
                                           "editor_karti" if editorial else "kaynak_metni" if excerpt else "ozet_bekliyor",
                                           published_at, updated_at, event_at, url, raw.get("language") or "bilinmiyor",
                                           now, revision, fingerprint, item_id))
                        connection.execute("INSERT INTO revisions VALUES(?,?,?,?)",
                                           (item_id, revision, now, json.dumps(snapshot, ensure_ascii=False)))
                        changed += 1
                    else:
                        connection.execute("UPDATE items SET last_seen_at=? WHERE id=?", (now, item_id))
                else:
                    item_id = uuid.uuid4().hex
                    recent = published is not None and timedelta(0) <= clock - published <= timedelta(days=14)
                    if published_at and len(published_at) == 10:
                        calendar_date = datetime.strptime(published_at, "%Y-%m-%d").date()
                        recent = timedelta(0) <= clock.date() - calendar_date <= timedelta(days=14)
                    archive = editorial or not row["initialized"] or not recent
                    words = (title + " " + excerpt).lower()
                    matches = sum(word in words for word in (
                        "windows", "cuda", "memory", "yerel", "sensor", "greenhouse", "sera",
                        "irrigation", "voice", "audio", "video", "robot", "agent", "automation"))
                    score = min(80, 45 + 5 * matches)
                    values = (item_id, source_id, url, title, excerpt, excerpt,
                              "editor_karti" if editorial else "kaynak_metni" if excerpt else "ozet_bekliyor",
                              source["topic"], raw.get("benefit") or source["benefit"],
                              raw.get("evidence_type") or "Birincil kaynak", raw.get("language") or "bilinmiyor",
                              published_at, event_at, updated_at, now, now, 1, fingerprint,
                              int(archive), score)
                    connection.execute("INSERT INTO items(id,source_id,url,title,excerpt,summary,summary_status,"
                                       "topic,benefit,evidence_type,language,published_at,event_at,updated_at,"
                                       "first_seen_at,last_seen_at,revision,content_hash,is_archive,score) "
                                       "VALUES(" + ",".join("?" for _ in values) + ")", values)
                    connection.execute("INSERT INTO revisions VALUES(?,?,?,?)",
                                       (item_id, 1, now, json.dumps(snapshot, ensure_ascii=False)))
                    added += 1
                connection.execute("INSERT OR IGNORE INTO source_entries VALUES(?,?,?)",
                                   (source_id, external_id, item_id))
            self.olay_yaz(connection, "icerik_alindi", source_id, {"yeni": added, "revizyon": changed})
        return {"added": added, "changed": changed}

    @staticmethod
    def olay_yaz(connection, kind, entity_id, detail):
        connection.execute("INSERT INTO audit(created_at,kind,entity_id,detail) VALUES(?,?,?,?)",
                           (simdi(), kind, entity_id, json.dumps(detail, ensure_ascii=False)))

    def tarama_sonucu(self, source_id, *, error=None, etag=None, modified=None):
        now = simdi()
        with self.baglanti(write=True) as connection:
            source = connection.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
            if source is None:
                raise KeyError(source_id)
            from radar.kaynak_yonetimi import tercihleri_uygula
            config = tercihleri_uygula([{**json.loads(source["config"]), **dict(source)}], connection)[0]
            failures = source["failures"] + 1 if error else 0
            hours = max(float(config["interval_hours"]), min(168, 2 ** min(failures, 7))) if error else float(config["interval_hours"])
            next_due = (tarih(now) + timedelta(hours=hours)).isoformat()
            if error:
                connection.execute("UPDATE sources SET last_checked_at=?,last_error=?,failures=?,"
                                   "next_due_at=? WHERE id=?", (now, str(error)[:600], failures, next_due, source_id))
            else:
                connection.execute("UPDATE sources SET initialized=1,last_checked_at=?,last_success_at=?,"
                                   "last_error=NULL,failures=0,next_due_at=?,etag=?,"
                                   "modified=? WHERE id=?", (now, now, next_due, etag, modified, source_id))
            self.olay_yaz(connection, "tarama_hata" if error else "tarama_tamam", source_id,
                          {"hata": str(error)[:600] if error else None})

    def geri_bildirim(self, item_id, value, note=""):
        if value not in {"yararli", "gereksiz", "sonra", "yok"}:
            raise ValueError("Geçersiz tercih.")
        if len(note) > 500:
            raise ValueError("Not en fazla 500 karakter olabilir.")
        until = (tarih(simdi()) + timedelta(days=7)).isoformat() if value == "sonra" else None
        with self.baglanti(write=True) as connection:
            if not connection.execute("SELECT 1 FROM items WHERE id=?", (item_id,)).fetchone():
                raise KeyError(item_id)
            connection.execute("UPDATE items SET feedback=?,snoozed_until=? WHERE id=?", (value, until, item_id))
            connection.execute("INSERT INTO feedback_events(item_id,value,note,created_at) VALUES(?,?,?,?)",
                               (item_id, value, note, simdi()))
            self.olay_yaz(connection, "tercih", item_id, {"value": value})

    def arastirma_sec(self, item_id):
        with self.baglanti(write=True) as connection:
            if not connection.execute("SELECT 1 FROM items WHERE id=?", (item_id,)).fetchone():
                raise KeyError(item_id)
            job = connection.execute("SELECT * FROM jobs WHERE item_id=?", (item_id,)).fetchone()
            if job:
                return dict(job)
            job_id, now = uuid.uuid4().hex, simdi()
            connection.execute("INSERT INTO jobs(id,item_id,created_at,updated_at) VALUES(?,?,?,?)",
                               (job_id, item_id, now, now))
            self.olay_yaz(connection, "arastirma_secildi", item_id, {"job_id": job_id})
            return dict(connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def editor_karti_yaz(self, card):
        """Türkçe değerlendirmeyi asıl kaynak metninden ayrı ve belirli revizyona bağlı saklar."""
        with self.baglanti(write=True) as connection:
            item = connection.execute("SELECT revision FROM items WHERE id=?", (card["id"],)).fetchone()
            if item is None:
                raise KeyError(card["id"])
            if card["source_revision"] != item["revision"]:
                raise ValueError("Kaynak değişti; Türkçe kart yeniden gözden geçirilmeli.")
            values = (card["id"], item["revision"], str(card["title_tr"])[:500],
                      str(card["summary_tr"])[:1600], str(card["benefit_tr"])[:800],
                      str(card.get("limitation", ""))[:800], simdi())
            connection.execute("INSERT OR REPLACE INTO editorial VALUES(?,?,?,?,?,?,?)", values)
            self.olay_yaz(connection, "editor_karti", card["id"], {"kaynak_revizyonu": item["revision"]})

    def gorunum(self):
        now = simdi()
        with self.baglanti() as connection:
            # Haberler, tercihler, işler ve kaynaklar aynı veritabanı anından okunur.
            connection.execute("BEGIN")
            from radar.katalog import kisisel_siralama_hazirla, KISISEL_SIRA_SQL
            settings = kisisel_siralama_hazirla(connection)
            workflow_pin = (" OR i.id IN (SELECT item_id FROM workflow_tasks) " if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='workflow_tasks'").fetchone() else '')
            items = [dict(row) for row in connection.execute("SELECT i.*, j.status AS job_status, "
                    "(j.id IS NOT NULL) AS selected FROM items i LEFT JOIN jobs j ON j.item_id=i.id "
                    "WHERE i.id IN (SELECT i.id FROM items i LEFT JOIN editorial e "
                    "ON e.item_id=i.id AND e.source_revision=i.revision ORDER BY " + KISISEL_SIRA_SQL +
                    " LIMIT 500) OR i.feedback='yararli' "
                    "OR (i.feedback='sonra' AND (julianday(i.snoozed_until) IS NULL "
                    "OR julianday(i.snoozed_until)>julianday(?))) OR j.id IS NOT NULL " + workflow_pin +
                    "ORDER BY i.is_archive ASC,i.published_at DESC,i.first_seen_at DESC,i.id ASC",
                    (now,))]
            jobs = [dict(row) for row in connection.execute("SELECT j.*,i.title FROM jobs j JOIN items i "
                                                           "ON j.item_id=i.id ORDER BY j.created_at DESC")]
            counts = connection.execute("SELECT COUNT(*) total,SUM(is_archive=0) new_count,"
                    "SUM(feedback!='yok') feedback_count,SUM(feedback='yararli') saved_count,"
                    "SUM(feedback='sonra' AND (julianday(snoozed_until) IS NULL "
                    "OR julianday(snoozed_until)>julianday(?))) later_count FROM items", (now,)).fetchone()
            editorial = {row["item_id"]: dict(row) for row in connection.execute("SELECT * FROM editorial")}
            sources = self.kaynaklar(connection)
            names = {source["id"]: source["name"] for source in sources}
            items = [kart_gorunumu(item, names, editorial) for item in items]
            from radar.zeka import ozetleri_ekle
            items = ozetleri_ekle(items, connection)
            from radar.kisisel import kisisel_sirala
            items = kisisel_sirala(items, settings, include_muted=True)
        return {"items": items, "sources": sources, "jobs": jobs,
                "stats": {"total": counts["total"], "new_count": counts["new_count"] or 0,
                          "selected": len(jobs), "source_count": len(sources),
                          "healthy_sources": sum(bool(source["last_success_at"]) and not source["last_error"] for source in sources),
                          "feedback_count": counts["feedback_count"] or 0,
                          "saved_count": counts["saved_count"] or 0, "later_count": counts["later_count"] or 0}}

    def revizyonlar(self, item_id):
        with self.baglanti() as connection:
            return [{**dict(row), "content": json.loads(row["content"])} for row in connection.execute(
                "SELECT * FROM revisions WHERE item_id=? ORDER BY revision DESC", (item_id,))]

    def disa_aktar(self):
        with self.baglanti() as connection:
            connection.execute("BEGIN")
            tables = [row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name<>'sources' ORDER BY name")]
            result = {}
            for table in tables:
                # Tablo adları kullanıcı girdisinden değil veritabanı şemasından gelir.
                identifier = table if table.replace("_", "").isalnum() else '"' + table.replace('"', '""') + '"'
                result[table] = [dict(row) for row in connection.execute("SELECT * FROM " + identifier)]
            result["sources"] = self.kaynaklar(connection)
        result["exported_at"] = simdi()
        result["schema_version"] = 2
        return result

    def yedekle(self, target):
        target = Path(target)
        if target.resolve() == self.path.resolve() or (target.exists() and target.samefile(self.path)):
            raise ValueError("Yedek dosyası asıl veritabanından farklı olmalı.")
        target.parent.mkdir(parents=True, exist_ok=True)
        with self.baglanti() as connection:
            backup = sqlite3.connect(target)
            try:
                connection.backup(backup)
            finally:
                backup.close()
