# Talep üzerine kaynak hafızası pilotu

[English](SOURCE-MEMORY.md)

Bu isteğe bağlı alfa özelliği, işletmecinin seçtiği az sayıda kamu sayfasının kontrol edilmiş temsilini tutar. Araştırma kütüphanesinden ayrıdır. Yapılandırma ve ayrı veritabanı birlikte verilmedikçe kapalıdır; tarayıcı, model veya zamanlayıcı başlatmaz. Mevcut araştırma araması ve asistan araçları aynı kalır.

## Ayrı pilotu başlatma

Yalnız gözden geçirilmiş tam kamu URL’lerini seç. Alan adı mevcut kaynak kataloğunun izinli listesinde bulunmalı. Oturumlu veya kişiselleştirilmiş sayfalar, kimlik veya özel sorgu parametreleri, yönlendirmeler ve kesilmiş makale metni kabul edilmez. İlk kapsam en fazla 20 URL, 40.000 karaktere kadar HTML makale metni ve tek bölümlü PostgreSQL DocBook belgelerinin sınırlı biçim desteğidir. Belirsiz veya desteklenmeyen yapılar reddedilir; genel amaçlı bir web sayfası çıkarıcısı değildir. İndirme ayrıca sınırlıdır.

PostgreSQL uyarlaması yalnız `www.postgresql.org/docs/<sayısal-sürüm>/<dosya>.html` adresinde tek doğrudan ana bölüm ve tek ana başlık için geçerlidir. Basit satır içi metin ve açıklamadaki doğrudan paragraflardan oluşan, dolu ve sıralı bir-terim/bir-açıklama `dt`/`dd` çiftleri desteklenir; boş, bozuk, iç içe veya desteklenmeyen listeler reddedilir. Tablolar, görseller ve desteklenmeyen başlık düzeyleri kapsam dışıdır. `article-v4-docbook-definitions` çıkarım sürümü özgün pre/code metnini ve tanım çifti yapısını içerik hash’ine katar; anlamlı boşluk, terim veya açıklama değişimi önceki özeti geçersiz kılar. Genel HTML çıkarım kapsamı aynı kalır; makale dışındaki ek kod değişimi de temkinli olarak kaydı geçersiz kılabilir.

Web kökünün dışında `source-memory.json` oluştur:

```json
{
  "schema_version": 1,
  "daily_limit": 100,
  "sources": [
    {"id": "selected-document", "url": "https://www.postgresql.org/docs/18/textsearch-indexes.html", "language": "en"}
  ]
}
```

Adres yalnız örnektir; ilgili sayfanın bu pilotla uyumlu olduğu garanti edilmez. Sürüm ve dil parametreleri korunur. Hafıza, topluluk ve haber veritabanları farklı dosyalar olmalı.

```powershell
python -X utf8 topluluk.py --db data/pilot-community.sqlite3 serve --public-url http://127.0.0.1:8782 --local-pilot --feed-db data/pilot-feed.sqlite3 --source-memory-config source-memory.json --source-memory-db data/pilot-memory.sqlite3
```

İki hafıza seçeneğini de vermezsen özellik açılmaz. Veriyi özel işletmeci dizininde tut; gerçek kullanıma geçerse yedek planına dahil et.

## Okuma ve kontrol

- `GET /api/community/source-memory`: seçili kaynakları ağ isteği yapmadan listeler.
- `GET /api/community/source-memory/{id}`: kısa kaydı ve varsa uygun özeti ağ isteği yapmadan okur.
- `POST /api/community/source-memory/{id}/check`: yalnız `max_age_seconds` (60–86400, varsayılan 3600) ve `freshness_required` (boolean, varsayılan true) alır. Mevcut aynı Origin, JSON ve anonim oturumun `X-Ufuk-CSRF` denetimi geçerlidir. Hesap gerekmez; keyfi URL, soru ve özel bağlam almaz.

Güncellik gerekmiyorsa kontrol yapılmaz. Yeterince yakın kontrol varsa ortak kayıt kullanılır. Gerektiğinde koşullu GET ve metin hash’i değişimi denetlenir; aynı temsile eşzamanlı istekler bir kontrolü paylaşır. Hata eski başarılı kontrolü yenilemez ve kısa tekrar bekleme süresi başlatır. Günlük ortak bütçe kalıcıdır.

`fresh`, kaynağın kontrol zamanını anlatır; doğruluk puanı değildir. `claims_verified` daima false kalır. Başarılı kontrol (`check_succeeded`) ile sonradan yeniden kullanılabilirlik (`fresh`) farklıdır: yayıncı her kullanımda kontrol isteyebilir. Sayfanın değişmemesi, dünyada başka bir gelişme olmadığını kanıtlamaz.

## Özet ve gizlilik

Kaynak gövdesi ve kullanıcı sorusu bu hafızada saklanmaz. Özet tam kaynak sürümü/hash değerine bağlanır; kaynak değişince geçersizleşir. Özet düzeltmeleri sürümlenir. Hata eski özeti yeni doğrulanmış saymaz.

Bu pilotta kamuya açık özet yazma yolu, otomatik model özeti, MCP kaynak hafızası aracı veya kullanıcının gezintisini otomatik toplama yoktur. Dahili özet kaydetme yöntemi ancak gerçek kamu içeriği incelemesi ve insan izni sonrasındaki güvenilir entegrasyon içindir. Modelin boolean onayı insan izni sayılmaz; mevcut katkıların insanlı önizleme/moderasyon yolu korunur.

`checks` kontrol denemelerini, `downloaded_bytes` alınabilen hedef yanıt gövdelerini sayar; robots, başlıklar ve yarım aktarım dahil toplam ağ faturası değildir. Bu mekanizma model çağırmadığından `model_calls` sıfırdır. Genel token tasarrufu ancak gerçek kullanım karşılaştırmasıyla ölçülebilir.
