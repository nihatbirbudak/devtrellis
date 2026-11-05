"""Hesaplı ve açık rızalı hesapsız katkıları, incelemeyi ve yayımlanan sürümleri ayrı depoda korur."""

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path

from radar.arastirma_arama import arama_gruplari
from radar.gelistirici_alani import (alanlari_dogrula, arama_metni, metin, paket_dogrula,
                                    paket_metni, surumu_dogrula)


INBOX_PENDING_LIMIT = 200
INBOX_TOTAL_LIMIT = 5000
INBOX_OWNER = "!devtrellis-inbox"
EVENT_RECEIPT_LIMIT = 100000
EVENT_RECEIPT_SECONDS = 7 * 86400
EVENT_COUNTERS = {"web_view": "web_views", "mcp_read": "mcp_reads", "helpful": "helpful",
                  "used_in_research": "used_in_research", "needs_update": "needs_update", "disputed": "disputed"}


class ToplulukHatasi(ValueError):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


def tarih_metni(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="seconds")


def parola_izi(password):
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise ValueError("Parola 12–256 karakter olmalı.")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 600000).hex()
    return f"pbkdf2_sha256$600000${salt}${digest}"


def parola_dogrula(password, stored):
    if not isinstance(password, str) or len(password) > 256:
        return False
    algorithm, rounds, salt, digest = stored.split("$")
    if algorithm != "pbkdf2_sha256" or rounds != "600000":
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(rounds)).hex()
    return hmac.compare_digest(actual, digest)


def kullanici_gorunumu(row):
    return {key: row[key] for key in ("id", "username", "display_name", "role")}


class ToplulukDeposu:
    def __init__(self, path, clock=None):
        self.path = Path(path)
        self.clock = clock or time.time
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.baglanti() as connection:
            connection.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS community_accounts (
                    id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('contributor','moderator')),
                    password_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS community_sessions (
                    token_hash TEXT PRIMARY KEY, user_id TEXT REFERENCES community_accounts(id), expires_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS community_entries (
                    id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES community_accounts(id),
                    version INTEGER NOT NULL, latest_revision INTEGER NOT NULL,
                    published_revision INTEGER, status TEXT NOT NULL,
                    created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, moderation_note TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS community_revisions (
                    entry_id TEXT NOT NULL REFERENCES community_entries(id), version INTEGER NOT NULL,
                    packet TEXT NOT NULL, kind TEXT NOT NULL, search_text TEXT NOT NULL, created_at INTEGER NOT NULL,
                    PRIMARY KEY(entry_id,version)
                );
                CREATE TABLE IF NOT EXISTS community_operations (
                    user_id TEXT NOT NULL REFERENCES community_accounts(id), request_key TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, result TEXT NOT NULL, PRIMARY KEY(user_id,request_key)
                );
                CREATE TABLE IF NOT EXISTS community_decisions (
                    id INTEGER PRIMARY KEY, entry_id TEXT NOT NULL REFERENCES community_entries(id),
                    revision INTEGER NOT NULL, moderator_id TEXT NOT NULL REFERENCES community_accounts(id),
                    decision TEXT NOT NULL, note TEXT NOT NULL, created_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS community_owner ON community_entries(owner_id,updated_at);
                CREATE INDEX IF NOT EXISTS community_queue ON community_entries(status,updated_at);
                CREATE TABLE IF NOT EXISTS community_inbox (
                    entry_id TEXT PRIMARY KEY REFERENCES community_entries(id),
                    capability_hash TEXT NOT NULL UNIQUE, request_key_hash TEXT NOT NULL,
                    packet_hash TEXT NOT NULL, consent_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS community_lineage (
                    entry_id TEXT NOT NULL, version INTEGER NOT NULL,
                    parent_id TEXT NOT NULL, parent_version INTEGER NOT NULL,
                    PRIMARY KEY(entry_id,version,parent_id),
                    FOREIGN KEY(entry_id,version) REFERENCES community_revisions(entry_id,version) ON DELETE CASCADE,
                    FOREIGN KEY(parent_id,parent_version) REFERENCES community_revisions(entry_id,version) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS community_parent ON community_lineage(parent_id,parent_version);
                CREATE TABLE IF NOT EXISTS community_public_revisions (
                    entry_id TEXT NOT NULL, version INTEGER NOT NULL, visible INTEGER NOT NULL CHECK(visible IN (0,1)),
                    PRIMARY KEY(entry_id,version),
                    FOREIGN KEY(entry_id,version) REFERENCES community_revisions(entry_id,version) ON DELETE CASCADE
                );
                INSERT OR IGNORE INTO community_public_revisions(entry_id,version,visible)
                    SELECT id,published_revision,1 FROM community_entries WHERE published_revision IS NOT NULL;
                CREATE TABLE IF NOT EXISTS community_engagement (
                    entry_id TEXT NOT NULL, version INTEGER NOT NULL, kind TEXT NOT NULL,
                    total INTEGER NOT NULL CHECK(total>=0), PRIMARY KEY(entry_id,version,kind),
                    FOREIGN KEY(entry_id,version) REFERENCES community_revisions(entry_id,version) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS community_event_receipts (
                    receipt_hash TEXT PRIMARY KEY, entry_id TEXT NOT NULL, version INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    FOREIGN KEY(entry_id,version) REFERENCES community_revisions(entry_id,version) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS community_receipt_expiry ON community_event_receipts(expires_at);
            """)
        self.dummy_hash = "pbkdf2_sha256$600000$" + "00" * 16 + "$" + "00" * 32

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
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def hesap_ekle(self, username, display_name, role, password):
        if not isinstance(username, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{3,40}", username):
            raise ValueError("Kullanıcı adı 3–40 harf, sayı, alt çizgi veya tire içermeli.")
        username = username.lower()
        display_name = metin(display_name, "Görünen ad", 100, required=True)
        if role not in {"contributor", "moderator"}:
            raise ValueError("Hesap rolü contributor veya moderator olmalı.")
        password_hash = parola_izi(password)
        account = {"id": uuid.uuid4().hex, "username": username, "display_name": display_name, "role": role}
        try:
            with self.baglanti(write=True) as connection:
                connection.execute("INSERT INTO community_accounts(id,username,display_name,role,password_hash,created_at) "
                                   "VALUES(?,?,?,?,?,?)", (*account.values(), password_hash, int(self.clock())))
        except sqlite3.IntegrityError as exc:
            raise ValueError("Bu kullanıcı adı zaten var.") from exc
        return account

    def kimlik_dogrula(self, username, password):
        if not isinstance(username, str) or not isinstance(password, str) or len(username) > 40 or len(password) > 256:
            raise ToplulukHatasi("Kullanıcı adı veya parola yanlış.", 401)
        with self.baglanti() as connection:
            account = connection.execute("SELECT * FROM community_accounts WHERE username=?", (username.lower(),)).fetchone()
        valid = parola_dogrula(password, account["password_hash"] if account else self.dummy_hash)
        if not valid or account is None or not account["active"]:
            raise ToplulukHatasi("Kullanıcı adı veya parola yanlış.", 401)
        return kullanici_gorunumu(account)

    def hesap_kapat(self, username):
        with self.baglanti(write=True) as connection:
            account = connection.execute("SELECT id FROM community_accounts WHERE username=?", (username.lower(),)).fetchone()
            if account is None:
                raise ValueError("Hesap bulunamadı.")
            connection.execute("UPDATE community_accounts SET active=0 WHERE id=?", (account["id"],))
            connection.execute("DELETE FROM community_sessions WHERE user_id=?", (account["id"],))

    def parola_yenile(self, username, password):
        password_hash = parola_izi(password)
        with self.baglanti(write=True) as connection:
            account = connection.execute("SELECT id FROM community_accounts WHERE username=?", (username.lower(),)).fetchone()
            if account is None:
                raise ValueError("Hesap bulunamadı.")
            connection.execute("UPDATE community_accounts SET password_hash=? WHERE id=?", (password_hash, account["id"]))
            connection.execute("DELETE FROM community_sessions WHERE user_id=?", (account["id"],))

    def oturum_ac(self, user_id=None, old_token=None):
        token = secrets.token_urlsafe(32)
        with self.baglanti(write=True) as connection:
            connection.execute("DELETE FROM community_sessions WHERE expires_at<=?", (int(self.clock()),))
            if old_token:
                connection.execute("DELETE FROM community_sessions WHERE token_hash=?", (self.oturum_izi(old_token),))
            connection.execute("INSERT INTO community_sessions VALUES(?,?,?)",
                               (self.oturum_izi(token), user_id, int(self.clock()) + 28800))
        return token

    @staticmethod
    def oturum_izi(token):
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def oturum_oku(self, token):
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            return None
        with self.baglanti() as connection:
            row = connection.execute("SELECT user_id FROM community_sessions WHERE token_hash=? AND expires_at>?",
                                     (self.oturum_izi(token), int(self.clock()))).fetchone()
            if row is None:
                return None
            account = None
            if row["user_id"]:
                account = connection.execute("SELECT * FROM community_accounts WHERE id=? AND active=1", (row["user_id"],)).fetchone()
                if account is None:
                    return None
            return {"user": kullanici_gorunumu(account) if account else None}

    def oturum_kapat(self, token):
        with self.baglanti(write=True) as connection:
            connection.execute("DELETE FROM community_sessions WHERE token_hash=?", (self.oturum_izi(token),))

    def kart(self, connection, entry_id, *, public=False, moderator=False, requested_version=None):
        row = connection.execute("SELECT e.*,a.username,a.display_name,i.entry_id AS inbox_id FROM community_entries e "
                                 "JOIN community_accounts a ON a.id=e.owner_id "
                                 "LEFT JOIN community_inbox i ON i.entry_id=e.id WHERE e.id=?", (entry_id,)).fetchone()
        if row is None or (public and row["published_revision"] is None):
            raise ToplulukHatasi("Kart bulunamadı.", 404)
        revision = row["published_revision"] if public else row["latest_revision"]
        if requested_version is not None:
            if not public or type(requested_version) is not int or not 1 <= requested_version <= 9223372036854775807:
                raise ValueError("İstenen yayın sürümü pozitif tam sayı olmalı.")
            approved = connection.execute("SELECT 1 FROM community_public_revisions WHERE entry_id=? AND version=? "
                                          "AND visible=1", (entry_id, requested_version)).fetchone()
            if approved is None:
                raise ToplulukHatasi("Yayımlanmış araştırma sürümü bulunamadı.", 404)
            revision = requested_version
        saved = connection.execute("SELECT packet,created_at FROM community_revisions WHERE entry_id=? AND version=?",
                                   (entry_id, revision)).fetchone()
        if saved is None:
            raise ToplulukHatasi("Yayımlanmış araştırma sürümü bulunamadı.", 404)
        card = {"id": row["id"], "owner": {"username": row["username"], "display_name": row["display_name"]},
                "version": revision if public else row["version"], "status": "published" if public else row["status"],
                "packet": json.loads(saved["packet"]), "created_at": tarih_metni(row["created_at"]),
                "updated_at": tarih_metni(saved["created_at"] if public else row["updated_at"]),
                "published_version": row["published_revision"]}
        card["relationships"] = self.iliski_gorunumu(connection, entry_id, card["packet"])
        card["engagement"] = self.etkilesim_gorunumu(connection, entry_id, revision)
        if row["inbox_id"]:
            card["owner"] = {"username": None, "display_name": "Hesapsız katkı", "verified": False}
            card["submission_type"] = "anonymous"
        if not public:
            card["moderation_note"] = row["moderation_note"]
        elif moderator:
            card["moderation_version"] = row["version"]
        return card

    @staticmethod
    def gelisimi_dogrula(connection, entry_id, packet):
        """Gönderimde ve yayında aynı işlem içinde kamusal sürümü ve döngüsüz atfı denetler."""
        for parent in packet.get("lineage", {}).get("parents", []):
            parent_id, parent_version = parent["research_id"], parent["version"]
            if parent_id == entry_id:
                raise ToplulukHatasi("Araştırma kendi gelişim kaynağı olamaz.", 409)
            saved = connection.execute(
                "SELECT r.kind FROM community_entries e JOIN community_revisions r ON r.entry_id=e.id "
                "WHERE e.id=? AND e.published_revision IS NOT NULL AND r.version=? "
                "AND EXISTS(SELECT 1 FROM community_public_revisions p WHERE p.entry_id=e.id "
                "AND p.version=r.version AND p.visible=1)", (parent_id, parent_version)).fetchone()
            if saved is None or saved["kind"] != "research":
                raise ToplulukHatasi("Geliştirilen araştırmanın yayımlanmış sürümü kullanılamıyor.", 409)
            cycle = connection.execute(
                "WITH RECURSIVE ancestors(id) AS (SELECT ? UNION "
                "SELECT l.parent_id FROM community_lineage l JOIN ancestors a ON l.entry_id=a.id) "
                "SELECT 1 FROM ancestors WHERE id=? LIMIT 1", (parent_id, entry_id)).fetchone()
            if cycle:
                raise ToplulukHatasi("Araştırma gelişim zinciri döngü içeremez.", 409)

    @staticmethod
    def gelisimi_kaydet(connection, entry_id, version, packet):
        connection.executemany("INSERT INTO community_lineage(entry_id,version,parent_id,parent_version) VALUES(?,?,?,?)",
            [(entry_id, version, parent["research_id"], parent["version"])
             for parent in packet.get("lineage", {}).get("parents", [])])

    @staticmethod
    def iliski_gorunumu(connection, entry_id, packet):
        parents = []
        for parent in packet.get("lineage", {}).get("parents", []):
            saved = connection.execute(
                "SELECT r.packet FROM community_entries e JOIN community_revisions r ON r.entry_id=e.id "
                "WHERE e.id=? AND e.published_revision IS NOT NULL AND r.version=? "
                "AND EXISTS(SELECT 1 FROM community_public_revisions p WHERE p.entry_id=e.id "
                "AND p.version=r.version AND p.visible=1)",
                (parent["research_id"], parent["version"])).fetchone()
            source = json.loads(saved["packet"]) if saved else None
            parents.append({"id": parent["research_id"], "version": parent["version"], "available": saved is not None,
                            "title": source["title"] if source else None, "author": source["author"] if source else None,
                            "license": source["license"] if source else None})
        selection = (" FROM community_lineage l JOIN community_entries e ON e.id=l.entry_id "
                     "AND e.published_revision=l.version JOIN community_revisions r ON r.entry_id=e.id "
                     "AND r.version=e.published_revision WHERE l.parent_id=?")
        total = connection.execute("SELECT COUNT(*)" + selection, (entry_id,)).fetchone()[0]
        children = connection.execute("SELECT e.id,r.version,r.packet" + selection +
                                      " ORDER BY r.created_at DESC,e.id LIMIT 20", (entry_id,)).fetchall()
        return {"parents": parents, "children": [{"id": child["id"], "version": child["version"],
                 "title": json.loads(child["packet"])["title"]} for child in children], "children_total": total}

    @staticmethod
    def etkilesim_gorunumu(connection, entry_id, version):
        result = dict.fromkeys(EVENT_COUNTERS.values(), 0)
        for row in connection.execute("SELECT kind,total FROM community_engagement WHERE entry_id=? AND version=?",
                                      (entry_id, version)):
            if row["kind"] in EVENT_COUNTERS:
                result[EVENT_COUNTERS[row["kind"]]] = row["total"]
        return result

    def etkilesim_ekle(self, entry_id, payload):
        """Kimliksiz sayaç tutar; yedi günlük tekrar fişinde IP, oturum veya ham olay kimliği saklamaz."""
        alanlari_dogrula(payload, {"kind", "version", "event_id"}, {"kind", "version", "event_id"})
        kind, version, event_id = payload["kind"], payload["version"], payload["event_id"]
        if not isinstance(kind, str) or kind not in EVENT_COUNTERS:
            raise ValueError("Etkileşim türü geçersiz.")
        if type(version) is not int or not 1 <= version <= 9223372036854775807:
            raise ValueError("Etkileşim için yayımlanmış sürüm gerekli.")
        if not isinstance(event_id, str) or not re.fullmatch(r"[a-f0-9]{32}", event_id):
            raise ValueError("Etkileşim kimliği 32 haneli rastgele kimlik olmalı.")
        now = int(self.clock())
        receipt = hashlib.sha256(f"{entry_id}:{version}:{kind}:{event_id}".encode("utf-8")).hexdigest()
        with self.baglanti(write=True) as connection:
            row = connection.execute("SELECT published_revision FROM community_entries WHERE id=?", (entry_id,)).fetchone()
            if row is None or row["published_revision"] is None:
                raise ToplulukHatasi("Kart bulunamadı.", 404)
            visible = connection.execute("SELECT 1 FROM community_public_revisions WHERE entry_id=? AND version=? AND visible=1",
                                         (entry_id, version)).fetchone()
            if visible is None:
                raise ToplulukHatasi("Araştırma sürümü yayında değil; güncel yayını yeniden açın.", 409)
            connection.execute("DELETE FROM community_event_receipts WHERE expires_at<=?", (now,))
            duplicate = connection.execute("SELECT 1 FROM community_event_receipts WHERE receipt_hash=?", (receipt,)).fetchone() is not None
            if not duplicate:
                if connection.execute("SELECT COUNT(*) FROM community_event_receipts").fetchone()[0] >= EVENT_RECEIPT_LIMIT:
                    raise ToplulukHatasi("Etkileşim kayıt alanı dolu; daha sonra tekrar deneyin.", 503)
                connection.execute("INSERT INTO community_event_receipts VALUES(?,?,?,?)",
                                   (receipt, entry_id, version, now + EVENT_RECEIPT_SECONDS))
                connection.execute("INSERT INTO community_engagement VALUES(?,?,?,1) "
                                   "ON CONFLICT(entry_id,version,kind) DO UPDATE SET total=MIN(total+1,9223372036854775807)",
                                   (entry_id, version, kind))
            return {"engagement": self.etkilesim_gorunumu(connection, entry_id, version), "duplicate": duplicate}

    @staticmethod
    def inbox_token_izi(token):
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            raise ToplulukHatasi("Geçerli gönderim yönetim anahtarı gerekli.", 401)
        return hashlib.sha256(token.encode("ascii")).hexdigest()

    @staticmethod
    def inbox_gorunumu(row):
        return {key: row[key] for key in ("id", "status", "version", "moderation_note")} | {
            "published_version": row["published_revision"], "created_at": tarih_metni(row["created_at"]),
            "updated_at": tarih_metni(row["updated_at"])}

    def inbox_satiri(self, connection, entry_id, capability_hash):
        row = connection.execute("SELECT e.* FROM community_entries e JOIN community_inbox i ON i.entry_id=e.id "
                                 "WHERE e.id=? AND i.capability_hash=?", (entry_id, capability_hash)).fetchone()
        if row is None:
            raise ToplulukHatasi("Gönderim bulunamadı.", 404)
        return row

    def inbox_gonder(self, token, payload):
        """Tek yönetim anahtarını tek katkıya bağlar; tekrar teslim yeni yayın veya yetki üretmez."""
        capability_hash = self.inbox_token_izi(token)
        alanlari_dogrula(payload, {"packet", "consent", "idempotency_key"}, {"packet", "consent", "idempotency_key"})
        if payload["consent"] is not True:
            raise ValueError("Topluluğa göndermek için açık paylaşım onayı gerekli.")
        request_key = payload["idempotency_key"]
        if not isinstance(request_key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", request_key):
            raise ValueError("Gönderim kimliği 8–128 harf, sayı, tire veya alt çizgi olmalı.")
        packet = paket_dogrula(payload["packet"])
        packet_text = paket_metni(packet)
        packet_hash = hashlib.sha256(packet_text.encode("utf-8")).hexdigest()
        request_hash = hashlib.sha256(request_key.encode("ascii")).hexdigest()
        now = int(self.clock())
        with self.baglanti(write=True) as connection:
            previous = connection.execute("SELECT entry_id,packet_hash FROM community_inbox WHERE capability_hash=?",
                                          (capability_hash,)).fetchone()
            if previous:
                if previous["packet_hash"] != packet_hash:
                    raise ToplulukHatasi("Bu yönetim anahtarı başka bir gönderime bağlı.", 409)
                row = self.inbox_satiri(connection, previous["entry_id"], capability_hash)
                return {"submission": self.inbox_gorunumu(row), "duplicate": True}
            total = connection.execute("SELECT COUNT(*) FROM community_inbox").fetchone()[0]
            pending = connection.execute("SELECT COUNT(*) FROM community_inbox i JOIN community_entries e "
                                         "ON e.id=i.entry_id WHERE e.status='pending'").fetchone()[0]
            if total >= INBOX_TOTAL_LIMIT or pending >= INBOX_PENDING_LIMIT:
                raise ToplulukHatasi("Hesapsız gönderim sırası dolu; daha sonra tekrar deneyin.", 503)
            # Giriş yapamayan iç sahip yalnız mevcut yabancı anahtar şemasını korur; kamu kimliği değildir.
            connection.execute("INSERT OR IGNORE INTO community_accounts(id,username,display_name,role,password_hash,active,created_at) "
                               "VALUES(?,?,?,'contributor',?,0,?)",
                               (INBOX_OWNER, INBOX_OWNER, "Hesapsız katkı", self.dummy_hash, now))
            entry_id = uuid.uuid4().hex
            self.gelisimi_dogrula(connection, entry_id, packet)
            connection.execute("INSERT INTO community_entries(id,owner_id,version,latest_revision,status,created_at,updated_at) "
                               "VALUES(?,?,1,1,'pending',?,?)", (entry_id, INBOX_OWNER, now, now))
            search_text = " ".join([packet["title"], packet["summary"], packet["body"], *packet["tags"], packet["author"]])
            connection.execute("INSERT INTO community_revisions VALUES(?,1,?,?,?,?)",
                               (entry_id, packet_text, packet["kind"], arama_metni(search_text), now))
            self.gelisimi_kaydet(connection, entry_id, 1, packet)
            connection.execute("INSERT INTO community_inbox VALUES(?,?,?,?,?)",
                               (entry_id, capability_hash, request_hash, packet_hash, now))
            row = self.inbox_satiri(connection, entry_id, capability_hash)
            return {"submission": self.inbox_gorunumu(row), "duplicate": False}

    def inbox_oku(self, token, entry_id):
        capability_hash = self.inbox_token_izi(token)
        with self.baglanti() as connection:
            return {"submission": self.inbox_gorunumu(self.inbox_satiri(connection, entry_id, capability_hash))}

    def inbox_bul(self, token):
        """Yanıtı kaybolmuş katkının yalnız sahibine teslim durumunu döndürür; içerik oluşturmaz."""
        capability_hash = self.inbox_token_izi(token)
        with self.baglanti() as connection:
            previous = connection.execute("SELECT entry_id FROM community_inbox WHERE capability_hash=?",
                                          (capability_hash,)).fetchone()
            if previous is None:
                raise ToplulukHatasi("Gönderim bulunamadı.", 404)
            return {"submission": self.inbox_gorunumu(self.inbox_satiri(connection, previous["entry_id"], capability_hash))}

    def inbox_geri_cek(self, token, entry_id, payload):
        capability_hash = self.inbox_token_izi(token)
        alanlari_dogrula(payload, {"expected_version"}, {"expected_version"})
        with self.baglanti(write=True) as connection:
            row = self.inbox_satiri(connection, entry_id, capability_hash)
            self.surum_kontrol(row, payload["expected_version"])
            if row["status"] != "withdrawn":
                connection.execute("UPDATE community_public_revisions SET visible=0 WHERE entry_id=?", (entry_id,))
                connection.execute("UPDATE community_entries SET status='withdrawn',published_revision=NULL,version=version+1,"
                                   "updated_at=? WHERE id=?", (int(self.clock()), entry_id))
            row = self.inbox_satiri(connection, entry_id, capability_hash)
            return {"submission": self.inbox_gorunumu(row)}

    @staticmethod
    def surum_kontrol(row, expected):
        surumu_dogrula(expected)
        if row["version"] != expected:
            raise ToplulukHatasi("Kart değişti; güncel sürümü yeniden açın.", 409)

    def gonder(self, user, payload, entry_id=None):
        alanlari_dogrula(payload, {"packet", "expected_version", "idempotency_key"},
                        {"packet", "expected_version", "idempotency_key"})
        packet = paket_dogrula(payload["packet"])
        surumu_dogrula(payload["expected_version"])
        request_key = payload["idempotency_key"]
        if not isinstance(request_key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", request_key):
            raise ValueError("Gönderim kimliği 8–128 harf, sayı, tire veya alt çizgi olmalı.")
        fingerprint = hashlib.sha256(paket_metni({"packet": packet, "entry_id": entry_id,
                                                "expected_version": payload["expected_version"]}).encode("utf-8")).hexdigest()
        now = int(self.clock())
        with self.baglanti(write=True) as connection:
            previous = connection.execute("SELECT * FROM community_operations WHERE user_id=? AND request_key=?",
                                          (user["id"], request_key)).fetchone()
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise ToplulukHatasi("Gönderim kimliği farklı bir işlemde kullanılmış.", 409)
                card = json.loads(previous["result"])
                # Tekrar teslim, sonradan gizlenen kaynağın eski atıf metnini geri açmamalı.
                card["relationships"] = self.iliski_gorunumu(connection, card["id"], card["packet"])
                card["engagement"] = self.etkilesim_gorunumu(connection, card["id"], card["version"])
                return {"card": card, "duplicate": True}
            if entry_id:
                row = connection.execute("SELECT * FROM community_entries WHERE id=?", (entry_id,)).fetchone()
                if row is None:
                    raise ToplulukHatasi("Kart bulunamadı.", 404)
                if row["owner_id"] != user["id"]:
                    raise ToplulukHatasi("Yalnız kendi kartınızı düzenleyebilirsiniz.", 403)
                self.surum_kontrol(row, payload["expected_version"])
                version = row["version"] + 1
                connection.execute("UPDATE community_entries SET version=?,latest_revision=?,status='pending',"
                                   "updated_at=?,moderation_note='' WHERE id=?", (version, version, now, entry_id))
            else:
                if payload["expected_version"] != 0:
                    raise ToplulukHatasi("Yeni kart için beklenen sürüm sıfır olmalı.", 409)
                entry_id, version = uuid.uuid4().hex, 1
                connection.execute("INSERT INTO community_entries(id,owner_id,version,latest_revision,status,created_at,updated_at) "
                                   "VALUES(?,?,1,1,'pending',?,?)", (entry_id, user["id"], now, now))
            search_text = " ".join([packet["title"], packet["summary"], packet["body"], *packet["tags"], user["display_name"]])
            self.gelisimi_dogrula(connection, entry_id, packet)
            connection.execute("INSERT INTO community_revisions VALUES(?,?,?,?,?,?)",
                               (entry_id, version, paket_metni(packet), packet["kind"], arama_metni(search_text), now))
            self.gelisimi_kaydet(connection, entry_id, version, packet)
            card = self.kart(connection, entry_id)
            connection.execute("INSERT INTO community_operations VALUES(?,?,?,?)",
                               (user["id"], request_key, fingerprint, paket_metni(card)))
            return {"card": card, "duplicate": False}

    def geri_cek(self, user, entry_id, payload):
        alanlari_dogrula(payload, {"expected_version"}, {"expected_version"})
        with self.baglanti(write=True) as connection:
            row = connection.execute("SELECT * FROM community_entries WHERE id=?", (entry_id,)).fetchone()
            if row is None:
                raise ToplulukHatasi("Kart bulunamadı.", 404)
            if row["owner_id"] != user["id"]:
                raise ToplulukHatasi("Yalnız kendi kartınızı geri çekebilirsiniz.", 403)
            self.surum_kontrol(row, payload["expected_version"])
            if row["status"] != "withdrawn":
                connection.execute("UPDATE community_public_revisions SET visible=0 WHERE entry_id=?", (entry_id,))
                connection.execute("UPDATE community_entries SET status='withdrawn',published_revision=NULL,version=version+1,"
                                   "updated_at=? WHERE id=?", (int(self.clock()), entry_id))
            return {"card": self.kart(connection, entry_id)}

    def karar(self, user, entry_id, payload):
        if user["role"] != "moderator":
            raise ToplulukHatasi("Bu işlem için moderatör hesabı gerekli.", 403)
        alanlari_dogrula(payload, {"decision", "expected_version", "note"}, {"decision", "expected_version"})
        if payload["decision"] not in ("publish", "reject"):
            raise ValueError("Karar publish veya reject olmalı.")
        note = metin(payload.get("note", ""), "İnceleme notu", 600, required=payload["decision"] == "reject", multiline=True)
        with self.baglanti(write=True) as connection:
            row = connection.execute("SELECT * FROM community_entries WHERE id=?", (entry_id,)).fetchone()
            if row is None:
                raise ToplulukHatasi("Kart bulunamadı.", 404)
            if row["owner_id"] == user["id"]:
                raise ToplulukHatasi("Kendi gönderiminizi başka bir moderatör incelemeli.", 403)
            self.surum_kontrol(row, payload["expected_version"])
            if row["status"] != "pending":
                raise ToplulukHatasi("Bu kart inceleme sırasında değil.", 409)
            if payload["decision"] == "publish":
                packet = json.loads(connection.execute("SELECT packet FROM community_revisions WHERE entry_id=? AND version=?",
                                    (entry_id, row["latest_revision"])).fetchone()["packet"])
                self.gelisimi_dogrula(connection, entry_id, packet)
            published = row["latest_revision"] if payload["decision"] == "publish" else row["published_revision"]
            status = "published" if payload["decision"] == "publish" else "rejected"
            now = int(self.clock())
            if payload["decision"] == "publish":
                connection.execute("INSERT INTO community_public_revisions VALUES(?,?,1) "
                                   "ON CONFLICT(entry_id,version) DO UPDATE SET visible=1", (entry_id, published))
            connection.execute("UPDATE community_entries SET status=?,published_revision=?,version=version+1,moderation_note=?,"
                               "updated_at=? WHERE id=?", (status, published, note, now, entry_id))
            connection.execute("INSERT INTO community_decisions(entry_id,revision,moderator_id,decision,note,created_at) "
                               "VALUES(?,?,?,?,?,?)", (entry_id, row["latest_revision"], user["id"], payload["decision"], note, now))
            return {"card": self.kart(connection, entry_id)}

    def yayindan_kaldir(self, user, entry_id, payload):
        if user["role"] != "moderator":
            raise ToplulukHatasi("Bu işlem için moderatör hesabı gerekli.", 403)
        alanlari_dogrula(payload, {"expected_version", "note"}, {"expected_version", "note"})
        note = metin(payload["note"], "Yayından kaldırma gerekçesi", 600, required=True, multiline=True)
        with self.baglanti(write=True) as connection:
            row = connection.execute("SELECT * FROM community_entries WHERE id=?", (entry_id,)).fetchone()
            if row is None:
                raise ToplulukHatasi("Kart bulunamadı.", 404)
            self.surum_kontrol(row, payload["expected_version"])
            if row["published_revision"] is None:
                raise ToplulukHatasi("Bu kartın yayında bir sürümü yok.", 409)
            now = int(self.clock())
            connection.execute("UPDATE community_public_revisions SET visible=0 WHERE entry_id=?", (entry_id,))
            connection.execute("UPDATE community_entries SET status='hidden',published_revision=NULL,version=version+1,"
                               "moderation_note=?,updated_at=? WHERE id=?", (note, now, entry_id))
            connection.execute("INSERT INTO community_decisions(entry_id,revision,moderator_id,decision,note,created_at) "
                               "VALUES(?,?,?,'hide',?,?)", (entry_id, row["published_revision"], user["id"], note, now))
            return {"card": self.kart(connection, entry_id)}

    def listele(self, *, user=None, review=False, query="", kind="", offset=0, limit=30, observer=None):
        query = metin(query, "Arama", 200)
        if kind not in ("", "research", "project"):
            raise ValueError("Kayıt türü geçersiz.")
        if type(offset) is not int or not 0 <= offset <= 9223372036854775807 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Sayfalama sınırı geçersiz.")
        public = user is None and not review
        if review and (user is None or user["role"] != "moderator"):
            raise ToplulukHatasi("Bu işlem için moderatör hesabı gerekli.", 403)
        revision_column = "published_revision" if public else "latest_revision"
        join = f" FROM community_entries e JOIN community_revisions r ON r.entry_id=e.id AND r.version=e.{revision_column}"
        conditions, values = [], []
        if review:
            conditions.append("e.status='pending'")
        elif user:
            conditions.append("e.owner_id=?")
            values.append(user["id"])
        if query:
            for group in arama_gruplari(query):
                conditions.append("(" + " OR ".join("instr(r.search_text,?)>0" for _ in group) + ")")
                values.extend(group)
        if kind:
            conditions.append("r.kind=?")
            values.append(kind)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self.baglanti() as connection:
            connection.execute("BEGIN")
            total = connection.execute("SELECT COUNT(*)" + join + where, values).fetchone()[0]
            rows = connection.execute("SELECT e.id" + join + where + " ORDER BY r.created_at DESC,e.id LIMIT ? OFFSET ?",
                                      [*values, limit, offset]).fetchall()
            moderator = observer is not None and observer["role"] == "moderator"
            result = {"items": [self.kart(connection, row["id"], public=public, moderator=moderator) for row in rows],
                      "total": total, "offset": offset, "limit": limit}
            if public:
                published_research = connection.execute("SELECT COUNT(*) FROM community_entries e "
                    "JOIN community_revisions r ON r.entry_id=e.id AND r.version=e.published_revision "
                    "WHERE r.kind='research'").fetchone()[0]
                result["library"] = {"published_research_total": published_research}
            return result

    def yayini_oku(self, entry_id, observer=None, version=None):
        with self.baglanti() as connection:
            connection.execute("BEGIN")
            return self.kart(connection, entry_id, public=True,
                             moderator=observer is not None and observer["role"] == "moderator", requested_version=version)

    def yedekle(self, target):
        return veritabani_kopyala(self.path, target)


def veritabani_kopyala(source, target, *, restore=False):
    """Tutarlı SQLite kopyasını yalnız yeni dosyaya yazar; var olan veriyi değiştirmez."""
    source, target = Path(source).resolve(), Path(target).resolve()
    if source == target or target.exists():
        raise ValueError("Hedef yeni bir dosya olmalı; mevcut veritabanının üzerine yazılmaz.")
    if not source.is_file():
        raise ValueError("Kaynak veritabanı bulunamadı.")
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original:
        if original.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Kaynak veritabanının bütünlük denetimi başarısız.")
        if original.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='community_accounts'").fetchone()[0] != 1:
            raise ValueError("Dosya topluluk veritabanı değil.")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb"):
            pass
        with closing(sqlite3.connect(target)) as copied:
            original.backup(copied)
            if restore:
                copied.execute("DELETE FROM community_sessions")
            copied.commit()
            if copied.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Kopyanın bütünlük denetimi başarısız.")
    return target
