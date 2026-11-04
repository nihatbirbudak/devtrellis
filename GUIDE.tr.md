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

Yeni model işi başlatmadan önce araştırma formundaki **Toplulukta hazır araştırma ara** seçeneğini kullan. Yalnız açıkça gönderdiğin arama metni topluluk sunucusuna gider. Sonucu okumak ve yerel kütüphanene almak model çağırmaz. **Bunu temel alarak araştır** önceki raporu güvenilmeyen başlangıç bağlamı olarak yeni, düzenlenebilir bir isteğe taşır. Yeni isteği başlatmak seçtiğin sağlayıcının limitlerini kullanır.

Raporlarda kaydedilmiş model adı, dil, tarih, kaynaklar ve kanıt denetimi görünür. Sağlayıcıyı değiştirmek eski raporun modelini değiştirmez. Modeli kaydedilmemiş eski raporlarda tahmin yapılmaz.

Model adı veya fiyatı doğruluk puanı değildir. Asıl kaynakları, tarihleri, alıntıları, desteklenmeyen iddiaları ve sınırlamaları incele. Otomatik kanıt denetimi bağımsız insan doğrulaması anlamına gelmez.

**Yeniden araştır**, ayrı istek açar. Farklı model istiyorsan önce sağlayıcıyı değiştir; soruyu gözden geçirip gönder. Önceki rapor korunur. Yeni araştırma kendi limitlerini kullanır ve yine yetersiz kanıtla sonuçlanabilir.

Rapor hazır olunca **Yerelde tut** seçimi onu özel bırakır. **Önizle ve paylaş**, dosya taşımadan ve hesap açmadan araştırmayı, kaynakları, kayıtlı modeli ve tarihi hazırlar. Yazar etiketi varsayılan olarak anonimdir. Metni gözden geçirip gerekirse düzenle; araştırma metninin CC BY 4.0 ile paylaşılmasına açıkça izin verdiğinde incelemeye gönderilir. Kaynakların kendi hakları korunur. Diğer kayıtlar, ham sohbetler, özel araştırma bağlamı ve API bilgileri eklenmez. Yaygın e-posta/anahtar/dosya yolu kalıpları önizlemeden önce kaldırılır; bütün kişisel bilgileri bulduğu iddia edilmez. Metindeki adları ve özel ayrıntıları kontrol et. Anonim yazar etiketi ağ anonimliği sağlamaz; sunucu ve ağ sağlayıcıları bağlantıyı görebilir.

Gönderi takibi ve geri çekme **Gönderimlerim** alanındadır. İşlem anahtarı işletim sisteminin güvenli kasasına kaydedilir; Windows Credential Manager, macOS Keychain veya Linux Secret Service/KWallet gerekir. Düz metin dosyaya anahtar yazılmaz. Başarısız gönderimi yeniden denemek aynı katkıyı tekrar oluşturmaz. Paylaşılan metin düzenlenirse eski kanıt denetimi doğrulanmamış olur. Eski sürüm 1/2 dosyaları gelişmiş seçenek olarak kullanılabilir. [Uyumlu asistanını MCP ile bağla](MCP.tr.md).

## Sosyal medya veya haber bağlantısını incele

1. Yeni araştırmada somut soru ve konu seç.
2. Herkese açık HTTPS bağlantısını ve kontrol edilecek iddiayı ekle. Örnek: “Bu robotun yayımlanmış testi gerçekten otonom çalıştığını gösteriyor mu?”
3. Özellikle Instagram/video için kullanmaya hakkın olan kısa açıklamayı, altyazıyı veya dökümü ekle.
4. Bağlantının erişim durumunu, bağımsız kaynakları ve açık kalan soruları incele.

Yalnız erişilebilen kamusal sayfa metni okunur; **videonun görüntüsü ve sesi incelenmez**. Eklediğin döküm doğrulanmamış bağlamdır; videonun gerçekliğinin kanıtı değildir. Giriş duvarı, robots kısıtı, erişilemeyen sayfa ve güvensiz adresler bildirilir; atlatılmaz. Hiçbir içerik kendiliğinden toplulukta yayımlanmaz.

Bağımsız keşif, geliştirici kaynak kataloğu ve OpenAlex, Semantic Scholar, Crossref ile sınırlı. Bütün haberleri ve sosyal medyayı kapsayan arama değildir. Kanıt bulunmaması iddianın çözümsüz kaldığı anlamına gelir; ilgisiz makale veya paylaşımın kendisi kanıt sayılmaz. Kapsam dışındaki olaylar uygulamanın henüz bulamadığı kaynaklar gerektirebilir.
