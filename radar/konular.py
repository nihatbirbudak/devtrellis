"""Konu kimliklerini, iki dilde adlarını ve genel araştırma bağlamlarını tek yerde tanımlar."""

import copy


def tanim(label_tr, label_en, query, github_queries, audit_queries, markers, summary,
          priorities, *, environment=None, constraints=None, legacy=False):
    return {"label_tr": label_tr, "label_en": label_en, "legacy": legacy, "query": query,
            "github_queries": github_queries, "audit_queries": audit_queries, "markers": markers,
            "profile": {"summary": summary, "environment": environment or ["Geliştirme bilgisayarı", "İzole test ortamı"],
                        "constraints": constraints or ["Üretim ortamı değiştirilmez.", "Kaynak iddiası yerel ölçüm sayılmaz."],
                        "priorities": priorities}}


_TANIMLAR = {
    "ai": tanim("Yapay zekâ araçları", "AI tools", "deep research agents evidence citation evaluation",
        ("topic:ai-agents", "topic:retrieval-augmented-generation"),
        ('"research agent" AND evaluation', '"retrieval augmented generation" AND grounding'),
        ("research", "agent", "retriev", "rag", "llm", "citation", "language model"),
        "Yazılım ve araştırma işlerine yararlı model ve açık araç gelişmelerini değerlendir.",
        ["Türkçe kalite", "Gecikme ve maliyet", "Ajan güvenilirliği"], legacy=True,
        environment=["Yerel bilgisayar", "Python ve SQLite"],
        constraints=["Ücretli hizmet kendiliğinden açılmaz.", "Kaynak iddiası yerel ölçüm sayılmaz."]),
    "jarvis": tanim("Ev otomasyonu", "Home automation", "voice assistant memory home automation",
        ("topic:voice-assistant", "topic:home-automation"),
        ('"voice assistant" AND privacy', '"personal assistant" AND memory'),
        ("assistant", "voice", "speech", "home-automation", "home automation", "smart home", "smarthome", "iot"),
        "Ev otomasyonu ve yerel asistanlar için yeni yöntemleri araştır.",
        ["Güvenilirlik", "Gizlilik", "Geri alınabilir deneme"], legacy=True,
        environment=["Yerel bilgisayar", "Python ve SQLite"],
        constraints=["Fiziksel cihazlarda değişiklik insan onayı ister.", "Özel veriler ve anahtarlar araştırmaya dahil edilmez."]),
    "robotik": tanim("Robotik ve algılama", "Robotics and perception", "robot perception sensor fusion navigation",
        ("topic:robotics", "topic:robot-navigation"), ('"robot manipulation"', '"robot navigation" AND perception'),
        ("robot", "ros", "slam", "manipulat", "navigation", "drone"),
        "Sensör, kamera ve robotik yöntemlerini araştır.", ["Algılama doğruluğu", "Deneme maliyeti"], legacy=True,
        environment=["Geliştirme bilgisayarı", "Sensörler"],
        constraints=["Fiziksel hareket başlatılmaz.", "Donanım uyumu ölçülmeden doğrulanmış sayılmaz."]),
    "tarim": tanim("Tarım ve sera", "Agriculture and greenhouse", "greenhouse irrigation climate control",
        ("topic:irrigation", "topic:greenhouse"), ("greenhouse AND irrigation", 'greenhouse AND "climate control"'),
        ("irrigat", "agricultur", "greenhouse", "farm", "crop", "garden", "sprinkler"),
        "Sera iklimi, sulama ve enerji alanındaki araştırmaları izle.", ["Su ve enerji tasarrufu", "Somut ölçüm"], legacy=True,
        environment=["Sera", "Sensör izleme"],
        constraints=["Araştırma önerisi saha komutu değildir.", "Güvenlik ve mevzuat uzman doğrulaması ister."]),
    "medya": tanim("Medya üretimi", "Media production", "audio video editing speech synthesis",
        ("topic:video-editing", "topic:text-to-speech"), ('"video editing"', '"speech synthesis" AND evaluation'),
        ("video", "audio", "speech", "editing", "tts", "synthesi", "multimedia"),
        "Video, ses ve içerik üretimi araçlarını araştır.", ["Üretim süresi", "Görüntü ve ses kalitesi"], legacy=True,
        environment=["Yerel bilgisayar", "Medya iş akışları"],
        constraints=["Telif ve lisans koşulları ayrıca doğrulanır.", "Yayın kendiliğinden yapılmaz."]),
    "software": tanim("Yazılım geliştirme", "Software development", "software engineering developer tools testing programming languages",
        ("topic:developer-tools", "topic:software-engineering"),
        ('"software engineering" AND testing', '"programming language" AND developer'),
        ("software", "developer", "programming", "compiler", "testing", "framework", "library"),
        "Programlama dilleri, geliştirme araçları, test ve bakım yöntemlerindeki gelişmeleri değerlendir.",
        ["Bakım kolaylığı", "Geriye uyumluluk", "Tekrarlanabilir testler"],
        constraints=["Kod çalıştırma ve bağımlılık kurma ayrıca onaylanır.", "Sürüm uyumu ölçülmeden doğrulanmış sayılmaz."]),
    "security": tanim("Yazılım güvenliği", "Software security", "software security vulnerability remediation secure development",
        ("topic:application-security", "topic:security-tools"),
        ('"software vulnerability" AND remediation', '"secure software" AND verification'),
        ("security", "vulnerab", "secure", "cryptograph", "patch", "threat", "hardening"),
        "Resmî güvenlik duyurularını, düzeltmeleri ve güvenli geliştirme yöntemlerini incele.",
        ["Etkilenen sürümlerin doğruluğu", "Düzeltme seçenekleri", "Kaynak tarihi"],
        constraints=["Duyuru kapsamı ve etkilenen sürümler birincil kaynaktan doğrulanır.", "Canlı sistemlerde tarama veya değişiklik yapılmaz."]),
    "data": tanim("Veri ve veritabanları", "Data and databases", "data engineering database query processing data quality",
        ("topic:data-engineering", "topic:database"),
        ('"database" AND performance AND reproducibility', '"data quality" AND engineering'),
        ("database", "data", "sql", "postgres", "storage", "analytics", "pipeline"),
        "Veritabanları, veri işleme ve veri kalitesi alanındaki gelişmeleri araştır.",
        ["Veri bütünlüğü", "Ölçülebilir performans", "Geri alınabilir geçiş"],
        environment=["Örnek veri kümesi", "Yerel veya izole veritabanı"],
        constraints=["Özel veri kümeleri araştırmaya gönderilmez.", "Veri geçişi ve üretim değişikliği ayrıca onaylanır."]),
}

KONULAR = tuple(_TANIMLAR)
ESKI_KONULAR = frozenset(identifier for identifier, value in _TANIMLAR.items() if value["legacy"])


def konu(identifier):
    """Konu tanımının bağımsız kopyasını verir; bilinmeyen kimliği sessizce değiştirmez."""
    return {"id": identifier, **copy.deepcopy(_TANIMLAR[identifier])}


def konulari_listele():
    return [{"id": identifier, "label_tr": value["label_tr"], "label_en": value["label_en"]}
            for identifier, value in _TANIMLAR.items()]


def konu_etiketleri(language="tr"):
    field = "label_en" if language == "en" else "label_tr"
    return {identifier: value[field] for identifier, value in _TANIMLAR.items()}
