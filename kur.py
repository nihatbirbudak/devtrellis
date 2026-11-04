"""Bağımlılıkları ayrı Python ortamına kurar; kullanıcı verisini ve mevcut ortamı silmez."""

import argparse
import os
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description="DevTrellis isolated environment setup / bağımsız ortam kurulumu")
    parser.add_argument("--venv", type=Path, default=ROOT / ".venv", help="Virtual environment directory")
    parser.add_argument("--check", action="store_true", help="Check only; do not install packages")
    args = parser.parse_args()
    if sys.version_info < (3, 12):
        parser.error("Python 3.12 or newer is required / Python 3.12 veya üstü gerekli.")
    target = args.venv.resolve()
    python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if target.exists() and not (target / "pyvenv.cfg").is_file():
        parser.error("Target exists and is not a virtual environment; nothing was changed.")
    if args.check and not python.is_file():
        parser.error("Environment not installed. Run setup without --check first.")
    if not args.check:
        if not target.exists():
            venv.EnvBuilder(with_pip=True).create(target)
        subprocess.run([str(python), "-m", "pip", "--isolated", "--disable-pip-version-check", "install",
                        "--no-input", "-r", str(ROOT / "requirements-lock.txt")], check=True)
    subprocess.run([str(python), "-m", "pip", "check"], check=True)
    subprocess.run([str(python), "-X", "utf8", "-c", "import fastapi,uvicorn,feedparser,bs4,httpx,keyring; "
                    "print('DevTrellis: dependencies ready / bağımlılıklar hazır')"], check=True)
    print(f'"{python}" -X utf8 "{ROOT / "calistir.py"}" sun --elle-toplama')


if __name__ == "__main__":
    main()
