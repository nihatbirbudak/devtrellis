"""Seçilmiş kamu kaynaklarının talep üzerine kontrolünü ve sürüme bağlı kısa özetlerini saklar."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import time
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup, Comment

from radar.gelistirici_alani import guvenli_url, metin
from radar.kaynak_havuzu import alan_adi_gecerli
from radar.okuma import OkumaHatasi, makale_ayristir
from radar.paylasim_gizliligi import sorguyu_dogrula
from radar.toplayici import FetchResult, kosullu_parametreleri_dogrula


EXTRACTOR_VERSION = "article-v4-docbook-definitions"
REPRESENTATION = "html-anonymous-v1"
LEASE_SECONDS = 90
ERROR_BACKOFF_SECONDS = 60
MAX_SOURCE_COUNT = 20


@dataclass(frozen=True)
class KaynakTanimi:
    """İşletmecinin seçtiği tam kamu adresi; kullanıcı sorusu veya kimliği taşımaz."""

    id: str
    url: str
    language: str = "en"
    representation: str = REPRESENTATION
    extractor_version: str = EXTRACTOR_VERSION

    def __post_init__(self):
        if not isinstance(self.id, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", self.id):
            raise ValueError("Kaynak kimliği geçersiz.")
        checked = guvenli_url(self.url, required=True)
        parts = urlsplit(checked)
        if (checked != self.url or parts.scheme != "https" or parts.port not in (None, 443)
                or parts.fragment or not alan_adi_gecerli(parts.hostname or "")):
            raise ValueError("Kaynak standart HTTPS üzerinde tam bir kamu adresi olmalı.")
        # Sürüm ve dil parametreleri aynen anahtarda kalır; oturum ve özel sorgular kabul edilmez.
        kosullu_parametreleri_dogrula(self.url)
        sorguyu_dogrula(unquote(parts.path))
        if not isinstance(self.language, str) or not re.fullmatch(r"[a-z]{2}(?:-[A-Z]{2})?", self.language):
            raise ValueError("Kaynak dili geçersiz.")
        if self.representation != REPRESENTATION or self.extractor_version != EXTRACTOR_VERSION:
            raise ValueError("Bu temsil veya çıkarım sürümü henüz desteklenmiyor.")

    @property
    def key(self):
        value = [self.url, self.language, self.representation, self.extractor_version]
        return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode("utf-8")).hexdigest()


def kosullu_oku(source, *, etag=None, modified=None):
    """Mevcut kamu okuma katmanının DNS, TLS, robots ve boyut sınırlarını kullanır."""
    from radar.toplayici import kosullu_sayfa_oku
    return kosullu_sayfa_oku(source.url, {"item_hosts": [urlsplit(source.url).hostname]},
                            etag=etag, modified=modified, language=source.language)


def kod_metni_izi(root):
    """Kodun anlamlı boşluklarını korur; iç içe kodu ikinci kez hash girdisine katmaz."""
    literals = [{"tag": node.name, "text": node.get_text("", strip=False)}
                for node in root.select("pre, code") if node.find_parent(["pre", "code"]) is None]
    return hashlib.sha256(json.dumps(literals, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def docbook_tanim_listelerini_uyarla(section, soup):
    """Yalnız sıralı, dolu ve iç içe olmayan dt/dd çiftlerini mevcut metin okuyucusuna uyarlar."""
    definitions = []
    inline = {"span", "em", "strong", "b", "i", "code", "a"}
    for listing in section.find_all("dl"):
        if listing.find_parent(["dl", "dt", "dd", "p", "pre", "blockquote"]):
            raise OkumaHatasi("PostgreSQL tanım listesi desteklenmeyen bir alan içinde.")
        children = []
        for node in listing.children:
            if isinstance(node, Comment):
                continue
            if getattr(node, "name", None) is None:
                if node.strip():
                    raise OkumaHatasi("PostgreSQL tanım listesinde çiftlerin dışında metin var.")
            else:
                children.append(node)
        if not children or len(children) % 2 or [node.name for node in children] != ["dt", "dd"] * (len(children) // 2):
            raise OkumaHatasi("PostgreSQL tanım listesi ardışık terim ve açıklama çiftleri içermeli.")
        pairs = []
        for term, description in zip(children[::2], children[1::2]):
            for node in (term, description):
                if not node.get_text("", strip=False).strip():
                    raise OkumaHatasi("PostgreSQL tanım listesinde boş terim veya açıklama var.")
                allowed = inline | ({"p"} if node is description else set())
                for child in node.find_all(True):
                    if child.name not in allowed or (child.name == "p" and child.parent is not description):
                        raise OkumaHatasi("PostgreSQL tanım listesinde desteklenmeyen içerik var.")
            # Sıra, terim/açıklama bağı ve özgün boşluklar ayrı hash girdisinde kalır.
            # HTML yalnız bellekte hash'lenir; veritabanına kaynak metni yazılmaz.
            pairs.append({"term": str(term), "description": str(description)})
        definitions.append(pairs)
        replacement = soup.new_tag("div")
        for term, description in zip(children[::2], children[1::2]):
            pair = soup.new_tag("blockquote")
            term.name, description.name = "p", "div"
            pair.append(term.extract())
            pair.append(description.extract())
            replacement.append(pair)
        listing.replace_with(replacement)
    return hashlib.sha256(json.dumps(definitions, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def kaynak_metnini_ayristir(response):
    """Tek PostgreSQL DocBook bölümünü tanır; belirsiz veya eksik çıkarılacak yapıları reddeder."""
    address = urlsplit(response.url)
    docbook = (address.scheme == "https" and address.hostname == "www.postgresql.org"
               and address.port in (None, 443) and address.username is None and address.password is None
               and re.fullmatch(r"/docs/[0-9]+(?:\.[0-9]+)?/[a-z0-9][a-z0-9-]*\.html", address.path))
    if not docbook:
        article = makale_ayristir(response)
        soup = BeautifulSoup(response.body, "html.parser")
        for element in soup.select("script, style, noscript, nav, footer, aside, form, button"):
            element.decompose()
        # Genel okuyucunun GitHub/forum alan seçimini yeniden uygulamadan bütün
        # kodu kapsar. Ana metin dışı kod değişimi temkinli bir ek geçersiz kılma yapabilir.
        article["literal_hash"] = kod_metni_izi(soup)
        return article
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if (response.status != 200 or content_type not in {"", "text/html", "application/xhtml+xml"}
            or response.body.lstrip().startswith(b"%PDF-")):
        raise OkumaHatasi("PostgreSQL bölüm uyarlaması yalnız başarılı HTML yanıtını kabul eder.")
    soup = BeautifulSoup(response.body, "html.parser")
    containers = soup.select("#docContent")
    if len(containers) != 1:
        raise OkumaHatasi("PostgreSQL belgesinde tek içerik alanı bulunamadı.")
    sections = containers[0].find_all("div", class_="sect1", recursive=False)
    if len(sections) != 1 or len(soup.select("div.sect1")) != 1:
        raise OkumaHatasi("PostgreSQL belgesi tek ve doğrudan ana bölüm içermeli.")
    section = sections[0]
    title_pages = section.find_all("div", class_="titlepage", recursive=False)
    headings = title_pages[0].select("h2.title") if len(title_pages) == 1 else []
    if len(headings) != 1 or len(section.find_all("h2")) != 1 or section.find("h1"):
        raise OkumaHatasi("PostgreSQL belgesinin tek ana bölüm başlığı doğrulanamadı.")
    # Bu küçük uyarlama yalnız metin bloklarını destekler; tablo/görsel veya
    # başka başlık düzeylerini sessizce atlayıp kaynağı değişmemiş saymaz.
    if section.select("table, h5, h6, img, svg, canvas, video, audio, math, iframe, object, embed"):
        raise OkumaHatasi("PostgreSQL bölümünde tam metin kontrolü için desteklenmeyen bir yapı var.")
    definition_hash = docbook_tanim_listelerini_uyarla(section, soup)
    for element in section.select("script, style, noscript, nav, footer, aside, form, button"):
        element.decompose()
    for node in section.find_all(string=True):
        if isinstance(node, Comment) or not node.strip():
            continue
        if node.find_parent(["p", "li", "h2", "h3", "h4", "pre", "blockquote"]) is None:
            raise OkumaHatasi("PostgreSQL bölümünde metin çıkarımının kapsamadığı bir alan var.")
    # Üst/alt gezinme ve belge sürümü menüsü ana bölüm dışındadır.
    section.name = "article"
    headings[0].name = "h1"
    normalized = FetchResult(response.status, str(section).encode("utf-8"),
                             {**response.headers, "content-type": "text/html; charset=utf-8"}, response.url)
    article = makale_ayristir(normalized)
    # Okunabilir gövdedeki boşluk normalizasyonu kodun anlamını değiştirebilir.
    # Kod bloklarının ve satır içi kodun özgün metni ayrıca sürüm izine katılır.
    article["literal_hash"] = kod_metni_izi(section)
    article["definition_hash"] = definition_hash
    return article


def kamu_metni(value, name, limit, *, required=True):
    checked = metin(value, name, limit, required=required, multiline=True)
    return sorguyu_dogrula(checked)


def ozet_dogrula(value):
    """Kısa özeti doğrular; özel bağlam alanları ve açık hassas kalıplar reddedilir."""
    fields = {"summary", "findings", "scope", "limitations", "sections", "model", "generated_at", "license"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("Özet yalnız tanımlı kamu alanlarını içermeli.")
    result = {}
    for field, limit in (("summary", 1600), ("scope", 500), ("limitations", 700), ("model", 160)):
        result[field] = kamu_metni(value[field], field, limit)
    for field, maximum, limit in (("findings", 5, 350), ("sections", 5, 160)):
        if not isinstance(value[field], list) or len(value[field]) > maximum:
            raise ValueError("Özetin bulgu veya bölüm listesi sınırı aşıyor.")
        result[field] = [kamu_metni(item, field, limit) for item in value[field]]
    if value["license"] not in {"CC-BY-4.0", "CC0-1.0"}:
        raise ValueError("Özetin içerik izni desteklenmiyor.")
    result["license"] = value["license"]
    stamp = metin(value["generated_at"], "Üretim tarihi", 40, required=True)
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("Özet tarihi geçersiz.") from None
    if parsed.tzinfo is None:
        raise ValueError("Özet tarihi saat dilimi içermeli.")
    result["generated_at"] = parsed.isoformat(timespec="seconds")
    return result


class KaynakHafizasi:
    """Ağ kontrolü yalnız kontrol_et ile başlar; kaynak gövdesi ve kullanıcı bağlamı saklanmaz.

    sources işletmeci yapılandırmasıdır. İnternete açık kullanıcı URL kaydı değildir.
    Özet kaydı yalnız mevcut kamu katkı/inceleme onayından sonra çağrılmalıdır.
    Aynı SQLite dosyasındaki ayrı süreçler ortak süreli iş sahipliğini kullanır.
    """

    def __init__(self, path, sources, *, fetcher=None, clock=None, daily_limit=100):
        if not isinstance(sources, (tuple, list)) or len(sources) > MAX_SOURCE_COUNT:
            raise ValueError("Kaynak hafızası en fazla 20 seçilmiş kaynakla açılabilir.")
        if any(not isinstance(source, KaynakTanimi) for source in sources):
            raise ValueError("Kaynaklar doğrulanmış tanımlar olmalı.")
        if len({source.id for source in sources}) != len(sources) or len({source.key for source in sources}) != len(sources):
            raise ValueError("Kaynak kimlikleri ve temsil anahtarları tekil olmalı.")
        if type(daily_limit) is not int or not 1 <= daily_limit <= 1000:
            raise ValueError("Günlük kaynak kontrol sınırı 1–1000 arasında olmalı.")
        self.path = Path(path)
        self.sources = {source.id: source for source in sources}
        self.fetcher = fetcher or kosullu_oku
        self.clock = clock or time.time
        self.daily_limit = daily_limit
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.baglanti() as connection:
            connection.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS source_memory (
                    key TEXT PRIMARY KEY, revision INTEGER NOT NULL DEFAULT 0,
                    content_hash TEXT, title TEXT, text_length INTEGER,
                    etag TEXT, modified TEXT, fetched_at REAL, checked_at REAL,
                    attempted_at REAL, last_error TEXT, method TEXT,
                    response_max_age INTEGER, revalidate INTEGER NOT NULL DEFAULT 0,
                    checks INTEGER NOT NULL DEFAULT 0, downloaded_bytes INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS source_memory_leases (
                    key TEXT PRIMARY KEY, owner TEXT NOT NULL, expires_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS source_memory_budget (
                    day INTEGER PRIMARY KEY, attempts INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS source_memory_summaries (
                    key TEXT NOT NULL, version INTEGER NOT NULL, source_revision INTEGER NOT NULL,
                    content_hash TEXT NOT NULL, packet TEXT NOT NULL, approved_at REAL NOT NULL,
                    PRIMARY KEY(key,version)
                );
            """)

    @contextmanager
    def baglanti(self, *, write=False):
        with closing(sqlite3.connect(self.path, timeout=5)) as connection:
            connection.row_factory = sqlite3.Row
            if write:
                connection.execute("BEGIN IMMEDIATE")
            with connection:
                yield connection

    def tanim(self, source_id):
        try:
            return self.sources[source_id]
        except (KeyError, TypeError):
            raise ValueError("Kaynak bu küçük kamu pilotunda tanımlı değil.") from None

    @staticmethod
    def yas_dogrula(max_age_seconds):
        if type(max_age_seconds) is not int or not 0 <= max_age_seconds <= 86400:
            raise ValueError("Kontrol yaşı 0–86400 saniye arasında olmalı.")

    @staticmethod
    def taze(row, now, max_age_seconds):
        if (not row or row["checked_at"] is None or row["last_error"] or row["revalidate"]
                or (row["attempted_at"] is not None and row["attempted_at"] > row["checked_at"])):
            return False
        if row["response_max_age"] is not None:
            max_age_seconds = min(max_age_seconds, row["response_max_age"])
        return 0 <= now - row["checked_at"] < max_age_seconds

    def oku(self, source_id, *, max_age_seconds=3600):
        """Kısa kaydı ve uygun özeti döndürür; hiçbir ağ veya model çağrısı yapmaz."""
        self.yas_dogrula(max_age_seconds)
        source = self.tanim(source_id)
        now = self.clock()
        with self.baglanti() as connection:
            connection.execute("BEGIN")
            row = connection.execute("SELECT * FROM source_memory WHERE key=?", (source.key,)).fetchone()
            lease = connection.execute("SELECT expires_at FROM source_memory_leases WHERE key=?", (source.key,)).fetchone()
            summary = connection.execute("SELECT * FROM source_memory_summaries WHERE key=? ORDER BY version DESC LIMIT 1",
                                         (source.key,)).fetchone()
        checking = bool(lease and lease["expires_at"] > now)
        fresh = bool(self.taze(row, now, max_age_seconds)) and not checking
        state = "checking" if checking else ("fresh" if fresh else "stale" if row and row["revision"] else "missing")
        if row and row["last_error"] and not checking:
            state = "error"
        current_summary = bool(summary and row and summary["source_revision"] == row["revision"]
                               and summary["content_hash"] == row["content_hash"])
        result = {"source_id": source.id, "key": source.key, "url": source.url, "language": source.language,
                  "representation": source.representation, "extractor_version": source.extractor_version,
                  "state": state, "fresh": fresh, "source_revision": row["revision"] if row else 0,
                  "summary_status": "current" if current_summary else "stale" if summary else "missing",
                  "summary_reusable": fresh and current_summary, "summary": None,
                  "claims_verified": False, "model_calls": 0}
        for field in ("content_hash", "title", "text_length", "fetched_at", "checked_at", "attempted_at", "last_error", "method"):
            result[field] = row[field] if row else None
        result["checks"] = row["checks"] if row else 0
        result["downloaded_bytes"] = row["downloaded_bytes"] if row else 0
        if current_summary:
            result["summary"] = {**json.loads(summary["packet"]), "version": summary["version"],
                                 "source_revision": summary["source_revision"], "content_hash": summary["content_hash"]}
        return result

    def kontrol_et(self, source_id, *, max_age_seconds=3600, freshness_required=True):
        """Gerekli ve süresi gelmiş tek temsili denetler; eşzamanlı istek ikinci iş açmaz."""
        self.yas_dogrula(max_age_seconds)
        if type(freshness_required) is not bool:
            raise ValueError("Güncellik gereksinimi doğru/yanlış olmalı.")
        source = self.tanim(source_id)
        if not freshness_required:
            return {**self.oku(source_id, max_age_seconds=max_age_seconds), "check_started": False}
        now = self.clock()
        owner = secrets.token_hex(16)
        with self.baglanti(write=True) as connection:
            row = connection.execute("SELECT * FROM source_memory WHERE key=?", (source.key,)).fetchone()
            lease = connection.execute("SELECT * FROM source_memory_leases WHERE key=?", (source.key,)).fetchone()
            waiting = bool(lease and lease["expires_at"] > now)
            backoff = bool(row and row["last_error"] and row["attempted_at"] is not None
                           and 0 <= now - row["attempted_at"] < ERROR_BACKOFF_SECONDS)
            used = connection.execute("SELECT attempts FROM source_memory_budget WHERE day=?", (int(now // 86400),)).fetchone()
            exhausted = bool(used and used["attempts"] >= self.daily_limit)
            active = connection.execute("SELECT COUNT(*) FROM source_memory_leases WHERE expires_at>?", (now,)).fetchone()[0]
            start = not (waiting or backoff or exhausted or active >= 2 or self.taze(row, now, max_age_seconds))
            if start:
                connection.execute("INSERT INTO source_memory(key) VALUES(?) ON CONFLICT(key) DO NOTHING", (source.key,))
                connection.execute("INSERT INTO source_memory_leases VALUES(?,?,?) ON CONFLICT(key) DO UPDATE "
                                   "SET owner=excluded.owner,expires_at=excluded.expires_at", (source.key, owner, now + LEASE_SECONDS))
                connection.execute("UPDATE source_memory SET attempted_at=?,checks=checks+1 WHERE key=?", (now, source.key))
                connection.execute("INSERT INTO source_memory_budget VALUES(?,1) ON CONFLICT(day) DO UPDATE SET attempts=attempts+1",
                                   (int(now // 86400),))
                connection.execute("DELETE FROM source_memory_budget WHERE day<?", (int(now // 86400) - 2,))
        if not start:
            result = self.oku(source_id, max_age_seconds=max_age_seconds)
            result.update(check_started=False, budget_exhausted=exhausted, retry_after_seconds=ERROR_BACKOFF_SECONDS if backoff else 0)
            return result
        error = None
        prepared = None
        response_size = 0
        try:
            response = self.fetcher(source, etag=row["etag"] if row else None, modified=row["modified"] if row else None)
            response_size = len(response.body)
            prepared = self.yaniti_hazirla(source, row, response)
        except Exception:
            # Ağ hataları veya yayıncı yanıtları özel metin/başlıklarla günlük veya API'ye taşınmaz.
            error = "Kaynak kontrolü tamamlanamadı; önceki başarılı kontrol yenilenmedi."
        finished = self.clock()
        with self.baglanti(write=True) as connection:
            lease = connection.execute("SELECT * FROM source_memory_leases WHERE key=?", (source.key,)).fetchone()
            owned = bool(lease and lease["owner"] == owner and lease["expires_at"] > finished)
            if owned:
                connection.execute("UPDATE source_memory SET downloaded_bytes=downloaded_bytes+? WHERE key=?", (response_size, source.key))
                if error:
                    connection.execute("UPDATE source_memory SET last_error=? WHERE key=?", (error, source.key))
                else:
                    self.basarili_kaydet(connection, source.key, row, prepared, finished)
                connection.execute("DELETE FROM source_memory_leases WHERE key=? AND owner=?", (source.key, owner))
            elif lease and lease["owner"] == owner:
                connection.execute("UPDATE source_memory SET last_error=? WHERE key=?",
                                   ("Kaynak kontrolünün süresi doldu; önceki başarılı kontrol yenilenmedi.", source.key))
                connection.execute("DELETE FROM source_memory_leases WHERE key=? AND owner=?", (source.key, owner))
        return {**self.oku(source_id, max_age_seconds=max_age_seconds), "check_started": True,
                "check_succeeded": owned and error is None, "ownership_lost": not owned}

    @staticmethod
    def yaniti_hazirla(source, previous, response):
        """HTTP doğrulamasını içerik doğruluğundan ayırır ve paylaşılmayan temsili reddeder."""
        if response.status not in (200, 304) or response.url != source.url or len(response.body) > 2_000_000:
            raise ValueError("Kontrol yanıtı veya hedefi uygun değil.")
        headers = {key.lower(): value for key, value in response.headers.items()}
        directives = {part.strip().split("=", 1)[0].lower(): part.strip().partition("=")[2].strip('"')
                      for part in headers.get("cache-control", "").split(",") if part.strip()}
        vary = {part.strip().lower() for part in headers.get("vary", "").split(",") if part.strip()}
        if ("private" in directives or "no-store" in directives or "set-cookie" in headers
                or vary - {"accept-language", "accept-encoding", "user-agent"}):
            raise ValueError("Yanıt anonim ortak saklamaya uygun değil.")
        if headers.get("content-language", source.language).lower() != source.language.lower():
            raise ValueError("Yanıt başka bir dil temsilini taşıyor.")
        result = {"etag": None, "modified": None, "max_age": None, "revalidate": int("no-cache" in directives)}
        for name, header in (("etag", "etag"), ("modified", "last-modified")):
            value = headers.get(header)
            if value is not None:
                if not isinstance(value, str) or len(value) > 512 or any(ord(char) < 32 or ord(char) == 127 or ord(char) > 255 for char in value):
                    raise ValueError("HTTP doğrulayıcısı geçersiz.")
                result[name] = value
        age_directive = "s-maxage" if "s-maxage" in directives else "max-age"
        if age_directive in directives:
            if not directives[age_directive].isdigit():
                raise ValueError("Önbellek yaşı geçersiz.")
            age = headers.get("age", "0")
            if not age.isdigit():
                raise ValueError("Yanıtın önbellekteki yaşı geçersiz.")
            result["max_age"] = max(0, min(86400, int(directives[age_directive])) - int(age))
        if response.status == 304:
            if not previous or not previous["content_hash"] or not (previous["etag"] or previous["modified"]) or response.body:
                raise ValueError("304 için önceki doğrulanmış temsil yok.")
            if result["etag"] and previous["etag"] and result["etag"].removeprefix("W/") != previous["etag"].removeprefix("W/"):
                raise ValueError("304 farklı bir kaynak sürümüne ait.")
            result.update(method="conditional_304", content_hash=previous["content_hash"], title=previous["title"],
                          text_length=previous["text_length"], etag=result["etag"] or previous["etag"],
                          modified=result["modified"] or previous["modified"])
            if "cache-control" not in headers:
                result.update(max_age=previous["response_max_age"], revalidate=previous["revalidate"])
            return result
        article = kaynak_metnini_ayristir(response)
        if article["truncated"]:
            raise ValueError("Kesilmiş metin bütün kaynak değişimini doğrulayamaz.")
        title = kamu_metni(article["title"], "Kaynak başlığı", 500)
        fingerprint = article["title"] + "\n\n" + article["body"]
        for field in ("literal_hash", "definition_hash"):
            if field in article:
                fingerprint += "\n\n" + article[field]
        digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()
        result.update(method="content_hash", content_hash=digest, title=title, text_length=len(article["body"]))
        return result

    @staticmethod
    def basarili_kaydet(connection, key, previous, result, now):
        changed = not previous or result["content_hash"] != previous["content_hash"]
        revision = (previous["revision"] if previous else 0) + int(changed)
        fetched = previous["fetched_at"] if result["method"] == "conditional_304" else now
        connection.execute("UPDATE source_memory SET revision=?,content_hash=?,title=?,text_length=?,etag=?,modified=?,"
                           "fetched_at=?,checked_at=?,last_error=NULL,method=?,response_max_age=?,revalidate=? WHERE key=?",
                           (revision, result["content_hash"], result["title"], result["text_length"], result["etag"],
                            result["modified"], fetched, now, result["method"], result["max_age"], result["revalidate"], key))

    def ozet_kaydet(self, source_id, *, expected_revision, expected_hash, summary, approved_public=False):
        """Onaylanmış kamu özetini değişmemiş kaynak sürümüne bağlar; model çağırmaz."""
        if approved_public is not True:
            raise ValueError("Özetin kamuya paylaşım onayı gerekli.")
        if (type(expected_revision) is not int or expected_revision < 1 or not isinstance(expected_hash, str)
                or not re.fullmatch(r"[a-f0-9]{64}", expected_hash)):
            raise ValueError("Özet doğrulanmış bir kaynak sürümüne ve hash değerine bağlanmalı.")
        source = self.tanim(source_id)
        packet = ozet_dogrula(summary)
        with self.baglanti(write=True) as connection:
            row = connection.execute("SELECT * FROM source_memory WHERE key=?", (source.key,)).fetchone()
            lease = connection.execute("SELECT expires_at FROM source_memory_leases WHERE key=?", (source.key,)).fetchone()
            if (not row or row["revision"] != expected_revision or row["content_hash"] != expected_hash
                    or row["last_error"] or (lease and lease["expires_at"] > self.clock())):
                raise ValueError("Özetin kaynak sürümü değişmiş veya kontrolü tamamlanmamış.")
            version = connection.execute("SELECT COALESCE(MAX(version),0)+1 FROM source_memory_summaries WHERE key=?",
                                         (source.key,)).fetchone()[0]
            connection.execute("INSERT INTO source_memory_summaries VALUES(?,?,?,?,?,?)",
                               (source.key, version, expected_revision, expected_hash,
                                json.dumps(packet, ensure_ascii=False), self.clock()))
        return self.oku(source_id)
