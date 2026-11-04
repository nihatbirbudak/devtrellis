# DevTrellis

**Keşfet. Araştır. Birlikte geliştir.**

[English](README.md) · [MIT lisansı](LICENSE) · [Araştırma ve ücretsiz NVIDIA rehberi](GUIDE.tr.md) · [Topluluk sunucusu](COMMUNITY.md)

**[Canlı topluluk: devtrellis.org](https://devtrellis.org)** — hesap açmadan hazır araştırmaları bul; kendi araştırman bitince istersen uygulamadan doğrudan paylaş. Dosya taşıman veya kayıt olman gerekmez.

DevTrellis, hobi olarak üretenlerden profesyonel geliştiricilere kadar herkes için açık bir teknoloji bilgi topluluğu kurmayı hedefliyor: yeni çalışmaları takip et, kaynaklarıyla araştır, önceki araştırmaları kullan ve projeni paylaş. Uzun vadede günlük gelişmelerin kalıcı konu sayfalarına bağlandığı, toplulukla güncellenen bir teknoloji ansiklopedisi olacak. [Ürün vizyonu ve yol haritası](PLATFORM.md), hazır olanlarla planlananları açıklıyor.

Kişisel alan bilgisayarında çalışır. Yeni model işi başlatmadan önce toplulukta hazır araştırmayı ara. Yeterliyse kaynakları, tarihi ve model bilgisiyle oku; değilse yeni araştırmana başlangıç bağlamı olarak kullan. Okuma ve içe alma model çağırmaz. Kendi raporun hazır olunca **Yerelde tut** veya **Önizle ve paylaş** seç: yalnız onayladığın araştırma kopyası, varsayılan olarak anonim biçimde incelemeye gider. Topluluk hesabı ve dosya aktarımı gerekmez.

İsteğe bağlı [MCP bağlantısı](MCP.tr.md), uyumlu asistanların hazır araştırmaları bulmasını ve kendi araştırmalarından açık onayla katkı sunmasını sağlar. Önceki çalışmayı kullanmak tekrarlanan model işini azaltabilir; belirli bir token tasarrufu veya model eğitimi vaat edilmez. Yaşayan konu sayfaları, saatlik topluluk araştırmacısı ve yayıncı bağlantıları plandadır. Otomatik kamusal araştırma için kullanıma izin veren sağlayıcı erişimi gerekir; NVIDIA deneme erişimi tek başına yeterli değildir.

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

Araştırma bittiğinde **Toplulukla paylaşmak ister misin?** önerisi görünür. **Yerelde tut** hiçbir araştırma metnini göndermez. **Önizle ve paylaş** seçeneğinde tam metni ve kaynakları görüp gerekirse düzenlersin; CC BY 4.0 metin iznini açıkça kabul edince incelemeye gönderilir. Gönderi takibi ve geri çekme uygulamadaki **Gönderimlerim** alanındadır. Yönetim anahtarı işletim sisteminin güvenli kasasında tutulur; Linux'ta çalışan bir Secret Service/KWallet gerekir. Dosya içe/dışa aktarma gelişmiş seçenek olarak korunur.

Yazılım **MIT** lisanslıdır; ticari kullanım da serbesttir. Kartın sahibi kendi metni için **CC BY 4.0** veya **CC0 1.0** seçer. Bu, kaynak makalenin veya bağlantı verilen projenin haklarını değiştirmez. Hesap açılması raporun doğrulandığı anlamına gelmez.

Test komutları ve katkı yönergeleri [İngilizce README](README.md) içindedir. Ayrıca [güvenlik bildirimi](SECURITY.md) ve [ürün kapsamına](PLATFORM.md) bakabilirsin.
