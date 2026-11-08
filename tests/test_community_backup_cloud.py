"""Bulut yedeğinde özel veri sınırı, gerçek SQLite kurtarma ve restic çağrı sözleşmesini sınar."""

import importlib.util
import getpass
import io
import json
import os
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from radar.topluluk_talepleri import TalepDeposu, RETENTION_SECONDS

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


backup_tests = load("backup_fixture", ROOT / "tests/test_community_backup.py")
cloud = load("cloud_backup", ROOT / "araclar/community-backup-cloud.py")
secret_session = load("secret_session", ROOT / "araclar/community-backup-secret-session.py")
backup = backup_tests.backup_module
REPOSITORY = "s3:https://s3.us-west-004.backblazeb2.com/devtrellis-fixture/devtrellis-community-v2"
SNAPSHOT = "a" * 64


@unittest.skipIf(backup_tests.Fernet is None, "Install requirements-backup.txt for backup acceptance")
class CloudBackupTest(unittest.TestCase):
    def setUp(self):
        self.fixture = backup_tests.BackupTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.requests = TalepDeposu(self.fixture.community.path, clock=lambda: self.fixture.now)
        self.requests.gonder({"kind": "support", "category": "setup", "subject": "PRIVATE_REQUEST_SUBJECT",
                              "message": "PRIVATE_REQUEST_MESSAGE", "email": "fixture@example.invalid", "name": "Private Name",
                              "language": "en", "consent": True, "idempotency_key": str(uuid.uuid4())})
        self.source = self.root / "encrypted"
        self.executable = self.root / ("restic.exe" if os.name == "nt" else "restic")
        self.executable.write_bytes(b"not executable: subprocess mocked")

    def create(self, cloud_safe=True):
        return backup.backup(self.fixture.paths, self.source, writers_stopped=True, cloud_safe=cloud_safe)

    def test_full_backup_private_fields_fail_cloud_gate_before_restic(self):
        self.create(False)
        with patch.object(cloud, "invoke") as invoke:
            with self.assertRaisesRegex(ValueError, "cloud-safe"):
                cloud.upload(self.source, self.executable, REPOSITORY)
        invoke.assert_not_called()

    def test_cloud_profile_removes_all_private_requests_objection_text_and_sessions_only_from_copy(self):
        self.create()
        backup.inspect_backup(self.source, require_cloud_safe=True)
        restored = backup.restore(self.source, self.root / "restored")
        with closing(sqlite3.connect(restored / backup.ROLES["community"])) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM community_requests").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM community_sessions").fetchone()[0], 0)
        with closing(sqlite3.connect(restored / backup.ROLES["objections"])) as connection:
            row = connection.execute("SELECT claim,reason,sources,private_purged,created_at FROM research_objections").fetchone()
            self.assertEqual(row[:4], ("", "", "[]", 1))
            self.assertEqual(row[4], self.fixture.now)
        with closing(sqlite3.connect(self.fixture.community.path)) as connection:
            self.assertEqual(connection.execute("SELECT message FROM community_requests").fetchone()[0], "PRIVATE_REQUEST_MESSAGE")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM community_sessions").fetchone()[0], 1)
        self.assertEqual(self.fixture.objections.oku(self.fixture.receipt)["reason"], "PRIVATE_OBJECTION_REASON")
        for role in ("community", "objections"):
            raw = (restored / backup.ROLES[role]).read_bytes()
            self.assertNotIn(b"PRIVATE_REQUEST_MESSAGE", raw)
            self.assertNotIn(b"PRIVATE_OBJECTION_REASON", raw)

    def test_restore_purges_at_original_ninety_day_boundary_before_any_store_operation(self):
        original = self.fixture.now - RETENTION_SECONDS + 1
        for path, table in ((self.fixture.community.path, "community_requests"),
                            (self.fixture.objections.path, "research_objections")):
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("UPDATE " + table + " SET created_at=?", (original,))
                connection.commit()
        with patch.object(backup.time, "time", return_value=self.fixture.now):
            self.create(False)
            before = backup.restore(self.source, self.root / "before")
        with closing(sqlite3.connect(before / backup.ROLES["community"])) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM community_requests").fetchone()[0], 1)
        with patch.object(backup.time, "time", return_value=self.fixture.now + 1):
            after = backup.restore(self.source, self.root / "at-boundary")
        with closing(sqlite3.connect(after / backup.ROLES["community"])) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM community_requests").fetchone()[0], 0)
        with closing(sqlite3.connect(after / backup.ROLES["objections"])) as connection:
            self.assertEqual(connection.execute("SELECT reason,private_purged FROM research_objections").fetchone(), ("", 1))
        self.assertNotIn(b"PRIVATE_REQUEST_MESSAGE", (after / backup.ROLES["community"]).read_bytes())
        self.assertNotIn(b"PRIVATE_OBJECTION_REASON", (after / backup.ROLES["objections"]).read_bytes())

    def test_expired_private_data_is_not_added_to_a_new_full_backup(self):
        with patch.object(backup.time, "time", return_value=self.fixture.now + RETENTION_SECONDS):
            self.create(False)
        # Inspect a restored image at an earlier clock: cleanup has already taken
        # place in the snapshot, not merely in a future application operation.
        with patch.object(backup.time, "time", return_value=self.fixture.now):
            restored = backup.restore(self.source, self.root / "restored")
        with closing(sqlite3.connect(restored / backup.ROLES["community"])) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM community_requests").fetchone()[0], 0)
        with closing(sqlite3.connect(self.fixture.community.path)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM community_requests").fetchone()[0], 1)

    def test_upload_passes_only_ciphertext_archive_and_accepts_single_snapshot_summary(self):
        self.create()
        captured = {}
        def invoke(executable, destination, args, **kwargs):
            captured["data"] = kwargs["stdin"].read()
            captured["args"] = args
            return json.dumps({"message_type": "summary", "snapshot_id": SNAPSHOT}).encode()
        with patch.object(cloud, "invoke", side_effect=invoke):
            result = cloud.upload(self.source, self.executable, REPOSITORY)
        self.assertEqual(result["snapshot"], SNAPSHOT)
        self.assertIn("--stdin", captured["args"])
        self.assertNotIn(b"SQLite format 3", captured["data"])
        self.assertNotIn(b"PRIVATE_REQUEST_MESSAGE", captured["data"])
        self.assertNotIn(os.environ["DEVTRELLIS_BACKUP_KEY"].encode(), captured["data"])
        with patch.object(cloud, "invoke", return_value=b'{"message_type":"summary","snapshot_id":"short"}'):
            with self.assertRaises(ValueError):
                cloud.upload(self.source, self.executable, REPOSITORY)

    def test_rehearsal_stub_restic_reads_actual_archive_then_real_sqlite_restore_without_activation(self):
        self.create()
        archive = cloud.pack(self.source)
        calls = []
        def invoke(executable, destination, args, **kwargs):
            calls.append(args)
            if args[0] == "snapshots":
                return json.dumps([{"id": SNAPSHOT, "hostname": cloud.NAMESPACE, "tags": [cloud.NAMESPACE],
                                    "time": datetime.now(timezone.utc).isoformat()}]).encode()
            if args[0] == "check":
                return b""
            if args[0] == "dump":
                return archive
            self.fail("Unexpected restic command")
        target = self.root / "rehearsal"
        with patch.object(cloud, "invoke", side_effect=invoke):
            result = cloud.rehearse(self.executable, REPOSITORY, target)
        self.assertEqual(result["sqlite_restore"], "passed")
        self.assertEqual(result["application_acceptance"], "pending")
        self.assertEqual([args[0] for args in calls], ["snapshots", "check", "dump"])
        for role, path in backup.ROLES.items():
            with closing(sqlite3.connect(target / "recovered" / path)) as connection:
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_archive_traversal_symlink_duplicate_and_missing_member_fail_before_output(self):
        self.create()
        for name, kind in (("../outside", "file"), ("manifest.json", "symlink"), ("manifest.json", "duplicate")):
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w") as archive:
                member = tarfile.TarInfo(name)
                if kind == "symlink":
                    member.type = tarfile.SYMTYPE
                    member.linkname = "../outside"
                else:
                    member.size = 2
                archive.addfile(member, io.BytesIO(b"{}") if kind != "symlink" else None)
                if kind == "duplicate":
                    archive.addfile(member, io.BytesIO(b"{}"))
            with self.assertRaises(ValueError):
                cloud.unpack(buffer.getvalue(), self.root / "unpacked")
            self.assertFalse((self.root / "unpacked").exists())
        with self.assertRaises(ValueError):
            cloud.unpack(b"x" * (cloud.MAX_PACKAGE + 1), self.root / "unpacked")

    def test_k1_disabled_server_packs_five_stores(self):
        # K1 deposu yalnız K1 açık sunucuda vardır; onsuz beş depolu paket yine paketlenir ve açılır.
        without_k1 = dict(self.fixture.paths)
        without_k1.pop("k1")
        backup.backup(without_k1, self.source, writers_stopped=True, cloud_safe=True)
        unpacked = cloud.unpack(cloud.pack(self.source), self.root / "unpacked-five")
        self.assertNotIn("k1", backup.inspect_backup(unpacked, require_cloud_safe=True)["roles"])

    def test_missing_store_extra_file_and_size_limits_fail(self):
        partial = dict(self.fixture.paths)
        partial.pop("rate_limits")
        backup.backup(partial, self.source, writers_stopped=True, cloud_safe=True)
        with self.assertRaisesRegex(ValueError, "beş"):
            cloud.pack(self.source)
        (self.source / "extra.txt").write_text("not uploaded")
        with self.assertRaises(ValueError):
            cloud.pack(self.source)
        (self.source / "extra.txt").unlink()
        module = cloud.backup_api()
        with patch.object(cloud, "MAX_PACKAGE", 1), patch.object(cloud, "backup_api", return_value=module), patch.object(
                module, "inspect_backup") as inspect:
            with self.assertRaisesRegex(ValueError, "64 MiB"):
                cloud.pack(self.source)
        inspect.assert_not_called()

    def test_secret_environment_does_not_inherit_app_keys_or_password_commands(self):
        fake = {name: "synthetic-value" for name in cloud.REQUIRED_SECRETS}
        fake.update(DEVTRELLIS_RATE_KEY="do-not-pass", NVIDIA_API_KEY="do-not-pass",
                    RESTIC_PASSWORD_COMMAND="do-not-run", RESTIC_PASSWORD_FILE="do-not-read",
                    AWS_SHARED_CREDENTIALS_FILE="do-not-read")
        with patch.dict(os.environ, fake):
            environment = cloud.restic_environment(REPOSITORY)
        self.assertEqual(environment["RESTIC_PASSWORD"], "synthetic-value")
        for name in ("DEVTRELLIS_BACKUP_KEY", "DEVTRELLIS_RATE_KEY", "NVIDIA_API_KEY", "RESTIC_PASSWORD_COMMAND",
                     "RESTIC_PASSWORD_FILE", "AWS_SHARED_CREDENTIALS_FILE"):
            self.assertNotIn(name, environment)

    def test_restic_process_uses_environment_without_shell_and_bounds_output(self):
        process_environment = {name: "synthetic-process-secret" for name in cloud.REQUIRED_SECRETS}
        class Process:
            def __init__(self, output):
                self.stdout = io.BytesIO(output)
                self.killed = False
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def wait(self):
                return 0
            def kill(self):
                self.killed = True
        process = Process(b"ok")
        with patch.dict(os.environ, process_environment), patch.object(cloud.subprocess, "Popen", return_value=process) as start:
            self.assertEqual(cloud.invoke(self.executable, REPOSITORY, ["check"]), b"ok")
        self.assertFalse(start.call_args.kwargs["shell"])
        self.assertEqual(start.call_args.kwargs["stderr"], subprocess.DEVNULL)
        self.assertNotIn("synthetic-process-secret", json.dumps(start.call_args.args))
        self.assertEqual(start.call_args.kwargs["env"]["RESTIC_PASSWORD"], "synthetic-process-secret")
        oversized = Process(b"output exceeds bound")
        with patch.dict(os.environ, process_environment), patch.object(cloud.subprocess, "Popen", return_value=oversized):
            with self.assertRaisesRegex(ValueError, "boyut"):
                cloud.invoke(self.executable, REPOSITORY, ["check"], limit=3)
        self.assertTrue(oversized.killed)

    def test_secret_session_requires_hidden_tty_and_restores_environment_on_failure(self):
        before = dict(os.environ)
        with patch.object(secret_session.sys.stdin, "isatty", return_value=False), patch.object(secret_session.getpass, "getpass") as prompt:
            with self.assertRaises(ValueError):
                secret_session.run("cloud", ["upload"])
        prompt.assert_not_called()
        captured = {}
        def fail(*args, **kwargs):
            captured.update({name: os.environ[name] for name in cloud.REQUIRED_SECRETS})
            raise ValueError("synthetic failure")
        with patch.object(secret_session.sys.stdin, "isatty", return_value=True), patch.object(
                secret_session.getpass, "getpass", return_value="synthetic-session-secret"), patch.object(
                secret_session.runpy, "run_path", side_effect=fail):
            with self.assertRaisesRegex(ValueError, "synthetic"):
                secret_session.run("cloud", ["upload"])
        self.assertEqual(captured["RESTIC_PASSWORD"], "synthetic-session-secret")
        self.assertEqual(dict(os.environ), before)
        with patch.object(secret_session.sys.stdin, "isatty", return_value=True), patch.object(
                secret_session.getpass, "getpass", side_effect=getpass.GetPassWarning("no hidden input")):
            with self.assertRaises(getpass.GetPassWarning):
                secret_session.run("snapshot", ["backup"])
        self.assertEqual(dict(os.environ), before)

    def test_repository_requires_https_provider_and_dedicated_prefix_without_secrets(self):
        self.assertEqual(cloud.repository(REPOSITORY), REPOSITORY)
        for invalid in ("sftp:anything", REPOSITORY.replace("https", "http"), REPOSITORY + "?token=secret",
                        REPOSITORY.replace("s3.us-west-004.backblazeb2.com", "user:secret@s3.us-west-004.backblazeb2.com"),
                        REPOSITORY.rsplit("/", 1)[0], REPOSITORY.replace("backblazeb2.com", "example.invalid")):
            with self.assertRaises(ValueError):
                cloud.repository(invalid)

    def test_retention_is_dry_run_only_and_cannot_claim_upload_health(self):
        with patch.object(cloud, "invoke", return_value=b"[]") as invoke:
            result = cloud.retention_plan(self.executable, REPOSITORY)
        args = invoke.call_args.args[2]
        self.assertIn("--dry-run", args)
        self.assertNotIn("--prune", args)
        self.assertFalse(result["deleted"])
        path = self.root / "retention-health.json"
        cloud.health_record(path, "retention-plan", "ok", result)
        with self.assertRaises(ValueError):
            cloud.check_health(path)

    def test_health_stale_failed_and_existing_files_are_rejected_without_overwrite(self):
        path = self.root / "health.json"
        cloud.health_record(path, "upload", "ok", {"snapshot": SNAPSHOT})
        self.assertEqual(cloud.check_health(path)["state"], "ok")
        original = path.read_bytes()
        with self.assertRaises(ValueError):
            cloud.health_record(path, "upload", "failed")
        self.assertEqual(path.read_bytes(), original)
        value = json.loads(original)
        value["finished_at"] = (datetime.now(timezone.utc) - timedelta(hours=31)).isoformat()
        path.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            cloud.check_health(path)
        failed = self.root / "failed.json"
        cloud.health_record(failed, "upload", "failed")
        with self.assertRaises(ValueError):
            cloud.check_health(failed)

    def test_default_command_is_plan_only_without_credentials_or_restic(self):
        result = subprocess.run([sys.executable, "-B", str(ROOT / "araclar/community-backup-cloud.py"), "upload"],
                                capture_output=True, check=True, text=True, encoding="utf-8", env={"PYTHONIOENCODING": "utf-8", "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")})
        value = json.loads(result.stdout)
        self.assertEqual(value["mode"], "plan_only")
        self.assertFalse(value["network"])
        self.assertEqual(value["action"], "upload")
        self.assertEqual(value["required_environment"], list(cloud.REQUIRED_SECRETS) + ["DEVTRELLIS_BACKUP_KEY"])
        self.assertEqual(value["private_requests_and_objection_text"], "excluded")
        self.assertEqual(value["g4"], "not_covered")
        self.assertEqual(value["schedule"], "not_installed")
        self.assertIn("Bu çıktı önkoşulları denetlemez", value["note"])

    def test_all_nonexecuting_plans_disclose_that_inputs_are_not_validated(self):
        # main() must remain static even when supplied nonexistent/invalid inputs.
        for action in ("upload", "rehearse", "retention-plan"):
            with self.subTest(action=action), patch.object(sys, "argv", [
                    "community-backup-cloud.py", action, "--repository", "invalid",
                    "--source", "missing-source", "--restic", "missing-executable",
                    "--target", "missing-parent/target"]), patch("sys.stdout", new_callable=io.StringIO) as output, \
                    patch.object(cloud, "repository") as validate_repository, \
                    patch.object(cloud, "safe_path") as validate_path, \
                    patch.object(cloud, "invoke") as invoke:
                cloud.main()
                value = json.loads(output.getvalue())
                self.assertEqual(value["action"], action)
                self.assertFalse(value["network"])
                self.assertIn("önkoşulları denetlemez", value["note"])
                validate_repository.assert_not_called()
                validate_path.assert_not_called()
                invoke.assert_not_called()


if __name__ == "__main__":
    unittest.main()
