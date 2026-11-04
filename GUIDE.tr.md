# Araştırma rehberi

[English](GUIDE.md) · [Kurulum](README.tr.md)

## NVIDIA'nın ücretsiz prototip API'siyle başla

Ücretli model aboneliği zorunlu değil. NVIDIA Developer Program üyeleri barındırılan API'leri ücretsiz prototipleme için kullanabiliyor. Erişim ve kota hesap/model koşullarına bağlı; sınırsız ücretsiz üretim sunucusu anlamına gelmiyor. [NVIDIA açıklaması](https://docs.api.nvidia.com/nim/docs/product)

1. [NVIDIA Build model sayfasını](https://build.nvidia.com/nvidia/nemotron-3-ultra-550b-a55b) aç. DevTrellis şu anda `nvidia/nemotron-3-ultra-550b-a55b` kullanıyor.
2. **Get API Key** seçeneğiyle giriş yap veya geliştirici hesabı aç; kendi anahtarını kopyala. [Resmî başlangıç yönergesi](https://docs.api.nvidia.com/nim/docs/api-quickstart) adımları açıklıyor.
3. Kurulumdan sonra uygulama klasöründe aşağıdakileri çalıştır. Anahtar gizli girişle alınır; komut geçmişine yazılmaz:

   ```powershell
   .\.venv\Scripts\python.exe -X utf8 nvidia_anahtar.py --save
   .\.venv\Scripts\python.exe -X utf8 calistir.py sun --elle-toplama
   ```

   `--save`, Windows Kimlik Bilgisi Yöneticisi'ndeki `ufuk-nvidia` / `api-key` kaydını kullanır. Kaydetmek istemezsen `nvidia_anahtar.py` komutunu bayraksız çalıştır; uygulamayı geçici anahtarla başlatır. Unix'te `.venv/bin/python nvidia_anahtar.py` kullan. Mevcut verin/portun için `--db` ve `--port` ekleyebilirsin. Barındırılan API için ekran kartı veya Docker gerekmez.

4. `http://127.0.0.1:8781/platform` sayfasında **Araştırma sağlayıcısı** bölümünü aç. **NVIDIA API** ve rapor dilini seçip kaydet. Claude/Codex hesabın varsa onları da seçebilirsin.
5. Ayrıntılı çalışma alanında küçük bir araştırma başlat. Hazır kayıtları okumak model kotası harcamaz. Eleme, araştırma ve kanıt denetimi birkaç çağrı yapabilir. Yerel deneme sınırı ve NVIDIA'nın kotası birlikte geçerlidir.

Anahtar/kota sorunu görünür hata olarak bildirilir; ücretli sağlayıcıya kendiliğinden geçilmez. Ortamda eski `UFUK_NVIDIA_API_KEY` veya `NVIDIA_API_KEY` varsa Windows kasasından önce kullanılır. Eski ortam değerini kaldırıp uygulamayı yeniden başlat.

[API deneme koşulları](https://assets.ngc.nvidia.com/products/api-catalog/legal/NVIDIA%20API%20Trial%20Terms%20of%20Service.pdf), üretim kullanımını ve deneme çıktılarının dağıtımını sınırlar. Çıktıyı toplulukta yayımlamadan veya gerçek kullanıcılara hizmet vermeden önce geçerli koşulları kontrol et; uygun erişim kullan. Yazılımın MIT lisansı sağlayıcı koşullarını değiştirmez. Seçilen metin sağlayıcıya gider; kamusal ve hassas olmayan içerik kullan. Anahtarını araştırmaya veya paylaşım kartına koyma.

## Model, kanıt ve yeniden araştırma

Raporlarda kaydedilmiş model adı, dil, tarih, kaynaklar ve kanıt denetimi görünür. Sağlayıcıyı değiştirmek eski raporun modelini değiştirmez. Modeli kaydedilmemiş eski raporlarda tahmin yapılmaz.

Model adı veya fiyatı doğruluk puanı değildir. Asıl kaynakları, tarihleri, alıntıları, desteklenmeyen iddiaları ve sınırlamaları incele. Otomatik kanıt denetimi bağımsız insan doğrulaması anlamına gelmez.

**Yeniden araştır**, ayrı istek açar. Farklı model istiyorsan önce sağlayıcıyı değiştir; soruyu gözden geçirip gönder. Önceki rapor korunur. Yeni araştırma kendi limitlerini kullanır ve yine yetersiz kanıtla sonuçlanabilir.

**Paylaşım taslağı oluştur**, tamamlanan raporu ayrı yerel karta kopyalar. Gözden geçir; yazar adını ve içerik iznini ekleyip önizle/indir. Rapor metni değişirse eski kanıt denetimi doğrulanmamış olarak işaretlenir. Ortak kartın model bilgisi katkıcının beyanıdır; imzalı sertifika değildir. Bu bilgi sürüm 2 paylaşım dosyasında taşınır; eski sürüm 1 dosyaları da açılır. Topluluk kartını indirip kendi uygulamana aktararak yeniden araştırabilirsin.

## Sosyal medya veya haber bağlantısını incele

1. Yeni araştırmada somut soru ve konu seç.
2. Herkese açık HTTPS bağlantısını ve kontrol edilecek iddiayı ekle. Örnek: “Bu robotun yayımlanmış testi gerçekten otonom çalıştığını gösteriyor mu?”
3. Özellikle Instagram/video için kullanmaya hakkın olan kısa açıklamayı, altyazıyı veya dökümü ekle.
4. Bağlantının erişim durumunu, bağımsız kaynakları ve açık kalan soruları incele.

Yalnız erişilebilen kamusal sayfa metni okunur; **videonun görüntüsü ve sesi incelenmez**. Eklediğin döküm doğrulanmamış bağlamdır; videonun gerçekliğinin kanıtı değildir. Giriş duvarı, robots kısıtı, erişilemeyen sayfa ve güvensiz adresler bildirilir; atlatılmaz. Hiçbir içerik kendiliğinden toplulukta yayımlanmaz.

Bağımsız keşif, geliştirici kaynak kataloğu ve OpenAlex, Semantic Scholar, Crossref ile sınırlı. Bütün haberleri ve sosyal medyayı kapsayan arama değildir. Kanıt bulunmaması iddianın çözümsüz kaldığı anlamına gelir; ilgisiz makale veya paylaşımın kendisi kanıt sayılmaz. Kapsam dışındaki olaylar uygulamanın henüz bulamadığı kaynaklar gerektirebilir.
