"""NVIDIA anahtarını gizli girişle alır; yalnız oturumda kullanır veya Windows kimlik kasasına kaydeder."""

import argparse
import getpass
import os
from pathlib import Path
import subprocess
import sys
import warnings


def main(argv=None):
    parser = argparse.ArgumentParser(description="DevTrellis NVIDIA API key setup / anahtar kurulumu")
    parser.add_argument("--save", action="store_true", help="Save in Windows Credential Manager / Windows kimlik kasasına kaydet")
    parser.add_argument("--db", help="Personal database path / kişisel veritabanı")
    parser.add_argument("--port", type=int, default=8781)
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("Port must be 1024–65535 / Port 1024–65535 arasında olmalı.")
    if args.save and sys.platform != "win32":
        parser.error("--save requires Windows Credential Manager; omit it for a temporary session.")
    print("Get your own key / Kendi anahtarını al: https://build.nvidia.com/")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            key = getpass.getpass("NVIDIA API key / API anahtarı (hidden / gizli): ").strip()
    except (getpass.GetPassWarning, EOFError):
        print("A terminal with hidden input is required; key not read. / Gizli giriş destekleyen terminal gerekli; anahtar okunmadı.", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    if not 24 <= len(key) <= 256 or any(char.isspace() or not char.isprintable() for char in key):
        print("Invalid key format / Anahtar biçimi geçersiz.", file=sys.stderr)
        return 2
    if args.save:
        try:
            from keyring.backends.Windows import WinVaultKeyring
            WinVaultKeyring().set_password("ufuk-nvidia", "api-key", key)
        except Exception:
            print("Credential Manager unavailable; key not saved. / Kimlik kasası kullanılamıyor; anahtar kaydedilmedi.", file=sys.stderr)
            return 1
        print("Saved in Windows Credential Manager. / Windows kimlik kasasına kaydedildi.")
        return 0
    environment = dict(os.environ)
    environment["UFUK_NVIDIA_API_KEY"] = key
    command = [sys.executable, "-X", "utf8", str(Path(__file__).with_name("calistir.py")),
               "sun", "--elle-toplama", "--port", str(args.port)]
    if args.db:
        command.extend(["--db", args.db])
    print("Temporary key; Ctrl+C stops the app. / Geçici anahtar; Ctrl+C uygulamayı durdurur.")
    try:
        return subprocess.run(command, env=environment, check=False).returncode
    except KeyboardInterrupt:
        return 130
    finally:
        environment.pop("UFUK_NVIDIA_API_KEY", None)


if __name__ == "__main__":
    raise SystemExit(main())
