"""Özel itirazın tam sürüm, fiş, moderasyon, kapasite, eşzamanlılık ve kamu sınırlarını sınar."""

import copy
import ctypes
import hashlib
import json
import os
import secrets
import sqlite3
import stat
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from radar import arastirma_itirazlari as module
from radar.arastirma_itirazlari import ItirazDeposu, ItirazHatasi


class ArastirmaItirazlariTestleri(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "objections" / "store.sqlite3"
        self.now = 1800000000
        self.research_id = "a" * 32
        self.moderator = {"id": "b" * 32, "role": "moderator", "username": "test-moderator", "display_name": "Test"}
        self.cards = {(self.research_id, version): {"id": self.research_id, "version": version, "status": "published", "packet": {"kind": "research"}}
                      for version in (1, 2, 3)}
        self.store = self.new_store()

    def new_store(self):
        return ItirazDeposu(self.path, published_report=lambda identifier, version: copy.deepcopy(self.cards[(identifier, version)]),
                            clock=lambda: self.now)

    def payload(self, **changes):
        result = {"research_id": self.research_id, "version": 1, "claim": "PRIVATE_CLAIM_MARKER: sürüm davranışı.",
                  "reason": "PRIVATE_REASON_MARKER: kamu belgesi farklı bir sınır gösteriyor.", "consent": True,
                  "sources": [{"title": "Public documentation", "url": "https://example.org/docs"}]}
        result.update(changes)
        return result

    def error(self, status, operation, *args, **kwargs):
        with self.assertRaises(ItirazHatasi) as raised:
            operation(*args, **kwargs)
        self.assertEqual(raised.exception.status, status)
        return str(raised.exception)

    def submit(self, **changes):
        receipt = secrets.token_urlsafe(32)
        result = self.store.gonder(receipt, self.payload(**changes))["objection"]
        return receipt, result

    def decision(self, row, status, **changes):
        payload = {"expected_revision": row["revision"], "research_id": row["research_id"], "version": row["version"], "status": status}
        payload.update(changes)
        return self.store.karar(self.moderator, row["id"], payload)

    def accept(self, row):
        reviewed = self.decision(row, "in_review")
        return self.decision(reviewed, "accepted", public_summary="Düzeltilen kamu bulgusu; yeni sürümde kapsam daraltıldı.",
                             correction={"research_id": self.research_id, "version": 2})

    def test_init_and_rejected_input_do_not_create_directory_or_database(self):
        self.assertFalse(self.path.parent.exists())
        self.error(401, self.store.gonder, "invalid", self.payload())
        self.error(422, self.store.gonder, secrets.token_urlsafe(32), self.payload(consent=False))
        self.error(403, self.store.inceleme_listele, {"role": "contributor"})
        self.assertFalse(self.path.parent.exists())

    def test_private_pending_receipt_never_enters_public_views_or_database(self):
        receipt, row = self.submit()
        self.assertEqual(row["status"], "pending")
        self.assertTrue({"claim", "reason", "sources", "receipt", "receipt_hash", "payload_hash"}.isdisjoint(row))
        detail = self.store.oku(receipt)
        self.assertEqual(detail["reason"], self.payload()["reason"])
        public = self.store.kamusal_ozetler(self.research_id, 1)
        self.assertEqual(public["items"], [])
        listing = self.store.inceleme_listele(self.moderator)
        encoded = json.dumps([public, row, listing])
        self.assertNotIn("PRIVATE_REASON_MARKER", encoded)
        self.assertNotIn("PRIVATE_CLAIM_MARKER", encoded)
        self.assertNotIn(receipt, encoded)
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("PRAGMA application_id").fetchone()[0], module.APPLICATION_ID)
            tables = {record[0] for record in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertEqual(tables, {"research_objections", "research_objection_decisions"})
        self.assertNotIn(receipt.encode(), self.path.read_bytes())
        self.assertNotIn(receipt, json.dumps(detail))

    def test_file_permissions_are_private_on_the_current_platform(self):
        self.submit()
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o700)
            return
        from ctypes import wintypes
        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        get = advapi.GetFileSecurityW
        get.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        get.restype = wintypes.BOOL
        convert = advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW
        convert.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.ULONG)]
        convert.restype = wintypes.BOOL
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        for target in (self.path, self.path.parent):
            size = wintypes.DWORD()
            get(str(target), 4, None, 0, ctypes.byref(size))
            descriptor = ctypes.create_string_buffer(size.value)
            self.assertTrue(get(str(target), 4, descriptor, size.value, ctypes.byref(size)))
            text = ctypes.c_void_p()
            self.assertTrue(convert(descriptor, 1, 4, ctypes.byref(text), None))
            try:
                sddl = ctypes.wstring_at(text)
                self.assertTrue(sddl.startswith("D:P"))
                for sid in ("SY", "BA", "OW"):
                    self.assertIn(";;;" + sid + ")", sddl)
                self.assertNotIn(";;;WD)", sddl)
                self.assertNotIn(";;;BU)", sddl)
            finally:
                kernel.LocalFree(text)

    def test_repeat_is_idempotent_but_changed_content_or_version_is_rejected(self):
        receipt = secrets.token_urlsafe(32)
        payload = self.payload()
        first = self.store.gonder(receipt, payload)
        self.now += 3600
        again = self.store.gonder(receipt, copy.deepcopy(payload))
        self.assertEqual(again, {**first, "duplicate": True})
        for changed in (self.payload(reason="Farklı gerekçe."), self.payload(version=2)):
            self.error(409, self.store.gonder, receipt, changed)
        self.assertEqual(self.store.inceleme_listele(self.moderator)["total"], 1)
        self.error(422, self.store.gonder, secrets.token_urlsafe(32), self.payload(reason="Contact person@example.test"))
        new_receipt = secrets.token_urlsafe(32)
        self.error(422, self.store.gonder, new_receipt, self.payload(reason=new_receipt))

    def test_receipt_and_role_access_do_not_cross_other_submissions(self):
        receipt, row = self.submit()
        self.error(404, self.store.oku, secrets.token_urlsafe(32))
        self.error(404, self.store.geri_cek, secrets.token_urlsafe(32), 1)
        for user in (None, {}, {"id": "c" * 32, "role": "contributor"}, {"id": "invalid", "role": "moderator"}):
            self.error(403, self.store.inceleme_oku, user, row["id"])
            self.error(403, self.store.karar, user, row["id"], {})
        self.assertEqual(self.store.oku(receipt)["revision"], 1)

    def test_public_callback_failure_and_tombstones_fail_closed(self):
        failures = [None, {"available": False}, self.cards[(self.research_id, 2)],
                    {**self.cards[(self.research_id, 1)], "status": "withdrawn"},
                    {**self.cards[(self.research_id, 1)], "available": False},
                    {**self.cards[(self.research_id, 1)], "version": True}]
        for card in failures:
            store = ItirazDeposu(self.path, published_report=lambda *_: card, clock=lambda: self.now)
            self.error(409, store.gonder, secrets.token_urlsafe(32), self.payload())
        def failed(*_):
            raise RuntimeError("PRIVATE_CALLBACK_DETAIL")
        store = ItirazDeposu(self.path, published_report=failed, clock=lambda: self.now)
        message = self.error(409, store.gonder, secrets.token_urlsafe(32), self.payload())
        self.assertNotIn("PRIVATE_CALLBACK_DETAIL", message)
        self.assertEqual(self.store.inceleme_listele(self.moderator)["total"], 0)

    def test_moderation_needs_exact_binding_and_published_new_correction(self):
        _, row = self.submit()
        reviewed = self.decision(row, "in_review")
        for changes in ({"research_id": "d" * 32}, {"version": 2}):
            self.error(409, self.decision, reviewed, "rejected", **changes)
        for correction in ({"research_id": self.research_id, "version": 1}, {"research_id": self.research_id, "version": 999}):
            self.error(409, self.decision, reviewed, "accepted", public_summary="Kamu düzeltme özeti.", correction=correction)
        accepted = self.decision(reviewed, "accepted", public_summary="Kamuya uygun ayrı inceleme özeti.",
                                 correction={"research_id": self.research_id, "version": 2})
        self.assertEqual(accepted["revision"], 3)
        public = self.store.kamusal_ozetler(self.research_id, 1)
        self.assertEqual(public["items"][0]["correction"], {"research_id": self.research_id, "version": 2})
        self.assertFalse(public["items"][0]["withdrawn"])
        for forbidden in ("PRIVATE_REASON_MARKER", "PRIVATE_CLAIM_MARKER", "sources", "receipt", "moderator", "payload_hash"):
            self.assertNotIn(forbidden, json.dumps(public))
        self.error(409, self.decision, reviewed, "rejected")
        self.error(409, self.decision, accepted, "in_review")

    def test_different_research_correction_requires_exact_parent_lineage(self):
        _, row = self.submit()
        reviewed = self.decision(row, "in_review")
        identifier = "c" * 32
        card = {"id": identifier, "version": 1, "status": "published", "packet": {"kind": "research"}}
        self.cards[(identifier, 1)] = card
        correction = {"research_id": identifier, "version": 1}
        for lineage in (None, {}, {"parents": None}, {"parents": [{"research_id": self.research_id, "version": 2}]},
                        {"parents": [{"research_id": self.research_id, "version": True}]}):
            card["packet"]["lineage"] = lineage
            self.error(409, self.decision, reviewed, "accepted", public_summary="Ayrı araştırma düzeltmesi.", correction=correction)
        card["packet"]["lineage"] = {"parents": [{"research_id": self.research_id, "version": 1}]}
        self.assertEqual(self.decision(reviewed, "accepted", public_summary="Ayrı araştırma düzeltmesi.", correction=correction)["status"], "accepted")

    def test_withdrawal_purges_private_text_but_keeps_accepted_correction_trace(self):
        receipt, row = self.submit()
        accepted = self.accept(row)
        withdrawn = self.store.geri_cek(receipt, accepted["revision"])
        self.assertEqual(withdrawn["status"], "withdrawn")
        private = self.store.oku(receipt)
        self.assertEqual((private["claim"], private["reason"], private["sources"]), ("", "", []))
        public = self.store.kamusal_ozetler(self.research_id, 1)
        self.assertTrue(public["items"][0]["withdrawn"])
        self.assertTrue(public["items"][0]["summary"])
        self.assertEqual(public["items"][0]["correction"]["version"], 2)
        repeated = self.store.gonder(receipt, self.payload())
        self.assertTrue(repeated["duplicate"])
        self.assertEqual(repeated["objection"]["status"], "withdrawn")
        another_receipt, pending = self.submit()
        self.store.geri_cek(another_receipt, pending["revision"])
        self.assertEqual(self.store.kamusal_ozetler(self.research_id, 1)["total"], 1)

    def test_retention_does_not_reset_receipt_or_remove_public_correction(self):
        receipt, row = self.submit()
        self.accept(row)
        other_receipt, _ = self.submit()
        self.now += module.RETENTION_SECONDS
        self.assertEqual(self.store.oku(other_receipt)["status"], "expired")
        self.assertEqual(self.store.oku(other_receipt)["reason"], "")
        self.assertTrue(self.store.oku(receipt)["private_purged"])
        self.assertEqual(self.store.kamusal_ozetler(self.research_id, 1)["total"], 1)
        self.assertEqual(self.store.gonder(other_receipt, self.payload())["objection"]["status"], "expired")
        self.error(409, self.store.gonder, other_receipt, self.payload(reason="Yeni gerekçe."))

    def test_caps_and_sliding_daily_limits_do_not_block_identical_retries(self):
        for name in ("TOTAL_LIMIT", "OPEN_LIMIT", "DAILY_LIMIT", "REPORT_DAILY_LIMIT"):
            with self.subTest(limit=name):
                self.path = self.root / name / "store.sqlite3"
                self.store = self.new_store()
                with patch.object(module, name, 1):
                    receipt = secrets.token_urlsafe(32)
                    self.store.gonder(receipt, self.payload())
                    self.error(429, self.store.gonder, secrets.token_urlsafe(32), self.payload())
                    self.assertTrue(self.store.gonder(receipt, self.payload())["duplicate"])
                    if name in {"DAILY_LIMIT", "REPORT_DAILY_LIMIT"}:
                        self.now += 86400
                        self.assertFalse(self.store.gonder(secrets.token_urlsafe(32), self.payload())["duplicate"])

    def test_concurrent_first_use_same_receipt_creates_exactly_one_record(self):
        receipt = secrets.token_urlsafe(32)
        barrier = threading.Barrier(6)
        def submit(_):
            store = self.new_store()
            barrier.wait(timeout=10)
            return store.gonder(receipt, self.payload())
        with ThreadPoolExecutor(max_workers=6) as executor:
            results = list(executor.map(submit, range(6)))
        self.assertEqual(len({result["objection"]["id"] for result in results}), 1)
        self.assertEqual(sum(not result["duplicate"] for result in results), 1)

    def test_report_rate_is_version_scoped_and_hidden_source_cannot_be_accepted(self):
        with patch.object(module, "REPORT_DAILY_LIMIT", 1):
            _, row = self.submit()
            self.error(429, self.store.gonder, secrets.token_urlsafe(32), self.payload())
            self.assertFalse(self.store.gonder(secrets.token_urlsafe(32), self.payload(version=2))["duplicate"])
        reviewed = self.decision(row, "in_review")
        self.cards[(self.research_id, 1)]["status"] = "hidden"
        self.error(409, self.decision, reviewed, "accepted", public_summary="Kamu özeti.",
                   correction={"research_id": self.research_id, "version": 2})
        self.assertEqual(self.store.inceleme_oku(self.moderator, row["id"])["status"], "in_review")

    def test_private_retention_uses_delete_journal_and_erases_current_database_text(self):
        receipt, _ = self.submit()
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        self.now += module.RETENTION_SECONDS
        self.store.oku(receipt)
        self.assertNotIn(b"PRIVATE_REASON_MARKER", self.path.read_bytes())
        self.assertNotIn(b"PRIVATE_CLAIM_MARKER", self.path.read_bytes())
        self.assertFalse(self.path.with_name(self.path.name + "-wal").exists())

    def test_concurrent_capacity_and_review_revision_are_atomic(self):
        barrier = threading.Barrier(5)
        def submit(_):
            barrier.wait(timeout=10)
            try:
                return self.store.gonder(secrets.token_urlsafe(32), self.payload())["objection"]
            except ItirazHatasi as exc:
                return exc.status
        with patch.object(module, "OPEN_LIMIT", 1), ThreadPoolExecutor(max_workers=5) as executor:
            results = list(executor.map(submit, range(5)))
        self.assertEqual(results.count(429), 4)
        row = next(result for result in results if isinstance(result, dict))
        reviewed = self.decision(row, "in_review")
        barrier = threading.Barrier(2)
        def decide(_):
            barrier.wait(timeout=10)
            try:
                return self.decision(reviewed, "rejected")["status"]
            except ItirazHatasi as exc:
                return exc.status
        with ThreadPoolExecutor(max_workers=2) as executor:
            decisions = list(executor.map(decide, range(2)))
        self.assertCountEqual(decisions, ["rejected", 409])

    def test_audit_failure_rolls_back_status_and_public_summary(self):
        receipt, row = self.submit()
        reviewed = self.decision(row, "in_review")
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON research_objection_decisions BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
        self.error(503, self.decision, reviewed, "accepted", public_summary="Kamu özeti.",
                   correction={"research_id": self.research_id, "version": 2})
        self.assertEqual(self.store.oku(receipt)["status"], "in_review")
        self.assertEqual(self.store.oku(receipt)["revision"], 2)
        self.assertEqual(self.store.kamusal_ozetler(self.research_id, 1)["items"], [])

    def test_unrelated_directory_and_foreign_database_are_not_reused(self):
        self.path.parent.mkdir()
        unrelated = self.path.parent / "keep.txt"
        unrelated.write_text("preserve", encoding="utf-8")
        self.error(409, self.store.gonder, secrets.token_urlsafe(32), self.payload())
        self.assertFalse(self.path.exists())
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "preserve")
        foreign_path = self.root / "foreign" / "store.sqlite3"
        foreign_path.parent.mkdir()
        with closing(sqlite3.connect(foreign_path)) as connection, connection:
            connection.execute("CREATE TABLE personal_data(value TEXT)")
            connection.execute("INSERT INTO personal_data VALUES ('keep private')")
        before = hashlib.sha256(foreign_path.read_bytes()).hexdigest()
        store = ItirazDeposu(foreign_path, published_report=self.store.published_report, clock=lambda: self.now)
        self.error(409, store.gonder, secrets.token_urlsafe(32), self.payload())
        self.assertEqual(hashlib.sha256(foreign_path.read_bytes()).hexdigest(), before)

    def test_linked_database_is_rejected_without_modifying_target(self):
        self.submit()
        target = self.path
        linked_directory = self.root / "linked"
        linked_directory.mkdir()
        linked = linked_directory / "store.sqlite3"
        try:
            os.link(target, linked)
        except OSError:
            self.skipTest("Hard links are unavailable on this filesystem")
        before = target.read_bytes()
        store = ItirazDeposu(linked, published_report=self.store.published_report, clock=lambda: self.now)
        self.error(409, store.gonder, secrets.token_urlsafe(32), self.payload())
        self.assertEqual(target.read_bytes(), before)

    def test_validation_limits_and_private_material_fail_without_echoing_input(self):
        invalid = [{"claim": "x" * 801}, {"reason": "x" * 2001}, {"sources": [{}] * 4}, {"sources": "https://example.org"},
                   {"claim": "\ud800"}, {"version": True}, {"consent": 1}, {"email": "PRIVATE_VALUE"},
                   {"sources": [{"title": "Doc", "url": "http://example.org/docs"}]},
                   {"sources": [{"title": "Doc", "url": "https://localhost/private"}]},
                   {"sources": [{"title": "Doc", "url": "https://example.org/?" + "token" + "=PRIVATE_VALUE"}]}]
        for changed in invalid:
            with self.subTest(changed=repr(changed)[:80]):
                message = self.error(422, self.store.gonder, secrets.token_urlsafe(32), self.payload(**changed))
                self.assertNotIn("PRIVATE_VALUE", message)
        self.assertFalse(self.path.exists())

    def test_listing_pagination_is_bounded_and_does_not_expose_private_text(self):
        for _ in range(3):
            _, row = self.submit()
            self.accept(row)
        public = self.store.kamusal_ozetler(self.research_id, 1, offset=1, limit=1)
        self.assertEqual(public["total"], 3)
        self.assertEqual(len(public["items"]), 1)
        private = self.store.inceleme_listele(self.moderator, "accepted", offset=2, limit=1)
        self.assertEqual(private["total"], 3)
        self.assertEqual(len(private["items"]), 1)
        self.assertNotIn("PRIVATE_REASON_MARKER", json.dumps(private))
        for args in ({"offset": -1}, {"limit": 101}, {"limit": True}):
            self.error(422, self.store.kamusal_ozetler, self.research_id, 1, **args)
        self.cards[(self.research_id, 1)]["status"] = "withdrawn"
        self.error(409, self.store.kamusal_ozetler, self.research_id, 1)


if __name__ == "__main__":
    unittest.main()
