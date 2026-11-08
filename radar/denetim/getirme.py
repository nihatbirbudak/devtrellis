"""Kaynakları kibarca getirir: robots.txt'e uyar, tanımlı kullanıcı aracı, süre ve boyut sınırı, site başına bekleme, aynı adres bir kez."""

from __future__ import annotations

import http.client
import json
import queue
import socket
import ssl
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

from .adres import genel_ip_mi, statik_denetle
from .ayarlar import KABUL, KULLANICI_ARACI, Ayarlar
from .robots import RobotsKurallari


class AgHatasi(Exception):
    """Ağ adımının neden koduyla başarısızlığı; ileti dış veriyi tekrarlamaz."""

    def __init__(self, kod):
        super().__init__(kod)
        self.kod = kod


@dataclass
class Yanit:
    durum: int
    basliklar: dict
    govde: bytes
    asildi: bool = False   # boyut sınırına gelindi, gövde kesik


@dataclass
class Sonuc:
    url: str
    erisim: str                    # "erisildi" | "kaldi" | "izin_vermiyor"
    neden: str | None = None       # neden kodu
    http_durum: int | None = None
    son_url: str | None = None
    icerik_turu: str = ""
    govde: bytes = field(default=b"", repr=False)
    asildi: bool = False
    zaman: str | None = None

    def ozet(self):
        return {"erisim": self.erisim, "neden": self.neden, "http_durum": self.http_durum,
                "son_url": self.son_url, "getirilme_zamani": self.zaman}


_IZIN_YOK = {"getirme.robots_izin_vermiyor", "getirme.erisim_kisitli", "getirme.giris_sayfasi"}

# Geçici ağ sorunları (D1): kalıcı ret değil "denenemedi" sayılır, hüküm en kötü "yayimlanamaz" olur ve sunucu sonra
# yeniden dener. Kapı kendisi yeniden denemez. DNS çözülememesi de buradadır: internet kesikken "ad yok" ile ayırt edilemez.
GECICI = {"getirme.baglanti", "getirme.zaman_asimi", "getirme.sunucu_hatasi", "getirme.hiz_siniri",
          "getirme.robots_gecici", "getirme.dns_cozulemedi", "acik.dns_cozulemedi"}


def gecici_mi(neden):
    return neden in GECICI


def _dns_coz(ad, zaman_asimi):
    """getaddrinfo'yu süre sınırıyla çağırır; bütün adresleri döndürür."""
    kuyruk = queue.Queue(maxsize=1)

    def coz():
        try:
            kuyruk.put([bilgi[4][0] for bilgi in socket.getaddrinfo(ad, 443, type=socket.SOCK_STREAM)])
        except Exception:
            kuyruk.put(None)

    threading.Thread(target=coz, daemon=True).start()
    try:
        adresler = kuyruk.get(timeout=zaman_asimi)
    except queue.Empty:
        raise AgHatasi("acik.dns_cozulemedi") from None
    if not adresler:
        raise AgHatasi("acik.dns_cozulemedi")
    return list(dict.fromkeys(adresler))


class _SabitIPBaglantisi(http.client.HTTPSConnection):
    """TLS kimliğini alan adıyla doğrular ama yalnız önceden denetlenen IP'ye bağlanır (DNS yeniden bağlama önlemi)."""

    def __init__(self, ad, ip, zaman_asimi, baglam):
        super().__init__(ad, 443, timeout=zaman_asimi, context=baglam)
        self._ip = ip

    def connect(self):
        ham = socket.create_connection((self._ip, 443), timeout=self.timeout)
        try:
            self.sock = self._context.wrap_socket(ham, server_hostname=self.host)
        except BaseException:
            ham.close()
            raise


_TLS = None


def gercek_baglayici(ad, ip, yol, basliklar, zaman_asimi, en_cok_bayt, son_an):
    """Tek HTTPS GET; yönlendirme izlemez. Gövdeyi en_cok_bayt'a kadar okur, aşınca asildi=True döner."""
    global _TLS
    if _TLS is None:
        _TLS = ssl.create_default_context()
    baglanti = _SabitIPBaglantisi(ad, ip, zaman_asimi, _TLS)
    zamanlayici = None
    try:
        kalan = son_an - time.monotonic()
        if kalan <= 0:
            raise AgHatasi("getirme.zaman_asimi")
        baglanti.connect()
        soket = baglanti.sock

        def kes():
            try:
                soket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        # Soket zaman aşımı yalnız hareketsizliği sınırlar; damla damla gelen yanıtı da kesin süre keser.
        zamanlayici = threading.Timer(kalan, kes)
        zamanlayici.daemon = True
        zamanlayici.start()
        baglanti.request("GET", yol, headers=basliklar)
        yanit = baglanti.getresponse()
        bas = {}
        for anahtar, deger in yanit.getheaders():
            anahtar = anahtar.lower()
            bas[anahtar] = bas[anahtar] + ", " + deger if anahtar in bas else deger
        if bas.get("content-encoding", "identity").lower() not in ("identity", ""):
            raise AgHatasi("getirme.sikistirilmis")
        parcalar, boyut, asildi = [], 0, False
        while True:
            if time.monotonic() > son_an:
                raise AgHatasi("getirme.zaman_asimi")
            parca = yanit.read1(min(65536, en_cok_bayt + 1 - boyut))
            if not parca:
                break
            boyut += len(parca)
            if boyut > en_cok_bayt:
                asildi = True
                break
            parcalar.append(parca)
        if time.monotonic() > son_an:
            raise AgHatasi("getirme.zaman_asimi")
        return Yanit(yanit.status, bas, b"".join(parcalar), asildi)
    except AgHatasi:
        raise
    except ssl.SSLCertVerificationError:
        raise AgHatasi("getirme.tls") from None
    except (socket.timeout, TimeoutError):
        raise AgHatasi("getirme.zaman_asimi") from None
    except (OSError, http.client.HTTPException, ValueError):
        if time.monotonic() > son_an:
            raise AgHatasi("getirme.zaman_asimi") from None
        raise AgHatasi("getirme.baglanti") from None
    finally:
        if zamanlayici is not None:
            zamanlayici.cancel()
        baglanti.close()


class Getirici:
    """Bir denetim çalışması boyunca paylaşılan getirici: DNS, robots ve sayfa sonuçlarını önbellekte tutar."""

    def __init__(self, *, ayarlar=None, baglayici=None, cozucu=None, saat=time.monotonic, uyu=time.sleep):
        self.ayarlar = ayarlar or Ayarlar()
        self.baglayici = baglayici or gercek_baglayici
        self.cozucu = cozucu or (lambda ad: _dns_coz(ad, self.ayarlar.dns_zaman_asimi))
        self.saat = saat
        self.uyu = uyu
        self.istekler = []           # her HTTP isteğinin kaydı (amaç, alan adı, yol, sonuç)
        self._dns = {}
        self._robots = {}
        self._sonuclar = {}
        self._son_istek = {}

    # --- DNS ---
    def coz(self, ad):
        """Alan adını bir kez çözer; bütün adresler genel değilse AgHatasi verir."""
        if ad not in self._dns:
            try:
                adresler = list(self.cozucu(ad))
                if not adresler:
                    raise AgHatasi("acik.dns_cozulemedi")
                if not all(genel_ip_mi(a) for a in adresler):
                    raise AgHatasi("acik.ozel_adrese_cozuldu")
                self._dns[ad] = adresler
            except AgHatasi as hata:
                self._dns[ad] = hata
            except Exception:
                self._dns[ad] = AgHatasi("acik.dns_cozulemedi")
        sonuc = self._dns[ad]
        if isinstance(sonuc, AgHatasi):
            raise sonuc
        return sonuc

    # --- tek istek ---
    def _istek(self, amac, ad, yol, basliklar, en_cok_bayt, son_an):
        adresler = self.coz(ad)
        ip = next((a for a in adresler if ":" not in a), adresler[0])
        bekleme = max(self.ayarlar.site_arasi_bekleme, self._robots_beklemesi(ad))
        onceki = self._son_istek.get(ad)
        if onceki is not None:
            kalan = onceki + bekleme - self.saat()
            if kalan > 0:
                if self.saat() + kalan > son_an:
                    raise AgHatasi("getirme.zaman_asimi")
                self.uyu(kalan)
        kayit = {"amac": amac, "alan_adi": ad, "yol": yol.split("?", 1)[0], "sonuc": None}
        self.istekler.append(kayit)
        self._son_istek[ad] = self.saat()
        try:
            yanit = self.baglayici(ad, ip, yol, basliklar, min(self.ayarlar.istek_zaman_asimi, max(0.1, son_an - self.saat())),
                                   en_cok_bayt, son_an)
        except AgHatasi as hata:
            kayit["sonuc"] = hata.kod
            raise
        finally:
            self._son_istek[ad] = self.saat()
        kayit["sonuc"] = yanit.durum
        return yanit

    def _basliklar(self, kabul=KABUL):
        return {"User-Agent": KULLANICI_ARACI, "Accept": kabul, "Accept-Language": "en",
                "Accept-Encoding": "identity", "Connection": "close"}

    # --- robots ---
    def _robots_beklemesi(self, ad):
        kural = self._robots.get(ad)
        return kural.bekleme(KULLANICI_ARACI.split("/")[0]) if isinstance(kural, RobotsKurallari) else 0.0

    def robots(self, ad, son_an):
        """Alan adının robots kurallarını bir kez okur; okunamazsa AgHatasi (robots_dogrulanamadi)."""
        if ad not in self._robots:
            try:
                yol = "/robots.txt"
                for _ in range(self.ayarlar.en_cok_yonlendirme + 1):
                    yanit = self._istek("robots", ad, yol, self._basliklar("text/plain, */*;q=0.1"),
                                        self.ayarlar.robots_en_cok_bayt, son_an)
                    if yanit.durum in (301, 302, 303, 307, 308) and yanit.basliklar.get("location"):
                        hedef = urlsplit(urljoin(f"https://{ad}{yol}", yanit.basliklar["location"]))
                        if hedef.scheme != "https" or (hedef.hostname or "").lower() != ad:
                            raise AgHatasi("getirme.robots_dogrulanamadi")
                        yol = hedef.path or "/"
                        continue
                    break
                else:
                    raise AgHatasi("getirme.robots_dogrulanamadi")
                if 200 <= yanit.durum < 300 and not yanit.asildi:
                    kural = RobotsKurallari.ayristir(yanit.govde.decode("utf-8", errors="replace"))
                elif yanit.durum in (401, 403):
                    kural = RobotsKurallari.ayristir("User-agent: *\nDisallow: /")
                elif yanit.durum == 429 or yanit.durum >= 500:
                    raise AgHatasi("getirme.robots_gecici")   # site yoğun ya da arızalı: sayfaya gidilmez, sonra yeniden
                elif 400 <= yanit.durum < 500:
                    kural = RobotsKurallari([])      # RFC 9309: 4xx → kısıt yok
                else:
                    raise AgHatasi("getirme.robots_dogrulanamadi")
                self._robots[ad] = kural
            except AgHatasi as hata:
                if hata.kod in ("acik.dns_cozulemedi", "acik.ozel_adrese_cozuldu"):
                    raise
                gecici = hata.kod in ("getirme.robots_gecici", "getirme.baglanti", "getirme.zaman_asimi")
                self._robots[ad] = AgHatasi("getirme.robots_gecici" if gecici else "getirme.robots_dogrulanamadi")
        kural = self._robots[ad]
        if isinstance(kural, AgHatasi):
            raise kural
        return kural

    # --- kaynak sayfası ---
    def kaynak_getir(self, url):
        """Kaynak sayfasını getirir ve sonucu önbellekte tutar; aynı adres bir çalışmada bir kez istenir."""
        if url in self._sonuclar:
            return self._sonuclar[url]
        sonuc = self._kaynak_getir(url)
        sonuc.zaman = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        self._sonuclar[url] = sonuc
        return sonuc

    def _kaynak_getir(self, url):
        kod, ad = statik_denetle(url)
        if kod:
            return Sonuc(url, "kaldi", "getirme.acik_olmayan_kaynak")
        son_an = self.saat() + self.ayarlar.kaynak_toplam_sure
        parca = urlsplit(url)
        yol = parca.path or "/"
        if parca.query:
            yol += "?" + parca.query
        try:
            for adim in range(self.ayarlar.en_cok_yonlendirme + 1):
                kural = self.robots(ad, son_an)
                if self._robots_beklemesi(ad) > self.ayarlar.en_cok_robots_bekleme:
                    return Sonuc(url, "kaldi", "getirme.robots_bekleme_siniri")
                if not kural.izin_var_mi(KULLANICI_ARACI.split("/")[0], yol):
                    return Sonuc(url, "izin_vermiyor", "getirme.robots_izin_vermiyor")
                yanit = self._istek("kaynak", ad, yol, self._basliklar(), self.ayarlar.en_cok_bayt, son_an)
                if yanit.durum not in (301, 302, 303, 307, 308):
                    break
                if adim == self.ayarlar.en_cok_yonlendirme:
                    return Sonuc(url, "kaldi", "getirme.yonlendirme_siniri", yanit.durum)
                konum = yanit.basliklar.get("location")
                if not konum:
                    return Sonuc(url, "kaldi", "getirme.yonlendirme_gecersiz", yanit.durum)
                hedef_url = urljoin(f"https://{ad}{yol}", konum)
                hedef_kod, hedef_ad = statik_denetle(hedef_url.split("#", 1)[0], sorgu_serbest=True)
                if hedef_kod:
                    return Sonuc(url, "kaldi", "getirme.yonlendirme_gecersiz", yanit.durum)
                if hedef_ad != ad:
                    return Sonuc(url, "kaldi", "getirme.yonlendirme_baska_alana", yanit.durum)
                hedef = urlsplit(hedef_url)
                yol = (hedef.path or "/") + ("?" + hedef.query if hedef.query else "")
        except AgHatasi as hata:
            if hata.kod == "acik.dns_cozulemedi":
                return Sonuc(url, "kaldi", "getirme.dns_cozulemedi")     # geçici sayılır (D1)
            if hata.kod.startswith("acik."):
                return Sonuc(url, "kaldi", "getirme.acik_olmayan_kaynak")
            return Sonuc(url, "kaldi", hata.kod)
        son_url = f"https://{ad}{yol.split('?', 1)[0]}" + ("?…" if "?" in yol else "")
        tur = yanit.basliklar.get("content-type", "").split(";", 1)[0].strip().lower()
        sonuc = Sonuc(url, "kaldi", None, yanit.durum, son_url, tur, yanit.govde, yanit.asildi)
        if yanit.durum in (401, 403, 451):
            sonuc.erisim, sonuc.neden = "izin_vermiyor", "getirme.erisim_kisitli"
        elif yanit.durum in (404, 410):
            sonuc.neden = "getirme.bulunamadi"
        elif yanit.durum == 429:
            sonuc.neden = "getirme.hiz_siniri"
        elif yanit.durum >= 500:
            sonuc.neden = "getirme.sunucu_hatasi"
        elif yanit.durum != 200:
            sonuc.neden = "getirme.http_hatasi"
        elif yanit.asildi:
            sonuc.neden = "getirme.boyut_siniri"
        elif tur not in ("text/html", "application/xhtml+xml", "text/plain", ""):
            sonuc.neden = "getirme.icerik_turu"
        elif _giris_sayfasi_mi(yol, yanit.govde):
            sonuc.erisim, sonuc.neden = "izin_vermiyor", "getirme.giris_sayfasi"
        else:
            sonuc.erisim = "erisildi"
        if sonuc.erisim != "erisildi":
            sonuc.govde = b""
        return sonuc

    # --- paket kayıtları (PyPI, npm) ---
    def kayit_getir(self, url):
        """Resmî kayıt JSON ucunu getirir; robots yerine belgelenmiş API kullanımı. (durum, json|None, büyük_mü)."""
        if url in self._sonuclar:
            return self._sonuclar[url]
        parca = urlsplit(url)
        ad = parca.hostname
        son_an = self.saat() + self.ayarlar.kaynak_toplam_sure
        try:
            yanit = self._istek("paket_kaydi", ad, parca.path, self._basliklar("application/json"),
                                self.ayarlar.kayit_en_cok_bayt, son_an)
            veri = None
            if yanit.durum == 200 and not yanit.asildi:
                try:
                    veri = json.loads(yanit.govde.decode("utf-8"))
                except ValueError:
                    veri = None
            sonuc = (yanit.durum, veri, yanit.asildi)
        except AgHatasi as hata:
            sonuc = (hata.kod, None, False)
        self._sonuclar[url] = sonuc
        return sonuc


def izin_yok_mu(neden):
    return neden in _IZIN_YOK


def _giris_sayfasi_mi(yol, govde):
    parcalar = {p for p in yol.split("?", 1)[0].lower().split("/") if p}
    if parcalar & {"login", "signin", "sign-in", "sign_in", "session", "sessions"}:
        return True
    return b'type="password"' in govde[:500_000].lower() or b"type='password'" in govde[:500_000].lower()


def denetle(kaynaklar, *, getirici=None, ag=True, ag_atla_kodu=None):
    """3. denetim: her kaynağı kibarca getirir; sonuç 'erişildi', 'kaldı' ya da 'kaynak izin vermiyor' olarak ayrışır."""
    from .adres import gosterilebilir
    from .sonuc import Denetim

    d = Denetim(3, "kibar_getirme", "Kibar getirme (robots.txt, sınırlar, bekleme)",
                gecti_aciklamasi="Bütün kaynaklar robots.txt'e uyularak, süre ve boyut sınırı içinde getirildi.")
    sonuclar = {}
    for sira, url in enumerate(kaynaklar):
        satir = {"kaynak": f"k{sira + 1}", "url": gosterilebilir(url)}
        d.ayrinti.append(satir)
        if not ag or getirici is None:
            kod = ag_atla_kodu or "getirme.ag_kapali"
            satir.update(erisim="denenemedi", neden=kod)
            d.denenemedi(kod)
            continue
        sonuc = getirici.kaynak_getir(url)
        sonuclar[url] = sonuc
        satir.update(sonuc.ozet())
        if sonuc.erisim != "erisildi":
            (d.denenemedi if gecici_mi(sonuc.neden) else d.kal)(sonuc.neden)
    if getirici is not None:
        d.ayrinti.append({"istek_sayisi": len(getirici.istekler)})
    return d, sonuclar
