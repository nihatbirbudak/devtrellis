"""Opt-in K0 public document tracking; no raw page storage, model or publication path."""
from __future__ import annotations

from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import re
import secrets
import sqlite3
import stat
import time
from pathlib import Path
from urllib.parse import urlsplit

from radar.kaynak_bolumleri import EXTRACTOR_VERSION, KaynakBolumHatasi, compare_documents, extract_document
from radar.kaynak_http import HTTPPolicyError, fetch_document, prepare_response, request_variant
from radar.kaynak_kimligi import KaynakKimligiHatasi, address_key, canonical_source_url, source_key
from radar.kaynak_kullanim import meta_robots, summarize
from radar import toplayici

REPRESENTATION = "public-doc-anonymous-v2"
LEASE_SECONDS = 90
BACKOFF_SECONDS = 60
MAX_SOURCES = 20
TABLES = {"k0_meta", "k0_sources", "k0_leases", "k0_budget"}
ERROR = "Kaynak kontrolü tamamlanamadı; önceki başarılı doğrulama yenilenmedi."
CODE = re.compile(r"[a-z][a-z0-9_]{0,79}\Z")
# Sabit hata kodunun anlamı. "source_refused": kaynak açıkça izin vermiyor (robots, 401/403/451); adres listeden
# çıkarılmaz, böyle kalır. Bilinmeyen kod "unclassified" sayılır; hiçbir sınıf "değişmedi" anlamına gelmez.
ERROR_CLASSES = {
    "source_refused": {"robots_disallowed", "http_forbidden"},
    "rate_limited": {"http_rate_limited", "crawl_delay_exceeds_limit"},
    "address_changed": {"redirect_trailing_slash", "redirect_html_suffix", "redirect_same_host", "redirect_other_host",
                        "redirect_no_location", "http_not_found"},
    "server_error": {"http_server_error", "http_status_other", "robots_unavailable"},
    "network_failure": {"timeout", "connection_closed", "connection_refused", "connection_failed", "tls_error",
                        "dns_busy", "dns_timeout", "dns_failed", "http_protocol_error", "incomplete_body",
                        "conflicting_headers", "unexpected_not_modified"},
    "security_block": {"tls_certificate", "non_public_address", "host_not_allowed", "invalid_url", "invalid_request",
                       "body_too_large", "unsupported_content_encoding"},
}


class KontrolHatasi(ValueError):
    """İzleyicinin kendi tutarlılık denetimi; sabit kod taşır."""


def error_code(error):
    """İstisnayı sabit koda çevirir; mesaj, adres veya başlık değeri taşımaz."""
    if isinstance(error, toplayici.CollectionError):
        code = getattr(error, "code", None)
    elif isinstance(error, (HTTPPolicyError, KaynakBolumHatasi, KaynakKimligiHatasi, KontrolHatasi)):
        code = error.args[0] if error.args else None
    else:
        code = None
    return code if isinstance(code, str) and CODE.fullmatch(code) else "unclassified_error"


def error_class(code, stage=None):
    if code is None:
        return None
    if stage == "policy":
        return "policy_refused"
    if stage == "extract":
        return "unsupported_document"
    if stage == "tracker":
        return "tracker_check"
    base = code.removeprefix("robots_") if code.startswith("robots_") and code not in ERROR_CLASSES["source_refused"] | {"robots_unavailable"} else code
    return next((name for name, codes in ERROR_CLASSES.items() if base in codes), "unclassified")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class TrackingSource:
    id: str
    url: str
    language: str = "en"
    allow_public_cookie: bool = False
    immutable_object_id: str | None = None
    expected_content_sha256: str | None = None
    signal_kind: str | None = None
    signal_url: str | None = None
    signal_interval_seconds: int = 86400

    def __post_init__(self):
        if not isinstance(self.id, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", self.id):
            raise ValueError("Kaynak kimliği geçersiz.")
        canonical = canonical_source_url(self.url)
        if canonical != self.url or urlsplit(self.url).fragment:
            raise ValueError("Katalog tam kanonik kaynak adresi içermeli.")
        if urlsplit(self.url).hostname not in toplayici.APPROVED_FETCH_HOSTS:
            raise ValueError("Kaynak alan adı bu kamu kataloğunda izinli değil.")
        request_variant(self.language)
        if type(self.allow_public_cookie) is not bool:
            raise ValueError("Anonim kamu çerezi politikası açık boolean olmalı.")
        if (self.immutable_object_id is None) != (self.expected_content_sha256 is None):
            raise ValueError("Değişmez nesne kimliği ve beklenen bayt özeti birlikte gerekli.")
        if self.immutable_object_id is not None:
            if (not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", self.immutable_object_id)
                    or not re.fullmatch(r"[a-f0-9]{64}", self.expected_content_sha256)
                    or self.immutable_object_id not in urlsplit(self.url).path.split("/")):
                raise ValueError("Değişmez nesne tam adres nesnesi ve bayt özetiyle sabitlenmeli.")
        if (self.signal_kind is None) != (self.signal_url is None):
            raise ValueError("Sinyal türü ve tam kamu adresi birlikte gerekli.")
        if self.signal_kind is not None:
            from radar.kaynak_sinyali import validate_signal
            validate_signal(self.signal_kind, self.signal_url, self.url)
        if type(self.signal_interval_seconds) is not int or not 3600 <= self.signal_interval_seconds <= 604800:
            raise ValueError("Sinyal aralığı 1 saat ile 7 gün arasında olmalı.")

    @property
    def representation(self):
        return REPRESENTATION

    @property
    def extractor_version(self):
        return EXTRACTOR_VERSION

    @property
    def key(self):
        return source_key(self.url, self.language, representation=REPRESENTATION,
                          request_variant=request_variant(self.language), extractor_version=EXTRACTOR_VERSION)

    @property
    def address_key(self):
        return address_key(self.url, self.language)

    @property
    def context_key(self):
        # Policy configuration changes cannot reuse a previously accepted cookie variant.
        return digest(asdict(self))

    def http_source(self):
        return {"url": self.url, "language": self.language, "allow_public_cookie": self.allow_public_cookie,
                "representation": REPRESENTATION, "extractor_version": EXTRACTOR_VERSION,
                "request_variant": request_variant(self.language), "source_key": self.key}


def _safe_path(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.exists() or part.is_symlink():
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("İzleme deposu bağlantılı bir yol kullanamaz.")
            if part == path and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
                raise ValueError("İzleme deposu ayrı normal dosya olmalı.")
    for suffix in ('-wal', '-shm', '-journal'):
        sidecar = path.with_name(path.name+suffix)
        if sidecar.exists() or sidecar.is_symlink():
            info = sidecar.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or getattr(info, 'st_file_attributes', 0) & 0x400):
                raise ValueError("İzleme yan dosyası bağlantılı olamaz.")
    return path


class SourceTracker:
    """A separate v2 store. Existing v1/community databases are never migrated here."""

    def __init__(self, path, sources, *, fetcher=None, signal_fetcher=None, clock=None, daily_limit=100):
        if (not isinstance(sources, (list, tuple)) or not 1 <= len(sources) <= MAX_SOURCES
                or any(not isinstance(item, TrackingSource) for item in sources)
                or len({item.id for item in sources}) != len(sources)
                or len({item.key for item in sources}) != len(sources)):
            raise ValueError("İzleme kataloğu en fazla 20 ayrı kaynak içermeli.")
        if type(daily_limit) is not int or not 1 <= daily_limit <= 1000:
            raise ValueError("Günlük kaynak kontrol sınırı geçersiz.")
        self.path = _safe_path(path)
        self.sources = {item.id: item for item in sources}
        self.fetcher = fetcher or fetch_document
        self.signal_fetcher = signal_fetcher
        self.clock = clock or time.time
        self.daily_limit = daily_limit
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            # Immutable read-only inspection creates no WAL/SHM and performs no
            # recovery. A new or pending WAL-only schema must be retried later.
            journal = self.path.with_name(self.path.name + '-journal')
            if journal.exists() and journal.stat().st_size:
                raise ValueError("Bekleyen geri alma kaydı olan depo açılmadı.")
            with closing(sqlite3.connect(self.path.as_uri() + '?mode=ro&immutable=1', uri=True)) as probe:
                tables = {row[0] for row in probe.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if tables != TABLES:
                    raise ValueError("Bu dosya K0 deposu değil; mevcut depo değiştirilmedi.")
                if probe.execute("SELECT version FROM k0_meta").fetchall() != [(1,)]:
                    raise ValueError("K0 depo şeması desteklenmiyor.")
        with self.connection() as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables and tables != TABLES:
                raise ValueError("Bu dosya K0 deposu değil; mevcut depo değiştirilmedi.")
            if tables and connection.execute("SELECT version FROM k0_meta").fetchall() != [(1,)]:
                raise ValueError("K0 depo şeması desteklenmiyor.")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS k0_meta(version INTEGER NOT NULL);
                INSERT INTO k0_meta SELECT 1 WHERE NOT EXISTS(SELECT 1 FROM k0_meta);
                CREATE TABLE IF NOT EXISTS k0_sources(
                    key TEXT PRIMARY KEY, context_key TEXT, revision INTEGER NOT NULL DEFAULT 0,
                    document TEXT, http TEXT, signal TEXT, changes TEXT, raw_hash TEXT,
                    attempted_at REAL, last_error TEXT, method TEXT,
                    checks INTEGER NOT NULL DEFAULT 0, requests INTEGER NOT NULL DEFAULT 0,
                    downloaded_bytes INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS k0_leases(key TEXT PRIMARY KEY, owner TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS k0_budget(day INTEGER PRIMARY KEY, attempts INTEGER NOT NULL);
            """)
            # İkinci dilim: sabit hata kodu ve aşaması. Eski K0 deposuna boş sütun olarak eklenir.
            columns = {row[1] for row in connection.execute("PRAGMA table_info(k0_sources)")}
            for name in ("error_code", "error_stage", "usage"):
                if name not in columns:
                    connection.execute(f"ALTER TABLE k0_sources ADD COLUMN {name} TEXT")

    @contextmanager
    def connection(self, *, write=False):
        _safe_path(self.path)
        with closing(sqlite3.connect(self.path, timeout=5)) as connection:
            if write:
                connection.execute("BEGIN IMMEDIATE")
            with connection:
                yield connection

    def _now(self):
        value = self.clock()
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError("Kontrol saati geçersiz.")
        return float(value)

    def source(self, source_id):
        try:
            return self.sources[source_id]
        except (KeyError, TypeError):
            raise ValueError("Kaynak seçilmiş katalogda bulunamadı.") from None

    @staticmethod
    def _age(value):
        if type(value) is not int or not 0 <= value <= 86400:
            raise ValueError("Kontrol yaşı 0–86400 saniye arasında olmalı.")

    @staticmethod
    def _row(connection, key):
        cursor = connection.execute("SELECT * FROM k0_sources WHERE key=?", (key,))
        row = cursor.fetchone()
        if row is None:
            return None
        result = dict(zip([item[0] for item in cursor.description], row))
        for name in ("document", "http", "signal", "changes", "usage"):
            result[name] = json.loads(result[name]) if result.get(name) else None
        return result

    @staticmethod
    def _immutable(source, row):
        return bool(row and row["context_key"] == source.context_key and not row["last_error"]
                    and row["document"] and row["http"] and not row["http"].get("revalidate")
                    and source.expected_content_sha256 and row["raw_hash"] == source.expected_content_sha256)

    @classmethod
    def _fresh(cls, source, row, now, age):
        if not row or row["context_key"] != source.context_key or row["last_error"] or not row["document"] or not row["http"]:
            return False
        meta = row["http"]
        checked = meta["representation_checked_at"]
        if now < checked or (row["attempted_at"] or 0) > checked:
            return False
        if cls._immutable(source, row):
            return True
        return not meta["revalidate"] and now < min(meta["fresh_until"], checked + age)

    def read(self, source_id, *, max_age_seconds=3600):
        self._age(max_age_seconds)
        source, now = self.source(source_id), self._now()
        with self.connection() as connection:
            connection.execute("BEGIN")
            row = self._row(connection, source.key)
            lease = connection.execute("SELECT expires FROM k0_leases WHERE key=?", (source.key,)).fetchone()
        checking = bool(lease and lease[0] > now)
        fresh = self._fresh(source, row, now, max_age_seconds) and not checking
        meta = row["http"] if row and row["http"] else {}
        signal = row["signal"] if row and row["signal"] else {}
        state = "checking" if checking else "fresh" if fresh else "error" if row and row["last_error"] else "stale" if row and row["document"] else "missing"
        return {"schema": "devtrellis-source-tracking-v2", "source_id": source.id, "address_key": source.address_key,
                "source_key": source.key, "url": source.url, "language": source.language,
                "representation": REPRESENTATION, "extractor_version": EXTRACTOR_VERSION,
                "state": state, "fresh": bool(fresh), "freshness_basis": "immutable_object" if fresh and self._immutable(source, row) else "http",
                "revision": row["revision"] if row else 0, "document": row["document"] if row else None,
                "changes": row["changes"] if row else None, "method": row["method"] if row else None,
                "representation_checked_at": meta.get("representation_checked_at"),
                "content_fetched_at": meta.get("content_fetched_at"), "quote_verified_at": None,
                "signal_checked_at": signal.get("checked_at"), "signal_kind": signal.get("kind"),
                "validation_kind": meta.get("validation_kind"), "byte_verified": meta.get("byte_verified", False),
                "attempted_at": row["attempted_at"] if row else None, "last_error": row["last_error"] if row else None,
                "error_code": row.get("error_code") if row and row["last_error"] else None,
                "error_stage": row.get("error_stage") if row and row["last_error"] else None,
                "error_class": error_class(row.get("error_code"), row.get("error_stage")) if row and row["last_error"] else None,
                "usage_signals": row.get("usage") if row else None,
                "checks": row["checks"] if row else 0, "requests": row["requests"] if row else 0,
                "downloaded_bytes": row["downloaded_bytes"] if row else 0,
                "model_calls": 0, "claims_verified": False}

    def check(self, source_id, *, max_age_seconds=3600, freshness_required=True):
        self._age(max_age_seconds)
        if type(freshness_required) is not bool:
            raise ValueError("Güncellik gereksinimi boolean olmalı.")
        source, now = self.source(source_id), self._now()
        if not freshness_required:
            return {**self.read(source_id, max_age_seconds=max_age_seconds), "check_started": False}
        owner = secrets.token_hex(16)
        with self.connection(write=True) as connection:
            row = self._row(connection, source.key)
            lease = connection.execute("SELECT expires FROM k0_leases WHERE key=?", (source.key,)).fetchone()
            used = connection.execute("SELECT attempts FROM k0_budget WHERE day=?", (int(now//86400),)).fetchone()
            backoff = bool(row and row["last_error"] and 0 <= now-(row["attempted_at"] or 0) < BACKOFF_SECONDS)
            exhausted = bool(used and used[0] >= self.daily_limit)
            active = connection.execute("SELECT COUNT(*) FROM k0_leases WHERE expires>?", (now,)).fetchone()[0]
            start = not ((lease and lease[0] > now) or backoff or exhausted or active >= 2 or self._fresh(source, row, now, max_age_seconds))
            if start:
                connection.execute("INSERT INTO k0_sources(key) VALUES(?) ON CONFLICT(key) DO NOTHING", (source.key,))
                connection.execute("UPDATE k0_sources SET attempted_at=?,checks=checks+1 WHERE key=?", (now, source.key))
                connection.execute("INSERT INTO k0_leases VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET owner=excluded.owner,expires=excluded.expires", (source.key, owner, now+LEASE_SECONDS))
                connection.execute("INSERT INTO k0_budget VALUES(?,1) ON CONFLICT(day) DO UPDATE SET attempts=attempts+1", (int(now//86400),))
                connection.execute("DELETE FROM k0_budget WHERE day<?", (int(now//86400)-2,))
        if not start:
            return {**self.read(source_id, max_age_seconds=max_age_seconds), "check_started": False, "budget_exhausted": exhausted, "retry_after_seconds": BACKOFF_SECONDS if backoff else 0}
        error, document, meta, changes = None, None, None, None
        code, stage, usage = None, None, None
        signal = row["signal"] if row else None
        size, requests = 0, 0
        raw_hash = row["raw_hash"] if row else None
        try:
            if source.signal_kind and (not signal or not 0 <= now-signal["checked_at"] < source.signal_interval_seconds):
                from radar.kaynak_sinyali import fetch_signal, validate_observation
                requests += 1
                try:
                    observed, signal_bytes = (self.signal_fetcher or fetch_signal)(source)
                    size += max(0, int(signal_bytes))
                    signal = validate_observation(observed, source.signal_kind, self._now())
                except Exception:
                    # An unavailable hint cannot suppress the required representation check.
                    pass
            previous_http = row["http"] if row and row["context_key"] == source.context_key else None
            etag = previous_http.get("etag") if previous_http else None
            modified = previous_http.get("modified") if previous_http else None
            requests += 1
            stage = "fetch"
            response, request_meta = self.fetcher(source.http_source(), etag=etag, modified=modified)
            size += len(response.body)
            stage = "tracker"
            if request_meta.get("request_variant") != request_variant(source.language):
                raise KontrolHatasi("request_variant_mismatch")
            if request_meta.get("sent_etag") != etag or request_meta.get("sent_modified") != modified:
                raise KontrolHatasi("sent_validator_mismatch")
            stage = "policy"
            meta, body = prepare_response(source.http_source(), previous_http, response, now=self._now(),
                                          sent_etag=request_meta.get("sent_etag"), sent_modified=request_meta.get("sent_modified"),
                                          request_started_at=request_meta.get("request_started_at"))
            stage = "tracker"
            # Kullanım sinyalleri: 304'te gövde yok; meta robots ve gelmeyen başlık önceki kayıttan taşınır.
            previous_usage = row["usage"] if row and row.get("usage") else {}
            signals = getattr(response, "usage", None) or {}
            tag = signals.get("x_robots_tag")
            if body is None and (not tag or not tag.get("present")) and previous_usage.get("x_robots_tag"):
                tag = previous_usage["x_robots_tag"]
            usage = summarize(signals.get("robots_content_signal"), tag,
                              meta_robots(body) if body is not None else previous_usage.get("meta_robots"), checked_at=self._now())
            if body is None:
                if not row or not row["document"]:
                    raise KontrolHatasi("previous_representation_missing")
                document = row["document"]
                changes = {"status": "ambiguous" if document.get("ambiguous") else "unchanged", "added": [], "removed": [], "changed": [], "moved": [], "ambiguous": document.get("ambiguous", False)}
            else:
                raw_hash = hashlib.sha256(body).hexdigest()
                if source.expected_content_sha256 and raw_hash != source.expected_content_sha256:
                    raise KontrolHatasi("immutable_hash_mismatch")
                stage = "extract"
                document = extract_document(body, source.url)
                stage = "compare"
                if row and row["document"]:
                    changes = compare_documents(row["document"], document)
                else:
                    changes = {"status": "initial", "added": [s["section_id"] for s in document["sections"]], "removed": [], "changed": [], "moved": [], "ambiguous": document.get("ambiguous", False)}
        except Exception as failure:
            error = ERROR
            code = error_code(failure)
            # Aşama istisna türünden okunur (ör. özel önbellek politikası belge okunurken de reddedilir);
            # karşılaştırma hatası bozuk/eski manifest demektir ve çıkarım sınıfında raporlanır.
            for kind, name in ((HTTPPolicyError, "policy"), (KaynakBolumHatasi, "extract"), (KontrolHatasi, "tracker"),
                               (toplayici.CollectionError, "fetch")):
                if isinstance(failure, kind):
                    stage = name
                    break
            stage = "extract" if stage == "compare" else stage
        finished = self._now()
        with self.connection(write=True) as connection:
            lease = connection.execute("SELECT owner,expires FROM k0_leases WHERE key=?", (source.key,)).fetchone()
            owned = bool(lease and lease[0] == owner and lease[1] > finished and finished >= now)
            if owned:
                connection.execute("UPDATE k0_sources SET requests=requests+?,downloaded_bytes=downloaded_bytes+?,signal=? WHERE key=?", (requests,size,json.dumps(signal) if signal else None,source.key))
                if error:
                    connection.execute("UPDATE k0_sources SET last_error=?,error_code=?,error_stage=? WHERE key=?", (error,code,stage,source.key))
                else:
                    revision = (row["revision"] if row else 0) + int(not row or not row["document"] or row["document"]["body_hash"] != document["body_hash"])
                    connection.execute("UPDATE k0_sources SET context_key=?,revision=?,document=?,http=?,changes=?,raw_hash=?,last_error=NULL,error_code=NULL,error_stage=NULL,usage=?,method=? WHERE key=?",
                                       (source.context_key,revision,json.dumps(document),json.dumps(meta),json.dumps(changes),raw_hash,
                                        json.dumps(usage) if usage else None,"conditional_304" if body is None else "content_hash",source.key))
                connection.execute("DELETE FROM k0_leases WHERE key=? AND owner=?", (source.key,owner))
            elif lease and lease[0] == owner:
                connection.execute("UPDATE k0_sources SET last_error=?,error_code='lease_expired',error_stage='tracker' WHERE key=?", (ERROR,source.key))
                connection.execute("DELETE FROM k0_leases WHERE key=? AND owner=?", (source.key,owner))
        return {**self.read(source_id, max_age_seconds=max_age_seconds), "check_started": True, "check_succeeded": owned and error is None,
                "ownership_lost": not owned, "request_count_this_check": requests, "downloaded_bytes_this_check": size}
