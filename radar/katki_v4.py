"""K1 v4 katkı sözleşmesi: ayarlar, aday girdi ve rıza denetimi, kapıdan geçen adaydan kamu kaydı ve kaldırma izi.

Sözleşme `k1-v4-r2` (K1-V4-SOZLESME-TASLAGI-r2.md). Tek yayın kapısı `radar.denetim` kütüphanesidir (KD-022): kaynak
kartı, araştırma raporu ve konu özeti aynı kapıdan geçer; alıntı eşleşmesi (`quote_match`) kapının sonucundan gelir.
Otomatik yayın ayarla açılır ve varsayılan kapalıdır; kapalıyken kapıdan geçen kayıt moderasyon kuyruğuna gider.
"""

import hashlib
import json
import re
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from pathlib import Path

from radar.denetim import metin as denetim_metni
from radar.denetim.kapi import KANITLAMADIKLARI

SOZLESME = "k1-v4-r2"
KAYIT_SEMASI = "devtrellis-record"
IZ_SEMASI = "devtrellis-record-tombstone"
YETENEK_SEMASI = "devtrellis-capabilities"
AYAR_SEMASI = "devtrellis-k1-settings"
YAYIN_POLITIKASI = "k1-publication-gates-v1"
# Yöneticinin sözleşme kararı 3: tek alıntı kuralı kapının kuralıdır. Kural v1'den beri aynı; kapının görünür metin
# çıkarıcısının sürümü ayrı alanda (`visible_text_version`) yazılır.
ALINTI_KURALI = "gorunur-metin-bosluk-tirnak-tire-v1"
# Karar 4: alıntı dayanağı kaynak bağlantılı kısa alıntıdır; ayrı hak kapısı yoktur, kaldırma talebi moderasyondadır.
ALINTI_DAYANAGI = "source_linked_short_quote"
ALINTI_EN_COK = 300
LISANSLAR = ("CC0-1.0", "CC-BY-4.0")
TURLER = ("source_card", "research_report", "topic_summary")
ICERIK_ALANLARI = ("title", "summary", "short_answer", "answers", "recommendations", "not_covered", "applies_to",
                   "sources", "generated_at")
_OZET = re.compile(r"[0-9a-f]{64}")


class KatkiHatasi(ValueError):
    """Katkıcıya dönen sabit hata; özel metni ya da girdiyi geri yansıtmaz."""

    def __init__(self, message, status=422, code="invalid_request", gate=None, retry_after=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.gate = gate
        self.retry_after = retry_after

    def basliklar(self):
        basliklar = {"Cache-Control": "no-store"}
        if self.retry_after:
            basliklar["Retry-After"] = str(self.retry_after)
        return basliklar

    def govde(self):
        govde = {"detail": str(self), "code": self.code}
        if self.gate is not None:
            govde["gate"] = self.gate
        return govde


@dataclass(frozen=True)
class K1Ayarlari:
    """Varsayılanlar güvenli yöndedir: otomatik yayın kapalı. Değerler ölçümle değiştirilecek başlangıçlardır."""
    otomatik_yayin: bool = False           # KD-022 yürürlüğe girene kadar kapalı kalır
    yayin_durduruldu: bool = False         # sunucu yapılandırmasındaki durdurma anahtarı
    kisi_dakika: int = 2                   # aynı anonim ağ kimliğinden dakikada gönderim
    kisi_gun: int = 10                     # aynı anonim ağ kimliğinden günde gönderim
    tum_saat: int = 120                    # bütün katkıcılardan saatte gönderim
    otomatik_yayin_gun: int = 50           # son 24 saatte otomatik yayın üst sınırı; aşılınca moderasyon
    bekleyen_en_cok: int = 500             # denetim sırasındaki (ve yeniden deneme bekleyen) kayıt üst sınırı
    moderasyon_en_cok: int = 300           # moderasyon kuyruğu doluysa yeni gönderim alınmaz
    deneme_en_cok: int = 5                 # `yayimlanamaz` için toplam deneme
    deneme_ilk_saniye: int = 600           # ilk yeniden deneme beklemesi; her denemede iki katı
    deneme_en_uzun_saniye: int = 21600     # bekleme üst sınırı
    denetim_agi: bool = True               # kapı kaynakları getirir; kapalıyken hüküm en iyi ihtimal `yayimlanamaz`
    isleme_araligi_saniye: int = 30        # sunucudaki arka plan işçisinin sırayı yoklama aralığı
    ozel_metin_saklama_gun: int = 30       # yayımlanmadan sonlanan gönderimin özel aday metni bu süre sonra silinir


# Ayar dosyasındaki ad → (alan, en az, en çok); boolean alanlarda sınır yok.
_AYAR_ADLARI = {
    "auto_publish": ("otomatik_yayin", None, None),
    "publishing_stopped": ("yayin_durduruldu", None, None),
    "gate_network": ("denetim_agi", None, None),
    "worker_interval_seconds": ("isleme_araligi_saniye", 5, 3600),
    "private_retention_days": ("ozel_metin_saklama_gun", 1, 3650),
}
_SINIR_ADLARI = {
    "per_identity_per_minute": ("kisi_dakika", 1, 60),
    "per_identity_per_day": ("kisi_gun", 1, 1000),
    "all_per_hour": ("tum_saat", 1, 100000),
    "auto_publish_per_day": ("otomatik_yayin_gun", 0, 100000),
    "queue_max": ("bekleyen_en_cok", 1, 100000),
    "review_queue_max": ("moderasyon_en_cok", 1, 100000),
}
_DENEME_ADLARI = {
    "max_attempts": ("deneme_en_cok", 1, 20),
    "first_delay_seconds": ("deneme_ilk_saniye", 1, 86400),
    "max_delay_seconds": ("deneme_en_uzun_saniye", 1, 7 * 86400),
}


def ayarlari_oku(path):
    """Kapalı alanlı JSON ayar dosyası; bilinmeyen ya da yanlış türde alan başlatmayı durdurur."""
    try:
        veri = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError("K1 ayar dosyası okunamadı ya da geçerli JSON değil.") from None
    return ayarlari_coz(veri)


def ayarlari_coz(veri):
    if not isinstance(veri, dict) or veri.get("schema") != AYAR_SEMASI or veri.get("version") != 1:
        raise ValueError(f"K1 ayar dosyası schema={AYAR_SEMASI} ve version=1 taşımalı.")
    degerler = {}

    def bolum(nesne, adlar, yer):
        if not isinstance(nesne, dict):
            raise ValueError(f"K1 ayarı {yer} bir nesne olmalı.")
        for ad, deger in nesne.items():
            if ad not in adlar:
                raise ValueError(f"K1 ayarında tanınmayan alan: {yer}.{ad}" if re.fullmatch(r"[a-z_]{1,40}", str(ad))
                                 else f"K1 ayarında tanınmayan alan ({yer}).")
            alan, en_az, en_cok = adlar[ad]
            if en_az is None:
                if type(deger) is not bool:
                    raise ValueError(f"K1 ayarı {yer}.{ad} true ya da false olmalı.")
            elif type(deger) is not int or not en_az <= deger <= en_cok:
                raise ValueError(f"K1 ayarı {yer}.{ad} {en_az}–{en_cok} arasında tam sayı olmalı.")
            degerler[alan] = deger

    ust = {ad: deger for ad, deger in veri.items() if ad not in ("schema", "version", "limits", "retry")}
    bolum(ust, _AYAR_ADLARI, "kök")
    bolum(veri.get("limits", {}), _SINIR_ADLARI, "limits")
    bolum(veri.get("retry", {}), _DENEME_ADLARI, "retry")
    ayarlar = K1Ayarlari(**degerler)
    if ayarlar.deneme_ilk_saniye > ayarlar.deneme_en_uzun_saniye:
        raise ValueError("K1 ayarı retry.first_delay_seconds, retry.max_delay_seconds değerini aşamaz.")
    return ayarlar


def ayar_ornegi():
    """Varsayılanları taşıyan örnek ayar dosyası içeriği (belge ve sınama için)."""
    varsayilan = K1Ayarlari()
    oku = lambda adlar: {ad: getattr(varsayilan, alan) for ad, (alan, _, _) in adlar.items()}
    return {"schema": AYAR_SEMASI, "version": 1, **oku(_AYAR_ADLARI), "limits": oku(_SINIR_ADLARI),
            "retry": oku(_DENEME_ADLARI)}


assert {alan.name for alan in fields(K1Ayarlari)} == {a for adlar in (_AYAR_ADLARI, _SINIR_ADLARI, _DENEME_ADLARI)
                                                      for a, _, _ in adlar.values()}


def kanonik_ozet(deger):
    """K0 ve kapıyla aynı kanonik JSON özeti; katkıcının önizlediği paketin parmak izi."""
    return hashlib.sha256(json.dumps(deger, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                                     allow_nan=False).encode("utf-8")).hexdigest()


def zaman_metni(saniye):
    return datetime.fromtimestamp(saniye, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def aday_on_denetimi(aday):
    """Kapıdan önce yalnız sunucunun ek kuralları: tür ve hak seçimi. Biçimin geri kalanını kapının şeması denetler."""
    if not isinstance(aday, dict) or aday.get("kind") not in TURLER:
        raise KatkiHatasi("Aday kind source_card, research_report ya da topic_summary olmalı.", code="invalid_candidate")
    hak = aday.get("rights")
    # Karar 4: rıza ayrı alanda (Paylaş seçimi); hak alanında yalnız katkıcının kendi metni için açık lisans seçimi.
    if not isinstance(hak, dict) or set(hak) != {"own_text_license"} or hak["own_text_license"] not in LISANSLAR:
        raise KatkiHatasi("rights yalnız own_text_license taşımalı: CC0-1.0 ya da CC-BY-4.0 (katkıcının açık seçimi).",
                          code="rights_choice_required")
    return aday


def riza_denetimi(riza, aday):
    """Karar 4: rıza, katkıcının önizlenen aynı pakette açık 'Paylaş' seçimidir; özet uyuşmazsa yeniden önizleme gerekir."""
    if not isinstance(riza, dict) or set(riza) != {"choice", "preview_sha256"}:
        raise KatkiHatasi("consent yalnız choice ve preview_sha256 taşımalı.", code="consent_required")
    if riza["choice"] != "share":
        raise KatkiHatasi("Yalnız açık 'share' seçimi gönderilir; 'Yerelde tut' seçiminde gönderim yapılmaz.",
                          code="consent_required")
    if not isinstance(riza["preview_sha256"], str) or not _OZET.fullmatch(riza["preview_sha256"]):
        raise KatkiHatasi("consent.preview_sha256 64 küçük onaltılık karakter olmalı.", code="consent_required")
    ozet = kanonik_ozet(aday)
    if riza["preview_sha256"] != ozet:
        raise KatkiHatasi("Gönderilen paket önizlenen paketle aynı değil; yeniden önizleyip seçin.", 409,
                          code="preview_mismatch")
    return ozet


def kapi_ozeti(sonuc, *, tam=False):
    """Katkıcıya dönen kapı özeti: hüküm, neden kodları ve denetimlerin açıklamaları. Ağ istekleri ve kaynak parçaları yok."""
    ozet = {"verdict": sonuc["hukum"], "gate_version": sonuc["denetim_surumu"], "ran_at": sonuc["calisma_zamani"],
            "reasons": list(sonuc["ret_nedenleri"]), "uncheckable": list(sonuc["denenemeyenler"]),
            "warnings": list(sonuc["uyarilar"])}
    if tam:
        ozet["checks"] = [{"name": d["ad"], "status": d["durum"], "reasons": d["nedenler"], "explanation_tr": d["aciklama"]}
                          for d in sonuc["denetimler"]]
    return ozet


def _alinti_satirlari(sonuc):
    for denetim in sonuc["denetimler"]:
        if denetim["ad"] == "alinti_eslesmesi":
            return {satir["iddia"]: satir for satir in denetim["ayrinti"] if "iddia" in satir}, next(
                (satir["normallestirme"] for satir in denetim["ayrinti"] if "normallestirme" in satir), None)
    return {}, None


def _adres_anahtari(url):
    from radar.kaynak_kimligi import KaynakKimligiHatasi, address_key
    try:
        return address_key(url, "en")
    except KaynakKimligiHatasi:
        return None


def etiket(sonuc, yol):
    """Kanıt etiketi kapının ürettiği metindir. Moderatör yolunda ilk parça yayını kimin açtığını doğru söyler."""
    tr, en = sonuc["kanit_etiketi"], sonuc["kanit_etiketi_en"]
    if yol == "moderator":
        tr = "Moderatör yayımladı, iddialar tek tek doğrulanmadı; " + tr.removeprefix("İnsan incelemedi; ")
        en = "Published by a moderator, claims not individually verified; " + en.removeprefix("Not human-reviewed; ")
    return {"tr": tr, "en": en}


def kayit_olustur(aday, sonuc, *, kayit_id, surum, yol, yayin_saniye, origin):
    """Kapıdan `yayimlanir` almış adaydan kamu kaydı. Sunucu alanları istemciden gelmez; hepsi burada üretilir."""
    if sonuc["hukum"] != "yayimlanir" or yol not in ("machine_gate", "moderator"):
        raise ValueError("Yalnız kapıdan geçen kayıt yayımlanır.")
    satirlar, gorunur_surum = _alinti_satirlari(sonuc)
    iddialar = []
    for sira, iddia in enumerate(aday["claims"]):
        aynen = satirlar.get(sira, {}).get("eslesme") == "aynen"
        if not aynen:
            raise ValueError("Kapıdan geçen kayıtta her alıntı aynen bulunmuş olmalı.")
        iddialar.append({
            "id": hashlib.sha256(f"{kayit_id}:{surum}:{sira}".encode("ascii")).hexdigest()[:32],
            "text": iddia["text"],
            "claim_type": iddia.get("claim_type"),
            "applies_to": iddia.get("applies_to"),
            "evidence": {
                "quote": iddia["quote"],
                "source_url": iddia["source_url"],
                "address_key": _adres_anahtari(iddia["source_url"]),
                "quote_hash": hashlib.sha256(denetim_metni.normallestir(iddia["quote"]).encode("utf-8")).hexdigest(),
                "normalization_version": ALINTI_KURALI,
                "visible_text_version": gorunur_surum,
                "checked_on": iddia["checked_on"],
                # K0 bölüm bağı ve destekleyen bağlam özeti bu dilimde üretilmez (K0 extract_document bağlanınca).
                "section_id": None,
                "section_hash": None,
                "supporting_context_hash": None,
            },
            "quote_match": {"state": "exact", "quote_verified_at": sonuc["calisma_zamani"]},
            "claim_review": {"state": "not_reviewed", "reviewed_at": None},
            "evidence_state": "current",
        })
    return {
        "schema": KAYIT_SEMASI,
        "schema_version": 4,
        "contract": SOZLESME,
        "kind": aday["kind"],
        "id": kayit_id,
        "version": surum,
        "language": "en",
        "content": {alan: aday[alan] for alan in ICERIK_ALANLARI if alan in aday},
        "claims": iddialar,
        "provenance": aday["provenance"],
        "rights": {"own_text_license": aday["rights"]["own_text_license"],
                   "quotes": {"basis": ALINTI_DAYANAGI, "max_chars": ALINTI_EN_COK}},
        "publication": {
            "state": "published",
            "path": yol,
            "policy_version": YAYIN_POLITIKASI,
            "published_at": zaman_metni(yayin_saniye),
            "human_reviewed": yol == "moderator",
            "gate": {"version": sonuc["denetim_surumu"], "ran_at": sonuc["calisma_zamani"],
                     "input_sha256": sonuc["girdi"]["paket_sha256"], "checks_passed": sonuc["gecilen_denetimler"],
                     "warnings": sonuc["uyarilar"], "fields_not_tied_to_sources": sonuc["kaynaga_baglanmayan_alanlar"]},
            "label": etiket(sonuc, yol),
        },
        "corrections": {"open": 0, "latest_public_version": None, "public_note": None},
        "link": f"{origin}/api/community/v4/records/{kayit_id}/v/{surum}",
    }


def kaldirma_izi(kayit_id, surum, neden, saniye):
    """Önceden yayımlanmış kayıt için asgari iz: iddia, alıntı ve gövde yok (sözleşme §6, §8)."""
    return {"schema": IZ_SEMASI, "schema_version": 4, "contract": SOZLESME, "id": kayit_id, "version": surum,
            "state": neden, "removed_at": zaman_metni(saniye)}


def yetenekler(otomatik_yayin_ayari):
    """İstemcinin ön görüşmesi. Adresle bulma her tür için (K1 ikinci dilim); kaynak kontrol ucu bu dilimde yok."""
    return {"schema": YETENEK_SEMASI, "schema_version": 1, "contract": SOZLESME, "record_schema_version": 4,
            "kinds": list(TURLER), "submissions": True, "auto_publish": otomatik_yayin_ayari,
            "quote_rule": ALINTI_KURALI, "quote_basis": ALINTI_DAYANAGI, "own_text_licenses": list(LISANSLAR),
            "lookup": True, "coverage": True, "topics": True, "source_checks": False, "does_not_prove_tr": KANITLAMADIKLARI}


AJAN_SEMASI = "devtrellis-agent-view"


def ajan_gorunumu(kayit):
    """Mühendislik raporu K3: ajana giden yanıtta sunucunun ürettiği, katkıcının beyan ettiği ve güvenilmeyen metin ayrı nesnelerdir.

    `untrusted` katkıcıdan gelen serbest metin ve alıntılardır; `server.content_sha256` onun kanonik özetidir. Veri alanlarına
    ajana yönelik buyruk konmaz; yapı yalnız betimler.
    """
    yayin, icerik = kayit["publication"], kayit["content"]
    guvenilmeyen = {"content": {alan: deger for alan, deger in icerik.items() if alan != "generated_at"},
                    "claims": [{"claim_id": i["id"], "text": i["text"], "quote": i["evidence"]["quote"],
                                "source_url": i["evidence"]["source_url"]} for i in kayit["claims"]]}
    return {
        "schema": AJAN_SEMASI, "schema_version": 1, "contract": SOZLESME,
        "server": {
            "id": kayit["id"], "kind": kayit["kind"], "version": kayit["version"], "language": kayit["language"],
            "content_sha256": kanonik_ozet(guvenilmeyen), "published_at": yayin["published_at"], "path": yayin["path"],
            "human_reviewed": yayin["human_reviewed"], "gate_version": yayin["gate"]["version"],
            "gate_ran_at": yayin["gate"]["ran_at"], "checks_passed": yayin["gate"]["checks_passed"],
            "warnings": yayin["gate"]["warnings"], "fields_not_tied_to_sources": yayin["gate"]["fields_not_tied_to_sources"],
            "label": yayin["label"], "quote_rights": kayit["rights"]["quotes"], "corrections": kayit["corrections"],
            "link": kayit["link"],
            "quote_matches": [{"claim_id": i["id"], "state": i["quote_match"]["state"],
                               "quote_verified_at": i["quote_match"]["quote_verified_at"],
                               "quote_hash": i["evidence"]["quote_hash"], "address_key": i["evidence"]["address_key"],
                               "normalization_version": i["evidence"]["normalization_version"]} for i in kayit["claims"]],
        },
        "declared": {
            "provenance": kayit["provenance"], "generated_at": icerik.get("generated_at"),
            "own_text_license": kayit["rights"]["own_text_license"],
            "claims": [{"claim_id": i["id"], "checked_on": i["evidence"]["checked_on"], "claim_type": i["claim_type"],
                        "applies_to": i["applies_to"]} for i in kayit["claims"]],
        },
        "untrusted": guvenilmeyen,
    }


def ajan_metni(kayit, sinir=None):
    """Metin temsili: güvenilmeyen gövde yanıt başına rastgele sınır işaretleri arasında, JSON kaçışlı (satır sonu taşıyamaz)."""
    import secrets
    gorunum = ajan_gorunumu(kayit)
    govde = json.dumps(gorunum["untrusted"], ensure_ascii=False, indent=2, sort_keys=True)
    sinir = sinir or secrets.token_hex(16)
    while sinir in govde:
        sinir = secrets.token_hex(16)
    tek = lambda deger: json.dumps(deger, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "\n".join([
        f"DevTrellis record {gorunum['server']['id']} v{gorunum['server']['version']} ({gorunum['server']['kind']})",
        "server: " + tek(gorunum["server"]),
        "declared: " + tek(gorunum["declared"]),
        f"untrusted-boundary: {sinir}",
        f"BEGIN UNTRUSTED {sinir}",
        govde,
        f"END UNTRUSTED {sinir}",
        "",
    ])


# ---------------------------------------------------------------------- adresle bulma, kapsam ve konu listesi (K2 için)
KAPSAM_KIMLIGI = "k0-canonical-json-v1"
KAPSAM_SURESI = 300          # r1 §7 başlangıç önerisi
_URUN = re.compile(r"[a-z][a-z0-9-]{0,63}")
_SURUM_METNI = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+_-]{0,39}")
UYGUN_ESLESMELER = ("exact", "unspecified")


def sozlesme_hatasi(kod, retry=None):
    """r1 `lookup-coverage.schema.json` hata gövdesi; girdiyi geri yansıtmaz."""
    return {"schema": "devtrellis-contract-error", "schema_version": 1, "code": kod, "retry_after_seconds": retry}


def bakis_parametreleri(sorgu):
    """Bulma ucu yalnız `product` (ürün kısa adı) ve `version` (kısa sürüm metni) alır; başka parametre geçersiz istektir."""
    ciftler = list(sorgu)
    if len(ciftler) != len({ad for ad, _ in ciftler}) or any(ad not in ("product", "version") for ad, _ in ciftler):
        return None
    degerler = dict(ciftler)
    if ("product" in degerler and not _URUN.fullmatch(degerler["product"])) or (
            "version" in degerler and not _SURUM_METNI.fullmatch(degerler["version"])):
        return None
    return degerler.get("product"), degerler.get("version")


def kaynak_anahtari(url):
    """K0 `source_key`: kapının ve K0 izlemenin kullandığı aynı anonim istek varyantı (r1 §2)."""
    from radar.denetim.ayarlar import KABUL, KULLANICI_ARACI
    from radar.kaynak_kimligi import EXTRACTOR_VERSION, REPRESENTATION, KaynakKimligiHatasi, source_key
    varyant = {"accept": KABUL, "accept-language": "en", "accept-encoding": "identity", "user-agent": KULLANICI_ARACI}
    try:
        return source_key(url, "en", representation=REPRESENTATION, request_variant=varyant,
                          extractor_version=EXTRACTOR_VERSION)
    except KaynakKimligiHatasi:
        return None


def kayit_adresleri(kayit):
    """Kaydın andığı kanonik adreslerin anahtarları, ilk geçiş sırasıyla ve yinelenmeden (K1 ikinci dilim).

    Her tür için: iddiaların `evidence.address_key` değerleri, sonra raporun `content.sources[]` adresleri (iddiasız kaynak
    da adres dizinine girer). Bir kayıt birden çok adres anabilir; adresle bulma her birinde kaydı verir.
    """
    anahtarlar = []
    for iddia in kayit.get("claims", []):
        anahtar = (iddia.get("evidence") or {}).get("address_key")
        if anahtar and anahtar not in anahtarlar:
            anahtarlar.append(anahtar)
    for kaynak in kayit.get("content", {}).get("sources") or []:
        anahtar = _adres_anahtari(kaynak.get("url")) if isinstance(kaynak, dict) else None
        if anahtar and anahtar not in anahtarlar:
            anahtarlar.append(anahtar)
    return anahtarlar


def adres_kaynak_adresi(kayit, address_key):
    """Verilen adres anahtarını anan ilk iddianın (yoksa `sources[]` girdisinin) kaynak adresi."""
    for iddia in kayit.get("claims", []):
        if (iddia.get("evidence") or {}).get("address_key") == address_key:
            return iddia["evidence"]["source_url"]
    for kaynak in kayit.get("content", {}).get("sources") or []:
        if isinstance(kaynak, dict) and _adres_anahtari(kaynak.get("url")) == address_key:
            return kaynak["url"]
    return None


def kapsam_listesi(kayit):
    """Kaydın ürün/sürüm kapsamı: iddiaların (ve raporun liste biçimli) `applies_to` öğeleri, yinelenmeden."""
    ogeler = []
    for iddia in kayit["claims"]:
        ogeler.extend(iddia.get("applies_to") or [])
    if isinstance(kayit["content"].get("applies_to"), list):
        ogeler.extend(kayit["content"]["applies_to"])
    sonuc, gorulen = [], set()
    for oge in ogeler:
        imza = json.dumps(oge, sort_keys=True)
        if imza not in gorulen:
            gorulen.add(imza)
            sonuc.append(dict(oge))
    return sonuc


def surum_eslesmesi(kayit, urun, surum):
    """Basit ve güvenli yön: ürün süzülür; sürüm yalnız aynı metinse `exact`. Aralık çözümlemesi yok (`in_range` üretilmez).

    Yapılı kapsamı olmayan kayıt (serbest metin `applies_to`; canlıdaki iki GitHub raporu) ürün ya da sürüm beyan etmez:
    sonuç `unspecified` (alpha.12 kabulü, orta bulgu 1). Eskiden ürün/sürüm verilince her zaman `mismatch` çıkıyor ve kayıt
    adresle bulmada gizleniyordu. Sözleşme r2 sürüm eşleşmesini yalnız yapılı öğeler üzerinden tanımlar; `topics` için çıkarılan
    ürün bir tahmindir, beyan sayılmaz ve sürüm taşımaz.
    """
    ogeler = kapsam_listesi(kayit)
    if not ogeler:
        return "unspecified"
    if urun is not None:
        ogeler = [oge for oge in ogeler if oge["product"] == urun]
        if not ogeler:
            return "mismatch"
    if surum is None:
        return "unspecified"
    return "exact" if any(oge["versions"].lower() == surum.lower() for oge in ogeler) else "mismatch"


def kart_referansi(kayit):
    return {"id": kayit["id"], "version": kayit["version"]}


def bakis_govdesi(address_key, revizyon, kayitlar, izler, urun, surum):
    """(HTTP durumu, `devtrellis-source-lookup` gövdesi). Kaynak sunucuda açılmaz, model çağrılmaz."""
    # Yönetici kararı (7 Kasım 23:5x): kart r2 ajan görünümüdür; tazelik kartın değil yanıtın kökündedir ve K0 bağlanana
    # kadar bilinmez. `answer_eligible` yalnız sürüm/kapsam/geri çekme uygunluğudur, tazelikten bağımsızdır.
    govde = {"schema": "devtrellis-source-lookup", "schema_version": 1, "address_key": address_key,
             "library_revision": revizyon, "result": "not_found", "answer_eligible": False,
             "version_match": "not_applicable", "cards": [], "candidates": [], "origin_fetches": 0, "model_calls": 0,
             "tombstone": None, "freshness": {"state": "unknown", "checked_at": None}}
    if not kayitlar:
        if izler:
            iz = izler[0]
            govde.update(result="withdrawn", tombstone={"card": {"id": iz["id"], "version": iz["version"]},
                                                       "withdrawn_at": iz["removed_at"], "public_reason": "withdrawn",
                                                       "library_revision": revizyon})
            return 410, govde
        return 404, govde
    eslesmeler = [(kayit, surum_eslesmesi(kayit, urun, surum)) for kayit in kayitlar]
    uygun = [(kayit, eslesme) for kayit, eslesme in eslesmeler if eslesme in UYGUN_ESLESMELER]
    secilen = uygun[0] if uygun else None

    def aday(kayit, eslesme):
        # Kaynak anahtarı bu adresi anan iddianın adresinden; rapor birden çok adres anar, ilk iddia başka sayfa olabilir.
        kaynak = adres_kaynak_adresi(kayit, address_key)
        return {"card": kart_referansi(kayit), "applies_to": kapsam_listesi(kayit)[:3], "version_match": eslesme,
                "source_key": kaynak_anahtari(kaynak) if kaynak else None}
    adaylar = [aday(k, e) for k, e in eslesmeler if secilen is None or k is not secilen[0]]
    govde["candidates"] = [a for a in adaylar if a["applies_to"] and a["source_key"]][:3]
    if secilen:
        govde.update(result="found", answer_eligible=True, version_match=secilen[1], cards=[ajan_gorunumu(secilen[0])])
    else:
        govde.update(result="version_mismatch", version_match="mismatch")
    return 200, govde


def kapsam_govdesi(revizyon, kayitlar, izler, simdi_saniye):
    """`devtrellis-coverage`: herkese aynı tam anahtar listesi; sorgu, kişisel ilgi ya da istek bilgisi yok."""
    adresler = {}
    for kayit in kayitlar:
        # Her tür, andığı her adreste listelenir (K1 ikinci dilim); kaynak anahtarı o adresi anan iddiadan.
        for anahtar in kayit_adresleri(kayit):
            kaynak_adresi = adres_kaynak_adresi(kayit, anahtar)
            kaynak = kaynak_anahtari(kaynak_adresi) if kaynak_adresi else None
            if kaynak:
                # Tazelik izlenmediği için kullanılabilirlik bilinmiyor; pozitif kayıt kartı taze göstermez (r1 §7).
                adresler.setdefault(anahtar, []).append({"card": kart_referansi(kayit), "source_key": kaynak,
                                                         "availability": "unknown"})
    tam = all(len(liste) <= 100 for liste in adresler.values())
    adresler = {anahtar: liste[:100] for anahtar, liste in adresler.items()}
    iz_listesi = {}
    for iz in izler:   # en yeni önce; adres başına tek iz, yayında kart varsa iz yok
        if iz["address_key"] not in adresler and iz["address_key"] not in iz_listesi:
            iz_listesi[iz["address_key"]] = {"address_key": iz["address_key"], "card": {"id": iz["id"], "version": iz["version"]},
                                             "library_revision": revizyon}
    iz_listesi = list(iz_listesi.values())
    return {"schema": "devtrellis-coverage", "schema_version": 1, "identity_version": KAPSAM_KIMLIGI,
            "generated_at": zaman_metni(simdi_saniye), "valid_until": zaman_metni(simdi_saniye + KAPSAM_SURESI),
            "library_revision": revizyon, "complete": tam, "addresses": adresler, "tombstones": iz_listesi}


# Serbest metin `applies_to` çıkarımında ürün sayılmayan alan adı parçaları ve genel sözcükler.
_GENEL_ALAN_PARCALARI = frozenset("www docs doc developer developers dev api help support learn en tr com org net io co "
                                  "edu gov info github-io readthedocs html htm".split())
_KAPSAM_BAGLACLARI = frozenset("a an and or the of for to in on at by with as from is are was were be been its it this that "
                               "these those documented documentation document documents send sends per such "
                               "all any only also ve veya ile icin bir bu su olan olarak gibi".split())
_SOZCUK = re.compile(r"[A-Za-z][A-Za-z0-9.+_-]*")
# Kayıtlı alan adını (eTLD+1) bulmak için sık görülen iki parçalı genel ekler. Tam Public Suffix List değildir; listede
# olmayan iki parçalı ekte etiket genel parça olur ve `_GENEL_ALAN_PARCALARI` ya da metinle kesişim onu eler.
_IKI_PARCALI_EKLER = frozenset(
    "co.uk org.uk ac.uk gov.uk me.uk com.au net.au org.au edu.au gov.au com.tr org.tr net.tr gov.tr edu.tr bel.tr k12.tr "
    "co.jp ne.jp or.jp ac.jp co.kr or.kr com.br org.br com.cn org.cn com.mx co.in org.in co.nz org.nz co.za com.sg "
    "com.hk com.tw github.io gitlab.io readthedocs.io netlify.app vercel.app pages.dev web.app firebaseapp.com "
    "herokuapp.com blogspot.com appspot.com".split())


def kayitli_alan_etiketi(ad):
    """Kayıtlı alan adının (eTLD+1) ilk etiketi; alt alan parçaları sayılmaz (K1 ikinci dilim kabulü, düşük bulgu 3).

    `docs.github.com` → `github`, `blog.rust-lang.org` → `rust-lang`, `cloud.google.com` → `google`,
    `docs.python.org` → `python`, `tokio-rs.github.io` → `tokio-rs`. IP adresi, tek parçalı ad ve genel parça → None."""
    parcalar = [p for p in str(ad or "").lower().rstrip(".").split(".") if p]
    if len(parcalar) < 2 or all(p.isdigit() for p in parcalar):
        return None
    ek = 2 if ".".join(parcalar[-2:]) in _IKI_PARCALI_EKLER else 1
    if len(parcalar) <= ek:
        return None
    etiket = parcalar[-ek - 1]
    return None if etiket in _GENEL_ALAN_PARCALARI else etiket


def kapsam_cikarimi(kayit):
    """Serbest metin `applies_to` için makul çıkarım (K1 ikinci dilim): `products` = metinde geçen ve kaynak adreslerinin
    kayıtlı alan adının (eTLD+1) ilk etiketiyle aynı olan ya da tireli etiketin bir parçası olan sözcükler
    (`docs.github.com` + "GitHub" → `github`; `blog.rust-lang.org` + "Rust" → `rust`); `blog`, `cloud`, `developer` gibi alt
    alan parçaları ürün sayılmaz. `terms` = metnin anlamlı sözcükleri. Liste biçimli `applies_to` olan kayıtta çıkarım
    yapılmaz. Sorgulardan ya da okuma davranışından hiçbir şey türetilmez."""
    metin_ = kayit.get("content", {}).get("applies_to")
    if not isinstance(metin_, str) or not metin_.strip():
        return [], []
    sozcukler = []
    for ham in _SOZCUK.findall(metin_):
        sozcuk = ham.strip(".-_+").lower()
        if len(sozcuk) >= 3 and sozcuk not in _KAPSAM_BAGLACLARI and sozcuk not in sozcukler:
            sozcukler.append(sozcuk[:40])
    from urllib.parse import urlsplit
    etiketler, etiket_parcalari = set(), set()
    adresler = [k.get("url") for k in kayit.get("content", {}).get("sources") or [] if isinstance(k, dict)]
    adresler += [(i.get("evidence") or {}).get("source_url") for i in kayit.get("claims", [])]
    for adres in adresler:
        try:
            ad = urlsplit(adres).hostname or "" if isinstance(adres, str) else ""
        except ValueError:
            ad = ""
        etiket = kayitli_alan_etiketi(ad)
        if etiket:
            etiketler.add(etiket)
            etiket_parcalari.update(p for p in etiket.split("-") if len(p) >= 3 and p not in _GENEL_ALAN_PARCALARI)
    # Ürün adayları terimlerden geniştir (alpha.12, alpha.11 kabulü düşük bulgu 3): noktalı ad noktasız da denenir
    # ("Node.js" + nodejs.org → `nodejs`); iki harfli ad yalnız kayıtlı alan etiketinin kendisiyse sayılır ("Go" + go.dev).
    adaylar = []
    for ham in _SOZCUK.findall(metin_):
        sozcuk = ham.strip(".-_+").lower()
        for aday in (sozcuk, sozcuk.replace(".", "")):
            if aday not in adaylar:
                adaylar.append(aday)
    urunler = [a for a in adaylar if (a in etiketler or (len(a) >= 3 and a in etiket_parcalari))
               and a not in _KAPSAM_BAGLACLARI and _URUN.fullmatch(a)]
    return urunler[:5], sozcukler[:60]


def konu_govdesi(revizyon, kayitlar, simdi_saniye):
    """`devtrellis-topics` v1 (K2 önerisi): kayıt başına tür, ürün/sürüm ve kısa terimler. Terimler katkı anındaki
    `applies_to` alanından türetilir (liste biçiminde ürün adları; serbest metinde `kapsam_cikarimi`); arama sorgularından
    ya da okuma davranışından türetilmez."""
    tur = {"research_report": "research", "topic_summary": "topic_summary", "source_card": "source_card"}
    girdiler = []
    for kayit in kayitlar:
        urunler = {}
        for oge in kapsam_listesi(kayit):
            surumler = urunler.setdefault(oge["product"], [])
            if _SURUM_METNI.fullmatch(oge["versions"]) and oge["versions"] not in surumler and len(surumler) < 10:
                surumler.append(oge["versions"])
        terimler = []
        for urun in list(urunler)[:5]:
            for terim in (urun, *urun.split("-")):
                if terim and terim not in terimler and len(terimler) < 60:
                    terimler.append(terim[:40])
        if not urunler:
            cikarilan_urunler, cikarilan_terimler = kapsam_cikarimi(kayit)
            urunler = {u: [] for u in cikarilan_urunler}
            for terim in cikarilan_terimler:
                if terim not in terimler and len(terimler) < 60:
                    terimler.append(terim)
        girdiler.append({"card": kart_referansi(kayit), "kind": tur[kayit["kind"]],
                         "products": [{"product": u, "versions": v} for u, v in list(urunler.items())[:5]],
                         "terms": terimler})
    return {"schema": "devtrellis-topics", "schema_version": 1, "generated_at": zaman_metni(simdi_saniye),
            "valid_until": zaman_metni(simdi_saniye + KAPSAM_SURESI), "library_revision": revizyon, "complete": True,
            "entries": girdiler}


# ---------------------------------------------------------------------- arama görünümü (v1 `cards` ucuyla birleşim, K1 ikinci dilim)
ARAMA_YAZARI = "Hesapsız katkı (makine denetimi)"
_GOVDE_SINIRI = 12000      # v1 paket gövdesi sınırı (gelistirici_alani.kayit_dogrula); K2 köprüsü paketi aynı kuralla doğrular
_OZET_SINIRI = 600
_BASLIK_SINIRI = 160


def _kirp(metin_, sinir):
    metin_ = " ".join(str(metin_ or "").split())
    return metin_ if len(metin_) <= sinir else metin_[:sinir - 1].rstrip() + "…"


def _tarih_v1(zaman):
    """`2026-11-08T06:31:49Z` → v1 paketlerinin beklediği saat dilimli ISO metni."""
    return datetime.fromisoformat(zaman.replace("Z", "+00:00")).isoformat(timespec="seconds")


def arama_etiketleri(kayit):
    """Arama dizininin etiket alanı: liste biçimli kapsamın ürün adları, yoksa serbest metin kapsamından çıkarılanlar."""
    etiketler = [oge["product"] for oge in kapsam_listesi(kayit)]
    if not etiketler:
        etiketler = kapsam_cikarimi(kayit)[0]
    sonuc = []
    for etiket_ in etiketler:
        if etiket_ not in sonuc and len(sonuc) < 12:
            sonuc.append(etiket_[:40])
    return sonuc


def arama_karti(kayit):
    """Yayımlanmış v4 kaydının v1 `cards` listesiyle uyumlu kartı.

    Paket v1 `ufuk-share` biçimindedir (mevcut arayüz, K2 köprüsü ve özet görünümü değişmeden okur); kaydın türü, yayın yolu
    ve etiketi kartın kökünde ayrı alanlardadır: `record_type: "v4"`, `kind`, `human_reviewed`, `publication`, `link`.
    Gövde aynı kaynaktan türetilir: kısa cevap, iddialar (alıntılarıyla), öneriler ve `## Limits` altında kapsam dışı notlar
    (dizinde `limits` alanına düşer). Kayıt değişmez; bu yalnız bir görünümdür.
    """
    icerik, yayin = kayit["content"], kayit["publication"]
    iddialar = kayit["claims"]
    baslik = icerik.get("title") or (iddialar[0]["text"] if iddialar else "Kaynak kartı")
    ozet = icerik.get("summary") or " ".join(i["text"] for i in iddialar)
    bolumler = []
    if icerik.get("short_answer"):
        bolumler.append(str(icerik["short_answer"]).strip())
    if iddialar:
        bolumler.append("## Claims\n" + "\n".join(f'- {i["text"]} — "{i["evidence"]["quote"]}" ({i["evidence"]["source_url"]})'
                                                 for i in iddialar))
    if icerik.get("recommendations"):
        bolumler.append("## Recommendations\n" + "\n".join(f"- {satir}" for satir in icerik["recommendations"]))
    if icerik.get("not_covered"):
        bolumler.append("## Limits\n" + "\n".join(f"- {satir}" for satir in icerik["not_covered"]))
    govde = "\n\n".join(bolumler)
    if len(govde) > _GOVDE_SINIRI:
        govde = govde[:_GOVDE_SINIRI].rsplit("\n", 1)[0]
    kaynaklar = []
    for kaynak in (icerik.get("sources") or [{"title": i["evidence"]["source_url"], "url": i["evidence"]["source_url"]}
                                            for i in iddialar]):
        if isinstance(kaynak, dict) and kaynak.get("url") and all(kaynak["url"] != k["url"] for k in kaynaklar):
            kaynaklar.append({"title": _kirp(kaynak.get("title") or kaynak["url"], _BASLIK_SINIRI), "url": kaynak["url"]})
        if len(kaynaklar) == 10:
            break
    zaman = _tarih_v1(yayin["published_at"])
    paket = {"schema": "ufuk-share", "schema_version": 1, "kind": "research", "title": _kirp(baslik, _BASLIK_SINIRI),
             "summary": _kirp(ozet, _OZET_SINIRI), "body": govde, "url": "", "tags": arama_etiketleri(kayit),
             "sources": kaynaklar, "author": ARAMA_YAZARI, "license": kayit["rights"]["own_text_license"],
             "created_at": zaman, "updated_at": zaman}
    return {"id": kayit["id"], "owner": {"username": None, "display_name": ARAMA_YAZARI, "verified": False},
            "version": kayit["version"], "status": "published", "packet": paket, "created_at": zaman, "updated_at": zaman,
            "published_version": kayit["version"],
            "relationships": {"parents": [], "children": [], "children_total": 0},
            "engagement": {"web_views": 0, "mcp_reads": 0, "helpful": 0, "used_in_research": 0, "needs_update": 0,
                           "disputed": 0},
            "submission_type": "anonymous",
            "record_type": "v4", "kind": kayit["kind"], "human_reviewed": yayin["human_reviewed"],
            "publication": {"path": yayin["path"], "human_reviewed": yayin["human_reviewed"], "label": yayin["label"],
                            "policy_version": yayin["policy_version"], "gate_version": yayin["gate"]["version"]},
            "answers": list(icerik.get("answers") or []), "link": kayit["link"]}


def arama_belgesi(kart):
    """Dizin alanlarına giren belge: v1 paket + `answers` (K1 teslim notunda sonraki dilime bırakılan bağ)."""
    return {**kart["packet"], "answers": kart.get("answers") or []}


def arama_metni_v1(kart):
    """v1 katı aramanın (`search_mode=all`, `instr`) taradığı düz metin; v1 ile aynı birleşim sırası."""
    paket = kart["packet"]
    return " ".join([paket["title"], paket["summary"], paket["body"], *paket["tags"], *(kart.get("answers") or []),
                     paket["author"]])
