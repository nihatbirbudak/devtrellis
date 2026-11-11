"""alpha.15 (dilim 14a) geri dönüş aracı: uzantıyla kurulmuş v4 kayıtlarını alpha.14 ve öncesi kodun okuyabileceği biçime çevirir.

Neden gerekli: eski kodun kamu dökümü kayıtta bilinmeyen üst düzey alanda (`lineage`) durur. Uzantı yolu yalnız sunucu ayarı
`extensions: true` iken çalışır; ayar hiç açılmadıysa depoda `lineage` yoktur ve bu araç bir şey değiştirmez. Araç sunucu ve
işçi dururken çalışır:

- `lineage` alanı kayıtlardan (güncel ve yerini bırakmış sürümler) silinir; iddialar, içerik ve kapı alanları aynen kalır;
- yan tablo `v4_extensions` silinmez (eski kod görmezden gelir; yeniden yükseltmede gerekir);
- güncel sürümün üstünde kalan geri çekilmiş ya da kaldırılmış uç sürüm satırları (uzantı katkıcısının geri çekmesi, moderatörün
  son sürümü kaldırması) yedeğe alınıp depodan çıkarılır: eski kod kimliksiz okumada en büyük sürüm numaralı satırı verdiği için
  bu satır kalırsa kaydın adresi, güncel sürüm yayındayken 410 döner. Eski kodda o sürümün adresi 410 yerine 404 olur;
  `--geri-yukle` satırı aynen geri yazar;
- sırada ya da moderasyonda bekleyen uzantı gönderimi varken `--yaz` çalışmaz: eski işçi bu gönderimi reddeder, eski moderatör
  yayımı ise onu üst kayda bağlamadan ayrı ve bozuk bir kayıt olarak yazar. Önce yeni sürümde işlenmeleri ya da karara
  bağlanmaları gerekir.

Silinen bilgi (sürüm zinciri, değişiklik özeti, sürüm başına köken) yedek dosyasında aynen durur; yeniden yükseltmeden sonra
`--geri-yukle <yedek>` kaydı, bu arada değişmediyse, önceki hâline döndürür.

Eski sürümde geçen sürede yapılan geri çekme ve kaldırmalar yeniden yükseltmede bu adımla uzlaştırılır (düzeltme turu 1, kabul
Ö5a): eski kod bütün kaydı indirirken yalnız görünür satırı işaretler; yerini bırakmış sürümler `removal` taşımadan kalır ve yeni
kod onları sürüm adresinde yeniden okutur. `--geri-yukle`, görünür sürümü kalmamış kayıtta `removal` taşımayan satırlara kaydın
indiği andaki `removal`/`removed_at` değerini yazar (kuru çalışmada listeler).

Okuma eki aracıyla (`okuma-eki-geri-al.py`) kesin sıra: geri almada önce bu araç, sonra okuma eki aracı; geri yüklemede ters sıra
(önce okuma eki, sonra bu araç). Yanlış sırada geri yükleme kısmi kalır ("dokunulmadı" satırları); okuma eki geri yüklemesi yeniden
çalıştırılınca tamamlanır, veri kaybolmaz. Yedek dosyası park edilen uç sürümlerin tam gövdesini taşır: geri yüklemeden sonra silin. Varsayılan kuru çalışmadır (yalnız yazdırır). Var olan
yedek dosyasının üstüne yazılmaz. Yedekte yalnız yayımlanmış kamu kayıtlarının gövdesi vardır (özel aday metni, anahtar yok).

Eski kodla bilinen farklar (geri dönüşten sonra): yerini bırakmış sürümün adresi 410 ve `state: null` döner; eski kodun temizliği
yerini bırakmış sürümlerin gövdesini saklama süresi (varsayılan 30 gün) dolunca siler; güncel sürüm bütün iddiaları taşıdığı için
kaybolan yalnız eski sürümlerin `not_covered` ve etiket metnidir. Uzantı katkıcısının geri çekmesi eski kodda kaydı değiştirmez.

Kullanım (sunucuda, Kullanıcı'ın sözüyle; önce sunucu ve işçi durur):
  python araclar/uzanti-geri-al.py --db <v4 deposu .sqlite3>                               # kuru
  python araclar/uzanti-geri-al.py --db <v4 deposu .sqlite3> --yaz --yedek <yeni yedek.json>
  python araclar/uzanti-geri-al.py --db <v4 deposu .sqlite3> --geri-yukle <yedek.json> [--yaz]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

YEDEK_SEMASI = "devtrellis-uzanti-yedek"
UZANTI_TURU = "record_extension"


def eski_bicim(kayit):
    """Kaydın `lineage`'sız biçimi; ek yoksa None (değişiklik gerekmez)."""
    if not isinstance(kayit, dict) or "lineage" not in kayit:
        return None
    return {alan: deger for alan, deger in kayit.items() if alan != "lineage"}


def bekleyen_uzantilar(baglanti):
    """Sırada ya da moderasyonda bekleyen uzantı gönderimlerinin (kimlik, durum) listesi; aday metni yazdırılmaz."""
    return [tuple(r) for r in baglanti.execute(
        "SELECT id, status FROM v4_submissions WHERE kind=? AND status IN ('queued','retry_wait','pending_review') "
        "ORDER BY created_at", (UZANTI_TURU,)).fetchall()]


KAYIT_SUTUNLARI = ("id", "version", "kind", "record", "path", "visible", "published_at", "removed_at", "removal", "address_key")


def ust_uclar(baglanti):
    """Güncel (görünür) sürümün üstünde kalan, inmiş uç sürüm satırları: [(satır sözlüğü, [adres satırları])]."""
    satirlar = baglanti.execute(
        "SELECT " + ",".join(KAYIT_SUTUNLARI) + " FROM v4_records r WHERE r.removal IS NOT NULL AND EXISTS "
        "(SELECT 1 FROM v4_records g WHERE g.id=r.id AND g.visible=1 AND g.version<r.version) ORDER BY id, version").fetchall()
    sonuc = []
    for satir in satirlar:
        kayit = dict(zip(KAYIT_SUTUNLARI, satir))
        adresler = [list(a) for a in baglanti.execute("SELECT address_key, position FROM v4_record_addresses WHERE id=? AND "
                                                      "version=? ORDER BY position", (kayit["id"], kayit["version"]))]
        sonuc.append((kayit, adresler))
    return sonuc


def uzlastirilacaklar(baglanti):
    """Görünür sürümü kalmamış (eski kodda bütünüyle inmiş) kayıtlarda `removal` taşımayan satırlar: [(id, sürüm, removal,
    removed_at)]. Değer kaydın en son inen satırından alınır."""
    sonuc = []
    for (kimlik,) in baglanti.execute(
            "SELECT DISTINCT id FROM v4_records r WHERE r.removal IS NULL AND NOT EXISTS "
            "(SELECT 1 FROM v4_records g WHERE g.id=r.id AND g.visible=1) AND EXISTS "
            "(SELECT 1 FROM v4_records g WHERE g.id=r.id AND g.removal IS NOT NULL) ORDER BY id").fetchall():
        removal, removed_at = baglanti.execute(
            "SELECT removal, removed_at FROM v4_records WHERE id=? AND removal IS NOT NULL "
            "ORDER BY removed_at DESC, version DESC LIMIT 1", (kimlik,)).fetchone()
        sonuc += [(kimlik, surum, removal, removed_at) for (surum,) in baglanti.execute(
            "SELECT version FROM v4_records WHERE id=? AND removal IS NULL ORDER BY version", (kimlik,)).fetchall()]
    return sonuc


def calistir(db, *, yaz=False, yedek=None, geri_yukle=None, cikti=print):
    """Değişen (ya da kuru çalışmada değişecek) kayıt ve satır sayısı."""
    with closing(sqlite3.connect(db)) as baglanti:
        satirlar = baglanti.execute("SELECT id, version, record FROM v4_records WHERE record<>'{}' "
                                    "ORDER BY published_at, version").fetchall()
        bekleyenler = [] if geri_yukle is not None else bekleyen_uzantilar(baglanti)
        uclar = [] if geri_yukle is not None else ust_uclar(baglanti)
        uzlasma = uzlastirilacaklar(baglanti) if geri_yukle is not None else []
        var_olan = {(r[0], r[1]) for r in baglanti.execute("SELECT id, version FROM v4_records")}
    for kimlik, durum in bekleyenler:
        cikti(f"UYARI {kimlik}: '{durum}' durumunda uzantı gönderimi bekliyor; eski sürüme dönmeden önce yeni sürümde "
              "işlenmeli ya da karara bağlanmalı")
    simdiki = {(kimlik, surum): metin for kimlik, surum, metin in satirlar}
    yazilacak = []    # (kimlik, sürüm, şimdiki metin, yeni metin)
    geri_konacak = []  # yedekten geri yazılacak uç sürüm satırları
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
        for uc in veri.get("parked") or []:
            anahtar = (uc["row"]["id"], uc["row"]["version"])
            if anahtar in var_olan:
                cikti(f"{anahtar[0]}@{anahtar[1]}: uç sürüm satırı zaten var; dokunulmadı")
            else:
                geri_konacak.append(uc)
                cikti(f"{anahtar[0]}@{anahtar[1]}: inmiş uç sürüm satırı geri yazılacak")
        for kimlik, surum, removal, _ in uzlasma:
            cikti(f"{kimlik}@{surum}: kayıt eski sürümde bütünüyle inmiş ({removal}); bu sürüm de inmiş olarak işaretlenecek "
                  "(yeni kod onu sürüm adresinde okutmasın)")
    else:
        cikacak = {(kayit["id"], kayit["version"]) for kayit, _ in uclar}
        for kimlik, surum, metin in satirlar:
            if (kimlik, surum) in cikacak:
                continue          # depodan çıkacak uç sürüm; yedekte özgün hâliyle durur
            try:
                yeni = eski_bicim(json.loads(metin))
            except ValueError:
                continue
            if yeni is not None:
                yazilacak.append((kimlik, surum, metin, json.dumps(yeni, ensure_ascii=False, sort_keys=True)))
                cikti(f"{kimlik}@{surum}: lineage silinecek")
        for kayit, _ in uclar:
            cikti(f"{kayit['id']}@{kayit['version']}: güncel sürümün üstünde inmiş ({kayit['removal']}) uç sürüm; yedeğe "
                  "alınıp depodan çıkarılacak (eski kod bu satırı kaydın güncel adresinde gösterirdi)")
    toplam = len(yazilacak) + len(uclar) + len(geri_konacak) + len(uzlasma)
    if not yaz:
        cikti(f"{toplam} kayıt ya da satır değişecek (kuru çalışma; yazmak için --yaz)")
        return toplam
    if bekleyenler:
        raise SystemExit(f"{len(bekleyenler)} uzantı gönderimi sırada ya da moderasyonda; önce yeni sürümde işlenmeli ya da "
                         "karara bağlanmalı. Hiçbir şey yazılmadı.")
    if (yazilacak or uclar) and geri_yukle is None:
        if yedek is None:
            raise SystemExit("--yaz ile --yedek <dosya> gerekir (sürüm zinciri ve uç sürüm satırları yalnız yedekte kalır).")
        try:
            with open(yedek, "x", encoding="utf-8") as dosya:
                dosya.write(json.dumps({"schema": YEDEK_SEMASI, "version": 1, "rows": [
                    {"id": k, "version": s, "before": once, "after": sonra} for k, s, once, sonra in yazilacak],
                    "parked": [{"row": kayit, "addresses": adresler} for kayit, adresler in uclar]},
                    ensure_ascii=False, indent=1))
        except FileExistsError:
            raise SystemExit(f"Yedek dosyası zaten var, üstüne yazılmaz: {Path(yedek).name}. Yeni bir dosya adı verin.") from None
    degisen = 0
    with closing(sqlite3.connect(db, timeout=10)) as baglanti:
        with baglanti:
            for kimlik, surum, once, sonra in yazilacak:
                degisen += baglanti.execute("UPDATE v4_records SET record=? WHERE id=? AND version=? AND record=?",
                                            (sonra, kimlik, surum, once)).rowcount
            for kayit, _ in uclar:
                baglanti.execute("DELETE FROM v4_record_addresses WHERE id=? AND version=?", (kayit["id"], kayit["version"]))
                degisen += baglanti.execute("DELETE FROM v4_records WHERE id=? AND version=? AND removal IS NOT NULL",
                                            (kayit["id"], kayit["version"])).rowcount
            for uc in geri_konacak:
                satir = uc["row"]
                baglanti.execute("INSERT INTO v4_records(" + ",".join(KAYIT_SUTUNLARI) + ") VALUES(" +
                                 ",".join("?" * len(KAYIT_SUTUNLARI)) + ")", tuple(satir[s] for s in KAYIT_SUTUNLARI))
                baglanti.executemany("INSERT OR IGNORE INTO v4_record_addresses(id,version,address_key,position) VALUES(?,?,?,?)",
                                     [(satir["id"], satir["version"], a, p) for a, p in uc["addresses"]])
                degisen += 1
            for kimlik, surum, removal, removed_at in uzlasma:
                degisen += baglanti.execute("UPDATE v4_records SET visible=0,removal=?,removed_at=? WHERE id=? AND version=? "
                                            "AND removal IS NULL", (removal, removed_at, kimlik, surum)).rowcount
    cikti(f"{degisen} kayıt ya da satır güncellendi")
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
    sys.exit(main())
