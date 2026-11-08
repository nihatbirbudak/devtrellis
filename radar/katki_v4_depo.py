"""K1 v4 katkılarını ayrı SQLite deposunda tutar: denetim sırası, yeniden deneme, moderasyon, yayın, kaldırma ve durdurma anahtarı.

v1 topluluk deposundan (community.sqlite3) ayrı dosyadır; v1 tablolarına yazmaz. Katkıcı hesapsızdır: v1 hesapsız
kutusundaki gibi her gönderim yerelde üretilen tek bir yönetim anahtarına bağlıdır, sunucu yalnız özetini saklar.
Kişi başına sınır API katmanında, mevcut istek sınırlayıcının gizli anahtarlı adres özetiyle uygulanır; bu depo adres tutmaz.
"""

import hashlib
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import closing, contextmanager
from pathlib import Path

from radar.arastirma_arama import arama_gruplari
from radar.arastirma_siralama import (FIELD_WEIGHTS, INDEX_FIELDS, INDEX_VERSION, aday_kapsami, alan_agirligi,
                                      belge_terimleri, eslesme_bilgisi)
from radar.gelistirici_alani import arama_metni
from radar.katki_v4 import (K1Ayarlari, KatkiHatasi, aday_on_denetimi, arama_belgesi, arama_metni_v1,
                            bakis_govdesi, kapi_ozeti, kaldirma_izi, kapsam_govdesi, kayit_adresleri, kayit_olustur,
                            konu_govdesi, riza_denetimi, zaman_metni)
from radar.katki_v4 import arama_karti as v4_arama_karti

UYGULAMA_KIMLIGI = 0x44543441   # "DT4A"
SURUM = 1
DURUMLAR = ("queued", "retry_wait", "rejected", "unverifiable", "pending_review", "published", "review_rejected",
            "withdrawn", "removed")
SIRADA = ("queued", "retry_wait")
KIRA_SANIYE = 1800        # kapı bir raporda dakikalar sürebilir (30 kaynak × 30 sn üst sınır)
YAYINDAKI_EN_COK = 500    # yinelenen yayın denetimine verilen en yeni kayıt sayısı
TABLOLAR = {"v4_submissions", "v4_records", "v4_events", "v4_state"}
# Adres dizini ve arama dizini türetilmiş veridir (K1 ikinci dilim, 8 Kasım 2026): açılışta kayıtlardan yeniden kurulabilir.
# Sürüm değişince (`v4_state.search_index_version` ≠ ARAMA_DIZINI_SURUMU) iki dizin de bütünüyle yeniden doldurulur.
ARAMA_DIZINI_SURUMU = f"k1-2.{INDEX_VERSION}"   # k1-2: kayıtlı alan adı kuralıyla ürün etiketleri (alpha.11)
# Adresle bulmada aynı adreste birden çok kayıt: kaynak kartı önce, sonra rapor, sonra konu özeti; her grupta en yeni yayın.
TUR_SIRASI = "CASE r.kind WHEN 'source_card' THEN 0 WHEN 'research_report' THEN 1 ELSE 2 END"
# Yayımlanmadan sonlanan gönderimler: özel aday metni saklama süresi dolunca silinir (yayındaki kaydın metni zaten kamuda).
OZEL_SONLANAN = ("rejected", "unverifiable", "review_rejected", "withdrawn", "removed")
GUNLUK = logging.getLogger("devtrellis.k1")


def k1_deposu_dogrula(connection):
    """Yedek aracının rol kilidi: dosya K1 deposu mu (uygulama kimliği, sürüm ve tablolar)."""
    tablolar = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if (connection.execute("PRAGMA application_id").fetchone()[0] != UYGULAMA_KIMLIGI
            or connection.execute("PRAGMA user_version").fetchone()[0] != SURUM or not TABLOLAR <= tablolar):
        raise ValueError("SQLite deposu beklenen role ait değil.")


def ozel_metni_temizle(connection, *, sinir_saniye=None):
    """Yayımlanmadan sonlanan gönderimlerin aday metnini ve inen kayıtların gövdesini siler; iz ve sayılar kalır.
    `sinir_saniye` verilirse yalnız o zamandan önce sonlananlar; verilmezse hepsi (bulut profili)."""
    kosul, degerler = ("", ()) if sinir_saniye is None else (" AND updated_at<=?", (sinir_saniye,))
    yer = ",".join("?" * len(OZEL_SONLANAN))
    silinen = connection.execute(
        f"UPDATE v4_submissions SET candidate='{{}}',candidate_purged=1 WHERE candidate_purged=0 AND status IN ({yer})" + kosul,
        (*OZEL_SONLANAN, *degerler)).rowcount
    kosul = "" if sinir_saniye is None else " AND removed_at<=?"
    silinen += connection.execute("UPDATE v4_records SET record='{}' WHERE visible=0 AND record<>'{}'" + kosul,
                                  degerler).rowcount
    return silinen


def ozel_metin_kaldi_mi(connection):
    yer = ",".join("?" * len(OZEL_SONLANAN))
    return bool(connection.execute(f"SELECT 1 FROM v4_submissions WHERE candidate_purged=0 AND status IN ({yer}) LIMIT 1",
                                   OZEL_SONLANAN).fetchone() or
                connection.execute("SELECT 1 FROM v4_records WHERE visible=0 AND record<>'{}' LIMIT 1").fetchone())


def _token_izi(token):
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        raise KatkiHatasi("Geçerli gönderim yönetim anahtarı gerekli.", 401, code="capability_required")
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _surum(beklenen):
    if type(beklenen) is not int or not 1 <= beklenen <= 2 ** 62:
        raise KatkiHatasi("expected_revision pozitif tam sayı olmalı.", code="invalid_request")
    return beklenen


def _alanlar(payload, izinli, zorunlu):
    if not isinstance(payload, dict) or payload.keys() - izinli or zorunlu - payload.keys():
        raise KatkiHatasi("İstek alanları eksik ya da tanınmayan alan içeriyor.", code="invalid_request")


def _not(deger, ad, zorunlu):
    if deger is None and not zorunlu:
        return ""
    if not isinstance(deger, str) or len(deger) > 600 or (zorunlu and not deger.strip()) or any(
            ord(c) < 32 and c not in "\n\t" for c in deger):
        raise KatkiHatasi(f"{ad} en çok 600 karakterlik metin olmalı.", code="invalid_request")
    return deger.strip()


def durduran(aktor):
    """Durdurma anahtarını kimin kapattığı: `moderator:<id>` → moderator, komut satırı → cli; eski kayıt (aktörsüz) moderator sayılır."""
    if not isinstance(aktor, str) or aktor.startswith("moderator"):
        return "moderator"
    return "cli" if aktor == "cli" else aktor


class KatkiDeposu:
    def __init__(self, path, *, clock=None, origin):
        self.path = Path(path)
        self.clock = clock or time.time
        self.origin = origin
        self.kilit = threading.Lock()
        # Özel aday metni taşır: itiraz deposu gibi kısıtlı dizin ve dosya (K1 kabulü bulgu 7).
        from radar.ozel_dosya_izinleri import ozel_dosya_izni
        if not self.path.parent.exists():
            self.path.parent.mkdir(mode=0o700, parents=True)
        # K1 dizini ayrıdır ve her açılışta kısıtlanır: önceden açılmış olsa da (örn. umask 022 ile 0755) POSIX 0700 /
        # Windows korumalı DACL. mkdir modu umask'a bağlıdır; os.chmod değildir (Ubuntu CI bulgusu, 8 Kasım 2026).
        ozel_dosya_izni(self.path.parent, directory=True)
        try:
            os.close(os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        except FileExistsError:
            pass
        ozel_dosya_izni(self.path)
        with self.baglanti(write=True) as connection:
            kimlik = connection.execute("PRAGMA application_id").fetchone()[0]
            surum = connection.execute("PRAGMA user_version").fetchone()[0]
            tablolar = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if tablolar and (kimlik != UYGULAMA_KIMLIGI or surum != SURUM):
                raise ValueError("K1 deposu için ayrı, desteklenen bir veritabanı gerekli.")
            connection.executescript(f"""
                CREATE TABLE IF NOT EXISTS v4_submissions (
                    id TEXT PRIMARY KEY, capability_hash TEXT NOT NULL UNIQUE, request_key_hash TEXT NOT NULL,
                    kind TEXT NOT NULL, candidate TEXT NOT NULL, candidate_sha256 TEXT NOT NULL,
                    consent_choice TEXT NOT NULL CHECK(consent_choice='share'), consent_at INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ({",".join(repr(d) for d in DURUMLAR)})),
                    revision INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at INTEGER, lease_id TEXT, lease_until INTEGER,
                    gate_result TEXT, hold_reason TEXT, note TEXT NOT NULL DEFAULT '',
                    candidate_purged INTEGER NOT NULL DEFAULT 0 CHECK(candidate_purged IN (0,1)),
                    created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS v4_queue ON v4_submissions(status, next_attempt_at);
                CREATE TABLE IF NOT EXISTS v4_records (
                    id TEXT NOT NULL REFERENCES v4_submissions(id), version INTEGER NOT NULL,
                    kind TEXT NOT NULL, record TEXT NOT NULL,
                    path TEXT NOT NULL CHECK(path IN ('machine_gate','moderator')),
                    visible INTEGER NOT NULL CHECK(visible IN (0,1)), published_at INTEGER NOT NULL,
                    removed_at INTEGER, removal TEXT CHECK(removal IN ('withdrawn','removed')),
                    address_key TEXT,
                    PRIMARY KEY(id, version)
                );
                CREATE INDEX IF NOT EXISTS v4_public ON v4_records(visible, published_at);
                CREATE INDEX IF NOT EXISTS v4_address ON v4_records(address_key, visible);
                CREATE TABLE IF NOT EXISTS v4_events (
                    id INTEGER PRIMARY KEY, submission_id TEXT, actor TEXT NOT NULL, event TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS v4_state (name TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS v4_record_addresses (
                    id TEXT NOT NULL, version INTEGER NOT NULL, address_key TEXT NOT NULL, position INTEGER NOT NULL,
                    PRIMARY KEY(id, version, address_key),
                    FOREIGN KEY(id, version) REFERENCES v4_records(id, version) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS v4_addresses_key ON v4_record_addresses(address_key);
                CREATE TABLE IF NOT EXISTS v4_search (
                    id TEXT PRIMARY KEY, version INTEGER NOT NULL, kind TEXT NOT NULL, search_text TEXT NOT NULL,
                    published_at INTEGER NOT NULL,
                    FOREIGN KEY(id, version) REFERENCES v4_records(id, version) ON DELETE CASCADE
                );
                PRAGMA application_id={UYGULAMA_KIMLIGI};
                PRAGMA user_version={SURUM};
            """)
            self.search_index_available = False
            self.arama_indeksini_hazirla(connection)
        # WAL, v1 deposu gibi: arama okuma anlık görüntüsü açıkken geri çekme/kaldırma yazıcısı beklemez (K1 ikinci dilim
        # kabulü, düşük bulgu 2). Rol doğrulamasından sonra; kip dosyada kalıcıdır (alpha.10 kodu WAL dosyasını olduğu gibi
        # açar). Yedek aracı kopyayı bağımsız (DELETE) biçime çevirir; geri yüklenen dosya ilk açılışta yine WAL'a geçer.
        with closing(sqlite3.connect(self.path, timeout=10)) as connection:
            connection.execute("PRAGMA journal_mode=WAL")

    def _wal_bosalt(self):
        """Özel metin silindikten sonra WAL dosyasını ana dosyaya aktarıp sıfırlar: silinen sayfaların eski kopyası WAL'da
        kalmaz (DELETE kipindeki davranışla eş). Açık okuyucu varsa SQLite kısmen aktarır; kalan çerçeveler sonraki
        yazımlarla üzerine yazılır."""
        with closing(sqlite3.connect(self.path, timeout=10)) as connection:
            mesgul, _, _ = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if mesgul:
            GUNLUK.info("K1 WAL sıfırlaması açık okuyucu yüzünden ertelendi.")

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

    def _olay(self, connection, kimlik, aktor, olay, not_="", simdi=None):
        connection.execute("INSERT INTO v4_events(submission_id,actor,event,note,created_at) VALUES(?,?,?,?,?)",
                           (kimlik, aktor, olay, not_, int(simdi if simdi is not None else self.clock())))

    # ------------------------------------------------------------------ adres ve arama dizinleri (türetilmiş veri)
    def fts5_kur(self, connection):
        columns = [row[1] for row in connection.execute("PRAGMA table_info(v4_search_fts)").fetchall()]
        if columns and tuple(columns) != ("id", "version", *INDEX_FIELDS):
            connection.execute("DROP TABLE v4_search_fts")
        connection.execute("CREATE VIRTUAL TABLE IF NOT EXISTS v4_search_fts USING fts5("
                           "id UNINDEXED,version UNINDEXED," + ",".join(INDEX_FIELDS) + ",tokenize='ascii')")

    def arama_indeksini_hazirla(self, connection, *, zorla=False):
        """Açılışta (ya da `k1-reindex` ile) adres ve arama dizinlerini yayındaki kayıtlarla eşitler.

        Eski depoda (alpha.10) adres yalnız `v4_records.address_key` sütununda ve yalnız kaynak kartı içindi; burada her kaydın
        bütün adresleri `v4_record_addresses` tablosuna, yayındakilerin arama satırı `v4_search` (+ FTS5) tablosuna yazılır.
        Gövdesi silinmiş (saklama süresi dolmuş) eski iz için adres yalnız sütundan alınır. İşlem bağlantının işlemi içindedir.
        """
        try:
            self.fts5_kur(connection)
            self.search_index_available = True
        except sqlite3.OperationalError as exc:
            if "no such module: fts5" not in str(exc).lower():
                raise
        kayitli = connection.execute("SELECT value FROM v4_state WHERE name='search_index_version'").fetchone()
        yenile = zorla or kayitli is None or kayitli["value"] != ARAMA_DIZINI_SURUMU
        if yenile:
            connection.execute("DELETE FROM v4_record_addresses")
            connection.execute("DELETE FROM v4_search")
            if self.search_index_available:
                connection.execute("DELETE FROM v4_search_fts")
        rows = connection.execute("SELECT r.id,r.version,r.kind,r.record,r.visible,r.published_at,r.address_key FROM v4_records r "
                                  "WHERE NOT EXISTS (SELECT 1 FROM v4_record_addresses a WHERE a.id=r.id AND a.version=r.version) "
                                  "ORDER BY r.published_at,r.id").fetchall()
        for row in rows:
            kayit = json.loads(row["record"]) if row["record"] != "{}" else None
            adresler = kayit_adresleri(kayit) if kayit else ([row["address_key"]] if row["address_key"] else [])
            self._adresleri_yaz(connection, row["id"], row["version"], adresler)
        # Yayında olmayan kaydın arama satırı düşer; yayındaki eksikse yazılır.
        connection.execute("DELETE FROM v4_search WHERE NOT EXISTS (SELECT 1 FROM v4_records r WHERE r.id=v4_search.id "
                           "AND r.version=v4_search.version AND r.visible=1)")
        if self.search_index_available:
            connection.execute("DELETE FROM v4_search_fts WHERE rowid NOT IN (SELECT rowid FROM v4_search)")
        eksik = connection.execute("SELECT r.id,r.version,r.record,r.published_at FROM v4_records r WHERE r.visible=1 AND NOT EXISTS "
                                   "(SELECT 1 FROM v4_search s WHERE s.id=r.id AND s.version=r.version)").fetchall()
        for row in eksik:
            self._arama_satirini_yaz(connection, json.loads(row["record"]), row["published_at"])
        connection.execute("INSERT INTO v4_state VALUES('search_index_version',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",
                           (ARAMA_DIZINI_SURUMU,))
        return len(rows), len(eksik)

    @staticmethod
    def _adresleri_yaz(connection, kimlik, surum, adresler):
        connection.executemany("INSERT OR IGNORE INTO v4_record_addresses(id,version,address_key,position) VALUES(?,?,?,?)",
                               [(kimlik, surum, anahtar, sira) for sira, anahtar in enumerate(adresler)])

    def _arama_satirini_yaz(self, connection, kayit, yayin_saniye):
        kart = v4_arama_karti(kayit)
        connection.execute("DELETE FROM v4_search WHERE id=?", (kayit["id"],))
        cursor = connection.execute("INSERT INTO v4_search(id,version,kind,search_text,published_at) VALUES(?,?,?,?,?)",
                                    (kayit["id"], kayit["version"], kayit["kind"], arama_metni(arama_metni_v1(kart)),
                                     yayin_saniye))
        if self.search_index_available:
            alanlar = belge_terimleri(arama_belgesi(kart))
            connection.execute("DELETE FROM v4_search_fts WHERE id=?", (kayit["id"],))
            connection.execute("INSERT INTO v4_search_fts(rowid,id,version," + ",".join(INDEX_FIELDS) + ") VALUES(" +
                               ",".join("?" for _ in range(3 + len(INDEX_FIELDS))) + ")",
                               (cursor.lastrowid, kayit["id"], kayit["version"], *(alanlar[ad] for ad in INDEX_FIELDS)))

    def _arama_satirini_sil(self, connection, kimlik):
        if self.search_index_available:
            connection.execute("DELETE FROM v4_search_fts WHERE id=?", (kimlik,))
        connection.execute("DELETE FROM v4_search WHERE id=?", (kimlik,))

    def yeniden_indeksle(self):
        """`topluluk.py k1-reindex`: adres ve arama dizinlerini kayıtlardan bütünüyle yeniden kurar."""
        with self.baglanti(write=True) as connection:
            adres, arama = self.arama_indeksini_hazirla(connection, zorla=True)
            yayinda = connection.execute("SELECT COUNT(*) FROM v4_search").fetchone()[0]
            adresli = connection.execute("SELECT COUNT(DISTINCT address_key) FROM v4_record_addresses").fetchone()[0]
        return {"records_addressed": adres, "search_rows_written": arama, "published_indexed": yayinda,
                "distinct_addresses": adresli, "fts5": self.search_index_available, "index_version": ARAMA_DIZINI_SURUMU}

    def yayinda_sayisi(self):
        with self.baglanti() as connection:
            return connection.execute("SELECT COUNT(*) FROM v4_records WHERE visible=1").fetchone()[0]

    def arama_adaylari(self, connection, *, plan=None, query="", en_cok, puanlayici=None):
        """Yayındaki v4 kayıtlarının arama adayları, v1 `topluluk_depo.sirali_adaylar` ile aynı sıralama anahtarlarıyla.

        Sıralı kipte (plan verildi): kapsam, alan ağırlığı, bm25, yayın zamanı, kimlik. Katı kipte (query): v1 gibi
        `instr(search_text, …)` grupları, yayın zamanı. Dönüş: (toplam, [{"id","version","key","fields"}]) — `key` birleşik
        sıralamanın karşılaştırma anahtarıdır (küçük önce).

        `puanlayici` (v1 deposunun `bm25_olcegi`) verilirse anahtardaki bm25 bu dizinin kendi değeri yerine v1 dizininin
        istatistiğiyle hesaplanır; iki kaynağın puanı aynı ölçekte olur. Yeni puan kapsam/ağırlık grubu içindeki sırayı
        değiştirebildiği için bu kipte eşleşen bütün adaylar puanlanıp sıralanır, sonra ilk `en_cok` alınır.
        """
        if plan is not None:
            if not plan["match"] or not self.search_index_available:
                return 0, []

            def coverage(*values):
                return aday_kapsami(plan, dict(zip(INDEX_FIELDS, values)))

            def weight(*values):
                return alan_agirligi(plan, dict(zip(INDEX_FIELDS, values)))

            connection.create_function("devtrellis_coverage", len(INDEX_FIELDS), coverage, deterministic=True)
            connection.create_function("devtrellis_weight", len(INDEX_FIELDS), weight, deterministic=True)
            field_list = ",".join("f." + name for name in INDEX_FIELDS)
            coverage_sql = "devtrellis_coverage(" + field_list + ")"
            weights = ",".join(["0.0", "0.0"] + [repr(FIELD_WEIGHTS[name]) for name in INDEX_FIELDS])
            selection = (" FROM v4_search_fts f JOIN v4_search s ON s.rowid=f.rowid AND s.id=f.id "
                         "WHERE v4_search_fts MATCH ? AND " + coverage_sql + ">=0")
            total = connection.execute("SELECT COUNT(*)" + selection, (plan["match"],)).fetchone()[0]
            rows = connection.execute("SELECT s.id,s.version,s.published_at," + field_list + "," + coverage_sql +
                                      " AS coverage,devtrellis_weight(" + field_list + ") AS weight,bm25(v4_search_fts," + weights +
                                      ") AS relevance" + selection + " ORDER BY coverage DESC,weight DESC,relevance ASC,"
                                      "s.published_at DESC,s.id" + ("" if puanlayici else " LIMIT ?"),
                                      (plan["match"],) if puanlayici else (plan["match"], en_cok)).fetchall()
            adaylar = []
            for row in rows:
                fields = {name: row[name] for name in INDEX_FIELDS}
                relevance = puanlayici(fields) if puanlayici else row["relevance"]
                adaylar.append({"id": row["id"], "version": row["version"], "fields": fields,
                                "key": (-row["coverage"], -row["weight"], relevance, -row["published_at"], row["id"])})
            if puanlayici:
                adaylar.sort(key=lambda aday: aday["key"])
            return total, adaylar[:en_cok]
        conditions, values = [], []
        if query:
            for group in arama_gruplari(query):
                conditions.append("(" + " OR ".join("instr(search_text,?)>0" for _ in group) + ")")
                values.extend(group)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        total = connection.execute("SELECT COUNT(*) FROM v4_search" + where, values).fetchone()[0]
        rows = connection.execute("SELECT id,version,published_at FROM v4_search" + where +
                                  " ORDER BY published_at DESC,id LIMIT ?", [*values, en_cok]).fetchall()
        return total, [{"id": row["id"], "version": row["version"], "key": (-row["published_at"], row["id"]), "fields": None}
                       for row in rows]

    def arama_karti(self, connection, kimlik, surum, *, plan=None, fields=None):
        row = connection.execute("SELECT record FROM v4_records WHERE id=? AND version=? AND visible=1", (kimlik, surum)).fetchone()
        if row is None:
            return None
        kart = v4_arama_karti(json.loads(row["record"]))
        if plan is not None:
            kart["search_match"] = eslesme_bilgisi(plan, fields if fields is not None else belge_terimleri(arama_belgesi(kart)))
        return kart

    def arama_kaynagi(self):
        """v1 `ToplulukDeposu.listele(ek_arama=…)` için uyarlayıcı: tek okuma işlemi içinde adaylar ve kartlar."""
        return _AramaKaynagi(self)

    # ------------------------------------------------------------------ görünümler
    @staticmethod
    def _gate(row, *, tam=False):
        return kapi_ozeti(json.loads(row["gate_result"]), tam=tam) if row["gate_result"] else None

    def _yayin(self, connection, kimlik):
        return connection.execute("SELECT version,visible,removal FROM v4_records WHERE id=? ORDER BY version DESC LIMIT 1",
                                  (kimlik,)).fetchone()

    def gorunum(self, connection, row, *, tam_kapi=True):
        yayin = self._yayin(connection, row["id"])
        return {"id": row["id"], "kind": row["kind"], "status": row["status"], "revision": row["revision"],
                "attempts": row["attempts"],
                "next_attempt_at": zaman_metni(row["next_attempt_at"]) if row["status"] == "retry_wait" else None,
                "hold_reason": row["hold_reason"] if row["status"] == "pending_review" else None,
                "note": row["note"] or None,
                "gate": self._gate(row, tam=tam_kapi),
                "published_version": yayin["version"] if yayin and yayin["visible"] else None,
                "link": (f"{self.origin}/api/community/v4/records/{row['id']}/v/{yayin['version']}"
                         if yayin and yayin["visible"] else None),
                "created_at": zaman_metni(row["created_at"]), "updated_at": zaman_metni(row["updated_at"])}

    def _sahip_satiri(self, connection, kimlik, iz):
        row = connection.execute("SELECT * FROM v4_submissions WHERE id=? AND capability_hash=?", (kimlik, iz)).fetchone()
        if row is None:
            raise KatkiHatasi("Gönderim bulunamadı.", 404, code="not_found")
        return row

    # ------------------------------------------------------------------ katkıcı
    def gonder(self, token, payload, *, ayarlar, on_denetim):
        """Önizlenen paket + açık Paylaş seçimi + gönderim kimliği. Ağsız kapı ret verirse hiçbir şey saklanmaz."""
        iz = _token_izi(token)
        _alanlar(payload, {"candidate", "consent", "idempotency_key"}, {"candidate", "consent", "idempotency_key"})
        istek = payload["idempotency_key"]
        if not isinstance(istek, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", istek):
            raise KatkiHatasi("idempotency_key 8–128 harf, sayı, tire ya da alt çizgi olmalı.", code="invalid_request")
        aday = aday_on_denetimi(payload["candidate"])
        ozet = riza_denetimi(payload["consent"], aday)
        istek_izi = hashlib.sha256(istek.encode("ascii")).hexdigest()
        with self.baglanti() as connection:
            onceki = connection.execute("SELECT * FROM v4_submissions WHERE capability_hash=?", (iz,)).fetchone()
            if onceki is not None:
                if onceki["candidate_sha256"] != ozet or onceki["request_key_hash"] != istek_izi:
                    raise KatkiHatasi("Bu yönetim anahtarı başka bir gönderime bağlı.", 409, code="capability_in_use")
                return {"submission": self.gorunum(connection, onceki), "duplicate": True}
        # Ağsız kapı (şema, gizli bilgi, yönerge, sağlayıcı, kısa alıntı, adres biçimi): ret hemen ve saklamadan döner.
        on_sonuc = on_denetim(aday, ag=False, yayindakiler=None)
        if on_sonuc["hukum"] == "reddedilir":
            raise KatkiHatasi("Katkı makine denetiminden geçmedi; nedenleri düzeltip yeniden önizleyin.", 422,
                              code="gate_rejected", gate=kapi_ozeti(on_sonuc, tam=True))
        simdi = int(self.clock())
        with self.baglanti(write=True) as connection:
            onceki = connection.execute("SELECT * FROM v4_submissions WHERE capability_hash=?", (iz,)).fetchone()
            if onceki is not None:
                if onceki["candidate_sha256"] != ozet or onceki["request_key_hash"] != istek_izi:
                    raise KatkiHatasi("Bu yönetim anahtarı başka bir gönderime bağlı.", 409, code="capability_in_use")
                return {"submission": self.gorunum(connection, onceki), "duplicate": True}
            sirada = connection.execute(f"SELECT COUNT(*) FROM v4_submissions WHERE status IN {SIRADA}").fetchone()[0]
            incelemede = connection.execute("SELECT COUNT(*) FROM v4_submissions WHERE status='pending_review'").fetchone()[0]
            if sirada >= ayarlar.bekleyen_en_cok or incelemede >= ayarlar.moderasyon_en_cok:
                # Yalnız yeni gönderim durur (429); okuma, moderasyon ve durum uçları etkilenmez (K1 kabulü bulgu 8).
                raise KatkiHatasi("Denetim ya da moderasyon sırası dolu; daha sonra yeniden deneyin.", 429, code="queue_full",
                                  retry_after=3600)
            kimlik = uuid.uuid4().hex
            connection.execute(
                "INSERT INTO v4_submissions(id,capability_hash,request_key_hash,kind,candidate,candidate_sha256,"
                "consent_choice,consent_at,status,revision,created_at,updated_at) VALUES(?,?,?,?,?,?,'share',?,'queued',1,?,?)",
                (kimlik, iz, istek_izi, aday["kind"], json.dumps(aday, ensure_ascii=False, sort_keys=True), ozet,
                 simdi, simdi, simdi))
            self._olay(connection, kimlik, "contributor", "submitted", simdi=simdi)
            row = connection.execute("SELECT * FROM v4_submissions WHERE id=?", (kimlik,)).fetchone()
            return {"submission": self.gorunum(connection, row), "duplicate": False}

    def oku(self, token, kimlik):
        iz = _token_izi(token)
        with self.baglanti() as connection:
            return {"submission": self.gorunum(connection, self._sahip_satiri(connection, kimlik, iz))}

    def bul(self, token):
        """Yanıtı kaybolmuş gönderimin durumunu yalnız anahtar sahibine döndürür."""
        iz = _token_izi(token)
        with self.baglanti() as connection:
            row = connection.execute("SELECT * FROM v4_submissions WHERE capability_hash=?", (iz,)).fetchone()
            if row is None:
                raise KatkiHatasi("Gönderim bulunamadı.", 404, code="not_found")
            return {"submission": self.gorunum(connection, row)}

    def geri_cek(self, token, kimlik, payload):
        iz = _token_izi(token)
        _alanlar(payload, {"expected_revision"}, {"expected_revision"})
        beklenen = _surum(payload["expected_revision"])
        simdi = int(self.clock())
        with self.baglanti(write=True) as connection:
            row = self._sahip_satiri(connection, kimlik, iz)
            if row["revision"] != beklenen:
                raise KatkiHatasi("Gönderim değişti; güncel durumu yeniden açın.", 409, code="revision_conflict")
            if row["status"] not in ("withdrawn", "removed"):
                if connection.execute("UPDATE v4_records SET visible=0,removed_at=?,removal='withdrawn' WHERE id=? AND visible=1",
                                      (simdi, kimlik)).rowcount:
                    self._arama_satirini_sil(connection, kimlik)
                    self._revizyon_artir(connection)
                connection.execute("UPDATE v4_submissions SET status='withdrawn',revision=revision+1,lease_id=NULL,"
                                   "lease_until=NULL,next_attempt_at=NULL,updated_at=? WHERE id=?", (simdi, kimlik))
                self._olay(connection, kimlik, "contributor", "withdrawn", simdi=simdi)
            return {"submission": self.gorunum(connection, self._sahip_satiri(connection, kimlik, iz))}

    # ------------------------------------------------------------------ durdurma anahtarı
    def durum(self, ayarlar):
        """Etkin yayın durumu: yapılandırmadaki anahtar ya da moderatörün anahtarı otomatik yayını durdurur."""
        with self.baglanti() as connection:
            kayit = connection.execute("SELECT value FROM v4_state WHERE name='publishing_stop'").fetchone()
            gun = connection.execute("SELECT COUNT(*) FROM v4_records WHERE path='machine_gate' AND published_at>?",
                                     (int(self.clock()) - 86400,)).fetchone()[0]
            sayilar = dict(connection.execute("SELECT status,COUNT(*) FROM v4_submissions GROUP BY status").fetchall())
        anahtar = json.loads(kayit["value"]) if kayit else None
        durdu = ayarlar.yayin_durduruldu or anahtar is not None
        return {"auto_publish_configured": ayarlar.otomatik_yayin,
                "stopped": durdu,
                "stopped_by": "config" if ayarlar.yayin_durduruldu else durduran(anahtar.get("by")) if anahtar else None,
                "stopped_at": anahtar["at"] if anahtar else None,
                "stop_reason": anahtar["reason"] if anahtar else None,
                "auto_publish_effective": ayarlar.otomatik_yayin and not durdu,
                "auto_published_last_24h": gun, "auto_publish_per_day": ayarlar.otomatik_yayin_gun,
                "counts": {durum: sayilar.get(durum, 0) for durum in DURUMLAR}}

    def durdur(self, aktor, neden, ayarlar):
        """Tek işlem: yeni otomatik yayın durur; yayındakiler yerinde kalır; kapıdan geçenler moderasyona gider."""
        neden = _not(neden, "Durdurma gerekçesi", False)
        simdi = int(self.clock())
        with self.baglanti(write=True) as connection:
            if connection.execute("SELECT 1 FROM v4_state WHERE name='publishing_stop'").fetchone() is None:
                connection.execute("INSERT INTO v4_state VALUES('publishing_stop',?)",
                                   (json.dumps({"at": zaman_metni(simdi), "reason": neden or None, "by": aktor}),))
                self._olay(connection, None, aktor, "publishing_stopped", neden, simdi=simdi)
        return self.durum(ayarlar)

    def devam_et(self, aktor, ayarlar):
        """Moderatörün anahtarını kaldırır; yapılandırmadaki anahtar yalnız ayar dosyasından kalkar."""
        simdi = int(self.clock())
        with self.baglanti(write=True) as connection:
            if connection.execute("DELETE FROM v4_state WHERE name='publishing_stop'").rowcount:
                self._olay(connection, None, aktor, "publishing_resumed", simdi=simdi)
        return self.durum(ayarlar)

    # ------------------------------------------------------------------ işçi
    def siradakini_al(self, ayarlar=None):
        """Zamanı gelmiş bir kaydı kirayla alır; aynı kayıt iki işçide aynı anda denetlenmez.

        Deneme sayısı işe başlarken yazılır (K1 kabulü bulgu 14): kapı çalışırken süreç ölürse kira düşünce kayıt yeniden
        alınır, ama sayı artmış olur; üst sınıra varan kayıt yeniden denenmez, `unverifiable` olur.
        """
        en_cok = (ayarlar or K1Ayarlari()).deneme_en_cok
        simdi = int(self.clock())
        kira = secrets.token_hex(16)
        with self.baglanti(write=True) as connection:
            while True:
                row = connection.execute(
                    "SELECT id,candidate,attempts FROM v4_submissions WHERE (status='queued' OR (status='retry_wait' AND "
                    "next_attempt_at<=?)) AND (lease_until IS NULL OR lease_until<=?) "
                    "ORDER BY COALESCE(next_attempt_at,created_at),created_at LIMIT 1", (simdi, simdi)).fetchone()
                if row is None:
                    return None
                if row["attempts"] < en_cok:
                    break
                connection.execute("UPDATE v4_submissions SET status='unverifiable',lease_id=NULL,lease_until=NULL,"
                                   "next_attempt_at=NULL,revision=revision+1,updated_at=? WHERE id=?", (simdi, row["id"]))
                self._olay(connection, row["id"], "gate", "unverifiable", "attempts_exhausted", simdi=simdi)
            connection.execute("UPDATE v4_submissions SET lease_id=?,lease_until=?,attempts=attempts+1 WHERE id=?",
                               (kira, simdi + KIRA_SANIYE, row["id"]))
            return row["id"], kira, json.loads(row["candidate"])

    def yayindakiler(self):
        """Yinelenen yayın denetimi için yayındaki kayıtların başlık ve iddiaları (kapının beklediği biçim)."""
        with self.baglanti() as connection:
            rows = connection.execute("SELECT r.record FROM v4_records r WHERE r.visible=1 ORDER BY r.published_at DESC LIMIT ?",
                                      (YAYINDAKI_EN_COK,)).fetchall()
        liste = []
        for row in rows:
            kayit = json.loads(row["record"])
            liste.append({"id": kayit["id"], "title": kayit["content"].get("title", ""),
                          "claims": [{"text": i["text"], "quote": i["evidence"]["quote"],
                                      "source_url": i["evidence"]["source_url"]} for i in kayit["claims"]]})
        return liste

    def isle(self, *, ayarlar, denetci, en_cok=20):
        """Sıradaki kayıtları kapıdan geçirir. Kapı yavaştır; kira dışında veritabanı kilidi tutulmaz."""
        islenen = []
        with self.kilit:
            self.temizle(ayarlar)
            for _ in range(en_cok):
                alinan = self.siradakini_al(ayarlar)
                if alinan is None:
                    break
                kimlik, kira, aday = alinan
                try:
                    sonuc = denetci(aday, yayindakiler=self.yayindakiler(), ag=ayarlar.denetim_agi)
                except Exception as exc:
                    # Günlüğe yalnız hata türü yazılır; aday metni ve hata iletisi özel içerik taşıyabilir.
                    GUNLUK.error("K1 kapısı bir gönderimde beklenmeyen hatayla durdu (%s); yeniden denenecek.",
                                 type(exc).__name__)
                    sonuc = None
                islenen.append(self.sonucu_yaz(kimlik, kira, sonuc, ayarlar=ayarlar))
        return islenen

    def temizle(self, ayarlar):
        """Saklama süresi dolan özel aday metnini siler (varsayılan 30 gün; ayar `private_retention_days`)."""
        sinir = int(self.clock()) - ayarlar.ozel_metin_saklama_gun * 86400
        with self.baglanti(write=True) as connection:
            connection.execute("PRAGMA secure_delete=ON")
            silinen = ozel_metni_temizle(connection, sinir_saniye=sinir)
        if silinen:
            self._wal_bosalt()
        return silinen

    def _bekleme(self, deneme, ayarlar):
        return min(ayarlar.deneme_en_uzun_saniye, ayarlar.deneme_ilk_saniye * 2 ** max(0, deneme - 1))

    def sonucu_yaz(self, kimlik, kira, sonuc, *, ayarlar):
        """Kapı hükmünü uygular. Durdurma anahtarı ve otomatik yayın ayarı yazma işlemi içinde okunur."""
        simdi = int(self.clock())
        with self.baglanti(write=True) as connection:
            row = connection.execute("SELECT * FROM v4_submissions WHERE id=?", (kimlik,)).fetchone()
            if row is None or row["lease_id"] != kira or row["status"] not in SIRADA:
                # Arada geri çekildi ya da kira başka işçiye geçti: bu sonuç yok sayılır.
                self._olay(connection, kimlik, "gate", "result_discarded", simdi=simdi)
                return {"id": kimlik, "status": row["status"] if row else None, "discarded": True}
            if sonuc is None:
                hukum = "yayimlanamaz"
                kayit_sonucu = None
            else:
                hukum = sonuc["hukum"]
                kayit_sonucu = json.dumps(sonuc, ensure_ascii=False, sort_keys=True)
            deneme = row["attempts"]   # işe başlarken artırıldı (siradakini_al)
            alanlar = {"attempts": deneme, "gate_result": kayit_sonucu if kayit_sonucu else row["gate_result"],
                       "lease_id": None, "lease_until": None, "next_attempt_at": None, "hold_reason": None}
            if hukum == "reddedilir":
                alanlar["status"] = "rejected"
            elif hukum == "yayimlanamaz":
                if deneme >= ayarlar.deneme_en_cok:
                    alanlar["status"] = "unverifiable"
                else:
                    alanlar["status"] = "retry_wait"
                    alanlar["next_attempt_at"] = simdi + self._bekleme(deneme, ayarlar)
            else:
                alanlar["status"], alanlar["hold_reason"] = self._yayin_karari(connection, ayarlar, simdi)
            atama = ",".join(f"{ad}=?" for ad in alanlar)
            connection.execute(f"UPDATE v4_submissions SET {atama},revision=revision+1,updated_at=? WHERE id=?",
                               (*alanlar.values(), simdi, kimlik))
            if alanlar["status"] == "published":
                self._yayinla(connection, kimlik, json.loads(row["candidate"]), sonuc, "machine_gate", simdi)
            self._olay(connection, kimlik, "gate", alanlar["status"], alanlar["hold_reason"] or "", simdi=simdi)
            return {"id": kimlik, "status": alanlar["status"], "verdict": hukum, "hold_reason": alanlar["hold_reason"]}

    def _yayin_karari(self, connection, ayarlar, simdi):
        if not ayarlar.otomatik_yayin:
            return "pending_review", "auto_publish_off"
        if ayarlar.yayin_durduruldu or connection.execute(
                "SELECT 1 FROM v4_state WHERE name='publishing_stop'").fetchone() is not None:
            return "pending_review", "publishing_stopped"
        gun = connection.execute("SELECT COUNT(*) FROM v4_records WHERE path='machine_gate' AND published_at>?",
                                 (simdi - 86400,)).fetchone()[0]
        if gun >= ayarlar.otomatik_yayin_gun:
            return "pending_review", "auto_publish_daily_cap"
        return "published", None

    def _yayinla(self, connection, kimlik, aday, sonuc, yol, simdi):
        surum = (connection.execute("SELECT MAX(version) FROM v4_records WHERE id=?", (kimlik,)).fetchone()[0] or 0) + 1
        kayit = kayit_olustur(aday, sonuc, kayit_id=kimlik, surum=surum, yol=yol, yayin_saniye=simdi, origin=self.origin)
        # Her tür adres dizinine girer (K1 ikinci dilim): iddiaların ve `sources[]` listesinin bütün kanonik adresleri
        # `v4_record_addresses` tablosunda; eski `address_key` sütunu ilk adresle dolar (eski sürüm ve yedek araçları için).
        adresler = kayit_adresleri(kayit)
        connection.execute("INSERT INTO v4_records(id,version,kind,record,path,visible,published_at,address_key) "
                           "VALUES(?,?,?,?,?,1,?,?)",
                           (kimlik, surum, aday["kind"], json.dumps(kayit, ensure_ascii=False, sort_keys=True), yol, simdi,
                            adresler[0] if adresler else None))
        self._adresleri_yaz(connection, kimlik, surum, adresler)
        # Yayınla aynı işlemde arama dizinine girer; geri çekme ve kaldırma aynı işlemde düşürür.
        self._arama_satirini_yaz(connection, kayit, simdi)
        self._revizyon_artir(connection)

    @staticmethod
    def _revizyon_artir(connection):
        """Kamu görünürlüğünü değiştiren her işlem aynı işlemde kütüphane revizyonunu artırır (r1 §7)."""
        connection.execute("INSERT INTO v4_state VALUES('library_revision','1') "
                           "ON CONFLICT(name) DO UPDATE SET value=CAST(value AS INTEGER)+1")

    @staticmethod
    def _revizyon(connection):
        row = connection.execute("SELECT value FROM v4_state WHERE name='library_revision'").fetchone()
        return int(row["value"]) if row else 0

    # ------------------------------------------------------------------ moderatör
    def inceleme_listele(self, *, status="pending_review", offset=0, limit=25):
        if status not in DURUMLAR:
            raise KatkiHatasi("Durum tanınmıyor.", code="invalid_request")
        with self.baglanti() as connection:
            toplam = connection.execute("SELECT COUNT(*) FROM v4_submissions WHERE status=?", (status,)).fetchone()[0]
            rows = connection.execute("SELECT * FROM v4_submissions WHERE status=? ORDER BY updated_at,id LIMIT ? OFFSET ?",
                                      (status, limit, offset)).fetchall()
            return {"items": [self.gorunum(connection, row, tam_kapi=False) for row in rows], "total": toplam,
                    "offset": offset, "limit": limit}

    def inceleme_oku(self, kimlik):
        with self.baglanti() as connection:
            row = connection.execute("SELECT * FROM v4_submissions WHERE id=?", (kimlik,)).fetchone()
            if row is None:
                raise KatkiHatasi("Gönderim bulunamadı.", 404, code="not_found")
            gorunum = self.gorunum(connection, row)
            gorunum["candidate"] = json.loads(row["candidate"])
            gorunum["gate_result"] = json.loads(row["gate_result"]) if row["gate_result"] else None
            return {"submission": gorunum}

    def karar(self, kullanici, kimlik, payload):
        """Moderasyon kuyruğundaki (kapıdan geçmiş) kayıt için yayımla ya da reddet. Kapının reddini moderatör çeviremez."""
        _alanlar(payload, {"decision", "expected_revision", "note"}, {"decision", "expected_revision"})
        if payload["decision"] not in ("publish", "reject"):
            raise KatkiHatasi("decision publish ya da reject olmalı.", code="invalid_request")
        beklenen = _surum(payload["expected_revision"])
        not_ = _not(payload.get("note"), "İnceleme notu", payload["decision"] == "reject")
        simdi = int(self.clock())
        with self.baglanti(write=True) as connection:
            row = connection.execute("SELECT * FROM v4_submissions WHERE id=?", (kimlik,)).fetchone()
            if row is None:
                raise KatkiHatasi("Gönderim bulunamadı.", 404, code="not_found")
            if row["revision"] != beklenen:
                raise KatkiHatasi("Gönderim değişti; güncel durumu yeniden açın.", 409, code="revision_conflict")
            if row["status"] != "pending_review":
                raise KatkiHatasi("Bu gönderim moderasyon kuyruğunda değil.", 409, code="not_in_review")
            durum = "published" if payload["decision"] == "publish" else "review_rejected"
            connection.execute("UPDATE v4_submissions SET status=?,note=?,hold_reason=NULL,revision=revision+1,updated_at=? "
                               "WHERE id=?", (durum, not_, simdi, kimlik))
            if durum == "published":
                self._yayinla(connection, kimlik, json.loads(row["candidate"]), json.loads(row["gate_result"]),
                              "moderator", simdi)
            self._olay(connection, kimlik, f"moderator:{kullanici['id']}", durum, not_, simdi=simdi)
            return {"submission": self.gorunum(connection, connection.execute(
                "SELECT * FROM v4_submissions WHERE id=?", (kimlik,)).fetchone())}

    def kaldir(self, kullanici, kimlik, payload):
        """Kaldırma talebi üzerine moderatör kararı: kayıt yayından iner, asgari iz kalır."""
        _alanlar(payload, {"expected_revision", "note"}, {"expected_revision", "note"})
        beklenen = _surum(payload["expected_revision"])
        not_ = _not(payload["note"], "Kaldırma gerekçesi", True)
        simdi = int(self.clock())
        with self.baglanti(write=True) as connection:
            row = connection.execute("SELECT * FROM v4_submissions WHERE id=?", (kimlik,)).fetchone()
            if row is None:
                raise KatkiHatasi("Gönderim bulunamadı.", 404, code="not_found")
            if row["revision"] != beklenen:
                raise KatkiHatasi("Gönderim değişti; güncel durumu yeniden açın.", 409, code="revision_conflict")
            if row["status"] != "published":
                raise KatkiHatasi("Bu gönderimin yayında sürümü yok.", 409, code="not_published")
            if connection.execute("UPDATE v4_records SET visible=0,removed_at=?,removal='removed' WHERE id=? AND visible=1",
                                  (simdi, kimlik)).rowcount:
                self._arama_satirini_sil(connection, kimlik)
                self._revizyon_artir(connection)
            connection.execute("UPDATE v4_submissions SET status='removed',note=?,revision=revision+1,updated_at=? WHERE id=?",
                               (not_, simdi, kimlik))
            self._olay(connection, kimlik, f"moderator:{kullanici['id']}", "removed", not_, simdi=simdi)
            return {"submission": self.gorunum(connection, connection.execute(
                "SELECT * FROM v4_submissions WHERE id=?", (kimlik,)).fetchone())}

    # ------------------------------------------------------------------ kamu okuması
    def kayit_oku(self, kimlik, surum=None):
        """(durum, gövde): 200 kayıt, 410 kaldırma izi, 404 hiç yayımlanmamış ya da yok."""
        if not isinstance(kimlik, str) or not re.fullmatch(r"[0-9a-f]{32}", kimlik):
            return 404, None
        with self.baglanti() as connection:
            if surum is None:
                row = connection.execute("SELECT * FROM v4_records WHERE id=? ORDER BY version DESC LIMIT 1", (kimlik,)).fetchone()
            else:
                row = connection.execute("SELECT * FROM v4_records WHERE id=? AND version=?", (kimlik, surum)).fetchone()
        if row is None:
            return 404, None
        if not row["visible"]:
            return 410, kaldirma_izi(kimlik, row["version"], row["removal"], row["removed_at"])
        return 200, json.loads(row["record"])

    def kayitlari_listele(self, *, offset=0, limit=30):
        with self.baglanti() as connection:
            toplam = connection.execute("SELECT COUNT(*) FROM v4_records WHERE visible=1").fetchone()[0]
            rows = connection.execute("SELECT record FROM v4_records WHERE visible=1 ORDER BY published_at DESC,id "
                                      "LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        return {"items": [json.loads(row["record"]) for row in rows], "total": toplam, "offset": offset, "limit": limit}

    def _gorunur_kayitlar(self, connection, kosul="", degerler=(), sira="r.published_at DESC,r.id"):
        rows = connection.execute("SELECT r.record FROM v4_records r WHERE r.visible=1" + kosul + " ORDER BY " + sira,
                                  degerler).fetchall()
        return [json.loads(row["record"]) for row in rows]

    def _izler(self, connection, kosul="", degerler=()):
        """Önceden yayımlanmış, sonra inen kayıtların adres başına asgari izleri (en yeni önce; her tür)."""
        return [dict(row) for row in connection.execute(
            "SELECT r.id,r.version,a.address_key,r.removed_at FROM v4_records r JOIN v4_record_addresses a ON a.id=r.id "
            "AND a.version=r.version WHERE r.visible=0" + kosul + " ORDER BY r.removed_at DESC,r.id,a.position", degerler).fetchall()]

    def bakis(self, address_key, urun, surum):
        with self.baglanti() as connection:
            connection.execute("BEGIN")
            kayitlar = self._gorunur_kayitlar(
                connection, " AND EXISTS (SELECT 1 FROM v4_record_addresses a WHERE a.id=r.id AND a.version=r.version "
                "AND a.address_key=?)", (address_key,), sira=TUR_SIRASI + ",r.published_at DESC,r.id")
            izler = [dict(iz, removed_at=zaman_metni(iz["removed_at"]))
                     for iz in self._izler(connection, " AND a.address_key=?", (address_key,))]
            return bakis_govdesi(address_key, self._revizyon(connection), kayitlar, izler, urun, surum)

    def kapsam(self):
        with self.baglanti() as connection:
            connection.execute("BEGIN")
            kayitlar = self._gorunur_kayitlar(
                connection, " AND EXISTS (SELECT 1 FROM v4_record_addresses a WHERE a.id=r.id AND a.version=r.version)")
            return kapsam_govdesi(self._revizyon(connection), kayitlar, self._izler(connection), int(self.clock()))

    def konular(self):
        with self.baglanti() as connection:
            connection.execute("BEGIN")
            return konu_govdesi(self._revizyon(connection), self._gorunur_kayitlar(connection), int(self.clock()))


class _AramaKaynagi:
    """v1 listesine katılan ikinci arama kaynağı. Bağlantı `__enter__` ile açılır; adaylar ve kartlar aynı okuma anlık
    görüntüsünden gelir (sayı ile sayfa tutarlıdır)."""

    def __init__(self, depo):
        self.depo = depo
        self.connection = None

    def __enter__(self):
        self.connection = sqlite3.connect(self.depo.path, timeout=10)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("BEGIN")
        return self

    def __exit__(self, *exc):
        self.connection.rollback()
        self.connection.close()
        self.connection = None

    @property
    def ranked_available(self):
        return self.depo.search_index_available

    def adaylar(self, *, plan=None, query="", en_cok, puanlayici=None):
        return self.depo.arama_adaylari(self.connection, plan=plan, query=query, en_cok=en_cok, puanlayici=puanlayici)

    def kart(self, aday, *, plan=None):
        return self.depo.arama_karti(self.connection, aday["id"], aday["version"], plan=plan, fields=aday["fields"])

    def yayinda(self):
        return self.connection.execute("SELECT COUNT(*) FROM v4_records WHERE visible=1").fetchone()[0]
