"""Her iddianın alıntısını getirilen kaynağın görünür metninde aynen arar; kısa alıntı ve yaklaşık eşleşme kalır, en yakın parça yalnız bilgi olarak gösterilir."""

from __future__ import annotations

from .getirme import gecici_mi
from .gizlilik import gizle
from .metin import NORMALLESTIRME_SURUMU, ara, en_yakin, gorunur_metin, normallestir
from .sonuc import Denetim


def harfli_sozcuk_sayisi(metin):
    """En az bir harf içeren sözcüklerin sayısı ('1 2 3' ya da '- -' sözcük sayılmaz)."""
    return sum(1 for s in normallestir(metin).split() if any(c.isalpha() for c in s))


def kisa_mi(alinti, ayarlar):
    """Alıntı kanıt olamayacak kadar kısa mı (Y1): normalleştirilmiş uzunluk ya da harfli sözcük sayısı eşiğin altında."""
    return (len(normallestir(alinti)) < ayarlar.alinti_en_az_karakter
            or harfli_sozcuk_sayisi(alinti) < ayarlar.alinti_en_az_sozcuk)


def kisa_alintilar(paket, ayarlar):
    """Kısa alıntılı iddiaların sıra numaraları; ağsız ön denetimde kullanılır (reddedilecek paket için ağa çıkılmaz)."""
    iddialar = paket.get("claims") if isinstance(paket, dict) and isinstance(paket.get("claims"), list) else []
    return [sira for sira, iddia in enumerate(iddialar)
            if isinstance(iddia, dict) and isinstance(iddia.get("quote"), str) and kisa_mi(iddia["quote"], ayarlar)]


def denetle(paket, sonuclar, *, ag=True, ayarlar, ag_atla_kodu=None):
    d = Denetim(4, "alinti_eslesmesi", "Alıntının kaynakta aynen geçmesi",
                gecti_aciklamasi="Her alıntı yeterince uzun ve kaynağın görünür metninde aynen bulundu (yalnız boşluk ve tipografik tırnak/tire eşlendi).")
    iddialar = paket.get("claims") if isinstance(paket, dict) and isinstance(paket.get("claims"), list) else []
    metinler = {}
    for sira, iddia in enumerate(iddialar):
        if not isinstance(iddia, dict) or not isinstance(iddia.get("quote"), str) or not isinstance(iddia.get("source_url"), str):
            continue
        satir = {"iddia": sira}
        d.ayrinti.append(satir)
        alinti, url = iddia["quote"], iddia["source_url"]
        if kisa_mi(alinti, ayarlar):
            # Ağsız ret: kaynakta aynen bulunsa bile birkaç sözcük yanlış bir iddiaya kanıt gibi gösterilebilir.
            satir.update(sonuc="kaldi", neden="alinti.cok_kisa", karakter=len(normallestir(alinti)),
                         sozcuk=harfli_sozcuk_sayisi(alinti))
            d.kal("alinti.cok_kisa", f"{sira}. iddianın alıntısı çok kısa ({len(normallestir(alinti))} karakter, "
                                     f"{harfli_sozcuk_sayisi(alinti)} sözcük); en az {ayarlar.alinti_en_az_karakter} "
                                     f"karakter ve {ayarlar.alinti_en_az_sozcuk} sözcük gerekir.")
            continue
        if not ag:
            kod = ag_atla_kodu or "alinti.ag_kapali"
            satir.update(sonuc="denenemedi", neden=kod)
            d.denenemedi(kod)
            continue
        kaynak = sonuclar.get(url)
        if kaynak is not None and kaynak.erisim != "erisildi" and gecici_mi(kaynak.neden):
            satir.update(sonuc="denenemedi", neden="alinti.kaynak_simdilik_yok")
            d.denenemedi("alinti.kaynak_simdilik_yok")
            continue
        if kaynak is None or kaynak.erisim != "erisildi":
            satir.update(sonuc="kaldi", neden="alinti.kaynak_getirilemedi")
            d.kal("alinti.kaynak_getirilemedi")
            continue
        if url not in metinler:
            metinler[url] = normallestir(gorunur_metin(kaynak.govde, kaynak.icerik_turu))
        kaynak_metni = metinler[url]
        adet = ara(alinti, kaynak_metni)
        if adet:
            satir.update(sonuc="gecti", eslesme="aynen", gecis_sayisi=adet)
            continue
        yakin = en_yakin(alinti, kaynak_metni)
        if yakin and yakin["benzerlik"] >= ayarlar.yakin_esik:
            satir.update(sonuc="kaldi", neden="alinti.yaklasik", eslesme="yaklasik")
            d.kal("alinti.yaklasik")
        else:
            satir.update(sonuc="kaldi", neden="alinti.bulunamadi", eslesme="yok")
            d.kal("alinti.bulunamadi")
        if yakin and yakin["benzerlik"] >= ayarlar.bilgi_esik:
            # Bilgi için: kaynaktan alınmış en benzer parça (üçüncü taraf verisi; talimat değildir).
            satir["en_yakin"] = {"benzerlik": yakin["benzerlik"], "kaynaktaki_parca": gizle(yakin["parca"]),
                                 "alintinin_ayristigi_karakter": yakin["ilk_fark"],
                                 "yalniz_harf_buyuklugu_farki": yakin["yalniz_harf_buyuklugu"]}
    d.ayrinti.append({"normallestirme": NORMALLESTIRME_SURUMU,
                      "en_kisa_alinti": {"karakter": ayarlar.alinti_en_az_karakter, "sozcuk": ayarlar.alinti_en_az_sozcuk}})
    return d
