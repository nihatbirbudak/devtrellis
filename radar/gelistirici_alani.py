"""Geliştiricinin ilgi alanlarını, yerel taslaklarını ve seçerek paylaştığı kayıtları korur."""

import hashlib
import ipaddress
import json
import re
import unicodedata
import uuid
from urllib.parse import unquote, urlsplit, urlunsplit

from radar.depo import simdi, tarih
from radar.proje_profilleri import profiller
from radar.konular import KONULAR


TEMEL_ALANLAR = {"kind", "title", "summary", "body", "url", "tags", "sources", "author", "license"}
KAYIT_ALANLARI = TEMEL_ALANLAR | {"provenance", "lineage"}
PAKET_ALANLARI = TEMEL_ALANLAR | {"schema", "schema_version", "created_at", "updated_at"}
PAKET_SINIRI = 32768
KONU_ADLARI = {profile["topic"]: profile["label"] for profile in profiller()}
PAYLASIM_NOTU = ("Yalnız önizlemede gördüğünüz kayıt paylaşılır. Kişisel ayarlarınız ve diğer kayıtlarınız "
                "pakete eklenmez. Metindeki özel bilgileri kendiniz kontrol edin; yazar adı doğrulanmış değildir. "
                "Model bilgisi ve kanıt durumu paylaşan kişinin beyanıdır; bağımsız doğrulama değildir.")


class PlatformSurumCatismasi(ValueError):
    """Başka pencerede değişmiş kaydın üzerine yazılmasını engeller."""


def alanlari_dogrula(value, allowed, required=None):
    if not isinstance(value, dict) or value.keys() - allowed or (required or set()) - value.keys():
        raise ValueError("İstek alanları eksik veya tanınmayan alan içeriyor.")


def metin(value, name, limit, *, required=False, multiline=False):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"{name} en fazla {limit} karakterlik metin olmalı.")
    allowed_controls = {"\n", "\r", "\t"} if multiline else set()
    if any(unicodedata.category(char) == "Cc" and char not in allowed_controls for char in value):
        raise ValueError(f"{name} geçersiz kontrol karakteri içeriyor.")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{name} boş bırakılamaz.")
    return value


def guvenli_url(value, *, required=False):
    """Kamusal HTTP bağlantısını ağ sorgusu yapmadan doğrular; hiçbir adresi ziyaret etmez."""
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("Bağlantı en fazla 2048 karakterlik metin olmalı.")
    # urlsplit bazı kontrol karakterlerini kendiliğinden kaldırdığı için önce ham metin denetlenir.
    if any(ord(char) < 33 or ord(char) == 127 for char in value) or "\\" in value:
        raise ValueError("Bağlantı boşluk veya kontrol karakteri içeremez.")
    decoded = unquote(value)
    if any(ord(char) < 32 or ord(char) == 127 for char in decoded) or "\\" in decoded:
        raise ValueError("Bağlantı geçersiz kodlanmış karakter içeriyor.")
    if not value and not required:
        return ""
    try:
        parts = urlsplit(value)
        host = parts.hostname
        port = parts.port
    except ValueError as exc:
        raise ValueError("Bağlantı geçersiz.") from exc
    if parts.scheme.lower() not in {"http", "https"} or not host or parts.username is not None or parts.password is not None:
        raise ValueError("Bağlantı kullanıcı bilgisi içermeyen HTTP veya HTTPS adresi olmalı.")
    host = host.rstrip(".").lower()
    if not host or "%" in host or host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".lan", ".home")):
        raise ValueError("Yerel ağ adresleri paylaşılamaz.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # Tarayıcıların sayısal IPv4 kısaltmalarını yerel adrese dönüştürmesini engeller.
        if re.fullmatch(r"(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|[0-9]+))*", host):
            raise ValueError("Sayısal kısaltmalı adresler paylaşılamaz.")
        try:
            ascii_host = host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError("Bağlantı alan adı geçersiz.") from exc
        if "." not in ascii_host or len(ascii_host) > 253 or any(
                not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                for label in ascii_host.split(".")):
            raise ValueError("Bağlantı kamusal bir alan adı içermeli.")
        netloc = ascii_host
    else:
        if not address.is_global or address.is_multicast or address.is_unspecified:
            raise ValueError("Yerel, özel veya ayrılmış IP adresleri paylaşılamaz.")
        if address.version == 6 and address.ipv4_mapped and not address.ipv4_mapped.is_global:
            raise ValueError("Yerel veya özel IP adresleri paylaşılamaz.")
        netloc = f"[{address.compressed}]" if address.version == 6 else str(address)
    if port is not None:
        netloc += f":{port}"
    return urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", parts.query, parts.fragment))


def etiketler(value):
    if not isinstance(value, list) or len(value) > 12:
        raise ValueError("En fazla 12 ilgi alanı veya etiket eklenebilir.")
    result, seen = [], set()
    for label in value:
        label = metin(label, "Etiket", 40, required=True)
        folded = arama_metni(label)
        if folded not in seen:
            result.append(label)
            seen.add(folded)
    return result


def arastirma_kokeni(value):
    if value is None:
        return None
    fields = {"model", "provider", "generated_at", "question", "topic", "source_url", "evidence_status", "language", "modified", "claim_text"}
    alanlari_dogrula(value, fields, fields - {"modified", "claim_text"})
    if value["provider"] not in ("nvidia", "codex", "claude", "unknown"):
        raise ValueError("Araştırmanın sağlayıcısı tanınmıyor.")
    if value["topic"] not in KONULAR or value["language"] not in ("tr", "en"):
        raise ValueError("Araştırmanın konusu veya dili geçersiz.")
    if value["evidence_status"] not in ("verified", "unverified", "insufficient", "unknown"):
        raise ValueError("Araştırmanın kanıt durumu geçersiz.")
    stamp = tarih(metin(value["generated_at"], "Araştırma tarihi", 40, required=True)) if value["generated_at"] is not None else None
    if (value["generated_at"] is not None and stamp is None) or type(value.get("modified", False)) is not bool:
        raise ValueError("Araştırma tarihi veya düzenleme bilgisi geçersiz.")
    result = {"model": metin(value["model"], "Model", 160), "provider": value["provider"],
            "generated_at": stamp.isoformat(timespec="seconds") if stamp else None,
            "question": metin(value["question"], "Araştırma sorusu", 500, required=True),
            "topic": value["topic"], "source_url": guvenli_url(value["source_url"]),
            "evidence_status": value["evidence_status"], "language": value["language"],
            "modified": value.get("modified", False)}
    if "claim_text" in value:
        result["claim_text"] = metin(value["claim_text"], "İncelenen iddia", 1000, multiline=True)
    return result


def arastirma_gelisimi(value):
    """Yalnız kamusal kayıt ve sürüm kimliğini taşır; konuşma veya kullanıcı kimliği kabul etmez."""
    if value is None:
        return None
    alanlari_dogrula(value, {"parents", "change_summary"}, {"parents", "change_summary"})
    parents = value["parents"]
    if not isinstance(parents, list) or not 1 <= len(parents) <= 5:
        raise ValueError("Geliştirilen araştırma sayısı 1–5 olmalı.")
    checked, seen = [], set()
    for parent in parents:
        alanlari_dogrula(parent, {"research_id", "version"}, {"research_id", "version"})
        identifier, version = parent["research_id"], parent["version"]
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise ValueError("Geliştirilen araştırmanın kimliği geçersiz.")
        if type(version) is not int or not 1 <= version <= 9223372036854775807:
            raise ValueError("Geliştirilen araştırmanın sürümü pozitif tam sayı olmalı.")
        if identifier in seen:
            raise ValueError("Aynı araştırma gelişim listesinde tekrarlanamaz.")
        seen.add(identifier)
        checked.append({"research_id": identifier, "version": version})
    return {"parents": checked, "change_summary": metin(value["change_summary"], "Yeni katkının özeti", 600,
                                                        required=True, multiline=True)}


def kayit_dogrula(value):
    alanlari_dogrula(value, KAYIT_ALANLARI, {"kind", "title"})
    if value["kind"] not in ("project", "research"):
        raise ValueError("Kayıt türü project veya research olmalı.")
    license_name = value.get("license")
    if license_name not in (None, "CC-BY-4.0", "CC0-1.0"):
        raise ValueError("Paylaşım lisansı desteklenmiyor.")
    sources = value.get("sources", [])
    if not isinstance(sources, list) or len(sources) > 10:
        raise ValueError("En fazla 10 kaynak eklenebilir.")
    checked_sources = []
    for source in sources:
        alanlari_dogrula(source, {"title", "url"}, {"title", "url"})
        checked_sources.append({"title": metin(source["title"], "Kaynak başlığı", 160, required=True),
                                "url": guvenli_url(source["url"], required=True)})
    record = {"kind": value["kind"], "title": metin(value["title"], "Başlık", 160, required=True),
            "summary": metin(value.get("summary", ""), "Özet", 600, multiline=True),
            "body": metin(value.get("body", ""), "İçerik", 12000, multiline=True),
            "url": guvenli_url(value.get("url", "")), "tags": etiketler(value.get("tags", [])),
            "sources": checked_sources, "author": metin(value.get("author", ""), "Yazar", 100),
            "license": license_name}
    provenance = arastirma_kokeni(value.get("provenance"))
    if provenance is not None:
        if value["kind"] != "research":
            raise ValueError("Model bilgisi yalnız araştırma kartına eklenebilir.")
        record["provenance"] = provenance
    lineage = arastirma_gelisimi(value.get("lineage"))
    if lineage is not None:
        if value["kind"] != "research":
            raise ValueError("Araştırma gelişim bilgisi yalnız araştırma kartına eklenebilir.")
        record["lineage"] = lineage
    return record


def paket_dogrula(value):
    alanlari_dogrula(value, PAKET_ALANLARI | {"provenance", "lineage"}, PAKET_ALANLARI)
    if value["schema"] != "ufuk-share" or type(value["schema_version"]) is not int or value["schema_version"] not in {1, 2, 3}:
        raise ValueError("Bu paylaşım dosyasının biçimi veya sürümü desteklenmiyor.")
    if (value["schema_version"] == 1 and "provenance" in value) or (value["schema_version"] == 2 and not value.get("provenance")):
        raise ValueError("Model bilgisi için paylaşım biçimi 2 gerekli.")
    if (value["schema_version"] != 3 and "lineage" in value) or (value["schema_version"] == 3 and not value.get("lineage")):
        raise ValueError("Araştırma gelişim bilgisi için paylaşım biçimi 3 gerekli.")
    record = kayit_dogrula({key: value[key] for key in KAYIT_ALANLARI if key in value})
    if not record["author"] or not record["license"]:
        raise ValueError("Paylaşım için yazar adı ve içerik lisansı gerekli.")
    if record["kind"] == "research" and not record["sources"]:
        raise ValueError("Araştırmayı paylaşmak için en az bir kaynak bağlantısı ekleyin.")
    if record["kind"] == "project" and not record["url"]:
        raise ValueError("Projeyi paylaşmak için kamusal proje bağlantısını ekleyin.")
    dates = {}
    for key in ("created_at", "updated_at"):
        raw = metin(value[key], "Kayıt tarihi", 40, required=True)
        parsed = tarih(raw)
        if parsed is None:
            raise ValueError("Kayıt tarihleri saat dilimi içeren ISO tarihleri olmalı.")
        dates[key] = parsed.isoformat(timespec="seconds")
    if tarih(dates["updated_at"]) < tarih(dates["created_at"]):
        raise ValueError("Güncelleme tarihi oluşturma tarihinden önce olamaz.")
    result = {"schema": "ufuk-share", "schema_version": value["schema_version"], **record, **dates}
    if len(paket_metni({"packet": result}).encode("utf-8")) > PAKET_SINIRI:
        raise ValueError("Paylaşım paketi 32 KiB sınırını aşıyor.")
    return result


def paket_metni(packet):
    return json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def surumu_dogrula(value):
    if type(value) is not int or not 0 <= value <= 9223372036854775807:
        raise ValueError("Beklenen sürüm 64 bit aralığında sıfır veya pozitif tam sayı olmalı.")


def arama_metni(value):
    return unicodedata.normalize("NFKC", str(value or "")).casefold().replace("\u0307", "").replace("ı", "i")


def kaynak_etiketleri(config):
    source = json.loads(config)
    topic = str(source.get("topic", ""))
    return " ".join([topic, KONU_ADLARI.get(topic, ""), str(source.get("source_type", "")),
                     str(source.get("publisher", "")), str(source.get("name", "")),
                     *map(str, source.get("branches", []))])


class GelistiriciAlani:
    def __init__(self, depo):
        self.depo = depo
        with depo.baglanti() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS platform_settings (
                    id INTEGER PRIMARY KEY CHECK(id=1), interests TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 0 CHECK(version>=0)
                );
                INSERT OR IGNORE INTO platform_settings(id,interests,version) VALUES(1,'[]',0);
                CREATE TABLE IF NOT EXISTS platform_entries (
                    id TEXT PRIMARY KEY, content TEXT NOT NULL,
                    origin TEXT NOT NULL CHECK(origin IN ('local','imported')),
                    version INTEGER NOT NULL CHECK(version>=1),
                    archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)),
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    imported_at TEXT, import_hash TEXT UNIQUE
                );
                CREATE TABLE IF NOT EXISTS platform_research_drafts (
                    task_id TEXT PRIMARY KEY, entry_id TEXT NOT NULL UNIQUE
                );
            """)

    def gorunum(self):
        with self.depo.baglanti() as connection:
            connection.execute("BEGIN")
            settings = connection.execute("SELECT interests,version FROM platform_settings WHERE id=1").fetchone()
            entries = connection.execute("SELECT * FROM platform_entries ORDER BY updated_at DESC,id").fetchall()
            coverage = connection.execute("SELECT COUNT(*) AS source_count,COUNT(last_checked_at) AS checked_sources,"
                                          "MAX(last_checked_at) AS last_checked_at,COUNT(last_success_at) AS successful_sources,"
                                          "COALESCE(SUM(CASE WHEN last_error IS NOT NULL AND last_error!='' THEN 1 ELSE 0 END),0) AS failed_sources,"
                                          "MAX(last_success_at) AS last_success_at FROM sources").fetchone()
            return {"settings": {"interests": json.loads(settings["interests"]), "version": settings["version"]},
                    "entries": [self.kayit_gorunumu(row) for row in entries], "coverage": dict(coverage), "mode": "local"}

    @staticmethod
    def kayit_gorunumu(row):
        return {**json.loads(row["content"]), "id": row["id"], "origin": row["origin"],
                "version": row["version"], "archived": bool(row["archived"]),
                "created_at": row["created_at"], "updated_at": row["updated_at"], "imported_at": row["imported_at"]}

    def oku(self, entry_id, connection=None):
        if connection is None:
            with self.depo.baglanti() as opened:
                return self.oku(entry_id, opened)
        row = connection.execute("SELECT * FROM platform_entries WHERE id=?", (entry_id,)).fetchone()
        if row is None:
            raise KeyError(entry_id)
        return self.kayit_gorunumu(row)

    def ayarlari_kaydet(self, payload):
        alanlari_dogrula(payload, {"interests", "expected_version"}, {"interests", "expected_version"})
        interests = etiketler(payload["interests"])
        surumu_dogrula(payload["expected_version"])
        with self.depo.baglanti(write=True) as connection:
            current = connection.execute("SELECT * FROM platform_settings WHERE id=1").fetchone()
            if current["version"] != payload["expected_version"]:
                raise PlatformSurumCatismasi("İlgi alanları başka bir pencerede değişti. Sayfayı yenileyin.")
            version = current["version"]
            if json.loads(current["interests"]) != interests:
                version += 1
                connection.execute("UPDATE platform_settings SET interests=?,version=? WHERE id=1",
                                   (json.dumps(interests, ensure_ascii=False), version))
            return {"interests": interests, "version": version}

    def olustur(self, payload):
        content = kayit_dogrula(payload)
        entry_id, now = uuid.uuid4().hex, simdi()
        with self.depo.baglanti(write=True) as connection:
            connection.execute("INSERT INTO platform_entries(id,content,origin,version,created_at,updated_at) "
                               "VALUES(?,?,'local',1,?,?)", (entry_id, paket_metni(content), now, now))
            return self.oku(entry_id, connection)

    def guncelle(self, entry_id, payload):
        alanlari_dogrula(payload, KAYIT_ALANLARI | {"expected_version"}, {"kind", "title", "expected_version"})
        surumu_dogrula(payload["expected_version"])
        content = kayit_dogrula({key: value for key, value in payload.items() if key != "expected_version"})
        with self.depo.baglanti(write=True) as connection:
            current = self.oku(entry_id, connection)
            self.surum_eslestir(current, payload["expected_version"])
            if current["origin"] != "local":
                raise ValueError("İçeri alınan paylaşımın yazar kaydı değiştirilemez; ayrı bir yerel taslak oluşturun.")
            if current.get("provenance") and "provenance" not in payload and content["kind"] == "research":
                content["provenance"] = dict(current["provenance"])
            if current.get("lineage") and "lineage" not in payload and content["kind"] == "research":
                content["lineage"] = dict(current["lineage"])
            if content.get("provenance") and any(content.get(key) != current.get(key) for key in ("title", "summary", "body", "sources", "url")):
                content["provenance"]["modified"] = True
                content["provenance"]["evidence_status"] = "unverified"
            if any(content.get(key) != current.get(key) for key in KAYIT_ALANLARI):
                connection.execute("UPDATE platform_entries SET content=?,version=version+1,updated_at=? WHERE id=?",
                                   (paket_metni(content), simdi(), entry_id))
            return self.oku(entry_id, connection)

    @staticmethod
    def surum_eslestir(current, expected_version):
        surumu_dogrula(expected_version)
        if current["version"] != expected_version:
            raise PlatformSurumCatismasi("Kayıt başka bir pencerede değişti. Güncel kaydı yeniden açın.")

    def arsivle(self, entry_id, payload):
        alanlari_dogrula(payload, {"archived", "expected_version"}, {"archived", "expected_version"})
        if type(payload["archived"]) is not bool:
            raise ValueError("Arşiv durumu doğru veya yanlış olmalı.")
        with self.depo.baglanti(write=True) as connection:
            current = self.oku(entry_id, connection)
            self.surum_eslestir(current, payload["expected_version"])
            if current["archived"] != payload["archived"]:
                # Arşivleme yerel bir seçimdir; yazarın içerik tarihini değiştirmez.
                connection.execute("UPDATE platform_entries SET archived=?,version=version+1 WHERE id=?",
                                   (int(payload["archived"]), entry_id))
            return self.oku(entry_id, connection)

    def onizle(self, entry_id, payload):
        alanlari_dogrula(payload, {"expected_version"}, {"expected_version"})
        current = self.oku(entry_id)
        self.surum_eslestir(current, payload["expected_version"])
        packet = {"schema": "ufuk-share", "schema_version": 3 if current.get("lineage") else 2 if current.get("provenance") else 1,
                  **{key: current[key] for key in KAYIT_ALANLARI if key in current},
                  "created_at": current["created_at"], "updated_at": current["updated_at"]}
        return {"packet": paket_dogrula(packet), "notice": PAYLASIM_NOTU}

    def ice_aktarma_onizle(self, payload):
        alanlari_dogrula(payload, {"packet"}, {"packet"})
        return {"packet": paket_dogrula(payload["packet"]), "notice": PAYLASIM_NOTU}

    def ice_aktar(self, payload):
        packet = self.ice_aktarma_onizle(payload)["packet"]
        fingerprint = hashlib.sha256(paket_metni(packet).encode("utf-8")).hexdigest()
        with self.depo.baglanti(write=True) as connection:
            existing = connection.execute("SELECT id FROM platform_entries WHERE import_hash=?", (fingerprint,)).fetchone()
            if existing is not None:
                return {"entry": self.oku(existing["id"], connection), "duplicate": True}
            entry_id = uuid.uuid4().hex
            content = {key: packet[key] for key in KAYIT_ALANLARI if key in packet}
            connection.execute("INSERT INTO platform_entries(id,content,origin,version,created_at,updated_at,imported_at,import_hash) "
                               "VALUES(?,?,'imported',1,?,?,?,?)", (entry_id, paket_metni(content), packet["created_at"],
                                packet["updated_at"], simdi(), fingerprint))
            return {"entry": self.oku(entry_id, connection), "duplicate": False}

    def arastirmadan_taslak(self, task):
        """Hazır raporu ve kaydedilmiş modelini ayrı, henüz yayımlanmamış taslağa kopyalar."""
        if task.get("status") != "ready" or task.get("kind") != "deep" or not task.get("result"):
            raise ValueError("Önce derin araştırma raporunun tamamlanmasını bekleyin.")
        result = task["result"]
        with self.depo.baglanti(write=True) as connection:
            previous = connection.execute("SELECT entry_id FROM platform_research_drafts WHERE task_id=?", (task["id"],)).fetchone()
            if previous:
                return {"entry": self.oku(previous["entry_id"], connection), "duplicate": True}
            request = None
            if task.get("request_id"):
                request = connection.execute("SELECT * FROM research_requests WHERE id=?", (task["request_id"],)).fetchone()
            question = request["question"] if request else result.get("title_tr", task.get("source_title", "Research"))
            source_url = request["source_url"] if request and "source_url" in request.keys() else ""
            quality = result.get("evidence_quality") or {}
            evidence = quality.get("status", "unknown")
            if evidence not in {"verified", "unverified", "insufficient", "unknown"}:
                evidence = "unverified"
            if task.get("stale") or task.get("coverage", {}).get("limited") or not quality.get("coverage_complete"):
                evidence = "unverified"
            sources = [{"title": s["title"][:160], "url": s["url"]} for s in task.get("sources", [])[:10]]
            body = task.get("report_text") or result.get("summary_tr", "")
            if len(body) > 12000:
                raise ValueError("Rapor kart sınırını aşıyor; rapordan ayrı bir özet kartı hazırlayın.")
            content = kayit_dogrula({"kind": "research", "title": (result.get("title_tr") or question)[:160],
                "summary": result.get("summary_tr", "")[:600], "body": body,
                "url": source_url or task.get("source_url", ""), "tags": [task["profile_topic"]], "sources": sources,
                "provenance": {"model": task.get("model", ""), "provider": task.get("engine") or "unknown",
                    "generated_at": task.get("report_generated_at"), "question": question[:500], "topic": task["profile_topic"],
                    "source_url": source_url, "evidence_status": evidence, "language": task.get("language", "tr"), "modified": False,
                    **({"claim_text": request["claim_text"]} if request and "claim_text" in request.keys() and request["claim_text"] else {})}})
            entry_id, now = uuid.uuid4().hex, simdi()
            connection.execute("INSERT INTO platform_entries(id,content,origin,version,created_at,updated_at) VALUES(?,?,'local',1,?,?)",
                               (entry_id, paket_metni(content), now, now))
            connection.execute("INSERT INTO platform_research_drafts(task_id,entry_id) VALUES(?,?)", (task["id"], entry_id))
            return {"entry": self.oku(entry_id, connection), "duplicate": False}

    def akis(self, *, query="", interest="", offset=0, limit=30):
        query = metin(query, "Arama", 200)
        interest = metin(interest, "İlgi alanı", 40)
        if type(offset) is not int or not 0 <= offset <= 9223372036854775807 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Sayfalama için sıfır veya pozitif başlangıç ve 1–100 arası sınır gerekli.")
        conditions, parameters = [], []
        if query:
            conditions.append("ufuk_eslesir(i.title||' '||i.excerpt||' '||i.topic||' '||ufuk_kaynak_adi(s.config),?)")
            parameters.append(query)
        if interest:
            conditions.append("ufuk_eslesir(i.title||' '||i.excerpt||' '||i.topic||' '||ufuk_kaynak_etiketleri(s.config),?)")
            parameters.append(interest)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self.depo.baglanti() as connection:
            connection.create_function("ufuk_eslesir", 2, lambda text, needle: int(arama_metni(needle) in arama_metni(text)))
            connection.create_function("ufuk_kaynak_adi", 1, lambda config: str(json.loads(config).get("name", "")))
            connection.create_function("ufuk_kaynak_etiketleri", 1, kaynak_etiketleri)
            connection.execute("BEGIN")
            table = " FROM items i JOIN sources s ON s.id=i.source_id"
            total = connection.execute("SELECT COUNT(*)" + table + where, parameters).fetchone()[0]
            rows = connection.execute("SELECT i.id,i.title,i.excerpt,i.url,ufuk_kaynak_adi(s.config) AS source_name,"
                                      "i.topic,i.language,i.published_at,i.last_seen_at,i.is_archive" + table + where +
                                      " ORDER BY CASE WHEN i.published_at IS NULL THEN 1 ELSE 0 END,"
                                      "julianday(i.published_at) DESC,i.last_seen_at DESC,i.id LIMIT ? OFFSET ?",
                                      [*parameters, limit, offset]).fetchall()
            items = []
            for row in rows:
                item = dict(row)
                item["topic_label"] = KONU_ADLARI.get(item["topic"], item["topic"])
                item["is_archive"] = bool(item["is_archive"])
                try:
                    item["url"] = guvenli_url(item["url"])
                except ValueError:
                    item["url"] = ""
                items.append(item)
            return {"items": items, "total": total, "offset": offset, "limit": limit}
