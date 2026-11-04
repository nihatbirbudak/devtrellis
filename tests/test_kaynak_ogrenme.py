"""Kaynak öğrenmesinin insan kanıtını ayırmasını, gerçek maliyet kaydını ve geri alınabilir karar sınırlarını doğrular."""

import json
import sys
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '.deps'))

from radar.arastirma_akisi import tablolari_hazirla
from radar.depo import Depo
from radar.hizmet import Toplama
from radar.kaynak_ogrenme import KaynakOgrenme, KaynakOgrenmeCatismasi
from radar.kaynak_yonetimi import KaynakYonetimi


class KaynakOgrenmeTestleri(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'radar.sqlite3'
        self.now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
        self.source = {'id': 'deneme', 'name': 'Deneme', 'topic': 'ai', 'kind': 'rss',
                       'url': 'https://example.org', 'fetch_url': 'https://example.org/feed',
                       'enabled': True, 'interval_hours': 48, 'benefit': 'Araştırma için.'}
        self.depo = Depo(self.path, [self.source])
        self.learning = KaynakOgrenme(self.depo, clock=lambda: self.now)
        with self.depo.baglanti(write=True) as connection:
            tablolari_hazirla(connection)

    def source_view(self):
        return self.learning.gorunum()['sources'][0]

    def item(self, number=1):
        self.depo.kaydet('deneme', [{'url': f'https://example.org/item/{number}', 'title': f'Haber {number}', 'excerpt': 'Kaynak metni'}])
        with self.depo.baglanti() as connection:
            return connection.execute('SELECT id FROM items WHERE url=?', (f'https://example.org/item/{number}',)).fetchone()[0]

    def task(self, item, *, kind='screen', status='ready', revision=1, decision=None, fit='low'):
        with self.depo.baglanti(write=True) as connection:
            connection.execute('''INSERT INTO workflow_tasks(id,item_id,kind,source_revision,source_url,status,
                stage,message,origin,profile_topic,profile_json,profile_hash,result_json,decision,decision_at,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (uuid.uuid4().hex, item, kind, revision, 'https://example.org/item', status, 'hazir', 'Hazır',
                 'manual', 'ai', '{}', 'test', json.dumps({'fit': fit}), decision,
                 self.now.isoformat() if decision else None, self.now.isoformat(), self.now.isoformat()))

    def feedback(self, item, value):
        with patch('radar.depo.simdi', return_value=self.now.isoformat()):
            self.depo.geri_bildirim(item, value)

    def insufficient(self, *, count=20, negative=5):
        self.now += timedelta(days=31)
        for index in range(count):
            identifier = self.item(index)
            self.task(identifier)
            if index < negative:
                self.feedback(identifier, 'gereksiz')

    def test_eski_koleksiyon_maliyeti_uydurulmaz(self):
        self.item()
        view = self.learning.gorunum()
        metrics = view['sources'][0]['metrics']
        self.assertEqual(metrics['collected'], 1)
        self.assertEqual(metrics['collection_runs'], 0)
        for field in ('duplicates', 'added', 'changed', 'returned', 'duration_seconds', 'cost_amount', 'tokens'):
            self.assertIsNone(metrics[field])
        self.assertIsNone(view['daily']['added'])
        self.assertFalse(view['sources'][0]['recommendation']['eligible'])

    def test_insan_oylari_tekil_son_secimdir_model_puani_ayridir(self):
        identifier = self.item()
        self.feedback(identifier, 'yararli')
        self.feedback(identifier, 'yararli')
        self.task(identifier, fit='high')
        self.task(identifier, kind='interest', fit='high')
        metrics = self.source_view()['metrics']
        self.assertEqual(metrics['human_positive'], 1)
        self.assertEqual(metrics['examined'], 1)
        self.assertEqual(metrics['model_high_fit'], 1)
        self.feedback(identifier, 'gereksiz')
        metrics = self.source_view()['metrics']
        self.assertEqual((metrics['human_positive'], metrics['human_negative']), (0, 1))
        self.feedback(identifier, 'yok')
        self.assertEqual(self.source_view()['metrics']['human_negative'], 0)
        with self.depo.baglanti(write=True) as connection:
            connection.execute('INSERT INTO feedback_events(item_id,value,note,created_at,actor) VALUES(?,?,?,?,?)',
                               (identifier, 'gereksiz', '', self.now.isoformat(), 'model'))
        self.assertEqual(self.source_view()['metrics']['human_negative'], 0)

    def test_gorulmeyen_bekleyen_hata_ve_eski_revizyon_olumsuz_degil(self):
        for index, status in enumerate(('queued', 'blocked', 'failed')):
            self.task(self.item(index), status=status)
        old = self.item(3)
        self.task(old, revision=1)
        with self.depo.baglanti(write=True) as connection:
            connection.execute('UPDATE items SET revision=2 WHERE id=?', (old,))
            connection.execute('INSERT INTO item_read_states VALUES(?,?,?)', (old, 1, self.now.isoformat()))
        metrics = self.source_view()['metrics']
        self.assertEqual(metrics['human_read'], 1)
        self.assertEqual(metrics['human_negative'], 0)
        self.assertEqual(metrics['examined'], 0)

    def test_gelistirme_yeni_revizyonda_kaybolmaz(self):
        identifier = self.item()
        self.task(identifier, kind='deep', decision='develop')
        with self.depo.baglanti(write=True) as connection:
            connection.execute('UPDATE items SET revision=2 WHERE id=?', (identifier,))
        metrics = self.source_view()['metrics']
        self.assertEqual(metrics['developed'], 1)
        self.assertEqual(metrics['examined'], 0)

    def test_olcum_oncesi_oylar_tek_basina_yavaslatma_kaniti_degil(self):
        self.insufficient()
        with self.depo.baglanti(write=True) as connection:
            connection.execute("UPDATE feedback_events SET created_at='2026-09-01T10:00:00+00:00'")
        metrics = self.source_view()['metrics']
        self.assertEqual(metrics['human_negative'], 5)
        self.assertEqual(metrics['observed_human_negative'], 0)
        self.assertEqual(self.learning.degerlendir()['changes'], [])

    def test_az_yayinlayan_ve_yeni_kaynak_korunur(self):
        self.insufficient(count=5)
        self.assertFalse(self.source_view()['recommendation']['eligible'])
        self.assertEqual(self.learning.degerlendir()['changes'], [])
        self.now -= timedelta(days=30)
        for index in range(5, 20):
            self.task(self.item(index))
        self.assertFalse(self.source_view()['recommendation']['eligible'])

    def test_yeterli_kanit_yedi_gunde_en_fazla_iki_kat_ve_sinirli_degistirir(self):
        self.insufficient()
        view = self.learning.degerlendir()
        self.assertEqual(view['sources'][0]['effective_interval_hours'], 96)
        self.assertEqual(len(view['changes']), 1)
        self.assertEqual(self.depo.kaynaklar()[0]['interval_hours'], 96)
        self.learning.degerlendir()
        self.now += timedelta(days=6, hours=23)
        self.assertEqual(len(self.learning.degerlendir()['changes']), 1)
        self.now += timedelta(hours=1)
        view = self.learning.degerlendir()
        self.assertEqual(len(view['changes']), 2)
        self.assertEqual(view['sources'][0]['effective_interval_hours'], 168)
        self.now += timedelta(days=7)
        self.assertEqual(len(self.learning.degerlendir()['changes']), 2)

    def test_olumlu_insan_ve_gelistirme_kaniti_hiz_kesmez(self):
        self.insufficient()
        identifier = self.item(50)
        self.task(identifier, kind='deep', decision='develop')
        self.assertEqual(self.learning.degerlendir()['changes'], [])

    def recovery_evidence(self, *, developed=False):
        self.now += timedelta(days=1)
        for index in range(100, 105):
            identifier = self.item(index)
            self.task(identifier, kind='deep' if developed and index == 100 else 'screen',
                      decision='develop' if developed and index == 100 else None, fit='high')
            if not developed and index < 103:
                self.feedback(identifier, 'yararli')

    def test_yavas_kaynak_yeni_yararla_eski_sikliga_doner_ve_eski_oylarla_salinmaz(self):
        self.insufficient(count=60, negative=60)
        self.learning.degerlendir()
        self.recovery_evidence()
        self.assertFalse(self.source_view()['recommendation']['eligible'])
        self.assertEqual(len(self.learning.degerlendir()['changes']), 1)
        self.now += timedelta(days=6)
        with patch('radar.depo.simdi', return_value=self.now.isoformat()):
            self.depo.tarama_sonucu('deneme')
        self.assertEqual(self.depo.kaynaklar()[0]['next_due_at'], (self.now + timedelta(hours=96)).isoformat())
        view = self.learning.degerlendir()
        source = view['sources'][0]
        self.assertEqual(source['lifecycle'], 'regular')
        self.assertEqual(source['effective_interval_hours'], 48)
        self.assertEqual(self.depo.kaynaklar()[0]['next_due_at'], (self.now + timedelta(hours=48)).isoformat())
        self.assertEqual(source['metrics']['human_negative'], 60)
        self.assertEqual(source['metrics']['observed_human_negative'], 0)
        self.assertEqual(len(view['changes']), 2)
        self.assertEqual(view['changes'][0]['before_interval'], 96)
        self.assertEqual(view['changes'][0]['after_interval'], 48)
        self.now += timedelta(days=7)
        self.assertEqual(len(self.learning.degerlendir()['changes']), 2)

    def test_eski_olumlu_oy_ve_yeni_model_puani_hiz_geri_kazandirmaya_yetmez(self):
        self.insufficient(count=60, negative=60)
        for index in range(60, 63):
            self.feedback(self.item(index), 'yararli')
        self.learning.degerlendir()
        self.now += timedelta(days=7)
        for index in range(100, 105):
            self.task(self.item(index), fit='high')
        source = self.source_view()
        self.assertEqual(source['metrics']['post_change_examined'], 5)
        self.assertEqual(source['metrics']['post_change_human_positive'], 0)
        self.assertNotEqual(source['recommendation']['action'], 'promote')
        self.assertEqual(self.learning.degerlendir()['sources'][0]['lifecycle'], 'slow')

    def test_yeni_gelistirme_kaniti_geri_dondurur_ama_geri_alma_beklemesi_korunur(self):
        self.insufficient()
        self.learning.degerlendir()
        self.recovery_evidence(developed=True)
        self.now += timedelta(days=6)
        view = self.learning.degerlendir()
        self.assertEqual(view['sources'][0]['lifecycle'], 'regular')
        restored = view['changes'][0]
        self.learning.geri_al(restored['id'], restored['version'])
        self.assertEqual(self.source_view()['lifecycle'], 'slow')
        self.assertEqual(self.source_view()['effective_interval_hours'], 96)
        self.now += timedelta(days=7)
        self.assertIn('30 günlük', self.source_view()['recommendation']['reason'])
        self.assertEqual(len(self.learning.degerlendir()['changes']), 2)

    def test_yarar_geri_gelse_de_koruma_duraklatma_ve_elle_siklik_onceliklidir(self):
        self.insufficient()
        self.learning.degerlendir()
        self.recovery_evidence()
        self.now += timedelta(days=6)
        self.assertEqual(self.source_view()['recommendation']['action'], 'promote')
        self.learning.ayarlari_kaydet(False, 0)
        self.assertFalse(self.source_view()['recommendation']['eligible'])
        self.learning.ayarlari_kaydet(True, 1)
        protected = self.learning.koru('deneme', True, self.source_view()['version'])
        self.assertFalse(self.source_view()['recommendation']['eligible'])
        self.learning.koru('deneme', False, protected['version'])
        management = KaynakYonetimi(self.depo)
        management.kaydet('deneme', enabled=True, interval_hours=72, expected_version=0)
        self.assertFalse(self.source_view()['recommendation']['eligible'])
        self.assertEqual(self.source_view()['effective_interval_hours'], 72)
        management.kaydet('deneme', enabled=False, interval_hours=72, expected_version=1)
        self.assertIn('kapalı', self.source_view()['recommendation']['reason'])
        self.assertEqual(len(self.learning.degerlendir()['changes']), 1)

    def test_elle_siklik_kapali_ve_temel_kaynak_otomatik_ezilmez(self):
        self.insufficient()
        management = KaynakYonetimi(self.depo)
        management.kaydet('deneme', enabled=False, interval_hours=48, expected_version=0)
        self.assertEqual(self.learning.degerlendir()['changes'], [])
        management.kaydet('deneme', enabled=True, interval_hours=240, expected_version=1)
        self.now += timedelta(days=7)
        self.assertEqual(self.learning.degerlendir()['sources'][0]['effective_interval_hours'], 240)
        self.assertEqual(self.learning.gorunum()['changes'], [])
        Depo(self.path, [{**self.source, 'id': 'home_assistant', 'name': 'Home Assistant'}])
        self.learning.degerlendir()
        source = next(source for source in self.learning.gorunum()['sources'] if source['source_id'] == 'home_assistant')
        self.assertTrue(source['protected'])
        self.assertFalse(source['recommendation']['eligible'])

    def test_koruma_ve_kapatma_araligi_aninda_geri_getirir(self):
        self.insufficient()
        view = self.learning.degerlendir()
        protected = self.learning.koru('deneme', True, view['sources'][0]['version'])
        self.assertEqual(protected['effective_interval_hours'], 48)
        self.assertTrue(protected['protected'])
        self.learning.koru('deneme', False, protected['version'])
        self.assertEqual(self.depo.kaynaklar()[0]['interval_hours'], 96)
        changed = self.learning.ayarlari_kaydet(False, expected_version=0)
        self.assertEqual(changed['sources'][0]['effective_interval_hours'], 48)
        self.assertFalse(self.depo.kaynaklar()[0]['learning_enabled'])
        with self.assertRaises(KaynakOgrenmeCatismasi):
            self.learning.ayarlari_kaydet(True, expected_version=0)

    def test_koruma_ve_duraklatma_vade_hata_beklemesi_ve_tekrar_hakkini_korur(self):
        self.insufficient()
        view = self.learning.degerlendir()
        with patch('radar.depo.simdi', return_value=self.now.isoformat()):
            self.depo.tarama_sonucu('deneme')
        protected = self.learning.koru('deneme', True, view['sources'][0]['version'])
        self.assertEqual(self.depo.kaynaklar()[0]['next_due_at'], (self.now + timedelta(hours=48)).isoformat())
        self.learning.koru('deneme', False, protected['version'])
        self.learning.ayarlari_kaydet(False, 0)
        self.assertEqual(self.depo.kaynaklar()[0]['next_due_at'], (self.now + timedelta(hours=48)).isoformat())
        with self.depo.baglanti(write=True) as connection:
            connection.execute("UPDATE sources SET failures=7,last_error='Kaynak hatası'")
        self.assertEqual(self.depo.kaynaklar()[0]['next_due_at'], (self.now + timedelta(hours=128)).isoformat())
        with self.depo.baglanti(write=True) as connection:
            connection.execute('INSERT INTO source_preferences(source_id,retry_pending,retry_requested_at,last_retry_at,updated_at) VALUES(?,?,?,?,?)',
                               ('deneme', 1, (self.now + timedelta(minutes=6)).isoformat(),
                                (self.now + timedelta(minutes=5)).isoformat(), self.now.isoformat()))
        self.assertEqual(self.depo.kaynaklar()[0]['next_due_at'], (self.now + timedelta(minutes=10)).isoformat())

    def test_katalog_araligi_degisince_eski_politika_yeni_araliga_uygulanmaz(self):
        self.insufficient()
        self.learning.degerlendir()
        with patch('radar.depo.simdi', return_value=self.now.isoformat()):
            self.depo.tarama_sonucu('deneme')
        Depo(self.path, [{**self.source, 'interval_hours': 24}])
        self.assertEqual(self.depo.kaynaklar()[0]['interval_hours'], 24)
        self.assertEqual(self.depo.kaynaklar()[0]['next_due_at'], (self.now + timedelta(hours=24)).isoformat())

    def test_geri_alma_vadeyi_duzeltir_ve_30_gun_tekrari_engeller(self):
        self.insufficient()
        view = self.learning.degerlendir()
        with patch('radar.depo.simdi', return_value=self.now.isoformat()):
            self.depo.tarama_sonucu('deneme')
        self.assertEqual(self.depo.kaynaklar()[0]['next_due_at'], (self.now + timedelta(hours=96)).isoformat())
        change = view['changes'][0]
        undone = self.learning.geri_al(change['id'], change['version'])
        self.assertEqual(undone['sources'][0]['effective_interval_hours'], 48)
        self.assertEqual(self.depo.kaynaklar()[0]['next_due_at'], (self.now + timedelta(hours=48)).isoformat())
        self.now += timedelta(days=7)
        self.assertEqual(len(self.learning.degerlendir()['changes']), 1)
        with self.assertRaises(KaynakOgrenmeCatismasi):
            self.learning.geri_al(change['id'], change['version'])

    def test_eski_karar_yeni_koruma_kararini_ezemez(self):
        self.insufficient()
        view = self.learning.degerlendir()
        self.learning.koru('deneme', True, view['sources'][0]['version'])
        self.assertFalse(self.learning.gorunum()['changes'][0]['can_revert'])
        with self.assertRaises(KaynakOgrenmeCatismasi):
            self.learning.geri_al(view['changes'][0]['id'], 0)

    def test_iki_isci_ayni_hafta_ayni_karari_uretmez(self):
        self.insufficient()
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: self.learning.degerlendir(), range(2)))
        self.assertEqual(len(self.learning.gorunum()['changes']), 1)

    def test_yeni_elle_ayar_ve_katalog_degisimi_eski_otomatik_araliga_ustundur(self):
        self.insufficient()
        change = self.learning.degerlendir()['changes'][0]
        KaynakYonetimi(self.depo).kaydet('deneme', enabled=True, interval_hours=72, expected_version=0)
        self.assertEqual(self.depo.kaynaklar()[0]['interval_hours'], 72)
        self.learning.geri_al(change['id'], 0)
        self.assertEqual(self.depo.kaynaklar()[0]['interval_hours'], 72)

    def test_deneme_idempotent_ve_model_tek_basina_terfi_ettirmez(self):
        initial = self.learning.kaynak_denemeye_al('deneme', {'discovered_from': 'https://example.org'})
        self.assertEqual(initial['lifecycle'], 'trial')
        self.now += timedelta(days=14)
        self.assertEqual(self.learning.kaynak_denemeye_al('deneme')['version'], initial['version'])
        for index in range(5):
            self.task(self.item(index), fit='high')
        self.assertFalse(self.source_view()['recommendation']['eligible'])
        for index in range(2):
            self.feedback(self.item(index), 'yararli')
        self.assertTrue(self.source_view()['recommendation']['eligible'])
        promoted = self.learning.degerlendir()
        self.assertEqual(promoted['sources'][0]['lifecycle'], 'regular')
        self.assertEqual(promoted['changes'][0]['after_lifecycle'], 'regular')
        self.assertEqual(self.learning.kaynak_denemeye_al('deneme')['lifecycle'], 'regular')
        self.learning.geri_al(promoted['changes'][0]['id'], 0)
        self.assertEqual(self.source_view()['lifecycle'], 'trial')

    def test_acik_olumsuz_deneme_seyreklestirilir_ve_geri_alinabilir(self):
        self.learning.kaynak_denemeye_al('deneme')
        self.insufficient()
        view = self.learning.degerlendir()
        self.assertEqual(view['sources'][0]['lifecycle'], 'slow')
        self.assertEqual(view['summary']['trial'], 0)
        self.assertEqual(view['changes'][0]['before_lifecycle'], 'trial')
        self.learning.geri_al(view['changes'][0]['id'], 0)
        self.assertEqual(self.source_view()['lifecycle'], 'trial')

    def test_olumlu_deneme_elle_siklik_ve_korumayi_degistirmeden_terfi_eder(self):
        trial = self.learning.kaynak_denemeye_al('deneme')
        self.learning.koru('deneme', True, trial['version'])
        KaynakYonetimi(self.depo).kaydet('deneme', enabled=True, interval_hours=120, expected_version=0)
        self.now += timedelta(days=14)
        for index in range(5):
            identifier = self.item(index)
            self.task(identifier)
            if index < 2:
                self.feedback(identifier, 'yararli')
        view = self.learning.degerlendir()
        self.assertEqual(view['sources'][0]['lifecycle'], 'regular')
        self.assertTrue(view['sources'][0]['protected'])
        self.assertEqual(view['sources'][0]['effective_interval_hours'], 120)
        self.assertEqual(view['changes'][0]['before_interval'], 120)
        self.assertEqual(view['changes'][0]['after_interval'], 120)

    def test_deneme_karari_gecikmesi_gorunur_ama_okunmayan_olumsuz_sayilmaz(self):
        self.learning.kaynak_denemeye_al('deneme')
        self.now += timedelta(days=31)
        view = self.learning.degerlendir()
        self.assertTrue(view['sources'][0]['trial_review_due'])
        self.assertEqual(view['summary']['trial_waiting_review'], 1)
        self.assertEqual(view['sources'][0]['lifecycle'], 'trial')
        self.assertEqual(view['sources'][0]['effective_interval_hours'], 48)
        self.assertIn('kararı bekleniyor', view['sources'][0]['recommendation']['reason'])

    def test_eski_tepkiler_90_gun_sonra_yeni_odagi_bastirmaz(self):
        self.insufficient()
        self.now += timedelta(days=91)
        view = self.learning.degerlendir()
        self.assertEqual(view['sources'][0]['metrics']['human_negative'], 5)
        self.assertEqual(view['sources'][0]['metrics']['observed_human_negative'], 0)
        self.assertEqual(view['changes'], [])

    def test_atomik_karar_hatasinda_yarim_siklik_kalmaz(self):
        self.insufficient()
        original = self.learning._gorunum
        calls = 0
        def broken(connection):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError('Deneme hatası')
            return original(connection)
        with patch.object(self.learning, '_gorunum', side_effect=broken), self.assertRaises(RuntimeError):
            self.learning.degerlendir()
        self.assertEqual(self.learning.gorunum()['changes'], [])
        self.assertEqual(self.depo.kaynaklar()[0]['interval_hours'], 48)

    def test_toplama_kancasi_tekrar_ve_kismi_hata_sayisini_gercek_turdan_alir(self):
        collector = Mock(return_value={'items': [{'url': 'https://example.org/one', 'title': 'Bir haber'}], 'pages': 1})
        service = Toplama(self.depo, collector)
        self.addCleanup(service.kapan)
        with patch.object(service.stop, 'wait', return_value=False):
            service.calistir()
            with self.depo.baglanti(write=True) as connection:
                connection.execute('UPDATE sources SET next_due_at=NULL')
            collector.return_value['error'] = 'Devam sayfası okunamadı.'
            service.calistir()
        metrics = self.source_view()['metrics']
        self.assertEqual(metrics['collection_runs'], 2)
        self.assertEqual(metrics['added'], 1)
        self.assertEqual(metrics['duplicates'], 1)
        self.assertEqual(metrics['errors'], 1)
        self.assertEqual(metrics['returned'], 2)
        self.assertGreaterEqual(metrics['duration_seconds'], 0)
        self.assertIsNone(metrics['cost_amount'])

    def test_hata_ve_degisiklik_yok_olcumleri_ayrilir(self):
        self.learning.tarama_kaydet('deneme', started_at=self.now.isoformat(), finished_at=self.now.isoformat(), status='error')
        metrics = self.source_view()['metrics']
        self.assertIsNone(metrics['duplicates'])
        self.assertIsNone(metrics['returned'])
        self.assertEqual(metrics['errors'], 1)
        self.learning.tarama_kaydet('deneme', started_at=self.now.isoformat(), finished_at=self.now.isoformat(),
                                   status='not_modified', returned=0, added=0, changed=0, duplicates=0)
        self.assertEqual(self.source_view()['metrics']['duplicates'], 0)

    def test_gunluk_sayim_turkiye_gunune_gore_ve_snapshot_tekildir(self):
        self.now = datetime(2026, 10, 1, 22, tzinfo=timezone.utc)
        self.learning.tarama_kaydet('deneme', started_at=self.now.isoformat(), finished_at=self.now.isoformat(),
                                   status='ok', returned=1, added=1, changed=0, duplicates=0)
        view = self.learning.degerlendir()
        self.assertEqual(view['daily']['period_start'], '2026-10-01T21:00:00+00:00')
        self.assertEqual(view['daily']['added'], 1)
        self.learning.degerlendir()
        with self.depo.baglanti() as connection:
            self.assertEqual(connection.execute('SELECT day FROM source_learning_snapshots').fetchall()[0][0], '2026-10-02')
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM source_learning_snapshots').fetchone()[0], 1)

    def test_gecersiz_girdiler_veriyi_degistirmez(self):
        for value in (True, -1, '0'):
            with self.assertRaises(ValueError):
                self.learning.koru('deneme', True, value)
        with self.assertRaises(ValueError):
            self.learning.ayarlari_kaydet(1, 0)
        with self.assertRaises(KeyError):
            self.learning.koru('olmayan', True, 0)
        with self.assertRaises(ValueError):
            self.learning.kaynak_denemeye_al('deneme', 'gecersiz')
        self.assertEqual(self.source_view()['version'], 0)


if __name__ == '__main__':
    unittest.main()
