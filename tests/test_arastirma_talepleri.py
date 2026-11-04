"""Soru taleplerinin kalıcılığını, ayrı bağlamını, kaynak seçimini ve iptal çakışmalarını doğrular."""

import json
import sqlite3
import threading
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '.deps'))

from radar.arastirma_talepleri import ArastirmaTalepleri, gorev_kaynak_baglami, tablolari_hazirla
from radar.calisma_masasi import SurumCatismasi
from radar.toplayici import FetchResult
from tests import test_arastirma_akisi as duzenek


class ArastirmaTalepleriTestleri(unittest.TestCase):
    open = duzenek.ArastirmaAkisiTestleri.open
    close = duzenek.ArastirmaAkisiTestleri.close

    def setUp(self):
        duzenek.ArastirmaAkisiTestleri.setUp(self)
        self.requests = ArastirmaTalepleri(self.depo, self.flow)
        self.addCleanup(lambda: self.requests.kapan())

    def create(self, key='request-01', **fields):
        return self.requests.olustur('Voice memory yöntemi nasıl çalışır?', 'jarvis', idempotency_key=key, **fields)

    def test_ayni_anahtar_ayni_talep_farkli_icerik_catismadir(self):
        first = self.create()
        self.assertEqual(self.create()['id'], first['id'])
        with self.assertRaises(SurumCatismasi):
            self.create(need='Farklı ihtiyaç')
        self.assertEqual(len(self.requests.listele()), 1)

    def test_yeni_gelistirici_konulari_model_cagirmadan_kalici_talep_olur(self):
        for topic in ("software", "security", "data"):
            with self.subTest(topic=topic):
                request = self.requests.olustur("Geliştirici araçlarının güncel sınırları nelerdir?", topic,
                                               idempotency_key="new-topic-" + topic)
                self.assertEqual(self.requests.oku(request["id"])["topic"], topic)
                self.assertEqual(request["status"], "awaiting_sources")
        self.provider.assert_not_called()

    def test_farkli_sorular_ayni_haberde_farkli_is_ve_profil_tasir(self):
        first = self.create()
        second = self.requests.olustur('Voice memory yönteminin sınırları nelerdir?', 'jarvis', idempotency_key='request-02')
        one = self.requests.adaylari_bagla(first['id'], [self.ids[0]], expected_version=first['version'])
        two = self.requests.adaylari_bagla(second['id'], [self.ids[0]], expected_version=second['version'])
        self.assertNotEqual(one['tasks'][0]['id'], two['tasks'][0]['id'])
        self.assertNotEqual(one['tasks'][0]['profile_hash'], two['tasks'][0]['profile_hash'])
        self.assertIn(first['question'], one['tasks'][0]['profile_snapshot']['summary'])
        self.assertFalse(one['tasks'][0]['stale'])
        self.assertFalse(two['tasks'][0]['stale'])
        self.provider.assert_not_called()

    def test_ilgisiz_ilk_aday_deep_olmaz_kaynak_yok_acik_soylenir(self):
        request = self.requests.olustur('Hidroponik marul besleme çözeltisi nedir?', 'jarvis', idempotency_key='request-03')
        result = self.requests.adaylari_bagla(request['id'], self.ids[:2], expected_version=request['version'])
        self.assertEqual(result['status'], 'no_sources')
        self.assertEqual(result['tasks'], [])
        self.assertFalse(any(row['eligible'] for row in result['candidates']))

    def test_turkce_ses_bellek_sorusu_ingilizce_kaynakla_acik_sozlukten_eslesir(self):
        request = self.requests.olustur('Ses ve bellek yöntemlerinin sınırları nelerdir?', 'jarvis', idempotency_key='request-tr')
        result = self.requests.adaylari_bagla(request['id'], self.ids[:1], expected_version=request['version'])
        self.assertEqual(result['status'], 'queued')
        self.assertIn('memory', result['candidates'][0]['matched_terms'])
        self.assertEqual(result['candidates'][0]['relevance_method'], 'bilingual_keyword_rules')

    def test_butce_aday_sayisini_sinirlar_ve_tek_derin_is_acar(self):
        request = self.create(budget={'max_candidates': 2, 'max_deep_tasks': 1})
        result = self.requests.adaylari_bagla(request['id'], self.ids, expected_version=request['version'])
        self.assertEqual(len(result['candidates']), 2)
        self.assertEqual(len(result['tasks']), 1)
        self.assertEqual(result['status'], 'queued')
        with self.assertRaises(SurumCatismasi):
            self.requests.adaylari_bagla(request['id'], self.ids, expected_version=request['version'])

    def test_kesif_run_kesinti_sonrasi_ayni_kimlikle_devam_eder(self):
        request = self.create()
        discovery = Mock()
        discovery.baslat.return_value = {'run_id': 'real-run-1'}
        discovery.oku.return_value = {'status': 'running', 'item_ids': []}
        self.assertTrue(self.requests.adim(discovery))
        self.requests.kapan()
        self.requests = ArastirmaTalepleri(self.depo, self.flow)
        self.assertFalse(self.requests.adim(discovery))
        discovery.baslat.assert_called_once_with(topic='jarvis', query=request['question'])
        discovery.oku.return_value = {'status': 'completed', 'item_ids': self.ids}
        self.assertTrue(self.requests.adim(discovery))
        result = self.requests.oku(request['id'])
        self.assertEqual(result['discovery_run_id'], 'real-run-1')
        self.assertEqual(len(result['tasks']), 1)
        self.assertEqual(result['status'], 'queued')

    def test_iptal_ve_eski_pencere_ayni_isi_yeniden_acamaz(self):
        request = self.create()
        queued = self.requests.adaylari_bagla(request['id'], self.ids, expected_version=request['version'])
        with self.assertRaises(SurumCatismasi):
            self.requests.iptal(request['id'], expected_version=request['version'])
        cancelled = self.requests.iptal(request['id'], expected_version=queued['version'])
        self.assertEqual(cancelled['status'], 'cancelled')
        self.assertEqual(cancelled['tasks'][0]['status'], 'cancelled')
        self.assertFalse(self.flow.adim())
        task = cancelled['tasks'][0]
        with self.assertRaises(SurumCatismasi):
            self.flow.tekrar(task['id'], expected_version=task['version'])
        self.requests.kapan()
        self.requests = ArastirmaTalepleri(self.depo, self.flow)
        self.assertEqual(self.requests.oku(request['id'])['status'], 'cancelled')

    def test_devam_eden_kesif_iptalde_durdurulmaz_sonucu_gorev_acmaz(self):
        request = self.create()
        discovery = Mock()
        discovery.baslat.return_value = {'run_id': 'shared-1'}
        self.requests.adim(discovery)
        running = self.requests.oku(request['id'])
        self.requests.iptal(request['id'], expected_version=running['version'])
        discovery.oku.return_value = {'status': 'completed', 'item_ids': self.ids}
        self.assertFalse(self.requests.adim(discovery))
        discovery.kapan.assert_not_called()
        discovery.oku.assert_not_called()
        self.assertEqual(self.requests.oku(request['id'])['tasks'], [])

    def test_hata_bos_basarili_sonuc_sayilmaz(self):
        request = self.create()
        discovery = Mock()
        discovery.baslat.return_value = {'run_id': 'failed-1'}
        discovery.oku.return_value = {'status': 'failed', 'item_ids': []}
        self.requests.adim(discovery)
        self.requests.adim(discovery)
        self.assertEqual(self.requests.oku(request['id'])['status'], 'failed')

    def test_sir_yanlis_tip_butce_ve_gecersiz_kaynak_reddedilir(self):
        for fields in ({'need': 'api_key=VERY_PRIVATE_SECRET'}, {'scope': ['x'] * 6},
                       {'budget': {'max_candidates': True, 'max_deep_tasks': 1}},
                       {'budget': {'max_candidates': 10, 'max_deep_tasks': 2}}):
            with self.subTest(fields=list(fields)):
                with self.assertRaises(ValueError):
                    self.create(**fields)
        request = self.create()
        with self.assertRaises(KeyError):
            self.requests.adaylari_bagla(request['id'], ['unknown'], expected_version=request['version'])
        self.assertEqual(self.requests.oku(request['id'])['status'], 'awaiting_sources')
        with self.depo.baglanti() as connection:
            self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_hazir_ama_denetlenmemis_rapor_talebi_tam_basarili_sayilmaz(self):
        request = self.create()
        queued = self.requests.adaylari_bagla(request['id'], self.ids, expected_version=request['version'])
        task_id = queued['tasks'][0]['id']
        with self.depo.baglanti(write=True) as connection:
            connection.execute("UPDATE workflow_tasks SET status='ready',result_json=?,coverage_json=? WHERE id=?",
                               (json.dumps({'title_tr': 'Önceki rapor'}), json.dumps({'limited': False}), task_id))
        self.requests.adim()
        result = self.requests.oku(request['id'])
        self.assertEqual(result['status'], 'partial')
        self.assertTrue(result['results'][0]['requires_verification'])

    def test_farkli_soru_onceki_sorunun_hazir_model_yanitini_paylasmaz(self):
        first = self.create()
        second = self.requests.olustur('Voice memory yönteminin sınırları nelerdir?', 'jarvis', idempotency_key='request-two')
        for request in (first, second):
            self.requests.adaylari_bagla(request['id'], self.ids[:1], expected_version=request['version'])
        self.assertTrue(self.flow.adim())
        self.now += timedelta(minutes=6)
        self.assertTrue(self.flow.adim())
        calls = duzenek.uretim_cagrilari(self.provider)
        self.assertEqual(len(calls), 2)
        summaries = [json.loads(call.args[0][1]['content'])['local_requirements']['summary'] for call in calls]
        self.assertTrue(any(first['question'] in summary for summary in summaries))
        self.assertTrue(any(second['question'] in summary for summary in summaries))
        for _ in range(3):
            self.requests.adim()
        for request in (first, second):
            current = self.requests.oku(request['id'])
            self.assertEqual(current['status'], 'ready', current['message'])
            self.assertTrue(current['results'][0]['can_develop'])
            self.assertIsNone(current['results'][0]['decision'])

    def test_model_surerken_iptal_edilen_talep_gec_sonucla_hazir_olmaz(self):
        request = self.create()
        queued = self.requests.adaylari_bagla(request['id'], self.ids[:1], expected_version=request['version'])
        entered, release = threading.Event(), threading.Event()
        def slow(messages, **kwargs):
            if json.loads(messages[1]['content'])['kind'] != 'verify':
                entered.set()
                release.wait(5)
            return duzenek.model(messages, **kwargs)
        self.provider.side_effect = slow
        worker = threading.Thread(target=self.flow.adim)
        worker.start()
        try:
            self.assertTrue(entered.wait(5))
            self.requests.iptal(request['id'], expected_version=queued['version'])
        finally:
            release.set()
            worker.join(timeout=8)
        self.assertFalse(worker.is_alive())
        current = self.requests.oku(request['id'])
        self.assertEqual(current['status'], 'cancelled')
        self.assertEqual(current['tasks'][0]['status'], 'cancelled')
        self.assertEqual(current['results'], [])
        self.assertFalse(self.requests.adim())

    def test_baglanti_olusturma_agsizdir_okuma_kalici_ve_tekrarsizdir(self):
        url = 'https://social.example.org/post/1'
        body = ('<article><h1>Public voice memory experiment</h1><p>' +
                'Voice memory latency is claimed to improve in this experiment. ' * 5 + '</p></article>').encode()
        self.requests.link_fetcher = Mock(return_value=FetchResult(200, body, {'content-type': 'text/html'}, url))
        request = self.create(source_url=url, claim_text='Voice memory latency improves.', context_text='Quoted transcript.\nSecond line.')
        self.assertEqual(request['link_access']['status'], 'pending')
        self.requests.link_fetcher.assert_not_called()
        discovery = Mock()
        discovery.baslat.return_value = {'run_id': 'claim-run'}
        self.assertTrue(self.requests.adim(discovery))
        discovery.baslat.assert_not_called()
        saved = self.requests.oku(request['id'])
        self.assertEqual(saved['link_access']['status'], 'read')
        self.assertFalse(saved['link_access']['media_processed'])
        self.assertTrue(self.requests.adim(discovery))
        self.assertTrue(discovery.baslat.call_args.kwargs['preserve_query'])
        self.assertIn('Voice memory latency improves.', discovery.baslat.call_args.kwargs['query'])
        self.requests.link_fetcher.assert_called_once()
        self.assertEqual(self.create(source_url=url, claim_text='Voice memory latency improves.',
                                     context_text='Quoted transcript.\nSecond line.')['id'], request['id'])
        self.requests.kapan()
        self.requests = ArastirmaTalepleri(self.depo, self.flow, link_fetcher=Mock())
        self.assertEqual(self.requests.oku(request['id'])['link_access'], saved['link_access'])

    def test_erisim_engeli_iddia_ve_transkriptle_bagimsiz_kesfi_durdurmaz(self):
        url = 'https://www.instagram.com/reel/example'
        self.requests.link_fetcher = Mock(return_value=FetchResult(403, b'', {}, url))
        request = self.requests.olustur('Bu iddia doğru mu, kaynaklarla araştır?', 'jarvis',
            source_url=url, claim_text='Voice memory latency improves.', context_text='Kullanıcının verdiği açıklama.',
            idempotency_key='claim-access-01')
        discovery = Mock()
        discovery.baslat.return_value = {'run_id': 'claim-blocked'}
        discovery.oku.return_value = {'status': 'completed', 'item_ids': self.ids[:1]}
        self.requests.adim(discovery)
        self.requests.adim(discovery)
        self.requests.adim(discovery)
        saved = self.requests.oku(request['id'])
        self.assertEqual(saved['link_access']['status'], 'unavailable')
        self.assertEqual(saved['link_access']['http_status'], 403)
        self.assertEqual(saved['status'], 'queued')
        self.assertEqual(saved['link_access']['text'], '')
        with self.depo.baglanti() as connection:
            context = gorev_kaynak_baglami(saved['tasks'][0], connection)
        self.assertEqual(context['claim_text'], 'Voice memory latency improves.')
        self.assertFalse(context['independent_evidence'])
        self.assertFalse(context['media_processed'])
        self.assertIn('güvenilmeyen iddia', ' '.join(saved['tasks'][0]['profile_snapshot']['constraints']))
        self.provider.assert_not_called()

    def test_ayni_soru_farkli_iddia_icin_eski_profil_ozetini_kullanmaz(self):
        first = self.create(key='claim-one', claim_text='Voice memory improves by ten percent.')
        second = self.create(key='claim-two', claim_text='Voice memory improves by ninety percent.')
        one = self.requests.adaylari_bagla(first['id'], self.ids[:1], expected_version=first['version'])
        two = self.requests.adaylari_bagla(second['id'], self.ids[:1], expected_version=second['version'])
        self.assertNotEqual(one['tasks'][0]['profile_hash'], two['tasks'][0]['profile_hash'])
        with self.assertRaises(SurumCatismasi):
            self.create(key='claim-one', claim_text=second['claim_text'])

    def test_arastirilan_url_kendi_kendisinin_bagimsiz_kaynagi_olamaz(self):
        with self.depo.baglanti() as connection:
            url = connection.execute('SELECT url FROM items WHERE id=?', (self.ids[0],)).fetchone()[0]
        self.requests.link_fetcher = Mock(return_value=FetchResult(403, b'', {}, url))
        request = self.create(source_url=url)
        self.requests.adim()
        updated = self.requests.oku(request['id'])
        result = self.requests.adaylari_bagla(request['id'], self.ids[:1], expected_version=updated['version'])
        self.assertEqual(result['status'], 'no_sources')
        self.assertEqual(result['tasks'], [])
        self.assertFalse(result['candidates'][0]['eligible'])

    def test_yalniz_baglanti_okunabildiginde_basligi_kesif_sorgusuna_katilir(self):
        url = 'https://social.example.org/post/2'
        title = 'PostgreSQL query latency measurement'
        body = ('<article><h1>' + title + '</h1><p>' + 'Public measurement statement. ' * 12 + '</p></article>').encode()
        self.requests.link_fetcher = Mock(return_value=FetchResult(200, body, {'content-type': 'text/html'}, url))
        self.requests.olustur('Bu bağlantıyı kaynaklarla araştır.', 'data', source_url=url, idempotency_key='url-only-01')
        discovery = Mock()
        discovery.baslat.return_value = {'run_id': 'url-only-run'}
        self.requests.adim(discovery)
        self.requests.adim(discovery)
        self.assertIn(title, discovery.baslat.call_args.kwargs['query'])
        self.assertTrue(discovery.baslat.call_args.kwargs['preserve_query'])

    def test_yeni_baglantilar_okunmus_talebin_kesfini_geciktirmez(self):
        self.requests.link_fetcher = Mock(side_effect=lambda url, **kwargs: FetchResult(403, b'', {}, url))
        self.create(key='first-link', source_url='https://social.example.org/first')
        self.now += timedelta(seconds=1)
        self.create(key='second-link', source_url='https://social.example.org/second')
        discovery = Mock()
        discovery.baslat.return_value = {'run_id': 'first-link-run'}
        self.requests.adim(discovery)
        self.requests.adim(discovery)
        self.requests.link_fetcher.assert_called_once()
        discovery.baslat.assert_called_once()

    def test_baglanti_okunurken_iptal_gec_metni_ve_yeni_kesfi_engeller(self):
        entered, release = threading.Event(), threading.Event()
        url = 'https://social.example.org/post/1'
        def slow(source_url, **kwargs):
            entered.set()
            release.wait(5)
            return FetchResult(403, b'', {}, source_url)
        self.requests.link_fetcher = slow
        request = self.create(source_url=url)
        discovery = Mock()
        worker = threading.Thread(target=self.requests.adim, args=(discovery,))
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            self.requests.iptal(request['id'], expected_version=request['version'])
        finally:
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        saved = self.requests.oku(request['id'])
        self.assertEqual(saved['status'], 'cancelled')
        self.assertEqual(saved['link_access']['status'], 'pending')
        discovery.baslat.assert_not_called()
        self.assertFalse(self.requests.adim(discovery))

    def test_kullanici_metinleri_ve_url_sinirli_ve_sirsiz_olmalidir(self):
        for fields in ({'source_url': 'https://127.0.0.1/private'}, {'source_url': None},
                       {'claim_text': 'x' * 1001}, {'context_text': 'x' * 6001},
                       {'claim_text': 'password=PRIVATE_TEST_VALUE'}, {'context_text': 'bad\x00text'}):
            with self.subTest(fields=list(fields)), self.assertRaises(ValueError):
                self.create(**fields)
        self.assertEqual(self.requests.listele(), [])


class TalepGecisTestleri(unittest.TestCase):
    def test_eski_talep_alani_veri_kaybi_ve_ozet_degismeden_genisler(self):
        connection = sqlite3.connect(':memory:')
        self.addCleanup(connection.close)
        connection.row_factory = sqlite3.Row
        connection.execute('''CREATE TABLE research_requests (
            id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE, input_hash TEXT NOT NULL,
            question TEXT NOT NULL, topic TEXT NOT NULL, need TEXT NOT NULL,
            scope_json TEXT NOT NULL, budget_json TEXT NOT NULL, status TEXT NOT NULL,
            discovery_run_id TEXT, candidates_json TEXT NOT NULL DEFAULT '[]', message TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
        connection.execute('INSERT INTO research_requests VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            ('old', 'legacy-key', 'legacy-hash', 'Old research question', 'ai', 'Need', '[]', '{}', 'ready',
             'run-old', '[]', 'Saved message', 7, '2026-10-01', '2026-10-02'))
        original = dict(connection.execute('SELECT * FROM research_requests').fetchone())
        tablolari_hazirla(connection)
        tablolari_hazirla(connection)
        upgraded = dict(connection.execute('SELECT * FROM research_requests').fetchone())
        self.assertEqual({key: upgraded[key] for key in original}, original)
        self.assertEqual(upgraded['source_url'], '')
        self.assertEqual(upgraded['claim_text'], '')
        self.assertEqual(upgraded['context_text'], '')
        self.assertEqual(upgraded['link_access_json'], '{}')
        self.assertIsNone(gorev_kaynak_baglami({'request_id': 'old'}, connection))


if __name__ == '__main__':
    unittest.main()
