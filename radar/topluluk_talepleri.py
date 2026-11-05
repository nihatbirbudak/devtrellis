"""Hesapsız başvuru ve destek mesajlarını yalnız özel yönetim kutusunda saklar.

Bu depo araştırma/yayın tablosu oluşturmaz, arama metni üretmez ve model çağırmaz.
HTTP yetkisi çağıran katmana aittir; yerel CLI veritabanına erişim yetkisi ister.
Saklama süresi ilk oluşturulmadan itibaren 90 gündür. Temizlik her işlemde yalnız
bu tablodan yapılır; ayrı yedek dosyalarının silindiği garantisini vermez.
"""

import hashlib
import hmac
import json
import re
import sqlite3
import time
import unicodedata
import uuid
from contextlib import contextmanager
from pathlib import Path

from radar.topluluk_depo import ToplulukHatasi, tarih_metni


RETENTION_SECONDS = 90 * 86400
TOTAL_LIMIT = 1000
NEW_LIMIT = 200
STATUSES = ("new", "in_review", "closed")
CATEGORIES = {
    "contribute": ("research", "code", "translation", "design", "sources", "other"),
    "support": ("setup", "bug", "content", "privacy", "other"),
}
_FIELDS = frozenset(("kind", "category", "subject", "message", "name", "email", "language",
                     "consent", "idempotency_key", "website"))
_REQUIRED = _FIELDS - {"name", "email", "website"}
_LIST_FIELDS = ("id", "kind", "category", "subject", "language", "status", "created_at", "updated_at")
_DETAIL_FIELDS = _LIST_FIELDS + ("message", "name", "email", "consent")


class TalepHatasi(ToplulukHatasi):
    """İçerik ve iletişim bilgisi taşımayan, HTTP durumlu talep hatası."""


def _metin(value, label, limit, *, required=False, multiline=False):
    if not isinstance(value, str) or len(value) > limit:
        raise TalepHatasi(f"{label} metin olmalı ve en fazla {limit} karakter içermeli.")
    if multiline:
        value = value.replace("\r\n", "\n")
    if any(unicodedata.category(char).startswith("C") and not (multiline and char == "\n") for char in value):
        raise TalepHatasi(f"{label} desteklenmeyen kontrol karakteri içeriyor.")
    value = value.strip()
    if required and not value:
        raise TalepHatasi(f"{label} boş olamaz.")
    return value


def _email(value):
    value = _metin(value, "E-posta", 254)
    if not value:
        return value
    # DNS'e bağlanmadan yaygın addr-spec biçimini doğrular; teslim garantisi değildir.
    if value.count("@") != 1:
        raise TalepHatasi("E-posta adresinin biçimi geçersiz.")
    local, domain = value.rsplit("@", 1)
    if (not 1 <= len(local) <= 64 or local.startswith(".") or local.endswith(".") or ".." in local
            or not re.fullmatch(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~.\-]+", local)):
        raise TalepHatasi("E-posta adresinin biçimi geçersiz.")
    try:
        domain = domain.encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise TalepHatasi("E-posta adresinin biçimi geçersiz.") from None
    labels = domain.split(".")
    if (len(labels) < 2 or len(domain) > 253
            or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)
            or not re.fullmatch(r"[a-z][a-z0-9-]{1,62}", labels[-1])
            or len(local + "@" + domain) > 254):
        raise TalepHatasi("E-posta adresinin biçimi geçersiz.")
    return local + "@" + domain


def _payload(payload):
    if not isinstance(payload, dict) or set(payload) - _FIELDS or _REQUIRED - set(payload):
        raise TalepHatasi("Talep yalnız desteklenen alanları ve gerekli alanların tamamını içermeli.")
    kind = _metin(payload["kind"], "Talep türü", 20, required=True)
    category = _metin(payload["category"], "Kategori", 20, required=True)
    language = _metin(payload["language"], "Dil", 2, required=True)
    if kind not in CATEGORIES or category not in CATEGORIES[kind]:
        raise TalepHatasi("Talep türü veya kategori geçersiz.")
    if language not in ("en", "tr"):
        raise TalepHatasi("Dil en veya tr olmalı.")
    if payload["consent"] is not True:
        raise TalepHatasi("Özel talep kutusuna gönderim için açık onay gerekli.")
    website = payload.get("website", "")
    if not isinstance(website, str) or website != "":
        raise TalepHatasi("Talep doğrulanamadı.")
    key = payload["idempotency_key"]
    if not isinstance(key, str) or not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", key):
        raise TalepHatasi("Gönderim anahtarı UUID biçiminde olmalı.")
    checked = {"kind": kind, "category": category,
               "subject": _metin(payload["subject"], "Konu", 160, required=True),
               "message": _metin(payload["message"], "Mesaj", 4000, required=True, multiline=True),
               "name": _metin(payload.get("name", ""), "Ad", 80),
               "email": _email(payload.get("email", "")), "language": language, "consent": True}
    key_hash = hashlib.sha256(str(uuid.UUID(key)).encode("ascii")).hexdigest()
    payload_hash = hashlib.sha256(json.dumps(checked, ensure_ascii=True, sort_keys=True,
                                            separators=(",", ":")).encode("ascii")).hexdigest()
    return checked, key_hash, payload_hash


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise TalepHatasi("Talep kimliği geçersiz.")
    return value


def _status(value, *, empty=False):
    if not isinstance(value, str) or value not in (STATUSES + (("",) if empty else ())):
        raise TalepHatasi("Talep durumu geçersiz.")
    return value


class TalepDeposu:
    def __init__(self, path, clock=None):
        self.path = Path(path)
        self.clock = clock or time.time
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.baglanti() as connection:
            connection.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS community_requests (
                    id TEXT PRIMARY KEY,
                    key_hash TEXT NOT NULL UNIQUE,
                    payload_hash TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('contribute','support')),
                    category TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    message TEXT NOT NULL,
                    name TEXT NOT NULL DEFAULT '',
                    email TEXT NOT NULL DEFAULT '',
                    language TEXT NOT NULL CHECK(language IN ('en','tr')),
                    consent INTEGER NOT NULL CHECK(consent=1),
                    status TEXT NOT NULL CHECK(status IN ('new','in_review','closed')),
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS community_request_expiry ON community_requests(created_at);
                CREATE INDEX IF NOT EXISTS community_request_queue ON community_requests(status,created_at,id);
            """)

    @contextmanager
    def baglanti(self, *, expire=False):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA secure_delete=ON")
            if expire:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("DELETE FROM community_requests WHERE created_at<=?",
                                   (int(self.clock()) - RETENTION_SECONDS,))
            yield connection
            connection.commit()
        except TalepHatasi:
            # Beklenen hatalar bütün yazma önkoşullarından önce üretilir.
            # 404/409/429 dönerken de süresi dolan özel kayıtlar temizlenir.
            connection.commit()
            raise
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _gorunum(row, *, detail=False):
        result = {key: row[key] for key in (_DETAIL_FIELDS if detail else _LIST_FIELDS)}
        for key in ("created_at", "updated_at"):
            result[key] = tarih_metni(result[key])
        if detail:
            result["consent"] = bool(result["consent"])
        return result

    @staticmethod
    def _receipt(row, *, duplicate):
        return {"id": row["id"], "status": row["status"],
                "created_at": tarih_metni(row["created_at"]), "duplicate": duplicate}

    def gonder(self, payload):
        checked, key_hash, payload_hash = _payload(payload)
        with self.baglanti(expire=True) as connection:
            existing = connection.execute("SELECT * FROM community_requests WHERE key_hash=?", (key_hash,)).fetchone()
            if existing is not None:
                if not hmac.compare_digest(existing["payload_hash"], payload_hash):
                    raise TalepHatasi("Bu gönderim anahtarı başka bir talep için kullanılmış.", 409)
                return self._receipt(existing, duplicate=True)
            count = connection.execute("SELECT COUNT(*) AS total, SUM(status='new') AS new FROM community_requests").fetchone()
            if count["total"] >= TOTAL_LIMIT or (count["new"] or 0) >= NEW_LIMIT:
                raise TalepHatasi("Talep kutusu şu anda dolu; daha sonra yeniden deneyin.", 429)
            identifier, now = uuid.uuid4().hex, int(self.clock())
            connection.execute("INSERT INTO community_requests "
                "(id,key_hash,payload_hash,kind,category,subject,message,name,email,language,consent,status,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,1,'new',?,?)",
                (identifier, key_hash, payload_hash, checked["kind"], checked["category"], checked["subject"],
                 checked["message"], checked["name"], checked["email"], checked["language"], now, now))
            return self._receipt({"id": identifier, "status": "new", "created_at": now}, duplicate=False)

    def listele(self, status="", offset=0, limit=25):
        _status(status, empty=True)
        if type(offset) is not int or not 0 <= offset <= 9223372036854775807 or type(limit) is not int or not 1 <= limit <= 100:
            raise TalepHatasi("Sayfalama sınırı geçersiz.")
        where, values = (" WHERE status=?", [status]) if status else ("", [])
        with self.baglanti(expire=True) as connection:
            total = connection.execute("SELECT COUNT(*) FROM community_requests" + where, values).fetchone()[0]
            rows = connection.execute("SELECT * FROM community_requests" + where + " ORDER BY created_at DESC,id LIMIT ? OFFSET ?",
                                      [*values, limit, offset]).fetchall()
            return {"items": [self._gorunum(row) for row in rows], "total": total, "offset": offset, "limit": limit}

    def oku(self, identifier):
        identifier = _identifier(identifier)
        with self.baglanti(expire=True) as connection:
            row = connection.execute("SELECT * FROM community_requests WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise TalepHatasi("Talep bulunamadı.", 404)
            return self._gorunum(row, detail=True)

    def durum_degistir(self, identifier, status):
        identifier, status = _identifier(identifier), _status(status)
        with self.baglanti(expire=True) as connection:
            row = connection.execute("SELECT * FROM community_requests WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise TalepHatasi("Talep bulunamadı.", 404)
            if row["status"] != status:
                if status == "new" and connection.execute("SELECT COUNT(*) FROM community_requests WHERE status='new'").fetchone()[0] >= NEW_LIMIT:
                    raise TalepHatasi("Yeni talepler kutusu şu anda dolu.", 429)
                connection.execute("UPDATE community_requests SET status=?,updated_at=? WHERE id=?",
                                   (status, int(self.clock()), identifier))
                row = connection.execute("SELECT * FROM community_requests WHERE id=?", (identifier,)).fetchone()
            return self._gorunum(row, detail=True)
