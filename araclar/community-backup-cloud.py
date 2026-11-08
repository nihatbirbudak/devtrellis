"""Şifreli ve özel taleplerden arındırılmış yedeği restic ile aktarır veya ayrı yerde sınar."""

import argparse
import hashlib
import importlib.util
import io
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from radar.ozel_dosya_izinleri import ozel_dosya_izni

NAMESPACE = "devtrellis-community-v2"
ARCHIVE = "devtrellis-backup.tar"
MAX_PACKAGE = 64 * 1024 * 1024
TIMEOUT = 300
REQUIRED_SECRETS = ("RESTIC_PASSWORD", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")
BASE_ENV = ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE",
            "SSL_CERT_FILE", "SSL_CERT_DIR", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY")


def backup_api():
    spec = importlib.util.spec_from_file_location("devtrellis_backup", Path(__file__).with_name("community-backup.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def safe_path(value, *, file=False, new=False):
    path = Path(os.path.abspath(value))
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or (getattr(info, "st_file_attributes", 0) & 0x400):
            raise ValueError("Bağlantılı yollar kabul edilmez.")
    if new and (path.exists() or not path.parent.is_dir()):
        raise ValueError("Üst dizini bulunan yeni bir hedef gerekli.")
    if file:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("Tek bağlantılı normal dosya gerekli.")
    return path


def repository(value):
    if not isinstance(value, str) or not value.startswith("s3:https://"):
        raise ValueError("Yalnız B2/R2 HTTPS S3 deposu desteklenir.")
    url = urlsplit(value[3:])
    host = url.hostname or ""
    valid_host = re.fullmatch(r"s3\.[a-z0-9-]+\.backblazeb2\.com", host) or re.fullmatch(
        r"[a-f0-9]{32}\.r2\.cloudflarestorage\.com", host)
    if (not valid_host or url.username or url.password or url.port or url.query or url.fragment
            or not re.fullmatch(r"/[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]/" + NAMESPACE, url.path)):
        raise ValueError("Ayrılmış kova ve devtrellis-community-v2 öneki gerekli; erişim bilgisi URL'ye yazılmaz.")
    return value


def restic_environment(destination):
    result = {name: os.environ[name] for name in BASE_ENV if name in os.environ}
    for name in REQUIRED_SECRETS:
        value = os.environ.get(name, "")
        if not value or "\x00" in value or "\n" in value or "\r" in value:
            raise ValueError("Restic erişimi güvenli işlem ortamında eksik veya geçersiz.")
        result[name] = value
    result["RESTIC_REPOSITORY"] = repository(destination)
    # Do not inherit password commands, file-based credentials, arbitrary restic
    # options, app secrets or model keys. Explicit S3 credentials are mandatory.
    return result


def invoke(executable, destination, arguments, *, stdin=None, limit=65536):
    executable = safe_path(executable, file=True)
    if executable.name.lower() not in ("restic", "restic.exe"):
        raise ValueError("Doğrulanmış restic çalıştırılabilir dosyasının tam yolu gerekli.")
    environment = restic_environment(destination)
    command = [str(executable), "--no-cache", "--retry-lock", "30s", *arguments]
    try:
        with subprocess.Popen(command, stdin=stdin or subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, env=environment, shell=False) as process:
            expired = threading.Event()
            def terminate_owned_process():
                expired.set()
                try:
                    process.kill()
                except OSError:
                    pass
            timer = threading.Timer(TIMEOUT, terminate_owned_process)
            timer.daemon = True
            timer.start()
            output = bytearray()
            try:
                while True:
                    part = process.stdout.read(min(65536, limit - len(output) + 1))
                    if not part:
                        break
                    output.extend(part)
                    if len(output) > limit:
                        process.kill()
                        raise ValueError("Restic çıktısı boyut sınırını aştı.")
                code = process.wait()
                if expired.is_set() or code != 0:
                    raise ValueError("Restic işlemi tamamlanmadı; zaman veya çıkış durumu başarısız.")
                return bytes(output)
            finally:
                timer.cancel()
    except OSError:
        raise ValueError("Restic çalıştırılamadı; kurulum ve erişim yerelde doğrulanmalı.") from None


def pack(source):
    module = backup_api()
    source = safe_path(source)
    allowed = {"manifest.json", *(role + ".sqlite3.enc" for role in module.ROLES)}
    total = 0
    for entry in source.iterdir():
        if entry.name not in allowed:
            raise ValueError("Bulut paketinde beklenmeyen dosya var.")
        total += safe_path(entry, file=True).stat().st_size
        if total > MAX_PACKAGE:
            raise ValueError("Bulut paketi 64 MiB pilot sınırını aşıyor.")
    manifest = module.inspect_backup(source, require_cloud_safe=True)
    # K1 deposu yalnız K1 açık sunucularda vardır; açıksa operatör --k1 ile ekler (docs/community-backup.md).
    if not set(module.ROLES) - {"k1"} <= set(manifest["roles"]):
        raise ValueError("Bu sunucu profili beş deponun tamamını ister; eksik rol sessizce atlanmaz.")
    source = safe_path(source)
    manifest_path = safe_path(source / "manifest.json", file=True)
    with manifest_path.open("rb") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError("Doğrulama sırasında manifest büyüdü.")
    if json.loads(raw) != manifest:
        raise ValueError("Doğrulama sırasında manifest değişti.")
    entries = {"manifest.json": raw}
    total = len(raw)
    for role, record in manifest["roles"].items():
        path = safe_path(source / record["file"], file=True)
        if path.stat().st_size > MAX_PACKAGE - total:
            raise ValueError("Bulut paketi 64 MiB pilot sınırını aşıyor.")
        with path.open("rb") as stream:
            data = stream.read(MAX_PACKAGE - total + 1)
        total += len(data)
        if total > MAX_PACKAGE or hashlib.sha256(data).hexdigest() != record["sha256"]:
            raise ValueError("Doğrulama sırasında şifreli dosya değişti veya büyüdü.")
        entries[record["file"]] = data
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, data in sorted(entries.items()):
            member = tarfile.TarInfo(name)
            member.size = len(data)
            member.mode = 0o600
            archive.addfile(member, io.BytesIO(data))
    if buffer.tell() > MAX_PACKAGE:
        raise ValueError("Bulut paketi 64 MiB pilot sınırını aşıyor.")
    return buffer.getvalue()


def upload(source, executable, destination):
    payload = pack(source)
    with tempfile.TemporaryFile() as stream:
        stream.write(payload)  # Only Fernet ciphertext and non-secret manifest.
        stream.seek(0)
        raw = invoke(executable, destination, ["backup", "--json", "--stdin", "--stdin-filename", ARCHIVE,
                                               "--host", NAMESPACE, "--tag", NAMESPACE], stdin=stream)
    try:
        summaries = [json.loads(line) for line in raw.splitlines() if line.strip()]
        matches = [item for item in summaries if item.get("message_type") == "summary"]
        identifier = matches[-1]["snapshot_id"]
        if len(matches) != 1 or not re.fullmatch(r"[a-f0-9]{64}", identifier):
            raise ValueError()
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        raise ValueError("Aktarım sonucu doğrulanamadı; başarı varsayılmadı.") from None
    return {"snapshot": identifier, "encrypted_package_bytes": len(payload), "profile": "without_private_requests"}


def unpack(data, target):
    if len(data) > MAX_PACKAGE:
        raise ValueError("Kurtarma paketi boyut sınırını aşıyor.")
    module = backup_api()
    allowed = {"manifest.json", *(role + ".sqlite3.enc" for role in module.ROLES)}
    contents = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
            for member in archive:
                if (not member.isfile() or member.name not in allowed or member.name in contents
                        or member.size < 0 or member.size > MAX_PACKAGE):
                    raise ValueError("Kurtarma arşivinde beklenmeyen öğe var.")
                stream = archive.extractfile(member)
                contents[member.name] = stream.read(MAX_PACKAGE + 1)
                if len(contents[member.name]) != member.size or sum(map(len, contents.values())) > MAX_PACKAGE:
                    raise ValueError("Kurtarma arşivi sınırları geçersiz.")
    except (tarfile.TarError, EOFError):
        raise ValueError("Kurtarma arşivi okunamadı.") from None
    # K1 deposu isteğe bağlıdır (yalnız K1 açık sunucuda); manifest ve inspect_backup paketin kendi rol listesini ayrıca doğrular.
    if not allowed - {"k1.sqlite3.enc"} <= set(contents):
        raise ValueError("Kurtarma paketinde beş depodan veya manifestten biri eksik.")
    target = safe_path(target, new=True)
    target.mkdir(mode=0o700)
    ozel_dosya_izni(target, directory=True)
    for name, content in contents.items():
        path = target / name
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            ozel_dosya_izni(path)
            stream.write(content)
    module.inspect_backup(target, require_cloud_safe=True)
    return target


def rehearse(executable, destination, target, snapshot="latest"):
    target = safe_path(target, new=True)
    raw = invoke(executable, destination, ["snapshots", "--json", "--host", NAMESPACE, "--tag", NAMESPACE])
    try:
        snapshots = json.loads(raw)
        valid = [item for item in snapshots if item.get("hostname") == NAMESPACE and NAMESPACE in item.get("tags", [])
                 and re.fullmatch(r"[a-f0-9]{64}", item.get("id", ""))]
        if snapshot == "latest":
            chosen = max(valid, key=lambda item: datetime.fromisoformat(item["time"].replace("Z", "+00:00")))
        else:
            chosen = next(item for item in valid if item["id"] == snapshot)
        identifier = chosen["id"]
    except (ValueError, TypeError, KeyError, StopIteration, AttributeError):
        raise ValueError("Ayrılmış depo için doğrulanmış kurtarma sürümü bulunamadı.") from None
    invoke(executable, destination, ["check", "--read-data"])
    data = invoke(executable, destination, ["dump", identifier, "/" + ARCHIVE], limit=MAX_PACKAGE)
    target.mkdir(mode=0o700)
    ozel_dosya_izni(target, directory=True)
    encrypted = unpack(data, target / "encrypted")
    backup_api().restore(encrypted, target / "recovered")
    return {"snapshot": identifier, "profile": "without_private_requests", "sqlite_restore": "passed",
            "application_acceptance": "pending", "g4_recovery": "not_covered"}


def retention_plan(executable, destination):
    # Dry-run only. No destructive operation is implemented by this wrapper.
    raw = invoke(executable, destination, ["forget", "--dry-run", "--json", "--keep-within", "30d", "--group-by", "",
                                          "--host", NAMESPACE, "--tag", NAMESPACE])
    try:
        groups = json.loads(raw)
        proposed = [record["id"] for group in groups for record in group.get("remove", [])]
        if any(not re.fullmatch(r"[a-f0-9]{64}", identifier) for identifier in proposed):
            raise ValueError()
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ValueError("Saklama önerisi ayrıştırılamadı; silme yapılmadı.") from None
    return {"retention_days": 30, "mode": "dry_run", "deleted": False, "proposed_snapshot_ids": proposed}


def health_record(path, action, state, details=None):
    path = safe_path(path, new=True)
    value = {"schema": 1, "action": action, "state": state,
             "finished_at": datetime.now(timezone.utc).isoformat(), **(details or {})}
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        ozel_dosya_izni(path)
        json.dump(value, stream, indent=2)
    return value


def check_health(path, *, max_age_hours=30):
    path = safe_path(path, file=True)
    if path.stat().st_size > 65536:
        raise ValueError("Sağlık işareti boyut sınırını aşıyor.")
    value = json.loads(path.read_text(encoding="utf-8"))
    age = time.time() - datetime.fromisoformat(value["finished_at"]).timestamp()
    if (value.get("schema") != 1 or value.get("state") != "ok" or value.get("action") not in ("upload", "rehearse")
            or not 0 <= age <= max_age_hours * 3600):
        raise ValueError("Sağlık işareti başarısız, eski veya uygun işleme ait değil.")
    return {"action": value["action"], "state": "ok", "age_hours": round(age / 3600, 2)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("upload", "rehearse", "retention-plan", "health"))
    parser.add_argument("--repository")
    parser.add_argument("--restic", type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--target", type=Path)
    parser.add_argument("--snapshot", default="latest")
    parser.add_argument("--health", type=Path)
    parser.add_argument("--max-age-hours", type=int, default=30)
    parser.add_argument("--execute", action="store_true", help="İşletmeci kurulumu/onayı ardından gerçek bulut işlemini başlatır.")
    args = parser.parse_args()
    if args.action == "health":
        if not args.health:
            parser.error("--health gerekli")
        print(json.dumps(check_health(args.health, max_age_hours=args.max_age_hours)))
        return
    if not args.execute:
        print(json.dumps({"mode": "plan_only", "action": args.action, "network": False,
            "required_environment": list(REQUIRED_SECRETS) + ["DEVTRELLIS_BACKUP_KEY"],
            "private_requests_and_objection_text": "excluded", "g4": "not_covered", "schedule": "not_installed",
            "note": "Bu çıktı önkoşulları denetlemez; yalnız işlem planını gösterir."}))
        return
    if not args.repository or not args.restic or not args.health:
        parser.error("--repository, --restic ve yeni --health yolu gerekli")
    repository(args.repository)
    safe_path(args.health, new=True)
    try:
        if args.action == "upload":
            if not args.source:
                parser.error("--source gerekli")
            result = upload(args.source, args.restic, args.repository)
        elif args.action == "rehearse":
            if not args.target:
                parser.error("--target gerekli")
            result = rehearse(args.restic, args.repository, args.target, args.snapshot)
        else:
            result = retention_plan(args.restic, args.repository)
    except (ValueError, OSError, KeyError, TypeError):
        health_record(args.health, args.action, "failed")
        raise ValueError("Yedek işi başarısız; özel içerik veya erişim bilgisi günlüğe yazılmadı.") from None
    print(json.dumps(health_record(args.health, args.action, "ok", result)))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError):
        print("Yedek işlemi tamamlanmadı. Sağlık işaretini ve yerel kurulum önkoşullarını kontrol edin.", file=sys.stderr)
        sys.exit(1)
