"""Kamu veri dökümü: yalnız yayımlanmış kayıtları tarihli JSON dosyasına yazar; geri okur ve sayar.

Zamanlı görev kurmaz, sunucuya dokunmaz, itme yapmaz. Komut satırı örnekleri docs/public-dump.md içindedir.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / ".deps"))

from radar.kamu_dokum import DokumHatasi, dokumu_oku, dokumu_yukle, gece_dokumu  # noqa: E402


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "Döküm seçenekleri geçersiz. Kullanım için --help çalıştırın.\n")


def main(argv=None):
    parser = Parser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export", help="Yayımlanmış kayıtları tarihli JSON dökümü ve manifest olarak yaz")
    export.add_argument("--community", type=Path, required=True, help="Topluluk veritabanı (salt okunur açılır)")
    export.add_argument("--k1-db", type=Path, help="K1 v4 deposu (salt okunur); verilmezse yalnız v1 kayıtlar")
    export.add_argument("--target", type=Path, required=True, help="Döküm dizini (yoksa oluşturulur)")
    export.add_argument("--public-url", required=True, help="Sitenin kamu adresi; kayıt bağlantıları buna göre yazılır")
    export.add_argument("--git-commit", action="store_true", help="Hedef bir git çalışma ağacıysa döküm dosyalarını yerel commit'le (itme yok)")
    inspect = sub.add_parser("inspect", help="Dökümü doğrula ve kayıtları say")
    inspect.add_argument("dump", type=Path)
    load = sub.add_parser("load", help="Dökümü yeni, boş bir salt okunur kütüphane deposuna aç ve say")
    load.add_argument("dump", type=Path)
    load.add_argument("--db", type=Path, required=True, help="Yeni SQLite dosyası; var olan dosya kabul edilmez")
    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            sonuc = gece_dokumu(community_db=args.community, public_url=args.public_url, target_dir=args.target,
                                k1_db=args.k1_db, git_commit=args.git_commit)
        elif args.command == "inspect":
            dokum, sayilar = dokumu_oku(args.dump)
            sonuc = {"schema": dokum["schema"], "schema_version": dokum["schema_version"], "generated_at": dokum["generated_at"],
                     "counts": sayilar, "public_url": dokum["site"]["public_url"]}
        else:
            sonuc = dokumu_yukle(args.dump, args.db)
        print(json.dumps(sonuc, ensure_ascii=False))
        return 0
    except DokumHatasi as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (OSError, ValueError):
        print("Döküm işlemi tamamlanmadı. Depo yollarını ve hedef dizini kontrol edin.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
