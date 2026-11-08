"""Tek bir denetimin sonucunu tutar: durum (geçti/uyarı/kaldı/denenemedi), neden kodları, açıklama ve ayrıntı."""

from __future__ import annotations

from dataclasses import dataclass, field

from .nedenler import NEDENLER, anlam

GECTI = "gecti"
UYARI = "uyari"
KALDI = "kaldi"
DENENEMEDI = "denenemedi"

# Birden çok bulgu olduğunda en ağır olan denetimin durumunu belirler.
_AGIRLIK = {GECTI: 0, UYARI: 1, DENENEMEDI: 2, KALDI: 3}


@dataclass
class Denetim:
    sira: int
    ad: str
    baslik: str
    engelleyici: bool = True
    durum: str = GECTI
    bulgular: list = field(default_factory=list)
    ayrinti: list = field(default_factory=list)
    gecti_aciklamasi: str = ""

    def _ekle(self, seviye, kod, aciklama=None):
        if kod not in NEDENLER:
            raise KeyError(f"Kayıtsız neden kodu: {kod}")
        metin = aciklama or anlam(kod)
        if not any(b["kod"] == kod and b["aciklama"] == metin for b in self.bulgular):
            self.bulgular.append({"seviye": seviye, "kod": kod, "aciklama": metin})
        if _AGIRLIK[seviye] > _AGIRLIK[self.durum]:
            self.durum = seviye

    def kal(self, kod, aciklama=None):
        self._ekle(KALDI, kod, aciklama)

    def uyar(self, kod, aciklama=None):
        self._ekle(UYARI, kod, aciklama)

    def denenemedi(self, kod, aciklama=None):
        self._ekle(DENENEMEDI, kod, aciklama)

    @property
    def nedenler(self):
        sirali = []
        for bulgu in self.bulgular:
            if bulgu["kod"] not in sirali:
                sirali.append(bulgu["kod"])
        return sirali

    def aciklama(self):
        if not self.bulgular:
            return self.gecti_aciklamasi or "Sorun bulunmadı."
        return " ".join(dict.fromkeys(b["aciklama"] for b in self.bulgular))

    def sozluk(self):
        return {
            "sira": self.sira,
            "ad": self.ad,
            "baslik": self.baslik,
            "engelleyici": self.engelleyici,
            "durum": self.durum,
            "nedenler": self.nedenler,
            "aciklama": self.aciklama(),
            "bulgular": self.bulgular,
            "ayrinti": self.ayrinti,
        }
