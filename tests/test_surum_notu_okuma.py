"""GitHub sürüm gövdesini arayüz başlıklarından ayırır ve iç içe gizli öğelerde okumanın sürmesini sınar."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '.deps'))
from radar.okuma import makale_ayristir, OkumaHatasi
from radar.toplayici import FetchResult


class SurumNotuOkumaTestleri(unittest.TestCase):
    def response(self, body, url='https://github.com/owner/project/releases/tag/v1.0'):
        return FetchResult(200, body.encode(), {'content-type': 'text/html'}, url)

    def test_surume_ozel_baslik_ve_kisa_ama_tam_yayin_notu_alinir(self):
        text = 'Fix an error when a pending tool request is cancelled during shutdown.'
        html = '<main><h1 class="d-inline">v1.0</h1><h1>Choose a tag to compare</h1><div class="markdown-body" data-test-selector="body-content"><p>' + text + '</p></div></main>'
        result = makale_ayristir(self.response(html))
        self.assertEqual(result['body'], text)
        self.assertEqual(result['title'], 'project · v1.0')
        with self.assertRaises(OkumaHatasi):
            makale_ayristir(self.response(html, 'https://example.org/news'))

    def test_ic_ice_gizli_yapi_silindikten_sonra_attr_erisimine_gidilmez(self):
        text = 'Visible source paragraph explains the release and its limitations. ' * 6
        html = '<article><h1>A real source article title</h1><p>' + text + '</p><div style="display:none"><span style="display:none">gizli bilgi</span></div></article>'
        result = makale_ayristir(self.response(html, 'https://example.org/article'))
        self.assertNotIn('gizli bilgi', result['body'])

    def test_birden_cok_surume_ait_govde_tek_makale_sayilmaz(self):
        html = '<main><h1 class="d-inline">v1.0</h1><h1>Choose a tag</h1>' + '<div class="markdown-body" data-test-selector="body-content"><p>notes</p></div>' * 2 + '</main>'
        with self.assertRaises(OkumaHatasi):
            makale_ayristir(self.response(html))
