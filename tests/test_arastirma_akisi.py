"""Kalıcı araştırmanın göçünü, günlük takibini, gerçek işçilerini ve yalnız insan kararıyla devrini doğrular."""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '.deps'))
from radar.arastirma_akisi import ArastirmaAkisi
from radar.calisma_masasi import SurumCatismasi
from radar.depo import Depo
from radar.hizmet import Toplama
from radar.okuma import OkumaIsleri
from radar.toplayici import FetchResult
from radar.zeka import NvidiaYaniti, ZekaIsleri
from tests.test_tam_uygulama import kaynak, haber, METIN


def model(messages, **kwargs):
    payload = json.loads(messages[1]['content'])
    if payload['kind'] == 'verify':
        # Bu sabit düzeneğin kaynakta karşılığı olan cümleleri ayrı ayrı incelendi.
        # Başka bir metin otomatik olarak destekli sayılmaz.
        supported = {'Yerel ses ve bellek yöntemi',
                     'Kaynak, yerel ses ajanlarının bellek modellerini ve sensörlerle çalışmasını anlatıyor.',
                     'Donanım seçimi öncesinde bağımsız test gerektiği belirtiliyor.',
                     'Kaynakta yerel bilgisayar ve sensörlerin birlikte çalışması ele alınıyor.',
                     'Sera donanımı seçmeden önce bağımsız test gerekiyor.'}
        suggestions = {'Yalıtılmış bir donanım denemesi değerlendirilmeli.',
                       'Özgün yöntem ve donanım gereksinimleri incelenmeli.',
                       'Mevcut donanımla küçük bir örnek denenmeli.',
                       'Mevcut sonuçla gecikme ve hata oranı karşılaştırılmalı.',
                       'Mevcut ses hattıyla uyum için hangi ölçümler gerekiyor?'}
        return json.dumps({'coverage_complete': True, 'claims': [
            {'id': claim['id'], 'status': 'supported' if claim['text'] in supported else
             'not_factual' if claim['text'] in suggestions or claim['id'] in payload.get('application_facts', []) else 'uncertain',
             'reason_tr': 'Deneme kaynağının pasajı bu ifadeyi destekliyor.' if claim['text'] in supported else
                          'Bu cümle henüz yapılmamış bir deneme önerisi veya açık sorudur.',
             'evidence': [payload['passages'][0]['passage_id']] if claim['text'] in supported else []}
            for claim in payload['claims']]}, ensure_ascii=False)
    sources = payload['sources']
    value = {'title_tr': 'Yerel ses ve bellek yöntemi',
             'summary_tr': 'Kaynak, yerel ses ajanlarının bellek modellerini ve sensörlerle çalışmasını anlatıyor.',
             'benefit_tr': 'Yalıtılmış bir donanım denemesi değerlendirilmeli.',
             'limitations': ['Donanım seçimi öncesinde bağımsız test gerektiği belirtiliyor.'],
             'next_steps': ['Özgün yöntem ve donanım gereksinimleri incelenmeli.'],
             'evidence': [{'item_id': source['item_id'], 'quote_id': source['quote_options'][0]['id']} for source in sources]}
    if payload['kind'] in {'interest', 'screen', 'review'}:
        value.update(relevance_score=96, relevance_reason_tr='Yerel ses işlemleri Ev otomasyonu gereksinimleri ile örtüşüyor.', fit='high', blockers=[])
        if payload['kind'] == 'review':
            value['research_decision'] = 'research'
    else:
        value.update(recommendation='denemeye_deger', compatibility_tr='Kaynakta yerel bilgisayar ve sensörlerin birlikte çalışması ele alınıyor.',
                     requirements=['Sera donanımı seçmeden önce bağımsız test gerekiyor.'],
                     integration_steps=['Mevcut donanımla küçük bir örnek denenmeli.'],
                     verification_plan=['Mevcut sonuçla gecikme ve hata oranı karşılaştırılmalı.'],
                     open_questions=['Mevcut ses hattıyla uyum için hangi ölçümler gerekiyor?'])
    return json.dumps(value, ensure_ascii=False)


def uretim_cagrilari(provider):
    return [call for call in provider.call_args_list if json.loads(call.args[0][1]['content'])['kind'] != 'verify']


class ArastirmaAkisiTestleri(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'akis.sqlite3'
        self.depo = Depo(self.path, [kaynak('one', 'jarvis'), kaynak('two', 'jarvis')])
        self.depo.kaydet('one', [haber(i) for i in range(1, 11)])
        self.ids = [item['id'] for item in self.depo.gorunum()['items']]
        self.now = datetime(2026, 10, 26, 6, tzinfo=timezone.utc)
        self.clock = lambda: self.now
        self.fetcher = Mock(side_effect=lambda url, source: FetchResult(200,
            ('<article><h1>Local voice agents and memory models</h1><p>' + METIN + '</p></article>').encode(),
            {'content-type': 'text/html'}, url))
        self.provider = Mock(side_effect=model)
        self.collection = Mock()
        self.collection.gorunum.return_value = {'running': False, 'remaining_due': 0}
        self.open()
        self.addCleanup(self.close)

    def open(self):
        self.reading = OkumaIsleri(self.depo, fetcher=self.fetcher, clock=self.clock, daily_limit=80)
        self.ai = ZekaIsleri(self.depo, provider=self.provider, credential_provider=lambda: 'FAKE-LOCAL-TEST', clock=self.clock, daily_limit=80)
        self.flow = ArastirmaAkisi(self.depo, self.reading, self.ai, self.collection, clock=self.clock)

    def close(self):
        self.flow.kapan()
        self.ai.kapan()
        self.reading.kapan()

    def test_rapor_dili_ve_modeli_ayar_degisiminden_ve_yeniden_acilistan_etkilenmez(self):
        task = self.flow.kuyruga_al(self.ids[0], 'deep', 'user')
        self.assertTrue(self.flow.adim())
        saved = self.flow.oku(task['id'])
        self.assertEqual(saved['status'], 'ready', saved['message'])
        self.assertEqual(saved['language'], 'tr')
        self.assertEqual(saved['model'], self.ai.oku(self.ids[0], 'deep')['model'])
        self.assertTrue(saved['model'])
        self.assertEqual(saved['report_generated_at'], self.ai.oku(self.ids[0], 'deep')['generated_at'])
        self.now += timedelta(days=1)
        self.flow.karar(task['id'], 'watch', expected_version=saved['version'])
        self.ai.output_language = 'en'
        self.ai.model_adi = Mock(return_value='codex/different-configured-model')
        changed = self.flow.oku(task['id'])
        self.assertEqual((changed['language'], changed['model']), (saved['language'], saved['model']))
        self.assertEqual(changed['result'], saved['result'])
        self.close()
        self.open()
        self.ai.output_language = 'en'
        self.ai.model_adi = Mock(return_value='codex/different-configured-model')
        reopened = self.flow.oku(task['id'])
        self.assertEqual((reopened['language'], reopened['model']), (saved['language'], saved['model']))
        self.assertEqual(reopened['result'], saved['result'])
        exported = self.flow.dosya(task['id'])
        self.assertIn('Model: ' + saved['model'], exported)
        self.assertIn('Rapor tarihi: ' + saved['report_generated_at'], exported)
        self.assertNotIn('Rapor tarihi: ' + reopened['updated_at'], exported)
        self.assertIn('Kanıt durumu: ' + saved['evidence_quality']['status'], exported)

    def test_talep_baglami_isciye_ayri_veri_olarak_aktarilir(self):
        from radar.arastirma_talepleri import ArastirmaTalepleri
        requests = ArastirmaTalepleri(self.depo, self.flow, link_fetcher=Mock(side_effect=RuntimeError('offline fixture')))
        self.addCleanup(requests.kapan)
        request = requests.olustur('Voice memory yöntemi nasıl çalışır?', 'jarvis', idempotency_key='context-flow',
                                  source_url='https://example.org/social-claim',
                                  claim_text='Bu yöntem bütün bellek sorunlarını çözüyor.',
                                  context_text='Kullanıcı tarafından sağlanan doğrulanmamış transkript.')
        self.assertTrue(requests.adim())
        request = requests.oku(request['id'])
        linked = requests.adaylari_bagla(request['id'], self.ids[:1], expected_version=request['version'])
        self.assertEqual(linked['status'], 'queued')
        self.assertTrue(self.flow.adim())
        ready = self.flow.oku(linked['tasks'][0]['id'])
        self.assertEqual(ready['status'], 'ready', ready['message'])
        payload = json.loads(uretim_cagrilari(self.provider)[0].args[0][1]['content'])
        self.assertEqual(payload['untrusted_request_context']['claim_text'], request['claim_text'])
        self.assertIs(payload['untrusted_request_context']['independent_evidence'], False)
        self.assertTrue(self.ai.oku(self.ids[0], 'deep')['request_context_hash'])
        self.assertTrue(any('Video veya ses izlenmedi' in scope for scope in ready['scope_notes']))

    def test_gercek_model_surumu_recoveryde_yeni_cagri_acmaz_model_ayari_degismesi_acar(self):
        self.provider.side_effect = lambda messages, **options: NvidiaYaniti(
            model(messages, **options), 'nvidia/actual-response-version')
        task = self.flow.kuyruga_al(self.ids[0], 'deep', 'user')
        self.assertTrue(self.flow.adim())
        saved = self.ai.oku(self.ids[0], 'deep')
        self.assertEqual(saved['status'], 'hazir')
        self.assertEqual(saved['model'], 'nvidia/actual-response-version')
        self.assertEqual(saved['requested_model'], self.ai.model_adi('deep'))
        calls = self.provider.call_count
        self.assertEqual(calls, 2)
        for changed_model in (False, True):
            with self.subTest(changed_model=changed_model):
                with self.depo.baglanti(write=True) as connection:
                    connection.execute("UPDATE workflow_tasks SET status='queued',stage='kuyruk' WHERE id=?", (task['id'],))
                self.now += timedelta(minutes=6)
                if changed_model:
                    self.ai.model_adi = Mock(return_value='nvidia/different-requested-model')
                self.assertTrue(self.flow.adim())
                ready = self.flow.oku(task['id'])
                self.assertEqual(ready['status'], 'ready', ready['message'])
                self.assertFalse(ready['stale'])
                self.assertEqual(ready['model'], saved['model'])
                self.assertEqual(self.provider.call_count, calls + (2 if changed_model else 0))

    def test_sekiz_gercek_secim_bir_kez_alinir_kisisel_notlar_korunur(self):
        for item_id in self.ids[:8]:
            self.depo.arastirma_sec(item_id)
        with self.depo.baglanti(write=True) as connection:
            job = connection.execute('SELECT id FROM jobs LIMIT 1').fetchone()[0]
            connection.execute('INSERT INTO research_notes VALUES(?,1,1,?,?)', (job, 'Korunacak kişisel not', self.now.isoformat()))
        self.flow.eski_secimleri_al()
        self.flow.eski_secimleri_al()
        snapshot = self.flow.gorunum()
        self.assertEqual(len(snapshot['tasks']), 8)
        self.assertTrue(all(task['origin'] == 'user' and task['kind'] == 'deep' for task in snapshot['tasks']))
        with self.depo.baglanti() as connection:
            self.assertEqual(connection.execute('SELECT body FROM research_notes').fetchone()[0], 'Korunacak kişisel not')
            self.assertEqual(connection.execute('SELECT count(*) FROM jobs').fetchone()[0], 8)
        self.provider.assert_not_called()

    def test_derin_arastirma_kendisi_okur_raporlar_not_istemez_devir_karar_bekler(self):
        task = self.flow.kuyruga_al(self.ids[0])
        self.assertTrue(self.flow.adim())
        ready = self.flow.oku(task['id'])
        self.assertEqual(ready['status'], 'ready', ready['message'])
        self.assertFalse(ready['coverage']['limited'])
        self.assertEqual(ready['coverage']['read_count'], 1)
        self.assertTrue(ready['result']['compatibility_tr'])
        self.assertEqual(len(uretim_cagrilari(self.provider)), 1)
        self.assertEqual(ready['evidence_quality']['status'], 'verified')
        self.assertEqual(len(self.flow.gorunum()['notifications']), 1)
        with self.assertRaises(SurumCatismasi):
            self.flow.dosya(task['id'], handoff=True)
        approved = self.flow.karar(task['id'], 'develop', expected_version=self.flow.oku(task['id'])['version'])
        self.assertEqual(approved['decision'], 'develop')
        output = self.flow.dosya(task['id'], handoff=True)
        self.assertIn('DevTrellis — Geliştirme devri', output)
        self.assertIn('Model: ' + ready['model'], output)
        self.assertIn('yalıtılmış', output.lower())
        self.assertIn('https://example.org', output)
        self.assertEqual(len(uretim_cagrilari(self.provider)), 1)
        with self.depo.baglanti() as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM research_notes').fetchone()[0], 0)

    def test_kisa_kaynak_aciklamasi_derin_arastirmanin_makaleyi_okumasini_engellemez(self):
        raw = haber(99, title='Short update', excerpt='Details.')
        self.assertLess(len(raw['title'] + '\n' + raw['excerpt']), 80)
        self.depo.kaydet('one', [raw])
        item_id = next(item['id'] for item in self.depo.gorunum()['items'] if item['url'] == raw['url'])
        self.fetcher.assert_not_called()
        task = self.flow.kuyruga_al(item_id, 'deep', 'user')
        self.assertTrue(self.flow.adim())
        ready = self.flow.oku(task['id'])
        self.assertEqual(ready['status'], 'ready', ready['message'])
        self.assertFalse(ready['stale'])
        self.assertFalse(ready['coverage']['limited'])
        self.assertEqual(ready['coverage']['read_count'], 1)
        self.fetcher.assert_called_once()
        self.assertEqual(self.fetcher.call_args.args[0], raw['url'])
        self.assertEqual(len(uretim_cagrilari(self.provider)), 1)
        article = self.reading.oku(item_id)
        self.assertEqual(article['body'], METIN)
        self.assertEqual(ready['sources'][0]['body_hash'], article['body_hash'])
        sent = json.loads(uretim_cagrilari(self.provider)[-1].args[0][1]['content'])
        self.assertIn(METIN, sent['sources'][0]['public_text'])
        self.assertIsNone(ready['decision'])

    def test_yerel_gun_saat_catchup_ve_gunluk_plan_tekrarlanmaz(self):
        self.flow.automatic = True
        self.now = self.now.replace(hour=4)  # Türkiye 07.00
        self.assertFalse(self.flow.gunu_planla()['started'])
        self.now += timedelta(hours=2)
        self.assertTrue(self.flow.gunu_planla()['started'])
        count = len(self.flow.gorunum()['tasks'])
        self.assertEqual(count, 10)
        self.assertFalse(self.flow.gunu_planla(manual=True)['started'])
        self.assertEqual(len(self.flow.gorunum()['tasks']), count)
        self.assertEqual(self.flow.gorunum()['status']['next_run_at'], '2026-10-27T08:00:00+03:00')

    def test_durum_yalniz_etkin_otomatik_takip_icin_sonraki_saati_soyler(self):
        for automatic in (False, True):
            for enabled in (False, True):
                with self.subTest(automatic=automatic, enabled=enabled):
                    self.flow.automatic = automatic
                    current = self.flow.ayarlar()
                    values = {key: current[key] for key in ('enabled', 'daily_hour', 'screen_limit', 'auto_deep_limit', 'auto_threshold')}
                    self.flow.ayarlari_yaz({**values, 'enabled': enabled}, current['version'])
                    snapshot = self.flow.gorunum()
                    status = snapshot['status']
                    self.assertEqual(snapshot['settings']['enabled'], enabled)
                    self.assertEqual(status['automatic'], automatic)
                    self.assertEqual(status['enabled'], automatic and enabled)
                    self.assertEqual(bool(status['next_run_at']), automatic and enabled)
                    if not automatic:
                        self.assertIn('bu oturumda kapalı', status['message'])
                    elif not enabled:
                        self.assertIn('duraklatıldı', status['message'])
                    else:
                        self.assertEqual(status['message'], 'Günlük takip hazır.')
        self.flow.automatic = False
        self.flow.active_id = 'explicit-manual-work'
        self.flow.message = 'Asıl yayın ve ilgili kaynakların metinleri okunuyor.'
        running = self.flow.gorunum()['status']
        self.assertTrue(running['running'])
        self.assertEqual(running['message'], self.flow.message)
        self.assertIsNone(running['next_run_at'])
        self.provider.assert_not_called()
        self.fetcher.assert_not_called()
        self.collection.baslat.assert_not_called()

    def test_kaynak_taramasi_bitmeden_eski_havuz_gunluk_secki_sayilmaz(self):
        self.collection.gorunum.return_value = {'running': False, 'remaining_due': 2}
        self.flow.gunu_planla()
        self.collection.baslat.assert_called_once()
        self.assertEqual(self.flow.gorunum()['tasks'], [])
        self.collection.gorunum.return_value = {'running': False, 'remaining_due': 0}
        self.assertTrue(self.flow.gunu_planla()['started'])

    def test_yuksek_uygunluk_gunluk_iki_derin_isle_sinirlidir_karar_vermez(self):
        self.flow.gunu_planla()
        for _ in range(30):
            self.flow.adim()
        deep = [task for task in self.flow.gorunum()['tasks'] if task['kind'] == 'deep']
        self.assertEqual(len(deep), 2)
        self.assertTrue(all(task['decision'] is None and task['origin'] == 'system' for task in deep))

    def test_kota_dolunca_is_kaybolmaz_butce_yenilenince_surur(self):
        task = self.flow.kuyruga_al(self.ids[0])
        self.ai.daily_limit = 1
        with self.depo.baglanti(write=True) as connection:
            connection.execute("INSERT INTO ai_attempts(id,item_id,kind,model,started_at,status) VALUES('spent',?,'summary','test',?,'hata')",
                               (self.ids[1], self.now.isoformat()))
        self.flow.adim()
        blocked = self.flow.oku(task['id'])
        self.assertEqual(blocked['status'], 'blocked')
        self.assertEqual(blocked['stage'], 'butce')
        self.assertTrue(self.flow.gorunum()['status']['budget_waiting'])
        self.fetcher.assert_not_called()
        self.now += timedelta(days=1)
        self.flow.adim()
        self.assertEqual(self.flow.oku(task['id'])['status'], 'ready')
        self.assertFalse(self.flow.gorunum()['status']['budget_waiting'])

    def test_model_baglantisi_beklerken_kota_doldu_demez(self):
        self.provider.side_effect = RuntimeError('Deneme bağlantısı yanıt vermedi.')
        task = self.flow.kuyruga_al(self.ids[0])
        self.flow.adim()
        self.assertEqual(self.flow.oku(task['id'])['status'], 'blocked')
        self.assertFalse(self.flow.gorunum()['status']['budget_waiting'])

    def test_kaynak_engelinde_sinirli_rapor_gelistirmeye_gecmez(self):
        from radar.toplayici import CollectionError
        self.fetcher.side_effect = CollectionError('robots kuralı okumayı engelledi')
        task = self.flow.kuyruga_al(self.ids[0])
        self.flow.adim()
        value = self.flow.oku(task['id'])
        self.assertEqual(value['status'], 'ready', value['message'])
        self.assertTrue(value['coverage']['limited'])
        self.assertIn('robots', ' '.join(value['scope_notes']))
        with self.assertRaises(ValueError):
            self.flow.karar(task['id'], 'develop', expected_version=self.flow.oku(task['id'])['version'])
        self.assertEqual(self.flow.karar(task['id'], 'watch', expected_version=self.flow.oku(task['id'])['version'])['decision'], 'watch')

    def test_kaynak_degisirse_onay_ve_devir_engellenir_yeni_surum_ayri_kayittir(self):
        task = self.flow.kuyruga_al(self.ids[0])
        self.flow.adim()
        self.flow.karar(task['id'], 'develop', expected_version=self.flow.oku(task['id'])['version'])
        with self.depo.baglanti(write=True) as connection:
            connection.execute('UPDATE items SET revision=revision+1 WHERE id=?', (self.ids[0],))
        self.assertTrue(self.flow.oku(task['id'])['stale'])
        with self.assertRaises(SurumCatismasi):
            self.flow.dosya(task['id'], handoff=True)
        new = self.flow.tekrar(task['id'], expected_version=self.flow.oku(task['id'])['version'])
        self.assertNotEqual(new['id'], task['id'])
        self.assertEqual(new['source_revision'], 2)

    def test_yarim_is_acilista_devam_eder_cift_secim_kopyalanmaz(self):
        task = self.flow.kuyruga_al(self.ids[0])
        self.assertEqual(self.flow.kuyruga_al(self.ids[0])['id'], task['id'])
        with self.depo.baglanti(write=True) as connection:
            connection.execute("UPDATE workflow_tasks SET status='analyzing' WHERE id=?", (task['id'],))
        self.close()
        self.open()
        self.assertEqual(self.flow.oku(task['id'])['status'], 'queued')
        self.assertEqual(len(self.flow.gorunum()['tasks']), 1)

    def test_duraklatma_sistem_isini_bekletir_kullanici_secimi_surur(self):
        system = self.flow.kuyruga_al(self.ids[0], 'screen', 'system')
        current = self.flow.ayarlar()
        values = {key: current[key] for key in ('enabled', 'daily_hour', 'screen_limit', 'auto_deep_limit', 'auto_threshold')}
        self.flow.ayarlari_yaz({**values, 'enabled': False}, current['version'])
        self.assertFalse(self.flow.adim())
        user = self.flow.kuyruga_al(self.ids[1])
        self.flow.adim()
        self.assertEqual(self.flow.oku(user['id'])['status'], 'ready')
        self.assertEqual(self.flow.oku(system['id'])['status'], 'queued')
        with self.assertRaises(SurumCatismasi):
            self.flow.ayarlari_yaz(values, current['version'])

    def test_on_inceleme_turkce_ve_proje_baglamli_olur(self):
        task = self.flow.kuyruga_al(self.ids[0], 'screen', 'user')
        self.flow.adim()
        value = self.flow.oku(task['id'])
        self.assertEqual(value['status'], 'ready', value['message'])
        self.assertEqual(self.fetcher.call_count, 1)
        sent = json.loads(uretim_cagrilari(self.provider)[-1].args[0][1]['content'])
        self.assertEqual(sent['local_requirements']['project_name'], 'Ev otomasyonu')
        self.assertNotIn('research_notes', sent)
        self.assertEqual(value['result']['relevance_score'], 96)


if __name__ == '__main__':
    unittest.main()
