# Araştırma asistanından DevTrellis kullan

[English](MCP.md) · [Araştırma rehberi](GUIDE.tr.md) · [Topluluk](COMMUNITY.md)

İsteğe bağlı MCP bağlantısı, asistanının **önce hazır araştırmaları aramasını**, işe yarayan raporları okumasını ve kendi araştırması bitince katkı göndermeyi önermesini sağlar. DevTrellis hesabı açman gerekmez. Araştırmayı asistanının kendi modeli yapar; bu MCP sunucusu model çağırmaz ve ortak model kotası kullanmaz.

Varsayılan akış: **özetlerde ara → uygun hazır yanıtı kullan → yalnız belirsiz veya değişken noktaları kontrol et → gerekirse derin araştırmayı öner**. Anlamlı yeni bulgu oluşursa ayrıca yerel katkı incelemesi, senin düğmeyle seçimin ve topluluk incelemesi gelir. Açıkça derin araştırma istendiğinde bu istek önceliklidir. MCP veya skill kurmak asistanı araçları kullanmaya zorlamaz; token tasarrufu veya doğru sonuç garantisi değildir.

Arama, rapor gövdelerini göndermeden özet, kaynak, rapor tarihleri, model bilgisi ve mevcut metadatayı taşır. MCP varsayılan olarak üç aday ister; tam raporu yalnız gerektiğinde okur. Yerel köprü ve topluluk sunucusu özet görünümünü destekliyorsa aramanın ağ yanıtında da rapor gövdeleri taşınmaz. Eski bileşenlerle çalışırken köprü tam arama yanıtını kabul eder ve sonuçları modele vermeden gövdeleri çıkarır. Yakın rapor tarihi, modelin ünü veya kaynak sayfasının değişmemesi tek başına iddianın güncel olduğunu kanıtlamaz. Rapor tarihleri kaynak kontrol tarihi değildir. Asistan kullanılan bulgularla gerçekten yaptığı kontrolleri ayırır, ortak raporun tam sürümüne bağlantı verir ve yanıtın ardından daha derin araştırmayı seçenek olarak sunar.

Kamusal arama uç noktası `view=summary` kabul eder; mevcut istemciler için varsayılan tam pakettir. Yerel arama API'si de varsayılan tam paket döndürür ve desteklediği `search_views` değerlerini oturum yanıtında bildirir. MCP, yerel destek bildirimi varsa özet görünümünü ister. Özet paketlerinde `body` yoktur; bunlar içe aktarılabilir katkı paketi değil arama sonucudur. Tam sürümü okuma ve indirme tam raporu döndürmeye devam eder. Yalnız rapor gövdesindeki bir uyarı özette bulunmayabilir; özet soruyu karşılamıyorsa rapor okunur.

Yerel köprü `search_modes` içinde destek bildirirse MCP ayrıca `search_mode=ranked` ister. Kamu API'si araştırma aramalarında bu seçeneği destekler; mevcut `all` varsayılanı korunur. Ayrı SQLite FTS5 indeksi yalnız şu anda yayımlanmış araştırma sürümlerini taşır. Önce sözcük kapsamı, ardından başlık/özet/etiket/gövde ağırlıkları, eşitlikte tarih ve kimlik sıralaması kullanılır. Küçük, açık bir TR/EN sözlüğü ve sınırlı sözcük biçimi eşleşmesi vardır; sınırsız anlamsal arama değildir. Tanınan noktalı sürümler ve teknik belirteçler eşleşmelidir; sorudaki sıradan miktarlar doğrulanacak iddia olabileceği için zorunlu filtre değildir. Adla belirtilen dağıtım farklarında rapor kapsamı ayrıca okunmalıdır.

Sıralı adaylar `search_match.matched_terms`, `missing_terms`, `all_terms_matched` ve `answer_sufficiency: "not_assessed"` taşır. Eksik terim varsa rapor kapsamı incelenir; bütün terimlerin eşleşmesi de cevabın yeterli veya doğru olduğunu kanıtlamaz. Eski sunucularda veya FTS5 olmayan SQLite sürümlerinde mevcut bütün-terim araması çalışır ve `search.fallback: true` gösterilir. Ek model çağrısı, embedding modeli veya dış vektör hizmeti gerekmez. İndeks türetilmiş veridir; biçimi değişirse yükseltmede yenilenir. Yayın ve geri çekme, indeksle aynı veritabanı işlemi içinde tamamlanır.

Doğrulanmış her arama sonucu ve rapor okumasında, ilgili raporun tam sürümüne giden `report_url` bulunur. Okuyucudan kimlik üzerinden bağlantı kurmasını istemek yerine bu değeri atıf olarak kullan. Köprü bağlantıyı topluluk adresi ve doğrulanmış kimlik/sürümden türetir; uzak kartın içindeki bir URL'ye güvenmez.

## 1. İsteğe bağlı MCP adaptörünü kur

Kaynak kodu [ana kurulum adımlarıyla](README.tr.md) indir. Kaynak klasöründe uygulama bağımlılıkları ile isteğe bağlı MCP SDK'sını içeren bir ortam oluştur:

```powershell
python -m venv .venv-mcp
.\.venv-mcp\Scripts\python.exe -m pip install -r requirements-lock.txt -r requirements-mcp.txt
.\.venv-mcp\Scripts\python.exe -X utf8 mcp_topluluk.py --help
```

macOS/Linux için:

```sh
python3 -m venv .venv-mcp
.venv-mcp/bin/python -m pip install -r requirements-lock.txt -r requirements-mcp.txt
.venv-mcp/bin/python mcp_topluluk.py --help
```

Doğrulanan çalışma ortamı Python 3.12'dir. `requirements-mcp.txt` resmi [MCP Python SDK 2.3.0](https://pypi.org/project/mcp/2.3.0/) sürümünü sabitler. Ana uygulamanın bu pakete ihtiyacı yoktur.

## 2. Yerel bağlantıyı seç

**En kolay kurulum `--standalone`.** Asistanın MCP ile birlikte kendi küçük yerel hizmetini açar; tam DevTrellis uygulamasını ayrıca başlatman gerekmez. Bu hizmet yalnız topluluk API'sini ve katkı inceleme sayfasını sunar, rastgele bir `127.0.0.1` portu kullanır ve MCP oturumu bitince kapanır. Model istemcisi oluşturmaz, kaynak taramaz veya araştırma planlamaz. Açık yerel DevTrellis süreçlerine dokunmaz.

Standalone kendi katkı kayıtlarını `~/.devtrellis/mcp-data/research.sqlite3` içinde tutar. Başka özel bir klasör için isteğe bağlı `--data-dir C:/DevTrellisMcpData` ekleyebilirsin. Klasör ilk kullanımda boş olmalı veya bu köprünün oluşturduğu sahiplik kaydını taşımalı; mevcut kişisel çalışma alanı kabul edilmez. Birden fazla asistan süreci bu özel veri klasörünü paylaşabilir; her birinin portu ve inceleme oturumu ayrıdır. Tarayıcıdaki inceleme bağlantısı ilgili MCP süreci çalışırken kullanılabilir.

**Zaten çalışan yerel uygulamayı kullanmak için** aşağıdaki örneklerde `--standalone` yerine `--local-url http://127.0.0.1:8781`, ya da uygulamanın `8899` gibi gerçek portunu yaz. İki modu birleştirme; `--local-url` ile `--data-dir` kullanma. Gerekirse tam yerel uygulamayı şu komutla aç:

```powershell
.\.venv\Scripts\python.exe -X utf8 calistir.py sun --elle-toplama
```

İki mod da arama, rapor okuma ve inceleme arayüzünde onayladığın katkılar için topluluk hizmetine bağlanır. Arama metni topluluğa gider; özel proje ayrıntıları yerine kısa, paylaşılabilir konu kullan. Adaptörün kendisi yalnız loopback adresine bağlanır. Herkese açık topluluk sitesinin adresi `--local-url` olamaz. Mevcut uygulamada bu sürümün `/api/platform/community/` uçları bulunmalı; `404` hatası eski sürümü gösterebilir.

## 3. Asistanına bağla

MCP destekleyen uygulamaya Python ve betiğin tam yollarını ver. Aşağıdaki `C:/DevTrellis` bölümünü kendi kurulum klasörünle değiştir. Bu ayarlara API anahtarı veya parola yazılmaz.

Claude Desktop için mevcut `mcpServers` ayarlarına aşağıdaki girdiyi ekle; diğer girdileri koru:

```json
{
  "mcpServers": {
    "devtrellis": {
      "command": "C:/DevTrellis/.venv-mcp/Scripts/python.exe",
      "args": [
        "-X", "utf8",
        "C:/DevTrellis/mcp_topluluk.py",
        "--standalone"
      ]
    }
  }
}
```

Claude Desktop'ın Windows ayarı `%APPDATA%\Claude\claude_desktop_config.json`, macOS ayarı `~/Library/Application Support/Claude/claude_desktop_config.json` konumundadır. Değişiklikten sonra uygulamayı yeniden başlat. Konumlar ve stdio bağlantı düzeni [resmi MCP SDK rehberinde](https://py.sdk.modelcontextprotocol.io/get-started/real-host/) açıklanır.

Claude Code için aynı başlatma komutunu kaydet:

```powershell
claude mcp add --scope user devtrellis -- "C:/DevTrellis/.venv-mcp/Scripts/python.exe" -X utf8 "C:/DevTrellis/mcp_topluluk.py" --standalone
```

Codex için aynı komutu kaydet; bağlantının yüklenmesi için yeni görev aç:

```powershell
codex mcp add devtrellis -- "C:/DevTrellis/.venv-mcp/Scripts/python.exe" -X utf8 "C:/DevTrellis/mcp_topluluk.py" --standalone
```

Oturumda `/mcp` ile üç araştırma aracının göründüğünü kontrol et. Diğer MCP uygulamalarında aynı komut ve argümanları kendi ayar biçimleriyle **stdio** sunucusu olarak ekle. macOS/Linux'ta Python yolu `/tam/yol/DevTrellis/.venv-mcp/bin/python` olur; betiğin yolunu da değiştir. Bu adaptör herkese açık MCP sunucusu başlatmaz; dışarıya port açılmaz.

## 4. Araştırma skill'ini kur

Taşınabilir skill, herkese açık teknoloji araştırmalarında “önce hazır çalışmalara bak” akışını sağlar. Uygun isteklerde otomatik seçilebilir; ilgisiz özel çalışmalar için kullanılmaz. Önce yukarıdaki MCP bağlantısını kur. Kaynak klasöründen kullanacağın uygulamayı seç:

```powershell
python -X utf8 araclar/skill-kur.py --target codex
python -X utf8 araclar/skill-kur.py --target claude
```

İkisi için `--target both` kullanabilirsin. Kurucu yalnız `SKILL.md`, `agents/openai.yaml` ve kendi sahiplik/hash kaydını `~/.codex/skills/devtrellis-research` ve/veya `~/.claude/skills/devtrellis-research` klasörüne kopyalar. MCP ayarını, anahtarları, uygulama ayarlarını, diğer skill'leri veya marketplace'i değiştirmez. Tekrar çalıştırıldığında aynı dosyalara yazmaz. Kendi oluşturduğu sağlam kurulum güncellenebilir; tanımadığı, düzenlenmiş veya ek dosyalar varsa onları koruyarak durur. `--home C:/OrnekEv` ayrı bir kullanıcı kökü seçer; izole test için kullanılabilir.

Kurulumdan sonra yeni görev/oturum aç. Codex'te `$devtrellis-research` ile açıkça çağırabilirsin; Claude Code uygun skill'i seçebilir veya `/devtrellis-research` kullanılabilir. `integrations/devtrellis-research` paketinde plugin akışları için taşınabilir `.codex-plugin/plugin.json` ve `.claude-plugin/plugin.json` dosyaları da bulunur. Bunlarda kişisel yol, hook veya gömülü anahtar yoktur; araçları ayrı kurulan MCP bağlantısı sağlar.

Özel skill ZIP yüklemesini destekleyen bir uygulama için yeni arşiv oluştur:

```powershell
python -X utf8 araclar/skill-kur.py --zip devtrellis-research-skill.zip
```

Arşiv `devtrellis-research/SKILL.md` ve arayüz metadata'sını içerir; kurucu, uygulama verisi veya kişisel dosya içermez. Mevcut arşivin üzerine yazmaz. Skill yüklemek yalnız yönerge sağlar; bulutta çalışan bir sohbeti bilgisayarındaki localhost hizmetine bağlamaz. Aynı uygulamada MCP bağlantısına erişim ayrıca gerekir. Desteklenen yükleme seçenekleri için [Claude'un resmi skill paketleme rehberine](https://support.claude.com/en/articles/12512198-how-to-create-custom-skills) bak.

Skill genel bir kamusal konuyu arar, yeterli bulguları kullanır ve yalnız gerekli kaynakları veya eksikleri araştırır. Ayrı kamusal katkı yalnız anlamlı yeni bulgular için hazırlanır; değişmemiş araştırmayı kullanmak yeniden rapor üretmeyi gerektirmez. Skill yerel dosyaları taramayı, anahtar çıkarmayı, bağımlılık kurmayı veya konuşma yüklemeyi istemez. Özel konuşma, profil, proje ayrıntıları ve herkese açık olmayan depo içeriği sorgu ve metadata dahil hiçbir araç girdisine eklenmemelidir.

Denemek için: **“Bu konuyu araştırmadan önce DevTrellis'te yararlı bir rapor var mı bak. İlgili kaynakları göster ve hangi kısmın güncellenmesi gerektiğini açıkla.”**

## Araçlar ve paylaşım

| Araç | İşlev |
| --- | --- |
| `search_research(query, limit=3)` | Yerel uygulama üzerinden 1–10 özet paket arar; `view=summary` döner, `packet.body` içermez. Varsayılan ve skill'in ilk isteği 3 adaydır. |
| `read_research(research_id, version=null)` | Güncel veya tam kamu sürümünü kaynaklarıyla okur; sayaç olayı göndermez. |
| `record_research_use(research_id, version)` | Bulgulardan gerçekten yararlanıldığını yalnız rapor kimliği/sürümüyle bildirir. Doğruluk oyu değildir. |
| `open_submission_history()` | Önizleme hazırlamadan veya topluluğa bağlanmadan yerel Gönderimlerim bağlantısını verir. |
| `preview_contribution(...)` | Yerel önizlemeyi ve etkileşimli inceleme panelini hazırlar; göndermez. |

`read_research` yalnız okuma yapan GET kullanır; erişim veya kullanım olayı göndermez. Böylece okuma yazma onayına bağlı değildir. `record_research_use` ayrı ve isteğe bağlı yazma işlemidir. Mevcut MCP erişim sayacı olay gönderen eski istemcileri kapsar; bütün MCP okumalarının sayısı değildir. Erişim ve kullanım sayıları benzersiz kişi veya güvenilirlik puanı sayılmaz.

Önizleme için asistan kendi başına anlaşılır kamusal başlık, özet ve rapor, `{ "title": "...", "url": "https://..." }` biçiminde 1–10 özgün kamusal kaynak ve gerçek model adını (bilinmiyorsa `unknown`) hazırlar. Başlık kamusal konuyu anlatmalı, özel konuşma veya proje bağlamını kopyalamamalıdır. Arka uç yazarı `Anonymous contributor` olarak sabitler ve kamusal araştırma konusunu başlıktan türetir. `CC0-1.0` istemediysen skill `CC-BY-4.0` önerir; bu öneri onay verdiğin anlamına gelmez. Panel önerilen lisansı gösterip kutuyla onayını ister; lisansı değiştirmez. Farklı desteklenen bir izin için yeni önizleme iste. Model adı veya üretim tarihi uydurulmaz.

Yazılımın lisansı MIT'dir. Rapor metni için ayrıca **CC BY 4.0** veya **CC0 1.0** seçilir; bağlantılı kaynakların hakları değişmez. MCP ile verilen model ve kanıt bilgisi katkıcının beyanıdır ve **denetlenmemiş** olarak başlar. Raporun hazır veya yayımlanmış olması doğruluğunu kanıtlamaz.

Yerel uygulama paketi doğrular ve tam önizlemeyi üretir. **Gönder** veya **özel kalsın** düğmelerinden birini seçmeden önce oku. Modelin gönderim aracı yoktur. `contribution_decision` yalnız inceleme arayüzüne aittir ve gizli inceleme nonce'u ister; skill, asistanın bunu çağırmasını veya senin yerine karar vermesini yasaklar. İçerik değişirse yeni önizleme gerekir. Gönderi moderasyon kuyruğuna girer, anında yayımlanmaz.

Uygulaman paneli gösteremiyorsa — örneğin satır içi MCP App desteklemeyen bir Codex sürümünde — yanıt yerel `review_url` bağlantısını verir. Gerçek düğmeleri kullanmak için bağlantıyı kendi tarayıcında aç. Asistan bağlantıyı sunup beklemeli; sayfayı otomasyonla açıp senin yerine onaya tıklamamalıdır. Paneli kapatmak veya reddetmek raporu özel tutar. Sohbetteki bir mesaj sessizce onay tıklamasına çevrilmez.

## Sınırlar ve sorun giderme

`provider` araştırma motorunu belirtir: `codex`, `claude`, `nvidia` veya `unknown`. Gerçek model adı ayrı alandır. `topic`, aracın sunduğu `ai`, `software`, `security`, `data` gibi kategori kodlarından biridir; araştırmanın özel konusunu `title` alanına yaz. Geçersiz kategoriler yerel API'ye istek gitmeden reddedilir.

İnceleme panelindeki **Gönderimlerim** veya `open_submission_history` aracı kayıtları 25'lik sayfalarla gösterir; yeni katkı önizlemesi gerekmez. Liste açılışı yalnız yerel kayıtları okur. **Durumu yenile** topluluktaki inceleme sonucunu sorgular; yayımlanmış raporun doğrudan web bağlantısı görünür. **Gönderiyi geri çek**, onayından sonra sunucudaki kopyanın kamusal erişimini kapatır; başkalarının önceden indirdiği kopyaları geri alamaz. Sayfa MCP oturumu açıkken çalışır; sonraki oturumda yeni geçmiş bağlantısı isteyebilirsin.

Gönderi topluluğa ulaşıp cevabı kaybolduysa **Durumu yenile**, sonraki oturumda mevcut güvenli kasa yönetim anahtarıyla kaydı bulabilir. Bu sorgu araştırmayı yeniden yüklemez. Kurtarma için sunucunun inbox durum bulma uç noktasını desteklemesi gerekir; eski sunucuda bağlantı hatası gösterilir ve yerel teslim belirsiz olarak kalır. Yönetim anahtarı kaybolursa bu yöntemle kurtarma yapılamaz.

Topluluk araması normalleştirilmiş terim gruplarını arar ve yayın tarihine göre sıralar. Küçük bir Türkçe/İngilizce teknik sözlük tam metin arama, sıralama, kaynak ve önbellek gibi belirli karşılıkları genişletir. Bütün gruplar eşleşmeli; bilinmeyen terimler atılmaz. Genel çeviri, anlamsal arama veya ilgililik sıralaması değildir. Birkaç ayırt edici teknik terim kullan; uygun rapor olmadığına karar vermeden önce daha kısa sorgu veya özgün yayının dilini dene. Sıfır sonuç, konunun daha önce araştırılmadığını tek başına kanıtlamaz.

## Geliştirilmiş araştırmalar ve göstergeler

Yeni kamu bulguları ekleyen araştırmada `lineage: {parents: [{research_id, version}], change_summary}` gönderilir. En fazla beş temel araştırmanın tam kamu sürümü ve en fazla 600 karakterlik değişiklik özeti kullanılır. Şema 3, önceki çalışmayı koruyarak ayrı bir inceleme kaydı oluşturur. Önizleme bu bağlantıları ve özeti gösterir. Özgün kaynakları koru; birbirinden türeyen raporlar bağımsız kanıt sayılmaz. Yalnız yeniden ifade edilen metin için yeni katkı önerilmemeli.

Web açılışı, MCP erişimi, yararlı bulma, bildirilen kullanım, güncellik ve itiraz sayımları ayrı ve yaklaşık göstergelerdir; benzersiz kişi veya doğruluk puanı değildir. Olaylarda yalnız rapor/sürüm, tür ve rastgele tekrar teslim kimliği bulunur. Sayaç veritabanında IP, sohbet, hesap veya raporlar arasında ortak cihaz kimliği saklanmaz. Bellekteki kısa süreli hız sınırı bağlantı adresini kullanır. Anonim istemci türü taklit edilebildiğinden kanal ve oylar doğrulanmış kimlik beyanı sayılmaz. Sayım hatası okumayı engellemez. Aynı yerel MCP oturumundaki tekrar kullanım bildirimleri tek kimliği kullanır; sunucu tekrar teslim kimliklerini yedi gün tutar, toplu sayımlar kalır.

- `--local-url` yalnız açık portlu `http://127.0.0.1:PORT` veya `http://localhost:PORT` kabul eder. Bağlantı doğrudan loopback'e yapılır; ortam proxy ayarları ve HTTP yönlendirmeleri kullanılmaz.
- İstek en fazla 32 KiB, yanıt en fazla 1 MiB olur; soket işlemleri 15 saniyede zaman aşımına uğrar. 12.000 karakterden uzun rapor sessizce kesilmez, reddedilir. Önizlemeden önce sınır içinde kendi başına anlaşılır bir rapor seç.
- Yerel CSRF anahtarı küçük `/api/platform/community/session` yanıtından alınır ve adaptörün içinde kalır. Kişisel kayıtlar ve çalışma alanının tamamı okunmaz.
- Pakette dosya tarayıcı, arka planda yükleme veya otomatik kurulum hook'u yoktur. Yönergeler özel konuşma, profil ve proje ayrıntılarını araç girdisinden dışarıda tutar. Asistan yine de metni yanlış hazırlayabilir; tam önizlemeyi kontrol et ve bunları mutlak gizlilik garantisi sayma.
- “Cannot reach local DevTrellis” yerel uygulamanın kapalı veya portun yanlış olduğunu gösterir. `403` için yerel uygulamanın adres ayarlarını kontrol et. SDK bulunamadı uyarısında asistanın kullandığı Python ortamını kontrol et.
- `--standalone` için iki bağımlılık dosyasını da asistanın kullandığı Python ortamına kur. Sahiplik kaydı hatası seçilen klasörde mevcut veya tanınmayan veri bulunduğunu gösterir; içeriği silmek yerine yeni, boş bir özel klasör seç.
- Adaptörü terminalde doğrudan başlatınca stdio mesajı bekleyerek sessiz kalması normaldir. Normal kullanımda süreci asistan uygulaması yönetir.

Testler geçici yerel HTTP hizmeti ve gerçek SDK protokolünü kullanır; internete istek göndermez ve model çağırmaz:

```powershell
.\.venv-mcp\Scripts\python.exe -X utf8 -m unittest discover -s tests -p test_community_mcp.py -v
.\.venv-mcp\Scripts\python.exe -X utf8 -m unittest discover -s tests -p test_mcp_yerel.py -v
.\.venv-mcp\Scripts\python.exe -X utf8 -m unittest discover -s tests -p test_skill_install.py -v
```

## Kısa arama ve tam sürüme bağlı bölümler

`search_research` varsayılan olarak `view="brief"` döndürür: özgün kaynaklar,
köken, sınırlar, yayın sürümü ve rapor bağlantısı korunur; dışa aktarma alanları ve
popülerlik çıkarılır. Eski metadata biçimi için `view="summary"` kullanılabilir.
Kısa çıktı paylaşım paketi değildir. `size_notice`, kanıtı sessizce kesmeden boyut
hedefinin aşıldığını bildirir.

`read_research` varsayılan tam yanıtını korur. Uzun raporda tam yayın sürümüyle
`view="outline"`, ardından dönen `section_id` ile `view="section"` kullanılabilir.
Kısmi çıktı gövde hash'ini, başlık bağlamını, devam işaretlerini ve
`complete_report=false` alanını taşır. Kaynak listesi bütün rapora aittir;
her cümle için doğrulanmış atıf sayılmaz. Rapor değişince eski bölüm kimliği
kullanılamaz. Kısa raporda tek tam okuma tercih edilir. Köprü her görünümde tam
raporu indirir; bu özellik modelin gördüğü metni azaltır, ağ aktarımını azaltmaz.
Bu okumalar model, kamuya yazma veya kullanım olayı başlatmaz.

İsteğe bağlı kaynak hafızası araçları yalnız `--source-memory-tools` ile görünür.
[Erişim ve sınırlar](SOURCE-MEMORY.tr.md) belgesine bakılabilir. Varsayılan araç
listesi küçük kalır; köprü seçeneği sunucu pilotunu açmaz. Araştırma skill'i katkı
ve kaynak hafızası referanslarını gerektiğinde okur; kurulum ve taşınabilir paket
başvurulan skill dosyalarının tamamını içermelidir.
