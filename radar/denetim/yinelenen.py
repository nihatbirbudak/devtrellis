"""Paketi yayındaki kayıtlarla karşılaştırır: başlık sözcük örtüşmesi ve iddia örtüşmesi; eşik üstünde 'uzantı öner' uyarısı verir, ret vermez."""

from __future__ import annotations

import re

from .sonuc import Denetim

_DOLGU = {"a", "an", "the", "and", "or", "of", "for", "to", "in", "on", "with", "by", "from", "at", "as", "is", "are",
          "be", "it", "its", "this", "that", "how", "what", "when", "using", "use", "via", "into", "not", "no", "does"}


def sozcuk_kumesi(metin):
    return {s for s in re.findall(r"[a-z0-9][a-z0-9_.-]*", (metin or "").lower()) if s not in _DOLGU}


def jaccard(a, b):
    return len(a & b) / len(a | b) if a and b else 0.0


def _iddialar(kayit):
    sonuc = []
    for iddia in kayit.get("claims") or []:
        if isinstance(iddia, dict):
            metin = iddia.get("text") or iddia.get("claim") or ""
            sonuc.append((sozcuk_kumesi(metin), " ".join(str(iddia.get("quote") or "").split()), iddia.get("source_url")))
    return sonuc


def benzerlik(paket, kayit, iddia_esigi=0.7):
    """(başlık_benzerliği, iddia_örtüşmesi) — örtüşme: yeni iddiaların yayındakilerden biriyle eşleşen payı.
    Eşleşme: aynı alıntı + aynı kaynak adresi, ya da iddia cümlelerinin sözcük Jaccard'ı ≥ 0.7."""
    baslik = jaccard(sozcuk_kumesi(paket.get("title")), sozcuk_kumesi(kayit.get("title")))
    yeniler, eskiler = _iddialar(paket), _iddialar(kayit)
    if not yeniler or not eskiler:
        return round(baslik, 3), 0.0
    eslesen = 0
    for kume, alinti, adres in yeniler:
        if any((alinti and alinti == e_alinti and adres == e_adres) or jaccard(kume, e_kume) >= iddia_esigi
               for e_kume, e_alinti, e_adres in eskiler):
            eslesen += 1
    return round(baslik, 3), round(eslesen / len(yeniler), 3)


def denetle(paket, yayindakiler, *, esik=0.5):
    d = Denetim(9, "yinelenen_yayin", "Yinelenen yayın (uyarı)", engelleyici=False,
                gecti_aciklamasi="Yayındaki kayıtlardan hiçbiri eşik üstünde benzemiyor.")
    if yayindakiler is None:
        d.denenemedi("yinelenen.liste_yok")
        return d
    olcumler = []
    for sira, kayit in enumerate(yayindakiler):
        if not isinstance(kayit, dict):
            continue
        baslik, ortusme = benzerlik(paket if isinstance(paket, dict) else {}, kayit)
        olcumler.append({"kayit": str(kayit.get("id") or f"#{sira + 1}")[:64], "baslik": str(kayit.get("title") or "")[:200],
                         "baslik_benzerligi": baslik, "iddia_ortusmesi": ortusme, "puan": max(baslik, ortusme)})
    olcumler.sort(key=lambda o: -o["puan"])
    d.ayrinti = olcumler[:3]
    d.ayrinti.append({"karsilastirilan_kayit": len(olcumler), "esik": esik,
                      "olcu": "puan = en büyük(başlık sözcük Jaccard'ı, iddia örtüşmesi)"})
    for olcum in olcumler:
        if olcum["puan"] >= esik:
            d.uyar("yinelenen.uzanti_oner", f"'{olcum['kayit']}' kaydına benziyor (başlık {olcum['baslik_benzerligi']}, "
                                             f"iddia örtüşmesi {olcum['iddia_ortusmesi']}); yeni kayıt yerine uzantı önerilir.")
    return d
