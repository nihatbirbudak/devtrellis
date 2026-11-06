# Özel araştırma itirazları

[English](OBJECTIONS.md)

Bu isteğe bağlı HTTP/depo temeli, gerekçeli itirazı yayımlanmış tam araştırma kimliği ve sürümüne bağlar. Varsayılan kapalıdır. Açılması, insan gönderim/inceleme arayüzünün, model akışının veya üretim kabulünün tamamlandığı anlamına gelmez. Çağıran arayüz özel teslimden önce insan önizlemesini ve açık izni sağlamalıdır.

## Ayrı kurulum

Hizmeti başlatmadan mevcut seçenekleri incele:

    python -X utf8 topluluk.py serve --help

Açıkça seçilmiş loopback pilotu için:

    python -X utf8 topluluk.py --db data/community.sqlite3 serve --public-url http://127.0.0.1:8782 --local-pilot --feed-db data/feed.sqlite3 --objections-db private-objections/objections.sqlite3

--objections-db verilmezse yollar ve depo kapalıdır. Web kökü dışında, topluluk, haber ve kaynak hafızası dizinlerinden ayrı özel bir dizin kullan. Bu dizinde yalnız itiraz DB'si ve SQLite yan dosyaları bulunabilir; yedekleri ve günlükleri başka yerde tut. Sembolik bağlantı, junction ve veritabanı çakışmaları reddedilir. DB ilk kullanımda oluşturulur.

POSIX 0700/0600 veya korumalı Windows DACL erişimi işletmeci/sahibe; Windows'ta ayrıca SYSTEM ve yöneticilere sınırlar. Bu, veri şifrelemesi veya yetkili makine yöneticisinden koruma değildir. Kopyalanan yedeklere ayrıca izin uygulanmalıdır.

## İstemci sözleşmesi ve özel fiş

Teslimden önce tam kamu iddiasını, gerekçeyi, kaynak URL'lerini ve hedef rapor sürümünü kişiye göster. consent=true alanı istek biçimi koşuludur; modelin insan izni aldığının kanıtı değildir. MCP veya sunucu istemcisi için tarayıcı oturum denetimlerini atlayan istisna yoktur.

Entegrasyonu yapan istemci, secrets.token_urlsafe(32) eşdeğeri rastgele 43 karakterli URL-güvenli fişi üretip saklamalıdır. Fişi bilen kişi o itirazı okuyabilir veya geri çekebilir. Uygun özel yerel depoda tut; URL, sorgu, gövde, HTML, telemetri veya günlüğe ekleme. Sunucu yalnız hash'ini tutar ve fişi yanıtta geri vermez. Örnek istek hazırlarken fişi ekrana yazdırma.

Anonim çerez ve csrf_token, GET /api/community/session yolundan alınır. Gönderim, özel okuma ve geri çekmede bu çerez, tam hizmet Origin'i, X-Ufuk-CSRF, application/json, geçerli Content-Length ve tek X-DevTrellis-Objection-Receipt başlığı gerekir. Bu fişle yönetilen işlemler hesap gerektirmez.

| /api/community/objections altındaki yöntem ve yol | İstek / erişim |
|---|---|
| Temel yola POST | research_id, version, claim, reason, consent=true; isteğe bağlı sources |
| POST /read | Boş JSON nesnesi; fiş ve oturum denetimleri |
| POST /withdraw | Güncel itirazdan expected_revision; aynı denetimler |
| GET /review | Güncel doğrulanmış moderatör; isteğe bağlı status, offset, limit |
| GET /review/{objection_id} | Güncel doğrulanmış moderatör |
| POST /review/{objection_id}/decision | Moderatör, aynı Origin/oturum/CSRF, tam itiraz revizyonu ve rapor bağı |
| GET /corrections/{research_id}/{version} | Kamuya onaylanmış düzeltme izi; isteğe bağlı offset/limit |

Kimlikler 32 küçük hexadecimal karakter, sürümler pozitif tam sayıdır. Hedef, erişilebilir yayımlanmış araştırma sürümü olmalıdır. İddia en fazla 800, gerekçe 2.000 karakterdir; title/url taşıyan en fazla üç kamusal HTTPS kaynak eklenebilir. Gönderim gövdesi en fazla 16 KiB'dır. Fazladan/özel bağlam alanları reddedilir. Kalıp denetimi keyfi metnin kişisel bilgi içermediğini garanti etmez.

Yeni teslim 201 ve pending döndürür; aynı fişle birebir tekrar 200 ve duplicate=true döndürür. Aynı fiş altında değiştirilmiş içerik veya rapor sürümü reddedilir. Geri çekme en son itiraz revizyonunu ister; eski değer 409 döndürür. Fiş, itiraz kimliği ve revizyonundan ayrıdır.

## Moderasyon ve kamu düzeltme izi

Özel metni yalnız güncel doğrulanmış moderatör inceleyip karar verebilir: pending → in_review/rejected, ardından in_review → accepted/rejected. Karar JSON'u expected_revision, research_id ve version alanlarıyla tam kayda bağlanır.

Kabul ayrıca public_summary (en fazla 600 karakter) ve correction={research_id, version} gerektirir. Düzeltme önceden yayımlanmış olmalıdır: aynı raporun daha yeni sürümü veya lineage alanında itiraz edilen tam rapor/sürümü belirten araştırma. Kabul yalnız izi kaydeder; araştırma yayımlamaz veya metni yeniden yazmaz.

Özellik açıkken kamu kart okumaları en fazla üç public_corrections kaydı içerir; kalanlar corrections yolundan sayfalanır. Yalnız onaylanan özet, düzeltme bağı, inceleme tarihi ve geri çekilme işareti kamuya açıktır. Özel iddia/gerekçe/kaynak metni ve fiş hash'i buradan verilmez. Sonradan geri çekme özel metni temizler; onaylanmış düzeltme izi withdrawn=true ile kalır.

## Saklama, sınırlar ve geri yükleme koşulu

Özel iddia, gerekçe ve kaynaklar ilk teslimden 90 gün sonraki ilk depo işleminde temizlenir; yeniden teslim süreyi uzatmaz. Pending/in_review kayıtları expired olur. Fiş/içerik hash'leri, durum, moderasyon izi ve onaylanmış kamu düzeltmeleri kalır. Arka plan temizleme zamanlayıcısı yoktur. Geri çekme özel metni hemen temizler.

Gönderimde bağlantı adresine dayalı bellek içi sınırlar (dakikada 4, günde 20) ve kalıcı kuyruk sınırları vardır: toplam 5.000 kayıt, 200 açık kayıt, günde 100 teslim, rapor sürümü başına günde 10 teslim. Diğer hizmet sınırları da uygulanır. İtiraz deposu IP kaydetmez; hız sınırı bağlantı adresini geçici kullanır. CLI erişim günlüğü kapalıdır; ters vekili gövde, çerez, CSRF ve fiş başlıklarını kaydetmeyecek biçimde yapılandır.

Mevcut topluluk backup/restore komutu bu DB'yi kapsamaz. Kamuya açmadan önce ayrı korumalı yedekleri, saklama sürelerini ve yeni özel dizine gösterilmiş geri yüklemeyi belirle. Sahiplik/izinleri, saklanan fişle okuma/geri çekmeyi, süresi dolan metnin temizlenmesini, moderatör erişimini ve eşleşen topluluk yedeğine göre düzeltme bağlarını doğrula. Geri yüklemeyi canlı verinin üzerine deneme. Etkin DB temizliği eski yedekleri silmez; kopyalar kalırken tam silinme iddia etme.

Uygulama: radar/arastirma_itiraz_api.py ve radar/arastirma_itirazlari.py. Ağsız sözleşme testleri: tests/test_arastirma_itiraz_api.py ve tests/test_arastirma_itirazlari.py. Gerçek insan arayüzü kabulü, host yapılandırması ve ayrı geri yükleme dağıtım koşulu olarak kalır.
