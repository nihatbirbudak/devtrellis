"""Araştırmada eski pencere kararlarını, kesinti sonrası hazır sonucu ve açık günlük isteği doğrular."""

import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '.deps'))
from datetime import timedelta

from radar.calisma_masasi import SurumCatismasi
from radar.toplayici import CollectionError
from tests import test_arastirma_akisi as akis_duzenegi


class AkisIncelemeTestleri(unittest.TestCase):
    # Aynı gerçek SQLite ve sahte ağ/model düzeneğini kullanır; asıl uygulamaya erişmez.
    setUp = akis_duzenegi.ArastirmaAkisiTestleri.setUp
    open = akis_duzenegi.ArastirmaAkisiTestleri.open
    close = akis_duzenegi.ArastirmaAkisiTestleri.close

    def test_eski_pencere_karari_ve_tekrari_son_insan_kararini_ezemez(self):
        task = self.flow.kuyruga_al(self.ids[0])
        self.assertTrue(self.flow.adim())
        before = self.flow.oku(task['id'])
        self.assertEqual(before['status'], 'ready', before['message'])
        latest = self.flow.karar(task['id'], 'watch', expected_version=before['version'])
        self.assertGreater(latest['version'], before['version'])
        with self.assertRaises(SurumCatismasi):
            self.flow.karar(task['id'], 'develop', expected_version=before['version'])
        with self.assertRaises(SurumCatismasi):
            self.flow.tekrar(task['id'], expected_version=before['version'])
        self.assertEqual(self.flow.oku(task['id'])['decision'], 'watch')
        self.assertEqual(self.flow.oku(task['id'])['version'], latest['version'])

    def test_butce_doluyken_kesinti_oncesi_hazir_sonuc_model_cagrilmadan_toparlanir(self):
        task = self.flow.kuyruga_al(self.ids[0], 'interest', 'user')
        self.ai.baslat(task['item_id'], kind='interest', profile=task['profile_snapshot'])
        self.ai.worker.join(timeout=5)
        self.assertFalse(self.ai.worker.is_alive())
        self.assertEqual(self.ai.oku(task['item_id'], 'interest')['status'], 'hazir')
        # Model sonucu tamamlandı; akış kendi satırına yazamadan süreç kapanmış olsun.
        with self.depo.baglanti(write=True) as connection:
            connection.execute("UPDATE workflow_tasks SET status='analyzing' WHERE id=?", (task['id'],))
        self.close()
        self.open()
        self.ai.daily_limit = 1
        self.assertEqual(self.ai.durum()['daily_remaining'], 0)
        self.provider.reset_mock()
        self.assertTrue(self.flow.adim())
        completed = self.flow.oku(task['id'])
        self.assertEqual(completed['status'], 'ready', completed['message'])
        self.assertFalse(completed['stale'])
        self.assertTrue(completed['result'])
        self.provider.assert_not_called()
        self.fetcher.assert_not_called()

    def test_duraklatilmis_takipte_acik_gunluk_istek_bir_kez_calistirilir(self):
        current = self.flow.ayarlar()
        settings = {key: current[key] for key in ('enabled', 'daily_hour', 'screen_limit', 'auto_deep_limit', 'auto_threshold')}
        self.flow.ayarlari_yaz({**settings, 'enabled': False}, current['version'])
        self.assertFalse(self.flow.gunu_planla()['started'])
        self.assertTrue(self.flow.gunu_planla(manual=True)['started'])
        self.assertTrue(self.flow.adim(), 'Açık kullanıcı isteği duraklatılmış otomatik takipten bağımsız yürümeli.')
        tasks = self.flow.gorunum()['tasks']
        completed = [task for task in tasks if task['status'] == 'ready']
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]['kind'], 'interest')
        self.assertFalse(self.flow.ayarlar()['enabled'])
        self.assertFalse(self.flow.gunu_planla(manual=True)['started'])
        self.assertFalse(any(task['kind'] == 'deep' for task in tasks))

    def test_acik_yeniden_inceleme_kesintiden_sonra_eski_raporu_yeni_saymaz(self):
        task = self.flow.kuyruga_al(self.ids[0])
        self.flow.adim()
        ready = self.flow.oku(task['id'])
        self.assertEqual(ready['status'], 'ready')
        self.now += timedelta(minutes=6)
        self.flow.tekrar(task['id'], expected_version=ready['version'])
        self.close()
        self.open()
        self.provider.reset_mock()
        self.flow.adim()
        current = self.flow.oku(task['id'])
        self.assertEqual(current['status'], 'ready', current['message'])
        self.assertEqual(len(akis_duzenegi.uretim_cagrilari(self.provider)), 1)

    def test_yeni_okunan_makale_eski_aciklama_raporunu_tam_arastirma_gibi_gostermez(self):
        successful_fetch = self.fetcher.side_effect
        self.fetcher.side_effect = CollectionError('Kaynak şu anda metin vermiyor.')
        task = self.flow.kuyruga_al(self.ids[0])
        self.flow.adim()
        limited = self.flow.oku(task['id'])
        self.assertEqual(limited['status'], 'ready', limited['message'])
        self.assertTrue(limited['coverage']['limited'])
        self.assertIsNone(limited['sources'][0]['body_hash'])
        # Kullanıcı makaleyi sonradan başarıyla okuyabilmiş olsun.
        self.now += timedelta(minutes=6)
        self.fetcher.side_effect = successful_fetch
        self.reading.baslat(task['item_id'])
        self.reading.worker.join(timeout=5)
        self.assertFalse(self.reading.worker.is_alive())
        article = self.reading.oku(task['item_id'])
        self.assertEqual(article['status'], 'hazir')
        self.assertTrue(self.flow.oku(task['id'])['stale'], 'Yeni kaynak metni eski raporu ekranda da eski olarak işaretlemeli.')
        self.provider.reset_mock()
        self.flow.tekrar(task['id'], expected_version=limited['version'])
        self.flow.adim()
        completed = self.flow.oku(task['id'])
        self.assertEqual(completed['status'], 'ready', completed['message'])
        self.assertFalse(completed['coverage']['limited'])
        self.assertEqual(completed['sources'][0]['body_hash'], article['body_hash'],
                         'Tam araştırma etiketi için rapor yeni okunan makale gövdesine dayanmalı.')
        self.assertEqual(len(akis_duzenegi.uretim_cagrilari(self.provider)), 1, 'Önceki açıklama raporu yeni makale okunmadan tam rapora dönüşmemeli.')


if __name__ == '__main__':
    unittest.main()
