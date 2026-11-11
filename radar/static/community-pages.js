// Public community pages and private, explicitly reviewed requests. Form data stays in memory until Send.
(() => {
  "use strict";
  const LANGUAGE_KEY = "devtrellis.ui.language";
  const LEGACY_LANGUAGE_KEY = "ufuk.ui.language";
  const REPOSITORY = "https://github.com/nihatbirbudak/devtrellis";
  const PAGES = ["about", "guidelines", "contribute", "support"];
  const CATEGORIES = {contribute: ["research", "code", "translation", "design", "sources", "other"],
    support: ["setup", "bug", "content", "privacy", "other"]};
  const COPY = {
    en: {
      community: "Community", skip: "Skip to content", language: "Language", navigation: "Community pages",
      home: "Explore", about: "About", guidelines: "Guidelines", contribute: "Contribute", support: "Support",
      code: "Source and setup", guide: "Read the guide", footer: "A shared starting point for the next question.",
      eyebrow: "KNOWLEDGE WE CAN BUILD ON",
      aboutTitle: "Start further ahead. Keep discovering.",
      aboutIntro: "Technology moves everywhere, every day. DevTrellis brings developments, projects and source-backed research into a shared place for people who build — whether it is a weekend hobby or their profession.",
      aboutSections: [
        ["Find a useful starting point", "Before asking the same question again, look for an existing investigation. Read it yourself and check its sources. If you connect your coding assistant over MCP, it can first ask DevTrellis about a documentation page and reuse a quoted record if one exists; new research uses your own model."],
        ["Build on what is already known", "A useful contribution can add a missing source, correct a finding or explore a deeper question. For shared reports, linked research versions make that development visible by linking back to earlier public versions; machine-gated records cannot be extended on this site yet."],
        ["Follow curiosity across fields", "Software, AI, robotics, data, security and creative tools belong in the same conversation. Discover an unfamiliar field, follow its original sources, and introduce a project that helps people explore it."],
        ["A growing library, with visible limits", "Coverage comes from registered sources and community contributions; it does not include every development worldwide. Search currently matches words, so try related terms when a useful result is missing. Reuse may reduce repeated work, but measured time or token savings are not promised."]
      ],
      aboutAside: "Read without an account",
      aboutAsideText: "Reading public research needs no installation, subscription or AI. Sharing your own work is optional, and you review its public copy before contributing.",
      aboutAction: "Explore the community", joinAction: "Find your way to contribute",
      guidelinesTitle: "Useful knowledge needs good habits.",
      guidelinesIntro: "Help the next person understand what was found, where it came from and what still needs checking.",
      guidelineSections: [
        ["Make claims traceable", "Prefer original documentation, papers, release notes and repositories. Include source links, relevant versions and dates. Separate observed results, source claims and your own interpretation; say when evidence is incomplete."],
        ["Show the work behind the answer", "Record the model when known, and use unknown when it is not. A model name, price, view count or helpfulness vote does not establish truth. A published record and moderation review are not independent verification."],
        ["Keep private context out", "Do not submit API keys, passwords, personal details, private project information, conversation history or raw logs as public research. Review every field of the public preview. Automated filtering cannot identify every private fact."],
        ["Be respectful and disclose your interests", "Critique claims without attacking people. Avoid spam and repetitive promotion. When sharing a project or product you are involved with, make that relationship clear and explain what others can learn or use."],
        ["Respect attribution and permissions", "The application code is MIT licensed. Research text has its own license, CC BY 4.0 or CC0 1.0, as stated on each record; it is not automatically MIT. Credit reused work and link the version you extended. Linked code, images and source publications keep their own permissions."],
        ["Correct the record", "Report outdated information or an accuracy concern on the research. Add evidence and explain a substantive correction or extension. Operators may review, decline or remove contributions; publication never makes a claim immune to correction."]
      ],
      guidelinesAction: "Discuss a content or privacy concern",
      contributeTitle: "Bring one useful thing.",
      contributeIntro: "A careful source, a clearer explanation or a small fix can help the next person. You do not need to apply, join a team or create an account to contribute research.",
      researchTitle: "Research first. Share when you choose.",
      researchText: "Start with the public library. Use the local app or a connected assistant to investigate a gap, then review the selected public report. You can send it to a moderator or keep it local. Records labelled “Machine gate · not human-reviewed” were published after automatic checks, without a person reading them. A new source or deeper finding can extend an existing shared report; machine-gated records cannot be extended yet.",
      contributeAction: "How research sharing works",
      rolesTitle: "More ways to help",
      roles: [
        ["Research", "Check a claim, compare sources or deepen an existing report."],
        ["Code", "Improve a feature, fix a bug or make reuse easier."],
        ["Documentation and translation", "Make the first steps clearer in English, Turkish or another language."],
        ["Design", "Make reading, searching and sharing more accessible."],
        ["Sources", "Suggest useful original publications and neglected areas of interest."]
      ],
      codeAction: "Find issues on GitHub",
      applicationTitle: "Optional volunteer application",
      applicationIntro: "Want to help regularly or discuss an idea with the operators? Introduce yourself here. This private message is separate from publishing research and grants no research-content license.",
      supportTitle: "A little help to keep going.",
      supportIntro: "Find a starting point below, or send the operators a private question. Reading and asking for help do not require an account.",
      faqTitle: "Common questions",
      faqs: [
        ["Do I need an account or an AI subscription?", "No account or AI is needed to read public research. Creating new research in the local app uses your chosen provider and its own access conditions. The guide covers setup and available options."],
        ["Why did search miss a topic?", "The library is still growing and search matches words. Try a shorter phrase or related terms, then inspect the sources and dates of relevant results."],
        ["How do I report a problem?", "Describe the steps, the result you expected and the result you saw. A software version or public research link can help. Do not paste API keys, passwords, full logs or private project links."],
        ["Can I raise a content or privacy concern?", "Yes. Use the content or privacy category below and identify the relevant public page. Keep the description to what the operators need to locate and review it."]
      ],
      supportFormTitle: "Send a private message",
      supportFormIntro: "For setup, a bug or a concern about the community. Public code issues can also be discussed on GitHub.",
      publicIssue: "Open GitHub issues",
      category: "Category", subject: "Subject", message: "Message", name: "Name (optional)", email: "Email (optional)",
      subjectHint: "A short description of what you need or would like to help with.",
      messageHint: "Include only what is needed. No keys, passwords, raw logs or private files.",
      emailHint: "An email address is needed for a reply. It will not be displayed publicly.",
      formPrivacy: "Only DevTrellis operators receive this message for manual review. It is not a public post and does not grant a license to research. There is no promised response time or automatic email confirmation.",
      retention: "The active database uses a 90-day retention period. Expired messages are removed when the request service next processes a valid operation. Backup copies follow a separate rotation.",
      consent: "I agree to send the details shown here to DevTrellis operators for this request.",
      preview: "Review message", previewTitle: "Check exactly what you will send",
      previewIntro: "Nothing has been sent yet. Only the details below will be sent to DevTrellis operators when you choose Send.",
      retryIntro: "Receipt has not been confirmed. Keep this message unchanged and retry the same request until receipt is confirmed.",
      attemptedIntro: "Receipt has not been confirmed. These same details will be sent when you retry.",
      kind: "Request type", contributionKind: "Volunteer application", supportKind: "Support request",
      messageLanguage: "Message language", consentLabel: "Permission", consentValue: "Agreed to send this request to DevTrellis operators.",
      notProvided: "Not provided", send: "Send to operators", retry: "Retry the same message", edit: "Edit message",
      busy: "Sending…", receivedTitle: "Message received",
      received: "The service confirmed receipt. The operators review messages manually; a reply is possible only if you supplied an email address. No automatic email or response time is promised.",
      duplicateReceived: "The service confirmed that this same message was already received.",
      reference: "Reference", backHome: "Back to the community",
      invalid: "Check the required fields, their lengths and the email address before reviewing.",
      consentNeeded: "Please read and select the permission checkbox before reviewing.",
      secureUnavailable: "A secure request identifier could not be created. Reload in a supported browser before sending.",
      failed: "Receipt could not be confirmed. You can retry this same message.",
      sessionFailed: "The sending session could not be opened. This attempt did not send the message; please try again.",
      rejected: "The service did not accept this message. Review the fields before trying again.",
      limited: "The service is receiving too many requests. Wait a little before retrying this same message.",
      expired: "The sending session could not be verified. Retry to open a fresh session.",
      receiptInvalid: "The service returned an unexpected receipt. Delivery is unconfirmed; retry the same message.",
      leaving: "This draft stays only on this page. Leaving or refreshing clears it.",
      honeypot: "Website — leave blank",
      categories: {research: "Research", code: "Code and documentation", translation: "Translation", design: "Design and accessibility",
        sources: "Sources and coverage", other: "Other", setup: "Setup and use", bug: "Bug report", content: "Content concern", privacy: "Privacy concern"}
    },
    tr: {
      community: "Topluluk", skip: "İçeriğe geç", language: "Dil", navigation: "Topluluk sayfaları",
      home: "Keşfet", about: "Hakkımızda", guidelines: "Topluluk kuralları", contribute: "Katkı ver", support: "Destek",
      code: "Kod ve kurulum", guide: "Rehberi oku", footer: "Bir sonraki soru için ortak bir başlangıç.",
      eyebrow: "BİRLİKTE GELİŞTİREBİLECEĞİMİZ BİLGİ",
      aboutTitle: "Hazır bilgiden başla. Keşfetmeye devam et.",
      aboutIntro: "Teknoloji her gün dünyanın her yerinde gelişiyor. DevTrellis; gelişmeleri, projeleri ve kaynaklı araştırmaları, hafta sonu hobi olarak da profesyonel olarak da üreten insanların buluşabileceği ortak bir alanda toplar.",
      aboutSections: [
        ["İyi bir başlangıç bul", "Aynı soruyu yeniden araştırmadan önce mevcut çalışmalara bak. Raporu kendin oku ve kaynaklarını kontrol et. Kodlama asistanını MCP ile bağlarsan, bir belge sayfasını açmadan önce DevTrellis'e sorabilir ve varsa alıntılı kaydı kullanabilir; yeni araştırma kendi modelinle yapılır."],
        ["Bilinenin üzerine ekle", "Eksik bir kaynak, düzeltilen bir bulgu veya daha derin bir soru değerli bir katkıdır. Paylaşılan raporlarda bağlantılı araştırma sürümleri, önceki kamuya açık sürümlerle bağlantı kurarak bilginin nasıl geliştiğini gösterir; makine denetimli kayıtlara bu sitede henüz ekleme yapılamaz."],
        ["Merakını farklı alanlara taşı", "Yazılım, yapay zekâ, robotik, veri, güvenlik ve yaratıcı araçlar aynı toplulukta buluşabilir. Bilmediğin bir alanı keşfet, asıl kaynaklarını izle ve başkalarının işine yarayabilecek projenle katıl."],
        ["Sınırları görünen, büyüyen bir kütüphane", "Kapsam, kayıtlı kaynaklardan ve topluluk katkılarından oluşur; dünyadaki her gelişmeyi içermez. Arama şu anda sözcük eşleşmelerini kullanır; sonuç bulamazsan ilgili başka terimler dene. Yeniden kullanım tekrarı azaltabilir, ancak ölçülmüş zaman veya token tasarrufu vaat edilmez."]
      ],
      aboutAside: "Hesap açmadan oku",
      aboutAsideText: "Kamusal araştırmaları okumak için kurulum, abonelik veya yapay zekâ gerekmez. Kendi çalışmanı paylaşmak isteğe bağlıdır; katkı vermeden önce kamuya açılacak kopyayı incelersin.",
      aboutAction: "Topluluğu keşfet", joinAction: "Sana uygun katkıyı bul",
      guidelinesTitle: "Yararlı bilgi, iyi alışkanlıklarla büyür.",
      guidelinesIntro: "Sıradaki kişinin ne bulunduğunu, hangi kaynağa dayandığını ve nelerin hâlâ kontrol edilmesi gerektiğini anlamasına yardımcı ol.",
      guidelineSections: [
        ["İddiaların kaynağına ulaşılabilsin", "Özgün belgeleri, makaleleri, sürüm notlarını ve depoları tercih et. Kaynak bağlantılarını, ilgili sürümleri ve tarihleri ekle. Gözlemini, kaynağın iddiasını ve yorumunu ayır; kanıtın eksik olduğu yeri belirt."],
        ["Yanıtın nasıl üretildiğini göster", "Biliniyorsa modeli kaydet; bilinmiyorsa bilinmiyor yaz. Model adı, fiyatı, okunma sayısı veya yararlılık oyu doğruluğu kanıtlamaz. Yayımlanmış olması ve moderasyon incelemesi bağımsız doğrulama değildir."],
        ["Özel bağlamı dışarıda tut", "API anahtarlarını, parolaları, kişisel bilgileri, özel proje ayrıntılarını, sohbet geçmişini ve ham günlükleri kamusal araştırmaya ekleme. Kamu önizlemesinin her alanını incele. Otomatik filtreler her özel bilgiyi tanıyamaz."],
        ["Saygılı ol, ilişkin varsa belirt", "İnsanlara saldırmadan iddiaları eleştir. Spam ve tekrarlanan tanıtımdan kaçın. İçinde yer aldığın bir proje veya ürünü paylaşırken bu ilişkiyi açıkla; başkalarının ne öğrenebileceğini veya kullanabileceğini anlat."],
        ["Atfa ve izinlere dikkat et", "Uygulama kodu MIT lisanslıdır. Araştırma metninin her kayıtta belirtilen kendi lisansı vardır (CC BY 4.0 veya CC0 1.0); kendiliğinden MIT olmaz. Yararlanılan çalışmaya atıf yap ve geliştirdiğin sürümü bağla. Bağlantılı kod, görsel ve kaynak yayınlarının kendi izinleri geçerlidir."],
        ["Kaydı birlikte düzelt", "Güncelliğini yitiren bilgi veya doğruluk itirazını araştırma üzerinden bildir. Kanıt ekle ve anlamlı düzeltmeyi ya da yeni katkıyı açıkla. Operatörler katkıları inceleyebilir, reddedebilir veya kaldırabilir; yayımlanmak bir iddiayı düzeltilemez yapmaz."]
      ],
      guidelinesAction: "İçerik veya gizlilik sorununu ilet",
      contributeTitle: "Yararlı bir şeyle katıl.",
      contributeIntro: "Dikkatle seçilmiş bir kaynak, daha anlaşılır bir açıklama veya küçük bir düzeltme sıradaki kişiye yardımcı olur. Araştırma katkısı için başvuru yapman, bir ekibe katılman veya hesap açman gerekmez.",
      researchTitle: "Önce araştır. İstersen paylaş.",
      researchText: "Kamusal kütüphaneden başla. Bir eksiği araştırmak için yerel uygulamayı veya bağlı asistanını kullan, ardından seçilmiş kamu raporunu incele. Moderatöre gönderebilir veya yerelde tutabilirsin. “Makine denetimi · insan incelemedi” etiketli kayıtlar otomatik denetimden sonra, bir insan okumadan yayımlandı. Yeni kaynak ya da daha derin bir bulgu, paylaşılmış bir raporu geliştirebilir; makine denetimli kayıtlara henüz ekleme yapılamaz.",
      contributeAction: "Araştırma paylaşımı nasıl çalışır?",
      rolesTitle: "Katılmanın başka yolları",
      roles: [
        ["Araştırma", "Bir iddiayı kontrol et, kaynakları karşılaştır veya bir raporu derinleştir."],
        ["Kod", "Bir özelliği geliştir, hata düzelt veya yeniden kullanımı kolaylaştır."],
        ["Belgeleme ve çeviri", "İlk adımları İngilizce, Türkçe veya başka bir dilde daha anlaşılır yap."],
        ["Tasarım", "Okumayı, aramayı ve paylaşmayı daha erişilebilir hâle getir."],
        ["Kaynaklar", "Yararlı özgün yayınlar ve yeterince izlenmeyen ilgi alanları öner."]
      ],
      codeAction: "GitHub'daki işlere bak",
      applicationTitle: "İsteğe bağlı gönüllü katkı başvurusu",
      applicationIntro: "Düzenli yardımcı olmak veya bir fikri operatörlerle konuşmak istiyorsan kendini burada tanıtabilirsin. Bu özel mesaj, araştırma yayımlamaktan ayrıdır ve araştırma içeriği için lisans izni vermez.",
      supportTitle: "Devam edebilmek için biraz yardım.",
      supportIntro: "Aşağıdaki yanıtlardan başla veya operatörlere özel bir soru gönder. Okumak ve yardım istemek için hesap gerekmez.",
      faqTitle: "Sık sorulanlar",
      faqs: [
        ["Hesap veya yapay zekâ aboneliği gerekiyor mu?", "Kamusal araştırmaları okumak için hesap veya yapay zekâ gerekmez. Yerel uygulamada yeni araştırma üretmek, seçtiğin sağlayıcıyı ve onun erişim koşullarını kullanır. Kurulum ve seçenekler rehberde açıklanır."],
        ["Arama neden bir konuyu bulamadı?", "Kütüphane büyümeye devam ediyor ve arama sözcük eşleşmelerini kullanıyor. Daha kısa bir ifade veya ilgili başka terimler dene; bulduğun çalışmaların kaynaklarını ve tarihlerini incele."],
        ["Bir hatayı nasıl bildirebilirim?", "İzlediğin adımları, beklediğin sonucu ve gördüğün sonucu anlat. Yazılım sürümü veya kamusal araştırma bağlantısı yardımcı olabilir. API anahtarı, parola, tam günlük veya özel proje bağlantısı yapıştırma."],
        ["İçerik ya da gizlilik sorununu iletebilir miyim?", "Evet. Aşağıdaki içerik veya gizlilik kategorisini seçip ilgili kamusal sayfayı belirt. Açıklamana yalnız operatörlerin konuyu bulup incelemesi için gereken bilgiyi ekle."]
      ],
      supportFormTitle: "Özel mesaj gönder",
      supportFormIntro: "Kurulum, hata veya toplulukla ilgili bir sorun için yazabilirsin. Kamusal kod sorunları GitHub'da da konuşulabilir.",
      publicIssue: "GitHub sorunlarını aç",
      category: "Kategori", subject: "Konu", message: "Mesaj", name: "Ad (isteğe bağlı)", email: "E-posta (isteğe bağlı)",
      subjectHint: "İhtiyacını veya nasıl yardımcı olmak istediğini kısaca anlat.",
      messageHint: "Yalnız gereken bilgiyi ekle. Anahtar, parola, ham günlük veya özel dosya ekleme.",
      emailHint: "Yanıt alabilmek için e-posta adresi gerekir. Adresin kamuya gösterilmez.",
      formPrivacy: "Bu mesajı yalnız DevTrellis operatörleri elle incelemek üzere alır. Kamusal bir gönderi değildir ve araştırma için lisans izni vermez. Yanıt süresi veya otomatik e-posta onayı vaat edilmez.",
      retention: "Canlı veritabanında 90 günlük saklama süresi uygulanır. Süresi dolan mesajlar, talep hizmeti sonraki geçerli işlemi yürüttüğünde kaldırılır. Yedek kopyaların rotasyonu ayrıdır.",
      consent: "Burada gösterilen bilgileri bu talep için DevTrellis operatörlerine göndermeyi kabul ediyorum.",
      preview: "Mesajı gözden geçir", previewTitle: "Gönderilecek bilgileri kontrol et",
      previewIntro: "Henüz hiçbir şey gönderilmedi. Gönder'i seçtiğinde yalnız aşağıdaki bilgiler DevTrellis operatörlerine iletilecek.",
      retryIntro: "Teslim henüz doğrulanmadı. Teslim doğrulanana kadar mesajı değiştirmeden aynı talebi yeniden dene.",
      attemptedIntro: "Teslim henüz doğrulanmadı. Yeniden denediğinde aynı bilgiler gönderilecek.",
      kind: "Talep türü", contributionKind: "Gönüllü katkı başvurusu", supportKind: "Destek talebi",
      messageLanguage: "Mesaj dili", consentLabel: "İzin", consentValue: "Bu talebin DevTrellis operatörlerine gönderilmesi kabul edildi.",
      notProvided: "Belirtilmedi", send: "Operatörlere gönder", retry: "Aynı mesajı yeniden dene", edit: "Mesajı düzenle",
      busy: "Gönderiliyor…", receivedTitle: "Mesaj alındı",
      received: "Hizmet, mesajın alındığını doğruladı. Operatörler mesajları elle inceler; yalnız e-posta adresi verdiysen yanıt gönderilebilir. Otomatik e-posta veya yanıt süresi vaat edilmez.",
      duplicateReceived: "Hizmet, aynı mesajın daha önce alındığını doğruladı.",
      reference: "Başvuru numarası", backHome: "Topluluğa dön",
      invalid: "Gözden geçirmeden önce zorunlu alanları, uzunluklarını ve e-posta adresini kontrol et.",
      consentNeeded: "Gözden geçirmeden önce izin metnini okuyup kutuyu seç.",
      secureUnavailable: "Güvenli talep kimliği oluşturulamadı. Göndermeden önce desteklenen bir tarayıcıda sayfayı yenile.",
      failed: "Mesajın alındığı doğrulanamadı. Aynı mesajı yeniden deneyebilirsin.",
      sessionFailed: "Gönderim oturumu açılamadı. Bu denemede mesaj gönderilmedi; yeniden deneyebilirsin.",
      rejected: "Hizmet bu mesajı kabul etmedi. Yeniden denemeden önce alanları gözden geçir.",
      limited: "Hizmet çok fazla talep alıyor. Aynı mesajı yeniden denemeden önce biraz bekle.",
      expired: "Gönderim oturumu doğrulanamadı. Yeni oturumla yeniden dene.",
      receiptInvalid: "Hizmet beklenmeyen bir teslim yanıtı verdi. Teslim doğrulanmadı; aynı mesajı yeniden dene.",
      leaving: "Taslak yalnız bu sayfada kalır. Sayfadan ayrılmak veya yenilemek taslağı siler.",
      honeypot: "Web sitesi — boş bırak",
      categories: {research: "Araştırma", code: "Kod ve belgeleme", translation: "Çeviri", design: "Tasarım ve erişilebilirlik",
        sources: "Kaynaklar ve kapsam", other: "Diğer", setup: "Kurulum ve kullanım", bug: "Hata bildirimi", content: "İçerik sorunu", privacy: "Gizlilik sorunu"}
    }
  };

  function pageFromPath(pathname) {
    const page = String(pathname || "").replace(/^\/|\/$/g, "");
    return PAGES.includes(page) ? page : "about";
  }
  function initialLanguage(storage) {
    for (const key of [LANGUAGE_KEY, LEGACY_LANGUAGE_KEY]) {
      try { const value = storage?.getItem(key); if (value === "tr" || value === "en") return value; } catch {}
    }
    return "en";
  }
  function requestPayload(kind, draft, language) {
    if (!CATEGORIES[kind]?.includes(draft.category)) throw new Error("invalid");
    const fields = {subject: 160, message: 4000, name: 80, email: 254};
    const result = {kind, category: draft.category};
    for (const [key, limit] of Object.entries(fields)) {
      if (typeof draft[key] !== "string") throw new Error("invalid");
      const value = draft[key].trim();
      if (value.length > limit || (["subject", "message"].includes(key) && !value) || /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(value)) throw new Error("invalid");
      result[key] = value;
    }
    if (result.email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(result.email)) throw new Error("invalid");
    if (draft.consent !== true) throw new Error("consentNeeded");
    if (draft.website !== "") throw new Error("invalid");
    return {...result, language: language === "tr" ? "tr" : "en", consent: true, website: ""};
  }
  function createUuid(crypto) {
    if (typeof crypto?.randomUUID === "function") {
      const value = crypto.randomUUID();
      if (/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/i.test(value)) return value;
    }
    if (typeof crypto?.getRandomValues !== "function") throw new Error("secureUnavailable");
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
    const hex = [...bytes].map(value => value.toString(16).padStart(2, "0")).join("");
    return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join("-");
  }
  function validReceipt(value) {
    return Boolean(value && typeof value === "object" && typeof value.id === "string" && /^[a-f0-9]{32}$/.test(value.id) &&
      ["new", "in_review", "closed"].includes(value.status) && typeof value.duplicate === "boolean" &&
      typeof value.created_at === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(value.created_at) &&
      Number.isFinite(Date.parse(value.created_at)));
  }

  function createApp({document, window, fetcher, crypto, storage}) {
    const root = document.getElementById("community-page-root");
    const page = pageFromPath(window.location.pathname);
    const kind = ["contribute", "support"].includes(page) ? page : null;
    let language = initialLanguage(storage);
    const draft = {category: CATEGORIES[kind]?.[0] || "", subject: "", message: "", name: "", email: "", consent: false, website: ""};
    let stage = "compose", prepared = null, busy = false, attempted = false, uncertain = false, errorKey = "", receipt = null;
    const text = key => COPY[language][key];
    const node = (tag, content, className) => {
      const element = document.createElement(tag);
      if (content !== undefined) element.textContent = String(content);
      if (className) element.className = className;
      return element;
    };
    const link = (label, href, className) => {
      const element = node("a", label, className); element.href = href;
      if (href.startsWith("https://")) { element.target = "_blank"; element.rel = "noopener noreferrer"; }
      return element;
    };
    const button = (label, id, handler, secondary = false) => {
      const element = node("button", label, secondary ? "button secondary" : "button");
      element.type = "button"; element.id = id; element.disabled = busy;
      element.addEventListener("click", handler); return element;
    };
    const focus = id => { document.getElementById(id)?.focus?.(); };
    function changeLanguage(value) {
      if (busy) return;
      language = value === "tr" ? "tr" : "en";
      try { storage?.setItem(LANGUAGE_KEY, language); } catch {}
      render(); focus("pages-language");
    }
    function header() {
      const skip = link(text("skip"), "#page-main", "skip-link");
      const head = node("header", undefined, "pages-header");
      const brand = link("", "/", "pages-brand");
      const image = node("img"); image.src = "/static/ufuk.svg"; image.width = 38; image.height = 38; image.alt = "";
      const brandName = node("span", "DevTrellis"); brandName.append(node("small", text("community")));
      brand.append(image, brandName);
      const nav = node("nav", undefined, "pages-nav"); nav.setAttribute("aria-label", text("navigation"));
      nav.append(link(text("home"), "/"));
      for (const item of PAGES) {
        const anchor = link(text(item), "/" + item);
        if (item === page) anchor.setAttribute("aria-current", "page");
        nav.append(anchor);
      }
      const label = node("label", text("language"), "pages-language-label"); label.htmlFor = "pages-language";
      const select = node("select"); select.id = "pages-language"; select.disabled = busy;
      for (const [value, title] of [["en", "English"], ["tr", "Türkçe"]]) { const option = node("option", title); option.value = value; select.append(option); }
      select.value = language; select.addEventListener("change", event => changeLanguage(event.target.value)); label.append(select);
      head.append(brand, nav, label); return [skip, head];
    }
    function sections(items, className = "pages-sections") {
      const container = node("div", undefined, className);
      for (const [title, content] of items) {
        const section = node("section", undefined, "pages-section");
        section.append(node("h2", title), node("p", content)); container.append(section);
      }
      return container;
    }
    function pageContent(main) {
      const titleKey = page + "Title", introKey = page + "Intro";
      const hero = node("section", undefined, "pages-hero");
      hero.append(node("p", text("eyebrow"), "eyebrow"), node("h1", text(titleKey)), node("p", text(introKey), "pages-lead"));
      main.append(hero);
      if (page === "about") {
        const layout = node("div", undefined, "pages-about-layout"), aside = node("aside", undefined, "pages-aside");
        aside.append(node("h2", text("aboutAside")), node("p", text("aboutAsideText")), link(text("aboutAction"), "/", "button"), link(text("joinAction"), "/contribute", "pages-text-link"));
        layout.append(sections(text("aboutSections")), aside); main.append(layout);
      } else if (page === "guidelines") {
        main.append(sections(text("guidelineSections")), link(text("guidelinesAction"), "/support", "button secondary"));
      } else if (page === "contribute") {
        const research = node("section", undefined, "pages-callout");
        research.append(node("h2", text("researchTitle")), node("p", text("researchText")), link(text("contributeAction"), REPOSITORY + (language === "tr" ? "/blob/main/README.tr.md" : "#install"), "button secondary"));
        main.append(research, node("h2", text("rolesTitle"), "pages-section-title"), sections(text("roles"), "pages-role-grid"), link(text("codeAction"), REPOSITORY + "/issues", "pages-text-link"));
        renderRequest(main);
      } else {
        const faq = node("section", undefined, "pages-faq"); faq.append(node("h2", text("faqTitle")));
        for (const [question, answer] of text("faqs")) {
          const detail = node("details"); detail.append(node("summary", question), node("p", answer)); faq.append(detail);
        }
        main.append(faq);
        const actions = node("div", undefined, "pages-actions");
        actions.append(link(text("guide"), REPOSITORY + (language === "tr" ? "/blob/main/README.tr.md" : "#install"), "button secondary"), link(text("publicIssue"), REPOSITORY + "/issues", "pages-text-link"));
        main.append(actions); renderRequest(main);
      }
    }
    function privacy(container) {
      container.append(node("p", text("formPrivacy"), "pages-policy"), node("p", text("retention"), "pages-policy"));
    }
    function renderRequest(main) {
      const section = node("section", undefined, "pages-request"); section.id = "request-section";
      if (stage === "received") {
        const title = node("h2", text("receivedTitle")); title.id = "request-received"; title.tabIndex = -1;
        const success = node("div", undefined, "pages-success"); success.setAttribute("role", "status");
        success.append(title, node("p", receipt.duplicate ? text("duplicateReceived") : text("received")),
          node("p", text("reference") + ": " + receipt.id));
        if (receipt.duplicate) success.append(node("p", text("received")));
        success.append(link(text("backHome"), "/", "button secondary")); section.append(success); main.append(section); return;
      }
      if (stage === "preview") {
        const title = node("h2", text("previewTitle")); title.id = "request-preview-title"; title.tabIndex = -1;
        section.append(title, node("p", uncertain ? text("retryIntro") : attempted ? text("attemptedIntro") : text("previewIntro")));
        const list = node("dl", undefined, "pages-preview");
        const fields = [
          [text("kind"), text(prepared.kind === "contribute" ? "contributionKind" : "supportKind")],
          [text("category"), COPY[language].categories[prepared.category]],
          [text("subject"), prepared.subject], [text("message"), prepared.message],
          [text("name"), prepared.name || text("notProvided")], [text("email"), prepared.email || text("notProvided")],
          [text("messageLanguage"), prepared.language === "tr" ? "Türkçe" : "English"], [text("consentLabel"), text("consentValue")]
        ];
        for (const [label, value] of fields) list.append(node("dt", label), node("dd", value));
        section.append(list); privacy(section); renderError(section);
        const actions = node("div", undefined, "pages-actions");
        const edit = button(text("edit"), "request-edit", () => { if (!busy && !uncertain) { stage = "compose"; errorKey = ""; render(); focus("request-subject"); } }, true);
        edit.disabled = busy || uncertain;
        actions.append(edit, button(busy ? text("busy") : attempted ? text("retry") : text("send"), "request-send", send));
        section.append(actions, node("p", text("leaving"), "field-help")); main.append(section); return;
      }
      section.append(node("h2", text(kind === "contribute" ? "applicationTitle" : "supportFormTitle")),
        node("p", text(kind === "contribute" ? "applicationIntro" : "supportFormIntro")));
      const form = node("form"); form.id = "community-request-form";
      form.addEventListener("submit", event => { event.preventDefault(); preview(); });
      const grid = node("div", undefined, "pages-form-grid");
      const categoryLabel = node("label", text("category")); categoryLabel.htmlFor = "request-category";
      const category = node("select"); category.id = "request-category"; category.required = true;
      for (const value of CATEGORIES[kind]) { const option = node("option", COPY[language].categories[value]); option.value = value; category.append(option); }
      category.value = draft.category; category.addEventListener("change", event => { draft.category = event.target.value; });
      const categoryField = node("div", undefined, "pages-full"); categoryField.append(categoryLabel, category); grid.append(categoryField);
      for (const [key, limit, type, required, hint] of [
        ["subject", 160, "text", true, "subjectHint"], ["message", 4000, "textarea", true, "messageHint"],
        ["name", 80, "text", false, null], ["email", 254, "email", false, "emailHint"]
      ]) {
        const field = node("div", undefined, ["subject", "message"].includes(key) ? "pages-full" : "");
        const label = node("label", text(key)); label.htmlFor = "request-" + key;
        const input = node(type === "textarea" ? "textarea" : "input"); input.id = "request-" + key; input.name = key;
        if (type !== "textarea") input.type = type; else input.rows = 7;
        input.maxLength = limit; input.required = required; input.value = draft[key]; input.autocomplete = "off";
        input.addEventListener("input", event => { draft[key] = event.target.value; }); field.append(label, input);
        if (hint) { const help = node("p", text(hint), "field-help"); help.id = "request-" + key + "-help"; input.setAttribute("aria-describedby", help.id); field.append(help); }
        grid.append(field);
      }
      const trap = node("div", undefined, "pages-honeypot"); trap.setAttribute("aria-hidden", "true");
      const trapLabel = node("label", text("honeypot")); trapLabel.htmlFor = "request-website";
      const website = node("input"); website.id = "request-website"; website.name = "website"; website.type = "text";
      website.tabIndex = -1; website.autocomplete = "off"; website.value = draft.website;
      website.addEventListener("input", event => { draft.website = event.target.value; }); trap.append(trapLabel, website); grid.append(trap);
      form.append(grid); privacy(form);
      const consentLabel = node("label", undefined, "pages-consent"); consentLabel.htmlFor = "request-consent";
      const consent = node("input"); consent.id = "request-consent"; consent.type = "checkbox"; consent.required = true; consent.checked = draft.consent;
      consent.addEventListener("change", event => { draft.consent = event.target.checked; });
      consentLabel.append(consent, node("span", text("consent"))); form.append(consentLabel); renderError(form);
      const review = node("button", text("preview"), "button"); review.id = "request-preview"; review.type = "submit"; form.append(review, node("p", text("leaving"), "field-help"));
      section.append(form); main.append(section);
    }
    function renderError(container) {
      if (!errorKey) return;
      const error = node("p", text(errorKey), "notice error"); error.id = "request-error"; error.tabIndex = -1; error.setAttribute("role", "alert"); container.append(error);
    }
    function preview() {
      if (busy || stage !== "compose") return;
      try {
        const payload = requestPayload(kind, draft, language);
        const previous = prepared ? {...prepared} : null;
        if (previous) delete previous.idempotency_key;
        const same = previous && JSON.stringify(previous) === JSON.stringify(payload);
        const key = same ? prepared.idempotency_key : createUuid(crypto);
        if (!same) attempted = false;
        prepared = {...payload, idempotency_key: key}; stage = "preview"; errorKey = ""; render(); focus("request-preview-title");
      } catch (error) { errorKey = Object.hasOwn(COPY.en, error.message) ? error.message : "invalid"; render(); focus("request-error"); }
    }
    async function send() {
      if (busy || stage !== "preview" || !prepared || receipt) return;
      busy = true; errorKey = ""; render(); let postStarted = false;
      try {
        const sessionResponse = await fetcher("/api/community/session", {method: "POST", credentials: "same-origin", headers: {Accept: "application/json", "Content-Type": "application/json"}, body: "{}"});
        if (!sessionResponse.ok) throw new Error("sessionFailed");
        const session = await sessionResponse.json();
        if (!session || typeof session.csrf_token !== "string" || !session.csrf_token || session.csrf_token.length > 256) throw new Error("sessionFailed");
        postStarted = true; attempted = true;
        const response = await fetcher("/api/community/requests", {method: "POST", credentials: "same-origin",
          headers: {Accept: "application/json", "Content-Type": "application/json", "X-Ufuk-CSRF": session.csrf_token},
          body: JSON.stringify(prepared)});
        if (!response.ok) {
          const knownRejection = [400, 401, 403, 404, 409, 413, 415, 422, 429].includes(response.status);
          const failure = new Error(response.status === 429 ? "limited" : response.status === 403 ? "expired" : knownRejection ? "rejected" : "failed");
          failure.knownRejection = knownRejection; throw failure;
        }
        const value = await response.json();
        if (![200, 201].includes(response.status) || !validReceipt(value)) throw new Error("receiptInvalid");
        receipt = value; stage = "received"; prepared = null;
        for (const key of ["subject", "message", "name", "email", "website"]) draft[key] = "";
        draft.consent = false;
      } catch (error) {
        if (postStarted && !error.knownRejection) uncertain = true;
        errorKey = postStarted ? (Object.hasOwn(COPY.en, error.message) ? error.message : "failed") : "sessionFailed";
      } finally { busy = false; render(); focus(stage === "received" ? "request-received" : "request-error"); }
    }
    function render() {
      document.documentElement.lang = language;
      document.title = text(page) + " — DevTrellis";
      root.replaceChildren(...header());
      const main = node("main", undefined, "pages-main"); main.id = "page-main"; main.tabIndex = -1;
      pageContent(main);
      const footer = node("footer", undefined, "pages-footer");
      footer.append(node("strong", "DevTrellis"), node("span", text("footer")), link(text("code"), REPOSITORY),
        link(text("guide"), REPOSITORY + (language === "tr" ? "/blob/main/README.tr.md" : "#install")));
      main.append(footer); root.append(main);
    }
    // UfukUI.initialize binds language changes to reload. Share its preference key,
    // but render these pages directly so switching EN/TR never discards a draft.
    render();
    return {render, changeLanguage};
  }
  if (typeof module !== "undefined" && module.exports) module.exports = {PAGES, COPY, pageFromPath, initialLanguage, requestPayload, createUuid, validReceipt, createApp};
  if (typeof document !== "undefined" && typeof window !== "undefined") {
    let storage; try { storage = window.localStorage; } catch {}
    createApp({document, window, storage, crypto: window.crypto, fetcher: (...args) => window.fetch(...args)});
  }
})();
