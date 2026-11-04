"""Kişisel tercihleri, okuma durumunu, koleksiyonları ve kayıtlı aramaları çakışmadan saklar."""

from __future__ import annotations

import json
import math
import re
import unicodedata
import uuid

from radar.calisma_masasi import SurumCatismasi
from radar.depo import simdi
from radar.katalog import arama_metni
from radar.konular import ESKI_KONULAR, KONULAR


AYAR_ALANLARI = frozenset({"topic_weights", "daily_limit", "keywords", "muted_keywords"})
SIRALAMA_ACIKLAMASI = "Kurala dayalı kişisel sıralama; yapay zekâ değerlendirmesi değildir."


def varsayilan_ayarlar():
    """Her çağrıda birbirinden bağımsız, değiştirilebilir varsayılan tercihleri verir."""
    return {"topic_weights": {topic: 3 for topic in KONULAR}, "daily_limit": 8,
            "keywords": [], "muted_keywords": []}


def _metin(value, field, limit, *, required=False, multiline=False):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"{field} en fazla {limit} karakterlik metin olmalı.")
    allowed_controls = {"\n", "\r", "\t"} if multiline else set()
    if any(unicodedata.category(char) == "Cc" and char not in allowed_controls for char in value):
        raise ValueError(f"{field} geçersiz kontrol karakteri içeriyor.")
    value = unicodedata.normalize("NFC", value).strip()
    if len(value) > limit:
        raise ValueError(f"{field} en fazla {limit} karakter olabilir.")
    if required and not value:
        raise ValueError(f"{field} boş bırakılamaz.")
    return value


def _kimlik(value, field="Kayıt kimliği", limit=128):
    result = _metin(value, field, limit, required=True)
    if result != value or any(char.isspace() for char in result):
        raise ValueError(f"{field} boşluk içeremez.")
    return result


def _surum(value):
    if type(value) is not int or value < 0 or value > 2**63 - 2:
        raise ValueError("Beklenen sürüm sıfır veya daha büyük bir tam sayı olmalı.")
    return value


def _mantik(value, field):
    if type(value) is not bool:
        raise ValueError(f"{field} doğru veya yanlış olmalı.")
    return value


def _ad(value):
    name = _metin(value, "Ad", 60, required=True)
    return " ".join(name.split())


def _ad_anahtari(value):
    return arama_metni(unicodedata.normalize("NFKC", value))


def _kelimeler(value, field):
    if not isinstance(value, list) or len(value) > 20:
        raise ValueError(f"{field} en fazla 20 metinden oluşan bir liste olmalı.")
    result, seen = [], set()
    for raw in value:
        word = _metin(raw, field, 100, required=True)
        key = _ad_anahtari(word)
        if key not in seen:
            result.append(word)
            seen.add(key)
    return result


def _ayarlar_dogrula(settings, *, metadata=False):
    if not isinstance(settings, dict):
        raise ValueError("Tercihler bir nesne olmalı.")
    allowed = AYAR_ALANLARI | ({"version", "updated_at"} if metadata else set())
    if not AYAR_ALANLARI <= settings.keys() or settings.keys() - allowed:
        raise ValueError("Tercih alanları topic_weights, daily_limit, keywords ve muted_keywords olmalı.")
    weights = settings["topic_weights"]
    if not isinstance(weights, dict) or set(weights) not in (set(KONULAR), ESKI_KONULAR):
        raise ValueError("Kayıtlı konuların tamamı için ağırlık belirtilmeli; eski beş konu biçimi de desteklenir.")
    if any(type(weight) is not int or not 0 <= weight <= 5 for weight in weights.values()):
        raise ValueError("Konu ağırlıkları 0 ile 5 arasında tam sayı olmalı.")
    limit = settings["daily_limit"]
    if type(limit) is not int or not 4 <= limit <= 20:
        raise ValueError("Günlük seçki sınırı 4 ile 20 arasında tam sayı olmalı.")
    return {"topic_weights": {topic: weights.get(topic, 3) for topic in KONULAR}, "daily_limit": limit,
            "keywords": _kelimeler(settings["keywords"], "İlgi sözcükleri"),
            "muted_keywords": _kelimeler(settings["muted_keywords"], "Sessize alınan sözcükler")}


def _kimlikler(values):
    if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= 100:
        raise ValueError("İşlem için 1 ile 100 arasında kayıt kimliği verilmeli.")
    return list(dict.fromkeys(_kimlik(value) for value in values))


def _kayitlari_dogrula(connection, item_ids):
    placeholders = ",".join("?" for _ in item_ids)
    found = {row["id"] for row in connection.execute(
        "SELECT id FROM items WHERE id IN (" + placeholders + ")", item_ids)}
    for item_id in item_ids:
        if item_id not in found:
            raise KeyError(item_id)


def arama_konu_semasini_guncelle(connection):
    """Eski sabit konu kısıtını kaldırır; satırlar, indeksler ve tetikleyiciler aynı işlemde korunur."""
    schema = connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='saved_searches'").fetchone()[0]
    pattern = r"topic\s+TEXT\s+CHECK\s*\(\s*topic\s+IS\s+NULL\s+OR\s+topic\s+IN\s*\([^)]*\)\s*\)"
    updated, count = re.subn(pattern, "topic TEXT", schema, count=1, flags=re.IGNORECASE)
    if not count:
        return
    updated = re.sub(r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:"saved_searches"|saved_searches)',
                     'CREATE TABLE "saved_searches_topic_upgrade"', updated, count=1, flags=re.IGNORECASE)
    columns = [row[1] for row in connection.execute("PRAGMA table_info(saved_searches)")]
    names = ",".join('"' + name.replace('"', '""') + '"' for name in columns)
    additions = [row[0] for row in connection.execute("SELECT sql FROM sqlite_master WHERE tbl_name='saved_searches' "
                                                     "AND type IN ('index','trigger') AND sql IS NOT NULL")]
    connection.execute("SAVEPOINT saved_search_topic_upgrade")
    try:
        connection.execute(updated)
        connection.execute(f"INSERT INTO saved_searches_topic_upgrade({names}) SELECT {names} FROM saved_searches")
        connection.execute("DROP TABLE saved_searches")
        connection.execute("ALTER TABLE saved_searches_topic_upgrade RENAME TO saved_searches")
        for statement in additions:
            connection.execute(statement)
        if connection.execute("PRAGMA foreign_key_check(saved_searches)").fetchall():
            raise ValueError("Kayıtlı arama geçişinde kaynak veya koleksiyon ilişkisi doğrulanamadı.")
        connection.execute("RELEASE saved_search_topic_upgrade")
    except BaseException:
        connection.execute("ROLLBACK TO saved_search_topic_upgrade")
        connection.execute("RELEASE saved_search_topic_upgrade")
        raise


def tablolari_hazirla(connection):
    """Açık SQLite işlemine katılarak tabloları kurar; çağıranın işlemini tamamlamaz."""
    statements = (
        "CREATE TABLE IF NOT EXISTS user_settings (id INTEGER PRIMARY KEY CHECK(id=1), "
        "settings TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 0 CHECK(version>=0), "
        "updated_at TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS item_read_states (item_id TEXT PRIMARY KEY REFERENCES items(id), "
        "is_read INTEGER NOT NULL CHECK(is_read IN (0,1)), updated_at TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS user_collections (id TEXT PRIMARY KEY, name TEXT NOT NULL, "
        "name_key TEXT NOT NULL UNIQUE, description TEXT NOT NULL DEFAULT '', "
        "archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)), "
        "version INTEGER NOT NULL DEFAULT 1 CHECK(version>=1), created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS collection_items (collection_id TEXT NOT NULL REFERENCES user_collections(id), "
        "item_id TEXT NOT NULL REFERENCES items(id), added_at TEXT NOT NULL, PRIMARY KEY(collection_id,item_id))",
        "CREATE INDEX IF NOT EXISTS collection_items_item ON collection_items(item_id,collection_id)",
        "CREATE TABLE IF NOT EXISTS saved_searches (id TEXT PRIMARY KEY, name TEXT NOT NULL, "
        "query TEXT NOT NULL, topic TEXT, "
        "source_id TEXT REFERENCES sources(id), archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)), "
        "version INTEGER NOT NULL DEFAULT 1 CHECK(version>=1), created_at TEXT NOT NULL, updated_at TEXT NOT NULL, "
        "collection_id TEXT REFERENCES user_collections(id), "
        "read_status TEXT NOT NULL DEFAULT 'all' CHECK(read_status IN ('all','read','unread')), "
        "sort TEXT NOT NULL DEFAULT 'relevance' CHECK(sort IN ('relevance','newest','oldest')))",
    )
    for statement in statements:
        connection.execute(statement)
    search_columns = {row[1] for row in connection.execute("PRAGMA table_info(saved_searches)")}
    additions = {
        "collection_id": "TEXT REFERENCES user_collections(id)",
        "read_status": "TEXT NOT NULL DEFAULT 'all' CHECK(read_status IN ('all','read','unread'))",
        "sort": "TEXT NOT NULL DEFAULT 'relevance' CHECK(sort IN ('relevance','newest','oldest'))",
    }
    for name, definition in additions.items():
        if name not in search_columns:
            connection.execute("ALTER TABLE saved_searches ADD COLUMN " + name + " " + definition)
    arama_konu_semasini_guncelle(connection)
    connection.execute("INSERT OR IGNORE INTO user_settings(id,settings,version,updated_at) VALUES(1,?,0,?)",
                       (json.dumps(varsayilan_ayarlar(), ensure_ascii=False), simdi()))
    saved = connection.execute("SELECT settings FROM user_settings WHERE id=1").fetchone()
    settings = json.loads(saved[0])
    weights = settings.get("topic_weights")
    if isinstance(weights, dict) and set(weights) == ESKI_KONULAR:
        settings["topic_weights"] = {topic: weights.get(topic, 3) for topic in KONULAR}
        connection.execute("UPDATE user_settings SET settings=? WHERE id=1", (json.dumps(settings, ensure_ascii=False),))


def _ayarlar_oku(connection):
    row = connection.execute("SELECT settings,version,updated_at FROM user_settings WHERE id=1").fetchone()
    return {**json.loads(row["settings"]), "version": row["version"], "updated_at": row["updated_at"]}


def _koleksiyonlar(connection, include_archived):
    condition = "" if include_archived else " WHERE archived=0"
    rows = connection.execute("SELECT * FROM user_collections" + condition + " ORDER BY name_key,id").fetchall()
    result = {row["id"]: {**dict(row), "archived": bool(row["archived"]), "item_ids": []} for row in rows}
    for entry in connection.execute("SELECT collection_id,item_id FROM collection_items ORDER BY added_at,item_id"):
        if entry["collection_id"] in result:
            result[entry["collection_id"]]["item_ids"].append(entry["item_id"])
    for collection in result.values():
        collection.pop("name_key", None)
        collection["item_count"] = len(collection["item_ids"])
    return list(result.values())


def _koleksiyon_oku(connection, collection_id):
    row = connection.execute("SELECT * FROM user_collections WHERE id=?", (collection_id,)).fetchone()
    if row is None:
        raise KeyError(collection_id)
    result = dict(row)
    result.pop("name_key")
    result["archived"] = bool(result["archived"])
    result["item_ids"] = [entry["item_id"] for entry in connection.execute(
        "SELECT item_id FROM collection_items WHERE collection_id=? ORDER BY added_at,item_id", (collection_id,))]
    result["item_count"] = len(result["item_ids"])
    return result


def _aramalar(connection, include_archived):
    condition = "" if include_archived else " WHERE archived=0"
    return [{**dict(row), "archived": bool(row["archived"])} for row in connection.execute(
        "SELECT * FROM saved_searches" + condition + " ORDER BY created_at DESC,id")]


def _surum_dogrula(current, expected):
    if current["version"] != expected:
        raise SurumCatismasi("Bu kayıt başka bir pencerede değişti. Güncel kaydı açıp değişikliğini yeniden değerlendir.")


class KisiselAlan:
    """Tek yerel kullanıcının çalışma alanıdır; bütün çoklu yazmalar tek işlemde tamamlanır.

    Ayarlar tam nesne olarak kaydedilir; ilk sürüm 0'dır. Koleksiyon ve aramalar 1'den
    başlar. Düzenleme, arşivleme ve koleksiyon üyeliği güncel expected_version ister.
    Aynı işlem güncel sürümle tekrarlanırsa sürüm artmaz. Eksik kimlik KeyError,
    geçersiz girdi ValueError, eski sürüm SurumCatismasi üretir. Silme yalnız arşivler.
    """

    def __init__(self, depo):
        self.depo = depo
        with depo.baglanti(write=True) as connection:
            tablolari_hazirla(connection)

    def ayarlar_oku(self):
        """Tercihlerin bir kopyasını version ve updated_at ile verir."""
        with self.depo.baglanti() as connection:
            return _ayarlar_oku(connection)

    def ayarlar_kaydet(self, settings, *, expected_version):
        """Konu ağırlıklarını, günlük sınırı ve sözcük listelerini eski sürümü ezmeden kaydeder."""
        settings = _ayarlar_dogrula(settings)
        expected_version = _surum(expected_version)
        with self.depo.baglanti(write=True) as connection:
            current = _ayarlar_oku(connection)
            _surum_dogrula(current, expected_version)
            if all(current[key] == value for key, value in settings.items()):
                return current
            connection.execute("UPDATE user_settings SET settings=?,version=?,updated_at=? WHERE id=1",
                               (json.dumps(settings, ensure_ascii=False), expected_version + 1, simdi()))
            self.depo.olay_yaz(connection, "kisisel_ayarlar", "1", {"version": expected_version + 1})
            return _ayarlar_oku(connection)

    def okundu_yaz(self, item_id, *, read):
        """Tek kaydı okundu veya okunmadı yapar; haber metnini ve yararlı seçimini değiştirmez."""
        return self.toplu_okundu_yaz([item_id], read=read)[0]

    def toplu_okundu_yaz(self, item_ids, *, read):
        """En fazla 100 açık kimliği birlikte işaretler; tek bir eksik kayıt varsa hiçbirini yazmaz."""
        item_ids, read = _kimlikler(item_ids), _mantik(read, "Okundu durumu")
        with self.depo.baglanti(write=True) as connection:
            _kayitlari_dogrula(connection, item_ids)
            now = simdi()
            connection.executemany(
                "INSERT INTO item_read_states(item_id,is_read,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(item_id) DO UPDATE SET is_read=excluded.is_read,updated_at=excluded.updated_at "
                "WHERE item_read_states.is_read<>excluded.is_read",
                [(item_id, int(read), now) for item_id in item_ids])
            placeholders = ",".join("?" for _ in item_ids)
            rows = {row["item_id"]: {"item_id": row["item_id"], "read": bool(row["is_read"]),
                                     "updated_at": row["updated_at"]} for row in connection.execute(
                "SELECT * FROM item_read_states WHERE item_id IN (" + placeholders + ")", item_ids)}
            self.depo.olay_yaz(connection, "okuma_durumu", "toplu", {"item_ids": item_ids, "read": read})
            return [rows[item_id] for item_id in item_ids]

    def koleksiyonlar(self, *, include_archived=False):
        """Koleksiyonları item_ids ve item_count ile verir; arşivlenenler açıkça istenebilir."""
        _mantik(include_archived, "Arşivi dahil et")
        with self.depo.baglanti() as connection:
            connection.execute("BEGIN")
            return _koleksiyonlar(connection, include_archived)

    def koleksiyon_olustur(self, *, name, description=""):
        """En fazla 60 karakterlik adı ve 300 karakterlik açıklaması olan koleksiyon açar."""
        name = _ad(name)
        description = _metin(description, "Açıklama", 300, multiline=True)
        with self.depo.baglanti(write=True) as connection:
            self._ad_musait(connection, name)
            collection_id, now = uuid.uuid4().hex, simdi()
            connection.execute("INSERT INTO user_collections(id,name,name_key,description,created_at,updated_at) "
                               "VALUES(?,?,?,?,?,?)", (collection_id, name, _ad_anahtari(name), description, now, now))
            self.depo.olay_yaz(connection, "koleksiyon_olustu", collection_id, {"version": 1})
            return _koleksiyon_oku(connection, collection_id)

    @staticmethod
    def _ad_musait(connection, name, collection_id=""):
        if connection.execute("SELECT id FROM user_collections WHERE name_key=? AND id<>?",
                              (_ad_anahtari(name), collection_id)).fetchone():
            raise ValueError("Bu adla bir koleksiyon zaten var; arşivlenmiş koleksiyonları da kontrol et.")

    def koleksiyon_duzenle(self, collection_id, *, name, description, expected_version):
        """Koleksiyon adını ve açıklamasını güncel sürüm üzerinden birlikte değiştirir."""
        collection_id, expected_version = _kimlik(collection_id), _surum(expected_version)
        name, description = _ad(name), _metin(description, "Açıklama", 300, multiline=True)
        with self.depo.baglanti(write=True) as connection:
            current = _koleksiyon_oku(connection, collection_id)
            _surum_dogrula(current, expected_version)
            self._ad_musait(connection, name, collection_id)
            if current["name"] == name and current["description"] == description:
                return current
            connection.execute("UPDATE user_collections SET name=?,name_key=?,description=?,version=?,updated_at=? "
                               "WHERE id=?", (name, _ad_anahtari(name), description, expected_version + 1, simdi(), collection_id))
            self.depo.olay_yaz(connection, "koleksiyon_duzenlendi", collection_id, {"version": expected_version + 1})
            return _koleksiyon_oku(connection, collection_id)

    def koleksiyon_arsivle(self, collection_id, *, archived, expected_version):
        """Koleksiyonu üyeleriyle birlikte arşivler veya geri açar; kalıcı silme yapmaz."""
        collection_id, expected_version = _kimlik(collection_id), _surum(expected_version)
        archived = _mantik(archived, "Arşiv durumu")
        with self.depo.baglanti(write=True) as connection:
            current = _koleksiyon_oku(connection, collection_id)
            _surum_dogrula(current, expected_version)
            if current["archived"] == archived:
                return current
            connection.execute("UPDATE user_collections SET archived=?,version=?,updated_at=? WHERE id=?",
                               (int(archived), expected_version + 1, simdi(), collection_id))
            self.depo.olay_yaz(connection, "koleksiyon_arsiv", collection_id,
                              {"archived": archived, "version": expected_version + 1})
            return _koleksiyon_oku(connection, collection_id)

    def koleksiyona_ekle(self, collection_id, item_ids, *, expected_version):
        """En fazla 100 kaydı koleksiyona atomik olarak ekler; aynı üyelik tekrarlanmaz."""
        return self._uyelik_degistir(collection_id, item_ids, expected_version, adding=True)

    def koleksiyondan_cikar(self, collection_id, item_ids, *, expected_version):
        """Üyelikleri kaldırır; haberlerin kendisini ve diğer koleksiyonları korur."""
        return self._uyelik_degistir(collection_id, item_ids, expected_version, adding=False)

    def _uyelik_degistir(self, collection_id, item_ids, expected_version, *, adding):
        collection_id, expected_version = _kimlik(collection_id), _surum(expected_version)
        item_ids = _kimlikler(item_ids)
        with self.depo.baglanti(write=True) as connection:
            current = _koleksiyon_oku(connection, collection_id)
            _surum_dogrula(current, expected_version)
            if current["archived"]:
                raise ValueError("Kayıtlarını değiştirmek için koleksiyonu önce arşivden çıkar.")
            _kayitlari_dogrula(connection, item_ids)
            members = set(current["item_ids"])
            changed = [item_id for item_id in item_ids if (item_id not in members) == adding]
            if not changed:
                return current
            now = simdi()
            if adding:
                connection.executemany("INSERT INTO collection_items(collection_id,item_id,added_at) VALUES(?,?,?)",
                                       [(collection_id, item_id, now) for item_id in changed])
            else:
                connection.executemany("DELETE FROM collection_items WHERE collection_id=? AND item_id=?",
                                       [(collection_id, item_id) for item_id in changed])
            connection.execute("UPDATE user_collections SET version=?,updated_at=? WHERE id=?",
                               (expected_version + 1, now, collection_id))
            self.depo.olay_yaz(connection, "koleksiyon_uyeleri", collection_id,
                              {"added": adding, "item_ids": changed, "version": expected_version + 1})
            return _koleksiyon_oku(connection, collection_id)

    def arama_kaydet(self, *, name, query="", topic=None, source_id=None, collection_id=None,
                    read_status="all", sort="relevance"):
        """Aramanın metnini, konu/kaynak/koleksiyonunu, okuma durumunu ve sırasını saklar."""
        name, query = _ad(name), _metin(query, "Arama", 200)
        if topic in (None, "", "all"):
            topic = None
        elif not isinstance(topic, str) or topic not in KONULAR:
            raise ValueError("Arama konusu geçersiz.")
        if source_id in (None, ""):
            source_id = None
        else:
            source_id = _kimlik(source_id, "Kaynak kimliği", 64)
        if collection_id in (None, ""):
            collection_id = None
        else:
            collection_id = _kimlik(collection_id, "Koleksiyon kimliği")
        if read_status not in ("all", "read", "unread") or sort not in ("relevance", "newest", "oldest"):
            raise ValueError("Aramanın okuma durumu veya sıralaması geçersiz.")
        with self.depo.baglanti(write=True) as connection:
            if source_id is not None and not connection.execute("SELECT 1 FROM sources WHERE id=?", (source_id,)).fetchone():
                raise KeyError(source_id)
            if collection_id is not None and not connection.execute(
                    "SELECT 1 FROM user_collections WHERE id=?", (collection_id,)).fetchone():
                raise KeyError(collection_id)
            search_id, now = uuid.uuid4().hex, simdi()
            connection.execute("INSERT INTO saved_searches(id,name,query,topic,source_id,created_at,updated_at,"
                               "collection_id,read_status,sort) VALUES(?,?,?,?,?,?,?,?,?,?)",
                               (search_id, name, query, topic, source_id, now, now, collection_id, read_status, sort))
            self.depo.olay_yaz(connection, "arama_kaydedildi", search_id, {"version": 1})
            return self._arama_oku(connection, search_id)

    @staticmethod
    def _arama_oku(connection, search_id):
        row = connection.execute("SELECT * FROM saved_searches WHERE id=?", (search_id,)).fetchone()
        if row is None:
            raise KeyError(search_id)
        return {**dict(row), "archived": bool(row["archived"])}

    def aramalar(self, *, include_archived=False):
        """Kaydedilmiş aramaları verir; arşivlenenler açıkça istenebilir."""
        _mantik(include_archived, "Arşivi dahil et")
        with self.depo.baglanti() as connection:
            return _aramalar(connection, include_archived)

    def arama_arsivle(self, search_id, *, archived, expected_version):
        """Kayıtlı aramayı kalıcı silmeden arşivler veya yeniden açar."""
        search_id, expected_version = _kimlik(search_id), _surum(expected_version)
        archived = _mantik(archived, "Arşiv durumu")
        with self.depo.baglanti(write=True) as connection:
            current = self._arama_oku(connection, search_id)
            _surum_dogrula(current, expected_version)
            if current["archived"] == archived:
                return current
            connection.execute("UPDATE saved_searches SET archived=?,version=?,updated_at=? WHERE id=?",
                               (int(archived), expected_version + 1, simdi(), search_id))
            self.depo.olay_yaz(connection, "arama_arsiv", search_id,
                              {"archived": archived, "version": expected_version + 1})
            return self._arama_oku(connection, search_id)

    def arama_sil(self, search_id, *, expected_version):
        """Silme isteğini geri alınabilir arşivleme olarak uygular."""
        return self.arama_arsivle(search_id, archived=True, expected_version=expected_version)

    def snapshot(self):
        """API ve dışa aktarım için tüm kişisel durumu aynı veritabanı anından verir."""
        with self.depo.baglanti() as connection:
            connection.execute("BEGIN")
            settings = _ayarlar_oku(connection)
            reads = [{"item_id": row["item_id"], "read": bool(row["is_read"]), "updated_at": row["updated_at"]}
                     for row in connection.execute("SELECT * FROM item_read_states ORDER BY item_id")]
            collections = _koleksiyonlar(connection, True)
            searches = _aramalar(connection, True)
            total = connection.execute("SELECT COUNT(*) FROM items").fetchone()[0]
            read_count = sum(row["read"] for row in reads)
            return {"settings": settings, "read_states": reads, "collections": collections,
                    "saved_searches": searches,
                    "stats": {"read_count": read_count, "unread_count": total - read_count,
                              "collection_count": sum(not row["archived"] for row in collections),
                              "saved_search_count": sum(not row["archived"] for row in searches)},
                    "ranking": {"method": "rules", "label": SIRALAMA_ACIKLAMASI}}


def _puan(item, settings):
    fields = ("title", "source_title", "excerpt", "summary", "benefit")
    content = " ".join(str(item.get(field) or "") for field in fields)
    turkish, common = arama_metni(unicodedata.normalize("NFC", content)), content.casefold()

    def matches(word):
        return arama_metni(word) in turkish or word.casefold() in common

    positive = [word for word in settings["keywords"] if matches(word)]
    muted = [word for word in settings["muted_keywords"] if matches(word)]
    raw_score = item.get("score", 0)
    if type(raw_score) is int or (type(raw_score) is float and math.isfinite(raw_score)):
        base = max(0, min(100, raw_score))
    else:
        base = 0
    topic = item.get("topic")
    weight = settings["topic_weights"].get(topic, 3) if isinstance(topic, str) else 3
    score = base + 15 * (weight - 3) + 8 * min(5, len(positive)) - (1000 if muted else 0)
    return {"score": score, "base_score": base, "topic_weight": weight,
            "keyword_matches": positive, "muted_keyword_matches": muted, "muted": bool(muted),
            "method": "rules", "label": SIRALAMA_ACIKLAMASI}


def kisisel_puan(item, settings=None):
    """Temel puana konu ağırlığı ve sözcük eşleşmesi ekler; puan bir olasılık yüzdesi değildir.

    3 nötr konu ağırlığıdır; her basamak 15 puan, ilk beş farklı ilgi sözcüğü
    sekizer puan getirir. Sessize alınan sözcük eşleşmesi muted işaretini ve
    1000 puanlık indirimi üretir. Metin eşleşmeleri kelime veya ifade alt dizisidir.
    """
    if not isinstance(item, dict):
        raise ValueError("Sıralanacak kayıt bir nesne olmalı.")
    settings = _ayarlar_dogrula(varsayilan_ayarlar() if settings is None else settings, metadata=True)
    return _puan(item, settings)


def kisisel_sirala(items, settings=None, *, include_muted=False):
    """Kayıt kopyalarını personal_score ve personal_rank ile sıralar; günlük sınırı uygulamaz.

    Sessize alınanlar varsayılan olarak dışarıda kalır. Eşit puanda giriş sırası
    korunur. Okuma ve arşiv durumunu değiştirmez; seçkinin kapsamını çağıran belirler.
    """
    if not isinstance(items, (list, tuple)) or any(not isinstance(item, dict) for item in items):
        raise ValueError("Sıralanacak kayıtlar bir nesne listesi olmalı.")
    _mantik(include_muted, "Sessize alınanları dahil et")
    settings = _ayarlar_dogrula(varsayilan_ayarlar() if settings is None else settings, metadata=True)
    result = []
    for item in items:
        rank = _puan(item, settings)
        if include_muted or not rank["muted"]:
            result.append({**item, "personal_score": rank["score"], "personal_rank": rank})
    return sorted(result, key=lambda item: item["personal_score"], reverse=True)
