"""DevTrellis araştırma skill'ini seçilen kullanıcı klasörüne mevcut dosyaları koruyarak kurar veya ZIP hazırlar."""

import argparse
import hashlib
import json
import os
import stat
import tempfile
import zipfile
from pathlib import Path


SKILL_NAME = "devtrellis-research"
PACKAGE = Path(__file__).resolve().parents[1] / "integrations" / SKILL_NAME
LEGACY_FILES = ("SKILL.md", "agents/openai.yaml")
FILES = (*LEGACY_FILES, "references/contribution.md", "references/source-memory.md")
MARKER = ".devtrellis-skill-install.json"
OWNER = "https://github.com/nihatbirbudak/devtrellis"


def linked(path):
    if path.is_symlink():
        return True
    return os.name == "nt" and path.exists() and bool(path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def bounded(path, base):
    """Yazılacak gerçek yolun açık hedef içinde kaldığını ve bağlantı içermediğini doğrular."""
    path, base = Path(path).absolute(), Path(base).absolute()
    if not path.is_relative_to(base):
        raise ValueError("Destination must remain inside the selected home directory.")
    if linked(base):
        raise ValueError("Symbolic links and junctions are not supported in the skill destination.")
    current = base
    for part in path.relative_to(base).parts:
        current /= part
        if linked(current):
            raise ValueError("Symbolic links and junctions are not supported in the skill destination.")
    if not path.resolve().is_relative_to(base.resolve()):
        raise ValueError("Resolved destination leaves the selected home directory.")
    return path


def digest(content):
    return hashlib.sha256(content).hexdigest()


def package_files(package=PACKAGE):
    package = Path(package).resolve()
    source = package / "skills" / SKILL_NAME
    files = {}
    for name in FILES:
        path = bounded(source / name, package)
        if not path.is_file() or path.stat().st_size > 128 * 1024:
            raise ValueError("The packaged skill is missing or unexpectedly large.")
        files[name] = path.read_bytes()
    return files


def manifest(files):
    return {"schema": 1, "owner": OWNER, "skill": SKILL_NAME,
            "files": {name: digest(content) for name, content in files.items()}}


def inspect_destination(destination, home):
    """Yalnız kendi sağlam kurulum kaydımızın işaret ettiği değişmemiş dosyaları güncellemeye izin verir."""
    bounded(destination, home)
    if not destination.exists():
        return None
    if not destination.is_dir():
        raise ValueError("The skill destination is not a directory.")
    marker = bounded(destination / MARKER, home)
    if not marker.is_file() or marker.stat().st_size > 8192:
        raise ValueError("An existing skill has no DevTrellis ownership record; nothing was changed.")
    try:
        saved = json.loads(marker.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError):
        raise ValueError("The installed skill ownership record is invalid; nothing was changed.") from None
    if (not isinstance(saved, dict) or set(saved) != {"schema", "owner", "skill", "files"}
            or type(saved["schema"]) is not int or saved["schema"] != 1 or saved["owner"] != OWNER
            or saved["skill"] != SKILL_NAME or not isinstance(saved["files"], dict)
            or set(saved["files"]) not in (set(LEGACY_FILES), set(FILES))):
        raise ValueError("The existing skill ownership record does not match DevTrellis.")
    prior = {MARKER: marker.read_bytes()}
    for name in FILES:
        if name not in saved["files"]:
            continue
        path = bounded(destination / name, home)
        if not path.is_file() or path.stat().st_size > 128 * 1024:
            raise ValueError("An installed skill file is missing or modified; nothing was changed.")
        content = path.read_bytes()
        if digest(content) != saved["files"][name]:
            raise ValueError("An installed skill file was edited; preserve or rename it before updating.")
        prior[name] = content
    # Değiştirilmiş ya da eklenmiş kişisel skill dosyaları yerinde kalır; otomatik güncellenmez.
    expected = {MARKER, *saved["files"]}
    expected.update(parent.as_posix() for name in saved["files"] for parent in Path(name).parents
                    if parent != Path("."))
    for directory, dirs, filenames in os.walk(destination, followlinks=False):
        for name in [*dirs, *filenames]:
            path = Path(directory) / name
            bounded(path, home)
            if path.relative_to(destination).as_posix() not in expected:
                raise ValueError("The skill contains additional files; nothing was changed.")
    return prior


def atomic_write(path, content):
    handle, temporary_name = tempfile.mkstemp(prefix=".devtrellis-write-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


class SkillInstallRecoveryError(RuntimeError):
    """A failed install preserved external changes or could not restore every file."""

    def __init__(self, recovery):
        self.recovery = recovery
        attention = [row for row in recovery if row["status"] not in {"restored", "already_restored"}]
        details = "; ".join(f'{row["target"]}/{row["file"]}: {row["status"]}' for row in attention)
        super().__init__("Skill installation failed; rollback needs attention: " + details)


def installed_bytes(path, home):
    path = bounded(path, home)
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError("A skill file was replaced by a directory; it was preserved.")
    return path.read_bytes()


def rollback_files(completed, home):
    """Restore each successful write independently, only while our own bytes remain."""
    recovery = []
    for host, destination, prior, written, created in reversed(completed):
        for name, last_written in reversed(list(written.items())):
            row = {"target": host, "file": name}
            try:
                path = bounded(destination / name, home)
                current = installed_bytes(path, home)
                previous = prior.get(name) if prior is not None else None
                if current == previous:
                    row["status"] = "already_restored"
                elif current != last_written:
                    row["status"] = "preserved_external_change"
                else:
                    if previous is None:
                        path.unlink()
                    else:
                        atomic_write(path, previous)
                    row["status"] = "restored"
            except Exception as error:
                row.update(status="failed", error_type=type(error).__name__)
            recovery.append(row)
        for directory in reversed(created):
            row = {"target": host, "file": directory.relative_to(destination).as_posix()}
            try:
                bounded(directory, home)
                if directory.exists():
                    directory.rmdir()  # Only empty directories created by this attempt.
                row["status"] = "restored"
            except Exception as error:
                row.update(status="preserved_directory", error_type=type(error).__name__)
            recovery.append(row)
    return recovery


def install(target, home=None, *, package=PACKAGE):
    if target not in {"codex", "claude", "both"}:
        raise ValueError("Choose codex, claude or both.")
    home = Path(home).expanduser().absolute() if home is not None else Path.home().absolute()
    if linked(home) or (home.exists() and not home.is_dir()):
        raise ValueError("Choose a regular home directory, without a link or junction.")
    files = package_files(package)
    wanted = {**files, MARKER: (json.dumps(manifest(files), indent=2, sort_keys=True) + "\n").encode("utf-8")}
    hosts = ("codex", "claude") if target == "both" else (target,)
    plans = []
    for host in hosts:
        destination = bounded(home / ("." + host) / "skills" / SKILL_NAME, home)
        prior = inspect_destination(destination, home)
        plans.append((host, destination, prior))
    completed, result = [], []
    try:
        for host, destination, prior in plans:
            if inspect_destination(destination, home) != prior:
                raise ValueError("The skill changed after preflight; external changes were preserved.")
            if prior == wanted:
                result.append({"target": host, "path": str(destination), "status": "unchanged"})
                continue
            written, created = {}, []
            completed.append((host, destination, prior, written, created))
            bounded(destination, home)
            destination.mkdir(parents=True, exist_ok=prior is not None)
            if prior is None:
                created.append(destination)
            directories = {parent for name in files for parent in Path(name).parents if parent != Path(".")}
            for relative in sorted(directories, key=lambda value: (len(value.parts), value.as_posix())):
                directory = bounded(destination / relative, home)
                if not directory.exists():
                    directory.mkdir()
                    created.append(directory)
                elif not directory.is_dir():
                    raise ValueError("A skill directory is not a regular directory; it was preserved.")
            for name, content in wanted.items():
                path = bounded(destination / name, home)
                previous = prior.get(name) if prior is not None else None
                if installed_bytes(path, home) != previous:
                    raise ValueError("A skill file changed during installation; it was preserved.")
                atomic_write(path, content)
                written[name] = content
            result.append({"target": host, "path": str(destination), "status": "updated" if prior else "installed"})
        for _, destination, _ in plans:
            if inspect_destination(destination, home) != wanted:
                raise ValueError("The installed skill changed before verification; it was preserved.")
    except Exception as error:
        recovery = rollback_files(completed, home)
        if any(row["status"] not in {"restored", "already_restored"} for row in recovery):
            raise SkillInstallRecoveryError(recovery) from error
        raise
    return result


def export_zip(output, *, package=PACKAGE):
    output = Path(output).expanduser().absolute()
    if output.exists() or linked(output):
        raise ValueError("The ZIP destination already exists; choose a new filename.")
    files = package_files(package)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            info = zipfile.ZipInfo(f"{SKILL_NAME}/{name}", date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)
    return str(output)


def main():
    parser = argparse.ArgumentParser(description="Install the DevTrellis research skill without changing MCP or host settings.")
    parser.add_argument("--target", choices=("codex", "claude", "both"))
    parser.add_argument("--home", type=Path, help="Explicit home directory; defaults to the current user's home.")
    parser.add_argument("--zip", type=Path, help="Create a portable skill ZIP at a new filename; does not install it.")
    options = parser.parse_args()
    if bool(options.target) == bool(options.zip) or (options.home is not None and options.target is None):
        parser.error("Choose --target codex|claude|both, or --zip NEW_FILE (without --home).")
    try:
        result = install(options.target, options.home) if options.target else {"zip": export_zip(options.zip)}
    except (ValueError, OSError, SkillInstallRecoveryError) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
