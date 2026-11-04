"""Bağlantı bağlamının açık erişim sınırlarını ve tek çağrılık alan adı iznini sahte ağla doğrular."""

import socket
import threading
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from radar import toplayici
from radar.baglanti_inceleme import baglanti_dogrula, baglantiyi_incele


URL = 'https://social.example.org/post/123'
BODY = ('<article><h1>Public voice memory claim</h1><p>' +
        'This publisher claims that voice memory improves the measured latency. ' * 5 + '</p></article>').encode()


class BaglantiIncelemeTestleri(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 4, tzinfo=timezone.utc)
        toplayici._ROBOTS_CACHE.clear()

    def response(self, status=200, body=BODY, content_type='text/html', url=URL):
        return toplayici.FetchResult(status, body, {'content-type': content_type}, url)

    def test_html_metni_okunur_medya_ve_iddia_dogrulanmis_sayilmaz(self):
        fetcher = Mock(return_value=self.response())
        result = baglantiyi_incele(URL, fetcher=fetcher, now=self.now)
        self.assertEqual(result['status'], 'read')
        self.assertIn('publisher claims', result['text'])
        self.assertFalse(result['media_processed'])
        self.assertEqual(result['fetched_at'], self.now.isoformat())
        self.assertIn('Paylaşım doğrulanmadı', result['message'])
        fetcher.assert_called_once_with(URL, stop_event=None)

    def test_oturum_sayfasi_video_ve_bos_metin_okunmus_sayilmaz(self):
        examples = [(self.response(status=403), 'access_restricted'),
                    (self.response(body=b'<video src="clip.mp4"/>', content_type='video/mp4'), 'unsupported_content'),
                    (self.response(body=b'<main><h1>Sign in to your social account</h1><input type="password"/></main>'), 'access_restricted'),
                    (self.response(body=b'<html><title>Empty social shell</title><script>loadVideo()</script></html>'), 'text_unavailable')]
        for response, code in examples:
            with self.subTest(code=code):
                result = baglantiyi_incele(URL, fetcher=Mock(return_value=response))
                self.assertEqual(result['status'], 'unavailable')
                self.assertEqual(result['reason_code'], code)
                self.assertEqual(result['text'], '')
                self.assertFalse(result['media_processed'])

    def test_okuma_hatasi_ham_yaniti_ve_sirlari_yaymaz(self):
        result = baglantiyi_incele(URL, fetcher=Mock(side_effect=RuntimeError('FAKE_PRIVATE_SECRET')))
        self.assertEqual(result['reason_code'], 'fetch_failed')
        self.assertNotIn('FAKE_PRIVATE_SECRET', str(result))

    def test_uzun_metin_kesildigini_acikca_bildirir(self):
        body = ('<article><h1>A longer public source article</h1><p>' + 'Research findings. ' * 1000 + '</p></article>').encode()
        result = baglantiyi_incele(URL, fetcher=Mock(return_value=self.response(body=body)))
        self.assertEqual(len(result['text']), 8000)
        self.assertTrue(result['truncated'])

    def test_guvensiz_url_istek_oncesi_reddedilir(self):
        for url in ('http://example.org/', 'https://example.org:8443/', 'https://127.0.0.1/',
                    'https://10.0.0.1/', 'https://localhost/', 'https://2130706433/',
                    'https://user:pass@example.org/', 'https://example.org/\nsecret', 'file:///private',
                    'https://example.org/?api_key=FAKE_SECRET'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                baglanti_dogrula(url)
        self.assertEqual(baglanti_dogrula(URL + '#caption'), URL)

    def test_tek_cagrilik_izin_genel_listeye_ve_diger_is_parcacigina_sizmaz(self):
        original = toplayici.APPROVED_FETCH_HOSTS
        errors = []
        def fetch(url, hosts, headers, deadline, **kwargs):
            self.assertEqual(toplayici._validate_url(url, hosts).hostname, 'social.example.org')
            def other():
                try:
                    toplayici._validate_url(url, hosts)
                except toplayici.CollectionError:
                    errors.append('blocked')
            worker = threading.Thread(target=other)
            worker.start()
            worker.join(2)
            self.assertFalse(worker.is_alive())
            return self.response()
        with patch.object(toplayici, '_fetch', side_effect=fetch):
            toplayici.kamusal_baglanti_oku(URL)
        self.assertEqual(errors, ['blocked'])
        self.assertEqual(toplayici.APPROVED_FETCH_HOSTS, original)
        with self.assertRaises(toplayici.CollectionError):
            toplayici._validate_url(URL, {'social.example.org'})

    def test_yonlendirme_tam_alan_adindan_cikamaz_izin_hata_sonrasi_silinir(self):
        for target in ('https://other.example.org/post', 'https://127.0.0.1/private', 'http://social.example.org/post'):
            redirect = toplayici.FetchResult(302, b'', {'location': target}, URL)
            with self.subTest(target=target), patch.object(toplayici, '_robots_allowed', return_value=True), \
                    patch.object(toplayici, '_request_once', return_value=redirect) as request:
                with self.assertRaises(toplayici.CollectionError):
                    toplayici.kamusal_baglanti_oku(URL)
                self.assertEqual(request.call_count, 1)
            with self.assertRaises(toplayici.CollectionError):
                toplayici._validate_url(URL, {'social.example.org'})

    def test_ozel_ve_karisik_dns_adreslerine_baglanilmaz(self):
        for addresses in (['127.0.0.1'], ['8.8.8.8', '10.0.0.1']):
            records = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, 443)) for address in addresses]
            with self.subTest(addresses=addresses), patch.object(toplayici.socket, 'getaddrinfo', return_value=records), \
                    patch.object(toplayici, '_PinnedHTTPSConnection') as connection, self.assertRaises(toplayici.CollectionError):
                toplayici.kamusal_baglanti_oku(URL)
            connection.assert_not_called()

    def test_robots_yasagi_sayfayi_okumaz_ve_hicbir_cerez_gonderilmez(self):
        response = toplayici.FetchResult(200, b'User-agent: *\nDisallow: /', {}, 'https://social.example.org/robots.txt')
        with patch.object(toplayici, '_request_once', return_value=response) as request, self.assertRaises(toplayici.CollectionError):
            toplayici.kamusal_baglanti_oku(URL)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.args[2], {})


if __name__ == '__main__':
    unittest.main()
