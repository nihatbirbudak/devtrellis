"""DevTrellis topluluk hizmetini, davetli hesap oluşturmayı ve güvenli yedek kopyalarını yönetir."""

import argparse
import getpass
import os
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
    serve.add_argument("--source-memory-config", type=Path, help="Açıkça seçilmiş küçük kamu kaynak kataloğu; varsayılan kapalı")
    serve.add_argument("--source-memory-db", type=Path, help="Kaynak hafızasının ayrı SQLite dosyası; config ile birlikte gerekir")
    serve.add_argument("--objections-db", type=Path,
                       help="Varsayılan kapalı özel itiraz deposu; ayrı dizin ve ayrı yedek/saklama planı gerekir; topluluk backup kapsamına girmez")
    serve.add_argument("--rate-db", type=Path, help="Ayrı, özel izinli kalıcı istek sınırı deposu")
    serve.add_argument("--k1-config", type=Path,
                       help="Varsayılan kapalı K1 v4 katkı yolu: ayar dosyası (otomatik yayın ayarda ve varsayılan kapalı)")
    serve.add_argument("--k1-db", type=Path, help="K1 v4 katkılarının ayrı dizindeki SQLite dosyası; --k1-config ile birlikte")
    sub.add_parser("k1-settings-example", help="K1 ayar dosyasının varsayılan değerli örneğini yazdır")
    k1_status = sub.add_parser("k1-status", help="K1 yayın durumunu ve kuyruk sayılarını göster")
    k1_stop = sub.add_parser("k1-stop", help="Tek işlem: yeni otomatik yayını durdur; yayındaki kayıtlar yerinde kalır")
    k1_stop.add_argument("--reason", default="")
    k1_resume = sub.add_parser("k1-resume", help="Moderatör/komut satırı durdurmasını kaldır (ayar dosyasındaki anahtar kalır)")
    k1_process = sub.add_parser("k1-process", help="Denetim sırasını bir tur kapıdan geçir (sunucu işçisi yerine elle)")
    k1_process.add_argument("--public-url", required=True)
    k1_process.add_argument("--local-pilot", action="store_true")
    k1_reindex = sub.add_parser("k1-reindex", help="Adres ve arama dizinlerini yayındaki v4 kayıtlarından yeniden kur "
                                                   "(açılışta kendiliğinden yapılır; elle yalnız onarım için)")
    # K1 deposunun yedeği şifreli operatör yedeğindedir: araclar/community-backup.py --k1 (düz kopya komutu yok).
    for k1_command in (k1_status, k1_stop, k1_resume, k1_process, k1_reindex):
        k1_command.add_argument("--k1-db", type=Path, required=True)
        k1_command.add_argument("--k1-config", type=Path, required=True)
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
    requests_list = sub.add_parser("requests-list", help="Özel başvuru/destek kutusunu listele; iletişim ve mesaj gövdesi gösterilmez")
    requests_list.add_argument("--status", choices=["new", "in_review", "closed"], default="")
    requests_list.add_argument("--offset", type=int, default=0)
    requests_list.add_argument("--limit", type=int, default=25)
    requests_show = sub.add_parser("requests-show", help="Bir özel başvuru/destek mesajını JSON olarak göster")
    requests_show.add_argument("id")
    requests_status = sub.add_parser("requests-status", help="Özel başvuru/destek talebinin durumunu değiştir")
    requests_status.add_argument("id")
    requests_status.add_argument("status", choices=["new", "in_review", "closed"])
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
            from radar.topluluk_api import adresi_dogrula, uygulama
            from radar.kaynak_hafizasi_api import kaynak_hafizasini_hazirla
            import uvicorn
            if args.local_pilot and urlsplit(args.public_url).port != args.port:
                raise ValueError("Yerel pilot URL portu ile --port aynı olmalı.")
            adresi_dogrula(args.public_url, args.local_pilot)
            if args.db.resolve() == args.feed_db.resolve():
                raise ValueError("Haber deposu ve topluluk deposu ayrı dosyalar olmalı.")
            if args.objections_db is not None:
                from radar.arastirma_itiraz_api import itiraz_depo_yolunu_dogrula
                itiraz_depo_yolunu_dogrula(args.objections_db, args.db, args.feed_db, args.source_memory_db)
            memory = kaynak_hafizasini_hazirla(args.source_memory_config, args.source_memory_db, args.db, args.feed_db)
            proxy_token = os.environ.get("DEVTRELLIS_PROXY_TOKEN")
            rate_hex = os.environ.get("DEVTRELLIS_RATE_KEY")
            if not args.local_pilot and (not proxy_token or len(proxy_token) < 32 or not rate_hex or not args.rate_db):
                raise ValueError("Kamu hizmeti için güvenilen vekil belirteci ve kalıcı istek sınırı ayarı gerekli.")
            try:
                rate_key = bytes.fromhex(rate_hex) if rate_hex else None
            except ValueError:
                raise ValueError("İstek sınırı anahtarı en az 32 baytlık hex olmalı.") from None
            if (args.k1_config is None) != (args.k1_db is None):
                raise ValueError("--k1-config ve --k1-db birlikte verilmeli.")
            k1_settings = None
            if args.k1_config is not None:
                from radar.katki_v4 import ayarlari_oku
                k1_settings = ayarlari_oku(args.k1_config)
            app = uygulama(args.db, public_url=args.public_url, local_pilot=args.local_pilot,
                           feed_db_path=args.feed_db, source_memory=memory, objections_db_path=args.objections_db,
                           trusted_proxy_token=proxy_token, rate_db_path=args.rate_db, rate_key=rate_key,
                           k1_db_path=args.k1_db, k1_settings=k1_settings, k1_worker=k1_settings is not None)
            print("DevTrellis topluluk hizmeti başlıyor; otomatik model çağrısı yapılmaz.", flush=True)
            if k1_settings is not None:
                print("K1 v4 katkı yolu açık; otomatik yayın " +
                      ("ayarda açık (durdurma anahtarı ayrıca denetlenir)." if k1_settings.otomatik_yayin
                       else "kapalı: kapıdan geçenler moderasyona gider."), flush=True)
            uvicorn.run(app, host="127.0.0.1", port=args.port, workers=1, access_log=False, log_level="warning",
                        proxy_headers=False)
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
        elif args.command in ("requests-list", "requests-show", "requests-status"):
            if not args.db.is_file():
                raise ValueError("Topluluk veritabanı bulunamadı.")
            import json
            from radar.topluluk_talepleri import TalepDeposu
            requests = TalepDeposu(args.db)
            if args.command == "requests-list":
                result = requests.listele(status=args.status, offset=args.offset, limit=args.limit)
            elif args.command == "requests-show":
                result = requests.oku(args.id)
            else:
                detail = requests.durum_degistir(args.id, args.status)
                result = {key: detail[key] for key in ("id", "status", "created_at", "updated_at")}
            # Gönderilen metin komut veya terminal kontrol dizisi olarak yorumlanmaz.
            print(json.dumps(result, ensure_ascii=True))
        elif args.command == "k1-settings-example":
            import json
            from radar.katki_v4 import ayar_ornegi
            print(json.dumps(ayar_ornegi(), indent=2))
        elif args.command in ("k1-status", "k1-stop", "k1-resume", "k1-process", "k1-reindex"):
            import json
            from radar.katki_v4 import ayarlari_oku
            from radar.katki_v4_depo import KatkiDeposu
            settings = ayarlari_oku(args.k1_config)
            if args.command != "k1-process" and not args.k1_db.is_file():
                raise ValueError("K1 veritabanı bulunamadı.")
            if args.command == "k1-process":
                from radar import denetim
                from radar.topluluk_api import adresi_dogrula
                k1 = KatkiDeposu(args.k1_db, origin=adresi_dogrula(args.public_url, args.local_pilot))
                result = k1.isle(ayarlar=settings, denetci=denetim.denetle)
            else:
                # Durum ve durdurma bağlantı üretmez; kamu adresi gerekmez.
                k1 = KatkiDeposu(args.k1_db, origin="")
                result = (k1.durum(settings) if args.command == "k1-status" else
                          k1.durdur("cli", args.reason, settings) if args.command == "k1-stop" else
                          k1.yeniden_indeksle() if args.command == "k1-reindex" else k1.devam_et("cli", settings))
            print(json.dumps(result, ensure_ascii=True))
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
