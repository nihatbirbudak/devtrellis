"""Paketin biçimini sınar: zorunlu alanlar, kapalı alan listesi, uzunluklar, alıntı en çok 300 karakter, adres, tarih ve dil alanı."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit

from .gizlilik import guvenli_ad
from .sonuc import Denetim

TURLER = ("source_card", "research_report", "topic_summary")
IDDIA_TURLERI = ("source_statement", "procedure", "version_scope", "measurement", "interpretation")
SURUM_SEMALARI = ("semver", "pep440", "exact")

# İstemcinin atayamayacağı, yalnız sunucunun ürettiği alanlar (K1 taslağı §5).
SUNUCU_ALANLARI = {"id", "version", "evidence", "quote_match", "claim_review", "evidence_state", "section_id",
                   "section_hash", "quote_hash", "supporting_context_hash", "quote_verified_at", "published",
                   "publication", "source_freshness", "corrections", "link", "source", "address_key", "source_key",
                   "body_hash", "normalization_version", "extractor_version", "schema"}

KART_ALANLARI = {"kind", "schema_version", "language", "claims", "not_covered", "provenance", "rights"}
RAPOR_ALANLARI = {"kind", "schema_version", "language", "title", "summary", "short_answer", "claims",
                  "recommendations", "not_covered", "applies_to", "sources", "provenance", "generated_at", "rights",
                  "answers"}
IDDIA_ALANLARI = {"text", "quote", "source_url", "checked_on", "claim_type", "applies_to"}
KART_IDDIA_SINIRI = 20
ASAMA_ALANLARI = {"provider", "model", "access_type", "basis", "terms_url"}

_GUN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ZAMAN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})$")
_URUN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_TURKCE = re.compile(r"[ğĞışŞİ]")


def _ad(anahtar):
    """Bilinmeyen alan adını yalnız güvenli bir tanımlayıcıysa yazar (alan adına gizli bilgi konmuş olabilir)."""
    return anahtar if guvenli_ad(anahtar) else "[gösterilmeyen ad]"


class _Sinayici:
    def __init__(self, denetim):
        self.d = denetim

    def alanlar(self, nesne, yer, izinli, zorunlu):
        if not isinstance(nesne, dict):
            self.d.kal("sema.tip", f"{yer} bir nesne olmalı.")
            return False
        for anahtar in nesne:
            if anahtar in izinli:
                continue
            if anahtar in SUNUCU_ALANLARI:
                self.d.kal("sema.sunucu_alani", f"{yer}.{_ad(anahtar)} yalnız sunucu tarafından atanır; istemci gönderemez.")
            else:
                self.d.kal("sema.bilinmeyen_alan", f"{yer}.{_ad(anahtar)} sözleşmede yok.")
        for anahtar in zorunlu:
            if anahtar not in nesne:
                self.d.kal("sema.eksik_alan", f"{yer}.{anahtar} zorunlu.")
        return True

    def metin(self, deger, yer, en_az, en_cok, zorunlu=True):
        if deger is None and not zorunlu:
            return None
        if not isinstance(deger, str):
            self.d.kal("sema.tip", f"{yer} metin olmalı.")
            return None
        if not en_az <= len(deger) <= en_cok or (en_az and not deger.strip()):
            self.d.kal("sema.uzunluk", f"{yer} {en_az}–{en_cok} karakter olmalı (şu an {len(deger)}).")
        return deger

    def liste(self, deger, yer, en_az, en_cok):
        if not isinstance(deger, list):
            self.d.kal("sema.tip", f"{yer} liste olmalı.")
            return []
        if not en_az <= len(deger) <= en_cok:
            self.d.kal("sema.liste_boyutu", f"{yer} {en_az}–{en_cok} öğe olmalı (şu an {len(deger)}).")
        return deger

    def adres(self, deger, yer):
        if not isinstance(deger, str):
            self.d.kal("sema.tip", f"{yer} metin olmalı.")
            return None
        try:
            parca = urlsplit(deger)
            gecerli = parca.scheme == "https" and bool(parca.hostname) and deger.startswith("https://") and len(deger) <= 2048
        except ValueError:
            gecerli = False
        if not gecerli:
            self.d.kal("sema.adres_bicimi", f"{yer} https:// ile başlayan, en çok 2048 karakterlik geçerli bir adres olmalı.")
            return None
        return deger

    def kapsam(self, deger, yer):
        liste = self.liste(deger, yer, 1, 3)
        for sira, oge in enumerate(liste):
            alt = f"{yer}[{sira}]"
            if not isinstance(oge, dict) or set(oge) != {"product", "versions", "version_scheme"}:
                self.d.kal("sema.kapsam", f"{alt} yalnız product, versions, version_scheme alanlarını taşımalı.")
                continue
            if not isinstance(oge["product"], str) or not _URUN.match(oge["product"]):
                self.d.kal("sema.kapsam", f"{alt}.product küçük harfli ürün kısa adı olmalı.")
            if not isinstance(oge["versions"], str) or not 1 <= len(oge["versions"]) <= 80:
                self.d.kal("sema.kapsam", f"{alt}.versions 1–80 karakter olmalı.")
            if oge["version_scheme"] not in SURUM_SEMALARI:
                self.d.kal("sema.kapsam", f"{alt}.version_scheme semver, pep440 ya da exact olmalı.")


def _koken(s, koken, yer):
    d = s.d
    if not isinstance(koken, dict) or set(koken) - {"origin", "stages"} or not {"origin", "stages"} <= set(koken):
        d.kal("sema.koken_bicimi", f"{yer} yalnız origin ve stages alanlarını taşımalı.")
        return
    if koken["origin"] not in ("human", "model_assisted", "mixed", "unknown"):
        d.kal("sema.koken_bicimi", f"{yer}.origin human, model_assisted, mixed ya da unknown olmalı.")
    asamalar = koken["stages"]
    if not isinstance(asamalar, list) or len(asamalar) > 20:
        d.kal("sema.koken_bicimi", f"{yer}.stages en çok 20 öğelik liste olmalı.")
        return
    if koken["origin"] == "human" and asamalar:
        d.kal("sema.koken_bicimi", f"{yer}: origin 'human' iken model aşaması yazılamaz.")
    for sira, asama in enumerate(asamalar):
        alt = f"{yer}.stages[{sira}]"
        if not isinstance(asama, dict) or set(asama) != ASAMA_ALANLARI:
            d.kal("sema.koken_bicimi", f"{alt} tam olarak provider, model, access_type, basis, terms_url alanlarını taşımalı.")
            continue
        if asama["provider"] not in ("nvidia", "codex", "claude", "other", "unknown"):
            d.kal("sema.koken_bicimi", f"{alt}.provider tanınmıyor.")
        if asama["access_type"] not in ("trial", "non_trial", "unknown"):
            d.kal("sema.koken_bicimi", f"{alt}.access_type trial, non_trial ya da unknown olmalı.")
        if asama["basis"] not in ("contributor_declaration", "reviewed_public_reference", "unknown"):
            d.kal("sema.koken_bicimi", f"{alt}.basis tanınmıyor.")
        if asama["model"] is not None:
            s.metin(asama["model"], f"{alt}.model", 1, 160)
        if asama["terms_url"] is not None:
            s.adres(asama["terms_url"], f"{alt}.terms_url")


def denetle(paket, *, bugun=None):
    bugun = bugun or datetime.now(timezone.utc).date()
    d = Denetim(1, "sema", "Şema ve sınırlar",
                gecti_aciklamasi="Zorunlu alanlar, uzunluklar, adres, tarih ve dil alanı sözleşmeye uyuyor.")
    s = _Sinayici(d)
    if not isinstance(paket, dict):
        d.kal("sema.kok_nesne")
        return d
    tur = paket.get("kind")
    if tur not in TURLER:
        d.kal("sema.tur", "kind source_card, research_report ya da topic_summary olmalı.")
        return d
    kart = tur == "source_card"
    if kart:
        s.alanlar(paket, "paket", KART_ALANLARI, ("kind", "schema_version", "language", "claims"))
    else:
        s.alanlar(paket, "paket", RAPOR_ALANLARI, ("kind", "schema_version", "language", "title", "claims"))
    if paket.get("schema_version") != 4 or isinstance(paket.get("schema_version"), bool):
        d.kal("sema.surum")
    if paket.get("language") != "en":
        d.kal("sema.dil")

    if not kart:
        s.metin(paket.get("title"), "paket.title", 8, 200, zorunlu="title" in paket)
        s.metin(paket.get("summary"), "paket.summary", 1, 1000, zorunlu=False)
        kisa = s.metin(paket.get("short_answer"), "paket.short_answer", 1, 1200, zorunlu=False)
        if kisa and len(kisa.split()) > 80:
            d.uyar("sema.kisa_cevap_uzun", f"Kısa cevap {len(kisa.split())} sözcük; öneri en çok 80.")
        if "recommendations" in paket:
            for sira, oge in enumerate(s.liste(paket["recommendations"], "paket.recommendations", 0, 20)):
                s.metin(oge, f"paket.recommendations[{sira}]", 1, 400)
        if "answers" in paket:
            # v0.2.2 (mühendislik raporu A2): raporun cevapladığı 3–5 soru; aramada kullanılır, kaynağa bağlanmaz.
            for sira, oge in enumerate(s.liste(paket["answers"], "paket.answers", 3, 5)):
                s.metin(oge, f"paket.answers[{sira}]", 1, 120)
        if "applies_to" in paket:
            if isinstance(paket["applies_to"], str):
                s.metin(paket["applies_to"], "paket.applies_to", 1, 400)
            else:
                s.kapsam(paket["applies_to"], "paket.applies_to")
        if "generated_at" in paket and (not isinstance(paket["generated_at"], str) or not _ZAMAN.match(paket["generated_at"])):
            d.kal("sema.tarih_bicimi", "paket.generated_at ISO 8601 zaman olmalı (ör. 2026-11-06T12:45:37Z).")
        if "sources" in paket:
            for sira, kaynak in enumerate(s.liste(paket["sources"], "paket.sources", 1, 30)):
                yer = f"paket.sources[{sira}]"
                if s.alanlar(kaynak, yer, {"title", "url"}, ("title", "url")):
                    s.metin(kaynak.get("title"), f"{yer}.title", 1, 200, zorunlu="title" in kaynak)
                    if "url" in kaynak:
                        s.adres(kaynak["url"], f"{yer}.url")
    if "not_covered" in paket:
        en_cok_oge, en_cok_uzunluk = (3, 80) if kart else (10, 240)
        for sira, oge in enumerate(s.liste(paket["not_covered"], "paket.not_covered", 0, en_cok_oge)):
            s.metin(oge, f"paket.not_covered[{sira}]", 1, en_cok_uzunluk)
    if "provenance" in paket:
        _koken(s, paket["provenance"], "paket.provenance")
    if "rights" in paket and not isinstance(paket["rights"], dict):
        d.kal("sema.tip", "paket.rights bir nesne olmalı (hak denetimi bu kapının dışında).")

    # alpha.13b: kaynak kartı 6 iddiayla sınırlıydı; teşhis T2 eksik kartın kapsamı düşürdüğünü gösterdi (sayfanın
    # bir sorusu 6 maddeye sığmıyordu). Sınır 20; rapor 60.
    iddialar = s.liste(paket.get("claims"), "paket.claims", 1, KART_IDDIA_SINIRI if kart else 60) if "claims" in paket else []
    gorulen, adresler = set(), []
    for sira, iddia in enumerate(iddialar):
        yer = f"paket.claims[{sira}]"
        zorunlu = ("text", "quote", "source_url", "checked_on") + (("claim_type", "applies_to") if kart else ())
        if not s.alanlar(iddia, yer, IDDIA_ALANLARI, zorunlu):
            continue
        metin = s.metin(iddia.get("text"), f"{yer}.text", 1, 240, zorunlu="text" in iddia)
        alinti = s.metin(iddia.get("quote"), f"{yer}.quote", 1, 300, zorunlu="quote" in iddia)
        adres = s.adres(iddia["source_url"], f"{yer}.source_url") if "source_url" in iddia else None
        if adres:
            adresler.append(adres)
        if metin and _TURKCE.search(metin):
            d.uyar("sema.dil_kuskusu", f"{yer}.text Türkçeye özgü harf içeriyor.")
        if "checked_on" in iddia:
            gun = iddia["checked_on"]
            try:
                tarih = date.fromisoformat(gun) if isinstance(gun, str) and _GUN.match(gun) else None
            except ValueError:
                tarih = None
            if tarih is None:
                d.kal("sema.tarih_bicimi", f"{yer}.checked_on YYYY-AA-GG olmalı.")
            elif tarih > bugun + timedelta(days=1):
                d.kal("sema.gelecek_tarih", f"{yer}.checked_on bugünden sonra.")
            elif tarih < bugun - timedelta(days=365):
                d.uyar("sema.eski_tarih", f"{yer}.checked_on bir yıldan eski.")
        if "claim_type" in iddia and iddia["claim_type"] not in IDDIA_TURLERI:
            d.kal("sema.iddia_turu", f"{yer}.claim_type tanınmıyor.")
        if "applies_to" in iddia:
            s.kapsam(iddia["applies_to"], f"{yer}.applies_to")
        if alinti and adres:
            imza = (" ".join(alinti.split()), adres)
            if imza in gorulen:
                d.uyar("sema.yinelenen_iddia", f"{yer} başka bir iddiayla aynı alıntı ve kaynağı taşıyor.")
            gorulen.add(imza)
    if kart and len(set(adresler)) > 1:
        d.kal("sema.kart_tek_kaynak", "Bir kaynak kartı tek kaynak adresine bağlıdır; birden çok kaynak rapor türüyle gönderilmeli.")
    if not kart and isinstance(paket.get("sources"), list):
        # Ş-1: url metin değilse (liste, sözlük) yukarıda sema.tip ile kaldı; burada çökmesin.
        listede = {k.get("url") for k in paket["sources"] if isinstance(k, dict) and isinstance(k.get("url"), str)}
        for adres in dict.fromkeys(adresler):
            if adres not in listede:
                d.uyar("sema.kaynak_listede_yok", "Bir iddianın kaynak adresi paket.sources listesinde yok.")
    for alan in ("title", "summary", "short_answer"):
        if isinstance(paket.get(alan), str) and _TURKCE.search(paket[alan]):
            d.uyar("sema.dil_kuskusu", f"paket.{alan} Türkçeye özgü harf içeriyor.")
    return d
