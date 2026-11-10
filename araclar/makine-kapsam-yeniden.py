"""alpha.13b dağıtım aracı: yayımlı v4 kayıtlarına makineyle kapsam listesini (`publication.gate.machine_not_covered`) bir kez yazar.

Eki taşımayan görünür kayıtların kaynak sayfaları kapının kibar getiricisiyle (robots.txt, hız sınırı) yeniden getirilir,
`radar.denetim.kapsam` alıntılarla karşılaştırır. Varsayılan kuru çalışmadır (yalnız yazdırır); `--yaz` kaydı günceller.
Önce bütün ekler hesaplanır (depoda açık işlem yokken), sonra tek kısa işlemde yazılır: getirme sürerken sunucu yazabilir
(alpha.13 şartlı kabulü, bulgu 1). Alıntılarından biri bugünkü sayfada bulunamayan kayıt atlanır (sayfa değişmiş olabilir;
bulgu 6). Geri dönüş: eski kod kamu dökümünde bilinmeyen alanı reddeder; eski sürüme dönmeden önce `--geri-al --yaz` eki siler.

Kullanım (sunucuda, Kullanıcı'ın sözüyle):
  python araclar/makine-kapsam-yeniden.py --db <v4 deposu .sqlite3> [--yaz]
  python araclar/makine-kapsam-yeniden.py --db <v4 deposu .sqlite3> --geri-al [--yaz]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from radar.denetim import getirme, kapsam  # noqa: E402
from radar.denetim.ayarlar import Ayarlar  # noqa: E402
from radar.denetim.metin import gorunur_metin, normallestir  # noqa: E402
from radar.katki_v4 import makine_kapsami  # noqa: E402

ALAN = "machine_not_covered"


def paket(kayit):
    """Kayıttan kapının gördüğü biçim: tür ve iddiaların metni, alıntısı, adresi."""
    return {"kind": kayit["kind"], "claims": [{"text": i["text"], "quote": i["evidence"]["quote"],
                                               "source_url": i["evidence"]["source_url"]} for i in kayit["claims"]]}


def hesapla(kayit, getirici):
    """(ek, neden): ek boşsa neden atlandığını söyler. Ağ işi burada; depoya dokunmaz."""
    p = paket(kayit)
    kaynaklar = list(dict.fromkeys(i["source_url"] for i in p["claims"]))
    sonuc = getirme.denetle(kaynaklar, getirici=getirici)
    sonuclar = sonuc[1] if isinstance(sonuc, tuple) else {}
    for iddia in p["claims"]:
        kaynak = sonuclar.get(iddia["source_url"])
        if kaynak is None or getattr(kaynak, "erisim", None) != "erisildi":
            continue
        if normallestir(iddia["quote"]) not in normallestir(gorunur_metin(kaynak.govde, kaynak.icerik_turu)):
            return {}, "alıntı bugünkü sayfada yok (sayfa değişmiş olabilir)"
    ek = makine_kapsami({"makine_kapsam_disi": kapsam.paket_kapsami(p, sonuclar)})
    return ek, (None if ek else "kaynak getirilemedi ya da bölüm yok")


def calistir(db, *, yaz=False, geri_al=False, getirici=None, cikti=print):
    """Değişen kayıt sayısını döndürür. `getirici` sınamada sahte ağ için verilir."""
    getirici = getirici or (None if geri_al else getirme.Getirici(ayarlar=Ayarlar()))
    with closing(sqlite3.connect(db)) as baglanti:
        satirlar = baglanti.execute("SELECT id, version, record FROM v4_records WHERE visible=1 AND record<>'{}' "
                                    "ORDER BY published_at").fetchall()
    # 1. Hesap: depoda işlem açık değil (bağlantı kapandı); sayfa getirme uzun sürebilir.
    yazilacak = []
    for kimlik, surum, metin in satirlar:
        kayit = json.loads(metin)
        kapi = kayit["publication"]["gate"]
        if geri_al:
            if ALAN in kapi:
                yazilacak.append((kimlik, surum, metin, None))
                cikti(f"{kimlik}@{surum}: ek silinecek")
            continue
        if ALAN in kapi:
            continue
        try:
            ek, neden = hesapla(kayit, getirici)
        except Exception as hata:  # tek kaydın hatası bütün çalışmayı durdurmasın
            ek, neden = {}, f"hesap hatası ({type(hata).__name__})"
        if not ek:
            cikti(f"{kimlik}@{surum}: {neden}; atlandı")
            continue
        yazilacak.append((kimlik, surum, metin, ek))
        cikti(f"{kimlik}@{surum}: {json.dumps(ek[ALAN], ensure_ascii=False)}")
    # 2. Yazma: tek kısa işlem. Kayıt bu arada değiştiyse (eş zamanlı yazım) dokunulmaz.
    degisen = 0
    if yaz and yazilacak:
        with closing(sqlite3.connect(db, timeout=10)) as baglanti:
            with baglanti:
                for kimlik, surum, eski_metin, ek in yazilacak:
                    kayit = json.loads(eski_metin)
                    if ek is None:
                        del kayit["publication"]["gate"][ALAN]
                    else:
                        kayit["publication"]["gate"].update(ek)
                    degisen += baglanti.execute(
                        "UPDATE v4_records SET record=? WHERE id=? AND version=? AND record=?",
                        (json.dumps(kayit, ensure_ascii=False, sort_keys=True), kimlik, surum, eski_metin)).rowcount
    elif not yaz:
        degisen = len(yazilacak)
    cikti(f"{degisen} kayıt {'güncellendi' if yaz else 'güncellenecek (kuru çalışma; yazmak için --yaz)'}")
    return degisen


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--yaz", action="store_true")
    ap.add_argument("--geri-al", action="store_true")
    a = ap.parse_args(argv)
    calistir(a.db, yaz=a.yaz, geri_al=a.geri_al)


if __name__ == "__main__":
    main()
