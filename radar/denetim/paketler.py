"""Kurulum komutlarındaki paket adlarını çıkarır; PyPI ve npm'de var olup olmadığını ve yaşını resmî JSON uçlarından GET ile denetler (cargo, go, conda denenemez)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import quote

from .gizlilik import ADRES_ALANLARI, metinleri_gez
from .sonuc import Denetim

# (komut kalıbı, ekosistem, biçim) — biçim: "liste" (bütün sözcükler paket), "ilk" (yalnız ilk sözcük paket),
# "bayrak" (yalnız --with değerleri paket; sözcükler çalıştırılacak komuttur), "olustur" (npm create/init: ilk sözcük
# create-<ad> paketine çevrilir). Kalıplar büyük/küçük harf duyarsızdır (PIP INSTALL); go/cargo düz yazıyla
# karışmasın diye ("Go get coffee") küçük harf ister. Listede olmayan komutlar (apt, brew, gem, choco …) denetlenmez.
KOMUTLAR = [
    (r"\b(?:(?:python[\d.]*|py(?:\s+-[\d.]+)?)\s+-m\s+)?pip[\d.]*\s+install\b", "pypi", "liste"),
    (r"\buv\s+pip\s+install\b", "pypi", "liste"),
    (r"\buv\s+add\b", "pypi", "liste"),
    (r"\buv\s+tool\s+(?:install|run)\b", "pypi", "ilk"),
    (r"\buv\s+run\b", "pypi", "bayrak"),
    (r"\buvx\b", "pypi", "ilk"),
    (r"\b(?:poetry|pdm|rye)\s+add\b", "pypi", "liste"),
    (r"\bpipenv\s+install\b", "pypi", "liste"),
    (r"\bpipx\s+(?:install|run)\b", "pypi", "ilk"),
    (r"\beasy_install\b", "pypi", "liste"),
    (r"\bdeno\s+(?:add|install|i)\b", "deno", "liste"),
    (r"\bdeno\s+run\b", "deno", "ilk"),
    (r"\bnpm\s+(?:install|i|add)\b", "npm", "liste"),
    (r"\bnpm\s+(?:exec|x)\b", "npm", "ilk"),
    (r"\b(?:npm|pnpm|yarn|bun)\s+create\b|\bnpm\s+init\b", "npm", "olustur"),
    (r"\bpnpm\s+(?:add|install|i)\b", "npm", "liste"),
    (r"\b(?:pnpm|yarn)\s+dlx\b", "npm", "ilk"),
    (r"\byarn\s+(?:global\s+)?add\b", "npm", "liste"),
    (r"\bbun\s+(?:add|install|i)\b", "npm", "liste"),
    (r"\bnpx\b|\bbunx\b|\bbun\s+x\b", "npm", "ilk"),
    (r"(?-i:\bcargo\s+(?:add|install)\b)", "crates", "liste"),
    (r"(?-i:\bgo\s+(?:get|install)\b)", "go", "liste"),
    (r"\b(?:conda|mamba|micromamba)\s+install\b", "conda", "liste"),
]
_KOMUT = re.compile("|".join(f"(?P<k{i}>{k})" for i, (k, _, _) in enumerate(KOMUTLAR)), re.I)
# Sözcük listesi şuralarda biter: ters tırnak, satır sonu, ; | & ), sözcüğe bitişik nokta (cümle sonu), virgül.
# Tek başına "." (pip install -e .) yerel dizindir, cümle sonu değildir.
_DUR = re.compile(r"[`\n;|&)]|(?<=\S)\.\s|(?<=\S)\.$|,\s")
# Satır devamı: kabukta "\", cmd'de " ^", PowerShell'de " `" + satır sonu tek satıra birleştirilir.
_DEVAM = re.compile(r"\\[ \t]*\r?\n[ \t]*|[ \t][\^`][ \t]*\r?\n[ \t]*")
_DUZYAZI = {"to", "and", "or", "then", "for", "with", "in", "the", "a", "an", "if", "which", "that", "is", "are",
            "will", "from", "on", "into", "first", "later", "it", "this", "now", "here", "there", "again", "also",
            "instead", "too", "as", "by", "via", "using", "so", "but", "when", "where", "before", "after", "once",
            "globally", "locally", "manually", "directly", "inside", "next", "its", "your", "you", "we", "they",
            "see", "below", "above", "following", "of", "at", "under", "within", "without", "only", "both", "each",
            "every", "command", "commands", "afterwards", "yourself", "run", "use", "should", "can", "must"}
_DEGERLI_BAYRAK = {"-r", "--requirement", "-c", "--constraint", "-e", "--editable", "-t", "--target", "-f",
                   "--find-links", "--prefix", "--root", "--python", "-w", "--with", "--workspace", "-p", "--package",
                   "--from", "-i", "--index-url", "--extra-index-url", "--index", "--registry", "--with-requirements",
                   "--with-editable", "--channel"}
_PAKET_BAYRAGI = {"--with", "-w"}       # değeri paket adı (virgülle birden çok) olan bayraklar (uv run, uvx)
_DIZIN_BAYRAGI = {"-i", "--index-url", "--extra-index-url", "--index", "--registry"}
_PYPI_AD = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")
_NPM_AD = re.compile(r"^(?:@[a-z0-9-~][a-z0-9-._~]*/)?[a-z0-9-~][a-z0-9-._~]*$")
_YER_TUTUCU = re.compile(r"^(?:<.*>|\{.*\}|\$.+|[A-Z][A-Z0-9_]*|\.\.\.|package(?:[-_]name)?|your[-_].+|my[-_]package)$")


def _sozcukler(satir):
    parca = _DUR.split(satir, 1)[0]
    sozcukler = []
    for s in parca.split():
        s = s.strip("\"'")
        sozcukler.append(s.rstrip(".,:") if len(s) > 1 else s)
    return sozcukler


def _olusturucu(ad):
    """npm create / init başlatıcısının paket adı: vite → create-vite, @kapsam → @kapsam/create, @kapsam/x → @kapsam/create-x."""
    if ad.startswith("@"):
        kapsam, _, kalan = ad.partition("/")
        if kalan:
            return f"{kapsam}/create-{kalan}"
        return "@" + kapsam[1:].split("@", 1)[0] + "/create"
    return "create-" + ad


def _deno_coz(aday):
    """deno belirteci: 'npm:ad' → npm; 'jsr:@k/ad' ya da yalın ad → jsr (denetlenmez); yerel dosya ve adres olduğu gibi."""
    if aday.startswith("npm:"):
        return "npm", aday[4:]
    if "://" not in aday and (aday.startswith((".", "/", "~")) or aday.endswith((".ts", ".js", ".tsx", ".jsx", ".mjs"))):
        return "yerel_dosya", aday     # deno run main.ts: paket değil
    return "jsr", aday[4:] if aday.startswith("jsr:") else aday


def komutlari_bul(metin):
    """[(ekosistem, ham_ad, bayrak_notu)] döndürür; düz yazı sözcüklerinde ve bayraklarda durur."""
    bulunan = []
    metin = _DEVAM.sub(" ", metin)
    for eslesme in _KOMUT.finditer(metin):
        sira = int(next(k for k, v in eslesme.groupdict().items() if v)[1:])
        _, ekosistem, bicim = KOMUTLAR[sira]
        sozcukler = _sozcukler(metin[eslesme.end():])
        atla, from_degeri = False, None
        adaylar, bayrak_paketleri, notlar = [], [], set()
        for n, sozcuk in enumerate(sozcukler):
            if not sozcuk:
                continue
            if atla:
                atla = False
                onceki = sozcukler[n - 1]
                if onceki in ("--from", "-p", "--package"):
                    from_degeri = sozcuk
                elif onceki in _PAKET_BAYRAGI:
                    bayrak_paketleri += [p for p in sozcuk.split(",") if p]
                continue
            if sozcuk.startswith("-") and sozcuk != "-":
                bayrak = sozcuk.split("=", 1)[0]
                if bayrak in _DIZIN_BAYRAGI:
                    notlar.add("ozel_dizin")
                if bayrak in _DEGERLI_BAYRAK and "=" not in sozcuk:
                    atla = True
                elif bayrak in ("--from", "--package") and "=" in sozcuk:
                    from_degeri = sozcuk.split("=", 1)[1]
                elif bayrak in _PAKET_BAYRAGI and "=" in sozcuk:
                    bayrak_paketleri += [p for p in sozcuk.split("=", 1)[1].split(",") if p]
                continue
            if bicim == "bayrak":
                break                       # uv run <komut>: kalan sözcükler paket değil
            if sozcuk.lower() in _DUZYAZI:
                break
            adaylar.append(_olusturucu(sozcuk) if bicim == "olustur" else sozcuk)
            if bicim in ("ilk", "olustur"):
                break
        if from_degeri:
            adaylar = [from_degeri]
        for aday in adaylar + bayrak_paketleri:
            eko, ad = _deno_coz(aday) if ekosistem == "deno" else (ekosistem, aday)
            bulunan.append((eko, ad, sorted(notlar)))
        if not adaylar and notlar:
            bulunan.append((ekosistem, None, sorted(notlar)))
    return bulunan


def ad_coz(ekosistem, ham):
    """('paket', ad) | ('adres', None) | ('yerel', None) | ('yer_tutucu', None) | ('gecersiz', None)."""
    if ham is None:
        return "yer_tutucu", None
    if ekosistem == "yerel_dosya":
        return "yerel", None
    if "://" in ham or ham.startswith(("git+", "github:", "file:", "git@")):
        return "adres", None
    if _YER_TUTUCU.match(ham):
        return "yer_tutucu", None
    if ekosistem == "pypi":
        if ham.startswith((".", "/", "~")) or "\\" in ham or ham.endswith((".whl", ".tar.gz", ".zip")):
            return "yerel", None
        ad = re.split(r"[\[=<>!~;@ ]", ham, maxsplit=1)[0]
        if not _PYPI_AD.match(ad):
            return "gecersiz", None
        return "paket", re.sub(r"[-_.]+", "-", ad).lower()
    if ekosistem == "npm":
        if ham.startswith((".", "/", "~")):
            return "yerel", None
        if not ham.startswith("@") and "/" in ham:
            return "adres", None     # kullanıcı/depo kısaltması GitHub'dan kurar
        ad = ham if ham.count("@") <= (1 if ham.startswith("@") else 0) else ham.rsplit("@", 1)[0]
        if not _NPM_AD.match(ad):
            return "gecersiz", None
        return "paket", ad
    return "paket", ham


def _yas_gun(ekosistem, veri, simdi):
    zamanlar = []
    if ekosistem == "pypi":
        for dosyalar in (veri.get("releases") or {}).values():
            for dosya in dosyalar or []:
                zamanlar.append(dosya.get("upload_time_iso_8601") or dosya.get("upload_time"))
        if not zamanlar:
            zamanlar = [d.get("upload_time_iso_8601") for d in veri.get("urls") or []]
    else:
        zamanlar = [(veri.get("time") or {}).get("created")]
    tarihler = []
    for z in zamanlar:
        if not isinstance(z, str):
            continue
        try:
            t = datetime.fromisoformat(z.replace("Z", "+00:00"))
            tarihler.append(t if t.tzinfo else t.replace(tzinfo=timezone.utc))
        except ValueError:
            continue
    return (simdi - min(tarihler)).days if tarihler else None


def kayit_adresi(ekosistem, ad):
    if ekosistem == "pypi":
        return f"https://pypi.org/pypi/{quote(ad, safe='')}/json"
    return f"https://registry.npmjs.org/{quote(ad, safe='@')}"   # kapsamlı ad: @kapsam%2Fad


def denetle(paket, *, getirici=None, ag=True, ayarlar, simdi=None, ag_atla_kodu=None):
    simdi = simdi or datetime.now(timezone.utc)
    d = Denetim(7, "paket_adlari", "Kurulum komutlarındaki paket adları",
                gecti_aciklamasi="Kurulum komutu yok ya da bütün paket adları resmî kayıtta var.")
    adaylar = {}
    for yol, alan, metin in metinleri_gez(paket):
        if alan in ADRES_ALANLARI:
            continue
        for ekosistem, ham, notlar in komutlari_bul(metin):
            tur, ad = ad_coz(ekosistem, ham)
            if "ozel_dizin" in notlar:
                d.uyar("paket.ozel_dizin")
            if tur == "adres":
                d.uyar("paket.dogrudan_adres")
                d.ayrinti.append({"ekosistem": ekosistem, "konum": yol, "sonuc": "uyari", "neden": "paket.dogrudan_adres"})
            elif tur == "gecersiz":
                d.kal("paket.gecersiz_ad")
                d.ayrinti.append({"ekosistem": ekosistem, "konum": yol, "sonuc": "kaldi", "neden": "paket.gecersiz_ad"})
            elif tur == "paket":
                adaylar.setdefault((ekosistem, ad), yol)
    if len(adaylar) > ayarlar.en_cok_paket:
        d.kal("paket.cok_fazla", f"{len(adaylar)} paket adı var; sınır {ayarlar.en_cok_paket}.")
        return d
    for (ekosistem, ad), yol in adaylar.items():
        satir = {"ekosistem": ekosistem, "paket": ad, "konum": yol}
        d.ayrinti.append(satir)
        if ekosistem not in ("pypi", "npm"):
            satir.update(sonuc="denenemedi", neden="paket.ekosistem_desteklenmiyor")
            if ayarlar.desteksiz_ekosistem == "uyari":
                d.uyar("paket.ekosistem_desteklenmiyor")
            else:
                d.denenemedi("paket.ekosistem_desteklenmiyor")
            continue
        if not ag or getirici is None:
            kod = ag_atla_kodu or "paket.ag_kapali"
            satir.update(sonuc="denenemedi", neden=kod)
            d.denenemedi(kod)
            continue
        durum, veri, buyuk = getirici.kayit_getir(kayit_adresi(ekosistem, ad))
        if durum == 404:
            satir.update(sonuc="kaldi", neden="paket.bulunamadi")
            d.kal("paket.bulunamadi")
        elif durum == 200 and buyuk:
            satir.update({"sonuc": "gecti", "not": "Kayıt yanıtı çok büyük (çok sürümlü, eski paket); yaşı ölçülmedi."})
        elif durum == 200 and isinstance(veri, dict):
            yas = _yas_gun(ekosistem, veri, simdi)
            satir.update(sonuc="gecti", yas_gun=yas)
            if yas is not None and yas < ayarlar.yeni_paket_gun:
                satir.update(sonuc="uyari", neden="paket.cok_yeni")
                d.uyar("paket.cok_yeni", f"Bir paketin ilk sürümü {yas} gün önce yayımlanmış (eşik {ayarlar.yeni_paket_gun} gün).")
        else:
            satir.update(sonuc="denenemedi", neden="paket.kayit_erisilemedi")
            d.denenemedi("paket.kayit_erisilemedi")
    return d
