"""robots.txt kurallarını RFC 9309'a göre okur: kullanıcı aracı grubu, joker (*) ve satır sonu ($), en uzun kural kazanır; yol ve kural yüzde kodu eşitlenerek karşılaştırılır."""

from __future__ import annotations

import re

_AYRILMAMIS = set(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
_YUZDE = re.compile(r"%([0-9A-Fa-f]{2})")
_URUN = re.compile(r"^[A-Za-z_-]+")


def esitle(yol):
    """RFC 9309 §2.2.2: ayrılmamış karakterin yüzde kodu çözülür, öteki kodlar büyük harfe çekilir, ASCII dışı kodlanır.
    Böylece '/%70rivate/' ile '/private/' aynı yol sayılır (O2)."""
    def coz(eslesme):
        bayt = int(eslesme.group(1), 16)
        return chr(bayt) if bayt in _AYRILMAMIS else "%" + eslesme.group(1).upper()
    yol = "".join(c if ord(c) < 128 else "".join(f"%{b:02X}" for b in c.encode("utf-8")) for c in yol)
    return _YUZDE.sub(coz, yol)


def urun_adi(deger):
    """'UfukRadar/0.1' gibi yazılmış grup adını ürün adına indirir (RFC 9309: yalnız harf, '_' ve '-'); '*' korunur."""
    deger = deger.strip()
    if deger.startswith("*"):
        return "*"
    eslesme = _URUN.match(deger)
    return eslesme.group().lower() if eslesme else deger.lower()


class RobotsKurallari:
    def __init__(self, gruplar):
        # gruplar: [(ajan_adlari:set, kurallar:[(izin:bool, kalip:str)], bekleme:float|None)]
        self.gruplar = gruplar

    @classmethod
    def ayristir(cls, metin):
        gruplar = []
        ajanlar, kurallar, bekleme = set(), [], None
        kural_goruldu = False
        metin = metin.lstrip("﻿")          # O-2b: baştaki BOM ilk User-agent satırını gizlemesin
        for satir in metin.splitlines():
            satir = satir.split("#", 1)[0].strip()
            if ":" not in satir:
                continue
            anahtar, deger = (p.strip() for p in satir.split(":", 1))
            anahtar = anahtar.lower()
            if anahtar == "user-agent":
                if kural_goruldu:
                    gruplar.append((ajanlar, kurallar, bekleme))
                    ajanlar, kurallar, bekleme, kural_goruldu = set(), [], None, False
                ajanlar.add(urun_adi(deger))
            elif anahtar in ("allow", "disallow"):
                if not ajanlar:
                    continue
                kural_goruldu = True
                if deger:
                    kurallar.append((anahtar == "allow", esitle(deger)))
            elif anahtar == "crawl-delay" and ajanlar:
                kural_goruldu = True
                try:
                    bekleme = float(deger)
                except ValueError:
                    pass
        if ajanlar:
            gruplar.append((ajanlar, kurallar, bekleme))
        return cls(gruplar)

    def _secilen(self, urun):
        urun = urun.lower()
        uyan = [g for g in self.gruplar if urun in g[0]]
        return uyan or [g for g in self.gruplar if "*" in g[0]]

    def izin_var_mi(self, urun, yol):
        """Yol (yol + sorgu) için izin; eşleşen kural yoksa izin vardır, eşit uzunlukta allow kazanır.
        Parça parametreleri (';x=1') atılmış yol da denetlenir: bazı sunucular onları atıp aynı sayfayı verir (O2l)."""
        sade = re.sub(r";[^/?]*", "", yol)
        if sade != yol and not self._izin(urun, sade):
            return False
        return self._izin(urun, yol)

    def _izin(self, urun, yol):
        yol = esitle(yol)
        if yol == "/robots.txt":
            return True
        en_iyi = None
        for _, kurallar, _ in self._secilen(urun):
            for izin, kalip in kurallar:
                if _eslesir(kalip, yol):
                    aday = (len(kalip), izin)
                    if en_iyi is None or aday > en_iyi:
                        en_iyi = aday
        return True if en_iyi is None else en_iyi[1]

    def bekleme(self, urun):
        degerler = [g[2] for g in self._secilen(urun) if g[2] is not None]
        return max(degerler) if degerler else 0.0


def _eslesir(kalip, yol):
    sonda = kalip.endswith("$")
    govde = kalip[:-1] if sonda else kalip
    duzenli = "".join(".*" if c == "*" else re.escape(c) for c in govde)
    return re.match(duzenli + ("$" if sonda else ""), yol) is not None
