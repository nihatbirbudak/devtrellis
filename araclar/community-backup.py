"""Durdurulmuş SQLite depolarını şifreler; yeni hedefe süre sınırlarını koruyarak açar."""

import argparse
import hashlib
import json
import os
import secrets
import sqlite3
import stat
import sys
import tempfile
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROLES = {"community": "community/community.sqlite3", "feed": "feed/community-feed.sqlite3",
         "source_memory": "source_memory/source-memory.sqlite3", "objections": "objections/objections.sqlite3",
         "rate_limits": "rate_limits/limits.sqlite3", "k1": "k1/k1.sqlite3"}
FORMAT = 2
MAX_DATABASE_BYTES = 256 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
REQUIRED_TABLES = {
    "community": {"community_accounts", "community_sessions", "community_entries", "community_revisions"},
    "feed": {"sources", "items", "revisions"},
    "source_memory": {"source_memory", "source_memory_leases", "source_memory_budget", "source_memory_summaries"},
}


def _cipher():
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        raise ValueError("Şifreli yedek için kurulu cryptography paketi gerekli.") from None
    try:
        return Fernet(os.environ.get("DEVTRELLIS_BACKUP_KEY", "").encode("ascii"))
    except (ValueError, TypeError, UnicodeError):
        raise ValueError("DEVTRELLIS_BACKUP_KEY güvenli ortamdan sağlanan geçerli Fernet anahtarı olmalı.") from None


def _path(path):
    """Do not resolve away links before checking every existing path component."""
    path = Path(os.path.abspath(path))
    for entry in (path, *path.parents):
        try:
            info = entry.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or (getattr(info, "st_file_attributes", 0)
                                        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
            raise ValueError("Yedek yolları sembolik bağlantı veya junction kullanamaz.")
    return path


def _file(path):
    path = _path(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("Yedek kaynağı tek bağlantılı normal dosya olmalı.")
    return path


def _read(path, limit):
    path = _file(path)
    if path.stat().st_size > limit:
        raise ValueError("Yedek dosyası boyut sınırını aşıyor.")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Yedek dosyası boyut sınırını aşıyor.")
    return data


def _target(path):
    path = _path(path)
    if path.exists():
        raise ValueError("Hedef yeni bir dizin olmalı; mevcut hedefin üzerine yazılmaz.")
    if not path.parent.is_dir():
        raise ValueError("Hedefin üst dizini önceden oluşturulmuş olmalı.")
    return path


def _remember(path, created, directory):
    info = path.lstat()
    created.append((path, info.st_dev, info.st_ino, directory))


def _directory(path, created):
    from radar.ozel_dosya_izinleri import ozel_dosya_izni
    _path(path)
    path.mkdir(mode=0o700)
    _remember(path, created, True)
    ozel_dosya_izni(path, directory=True)


def _write(path, data, created):
    from radar.ozel_dosya_izinleri import ozel_dosya_izni
    _path(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        _remember(path, created, False)
        ozel_dosya_izni(path)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _rollback(created):
    """Remove only paths created by this invocation, never traverse a changed tree."""
    failed = False
    for path, device, inode, directory in reversed(created):
        try:
            _path(path)
            info = path.lstat()
            if (info.st_dev, info.st_ino) != (device, inode):
                raise ValueError("Yeni çıktı başka bir dosyayla değiştirildi.")
            path.rmdir() if directory else path.unlink()
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            failed = True
    if failed:
        raise OSError("Başarısız işlemin yeni hedefi tamamen temizlenemedi; hedefi kullanmayın.")


def _validate_database(connection, role):
    connection.execute("PRAGMA trusted_schema=OFF")
    if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise ValueError("SQLite bütünlük denetiminden geçmedi.")
    if role == "objections":
        from radar.arastirma_itirazlari import ItirazDeposu
        ItirazDeposu._sahiplik(connection)
    elif role == "rate_limits":
        from radar.topluluk_api import _rate_store
        _rate_store(connection)
    elif role == "k1":
        from radar.katki_v4_depo import k1_deposu_dogrula
        k1_deposu_dogrula(connection)
    else:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not REQUIRED_TABLES[role].issubset(tables):
            raise ValueError("SQLite deposu beklenen role ait değil.")
    if role == "source_memory":
        if connection.execute("SELECT 1 FROM source_memory_leases WHERE expires_at>? LIMIT 1", (time.time(),)).fetchone():
            raise ValueError("Kaynak kontrol işi hâlâ etkin; işlerin bitmesini bekleyin.")


def _private_retention(connection, role, *, now, cloud_safe=False):
    """Yalnız kopyadaki özel alanları ilk oluşturma zamanına göre temizler."""
    connection.execute("PRAGMA secure_delete=ON")
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if role == "community":
        if "community_requests" in tables:
            from radar.topluluk_talepleri import RETENTION_SECONDS
            if cloud_safe:
                connection.execute("DELETE FROM community_requests")
            else:
                connection.execute("DELETE FROM community_requests WHERE created_at<=?", (now - RETENTION_SECONDS,))
        if cloud_safe:
            connection.execute("DELETE FROM community_sessions")
    elif role == "objections":
        from radar.arastirma_itirazlari import RETENTION_SECONDS
        clause = "private_purged=0" if cloud_safe else "private_purged=0 AND created_at<=?"
        values = (now,) if cloud_safe else (now, now - RETENTION_SECONDS)
        connection.execute("UPDATE research_objections SET claim='',reason='',sources='[]',private_purged=1,"
            "revision=revision+1,updated_at=?,status=CASE WHEN status IN ('pending','in_review') THEN 'expired' ELSE status END "
            "WHERE " + clause, values)
    elif role == "k1":
        # Yayımlanmadan sonlanan gönderimlerin özel aday metni: bulut profilinde hepsi, tam yedekte saklama süresi dolanlar.
        from radar.katki_v4 import K1Ayarlari
        from radar.katki_v4_depo import ozel_metni_temizle
        ozel_metni_temizle(connection, sinir_saniye=None if cloud_safe else now - K1Ayarlari().ozel_metin_saklama_gun * 86400)
    connection.commit()
    # Remove freed pages from the logical copy before serializing it. This does
    # not erase another copy of a backup, nor guarantee physical disk erasure.
    connection.execute("VACUUM")


def _cloud_safe(connection, role):
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if role == "community":
        if connection.execute("SELECT 1 FROM community_sessions LIMIT 1").fetchone():
            return False
        if "community_requests" in tables and connection.execute("SELECT 1 FROM community_requests LIMIT 1").fetchone():
            return False
    if role == "objections" and connection.execute(
            "SELECT 1 FROM research_objections WHERE claim<>'' OR reason<>'' OR sources<>'[]' OR private_purged<>1 LIMIT 1").fetchone():
        return False
    if role == "k1":
        from radar.katki_v4_depo import ozel_metin_kaldi_mi
        return not ozel_metin_kaldi_mi(connection)
    return True


def _snapshot(path, role, *, cloud_safe=False):
    """SQLite backup includes committed WAL pages; normalize only a private copy."""
    from radar.ozel_dosya_izinleri import ozel_dosya_izni
    source = _file(path)
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original:
        original.execute("PRAGMA query_only=ON")
        _validate_database(original, role)
        pages = original.execute("PRAGMA page_count").fetchone()[0]
        page_size = original.execute("PRAGMA page_size").fetchone()[0]
        if pages * page_size > MAX_DATABASE_BYTES:
            raise ValueError("SQLite deposu yedek boyut sınırını aşıyor.")
        # Only this newly created temporary directory contains plaintext. Its ACL
        # is restricted before creating the database and it is removed on exit.
        with tempfile.TemporaryDirectory(prefix="devtrellis-backup-") as temporary:
            directory = _path(temporary)
            ozel_dosya_izni(directory, directory=True)
            copy_path = directory / "snapshot.sqlite3"
            descriptor = os.open(copy_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
            ozel_dosya_izni(copy_path)
            with closing(sqlite3.connect(copy_path)) as copy:
                original.backup(copy)
                if copy.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
                    raise ValueError("SQLite kopyası bağımsız dosya biçimine çevrilemedi.")
                _validate_database(copy, role)
                _private_retention(copy, role, now=int(time.time()), cloud_safe=cloud_safe)
                _validate_database(copy, role)
                copy.commit()
            data = _read(copy_path, MAX_DATABASE_BYTES)
    _file(source)
    return data


def _binding(manifest, role):
    return {"format": FORMAT, "backup_id": manifest["backup_id"], "created_at": manifest["created_at"],
            "consistency": "writers_stopped", "role": role, "roles": sorted(manifest["roles"])}


def backup(paths, target, *, writers_stopped=False, cloud_safe=False):
    if writers_stopped is not True:
        raise ValueError("Önce tüm yazıcıları ve çalışan işleri durdurun; --writers-stopped ile doğrulayın.")
    if set(paths) - set(ROLES) or not paths.get("community") or not paths.get("feed"):
        raise ValueError("Topluluk ve akış depoları zorunlu; bilinmeyen depo rolü kullanılamaz.")
    cipher = _cipher()
    selected = {role: _file(path) for role, path in paths.items() if path}
    identities = {(path.stat().st_dev, path.stat().st_ino) for path in selected.values()}
    if len(identities) != len(selected):
        raise ValueError("Her depoya ayrı kaynak dosyası gerekli.")
    destination = _target(target)
    manifest = {"format": FORMAT, "backup_id": secrets.token_hex(16),
                "created_at": datetime.now(timezone.utc).isoformat(), "consistency": "writers_stopped",
                "roles": {role: {} for role in selected}}
    encrypted_stores = {}
    for role, source in selected.items():
        header = json.dumps(_binding(manifest, role), sort_keys=True).encode("utf-8")
        encrypted = cipher.encrypt(header + b"\n" + _snapshot(source, role, cloud_safe=cloud_safe))
        encrypted_stores[role] = encrypted
        manifest["roles"][role] = {"file": role + ".sqlite3.enc", "sha256": hashlib.sha256(encrypted).hexdigest()}
    # All sources are validated before creating any output. The operator's
    # stopped-writers acknowledgement is not an online atomic-snapshot lock.
    created = []
    try:
        _directory(destination, created)
        for role, data in encrypted_stores.items():
            _write(destination / manifest["roles"][role]["file"], data, created)
        _write(destination / "manifest.json", json.dumps(manifest, indent=2).encode("utf-8"), created)
    except BaseException:
        _rollback(created)
        raise
    return manifest


def _manifest(source):
    try:
        value = json.loads(_read(source / "manifest.json", MAX_MANIFEST_BYTES))
        if (not isinstance(value, dict) or set(value) != {"format", "backup_id", "created_at", "consistency", "roles"}
                or value["format"] != FORMAT or value["consistency"] != "writers_stopped"
                or not isinstance(value["backup_id"], str) or len(value["backup_id"]) != 32
                or any(char not in "0123456789abcdef" for char in value["backup_id"])
                or not isinstance(value["created_at"], str) or len(value["created_at"]) > 64
                or not isinstance(value["roles"], dict)
                or not {"community", "feed"}.issubset(value["roles"]) or set(value["roles"]) - set(ROLES)):
            raise ValueError()
        for role, record in value["roles"].items():
            if (not isinstance(record, dict) or set(record) != {"file", "sha256"}
                    or record["file"] != role + ".sqlite3.enc"
                    or not isinstance(record["sha256"], str) or len(record["sha256"]) != 64
                    or any(char not in "0123456789abcdef" for char in record["sha256"])):
                raise ValueError()
        return value
    except (ValueError, TypeError, UnicodeError):
        raise ValueError("Yedek manifesti geçersiz veya desteklenmeyen biçimde.") from None


def _load_backup(source, *, require_cloud_safe=False):
    cipher = _cipher()
    source = _path(source)
    if not source.is_dir():
        raise ValueError("Yedek kaynağı bir dizin olmalı.")
    manifest = _manifest(source)
    expected = {"manifest.json", *(record["file"] for record in manifest["roles"].values())}
    if {entry.name for entry in source.iterdir()} != expected:
        raise ValueError("Yedek dizininde yalnız manifestteki dosyalar bulunabilir.")
    restored_stores = {}
    for role, record in manifest["roles"].items():
        encrypted = _read(source / record["file"], (MAX_DATABASE_BYTES + 8192) * 4 // 3 + 8192)
        if hashlib.sha256(encrypted).hexdigest() != record["sha256"]:
            raise ValueError("Şifreli dosyanın özeti uyuşmuyor.")
        try:
            payload = cipher.decrypt(encrypted)
            header, plain = payload.split(b"\n", 1)
            if len(header) > 8192 or json.loads(header) != _binding(manifest, role):
                raise ValueError()
        except Exception:
            raise ValueError("Yedek anahtarı, depo rolü veya şifreli içerik doğrulanamadı.") from None
        if (len(plain) > MAX_DATABASE_BYTES or not plain.startswith(b"SQLite format 3\x00")
                or plain[18:20] != b"\x01\x01"):
            raise ValueError("Bağımsız SQLite görüntüsü geçersiz.")
        with closing(sqlite3.connect(":memory:")) as memory:
            memory.deserialize(plain)
            _validate_database(memory, role)
            if require_cloud_safe and not _cloud_safe(memory, role):
                raise ValueError("Bulut aktarımı yalnız --cloud-safe kopyaları kabul eder; özel talep/itiraz metinleri kapsam dışıdır.")
            _private_retention(memory, role, now=int(time.time()))
            if role == "community":
                memory.execute("PRAGMA secure_delete=ON")
                memory.execute("DELETE FROM community_sessions")
                memory.commit()
            if role == "k1":
                # Yarım kalmış kapı kiraları geri yüklenen kopyada yeniden alınabilsin.
                memory.execute("UPDATE v4_submissions SET lease_id=NULL,lease_until=NULL")
                memory.commit()
            _validate_database(memory, role)
            restored_stores[role] = memory.serialize()
    return source, manifest, restored_stores


def inspect_backup(source, *, require_cloud_safe=False):
    """Şifreyi ve kapsamı doğrular; özel kayıtları veya anahtarı döndürmez."""
    _source, manifest, _stores = _load_backup(source, require_cloud_safe=require_cloud_safe)
    return manifest


def restore(source, target):
    destination = _target(target)
    _source, manifest, restored_stores = _load_backup(source)
    created = []
    try:
        _directory(destination, created)
        for role, data in restored_stores.items():
            output = destination / ROLES[role]
            _directory(output.parent, created)
            _write(output, data, created)
    except BaseException:
        _rollback(created)
        raise
    return destination


class _CLIArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's message may include a mistyped secret or a private path.
        self.exit(2, "Yedek komutunun seçenekleri geçersiz veya eksik. Kullanım için --help çalıştırın.\n")


def main():
    parser = _CLIArgumentParser(description="Durdurulmuş yazıcılarla şifreli SQLite yedeği; çevrimiçi ortak snapshot değildir.")
    commands = parser.add_subparsers(dest="command", required=True)
    save = commands.add_parser("backup")
    for role in ROLES:
        save.add_argument("--" + role.replace("_", "-"), type=Path, required=role in {"community", "feed"})
    save.add_argument("--target", type=Path, required=True)
    save.add_argument("--cloud-safe", action="store_true", help="Kopyadan tüm özel talepleri, itiraz özel metnini, K1 gönderimlerinin yayımlanmamış aday metnini ve oturumları çıkarır; canlı kaynak değişmez.")
    save.add_argument("--writers-stopped", action="store_true", required=True,
                      help="İşletmeci tüm yazıcıları durdurmuş ve devam eden işlerin bitmesini beklemiş olmalı.")
    load = commands.add_parser("restore")
    load.add_argument("--source", type=Path, required=True)
    load.add_argument("--target", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "backup":
        result = backup({role: getattr(args, role) for role in ROLES}, args.target, writers_stopped=args.writers_stopped, cloud_safe=args.cloud_safe)
        if args.cloud_safe:
            print("Bulut kopyası: özel başvuru/destek kayıtları, itiraz özel metni ve oturumlar kurtarılamaz; kaynak depolar korundu.")
        print(f"Şifreli yedek hazır: {len(result['roles'])} depo. G4 özel taslak iş DB'si kapsam dışıdır; tam G4 kurtarma kabulü kapalıdır.")
    else:
        restore(args.source, args.target)
        print("Yedek yeni hedefte ayrı özel dizinlere açıldı. Kabul tamamlanmadan canlı hizmete bağlamayın.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Sanitize the CLI boundary only; imported backup/restore APIs still raise.
        print("Yedek işlemi tamamlanmadı. Anahtarı, kaynakları ve yeni hedef koşulunu kontrol edin.", file=sys.stderr)
        sys.exit(1)
