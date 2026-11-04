"""Eski akış göçünde veriyi, kanıt gerektiren insan kararını ve sınırlı belirsizlik payını korur."""

import json
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '.deps'))

from radar.arastirma_akisi import ArastirmaAkisi, tablolari_hazirla
from radar.calisma_masasi import SurumCatismasi
from tests import test_arastirma_akisi as duzenek


class AkisGuvenceTestleri(unittest.TestCase):
    setUp = duzenek.ArastirmaAkisiTestleri.setUp
    open = duzenek.ArastirmaAkisiTestleri.open
    close = duzenek.ArastirmaAkisiTestleri.close

    def eski_sema(self):
        task = self.flow.kuyruga_al(self.ids[0])
        self.flow.kapan()
        with self.depo.baglanti(write=True) as connection:
            connection.execute("UPDATE workflow_tasks SET status='ready',decision='watch',report_text='Korunan eski rapor',result_json=? WHERE id=?",
                               (json.dumps({'title_tr': 'Eski rapor'}), task['id']))
            row = dict(connection.execute('SELECT * FROM workflow_tasks WHERE id=?', (task['id'],)).fetchone())
            # Gerçek eski şema sabittir; yeni sütunlar tablo tekillik kuralının
            # arkasına eklenerek geçersiz bir CREATE TABLE üretilmemeli.
            schema = '''CREATE TABLE workflow_tasks (
                id TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES items(id), kind TEXT NOT NULL,
                source_revision INTEGER NOT NULL, source_url TEXT NOT NULL, status TEXT NOT NULL,
                stage TEXT NOT NULL, message TEXT NOT NULL, origin TEXT NOT NULL,
                profile_topic TEXT NOT NULL, profile_json TEXT NOT NULL, profile_hash TEXT NOT NULL,
                result_json TEXT, sources_json TEXT NOT NULL DEFAULT '[]', report_text TEXT NOT NULL DEFAULT '',
                scope_json TEXT NOT NULL DEFAULT '[]', coverage_json TEXT NOT NULL DEFAULT '{}',
                attempts INTEGER NOT NULL DEFAULT 0, retry_at TEXT, version INTEGER NOT NULL DEFAULT 0,
                decision TEXT, decision_at TEXT, notification_read INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, refresh_requested_at TEXT,
                UNIQUE(item_id,kind,source_revision)
            )'''
            connection.execute('DROP TABLE workflow_tasks')
            connection.execute(schema)
            for field in ('request_id', 'selection_json', 'language', 'report_model', 'report_generated_at'):
                row.pop(field)
            connection.execute('INSERT INTO workflow_tasks(' + ','.join(row) + ') VALUES(' + ','.join('?' for _ in row) + ')', tuple(row.values()))
        return row

    def test_eski_sema_butun_rapor_karar_ve_kisisel_kayitlari_korur(self):
        self.depo.arastirma_sec(self.ids[0])
        self.depo.geri_bildirim(self.ids[0], 'yararli', 'Korunacak kullanıcı tercihi')
        with self.depo.baglanti(write=True) as connection:
            job_id = connection.execute('SELECT id FROM jobs LIMIT 1').fetchone()[0]
            connection.execute('INSERT INTO research_notes VALUES(?,1,1,?,?)', (job_id, 'Kişisel not korunacak.', self.now.isoformat()))
            old_jobs = [tuple(row) for row in connection.execute('SELECT * FROM jobs')]
            old_feedback = [tuple(row) for row in connection.execute('SELECT * FROM feedback_events')]
        old = self.eski_sema()
        self.flow = ArastirmaAkisi(self.depo, self.reading, self.ai, self.collection, clock=self.clock)
        with self.depo.baglanti() as connection:
            migrated = dict(connection.execute('SELECT * FROM workflow_tasks WHERE id=?', (old['id'],)).fetchone())
            self.assertEqual({key: migrated[key] for key in old}, old)
            self.assertEqual([tuple(row) for row in connection.execute('SELECT * FROM jobs')], old_jobs)
            self.assertEqual([tuple(row) for row in connection.execute('SELECT * FROM feedback_events')], old_feedback)
            self.assertEqual(connection.execute('SELECT body FROM research_notes').fetchone()[0], 'Kişisel not korunacak.')
            self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.assertTrue(self.flow.oku(old['id'])['requires_verification'])
        self.assertEqual(self.flow.kuyruga_al(old['item_id'])['id'], old['id'])

    def test_yarida_kalan_goc_eski_tabloyu_ve_satiri_geri_alir(self):
        old = self.eski_sema()
        with self.assertRaises(RuntimeError):
            with self.depo.baglanti(write=True) as connection:
                tablolari_hazirla(connection)
                raise RuntimeError('Deneme kesintisi')
        with self.depo.baglanti() as connection:
            columns = {row['name'] for row in connection.execute('PRAGMA table_info(workflow_tasks)')}
            self.assertNotIn('request_id', columns)
            self.assertEqual(dict(connection.execute('SELECT * FROM workflow_tasks').fetchone()), old)
        self.flow = ArastirmaAkisi(self.depo, self.reading, self.ai, self.collection, clock=self.clock)

    def test_eski_ve_kismi_rapor_onayi_devir_kapisini_acamaz(self):
        task = self.flow.kuyruga_al(self.ids[0])
        for quality in (None, {'status': 'verified', 'coverage_complete': False},
                        {'status': 'partial', 'coverage_complete': True}, {'status': 'contradicted', 'coverage_complete': True}):
            result = {'title_tr': 'Önceki rapor', **({'evidence_quality': quality} if quality else {})}
            with self.depo.baglanti(write=True) as connection:
                connection.execute("UPDATE workflow_tasks SET status='ready',result_json=?,coverage_json=?,decision='develop' WHERE id=?",
                                   (json.dumps(result), json.dumps({'limited': False}), task['id']))
            current = self.flow.oku(task['id'])
            self.assertTrue(current['requires_verification'])
            self.assertFalse(current['can_develop'])
            with self.assertRaises(ValueError):
                self.flow.karar(task['id'], 'develop', expected_version=current['version'])
            with self.assertRaises(SurumCatismasi):
                self.flow.dosya(task['id'], handoff=True)
            self.assertIn('denetimi tamamlanmadı', self.flow.dosya(task['id']))
            self.assertEqual(self.flow.karar(task['id'], 'watch', expected_version=current['version'])['decision'], 'watch')

    def test_gunluk_karar_butcesinin_bir_payi_belirsiz_yararli_adaya_ayrilir(self):
        self.flow.gunu_planla()
        with self.depo.baglanti(write=True) as connection:
            connection.execute("UPDATE workflow_tasks SET status='cancelled' WHERE kind='interest'")
        for index, score in enumerate((96, 70, 68)):
            task = self.flow.kuyruga_al(self.ids[index], 'screen', 'system')
            result = {'fit': 'high' if index == 0 else 'possible', 'relevance_score': score, 'blockers': []}
            with self.depo.baglanti(write=True) as connection:
                connection.execute("UPDATE workflow_tasks SET status='ready',result_json=? WHERE id=?", (json.dumps(result), task['id']))
        self.flow.adim()
        reviews = [task for task in self.flow.gorunum()['tasks'] if task['kind'] == 'review']
        self.assertEqual(len(reviews), 2)
        self.assertEqual({task['selection']['review_route'] for task in reviews}, {'exploratory', 'high_relevance'})
        with self.depo.baglanti() as connection:
            run = connection.execute('SELECT * FROM workflow_runs').fetchone()
            self.assertEqual(run['automatic_deep_count'], 2)
            self.assertEqual(run['exploratory_review_count'], 1)
        self.assertTrue(all(task['decision'] is None for task in reviews))

    def test_kapali_konu_yuksek_puana_ragmen_gunluk_secime_giremez(self):
        with self.depo.baglanti(write=True) as connection:
            settings = json.loads(connection.execute('SELECT settings FROM user_settings').fetchone()[0])
            settings['topic_weights']['jarvis'] = 0
            connection.execute('UPDATE user_settings SET settings=?', (json.dumps(settings),))
        self.flow.gunu_planla()
        self.assertEqual(self.flow.gorunum()['tasks'], [])

    def bakim_kaydi(self, source_id, archived, status='ok'):
        with self.depo.baglanti(write=True) as connection:
            connection.execute('''CREATE TABLE IF NOT EXISTS source_maintenance (
                source_id TEXT PRIMARY KEY REFERENCES sources(id), checked_at TEXT,
                archived INTEGER, disabled INTEGER, pushed_at TEXT, last_release_at TEXT,
                repository_id INTEGER, status TEXT NOT NULL, error TEXT)''')
            connection.execute('INSERT INTO source_maintenance(source_id,checked_at,archived,status) VALUES(?,?,?,?)',
                               (source_id, self.now.isoformat(), archived, status))

    def test_arsivli_proje_gunluk_secime_girmez_eski_rapor_ve_insan_secimi_korunur(self):
        self.depo.kaydet('two', [duzenek.haber(100)])
        old = self.flow.kuyruga_al(self.ids[0], 'deep', 'user')
        with self.depo.baglanti(write=True) as connection:
            connection.execute("UPDATE workflow_tasks SET status='ready',decision='watch',report_text='Korunan arşiv raporu' WHERE id=?", (old['id'],))
        self.bakim_kaydi('one', 1, 'error')  # Son erişim hatası önceki arşiv bilgisini silmez.
        self.bakim_kaydi('two', None)
        self.flow.gunu_planla()
        tasks = self.flow.gorunum()['tasks']
        planned = [task for task in tasks if task['kind'] == 'interest']
        self.assertEqual(len(planned), 1)
        self.assertNotIn(planned[0]['item_id'], self.ids)
        self.assertEqual(self.flow.oku(old['id'])['report_text'], 'Korunan arşiv raporu')
        self.assertEqual(self.flow.oku(old['id'])['decision'], 'watch')
        manual = self.flow.kuyruga_al(self.ids[1], 'deep', 'user')
        self.assertEqual(manual['status'], 'queued')
        self.assertEqual(len(self.depo.gorunum()['items']), 11)
        with self.depo.baglanti() as connection:
            event = connection.execute("SELECT detail FROM audit WHERE kind='akis_gunluk_plan'").fetchone()
            detail = json.loads(event[0])
            self.assertEqual(detail['excluded_archived_count'], 9)
            self.assertEqual(detail['archived_sources'], ['one'])

    def test_arsivlenmemis_ve_bilinmeyen_kaynak_ayri_kalir_ikisi_de_secilebilir(self):
        self.depo.kaydet('two', [duzenek.haber(100)])
        self.bakim_kaydi('one', 0)
        self.bakim_kaydi('two', None)
        self.flow.gunu_planla()
        self.assertEqual(len(self.flow.gorunum()['tasks']), 11)
        with self.depo.baglanti() as connection:
            values = {row['source_id']: row['archived'] for row in connection.execute('SELECT * FROM source_maintenance')}
            self.assertEqual(values, {'one': 0, 'two': None})


if __name__ == '__main__':
    unittest.main()
