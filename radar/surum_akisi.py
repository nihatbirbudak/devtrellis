"""Yayındaki kayıtların ürün/sürüm kapsamını kamu sürüm akışlarıyla karşılaştırır; model çağrısı ve yazma yok.

Kaynaklar: endoflife.date (v1 API), deps.dev (v3), PyPI JSON ve yalnız açıkça eşlenen ürünler için GitHub
(jetonsuz, salt okunur). Ürün kimliği purl ya da endoflife.date kimliğiyle eşlenir; eşlenemeyen ürün
"bilinmiyor" kalır. Kaynak başına günde en çok bir istek yapılır; sonuç ayrı SQLite önbelleğinde tutulur.
Etiket raporu gizlemez: tetikleyen sürüm ve tarihi gösterir.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path

SEMA = "devtrellis-version-feed-v1"
USER_AGENT = "DevTrellis-VersionFeed/0.1 (+https://devtrellis.org)"
MAX_BODY_BYTES = 2_000_000
TIMEOUT_SECONDS = 20
CONFIG_LIMIT = 256 * 1024
ETIKETLER = {"current": "güncel", "newer_version": "yeni sürüm var", "maybe_outdated": "eskimiş olabilir",
             "unknown": "bilinmiyor"}
ETIKET_SIRASI = {"unknown": 0, "current": 1, "newer_version": 2, "maybe_outdated": 3}
NEDENLER = {"no_applies_to": "kayıtta ürün/sürüm kapsamı yok", "product_not_mapped": "ürün bir kaynağa eşlenmedi",
            "source_unavailable": "kaynak bugün yanıt vermedi", "source_payload_unparsed": "kaynak yanıtı beklenen biçimde değil",
            "version_names_not_numeric": "kaynaktaki sürüm adı sayısal değil (kod adı; ayarda cycle_names ile eşlenebilir)",
            "scope_version_unparsed": "kapsam sürümü ayrıştırılamadı", "adapter_error": "kaynak işlenirken hata; öteki kayıtlar sürdü"}
KOD_ADI = re.compile(r"^[a-z][a-z0-9.-]{0,40}$")
KAYNAKLAR = ("endoflife", "depsdev", "pypi", "github")
KAYNAK_ALANLARI = {"endoflife": "https://endoflife.date", "depsdev": "https://api.deps.dev",
                   "pypi": "https://pypi.org", "github": "https://api.github.com"}
DEPSDEV_SISTEMLERI = {"npm": "npm", "pypi": "pypi", "cargo": "cargo", "golang": "go", "maven": "maven", "nuget": "nuget"}
URUN_ADI = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
SURUM_PARCASI = re.compile(r"(?<![\w.])v?(\d+(?:\.\d+){0,3})(?:\.[x*])?(?![\w.])")


class SurumAkisiHatasi(ValueError):
    """Ayar ya da depo hatası; ağ yanıtı içeriği taşımaz."""


def simdi_metni(saniye):
    return datetime.fromtimestamp(saniye, timezone.utc).isoformat(timespec="seconds")


def gun_metni(saniye):
    return datetime.fromtimestamp(saniye, timezone.utc).strftime("%Y-%m-%d")


# --- ayar ---------------------------------------------------------------------------------------------

def _tekrarsiz(pairs):
    sonuc = {}
    for anahtar, deger in pairs:
        if anahtar in sonuc:
            raise SurumAkisiHatasi("Ayar dosyasında tekrarlanan alan var.")
        sonuc[anahtar] = deger
    return sonuc


def ayarlari_oku(path):
    path = Path(path)
    with path.open("rb") as stream:
        ham = stream.read(CONFIG_LIMIT + 1)
    if len(ham) > CONFIG_LIMIT:
        raise SurumAkisiHatasi("Ayar dosyası boyut sınırını aşıyor.")
    try:
        deger = json.loads(ham.decode("utf-8-sig"), object_pairs_hook=_tekrarsiz)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SurumAkisiHatasi("Ayar dosyası geçerli UTF-8 JSON olmalı.") from exc
    return ayarlari_dogrula(deger)


def ayarlari_dogrula(deger):
    izinli = {"schema", "enabled", "sources", "request_interval_seconds", "products", "v1_reports"}
    if not isinstance(deger, dict) or set(deger) - izinli or deger.get("schema") != SEMA:
        raise SurumAkisiHatasi("Sürüm akışı ayarı tanınmıyor; schema ve izinli alanlar gerekli.")
    ayar = {"schema": SEMA, "enabled": deger.get("enabled", False), "sources": {}, "products": {}, "v1_reports": {},
            "request_interval_seconds": deger.get("request_interval_seconds", 1)}
    if type(ayar["enabled"]) is not bool:
        raise SurumAkisiHatasi("enabled true ya da false olmalı.")
    aralik = ayar["request_interval_seconds"]
    if type(aralik) not in (int, float) or not 0 <= aralik <= 60:
        raise SurumAkisiHatasi("request_interval_seconds 0–60 saniye olmalı.")
    kaynaklar = deger.get("sources", {})
    if not isinstance(kaynaklar, dict) or set(kaynaklar) - set(KAYNAKLAR):
        raise SurumAkisiHatasi("sources yalnız endoflife, depsdev, pypi ve github anahtarlarını alır.")
    for ad in KAYNAKLAR:
        acik = kaynaklar.get(ad, ad != "github")
        if type(acik) is not bool:
            raise SurumAkisiHatasi("Kaynak anahtarı true ya da false olmalı.")
        ayar["sources"][ad] = acik
    urunler = deger.get("products", {})
    if not isinstance(urunler, dict) or len(urunler) > 500:
        raise SurumAkisiHatasi("products bir nesne olmalı (en çok 500 ürün).")
    for ad, eslesme in urunler.items():
        ayar["products"][ad] = _urun_eslesmesi(ad, eslesme)
    raporlar = deger.get("v1_reports", {})
    if not isinstance(raporlar, dict) or len(raporlar) > 5000:
        raise SurumAkisiHatasi("v1_reports bir nesne olmalı.")
    for kimlik, kapsam in raporlar.items():
        if not isinstance(kimlik, str) or not re.fullmatch(r"[a-f0-9]{32}", kimlik):
            raise SurumAkisiHatasi("v1_reports anahtarı 32 haneli rapor kimliği olmalı.")
        ayar["v1_reports"][kimlik] = [kapsam_ogesi(oge) for oge in _liste(kapsam, 10)]
    return ayar


def _liste(deger, en_cok):
    if not isinstance(deger, list) or not 1 <= len(deger) <= en_cok:
        raise SurumAkisiHatasi("Kapsam listesi 1–%d öğe olmalı." % en_cok)
    return deger


def _urun_eslesmesi(ad, eslesme):
    if not isinstance(ad, str) or not URUN_ADI.fullmatch(ad):
        raise SurumAkisiHatasi("Ürün adı küçük harf, rakam ve tire ile 1–64 karakter olmalı.")
    izinli = {"endoflife", "purl", "github", "aliases", "cycle_names"}
    if not isinstance(eslesme, dict) or set(eslesme) - izinli:
        raise SurumAkisiHatasi("Ürün eşlemesi yalnız endoflife, purl, github, aliases ve cycle_names alanlarını alır.")
    sonuc = {"cycle_names": {}}
    kod_adlari = eslesme.get("cycle_names", {})
    if not isinstance(kod_adlari, dict) or len(kod_adlari) > 100:
        raise SurumAkisiHatasi("cycle_names kod adı → sayısal sürüm nesnesi olmalı (en çok 100).")
    for kod, surum in kod_adlari.items():
        if (not isinstance(kod, str) or not KOD_ADI.fullmatch(kod) or not isinstance(surum, str)
                or not re.fullmatch(r"\d+(?:\.\d+){0,3}", surum)):
            raise SurumAkisiHatasi("cycle_names anahtarı küçük harfli kod adı, değeri sayısal sürüm metni olmalı (ör. noetic → 1).")
        sonuc["cycle_names"][kod] = surum
    if "endoflife" in eslesme:
        if not isinstance(eslesme["endoflife"], str) or not URUN_ADI.fullmatch(eslesme["endoflife"]):
            raise SurumAkisiHatasi("endoflife kimliği küçük harf, rakam ve tire olmalı.")
        sonuc["endoflife"] = eslesme["endoflife"]
    if "purl" in eslesme:
        sonuc["purl"] = purl_ayristir(eslesme["purl"])
    if "github" in eslesme:
        if not isinstance(eslesme["github"], str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}", eslesme["github"]):
            raise SurumAkisiHatasi("github eşlemesi sahip/depo biçiminde olmalı.")
        sonuc["github"] = eslesme["github"]
    takma = eslesme.get("aliases", [])
    if not isinstance(takma, list) or len(takma) > 10 or any(
            not isinstance(t, str) or not 1 <= len(t) <= 60 or "\n" in t for t in takma):
        raise SurumAkisiHatasi("aliases en çok 10 kısa metin olmalı.")
    sonuc["aliases"] = list(takma)
    return sonuc


def purl_ayristir(metin):
    """pkg:tür/ad biçimini çözer; yalnız deps.dev ve PyPI'nin tanıdığı türler kabul edilir."""
    if not isinstance(metin, str) or not metin.startswith("pkg:") or len(metin) > 300:
        raise SurumAkisiHatasi("purl pkg:tür/ad biçiminde olmalı.")
    govde = metin[4:].split("?", 1)[0].split("#", 1)[0]
    if "@" in govde:
        govde = govde.rsplit("@", 1)[0]
    tur, _, ad = govde.partition("/")
    tur = tur.lower()
    ad = urllib.parse.unquote(ad)
    if tur not in DEPSDEV_SISTEMLERI or not ad or "/" in ad.strip("/") and tur not in ("npm", "golang", "maven"):
        raise SurumAkisiHatasi("purl türü desteklenmiyor ya da ad eksik.")
    if tur == "maven":
        if ad.count("/") != 1:
            raise SurumAkisiHatasi("maven purl grup/ad biçiminde olmalı.")
        ad = ad.replace("/", ":")
    if not re.fullmatch(r"[A-Za-z0-9@._/:+-]{1,200}", ad):
        raise SurumAkisiHatasi("purl adı beklenmeyen karakter içeriyor.")
    return {"type": tur, "name": ad}


def kapsam_ogesi(oge):
    """K1 r2 `applies_to` öğesi: product, versions, version_scheme."""
    if not isinstance(oge, dict) or set(oge) - {"product", "versions", "version_scheme"}:
        raise SurumAkisiHatasi("Kapsam öğesi product, versions ve version_scheme alanlarını alır.")
    urun, surumler, duzen = oge.get("product"), oge.get("versions"), oge.get("version_scheme", "exact")
    if not isinstance(urun, str) or not URUN_ADI.fullmatch(urun):
        raise SurumAkisiHatasi("Kapsam ürünü küçük harf, rakam ve tire olmalı.")
    if not isinstance(surumler, str) or not 1 <= len(surumler) <= 80:
        raise SurumAkisiHatasi("Kapsam sürümü 1–80 karakter olmalı.")
    if duzen not in ("semver", "pep440", "exact"):
        raise SurumAkisiHatasi("version_scheme semver, pep440 ya da exact olmalı.")
    return {"product": urun, "versions": surumler, "version_scheme": duzen}


def ornek_ayar():
    return {"schema": SEMA, "enabled": False,
            "sources": {"endoflife": True, "depsdev": True, "pypi": True, "github": False},
            "request_interval_seconds": 1,
            "products": {"python": {"endoflife": "python", "aliases": ["Python"]},
                         "fastapi": {"purl": "pkg:pypi/fastapi"},
                         "left-pad": {"purl": "pkg:npm/left-pad"},
                         "ros": {"endoflife": "ros", "aliases": ["ROS"],
                                 "cycle_names": {"kinetic": "1.12", "lunar": "1.13", "melodic": "1.14", "noetic": "1.15"}}},
            "v1_reports": {}}


# --- sürüm metni -------------------------------------------------------------------------------------

def surum_ayristir(metin):
    """Baştaki sayısal parçaları tam sayı demetine çevirir; ayrıştırılamazsa None."""
    if not isinstance(metin, str):
        return None
    eslesme = re.match(r"\s*v?(\d+(?:\.\d+){0,3})", metin)
    if not eslesme:
        return None
    return tuple(int(parca) for parca in eslesme.group(1).split("."))


def kapsam_surumu(surumler, kod_adlari=None):
    """Kapsam metnindeki en yeni sayısal sürüm; '>=3.11,<3.13' için 3.13 değil 3.11 (üst sınır dışlanır).

    Kod adlı kapsam ('jazzy') yalnız ürünün `cycle_names` eşlemesiyle sayısala çevrilir."""
    if kod_adlari and isinstance(surumler, str) and surumler.strip().lower() in kod_adlari:
        return surum_ayristir(kod_adlari[surumler.strip().lower()])
    adaylar = []
    for eslesme in SURUM_PARCASI.finditer(surumler):
        bas = eslesme.start()
        onceki = surumler[max(0, bas - 2):bas].strip()
        if onceki.endswith("<") and not onceki.endswith("<="):
            continue  # dışlanan üst sınır kapsamda değildir
        adaylar.append(surum_ayristir(eslesme.group(1)))
    adaylar = [a for a in adaylar if a]
    return max(adaylar) if adaylar else None


def _dolgu(demet, uzunluk):
    return tuple(demet) + (0,) * (uzunluk - len(demet))


def karsilastir(referans, guncel):
    """Referansın hassasiyetinde karşılaştırır: tek parça yalnız büyük, iki parça büyük+küçük, üçten çok yama.

    Dönüş: ("major"|"minor"|"patch"|None, True/False) — daha yeni sürüm türü ve güncel olup olmadığı."""
    if not referans or not guncel:
        return None, False
    hassasiyet = max(1, min(len(referans), 3))
    r, g = _dolgu(referans, 3), _dolgu(guncel, 3)
    if g[0] > r[0]:
        return "major", False
    if g[0] < r[0]:
        return None, True
    if hassasiyet >= 2 and g[1] > r[1]:
        return "minor", False
    if hassasiyet >= 2 and g[1] < r[1]:
        return None, True
    if hassasiyet >= 3 and g[2] > r[2]:
        return "patch", False
    return None, True


def metinden_kapsam(metin, urunler):
    """Yalnız ayarda `aliases` verilen ürünler için 'Python 3.12' gibi açık, noktalı sürüm anmasını bulur.

    Büyük/küçük harfe duyarlıdır (ürün anahtarı taranmaz). '3.8+' ve '>=3.8' gibi aralık anmaları atlanır;
    birden çok anma varsa en yeni sürüm alınır."""
    sonuc = []
    if not isinstance(metin, str) or not metin:
        return sonuc
    for ad, eslesme in urunler.items():
        bulunanlar = []
        for takma in eslesme.get("aliases", []):
            desen = re.compile(r"(?<![\w-])" + re.escape(takma) + r"\s+(?![<>=~^])v?(\d+(?:\.\d+){1,3})(?![\w+*.])")
            for eslesen in desen.finditer(metin):
                aday = surum_ayristir(eslesen.group(1))
                if aday:
                    bulunanlar.append((aday, eslesen.group(1)))
        if bulunanlar:
            sonuc.append({"product": ad, "versions": max(bulunanlar)[1], "version_scheme": "exact", "derived": "text"})
    return sonuc


# --- kayıtlar -----------------------------------------------------------------------------------------

def _salt_okunur(path):
    path = Path(path)
    if not path.is_file():
        raise SurumAkisiHatasi("Depo dosyası bulunamadı.")
    baglanti = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    baglanti.row_factory = sqlite3.Row
    return baglanti


def v1_kayitlari(community_db, ayar):
    """Yayındaki v1 araştırmalar (güncel kamu sürümü). Kapsam: ayar eşlemesi, yoksa takma ad taraması."""
    sonuc = []
    with closing(_salt_okunur(community_db)) as baglanti:
        tablolar = {row[0] for row in baglanti.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"community_entries", "community_revisions", "community_public_revisions"} <= tablolar:
            raise SurumAkisiHatasi("Dosya topluluk veritabanı değil.")
        rows = baglanti.execute(
            "SELECT e.id,e.published_revision AS version,r.packet FROM community_entries e "
            "JOIN community_revisions r ON r.entry_id=e.id AND r.version=e.published_revision "
            "JOIN community_public_revisions p ON p.entry_id=e.id AND p.version=r.version AND p.visible=1 "
            "WHERE e.published_revision IS NOT NULL AND r.kind='research' ORDER BY e.id").fetchall()
    for row in rows:
        paket = json.loads(row["packet"])
        kapsam = ayar["v1_reports"].get(row["id"])
        kaynak = "config"
        if kapsam is None:
            kapsam = metinden_kapsam(" ".join([paket.get("title", ""), paket.get("summary", "")]), ayar["products"])
            kaynak = "text" if kapsam else None
        sonuc.append({"ref": f"v1:{row['id']}:{row['version']}", "kind": "v1", "id": row["id"], "version": row["version"],
                      "title": paket.get("title", ""), "applies_to": kapsam, "scope_source": kaynak})
    return sonuc


def v4_kayitlari(k1_db, ayar):
    """K1 deposundaki görünür v4 kayıtlar; kapsam iddiaların ve içeriğin `applies_to` öğelerinden."""
    sonuc = []
    with closing(_salt_okunur(k1_db)) as baglanti:
        tablolar = {row[0] for row in baglanti.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "v4_records" not in tablolar:
            raise SurumAkisiHatasi("Dosya K1 v4 deposu değil.")
        rows = baglanti.execute("SELECT id,version,record FROM v4_records WHERE visible=1 ORDER BY id,version").fetchall()
    for row in rows:
        kayit = json.loads(row["record"])
        if not isinstance(kayit, dict) or not kayit:
            continue
        kapsam, gorulen = [], set()
        ogeler = []
        for iddia in kayit.get("claims") or []:
            ogeler.extend(iddia.get("applies_to") or [])
        icerik = kayit.get("content") or {}
        if isinstance(icerik.get("applies_to"), list):
            ogeler.extend(icerik["applies_to"])
        for oge in ogeler:
            try:
                temiz = kapsam_ogesi(oge)
            except SurumAkisiHatasi:
                continue
            imza = json.dumps(temiz, sort_keys=True)
            if imza not in gorulen:
                gorulen.add(imza)
                kapsam.append(temiz)
        kaynak = "applies_to" if kapsam else None
        if not kapsam and isinstance(icerik.get("applies_to"), str):
            kapsam = metinden_kapsam(icerik["applies_to"], ayar["products"])
            kaynak = "text" if kapsam else None
        sonuc.append({"ref": f"v4:{row['id']}:{row['version']}", "kind": "v4", "id": row["id"], "version": row["version"],
                      "title": icerik.get("title", ""), "applies_to": kapsam, "scope_source": kaynak})
    return sonuc


# --- ağ -----------------------------------------------------------------------------------------------

class _AyniAlanYonlendirme(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        eski, yeni = urllib.parse.urlsplit(req.full_url), urllib.parse.urlsplit(newurl)
        if yeni.scheme != "https" or yeni.netloc != eski.netloc:
            raise urllib.error.HTTPError(newurl, code, "yönlendirme başka alana", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def varsayilan_getirici(url, headers):
    """HTTPS, aynı alan içinde yönlendirme, 2 MB gövde sınırı. (durum, gövde) döndürür; ağ hatası (0, b'')."""
    parcalar = urllib.parse.urlsplit(url)
    if parcalar.scheme != "https" or f"https://{parcalar.netloc}" not in KAYNAK_ALANLARI.values():
        raise SurumAkisiHatasi("Yalnız izinli kaynak alanlarına HTTPS isteği yapılır.")
    istek = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity", **headers})
    acici = urllib.request.build_opener(_AyniAlanYonlendirme())
    try:
        with acici.open(istek, timeout=TIMEOUT_SECONDS) as yanit:
            govde = yanit.read(MAX_BODY_BYTES + 1)
            if len(govde) > MAX_BODY_BYTES:
                return 0, b""
            return yanit.status, govde
    except urllib.error.HTTPError as exc:
        return exc.code, b""
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return 0, b""


# --- akış ---------------------------------------------------------------------------------------------

class SurumAkisi:
    """Ayrı SQLite önbelleğiyle çalışır; kaynak başına günde en çok bir istek."""

    def __init__(self, db_path, ayar, *, fetcher=None, clock=None, sleeper=None):
        self.path = Path(db_path)
        self.ayar = ayar
        self.fetcher = fetcher or varsayilan_getirici
        self.clock = clock or time.time
        self.sleeper = sleeper or time.sleep
        self.requests = 0
        self._son_istek = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.baglanti(write=True) as baglanti:
            baglanti.executescript("""
                CREATE TABLE IF NOT EXISTS feed_cache (
                    source TEXT NOT NULL, key TEXT NOT NULL, day TEXT NOT NULL, fetched_at INTEGER NOT NULL,
                    status TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(source,key)
                );
                CREATE TABLE IF NOT EXISTS feed_labels (
                    ref TEXT NOT NULL, product TEXT NOT NULL, kind TEXT NOT NULL, record_id TEXT NOT NULL,
                    record_version INTEGER NOT NULL, label TEXT NOT NULL, detail TEXT NOT NULL,
                    checked_at INTEGER NOT NULL, PRIMARY KEY(ref,product)
                );
                CREATE TABLE IF NOT EXISTS feed_runs (
                    id INTEGER PRIMARY KEY, started_at INTEGER NOT NULL, finished_at INTEGER NOT NULL,
                    requests INTEGER NOT NULL, records INTEGER NOT NULL, summary TEXT NOT NULL
                );
            """)

    @contextmanager
    def baglanti(self, write=False):
        baglanti = sqlite3.connect(self.path, timeout=10)
        baglanti.row_factory = sqlite3.Row
        try:
            if write:
                baglanti.execute("BEGIN IMMEDIATE")
            yield baglanti
            baglanti.commit()
        except BaseException:
            baglanti.rollback()
            raise
        finally:
            baglanti.close()

    # -- önbellekli getirme --

    def _getir(self, kaynak, anahtar, url, headers=None):
        """Aynı UTC gününde aynı anahtar için ikinci istek yapılmaz; hata da günlük tek denemedir."""
        if not self.ayar["sources"].get(kaynak, False):
            return {"status": "disabled", "payload": None}
        bugun = gun_metni(self.clock())
        with self.baglanti() as baglanti:
            row = baglanti.execute("SELECT day,status,payload FROM feed_cache WHERE source=? AND key=?",
                                   (kaynak, anahtar)).fetchone()
        if row is not None and row["day"] == bugun:
            return {"status": row["status"], "payload": json.loads(row["payload"]) if row["status"] == "ok" else None,
                    "reason": row["payload"] if row["status"] == "unparsed" else None, "cached": True}
        self._kibar_bekle()
        durum, govde = self.fetcher(url, headers or {"Accept": "application/json"})
        self.requests += 1
        yuk, etiket = None, "error"
        if durum == 200:
            try:
                yuk = json.loads(govde.decode("utf-8"))
                etiket = "ok"
            except (UnicodeDecodeError, json.JSONDecodeError):
                etiket = "bad_json"
        elif durum == 404:
            etiket = "not_found"
        elif durum == 429:
            etiket = "rate_limited"
        elif durum:
            etiket = f"http_{durum}"
        with self.baglanti(write=True) as baglanti:
            baglanti.execute("INSERT INTO feed_cache VALUES(?,?,?,?,?,?) ON CONFLICT(source,key) DO UPDATE SET "
                             "day=excluded.day,fetched_at=excluded.fetched_at,status=excluded.status,payload=excluded.payload",
                             (kaynak, anahtar, bugun, int(self.clock()), etiket,
                              json.dumps(yuk, ensure_ascii=False) if etiket == "ok" else ""))
        return {"status": etiket, "payload": yuk, "reason": None, "cached": False}

    def _ayristirilamadi(self, kaynak, anahtar, url, neden):
        """Yanıt 200 ama sürüm bilgisi çıkmadı: önbellekte 'ok' değil 'unparsed' kalır (günde 1 istek kuralı sürer)."""
        with self.baglanti(write=True) as baglanti:
            baglanti.execute("UPDATE feed_cache SET status='unparsed',payload=? WHERE source=? AND key=?", (neden, kaynak, anahtar))
        return {"error": neden, "source_status": "unparsed", "source_url": url}

    @staticmethod
    def _kaynak_hatasi(sonuc, url):
        if sonuc["status"] == "unparsed":
            return {"error": sonuc.get("reason") or "source_payload_unparsed", "source_status": "unparsed", "source_url": url}
        return {"error": "source_unavailable", "source_status": sonuc["status"], "source_url": url}

    def _kibar_bekle(self):
        aralik = self.ayar["request_interval_seconds"]
        if self._son_istek is not None and aralik > 0:
            self.sleeper(aralik)
        self._son_istek = self.clock()

    # -- kaynak bağdaştırıcıları: {"latest": str, "latest_date": str|None, "eol": {...}|None, "source_url": str} --

    def endoflife_urunleri(self):
        sonuc = self._getir("endoflife", "products", "https://endoflife.date/api/v1/products")
        if sonuc["status"] != "ok" or not isinstance(sonuc["payload"], dict):
            return None
        urunler = sonuc["payload"].get("result")
        if not isinstance(urunler, list):
            return None
        return {u.get("name") for u in urunler if isinstance(u, dict) and isinstance(u.get("name"), str)}

    def endoflife(self, kimlik, referans, kod_adlari=None):
        """Kod adlı döngüler (ros: noetic, humble) yalnız ürünün `cycle_names` eşlemesiyle sayılır; yoksa 'sayısal değil'."""
        kod_adlari = kod_adlari or {}
        anahtar = "product:" + kimlik
        url = f"https://endoflife.date/api/v1/products/{urllib.parse.quote(kimlik, safe='')}"
        sonuc = self._getir("endoflife", anahtar, url)
        if sonuc["status"] != "ok":
            return self._kaynak_hatasi(sonuc, url)
        govde = sonuc["payload"].get("result") if isinstance(sonuc["payload"], dict) else None
        yayinlar = govde.get("releases") if isinstance(govde, dict) else None
        if not isinstance(yayinlar, list) or not yayinlar:
            return self._ayristirilamadi("endoflife", anahtar, url, "source_payload_unparsed")

        def dongu_surumu(ad):
            if isinstance(ad, str) and ad.strip().lower() in kod_adlari:
                return surum_ayristir(kod_adlari[ad.strip().lower()])
            return surum_ayristir(ad)

        en_yeni, en_yeni_tarih, en_yeni_dongu = None, None, None
        for yayin in yayinlar:
            if not isinstance(yayin, dict):
                continue
            son = yayin.get("latest") if isinstance(yayin.get("latest"), dict) else {}
            aday = surum_ayristir(son.get("name")) or dongu_surumu(yayin.get("name"))
            if aday and (en_yeni is None or aday > en_yeni):
                en_yeni, en_yeni_tarih, en_yeni_dongu = aday, son.get("date") or yayin.get("releaseDate"), yayin.get("name")
        if en_yeni is None:
            return self._ayristirilamadi("endoflife", anahtar, url, "version_names_not_numeric")
        eol = None
        if referans:
            for yayin in yayinlar:
                if not isinstance(yayin, dict):
                    continue
                dongu = dongu_surumu(yayin.get("name"))
                if dongu and dongu == referans[:len(dongu)] and len(dongu) <= len(referans):
                    eol = {"cycle": yayin.get("name"), "is_eol": bool(yayin.get("isEol")), "eol_from": yayin.get("eolFrom"),
                           "is_eoas": yayin.get("isEoas"), "is_maintained": yayin.get("isMaintained")}
                    break
        return {"latest": en_yeni, "latest_date": en_yeni_tarih, "latest_cycle": en_yeni_dongu, "eol": eol, "source_url": url,
                "source": "endoflife"}

    def pypi(self, ad):
        url = f"https://pypi.org/pypi/{urllib.parse.quote(ad, safe='')}/json"
        sonuc = self._getir("pypi", ad, url)
        if sonuc["status"] != "ok":
            return self._kaynak_hatasi(sonuc, url)
        yuk = sonuc["payload"]
        bilgi = yuk.get("info") if isinstance(yuk, dict) else None
        if not isinstance(bilgi, dict) or not isinstance(bilgi.get("version"), str):
            return self._ayristirilamadi("pypi", ad, url, "source_payload_unparsed")
        surum = surum_ayristir(bilgi["version"])
        if not surum:
            return self._ayristirilamadi("pypi", ad, url, "version_names_not_numeric")
        tarih = None
        dosyalar = (yuk.get("releases") or {}).get(bilgi["version"]) if isinstance(yuk.get("releases"), dict) else None
        if isinstance(dosyalar, list) and dosyalar and isinstance(dosyalar[0], dict):
            tarih = dosyalar[0].get("upload_time_iso_8601") or dosyalar[0].get("upload_time")
        return {"latest": surum, "latest_date": tarih, "eol": None, "source_url": url, "source": "pypi"}

    def depsdev(self, purl):
        sistem = DEPSDEV_SISTEMLERI[purl["type"]]
        anahtar = f"{sistem}:{purl['name']}"
        url = f"https://api.deps.dev/v3/systems/{sistem}/packages/{urllib.parse.quote(purl['name'], safe='')}"
        sonuc = self._getir("depsdev", anahtar, url)
        if sonuc["status"] != "ok":
            return self._kaynak_hatasi(sonuc, url)
        surumler = sonuc["payload"].get("versions") if isinstance(sonuc["payload"], dict) else None
        if not isinstance(surumler, list) or not surumler:
            return self._ayristirilamadi("depsdev", anahtar, url, "source_payload_unparsed")
        varsayilan, en_yeni = None, None
        for oge in surumler:
            if not isinstance(oge, dict):
                continue
            surum_anahtari = oge.get("versionKey") if isinstance(oge.get("versionKey"), dict) else {}
            aday = surum_ayristir(surum_anahtari.get("version"))
            if not aday:
                continue
            kayit = (aday, oge.get("publishedAt"))
            if oge.get("isDefault"):
                varsayilan = kayit
            if en_yeni is None or aday > en_yeni[0]:
                en_yeni = kayit
        secilen = varsayilan or en_yeni
        if secilen is None:
            return self._ayristirilamadi("depsdev", anahtar, url, "version_names_not_numeric")
        return {"latest": secilen[0], "latest_date": secilen[1], "eol": None, "source_url": url, "source": "depsdev"}

    def github(self, depo):
        url = f"https://api.github.com/repos/{depo}/releases/latest"
        sonuc = self._getir("github", depo, url, {"Accept": "application/vnd.github+json"})
        if sonuc["status"] != "ok":
            return self._kaynak_hatasi(sonuc, url)
        yuk = sonuc["payload"]
        if not isinstance(yuk, dict) or not isinstance(yuk.get("tag_name"), str):
            return self._ayristirilamadi("github", depo, url, "source_payload_unparsed")
        surum = surum_ayristir(yuk["tag_name"])
        if not surum:
            return self._ayristirilamadi("github", depo, url, "version_names_not_numeric")
        return {"latest": surum, "latest_date": yuk.get("published_at"), "eol": None, "source_url": url, "source": "github"}

    # -- eşleme ve etiket --

    def urunu_coz(self, oge, endoflife_adlari):
        """Sıra: ayar eşlemesi (endoflife > purl > github); sonra endoflife.date ürün listesindeki aynı ad.

        Eşlenmemiş `pep440` ürün adı PyPI'da aranmaz (aynı adlı ilgisiz paket yanlış etiket üretir);
        PyPI yalnız açık `purl: pkg:pypi/...` eşlemesiyle kullanılır."""
        ad = oge["product"]
        eslesme = self.ayar["products"].get(ad, {})
        if eslesme.get("endoflife") and self.ayar["sources"]["endoflife"]:
            return ("endoflife", eslesme["endoflife"])
        if eslesme.get("purl"):
            purl = eslesme["purl"]
            if purl["type"] == "pypi" and self.ayar["sources"]["pypi"]:
                return ("pypi", purl["name"])
            if self.ayar["sources"]["depsdev"]:
                return ("depsdev", purl)
        if eslesme.get("github") and self.ayar["sources"]["github"]:
            return ("github", eslesme["github"])
        if endoflife_adlari and ad in endoflife_adlari and self.ayar["sources"]["endoflife"]:
            return ("endoflife", ad)
        return None

    @staticmethod
    def _bilinmiyor(ayrinti, neden, **ek):
        return "unknown", {**ayrinti, "reason": neden, "reason_tr": NEDENLER[neden], **ek}

    def etiketle(self, oge, cozum, simdi):
        kod_adlari = self.ayar["products"].get(oge["product"], {}).get("cycle_names") or {}
        referans = kapsam_surumu(oge["versions"], kod_adlari)
        ayrinti = {"product": oge["product"], "versions": oge["versions"], "version_scheme": oge["version_scheme"],
                   "reference_version": ".".join(map(str, referans)) if referans else None,
                   "scope_derived_from": oge.get("derived"), "checked_at": simdi_metni(simdi)}
        if cozum is None:
            return self._bilinmiyor(ayrinti, "product_not_mapped")
        kaynak, hedef = cozum
        bilgi = self.endoflife(hedef, referans, kod_adlari) if kaynak == "endoflife" else getattr(self, kaynak)(hedef)
        ayrinti["source"] = kaynak
        ayrinti["source_url"] = bilgi.get("source_url")
        if "error" in bilgi:
            return self._bilinmiyor(ayrinti, bilgi["error"], source_status=bilgi["source_status"])
        ayrinti["latest_version"] = ".".join(map(str, bilgi["latest"]))
        ayrinti["latest_date"] = bilgi.get("latest_date")
        if bilgi.get("latest_cycle") is not None:
            ayrinti["latest_cycle"] = bilgi["latest_cycle"]
        if referans is None:
            return self._bilinmiyor(ayrinti, "scope_version_unparsed")
        tur, guncel = karsilastir(referans, bilgi["latest"])
        eol = bilgi.get("eol")
        if eol:
            ayrinti["cycle"] = eol["cycle"]
            ayrinti["eol_date"] = eol.get("eol_from")
            ayrinti["is_eol"] = eol["is_eol"]
        if eol and eol["is_eol"]:
            return "maybe_outdated", {**ayrinti, "trigger": "support_ended"}
        if tur == "major":
            return "maybe_outdated", {**ayrinti, "trigger": "new_major_version"}
        if tur in ("minor", "patch"):
            return "newer_version", {**ayrinti, "trigger": f"new_{tur}_version"}
        return "current", ayrinti

    def calistir(self, kayitlar):
        """Bütün kayıtları etiketler, önbelleği ve etiket tablosunu günceller; özet döndürür."""
        basladi = int(self.clock())
        self.requests = 0
        endoflife_adlari = None
        if self.ayar["sources"]["endoflife"] and any(
                oge["product"] not in self.ayar["products"] for k in kayitlar for oge in (k["applies_to"] or [])):
            endoflife_adlari = self.endoflife_urunleri()
        sonuclar, sayim, hatalar = [], dict.fromkeys(ETIKETLER, 0), 0
        for kayit in kayitlar:
            etiketler = []
            if not kayit["applies_to"]:
                etiketler.append(self._bilinmiyor({"product": None, "checked_at": simdi_metni(self.clock())}, "no_applies_to"))
            for oge in kayit["applies_to"] or []:
                # Bir ürünün kaynağı ya da yanıtı ne olursa olsun tur sürer; o ürün "bilinmiyor" kalır.
                try:
                    cozum = self.urunu_coz(oge, endoflife_adlari)
                    etiketler.append(self.etiketle(oge, cozum, int(self.clock())))
                except Exception as exc:  # noqa: BLE001 - kaynak başına yalıtım; hata türü yazılır, iletisi yazılmaz
                    hatalar += 1
                    etiketler.append(self._bilinmiyor({"product": oge.get("product"), "versions": oge.get("versions"),
                                                       "checked_at": simdi_metni(self.clock())}, "adapter_error",
                                                      error_type=type(exc).__name__))
            kayit_etiketi = max((e for e, _ in etiketler), key=ETIKET_SIRASI.__getitem__)
            sayim[kayit_etiketi] += 1
            sonuclar.append({"ref": kayit["ref"], "kind": kayit["kind"], "id": kayit["id"], "version": kayit["version"],
                             "title": kayit["title"], "label": kayit_etiketi, "label_tr": ETIKETLER[kayit_etiketi],
                             "scope_source": kayit["scope_source"],
                             "products": [{"label": e, "label_tr": ETIKETLER[e], **a} for e, a in etiketler]})
        bitti = int(self.clock())
        ozet = {"schema": SEMA, "started_at": simdi_metni(basladi), "finished_at": simdi_metni(bitti),
                "records": len(kayitlar), "requests": self.requests, "counts": sayim, "adapter_errors": hatalar, "model_calls": 0}
        with self.baglanti(write=True) as baglanti:
            for sonuc in sonuclar:
                for urun in sonuc["products"]:
                    baglanti.execute("INSERT INTO feed_labels VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(ref,product) DO UPDATE SET "
                                     "label=excluded.label,detail=excluded.detail,checked_at=excluded.checked_at",
                                     (sonuc["ref"], urun.get("product") or "", sonuc["kind"], sonuc["id"], sonuc["version"],
                                      urun["label"], json.dumps(urun, ensure_ascii=False, sort_keys=True), bitti))
            baglanti.execute("INSERT INTO feed_runs(started_at,finished_at,requests,records,summary) VALUES(?,?,?,?,?)",
                             (basladi, bitti, self.requests, len(kayitlar), json.dumps(ozet, ensure_ascii=False)))
        return {**ozet, "labels": sonuclar}

    def etiketleri_oku(self, kind=None, record_id=None, version=None):
        """Sitenin ya da MCP'nin göstereceği etiketler; kayıt gizlenmez, tetikleyen sürüm ve tarih ayrıntıdadır."""
        kosul, degerler = [], []
        if kind:
            kosul.append("kind=?")
            degerler.append(kind)
        if record_id:
            kosul.append("record_id=?")
            degerler.append(record_id)
        if version is not None:
            kosul.append("record_version=?")
            degerler.append(version)
        where = " WHERE " + " AND ".join(kosul) if kosul else ""
        with self.baglanti() as baglanti:
            rows = baglanti.execute("SELECT ref,kind,record_id,record_version,label,detail,checked_at FROM feed_labels" + where +
                                    " ORDER BY ref,product", degerler).fetchall()
        return [{"ref": r["ref"], "kind": r["kind"], "id": r["record_id"], "version": r["record_version"],
                 "label": r["label"], "label_tr": ETIKETLER[r["label"]], "checked_at": simdi_metni(r["checked_at"]),
                 "detail": json.loads(r["detail"])} for r in rows]


def kayitlari_topla(community_db, ayar, k1_db=None):
    kayitlar = v1_kayitlari(community_db, ayar)
    if k1_db is not None:
        kayitlar.extend(v4_kayitlari(k1_db, ayar))
    return kayitlar


def gece_isi(ayar_yolu, db_yolu, community_db, *, k1_db=None, fetcher=None, clock=None, sleeper=None, force=False):
    """Gece işi girişi: ayar kapalıysa hiçbir şey okumaz ve istek atmaz (force yalnız elle deneme için)."""
    ayar = ayarlari_oku(ayar_yolu)
    if not ayar["enabled"] and not force:
        return {"schema": SEMA, "status": "disabled", "requests": 0, "records": 0, "model_calls": 0}
    akis = SurumAkisi(db_yolu, ayar, fetcher=fetcher, clock=clock, sleeper=sleeper)
    sonuc = akis.calistir(kayitlari_topla(community_db, ayar, k1_db))
    return {**sonuc, "status": "ok"}
