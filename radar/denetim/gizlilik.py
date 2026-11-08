"""Paketteki gizli bilgi, kişisel veri ve görünmez karakterleri bilinen kalıplarla arar; bulunan değeri asla yazmaz, yalnız türünü ve konumunu verir."""

from __future__ import annotations

import base64
import binascii
import html
import ipaddress
import re
import unicodedata
from urllib.parse import unquote

from .sonuc import Denetim

# Bu denetim bir kalıp listesidir: listede olmayan biçimde yazılmış gizli bilgiyi (ad, adres, müşteri adı, yeni bir
# jeton biçimi, özel bir disk yolu) yakalamaz. README §3 ve nihat-icin-ozet.md bunu "bilinen kalıplar" diye yazar.

ADRES_ALANLARI = {"source_url", "url", "terms_url"}
_GUVENLI_AD = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,40}$")

_YER_TUTUCU = re.compile(r"^(?:[A-Z][A-Z0-9_]*|<[^>]*>|\$\{?[A-Za-z_]+\}?|x+|\*+|\.\.\.)$")
_PAROLA_YER_TUTUCU = ("your", "my", "example", "sample", "dummy", "placeholder", "changeme", "password", "secret",
                      "redacted", "hidden", "string", "required", "optional", "none", "null", "true", "false", "test")


def _gizliye_benzer(deger):
    """Atamadaki değerin gerçek bir gizli bilgiye benzeyip benzemediği (yer tutucuları ayıklar)."""
    if _YER_TUTUCU.match(deger):
        return False
    return any(c.isdigit() for c in deger) and any(c.isalpha() for c in deger)


def _parolaya_benzer(deger):
    """Parola atamasında rakamsız uzun değer de parola sayılır ('correcthorsebatterystaple'); yer tutucular hariç."""
    if _YER_TUTUCU.match(deger) or deger.lower().lstrip("<{$_").startswith(_PAROLA_YER_TUTUCU):
        return False
    return _gizliye_benzer(deger) or (len(deger) >= 12 and deger.isalpha())


# Ayırt edici önekli anahtar ve jetonlar. Bölünmüş (iki alana ya da boşlukla) yazımı da bunlarla aranır.
_ONEKLI = re.compile(
    r"\b(?:sk-(?:proj-|ant-)?[A-Za-z0-9_-]{16,}|nvapi-[A-Za-z0-9_-]{16,}|AKIA[A-Z0-9]{16}|AIza[0-9A-Za-z_-]{35}|"
    r"xox[abposr]-[A-Za-z0-9-]{10,}|[rs]k_(?:live|test)_[A-Za-z0-9]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|glpat-[A-Za-z0-9_-]{20,}|npm_[A-Za-z0-9]{36}|pypi-AgEIcHlwaS5vcmc[A-Za-z0-9_-]{20,}|"
    r"hf_[A-Za-z0-9]{30,})")
_ONEK_BOSLUK = re.compile(r"\b(sk-(?:proj-|ant-)?|nvapi-|gh[pousr]_|github_pat_|glpat-|npm_|hf_|xox[abposr]-|[rs]k_(?:live|test)_)"
                          r"\s+(?=[A-Za-z0-9_-]{16,})")

# (tür, kalıp, ek süzgeç). İlk kalıplar Codex'in paylasim_gizliligi.py kalıplarından türetildi; genel belgede sık geçen yer
# tutucuları (YOUR_TOKEN, <token>) ret saymamak için daraltıldı. v0.2'de kabul raporu O4'teki kalıplar eklendi.
_KALIPLAR = [
    ("ozel_anahtar", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----"), None),
    ("anahtar", re.compile(r"\b(?:sk-(?:proj-|ant-)?[A-Za-z0-9_-]{16,}|nvapi-[A-Za-z0-9_-]{16,}|AKIA[A-Z0-9]{16}|"
                           r"AIza[0-9A-Za-z_-]{35}|xox[abposr]-[A-Za-z0-9-]{10,}|[rs]k_(?:live|test)_[A-Za-z0-9]{16,})\b"), None),
    ("jeton", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|glpat-[A-Za-z0-9_-]{20,}|"
                         r"npm_[A-Za-z0-9]{36}|pypi-AgEIcHlwaS5vcmc[A-Za-z0-9_-]{20,}|hf_[A-Za-z0-9]{30,}|"
                         r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})"), None),
    # Telegram bot jetonu: sayısal kimlik + ':' + ~35 karakter.
    ("jeton", re.compile(r"(?<![\w:])\d{8,10}:[A-Za-z0-9_-]{30,}(?![\w-])"), None),
    # Gizli anahtar taşıyan webhook adresleri.
    ("jeton", re.compile(r"(?i)\bhttps?://hooks\.slack\.com/(?:services|workflows|triggers)/[A-Za-z0-9/_-]{10,}|"
                         r"\bhttps?://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_-]{20,}"), None),
    ("jeton", re.compile(r"\bBearer\s+([A-Za-z0-9._~+/=-]{16,})"), 1),
    ("anahtar", re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|auth[_-]?token|client[_-]?secret|"
                           r"secret[_-]?key|token|secret|password|passwd)\b[\"']?\s*[:=]\s*[\"']?([A-Za-z0-9_\-./+=]{12,})"), 1),
    ("anahtar", re.compile(r"(?i)\b(?:aws[_-]?)?secret[_-]?access[_-]?key\b[\"']?\s*[:=]\s*[\"']?([A-Za-z0-9/+=]{30,})"), 1),
    ("anahtar", re.compile(r"(?i)\b(?:password|passwd|pwd|parola|şifre|sifre)\b[\"']?\s*[:=]\s*[\"']?([^\s\"'<>,;]{8,})"), "parola"),
    ("eposta", re.compile(r"\b[\w.!#$%&'*+/=?^`{|}~-]+@(?!example\.(?:com|org|net)\b)[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}\b"), None),
    # Gizlenmiş e-posta: ad [at] alan [dot] com, ad (at) alan (dot) com, ad at alan dot com.
    ("eposta", re.compile(r"(?i)\b[\w.+-]+\s*[\[\(\{<]\s*(?:at|@)\s*[\]\)\}>]\s*(?!example\b)[\w-]+"
                          r"(?:\s*(?:[\[\(\{<]\s*(?:dot|\.)\s*[\]\)\}>]|\.)\s*[\w-]+)+"), None),
    ("eposta", re.compile(r"(?i)\b[\w.+-]+\s+at\s+(?!example\b)[\w-]+(?:\s+dot\s+[\w-]+)*\s+dot\s+"
                          r"(?:com|net|org|io|dev|edu|gov|co|me|info|tr|de|uk)\b"), None),
    ("telefon", re.compile(r"(?<![\w.:/-])(?:\+\d{1,3}[\s().-]*)?(?:\(?\d{2,4}\)?[\s.-]+){2,4}\d{2,4}(?![\w:/-]|\.\w)"), "telefon"),
    ("telefon", re.compile(r"(?<![\w.:/-])\+\d{10,15}(?![\w:/-]|\.\w)"), None),
    # Türkiye biçiminde bitişik numara: 05321234567, 905321234567 (sabit hat 02xx/03xx/04xx de).
    ("telefon", re.compile(r"(?<![\w.:/+-])(?:0|90)[2-5]\d{9}(?![\w:/-]|\.\w)"), None),
    ("yerel_yol", re.compile(r"(?i)(?:\b[A-Z]:[\\/](?:Users|Documents and Settings)[\\/]|(?<![\w.])/(?:home|Users)/|"
                             r"(?<![\w.])/mnt/[a-z]/(?:Users|home)/)"
                             r"(?!(?:user|username|you|yourname|me|runner|ubuntu|ec2-user|vscode|node|name|<[^>]+>|\$USER|%USERNAME%)[\\/\s])"
                             r"[^\\/\s\"<>]+"), None),
    ("yerel_yol", re.compile(r"(?:\\\\[A-Za-z0-9._$@-]+\\[^\s\"<>]+|\bfile://[^\s\"<>]+)"), None),
    ("ozel_alan_adi", re.compile(r"(?i)(?<![\w.-])(?:[a-z0-9-]+\.)+(?:local|internal|lan|home|corp|intranet|localdomain|home\.arpa)(?![\w-]|\.\w)"), None),
    # v0.2.1 (yeniden sınama O-4b, Y-2b): Azure anahtarı; sağlama hesabıyla IBAN, ödeme kartı, T.C. kimlik; özel ağ IP'si.
    ("anahtar", re.compile(r"(?i)\b(?:AccountKey|SharedAccessKey)\s*=\s*([A-Za-z0-9+/]{30,}={0,2})"), 1),
    ("mali_bilgi", re.compile(r"\b([A-Z]{2}\d{2}(?: ?[A-Z0-9]){11,32})"), "iban"),
    ("mali_bilgi", re.compile(r"(?<![\d-])(?:\d[ -]?){12,18}\d(?![\d-])"), "kart"),
    ("kimlik_numarasi", re.compile(r"(?<![\d.,/-])[1-9]\d{10}(?![\d,/-]|\.\d)"), "tc"),
    ("ozel_ip", re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?!\d|\.\d|/\d)"), "ozel_ip"),
    ("ozel_ip", re.compile(r"(?i)(?<![\w:])(?:f[cd][0-9a-f]{2}|fe[89ab][0-9a-f]):[0-9a-f:]{2,}(?![\w:])"), "ozel_ip"),
]

_ADRESTE_ATLANAN = {"telefon", "yerel_yol", "ozel_alan_adi", "mali_bilgi", "kimlik_numarasi", "ozel_ip"}

# IBAN uzunlukları (ülke kodu → karakter); listede olmayan ülke kodu IBAN sayılmaz.
_IBAN_UZUNLUK = {
    "AD": 24, "AE": 23, "AL": 28, "AT": 20, "AZ": 28, "BA": 20, "BE": 16, "BG": 22, "BH": 22, "BR": 29, "BY": 28, "CH": 21,
    "CR": 22, "CY": 28, "CZ": 24, "DE": 22, "DK": 18, "DO": 28, "EE": 20, "EG": 29, "ES": 24, "FI": 18, "FO": 18, "FR": 27,
    "GB": 22, "GE": 22, "GI": 23, "GL": 18, "GR": 27, "GT": 28, "HR": 21, "HU": 28, "IE": 22, "IL": 23, "IQ": 23, "IS": 26,
    "IT": 27, "JO": 30, "KW": 30, "KZ": 20, "LB": 28, "LC": 32, "LI": 21, "LT": 20, "LU": 20, "LV": 21, "MC": 27, "MD": 24,
    "ME": 22, "MK": 19, "MR": 27, "MT": 31, "MU": 30, "NL": 18, "NO": 15, "PK": 24, "PL": 28, "PS": 29, "PT": 25, "QA": 29,
    "RO": 24, "RS": 22, "SA": 24, "SC": 31, "SE": 24, "SI": 19, "SK": 24, "SM": 27, "ST": 25, "SV": 28, "TL": 23, "TN": 24,
    "TR": 26, "UA": 29, "VA": 22, "VG": 24, "XK": 20,
}
# Ödeme sağlayıcılarının herkese açık sınama kartları gizli bilgi değildir (belgelerde geçer).
_SINAMA_KARTLARI = {"4111111111111111", "4242424242424242", "4000056655665556", "5555555555554444", "2223003122003222",
                    "5200828282828210", "5105105105105100", "378282246310005", "371449635398431", "6011111111111117",
                    "6011000990139424", "3056930009020004", "36227206271667", "3566002020360505", "4012888888881881",
                    "4000000000000002", "4000000000009995", "4000002500003155", "4012000033330026"}
_OZEL_AGLAR = [ipaddress.ip_network(a) for a in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16",
                                                  "100.64.0.0/10", "fc00::/7", "fe80::/10")]
_SURUM_BAGLAMI = re.compile(r"(?i)(?:\bv|version|release|ver\.?|upgrade to|update to|build|tag)\s*$")


def _iban_mi(eslesme):
    yazi = eslesme.group(1).replace(" ", "")
    uzunluk = _IBAN_UZUNLUK.get(yazi[:2])
    if not uzunluk or len(yazi) < uzunluk:
        return False
    yazi = yazi[:uzunluk]
    sayi = "".join(str(int(c, 36)) for c in yazi[4:] + yazi[:4])
    return int(sayi) % 97 == 1


def _luhn(rakamlar):
    toplam = 0
    for sira, rakam in enumerate(reversed(rakamlar)):
        n = int(rakam)
        if sira % 2:
            n = n * 2 - 9 if n > 4 else n * 2
        toplam += n
    return toplam % 10 == 0


def _kart_mi(eslesme):
    rakamlar = re.sub(r"\D", "", eslesme.group())
    if not 13 <= len(rakamlar) <= 19 or rakamlar in _SINAMA_KARTLARI:
        return False
    on = int(rakamlar[:2])
    iin = (rakamlar[0] == "4" or 51 <= on <= 55 or 2221 <= int(rakamlar[:4]) <= 2720 or on in (34, 37, 35, 36, 38, 30)
           or rakamlar.startswith(("6011", "65")) or 644 <= int(rakamlar[:3]) <= 649 or rakamlar.startswith("62"))
    return iin and _luhn(rakamlar)


def _tc_mi(eslesme):
    d = [int(c) for c in eslesme.group()]
    return (sum(d[0:9:2]) * 7 - sum(d[1:8:2])) % 10 == d[9] and sum(d[:10]) % 10 == d[10]


def _ozel_ip_mi(eslesme):
    if _SURUM_BAGLAMI.search(eslesme.string[max(0, eslesme.start() - 20):eslesme.start()]):
        return False                      # "version 10.0.0.1" bir IP değildir
    try:
        ip = ipaddress.ip_address(eslesme.group())
    except ValueError:
        return False
    return any(ip in ag for ag in _OZEL_AGLAR if ag.version == ip.version)
_BASE64 = re.compile(r"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/_-]{24,}={0,2}(?![A-Za-z0-9+/=_-])")


def _suzgecten_gecer(eslesme, suzgec):
    if suzgec == "telefon":
        return 10 <= sum(c.isdigit() for c in eslesme.group()) <= 15
    if suzgec == "parola":
        return _parolaya_benzer(eslesme.group(1))
    if suzgec in _DOGRULAYICI:
        return _DOGRULAYICI[suzgec](eslesme)
    if isinstance(suzgec, int):
        return _gizliye_benzer(eslesme.group(suzgec))
    return True


_DOGRULAYICI = {"iban": _iban_mi, "kart": _kart_mi, "tc": _tc_mi, "ozel_ip": _ozel_ip_mi}


def _varyantlar(metin):
    """Denetlenecek biçimler: ham metin, NFKC (tam genişlik '＠' → '@'), HTML varlığı çözülmüş ('&#64;' → '@'),
    yüzde kodu çözülmüş ('%40' → '@', 'sk%2Dproj' → 'sk-proj') ve önekten sonraki boşluğu atılmış ('sk-proj- …')."""
    varyantlar = [metin]
    nfkc = unicodedata.normalize("NFKC", metin)
    cozulmus = unicodedata.normalize("NFKC", html.unescape(nfkc))
    for _ in range(3):
        if "%" not in cozulmus:
            break
        yeni = unquote(cozulmus)
        if yeni == cozulmus:
            break
        cozulmus = yeni
    for aday in (nfkc, cozulmus, _ONEK_BOSLUK.sub(r"\1", cozulmus)):
        if aday not in varyantlar:
            varyantlar.append(aday)
    return varyantlar


def _base64_icerikleri(metin):
    """Metindeki base64 görünümlü parçaların çözülmüş, yazdırılabilir ASCII hâlleri."""
    for eslesme in _BASE64.finditer(metin):
        parca = eslesme.group()
        for cozucu in (base64.b64decode, base64.urlsafe_b64decode):
            try:
                ham = cozucu(parca + "=" * (-len(parca) % 4))
            except (binascii.Error, ValueError):
                continue
            if len(ham) >= 16 and all(32 <= b < 127 for b in ham):
                yield eslesme.start(), ham.decode("ascii")
                break


def gizli_kalip_bul(metin, *, adres=False):
    """[(tür, konum)] döndürür; değerin kendisini döndürmez. Konum, kalıbın bulunduğu biçimdeki (ham ya da çözülmüş) yerdir."""
    bulunan = []
    for varyant in _varyantlar(metin):
        for tur, kalip, suzgec in _KALIPLAR:
            if adres and tur in _ADRESTE_ATLANAN:
                continue
            for eslesme in kalip.finditer(varyant):
                if _suzgecten_gecer(eslesme, suzgec):
                    bulunan.append((tur, eslesme.start()))
    for konum, icerik in _base64_icerikleri(metin):
        for tur, kalip, suzgec in _KALIPLAR[:3]:            # base64 içinde anahtar, jeton, özel anahtar bloğu
            if any(_suzgecten_gecer(e, suzgec) for e in kalip.finditer(icerik)):
                bulunan.append((tur, konum))
    tekil = {}
    for tur, konum in bulunan:
        if tur not in tekil or konum < tekil[tur]:
            tekil[tur] = konum
    return sorted(tekil.items(), key=lambda t: t[1])


def gizle(metin):
    """Gösterilecek bir metindeki gizli kalıpları [gizlendi] ile değiştirir."""
    for _, kalip, suzgec in _KALIPLAR:
        metin = kalip.sub(lambda e, s=suzgec: "[gizlendi]" if _suzgecten_gecer(e, s) else e.group(), metin)
    return metin


_YON = set(range(0x200E, 0x2010)) | set(range(0x202A, 0x202F)) | set(range(0x2066, 0x206A)) | {0x061C}

# Unicode Default_Ignorable_Code_Point kümesi (DerivedCoreProperties.txt, Unicode 15.1). Kategorisi Cf olmayanlar da
# (U+034F, U+115F, U+3164, U+FFA0, U+180B …) çizilmez ya da boşluk gibi görünür.
_YOK_SAYILAN = [(0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160), (0x17B4, 0x17B5),
                (0x180B, 0x180F), (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x206F), (0x3164, 0x3164),
                (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF), (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8), (0x1BCA0, 0x1BCA3),
                (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF)]
# Default_Ignorable olmayan ama boş görünen karakterler.
_BOS_GORUNEN = {0x2800}


def gorunmez_tur(karakter):
    """Görünmez ya da denetim karakterinin türünü döndürür; olağan karakter için None."""
    kod = ord(karakter)
    if 0xFE00 <= kod <= 0xFE0F or 0xE0100 <= kod <= 0xE01EF:
        return "varyasyon_secici"
    if 0xE0000 <= kod <= 0xE007F:
        return "etiket_blogu"
    if kod in _YON:
        return "yon_isareti"
    kategori = unicodedata.category(karakter)
    if kategori == "Cf":
        return "bicim_denetim"
    if kategori in ("Zl", "Zp"):
        return "satir_ayirici"
    if kategori == "Co":
        return "ozel_kullanim"
    if kategori == "Cs":
        return "vekil"
    if kategori == "Cc" and karakter not in "\n\t\r":
        return "denetim_karakteri"
    if any(bas <= kod <= son for bas, son in _YOK_SAYILAN):
        return "yok_sayilan"
    if kod in _BOS_GORUNEN:
        return "bos_gorunen"
    return None


def _emoji_mi(karakter):
    return unicodedata.category(karakter) == "So" or 0x1F000 <= ord(karakter) <= 0x1FAFF


def emoji_hosgorusu(metin, sira):
    """O-4c: emoji dizisinin parçası olan U+FE0F ve U+200D hoş görülür; tek başına, yinelenen ya da harf arasındaki ret kalır.
    U+FE0F: hemen önünde bir sembol/emoji (ya da ardından U+20E3 gelen 0–9, #, *) olmalı.
    U+200D: iki yanında emoji olmalı (öndeki U+FE0F ve ten rengi değiştiricisi atlanır)."""
    karakter = metin[sira]
    once = metin[sira - 1] if sira > 0 else ""
    sonra = metin[sira + 1] if sira + 1 < len(metin) else ""
    if karakter == "️":
        if not once or once == "️":
            return False
        return _emoji_mi(once) or (once in "0123456789#*" and sonra == "⃣")
    if karakter == "‍":
        j = sira - 1
        while j >= 0 and (metin[j] == "️" or 0x1F3FB <= ord(metin[j]) <= 0x1F3FF):
            j -= 1
        return j >= 0 and _emoji_mi(metin[j]) and bool(sonra) and _emoji_mi(sonra)
    return False


def guvenli_ad(ad):
    """Alan adı sonuca yazılabilir mi: kısa tanımlayıcı ve içinde gizli kalıp yok."""
    return isinstance(ad, str) and bool(_GUVENLI_AD.match(ad)) and len(ad) <= 32 and not gizli_kalip_bul(ad)


def metinleri_gez(deger, yol="paket"):
    """Paketteki bütün metinleri (yol, anahtar adı, metin) olarak verir; sözlük anahtarlarını da gezer."""
    if isinstance(deger, dict):
        for sira, (anahtar, alt) in enumerate(deger.items()):
            if isinstance(anahtar, str):
                yield f"{yol}.<{sira + 1}. alan adı>", None, anahtar
            ad = anahtar if guvenli_ad(anahtar) else f"<{sira + 1}. alan>"
            yield from metinleri_gez(alt, f"{yol}.{ad}")
    elif isinstance(deger, list):
        for sira, alt in enumerate(deger):
            yield from metinleri_gez(alt, f"{yol}[{sira}]")
    elif isinstance(deger, str):
        yield yol, yol.rsplit(".", 1)[-1], deger


def denetle(paket):
    d = Denetim(5, "gizli_bilgi", "Gizli bilgi, özel veri ve görünmez karakter (bilinen kalıplar)",
                gecti_aciklamasi="Bilinen kalıplardan hiçbiri (anahtar, jeton, e-posta, telefon, yerel yol, özel alan adı, "
                                 "görünmez karakter) bulunmadı. Kalıp dışı kişisel veri yakalanmaz.")
    metinler = []
    for yol, alan, metin in metinleri_gez(paket):
        metinler.append(metin)
        gorunmezler = {}
        for sira, karakter in enumerate(metin):
            tur = gorunmez_tur(karakter)
            if tur and karakter in "️‍" and emoji_hosgorusu(metin, sira):
                continue
            if tur:
                kayit = gorunmezler.setdefault(tur, {"ilk": sira, "adet": 0, "kod_noktalari": []})
                kayit["adet"] += 1
                nokta = f"U+{ord(karakter):04X}"
                if nokta not in kayit["kod_noktalari"] and len(kayit["kod_noktalari"]) < 5:
                    kayit["kod_noktalari"].append(nokta)
        for tur, kayit in gorunmezler.items():
            kod = "gizli.denetim_karakteri" if tur == "denetim_karakteri" else "gizli.gorunmez_karakter"
            d.kal(kod)
            d.ayrinti.append({"tur": tur, "konum": yol, "karakter": kayit["ilk"], "adet": kayit["adet"],
                              "kod_noktalari": kayit["kod_noktalari"]})
        for tur, konum in gizli_kalip_bul(metin, adres=alan in ADRES_ALANLARI):
            d.kal(f"gizli.{tur}")
            d.ayrinti.append({"tur": tur, "konum": yol, "karakter": konum})
    # İki alana bölünmüş önekli anahtar: alanlar yan yana eklenince tek tek bakıldığından fazla eşleşme çıkıyorsa ret.
    birlesik = "".join(metinler)
    if len(_ONEKLI.findall(birlesik)) > sum(len(_ONEKLI.findall(m)) for m in metinler):
        d.kal("gizli.anahtar", "Alanlara bölünmüş bir anahtar ya da jeton kalıbı bulundu.")
        d.ayrinti.append({"tur": "anahtar", "konum": "paket.<alanlar arası>", "karakter": None})
    return d
