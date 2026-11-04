"""Kaynak tercihlerini ve özel beslemeleri saklar; başarısız okumalar için tek kullanımlık yeniden deneme hakkı verir."""

import json
import re
import uuid
from datetime import timedelta
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from radar.depo import simdi, tarih
from radar.kaynak_havuzu import KONULAR, alan_adi_gecerli, katalogu_dogrula


YENIDEN_DENEME_BEKLEME = timedelta(minutes=5)


class KaynakSurumCatismasi(ValueError):
    """Başka pencerede değişen kaynak tercihinin üzerine yazılmasını önler."""


class YenidenDenemeBekliyor(ValueError):
    """Bekleyen bir deneme veya asgari bekleme süresi varken yeni denemeyi engeller."""

    def __init__(self, message, available_at=None):
        super().__init__(message)
        self.available_at = available_at


def tablolari_hazirla(connection):
    """Depo açılırken kullanıcı ayarları için gerekli ek tabloları hazırlar."""
    connection.execute("""CREATE TABLE IF NOT EXISTS source_preferences (
        source_id TEXT PRIMARY KEY REFERENCES sources(id),
        enabled INTEGER CHECK(enabled IN (0,1)),
        interval_hours INTEGER CHECK(interval_hours BETWEEN 24 AND 720),
        version INTEGER NOT NULL DEFAULT 0 CHECK(version >= 0),
        retry_pending INTEGER NOT NULL DEFAULT 0 CHECK(retry_pending IN (0,1)),
        retry_requested_at TEXT, last_retry_at TEXT, updated_at TEXT NOT NULL
    )""")
    connection.execute("""CREATE TABLE IF NOT EXISTS custom_sources (
        source_id TEXT PRIMARY KEY REFERENCES sources(id),
        config TEXT NOT NULL, created_at TEXT NOT NULL
    )""")


def tercihleri_uygula(sources, connection):
    """Ham katalog ayarlarını değiştirmeden kalıcı kullanıcı tercihlerini görünüşe ekler."""
    preferences = {row["source_id"]: dict(row) for row in connection.execute("SELECT * FROM source_preferences")}
    custom_ids = {row[0] for row in connection.execute("SELECT source_id FROM custom_sources")}
    from radar.kaynak_ogrenme import araligi_uygula, etkin_araliklar
    learning = etkin_araliklar(connection)
    now = tarih(simdi())
    result = []
    for source in sources:
        current = dict(source)
        preference = preferences.get(source["id"], {})
        for field in ("enabled", "interval_hours"):
            if preference.get(field) is not None:
                current[field] = bool(preference[field]) if field == "enabled" else preference[field]
        # Önceden süzülmüş kaynak tekrar işlendiğinde otomatik aralık, yapılandırma
        # sanılmamalı; açık kullanıcı tercihi her zaman önceliklidir.
        if preference.get('interval_hours') is not None:
            current['configured_interval_hours'] = preference['interval_hours']
        araligi_uygula(current, preference, learning.get(source['id'], {}))
        current.update(preference_version=preference.get("version", 0),
                       retry_pending=bool(preference.get("retry_pending", 0)),
                       retry_requested_at=preference.get("retry_requested_at"),
                       last_retry_at=preference.get("last_retry_at"),
                       custom_source=source["id"] in custom_ids)
        times = [value for key in ("last_checked_at", "retry_requested_at", "last_retry_at")
                 if (value := tarih(current.get(key))) is not None]
        available = max(times) + YENIDEN_DENEME_BEKLEME if times else None
        current["retry_available_at"] = available.isoformat() if available else None
        current["can_retry"] = bool(current.get("kind") != "discovery" and current.get("enabled", True) and not current["retry_pending"]
                                    and (current.get("last_error") or current.get("failures", 0))
                                    and (available is None or now >= available))
        if current["retry_pending"] and current.get("enabled", True):
            requested = tarih(current["retry_requested_at"])
            attempts = [value for key in ("last_checked_at", "last_retry_at")
                        if (value := tarih(current.get(key))) is not None]
            earliest = max(attempts) + YENIDEN_DENEME_BEKLEME if attempts else requested
            current["next_due_at"] = max(requested, earliest).isoformat()
        result.append(current)
    return result


def _surumu_dogrula(value):
    if type(value) is not int or value < 0:
        raise ValueError("Kaynak tercih sürümü geçersiz.")


def _araligi_dogrula(value):
    if type(value) is not int or not 24 <= value <= 720:
        raise ValueError("Kontrol aralığı 24–720 arasında tam saat olmalı.")


def _metni_dogrula(value, field, limit=200):
    if not isinstance(value, str) or not 2 <= len(value.strip()) <= limit or any(
            ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{field} 2–{limit} karakterlik düz metin olmalı.")
    return value.strip()


def _besleme_adresi(value):
    if not isinstance(value, str) or len(value) > 4096 or "\\" in value or any(
            ord(char) <= 32 or ord(char) == 127 for char in value):
        raise ValueError("Besleme adresi geçersiz.")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        valid = (parsed.scheme == "https" and alan_adi_gecerli(host)
                 and parsed.port in (None, 443) and parsed.username is None
                 and parsed.password is None and not parsed.fragment)
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError("Besleme açık bir alan adında, standart HTTPS adresi olmalı.")
    secret_fields = {"token", "access_token", "api_key", "apikey", "password", "secret", "authorization", "signature"}
    if any(key.lower() in secret_fields for key, _ in parse_qsl(parsed.query, keep_blank_values=True)):
        raise ValueError("Kimlik bilgisi içeren beslemeler kaynak kütüphanesine eklenemez.")
    return urlunsplit(("https", host, parsed.path or "/", parsed.query, "")), host


class KaynakYonetimi:
    def __init__(self, depo):
        self.depo = depo
        with depo.baglanti(write=True) as connection:
            tablolari_hazirla(connection)

    def oku(self, source_id, connection=None):
        if connection is None:
            with self.depo.baglanti() as current:
                current.execute("BEGIN")
                return self.oku(source_id, current)
        row = connection.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        if row is None:
            raise KeyError(source_id)
        source = {**json.loads(row["config"]), **{key: row[key] for key in row.keys() if key != "config"}}
        return tercihleri_uygula([source], connection)[0]

    def kaydet(self, source_id, *, enabled, interval_hours, expected_version):
        _surumu_dogrula(expected_version)
        _araligi_dogrula(interval_hours)
        if type(enabled) is not bool:
            raise ValueError("Kaynak etkinliği doğru/yanlış olmalı.")
        with self.depo.baglanti(write=True) as connection:
            current = self.oku(source_id, connection)
            if current["preference_version"] != expected_version:
                raise KaynakSurumCatismasi("Kaynak tercihleri başka bir pencerede değişti. Güncel kaydı açın.")
            preference = connection.execute("SELECT * FROM source_preferences WHERE source_id=?", (source_id,)).fetchone()
            if (preference is not None and preference["enabled"] == int(enabled)
                    and preference["interval_hours"] == interval_hours):
                return current
            now = simdi()
            connection.execute(
                "INSERT INTO source_preferences(source_id,enabled,interval_hours,version,updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(source_id) DO UPDATE SET enabled=excluded.enabled,interval_hours=excluded.interval_hours,"
                "version=excluded.version,updated_at=excluded.updated_at,"
                "retry_pending=CASE WHEN excluded.enabled=0 THEN 0 ELSE source_preferences.retry_pending END",
                (source_id, int(enabled), interval_hours, expected_version + 1, now))
            # Aralık değişince son kontrol korunur; yeni vade bu kontrolden hesaplanır.
            if current.get("interval_hours") != interval_hours and (checked := tarih(current.get("last_checked_at"))):
                backoff = min(168, 2 ** min(int(current.get("failures", 0)), 7)) if current.get("last_error") else 0
                next_due = (checked + timedelta(hours=max(interval_hours, backoff))).isoformat()
                connection.execute("UPDATE sources SET next_due_at=? WHERE id=?", (next_due, source_id))
            self.depo.olay_yaz(connection, "kaynak_tercihi", source_id,
                              {"enabled": enabled, "interval_hours": interval_hours, "version": expected_version + 1})
            return self.oku(source_id, connection)

    def yeniden_dene(self, source_id, *, expected_version):
        _surumu_dogrula(expected_version)
        with self.depo.baglanti(write=True) as connection:
            current = self.oku(source_id, connection)
            if current["preference_version"] != expected_version:
                raise KaynakSurumCatismasi("Kaynak tercihleri başka bir pencerede değişti. Güncel kaydı açın.")
            if not current.get("enabled", True):
                raise ValueError("Kapalı kaynak için yeniden deneme başlatılamaz.")
            if not current.get("last_error") and not current.get("failures", 0):
                raise ValueError("Yalnız son kontrolü başarısız kaynak yeniden denenebilir.")
            if current["retry_pending"]:
                raise YenidenDenemeBekliyor("Bu kaynak için bir yeniden deneme zaten bekliyor.", current["retry_available_at"])
            if not current["can_retry"]:
                raise YenidenDenemeBekliyor("Yeni denemeden önce son kontrolden en az 5 dakika geçmeli.", current["retry_available_at"])
            now = simdi()
            connection.execute(
                "INSERT INTO source_preferences(source_id,version,retry_pending,retry_requested_at,updated_at) VALUES(?,?,1,?,?) "
                "ON CONFLICT(source_id) DO UPDATE SET version=excluded.version,retry_pending=1,"
                "retry_requested_at=excluded.retry_requested_at,updated_at=excluded.updated_at",
                (source_id, expected_version + 1, now, now))
            self.depo.olay_yaz(connection, "kaynak_yeniden_deneme_istendi", source_id,
                              {"version": expected_version + 1})
            return self.oku(source_id, connection)

    def yeniden_deneme_al(self, source_id):
        """Tarama işçisi ağı okumadan hemen önce bekleyen hakkı yalnız bir kez tüketir."""
        with self.depo.baglanti(write=True) as connection:
            current = self.oku(source_id, connection)
            if not current["retry_pending"]:
                return False
            now = simdi()
            allowed = bool(current.get("enabled", True) and (current.get("last_error") or current.get("failures", 0)))
            # İstekten sonra süren başka bir kontrol tamamlanmış olabilir.
            # Bilet alınırken de son gerçek denemeden itibaren bekleme korunur.
            if allowed and (due := tarih(current.get("next_due_at"))) and tarih(now) < due:
                return False
            connection.execute("UPDATE source_preferences SET retry_pending=0,last_retry_at=?,"
                               "version=version+1,updated_at=? WHERE source_id=?", (now, now, source_id))
            self.depo.olay_yaz(connection, "kaynak_yeniden_deneme_alindi" if allowed else "kaynak_yeniden_deneme_iptal",
                              source_id, {"allowed": allowed})
            return allowed

    def ekle(self, *, name, fetch_url, kind, topic, publisher, branches, interval_hours=48,
             language="bilinmiyor", source_type="Teknik yayın"):
        name = _metni_dogrula(name, "Kaynak başlığı")
        publisher = _metni_dogrula(publisher, "Yayıncı")
        if not isinstance(kind, str) or kind not in ("rss", "atom") or not isinstance(topic, str) or topic not in KONULAR:
            raise ValueError("Özel kaynak RSS/Atom olmalı ve geçerli bir konu seçilmeli.")
        _araligi_dogrula(interval_hours)
        if not isinstance(branches, list) or not 1 <= len(branches) <= 10:
            raise ValueError("Kaynak için 1–10 alt dal seçilmeli.")
        branches = list(dict.fromkeys(_metni_dogrula(branch, "Alt dal", 100) for branch in branches))
        if not isinstance(language, str) or (language != "bilinmiyor" and not re.fullmatch(r"[a-z]{2,3}(?:-[a-z0-9]{2,8})*", language)):
            raise ValueError("Kaynak dili geçersiz.")
        fetch_url, host = _besleme_adresi(fetch_url)
        identifier = "custom_" + uuid.uuid4().hex
        config = {"id": identifier, "name": name, "topic": topic, "url": fetch_url, "fetch_url": fetch_url,
                  "kind": kind, "interval_hours": interval_hours, "enabled": True,
                  "language": language, "fetch_hosts": [host], "item_hosts": [host],
                  "publisher": publisher, "branches": branches, "source_type": source_type,
                  "discovered_from": fetch_url, "evidence_type": "kaynak_akisi",
                  "benefit": "Kullanıcının seçtiği kaynak; açıklamalar yayıncının beyanıdır."}
        katalogu_dogrula([config])
        with self.depo.baglanti(write=True) as connection:
            rows = connection.execute("SELECT config FROM sources").fetchall()
            if len(rows) >= 200:
                raise ValueError("Kaynak kütüphanesi en fazla 200 kayıt içerebilir.")
            for row in rows:
                previous_url = json.loads(row[0]).get("fetch_url")
                if previous_url:
                    try:
                        previous_url, _ = _besleme_adresi(previous_url)
                    except ValueError:
                        pass
                    if previous_url == fetch_url:
                        raise ValueError("Bu besleme zaten kaynak kütüphanesinde var.")
            encoded = json.dumps(config, ensure_ascii=False)
            connection.execute("INSERT INTO sources(id,config) VALUES(?,?)", (identifier, encoded))
            connection.execute("INSERT INTO custom_sources(source_id,config,created_at) VALUES(?,?,?)",
                               (identifier, encoded, simdi()))
            self.depo.olay_yaz(connection, "ozel_kaynak_eklendi", identifier, {"kind": kind, "topic": topic})
            return self.oku(identifier, connection)

    def ozel_kaynaklar(self):
        """Host yetkilendirmesi için kalıcı özel kayıtları yeniden doğrulayarak verir."""
        with self.depo.baglanti() as connection:
            rows = connection.execute("SELECT source_id,config FROM custom_sources ORDER BY source_id").fetchall()
        sources = []
        for row in rows:
            source = json.loads(row["config"])
            fetch_url, host = _besleme_adresi(source.get("fetch_url"))
            if (source.get("id") != row["source_id"] or source.get("kind") not in ("rss", "atom")
                    or source.get("fetch_url") != fetch_url or source.get("url") != fetch_url
                    or source.get("fetch_hosts") != [host] or source.get("item_hosts") != [host]):
                raise ValueError("Özel kaynak kaydı beklenen alan adı sınırlarıyla eşleşmiyor.")
            katalogu_dogrula([source])
            sources.append(source)
        return sources
