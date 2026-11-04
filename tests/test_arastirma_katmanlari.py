"""NVIDIA ilgi ve ön araştırmasını Claude kararından ve derin araştırmadan ayırır."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '.deps'))
from radar.arastirma_akisi import ArastirmaAkisi
from radar.zeka import ZekaIsleri, DERIN_MODEL
from radar.derin_arastirma import DerinArastirmaZamanAsimi
from tests import test_arastirma_akisi as duzenek


class ArastirmaKatmanlariTestleri(unittest.TestCase):
    setUp = duzenek.ArastirmaAkisiTestleri.setUp
    open = duzenek.ArastirmaAkisiTestleri.open
    close = duzenek.ArastirmaAkisiTestleri.close

    def hazirla(self, decision='research'):
        self.flow.kapan()
        self.ai.kapan()
        self.nvidia = Mock(side_effect=duzenek.model)
        def brain(messages, **kwargs):
            value = json.loads(duzenek.model(messages, **kwargs))
            if json.loads(messages[1]['content'])['kind'] == 'review':
                value['research_decision'] = decision
            return json.dumps(value, ensure_ascii=False)
        self.claude = Mock(side_effect=brain)
        self.credentials = Mock(return_value='FAKE-KEY')
        self.ai = ZekaIsleri(self.depo, provider=self.nvidia, deep_provider=self.claude,
                            deep_available=lambda: True, credential_provider=self.credentials,
                            clock=self.clock, daily_limit=80)
        self.flow = ArastirmaAkisi(self.depo, self.reading, self.ai, self.collection, clock=self.clock)
        settings = self.flow.ayarlar()
        self.flow.ayarlari_yaz({key: (1 if key == 'screen_limit' else settings[key])
            for key in ('enabled', 'daily_hour', 'screen_limit', 'auto_deep_limit', 'auto_threshold')}, settings['version'])

    def test_yuksek_nvidia_puani_claude_kararini_atlayamaz(self):
        self.hazirla()
        self.flow.gunu_planla()
        self.flow.adim()
        self.assertEqual(len(duzenek.uretim_cagrilari(self.nvidia)), 1)
        self.fetcher.assert_not_called()
        self.assertEqual(len(duzenek.uretim_cagrilari(self.claude)), 0)
        self.flow.adim()
        self.assertEqual(len(duzenek.uretim_cagrilari(self.nvidia)), 2)
        self.assertEqual(self.fetcher.call_count, 1)
        self.assertEqual(len(duzenek.uretim_cagrilari(self.claude)), 0)
        self.assertEqual({t['kind'] for t in self.flow.gorunum()['tasks']}, {'interest', 'screen', 'review'})
        self.flow.adim()
        self.assertEqual(len(duzenek.uretim_cagrilari(self.claude)), 1)
        sent = json.loads(duzenek.uretim_cagrilari(self.claude)[-1][0][0][1]['content'])
        self.assertEqual(sent['kind'], 'review')
        self.assertIn('preliminary_assessment', sent)
        self.assertIsNone(duzenek.uretim_cagrilari(self.claude)[-1].kwargs['api_key'])
        payload = json.loads(duzenek.uretim_cagrilari(self.claude)[-1][0][0][1]['content'])
        self.assertTrue(payload['sources'][0]['url'].startswith('https://'))

        self.assertEqual(duzenek.uretim_cagrilari(self.claude)[-1].kwargs['model'], DERIN_MODEL)
        self.flow.adim()
        self.assertEqual(len(duzenek.uretim_cagrilari(self.claude)), 2)
        self.assertEqual(len(duzenek.uretim_cagrilari(self.nvidia)), 2)
        deep = next(t for t in self.flow.gorunum()['tasks'] if t['kind'] == 'deep')
        self.assertEqual(deep['status'], 'ready', deep['message'])
        self.assertIsNone(deep['decision'])
        self.assertEqual(deep['engine'], 'claude')
        self.assertEqual(self.ai.durum()['daily_used'], 4)
        self.assertEqual(self.ai.durum()['deep_daily_used'], 4)

    def test_claude_hatasi_guvenli_ayri_aciklamayla_gorunur(self):
        self.hazirla()
        self.claude.side_effect = DerinArastirmaZamanAsimi('PRIVATE-SECRET-error')
        task = self.flow.kuyruga_al(self.ids[0], 'deep', 'user')
        self.flow.adim()
        current = self.flow.oku(task['id'])
        self.assertNotEqual(current['status'], 'ready')
        self.assertIn('Claude', current['message'])
        self.assertNotIn('PRIVATE-SECRET', current['message'])
        self.nvidia.assert_not_called()

    def test_claude_izle_derse_otomatik_derin_arastirma_acilmaz(self):
        self.hazirla('watch')
        self.flow.gunu_planla()
        for _ in range(3):
            self.flow.adim()
        self.assertFalse(any(t['kind'] == 'deep' for t in self.flow.gorunum()['tasks']))
        self.assertFalse(self.flow.adim())
        self.assertEqual(len(duzenek.uretim_cagrilari(self.claude)), 1)

    def test_kullanici_secimi_dogrudan_claude_ile_arastirilir(self):
        self.hazirla()
        self.credentials.return_value = None
        task = self.flow.kuyruga_al(self.ids[0], 'deep', 'user')
        self.flow.adim()
        self.assertEqual(self.flow.oku(task['id'])['status'], 'ready')
        self.nvidia.assert_not_called()
        self.assertEqual(len(duzenek.uretim_cagrilari(self.claude)), 1)
        self.assertIsNone(duzenek.uretim_cagrilari(self.claude)[-1].kwargs['api_key'])

    def test_claude_yokken_nvidiaya_gizli_geri_dusme_yok(self):
        self.hazirla()
        self.ai.deep_available = lambda: False
        task = self.flow.kuyruga_al(self.ids[0], 'deep', 'user')
        self.flow.adim()
        current = self.flow.oku(task['id'])
        self.assertEqual(current['status'], 'blocked')
        self.assertIn('Claude', current['message'])
        self.claude.assert_not_called()
        self.nvidia.assert_not_called()
        self.assertFalse(self.flow.gorunum()['status']['budget_waiting'])


if __name__ == '__main__':
    unittest.main()
