"""Kaynak sayfasını görünür düz metne çevirir, alıntıyı yalnız boşluk ve tipografik tırnak/tire eşlemesiyle aynen arar, bulamazsa en yakın parçayı ölçer."""

from __future__ import annotations

import heapq
import re
from difflib import SequenceMatcher
from html.parser import HTMLParser

from . import gorunurluk

# v2 (v0.2): normalleştirme kuralı aynı, görünür metin CSS gizlemesini ayırıyor. v3 (v0.2.1): calc(0), scale<0,1, filter:opacity(0), öznitelik seçici.
NORMALLESTIRME_SURUMU = "gorunur-metin-bosluk-tirnak-tire-v3"

# Tarayıcının hiç göstermediği öğeler (HTML'in varsayılan stil sayfası ve betikli tarayıcı davranışı).
_ATLANAN = {"script", "style", "noscript", "template", "svg", "head", "iframe", "object", "canvas", "title", "datalist",
            "noembed", "noframes", "rp", "video", "audio"}
_BLOK = {"address", "article", "aside", "blockquote", "br", "caption", "dd", "details", "dialog", "div", "dl", "dt",
         "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr",
         "li", "main", "nav", "ol", "p", "pre", "section", "summary", "table", "tbody", "td", "tfoot", "th", "thead",
         "tr", "ul", "option", "label", "legend"}
_BOS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

# Yalnız bunlar eşlenir: tipografik tek/çift tırnak → düz tırnak; tire çeşitleri → kısa çizgi.
_TIRNAK_TIRE = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-",
})
# Görünmeyen ve sayfada hiçbir şey çizmeyen karakterler kaynak metninden düşer
# (yumuşak tire, sıfır genişlikli boşluk/birleştirici, BOM). Alıntıda bunlar 5. denetimde reddedilir.
_GORUNMEZ = re.compile("[­​‌‍⁠﻿]")


class _StilToplayici(HTMLParser):
    """Birinci geçiş: sayfadaki bütün <style> bloklarını (yeri ne olursa olsun) ekran için toplar."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.kurallar = gorunurluk.StilKurallari()
        self._icinde = False
        self._parca = []

    def handle_starttag(self, etiket, nitelikler):
        if etiket == "style":
            ortam = (dict(nitelikler).get("media") or "").lower()
            self._icinde = not ortam or "screen" in ortam or "all" in ortam
            self._parca = []

    def handle_endtag(self, etiket):
        if etiket == "style" and self._icinde:
            self.kurallar.ekle("".join(self._parca))
            self._icinde = False

    def handle_data(self, veri):
        if self._icinde:
            self._parca.append(veri)


class _GorunurMetin(HTMLParser):
    def __init__(self, kurallar=None, xhtml=False):
        super().__init__(convert_charrefs=True)
        self.parcalar = []
        self.yigin = []          # [(etiket, kesin_gizli, miras_durumu)]
        self.gizli = 0           # kesin gizli ata sayısı
        self.kurallar = kurallar or gorunurluk.StilKurallari()
        self.xhtml = xhtml       # XML olarak okunan XHTML'de "/>" öğeyi gerçekten kapatır

    def _durum(self):
        return self.yigin[-1][2] if self.yigin else gorunurluk.KOK_DURUM

    def _kesin_gizli_mi(self, etiket, nitelik, bildirim):
        if etiket in _ATLANAN or "hidden" in nitelik or "popover" in nitelik:
            return True
        if etiket == "dialog" and "open" not in nitelik:
            return True                       # açılmamış <dialog> çizilmez
        return gorunurluk.kesin_gizli_mi(bildirim)

    def handle_starttag(self, etiket, nitelikler):
        nitelik = {k.lower(): (v or "") for k, v in nitelikler}
        if etiket == "body":
            self.handle_endtag("head")   # kapanmamış <head> gövdeyi gizlemesin
        if etiket in _BLOK:
            self.parcalar.append("\n")
        if etiket in _BOS:
            return
        bildirim = self.kurallar.uygula(etiket, nitelik)
        bildirim.update(gorunurluk.bildirimler(nitelik.get("style")))
        gizli = self._kesin_gizli_mi(etiket, nitelik, bildirim)
        self.yigin.append((etiket, gizli, gorunurluk.durum_turet(self._durum(), bildirim)))
        self.gizli += gizli

    def handle_startendtag(self, etiket, nitelikler):
        # HTML'de boş olmayan öğede "/>" yok sayılır: <div hidden/> açık bir <div>'dir ve sonraki metni gizler.
        self.handle_starttag(etiket, nitelikler)
        if self.xhtml and etiket not in _BOS:
            self.handle_endtag(etiket)

    def handle_endtag(self, etiket):
        if etiket in _BLOK:
            self.parcalar.append("\n")
        for sira in range(len(self.yigin) - 1, -1, -1):
            if self.yigin[sira][0] == etiket:
                for _, gizli, _ in self.yigin[sira:]:
                    self.gizli -= gizli
                del self.yigin[sira:]
                break

    def handle_data(self, veri):
        if not self.gizli and not gorunurluk.durum_gizli_mi(self._durum()):
            self.parcalar.append(veri)


def _karakter_kumesi(govde, icerik_turu_basligi):
    for kaynak in (icerik_turu_basligi or "", govde[:4096].decode("ascii", errors="ignore")):
        eslesme = re.search(r"charset\s*=\s*[\"']?([A-Za-z0-9_.:-]+)", kaynak, re.I)
        if eslesme:
            return eslesme.group(1)
    return "utf-8"


def gorunur_metin(govde, icerik_turu="text/html", icerik_turu_basligi=""):
    """Bayt gövdeyi görünür metne çevirir; betik, stil, <head>, gizli öğe ve bilinen CSS gizleme yollarıyla saklanan metin alınmaz."""
    try:
        yazi = govde.decode(_karakter_kumesi(govde, icerik_turu_basligi), errors="replace")
    except LookupError:
        yazi = govde.decode("utf-8", errors="replace")
    if icerik_turu == "text/plain":
        return yazi
    toplayici = _StilToplayici()
    toplayici.feed(yazi)
    toplayici.close()
    ayristirici = _GorunurMetin(toplayici.kurallar, xhtml=icerik_turu == "application/xhtml+xml")
    ayristirici.feed(yazi)
    ayristirici.close()
    return "".join(ayristirici.parcalar)


def normallestir(metin):
    """Bütün boşluk dizilerini tek boşluğa indirir, tipografik tırnak ve tireleri eşler; harf, aksan ve büyük/küçük harf korunur."""
    metin = _GORUNMEZ.sub("", metin).translate(_TIRNAK_TIRE)
    return " ".join(metin.split())


def ara(alinti, kaynak_norm):
    """Normalleştirilmiş kaynakta alıntının geçiş sayısını döndürür."""
    hedef = normallestir(alinti)
    if not hedef:
        return 0
    return kaynak_norm.count(hedef)


def en_yakin(alinti, kaynak_norm, aday_sayisi=5):
    """Aynen bulunmayan alıntıya en benzer kaynak parçasını bulur: {benzerlik, parca, ilk_fark, yalniz_harf_buyuklugu}."""
    hedef = normallestir(alinti)
    sozcukler = [(m.start(), m.end(), m.group()) for m in re.finditer(r"\S+", kaynak_norm)]
    if not hedef or not sozcukler:
        return None

    def kok(s):
        return s.strip(".,;:!?()[]{}\"'").lower()

    hedef_kume = {kok(s) for s in hedef.split(" ")}
    k = min(len(hedef.split(" ")), len(sozcukler))
    isaret = [1 if kok(s) in hedef_kume else 0 for _, _, s in sozcukler]
    toplam = sum(isaret[:k])
    pencereler = [(toplam, 0)]
    for i in range(1, len(sozcukler) - k + 1):
        toplam += isaret[i + k - 1] - isaret[i - 1]
        pencereler.append((toplam, i))
    adaylar = heapq.nlargest(aday_sayisi, pencereler, key=lambda t: (t[0], -t[1]))
    en_iyi = (0.0, "")
    denenen = set()
    for _, i in adaylar:
        for bas in range(max(0, i - 2), min(len(sozcukler) - 1, i + 2) + 1):
            for uzunluk in range(max(1, k - 2), k + 3):
                son = bas + uzunluk - 1
                if son >= len(sozcukler) or (bas, son) in denenen:
                    continue
                denenen.add((bas, son))
                parca = kaynak_norm[sozcukler[bas][0]:sozcukler[son][1]]
                oran = SequenceMatcher(None, hedef, parca, autojunk=False).ratio()
                if oran > en_iyi[0]:
                    en_iyi = (oran, parca)
    oran, parca = en_iyi
    ilk_fark = next((n for n, (a, b) in enumerate(zip(hedef, parca)) if a != b), min(len(hedef), len(parca)))
    return {"benzerlik": round(oran, 3), "parca": parca[:300], "ilk_fark": ilk_fark,
            "yalniz_harf_buyuklugu": hedef.lower() in kaynak_norm.lower()}
