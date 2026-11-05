# DevTrellis

**Keşfet. Araştır. Birlikte geliştir.**

[English](README.md) · [MIT lisansı](LICENSE) · [Araştırma ve ücretsiz NVIDIA rehberi](GUIDE.tr.md) · [Topluluk sunucusu](COMMUNITY.md)

DevTrellis, hobi olarak üretenlerden profesyonel geliştiricilere kadar herkes için açık bir teknoloji bilgi topluluğudur. Amacı, yararlı araştırmayı bulup yeniden kullanmayı kolaylaştırarak aynı konuyu tekrar tekrar yapay zekâya araştırttırmanın zaman ve token maliyetini azaltmaya yardımcı olmaktır. Güncel haberler, araştırmalar, projeler ve gelişmeler biriktikçe insanlar aramayı bile düşünmedikleri çalışmaları ve konuları da keşfedebilir.

Önce **[devtrellis.org](https://devtrellis.org)** üzerinde hazır araştırmayı ara. Başlığa veya **Araştırmayı oku** düğmesine tıklayarak tam raporu oku; GitHub, proje ve özgün kaynak bağlantılarını incele. Tarihini, kaynaklarını, kapsamını ve sınırlarını kontrol et. Yeterliyse kullan; eksik veya eski kalan kısmı kendi modelinle yeniden araştır. Sitede okumak için hesap, kurulum, API anahtarı veya yapay zekâ asistanı gerekmez. Hazır raporu okumak ve içe almak model çağırmaz.

Kendi araştırman bilgisayarında kalır. Hazır olduğunda **Yerelde tut** veya **Önizle ve paylaş** seç: kamusal metni, kaynakları, model bilgisini ve metin iznini kontrol edip onayladığın anonim kopyayı incelemeye gönder. Kişisel yazar adı ve özel soru/iddia alanları eklenmez. Katkı isteğe bağlıdır; hesap açmak veya dosya taşımak gerekmez. Böylece bulma, kontrol etme, yeniden kullanma ve katkı sunma döngüsüyle ortak bilgi birikir. [Ürün vizyonu ve yol haritası](PLATFORM.md), hazır olanlarla planlananları ayırır.

## Bugünkü kapsam

Araştırmalar bağlantılı katkılarla gelişebilir: önceki raporun tam sürümünü belirt, yeni bulguları açıkla ve özgün kaynakları koru. Okurlar gelişim geçmişini izleyebilir; yararlılık, kullanım, eski bilgi ve itiraz bildirimi yapabilir. Ayrı web/MCP erişim sayımları yaklaşık etkinlik gösterir; popülerlik ve model adı doğruluğu kanıtlamaz. [Araştırma gelişimi ve gizlilik](MCP.tr.md).

Erken alfa sürümüdür. Önceki adı UFUK'tu; `UFUK_*` ayarları, veri kimlikleri ve `ufuk-share` dosya biçimi uyumluluk için korunur. Topluluk hizmeti ayrıca kurulup işletilir. Yaşayan konu sayfaları, saatlik topluluk araştırmacısı ve yayıncı bağlantıları plandadır.

İsteğe bağlı [MCP bağlantısı](MCP.tr.md), uyumlu asistanların hazır araştırmaları bulup okumasını ve önizleme ile açık onay sonrasında katkı sunmasını sağlar. Asistana dönen rapor metni yine bağlam tokeni tüketebilir. Yeniden kullanım tekrarlanan model işini azaltabilir; henüz belirli bir token veya zaman tasarrufu ölçülmemiştir.

Paketteki **Claude Code / Codex skill** kamusal teknoloji araştırmalarında bu akışı kullanır. MCP Apps destekleyen istemcilerde **Paylaş / Yerelde tut** düğmeleri panelde, diğerlerinde yerel inceleme sayfasında görünür. Modelin yalnızca “onaylandı” demesi gönderim yapamaz; önizlemeye bağlı yetki ve başlangıçta boş iki onay kutusu gerekir. Kalıp filtreleri bütün özel ayrıntıları tanıyamaz; tam kamu kopyasını kontrol et. [Kurulum ve gizlilik sınırları](MCP.tr.md).

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
