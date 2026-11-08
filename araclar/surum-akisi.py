"""Gece sürüm akışı: yayındaki kayıtların kapsamını kamu sürüm akışlarıyla karşılaştırır; model çağrısı ve yayın yok.

Varsayılan kapalıdır: ayar dosyasında `enabled` true olmadan hiçbir depo okunmaz ve istek atılmaz.
Zamanlı görev kurmaz; komut satırı belgede (docs/version-feed.md) örnek olarak verilir.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / ".deps"))

from radar.surum_akisi import SurumAkisi, SurumAkisiHatasi, ayarlari_oku, gece_isi, ornek_ayar  # noqa: E402


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "Sürüm akışı seçenekleri geçersiz. Kullanım için --help çalıştırın.\n")


def main(argv=None):
    parser = Parser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Bir gece turu: kayıtları oku, akışları (günde en çok bir kez) sor, etiketleri yaz")
    run.add_argument("--config", type=Path, required=True, help="devtrellis-version-feed-v1 ayar dosyası")
    run.add_argument("--db", type=Path, required=True, help="Ayrı önbellek/etiket SQLite dosyası (topluluk deposu değil)")
    run.add_argument("--community", type=Path, required=True, help="Topluluk veritabanı (salt okunur açılır)")
    run.add_argument("--k1-db", type=Path, help="K1 v4 deposu (salt okunur); verilmezse yalnız v1 raporlar")
    run.add_argument("--out", type=Path, help="Etiket raporunun yazılacağı yeni JSON dosyası")
    run.add_argument("--force", action="store_true", help="Ayar kapalı olsa da bir kez çalıştır (elle deneme)")
    sub.add_parser("example-config", help="Varsayılan değerli örnek ayarı yazdır (enabled=false)")
    labels = sub.add_parser("labels", help="Önbellekteki etiketleri göster")
    labels.add_argument("--db", type=Path, required=True)
    labels.add_argument("--kind", choices=["v1", "v4"])
    labels.add_argument("--id")
    labels.add_argument("--version", type=int)
    args = parser.parse_args(argv)
    try:
        if args.command == "example-config":
            print(json.dumps(ornek_ayar(), ensure_ascii=False, indent=2))
            return 0
        if args.command == "labels":
            if not args.db.is_file():
                raise SurumAkisiHatasi("Önbellek dosyası bulunamadı.")
            ayar = {**ornek_ayar(), "sources": {k: False for k in ("endoflife", "depsdev", "pypi", "github")}}
            akis = SurumAkisi(args.db, ayar)
            print(json.dumps(akis.etiketleri_oku(args.kind, args.id, args.version), ensure_ascii=False, indent=2))
            return 0
        if args.out is not None and args.out.exists():
            raise SurumAkisiHatasi("Çıktı dosyası yeni olmalı; var olan dosyanın üzerine yazılmaz.")
        sonuc = gece_isi(args.config, args.db, args.community, k1_db=args.k1_db, force=args.force)
        if sonuc["status"] == "disabled":
            print(json.dumps(sonuc, ensure_ascii=False))
            print("Sürüm akışı ayarda kapalı (enabled=false); hiçbir depo okunmadı, istek atılmadı.", file=sys.stderr)
            return 2
        if args.out is not None:
            with args.out.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(sonuc, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
        ozet = {k: sonuc[k] for k in ("schema", "status", "records", "requests", "counts", "adapter_errors", "model_calls",
                                      "started_at", "finished_at")}
        print(json.dumps(ozet, ensure_ascii=False))
        return 0
    except SurumAkisiHatasi as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - ham Python izi yerine sade mesaj; yalnız hata türü yazılır
        print(f"Sürüm akışı tamamlanmadı ({type(exc).__name__}). Ayar dosyasını ve depo yollarını kontrol edin.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
