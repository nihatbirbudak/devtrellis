"""Public aliases keep existing storage, filters, consent checks and deduplication intact."""

import copy
import hashlib
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError

from radar.depo import Depo
from radar.gelistirici_alani import GelistiriciAlani, paket_dogrula, paket_metni
from radar.katalog import kartlari_listele
from radar.kesif import kesif_kaynaklari
from radar.konular import KONULAR, KONU_TAKMA_ADLARI, konu, konu_kimligi, konulari_listele
from radar.proje_profilleri import profiller
from radar.sunucu import ArastirmaTalebi, KayitliArama, Konu
from radar.topluluk_api import uygulama


def packet(version=1, topic="jarvis"):
    value = {
        "schema": "ufuk-share", "schema_version": version, "kind": "research",
        "title": "Public research", "summary": "A public summary.", "body": "A source-backed finding.",
        "url": "https://example.org/research", "tags": ["automation"],
        "sources": [{"title": "Public source", "url": "https://example.org/docs"}],
        "author": "Contributor", "license": "CC-BY-4.0",
        "created_at": "2026-11-01T12:00:00+00:00", "updated_at": "2026-11-01T12:00:00+00:00",
    }
    if version >= 2:
        value["provenance"] = {
            "model": "test-model", "provider": "unknown", "generated_at": None,
            "question": "What is covered?", "topic": topic, "source_url": "https://example.org/docs",
            "evidence_status": "unverified", "language": "en", "modified": False,
        }
    if version == 3:
        value["lineage"] = {"parents": [{"research_id": "a" * 32, "version": 1}],
                            "change_summary": "Adds a public finding."}
    return value


class PublicAliasTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def sources(self):
        return [{"id": topic, "name": f"Public {topic}", "topic": topic, "enabled": True,
                 "interval_hours": 24, "benefit": "Public research"}
                for topic in KONU_TAKMA_ADLARI.values()]

    def seed(self, store):
        for topic in KONU_TAKMA_ADLARI.values():
            store.kaydet(topic, [{"url": f"https://example.org/{topic}", "title": f"Public finding {topic}"}])

    def test_registry_exposes_aliases_once_and_keeps_scheduled_topics(self):
        rows = konulari_listele()
        self.assertEqual(len(rows), 8)
        self.assertEqual({row["id"] for row in rows}, set(KONULAR))
        self.assertEqual({row["topic"] for row in profiller()}, set(KONULAR))
        self.assertEqual({row["topic"] for row in kesif_kaynaklari()}, set(KONULAR))
        for alias, canonical in KONU_TAKMA_ADLARI.items():
            row = next(row for row in rows if row["id"] == canonical)
            self.assertEqual(row["public_id"], alias)
            self.assertEqual(row["aliases"], [alias])
            self.assertEqual(konu(alias), konu(canonical))
            self.assertNotIn(alias, KONULAR)
        rows[1]["aliases"].append("not-a-real-alias")
        self.assertNotIn("not-a-real-alias", konulari_listele()[1]["aliases"])
        self.assertEqual(konu_kimligi("unknown"), "unknown")
        with self.assertRaises(KeyError):
            konu("unknown")

    def test_public_topics_and_feed_accept_aliases_without_new_topics(self):
        app = uygulama(self.root / "community.sqlite3", public_url="https://community.example.org",
                       sources=self.sources())
        self.seed(app.state.feed_depo)
        with TestClient(app, base_url="https://community.example.org") as client:
            response = client.get("/api/topics")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), konulari_listele())
            self.assertNotIn("set-cookie", response.headers)
            for alias, canonical in KONU_TAKMA_ADLARI.items():
                old = client.get("/api/community/feed", params={"interest": canonical})
                new = client.get("/api/community/feed", params={"interest": alias})
                self.assertEqual(old.status_code, 200)
                self.assertEqual(new.json(), old.json())
                self.assertEqual(new.json()["total"], 1)
                self.assertEqual(new.json()["items"][0]["topic"], canonical)
            # This endpoint also accepts free text; an unknown interest remains a normal no-match query.
            self.assertEqual(client.get("/api/community/feed", params={"interest": "missing phrase"}).json()["total"], 0)

    def test_catalog_alias_filter_reads_existing_rows(self):
        store = Depo(self.root / "catalog.sqlite3", self.sources())
        self.seed(store)
        for alias, canonical in KONU_TAKMA_ADLARI.items():
            self.assertEqual(kartlari_listele(store, topic=alias), kartlari_listele(store, topic=canonical))
            self.assertEqual(kartlari_listele(store, topic=alias)["total"], 1)
        with store.baglanti() as connection:
            self.assertEqual({row[0] for row in connection.execute("SELECT topic FROM items")},
                             set(KONU_TAKMA_ADLARI.values()))
        with self.assertRaises(ValueError):
            kartlari_listele(store, topic="unknown")

    def test_local_request_models_canonicalize_before_domain_validation(self):
        validator = TypeAdapter(Konu)
        for alias, canonical in KONU_TAKMA_ADLARI.items():
            self.assertEqual(validator.validate_python(alias), canonical)
            self.assertEqual(validator.validate_python(canonical), canonical)
            request = ArastirmaTalebi(question="A public question?", topic=alias, idempotency_key="public-alias-test")
            self.assertEqual(request.topic, canonical)
            self.assertEqual(KayitliArama(name="Saved", topic=alias).topic, canonical)
        self.assertIsNone(KayitliArama(name="Unfiltered").topic)
        for invalid in ("unknown", [], None, True):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                validator.validate_python(invalid)

    def test_both_schema_names_have_identical_canonical_bytes_for_all_versions(self):
        for version in (1, 2, 3):
            for alias, canonical in KONU_TAKMA_ADLARI.items():
                original = packet(version, canonical)
                alternate = copy.deepcopy(original)
                alternate["schema"] = "devtrellis-share"
                if version >= 2:
                    alternate["provenance"]["topic"] = alias
                before = copy.deepcopy(alternate)
                self.assertEqual(paket_dogrula(original), original)
                self.assertEqual(paket_metni(paket_dogrula(alternate)), paket_metni(original))
                self.assertEqual(alternate, before, "validation must not mutate the caller's package")

    def test_new_schema_keeps_all_security_and_version_checks(self):
        invalids = []
        for changes in ({"private_context": "not accepted"}, {"schema_version": True},
                        {"author": ""}, {"sources": [{"title": "Private", "url": "http://127.0.0.1/"}]},
                        {"updated_at": "2025-01-01T00:00:00+00:00"}, {"body": "x" * 12001}):
            invalids.append({**packet(), **changes})
        missing_provenance = packet(2)
        missing_provenance.pop("provenance")
        invalids.append(missing_provenance)
        wrong_version = packet(2)
        wrong_version["schema_version"] = 1
        invalids.append(wrong_version)
        missing_lineage = packet(3)
        missing_lineage.pop("lineage")
        invalids.append(missing_lineage)
        for original in invalids:
            for schema in ("ufuk-share", "devtrellis-share"):
                value = {**original, "schema": schema}
                with self.subTest(schema=schema, fields=list(original)), self.assertRaises(ValueError):
                    paket_dogrula(value)
        with self.assertRaises(ValueError):
            paket_dogrula({**packet(), "schema": "other-share"})

    def test_existing_import_hash_and_dedup_survive_schema_and_topic_aliases(self):
        area = GelistiriciAlani(Depo(self.root / "imports.sqlite3", []))
        original = packet(2)
        expected_hash = hashlib.sha256(paket_metni(original).encode("utf-8")).hexdigest()
        first = area.ice_aktar({"packet": original})
        alternate = copy.deepcopy(original)
        alternate["schema"] = "devtrellis-share"
        alternate["provenance"]["topic"] = "home_automation"
        repeated = area.ice_aktar({"packet": alternate})
        self.assertFalse(first["duplicate"])
        self.assertTrue(repeated["duplicate"])
        self.assertEqual(repeated["entry"], first["entry"])
        with area.depo.baglanti() as connection:
            rows = connection.execute("SELECT import_hash FROM platform_entries").fetchall()
        self.assertEqual([row[0] for row in rows], [expected_hash])


if __name__ == "__main__":
    unittest.main()
