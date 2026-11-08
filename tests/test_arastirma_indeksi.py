"""Sıralı aramanın kamu sürümü sınırını, işlem bütünlüğünü ve doğru sayfalamasını sınar."""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import json

from radar.arastirma_siralama import INDEX_FIELDS, INDEX_VERSION
from radar.topluluk_depo import ToplulukDeposu, veritabani_kopyala


def fts5_var():
    with sqlite3.connect(":memory:") as connection:
        try:
            connection.execute("CREATE VIRTUAL TABLE probe USING fts5(text)")
        except sqlite3.OperationalError:
            return False
    return True


class IndeksKurulumu(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.path = self.directory / "community.sqlite3"
        self.now = 1793779200
        self.store = ToplulukDeposu(self.path, clock=lambda: self.now)
        self.author = self.store.hesap_ekle("author", "Contributor", "contributor", "synthetic-test-password")
        self.moderator = self.store.hesap_ekle("reviewer", "Reviewer", "moderator", "synthetic-test-password")
        self.number = 0

    def packet(self, **changes):
        return {"schema": "ufuk-share", "schema_version": 1, "kind": "research",
                "title": "Python", "summary": "Public evidence.", "body": "Stable findings.",
                "url": "https://example.org/project", "tags": [],
                "sources": [{"title": "Original source", "url": "https://example.org/docs"}],
                "author": "Anonymous contributor", "license": "CC-BY-4.0",
                "created_at": "2026-11-01T12:00:00+00:00", "updated_at": "2026-11-01T12:00:00+00:00", **changes}

    def submit(self, packet=None, *, previous=None, store=None):
        store = store or self.store
        self.number += 1
        return store.gonder(self.author, {"packet": packet or self.packet(),
            "expected_version": previous["version"] if previous else 0,
            "idempotency_key": f"synthetic-request-{self.number:05d}"},
            previous["id"] if previous else None)["card"]

    def publish(self, pending=None, *, store=None):
        store = store or self.store
        pending = pending or self.submit(store=store)
        return store.karar(self.moderator, pending["id"],
            {"expected_version": pending["version"], "decision": "publish"})["card"]

    def search(self, query="python", **changes):
        return self.store.listele(query=query, kind="research", search_mode="ranked", **changes)

    def indexed(self):
        with self.store.baglanti() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM community_research_fts ORDER BY entry_id")]


@unittest.skipUnless(fts5_var(), "SQLite FTS5 is unavailable; strict fallback is tested separately.")
class ArastirmaIndeksiTestleri(IndeksKurulumu):
    def test_pending_private_and_project_text_never_enter_public_index(self):
        public = self.publish()
        before = self.search()
        self.submit(self.packet(title="Private Python nebula", body="Undisclosed details."))
        changed = self.submit(self.packet(title="Private Python nebula", body="New private body."), previous=public)
        self.publish(self.submit(self.packet(kind="project", title="Python project nebula")))
        self.assertEqual(len(self.indexed()), 1)
        self.assertEqual(self.search(), before)
        self.assertEqual(self.search("python nebula")["total"], 0)
        self.assertEqual(self.indexed()[0]["version"], public["published_version"])
        self.assertNotEqual(changed["packet"]["body"], self.store.yayini_oku(public["id"])["packet"]["body"])

    def test_publish_replaces_index_but_reject_preserves_existing_public_revision(self):
        public = self.publish()
        pending = self.submit(self.packet(body="Novel findings."), previous=public)
        rejected = self.store.karar(self.moderator, public["id"], {
            "expected_version": pending["version"], "decision": "reject", "note": "More sources needed."})["card"]
        self.assertEqual(self.indexed()[0]["version"], 1)
        self.assertEqual(self.search("novel")["total"], 0)
        replacement = self.publish(self.submit(self.packet(body="Novel findings."), previous=rejected))
        self.assertEqual(len(self.indexed()), 1)
        self.assertEqual(self.indexed()[0]["version"], replacement["published_version"])
        self.assertEqual(self.search("stable")["total"], 0)
        self.assertEqual(self.search("novel")["total"], 1)
        self.assertEqual(self.store.yayini_oku(public["id"], version=1)["packet"]["body"], "Stable findings.")

    def test_hide_and_both_withdrawal_paths_remove_public_index_rows(self):
        hidden = self.publish()
        withdrawn = self.publish()
        token = "a" * 43
        anonymous = self.store.inbox_gonder(token, {"packet": self.packet(), "consent": True,
            "idempotency_key": "anonymous-test-request"})["submission"]
        published_anonymous = self.publish(anonymous)
        self.assertEqual(len(self.indexed()), 3)
        self.store.yayindan_kaldir(self.moderator, hidden["id"],
            {"expected_version": hidden["version"], "note": "Incorrect evidence."})
        self.store.geri_cek(self.author, withdrawn["id"], {"expected_version": withdrawn["version"]})
        self.store.inbox_geri_cek(token, anonymous["id"], {"expected_version": published_anonymous["version"]})
        self.assertEqual(self.indexed(), [])
        self.assertEqual(self.search()["total"], 0)

    def test_index_failure_rolls_back_publication_and_withdrawal_together(self):
        pending = self.submit()
        with patch.object(self.store, "arama_indeksini_esitle", side_effect=RuntimeError("synthetic index failure")):
            with self.assertRaises(RuntimeError):
                self.publish(pending)
        self.assertEqual(self.store.listele()["total"], 0)
        self.assertEqual(self.indexed(), [])
        public = self.publish(pending)
        sync = self.store.arama_indeksini_esitle

        def fail_after_sync(connection, entry_id):
            sync(connection, entry_id)
            raise RuntimeError("synthetic failure after index delete")

        with patch.object(self.store, "arama_indeksini_esitle", side_effect=fail_after_sync):
            with self.assertRaises(RuntimeError):
                self.store.geri_cek(self.author, public["id"], {"expected_version": public["version"]})
        self.assertEqual(len(self.indexed()), 1)
        self.assertEqual(self.search()["total"], 1)

    def test_old_database_backfill_and_reopen_are_idempotent(self):
        public = self.publish()
        self.submit(self.packet(title="Secret never indexed"))
        with self.store.baglanti(write=True) as connection:
            connection.execute("DROP TABLE community_research_fts")
            connection.execute("DROP TABLE community_search_metadata")
        for _ in range(2):
            self.store = ToplulukDeposu(self.path)
            self.assertEqual(len(self.indexed()), 1)
            self.assertEqual(self.indexed()[0]["entry_id"], public["id"])
            self.assertEqual(self.search()["total"], 1)
        with self.store.baglanti() as connection:
            self.assertEqual(connection.execute("SELECT value FROM community_search_metadata WHERE name='index_version'").fetchone()[0], str(INDEX_VERSION))

    def test_version_change_rebuild_and_backup_restore_preserve_public_selection(self):
        public = self.publish()
        with self.store.baglanti(write=True) as connection:
            connection.execute("UPDATE community_search_metadata SET value='0' WHERE name='index_version'")
            connection.execute("UPDATE community_research_fts SET title='t626f677573'")
        self.store = ToplulukDeposu(self.path)
        self.assertEqual(self.search()["items"][0]["id"], public["id"])
        backup = self.directory / "backup.sqlite3"
        self.store.yedekle(backup)
        restored = self.directory / "restored.sqlite3"
        veritabani_kopyala(backup, restored, restore=True)
        self.assertEqual(ToplulukDeposu(restored).listele(query="python", kind="research", search_mode="ranked")["total"], 1)

    def test_coverage_precedes_field_weight_and_partial_match_is_explicit(self):
        partial = self.publish(self.submit(self.packet(title="Alpha beta", summary="", body="")))
        complete = self.publish(self.submit(self.packet(title="Reference", summary="", body="Alpha beta gamma")))
        result = self.search("alpha beta gamma")
        self.assertEqual([card["id"] for card in result["items"]], [complete["id"], partial["id"]])
        self.assertEqual(result["search"], {"mode": "ranked", "fallback": False})
        self.assertTrue(result["items"][0]["search_match"]["all_terms_matched"])
        self.assertEqual(result["items"][1]["search_match"]["missing_terms"], ["gamma"])
        self.assertEqual(result["items"][1]["search_match"]["answer_sufficiency"], "not_assessed")

    def test_title_weight_outranks_body_with_same_coverage(self):
        body = self.publish(self.submit(self.packet(title="Reference", summary="", body="Python", tags=[])))
        title = self.publish(self.submit(self.packet(title="Python", summary="", body="Reference", tags=[])))
        self.assertEqual([card["id"] for card in self.search()["items"]], [title["id"], body["id"]])

    def answers_ekle(self, card, answers):
        """Paket şeması answers alanını henüz tanımıyor (K1 kolu, v4); indeks şimdiden okur.

        Test, yayımlanmış paketi doğrudan kayıt satırında genişletip indeksi yeniden kurar.
        """
        with self.store.baglanti(write=True) as connection:
            row = connection.execute("SELECT r.packet,r.version FROM community_revisions r JOIN community_entries e "
                                     "ON e.id=r.entry_id AND r.version=e.published_revision WHERE e.id=?", (card["id"],)).fetchone()
            packet = json.loads(row["packet"])
            packet["answers"] = answers
            connection.execute("UPDATE community_revisions SET packet=? WHERE entry_id=? AND version=?",
                               (json.dumps(packet, ensure_ascii=False, sort_keys=True), card["id"], row["version"]))
            self.store.arama_indeksini_esitle(connection, card["id"])
        return card

    def test_field_weights_order_title_answers_tags_summary_body_limits(self):
        limits = self.publish(self.submit(self.packet(title="Reference", summary="", body="## Limits\nPython", tags=[])))
        body = self.publish(self.submit(self.packet(title="Reference", summary="", body="Python", tags=[])))
        summary = self.publish(self.submit(self.packet(title="Reference", summary="Python", body="", tags=[])))
        tags = self.publish(self.submit(self.packet(title="Reference", summary="", body="", tags=["Python"])))
        answers = self.answers_ekle(self.publish(self.submit(self.packet(title="Reference", summary="", body="", tags=[]))),
                                    ["Is Python ready?"])
        title = self.publish(self.submit(self.packet(title="Python", summary="", body="", tags=[])))
        result = self.search()
        self.assertEqual([card["id"] for card in result["items"]],
                         [title["id"], answers["id"], tags["id"], summary["id"], body["id"], limits["id"]])
        self.assertTrue(all(card["search_match"]["all_terms_matched"] for card in result["items"]))
        with self.store.baglanti() as connection:
            columns = [row["name"] for row in connection.execute("PRAGMA table_info(community_research_fts)")]
        self.assertEqual(columns, ["entry_id", "version", *INDEX_FIELDS])

    def test_answers_field_finds_question_wording_without_dictionary(self):
        report = self.publish(self.submit(self.packet(title="Python 3.14 free-threading", summary="Optional builds.",
                                                      body="Dependency checks and realistic speedups.", tags=[])))
        self.assertEqual(self.search("nogil python production")["total"], 0)
        self.answers_ekle(report, ["Is nogil Python production ready?"])
        result = self.search("nogil python production")
        self.assertEqual([card["id"] for card in result["items"]], [report["id"]])
        self.assertTrue(result["items"][0]["search_match"]["all_terms_matched"])

    def test_generation_note_in_body_does_not_make_report_a_candidate(self):
        noise = self.publish(self.submit(self.packet(title="GitHub release feed", summary="Polling and webhooks.",
            body="## Limits\nThis is a documentation synthesis generated with NVIDIA and edited after source review. "
                 "It does not measure API operating cost.", tags=[])))
        topical = self.publish(self.submit(self.packet(title="NVIDIA NIM APIs", summary="Access and limits.",
                                                       body="Free Developer Program API access.", tags=[])))
        self.assertEqual([card["id"] for card in self.search("nvidia api")["items"]], [topical["id"]])
        # Limits bölümündeki kapsam bilgisi silinmez; aranınca bulunur.
        self.assertEqual([card["id"] for card in self.search("github operating cost")["items"]], [noise["id"]])

    def test_old_four_column_index_is_rebuilt_on_open(self):
        public = self.publish()
        with self.store.baglanti(write=True) as connection:
            connection.execute("DROP TABLE community_research_fts")
            connection.execute("CREATE VIRTUAL TABLE community_research_fts USING fts5("
                               "entry_id UNINDEXED,version UNINDEXED,title,summary,tags,body,tokenize='ascii')")
            connection.execute("UPDATE community_search_metadata SET value='4' WHERE name='index_version'")
        reopened = ToplulukDeposu(self.path)
        with reopened.baglanti() as connection:
            columns = [row["name"] for row in connection.execute("PRAGMA table_info(community_research_fts)")]
            version = connection.execute("SELECT value FROM community_search_metadata WHERE name='index_version'").fetchone()["value"]
        self.assertEqual(columns, ["entry_id", "version", *INDEX_FIELDS])
        self.assertEqual(version, str(INDEX_VERSION))
        self.assertEqual([card["id"] for card in reopened.listele(query="python", kind="research", search_mode="ranked")["items"]],
                         [public["id"]])

    def test_spelling_variants_of_technical_names_match_in_both_directions(self):
        separated = self.publish(self.submit(self.packet(title="ROS 2 Jazzy rosbag2 replay", summary="", body="", tags=[])))
        combined = self.publish(self.submit(self.packet(title="h264 decoding on ros2", summary="", body="", tags=[])))
        self.assertEqual({card["id"] for card in self.search("ros2 replay")["items"]}, {separated["id"]})
        self.assertEqual({card["id"] for card in self.search("ROS 2 decoding")["items"]}, {combined["id"]})
        self.assertEqual({card["id"] for card in self.search("H 264 ros 2")["items"]}, {combined["id"]})
        self.assertEqual(self.search("ros 1 replay")["total"], 0)
        self.assertEqual(self.search("h265 decoding")["total"], 0)

    def test_required_technical_terms_and_exact_token_boundaries(self):
        wanted = self.publish(self.submit(self.packet(title="C++ v1.2 cache", summary="", body="")))
        self.publish(self.submit(self.packet(title="C v1.2 cache", summary="", body="")))
        self.publish(self.submit(self.packet(title="C++ v2.0 cache", summary="", body="")))
        self.publish(self.submit(self.packet(title="Frank caching", summary="", body="")))
        self.assertEqual([card["id"] for card in self.search("C++ v1.2 cache")["items"]], [wanted["id"]])
        self.assertEqual(self.search("rank")["total"], 0)
        for query in ("' OR 1=1 --", "%", "_", "x'); DROP TABLE community_entries; --", "python missing_xyz"):
            with self.subTest(query=query):
                self.assertEqual(self.search(query)["total"], 0)

    def test_exact_total_and_stable_pagination_beyond_one_hundred(self):
        identifiers = sorted(self.publish()["id"] for _ in range(105))
        first, second = self.search(limit=100), self.search(offset=100, limit=100)
        self.assertEqual(first["total"], 105)
        self.assertEqual(second["total"], 105)
        self.assertEqual([card["id"] for card in first["items"] + second["items"]], identifiers)
        self.assertEqual(self.search(offset=105)["items"], [])
        self.assertEqual(self.search(offset=9223372036854775807)["total"], 105)

    def test_nonpublic_scope_and_empty_query_keep_strict_listing(self):
        public = self.publish()
        private = self.submit(self.packet(title="Python pending"))
        all_result = self.store.listele()
        empty_ranked = self.search("")
        self.assertEqual(empty_ranked["items"], all_result["items"])
        self.assertEqual(empty_ranked["search"], {"mode": "all", "fallback": False})
        for scope in ({"user": self.author}, {"user": self.moderator, "review": True}):
            result = self.store.listele(query="python absent", kind="research", search_mode="ranked", **scope)
            self.assertEqual(result["total"], 0)
            self.assertEqual(result["search"]["mode"], "all")
        self.assertNotIn("search", self.store.listele(query="python"))
        self.assertEqual(self.search()["items"][0]["id"], public["id"])
        self.assertNotIn(private["id"], [card["id"] for card in self.search()["items"]])
        for mode in (True, None, [], "approximate"):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.store.listele(search_mode=mode)

    def test_count_page_and_cards_share_one_read_snapshot(self):
        public = self.publish()
        original = self.store.kart
        changed = []

        def hide_after_selection(connection, entry_id, **kwargs):
            if not changed:
                changed.append(True)
                self.store.yayindan_kaldir(self.moderator, entry_id,
                    {"expected_version": public["version"], "note": "Concurrent hide."})
            return original(connection, entry_id, **kwargs)

        with patch.object(self.store, "kart", side_effect=hide_after_selection):
            result = self.search()
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["id"], public["id"])
        self.assertEqual(self.search()["total"], 0)


class ArastirmaIndeksiGeriDonusTestleri(IndeksKurulumu):
    def test_missing_fts5_is_explicit_strict_fallback_without_hiding_other_errors(self):
        public = self.publish()
        with patch.object(ToplulukDeposu, "fts5_kur", side_effect=sqlite3.OperationalError("no such module: fts5")):
            limited = ToplulukDeposu(self.path)
        self.assertFalse(limited.search_index_available)
        result = limited.listele(query="python", kind="research", search_mode="ranked")
        self.assertEqual(result["search"], {"mode": "all", "fallback": True})
        self.assertEqual(result["items"][0]["id"], public["id"])
        self.assertEqual(limited.listele(query="python absent", kind="research", search_mode="ranked")["total"], 0)
        self.assertEqual(result["items"][0]["search_match"]["answer_sufficiency"], "not_assessed")
        with patch.object(ToplulukDeposu, "fts5_kur", side_effect=sqlite3.OperationalError("database is locked")):
            with self.assertRaisesRegex(sqlite3.OperationalError, "locked"):
                ToplulukDeposu(self.path)


if __name__ == "__main__":
    unittest.main()
