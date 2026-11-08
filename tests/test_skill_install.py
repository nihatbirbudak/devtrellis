"""Skill paketinin taşınabilirliğini ve kurulumun kullanıcı dosyalarını korumasını geçici klasörlerde doğrular."""

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("devtrellis_skill_installer", ROOT / "araclar" / "skill-kur.py")
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class SkillInstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()

    def destination(self, host="codex"):
        return self.home / ("." + host) / "skills" / installer.SKILL_NAME

    def snapshot(self):
        return {path.relative_to(self.home).as_posix(): path.read_bytes()
                for path in self.home.rglob("*") if path.is_file()}

    def altered_package(self):
        package = self.root / "package"
        for name, content in installer.package_files().items():
            path = package / "skills" / installer.SKILL_NAME / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content + (b"\nPublic example refinement.\n" if name == "SKILL.md" else b"\n"))
        return package


    def seed_legacy(self, hosts=("codex", "claude")):
        legacy = {name: content for name, content in installer.package_files().items()
                  if name in installer.LEGACY_FILES}
        legacy["SKILL.md"] = b"# Previously owned public research skill\n"
        for host in hosts:
            destination = self.destination(host)
            for name, content in legacy.items():
                target = destination / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            (destination / installer.MARKER).write_text(
                json.dumps(installer.manifest(legacy), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def test_verified_legacy_two_file_install_migrates_both_hosts(self):
        self.seed_legacy()
        result = installer.install("both", self.home)
        self.assertEqual([row["status"] for row in result], ["updated", "updated"])
        for host in ("codex", "claude"):
            actual = json.loads((self.destination(host) / installer.MARKER).read_text(encoding="utf-8"))
            self.assertEqual(actual, installer.manifest(installer.package_files()))
            for name, content in installer.package_files().items():
                self.assertEqual((self.destination(host) / name).read_bytes(), content)
        self.assertEqual([row["status"] for row in installer.install("both", self.home)],
                         ["unchanged", "unchanged"])

    def test_legacy_untracked_reference_blocks_before_either_host_write(self):
        self.seed_legacy()
        path = self.destination("claude") / "references/contribution.md"
        path.parent.mkdir()
        path.write_bytes(b"Personal reference, not owned by the old marker.")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "additional files"):
            installer.install("both", self.home)
        self.assertEqual(self.snapshot(), before)

    def test_legacy_modified_owned_file_is_preserved(self):
        self.seed_legacy()
        path = self.destination("codex") / "SKILL.md"
        path.write_bytes(path.read_bytes() + b"Personal change")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "edited"):
            installer.install("both", self.home)
        self.assertEqual(self.snapshot(), before)

    def test_legacy_migration_failure_restores_files_markers_and_new_directories(self):
        self.seed_legacy()
        before = self.snapshot()
        real_write, failed = installer.atomic_write, False

        def fail_once(path, content):
            nonlocal failed
            if not failed and path == self.destination("claude") / installer.MARKER:
                failed = True
                raise OSError("simulated legacy migration failure")
            return real_write(path, content)

        with patch.object(installer, "atomic_write", side_effect=fail_once), self.assertRaises(OSError):
            installer.install("both", self.home)
        self.assertEqual(self.snapshot(), before)
        for host in ("codex", "claude"):
            self.assertFalse((self.destination(host) / "references").exists())

    def test_legacy_migration_rollback_preserves_external_reference_edit(self):
        self.seed_legacy()
        before = self.snapshot()
        real_write = installer.atomic_write
        relative = "references/contribution.md"
        personal = b"New user-owned reference edit during migration."

        def edit_then_fail(path, content):
            if path == self.destination("claude") / "SKILL.md":
                (self.destination("codex") / relative).write_bytes(personal)
                raise OSError("simulated second-host migration failure")
            return real_write(path, content)

        with patch.object(installer, "atomic_write", side_effect=edit_then_fail):
            with self.assertRaises(installer.SkillInstallRecoveryError) as caught:
                installer.install("both", self.home)
        expected = dict(before)
        expected[".codex/skills/devtrellis-research/" + relative] = personal
        self.assertEqual(self.snapshot(), expected)
        self.assertIn({"target": "codex", "file": relative, "status": "preserved_external_change"},
                      caught.exception.recovery)

    def test_reference_links_exist_in_install_and_zip(self):
        expected = {"references/contribution.md", "references/source-memory.md"}
        files = installer.package_files()
        self.assertEqual(set(files), set(installer.LEGACY_FILES) | expected)
        links = set(re.findall(r"\]\((references/[^)\s]+)\)", files["SKILL.md"].decode("utf-8")))
        self.assertEqual(links, expected)
        installer.install("both", self.home)
        archive_path = self.root / "complete-skill.zip"
        installer.export_zip(archive_path)
        with zipfile.ZipFile(archive_path) as archive:
            for name in links:
                self.assertEqual(archive.read(installer.SKILL_NAME + "/" + name), files[name])
                for host in ("codex", "claude"):
                    self.assertEqual((self.destination(host) / name).read_bytes(), files[name])
    @unittest.skipUnless((ROOT / "araclar/yayin-kopyasi.py").is_file() and (ROOT / "README-OSS.md").is_file(),
                         "Source-tree exporter is deliberately absent from the public distribution")
    def test_source_export_includes_only_allowlisted_skill_references(self):
        files = installer.package_files()
        links = {"references/contribution.md", "references/source-memory.md"}
        spec = importlib.util.spec_from_file_location("public_export_for_skill_test", ROOT / "araclar/yayin-kopyasi.py")
        exporter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(exporter)
        public = self.root / "public-export"
        exporter.export(public)
        for name in links:
            self.assertEqual((public / "integrations" / installer.SKILL_NAME / "skills" / installer.SKILL_NAME / name).read_bytes(),
                             files[name])
        fake_root = self.root / "fake-source"
        private = Path("integrations/devtrellis-research/skills/devtrellis-research/references/personal.md")
        (fake_root / private).parent.mkdir(parents=True)
        (fake_root / private).write_bytes(b"Never allowlist arbitrary personal references.")
        with patch.object(exporter, "ROOT", fake_root):
            self.assertNotIn(private, exporter.files_to_copy())

    def test_both_installs_only_owned_skill_and_preserves_configuration(self):
        settings = {".codex/config.toml": b"existing = true\n", ".claude/settings.json": b'{"unchanged":true}',
                    ".codex/skills/other/SKILL.md": b"unrelated skill"}
        for name, content in settings.items():
            path = self.home / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        result = installer.install("both", self.home)
        self.assertEqual([row["status"] for row in result], ["installed", "installed"])
        actual = self.snapshot()
        for name, content in settings.items():
            self.assertEqual(actual[name], content)
        self.assertEqual(len(actual), len(settings) + 2 * (len(installer.FILES) + 1))
        for host in ("codex", "claude"):
            for name, content in installer.package_files().items():
                self.assertEqual((self.destination(host) / name).read_bytes(), content)

    def test_repeat_is_unchanged_without_rewriting_files(self):
        installer.install("codex", self.home)
        before = self.snapshot()
        stamps = {path: path.stat().st_mtime_ns for path in self.destination().rglob("*") if path.is_file()}
        self.assertEqual(installer.install("codex", self.home)[0]["status"], "unchanged")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual({path: path.stat().st_mtime_ns for path in stamps}, stamps)
        self.assertFalse((self.home / ".claude").exists())

    def test_verified_owned_version_can_update(self):
        installer.install("both", self.home)
        package = self.altered_package()
        result = installer.install("both", self.home, package=package)
        self.assertEqual([row["status"] for row in result], ["updated", "updated"])
        self.assertEqual((self.destination() / "SKILL.md").read_bytes(), installer.package_files(package)["SKILL.md"])
        self.assertEqual(installer.install("both", self.home, package=package)[0]["status"], "unchanged")

    def test_unknown_existing_skill_blocks_both_before_any_write(self):
        destination = self.destination("claude")
        destination.mkdir(parents=True)
        (destination / "SKILL.md").write_text("my own skill", encoding="utf-8")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "ownership"):
            installer.install("both", self.home)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.home / ".codex").exists())

    def test_modified_owned_skill_is_preserved(self):
        installer.install("codex", self.home)
        path = self.destination() / "SKILL.md"
        path.write_bytes(path.read_bytes() + b"\nPersonal instructions.\n")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "edited"):
            installer.install("codex", self.home)
        self.assertEqual(self.snapshot(), before)

    def test_extra_files_are_preserved_and_block_update(self):
        installer.install("claude", self.home)
        (self.destination("claude") / "personal.txt").write_text("keep", encoding="utf-8")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "additional files"):
            installer.install("claude", self.home)
        self.assertEqual(self.snapshot(), before)

    def test_foreign_or_traversing_manifest_is_rejected(self):
        installer.install("codex", self.home)
        path = self.destination() / installer.MARKER
        original = json.loads(path.read_text(encoding="utf-8"))
        for mutation in [{**original, "owner": "someone else"},
                         {**original, "files": {"../../config.toml": "a" * 64}},
                         {**original, "schema": True}]:
            path.write_text(json.dumps(mutation), encoding="utf-8")
            before = self.snapshot()
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                installer.install("codex", self.home)
            self.assertEqual(self.snapshot(), before)

    def test_failure_rolls_back_both_hosts_without_touching_configuration(self):
        installer.install("both", self.home)
        before = self.snapshot()
        real_write, failed = installer.atomic_write, False

        def fail_once(path, content):
            nonlocal failed
            if not failed and path == self.destination("claude") / installer.MARKER:
                failed = True
                raise OSError("simulated disk failure")
            return real_write(path, content)

        with patch.object(installer, "atomic_write", side_effect=fail_once), self.assertRaises(OSError):
            installer.install("both", self.home, package=self.altered_package())
        self.assertEqual(self.snapshot(), before)

    def test_first_install_failure_removes_only_its_partial_files(self):
        with patch.object(installer, "atomic_write", side_effect=OSError("simulated disk failure")), self.assertRaises(OSError):
            installer.install("codex", self.home)
        self.assertFalse(self.destination().exists())
        self.assertEqual(self.snapshot(), {})

    def test_rollback_preserves_a_concurrent_personal_edit(self):
        installer.install("both", self.home)
        before = self.snapshot()
        real_write = installer.atomic_write
        personal = b"Personal instructions written during installation.\\n"

        def edit_then_fail(path, content):
            if path == self.destination("claude") / "SKILL.md":
                (self.destination() / "SKILL.md").write_bytes(personal)
                raise OSError("simulated second-host disk failure")
            return real_write(path, content)

        with patch.object(installer, "atomic_write", side_effect=edit_then_fail):
            with self.assertRaises(installer.SkillInstallRecoveryError) as caught:
                installer.install("both", self.home, package=self.altered_package())
        expected = dict(before)
        expected[".codex/skills/devtrellis-research/SKILL.md"] = personal
        self.assertEqual(self.snapshot(), expected)
        self.assertIn({"target": "codex", "file": "SKILL.md", "status": "preserved_external_change"},
                      caught.exception.recovery)

    def test_rollback_failure_does_not_skip_other_files_or_host(self):
        installer.install("both", self.home)
        before = self.snapshot()
        package = self.altered_package()
        real_write = installer.atomic_write
        failed = False

        def fail_install_and_one_restore(path, content):
            nonlocal failed
            if not failed and path == self.destination("claude") / installer.MARKER:
                failed = True
                raise OSError("simulated installation failure")
            if failed and path == self.destination("claude") / "agents/openai.yaml":
                raise OSError("simulated rollback failure")
            return real_write(path, content)

        with patch.object(installer, "atomic_write", side_effect=fail_install_and_one_restore):
            with self.assertRaises(installer.SkillInstallRecoveryError) as caught:
                installer.install("both", self.home, package=package)
        expected = dict(before)
        expected[".claude/skills/devtrellis-research/agents/openai.yaml"] = installer.package_files(package)["agents/openai.yaml"]
        self.assertEqual(self.snapshot(), expected)
        self.assertIn({"target": "claude", "file": "agents/openai.yaml", "status": "failed", "error_type": "OSError"},
                      caught.exception.recovery)
        self.assertIn({"target": "claude", "file": "SKILL.md", "status": "restored"}, caught.exception.recovery)
        self.assertIn({"target": "codex", "file": "SKILL.md", "status": "restored"}, caught.exception.recovery)

    def test_first_install_rollback_preserves_concurrent_personal_files(self):
        real_write = installer.atomic_write
        personal = b"Keep my new instructions."
        extra = self.destination() / "personal.txt"

        def edit_then_fail(path, content):
            if path == self.destination() / "agents/openai.yaml":
                (self.destination() / "SKILL.md").write_bytes(personal)
                extra.write_bytes(b"Keep this additional file.")
                raise OSError("simulated installation failure")
            return real_write(path, content)

        with patch.object(installer, "atomic_write", side_effect=edit_then_fail):
            with self.assertRaises(installer.SkillInstallRecoveryError) as caught:
                installer.install("codex", self.home)
        self.assertEqual((self.destination() / "SKILL.md").read_bytes(), personal)
        self.assertEqual(extra.read_bytes(), b"Keep this additional file.")
        self.assertFalse((self.destination() / "agents").exists())
        self.assertFalse((self.destination() / installer.MARKER).exists())
        self.assertIn({"target": "codex", "file": "SKILL.md", "status": "preserved_external_change"},
                      caught.exception.recovery)

    def test_linked_destination_cannot_escape_home(self):
        outside = self.root / "outside"
        outside.mkdir()
        link = self.home / ".codex"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            if os.name != "nt":
                raise
            quoted_link = "'" + str(link).replace("'", "''") + "'"
            quoted_outside = "'" + str(outside).replace("'", "''") + "'"
            result = subprocess.run(["powershell", "-NoProfile", "-Command",
                                     f"New-Item -ItemType Junction -Path {quoted_link} -Target {quoted_outside} | Out-Null"],
                                    capture_output=True, text=True)
            if result.returncode:
                self.skipTest("The host does not permit symbolic links or junctions.")
        with self.assertRaisesRegex(ValueError, "links|junction"):
            installer.install("codex", self.home)
        self.assertEqual(list(outside.iterdir()), [])
        if link.is_symlink():
            link.unlink()
        else:
            link.rmdir()

    def test_bounded_rejects_parent_escape(self):
        with self.assertRaises(ValueError):
            installer.bounded(self.home / ".." / "outside", self.home)

    def test_portable_zip_is_exact_and_never_overwrites(self):
        output = self.root / "skill.zip"
        installer.export_zip(output)
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(set(archive.namelist()), {f"devtrellis-research/{name}" for name in installer.FILES})
            for name, content in installer.package_files().items():
                self.assertEqual(archive.read(f"devtrellis-research/{name}"), content)
        original = output.read_bytes()
        with self.assertRaises(ValueError):
            installer.export_zip(output)
        self.assertEqual(output.read_bytes(), original)
        self.assertEqual(self.snapshot(), {})

    def test_cli_explicit_home_and_conflict_exit(self):
        command = [sys.executable, "-B", "-X", "utf8", str(ROOT / "araclar" / "skill-kur.py"),
                   "--target", "claude", "--home", str(self.home)]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["status"], "installed")
        (self.destination("claude") / "SKILL.md").write_text("edited", encoding="utf-8")
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=15)
        self.assertEqual(result.returncode, 1)
        self.assertEqual((self.destination("claude") / "SKILL.md").read_text(encoding="utf-8"), "edited")

    def test_manifests_are_portable_and_reference_the_same_skill(self):
        for host in ("codex", "claude"):
            manifest = json.loads((installer.PACKAGE / f".{host}-plugin" / "plugin.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["name"], installer.SKILL_NAME)
            self.assertEqual(manifest["skills"], "./skills/")
            self.assertTrue((installer.PACKAGE / manifest["skills"] / installer.SKILL_NAME / "SKILL.md").is_file())
            self.assertNotIn("hooks", manifest)
            self.assertNotIn("mcpServers", manifest)
            self.assertNotIn("C:/" + "Users/", json.dumps(manifest))


if __name__ == "__main__":
    unittest.main()
