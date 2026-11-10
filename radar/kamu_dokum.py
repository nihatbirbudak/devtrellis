"""Yalnız yayımlanmış kamu kayıtlarını JSON dökümüne yazar; döküm yeni boş bir depoya geri okunabilir.

Dökümde olmayanlar: özel aday metni, itiraz özel metinleri, moderatör notları, kimlik ve IP kovaları,
oturum ve yönetim anahtarları, parola özetleri, sayaçlar. Kaynak depolar salt okunur açılır.
Kayıt türleri: v1 paylaşım paketleri (`community_*`) ve K1 v4 kayıtları (`v4_records`, varsa).
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

SEMA = "devtrellis-public-dump"
SEMA_SURUMU = 1
URETICI = "devtrellis kamu-dokum 1"
DOSYA_ONEKI = f"devtrellis-public-dump-v{SEMA_SURUMU}-"
MAX_DUMP_BYTES = 512 * 1024 * 1024

# Kapalı alan kümeleri, iç içe dahil: bilinmeyen bir alan dökümü DURDURUR (yeni özel alan farkında olmadan dışarı çıkmasın).
# v1 kümeleri `radar/gelistirici_alani.py` (kayit_dogrula, arastirma_kokeni, arastirma_gelisimi), v4 kümeleri
# K1 r2 `public-record-v4-r2.schema.json` (hepsinde additionalProperties:false) ve aday şemasının provenance/applies_to tanımları.
V1_KAYIT_ALANLARI = {"schema", "kind", "id", "version", "submitted_at", "published_at", "link", "packet"}
V1_PAKET_ALANLARI = {"schema", "schema_version", "kind", "title", "summary", "body", "url", "tags", "sources", "author",
                     "license", "created_at", "updated_at", "provenance", "lineage"}
V1_KAYNAK_ALANLARI = {"title", "url"}
V1_KOKEN_ALANLARI = {"model", "provider", "generated_at", "question", "topic", "source_url", "evidence_status", "language",
                     "modified", "claim_text"}
V1_GELISIM_ALANLARI = {"parents", "change_summary"}
V1_EBEVEYN_ALANLARI = {"research_id", "version"}
V4_KAYIT_ALANLARI = {"schema", "schema_version", "contract", "kind", "id", "version", "language", "content", "claims",
                     "provenance", "rights", "publication", "corrections", "link"}
V4_ICERIK_ALANLARI = {"title", "summary", "short_answer", "answers", "recommendations", "not_covered", "applies_to", "sources",
                      "generated_at"}
V4_KAPSAM_ALANLARI = {"product", "versions", "version_scheme"}
V4_KAYNAK_ALANLARI = {"title", "url"}
V4_IDDIA_ALANLARI = {"id", "text", "claim_type", "applies_to", "evidence", "quote_match", "claim_review", "evidence_state"}
V4_KANIT_ALANLARI = {"quote", "source_url", "address_key", "quote_hash", "normalization_version", "visible_text_version",
                     "checked_on", "section_id", "section_hash", "supporting_context_hash"}
V4_ALINTI_ALANLARI = {"state", "quote_verified_at"}
V4_INCELEME_ALANLARI = {"state", "reviewed_at"}
V4_KOKEN_ALANLARI = {"origin", "stages"}
V4_ASAMA_ALANLARI = {"provider", "model", "access_type", "basis", "terms_url"}
V4_HAK_ALANLARI = {"own_text_license", "quotes"}
V4_ALINTI_HAK_ALANLARI = {"basis", "max_chars"}
V4_YAYIN_ALANLARI = {"state", "path", "policy_version", "published_at", "human_reviewed", "gate", "label"}
# alpha.13b kayıt eki `machine_not_covered` (isteğe bağlı; yetenekler `record_extensions`).
V4_KAPI_ALANLARI = {"version", "ran_at", "input_sha256", "checks_passed", "warnings", "fields_not_tied_to_sources",
                    "machine_not_covered"}
V4_ETIKET_ALANLARI = {"tr", "en"}
V4_DUZELTME_ALANLARI = {"open", "latest_public_version", "public_note"}
V4_IZ_ALANLARI = {"schema", "schema_version", "contract", "id", "version", "state", "removed_at"}
LISANS_NOTU = {
    "records": "Each record keeps its own license: v1 packet.license and v4 rights.own_text_license are CC0-1.0 or "
               "CC-BY-4.0 and apply to the contributor's own text only. Quotes inside v4 claims are short source-linked "
               "quotes (at most 300 characters); the quoted sources keep their own licenses, which this dump does not change. "
               "This note is not legal advice.",
    "dump_structure": "CC0-1.0",
    "attribution": "For CC-BY-4.0 records credit the record's author field (v1) or the record link (v4).",
    "note_tr": "Her kaydın lisansı kendi alanındadır (v1 packet.license, v4 rights.own_text_license; CC0-1.0 ya da "
               "CC-BY-4.0, yalnız katkıcının kendi metni için). Dökümün yapısı CC0-1.0. Alıntılanan kaynakların lisansı "
               "ayrıdır. Bu bir hukuki görüş değildir.",
    "legal_advice": False,
}


class DokumHatasi(ValueError):
    """Döküm ya da geri okuma durduruldu; özel içerik taşımaz."""


def zaman_metni(saniye):
    return datetime.fromtimestamp(saniye, timezone.utc).isoformat(timespec="seconds")


def _salt_okunur(path):
    path = Path(path)
    if not path.is_file():
        raise DokumHatasi("Depo dosyası bulunamadı.")
    baglanti = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    baglanti.row_factory = sqlite3.Row
    return baglanti


def _tablolar(baglanti):
    return {row[0] for row in baglanti.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def rapor_baglantisi(public_url, kimlik, surum):
    return f"{public_url.rstrip('/')}/r/{kimlik}/v/{surum}"


def _kapali(nesne, izinli, yol):
    """Yalnız anahtar adlarını denetler; bilinmeyen anahtar yolunu söyleyip durdurur (değer dışarı yazılmaz)."""
    if not isinstance(nesne, dict):
        raise DokumHatasi(f"{yol} bir nesne olmalı.")
    fazla = set(nesne) - izinli
    if fazla:
        raise DokumHatasi(f"{yol} içinde dökümde tanımlı olmayan alan var: " + ", ".join(sorted(fazla)))


def _liste(deger, yol):
    if deger is None:
        return []
    if not isinstance(deger, list):
        raise DokumHatasi(f"{yol} bir liste olmalı.")
    return deger


def v1_paketini_dogrula(paket):
    """v1 paketi ve iç nesneleri (sources[], provenance, lineage.parents[]) kapalı kümeyle."""
    _kapali(paket, V1_PAKET_ALANLARI, "packet")
    for i, kaynak in enumerate(_liste(paket.get("sources"), "packet.sources")):
        _kapali(kaynak, V1_KAYNAK_ALANLARI, f"packet.sources[{i}]")
    if paket.get("provenance") is not None:
        _kapali(paket["provenance"], V1_KOKEN_ALANLARI, "packet.provenance")
    if paket.get("lineage") is not None:
        _kapali(paket["lineage"], V1_GELISIM_ALANLARI, "packet.lineage")
        for i, ebeveyn in enumerate(_liste(paket["lineage"].get("parents"), "packet.lineage.parents")):
            _kapali(ebeveyn, V1_EBEVEYN_ALANLARI, f"packet.lineage.parents[{i}]")


def _kapsam_listesi(deger, yol):
    if isinstance(deger, list):
        for i, oge in enumerate(deger):
            _kapali(oge, V4_KAPSAM_ALANLARI, f"{yol}[{i}]")


def v4_kaydini_dogrula(kayit):
    """v4 kaydı ve bütün iç nesneleri r2 şemasının kapalı kümeleriyle; bilinmeyen iç alan dökümü durdurur."""
    if not isinstance(kayit, dict) or kayit.get("schema") != "devtrellis-record":
        raise DokumHatasi("v4 kaydı beklenen şemada değil.")
    _kapali(kayit, V4_KAYIT_ALANLARI, "record")
    icerik = kayit.get("content")
    if icerik is not None:
        _kapali(icerik, V4_ICERIK_ALANLARI, "record.content")
        _kapsam_listesi(icerik.get("applies_to"), "record.content.applies_to")
        for i, kaynak in enumerate(_liste(icerik.get("sources"), "record.content.sources")):
            _kapali(kaynak, V4_KAYNAK_ALANLARI, f"record.content.sources[{i}]")
    for i, iddia in enumerate(_liste(kayit.get("claims"), "record.claims")):
        yol = f"record.claims[{i}]"
        _kapali(iddia, V4_IDDIA_ALANLARI, yol)
        _kapsam_listesi(iddia.get("applies_to"), yol + ".applies_to")
        for ad, kume in (("evidence", V4_KANIT_ALANLARI), ("quote_match", V4_ALINTI_ALANLARI), ("claim_review", V4_INCELEME_ALANLARI)):
            if iddia.get(ad) is not None:
                _kapali(iddia[ad], kume, f"{yol}.{ad}")
    if kayit.get("provenance") is not None:
        _kapali(kayit["provenance"], V4_KOKEN_ALANLARI, "record.provenance")
        for i, asama in enumerate(_liste(kayit["provenance"].get("stages"), "record.provenance.stages")):
            _kapali(asama, V4_ASAMA_ALANLARI, f"record.provenance.stages[{i}]")
    if kayit.get("rights") is not None:
        _kapali(kayit["rights"], V4_HAK_ALANLARI, "record.rights")
        if kayit["rights"].get("quotes") is not None:
            _kapali(kayit["rights"]["quotes"], V4_ALINTI_HAK_ALANLARI, "record.rights.quotes")
    if kayit.get("publication") is not None:
        _kapali(kayit["publication"], V4_YAYIN_ALANLARI, "record.publication")
        if kayit["publication"].get("gate") is not None:
            _kapali(kayit["publication"]["gate"], V4_KAPI_ALANLARI, "record.publication.gate")
        if kayit["publication"].get("label") is not None:
            _kapali(kayit["publication"]["label"], V4_ETIKET_ALANLARI, "record.publication.label")
    if kayit.get("corrections") is not None:
        _kapali(kayit["corrections"], V4_DUZELTME_ALANLARI, "record.corrections")


def v1_kayitlari(community_db, public_url):
    """Görünür bütün yayımlanmış v1 sürümleri (araştırma ve proje). Hesap, oturum, kutu tabloları okunmaz;
    karar tablosundan yalnız yayın kararının zamanı (`created_at`) okunur, not sütunu okunmaz."""
    with closing(_salt_okunur(community_db)) as baglanti:
        tablolar = _tablolar(baglanti)
        if not {"community_entries", "community_revisions", "community_public_revisions"} <= tablolar:
            raise DokumHatasi("Dosya topluluk veritabanı değil.")
        rows = baglanti.execute(
            "SELECT e.id,p.version,r.kind,r.packet,r.created_at FROM community_public_revisions p "
            "JOIN community_entries e ON e.id=p.entry_id AND e.published_revision IS NOT NULL "
            "JOIN community_revisions r ON r.entry_id=p.entry_id AND r.version=p.version "
            "WHERE p.visible=1 ORDER BY e.id,p.version").fetchall()
        kararlar = {}
        if "community_decisions" in tablolar:
            for karar in baglanti.execute("SELECT entry_id,revision,MIN(created_at) AS at FROM community_decisions "
                                          "WHERE decision='publish' GROUP BY entry_id,revision"):
                kararlar[(karar["entry_id"], karar["revision"])] = karar["at"]
    sonuc = []
    for row in rows:
        paket = json.loads(row["packet"])
        v1_paketini_dogrula(paket)
        yayin = kararlar.get((row["id"], row["version"]))
        sonuc.append({"schema": "devtrellis-share-public", "kind": row["kind"], "id": row["id"], "version": row["version"],
                      "submitted_at": zaman_metni(row["created_at"]),
                      "published_at": zaman_metni(yayin) if yayin is not None else None,
                      "link": rapor_baglantisi(public_url, row["id"], row["version"]), "packet": paket})
    return sonuc


def v4_kayitlari(k1_db):
    """Görünür v4 kayıtları ve asgari kaldırma izleri. `v4_submissions` ve `v4_events` (aday metni, notlar) hiç okunmaz."""
    with closing(_salt_okunur(k1_db)) as baglanti:
        if "v4_records" not in _tablolar(baglanti):
            raise DokumHatasi("Dosya K1 v4 deposu değil.")
        rows = baglanti.execute("SELECT id,version,record,visible,removal,removed_at,published_at FROM v4_records "
                                "ORDER BY id,version").fetchall()
    kayitlar, izler = [], []
    for row in rows:
        if row["visible"] == 1:
            kayit = json.loads(row["record"])
            v4_kaydini_dogrula(kayit)
            kayitlar.append(kayit)
        elif row["removal"] in ("withdrawn", "removed") and row["removed_at"] is not None:
            # İnen kaydın gövdesinden yalnız sözleşme adı alınır, başka hiçbir alan dışarı yazılmaz
            # (saklama süresi dolunca gövde '{}' olur, ad null kalır).
            try:
                sozlesme = json.loads(row["record"]).get("contract")
            except (ValueError, AttributeError):
                sozlesme = None
            izler.append({"schema": "devtrellis-record-tombstone", "schema_version": 4,
                          "contract": sozlesme if isinstance(sozlesme, str) else None, "id": row["id"],
                          "version": row["version"], "state": row["removal"], "removed_at": zaman_metni(row["removed_at"])})
    return kayitlar, izler


def dokum_olustur(*, community_db, public_url, k1_db=None, now=None):
    now = int(now if now is not None else time.time())
    public_url = public_url.rstrip("/")
    if not public_url.startswith("https://") and not public_url.startswith("http://localhost") \
            and not public_url.startswith("http://127.0.0.1"):
        raise DokumHatasi("Kamu adresi https:// ile başlamalı.")
    v1 = v1_kayitlari(community_db, public_url)
    v4, izler = v4_kayitlari(k1_db) if k1_db is not None else ([], [])
    dokum = {"schema": SEMA, "schema_version": SEMA_SURUMU, "generated_at": zaman_metni(now), "generator": URETICI,
             "site": {"public_url": public_url, "sitemap": public_url + "/sitemap.xml", "llms_txt": public_url + "/llms.txt",
                      "feed": public_url + "/feed.atom", "note": "v1 reports are listed in sitemap.xml and llms.txt; "
                      "v4 records are reachable through each record's link."},
             "license": LISANS_NOTU,
             "excluded": ["private candidate text", "objection private text", "moderator notes", "identity and IP buckets",
                          "session and management keys", "password hashes", "engagement counters"],
             "counts": {"v1_records": len(v1), "v4_records": len(v4), "v4_tombstones": len(izler)},
             "records": {"v1": v1, "v4": v4}, "tombstones": {"v4": izler}}
    dokumu_dogrula(dokum)
    return dokum


def dokumu_dogrula(dokum):
    """Şema, kapalı alan kümeleri ve sayılar; geri okumadan önce de çağrılır."""
    if (not isinstance(dokum, dict) or dokum.get("schema") != SEMA or dokum.get("schema_version") != SEMA_SURUMU
            or set(dokum) != {"schema", "schema_version", "generated_at", "generator", "site", "license", "excluded",
                              "counts", "records", "tombstones"}):
        raise DokumHatasi("Döküm şeması tanınmıyor.")
    kayitlar, izler = dokum["records"], dokum["tombstones"]
    if set(kayitlar) != {"v1", "v4"} or set(izler) != {"v4"}:
        raise DokumHatasi("Döküm kayıt grupları eksik ya da fazla.")
    for kayit in kayitlar["v1"]:
        if (not isinstance(kayit, dict) or set(kayit) != V1_KAYIT_ALANLARI or kayit["schema"] != "devtrellis-share-public"
                or type(kayit["version"]) is not int):
            raise DokumHatasi("v1 kaydı döküm biçimine uymuyor.")
        v1_paketini_dogrula(kayit["packet"])
    for kayit in kayitlar["v4"]:
        v4_kaydini_dogrula(kayit)
        if type(kayit.get("version")) is not int or not isinstance(kayit.get("id"), str):
            raise DokumHatasi("v4 kaydı döküm biçimine uymuyor.")
    for iz in izler["v4"]:
        if not isinstance(iz, dict) or set(iz) != V4_IZ_ALANLARI or iz["state"] not in ("withdrawn", "removed"):
            raise DokumHatasi("v4 kaldırma izi döküm biçimine uymuyor.")
    sayilar = {"v1_records": len(kayitlar["v1"]), "v4_records": len(kayitlar["v4"]), "v4_tombstones": len(izler["v4"])}
    if dokum["counts"] != sayilar:
        raise DokumHatasi("Döküm sayıları kayıtlarla uyuşmuyor.")
    if dokum["license"] != LISANS_NOTU:
        raise DokumHatasi("Döküm lisans notu beklenen metin değil.")
    return sayilar


def dokum_metni(dokum):
    return json.dumps(dokum, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def dokum_yaz(dokum, target_dir):
    """Tarihli yeni dosya ve manifest; var olan dosyanın üzerine yazmaz."""
    hedef = Path(target_dir)
    if hedef.exists() and not hedef.is_dir():
        raise DokumHatasi("Hedef bir dizin olmalı.")
    hedef.mkdir(parents=True, exist_ok=True)
    gun = dokum["generated_at"][:10].replace("-", "")
    metin = dokum_metni(dokum).encode("utf-8")
    ad = f"{DOSYA_ONEKI}{gun}.json"
    dosya = hedef / ad
    manifest_dosyasi = hedef / f"{DOSYA_ONEKI}{gun}.manifest.json"
    if dosya.exists() or manifest_dosyasi.exists():
        raise DokumHatasi("Bugünün dökümü zaten var; üzerine yazılmaz.")
    manifest = {"schema": SEMA + "-manifest", "schema_version": SEMA_SURUMU, "file": ad, "bytes": len(metin),
                "sha256": hashlib.sha256(metin).hexdigest(), "generated_at": dokum["generated_at"],
                "counts": dokum["counts"], "license": LISANS_NOTU["dump_structure"]}
    with dosya.open("xb") as stream:
        stream.write(metin)
    with manifest_dosyasi.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    return {"file": dosya, "manifest": manifest_dosyasi, "sha256": manifest["sha256"], "bytes": len(metin)}


def dokumu_oku(path):
    path = Path(path)
    if not path.is_file():
        raise DokumHatasi("Döküm dosyası bulunamadı.")
    if path.stat().st_size > MAX_DUMP_BYTES:
        raise DokumHatasi("Döküm dosyası boyut sınırını aşıyor.")
    with path.open("rb") as stream:
        ham = stream.read()
    try:
        dokum = json.loads(ham.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DokumHatasi("Döküm geçerli UTF-8 JSON değil.") from exc
    manifest = path.with_name(path.name[:-5] + ".manifest.json") if path.name.endswith(".json") else None
    if manifest is None or not manifest.is_file():
        raise DokumHatasi("Dökümün manifesti yanında yok; bütünlük denetimi yapılamadan döküm kabul edilmez.")
    try:
        beyan = json.loads(manifest.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DokumHatasi("Döküm manifesti geçerli JSON değil.") from exc
    if (not isinstance(beyan, dict) or beyan.get("schema") != SEMA + "-manifest" or beyan.get("file") != path.name
            or beyan.get("bytes") != len(ham) or beyan.get("sha256") != hashlib.sha256(ham).hexdigest()):
        raise DokumHatasi("Döküm özeti manifestle uyuşmuyor.")
    sayilar = dokumu_dogrula(dokum)
    return dokum, sayilar


def dokumu_yukle(path, db_path):
    """Dökümü yeni, boş bir salt okunur kütüphane deposuna açar; kayıt sayısını döndürür."""
    db_path = Path(db_path)
    if db_path.exists():
        raise DokumHatasi("Hedef veritabanı yeni bir dosya olmalı; mevcut dosyanın üzerine yazılmaz.")
    dokum, sayilar = dokumu_oku(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with db_path.open("xb"):
        pass
    with closing(sqlite3.connect(db_path)) as baglanti:
        baglanti.executescript("""
            CREATE TABLE public_dump_meta (name TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE public_records (
                schema TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL, version INTEGER NOT NULL,
                published_at TEXT, link TEXT, record TEXT NOT NULL, PRIMARY KEY(schema,id,version)
            );
            CREATE TABLE public_tombstones (
                id TEXT NOT NULL, version INTEGER NOT NULL, state TEXT NOT NULL, removed_at TEXT NOT NULL,
                PRIMARY KEY(id,version)
            );
        """)
        for kayit in dokum["records"]["v1"]:
            baglanti.execute("INSERT INTO public_records VALUES(?,?,?,?,?,?,?)",
                             (kayit["schema"], kayit["kind"], kayit["id"], kayit["version"],
                              kayit["published_at"] or kayit["submitted_at"],
                              kayit["link"], json.dumps(kayit, ensure_ascii=False, sort_keys=True)))
        for kayit in dokum["records"]["v4"]:
            baglanti.execute("INSERT INTO public_records VALUES(?,?,?,?,?,?,?)",
                             (kayit["schema"], kayit["kind"], kayit["id"], kayit["version"],
                              (kayit.get("publication") or {}).get("published_at"), kayit.get("link"),
                              json.dumps(kayit, ensure_ascii=False, sort_keys=True)))
        for iz in dokum["tombstones"]["v4"]:
            baglanti.execute("INSERT INTO public_tombstones VALUES(?,?,?,?)", (iz["id"], iz["version"], iz["state"], iz["removed_at"]))
        for ad, deger in (("schema", SEMA), ("schema_version", str(SEMA_SURUMU)), ("generated_at", dokum["generated_at"]),
                          ("source_file", Path(path).name), ("license", json.dumps(LISANS_NOTU, ensure_ascii=False)),
                          ("public_url", dokum["site"]["public_url"])):
            baglanti.execute("INSERT INTO public_dump_meta VALUES(?,?)", (ad, deger))
        baglanti.commit()
        toplam = baglanti.execute("SELECT COUNT(*) FROM public_records").fetchone()[0]
        izler = baglanti.execute("SELECT COUNT(*) FROM public_tombstones").fetchone()[0]
    if toplam != sayilar["v1_records"] + sayilar["v4_records"] or izler != sayilar["v4_tombstones"]:
        raise DokumHatasi("Geri okunan kayıt sayısı dökümle uyuşmuyor.")
    return {**sayilar, "records_loaded": toplam, "db": str(db_path)}


def git_kaydet(target_dir, dosyalar, mesaj):
    """İsteğe bağlı: hedef, yalnız dökümler için açılmış bir deponun KÖKÜ ise döküm dosyalarını yerel commit'ler; itme yok.

    Başka bir deponun alt dizini (örneğin uygulama deposu içindeki `dumps/`) reddedilir."""
    hedef = Path(target_dir)
    kontrol = subprocess.run(["git", "-C", str(hedef), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if kontrol.returncode != 0 or not kontrol.stdout.strip():
        raise DokumHatasi("Hedef dizin bir git çalışma ağacı değil; --git-commit için önce dökümlere ayrılmış depo açın.")
    kok = Path(kontrol.stdout.strip()).resolve()
    if os.path.normcase(str(kok)) != os.path.normcase(str(hedef.resolve())):
        raise DokumHatasi("Hedef dizin başka bir deponun alt dizini; --git-commit yalnız dökümlere ayrılmış deponun kökünde çalışır.")
    goreli = [str(Path(d).resolve().relative_to(hedef.resolve())) for d in dosyalar]
    subprocess.run(["git", "-C", str(hedef), "add", "--", *goreli], check=True, capture_output=True)
    commit = subprocess.run(["git", "-C", str(hedef), "-c", "user.name=DevTrellis public dump",
                             "-c", "user.email=noreply@devtrellis.org", "commit", "-q", "-m", mesaj, "--", *goreli],
                            capture_output=True, text=True)
    if commit.returncode != 0:
        raise DokumHatasi("Git commit başarısız oldu; döküm dosyaları yerinde duruyor.")
    return subprocess.run(["git", "-C", str(hedef), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()


def gece_dokumu(*, community_db, public_url, target_dir, k1_db=None, now=None, git_commit=False):
    dokum = dokum_olustur(community_db=community_db, public_url=public_url, k1_db=k1_db, now=now)
    yazilan = dokum_yaz(dokum, target_dir)
    sonuc = {"schema": SEMA, "schema_version": SEMA_SURUMU, "file": str(yazilan["file"]), "manifest": str(yazilan["manifest"]),
             "sha256": yazilan["sha256"], "bytes": yazilan["bytes"], "counts": dokum["counts"], "model_calls": 0}
    if git_commit:
        sonuc["commit"] = git_kaydet(target_dir, [yazilan["file"], yazilan["manifest"]],
                                     f"Public dump {dokum['generated_at'][:10]}: {dokum['counts']['v1_records']} v1, "
                                     f"{dokum['counts']['v4_records']} v4 records")
    return sonuc
