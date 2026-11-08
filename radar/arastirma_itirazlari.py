"""Sürüme bağlı gerekçeli itirazı özel depoda tutar; yalnız moderatör özetini kamuya ayırır.

HTTP kimlik doğrulaması, insan önizlemesi/onayı ve CSRF çağıran katmandadır. user
yalnız sunucunun doğruladığı mevcut kullanıcı nesnesi olmalı; istemci sözlüğü
yetki değildir. consent=True iç biçim koşuludur, insan onayının kanıtı değildir.
published_report ağsız kamu deposu okuyucusudur; özel kart okuyucusu bağlanmaz.

DB yalnız kendisine ayrılmış dizinde tembel açılır. POSIX izinleri ve Windows
korumalı DACL sahibi, SYSTEM ve yöneticilere erişim verir; yedekleri kapsamaz.
Özel metin ilk teslimden 90 gün sonra silinir. Fiş/hash/durum ve açıkça onaylanan
kamu özeti kalır; tekrar teslim süresi uzatmaz. Geri çekme yayımlanmış düzeltme
izini silmez. Hiçbir yöntem araştırma yayımlamaz, ağ/model çağırmaz veya IP tutmaz.
"""

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import stat
import time
from contextlib import closing, contextmanager
from pathlib import Path

from radar.gelistirici_alani import alanlari_dogrula, metin
from radar.ozel_dosya_izinleri import ozel_dosya_izni
from radar.paylasim_gizliligi import kamusal_url, sorguyu_dogrula
from radar.topluluk_depo import ToplulukHatasi, tarih_metni


APPLICATION_ID = 0x44544931
SCHEMA_VERSION = 1
TOTAL_LIMIT = 5000
OPEN_LIMIT = 200
DAILY_LIMIT = 100
REPORT_DAILY_LIMIT = 10
RETENTION_SECONDS = 90 * 86400
STATUSES = frozenset({"pending", "in_review", "accepted", "rejected", "withdrawn", "expired"})
_TABLES = {"research_objections", "research_objection_decisions"}


class ItirazHatasi(ToplulukHatasi):
    """Özel metni veya fişi açıklamayan itiraz hatası."""


def _alanlar(value, allowed, required=None):
    try:
        alanlari_dogrula(value, allowed, required)
    except ValueError:
        raise ItirazHatasi("İtiraz alanları eksik veya desteklenmeyen alan içeriyor.") from None


def _kimlik(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise ItirazHatasi("Araştırma veya itiraz kimliği geçersiz.")
    return value


def _surum(value):
    if type(value) is not int or not 1 <= value <= 9223372036854775807:
        raise ItirazHatasi("Tam pozitif sürüm gerekli.")
    return value


def _kamu_metni(value, label, limit):
    try:
        checked = metin(value, label, limit, required=True, multiline=True)
        checked.encode("utf-8")
        return sorguyu_dogrula(checked)
    except (ValueError, UnicodeError):
        raise ItirazHatasi(f"{label} geçersiz veya belirgin özel bilgi içeriyor.") from None


def _fis_hash(receipt):
    if not isinstance(receipt, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", receipt):
        raise ItirazHatasi("Geçerli özel itiraz fişi gerekli.", 401)
    return hashlib.sha256(("devtrellis-objection:" + receipt).encode("ascii")).hexdigest()


def _moderator(user):
    if not isinstance(user, dict) or user.get("role") != "moderator":
        raise ItirazHatasi("Bu işlem moderatör yetkisi gerektirir.", 403)
    try:
        return _kimlik(user.get("id"))
    except ItirazHatasi:
        raise ItirazHatasi("Bu işlem moderatör yetkisi gerektirir.", 403) from None


def _payload(payload):
    _alanlar(payload, {"research_id", "version", "claim", "reason", "sources", "consent"},
             {"research_id", "version", "claim", "reason", "consent"})
    if payload["consent"] is not True:
        raise ItirazHatasi("Özel inceleme kutusuna gönderim onayı gerekli.")
    sources = payload.get("sources", [])
    if not isinstance(sources, list) or len(sources) > 3:
        raise ItirazHatasi("En fazla üç kamu kaynağı eklenebilir.")
    checked_sources = []
    for source in sources:
        _alanlar(source, {"title", "url"}, {"title", "url"})
        try:
            url = kamusal_url(source["url"])
            if not url:
                raise ValueError()
        except ValueError:
            raise ItirazHatasi("İtiraz kaynağı kamusal HTTPS adresi olmalı.") from None
        checked_sources.append({"title": _kamu_metni(source["title"], "Kaynak başlığı", 160), "url": url})
    checked = {"research_id": _kimlik(payload["research_id"]), "version": _surum(payload["version"]),
               "claim": _kamu_metni(payload["claim"], "İddia veya kısa alıntı", 800),
               "reason": _kamu_metni(payload["reason"], "Gerekçe", 2000), "sources": checked_sources}
    encoded = json.dumps(checked, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return checked, hashlib.sha256(encoded.encode("ascii")).hexdigest()


def _baglantisiz_yol(path):
    for candidate in (path, *path.parents):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or (getattr(info, "st_file_attributes", 0)
                                        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
            raise ItirazHatasi("İtiraz deposu sembolik bağlantı veya junction kullanamaz.", 409)


def _ozel_izin(path, *, directory=False):
    try:
        ozel_dosya_izni(path, directory=directory)
    except (OSError, ValueError):
        raise ItirazHatasi("İtiraz deposunun özel dosya izni uygulanamadı.", 503) from None


class ItirazDeposu:
    def __init__(self, path, *, published_report, clock=None):
        if not callable(published_report):
            raise ValueError("Tam kamu sürümünü okuyan işlev gerekli.")
        self.path = Path(os.path.abspath(path))
        self.published_report = published_report
        self.clock = clock or time.time

    @staticmethod
    def _sahiplik(connection, *, allow_empty=False):
        app_id = connection.execute("PRAGMA application_id").fetchone()[0]
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if allow_empty and not tables and app_id == 0 and version == 0:
            return False
        if app_id != APPLICATION_ID or version != SCHEMA_VERSION or tables != _TABLES:
            raise ItirazHatasi("İtirazlar için ayrı, desteklenen özel veritabanı gerekli.", 409)
        return True

    def _hazirla(self):
        _baglantisiz_yol(self.path)
        if self.path.exists() and self.path.stat().st_size:
            with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=10)) as connection:
                self._sahiplik(connection)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        allowed = {self.path.name, *(self.path.name + suffix for suffix in ("-journal", "-wal", "-shm"))}
        for child in self.path.parent.iterdir():
            try:
                info = child.lstat()
            except FileNotFoundError:
                continue  # Eşzamanlı SQLite işleminin günlük dosyası kapanmış olabilir.
            linked = stat.S_ISLNK(info.st_mode) or (getattr(info, "st_file_attributes", 0)
                                                  & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
            if child.name not in allowed or not stat.S_ISREG(info.st_mode) or linked or info.st_nlink != 1:
                raise ItirazHatasi("İtiraz deposu yalnız kendisine ayrılmış dizinde tutulmalı.", 409)
        _ozel_izin(self.path.parent, directory=True)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        _ozel_izin(self.path)

    @contextmanager
    def baglanti(self):
        connection = None
        try:
            self._hazirla()
            connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA secure_delete=ON")
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("BEGIN IMMEDIATE")
            if not self._sahiplik(connection, allow_empty=True):
                connection.execute("CREATE TABLE research_objections ("
                    "id TEXT PRIMARY KEY, receipt_hash TEXT NOT NULL UNIQUE, payload_hash TEXT NOT NULL, "
                    "research_id TEXT NOT NULL, report_version INTEGER NOT NULL, claim TEXT NOT NULL, reason TEXT NOT NULL, sources TEXT NOT NULL, "
                    "status TEXT NOT NULL, revision INTEGER NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, "
                    "private_purged INTEGER NOT NULL DEFAULT 0, public_summary TEXT NOT NULL DEFAULT '', "
                    "correction_id TEXT, correction_version INTEGER, reviewed_at INTEGER)")
                connection.execute("CREATE TABLE research_objection_decisions ("
                    "id INTEGER PRIMARY KEY, objection_id TEXT NOT NULL, revision INTEGER NOT NULL, moderator_id TEXT NOT NULL, "
                    "status TEXT NOT NULL, created_at INTEGER NOT NULL)")
                connection.execute("CREATE INDEX objection_report ON research_objections(research_id,report_version,created_at)")
                connection.execute("CREATE INDEX objection_queue ON research_objections(status,created_at)")
                connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
                connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            now = int(self.clock())
            connection.execute("UPDATE research_objections SET claim='',reason='',sources='[]',private_purged=1,revision=revision+1,updated_at=?, "
                "status=CASE WHEN status IN ('pending','in_review') THEN 'expired' ELSE status END "
                "WHERE private_purged=0 AND created_at<=?", (now, now - RETENTION_SECONDS))
            yield connection
            connection.commit()
        except (sqlite3.Error, OSError):
            if connection is not None:
                connection.rollback()
            raise ItirazHatasi("Özel itiraz deposuna güvenli erişim sağlanamadı.", 503) from None
        except BaseException:
            if connection is not None:
                connection.rollback()
            raise
        finally:
            if connection is not None:
                connection.close()

    def _kamu_raporu(self, research_id, version):
        try:
            card = self.published_report(research_id, version)
        except Exception:
            raise ItirazHatasi("Tam kamu sürümü doğrulanamadı; işlem yapılmadı.", 409) from None
        if (not isinstance(card, dict) or card.get("id") != research_id or type(card.get("version")) is not int
                or card["version"] != version or card.get("status") != "published" or card.get("available") is False
                or not isinstance(card.get("packet"), dict) or card["packet"].get("kind") != "research"):
            raise ItirazHatasi("Tam kamu sürümü doğrulanamadı; işlem yapılmadı.", 409)
        return card

    @staticmethod
    def _gorunum(row, *, detail=False):
        result = {"id": row["id"], "research_id": row["research_id"], "version": row["report_version"],
                  "status": row["status"], "revision": row["revision"], "private_purged": bool(row["private_purged"]),
                  "created_at": tarih_metni(row["created_at"]), "updated_at": tarih_metni(row["updated_at"])}
        if detail:
            result.update(claim=row["claim"], reason=row["reason"], sources=json.loads(row["sources"]),
                          public_summary=row["public_summary"], correction={"research_id": row["correction_id"],
                          "version": row["correction_version"]} if row["correction_id"] else None)
        return result

    @staticmethod
    def _surum_kontrol(row, expected):
        if row["revision"] != _surum(expected):
            raise ItirazHatasi("İtiraz değişti; güncel kaydı yeniden açın.", 409)

    @staticmethod
    def _satir(connection, column, value):
        if column not in {"id", "receipt_hash"}:
            raise ValueError("Desteklenmeyen arama alanı.")
        row = connection.execute(f"SELECT * FROM research_objections WHERE {column}=?", (value,)).fetchone()
        if row is None:
            raise ItirazHatasi("İtiraz bulunamadı.", 404)
        return row

    def gonder(self, receipt, payload):
        """Yerel gizli fiş tek sabit kamu taslağına bağlanır; başlangıç yalnız private pending."""
        receipt_hash = _fis_hash(receipt)
        checked, payload_hash = _payload(payload)
        if receipt in json.dumps(checked, ensure_ascii=True):
            raise ItirazHatasi("Özel itiraz fişi içeriğe eklenemez.")
        with self.baglanti() as connection:
            previous = connection.execute("SELECT * FROM research_objections WHERE receipt_hash=?", (receipt_hash,)).fetchone()
            if previous is not None:
                if not hmac.compare_digest(previous["payload_hash"], payload_hash):
                    raise ItirazHatasi("Bu özel fiş başka bir itiraz veya sürüme bağlı.", 409)
                return {"objection": self._gorunum(previous), "duplicate": True}
            self._kamu_raporu(checked["research_id"], checked["version"])
            now = int(self.clock())
            counts = connection.execute("SELECT COUNT(*) AS total,SUM(status IN ('pending','in_review')) AS open,"
                "SUM(created_at>?) AS recent FROM research_objections", (now - 86400,)).fetchone()
            report_count = connection.execute("SELECT COUNT(*) FROM research_objections WHERE research_id=? AND report_version=? AND created_at>?",
                (checked["research_id"], checked["version"], now - 86400)).fetchone()[0]
            if (counts["total"] >= TOTAL_LIMIT or (counts["open"] or 0) >= OPEN_LIMIT
                    or (counts["recent"] or 0) >= DAILY_LIMIT or report_count >= REPORT_DAILY_LIMIT):
                raise ItirazHatasi("İtiraz kuyruğu veya gönderim sınırı dolu; daha sonra deneyin.", 429)
            identifier = secrets.token_hex(16)
            connection.execute("INSERT INTO research_objections "
                "(id,receipt_hash,payload_hash,research_id,report_version,claim,reason,sources,status,revision,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,'pending',1,?,?)", (identifier, receipt_hash, payload_hash, checked["research_id"],
                    checked["version"], checked["claim"], checked["reason"], json.dumps(checked["sources"], ensure_ascii=True), now, now))
            return {"objection": self._gorunum(self._satir(connection, "id", identifier)), "duplicate": False}

    def oku(self, receipt):
        receipt_hash = _fis_hash(receipt)
        with self.baglanti() as connection:
            return self._gorunum(self._satir(connection, "receipt_hash", receipt_hash), detail=True)

    def geri_cek(self, receipt, expected_revision):
        receipt_hash = _fis_hash(receipt)
        _surum(expected_revision)
        with self.baglanti() as connection:
            row = self._satir(connection, "receipt_hash", receipt_hash)
            self._surum_kontrol(row, expected_revision)
            if row["status"] != "withdrawn":
                connection.execute("UPDATE research_objections SET status='withdrawn',claim='',reason='',sources='[]',private_purged=1,"
                    "revision=revision+1,updated_at=? WHERE id=?", (int(self.clock()), row["id"]))
            return self._gorunum(self._satir(connection, "id", row["id"]))

    def inceleme_listele(self, user, status="", offset=0, limit=25):
        _moderator(user)
        if (not isinstance(status, str) or (status and status not in STATUSES) or type(offset) is not int
                or not 0 <= offset <= 9223372036854775807 or type(limit) is not int or not 1 <= limit <= 100):
            raise ItirazHatasi("İnceleme filtresi veya sayfalama sınırı geçersiz.")
        where, values = (" WHERE status=?", [status]) if status else ("", [])
        with self.baglanti() as connection:
            total = connection.execute("SELECT COUNT(*) FROM research_objections" + where, values).fetchone()[0]
            rows = connection.execute("SELECT * FROM research_objections" + where + " ORDER BY created_at DESC,id LIMIT ? OFFSET ?",
                                      [*values, limit, offset]).fetchall()
            return {"items": [self._gorunum(row) for row in rows], "total": total, "offset": offset, "limit": limit}

    def inceleme_oku(self, user, identifier):
        _moderator(user)
        _kimlik(identifier)
        with self.baglanti() as connection:
            return self._gorunum(self._satir(connection, "id", identifier), detail=True)

    def karar(self, user, identifier, payload):
        moderator_id = _moderator(user)
        _kimlik(identifier)
        _alanlar(payload, {"expected_revision", "research_id", "version", "status", "public_summary", "correction"},
                 {"expected_revision", "research_id", "version", "status"})
        _surum(payload["expected_revision"])
        research_id, version = _kimlik(payload["research_id"]), _surum(payload["version"])
        decision = payload["status"]
        if not isinstance(decision, str) or decision not in {"in_review", "accepted", "rejected"}:
            raise ItirazHatasi("İnceleme kararı geçersiz.")
        summary, correction_id, correction_version = "", None, None
        if decision == "accepted":
            summary = _kamu_metni(payload.get("public_summary"), "Kamu düzeltme özeti", 600)
            correction = payload.get("correction")
            _alanlar(correction, {"research_id", "version"}, {"research_id", "version"})
            correction_id, correction_version = _kimlik(correction["research_id"]), _surum(correction["version"])
        elif {"public_summary", "correction"}.intersection(payload):
            raise ItirazHatasi("Kamu özeti ve düzeltme bağı yalnız kabul kararında kullanılabilir.")
        with self.baglanti() as connection:
            row = self._satir(connection, "id", identifier)
            self._surum_kontrol(row, payload["expected_revision"])
            if row["research_id"] != research_id or row["report_version"] != version:
                raise ItirazHatasi("İtiraz başka bir rapor veya sürüme taşınamaz.", 409)
            transitions = {"pending": {"in_review", "rejected"}, "in_review": {"accepted", "rejected"}}
            if decision not in transitions.get(row["status"], set()):
                raise ItirazHatasi("Bu itiraz durumunda istenen inceleme kararı uygulanamaz.", 409)
            if decision == "accepted":
                self._kamu_raporu(research_id, version)
                corrected = self._kamu_raporu(correction_id, correction_version)
                if correction_id == research_id:
                    linked = correction_version > version
                else:
                    lineage = corrected["packet"].get("lineage")
                    parents = lineage.get("parents", []) if isinstance(lineage, dict) else []
                    if not isinstance(parents, list):
                        parents = []
                    linked = any(parent.get("research_id") == research_id and type(parent.get("version")) is int
                                 and parent["version"] == version for parent in parents if isinstance(parent, dict))
                if not linked:
                    raise ItirazHatasi("Düzeltme tam itiraz sürümünü geliştiren yayımlı araştırmaya bağlanmalı.", 409)
            now = int(self.clock())
            connection.execute("UPDATE research_objections SET status=?,revision=revision+1,updated_at=?,public_summary=?,"
                "correction_id=?,correction_version=?,reviewed_at=? WHERE id=?",
                (decision, now, summary, correction_id, correction_version, now, identifier))
            connection.execute("INSERT INTO research_objection_decisions(objection_id,revision,moderator_id,status,created_at) VALUES(?,?,?,?,?)",
                               (identifier, row["revision"] + 1, moderator_id, decision, now))
            return self._gorunum(self._satir(connection, "id", identifier), detail=True)

    def kamusal_ozetler(self, research_id, version, *, offset=0, limit=25):
        research_id, version = _kimlik(research_id), _surum(version)
        if type(offset) is not int or not 0 <= offset <= 9223372036854775807 or type(limit) is not int or not 1 <= limit <= 100:
            raise ItirazHatasi("Kamu özeti sayfalama sınırı geçersiz.")
        self._kamu_raporu(research_id, version)
        with self.baglanti() as connection:
            total = connection.execute("SELECT COUNT(*) FROM research_objections WHERE research_id=? AND report_version=? AND public_summary<>''",
                                       (research_id, version)).fetchone()[0]
            rows = connection.execute("SELECT id,status,public_summary,correction_id,correction_version,reviewed_at "
                "FROM research_objections WHERE research_id=? AND report_version=? AND public_summary<>'' "
                "ORDER BY reviewed_at,id LIMIT ? OFFSET ?", (research_id, version, limit, offset)).fetchall()
            return {"research_id": research_id, "version": version, "total": total, "offset": offset, "limit": limit, "items": [
                {"id": row["id"], "summary": row["public_summary"], "withdrawn": row["status"] == "withdrawn",
                 "correction": {"research_id": row["correction_id"], "version": row["correction_version"]},
                 "reviewed_at": tarih_metni(row["reviewed_at"])} for row in rows]}
