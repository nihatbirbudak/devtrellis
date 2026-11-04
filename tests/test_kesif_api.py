"""Konu keşfi ve soru taleplerinin yerel yetkisini, tekrar güvenliğini ve kalıcılığını doğrular."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '.deps'))

from fastapi.testclient import TestClient
from radar.kesif import KesifMesgul
from radar.sunucu import uygulama

ADRES = 'http://127.0.0.1:8781'


class KesifApiTestleri(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'radar.sqlite3'
        self.open()
        self.addCleanup(self.close)

    def open(self):
        self.fetcher = Mock(side_effect=AssertionError('Bu API testinde dış istek olmamalı.'))
        self.app = uygulama(self.path, sources=[], seed=False, discovery_fetcher=self.fetcher)
        self.client = TestClient(self.app, base_url=ADRES)
        self.client.__enter__()
        self.headers = {'X-Radar-CSRF': self.client.get('/api/state').json()['csrf_token'], 'Origin': ADRES}
        self.patches = [patch.object(self.app.state.workflow, 'baslat'),
                        patch.object(self.app.state.research_requests, 'zamanli_calistir')]
        for value in self.patches:
            value.start()

    def close(self):
        if self.client is not None:
            for value in self.patches:
                value.stop()
            self.client.__exit__(None, None, None)
            self.client = None

    def payload(self, **changes):
        return {'question': 'Ev otomasyonu için kaynaklı araştırma nasıl gelişir?', 'topic': 'jarvis',
                'idempotency_key': 'test-question-001', **changes}

    def post(self, url, body):
        return self.client.post(url, json=body, headers=self.headers)

    def test_get_kesif_baslatmaz_ve_yazma_yerel_oturum_gerektirir(self):
        for url in ('/api/discovery', '/api/research-requests'):
            self.assertEqual(self.client.get(url).status_code, 200)
            self.assertEqual(self.client.post(url, json={}).status_code, 403)
            self.assertEqual(self.client.get(url, headers={'Origin': 'https://example.org'}).status_code, 403)
        self.fetcher.assert_not_called()

    def test_ayni_talep_tekrarinda_tek_kayit_ve_surumlu_iptal_kalici(self):
        first = self.post('/api/research-requests', self.payload())
        self.assertEqual(first.status_code, 202, first.text)
        second = self.post('/api/research-requests', self.payload())
        self.assertEqual(second.json()['id'], first.json()['id'])
        conflict = self.post('/api/research-requests', self.payload(question='Farklı bir araştırma sorusu'))
        self.assertEqual(conflict.status_code, 409)
        row = first.json()
        url = '/api/research-requests/' + row['id']
        stale = self.post(url + '/cancel', {'expected_version': row['version'] + 1})
        self.assertEqual(stale.status_code, 409)
        cancelled = self.post(url + '/cancel', {'expected_version': row['version']})
        self.assertEqual(cancelled.json()['status'], 'cancelled')
        self.close()
        self.open()
        self.assertEqual(self.client.get(url).json()['status'], 'cancelled')
        self.assertEqual(len(self.client.get('/api/research-requests').json()['requests']), 1)
        exported = self.client.get('/api/export').json()
        self.assertEqual(exported['research_requests'][0]['id'], row['id'])
        self.fetcher.assert_not_called()

    def test_bilinmeyen_kayit_ve_gecersiz_kapsam_butce_reddedilir(self):
        self.assertEqual(self.client.get('/api/research-requests/olmayan').status_code, 404)
        cases = [{'question': '   '}, {'topic': 'baska'}, {'question': 'x' * 501}, {'scope': ['x' * 121]},
                 {'budget': {'max_candidates': 21, 'max_deep_tasks': 1}},
                 {'budget': {'max_candidates': True, 'max_deep_tasks': 1}},
                 {'budget': {'max_candidates': 10, 'max_deep_tasks': 2}}, {'idempotency_key': '../kisa'},
                 {'execute': True}]
        for changes in cases:
            with self.subTest(changes=changes):
                result = self.post('/api/research-requests', self.payload(**changes))
                self.assertEqual(result.status_code, 422, result.text)
        self.assertEqual(self.client.get('/api/research-requests').json()['requests'], [])

    def test_mesgul_kesif_409_ve_tek_yazma_ile_baslatma(self):
        with patch.object(self.app.state.discovery, 'baslat', side_effect=KesifMesgul('Keşif sürüyor.')):
            self.assertEqual(self.post('/api/discovery', {'topic': 'ai'}).status_code, 409)
        with patch.object(self.app.state.discovery, 'baslat', return_value={'run_id': 'r1'}) as start:
            response = self.post('/api/discovery', {'topic': 'jarvis', 'query': 'agent research'})
            self.assertEqual(response.status_code, 202, response.text)
            start.assert_called_once_with(topic='jarvis', query='agent research')
        self.assertEqual(self.post('/api/discovery', {'topic': 'ai', 'query': 'x'}).status_code, 422)


if __name__ == '__main__':
    unittest.main()
