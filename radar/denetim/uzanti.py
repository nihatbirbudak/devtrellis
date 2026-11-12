"""10. denetim (alpha.15, dilim 14a): uzantı kuralları.

Uzantı (`kind: record_extension`) yayındaki bir kayda yalnız yeni iddia (ve raporda kaynak, cevaplanan soru) ekleyen bir fark
paketidir (tasarım `isler/uzanti-yolu-20261111/tasarim.md` §2b–§2e). Kapı fark paketini değil, sunucunun kapı anında üst kaydın
güncel sürümüyle kurduğu **birleşik paketi** bütün denetimlerden geçirir; bu modül ona uzantıya özgü kuralları ekler:

- birleştirmeden önce (`on_denetim`, ağsız): fark paketinin biçimi, üst kaydın yayında olması, katkıcının gördüğü sürümün
  güncel sürümün atası olması, tür uyumu, kaynak kartında tek adres, lisansın üstünkiyle aynı olması;
- birleşik pakette (`denetle`, engelleyici): yeni iddia üstteki bir iddiayla aynıysa ret; kaldırılan `not_covered` maddesi yeni
  iddialarca karşılanmıyorsa ya da yeni iddia aynı kaynaktaki bir üst iddiayla çelişiyor görünüyorsa uyarı (sunucu bunları
  moderasyona gönderir); fark paketinin kendi metni (değişiklik özeti, köken beyanı) gizli bilgi, yönerge ve sağlayıcı
  kuralından geçer; üst kaydın bir alıntısı sayfada artık bulunmuyorsa (sürüklenme) hüküm `yayimlanamaz` olur.

Uzantı üst kaydın iddialarını değiştiremez ya da silemez: fark paketinde böyle bir alan yoktur, birleşik paketi sunucu kurar.
"""

from __future__ import annotations

import re
from collections import Counter

from . import gizlilik, saglayici, sema, yonerge
from .metin import normallestir
from .sonuc import DENENEMEDI, KALDI, UYARI, Denetim
from .yinelenen import jaccard, sozcuk_kumesi

UZANTI_TURU = "record_extension"
EK_ALANLARI = {"kind", "schema_version", "language", "extends", "claims", "sources_added", "answers_added",
               "not_covered_remove", "not_covered_add", "provenance", "rights"}
EK_ZORUNLU = ("kind", "schema_version", "language", "extends", "claims", "provenance", "rights")
BAG_ALANLARI = {"id", "version", "change_summary"}
YALNIZ_RAPOR = ("sources_added", "answers_added")
CEVAP_BAG_ALANLARI = ("claims", "inferred_from")   # okuma_eki.CEVAP_BAG_ALANLARI ile aynı
EN_COK_YENI_IDDIA = 10
OZET_SINIRI = 300
YINELENEN_ESIK = 0.7          # yinelenen.py ile aynı iddia cümlesi Jaccard eşiği
CELISKI_ESIK = 0.5
_ID32 = re.compile(r"[0-9a-f]{32}")
_SAYI = re.compile(r"\d+(?:[.,]\d+)*")
_OLUMSUZ = {"not", "no", "never", "cannot", "can't", "without", "none", "nor", "neither", "disabled", "isn't", "aren't",
            "doesn't", "don't", "won't", "unsupported", "never"}
_DOLGU = {"that", "this", "with", "from", "into", "when", "which", "their", "there", "have", "been", "does", "were",
          "will", "would", "should", "could", "about", "than", "then", "they", "them", "also", "only", "each", "page",
          "pages", "cited", "state", "states", "stated", "covered", "record", "claims", "claim"}
_ALINTI_KODLARI = ("alinti.yaklasik", "alinti.bulunamadi", "alinti.kaynak_getirilemedi")


def soy_surumleri(kayit):
    """Kaydın atası olan sürümler (kendisi dahil). Uzantıyla kurulmuş kayıtta `lineage.versions`, öteki kayıtta yalnız kendisi."""
    surumler = [s.get("version") for s in ((kayit.get("lineage") or {}).get("versions") or []) if isinstance(s, dict)]
    return [s for s in surumler if type(s) is int] or [kayit.get("version")]


def _ad(anahtar):
    return sema._ad(anahtar)


def on_denetim(ek, ust, gorulen, *, adres_anahtari):
    """Birleştirmeden önce (ağsız): [(neden kodu, açıklama)]. Boş liste: birleştirilebilir. `ust` üst kaydın görünür güncel
    sürümü (yoksa None), `gorulen` katkıcının gördüğü sürümün kaydı (yoksa None)."""
    hatalar = []

    def hata(kod, metin):
        hatalar.append((kod, metin))

    if not isinstance(ek, dict):
        return [("uzanti.bicim", "Uzantı paketi bir nesne olmalı.")]
    for anahtar in ek:
        if anahtar not in EK_ALANLARI:
            hata("uzanti.bicim", f"paket.{_ad(anahtar)} uzantı paketinde yok (izinli: extends, claims, sources_added, "
                                 "answers_added, not_covered_remove, not_covered_add, provenance, rights).")
    for anahtar in EK_ZORUNLU:
        if anahtar not in ek:
            hata("uzanti.bicim", f"paket.{anahtar} zorunlu.")
    if ek.get("schema_version") != 4 or isinstance(ek.get("schema_version"), bool):
        hata("uzanti.bicim", "paket.schema_version 4 olmalı.")
    if ek.get("language") != "en":
        hata("uzanti.bicim", "paket.language en olmalı.")
    bag = ek.get("extends")
    if not isinstance(bag, dict) or set(bag) != BAG_ALANLARI:
        hata("uzanti.bicim", "paket.extends tam olarak id, version ve change_summary alanlarını taşımalı.")
    else:
        if not isinstance(bag["id"], str) or not _ID32.fullmatch(bag["id"]):
            hata("uzanti.bicim", "paket.extends.id 32 küçük onaltılık karakterlik kayıt kimliği olmalı.")
        if type(bag["version"]) is not int or not 1 <= bag["version"] <= 2 ** 31:
            hata("uzanti.bicim", "paket.extends.version pozitif tam sayı olmalı (gördüğünüz sürüm).")
        ozet = bag["change_summary"]
        if not isinstance(ozet, str) or not ozet.strip() or len(ozet) > OZET_SINIRI:
            hata("uzanti.bicim", f"paket.extends.change_summary 1–{OZET_SINIRI} karakterlik metin olmalı.")
    iddialar = ek.get("claims")
    if isinstance(iddialar, list) and not iddialar:
        hata("uzanti.bos", "Uzantı en az bir yeni iddia taşımalı.")
    elif not isinstance(iddialar, list) or len(iddialar) > EN_COK_YENI_IDDIA or not all(isinstance(i, dict) for i in iddialar):
        hata("uzanti.bicim", f"paket.claims 1–{EN_COK_YENI_IDDIA} iddia nesnesinden oluşan bir liste olmalı.")
    for alan in ("sources_added", "answers_added", "not_covered_add"):
        if alan in ek and not isinstance(ek[alan], list):
            hata("uzanti.bicim", f"paket.{alan} liste olmalı.")
    if ek.get("sources_added") == []:
        # Düzeltme turu 1 (kabul K9): şemadaki `minItems: 1` ile aynı (alan ya yok ya da en az bir kaynak taşır).
        hata("uzanti.bicim", "paket.sources_added verilirse en az bir kaynak taşımalı (boşsa alanı hiç yazmayın).")
    silinen = ek.get("not_covered_remove", [])
    if not isinstance(silinen, list) or any(type(n) is not int for n in silinen) or len(set(silinen)) != len(silinen):
        hata("uzanti.bicim", "paket.not_covered_remove yinelenmeyen tam sayılar (gördüğünüz sürümün not_covered sırası, "
                             "0'dan) listesi olmalı.")
        silinen = []
    if not isinstance(ek.get("rights"), dict) or set(ek["rights"]) != {"own_text_license"}:
        hata("uzanti.bicim", "paket.rights yalnız own_text_license taşımalı.")
    if hatalar:
        return hatalar
    if ust is None:
        return [("uzanti.ust_yok", "Uzatılmak istenen kayıt yayında değil (hiç yayımlanmadı, geri çekildi ya da kaldırıldı).")]
    if (gorulen is None or gorulen.get("version") not in soy_surumleri(ust)
            or [i.get("id") for i in gorulen.get("claims", [])] != [i.get("id") for i in ust["claims"][:len(gorulen.get("claims", []))]]):
        return [("uzanti.gorulen_surum_yok", "Gördüğünüz sürüm kaydın bugünkü sürümünün geçmişinde yok (o sürüm geri çekilmiş "
                                             "ya da kaldırılmış olabilir); kaydın güncel sürümünü yeniden okuyup önizleyin.")]
    kart = ust["kind"] == "source_card"
    if kart and any(alan in ek for alan in YALNIZ_RAPOR):
        hata("uzanti.tur", "Kaynak kartına sources_added ya da answers_added eklenemez (kart tek kaynaklıdır).")
    if kart:
        anahtar = (ust["claims"][0].get("evidence") or {}).get("address_key") if ust["claims"] else None
        for sira, iddia in enumerate(iddialar):
            adres = iddia.get("source_url")
            if isinstance(adres, str) and adres_anahtari(adres) != anahtar:
                hata("uzanti.kart_adresi", f"Yeni iddia {sira + 1} kartın kaynak adresinde değil; kaynak kartının bütün "
                                           "iddiaları tek sayfadan gelir.")
    if ek["rights"]["own_text_license"] != (ust.get("rights") or {}).get("own_text_license"):
        hata("uzanti.lisans", "Uzantının lisansı (rights.own_text_license) üst kaydınkiyle aynı olmalı.")
    # Düzeltme turu 1 (kabul Ö2): uzantının eklediği cevap bağları yalnız uzantının kendi yeni iddialarını (P+k) gösterir.
    # Gördüğü sürümün iddiasına (1..P) bağ, başkasının iddiasını yazılmadığı bir soruya cevap gibi gösterebilirdi.
    gorulen_sayi = len(gorulen.get("claims", []))
    for sira, oge in enumerate(ek.get("answers_added") or []):
        bagli = [n for alan in CEVAP_BAG_ALANLARI if isinstance(oge, dict) and isinstance(oge.get(alan), list)
                 for n in oge[alan] if type(n) is int and 1 <= n <= gorulen_sayi]
        if bagli:
            hata("uzanti.ust_iddia_bagi", f"answers_added {sira + 1}. madde kaydın var olan iddiasına bağlı ("
                                          + ", ".join(f"[{n}]" for n in bagli) + "); uzantının cevap bağları yalnız kendi "
                                          f"yeni iddialarını ({gorulen_sayi + 1}–{gorulen_sayi + len(iddialar)}) gösterebilir.")
    gorulen_nc = (gorulen.get("content") or {}).get("not_covered") or []
    if any(not 0 <= n < len(gorulen_nc) for n in silinen):
        hata("uzanti.bicim", f"paket.not_covered_remove gördüğünüz sürümün not_covered sırasında olmalı (0–{len(gorulen_nc) - 1}).")
    return hatalar


def ust_alinti_ayir(d4, ust_iddia_sayisi):
    """Alıntı denetiminde üst kaydın iddialarının bulunamaması uzantının hatası değildir (sayfa değişmiş olabilir): o satırlar
    ret sayılmaz, `uzanti.ust_alinti_kaydi` (denenemedi) olur. Yeni iddiaların hataları aynen kalır. Dönüş (d4, kaydı_mı)."""
    satirlar = [s for s in d4.ayrinti if "iddia" in s]
    ust_hatali = [s for s in satirlar if s["iddia"] < ust_iddia_sayisi and s.get("sonuc") == KALDI
                  and s.get("neden") in _ALINTI_KODLARI]
    if not ust_hatali:
        return d4, False
    yeni = Denetim(d4.sira, d4.ad, d4.baslik, engelleyici=d4.engelleyici, gecti_aciklamasi=d4.gecti_aciklamasi)
    yeni.ayrinti = d4.ayrinti
    for bulgu in d4.bulgular:
        if not (bulgu["seviye"] == KALDI and bulgu["kod"] in _ALINTI_KODLARI):
            yeni._ekle(bulgu["seviye"], bulgu["kod"], bulgu["aciklama"])
    for satir in satirlar:
        if satir["iddia"] >= ust_iddia_sayisi and satir.get("sonuc") == KALDI and satir.get("neden") in _ALINTI_KODLARI:
            yeni.kal(satir["neden"])
    yeni.denenemedi("uzanti.ust_alinti_kaydi", "Üst kaydın " + ", ".join(f"[{s['iddia'] + 1}]" for s in ust_hatali)
                    + " alıntısı sayfada artık aynen bulunmuyor (sayfa değişmiş olabilir); uzantı şimdi yayımlanamaz, "
                      "yeniden denenecek.")
    return yeni, True


def _icerik_sozcukleri(metin):
    return {s for s in sozcuk_kumesi(metin) if len(s) >= 4 and s not in _DOLGU}


def _olumsuz(metin):
    return bool(set(re.findall(r"[a-z']+", str(metin or "").lower())) & _OLUMSUZ)


def _olumsuz_sayimi(metin):
    """Olumsuzluk sözcüklerinin sayımı (düzeltme turu 1, kabul K1): "not available … disabled" ile "keeps … disabled" iki
    taraf da olumsuz sözcük taşıdığı için var/yok karşılaştırması çelişkiyi kaçırıyordu."""
    return Counter(s for s in re.findall(r"[a-z']+", str(metin or "").lower()) if s in _OLUMSUZ)


def _adres_anahtari(url):
    from radar.kaynak_kimligi import KaynakKimligiHatasi, address_key
    try:
        return address_key(url, "en")
    except (KaynakKimligiHatasi, TypeError, ValueError):
        return None


def _ayni_adres(a, b, anahtar, sayfa=None):
    """Düzeltme turu 1 (kabul Ö1): adres ham metinle değil adres anahtarıyla karşılaştırılır (`DOCS.example.org`, `:443`
    aynı sayfadır; kapının adres denetimi ve getirici de öyle sayar). Anahtar çıkmazsa ham metin.

    Düzeltme turu 2 (kabul Ö-B): anahtar yolu, sondaki `/`'yi, yüzde kodlamasını ve sorgu sırasını korur; sunucu ise bu
    yazımlarda çoğunlukla aynı sayfayı verir. Bu yüzden kapının bu çalışmada iki adresten getirdiği görünür metin aynıysa
    da "aynı sayfa" sayılır (`sayfa`: adres → görünür metin özeti ya da None). Yanılgı yalnız moderasyon yönündedir."""
    ka, kb = anahtar(a), anahtar(b)
    if ka == kb if ka and kb else a == b:
        return True
    oa, ob = (sayfa(a), sayfa(b)) if sayfa else (None, None)
    return bool(oa) and oa == ob


def _sayilar(metin):
    return set(_SAYI.findall(str(metin or "")))


def denetle(baglam, birlesik):
    """Birleşik pakette uzantı kuralları. `baglam`: {"ek", "ust", "bilgi", "ust_alinti_kaydi"}."""
    d = Denetim(10, "uzanti", "Uzantı kuralları (yalnız ekler, yineleme, kapsam silme, çelişki kuşkusu)",
                gecti_aciklamasi="Yeni iddialar üst kayıttakileri yinelemiyor; kaldırılan kapsam notu yok ya da yeni "
                                 "iddialarca karşılanıyor; çelişki kuşkusu yok.")
    ek, ust, bilgi = baglam["ek"], baglam["ust"], baglam["bilgi"]
    anahtar = baglam.get("adres_anahtari") or _adres_anahtari
    sayfa = baglam.get("sayfa_kimligi")
    sayi = len(ust["claims"])
    iddialar = birlesik.get("claims") if isinstance(birlesik.get("claims"), list) else []
    mevcut, yeniler = iddialar[:sayi], iddialar[sayi:]
    for sira, yeni in enumerate(yeniler):
        if not isinstance(yeni, dict):
            continue
        alinti = " ".join(normallestir(str(yeni.get("quote") or "")).split())
        kume = sozcuk_kumesi(yeni.get("text"))
        for no, eski in enumerate(mevcut, 1):
            ayni = alinti and alinti == " ".join(normallestir(str(eski.get("quote") or "")).split()) \
                and _ayni_adres(yeni.get("source_url"), eski.get("source_url"), anahtar, sayfa)
            # Cümle benzerliği yalnız olumsuzluk ve sayılar da aynıysa yinelemedir; farklıysa aşağıdaki çelişki kuşkusu.
            benzer = (jaccard(kume, sozcuk_kumesi(eski.get("text"))) >= YINELENEN_ESIK
                      and _olumsuz(yeni.get("text")) == _olumsuz(eski.get("text"))
                      and _sayilar(yeni.get("text")) == _sayilar(eski.get("text")))
            if ayni or benzer:
                d.kal("uzanti.yinelenen_iddia", f"Yeni iddia {sira + 1} kayıttaki [{no}] iddiasıyla aynı ya da çok benzer "
                                                "(başkası zaten eklemiş olabilir).")
                break
    # Düzeltme turu 1 (kabul K2): kaldırılan maddenin karşılandığı yalnız yeni iddiaların **alıntısında** aranır (alıntı sayfada
    # aynen bulunur); iddia metnine maddenin sözcüklerini yazmak uyarıyı artık aşmaz.
    yeni_sozcukler = set()
    for yeni in yeniler:
        if isinstance(yeni, dict):
            yeni_sozcukler |= _icerik_sozcukleri(yeni.get("quote"))
    for sira, metin in enumerate(bilgi.get("kaldirilan_not_covered") or []):
        sozcukler = _icerik_sozcukleri(metin)
        if not sozcukler or len(sozcukler & yeni_sozcukler) * 2 < len(sozcukler):
            d.uyar("uzanti.kapsam_silme", f"Kaldırılan {sira + 1}. not_covered maddesinin içerik sözcüklerinin yarısı yeni "
                                          "iddialarda geçmiyor; madde gerçekten karşılandı mı, moderatör bakacak.")
    for sira, yeni in enumerate(yeniler):
        if not isinstance(yeni, dict):
            continue
        for no, eski in enumerate(mevcut, 1):
            if not _ayni_adres(yeni.get("source_url"), eski.get("source_url"), anahtar, sayfa):
                continue
            benzer = jaccard(_icerik_sozcukleri(yeni.get("text")), _icerik_sozcukleri(eski.get("text")))
            if benzer >= CELISKI_ESIK and (_olumsuz_sayimi(yeni.get("text")) != _olumsuz_sayimi(eski.get("text"))
                                                          or _sayilar(yeni.get("text")) != _sayilar(eski.get("text"))):
                d.uyar("uzanti.celiski", f"Yeni iddia {sira + 1} aynı kaynaktaki [{no}] iddiasına benziyor ama olumsuzluk ya da "
                                         "sayı farklı; çelişki kuşkusu, moderatör bakacak.")
                break
    # Fark paketinin birleşik pakette olmayan kendi metni: değişiklik özeti ve köken beyanı (kayıtta `lineage`'a girer).
    tarama = {"change_summary": (ek.get("extends") or {}).get("change_summary") or ""}
    s = sema._Sinayici(d)
    sema._koken(s, ek.get("provenance"), "paket.provenance")
    for alt in (gizlilik.denetle(tarama), yonerge.denetle(tarama), saglayici.denetle({"provenance": ek.get("provenance")})):
        for bulgu in alt.bulgular:
            d._ekle(bulgu["seviye"], bulgu["kod"], bulgu["aciklama"])
    if baglam.get("ust_alinti_kaydi"):
        d.denenemedi("uzanti.ust_alinti_kaydi")
    d.ayrinti = [{"ust_surum": ust.get("version"), "gorulen_surum": bilgi.get("gorulen_surum"), "ust_iddia": sayi,
                  "yeni_iddia": len(yeniler), "kaldirilan_not_covered": len(bilgi.get("kaldirilan_not_covered") or [])}]
    return d


def benzerlik_uyarisi(d9):
    """Düzeltme turu 1 (kabul K10): uzantı sonucunda "yeni kayıt yerine uzantı önerilir" cümlesi anlamsız; aynı ölçüm
    `yinelenen.benzer_kayit` (bilgi uyarısı) olarak yazılır. Üst kayıt listeden zaten çıkarıldı; hüküm etkilenmez."""
    for bulgu in d9.bulgular:
        if bulgu["kod"] == "yinelenen.uzanti_oner":
            bulgu["kod"] = "yinelenen.benzer_kayit"
            bulgu["aciklama"] = bulgu["aciklama"].replace("; yeni kayıt yerine uzantı önerilir.",
                                                          "; uzantının eklediği iddialar o kayıtta zaten olabilir (bilgi).")
    return d9


def kapsam_silme_ya_da_celiski(sonuc):
    """Kapı sonucunda moderasyona gönderilecek uzantı uyarısı (hold_reason) ya da None."""
    uyarilar = set(sonuc.get("uyarilar") or [])
    if "uzanti.kapsam_silme" in uyarilar:
        return "extension_coverage_removal"
    if "uzanti.celiski" in uyarilar:
        return "extension_conflict"
    return None


__all__ = ["UZANTI_TURU", "on_denetim", "ust_alinti_ayir", "denetle", "soy_surumleri", "kapsam_silme_ya_da_celiski",
           "DENENEMEDI", "UYARI"]
