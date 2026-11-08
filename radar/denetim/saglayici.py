"""Model aşamalarının sağlayıcısını veriyle tanımlı kural tablosuna göre sınar (KD-020): deneme erişimi her sağlayıcıda ret; erişim türü belirtilmemişse claude dışında ret; Latin dışı harfli ad ret."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from .sonuc import Denetim

KURAL_DOSYASI = Path(__file__).with_name("veri") / "saglayici-kurallari.json"


def kurallari_yukle(yol=KURAL_DOSYASI):
    with open(yol, encoding="utf-8") as dosya:
        return json.load(dosya)


def _esit(deger):
    """Karşılaştırma için: metin değilse boş; NFKC (tam genişlik harf → olağan), baş/son boşluk kırpılmış, küçük harf."""
    return unicodedata.normalize("NFKC", deger).strip().lower() if isinstance(deger, str) else ""


def _eslesir(kural, asama):
    saglayici = _esit(asama.get("provider")) if isinstance(asama.get("provider"), str) else asama.get("provider")
    # Model adı ve koşul adresi birlikte aranır: "build.nvidia.com/meta/llama..." ya da NVIDIA koşul sayfası da yakalanır.
    aranan = _esit(asama.get("model")) + " " + _esit(asama.get("terms_url"))
    hepsi = kural["saglayici"] == "*" and saglayici not in (kural.get("saglayici_haric") or [])
    uyan = hepsi or saglayici in kural["saglayici"] or bool(
        kural.get("model_kalibi") and re.search(kural["model_kalibi"], aranan.strip(), re.I))
    if not uyan:
        return False
    erisimler = kural["erisim_turleri"]
    erisim = _esit(asama.get("access_type")) if isinstance(asama.get("access_type"), str) else asama.get("access_type")
    return erisimler == "*" or erisim in erisimler


def latin_disi_harf_var(deger):
    """NFKC'den sonra Latin alfabesi dışında bir harf (Kiril, Yunan …) var mı; 'nvіdia' gibi benzer görünümlü yazımı yakalar."""
    if not isinstance(deger, str):
        return False
    for karakter in unicodedata.normalize("NFKC", deger):
        if unicodedata.category(karakter).startswith("L") and not unicodedata.name(karakter, "").startswith("LATIN"):
            return True
    return False


def denetle(paket, kurallar=None):
    kurallar = kurallar or kurallari_yukle()
    d = Denetim(8, "saglayici_kurali", "Sağlayıcı kuralı (NVIDIA deneme çıktısı)",
                gecti_aciklamasi="Model aşamalarında yayını engelleyen sağlayıcı bulunmadı.")
    koken = paket.get("provenance") if isinstance(paket, dict) else None
    if not isinstance(koken, dict):
        d.kal("saglayici.koken_yok")
        return d
    koken_turu = koken.get("origin")
    asamalar = koken.get("stages") if isinstance(koken.get("stages"), list) else []
    if koken_turu == "unknown":
        d.kal("saglayici.koken_bilinmiyor")
    if koken_turu in ("model_assisted", "mixed") and not asamalar:
        d.kal("saglayici.asama_yok")
    for sira, asama in enumerate(asamalar):
        if not isinstance(asama, dict):
            continue
        if latin_disi_harf_var(asama.get("provider")) or latin_disi_harf_var(asama.get("model")):
            d.kal("saglayici.latin_disi_harf")
            d.ayrinti.append({"asama": sira, "kural": "latin_disi_harf", "sonuc": "kaldi"})
            continue
        uygulanan = next((k for k in kurallar["kurallar"] if _eslesir(k, asama)), None)
        satir = {"asama": sira, "saglayici": asama.get("provider"), "erisim_turu": asama.get("access_type", None),
                 "kural": uygulanan["ad"] if uygulanan else None,
                 "sonuc": uygulanan["sonuc"] if uygulanan else "gecti"}
        d.ayrinti.append(satir)
        if uygulanan:
            (d.kal if uygulanan["sonuc"] == "kaldi" else d.uyar)(uygulanan["neden"])
    d.ayrinti.append({"kural_tablosu": kurallar.get("surum")})
    return d
