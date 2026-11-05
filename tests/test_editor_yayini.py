"""Editoryal yayının incelenen dosyaya bağlılığını ve kaldırılan raporu yeniden açmamasını sınar."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from mcp_topluluk import contribution_packet
from radar.editor_yayini import EDITOR_ID, incelenen_dosyayi_yayinla
from radar.topluluk_depo import ToplulukDeposu
from tests.test_community_mcp import report


class EditorYayiniTestleri(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.database = self.root / "community.sqlite3"
        self.store = ToplulukDeposu(self.database)
        self.path = self.root / "reviewed.json"
        self.packet = contribution_packet(**report())

    def save(self):
        raw = json.dumps(self.packet, ensure_ascii=False).encode("utf-8")
        self.path.write_bytes(raw)
        return hashlib.sha256(raw).hexdigest()

    def test_review_hash_required_and_sensitive_content_never_creates_editor(self):
        digest = self.save()
        with self.assertRaises(ValueError):
            incelenen_dosyayi_yayinla(self.database, self.path, "0" * 64, "Sources checked")
        self.packet["body"] = "Private contact: private@example.org"
        digest = self.save()
        with self.assertRaises(ValueError):
            incelenen_dosyayi_yayinla(self.database, self.path, digest, "Sources checked")
        with self.store.baglanti() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM community_accounts").fetchone()[0], 0)

    def test_published_once_without_login_and_withdrawal_is_not_reversed(self):
        digest = self.save()
        result = incelenen_dosyayi_yayinla(self.database, self.path, digest, "Original sources checked")
        duplicate = incelenen_dosyayi_yayinla(self.database, self.path, digest, "Original sources checked")
        self.assertEqual(result["card"]["status"], "published")
        self.assertEqual(result["card"]["id"], duplicate["card"]["id"])
        self.assertTrue(duplicate["duplicate"])
        with self.store.baglanti() as connection:
            actor = dict(connection.execute("SELECT * FROM community_accounts WHERE id=?", (EDITOR_ID,)).fetchone())
            self.assertEqual(actor["active"], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM community_sessions").fetchone()[0], 0)
            current = self.store.kart(connection, result["card"]["id"])
        self.store.geri_cek(actor, current["id"], {"expected_version": current["version"]})
        with self.assertRaises(ValueError):
            incelenen_dosyayi_yayinla(self.database, self.path, digest, "Original sources checked")
        self.assertEqual(self.store.listele()["total"], 0)


if __name__ == "__main__":
    unittest.main()
