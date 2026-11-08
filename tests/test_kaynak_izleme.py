import hashlib
from contextlib import closing
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from email.utils import formatdate
from unittest.mock import patch

from radar import toplayici
from radar.kaynak_http import request_variant
from radar.kaynak_izleme import SourceTracker, TrackingSource
from radar.kaynak_sinyali import _lastmod, fetch_signal, validate_observation, validate_signal
from radar.toplayici import FetchResult

PAGE = b'<main><h1>Example documentation</h1><h2 id="first">First</h2><p>Stable public technical facts about the first topic.</p><h2 id="second">Second</h2><p>Original public technical facts about the second topic.</p></main>'


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'tracker.sqlite3'
        self.now = 1793970000.0
        self.source = TrackingSource('document','https://www.postgresql.org/docs/18/example.html')
        self.calls = []
        self.body = PAGE
        self.status = 200
        self.failure = False
        self.headers = {}
        self.tracker = self.make()

    def fetch(self, source, *, etag=None, modified=None):
        self.calls.append((etag,modified))
        if self.failure:
            raise ValueError('SECRET-DO-NOT-STORE private query')
        headers = {'content-type':'text/html; charset=utf-8','cache-control':'public, max-age=600',
                   'date':formatdate(self.now,usegmt=True),'etag':'"v1"',**self.headers}
        response = FetchResult(self.status,self.body if self.status==200 else b'',headers,source['url'])
        return response,{'sent_etag':etag,'sent_modified':modified,'request_variant':request_variant(source['language']),
                         'request_started_at':self.now,'response_received_at':self.now}

    def make(self, **kwargs):
        return SourceTracker(self.path,[kwargs.pop('source',self.source)],fetcher=kwargs.pop('fetcher',self.fetch),clock=lambda:self.now,**kwargs)

    def test_first_and_unchanged_200_are_models_free_no_raw_storage(self):
        first=self.tracker.check('document',max_age_seconds=0)
        self.assertTrue(first['check_succeeded'],first)
        second=self.tracker.check('document',max_age_seconds=0)
        self.assertTrue(second['check_succeeded'],second)
        self.assertEqual(second['revision'],1)
        self.assertEqual(second['changes']['status'],'unchanged')
        self.assertEqual(second['model_calls'],0)
        self.assertEqual(second['downloaded_bytes'],len(PAGE)*2)
        with closing(sqlite3.connect(self.path)) as connection:
            dump='\n'.join(connection.iterdump())
        self.assertNotIn('Stable public technical facts',dump)
        self.assertNotIn('<main>',dump)

    def test_one_changed_section_and_version_increment(self):
        self.tracker.check('document',max_age_seconds=0)
        self.body=PAGE.replace(b'Original',b'Updated')
        result=self.tracker.check('document',max_age_seconds=0)
        self.assertTrue(result['check_succeeded'],result)
        self.assertEqual(result['revision'],2)
        self.assertEqual(len(result['changes']['changed']),1)
        self.assertEqual(result['changes']['added'],[])
        self.assertEqual(result['changes']['removed'],[])

    def test_fresh_read_never_fetches_and_304_does_not_refresh_bytes(self):
        initial=self.tracker.check('document')
        self.now+=10
        fresh=self.tracker.check('document')
        self.assertFalse(fresh['check_started'])
        self.assertEqual(len(self.calls),1)
        self.now+=600
        self.status=304
        checked=self.tracker.check('document')
        self.assertTrue(checked['check_succeeded'],checked)
        self.assertEqual(checked['content_fetched_at'],initial['content_fetched_at'])
        self.assertGreater(checked['representation_checked_at'],initial['representation_checked_at'])
        self.assertFalse(checked['byte_verified'])
        self.assertIsNone(checked['quote_verified_at'])
        self.assertEqual(self.calls[-1],('"v1"',None))

    def test_network_failure_does_not_refresh_success_or_leak_error(self):
        first=self.tracker.check('document')
        self.now+=601
        self.failure=True
        failed=self.tracker.check('document')
        self.assertFalse(failed['check_succeeded'])
        self.assertEqual(failed['representation_checked_at'],first['representation_checked_at'])
        self.assertFalse(failed['fresh'])
        self.assertNotIn('SECRET',json.dumps(failed))
        again=self.tracker.check('document')
        self.assertFalse(again['check_started'])
        self.assertEqual(len(self.calls),2)

    def test_signal_only_advances_its_own_time_when_document_fails(self):
        source=TrackingSource('document',self.source.url,signal_kind='sitemap',signal_url='https://www.postgresql.org/sitemap.xml')
        tracker=self.make(source=source,signal_fetcher=lambda s:({'kind':'sitemap','fingerprint':'a'*64},100))
        self.failure=True
        result=tracker.check('document')
        self.assertEqual(result['signal_checked_at'],self.now)
        self.assertIsNone(result['representation_checked_at'])
        self.assertFalse(result['fresh'])
        self.assertEqual(result['requests'],2)

    def test_unchanged_git_hint_does_not_skip_changed_http_document(self):
        source=TrackingSource('document',self.source.url,signal_kind='git_commit',signal_url='https://api.github.com/repos/example/public/commits/main',signal_interval_seconds=3600)
        tracker=self.make(source=source,signal_fetcher=lambda s:({'kind':'git_commit','fingerprint':'b'*64},100))
        first=tracker.check('document')
        self.now+=3601
        self.body=PAGE.replace(b'Original',b'Updated')
        second=tracker.check('document')
        self.assertTrue(second['check_succeeded'],second)
        self.assertEqual(second['revision'],2)
        self.assertGreater(second['signal_checked_at'],first['signal_checked_at'])
        self.assertEqual(len(self.calls),2)

    def test_invalid_signal_cannot_block_required_get(self):
        source=TrackingSource('document',self.source.url,signal_kind='sitemap',signal_url='https://www.postgresql.org/sitemap.xml')
        tracker=self.make(source=source,signal_fetcher=lambda s:({'kind':'sitemap','fingerprint':'PRIVATE'},1))
        result=tracker.check('document')
        self.assertTrue(result['check_succeeded'],result)
        self.assertIsNone(result['signal_checked_at'])

    def test_two_instances_share_one_lease(self):
        other=self.make()
        observations=[]
        def fetch(source,**kwargs):
            observations.append(other.check('document',max_age_seconds=0))
            return self.fetch(source,**kwargs)
        tracker=self.make(fetcher=fetch)
        result=tracker.check('document')
        self.assertTrue(result['check_succeeded'],result)
        self.assertFalse(observations[0]['check_started'])
        self.assertEqual(observations[0]['state'],'checking')
        self.assertEqual(len(self.calls),1)

    def test_expired_lease_cannot_commit_partial_result(self):
        def delayed(source,**kwargs):
            response=self.fetch(source,**kwargs)
            self.now+=91
            return response
        tracker=self.make(fetcher=delayed)
        result=tracker.check('document')
        self.assertFalse(result['check_succeeded'])
        self.assertTrue(result['ownership_lost'])
        self.assertEqual(result['revision'],0)
        self.assertIsNone(result['document'])

    def test_budget_and_clock_rollback_do_not_report_freshness(self):
        tracker=self.make(daily_limit=1)
        tracker.check('document')
        result=tracker.check('document',max_age_seconds=0)
        self.assertFalse(result['check_started'])
        self.assertTrue(result['budget_exhausted'])
        self.now-=10
        self.assertFalse(tracker.read('document')['fresh'])

    def test_policy_configuration_change_forces_full_validation(self):
        self.tracker.check('document')
        changed=TrackingSource('document',self.source.url,allow_public_cookie=True)
        result=self.make(source=changed).check('document')
        self.assertTrue(result['check_started'])
        self.assertEqual(self.calls[-1],(None,None))

    def test_verified_pinned_object_reuse_keeps_check_timestamp(self):
        object_id='c'*40
        source=TrackingSource('document','https://www.postgresql.org/docs/'+object_id+'/example.html',immutable_object_id=object_id,expected_content_sha256=hashlib.sha256(PAGE).hexdigest())
        tracker=self.make(source=source)
        first=tracker.check('document')
        self.now+=5000
        second=tracker.check('document')
        self.assertFalse(second['check_started'])
        self.assertEqual(second['freshness_basis'],'immutable_object')
        self.assertEqual(first['representation_checked_at'],second['representation_checked_at'])
        self.assertEqual(len(self.calls),1)

    def test_wrong_pinned_object_hash_is_rejected(self):
        object_id='c'*40
        source=TrackingSource('document','https://www.postgresql.org/docs/'+object_id+'/example.html',immutable_object_id=object_id,expected_content_sha256='d'*64)
        result=self.make(source=source).check('document')
        self.assertFalse(result['check_succeeded'])
        self.assertEqual(result['revision'],0)

    def test_existing_v1_database_and_hardlink_are_not_modified(self):
        foreign=Path(self.temp.name)/'old.sqlite3'
        with closing(sqlite3.connect(foreign)) as connection:
            connection.execute('CREATE TABLE source_memory(key TEXT)')
        before=foreign.read_bytes()
        with self.assertRaises(ValueError):
            SourceTracker(foreign,[self.source])
        self.assertEqual(foreign.read_bytes(),before)
        linked=Path(self.temp.name)/'hardlink.sqlite3'
        linked.hardlink_to(foreign)
        with self.assertRaises(ValueError):
            SourceTracker(linked,[self.source])
        self.assertEqual(foreign.read_bytes(),before)

    def test_foreign_hot_journal_is_rejected_without_recovery(self):
        foreign=Path(self.temp.name)/'hot.sqlite3'
        child='''import os,sqlite3,sys
c=sqlite3.connect(sys.argv[1])
c.execute('PRAGMA journal_mode=DELETE')
c.execute('PRAGMA cache_size=1')
c.execute('CREATE TABLE foreign_data(value TEXT)')
c.executemany('INSERT INTO foreign_data VALUES(?)',[('old'*1000,)]*100)
c.commit()
c.execute('BEGIN IMMEDIATE')
c.execute("UPDATE foreign_data SET value='new' || value")
os._exit(0)
'''
        subprocess.run([sys.executable,'-B','-c',child,str(foreign)],check=True)
        journal=foreign.with_name(foreign.name+'-journal')
        self.assertGreater(journal.stat().st_size,0)
        before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (foreign,journal)}
        with self.assertRaises(ValueError):
            SourceTracker(foreign,[self.source])
        self.assertEqual(before,{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (foreign,journal)})
        self.assertFalse(foreign.with_name(foreign.name+'-shm').exists())

    def test_linked_sqlite_sidecar_is_rejected_before_open(self):
        other=Path(self.temp.name)/'unrelated.txt'
        other.write_text('preserve',encoding='utf-8')
        sidecar=self.path.with_name(self.path.name+'-wal')
        self.assertFalse(sidecar.exists())
        sidecar.hardlink_to(other)
        with self.assertRaises(ValueError):
            self.tracker.read('document')
        self.assertEqual(other.read_text(encoding='utf-8'),'preserve')


class SignalTests(unittest.TestCase):
    def test_default_git_hint_uses_temporary_host_scope_and_restores_it(self):
        source=TrackingSource('document','https://www.postgresql.org/docs/18/example.html',
                              signal_kind='git_commit',signal_url='https://api.github.com/repos/example/public/commits/main')
        original=toplayici._REQUEST_FETCH_HOSTS.get()
        catalog=set(toplayici.APPROVED_FETCH_HOSTS)
        def robots(url,hosts,deadline):
            self.assertEqual(toplayici._validate_url(url,hosts).hostname,'api.github.com')
            return True
        def request(url,hosts,headers,deadline,**kwargs):
            self.assertEqual(toplayici._validate_url(url,hosts).hostname,'api.github.com')
            self.assertNotIn('Authorization',headers)
            return FetchResult(200,json.dumps({'sha':'c'*40}).encode(),{},url)
        with patch.object(toplayici,'_robots_allowed',robots),patch.object(toplayici,'_request_once',request):
            result,size=fetch_signal(source)
        self.assertEqual(result['kind'],'git_commit')
        self.assertGreater(size,0)
        self.assertEqual(toplayici._REQUEST_FETCH_HOSTS.get(),original)
        self.assertEqual(set(toplayici.APPROVED_FETCH_HOSTS),catalog)
        with patch.object(toplayici,'_robots_allowed',side_effect=ValueError('failed')):
            with self.assertRaises(ValueError):
                fetch_signal(source)
        self.assertEqual(toplayici._REQUEST_FETCH_HOSTS.get(),original)

    def test_sitemap_exact_url_date_and_xml_entity_boundaries(self):
        url='https://www.postgresql.org/docs/18/example.html'
        body=('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>'+url+'</loc><lastmod>2026-01-01</lastmod></url></urlset>').encode()
        self.assertEqual(_lastmod(body,url),'2026-01-01T00:00:00+00:00')
        with self.assertRaises(ValueError):
            _lastmod(body,url.replace('/18/','/17/'))
        with self.assertRaises(ValueError):
            _lastmod(b'<!DOCTYPE x [<!ENTITY x "bad">]><urlset/>',url)
        with self.assertRaises(ValueError):
            validate_signal('sitemap','https://example.com/sitemap.xml',url)
        with self.assertRaises(ValueError):
            validate_observation({'kind':'sitemap','fingerprint':'a'*64,'private':'data'},'sitemap',1)


class CLITests(unittest.TestCase):
    def setUp(self):
        spec=importlib.util.spec_from_file_location('source_track_cli',Path(__file__).resolve().parents[1]/'araclar'/'source-track.py')
        self.cli=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.cli)

    def test_local_read_has_no_network_and_deferred_check_is_not_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            config=Path(tmp)/'sources.json'
            config.write_text(json.dumps({'schema':'devtrellis-source-tracking-v2','daily_limit':100,
                'sources':[{'id':'document','url':'https://www.postgresql.org/docs/18/example.html'}]}),encoding='utf-8')
            args=['source-track.py','--config',str(config),'--db',str(Path(tmp)/'k0.sqlite3'),'--source','document']
            output=io.StringIO()
            with patch.object(sys,'argv',args),patch.object(sys,'stdout',output),patch('radar.kaynak_izleme.fetch_document',side_effect=AssertionError('no network')) as fetch:
                self.assertEqual(self.cli.main(),0)
            fetch.assert_not_called()
            self.assertEqual(json.loads(output.getvalue())['state'],'missing')
            with patch.object(sys,'argv',args+['--check']),patch.object(sys,'stdout',io.StringIO()),patch.object(SourceTracker,'check',return_value={'check_started':False,'fresh':False,'budget_exhausted':True}):
                self.assertEqual(self.cli.main(),2)

    def test_duplicate_config_fields_and_private_argument_echo_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            config=Path(tmp)/'bad.json'
            config.write_text('{"schema":"first","schema":"second"}',encoding='utf-8')
            with self.assertRaises(ValueError):
                self.cli.load_config(config)
        output=io.StringIO()
        with patch.object(sys,'argv',['source-track.py','--secret','PRIVATE-SENTINEL']),patch.object(sys,'stderr',output),self.assertRaises(SystemExit) as error:
            self.cli.main()
        self.assertEqual(error.exception.code,2)
        self.assertNotIn('PRIVATE-SENTINEL',output.getvalue())


if __name__=='__main__':
    unittest.main()
