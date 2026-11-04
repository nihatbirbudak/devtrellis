"""Günlük seçimde konu ve proje çeşitliliğini, kapatılan ilgiyi ve bekleyen aday adaletini doğrular."""

import unittest
from datetime import datetime, timedelta, timezone

from radar.secim import gunluk_sec

NOW = datetime(2026, 10, 26, tzinfo=timezone.utc)


def aday(identifier, topic='ai', score=90, days=0, **values):
    return {'id': identifier, 'topic': topic, 'score': score, 'personal_score': score,
            'revision': 1, 'first_seen_at': (NOW - timedelta(days=days)).isoformat(),
            'url': 'https://example.org/' + identifier, 'title': identifier,
            'personal_rank': {'topic_weight': 3, 'muted': False}, **values}


class SecimTestleri(unittest.TestCase):
    def test_yuksek_ai_puanlari_diger_bes_konuyu_dislayamaz(self):
        items = [aday(str(number)) for number in range(20)]
        items += [aday(topic, topic, 10) for topic in ('jarvis', 'robotik', 'tarim', 'medya')]
        selected = gunluk_sec(items, limit=5, now=NOW)
        self.assertEqual({row['topic'] for row in selected}, {'ai', 'jarvis', 'robotik', 'tarim', 'medya'})

    def test_ayni_deponun_surumu_gunde_bir_kez_secilir(self):
        items = [aday(str(number), url=f'https://github.com/example/project/releases/tag/v{number}.0') for number in range(10)]
        items += [aday('other', url='https://github.com/example/other/releases/tag/v1.0', score=20)]
        selected = gunluk_sec(items, limit=12, now=NOW)
        self.assertEqual(len(selected), 2)
        self.assertEqual(len({row['project_key'] for row in selected}), 2)

    def test_bekleyen_dusuk_puanli_aday_zamanla_one_gecer(self):
        selected = gunluk_sec([aday('new', score=100), aday('waiting', score=20, days=45)], limit=1, now=NOW)
        self.assertEqual(selected[0]['item_id'], 'waiting')
        self.assertEqual(selected[0]['age_bonus'], 90)
        self.assertEqual(selected[0]['waiting_days'], 45)

    def test_kapatilan_konu_sessiz_sozcuk_ve_gereksiz_dahil_edilmez(self):
        items = [aday('off', personal_rank={'topic_weight': 0}), aday('muted', personal_rank={'muted': True}),
                 aday('dismissed', feedback='gereksiz'), aday('good', 'tarim')]
        self.assertEqual([row['item_id'] for row in gunluk_sec(items, limit=5, now=NOW)], ['good'])

    def test_tek_yer_varsa_onceki_gun_secilmeyen_konuya_sira_gelir(self):
        selected = gunluk_sec([aday('ai'), aday('tarim', 'tarim', 10)], limit=1, now=NOW,
                             last_topics={'ai': '2026-10-25', 'tarim': '2026-10-24'})
        self.assertEqual(selected[0]['topic'], 'tarim')

    def test_tarihsiz_adaya_bekleme_tarihi_uydurulmaz(self):
        selected = gunluk_sec([aday('unknown', first_seen_at=None)], limit=1, now=NOW)
        self.assertEqual(selected[0]['waiting_days'], 0)

    def test_ayni_yayincinin_farkli_haberleri_tek_proje_sayilmaz(self):
        selected = gunluk_sec([aday('first'), aday('second')], limit=2, now=NOW)
        self.assertEqual(len(selected), 2)

    def test_yeni_kaynak_pay_alir_ama_diger_konulari_ve_gunluk_limiti_asamaz(self):
        items = [aday(f'trial-{n}', score=10, source_id='trial') for n in range(12)]
        items += [aday(f'regular-{n}', score=100, source_id='regular') for n in range(12)]
        items += [aday(topic, topic, 20, source_id='regular') for topic in ('jarvis', 'robotik', 'tarim', 'medya')]
        sources = {'trial': {'learning_lifecycle': 'trial'}, 'regular': {'learning_lifecycle': 'regular'}}
        selected = gunluk_sec(items, limit=12, now=NOW, sources=sources, exploration_percent=20)
        self.assertEqual(len(selected), 12)
        self.assertEqual(sum(row['source_trial'] for row in selected), 2)
        self.assertEqual({row['topic'] for row in selected[:5]}, set(('ai','jarvis','robotik','tarim','medya')))
        self.assertIn('deneme payı', next(row['reason'] for row in selected if row['source_trial']))

    def test_deneme_kaynagi_sessiz_ilgiyi_ve_ayni_proje_kuralini_asamaz(self):
        items = [aday('muted', source_id='trial', personal_rank={'muted': True})]
        items += [aday(f'v{n}', source_id='trial', url=f'https://github.com/org/repo/releases/tag/v{n}') for n in range(4)]
        selected = gunluk_sec(items, limit=12, now=NOW, sources={'trial': {'learning_lifecycle':'trial'}}, exploration_percent=20)
        self.assertEqual(len(selected), 1)
        self.assertNotEqual(selected[0]['item_id'], 'muted')

    def test_deneme_yokken_butun_yerler_normal_adaylara_acik(self):
        items = [aday(str(n)) for n in range(15)]
        self.assertEqual(len(gunluk_sec(items,limit=12,now=NOW,exploration_percent=20)),12)

    def test_yeni_kaynaklar_tek_basina_butun_gunluk_hakki_tuketmez(self):
        items = [aday(str(n),source_id='trial') for n in range(15)]
        selected=gunluk_sec(items,limit=12,now=NOW,sources={'trial':{'learning_lifecycle':'trial'}},exploration_percent=20)
        self.assertEqual(len(selected),2)

    def test_deneme_payi_gecersiz_tur_ve_araliklari_reddeder(self):
        for invalid in (True, -1, 51, 20.0, '20'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                gunluk_sec([],limit=12,now=NOW,exploration_percent=invalid)

    def test_ayni_projede_esit_puan_ve_beklemede_daha_yeni_yayin_kimlikten_once_gelir(self):
        items = [aday('aaa-old', url='https://github.com/org/tool/releases/tag/v1', published_at='2026-10-01'),
                 aday('zzz-new', url='https://github.com/org/tool/releases/tag/v2', published_at='2026-10-25')]
        selected = gunluk_sec(items, limit=12, now=NOW)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]['item_id'], 'zzz-new')
        self.assertEqual(selected[0]['selection_date'], '2026-10-25')
        self.assertEqual(selected[0]['selection_date_kind'], 'publication')

    def test_atom_guncellemesi_yayin_tarihi_uydurmadan_deneme_esitligini_cozer(self):
        items = [aday('aaa-old', source_id='trial', url='https://github.com/org/tool/releases/tag/v1',
                      published_at=None, updated_at='2026-10-10T00:00:00+00:00'),
                 aday('zzz-new', source_id='trial', url='https://github.com/org/tool/releases/tag/v2',
                      published_at=None, updated_at='2026-10-25T00:00:00+00:00')]
        selected = gunluk_sec(items, limit=12, now=NOW,
                             sources={'trial': {'learning_lifecycle': 'trial'}}, exploration_percent=20)
        self.assertEqual([row['item_id'] for row in selected], ['zzz-new'])
        self.assertEqual(selected[0]['selection_date_kind'], 'source_updated')
        self.assertIn('kaynak güncelleme tarihi', selected[0]['reason'])
        self.assertIsNone(items[1]['published_at'])

    def test_gelecek_ve_bozuk_kaynak_tarihleri_oncelik_yaratmaz(self):
        items = [aday('aaa-future', published_at='2026-10-27', updated_at='2026-10-28T00:00:00Z'),
                 aday('aab-broken', published_at='2026-02-30', updated_at='hatalı'),
                 aday('zzz-valid', published_at='2026-10-20T00:00:00+00:00')]
        selected = gunluk_sec(items, limit=3, now=NOW)
        self.assertEqual(selected[0]['item_id'], 'zzz-valid')
        for row in selected[1:]:
            self.assertIsNone(row['selection_date'])
            self.assertIsNone(row['selection_date_kind'])

    def test_kaynak_tarihi_puani_beklemeyi_ve_gecerli_yayin_tarihini_ezmez(self):
        items = [aday('old-score', score=100, published_at='2026-01-01'),
                 aday('new-score', score=90, published_at='2026-10-25')]
        self.assertEqual(gunluk_sec(items, limit=1, now=NOW)[0]['item_id'], 'old-score')
        items = [aday('older-wait', score=80, days=10, published_at='2026-01-01'),
                 aday('new-wait', score=100, published_at='2026-10-25')]
        self.assertEqual(gunluk_sec(items, limit=1, now=NOW)[0]['item_id'], 'older-wait')
        items = [aday('old-publication', published_at='2026-01-01', updated_at='2026-10-25'),
                 aday('new-publication', published_at='2026-10-20')]
        self.assertEqual(gunluk_sec(items, limit=1, now=NOW)[0]['item_id'], 'new-publication')


if __name__ == '__main__':
    unittest.main()
