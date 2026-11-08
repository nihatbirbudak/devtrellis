"""Yedek erişim bilgilerini gizli terminalden yalnız geçici işlem belleğine alır."""

import argparse
import getpass
import os
import runpy
import sys
import warnings
from pathlib import Path


def run(script, arguments):
    if script not in ("snapshot", "cloud"):
        raise ValueError("Desteklenmeyen yedek aracı.")
    if not sys.stdin.isatty():
        raise ValueError("Gizli giriş için etkileşimli terminal gerekli; sohbet, boru veya dosya kullanılmaz.")
    names = ["DEVTRELLIS_BACKUP_KEY"]
    if script == "cloud":
        names += ["RESTIC_PASSWORD", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"]
    previous = {name: os.environ.get(name) for name in names}
    old_arguments = sys.argv
    try:
        for name in names:
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                value = getpass.getpass(name + ": ")
            if not value or "\x00" in value or "\n" in value or "\r" in value:
                raise ValueError("Boş veya geçersiz gizli giriş.")
            os.environ[name] = value
            del value
        target = Path(__file__).with_name("community-backup.py" if script == "snapshot" else "community-backup-cloud.py")
        sys.argv = [str(target), *arguments]
        runpy.run_path(str(target), run_name="__main__")
    finally:
        sys.argv = old_arguments
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("script", choices=("snapshot", "cloud"))
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    arguments = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
    run(args.script, arguments)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, EOFError, getpass.GetPassWarning):
        print("Gizli giriş tamamlanmadı. Erişim bilgisini sohbete veya dosyaya yazmayın.", file=sys.stderr)
        sys.exit(1)
