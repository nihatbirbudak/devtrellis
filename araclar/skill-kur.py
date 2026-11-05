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
FILES = ("SKILL.md", "agents/openai.yaml")
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
            or set(saved["files"]) != set(FILES)):
        raise ValueError("The existing skill ownership record does not match DevTrellis.")
    prior = {MARKER: marker.read_bytes()}
    for name in FILES:
        path = bounded(destination / name, home)
        if not path.is_file() or path.stat().st_size > 128 * 1024:
            raise ValueError("An installed skill file is missing or modified; nothing was changed.")
        content = path.read_bytes()
        if digest(content) != saved["files"][name]:
            raise ValueError("An installed skill file was edited; preserve or rename it before updating.")
        prior[name] = content
    # Değiştirilmiş ya da eklenmiş kişisel skill dosyaları yerinde kalır; otomatik güncellenmez.
    expected = {"agents", MARKER, *FILES}
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
            if prior == wanted:
                result.append({"target": host, "path": str(destination), "status": "unchanged"})
                continue
            bounded(destination, home)
            destination.mkdir(parents=True, exist_ok=prior is not None)
            completed.append((destination, prior))
            bounded(destination / "agents", home).mkdir(exist_ok=True)
            for name, content in wanted.items():
                atomic_write(bounded(destination / name, home), content)
            result.append({"target": host, "path": str(destination), "status": "updated" if prior else "installed"})
    except Exception:
        for destination, prior in reversed(completed):
            bounded(destination, home)
            if prior is not None:
                for name, content in prior.items():
                    atomic_write(bounded(destination / name, home), content)
            else:
                for name in (*FILES, MARKER):
                    path = bounded(destination / name, home)
                    if path.exists():
                        path.unlink()
                if (destination / "agents").exists():
                    (destination / "agents").rmdir()
                destination.rmdir()
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
    except (ValueError, OSError) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
