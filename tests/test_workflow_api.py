"""Araştırma API'sinin kalıcılığını, erişim sınırlarını ve yalnız insan onayıyla devir üretmesini doğrular."""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '.deps'))

from fastapi.testclient import TestClient

from radar.sunucu import uygulama
from radar.toplayici import CollectionError, FetchResult
from tests.test_arastirma_akisi import model, kaynak, haber, METIN, uretim_cagrilari


ADRES = 'http://127.0.0.1:8781'
AKIS_TABLOLARI = ('workflow_settings', 'workflow_runs', 'workflow_meta', 'workflow_tasks')


class ArastirmaApiTestleri(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'akis-api.sqlite3'
        self.now = datetime(2026, 10, 26, 6, tzinfo=timezone.utc)
        self.collector = Mock(return_value={'items': [], 'etag': None, 'modified': None, 'not_modified': False})
        self.fetcher = Mock(side_effect=lambda url, source: FetchResult(
            200, ('<article><h1>Local voice agents and memory models</h1><p>' + METIN + '</p></article>').encode(),
            {'content-type': 'text/html; charset=utf-8'}, url))
        self.provider = Mock(side_effect=model)
        self.credentials = Mock(return_value='FAKE-WORKFLOW-API-KEY')
        self.client = None
        self.open()
        self.addCleanup(self.close)
        self.app.state.depo.kaydet('one', [haber(1), haber(2), haber(3)])
        self.ids = {item['url'].rsplit('-', 1)[-1]: item['id'] for item in self.state()['items']}

    def open(self):
        self.app = uygulama(self.path, sources=[kaynak('one', 'jarvis')], seed=False,
                            collector=self.collector, article_fetcher=self.fetcher,
                            ai_provider=self.provider, credential_provider=self.credentials)
        self.flow = self.app.state.workflow
        for service in (self.flow, self.app.state.ai, self.app.state.reading):
            service.clock = lambda: self.now
        self.client = TestClient(self.app, base_url=ADRES)
        self.client.__enter__()
        self.headers = {'X-Radar-CSRF': self.state()['csrf_token'], 'Origin': ADRES}
        # API işi gerçekten kuyruğa yazar; zamanlayıcı yerine aşağıda tek adım çalıştırılır.
        self.start_patch = patch.object(self.flow, 'baslat')
        self.start_mock = self.start_patch.start()

    def close(self):
        if self.client is not None:
            self.start_patch.stop()
            self.client.__exit__(None, None, None)
            self.client = None

    def state(self):
        response = self.client.get('/api/state')
        self.assertEqual(response.status_code, 200)
        return response.json()

    def post(self, path, payload=None):
        return self.client.post(path, json={} if payload is None else payload, headers=self.headers)

    def versioned(self, task_id, suffix, payload=None):
        base = '/api/workflow/tasks/' + task_id
        current = self.client.get(base).json()
        return self.post(base + suffix, {'expected_version': current['version'], **(payload or {})})

    def queue(self, number='1'):
        response = self.post(f'/api/workflow/items/{self.ids[number]}/research')
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()

    def ready(self, number='1'):
        task = self.queue(number)
        self.assertTrue(self.flow.adim())
        response = self.client.get('/api/workflow/tasks/' + task['id'])
        self.assertEqual(response.status_code, 200)
        value = response.json()
        self.assertEqual(value['status'], 'ready', value['message'])
        return value

    def settings(self, **changes):
        current = self.client.get('/api/workflow').json()['settings']
        return {key: current[key] for key in ('enabled', 'daily_hour', 'screen_limit', 'auto_deep_limit', 'auto_threshold')} | {
            'expected_version': current['version'], **changes}

    def test_varsayilan_acilis_ve_get_istekleri_is_ve_dis_cagri_baslatmaz(self):
        state = self.state()
        self.assertFalse(state['settings']['automatic_research'])
        self.assertFalse(state['settings']['automatic_collection'])
        self.assertFalse(state['settings']['automatic_backup'])
        self.assertFalse(state['workflow']['status']['automatic'])
        self.assertFalse(state['workflow']['status']['enabled'])
        self.assertIsNone(state['workflow']['status']['next_run_at'])
        self.assertIn('bu oturumda kapalı', state['workflow']['status']['message'])
        self.assertIsNone(self.flow.worker)
        self.assertEqual(state['workflow']['tasks'], [])
        task = self.queue()
        before = self.app.state.depo.disa_aktar()
        paths = {'/api/state': 200, '/api/workflow': 200, '/api/health': 200,
                 '/api/workflow/tasks/' + task['id']: 200,
                 '/api/workflow/tasks/' + task['id'] + '/download': 422,
                 '/api/workflow/tasks/' + task['id'] + '/handoff': 422}
        for path, status in paths.items():
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, status)
        after = self.app.state.depo.disa_aktar()
        for table in (*AKIS_TABLOLARI, 'ai_attempts', 'article_read_attempts'):
            self.assertEqual(before[table], after[table])
        for operation in (self.collector, self.fetcher, self.provider):
            operation.assert_not_called()
        self.start_mock.assert_called_once()

    def test_tum_yazma_islemleri_csrf_ister_yabanci_origin_okuyamaz(self):
        task = self.queue()
        base = '/api/workflow/tasks/' + task['id']
        routes = [('/api/workflow/run', {}), ('/api/workflow/settings', self.settings()),
                  (f'/api/workflow/items/{self.ids["2"]}/research', {}), (base + '/retry', {'expected_version': task['version']}),
                  (base + '/seen', {}), (base + '/decision', {'decision': 'develop', 'expected_version': task['version']})]
        before = self.app.state.depo.disa_aktar()
        for path, payload in routes:
            for headers in ({}, {'X-Radar-CSRF': 'yanlis'}):
                with self.subTest(path=path, headers=headers):
                    self.assertEqual(self.client.post(path, json=payload, headers=headers).status_code, 403)
        for path in ('/api/workflow', base, base + '/download', base + '/handoff'):
            self.assertEqual(self.client.get(path, headers={'Origin': 'https://example.org'}).status_code, 403)
        after = self.app.state.depo.disa_aktar()
        for table in AKIS_TABLOLARI:
            self.assertEqual(before[table], after[table])
        self.provider.assert_not_called()
        self.fetcher.assert_not_called()
        self.start_mock.assert_called_once()

    def test_kuyruk_tekrari_ayni_is_ve_gecersiz_istekler_acik_hata_verir(self):
        task = self.queue()
        self.assertEqual(self.queue()['id'], task['id'])
        self.assertEqual(task['status'], 'queued')
        self.assertEqual(task['kind'], 'deep')
        self.assertEqual(task['origin'], 'user')
        self.assertEqual(len(self.state()['workflow']['tasks']), 1)
        base = '/api/workflow/tasks/' + task['id']
        self.assertEqual(self.versioned(task['id'], '/decision', {'decision': 'develop'}).status_code, 409)
        self.assertEqual(self.versioned(task['id'], '/decision', {'decision': 'deploy'}).status_code, 422)
        self.assertEqual(self.versioned(task['id'], '/decision', {'decision': 'develop', 'command': 'execute'}).status_code, 422)
        self.assertEqual(self.post(base + '/decision', {'decision': 'develop'}).status_code, 422)
        self.assertEqual(self.post(base + '/retry').status_code, 422)
        self.assertEqual(self.post(f'/api/workflow/items/{self.ids["2"]}/research', {'execute': True}).status_code, 422)
        for suffix in ('', '/download', '/handoff'):
            self.assertEqual(self.client.get('/api/workflow/tasks/missing' + suffix).status_code, 404)
        for suffix, payload in (('/retry', {'expected_version': 0}), ('/seen', {}),
                                ('/decision', {'decision': 'watch', 'expected_version': 0})):
            self.assertEqual(self.post('/api/workflow/tasks/missing' + suffix, payload).status_code, 404)
        self.assertEqual(self.post('/api/workflow/items/missing/research').status_code, 404)
        self.provider.assert_not_called()
        self.fetcher.assert_not_called()

    def test_hazir_rapor_okunur_indirilir_devir_yalniz_acik_karardan_sonra_acilir(self):
        task = self.ready()
        base = '/api/workflow/tasks/' + task['id']
        self.assertFalse(task['coverage']['limited'])
        self.assertEqual(task['evidence_quality']['status'], 'verified')
        self.assertTrue(task['evidence_quality']['coverage_complete'])
        self.assertIsNone(task['decision'])
        self.assertEqual(self.client.get(base + '/handoff').status_code, 409)
        report = self.client.get(base + '/download')
        self.assertEqual(report.status_code, 200)
        self.assertEqual(report.headers['content-type'], 'text/plain; charset=utf-8')
        self.assertIn('attachment', report.headers['content-disposition'])
        self.assertIn(task['report_text'], report.text)
        self.assertIn(task['sources'][0]['url'], report.text)
        self.assertIn(task['result']['evidence'][0]['quote'], report.text)
        self.assertNotIn('FAKE-WORKFLOW-API-KEY', report.text)
        self.assertEqual(self.state()['workflow']['counts']['notifications'], 1)
        seen = self.post(base + '/seen')
        self.assertEqual(seen.status_code, 200)
        self.assertTrue(seen.json()['notification_read'])
        self.assertIsNone(seen.json()['decision'])
        self.assertEqual(self.state()['workflow']['counts']['notifications'], 0)
        self.assertEqual(self.versioned(task['id'], '/decision', {'decision': 'watch'}).status_code, 200)
        self.assertEqual(self.client.get(base + '/handoff').status_code, 409)
        self.assertEqual(self.versioned(task['id'], '/decision', {'decision': 'develop'}).json()['decision'], 'develop')
        handoff = self.client.get(base + '/handoff')
        self.assertEqual(handoff.status_code, 200)
        self.assertIn('ufuk-gelistirme-devri.txt', handoff.headers['content-disposition'])
        self.assertIn('Model: ' + task['model'], handoff.text)
        self.assertIn('Rapor tarihi: ' + task['report_generated_at'], handoff.text)
        self.assertIn('yetkisi bu dosyayla verilmez', handoff.text)
        self.assertEqual(self.state()['workflow']['counts']['development'], 1)
        self.assertEqual(len(uretim_cagrilari(self.provider)), 1)
        self.assertEqual(self.fetcher.call_count, 1)
        self.collector.assert_not_called()

    def test_robots_nedeniyle_sinirli_rapor_gelistirme_onayi_alamaz(self):
        self.fetcher.side_effect = CollectionError('robots kuralı okumayı engelledi')
        task = self.ready()
        base = '/api/workflow/tasks/' + task['id']
        self.assertTrue(task['coverage']['limited'])
        self.assertEqual(task['coverage']['read_count'], 0)
        response = self.versioned(task['id'], '/decision', {'decision': 'develop'})
        self.assertEqual(response.status_code, 422)
        self.assertIn('sınırlı', response.json()['detail'])
        self.assertIsNone(self.client.get(base).json()['decision'])
        self.assertEqual(self.client.get(base + '/handoff').status_code, 409)
        self.assertIn('robots', self.client.get(base + '/download').text)
        self.assertEqual(self.versioned(task['id'], '/decision', {'decision': 'dismiss'}).json()['decision'], 'dismiss')
        self.assertEqual(len(uretim_cagrilari(self.provider)), 1)
        self.assertEqual(self.fetcher.call_count, 1)

    def test_kaynak_degisince_onay_devri_kapanir_yeniden_deneme_eski_raporu_korur(self):
        task = self.ready()
        base = '/api/workflow/tasks/' + task['id']
        self.assertEqual(self.versioned(task['id'], '/decision', {'decision': 'develop'}).status_code, 200)
        self.app.state.depo.kaydet('one', [haber(1, excerpt=METIN + ' Updated public source information.')])
        stale = self.client.get(base).json()
        self.assertTrue(stale['stale'])
        self.assertEqual(self.versioned(task['id'], '/decision', {'decision': 'develop'}).status_code, 409)
        self.assertEqual(self.client.get(base + '/handoff').status_code, 409)
        report = self.client.get(base + '/download')
        self.assertEqual(report.status_code, 200)
        self.assertIn('eski sürüme aittir', report.text)
        retried = self.versioned(task['id'], '/retry')
        self.assertEqual(retried.status_code, 202)
        self.assertNotEqual(retried.json()['id'], task['id'])
        self.assertEqual(retried.json()['source_revision'], task['source_revision'] + 1)
        self.assertIsNone(retried.json()['decision'])
        self.assertEqual(self.client.get(base).json()['result'], task['result'])
        self.assertEqual(self.state()['workflow']['counts']['development'], 0)
        self.assertEqual(len(uretim_cagrilari(self.provider)), 1)

    def test_ayarlar_surumlu_sinirli_ve_yeniden_acilista_kalicidir(self):
        initial = self.settings()
        changed = {**initial, 'enabled': False, 'daily_hour': 11, 'screen_limit': 4, 'auto_deep_limit': 0}
        response = self.post('/api/workflow/settings', changed)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['version'], initial['expected_version'] + 1)
        self.assertEqual(self.post('/api/workflow/settings', initial).status_code, 409)
        for change in ({'enabled': 'false'}, {'daily_hour': True}, {'daily_hour': 24}, {'screen_limit': 25},
                       {'auto_deep_limit': 6}, {'auto_threshold': 79}, {'expected_version': True}, {'execute': True}):
            with self.subTest(change=change):
                self.assertEqual(self.post('/api/workflow/settings', self.settings(**change)).status_code, 422)
        self.assertIsNone(self.client.get('/api/workflow').json()['status']['next_run_at'])
        self.close()
        self.open()
        saved = self.client.get('/api/workflow').json()['settings']
        for key in ('enabled', 'daily_hour', 'screen_limit', 'auto_deep_limit'):
            self.assertEqual(saved[key], changed[key])
        self.assertEqual(saved['ai_daily_limit'], 20)
        self.assertEqual(saved['article_daily_limit'], 20)
        self.provider.assert_not_called()
        self.fetcher.assert_not_called()
        self.collector.assert_not_called()

    def test_gunluk_run_istegi_kalici_ve_ayni_gun_plan_tekrarsizdir(self):
        settings = self.settings(screen_limit=2, auto_deep_limit=0)
        self.assertEqual(self.post('/api/workflow/settings', settings).status_code, 200)
        response = self.post('/api/workflow/run')
        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.json()['started'])
        with self.app.state.depo.baglanti() as connection:
            self.assertEqual(connection.execute("SELECT value FROM workflow_meta WHERE key='manual_requested'").fetchone()[0], '1')
        self.assertEqual(self.client.get('/api/workflow').json()['tasks'], [])
        with patch.object(self.app.state.collection, 'gorunum', return_value={'running': False, 'remaining_due': 0}):
            self.assertTrue(self.flow.gunu_planla(manual=True)['started'])
            self.assertEqual(self.post('/api/workflow/run').status_code, 202)
            self.assertFalse(self.flow.gunu_planla(manual=True)['started'])
        self.assertEqual(len(self.client.get('/api/workflow').json()['tasks']), 2)
        self.assertTrue(self.flow.adim())
        state = self.client.get('/api/workflow').json()
        self.assertEqual(state['counts']['ready'], 0)
        self.assertEqual(state['counts']['queued'], 2)
        self.assertEqual(sum(task['kind'] == 'interest' for task in state['tasks']), 2)
        self.assertEqual(sum(task['kind'] == 'screen' for task in state['tasks']), 1)
        self.assertTrue(all(task['origin'] == 'user' for task in state['tasks']))
        self.assertEqual(len(uretim_cagrilari(self.provider)), 1)
        self.fetcher.assert_not_called()
        self.collector.assert_not_called()

    def test_tam_disa_aktarim_akisi_model_kanitini_ve_insan_kararini_tasir(self):
        task = self.ready()
        self.assertEqual(self.versioned(task['id'], '/decision', {'decision': 'develop'}).status_code, 200)
        self.assertEqual(self.post('/api/workflow/run').status_code, 202)
        with patch.object(self.app.state.collection, 'gorunum', return_value={'running': False, 'remaining_due': 0}):
            self.flow.gunu_planla(manual=True)
        response = self.client.get('/api/export')
        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment', response.headers['content-disposition'])
        exported = response.json()
        for table in (*AKIS_TABLOLARI, 'ai_results', 'ai_attempts', 'article_reads', 'article_read_attempts'):
            with self.subTest(table=table), self.app.state.depo.baglanti() as connection:
                rows = [dict(row) for row in connection.execute('SELECT * FROM ' + table)]
                self.assertGreater(len(rows), 0)
                self.assertEqual(exported[table], rows)
        saved = next(row for row in exported['workflow_tasks'] if row['id'] == task['id'])
        self.assertEqual(saved['decision'], 'develop')
        self.assertEqual(json.loads(saved['result_json']), task['result'])
        self.assertTrue(any(row['kind'] == 'akis_insan_karari' for row in exported['audit']))
        self.assertNotIn('FAKE-WORKFLOW-API-KEY', json.dumps(exported))
        self.assertNotIn('csrf_token', exported)
        self.close()
        self.open()
        restored = self.client.get('/api/workflow/tasks/' + task['id']).json()
        self.assertEqual(restored['result'], task['result'])
        self.assertEqual(restored['decision'], 'develop')
        self.assertEqual(len(uretim_cagrilari(self.provider)), 1)
        self.assertEqual(self.fetcher.call_count, 1)
        self.collector.assert_not_called()

    def test_iki_pencerede_eski_karar_ve_tekrar_yeni_karari_ezemez(self):
        task = self.ready()
        base = '/api/workflow/tasks/' + task['id']
        old = {'decision': 'develop', 'expected_version': task['version']}
        saved = self.post(base + '/decision', old)
        self.assertEqual(saved.status_code, 200)
        self.assertGreater(saved.json()['version'], task['version'])
        stale_decision = self.post(base + '/decision', {**old, 'decision': 'dismiss'})
        self.assertEqual(stale_decision.status_code, 409)
        self.assertEqual(self.post(base + '/retry', {'expected_version': task['version']}).status_code, 409)
        self.assertEqual(self.client.get(base).json()['decision'], 'develop')
        before = self.app.state.depo.disa_aktar()
        for suffix in ('', '/download', '/handoff'):
            self.assertEqual(self.client.get(base + suffix).status_code, 200)
        after = self.app.state.depo.disa_aktar()
        for table in (*AKIS_TABLOLARI, 'ai_attempts', 'article_read_attempts'):
            self.assertEqual(before[table], after[table])
        self.assertEqual(len(uretim_cagrilari(self.provider)), 1)
        self.assertEqual(self.fetcher.call_count, 1)

    def test_turkce_akis_basligi_ozeti_ve_yarari_guncelken_tek_kart_olarak_aranir(self):
        queries = ('yöntemi', 'anlatıyor', 'yalıtılmış')
        for query in queries:
            self.assertEqual(self.client.get('/api/items', params={'query': query}).json()['total'], 0)
        deep = self.ready()
        screen = self.flow.kuyruga_al(self.ids['1'], 'screen', 'user')
        self.assertTrue(self.flow.adim())
        self.assertEqual(self.flow.oku(screen['id'])['status'], 'ready')
        for query in queries:
            with self.subTest(query=query):
                found = self.client.get('/api/items', params={'query': query, 'limit': 1}).json()
                self.assertEqual(found['total'], 1)
                self.assertEqual([item['id'] for item in found['items']], [self.ids['1']])
                following = self.client.get('/api/items', params={'query': query, 'limit': 1, 'offset': 1}).json()
                self.assertEqual(following['total'], 1)
                self.assertEqual(following['items'], [])
        project = {**deep['profile_snapshot'], 'summary': 'Değişen yerel proje gereksinimi.'}
        with patch('radar.proje_profilleri.profil', return_value=project):
            self.assertEqual(self.client.get('/api/items', params={'query': 'yöntemi'}).json()['total'], 0)
        with self.app.state.depo.baglanti(write=True) as connection:
            connection.execute("UPDATE workflow_tasks SET status='queued'")
        self.assertEqual(self.client.get('/api/items', params={'query': 'yöntemi'}).json()['total'], 0)
        with self.app.state.depo.baglanti(write=True) as connection:
            connection.execute("UPDATE workflow_tasks SET status='ready'")
            connection.execute("UPDATE article_reads SET body_hash='changed-body-hash' WHERE item_id=?", (self.ids['1'],))
        self.assertEqual(self.client.get('/api/items', params={'query': 'yöntemi'}).json()['total'], 0)
        self.assertEqual(len(uretim_cagrilari(self.provider)), 2)
        self.assertEqual(self.fetcher.call_count, 1)

    def test_ayni_haberin_iki_soru_raporu_genel_haber_aramasina_karismaz(self):
        general = self.ready()
        requests = self.app.state.research_requests
        rows = []
        for suffix in ('birinci', 'ikinci'):
            request = requests.olustur('Voice memory yöntemi için ' + suffix + ' soru nedir?', 'jarvis',
                                      idempotency_key='arama-sorusu-' + suffix)
            linked = requests.adaylari_bagla(request['id'], [self.ids['1']], expected_version=request['version'])
            task = linked['tasks'][0]
            # Yalnız arama ayrımını sınayan kayıtlı rapor düzeneği: her sorunun kendi
            # profil izi korunur; ortak güncel kaynağa farklı görünür başlık verilir.
            result = {**general['result'], 'title_tr': suffix + 'sorusununözelbaşlığı'}
            with self.app.state.depo.baglanti(write=True) as connection:
                connection.execute("UPDATE workflow_tasks SET status='ready',result_json=?,sources_json=? WHERE id=?",
                                   (json.dumps(result, ensure_ascii=False), json.dumps(general['sources']), task['id']))
            rows.append((request['id'], task['id'], result['title_tr']))
        self.assertEqual(len(self.client.get('/api/workflow').json()['tasks']), 3)
        found = self.client.get('/api/items', params={'query': general['result']['title_tr']}).json()
        self.assertEqual([item['id'] for item in found['items']], [self.ids['1']])
        for request_id, task_id, title in rows:
            response = self.client.get('/api/workflow/tasks/' + task_id).json()
            self.assertFalse(response['stale'])
            self.assertEqual(response['request_id'], request_id)
            self.assertEqual(response['result']['title_tr'], title)
            self.assertEqual(self.client.get('/api/items', params={'query': title}).json()['total'], 0)
            saved = self.client.get('/api/research-requests/' + request_id).json()
            self.assertEqual([task['id'] for task in saved['tasks']], [task_id])


if __name__ == '__main__':
    unittest.main()
