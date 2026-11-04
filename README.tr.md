# DevTrellis

**Keşfet. Araştır. Birlikte geliştir.**

[English](README.md) · [MIT lisansı](LICENSE) · [Araştırma ve ücretsiz NVIDIA rehberi](GUIDE.tr.md) · [Topluluk sunucusu](COMMUNITY.md)

**[Canlı topluluk önizlemesi: devtrellis.org](https://devtrellis.org)** — herkes okuyup arayabilir; alfa pilotunda katkı hesapları davetle açılır.

DevTrellis, hobi olarak üretenlerden profesyonel geliştiricilere kadar herkes için açık bir teknoloji bilgi topluluğu kurmayı hedefliyor: yeni çalışmaları takip et, kaynaklarıyla araştır, önceki araştırmaları kullan ve projeni paylaş. Uzun vadede günlük gelişmelerin kalıcı konu sayfalarına bağlandığı, toplulukla güncellenen bir teknoloji ansiklopedisi olacak. [Ürün vizyonu ve yol haritası](PLATFORM.md), hazır olanlarla planlananları açıklıyor.

Bugün kişisel alan bilgisayarında çalışır. Ayrı topluluk hizmeti yalnız seçilip gönderilen kartları sunar. Hazır araştırmayı okumak için katkı yapman veya model hesabın olması gerekmez. Yaşayan konu sayfaları, yayından sonra saatlik topluluk araştırmacısı ve yayıncı API/MCP bağlantıları geliştirme planındadır. Otomatik kamusal araştırma için kullanıma izin veren sağlayıcı erişimi gerekir; NVIDIA deneme erişimi tek başına yeterli değildir.

Erken alfa sürümüdür. Önceki adı UFUK'tu; `UFUK_*` ayarları, veri kimlikleri ve `ufuk-share` dosya biçimi uyumluluk için korunur. Topluluk hizmeti ayrıca kurulup işletilir.

## Kurulum

Doğrulanan Python sürümü **3.12**. Node.js yalnız JavaScript testleri için gerekir. Temel kullanım için Docker, yönetici hesabı veya model aboneliği gerekmez. Windows'ta kaynak klasöründe:

```powershell
python -X utf8 kur.py
.\.venv\Scripts\python.exe -X utf8 calistir.py sun --elle-toplama
```

**http://127.0.0.1:8781/platform** adresini aç. Durdurmak için Ctrl+C kullan. Kurulum ayrı `.venv` oluşturur ve bağımlılıkları denetler; kullanıcı verisini silmez. `python -X utf8 kur.py --check` yalnız mevcut ortamı denetler.

Unix karşılıkları `python3 kur.py` ve `.venv/bin/python calistir.py sun --elle-toplama` komutlarıdır. Denenmiş işletim sistemleri sürüm notlarında belirtilir.

`--elle-toplama` zamanlı toplamayı ve araştırmayı kapalı başlatır. Kaynakları açıkça toplamak için:

```powershell
.\.venv\Scripts\python.exe -X utf8 calistir.py topla
```

Toplama kayıtlı kamusal kaynaklara internet isteği gönderir. Kayıtlı içeriği okumak model kotası harcamaz. Bayraksız başlatmak zamanlı toplama ve ayarlanmış araştırma akışını açar; önce ayarlarını incele.

## Dil ve sağlayıcılar

Geliştirici alanı, ayrıntılı çalışma alanı ve topluluk ekranlarında **English / Türkçe** seçimi bulunur. Kaynak ve katkıcı metni özgün dilinde kalır. Rapor dili sağlayıcı ayarında ayrıca seçilir.

- NVIDIA: kendi anahtarınla özetleme, eleme ve kaynaklara dayalı araştırma. Hesap/model koşullarıyla ücretsiz prototip erişimi için [adım adım rehberi](GUIDE.tr.md) izle. Gizli anahtar giriş aracı `nvidia_anahtar.py`; `UFUK_NVIDIA_API_KEY` / `NVIDIA_API_KEY` süreç ortamı veya Windows `ufuk-nvidia` / `api-key` kimlik kasası da kullanılabilir.
- Claude: kendi hesabınla oturum açılmış CLI üzerinden araştırma. Gerektiğinde `UFUK_CLAUDE_CLI` ile yolunu seç.
- Codex: kendi CLI oturumunla, DevTrellis'in topladığı kaynakların isteğe bağlı analizi. Bağımsız web araştırması iddiası yoktur. CLI kurulumu ve oturum açma ayrıdır; [resmî oturum yönergesini](https://learn.chatgpt.com/docs/auth) izle. Algılanmazsa `UFUK_CODEX_CLI` ile gerçek çalıştırılabilir dosyayı seç; Windows'ta `.exe` gerekir. Model alanını boş bırakmak mevcut CLI model seçimini kullanır.

Her sağlayıcının kota ve ücretleri kullanıcıya aittir. Seçilmiş bağlam sağlayıcıya gider; kimlik bilgileri ortak sunucuya gönderilmez. Gerçek çağrıyla doğrulanan kapsam sürüm notlarında açıklanır.

## Kayıtları korumak

Varsayılan veritabanı `data/radar.sqlite3`. Veriyi sürüm klasöründen ayrı tutmak için bütün komutlarda aynı yolu kullan:

```powershell
.\.venv\Scripts\python.exe -X utf8 calistir.py yedekle --db C:\DevTrellisData\personal.sqlite3
.\.venv\Scripts\python.exe -X utf8 calistir.py sun --elle-toplama --db C:\DevTrellisData\personal.sqlite3
```

Güncellemeden önce uygulamayı durdurup tutarlı yedek al. Yeni sürümü ayrı klasöre çıkar ve aynı veritabanıyla başlat. Eski sürümü ve yedeği sakla. Veri biçimi değişmişse geri dönüş eski yedekle yapılır; sonraki düzenlemeleri korumak için yeni veritabanını ayrıca sakla.

Veri, yedek, günlük, anahtar ve özel araştırmalar yayın paketine konmaz. Yerel uygulama yalnız `127.0.0.1` adresinde tek kullanıcıya hizmet verir. Ortak erişim için ayrı [topluluk hizmeti](COMMUNITY.md) kullanılır.

## Paylaşım ve katkı

Kartı kaydet, yazar adını ve içerik iznini seç, önizlemeyi oku ve dosyayı indir. İçe alma da kayıt öncesi önizleme yapar. Topluluk sunucusuna gönderim ayrı ve isteğe bağlıdır.

Yazılım **MIT** lisanslıdır; ticari kullanım da serbesttir. Kartın sahibi kendi metni için **CC BY 4.0** veya **CC0 1.0** seçer. Bu, kaynak makalenin veya bağlantı verilen projenin haklarını değiştirmez. Hesap açılması raporun doğrulandığı anlamına gelmez.

Test komutları ve katkı yönergeleri [İngilizce README](README.md) içindedir. Ayrıca [güvenlik bildirimi](SECURITY.md) ve [ürün kapsamına](PLATFORM.md) bakabilirsin.
