"""Kaynak kataloğunun tekrarsız, sınıflanmış ve tam alan adlarıyla sınırlandırılmış olduğunu doğrular."""

import copy
import unittest

from radar.kaynak_havuzu import alan_adi_gecerli, katalogu_dogrula, kaynaklari_oku, izinli_alan_adlari
from radar.konular import KONULAR


class KaynakHavuzuTestleri(unittest.TestCase):
    def test_gercek_katalog_yayinci_dal_ve_adresleriyle_okunur(self):
        sources = kaynaklari_oku()
        self.assertEqual({source['topic'] for source in sources}, set(KONULAR))
        self.assertEqual(izinli_alan_adlari(), frozenset(host for source in sources for host in source['fetch_hosts']))
        self.assertTrue(all(source['publisher'] and source['branches'] for source in sources))

    def test_alan_adi_kapsami_genisletilemez(self):
        for host in ['*.example.org', 'localhost', '127.0.0.1', 'server.local', 'a..org', '-a.org', 'www.EXAMPLE.org', 'example.org:443', 'a.internal']:
            with self.subTest(host=host):
                self.assertFalse(alan_adi_gecerli(host))
        self.assertTrue(alan_adi_gecerli('research.example.org'))

    def test_tekrar_kimlik_ve_tekrar_besleme_reddedilir(self):
        source = copy.deepcopy(kaynaklari_oku()[0])
        with self.assertRaises(ValueError):
            katalogu_dogrula([source, copy.deepcopy(source)])
        other = {**source, 'id': 'diger'}
        with self.assertRaises(ValueError):
            katalogu_dogrula([source, other])

    def test_hatalar_kaynak_acilmadan_yakalanir(self):
        for changes in [{'fetch_url': 'https://127.0.0.1/'}, {'fetch_url': 'http://www.anthropic.com/news'},
                        {'fetch_url': 'https://user:pass@www.anthropic.com/news'}, {'fetch_url': 'https://www.anthropic.com:8443/news'},
                        {'fetch_hosts': ['*.anthropic.com']}, {'branches': []}, {'publisher': ''},
                        {'interval_hours': 0}, {'interval_hours': True}, {'enabled': 'true'}, {'topic': 'bilinmiyor'}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                katalogu_dogrula([{**kaynaklari_oku()[0], **changes}])


if __name__ == '__main__':
    unittest.main()
