"""Testler için ağsız sahte ağ: enjekte edilen DNS çözücü, HTTP bağlayıcı ve sahte saat; gerçek sokete hiç dokunmaz."""

from __future__ import annotations

import copy

from radar.denetim.ayarlar import Ayarlar
from radar.denetim.getirme import AgHatasi, Getirici, Yanit

KAYNAK_1 = "https://docs.example.org/widget/2.0/reference.html"
KAYNAK_2 = "https://docs.example.org/widget/2.0/storage.html"

SAYFA_1 = """<!doctype html><html><head><title>Widget reference</title>
<script>var gizli = "Widget 2.0 encrypts every file with a hidden key.";</script><style>p{}</style></head>
<body><nav>Home</nav><main><h1>Widget 2.0 reference</h1>
<p>Widget&nbsp;2.0 stores   projects locally. Each project is a folder in the user's data directory.</p>
<p>Cloud synchronization is disabled by default.</p>
<p>The “export” command writes a single archive — one file per project.</p>
<p hidden>Widget 2.0 sends telemetry to a hidden server.</p>
<div style="display:none">Widget 2.0 uploads crash dumps silently to the vendor.</div>
<table><tr><td>per_page</td><td>The number of results per page (max 100).</td></tr>
<tr><td></td><td>Default: <code>30</code></td></tr></table>
</main></body></html>"""

UZUN_ALINTI = "Widget 2.0 stores projects locally. Each project is a folder in the user's data directory."

SAYFA_2 = """<html><body><main><h1>Storage</h1><p>Projects are kept in the user's data directory.</p>
<p>Backups are created every 24 hours when enabled.</p></main></body></html>"""


class SahteAg:
    """Alan adı → IP listesi ve (alan adı, yol) → (durum, başlıklar, gövde) tablosuyla çalışan sahte ağ."""

    def __init__(self, sayfalar=None, dns=None):
        self.sayfalar = sayfalar if sayfalar is not None else {}
        self.dns = dns if dns is not None else {}
        self.cagrilar = []
        self.zaman = 1000.0
        self.uykular = []

    # Saat ve uyku: bekleme testleri gerçek süre harcamaz.
    def saat(self):
        return self.zaman

    def uyu(self, saniye):
        self.uykular.append(round(saniye, 3))
        self.zaman += saniye

    def cozucu(self, ad):
        if ad not in self.dns:
            raise OSError("bilinmeyen ad")
        return self.dns[ad]

    def baglayici(self, ad, ip, yol, basliklar, zaman_asimi, en_cok_bayt, son_an):
        self.cagrilar.append({"ad": ad, "ip": ip, "yol": yol, "basliklar": dict(basliklar)})
        self.zaman += 0.01
        kayit = self.sayfalar.get((ad, yol))
        if kayit is None:
            return Yanit(404, {"content-type": "text/html"}, b"not found")
        if isinstance(kayit, AgHatasi):
            raise kayit
        durum, bas, govde = kayit
        if isinstance(govde, str):
            govde = govde.encode("utf-8")
        if len(govde) > en_cok_bayt:
            return Yanit(durum, dict(bas), govde[:en_cok_bayt], True)
        return Yanit(durum, dict(bas), govde)

    def getirici(self, ayarlar=None):
        return Getirici(ayarlar=ayarlar or Ayarlar(), baglayici=self.baglayici, cozucu=self.cozucu,
                        saat=self.saat, uyu=self.uyu)


def html(govde, durum=200):
    return (durum, {"content-type": "text/html; charset=utf-8"}, govde)


def temel_ag():
    """docs.example.org: robots herkese açık, iki sayfa; genel bir IP'ye çözülür."""
    return SahteAg(
        sayfalar={
            ("docs.example.org", "/robots.txt"): (200, {"content-type": "text/plain"}, "User-agent: *\nDisallow: /private/\n"),
            ("docs.example.org", "/widget/2.0/reference.html"): html(SAYFA_1),
            ("docs.example.org", "/widget/2.0/storage.html"): html(SAYFA_2),
        },
        dns={"docs.example.org": ["93.184.215.14"], "pypi.org": ["151.101.0.223"],
             "registry.npmjs.org": ["104.16.0.35"]},
    )


TEMEL_KART = {
    "kind": "source_card",
    "schema_version": 4,
    "language": "en",
    "claims": [
        {
            "text": "Widget 2.0 stores project files locally.",
            "quote": UZUN_ALINTI,
            "source_url": KAYNAK_1,
            "checked_on": "2026-11-06",
            "claim_type": "source_statement",
            "applies_to": [{"product": "widget", "versions": "2.0", "version_scheme": "exact"}],
        },
        {
            "text": "Cloud synchronization is off unless the user turns it on.",
            "quote": "Cloud synchronization is disabled by default.",
            "source_url": KAYNAK_1,
            "checked_on": "2026-11-06",
            "claim_type": "source_statement",
            "applies_to": [{"product": "widget", "versions": "2.0", "version_scheme": "exact"}],
        },
    ],
    "provenance": {"origin": "model_assisted", "stages": [
        {"provider": "claude", "model": "claude-opus-5-5", "access_type": "unknown",
         "basis": "contributor_declaration", "terms_url": None}]},
}

TEMEL_RAPOR = {
    "kind": "research_report",
    "schema_version": 4,
    "language": "en",
    "title": "How Widget 2.0 stores projects and backups",
    "summary": "Where Widget 2.0 keeps project data and how backups behave.",
    "claims": [
        {"text": "Widget 2.0 stores project files locally.", "quote": UZUN_ALINTI,
         "source_url": KAYNAK_1, "checked_on": "2026-11-06"},
        {"text": "The export command writes one archive per project.",
         "quote": "The \"export\" command writes a single archive - one file per project.",
         "source_url": KAYNAK_1, "checked_on": "2026-11-06"},
        {"text": "Backups run every 24 hours when enabled.", "quote": "Backups are created every 24 hours when enabled.",
         "source_url": KAYNAK_2, "checked_on": "2026-11-06"},
    ],
    "sources": [{"title": "Widget reference", "url": KAYNAK_1}, {"title": "Widget storage", "url": KAYNAK_2}],
    "provenance": {"origin": "model_assisted", "stages": [
        {"provider": "claude", "model": "claude-opus-5-5", "access_type": "unknown",
         "basis": "contributor_declaration", "terms_url": None}]},
}


def kart(**degisiklik):
    paket = copy.deepcopy(TEMEL_KART)
    paket.update(degisiklik)
    return paket


def rapor(**degisiklik):
    paket = copy.deepcopy(TEMEL_RAPOR)
    paket.update(degisiklik)
    return paket


def denetim(sonuc, ad):
    return next(d for d in sonuc["denetimler"] if d["ad"] == ad)
