"""Yerel uygulamanın K1-V4 sözleşme geçişi: bakış, kapsam ve konu yanıtları değiştirilmeden geçer; eski sunucu ve
ulaşılamama sözleşme hata gövdesiyle anlatılır; sunucunun kendi hata metni yerel kullanıcıya yansımaz."""

import tempfile
import unittest
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from radar.depo import Depo
from radar.katki_v4 import K1Ayarlari, sozlesme_hatasi as sunucu_hatasi
from radar.platform_api import router_olustur
from radar.topluluk_api import uygulama
from radar.topluluk_baglantisi import COMMUNITY_URL, ToplulukBaglantisi, sozlesme_hatasi

ANAHTAR = "a" * 64


class Kasa:
    def __init__(self):
        self.values = {}

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value

    def delete(self, name):
        self.values.pop(name, None)


class SozlesmeGecisiTestleri(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.depo = Depo(self.root / "personal.sqlite3", [])
        self.calls = []

    def remote(self, k1=True):
        """Gerçek topluluk uygulaması (K1 açık ya da eski sunucu gibi kapalı) sahte taşımanın arkasında."""
        kwargs = {"k1_db_path": self.root / f"k1-{int(k1)}" / "k1.sqlite3", "k1_settings": K1Ayarlari()} if k1 else {}
        # Sabit saat: kapsam/konu gövdesi `generated_at` taşır ve zayıf ETag gövdenin özetidir; saniye sınırı 304'ü bozmasın.
        app = uygulama(self.root / f"community-{int(k1)}.sqlite3", public_url=COMMUNITY_URL, sources=[],
                       clock=lambda: 1_793_966_400, **kwargs)
        client = TestClient(app, base_url=COMMUNITY_URL)
        self.addCleanup(client.close)

        def exchange(request):
            self.calls.append(request)
            response = client.request(request.method, str(request.url), content=request.read(), headers=dict(request.headers))
            return httpx.Response(response.status_code, content=response.content, headers=dict(response.headers))

        return httpx.MockTransport(exchange)

    def local(self, transport):
        bridge = ToplulukBaglantisi(self.depo, transport=transport, vault=Kasa())
        app = FastAPI()
        app.include_router(router_olustur(self.depo, "csrf-token", community=bridge))
        client = TestClient(app)
        self.addCleanup(client.close)
        return client

    def test_kapsam_ve_konular_etag_ile_gecer_304_dahil(self):
        local = self.local(self.remote())
        for path, schema in (("/api/platform/community/coverage", "devtrellis-coverage"),
                             ("/api/platform/community/topics", "devtrellis-topics")):
            with self.subTest(path=path):
                first = local.get(path)
                self.assertEqual(first.status_code, 200, first.text)
                self.assertEqual(first.json()["schema"], schema)
                self.assertTrue(first.headers.get("etag"))
                again = local.get(path, headers={"If-None-Match": first.headers["etag"]})
                self.assertEqual(again.status_code, 304)
                self.assertEqual(again.headers.get("etag"), first.headers["etag"])
        self.assertTrue(all(call.method == "GET" for call in self.calls))

    def test_bakis_govdesi_ve_parametreler_aynen_gecer(self):
        local = self.local(self.remote())
        miss = local.get(f"/api/platform/community/sources/{ANAHTAR}")
        self.assertEqual(miss.status_code, 404)
        self.assertEqual((miss.json()["schema"], miss.json()["result"], miss.json()["address_key"]),
                         ("devtrellis-source-lookup", "not_found", ANAHTAR))
        local.get(f"/api/platform/community/sources/{ANAHTAR}?product=widget&version=2.0")
        self.assertEqual(dict(self.calls[-1].url.params), {"product": "widget", "version": "2.0"})
        self.assertTrue(str(self.calls[-1].url).startswith(COMMUNITY_URL + "/api/community/sources/" + ANAHTAR))

    def test_gecersiz_anahtar_ve_parametre_sunucuya_gitmeden_sozlesme_hatasi(self):
        local = self.local(self.remote())
        for path in ("/api/platform/community/sources/not-a-key", "/api/platform/community/sources/" + "A" * 64,
                     f"/api/platform/community/sources/{ANAHTAR}?token=abc", f"/api/platform/community/sources/{ANAHTAR}?product=a&product=b",
                     "/api/platform/community/coverage?x=1", "/api/platform/community/topics?product=widget",
                     f"/api/platform/community/sources/{ANAHTAR}?version=" + "9" * 81):
            with self.subTest(path=path):
                response = local.get(path)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json(), sozlesme_hatasi("invalid_request"))
        self.assertEqual(self.calls, [])

    def test_eski_sunucu_unsupported_contract_ve_metni_yansimaz(self):
        local = self.local(self.remote(k1=False))
        for path in ("/api/platform/community/coverage", f"/api/platform/community/sources/{ANAHTAR}"):
            with self.subTest(path=path):
                response = local.get(path)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json(), sozlesme_hatasi("unsupported_contract"))
                self.assertNotIn("detail", response.text)

    def test_ulasilamama_hiz_siniri_ve_sozlesme_hatasi_govdeleri(self):
        def unreachable(request):
            raise httpx.ConnectError("refused", request=request)

        cases = (
            (httpx.MockTransport(unreachable), 503, sozlesme_hatasi("temporarily_unavailable")),
            (httpx.MockTransport(lambda r: httpx.Response(429, text="slow down")), 429, sozlesme_hatasi("rate_limited")),
            (httpx.MockTransport(lambda r: httpx.Response(429, json=sunucu_hatasi("rate_limited", 30))), 429,
             sozlesme_hatasi("rate_limited", 30)),
            (httpx.MockTransport(lambda r: httpx.Response(422, json=sunucu_hatasi("invalid_request"))), 422,
             sozlesme_hatasi("invalid_request")),
            (httpx.MockTransport(lambda r: httpx.Response(500, text="<html>private trace</html>")), 503,
             sozlesme_hatasi("temporarily_unavailable")),
            (httpx.MockTransport(lambda r: httpx.Response(200, json={"detail": "not a contract body"})), 503,
             sozlesme_hatasi("temporarily_unavailable")),
            (httpx.MockTransport(lambda r: httpx.Response(406, text="")), 404, sozlesme_hatasi("unsupported_contract")),
            (httpx.MockTransport(lambda r: httpx.Response(200, content=b"x" * (1024 * 1024 + 1),
                                                          headers={"Content-Type": "application/json"})), 503,
             sozlesme_hatasi("temporarily_unavailable")),
        )
        for transport, status, body in cases:
            with self.subTest(status=status, code=body["code"]):
                response = self.local(transport).get("/api/platform/community/coverage")
                self.assertEqual((response.status_code, response.json()), (status, body))
                self.assertNotIn("private trace", response.text)

    def test_hata_govdesi_sunucunun_yardimcisiyla_ayni_bicimde(self):
        self.assertEqual(sozlesme_hatasi("rate_limited", 5), sunucu_hatasi("rate_limited", 5))
        self.assertEqual(sozlesme_hatasi("unsupported_contract"), sunucu_hatasi("unsupported_contract"))

    def test_oturum_hedefi_topluluk_adresini_soyler(self):
        local = self.local(self.remote(k1=False))
        self.assertEqual(local.get("/api/platform/community/session").json()["destination"], COMMUNITY_URL)


if __name__ == "__main__":
    unittest.main()
