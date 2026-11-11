"""Komut satırı: tek paket dosyasını denetler, okunur özeti ekrana, istenirse JSON ve metin sonucunu dosyaya yazar."""

from __future__ import annotations

import argparse
import json
import sys

from .kapi import REDDEDILIR, YAYIMLANIR, denetle
from .rapor import metin

CIKIS = {YAYIMLANIR: 0, REDDEDILIR: 1}


def _oku(yol):
    with open(yol, encoding="utf-8") as dosya:
        return json.load(dosya)


def ana(argumanlar=None):
    ayristirici = argparse.ArgumentParser(prog="python -m denetim", description="Yayın öncesi makine denetim kapısı.")
    ayristirici.add_argument("paket", help="denetlenecek paket (JSON)")
    ayristirici.add_argument("--yayindakiler", help="yayındaki kayıtlar listesi (JSON liste ya da {'kayitlar': [...]})")
    ayristirici.add_argument("--ag-yok", action="store_true", help="ağa çıkma; ağ gerektiren denetimler 'denenemedi' olur")
    ayristirici.add_argument("--tam", action="store_true", help="ağsız denetimlerde ret olsa da kaynakları getir")
    ayristirici.add_argument("--okuma-eki", action="store_true",
                             help="alpha.15 okuma eki kurallarını da çalıştır (sunucuda ayar `reading_extensions`; varsayılan kapalı)")
    ayristirici.add_argument("--cikti", help="JSON sonucun yazılacağı dosya")
    ayristirici.add_argument("--metin-cikti", help="okunur özetin yazılacağı dosya")
    ayristirici.add_argument("--baslik", help="okunur özetin başlığı")
    secim = ayristirici.parse_args(argumanlar)
    try:
        paket = _oku(secim.paket)
        yayindakiler = None
        if secim.yayindakiler:
            yayindakiler = _oku(secim.yayindakiler)
            if isinstance(yayindakiler, dict):
                yayindakiler = yayindakiler.get("kayitlar", [])
    except (OSError, ValueError) as hata:
        print(f"Dosya okunamadı ya da geçerli JSON değil: {type(hata).__name__}", file=sys.stderr)
        return 3
    sonuc = denetle(paket, yayindakiler=yayindakiler, ag=not secim.ag_yok, tam=secim.tam, okuma_eki=secim.okuma_eki)
    okunur = metin(sonuc, secim.baslik)
    if secim.cikti:
        with open(secim.cikti, "w", encoding="utf-8", newline="\n") as dosya:
            json.dump(sonuc, dosya, ensure_ascii=False, indent=2)
            dosya.write("\n")
    if secim.metin_cikti:
        with open(secim.metin_cikti, "w", encoding="utf-8", newline="\n") as dosya:
            dosya.write(okunur)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    print(okunur)
    return CIKIS.get(sonuc["hukum"], 2)


if __name__ == "__main__":
    sys.exit(ana())
