# Araştırma asistanından DevTrellis kullan

[English](MCP.md) · [Araştırma rehberi](GUIDE.tr.md) · [Topluluk](COMMUNITY.md)

İsteğe bağlı MCP bağlantısı, asistanının **önce hazır araştırmaları aramasını**, işe yarayan raporları okumasını ve kendi araştırması bitince katkı göndermeyi önermesini sağlar. DevTrellis hesabı açman gerekmez. Araştırmayı asistanının kendi modeli yapar; bu MCP sunucusu model çağırmaz ve ortak model kotası kullanmaz.

Akış: **ara → kaynakları oku ve eksikleri belirle → eksik kısmı araştır → yerel inceleme paneli → sen düğmeyle karar ver → topluluk incelemesi**. Uygun hazır raporlar tekrar araştırmayı azaltabilir. MCP veya skill kurmak asistanı araçları kullanmaya zorlamaz; token tasarrufu veya doğru sonuç garantisi değildir.

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

Skill'in akışı: genel bir kamusal konuyu ara, özgün kaynak ve tarihleri incele, eksikleri kendi modelinle araştır, bitince kendi başına anlaşılır kamusal bir rapor hazırla. Skill yerel dosyaları taramayı, anahtar çıkarmayı, bağımlılık kurmayı veya konuşma yüklemeyi istemez. Özel konuşma, profil, proje ayrıntıları ve herkese açık olmayan depo içeriği sorgu ve metadata dahil hiçbir araç girdisine eklenmemelidir.

Denemek için: **“Bu konuyu araştırmadan önce DevTrellis'te yararlı bir rapor var mı bak. İlgili kaynakları göster ve hangi kısmın güncellenmesi gerektiğini açıkla.”**

## Araçlar ve paylaşım

| Araç | İşlev |
| --- | --- |
| `search_research(query, limit=10)` | Yerel uygulama üzerinden 1–10 topluluk araştırması arar. |
| `read_research(research_id)` | Aramada bulunan raporu, kaynaklarını ve üretim bilgisini okur. |
| `preview_contribution(...)` | Yerel önizlemeyi ve etkileşimli inceleme panelini hazırlar; göndermez. |

Önizleme için asistan kendi başına anlaşılır kamusal başlık, özet ve rapor, `{ "title": "...", "url": "https://..." }` biçiminde 1–10 özgün kamusal kaynak ve gerçek model adını (bilinmiyorsa `unknown`) hazırlar. Başlık kamusal konuyu anlatmalı, özel konuşma veya proje bağlamını kopyalamamalıdır. Arka uç yazarı `Anonymous contributor` olarak sabitler ve kamusal araştırma konusunu başlıktan türetir. `CC0-1.0` istemediysen skill `CC-BY-4.0` önerir; bu öneri onay verdiğin anlamına gelmez. Panel önerilen lisansı gösterip kutuyla onayını ister; lisansı değiştirmez. Farklı desteklenen bir izin için yeni önizleme iste. Model adı veya üretim tarihi uydurulmaz.

Yazılımın lisansı MIT'dir. Rapor metni için ayrıca **CC BY 4.0** veya **CC0 1.0** seçilir; bağlantılı kaynakların hakları değişmez. MCP ile verilen model ve kanıt bilgisi katkıcının beyanıdır ve **denetlenmemiş** olarak başlar. Raporun hazır veya yayımlanmış olması doğruluğunu kanıtlamaz.

Yerel uygulama paketi doğrular ve tam önizlemeyi üretir. **Gönder** veya **özel kalsın** düğmelerinden birini seçmeden önce oku. Modelin gönderim aracı yoktur. `contribution_decision` yalnız inceleme arayüzüne aittir ve gizli inceleme nonce'u ister; skill, asistanın bunu çağırmasını veya senin yerine karar vermesini yasaklar. İçerik değişirse yeni önizleme gerekir. Gönderi moderasyon kuyruğuna girer, anında yayımlanmaz.

Uygulaman paneli gösteremiyorsa — örneğin satır içi MCP App desteklemeyen bir Codex sürümünde — yanıt yerel `review_url` bağlantısını verir. Gerçek düğmeleri kullanmak için bağlantıyı kendi tarayıcında aç. Asistan bağlantıyı sunup beklemeli; sayfayı otomasyonla açıp senin yerine onaya tıklamamalıdır. Paneli kapatmak veya reddetmek raporu özel tutar. Sohbetteki bir mesaj sessizce onay tıklamasına çevrilmez.

## Sınırlar ve sorun giderme

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
