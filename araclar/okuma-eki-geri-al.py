"""alpha.15 (14r2) geri dönüş aracı: okuma eki taşıyan v4 kayıtlarını alpha.14 ve öncesi kodun okuyabileceği biçime çevirir.

Neden gerekli: eski kod (alpha.14 ve öncesi) `content.answers` maddesini metin sayar (arama dizini kurulurken nesne madde hata
verir) ve kamu dökümü bilinmeyen alanda (`not_in_sources`, `not_researched`, `publication.gate.source_snapshots`,
`publication.gate.absence_checks`) durur. Bu araç eski sürüme dönmeden önce, sunucu ve işçi dururken çalışır:

- cevap bağı nesnesi → yalnız soru metni (`{"q": …, "claims": […]}` → `"…"`);
- `not_in_sources` maddesi → `not_covered` sonuna "Not stated in the cited pages (S1, S3): …";
- `not_researched` maddesi → `not_covered` sonuna "Not researched: …";
- `publication.gate.source_snapshots` ve `absence_checks` silinir.

Kayıp bilgi (bağ numaraları, kaynak numaraları, anlık özetleri) yedek dosyasında aynen durur; yeniden yükseltmeden sonra
`--geri-yukle <yedek>` kaydı, bu arada değişmediyse, önceki hâline döndürür. Varsayılan kuru çalışmadır (yalnız yazdırır).
Yedekte yalnız yayımlanmış kamu kayıtlarının gövdesi vardır (özel aday metni, anahtar, kimlik yok).

Kullanım (sunucuda, Kullanıcı'ın sözüyle; önce sunucu ve işçi durur):
  python araclar/okuma-eki-geri-al.py --db <v4 deposu .sqlite3>                               # kuru
  python araclar/okuma-eki-geri-al.py --db <v4 deposu .sqlite3> --yaz --yedek <yedek.json>
  python araclar/okuma-eki-geri-al.py --db <v4 deposu .sqlite3> --geri-yukle <yedek.json> [--yaz]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from radar import okuma_eki  # noqa: E402
from radar.katki_v4 import _adres_anahtari  # noqa: E402

YEDEK_SEMASI = "devtrellis-okuma-eki-yedek"
KAPI_EKLERI = okuma_eki.KAPI_EK_ALANLARI


def eski_bicim(kayit):
    """Kaydın alpha.14 biçimi; okuma eki yoksa None (değişiklik gerekmez)."""
    icerik = kayit.get("content") or {}
    cevaplar = icerik.get("answers") if isinstance(icerik.get("answers"), list) else []
    if not okuma_eki.okuma_ekli_kayit_mi(kayit):     # yetenek listesiyle aynı tanım (düzeltme turu 2)
        return None
    yeni = json.loads(json.dumps(kayit))
    icerik, kapi = yeni.get("content") or {}, (yeni.get("publication") or {}).get("gate") or {}
    if cevaplar:
        icerik["answers"] = [soru for soru in map(okuma_eki.cevap_sorusu, cevaplar) if soru]
    sinirlar = list(icerik.get("not_covered") or [])
    iddialar = yeni.get("claims") or []
    # Düzeltme turu 1 (yönetici kararı 2): aranan sayfalar sunucunun yayımda çözdüğü adres anahtarlarından adres olarak
    # yazılır (numara kayabilir); anahtar yoksa katkıcının numarası o anki sıraya göre çözülür.
    adres_of = {(i.get("evidence") or {}).get("address_key"): (i.get("evidence") or {}).get("source_url") for i in iddialar}
    for kaynak in icerik.get("sources") or []:
        if isinstance(kaynak, dict) and isinstance(kaynak.get("url"), str):
            adres_of.setdefault(_adres_anahtari(kaynak["url"]), kaynak["url"])
    satirlar = {s.get("item"): s for s in kapi.get("absence_checks") or [] if isinstance(s, dict)}
    sira = okuma_eki.kaynak_sirasi(icerik.get("sources"), [(i.get("evidence") or {}).get("source_url") for i in iddialar])
    for konum, madde in enumerate(icerik.pop("not_in_sources", None) or []):
        if isinstance(madde, dict) and isinstance(madde.get("item"), str):
            anahtarlar = (satirlar.get(konum) or {}).get("checked_keys")
            if isinstance(anahtarlar, list):
                adresler = [adres_of[k] for k in anahtarlar if adres_of.get(k)]
            else:
                adresler = [sira[n - 1] for n in madde.get("checked") or [] if type(n) is int and 0 < n <= len(sira)]
            sinirlar.append(f"Not stated in the cited pages ({', '.join(adresler) or 'cited pages'}): {madde['item']}")
    for madde in icerik.pop("not_researched", None) or []:
        if isinstance(madde, str):
            sinirlar.append(f"Not researched: {madde}")
    if sinirlar:
        icerik["not_covered"] = sinirlar
    for ad in KAPI_EKLERI:
        kapi.pop(ad, None)
    return yeni


okuma_ekli_aday_mi = okuma_eki.okuma_ekli_aday_mi


def bekleyen_okuma_ekli(baglanti):
    """Sırada ya da moderasyonda bekleyen, okuma eki taşıyan gönderimlerin (kimlik, durum) listesi; aday metni yazdırılmaz."""
    satirlar = baglanti.execute("SELECT id, status, candidate FROM v4_submissions "
                                "WHERE status IN ('queued','retry_wait','pending_review') AND candidate_purged=0").fetchall()
    sonuc = []
    for kimlik, durum, metin in satirlar:
        try:
            if okuma_ekli_aday_mi(json.loads(metin)):
                sonuc.append((kimlik, durum))
        except ValueError:
            continue
    return sonuc


def calistir(db, *, yaz=False, yedek=None, geri_yukle=None, cikti=print):
    """Değişen (ya da kuru çalışmada değişecek) kayıt sayısı."""
    with closing(sqlite3.connect(db)) as baglanti:
        satirlar = baglanti.execute("SELECT id, version, record FROM v4_records WHERE record<>'{}' ORDER BY published_at").fetchall()
        bekleyenler = [] if geri_yukle is not None else bekleyen_okuma_ekli(baglanti)
    for kimlik, durum in bekleyenler:
        # Bu araç yalnız yayımlanmış kayıtları çevirir. Eski kod bu gönderimi yayımlarsa yeni alanları düşürür (ya da bağlı
        # cevapta arama dizini hata verir): geri dönmeden önce moderatör karara bağlamalı ya da katkıcı geri çekmeli.
        cikti(f"UYARI {kimlik}: '{durum}' durumunda okuma ekli gönderim bekliyor; eski sürüme dönmeden önce karara bağlayın")
    simdiki = {(kimlik, surum): metin for kimlik, surum, metin in satirlar}
    yazilacak = []    # (kimlik, sürüm, şimdiki metin, yeni metin)
    if geri_yukle is not None:
        veri = json.loads(Path(geri_yukle).read_text(encoding="utf-8"))
        if veri.get("schema") != YEDEK_SEMASI or veri.get("version") != 1:
            raise SystemExit("Yedek dosyası bu aracın yedeği değil.")
        for satir in veri["rows"]:
            anahtar = (satir["id"], satir["version"])
            if simdiki.get(anahtar) == satir["after"]:
                yazilacak.append((*anahtar, satir["after"], satir["before"]))
                cikti(f"{anahtar[0]}@{anahtar[1]}: önceki hâline dönecek")
            else:
                cikti(f"{anahtar[0]}@{anahtar[1]}: geri almadan sonra değişmiş ya da yok; dokunulmadı")
    else:
        for kimlik, surum, metin in satirlar:
            yeni = eski_bicim(json.loads(metin))
            if yeni is not None:
                yazilacak.append((kimlik, surum, metin, json.dumps(yeni, ensure_ascii=False, sort_keys=True)))
                cikti(f"{kimlik}@{surum}: okuma eki eski biçime çevrilecek")
    if not yaz:
        cikti(f"{len(yazilacak)} kayıt değişecek (kuru çalışma; yazmak için --yaz)")
        return len(yazilacak)
    if yazilacak and geri_yukle is None:
        if yedek is None:
            raise SystemExit("--yaz ile --yedek <dosya> gerekir (bağ ve anlık bilgisi yalnız yedekte kalır).")
        # Düzeltme turu 1 (kabul Ö3): var olan yedeğin üstüne yazılmaz (önceki geri dönüşün özgün kayıtları kaybolurdu).
        try:
            with open(yedek, "x", encoding="utf-8") as dosya:
                dosya.write(json.dumps({"schema": YEDEK_SEMASI, "version": 1, "rows": [
                    {"id": k, "version": s, "before": once, "after": sonra} for k, s, once, sonra in yazilacak]},
                    ensure_ascii=False, indent=1))
        except FileExistsError:
            raise SystemExit(f"Yedek dosyası zaten var, üstüne yazılmaz: {Path(yedek).name}. Yeni bir dosya adı verin "
                             "(eski yedek önceki geri dönüşün özgün kayıtlarını taşıyor olabilir).") from None
    degisen = 0
    with closing(sqlite3.connect(db, timeout=10)) as baglanti:
        with baglanti:
            for kimlik, surum, once, sonra in yazilacak:
                degisen += baglanti.execute("UPDATE v4_records SET record=? WHERE id=? AND version=? AND record=?",
                                            (sonra, kimlik, surum, once)).rowcount
    cikti(f"{degisen} kayıt güncellendi")
    return degisen


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--yaz", action="store_true")
    ap.add_argument("--yedek", type=Path)
    ap.add_argument("--geri-yukle", type=Path)
    a = ap.parse_args(argv)
    calistir(a.db, yaz=a.yaz, yedek=a.yedek, geri_yukle=a.geri_yukle)


if __name__ == "__main__":
    main()
