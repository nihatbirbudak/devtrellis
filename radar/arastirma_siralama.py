"""Kamu araştırmalarını sözcük kapsamıyla sıralar; eşleşmeyi cevap doğruluğu saymaz."""

import re
import unicodedata

from radar.arastirma_arama import GRUP_SINIRI
from radar.gelistirici_alani import arama_metni, metin


INDEX_VERSION = 4
_TOKEN = re.compile(r"[^\W_]+(?:[._/-][^\W_]+)*(?:\+{1,2}|#)?", re.UNICODE)
_CONNECTORS = frozenset("a an the and or vs versus of for to in on at by with is are was were be been do does did can could would should how why what which when where i my me we our you your it its this that these those please about ve veya ile icin bir bu su o da de mi mu ne neden nasil nedir benim bizim sen siz sizin olan olarak bana bize lutfen hakkinda".split())
# Küçük ve açık bir teknik sözlük; bütün dillerde anlamsal eşdeğerlik iddiası yok.
_CONCEPTS = (
    ("research", "arastirma", "arastirmalar", "arastirmasi", "arastirmayi", "investigation", "investigations"),
    ("search", "arama", "aramasi", "aramayi", "retrieval"),
    ("source", "sources", "kaynak", "kaynaklar", "kaynaklari"),
    ("ranking", "rank", "siralama", "siralamasi"),
    ("cache", "caching", "onbellek", "onbellekleme"),
    ("permission", "permissions", "izin", "izinler", "izinleri"),
    ("release", "releases", "surum", "surumu", "surumler", "version", "versions"),
    ("update", "updates", "guncelleme", "guncellemeler"),
    ("local", "yerel"),
    ("review", "inceleme", "incelemesi"),
    ("reuse", "reusable", "reusing"),
    ("limit", "limits", "limitation", "limitations", "sinir", "sinirlari"),
    ("access", "erisim", "erisimi"),
    ("data", "veri", "veriler"),
    ("database", "databases", "veritabani", "veritabanlari"),
    ("postgresql", "postgres"),
    ("analysis", "analyses", "analiz", "analizi"),
    ("memory", "bellek", "bellekte"),
    ("index", "indexing", "indeks", "dizin"),
    ("dependency", "dependencies", "bagimlilik", "bagimliliklar"),
    ("keyboard", "klavye"),
    ("focus", "odak", "odagi"),
    ("accessibility", "erisilebilirlik"),
    ("size", "boyut", "boyutu"),
    ("target", "hedef"),
    ("thread", "threads", "threading", "threaded"),
    ("request", "requests", "istek", "istekler", "istekleri"),
    ("conditional", "kosullu"),
    ("notification", "notifications", "bildirim", "bildirimler"),
)
# Sınırlı teknik adlar kısmi eşleşmede düşürülemez; bu bir varlık tanıma modeli değildir.
_EXACT_NAMES = frozenset("python sqlite postgresql duckdb polars github gitlab nvidia cuda rocm claude codex openai blender kubernetes docker react android firebase webauthn wcag mcp".split())
_ROS_RELEASES = frozenset("humble jazzy kilted foxy galactic".split())
_ALIASES = {word: group[0] for group in _CONCEPTS for word in group}
_ALIAS_BASES = tuple(sorted(_ALIASES, key=len, reverse=True))
_TURKISH_ENDINGS = frozenset(("i", "u", "si", "su", "yi", "yu", "ni", "nu", "nin", "nun", "ini", "unu",
                            "da", "de", "dan", "den", "nda", "nde", "ndan", "nden", "yla", "yle",
                            "lar", "ler", "lari", "leri", "larini", "lerini", "larinda", "lerinde",
                            "larindan", "lerinden", "sinda", "sinde", "sindan", "sinden"))


def _normalize(value):
    return "".join(char for char in unicodedata.normalize("NFKD", arama_metni(value))
                   if not unicodedata.combining(char))


def _words(value):
    # Apostrofla yazılmış Türkçe özel ad ekleri sözcüğün parçası değildir.
    value = re.sub(r"(?<=\w)['’](?:nin|nun|in|un|ya|ye|yi|yu|dan|den|da|de|a|e|i|u|s)\b", "", _normalize(value))
    value = re.sub(r"\bveri taban(i|lari)\b", r"veritaban\1", value)
    # ROS 2/ROS2 aynı teknik addır; sayı birlikte korunur, ROS 1 ile karışmaz.
    # Diğer sözcük+sayılar miktar olabilir; genel birleştirme uygulanmaz.
    value = re.sub(r"(?<![\w./+#-])ros\s+([0-9]+)(?![\w./+#-])", r"ros\1", value)
    return _TOKEN.findall(value)


def _variants(word):
    if word in _ALIASES:
        return (_ALIASES[word],)
    # Yalnız sözlükteki bilinen Türkçe terimin açık ekleri; rastgele adları kesmez.
    for base in _ALIAS_BASES:
        if len(base) >= 4 and word.startswith(base) and word[len(base):] in _TURKISH_ENDINGS:
            return (_ALIASES[base],)
    result = {word}
    if "-" in word and not any(char.isdigit() for char in word):
        result.add("-".join(_ALIASES.get(part, part) for part in word.split("-")))
    # Teknik ad, sürüm, yol ve alt çizgili belirteçlere ek kesme uygulanmaz.
    if word.isalpha() and len(word) > 4:
        if word.endswith("ies") and len(word) > 5:
            result.add(word[:-3] + "y")
        elif word.endswith("s") and not word.endswith(("ss", "us", "is")):
            result.add(word[:-1])
        for suffix in ("ing", "ed"):
            if word.endswith(suffix) and len(word) - len(suffix) >= 4:
                stem = word[:-len(suffix)]
                result.add(stem)
                result.add(stem + "e")
                if len(stem) > 4 and stem[-1] == stem[-2]:
                    result.add(stem[:-1])
                break
    return tuple(sorted(result))


def _encode(word):
    # FTS sorgu dili ile C++, sürüm ve yol işaretleri birbirine karışmaz.
    return "t" + word.encode("utf-8").hex()


def _options(word):
    return tuple(_encode(value) for value in _variants(word))


def _index_text(text):
    tokens = set()
    for word in _words(text):
        tokens.update(_options(word))
        # Sürümsüz ROS araması üst adı bulur; sorgudaki ros2 yine zorunludur.
        if re.fullmatch(r"ros[0-9]+", word):
            tokens.update(_options("ros"))
        # Birleşik sözcüğün tam biçimi de korunur; sayısal sürümler bölünmez.
        if "-" in word and not any(char.isdigit() for char in word):
            for part in word.split("-"):
                tokens.update(_options(part))
    return " ".join(sorted(tokens))


def belge_terimleri(packet):
    """Yalnız rapor içeriğini indeksler; hesap, oy ve model adı sıralamaya girmez."""
    return {"title": _index_text(packet["title"]), "summary": _index_text(packet["summary"]),
            "tags": _index_text(" ".join(packet["tags"])), "body": _index_text(packet["body"])}


def sorgu_hazirla(query):
    """Kısmi adayların asgari kapsamını ve korunacak teknik belirteçleri hazırlar."""
    words = list(dict.fromkeys(_words(metin(query, "Arama", 200))))
    content = [word for word in words if word not in _CONNECTORS]
    # Salt bağlaç/noktalama sorgusu bütün kütüphaneye dönüşmez.
    if not content:
        content = words or (["!empty-query!"] if query.strip() else [])
    if len(content) > GRUP_SINIRI:
        raise ValueError(f"Arama en fazla {GRUP_SINIRI} farklı terim grubu içerebilir.")
    groups, labels, required = [], [], []
    for word in content:
        options = _options(word)
        if options in groups:
            continue
        index = len(groups)
        groups.append(options)
        labels.append(word)
        # Bir sorudaki 44 piksel gibi yanlış olabilecek miktarlar filtre değildir.
        # Ondalıklı sürümler, alfasayısal teknik adlar ve yollar korunur.
        if ((any(char.isdigit() for char in word) and not word.isdecimal())
                or any(char in word for char in "_/.+#")
                or _EXACT_NAMES.intersection(_variants(word)) or word in _ROS_RELEASES
                or (word in {"iron", "rolling"} and {"ros", "ros2"}.intersection(content))):
            required.append(index)
    # Uzun doğal sorunun bilinmeyen sözcükleri, bulunan iki bağımsız kavramı
    # yok etmez. Eksik kalan bütün terimler adayla birlikte açıkça döndürülür.
    minimum = min(2, len(groups))
    expression = " OR ".join('"' + term + '"' for term in dict.fromkeys(term for group in groups for term in group))
    return {"groups": tuple(groups), "labels": tuple(labels), "required": tuple(required),
            "minimum": minimum, "match": expression}


def eslesme_bilgisi(plan, doc_fields):
    """Sözcük kapsamını açıklar; tam sözcük eşleşmesi bile yeterli cevap demek değildir."""
    tokens = set(" ".join(doc_fields.values()).split())
    matched, missing = [], []
    for label, group in zip(plan["labels"], plan["groups"]):
        (matched if tokens.intersection(group) else missing).append(label)
    return {"matched_terms": matched, "missing_terms": missing, "all_terms_matched": not missing,
            "answer_sufficiency": "not_assessed"}


def aday_kapsami(plan, doc_fields):
    """Genel gövde sözcüklerinden doğan zayıf kısmi adayı sıralamaya sokmaz."""
    tokens = set(" ".join(doc_fields.values()).split())
    matched = [bool(tokens.intersection(group)) for group in plan["groups"]]
    count = sum(matched)
    if count < plan["minimum"] or any(not matched[index] for index in plan["required"]):
        return -1
    headline = set(" ".join(doc_fields[name] for name in ("title", "summary", "tags")).split())
    if (count < len(matched) and not plan["required"]
            and not any(headline.intersection(group) for group in plan["groups"])):
        return -1
    return count
