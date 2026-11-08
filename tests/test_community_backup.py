"""Real store recovery, committed WAL data, privacy, paths and failure cleanup."""

import hashlib
import importlib.util
import json
import os
import secrets
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from radar.arastirma_itirazlari import ItirazDeposu, RETENTION_SECONDS
from radar.depo import Depo
from radar.katki_v4_depo import KatkiDeposu
from radar.kaynak_hafizasi import KaynakHafizasi
from radar.topluluk_depo import ToplulukDeposu
from radar.topluluk_api import IstekSiniri

try:
    from cryptography.fernet import Fernet
except ImportError:
    Fernet = None

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("community_backup", ROOT / "araclar" / "community-backup.py")
backup_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backup_module)


@unittest.skipIf(Fernet is None, "Install requirements-backup.txt to run backup acceptance")
class BackupTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.now = int(time.time())
        self.community = ToplulukDeposu(self.root / "live-community" / "community.sqlite3")
        self.community.oturum_ac()
        self.feed = Depo(self.root / "live-feed" / "feed.sqlite3", [])
        self.memory = KaynakHafizasi(self.root / "live-memory" / "memory.sqlite3", [])
        self.rate_key = b"synthetic-backup-rate-key-32-bytes"
        self.rate_path = self.root / "live-rate" / "limits.sqlite3"
        self.rate = IstekSiniri(clock=lambda: self.now, db_path=self.rate_path, key=self.rate_key)
        self.assertTrue(self.rate.izin("192.0.2.1", "fixture", 1, period=86400))
        self.objections = ItirazDeposu(self.root / "live-objections" / "objections.sqlite3",
                                      published_report=self.published, clock=lambda: self.now)
        self.receipt = secrets.token_urlsafe(32)
        self.objections.gonder(self.receipt, {"research_id": "a" * 32, "version": 1,
            "claim": "A public claim", "reason": "PRIVATE_OBJECTION_REASON", "consent": True, "sources": []})
        with self.memory.baglanti(write=True) as connection:
            connection.execute("INSERT INTO source_memory_budget VALUES(?,7)", (self.now // 86400,))
        # K1 açık sunucu: v4 katkı deposu da şifreli yedeğin rolüdür.
        self.k1 = KatkiDeposu(self.root / "live-k1" / "k1.sqlite3", origin="https://community.example.org")
        self.paths = {"community": self.community.path, "feed": self.feed.path,
                      "source_memory": self.memory.path, "objections": self.objections.path, "rate_limits": self.rate_path,
                      "k1": self.k1.path}
        self.environment = patch.dict(os.environ, {"DEVTRELLIS_BACKUP_KEY": Fernet.generate_key().decode()})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    @staticmethod
    def published(identifier, version):
        return {"id": identifier, "version": version, "status": "published", "packet": {"kind": "research"}}

    def backup(self, **kwargs):
        return backup_module.backup(self.paths, self.root / "encrypted", writers_stopped=True, **kwargs)

    def restore(self):
        return backup_module.restore(self.root / "encrypted", self.root / "restored")

    def test_real_five_store_wal_recovery_with_private_directories_budgets_and_retention(self):
        # Hold a real WAL connection open: committed pages have not been checkpointed.
        with closing(sqlite3.connect(self.feed.path)) as writer:
            writer.execute("INSERT INTO sources(id,config) VALUES('wal-sentinel','{}')")
            writer.commit()
            self.assertTrue(Path(str(self.feed.path) + "-wal").exists())
            manifest = self.backup()
            self.assertEqual(writer.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(set(manifest["roles"]), set(self.paths))
        for role in self.paths:
            data = (self.root / "encrypted" / (role + ".sqlite3.enc")).read_bytes()
            self.assertNotIn(b"SQLite format 3", data)
            self.assertNotIn(b"PRIVATE_OBJECTION_REASON", data)
            self.assertNotIn(self.receipt.encode(), data)
        restored = self.restore()
        community = ToplulukDeposu(restored / backup_module.ROLES["community"])
        with community.baglanti() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM community_sessions").fetchone()[0], 0)
        with self.community.baglanti() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM community_sessions").fetchone()[0], 1)
        feed = Depo(restored / backup_module.ROLES["feed"], [])
        with feed.baglanti() as connection:
            self.assertIsNotNone(connection.execute("SELECT id FROM sources WHERE id='wal-sentinel'").fetchone())
        memory = KaynakHafizasi(restored / backup_module.ROLES["source_memory"], [])
        with memory.baglanti() as connection:
            self.assertEqual(connection.execute("SELECT attempts FROM source_memory_budget").fetchone()[0], 7)
        rate = IstekSiniri(clock=lambda: self.now, db_path=restored / backup_module.ROLES["rate_limits"], key=self.rate_key)
        self.assertFalse(rate.izin("192.0.2.1", "fixture", 1, period=86400))
        rate_bytes = (restored / backup_module.ROLES["rate_limits"]).read_bytes()
        self.assertNotIn(b"192.0.2.1", rate_bytes)
        self.assertNotIn(self.rate_key, rate_bytes)
        objections = ItirazDeposu(restored / backup_module.ROLES["objections"],
                                  published_report=self.published, clock=lambda: self.now)
        self.assertEqual(objections.oku(self.receipt)["reason"], "PRIVATE_OBJECTION_REASON")
        self.now += RETENTION_SECONDS + 1
        expired = objections.oku(self.receipt)
        self.assertTrue(expired["private_purged"])
        self.assertNotIn("PRIVATE_OBJECTION_REASON", json.dumps(expired))
        self.assertTrue(rate.izin("192.0.2.1", "fixture", 1, period=86400))
        if os.name != "nt":
            for role in self.paths:
                output = restored / backup_module.ROLES[role]
                self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(output.parent.stat().st_mode), 0o700)

    def test_writers_stopped_must_be_acknowledged_before_output(self):
        with self.assertRaises(ValueError):
            backup_module.backup(self.paths, self.root / "encrypted")
        self.assertFalse((self.root / "encrypted").exists())

    def test_active_source_work_is_rejected_without_changing_live_lease(self):
        with self.memory.baglanti(write=True) as connection:
            connection.execute("INSERT INTO source_memory_leases VALUES('key','owner',?)", (time.time() + 600,))
        with self.assertRaisesRegex(ValueError, "etkin"):
            self.backup()
        self.assertFalse((self.root / "encrypted").exists())
        with self.memory.baglanti() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM source_memory_leases").fetchone()[0], 1)

    def test_wrong_role_database_fails_before_output(self):
        paths = dict(self.paths, feed=self.objections.path)
        # Use a copy so duplicate-file validation does not hide role validation.
        other = self.root / "wrong-feed.sqlite3"
        other.write_bytes(self.objections.path.read_bytes())
        paths["feed"] = other
        with self.assertRaisesRegex(ValueError, "role"):
            backup_module.backup(paths, self.root / "encrypted", writers_stopped=True)
        self.assertFalse((self.root / "encrypted").exists())

    def test_corrupt_last_store_and_wrong_key_do_not_create_partial_restore(self):
        self.backup()
        path = self.root / "encrypted" / "objections.sqlite3.enc"
        original = path.read_bytes()
        path.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
        with self.assertRaises(ValueError):
            self.restore()
        self.assertFalse((self.root / "restored").exists())
        path.write_bytes(original)
        with patch.dict(os.environ, {"DEVTRELLIS_BACKUP_KEY": Fernet.generate_key().decode()}):
            with self.assertRaises(ValueError):
                self.restore()
        self.assertFalse((self.root / "restored").exists())

    def test_ciphertext_cannot_be_moved_to_another_role_even_with_updated_hash(self):
        self.backup()
        directory = self.root / "encrypted"
        manifest_path = directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        data = (directory / "source_memory.sqlite3.enc").read_bytes()
        (directory / "feed.sqlite3.enc").write_bytes(data)
        manifest["roles"]["feed"]["sha256"] = hashlib.sha256(data).hexdigest()
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):
            self.restore()
        self.assertFalse((self.root / "restored").exists())

    def test_manifest_cannot_silently_omit_an_optional_store(self):
        self.backup()
        path = self.root / "encrypted" / "manifest.json"
        manifest = json.loads(path.read_text())
        del manifest["roles"]["objections"]
        path.write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):
            self.restore()
        self.assertFalse((self.root / "restored").exists())

    def test_existing_target_is_preserved_and_missing_parent_is_not_created(self):
        self.backup()
        target = self.root / "restored"
        target.mkdir()
        sentinel = target / "sentinel.txt"
        sentinel.write_text("unchanged")
        with self.assertRaises(ValueError):
            self.restore()
        self.assertEqual(sentinel.read_text(), "unchanged")
        with self.assertRaises(ValueError):
            backup_module.restore(self.root / "encrypted", self.root / "absent" / "restored")
        self.assertFalse((self.root / "absent").exists())

    def test_write_failure_removes_only_new_restore_outputs(self):
        self.backup()
        original = backup_module._write
        calls = 0
        def fail_after_write(path, data, created):
            nonlocal calls
            calls += 1
            original(path, data, created)
            if calls == 2:
                raise OSError("synthetic write failure")
        with patch.object(backup_module, "_write", side_effect=fail_after_write):
            with self.assertRaisesRegex(OSError, "synthetic"):
                self.restore()
        self.assertFalse((self.root / "restored").exists())
        self.assertTrue((self.root / "encrypted" / "manifest.json").is_file())
        self.assertTrue(self.objections.path.is_file())

    def test_backup_write_failure_removes_new_output(self):
        original = backup_module._write
        def fail_after_write(path, data, created):
            original(path, data, created)
            raise OSError("synthetic write failure")
        with patch.object(backup_module, "_write", side_effect=fail_after_write):
            with self.assertRaisesRegex(OSError, "synthetic"):
                self.backup()
        self.assertFalse((self.root / "encrypted").exists())

    def test_hardlinked_source_and_backup_component_are_rejected(self):
        alias = self.root / "alias.sqlite3"
        os.link(self.objections.path, alias)
        with self.assertRaisesRegex(ValueError, "bağlantılı"):
            self.backup()
        alias.unlink()
        self.backup()
        os.link(self.root / "encrypted" / "manifest.json", self.root / "manifest-alias.json")
        with self.assertRaises(ValueError):
            self.restore()
        self.assertFalse((self.root / "restored").exists())

    def test_symlink_source_and_parent_are_rejected(self):
        alias = self.root / "alias.sqlite3"
        try:
            alias.symlink_to(self.objections.path)
        except OSError:
            self.skipTest("This host does not permit creating symbolic links")
        with self.assertRaisesRegex(ValueError, "bağlantı"):
            backup_module.backup(dict(self.paths, objections=alias), self.root / "encrypted", writers_stopped=True)
        parent = self.root / "alias-parent"
        parent.symlink_to(self.root / "live-objections", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "bağlantı"):
            backup_module.backup(dict(self.paths, objections=parent / "objections.sqlite3"),
                                 self.root / "encrypted", writers_stopped=True)
        with self.assertRaisesRegex(ValueError, "bağlantı"):
            backup_module.backup(self.paths, parent / "backup", writers_stopped=True)

    def test_reparse_attribute_is_rejected_before_file_access(self):
        path = self.paths["community"]
        original = Path.lstat
        class ReparseInfo:
            st_mode = stat.S_IFREG | 0o600
            st_file_attributes = 0x400
        def information(entry, *args, **kwargs):
            return ReparseInfo() if entry == path else original(entry, *args, **kwargs)
        with patch.object(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400, create=True), patch.object(Path, "lstat", information):
            with self.assertRaisesRegex(ValueError, "junction"):
                self.backup()


    def cli(self, *arguments, key=None):
        # Deliberately pass only a synthetic key, never the host's secret environment.
        environment = {"SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
                       "DEVTRELLIS_BACKUP_KEY": key or "SYNTHETIC_INVALID_KEY"}
        return subprocess.run([sys.executable, "-B", "-X", "utf8",
                               str(ROOT / "araclar/community-backup.py"), *map(str, arguments)],
                              capture_output=True, text=True, encoding="utf-8", env=environment)

    def assert_safe_cli_error(self, result, *private_values, exit_code=1):
        self.assertEqual(result.returncode, exit_code)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn(str(ROOT), result.stderr)
        self.assertNotIn(str(self.root), result.stderr)
        for value in private_values:
            self.assertNotIn(value, result.stderr)
        self.assertLess(len(result.stderr), 200)
        self.assertIn("Yedek ", result.stderr)

    def test_cli_wrong_key_and_existing_target_are_short_private_safe_errors(self):
        self.backup()
        wrong_key = Fernet.generate_key().decode()
        result = self.cli("restore", "--source", self.root / "encrypted",
                          "--target", self.root / "restored", key=wrong_key)
        self.assert_safe_cli_error(result, wrong_key)
        self.assertFalse((self.root / "restored").exists())
        target = self.root / "PRIVATE_EXISTING_TARGET"
        target.mkdir()
        sentinel = target / "sentinel.txt"
        sentinel.write_text("unchanged")
        result = self.cli("restore", "--source", self.root / "encrypted",
                          "--target", target, key=wrong_key)
        self.assert_safe_cli_error(result, wrong_key, "PRIVATE_EXISTING_TARGET")
        self.assertEqual(sentinel.read_text(), "unchanged")

    def test_cli_missing_source_and_invalid_sqlite_do_not_expose_paths(self):
        key = Fernet.generate_key().decode()
        missing = self.root / "PRIVATE_MISSING_DATABASE.sqlite3"
        result = self.cli("backup", "--community", self.community.path,
                          "--feed", missing, "--target", self.root / "new-backup",
                          "--writers-stopped", key=key)
        self.assert_safe_cli_error(result, key, missing.name)
        missing.write_text("PRIVATE_INVALID_DATABASE_CONTENT")
        result = self.cli("backup", "--community", self.community.path,
                          "--feed", missing, "--target", self.root / "new-backup",
                          "--writers-stopped", key=key)
        self.assert_safe_cli_error(result, key, missing.name, "PRIVATE_INVALID_DATABASE_CONTENT")
        self.assertFalse((self.root / "new-backup").exists())

    def test_cli_invalid_arguments_do_not_echo_secret_or_private_path(self):
        for arguments in [
                ("PRIVATE_MISTYPED_SECRET",),
                ("restore", "--source", self.root / "PRIVATE_SOURCE", "--target", self.root / "new",
                 "--PRIVATE_MISTYPED_SECRET"),
                ("restore", "--source", self.root / "PRIVATE_SOURCE")]:
            result = self.cli(*arguments)
            self.assert_safe_cli_error(result, "PRIVATE_MISTYPED_SECRET", "PRIVATE_SOURCE", exit_code=2)
        help_result = self.cli("--help")
        self.assertEqual(help_result.returncode, 0)
        self.assertIn("backup", help_result.stdout)
        self.assertEqual(help_result.stderr, "")


if __name__ == "__main__":
    unittest.main()
