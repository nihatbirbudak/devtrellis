"""Akademik sorgunun gerçek kartlara dönüşmesini, yinelenmemesini ve ağ sınırlarının korunmasını sınar."""

import json
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from radar.depo import Depo
from radar.konular import KONULAR
from radar.kesif import Kesif, KesifMesgul, kesif_kaynaklari, sorguyu_genislet
from radar.toplayici import CollectionError, FetchResult, APPROVED_FETCH_HOSTS


def yayin(number=1, **fields):
    return {"id": f"https://openalex.org/W{number}", "doi": f"https://doi.org/10.1234/paper-{number}",
            "title": f"Evidence based research agents experiment {number}", "publication_date": "2026-10-25",
            "abstract_inverted_index": {"Research": [0], "agents": [1], "evaluate": [2], "citations.": [3]},
            "primary_location": {"landing_page_url": "https://journal.example.org/article", "source": {"display_name": "A journal"}}, **fields}


def yanit(url, works=None, status=200):
    return FetchResult(status, json.dumps({"results": works if works is not None else [yayin()]}).encode(), {}, url)


class KesifTestleri(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.depo = Depo(Path(self.temp.name) / "radar.sqlite3", [])
        self.calls = []
        def fetch(url, hosts, **kwargs):
            self.calls.append((url, hosts, kwargs))
            return yanit(url)
        self.fetch = fetch

    def servis(self, fetch=None):
        service = Kesif(self.depo, fetcher=fetch or self.fetch, clock=lambda: datetime(2026, 10, 26, 12, tzinfo=timezone.utc))
        self.addCleanup(service.kapan)
        return service

    def test_baslangic_ve_durum_ag_cagrisi_yapmaz(self):
        service = self.servis()
        self.assertFalse(service.gorunum()["running"])
        self.assertEqual(service.gorunum()["weekly_interval_hours"], 168)
        self.assertEqual(self.calls, [])
        self.assertEqual(len(self.depo.kaynaklar()), len(KONULAR))

    def test_turkce_soru_gercek_yayin_kaydi_ve_kullanilan_sorguyla_saklanir(self):
        service = self.servis()
        original = "Ev otomasyonu için kaynaklı araştırmayı nasıl geliştirebiliriz?"
        result = service.calistir("jarvis", original)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["query"], original)
        self.assertIn("deep research", result["searches"][0]["query"])
        self.assertEqual(result["added"], 1)
        self.assertEqual(service.oku(result["run_id"]), result)
        with self.depo.baglanti() as connection:
            row = connection.execute("SELECT * FROM items").fetchone()
            match = connection.execute("SELECT * FROM kesif_matches").fetchone()
        self.assertEqual(row["id"], result["item_ids"][0])
        self.assertEqual(row["excerpt"], "Research agents evaluate citations.")
        self.assertEqual(match["category"], "recent")
        self.assertIn("stop_event", self.calls[0][2])

    def test_doi_ve_konu_degisse_de_ayni_yayin_yeni_kart_sayilmaz(self):
        service = self.servis()
        first = service.calistir("ai", "research agents")
        second = service.calistir("jarvis", "research agents")
        self.assertEqual(first["item_ids"], second["item_ids"])
        self.assertEqual(second["added"], 0)
        self.assertEqual(second["duplicates"], 1)
        with self.depo.baglanti() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM items").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM source_entries").fetchone()[0], 2)

    def test_sosyal_iddia_sorgusu_genel_arastirma_ajanlari_sorgusuna_donusmez(self):
        service = self.servis()
        query = 'Bu iddiayı kaynaklarla araştır: PostgreSQL 19 latency improves by 40 percent'
        result = service.calistir('data', query, preserve_query=True)
        self.assertEqual(result['status'], 'completed')
        self.assertTrue(result['preserve_query'])
        self.assertEqual(result['searches'][0]['query'], query)
        self.assertIn('PostgreSQL 19', result['searches'][0]['query'])

    def test_beslemede_onceden_bulunan_doi_yeniden_yazilmaz(self):
        self.servis()
        self.depo.kaydet("kesif_ai", [{"url": "https://doi.org/10.1234/PAPER-1", "title": "Original public title", "excerpt": "Original abstract"}])
        service = self.servis()
        result = service.calistir("ai", "research agents")
        self.assertEqual(result["added"], 0)
        with self.depo.baglanti() as connection:
            self.assertEqual(connection.execute("SELECT title FROM items").fetchone()[0], "Original public title")

    def test_dizin_erisim_hatasi_gorunur_ve_yedek_sorgu_calistirilir(self):
        def fetch(url, hosts, **kwargs):
            if "openalex" in url:
                return yanit(url, status=429)
            if "semanticscholar" in url:
                raise CollectionError("Robots erişimi bu isteğe izin vermiyor.")
            return FetchResult(200, json.dumps({"message": {"items": [{"DOI": "10.1234/paper-2", "title": ["Research evidence and citations"], "published": {"date-parts": [[2020, 2, 1]]}}]}}).encode(), {}, url)
        service = self.servis(fetch)
        with patch.object(service.stop, 'wait', return_value=False):
            result = service.calistir("ai", "research agents")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["errors"]), 2)
        self.assertEqual(result["searches"][0]["provider"], "crossref")
        with self.depo.baglanti() as connection:
            self.assertEqual(connection.execute("SELECT category FROM kesif_matches").fetchone()[0], "foundation")

    def test_erisim_yoksa_bos_liste_basarisi_uydurulmaz(self):
        service = self.servis(lambda url, *args, **kwargs: yanit(url, status=403))
        with patch.object(service.stop, 'wait', return_value=False):
            result = service.calistir("ai", "research agents")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(result["errors"]), 3)
        self.assertEqual(result["item_ids"], [])

    def test_gecerli_bos_yanit_ile_erisim_hatasi_ayridir(self):
        result = self.servis(lambda url, *args, **kwargs: yanit(url, [])).calistir("ai", "research agents")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["item_ids"], [])

    def test_konu_registry_ve_haftalik_vade_kalici_gercek_sorgulardan_olusur(self):
        service = self.servis()
        result = service.calistir()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(self.calls), len(KONULAR) * 2)
        self.assertEqual({row["topic"] for row in result["searches"]}, set(KONULAR))
        self.assertEqual(service.gorunum()["next_due_at"], "2026-11-02T12:00:00+00:00")

    def test_iki_nesne_tek_is_calistirir_ve_oku_ayri_isi_karistirmaz(self):
        started, release = threading.Event(), threading.Event()
        def fetch(url, *args, **kwargs):
            started.set()
            release.wait(3)
            return yanit(url)
        first = self.servis(fetch)
        second = self.servis()
        self.addCleanup(release.set)
        run = first.baslat("ai", "research agents")
        self.assertTrue(started.wait(2))
        try:
            with self.assertRaises(KesifMesgul):
                second.baslat("ai", "different query")
            self.assertEqual(second.oku(run["run_id"])["status"], "running")
            # Yeni nesnenin açılması süren işi interrupted yapamaz.
            self.assertEqual(self.servis().oku(run["run_id"])["status"], "running")
        finally:
            release.set()
            first.thread.join(5)
        self.assertEqual(first.oku(run["run_id"])["status"], "completed")

    def test_kapanis_iptali_sonraki_konuya_ag_istegi_yapmaz(self):
        holder = {}
        def fetch(url, *args, **kwargs):
            holder['service'].stop.set()
            return yanit(url)
        holder['service'] = service = self.servis(fetch)
        result = service.calistir()
        self.assertEqual(result['status'], 'interrupted')
        self.assertEqual(len(result['searches']), 1)
        self.assertEqual(result['item_ids'], [])

    def test_yayin_hostlari_kalici_ama_ozel_adresler_yetkilendirilemez(self):
        service = self.servis()
        service.calistir('ai', 'research agents')
        reopened = self.servis()
        self.assertIn('journal.example.org', next(s for s in reopened.depo.kaynaklar() if s['id']=='kesif_ai')['item_hosts'])
        bad = yayin(3, doi=None, id='https://127.0.0.1/private', primary_location={'landing_page_url': 'https://user:pass@private.example.org/'})
        result = self.servis(lambda url, *args, **kwargs: yanit(url, [bad])).calistir('ai', 'research agents')
        self.assertEqual(result['item_ids'], [])
        self.assertNotIn('127.0.0.1', APPROVED_FETCH_HOSTS)

    def test_uzun_soru_500_karaktere_kadar_korunur_genel_konuya_donmez(self):
        query = ('Kaynaklı araştırmayı nasıl geliştirebiliriz? ' * 10)[:450]
        service = self.servis()
        result = service.calistir('jarvis', query)
        self.assertEqual(result['query'], query.strip())
        self.assertEqual(sorguyu_genislet('Unknown highly specific question'), 'Unknown highly specific question')
        with self.assertRaises(ValueError):
            service.calistir('ai', 'x' * 501)

    def test_kaynakca_baglari_uc_yayinla_sinirlidir_ve_sorgu_sonucundan_ayrilir(self):
        calls = []
        def fetch(url, *args, **kwargs):
            calls.append(url)
            if '/works/W' in url:
                number = int(url.rsplit('W',1)[-1])
                return FetchResult(200, json.dumps(yayin(number, publication_date='2020-01-10')).encode(), {}, url)
            return yanit(url, [yayin(referenced_works=['https://openalex.org/W'+str(i) for i in range(2,10)] + ['https://private.example.org/a'])])
        service = self.servis(fetch)
        result = service.calistir('ai', 'research agents')
        self.assertEqual(len(calls), 4)
        self.assertEqual(len(result['relations']), 3)
        self.assertEqual(len(result['item_ids']), 4)
        self.assertEqual(len(result['searches']), 1)
        self.assertEqual({r['parent_item_id'] for r in result['relations']}, {result['item_ids'][0]})
        self.assertEqual({r['relation'] for r in result['relations']}, {'reference'})

    def test_arxiv_url_ile_acik_doi_ayni_karti_bulur(self):
        service = self.servis(lambda url, *args, **kwargs: yanit(url, [yayin(doi='https://doi.org/10.48550/arxiv.2601.12369')]))
        self.depo.kaydet('kesif_ai', [{'url':'https://arxiv.org/abs/2601.12369v2', 'title':'Existing arXiv research publication'}])
        result = service.calistir('ai','research agents')
        self.assertEqual(result['added'],0)
        self.assertEqual(result['duplicates'],1)

    def test_zamanli_baslatma_hatasi_donguyu_oldurmez(self):
        service = self.servis()
        with patch.object(service, 'baslat', side_effect=[OSError('private details'), {'started':True}]) as start, patch.object(service.stop, 'wait', side_effect=[False,True]):
            service.zamanli_calistir()
        self.assertEqual(start.call_count, 2)
        self.assertIsNone(service.gorunum()['schedule_error'])
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
