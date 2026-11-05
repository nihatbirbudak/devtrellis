"""DevTrellis topluluk hizmetini, davetli hesap oluşturmayı ve güvenli yedek kopyalarını yönetir."""

import argparse
import getpass
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / ".deps"))


def gizli_parola_oku():
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            password = getpass.getpass("Yeni parola (en az 12 karakter): ")
            repeated = getpass.getpass("Parolayı tekrar girin: ")
        except getpass.GetPassWarning as exc:
            raise ValueError("Parola gizleyen etkileşimli bir terminal gerekli.") from exc
    if password != repeated:
        raise ValueError("Parolalar eşleşmiyor.")
    return password


def main(argv=None):
    parser = argparse.ArgumentParser(description="DevTrellis topluluk hizmeti")
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "community.sqlite3")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="HTTPS hizmetini veya açıkça seçilmiş yerel pilotu başlat")
    serve.add_argument("--public-url", required=True)
    serve.add_argument("--local-pilot", action="store_true", help="Yalnız loopback üzerinde HTTP pilotuna izin ver")
    serve.add_argument("--port", type=int, default=8782)
    serve.add_argument("--feed-db", type=Path, default=ROOT / "data" / "community-feed.sqlite3")
    account = sub.add_parser("account-add", help="Davet edilen kişinin hesabını gizli parola istemiyle oluştur")
    account.add_argument("username")
    account.add_argument("--display-name", required=True)
    account.add_argument("--role", choices=["contributor", "moderator"], default="contributor")
    disable = sub.add_parser("account-disable", help="Davetli hesabı kapat ve açık oturumlarını iptal et")
    disable.add_argument("username")
    reset = sub.add_parser("password-reset", help="Gizli istemle parolayı değiştir ve önceki oturumları iptal et")
    reset.add_argument("username")
    publish = sub.add_parser("publish-reviewed", help="Yöneticinin incelediği anonim kamu raporunu dosya özetiyle sabitleyerek yayımla")
    publish.add_argument("packet", type=Path)
    publish.add_argument("--sha256", required=True, help="İncelenen dosyanın SHA256 özeti")
    publish.add_argument("--note", required=True, help="Kaynak incelemesinin kısa notu")
    backup = sub.add_parser("backup", help="Yeni dosyaya tutarlı SQLite yedeği al")
    backup.add_argument("target", type=Path)
    restore = sub.add_parser("restore", help="Yedeği yeni veritabanına aç; mevcut dosyayı değiştirme")
    restore.add_argument("source", type=Path)
    args = parser.parse_args(argv)
    from radar.topluluk_depo import ToplulukDeposu, veritabani_kopyala
    try:
        if args.command == "serve":
            if not 1 <= args.port <= 65535:
                raise ValueError("Port 1–65535 arasında olmalı.")
            from urllib.parse import urlsplit
            from radar.topluluk_api import uygulama
            import uvicorn
            if args.local_pilot and urlsplit(args.public_url).port != args.port:
                raise ValueError("Yerel pilot URL portu ile --port aynı olmalı.")
            app = uygulama(args.db, public_url=args.public_url, local_pilot=args.local_pilot, feed_db_path=args.feed_db)
            print("DevTrellis topluluk hizmeti başlıyor; otomatik model çağrısı yapılmaz.", flush=True)
            uvicorn.run(app, host="127.0.0.1", port=args.port, workers=1, access_log=False, log_level="warning",
                        proxy_headers=not args.local_pilot, forwarded_allow_ips="127.0.0.1")
        elif args.command == "account-add":
            password = gizli_parola_oku()
            ToplulukDeposu(args.db).hesap_ekle(args.username, args.display_name, args.role, password)
            print("Davetli hesap oluşturuldu.")
        elif args.command == "account-disable":
            if not args.db.is_file():
                raise ValueError("Topluluk veritabanı bulunamadı.")
            ToplulukDeposu(args.db).hesap_kapat(args.username)
            print("Hesap kapatıldı ve oturumları iptal edildi.")
        elif args.command == "password-reset":
            if not args.db.is_file():
                raise ValueError("Topluluk veritabanı bulunamadı.")
            ToplulukDeposu(args.db).parola_yenile(args.username, gizli_parola_oku())
            print("Parola yenilendi ve önceki oturumlar iptal edildi.")
        elif args.command == "publish-reviewed":
            import json
            from radar.editor_yayini import incelenen_dosyayi_yayinla
            result = incelenen_dosyayi_yayinla(args.db, args.packet, args.sha256, args.note)
            print(json.dumps({"id": result["card"]["id"], "version": result["card"]["version"],
                              "status": result["card"]["status"], "duplicate": result["duplicate"]}))
        elif args.command == "backup":
            if not args.db.is_file():
                raise ValueError("Topluluk veritabanı bulunamadı.")
            veritabani_kopyala(args.db, args.target)
            print("Yedek alındı ve bütünlüğü doğrulandı.")
        elif args.command == "restore":
            veritabani_kopyala(args.source, args.db, restore=True)
            print("Yeni veritabanına geri yüklendi; önceki oturumlar kapatıldı. Hizmeti bu dosyayla başlatın.")
        return 0
    except (ValueError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
