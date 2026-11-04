"""Radarı bağımsız bağımlılıklarıyla başlatır, kaynakları toplar veya tutarlı yedek alır."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / ".deps"))


def main():
    parser = argparse.ArgumentParser(description="DevTrellis personal developer workspace")
    parser.add_argument("command", choices=["sun", "topla", "yedekle", "durum", "ilk-sayi"], nargs="?", default="sun")
    parser.add_argument("--port", type=int, default=8781)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "radar.sqlite3")
    parser.add_argument("--kaynak")
    parser.add_argument("--elle-toplama", action="store_true", help="Uygulama açıkken zamanlı toplamayı kapat")
    args = parser.parse_args()
    from radar.depo import Depo
    from radar.kaynak_havuzu import kaynaklari_oku
    sources = kaynaklari_oku()
    if args.command == "sun":
        import uvicorn
        from radar.sunucu import uygulama
        app = uygulama(args.db, sources, port=args.port, automatic_collection=not args.elle_toplama,
                       automatic_backup=True, automatic_workflow=not args.elle_toplama)
        print(f"DevTrellis: http://127.0.0.1:{args.port}/platform", flush=True)
        uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False, log_level="warning")
        return
    depo = Depo(args.db, sources)
    from radar.baslangic import kartlari_ekle
    kartlari_ekle(depo)
    if args.command == "topla":
        from radar.hizmet import Toplama
        from radar.kaynak_yonetimi import KaynakYonetimi
        from radar.toplayici import kaynaklari_yetkilendir
        kaynaklari_yetkilendir(sources + KaynakYonetimi(depo).ozel_kaynaklar())
        service = Toplama(depo)
        try:
            started = service.baslat(args.kaynak)
            if started and service.thread:
                service.thread.join()
            result = service.gorunum()
            if not started:
                result["message"] = "Başka bir tarama sürüyor; ikinci tarama başlatılmadı."
            print(json.dumps(result, ensure_ascii=False))
        finally:
            service.kapan()
    elif args.command == "ilk-sayi":
        from radar.baslangic import ilk_sayiyi_ekle
        cards = json.loads((ROOT / "radar" / "ilk_sayi.json").read_text(encoding="utf-8"))
        print(json.dumps(ilk_sayiyi_ekle(depo, cards), ensure_ascii=False))
    elif args.command == "yedekle":
        from radar.bakim import Bakim
        backup = Bakim(depo)
        if backup.yedekle():
            print(f"Tutarlı yedek: {backup.dosya()}")
    else:
        print(json.dumps(depo.gorunum()["stats"], ensure_ascii=False))


if __name__ == "__main__":
    main()
