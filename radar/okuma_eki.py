"""alpha.15 (14r2) okuma eki: kayıt sözleşmesinin isteğe bağlı üç parçası için ortak, saf kurallar.

Kapı (`radar.denetim`), sunucu (`radar.katki_v4`) ve köprü (`mcp_topluluk.py`) aynı tanımı kullanır; bu modül ağa, depoya ya da
HTML ayrıştırıcıya dokunmaz.

- **Yokluk notları** (`not_in_sources`, `not_researched`): eski `not_covered` iki farklı şeyi karıştırıyordu: "katkıcı atıf yapılan
  sayfalarda aradı, yok" ile "hiç bakılmadı". `not_in_sources` maddesi `{"item": metin, "checked": [kaynak no]}` biçimindedir;
  `not_researched` maddesi düz metindir. İkisi de katkıcının beyanıdır; DevTrellis yokluğu doğrulamaz.
- **Cevap bağları** (`answers`): madde düz metin (eski biçim, bağsız) ya da `{"q": soru, "claims": [iddia no]}` (bu iddiaların
  alıntıları soruyu karşılar) ya da `{"q": soru, "inferred_from": [iddia no]}` (yalnız çıkarımla; liste boş olabilir).
- **Terimler:** kısa cevaptaki ve bağlı cevaptaki sayı, sürüm ve kod adlarının alıntılarda geçip geçmediğine bakan ucuz denetim
  (uyarı; ret değil).

Numaralar 1'den başlar ve köprünün gösterdiği numaralarla aynıdır: iddia `[n]` kayıttaki sırasıdır; kaynak `Sn` önce `sources`
listesinin adresleri (sıra korunur, yinelenen düşer), sonra listede olmayan iddia adresleri (ilk geçiş sırasıyla).
"""

from __future__ import annotations

import re
import unicodedata

YOKLUK_ALANLARI = ("not_in_sources", "not_researched")
CEVAP_BAG_ALANLARI = ("claims", "inferred_from")
EN_COK_BAG = 10            # bir cevap maddesinin andığı en çok iddia
# Kart ve rapor sınırları eski `not_covered` ile aynı: kart 3 madde × 80 karakter, rapor 10 madde × 240 karakter.
YOKLUK_SINIRI = {"source_card": (3, 80), "report": (10, 240)}

# Kod biçimli parça: harf/rakam/alt çizgi ile başlayan, içinde . / + - { } olabilen dizi; ya da `-X`, `--disable-gil` seçeneği.
_PARCA = re.compile(r"--?[A-Za-z][A-Za-z0-9-]*|[A-Za-z0-9_/{}][A-Za-z0-9_./+{}-]*")
_SURUM = re.compile(r"\d+(?:\.\d+)+")
_SAYI = re.compile(r"\d{2,}")
_SECENEK = re.compile(r"--?[A-Za-z][A-Za-z0-9-]*")
# Yalnız ters tırnak ve çift tırnak (düz ya da tipografik). Düzeltme turu 2 (kabul Y1): tek tırnak çıkarıldı; düzyazıda
# vurgu için kullanılır ('latest' → ilgisiz metinde yanlış uyarı) ve kesme işaretiyle karışır (`DuckDB's … Polars' GPU`).
_TIRNAKLI = re.compile(r"`([^`]{2,60})`|\"([^\"]{2,60})\"|“([^”]{2,60})”")


def _kod_mu(parca):
    if _SECENEK.fullmatch(parca):
        return True
    if _SURUM.fullmatch(parca) or _SAYI.fullmatch(parca):
        return True
    harf = any(c.isalpha() for c in parca)
    rakam = any(c.isdigit() for c in parca)
    if not harf:
        return False
    if rakam or "_" in parca:
        return True
    if parca.startswith("/") and len(parca) > 1:
        return True                                    # yol: /repos/{owner}/{repo}/releases
    if "." in parca and all(len(p) >= 2 for p in parca.split(".")):
        return True                                    # noktalı ad: os.path, Node.js
    govde = parca.replace("-", "")
    if any(a.islower() and b.isupper() for a, b in zip(govde, govde[1:])):
        return True                                    # karışık büyük harf: getElementById, GitHub
    return govde.isupper() and len(govde) >= 4         # kısaltma: HMAC, WCAG (ETag gibi üç harfli değil)


def terimler(metin):
    """Metindeki sayı, sürüm ve kod biçimli adlar (sıralı, yinelenmeden). Düz sözcük (Python, page) terim değildir.

    Düzeltme turu 1 (kabul Ö5): yol olmayan birleşik yazım `/` yerinden bölünür (`v24.12.0/v25.2.0` → iki sürüm,
    `HTTP/3/QUIC` → `QUIC`); `/` ile başlayan ya da `{` taşıyan dizi yoldur ve bütün kalır (`/repos/{owner}/…`)."""
    if not isinstance(metin, str):
        return []
    sonuc = []
    for eslesme in _PARCA.finditer(metin):
        parca = eslesme.group()
        parcalar = [parca]
        if "/" in parca and not parca.startswith("/") and "{" not in parca:
            parcalar = [p for p in parca.split("/") if p]
        for parca in parcalar:
            if not parca.startswith("-"):
                parca = parca.rstrip(".-/+")
            # `=` ve tırnak ayırıcıdır (`PYTHON_GIL=0` → PYTHON_GIL, 0); tek rakam terim sayılmaz (çok gürültülü).
            if len(parca) >= 2 and _kod_mu(parca) and parca not in sonuc:
                sonuc.append(parca[:60])
    return sonuc


_SIKISTIR = re.compile(r"[\s_\-]+")
_SOZCUK_DISI = re.compile(r"[^0-9a-z]+")
# Düzeltme turu 2 (kabul Y1, Y5): eşleştirmeden önce iki yana da uygulanan katlama. Görünmeyen karakterler düşer (kapının
# sayfa metni normalleştirmesiyle aynı küme), NFKC (tam genişlik harf → olağan), sonra küçük harf ve Latin harfine benzeyen
# Kiril/Yunan harfleri Latin karşılığına iner (`\u0440er_page` → `per_page`). Yalnız karşılaştırma içindir; hiçbir metin
# katlanmış biçimiyle yazılmaz ya da gösterilmez.
_GORUNMEZ = re.compile("[\u00ad\u200b\u200c\u200d\u2060\ufeff]")
_BENZER_HARF = str.maketrans({
    "\u0430": "a", "\u0432": "b", "\u0435": "e", "\u0451": "e", "\u043a": "k", "\u043c": "m", "\u043d": "h", "\u043e": "o", "\u0440": "p", "\u0441": "c", "\u0442": "t",
    "\u0443": "y", "\u0445": "x", "\u0456": "i", "\u0457": "i", "\u0458": "j", "\u0455": "s", "\u04bb": "h", "\u0501": "d", "\u051b": "q", "\u051d": "w", "\u04cf": "l",
    "\u03b1": "a", "\u03b2": "b", "\u03b5": "e", "\u03b9": "i", "\u03ba": "k", "\u03bd": "v", "\u03bf": "o", "\u03c1": "p", "\u03c4": "t", "\u03c5": "u", "\u03c7": "x",
})


def katla(metin):
    """Karşılaştırma biçimi: görünmeyen karakter yok, NFKC, boşluk dizisi tek boşluk, küçük harf, benzer harf Latin."""
    metin = unicodedata.normalize("NFKC", _GORUNMEZ.sub("", str(metin)))
    return " ".join(metin.split()).lower().translate(_BENZER_HARF)


_SOZCUK_KOSUSU = re.compile(r"[0-9a-z_-]+")   # yazım çeşitlemesinde "sözcük": alt çizgi ve tire sözcüğün içindedir
_ALNUM_DISI = re.compile(r"[^0-9a-z]+")


def aranacak_metin(metin):
    """Bir metnin terim ve ifade araması için hazırlanmış biçimleri: (küçük harf, ayraçsız, sözcük dizisi, ayraçsız
    sözcükler). Sonuncu yokluk aramasının sözcük sınırlı yazım çeşitlemesi içindir (düzeltme sonrası, kabul K-a)."""
    kucuk = katla(metin)
    sozcukler = tuple(w for w in (_ALNUM_DISI.sub("", k) for k in _SOZCUK_KOSUSU.findall(kucuk)) if w)
    return kucuk, _SIKISTIR.sub("", kucuk), " " + " ".join(_SOZCUK_DISI.sub(" ", kucuk).split()) + " ", sozcukler


def terim_geciyor(terim, hazir):
    """Terim metinde geçiyor mu: büyük/küçük harf duyarsız alt dizge; ayrıca yazım çeşitlemesi (`per_page`, `per-page`,
    `perpage`, `PER PAGE` aynı sayılır, en az 4 karakterlik ayraçsız biçimde). `hazir` = `aranacak_metin(...)`."""
    kucuk = katla(terim)
    sikisik = _SIKISTIR.sub("", kucuk)
    return kucuk in hazir[0] or (len(sikisik) >= 4 and sikisik in hazir[1])


_EN_COK_BIRLESIK = 6   # yazım çeşitlemesinde birleştirilen en çok ardışık sözcük (`per page` → `perpage`)


def yokluk_terim_geciyor(terim, hazir):
    """Yokluk aramasında tırnaklı terim metinde **sözcük olarak** geçiyor mu (kabul K-a: alt dizge değil; `page`
    `per_page` içinde, `dat` "data" içinde eşleşmez). İki yol:

    - aynen: katlanmış terim metinde geçer ve terimin harf, rakam, alt çizgi ya da tireyle biten uçlarında komşu karakter
      bunlardan biri değildir;
    - yazım çeşitlemesi (terimin harf ve rakamları en az 4 karakter): metnin ardışık bir ya da birkaç sözcüğü (sözcük =
      harf, rakam, alt çizgi, tire dizisi) ayraçları atılınca terimin harf ve rakamlarına eşittir: `per-page`, `perpage`,
      `PER_PAGE`, `per page` ↔ sayfadaki `per_page`.

    Kısa cevap ve bağlı cevap denetimi eski kuralı (`terim_geciyor`) kullanır; ölçümleri değişmez."""
    kucuk = katla(terim)
    if not kucuk:
        return False
    sol = r"(?<![0-9a-z_-])" if re.match(r"[0-9a-z_-]", kucuk) else ""
    sag = r"(?![0-9a-z_-])" if re.match(r"[0-9a-z_-]", kucuk[-1]) else ""
    if re.search(sol + re.escape(kucuk) + sag, hazir[0]):
        return True
    hedef = _ALNUM_DISI.sub("", kucuk)
    if len(hedef) < 4:
        return False
    sozcukler = hazir[3]
    for bas, sozcuk in enumerate(sozcukler):
        if not hedef.startswith(sozcuk):
            continue
        birlesik = sozcuk
        for sonraki in sozcukler[bas + 1:bas + _EN_COK_BIRLESIK]:
            if birlesik == hedef or not hedef.startswith(birlesik):
                break
            birlesik += sonraki
        if birlesik == hedef:
            return True
    return False


def eksik_terimler(terim_listesi, samanlik):
    """Samanlıkta geçmeyen terimler (`terim_geciyor` kuralıyla)."""
    hazir = aranacak_metin(" ".join(" ".join(str(parca).split()) for parca in samanlik if isinstance(parca, str)))
    return [terim for terim in terim_listesi if not terim_geciyor(terim, hazir)]


_ICERIK_SOZCUGU = 4    # ifade penceresinde "içerik sözcüğü" sayılan en kısa sözcük
_PENCERE = 5           # yokluk maddesinin sayfada aranan ardışık sözcük sayısı
_PENCERE_ICERIK = 2    # bir pencerede en az bu kadar içerik sözcüğü olmalı


def _pencereler(madde):
    """Madde cümlesinin aranacak 5 sözcüklük pencereleri (katlanmış, sözcük dizisi biçiminde)."""
    sozcukler = _SOZCUK_DISI.sub(" ", katla(madde)).split()
    for bas in range(0, max(0, len(sozcukler) - _PENCERE + 1)):
        pencere = sozcukler[bas:bas + _PENCERE]
        if sum(1 for s in pencere if len(s) >= _ICERIK_SOZCUGU) >= _PENCERE_ICERIK:
            yield " ".join(pencere)


def ifade_gecen(madde, hazir):
    """Madde cümlesinden ardışık 5 sözcüklük (en az 2'si içerik sözcüğü) bir pencere metinde aynen geçiyorsa o pencere,
    yoksa None. "Sayfa X'i söylemiyor" maddesindeki X sayfada cümle olarak duruyorsa yakalar; yokluğu kanıtlamaz."""
    return next((p for p in _pencereler(madde) if " " + p + " " in hazir[2]), None)


def aranabilir_mi(madde):
    """Maddede kapının arayabileceği bir şey var mı: tırnaklı terim ya da en az 2 içerik sözcüklü 5 sözcüklük pencere.
    Yoksa kapı o madde için hiçbir şey denetlemez (köprü bunu açıkça yazar)."""
    return bool(yokluk_terimleri(madde)) or next(_pencereler(madde), None) is not None


def yokluk_eslesmesi(madde, hazir):
    """`not_in_sources` maddesi metinle çelişiyor mu: "term" (maddenin tırnaklı terimi geçiyor), "wording" (madde
    cümlesinin 5 sözcüklük bir parçası geçiyor) ya da None."""
    if any(yokluk_terim_geciyor(t, hazir) for t in yokluk_terimleri(madde)):
        return "term"
    return "wording" if ifade_gecen(madde, hazir) else None


def okuma_ekli_aday_mi(aday):
    """Aday paketi okuma eki taşıyor mu (yokluk notu alanı ya da nesne biçimli cevap maddesi)."""
    if not isinstance(aday, dict):
        return False
    cevaplar = aday.get("answers") if isinstance(aday.get("answers"), list) else []
    return any(a in aday for a in YOKLUK_ALANLARI) or any(isinstance(c, dict) for c in cevaplar)


KAPI_EK_ALANLARI = ("source_snapshots", "absence_checks")   # `publication.gate` altındaki okuma eki alanları


def okuma_ekli_kayit_mi(kayit):
    """Yayımlı kamu kaydı okuma eki taşıyor mu: içerikte yokluk notu alanı ya da nesne biçimli cevap maddesi, kapı bloğunda
    kaynak anlığı ya da terim araması. Geri dönüş aracı ve yetenek listesi aynı tanımı kullanır (düzeltme turu 2, kabul Y2)."""
    if not isinstance(kayit, dict):
        return False
    icerik = kayit.get("content") if isinstance(kayit.get("content"), dict) else {}
    yayim = kayit.get("publication") if isinstance(kayit.get("publication"), dict) else {}
    kapi = yayim.get("gate") if isinstance(yayim.get("gate"), dict) else {}
    cevaplar = icerik.get("answers") if isinstance(icerik.get("answers"), list) else []
    return (any(a in icerik for a in YOKLUK_ALANLARI) or any(not isinstance(c, str) for c in cevaplar)
            or any(a in kapi for a in KAPI_EK_ALANLARI))


# Düz metin cevap maddesinde bağ biçimi taklidi (`… -> claims 1, 2`): kapı uyarır, köprü maddeyi JSON dizgisi yazar.
BAG_TAKLIDI = re.compile(r"->\s*(claims?|inferred)\b", re.I)


def tirnakli_terimler(metin):
    """Tırnak ya da ters tırnak içindeki ifadeler (2–60 karakter)."""
    if not isinstance(metin, str):
        return []
    sonuc = []
    for eslesme in _TIRNAKLI.finditer(metin):
        ifade = " ".join(next(g for g in eslesme.groups() if g).split())
        if len(ifade) >= 2 and ifade not in sonuc:
            sonuc.append(ifade)
    return sonuc


def kaynak_sirasi(kaynaklar, iddia_adresleri):
    """Kaynak numaralarının sırası (1'den): önce `sources` adresleri, sonra listede olmayan iddia adresleri."""
    listede = [k.get("url") for k in kaynaklar or [] if isinstance(k, dict) and isinstance(k.get("url"), str)] \
        if isinstance(kaynaklar, list) else []
    return list(dict.fromkeys(listede + [u for u in iddia_adresleri if isinstance(u, str)]))


def paket_kaynak_sirasi(paket):
    """Aday paketinde kaynak numaralarının sırası."""
    iddialar = paket.get("claims") if isinstance(paket.get("claims"), list) else []
    return kaynak_sirasi(paket.get("sources"), [i.get("source_url") for i in iddialar if isinstance(i, dict)])


def cevap_sorusu(oge):
    """Cevap maddesinin soru metni (arama dizini ve eski biçimli görünüm için); biçim dışıysa None."""
    if isinstance(oge, str):
        return oge
    if isinstance(oge, dict) and isinstance(oge.get("q"), str):
        return oge["q"]
    return None


def cevap_bagi(oge):
    """("claims" | "inferred_from", [numaralar]) ya da bağsız/biçim dışı maddede (None, [])."""
    if isinstance(oge, dict):
        for alan in CEVAP_BAG_ALANLARI:
            if isinstance(oge.get(alan), list):
                return alan, [n for n in oge[alan] if type(n) is int]
    return None, []


def _kapsam_metinleri(kapsam, iddia_kapsamlari):
    """Kayıt düzeyi `applies_to` (metin ya da öğe listesi) ve iddia düzeyi öğe listelerinden 'ürün sürüm' metinleri."""
    metinler, ogeler = [], []
    if isinstance(kapsam, str):
        metinler.append(kapsam)
    elif isinstance(kapsam, list):
        ogeler.extend(kapsam)
    for liste in iddia_kapsamlari:
        if isinstance(liste, list):
            ogeler.extend(liste)
    for oge in ogeler:
        if isinstance(oge, dict):
            metinler.append(f"{oge.get('product') or ''} {oge.get('versions') or ''}")
    return metinler


def kisa_cevap_eksikleri(kisa, *, baslik=None, kapsam=None, iddia_kapsamlari=(), alintilar=(), adresler=(), kaynaklar=()):
    """Kısa cevaptaki sayı, sürüm ve kod adlarından hiçbir alıntıda, başlıkta, kapsamda, kaynak adresinde ya da başlığında
    geçmeyenler. Kapı ve köprü bu tek işlevi aynı ham alanlarla çağırır (düzeltme turu 1, kabul K4). Katkıcının iddia
    metni sayılmaz: amaç kısa cevaptaki sayının ya da kod adının alıntıyla dayanağı olup olmadığıdır."""
    if not isinstance(kisa, str) or not kisa.strip():
        return []
    kaynaklar = [k for k in kaynaklar if isinstance(k, dict)]
    samanlik = [*alintilar, baslik if isinstance(baslik, str) else "", *_kapsam_metinleri(kapsam, iddia_kapsamlari),
                *adresler, *(k.get("url") for k in kaynaklar), *(k.get("title") for k in kaynaklar)]
    return eksik_terimler(terimler(kisa), samanlik)


def paket_kisa_cevap_eksikleri(paket):
    """Kapı için: aday paketin kısa cevabında alıntılarda (ya da başlık, kapsam, adreste) geçmeyen terimler."""
    if not isinstance(paket, dict):
        return []
    iddialar = [i for i in paket.get("claims") or [] if isinstance(i, dict)] if isinstance(paket.get("claims"), list) else []
    return kisa_cevap_eksikleri(
        paket.get("short_answer"), baslik=paket.get("title"), kapsam=paket.get("applies_to"),
        iddia_kapsamlari=[i.get("applies_to") for i in iddialar], alintilar=[i.get("quote") for i in iddialar],
        adresler=[i.get("source_url") for i in iddialar],
        kaynaklar=paket.get("sources") if isinstance(paket.get("sources"), list) else [])


def bagli_cevap_eksikleri(soru, alintilar, adresler=()):
    """Bağlı cevap maddesinin kod adı ve sayılarından, bağlı iddiaların alıntılarında (ya da adresinde) geçmeyenler."""
    return eksik_terimler(terimler(soru), [*alintilar, *adresler])


def yokluk_terimleri(madde):
    """`not_in_sources` maddesinde sayfada aranacak terimler: yalnız ters tırnak ya da çift tırnak içindeki ifadeler.

    Düzeltme turu 2 (kabul Y1): tırnaksız kod biçimli adlar (`GitHub`, `Node.js`, `DuckDB`, `17`) ve tek tırnaklı sözler
    artık aranmaz. Yokluk maddesi konuyu anar ("none of the cited pages compares DuckDB and Polars speed"); konunun adı
    sayfada da, kaydın kendi alıntılarında da hep geçer. 26 paketin 121 gerçek `not_covered` maddesinde eski arama 46 maddede
    kaydın kendi alıntısında, 38 maddede ilgisiz alıntılarda eşleşiyordu; bu kuralla 1 ve 0. Katkıcı yok dediği şeyi
    tırnakla işaretler; tırnaksız ad aranmaz ve köprü notu bunu açıkça söyler (aranmadığı halde "denetlendi" denmez)."""
    return tirnakli_terimler(madde)
