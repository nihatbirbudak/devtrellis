"""İnsanın görmediği metni ayırmak için CSS sezgileri: satır içi stil ve sayfadaki <style> bloklarının basit kuralları; tam bir CSS motoru değildir."""

from __future__ import annotations

import re

# Bu modül bilerek sınırlıdır (README §3, 4. denetim): dış stil dosyası (<link rel=stylesheet>) okunmaz, betikle
# değişen görünüm görülmez, seçici özgüllüğü hesaplanmaz. Amaç bilinen gizleme yollarını yakalamak; yanlış yönde
# hata "alıntı bulunamadı" (yayımlanmaz) olur, kaçak değil.

_YORUM = re.compile(r"/\*.*?\*/", re.S)
_KACIS = re.compile(r"\\([0-9a-fA-F]{1,6}\s?|[^\n0-9a-fA-F])")
_SAYI = re.compile(r"^([+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?)([a-z%]*)$")
_ONEMLI = re.compile(r"!\s*important\s*$", re.I)
_PARCA = re.compile(r"([.#])((?:[A-Za-z0-9_-]|\\[0-9a-fA-F]{1,6}\s?|\\[^\n0-9a-fA-F])+)")
_ETIKET = re.compile(r"^([a-zA-Z][a-zA-Z0-9-]*)?")
_GRUP_KURALI = re.compile(r"@(?:media|supports|layer|container|document|scope|-moz-document)\b", re.I)
_PARANTEZ = re.compile(r"[{}]")

_MUTLAK = {"px": 1.0, "pt": 4 / 3, "pc": 16.0, "q": 0.25 * 96 / 25.4, "mm": 96 / 25.4, "cm": 96 / 2.54, "in": 96.0,
           "rem": 16.0}
_GORELI_YAZI = {"em": 16.0, "ex": 8.0, "ch": 8.0}
_EKRAN = {"%", "vw", "vh", "vmin", "vmax", "svw", "svh", "lvw", "lvh", "dvw", "dvh"}

_ADLI_RENK = {
    "white": (255, 255, 255), "black": (0, 0, 0), "red": (255, 0, 0), "green": (0, 128, 0), "blue": (0, 0, 255),
    "yellow": (255, 255, 0), "gray": (128, 128, 128), "grey": (128, 128, 128), "silver": (192, 192, 192),
    "maroon": (128, 0, 0), "purple": (128, 0, 128), "fuchsia": (255, 0, 255), "lime": (0, 255, 0),
    "olive": (128, 128, 0), "navy": (0, 0, 128), "teal": (0, 128, 128), "aqua": (0, 255, 255),
    "orange": (255, 165, 0), "whitesmoke": (245, 245, 245), "snow": (255, 250, 250), "ivory": (255, 255, 240),
    "ghostwhite": (248, 248, 255), "lightgray": (211, 211, 211), "lightgrey": (211, 211, 211),
    "darkgray": (169, 169, 169), "darkgrey": (169, 169, 169),
}


def _kacis_coz(metin):
    """CSS kaçışlarını çözer (\\64 isplay → display, \\: → :)."""
    def coz(eslesme):
        govde = eslesme.group(1)
        if re.match(r"[0-9a-fA-F]", govde):
            try:
                return chr(int(govde.strip(), 16))
            except (ValueError, OverflowError):
                return ""
        return govde
    return _KACIS.sub(coz, metin)


def bildirimler(stil):
    """'a: b; c: d' → {'a': 'b', 'c': 'd'}: küçük harf, yorumsuz, kaçışı çözülmüş, !important atılmış; sonraki öncekini ezer."""
    sonuc = {}
    if not stil:
        return sonuc
    for parca in _kacis_coz(_YORUM.sub("", stil)).split(";"):
        if ":" not in parca:
            continue
        ad, deger = parca.split(":", 1)
        ad = ad.strip().lower()
        deger = _ONEMLI.sub("", deger.strip()).strip().lower()
        if ad and deger:
            sonuc[ad] = deger
    return sonuc


_CALC = re.compile(r"^calc\(\s*(.*?)\s*\)$")
_CALC_TERIM = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?[a-z%]*")


def _calc_ac(deger):
    """O-1b: basit calc() çözülür. Tek terim → o terim; çarpımda sıfır çarpan ya da toplamda hepsi sıfır → '0'; öteki → olduğu gibi."""
    eslesme = _CALC.match(deger)
    if not eslesme:
        return deger
    ic = eslesme.group(1)
    if _CALC_TERIM.fullmatch(ic):
        return ic
    if "(" in ic:
        return deger
    if "*" in ic and not re.search(r"\s[+-]\s", ic):          # çarpım: pay çarpanlarından biri sıfırsa sonuç sıfır
        carpanlar = [t.strip() for t in ic.split("/")[0].split("*")]
        if all(_CALC_TERIM.fullmatch(t) for t in carpanlar) and any(_sifir_terim(t) for t in carpanlar):
            return "0"
        return deger
    terimler = [t.strip() for t in re.split(r"\s[+-]\s", ic)]   # toplam: bütün terimler sıfırsa sonuç sıfır
    if all(_CALC_TERIM.fullmatch(t) and _sifir_terim(t) for t in terimler):
        return "0"
    return deger


def _sifir_terim(terim):
    return float(re.match(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?", terim).group()) == 0


def _sayi(deger):
    eslesme = _SAYI.match(_calc_ac(deger.strip()))
    if not eslesme:
        return None, None
    try:
        return float(eslesme.group(1)), eslesme.group(2)
    except ValueError:
        return None, None


def _uzaklik_px(deger, yazi_px=16.0):
    """Uzunluğu yaklaşık piksele çevirir; ekran yüzdesi için ('yuzde', n); anlaşılmazsa None."""
    n, birim = _sayi(deger)
    if n is None:
        return None
    if birim == "" and n == 0:
        return 0.0
    if birim in _MUTLAK:
        return n * _MUTLAK[birim]
    if birim in _GORELI_YAZI:
        return n * _GORELI_YAZI[birim] / 16.0 * yazi_px
    if birim in _EKRAN:
        return ("yuzde", n)
    return None


def _cok_eksi(deger, px_sinir):
    """Değer ekran dışına itecek kadar eksi mi (px sınırı ya da ekranın tamamı kadar yüzde)."""
    for parca in deger.replace(",", " ").split():
        olcu = _uzaklik_px(parca)
        if isinstance(olcu, tuple):
            if olcu[1] <= -100:
                return True
        elif olcu is not None and olcu <= -px_sinir:
            return True
    return False


def _islevler(deger, ad):
    return re.findall(rf"\b{ad}\s*\(([^()]*)\)", deger)


def _sifir_alan_kirpma(deger):
    """clip: rect(üst, sağ, alt, sol) görünür alanı sıfır mı."""
    for ic in _islevler(deger, "rect"):
        parcalar = ic.replace(",", " ").split()
        if len(parcalar) != 4:
            continue
        olculer = [None if p == "auto" else _uzaklik_px(p) for p in parcalar]
        if any(isinstance(o, tuple) for o in olculer):
            continue
        ust, sag, alt, sol = olculer
        if (ust is not None and alt is not None and alt - ust <= 1) or (sol is not None and sag is not None and sag - sol <= 1):
            return True
    return False


def _sifir_alan_yol(deger):
    """clip-path: inset(≥%50), circle(0), ellipse(0 0) görünür alanı sıfırlıyor mu."""
    for ic in _islevler(deger, "inset"):
        for parca in ic.split():
            n, birim = _sayi(parca)
            if n is not None and birim == "%" and n >= 50:
                return True
    for ad in ("circle", "ellipse"):
        for ic in _islevler(deger, ad):
            parcalar = ic.split(" at ")[0].split()
            if parcalar and all(_sayi(p)[0] == 0 for p in parcalar):
                return True
    return False


def _kucuk_boyut(deger):
    olcu = _uzaklik_px(deger)
    return olcu is not None and not isinstance(olcu, tuple) and olcu <= 1


def _kucuk_olcek(deger):
    """scale() çarpanı yazıyı okunmaz kılacak kadar küçük mü (O-1b: scale(0.001)); %10'un altı."""
    n, birim = _sayi(deger)
    if n is None:
        return False
    return abs(n / 100 if birim == "%" else n) < 0.1


def kesin_gizli_mi(b):
    """Bildirimler öğeyi ve bütün altını kesin gizliyor mu (alt öğe geri açamaz)."""
    if b.get("display") == "none" or b.get("content-visibility") == "hidden":
        return True
    if "opacity" in b:
        n, birim = _sayi(b["opacity"])
        if n is not None and ((birim == "%" and n <= 5) or (birim == "" and n <= 0.05)):
            return True
    for ad in ("left", "top", "right", "bottom", "inset", "inset-inline-start", "inset-inline-end",
               "inset-block-start", "inset-block-end"):
        if ad in b and _cok_eksi(b[ad], 500):
            return True
    for ad in ("margin", "margin-left", "margin-top", "margin-right", "margin-bottom", "margin-inline-start",
               "margin-block-start"):
        if ad in b and _cok_eksi(b[ad], 1000):
            return True
    if "text-indent" in b:
        olcu = _uzaklik_px(b["text-indent"].split()[0])
        if isinstance(olcu, tuple) and abs(olcu[1]) >= 100 or isinstance(olcu, float) and abs(olcu) >= 500:
            return True
    donusum = b.get("transform", "") + " " + b.get("translate", "") + " " + b.get("scale", "")
    for ad in ("scale", "scalex", "scaley", "scale3d"):
        for ic in _islevler(donusum, ad):
            if any(_kucuk_olcek(p) for p in ic.replace(",", " ").split()):
                return True
    if b.get("scale") and any(_kucuk_olcek(p) for p in b["scale"].split()):
        return True
    for ic in _islevler(b.get("filter", "") + " " + b.get("-webkit-filter", ""), "opacity"):
        n, birim = _sayi(ic)
        if n is not None and ((birim == "%" and n <= 5) or (birim == "" and n <= 0.05)):
            return True
    for ad in ("translate", "translatex", "translatey", "translate3d"):
        for ic in _islevler(donusum, ad):
            if _cok_eksi(ic, 500):
                return True
    if b.get("translate") and _cok_eksi(b["translate"], 500):
        return True
    if "clip" in b and _sifir_alan_kirpma(b["clip"]):
        return True
    if "clip-path" in b and _sifir_alan_yol(b["clip-path"]):
        return True
    tasma = {b.get("overflow", ""), b.get("overflow-x", ""), b.get("overflow-y", "")}
    if tasma & {"hidden", "clip"} or any(v.startswith(("hidden", "clip")) for v in tasma):
        for ad in ("width", "height", "max-width", "max-height"):
            if ad in b and _kucuk_boyut(b[ad]):
                return True
    return False


def renk(deger):
    """Renk değerini (r, g, b, alfa) yapar; anlaşılmazsa None. 'transparent' alfa 0'dır."""
    if not deger:
        return None
    deger = deger.strip().lower()
    if deger == "transparent":
        return (0, 0, 0, 0.0)
    if deger in _ADLI_RENK:
        return (*_ADLI_RENK[deger], 1.0)
    onaltilik = re.fullmatch(r"#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})", deger)
    if onaltilik:
        h = onaltilik.group(1)
        if len(h) in (3, 4):
            h = "".join(c * 2 for c in h)
        alfa = int(h[6:8], 16) / 255 if len(h) == 8 else 1.0
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alfa)
    islev = re.fullmatch(r"rgba?\(([^()]*)\)", deger)
    if islev:
        parcalar = [p for p in re.split(r"[\s,/]+", islev.group(1).strip()) if p]
        if len(parcalar) not in (3, 4):
            return None
        kanallar = []
        for p in parcalar[:3]:
            n, birim = _sayi(p)
            if n is None or birim not in ("", "%"):
                return None
            kanallar.append(round(n * 2.55) if birim == "%" else round(n))
        alfa = 1.0
        if len(parcalar) == 4:
            n, birim = _sayi(parcalar[3])
            if n is None or birim not in ("", "%"):
                return None
            alfa = n / 100 if birim == "%" else n
        return (*kanallar, alfa)
    return None


def arka_renk(b):
    """Öğenin kendi arka plan rengi; resim ya da geçiş varsa ya da belirtilmemişse None."""
    if "background-color" in b:
        return renk(b["background-color"])
    deger = b.get("background")
    if not deger or "url(" in deger or "gradient(" in deger:
        return None
    for parca in re.findall(r"#[0-9a-f]{3,8}\b|rgba?\([^()]*\)|[a-z]+", deger):
        r = renk(parca)
        if r is not None:
            return r
    return None


def _yakin(a, b):
    return a is not None and b is not None and a[3] > 0.05 and b[3] > 0.05 and max(abs(x - y) for x, y in zip(a[:3], b[:3])) <= 8


def yazi_kucuk_mu(deger, ust_kucuk):
    """font-size değeri yazıyı okunmaz kılıyor mu; göreli birimde üst öğenin durumu miras alınır."""
    n, birim = _sayi(deger)
    if n is None:
        # Anahtar sözcükler: small, large, medium ... okunur boyuttur; göreli olanlar üstün durumunu sürdürür.
        return ust_kucuk if deger in ("smaller", "larger", "inherit", "unset", "revert") else False
    if n == 0:
        return True
    if birim in _MUTLAK:
        return n * _MUTLAK[birim] < 2
    if birim in ("em", "ex", "ch", "%"):
        return ust_kucuk or (n < 15 if birim == "%" else n * _GORELI_YAZI.get(birim, 16.0) < 2)
    if birim in _EKRAN:
        return n < 0.2
    return False


KOK_DURUM = {"yazi_kucuk": False, "gorunmez": False, "renk": None, "arka": None, "metin_kirpma": False}


def durum_turet(ust, b):
    """Miras alınan görünürlük durumunu öğenin bildirimleriyle günceller (alt öğe bunları geri açabilir)."""
    yeni = dict(ust)
    if "font-size" in b:
        yeni["yazi_kucuk"] = yazi_kucuk_mu(b["font-size"], ust["yazi_kucuk"])
    if "visibility" in b and b["visibility"] in ("hidden", "collapse", "visible"):
        yeni["gorunmez"] = b["visibility"] != "visible"
    yazi_rengi = b.get("-webkit-text-fill-color") or b.get("color")
    if yazi_rengi and yazi_rengi not in ("inherit", "currentcolor", "unset", "initial", "revert"):
        yeni["renk"] = renk(yazi_rengi)
    if b.get("background-clip") == "text" or b.get("-webkit-background-clip") == "text":
        yeni["metin_kirpma"] = True
    arka = arka_renk(b)
    resim = b.get("background", "") + " " + b.get("background-image", "")
    if "url(" in resim or "gradient(" in resim:
        yeni["arka"] = None                  # resim ya da geçiş: zemin rengi bilinmiyor
    elif arka is not None and arka[3] > 0.05:
        yeni["arka"] = arka
    return yeni


def durum_gizli_mi(durum):
    """Miras durumu metni görünmez kılıyor mu: küçük yazı, visibility:hidden, saydam yazı ya da zeminle aynı renk."""
    if durum["yazi_kucuk"] or durum["gorunmez"]:
        return True
    r = durum["renk"]
    if r is not None and r[3] <= 0.05 and not durum["metin_kirpma"]:
        return True
    return _yakin(r, durum["arka"])


def _gosteren(b):
    """Kural öğeyi görünür kılan bir bildirim taşıyor mu (duyarlı tasarımda 'göster' kuralları)."""
    if b.get("display") not in (None, "none"):
        return True
    if b.get("visibility") == "visible" or b.get("content-visibility") == "visible":
        return True
    if "opacity" in b:
        n, _ = _sayi(b["opacity"])
        return n is not None and n > 0.05
    return False


# Öznitelik seçici: [ad], [ad=değer], [ad~=değer], [ad^=…], [ad$=…], [ad*=…], [ad|=…] (O-1b).
_OZNITELIK = re.compile(r"\[\s*([A-Za-z_][\w:-]*)\s*(?:([~|^$*]?=)\s*(\"[^\"]*\"|'[^']*'|[^\]\s]+)\s*(?:i\s*)?)?\]")


def _basit_secici(secici):
    """'etiket', '.sinif', '#kimlik', '[öznitelik]' ya da bunların birleşimi → (etiket|None, sınıflar, kimlikler, öznitelikler);
    soy, çocuk, kardeş ve sözde sınıf içeren seçiciler None (uygulanmaz; README sınırı)."""
    eslesme = _ETIKET.match(secici)
    etiket = eslesme.group(1).lower() if eslesme.group(1) else None
    kalan = secici[eslesme.end():]
    siniflar, kimlikler, oznitelikler = set(), set(), set()
    while kalan:
        parca = _PARCA.match(kalan)
        if parca:
            (siniflar if parca.group(1) == "." else kimlikler).add(_kacis_coz(parca.group(2)).strip())
            kalan = kalan[parca.end():]
            continue
        oz = _OZNITELIK.match(kalan)
        if not oz:
            return None
        deger = oz.group(3)
        if deger and deger[0] in "\"'":
            deger = deger[1:-1]
        oznitelikler.add((oz.group(1).lower(), oz.group(2) or "", deger or ""))
        kalan = kalan[oz.end():]
    if etiket is None and not siniflar and not kimlikler and not oznitelikler:
        return None
    return etiket, frozenset(siniflar), frozenset(kimlikler), frozenset(oznitelikler)


def _oznitelik_uyar(nitelik, ad, islec, beklenen):
    if ad not in nitelik:
        return False
    deger = nitelik[ad]
    if not islec:
        return True
    return {"=": deger == beklenen, "~=": beklenen in deger.split(), "^=": bool(beklenen) and deger.startswith(beklenen),
            "$=": bool(beklenen) and deger.endswith(beklenen), "*=": bool(beklenen) and beklenen in deger,
            "|=": deger == beklenen or deger.startswith(beklenen + "-")}[islec]


# <style> kurallarından yalnız bunlar uygulanır. Yazı boyutu, renk ve zemin uygulanmaz: alt öğeler bunları çoğu zaman
# karmaşık seçicilerle (".satir > .sutun") geri açar ve kapı onları görmez; uygulamak gerçek sayfada yanlış ret doğururdu.
KURAL_OZELLIKLERI = {"display", "visibility", "content-visibility", "opacity", "left", "top", "right", "bottom", "inset",
                     "inset-inline-start", "inset-inline-end", "inset-block-start", "inset-block-end", "margin",
                     "margin-left", "margin-top", "margin-right", "margin-bottom", "margin-inline-start",
                     "margin-block-start", "text-indent", "transform", "translate", "scale", "clip", "clip-path",
                     "overflow", "overflow-x", "overflow-y", "width", "height", "max-width", "max-height", "filter",
                     "-webkit-filter"}


class StilKurallari:
    """<style> bloklarından çıkarılan kurallar: üst düzey basit seçicilerin bildirimleri ve 'göster' kurallarında geçen adlar."""

    def __init__(self):
        self.kurallar = []        # [(etiket|None, sınıflar, kimlikler, öznitelikler, bildirimler)] kaynak sırasıyla
        self.gosteren = set()     # '.sinif', '#kimlik', '[öznitelik' — herhangi bir derinlikte 'göster' kuralında geçenler
        self._dizin = {}          # '.sinif' / '#kimlik' / '[öznitelik' / 'etiket' → kural sıraları (büyük sayfada hız için)

    def ekle(self, css):
        self._bloklar(_YORUM.sub("", css), ust_duzey=True)

    def _ekle_kural(self, kural):
        etiket, siniflar, kimlikler, oznitelikler, _ = kural
        if siniflar:
            anahtar = "." + min(siniflar)
        elif kimlikler:
            anahtar = "#" + min(kimlikler)
        elif oznitelikler:
            anahtar = "[" + min(oznitelikler)[0]
        else:
            anahtar = etiket
        self._dizin.setdefault(anahtar, []).append(len(self.kurallar))
        self.kurallar.append(kural)

    def _bloklar(self, css, ust_duzey):
        i = 0
        while True:
            acilis = css.find("{", i)
            if acilis < 0:
                return
            on = re.split(r"[;}]", css[i:acilis])[-1].strip()
            derinlik, j = 1, acilis + 1
            for parantez in _PARANTEZ.finditer(css, acilis + 1):
                derinlik += 1 if parantez.group() == "{" else -1
                if not derinlik:
                    j = parantez.end()
                    break
            else:
                j = len(css) + 1
            govde = css[acilis + 1:j - 1]
            i = j
            if on.startswith("@"):
                if _GRUP_KURALI.match(on):
                    self._bloklar(govde, ust_duzey=False)   # @media print, duyarlı kurallar: gizleme uygulanmaz
                continue
            if "{" in govde:                                  # CSS iç içe yazımı
                self._bloklar(govde, ust_duzey=False)
                govde = re.sub(r"[^{}]*\{[^{}]*\}", "", govde)
            b = bildirimler(govde)
            if _gosteren(b):
                for parca in _PARCA.finditer(on):
                    self.gosteren.add(parca.group(1) + _kacis_coz(parca.group(2)).strip())
                for oz in _OZNITELIK.finditer(on):
                    self.gosteren.add("[" + oz.group(1).lower())
            if not ust_duzey or not b:
                continue
            b = {k: v for k, v in b.items() if k in KURAL_OZELLIKLERI}
            if not b:
                continue
            for secici in on.split(","):
                basit = _basit_secici(secici.strip())
                if basit:
                    self._ekle_kural((*basit, b))

    def uygula(self, etiket, nitelik):
        """Öğeye uyan üst düzey kuralların birleşik bildirimleri (kaynak sırasıyla); öğe bir 'göster' kuralında geçiyorsa boş."""
        if not self.kurallar:
            return {}
        sinif, kimlik = nitelik.get("class"), nitelik.get("id")
        siniflar = set(sinif.split()) if sinif else set()
        if (any("." + s in self.gosteren for s in siniflar) or (kimlik and "#" + kimlik in self.gosteren)
                or any("[" + ad in self.gosteren for ad in nitelik)):
            return {}
        adaylar = set(self._dizin.get(etiket, ()))
        for s in siniflar:
            adaylar.update(self._dizin.get("." + s, ()))
        if kimlik:
            adaylar.update(self._dizin.get("#" + kimlik, ()))
        for ad in nitelik:
            adaylar.update(self._dizin.get("[" + ad, ()))
        sonuc = {}
        for sira in sorted(adaylar):
            k_etiket, k_siniflar, k_kimlikler, k_oznitelikler, b = self.kurallar[sira]
            if ((k_etiket is None or k_etiket == etiket) and k_siniflar <= siniflar
                    and (not k_kimlikler or k_kimlikler == {kimlik})
                    and all(_oznitelik_uyar(nitelik, ad, islec, deger) for ad, islec, deger in k_oznitelikler)):
                sonuc.update(b)
        return sonuc

