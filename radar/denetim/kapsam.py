"""Makineyle kapsam (alpha.13b): kaynak sayfasının bölümleri ile paketin alıntılarını karşılaştırır.

Bölüm = ana içerikteki başlık (h1–h4) ya da kimlikli tanım terimi (`<dt id=…>`, Sphinx gibi API belgeleri). Bir bölüm, içinde
bir alıntı aynen geçiyorsa "kapsandı" sayılır. Kapsanmayan bölümlerden en anlamlıları ad olarak döner. Bu bir **bilgidir,
ret değildir**: katkıcıya "kartın alıntı almadığı sayfa bölümleri" olarak, ajana kayıtla birlikte `machine_not_covered`
olarak gösterilir. Teşhis T2: kartta eksik madde vardı ama elle yazılan `not_covered` bunu söylemiyordu, ajan kartı tam sandı.

Sıra (en anlamlı önce): (a) kartın metninde ya da alıntısında adı kod biçiminde geçen ama kendi bölümünden alıntı olmayan
tanımlar (`bounds()`, `COMPRESSION_LEVEL_DEFAULT`); (b) kaynak kartında, kapsanan bir tanımla aynı başlık altındaki kardeş
tanımlar (`ZstdDecompressor`); (c) kaynak kartında kalan başlıklar, belge sırasıyla. Raporda yalnız (a): rapor birçok sayfadan
seçerek alıntılar, sayfanın geri kalanı kapsam dışı değil seçimdir.
"""

from __future__ import annotations

import re

from .metin import _GorunurMetin, _StilToplayici, _karakter_kumesi, normallestir

EN_COK_AD = 12
_BASLIK = {"h1": 1, "h2": 2, "h3": 3, "h4": 4}
# Gezinme, kenar çubuğu ve altbilgi başlıkları içerik bölümü sayılmaz.
_GEZINME = re.compile(r"^(table of contents|contents|on this page|in this (article|page)|this page|previous( topic)?|"
                      r"next( topic)?|navigation|quick search|search|footnotes|related( topics| articles)?|help and support|"
                      r"did you find what you needed\??|feedback|submit correction|edit this page|legal|resources|"
                      r"further reading|see also|prev|up|home)$", re.I)
# Düşük değerli başlıklar (yönetici koşulu 2): kapsam dışı listesinde yer kaplamaz.
_DUSUK_DEGER = re.compile(r"^(\d+(\.\d+)*\.?\s*)?(examples?|usage examples?|notes?|footnotes|changelog|change log|history|"
                          r"glossary|index|acknowledge?ments?|licen[cs]e|copyright|references|bibliography|"
                          r"next steps|summary|overview|introduction)$", re.I)
_KOD_ADI = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# Gezinme kapları: içlerindeki başlık, madde ve satır birim sayılmaz (PostgreSQL `navfooter` tablosu, kenar çubukları).
_GEZINME_KABI = re.compile(r"nav|footer|header|breadcrumb|sidebar|sphinxsidebar|toc|menu", re.I)


class _Bolum:
    __slots__ = ("tur", "duzey", "baslik", "kimlik", "bas", "son", "ust", "metin")

    def __init__(self, tur, duzey, bas, kimlik=None, ust=None):
        self.tur, self.duzey, self.bas, self.kimlik, self.ust = tur, duzey, bas, kimlik, ust
        self.baslik, self.son, self.metin = "", None, ""


class _Bolumleyici(_GorunurMetin):
    """Görünür metni toplarken bölüm sınırlarını (parça sırası olarak) kaydeder."""

    def __init__(self, kurallar=None, xhtml=False):
        super().__init__(kurallar, xhtml)
        self.bolumler = []
        self.acik_baslik = None         # başlığın metni toplanırken
        self.basliklar = []             # açık başlık bölümleri (düzeye göre yığın)
        self.dl_derinligi = 0
        self.dt_yigini = []             # (dl derinliği, bölüm)
        self.icerik_basladi = False
        self.gezinme = []               # açık gezinme kaplarının yığın derinliği
        self.maddeler = []              # en dıştaki liste maddeleri ve tablo satırları (az bölümlü sayfada birim olur)
        self.li_derinligi = 0

    def _ust(self):
        # İç içe tanımın üstü kapsayan tanım; yoksa en yakın açık başlık.
        if self.dt_yigini:
            return self.dt_yigini[-1][1]
        return self.basliklar[-1] if self.basliklar else None

    def handle_starttag(self, etiket, nitelikler):
        nitelik = dict(nitelikler)
        if (etiket in ("nav", "footer", "header", "aside")
                or _GEZINME_KABI.search(f"{nitelik.get('class') or ''} {nitelik.get('id') or ''} {nitelik.get('role') or ''}")):
            if etiket not in ("h1", "h2", "h3", "h4", "dt", "dl", "li", "tr"):
                self.gezinme.append(len(self.yigin))
        if self.gezinme:
            super().handle_starttag(etiket, nitelikler)
            return
        if etiket in _BASLIK and not self.gizli:
            duzey = _BASLIK[etiket]
            # Ana içerik ilk h1'de (PostgreSQL bölüm sayfalarında h1 yok: ilk h2'de) başlar; Sphinx kenar çubuğu h3/h4'tür.
            if duzey <= 2:
                self.icerik_basladi = True
            konum = len(self.parcalar)
            while self.basliklar and self.basliklar[-1].duzey >= duzey:
                self.basliklar.pop().son = konum
            bolum = _Bolum("baslik", duzey, konum, ust=self.basliklar[-1] if self.basliklar else None)
            self.acik_baslik = (etiket, bolum)
        elif etiket == "dl":
            self.dl_derinligi += 1
        elif etiket in ("li", "tr"):
            # PostgreSQL işlev sayfaları tablodur: satır da madde gibi birimdir.
            self.li_derinligi += 1
            if self.li_derinligi == 1 and self.icerik_basladi and not self.gizli:
                self.maddeler.append(_Bolum("madde", 20, len(self.parcalar), ust=self._ust()))
        elif etiket == "dt" and not self.gizli:
            kimlik = dict(nitelikler).get("id")
            konum = len(self.parcalar)
            while self.dt_yigini and self.dt_yigini[-1][0] >= self.dl_derinligi:
                self.dt_yigini.pop()[1].son = konum
            if kimlik and self.icerik_basladi:
                bolum = _Bolum("tanim", 10 + self.dl_derinligi, konum, kimlik=kimlik, ust=self._ust())
                bolum.baslik = kimlik
                self.bolumler.append(bolum)
                self.dt_yigini.append((self.dl_derinligi, bolum))
        super().handle_starttag(etiket, nitelikler)

    def handle_endtag(self, etiket):
        super().handle_endtag(etiket)
        while self.gezinme and self.gezinme[-1] >= len(self.yigin):
            self.gezinme.pop()
        if self.gezinme:
            return
        if self.acik_baslik is not None and etiket == self.acik_baslik[0]:
            _, bolum = self.acik_baslik
            self.acik_baslik = None
            bolum.baslik = normallestir("".join(self.parcalar[bolum.bas:])).rstrip("¶#🔗 ").strip()
            if self.icerik_basladi and bolum.baslik and not _GEZINME.match(bolum.baslik):
                self.bolumler.append(bolum)
                self.basliklar.append(bolum)
        elif etiket in ("li", "tr") and self.li_derinligi:
            self.li_derinligi -= 1
            if self.li_derinligi == 0 and self.maddeler and self.maddeler[-1].son is None:
                self.maddeler[-1].son = len(self.parcalar)
        elif etiket == "dl":
            konum = len(self.parcalar)
            while self.dt_yigini and self.dt_yigini[-1][0] >= self.dl_derinligi:
                self.dt_yigini.pop()[1].son = konum
            self.dl_derinligi = max(0, self.dl_derinligi - 1)


def bolumler(govde, icerik_turu="text/html", icerik_turu_basligi=""):
    """Sayfanın içerik bölümleri: her biri tür, düzey, başlık/kimlik, üst bölüm ve normalleştirilmiş metinle."""
    if icerik_turu not in ("text/html", "application/xhtml+xml"):
        return []
    try:
        yazi = govde.decode(_karakter_kumesi(govde, icerik_turu_basligi), errors="replace")
    except LookupError:
        yazi = govde.decode("utf-8", errors="replace")
    toplayici = _StilToplayici()
    toplayici.feed(yazi)
    toplayici.close()
    ayristirici = _Bolumleyici(toplayici.kurallar, xhtml=icerik_turu == "application/xhtml+xml")
    ayristirici.feed(yazi)
    ayristirici.close()
    sonu = len(ayristirici.parcalar)
    liste = list(ayristirici.bolumler)
    # Az bölümlü sayfada (PostgreSQL'in tek başlıklı sayfaları) birim liste maddeleridir: "kısıtlar" listesinin
    # kartta olmayan maddeleri ancak böyle görünür. Altı sözcükten kısa madde (gezinme bağlantısı) sayılmaz.
    if sum(1 for b in liste if b.tur == "tanim" or b.duzey >= 2) < 4:
        liste.extend(m for m in ayristirici.maddeler if m.son is not None)
    for bolum in liste:
        bolum.metin = normallestir("".join(ayristirici.parcalar[bolum.bas:bolum.son if bolum.son is not None else sonu]))
        if bolum.tur == "madde":
            bolum.baslik = bolum.metin[:200]  # gösterimde 60 karaktere sözcük sınırında kısalır
    return [b for b in liste if b.tur != "madde" or len(b.metin.split()) >= 6]


def _ortak_onek(kimlikler):
    """Tanım kimliklerinin ortak modül öneki ("compression.zstd."); gösterimde düşer."""
    parcalar = [k.split(".") for k in kimlikler if "." in k]
    if len(parcalar) < 2:
        return ""
    onek = []
    for sutun in zip(*parcalar):
        if len(set(sutun)) != 1:
            break
        onek.append(sutun[0])
    onek = onek[:min(len(p) for p in parcalar) - 1]
    return ".".join(onek) + "." if onek else ""


def _ad(bolum, onek):
    if bolum.tur == "tanim":
        ad = bolum.kimlik[len(onek):] if onek and bolum.kimlik.startswith(onek) else bolum.kimlik
        return ad[:60]
    if len(bolum.baslik) <= 60:
        return bolum.baslik
    return bolum.baslik[:59].rsplit(" ", 1)[0].rstrip(",.;:") + "…"


def _kod_bicimli_adlar(metin):
    """Metinde kod gibi yazılmış adlar: önünde nokta, ardında parantez, alt çizgili, büyük harf karışık ya da sayılı."""
    adlar = set()
    for eslesme in _KOD_ADI.finditer(metin):
        ad, bas, son = eslesme.group(), eslesme.start(), eslesme.end()
        kod = ((bas > 0 and metin[bas - 1] == ".") or metin[son:son + 1] == "(" or "_" in ad
               or (any(c.isupper() for c in ad[1:]) and any(c.islower() for c in ad)) or (ad.isupper() and len(ad) > 3))
        if kod:
            adlar.add(ad)
    return adlar


def kapsam_disi(bolum_listesi, alintilar, kart_metni, *, rapor=False, en_cok=EN_COK_AD):
    """Kapsanmayan bölümler: {"bolum": toplam, "kapsanan": n, "kapsanmayan": n, "adlar": [en anlamlı ≤ en_cok]}."""
    hedefler = [normallestir(a) for a in alintilar if isinstance(a, str) and normallestir(a)]
    kapsanan = set()
    for bolum in bolum_listesi:
        if any(h in bolum.metin for h in hedefler):
            kapsanan.add(id(bolum))
    # Kapsanan bölümün bütün üstleri de kapsanmıştır (alıntı onların metninde de geçer); alt bölümleri değil.
    onek = _ortak_onek([b.kimlik for b in bolum_listesi if b.tur == "tanim"])
    adlar_metinde = _kod_bicimli_adlar(kart_metni)

    def ust_kapsanmis_mi(bolum):
        return bolum.ust is not None and id(bolum.ust) in kapsanan

    kapsanmayan = [b for b in bolum_listesi if id(b) not in kapsanan]
    # (a) adı kartta kod biçiminde geçen tanımlar; aynı yaprak adlı birden çok tanım varsa (`CompressionParameter.bounds`,
    # `DecompressionParameter.bounds`) üst adı da metinde geçenler seçilir.
    # Kapsanan bir tanımla aynı yaprak adı taşıyan ad o tanımı anar (`compress()` → modül işlevi, `ZstdCompressor.compress`
    # değil), meğer ki metinde üst adıyla birlikte (`ZstdCompressor.compress`) yazılmış olsun.
    kapsanan_yapraklar = {b.kimlik.split(".")[-1] for b in bolum_listesi if b.tur == "tanim" and id(b) in kapsanan}
    adayli = {}
    for bolum in kapsanmayan:
        if bolum.tur != "tanim":
            continue
        parca = bolum.kimlik.split(".")
        ust_adi = parca[-2] if len(parca) > 1 else ""
        if parca[-1] in adlar_metinde and (parca[-1] not in kapsanan_yapraklar or (ust_adi and f"{ust_adi}.{parca[-1]}" in kart_metni)):
            adayli.setdefault(parca[-1], []).append(bolum)
    birinci = []
    for yaprak, adaylar in adayli.items():
        nitelikli = [b for b in adaylar if len(b.kimlik.split(".")) > 1 and b.kimlik.split(".")[-2] in kart_metni]
        birinci.extend(nitelikli or (adaylar if len(adaylar) == 1 else []))
    birinci.sort(key=lambda b: b.bas)
    secilen = list(birinci)
    if not rapor:
        # (b) kapsanan bir tanımla aynı üst başlık altındaki üst düzey kardeş tanımlar.
        kapsanan_ustler = {id(b.ust) for b in bolum_listesi if id(b) in kapsanan and b.tur == "tanim" and b.ust is not None}
        ikinci = [b for b in kapsanmayan if b.tur == "tanim" and b.ust is not None and id(b.ust) in kapsanan_ustler
                  and b.ust.tur == "baslik"]
        # (c) kalan başlıklar ve üstü kapsanmamış üst düzey tanımlar, belge sırasıyla.
        ucuncu = [b for b in kapsanmayan if (b.tur == "baslik" and b.duzey >= 2 and not _DUSUK_DEGER.match(b.baslik))
                  or b.tur == "madde"
                  or (b.tur == "tanim" and (b.ust is None or b.ust.tur == "baslik") and not ust_kapsanmis_mi(b))]
        for bolum in ikinci + ucuncu:
            if bolum not in secilen:
                secilen.append(bolum)
    # Gösterilen bir tanımın alt tanımları ayrıca yazılmaz (adı geçenler dışında).
    gosterilen = []
    for bolum in secilen:
        ata, alt = bolum.ust, False
        while ata is not None:
            if ata in gosterilen:
                alt = True
                break
            ata = ata.ust
        if not alt or bolum in birinci:
            gosterilen.append(bolum)
    adlar = list(dict.fromkeys(_ad(b, onek) for b in gosterilen))
    sayilan = len(adlar) if rapor else len({_ad(b, onek) for b in kapsanmayan
                                             if b.tur == "baslik" or b.ust is None or b.ust.tur == "baslik"} | set(adlar))
    return {"bolum": len(bolum_listesi), "kapsanan": len(kapsanan), "kapsanmayan": sayilan, "adlar": adlar[:en_cok]}


def paket_kapsami(paket, sonuclar, *, en_cok=EN_COK_AD):
    """Kapı için: her erişilen kaynak sayfasında paketin alıntılarına göre kapsam dışı bölümler.

    Dönüş: {"kaynaklar": [{"url", "bolum", "kapsanan", "kapsanmayan", "adlar"}], "adlar": [birleşik ≤ en_cok]}. Raporda
    ad, hangi sayfaya ait olduğu anlaşılsın diye sayfa adresinin son parçasıyla yazılır. Hesap hata verirse boş döner.
    """
    if not isinstance(paket, dict) or not isinstance(paket.get("claims"), list):
        return {"kaynaklar": [], "adlar": []}
    rapor = paket.get("kind") != "source_card"
    iddialar = [i for i in paket["claims"] if isinstance(i, dict) and isinstance(i.get("source_url"), str)]
    kart_metni = " ".join(str(i.get(alan) or "") for i in iddialar for alan in ("text", "quote"))
    kaynaklar, birlesik = [], []
    for url in dict.fromkeys(i["source_url"] for i in iddialar):
        kaynak = sonuclar.get(url)
        if kaynak is None or getattr(kaynak, "erisim", None) != "erisildi":
            continue
        try:
            liste = bolumler(kaynak.govde, kaynak.icerik_turu)
        except Exception:
            continue
        if not liste:
            continue
        sonuc = kapsam_disi(liste, [i.get("quote") for i in iddialar if i["source_url"] == url], kart_metni,
                            rapor=rapor, en_cok=en_cok)
        kaynaklar.append({"url": url, **sonuc})
        sayfa = url.rstrip("/").rsplit("/", 1)[-1][:40]
        birlesik.extend(f"{ad} ({sayfa})" if rapor else ad for ad in sonuc["adlar"])
    return {"kaynaklar": kaynaklar, "adlar": list(dict.fromkeys(birlesik))[:en_cok]}
