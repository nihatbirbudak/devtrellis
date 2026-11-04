"""Kaynak öğrenmesi API'sinde yerel yetkiyi, sürüm çakışmalarını ve ağsız okumayı doğrular."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '.deps'))

from fastapi.testclient import TestClient
from radar.sunucu import uygulama
from radar.kaynak_arastirmacisi import KaynakArastirmaciMesgul, KaynakAdayCatismasi, KaynakArastirmaciSiniri

ADRES = 'http://127.0.0.1:8781'
KAYNAK = {'id':'deneme','name':'Deneme kaynağı','topic':'ai','enabled':True,
          'kind':'rss','interval_hours':48,'benefit':'Araştırma için.',
          'fetch_url':'https://example.org/feed','url':'https://example.org/feed'}


class KaynakOgrenmeApiTestleri(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'radar.sqlite3'
        self.fetcher = Mock(side_effect=AssertionError('Okuma ağ isteği başlatmamalı.'))
        self.app = uygulama(self.path, sources=[KAYNAK], seed=False,
                            source_scout_fetcher=self.fetcher, discovery_fetcher=self.fetcher)
        self.client = TestClient(self.app,base_url=ADRES)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__,None,None,None)
        self.headers = {'Origin':ADRES,'X-Radar-CSRF':self.client.get('/api/state').json()['csrf_token']}

    def post(self,path,body):
        return self.client.post(path,json=body,headers=self.headers)

    def source(self):
        return next(row for row in self.client.get('/api/source-learning').json()['learning']['sources'] if row['source_id']=='deneme')

    def test_okuma_ag_ve_model_cagrisi_acmaz(self):
        for _ in range(2):
            response = self.client.get('/api/source-learning')
            self.assertEqual(response.status_code,200,response.text)
            self.assertIn('learning',response.json())
            self.assertIn('scout',response.json())
        state=self.client.get('/api/state').json()
        self.assertIn('source_learning',state)
        self.assertIn('source_scout',state)
        self.fetcher.assert_not_called()
        with self.app.state.depo.baglanti() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM ai_attempts').fetchone()[0],0)

    def test_butun_yazma_yollari_csrf_ve_ayni_koken_ister(self):
        paths = ['/settings','/sources/deneme/protect','/changes/olmayan/undo','/scout/start','/candidates/olmayan/decision']
        for path in paths:
            with self.subTest(path=path):
                url='/api/source-learning'+path
                self.assertEqual(self.client.post(url,json={}).status_code,403)
                self.assertEqual(self.client.post(url,json={},headers={**self.headers,'Origin':'https://example.org'}).status_code,403)
        self.assertEqual(self.client.get('/api/source-learning',headers={'Origin':'https://example.org'}).status_code,403)
        self.fetcher.assert_not_called()

    def test_koruma_surumlu_ve_kalici_kullanici_araligini_degistirmez(self):
        before=self.source()
        response=self.post('/api/source-learning/sources/deneme/protect',{'protected':True,'expected_version':before['version']})
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(self.source()['protected'])
        conflict=self.post('/api/source-learning/sources/deneme/protect',{'protected':False,'expected_version':before['version']})
        self.assertEqual(conflict.status_code,409,conflict.text)
        self.assertTrue(self.source()['protected'])
        source=self.app.state.source_manager.oku('deneme')
        self.assertEqual(source['interval_hours'],48)
        with self.app.state.depo.baglanti() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM source_preferences').fetchone()[0],0)

    def test_ogrenme_duraklatma_surumlu_ve_mevcut_kaynaklari_kapatmaz(self):
        settings=self.client.get('/api/source-learning').json()['learning']['settings']
        body={'enabled':False,'expected_version':settings['version']}
        response=self.post('/api/source-learning/settings',body)
        self.assertEqual(response.status_code,200,response.text)
        self.assertFalse(self.client.get('/api/source-learning').json()['learning']['settings']['enabled'])
        conflict=self.post('/api/source-learning/settings',{**body,'enabled':True})
        self.assertEqual(conflict.status_code,409,conflict.text)
        self.assertTrue(self.app.state.source_manager.oku('deneme')['enabled'])

    def test_bilinmeyen_kaynak_ve_geri_alma_404(self):
        for suffix,body in [('/sources/olmayan/protect',{'protected':True,'expected_version':0}),
                            ('/changes/olmayan/undo',{'expected_version':0})]:
            response=self.post('/api/source-learning'+suffix,body)
            self.assertEqual(response.status_code,404,response.text)

    def test_gecersiz_degerler_sessizce_donusturulmez(self):
        cases=[('/settings',{'enabled':'false','expected_version':0}),
               ('/settings',{'enabled':True,'expected_version':True}),
               ('/settings',{'enabled':True,'expected_version':0,'daily_limit':500}),
               ('/sources/deneme/protect',{'protected':1,'expected_version':0}),
               ('/candidates/none/decision',{'decision':'delete','expected_version':0}),
               ('/scout/start',{'unlimited':True})]
        for suffix,body in cases:
            with self.subTest(suffix=suffix,body=body):
                response=self.post('/api/source-learning'+suffix,body)
                self.assertEqual(response.status_code,422,response.text)
        self.fetcher.assert_not_called()

    def test_yeni_kaynak_arastirma_baslatma_ve_mesgul_cevabi(self):
        with patch.object(self.app.state.source_scout,'baslat',return_value=True) as start:
            self.assertEqual(self.post('/api/source-learning/scout/start',{}).status_code,202)
            start.assert_called_once_with()
        with patch.object(self.app.state.source_scout,'baslat',side_effect=KaynakArastirmaciMesgul('Araştırma sürüyor.')):
            self.assertEqual(self.post('/api/source-learning/scout/start',{}).status_code,409)

    def test_aday_karari_surumle_iletilir_ve_cakisma_409(self):
        path='/api/source-learning/candidates/aday1/decision'
        with patch.object(self.app.state.source_scout,'aday_karari',return_value={'status':'dismissed'}) as decide:
            response=self.post(path,{'decision':'dismiss','expected_version':3})
            self.assertEqual(response.status_code,200,response.text)
            decide.assert_called_once_with('aday1',decision='dismiss',expected_version=3)
        with patch.object(self.app.state.source_scout,'aday_karari',side_effect=KaynakAdayCatismasi('Aday değişti.')):
            self.assertEqual(self.post(path,{'decision':'trial','expected_version':2}).status_code,409)

    def test_kesif_butcesi_dolunca_429_ve_yeniden_deneme_suresi_gorunur(self):
        limit = KaynakArastirmaciSiniri('Haftalık kaynak keşfi bütçesi doldu.', next_due_at='2099-01-05T00:00:00+00:00')
        with patch.object(self.app.state.source_scout,'baslat',side_effect=limit):
            response=self.post('/api/source-learning/scout/start',{})
        self.assertEqual(response.status_code,429,response.text)
        self.assertIn('Haftalık',response.json()['detail'])
        self.assertGreater(int(response.headers['Retry-After']),0)
        self.fetcher.assert_not_called()

    def test_yeni_tablolar_tam_disa_aktarima_dahildir(self):
        self.post('/api/source-learning/sources/deneme/protect',{'protected':True,'expected_version':self.source()['version']})
        exported=self.client.get('/api/export').json()
        with self.app.state.depo.baglanti() as connection:
            tables={row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        self.assertTrue(tables.issubset(exported))
        self.assertEqual(len(exported['items']),0)


if __name__=='__main__':
    unittest.main()
