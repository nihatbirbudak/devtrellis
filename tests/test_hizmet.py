"""Tarama durumunun kalıcılığını ve süreçler arasındaki tek işçi sınırını doğrular."""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".deps"))

from radar.depo import Depo
from radar.hizmet import Toplama


def kaynak():
    return {
        "id": "deneme", "name": "Deneme kaynağı", "topic": "ai",
        "url": "https://example.org/", "fetch_url": "https://example.org/feed.xml",
        "kind": "rss", "interval_hours": 12, "benefit": "Yerel araştırmaya katkı.",
        "enabled": True,
    }


class ToplamaKabulTestleri(unittest.TestCase):
    def setUp(self):
        self.gecici = tempfile.TemporaryDirectory()
        self.addCleanup(self.gecici.cleanup)
        self.dosya = Path(self.gecici.name) / "radar.sqlite3"
        self.depo = Depo(self.dosya, [kaynak()])
        self.devam = threading.Event()
        self.addCleanup(self.devam.set)

    def hizmet(self, collector=None):
        collector = collector or Mock(return_value={"items": [], "not_modified": True})
        hizmet = Toplama(Depo(self.dosya, [kaynak()]), collector)
        self.addCleanup(hizmet.kapan)
        return hizmet

    def test_hic_taranmamis_depo_yanlis_son_kontrol_gostermez(self):
        state = self.hizmet().gorunum()
        self.assertFalse(state["running"])
        self.assertIsNone(state["last_finished_at"])
        self.assertIsNone(state["last_checked_at"])
        self.assertIsNone(state["last_success_at"])
        self.assertIn("Henüz", state["message"])

    def test_buyuk_katalogda_konular_sirayla_taranir_ve_kaynak_atlanmaz(self):
        topics = ['ai', 'jarvis', 'robotik', 'tarim', 'medya']
        sources = [{**kaynak(), 'id': f'{topic}_{i}', 'topic': topic} for topic in topics for i in range(5)]
        depot = Depo(Path(self.gecici.name) / 'buyuk.sqlite3', sources)
        calls = []
        def collect(source, **kwargs):
            calls.append((source['id'], source['topic']))
            return {'items': [], 'not_modified': False}
        service = Toplama(depot, collect)
        self.addCleanup(service.kapan)
        with patch.object(service.stop, 'wait', return_value=False):
            self.assertTrue(service.calistir())
            self.assertEqual(len(calls), 12)
            self.assertEqual({topic for _, topic in calls[:5]}, set(topics))
            self.assertEqual(service.gorunum()['remaining_due'], 13)
            self.assertTrue(service.calistir())
            self.assertEqual(len(calls), 24)
            self.assertTrue(service.calistir())
        self.assertEqual(len(calls), 25)
        self.assertEqual(len({identifier for identifier, _ in calls}), 25)
        self.assertEqual(service.gorunum()['remaining_due'], 0)

    def test_gecerli_bos_besleme_kaynak_hatasi_sayilmaz(self):
        hizmet = self.hizmet(Mock(return_value={"items": [], "not_modified": False}))
        self.assertTrue(hizmet.calistir())
        source = self.depo.kaynaklar()[0]
        self.assertIsNone(source["last_error"])
        self.assertIsNotNone(source["last_success_at"])
        self.assertEqual(self.depo.gorunum()["stats"]["total"], 0)

    def test_sayfalama_kayitlari_ilk_onda_kesilmez_ve_devam_yeniden_acilista_korunur(self):
        items = [{'url': f'https://example.org/article/{i}', 'title': f'Public research {i}', 'excerpt': 'Public abstract'} for i in range(35)]
        result = {'items': items, 'pages': 3, 'next_url': 'https://example.org/feed.xml?page=4', 'truncated': True}
        service = Toplama(self.depo)
        self.addCleanup(service.kapan)
        with patch('radar.toplayici.collect_source', return_value=result):
            self.assertTrue(service.calistir())
        self.assertEqual(self.depo.gorunum()['stats']['total'], 35)
        with self.depo.baglanti(write=True) as connection:
            connection.execute('UPDATE sources SET next_due_at=NULL')
        reopened = Toplama(self.depo)
        self.addCleanup(reopened.kapan)
        with patch('radar.toplayici.collect_source', return_value={'items': [], 'not_modified': True, 'pages': 2, 'next_url': None}) as collect:
            self.assertTrue(reopened.calistir())
        self.assertEqual(collect.call_args.kwargs['continuation'], result['next_url'])
        self.assertEqual(reopened.gorunum()['pagination'][0]['pages_completed'], 5)
        self.assertIsNone(reopened.gorunum()['pagination'][0]['next_url'])

    def test_kismi_sayfalama_hatasi_alinan_kartlari_korur_ama_tam_basari_sayilmaz(self):
        service = self.hizmet(Mock(return_value={'items': [{'url':'https://example.org/article/1', 'title':'Public research title'}],
                                                'pages': 1, 'next_url':'https://example.org/feed.xml?page=2',
                                                'error':'Devam sayfası okunamadı.', 'truncated':True}))
        self.assertTrue(service.calistir())
        self.assertEqual(self.depo.gorunum()['stats']['total'], 1)
        self.assertEqual(self.depo.kaynaklar()[0]['last_error'], 'Devam sayfası okunamadı.')
        self.assertEqual(service.gorunum()['pagination'][0]['next_url'], 'https://example.org/feed.xml?page=2')

    def test_akademik_kesif_gunluk_besleme_turunda_calistirilmaz(self):
        from radar.kesif import Kesif
        discovery = Kesif(self.depo)
        self.addCleanup(discovery.kapan)
        collector = Mock(return_value={'items': [], 'not_modified':True})
        service = self.hizmet(collector)
        self.assertTrue(service.calistir())
        self.assertEqual(collector.call_count, 1)
        self.assertEqual(collector.call_args.args[0]['id'], 'deneme')

    def test_bakim_erisim_hatasi_son_arsiv_bilgisini_silmez_ve_tarama_sirasinda_gorunur(self):
        collector = Mock(return_value={'items':[], 'not_modified':True,
                                      'maintenance':{'status':'ok','error':None,'checked_at':'2026-10-26T12:00:00Z','archived':True,'disabled':False,'repository_id':123}})
        service = self.hizmet(collector)
        service.calistir()
        with self.depo.baglanti(write=True) as connection:
            connection.execute('UPDATE sources SET next_due_at=NULL')
        collector.return_value = {'items':[], 'not_modified':True, 'maintenance':{'status':'error','error':'HTTP 429','checked_at':'2026-10-27T12:00:00Z'}}
        service.calistir()
        service.state['running'] = True
        state = service.gorunum()
        service.state['running'] = False
        self.assertEqual(state['maintenance'][0]['archived'],1)
        self.assertEqual(state['maintenance'][0]['status'],'error')
        self.assertIn('pagination',state)

    def test_baska_nesnenin_kalici_sonuclari_yeniden_baslatmadan_gorunur(self):
        hizmet = self.hizmet()
        with patch("radar.depo.simdi", return_value="2026-10-26T12:00:00+00:00"):
            self.depo.tarama_sonucu("deneme")
        self.assertEqual(hizmet.gorunum()["last_finished_at"], "2026-10-26T12:00:00+00:00")
        self.assertIn("1 kaynak başarılı", hizmet.gorunum()["message"])
        with patch("radar.depo.simdi", return_value="2026-10-26T14:00:00+00:00"):
            self.depo.tarama_sonucu("deneme", error="Deneme hatası.")
        for servis in (hizmet, self.hizmet()):
            state = servis.gorunum()
            self.assertEqual(state["last_checked_at"], "2026-10-26T14:00:00+00:00")
            self.assertEqual(state["last_success_at"], "2026-10-26T12:00:00+00:00")
            self.assertIn("1 kaynak sorunlu", state["message"])
            self.assertNotIn("Henüz tarama yapılmadı", state["message"])

    def test_ayni_deponun_iki_nesnesi_tek_ag_taramasi_yapar(self):
        basladi = threading.Event()

        def beklet(*args, **kwargs):
            basladi.set()
            self.devam.wait(timeout=5)
            return {"items": [], "not_modified": True}

        ilk = self.hizmet(Mock(side_effect=beklet))
        diger = self.hizmet()
        self.assertTrue(ilk.baslat())
        self.assertTrue(basladi.wait(timeout=2))
        self.assertTrue(ilk.gorunum()["running"])
        self.assertIn("kontrol ediliyor", ilk.gorunum()["message"])
        self.assertFalse(diger.baslat())
        self.assertFalse(diger.calistir())
        diger.collector.assert_not_called()
        self.devam.set()
        ilk.thread.join(timeout=3)
        self.assertFalse(ilk.thread.is_alive())
        self.assertTrue(diger.baslat())
        diger.thread.join(timeout=3)
        self.assertFalse(diger.thread.is_alive())
        diger.collector.assert_not_called()  # Başarılı kaynağın yeni kontrol zamanı gelmedi.

    def test_bitis_sonrasi_hata_bile_kilidi_tutmaz(self):
        ilk = self.hizmet(Mock(side_effect=ValueError("Deneme hatası.")))
        self.assertTrue(ilk.calistir())
        self.assertFalse(ilk.gorunum()["running"])
        self.assertIn("1 kaynak sorunlu", ilk.gorunum()["message"])
        diger = self.hizmet()
        self.assertTrue(diger.calistir())

    def test_isci_baslayamazsa_kilit_birakilir(self):
        ilk = self.hizmet()
        with patch("radar.hizmet.threading.Thread.start", side_effect=RuntimeError("Deneme.")):
            with self.assertRaises(RuntimeError):
                ilk.baslat()
        self.assertFalse(ilk.gorunum()["running"])
        self.assertTrue(self.hizmet().calistir())

    def test_kilit_dosyasi_acilamazsa_tarama_atlanarak_calistirilmaz(self):
        hizmet = self.hizmet()
        with patch("radar.hizmet.Path.open", side_effect=PermissionError("Deneme.")):
            with self.assertRaises(PermissionError):
                hizmet.baslat()
        hizmet.collector.assert_not_called()
        self.assertFalse(hizmet.gorunum()["running"])

    def test_zamanli_tarama_gecici_baslatma_hatasindan_sonra_devam_eder(self):
        hizmet = self.hizmet()
        # Önceki başarı, yeni hizmet hatasını görünümden gizlememeli.
        self.depo.tarama_sonucu("deneme")
        waits = []

        def wait_next(seconds):
            waits.append(seconds)
            if len(waits) == 1:
                self.assertIn("başlatılamadı", hizmet.gorunum()["message"])
            else:
                hizmet.stop.set()
            return hizmet.stop.is_set()

        with patch.object(hizmet, "baslat", side_effect=[PermissionError("Deneme."), False]) as start, \
             patch.object(hizmet.stop, "wait", side_effect=wait_next), \
             self.assertLogs("radar", level="ERROR") as logs:
            hizmet.zamanli_calistir()
        self.assertEqual(start.call_count, 2)
        self.assertEqual(waits, [900, 900])
        self.assertIn("yeniden denenecek", logs.output[0])

    def test_genel_tarama_hatasi_onceki_basarinin_altinda_gizlenmez(self):
        hizmet = self.hizmet()
        self.depo.tarama_sonucu("deneme")
        with patch.object(hizmet.depo, "kaynaklar", side_effect=OSError("Deneme.")), \
             self.assertLogs("radar", level="ERROR"):
            self.assertTrue(hizmet.calistir())
        self.assertIn("tamamlanamadı", hizmet.gorunum()["message"])
        self.assertFalse(hizmet.gorunum()["running"])
        self.assertTrue(hizmet.calistir())
        self.assertIsNone(hizmet.gorunum()["error"])
        self.assertIn("1 kaynak başarılı", hizmet.gorunum()["message"])

    def test_kapanmis_hizmet_yeniden_tarama_baslatmaz(self):
        hizmet = self.hizmet()
        hizmet.kapan()
        self.assertFalse(hizmet.baslat())
        self.assertFalse(hizmet.calistir())
        hizmet.collector.assert_not_called()

    def test_kapanis_suren_okumanin_kilidini_erken_birakmaz(self):
        basladi = threading.Event()

        def beklet(*args, **kwargs):
            basladi.set()
            self.devam.wait(timeout=5)
            return {"items": [], "not_modified": True}

        ilk = self.hizmet(Mock(side_effect=beklet))
        diger = self.hizmet()
        self.assertTrue(ilk.baslat())
        self.assertTrue(basladi.wait(timeout=2))
        kapanis = threading.Thread(target=ilk.kapan)
        kapanis.start()
        self.assertTrue(ilk.stop.wait(timeout=2))
        self.assertFalse(diger.baslat())
        self.devam.set()
        kapanis.join(timeout=3)
        self.assertFalse(kapanis.is_alive())
        self.assertFalse(ilk.thread.is_alive())
        self.assertTrue(diger.calistir())

    def test_surec_kilidi_engeller_ve_surec_cokunce_otomatik_birakilir(self):
        kok = Path(__file__).resolve().parents[1]
        hazir = Path(self.gecici.name) / "isci-hazir"
        kod = """
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / '.deps'))
from radar.depo import Depo
from radar.hizmet import Toplama
def beklet(*args, **kwargs):
    Path(sys.argv[3]).write_text('hazir', encoding='utf-8')
    time.sleep(60)
    return {'items': [], 'not_modified': True}
hizmet = Toplama(Depo(sys.argv[1], [json.loads(sys.argv[2])]), beklet)
hizmet.calistir()
"""
        process = subprocess.Popen(
            [sys.executable, "-c", kod, str(self.dosya), json.dumps(kaynak()), str(hazir)],
            cwd=kok, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

        def kapat():
            if process.poll() is None:
                process.terminate()
            process.communicate(timeout=5)

        self.addCleanup(kapat)
        son = time.monotonic() + 10
        while not hazir.exists() and process.poll() is None and time.monotonic() < son:
            time.sleep(0.02)
        self.assertTrue(hazir.exists(), "Deneme işçisi kilit alıp toplayıcıya ulaşamadı.")
        diger = self.hizmet()
        self.assertFalse(diger.baslat())
        diger.collector.assert_not_called()
        process.terminate()
        process.communicate(timeout=5)
        self.assertTrue(diger.calistir())
        diger.collector.assert_called_once()


if __name__ == "__main__":
    unittest.main()
