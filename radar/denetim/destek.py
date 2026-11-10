"""İddia–alıntı desteği önerisi (alpha.13b, madde 5; alpha.12 kabulünün önerisi): yalnız katkı önizlemesinde bilgi, ret değil.

İddia metnindeki kod adı ya da sayı alıntıda yoksa alıntının sayfadaki yerinin ±300 karakter çevresine bakılır: orada geçiyorsa
"alıntıyı genişlet" önerilir, geçmiyorsa "iddia alıntıdan fazlasını söylüyor olabilir" denir. K5 ölçümünde bu kural kapıda yanlış
ret üretti (kod adı alıntının hemen yanında duruyordu); bu yüzden kapıya konmaz, yalnız katkıcıya yol gösterir.
"""

from __future__ import annotations

import re

from .kapsam import _kod_bicimli_adlar
from .metin import gorunur_metin, normallestir

CEVRE = 300
_SAYI = re.compile(r"(?<![\w.])\d+(?:\.\d+)*(?![\w.])")


def _gerekenler(iddia):
    """İddia metninin alıntıda da geçmesi gereken parçaları: kod biçimli adlar ve sayılar. İddianın kapsam sürümleri
    (`applies_to`, ör. "3.14") sayılmaz; o bilgi kapsam alanındadır, alıntıda geçmesi beklenmez."""
    surumler = {str(o.get("versions")) for o in iddia.get("applies_to") or [] if isinstance(o, dict)}
    return sorted(_kod_bicimli_adlar(iddia["text"]) | {s for s in _SAYI.findall(iddia["text"]) if s not in surumler})


def oneriler(paket, sonuclar):
    """Her iddia için: alıntıda olmayan adlar ve ne yapılacağı. Dönüş listesi, sorunsuz iddialar yazılmaz."""
    iddialar = paket.get("claims") if isinstance(paket, dict) and isinstance(paket.get("claims"), list) else []
    metinler, sonuc = {}, []
    for sira, iddia in enumerate(iddialar):
        if not isinstance(iddia, dict) or not all(isinstance(iddia.get(a), str) for a in ("text", "quote", "source_url")):
            continue
        alinti = normallestir(iddia["quote"])
        eksik = [ad for ad in _gerekenler(iddia) if ad not in alinti]
        if not eksik:
            continue
        kaynak = sonuclar.get(iddia["source_url"])
        if kaynak is None or getattr(kaynak, "erisim", None) != "erisildi":
            sonuc.append({"iddia": sira, "eksik": eksik, "oneri": "kaynak_yok"})
            continue
        if iddia["source_url"] not in metinler:
            metinler[iddia["source_url"]] = normallestir(gorunur_metin(kaynak.govde, kaynak.icerik_turu))
        sayfa = metinler[iddia["source_url"]]
        yer = sayfa.find(alinti)
        if yer < 0:
            continue  # alıntı bulunamadıysa alıntı denetimi zaten söyler
        cevre = sayfa[max(0, yer - CEVRE):yer + len(alinti) + CEVRE]
        yakinda = [ad for ad in eksik if ad in cevre]
        uzakta = [ad for ad in eksik if ad not in cevre]
        if yakinda:
            sonuc.append({"iddia": sira, "eksik": yakinda, "oneri": "alintiyi_genislet"})
        if uzakta:
            sonuc.append({"iddia": sira, "eksik": uzakta, "oneri": "iddia_alintidan_fazla"})
    return sonuc
