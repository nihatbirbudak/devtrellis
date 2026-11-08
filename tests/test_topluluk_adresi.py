"""Topluluk adresi ayarı: varsayılan devtrellis.org; DEVTRELLIS_COMMUNITY_URL yalnız şema + sunucu, düz http yalnız loopback."""

import unittest
from unittest.mock import patch

from radar.topluluk_adresi import ORTAM_DEGISKENI, VARSAYILAN, topluluk_adresi


class ToplulukAdresiTestleri(unittest.TestCase):
    def test_varsayilan_ve_bos_deger(self):
        with patch.dict("os.environ", {ORTAM_DEGISKENI: ""}, clear=False):
            self.assertEqual(topluluk_adresi(), VARSAYILAN)
        self.assertEqual(topluluk_adresi(""), "https://devtrellis.org")
        self.assertEqual(topluluk_adresi("  "), "https://devtrellis.org")

    def test_ortam_degiskeni_okunur_ve_sadelestirilir(self):
        with patch.dict("os.environ", {ORTAM_DEGISKENI: "https://staging.example.org/"}, clear=False):
            self.assertEqual(topluluk_adresi(), "https://staging.example.org")
        self.assertEqual(topluluk_adresi("http://127.0.0.1:18941"), "http://127.0.0.1:18941")
        self.assertEqual(topluluk_adresi("http://localhost:8782/"), "http://localhost:8782")

    def test_gecersiz_degerler_reddedilir(self):
        for kotu in ("http://example.org", "ftp://devtrellis.org", "https://devtrellis.org/api", "https://devtrellis.org/?x=1",
                     "https://devtrellis.org/#a", "https://user:pw@devtrellis.org", "devtrellis.org", "http://10.0.0.5:8782"):
            with self.subTest(kotu=kotu), self.assertRaises(ValueError):
                topluluk_adresi(kotu)


if __name__ == "__main__":
    unittest.main()
