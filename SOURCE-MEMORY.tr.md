# Talep üzerine kaynak hafızası pilotu

## İstemci ve sunucuyu birlikte güncelleme

U1 oturum değişikliğinde topluluk sunucusu ile kaynak hafızası istemcilerini aynı sınanmış paketten güncelle; sonra kontrol özelliğini yeniden aç. Kurulu r5 istemcisi `GET /api/community/session` kullanır. Yeni sunucuda bu okuma çerez oluşturmaz ve `csrf_token: null` dönebilir. Yeni istemci `{}` JSON gövdesi ve hizmetin `Origin` başlığıyla açıkça `POST` yapar; ardından CSRF değeriyle kaynak kontrolünü gönderir. Yalnız beceri dosyasını güncellemek yetmez; yerel uygulama/MCP köprüsü yeni istemci kodunu yüklemeli.

1. Bakım sırasında kaynak kontrolünü kapalı tut. Eski sunucu ve istemci sürümlerini, ayarları ve kabul edilmiş yedeği koru.
2. Aynı kabul edilmiş paketi sunucuya ve ilgili yerel istemcilere kur. Bu belge kendiliğinden kurulum veya canlı geçiş yapmaz.
3. Yerel uygulama ve MCP'yi kullanan süreçleri yeniden başlat; çalışan dosya yolu/sürümü doğrula. Dosyanın değişmesi açık sürecin yenilendiğini göstermez.
4. Gerçek sunucuda çerezsiz GET, açık POST oturumu ve yetkili kaynak kontrolünü doğrula. Gerçek HTTPS/vekil zinciri de sınanmalı. Sayfa okuyabilmek kaynak kontrolü kabulü değildir.
5. Kontrol geçerse özelliği aç. Başarısızsa kapalı tutup uyumlu önceki uygulama grubuna dön; yeni şemalı DB'yi desteklemeyen eski kodla açma.

Hazırlık turu kurulu r5'i değiştirmedi. Gerçek dağıtım ve host kabulü ayrı adımlardır.

[English](SOURCE-MEMORY.md)

Bu isteğe bağlı alfa özelliği, işletmecinin seçtiği az sayıda kamu sayfasının kontrol edilmiş temsilini tutar. Araştırma kütüphanesinden ayrıdır. Yapılandırma ve ayrı veritabanı birlikte verilmedikçe kapalıdır; kendiliğinden tarayıcı, model veya zamanlayıcı başlatmaz. Mevcut araştırma araması ve asistan araçları aynı kalır.

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

Bu pilotta kamuya açık özet yazma yolu, kendiliğinden açılan model özeti veya kullanıcının gezintisini otomatik toplama yoktur. İsteğe bağlı MCP araçları artık mevcut okuma/kontrol API'sine bağlanır. Dahili özet kaydetme yöntemi ancak gerçek kamu içeriği incelemesi ve insan izni sonrasındaki güvenilir entegrasyon içindir. Modelin boolean onayı insan izni sayılmaz; mevcut katkıların insanlı önizleme/moderasyon yolu korunur.

`checks` kontrol denemelerini, `downloaded_bytes` alınabilen hedef yanıt gövdelerini sayar; robots, başlıklar ve yarım aktarım dahil toplam ağ faturası değildir. Okuma/kontrol mekanizması model çağırmadığından `model_calls` sıfırdır. Genel token tasarrufu ancak gerçek kullanım karşılaştırmasıyla ölçülebilir.

## İsteğe bağlı MCP erişimi

Sunucuda ayrı yapılandırma ve DB verilmedikçe pilot kapalı kalır. Uyumlu yerel
köprüde mevcut MCP başlatma argümanlarına `--source-memory-tools` eklenmesi iki ek
aracı açar; uzak pilotu etkinleştirmez ve arka plan kontrolü başlatmaz. Varsayılan
altı araçlı küçük arayüz için kapalı bırakılabilir.

- `read_source_memory()` en fazla 20 incelenmiş kaynak tanımını listeler; yayıncıya gitmez.
- `research_id` ve `version` birlikte verilirse tam raporun kaynak URL/dilini katalogla
  eşler. URL parçası (#...) çıkarılır; sorgu parametreleri aynen karşılaştırılır.
  Bu yalnız URL/dil eşlemesidir; eski raporun o hash'ten üretildiğini kanıtlamaz.
- `source_id` ile kayıt ve yalnız güncel, yeniden kullanılabilir incelenmiş özet okunur.
  Eski/hatalı kontrolün özeti güncelmiş gibi taşınmaz.
- `check_source_freshness(source_id, max_age_seconds=3600)` yalnız gerektiğinde açık
  kontrol yapar. Ortak önbelleği güncelleyip yayıncıya gidebilir; model çağırmaz,
  rapor yayımlamaz, kullanım olayı göndermez ve serbest URL almaz.

Sabit kamu hizmeti istemcisi kontrol için geçici anonim oturum ve mevcut CSRF
sınırını kullanır. Kullanıcının tarayıcı çerezini veya kimlik bilgisini almaz.
Yönlendirme, sıkıştırılmış/sınırsız yanıt ve beklenmeyen şema reddedilir. Kapalı
pilot veya hata otomatik tekrar yapmadan bildirilir. Yerel yetenek bilgisi köprü
desteğini belirtir; `remote_enabled="unknown"` sunucu çalışıyor anlamına gelmez.

## İsteğe bağlı özel özet hazırlama

Kaynak pakette radar/kaynak_ozeti_hazirlama.py içindeki KaynakOzetiHazirlayici bulunur. Çağıranın sağladığı güvenilir model adaptörünü kullanan bir Python entegrasyon API'sidir; hazırlama CLI komutu, HTTP yolu veya MCP aracı yoktur. Okuma/kontrol pilotunu açmak model üretimini açmaz. Nesne oluşturmak ağ veya model çağrısı yapmaz.

Çağıran, mevcut KaynakHafizasi nesnesini, kendine ayrılmış boş veya sahipliği doğrulanmış özel dizindeki açık iş DB yolunu ve model işlevini sağlar. Kaynak ve iş veritabanları farklı olmalıdır. Uygulama memory ve adapter nesnelerini yapılandırdıktan sonraki kısa entegrasyon çağrısı:

    from radar.kaynak_ozeti_hazirlama import KaynakOzetiHazirlayici

    record = memory.oku("selected-document")
    preparer = KaynakOzetiHazirlayici(
        memory, "private-summary/jobs.sqlite3", adapter, daily_limit=10
    )
    draft = preparer.prepare(
        "selected-document",
        expected_revision=record["source_revision"],
        expected_hash=record["content_hash"],
    )

Seçili kaynağın önceden kontrol edilmiş sürümü ve hash'i bulunmalıdır; bilinmeyen kayıt bu değerleri sağlayamaz. İlk hazırlama, aynı hash'e sahip tam HTTP 200 temsilini gerektiren bir sınırlı kaynak kontrolü ve ardından bir adaptör çağrısı yapar. Yayıncı politikası, çıkarım, kaynak bütçesi ve eşzamanlılık sınırları korunur; robots kontrolü ek ağ isteği yapabilir. Kaynak metni bellekte kalır, iş DB'sine yazılmaz.

Adaptör yalnız sabit sistem talimatını, güvenilmeyen kamu belgesini, tam kaynak bağını, istenen özet alanlarını, süre ve çıktı token sınırını alır. Özet, sağlayıcı/model kökeni ve isteğe bağlı giriş/çıkış token sayılarını döndürür. Verilen sınırları uygulamalı; isteme araç, özel konuşma veya kimlik bilgisi eklememelidir. Hazırlayıcı keyfi senkron işlevi zorla durduramaz; süresi geçen sonuç reddedilir ve deneme günlük bütçeyi tüketmiş kalır. Sağlayıcının saklama politikası ve gerçek kimlik bilgisi yönetimi entegrasyonu yapan işletmecinin sorumluluğundadır.

Başarılı sonuçta draft_status=private_pending, human_review_required=true ve publication_performed=false bulunur. summary_reusable ve public_summary_reusable false kalır. Kamu özet tablosuna yazılmaz ve katkı onaylanmaz. Sağlayıcı/model adı adapter_reported bilgisidir; bağımsız kimlik doğrulaması değildir. Eksik token sayısı unknown kalır; karakterden token hesaplanmaz.

Aynı kaynak sürümü/hash'i için henüz süresi dolmamış taslak, kayıtlı kaynak güncel ve hatasızsa yeniden kullanılır: bu tekrar sıfır yayıncı isteği ve sıfır model çağrısı yapar. Çağrıya ait metrics sıfırdır; generation_metrics ilk üretimin bildirilen kullanımını korur. Tekrar kullanım taslağı incelenmiş kamu özetine dönüştürmez.

Modelden sonra zorunlu ikinci GET yoktur. Son denetim veritabanındaki kaynak kimliğini, hash'i, güncelliği ve hata durumunu karşılaştırır. Üretim sırasında yayıncıda gerçekleşip henüz bu veritabanına yansımayan değişiklik görülemeyebilir. İnsan incelemesi, yayın anında yeniden kaynak kontrolü ve kamuya yayın entegrasyonu ayrı, tamamlanmamış kabul adımlarıdır; bu belge gerçek sağlayıcı veya host kabulü yapıldığını göstermez.

### Özel iş verisi ve geri yükleme

- Hazır taslak yedi gün, başarısız kayıt bir gün sonra süre aşımına uğrar. Taslağı tekrar okumak süreyi uzatmaz.
- Süresi dolan ready, failed ve running kayıtları bir sonraki hazırlamanın iş alma transaction'ında temizlenir. Etkin işler korunur. Arka plan temizleme zamanlayıcısı yoktur.
- En fazla 200 iş kaydı tutulur. Varsayılan günlük deneme bütçesi 10'dur (1–100 ayarlanabilir); aynı anda en fazla iki etkin iş ve varsayılan 180 saniye iş süresi vardır (60–600). Dolu kuyruk ağ/model çağrısı yapmadan busy döndürür.
- Özel dizinde yalnız bileşenin sahiplik işareti, veritabanı ve SQLite yan dosyaları bulunur. POSIX izinleri veya korumalı Windows DACL erişimi sınırlar; veri şifrelemesi ya da kopyalanan yedeklerin güvenliği anlamına gelmez.
- İş kaydı sınırlı ve doğrulanmış taslağı/kökeni tutar; kaynak gövdesini, ham model yanıtını veya özel soruyu tutmaz. Etkin DB'deki süre temizliği eski yedekleri silmez.
- Topluluk backup/restore komutları kaynak hafızası veya özel iş veritabanını kapsamaz. İşletime geçmeden ayrı korumalı kopya, saklama süresi ve gösterilmiş geri yükleme belirlenmelidir. Sahiplik işaretiyle ayrı dizine geri yükle; geri alınan taslak kullanılmadan kaynak bağı ve süre denetimini doğrula.

Ağsız adaptör örnekleri ve hata durumları tests/test_kaynak_ozeti_hazirlama.py içindedir. Bunlar gerçek NVIDIA, Claude veya Codex modeli çalıştırıldığına kanıt değildir.
