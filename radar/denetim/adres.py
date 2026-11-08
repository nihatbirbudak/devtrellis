"""Kaynak adresinin herkese açık bir HTTPS sayfası olduğunu ağsız biçim kurallarıyla ve DNS çözümünden sonra IP denetimiyle sınar."""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import unquote, urlsplit

from radar.kamu_sorgu import sorgu_izinli

from .gizlilik import gizli_kalip_bul, gorunmez_tur

OZEL_SONEKLER = (".localhost", ".local", ".internal", ".lan", ".home", ".home.arpa", ".corp", ".intranet",
                 ".localdomain", ".test", ".example", ".invalid", ".onion", ".arpa")
KISALTICILAR = {"bit.ly", "t.co", "tinyurl.com", "goo.gl", "ow.ly", "is.gd", "buff.ly", "rebrand.ly", "cutt.ly",
                "shorturl.at", "rb.gy", "t.ly", "tiny.cc", "lnkd.in", "s.id", "v.gd", "trib.al", "shorte.st"}
_ETIKET = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_SAYISAL_AD = re.compile(r"^(?:0x[0-9a-f]+|\d+)(?:\.(?:0x[0-9a-f]+|\d+)){0,3}$")


def genel_ip_mi(deger):
    """IP adresinin genel internete ait olup olmadığını söyler (özel, yerel, ayrılmış ve geçiş adresleri hayır)."""
    try:
        ip = ipaddress.ip_address(deger.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None or ip.sixtofour is not None or ip.teredo is not None:
            return False
    return bool(ip.is_global and not ip.is_multicast and not ip.is_reserved)


def _ip_mi(ad):
    try:
        ipaddress.ip_address(ad.strip("[]"))
        return True
    except ValueError:
        # 2130706433, 0x7f.1 gibi sayısal yazımlar da IP sayılır.
        return bool(_SAYISAL_AD.match(ad))


_KODLU_AYRAC = re.compile(r"%(?:2f|5c)", re.I)
_BOS_PARCA = re.compile(r"(?<!:)//")      # "://" (arşiv adresinde gömülü adres: /web/2024/https://...) boş parça sayılmaz


def yol_bicimi_bozuk(yol):
    """Yolda '.' / '..' parçası (yüzde kodlu dahil), boş parça (//) ya da kodlanmış bölü var mı (O2).
    Sunucu bunları çoğu zaman temizleyip başka sayfa verir; robots.txt kuralı ise ham yola bakar."""
    if _KODLU_AYRAC.search(yol) or _BOS_PARCA.search(yol):
        return True
    parcalar = unquote(yol).split("/")
    return any(p in (".", "..") for p in parcalar)


def statik_denetle(url, *, sorgu_serbest=False):
    """Ağa çıkmadan (kod, None) ya da (None, alan_adı) döndürür; kod bir 'acik.*' neden kodudur."""
    if not isinstance(url, str) or not url or len(url) > 2048:
        return "acik.ayristirilamadi", None
    if any(ord(c) <= 32 or ord(c) >= 127 or c == "\\" for c in url):
        return "acik.gecersiz_karakter", None
    try:
        parca = urlsplit(url)
        port = parca.port
        ad = (parca.hostname or "").lower()
    except ValueError:
        return "acik.ayristirilamadi", None
    if parca.scheme != "https":
        return "acik.https_degil", None
    if parca.username is not None or parca.password is not None or "@" in parca.netloc:
        return "acik.kimlik_bilgisi", None
    if port not in (None, 443):
        return "acik.standart_disi_port", None
    ad = ad.rstrip(".")
    if not ad:
        return "acik.ayristirilamadi", None
    if _ip_mi(ad):
        return "acik.ip_adresi", None
    if ad == "localhost" or ad.endswith(OZEL_SONEKLER) or "." not in ad:
        return "acik.yerel_ad", None
    etiketler = ad.split(".")
    if not all(_ETIKET.match(e) for e in etiketler) or not etiketler[-1].isalpha() or len(etiketler[-1]) < 2:
        return "acik.gecersiz_alan_adi", None
    if any(e.startswith("xn--") for e in etiketler):
        return "acik.punycode", None
    if ad in KISALTICILAR:
        return "acik.kisaltici", None
    # v0.2.2 (yönetici kararı 7 Kasım 22:4x): K0'ın beş sürüm/dil parametresi izinli (radar.kamu_sorgu, tek sabit); gerisi ret.
    if (url.split("#", 1)[0].endswith("?") or (parca.query and not sorgu_izinli(parca.query))) and not sorgu_serbest:
        return "acik.sorgu_parametresi", None
    if parca.fragment or "#" in url:
        return "acik.parca_isareti", None
    if yol_bicimi_bozuk(parca.path):
        return "acik.yol_bicimi", None
    cozulmus = url
    for _ in range(3):
        yeni = unquote(cozulmus)
        if yeni == cozulmus:
            break
        cozulmus = yeni
    if any(gorunmez_tur(c) for c in cozulmus) or any(ord(c) < 32 for c in cozulmus):
        return "acik.gecersiz_karakter", None
    if gizli_kalip_bul(cozulmus, adres=True):
        return "acik.kimlik_tasiyan_adres", None
    return None, ad


def gosterilebilir(url):
    """Sonuca yalnız biçim denetiminden geçen adres yazılır; geçmeyen adres (gizli bilgi taşıyabilir) gösterilmez."""
    return url if statik_denetle(url)[0] is None else None


def denetle(kaynaklar, *, getirici=None, ag=True, ag_atla_kodu=None):
    """2. denetim: her kaynak için biçim kuralları, sonra (ağ açıksa) DNS çözümü ve çözülen IP'lerin genel olması."""
    from .getirme import AgHatasi, gecici_mi
    from .sonuc import Denetim

    d = Denetim(2, "kaynak_acikligi", "Kaynağın herkese açık olması",
                gecti_aciklamasi="Bütün kaynaklar standart HTTPS adresinde, kamusal alan adında ve genel IP'ye çözülüyor.")
    for sira, url in enumerate(kaynaklar):
        satir = {"kaynak": f"k{sira + 1}", "url": gosterilebilir(url)}
        d.ayrinti.append(satir)
        kod, ad = statik_denetle(url)
        if kod:
            satir.update(sonuc="kaldi", neden=kod)
            d.kal(kod)
            continue
        if not ag or getirici is None:
            kod = ag_atla_kodu or "acik.ag_kapali"
            satir.update(sonuc="denenemedi", neden=kod)
            d.denenemedi(kod)
            continue
        try:
            getirici.coz(ad)
            satir.update(sonuc="gecti")
        except AgHatasi as hata:
            if gecici_mi(hata.kod):
                satir.update(sonuc="denenemedi", neden=hata.kod)
                d.denenemedi(hata.kod)
            else:
                satir.update(sonuc="kaldi", neden=hata.kod)
                d.kal(hata.kod)
    return d
