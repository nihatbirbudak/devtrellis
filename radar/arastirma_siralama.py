"""Kamu araştırmalarını sözcük kapsamıyla sıralar; eşleşmeyi cevap doğruluğu saymaz.

Dizin ve sorgu aynı normalleştiriciden geçer (``belirtecler``): ı/İ katlama, aksan düşürme,
harf-rakam sınırında birleşik ve ayrık biçim (ros2 ⇄ ros 2, h264, fts5), C++/C#/.NET/snake_case
ve sürüm numaraları tek belirteç, vs/versus/karşı bağlaç. Birleşik biçim hiçbir zaman zorunlu
değildir: belgede ayrık yazım varsa parçalar birlikte eşleşir.
"""

import re
import unicodedata

from radar.arastirma_arama import GRUP_SINIRI
from radar.gelistirici_alani import arama_metni, metin


INDEX_VERSION = 5
# Alan sırası FTS tablosu ve bm25 ağırlıklarıyla aynıdır (topluluk_depo.sirali_arama).
INDEX_FIELDS = ("title", "answers", "tags", "summary", "body", "limits")
FIELD_WEIGHTS = {"title": 6.0, "answers": 5.0, "tags": 4.0, "summary": 3.0, "body": 1.0, "limits": 0.5}
HEADLINE_FIELDS = ("title", "answers", "tags", "summary")
_TOKEN = re.compile(r"\.?[^\W_]+(?:[._/-][^\W_]+)*(?:\+{1,2}|#)?", re.UNICODE)
_PART_SPLIT = re.compile(r"[._/-]")
_LETTERS_DIGITS = re.compile(r"([a-z]+)([0-9]+)")
# Sürüm sayıları (3.14, v1.2.3) bölünmez; parçaları yoktur.
_NUMERIC = re.compile(r"v?[0-9]+(?:\.[0-9]+)*")
_DOTTED = re.compile(r"([0-9]+)(?:\.[0-9]+)+")
# Kısa harf öbeği + tam sayı (ros 2, h 264, fts 5) aynı teknik adın ayrık yazımıdır; uzun sözcük + sayı
# (python 3, requires 44) miktar veya gevşek sürüm olabilir, birleştirilmez. Ondalık sürüm (2.2) ayrı kalır.
_ACRONYM_NUMBER = re.compile(r"(?<![\w./+#-])([a-z]{1,4})\s+([0-9]{1,4})(?![\w./+#-])")
# Sayı sözcükleri ve bağlaçlar birleştirilmez (iki 3 adım, the 3 steps).
_NO_MERGE_WORDS = frozenset("one two three four five six seven eight nine ten bir iki uc dort bes alti yedi sekiz dokuz on yuz bin".split())
_CONNECTORS = frozenset("a an the and or vs versus of for to in on at by with is are was were be been do does did can could would should how why what which when where i my me we our you your it its this that these those please about ve veya ile icin karsi bir bu su o da de ya mi mu ne neden nasil nedir benim bizim sen siz sizin olan olarak bana bize lutfen hakkinda".split())
# Küçük ve açık bir teknik sözlük; bütün dillerde anlamsal eşdeğerlik iddiası yok.
# DONDURULDU (7 Kasım 2026, KD-031): 29 grup. Test ve altın küme sorgularından giriş eklenmez;
# yeni karşılıklar sözlükten değil raporun kendi alanlarından (answers, etiket) gelir.
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
CONCEPT_GROUP_COUNT = 29
# Sınırlı teknik adlar kısmi eşleşmede düşürülemez; bu bir varlık tanıma modeli değildir.
_EXACT_NAMES = frozenset("python sqlite postgresql duckdb polars github gitlab nvidia cuda rocm claude codex openai blender kubernetes docker react android firebase webauthn wcag mcp".split())
_ROS_RELEASES = frozenset("humble jazzy kilted foxy galactic".split())
_ALIASES = {word: group[0] for group in _CONCEPTS for word in group}
_ALIAS_BASES = tuple(sorted(_ALIASES, key=len, reverse=True))
_TURKISH_ENDINGS = frozenset(("i", "u", "si", "su", "yi", "yu", "ni", "nu", "nin", "nun", "ini", "unu",
                            "da", "de", "dan", "den", "nda", "nde", "ndan", "nden", "yla", "yle",
                            "lar", "ler", "lari", "leri", "larini", "lerini", "larinda", "lerinde",
                            "larindan", "lerinden", "sinda", "sinde", "sindan", "sinden"))
# Gövdedeki model üretim notu araştırmanın konusu değildir; yalnız indeks dışında tutulur, kayıt değişmez.
# Desen cümle başına demirlidir ve yalnız öz-referanslı nota uyar ("This … synthesis generated with NVIDIA",
# "Generated with …", "Bu rapor … model ile üretildi"); konuyla ilgili cümleler ("Nemotron models are produced
# by NVIDIA") kalır. Cümle cümle uygulanır; geri izleme cümle uzunluğuyla sınırlıdır (doğrusal).
_PROVIDERS = (r"(?:nvidia|openai|anthropic|google|meta|mistral|claude|codex|gpt|gemini|llama|nemotron|deepseek|qwen|copilot|"
              r"language\s+model|llm|ai\s+model|model)")
_GENERATION_VERB = r"(?:generated|synthesi[sz]ed|produced|drafted|written|created|prepared|compiled)\s+(?:with|by|using)\s+(?:an?\s+|the\s+)?"
_GENERATION_NOTE = re.compile(
    r"^\s*(?:this\s+(?:[\w-]+\s+){0,3}?(?:report|summary|synthesis|review|document|text|note|draft|research|article|"
    r"overview|content|analysis|page|section)\b(?:\s+[\w-]+){0,6}?\s+" + _GENERATION_VERB + _PROVIDERS + r"\b"
    r"|" + _GENERATION_VERB + _PROVIDERS + r"\b"
    r"|bu\s+(?:rapor|ozet|özet|metin|belge|taslak|inceleme|sentez|not|arastirma|araştırma|icerik|içerik)\b(?:\s+[\w-]+){0,8}?\s+"
    r"(?:ile|tarafindan|tarafından)\s+(?:uretil|üretil|olusturul|oluşturul|yazil|yazıl|hazirlan|hazırlan)\w*)",
    re.IGNORECASE)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_LIMITS_HEADING = re.compile(r"^#{1,6}\s*(?:limits?|limitations?|known\s+limitations?|scope\s+limits?|"
                             r"s[ıi]n[ıi]rlar(?:[ıi]|[ıi]m[ıi]z)?|k[ıi]s[ıi]tlar|kapsam\s+d[ıi]ş[ıi])\s*$", re.IGNORECASE)
_ANY_HEADING = re.compile(r"^#{1,6}\s")


def normallestir(value):
    """Tek normalleştirici: NFKC + casefold + ı/İ katlama (arama_metni), sonra aksanlar düşer."""
    return "".join(char for char in unicodedata.normalize("NFKD", arama_metni(value))
                   if not unicodedata.combining(char))


def belirtecler(value):
    """Dizin ve sorgu için ortak belirteçleme; birleştirme yalnız kısa ad + tam sayı için yapılır."""
    # Apostrofla yazılmış Türkçe özel ad ekleri sözcüğün parçası değildir.
    value = re.sub(r"(?<=\w)['’](?:nin|nun|in|un|ya|ye|yi|yu|dan|den|da|de|a|e|i|u|s)\b", "", normallestir(value))
    value = re.sub(r"\bveri taban(i|lari)\b", r"veritaban\1", value)
    value = _ACRONYM_NUMBER.sub(lambda match: match[0] if match[1] in _CONNECTORS or match[1] in _NO_MERGE_WORDS
                                else match[1] + match[2], value)
    return _TOKEN.findall(value)


_words = belirtecler


def _parcalar(word):
    """Bileşik belirtecin ayrık yazımı: tire/nokta/eğik çizgi parçaları ve harf-rakam sınırı.

    Sürüm numaraları (3.14, 2.2.1, v1.2.3) bölünmez. Parçası olmayan belirteç için boş döner.
    Dizin tarafı bunları da yazar; sorguda parça eşleşmesi yalnız rakamsız bileşikler için geçerlidir
    (``_sorgu_parcalari``), yoksa "ros2" gövdedeki başıboş bir "2" ile eşleşir ve sürüm tuzağı delinir.
    """
    if _NUMERIC.fullmatch(word):
        return ()
    parts = [part for part in _PART_SPLIT.split(word) if part]
    if len(parts) == 1:
        match = _LETTERS_DIGITS.fullmatch(word)
        parts = [match[1], match[2]] if match else []
    return tuple(part for part in parts if part != word)


def _sorgu_parcalari(word):
    """Sorguda bütün parçaların ayrı ayrı geçmesi yalnız rakamsız bileşikte (x-github-delivery) yeter."""
    if any(char.isdigit() for char in word):
        return ()
    return _parcalar(word)


def _variants(word):
    if word in _ALIASES:
        return (_ALIASES[word],)
    # Yalnız sözlükteki bilinen Türkçe terimin açık ekleri; rastgele adları kesmez.
    for base in _ALIAS_BASES:
        if len(base) >= 4 and word.startswith(base) and word[len(base):] in _TURKISH_ENDINGS:
            return (_ALIASES[base],)
    result = {word}
    if word.startswith(".") and len(word) > 1:
        result.add(word[1:])
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
    words = belirtecler(text)
    for position, word in enumerate(words):
        tokens.update(_options(word))
        # Bileşik belirtecin parçaları da indekste: ros2 → ros, 2; x-github-delivery → x, github, delivery.
        parts = _parcalar(word)
        for part in parts:
            tokens.update(_options(part))
        # Tireli/eğik çizgili bileşiğin yapışık biçimi (gpt-4 → gpt4, if-none-match → ifnonematch).
        if len(parts) > 1:
            tokens.add(_encode("".join(parts)))
        # Ayrık yazımın birleşik biçimi de indekste (python 3 → python3, wcag 2.2 → wcag2.2 ve wcag2),
        # böylece sorgu birleşik ya da yalnız ana sürümle yazsa da bulunur; birleşik biçim belgede zorunlu değildir.
        if position + 1 < len(words) and word.isalpha():
            following = words[position + 1]
            if _NUMERIC.fullmatch(following) and not following.startswith("v"):
                tokens.add(_encode(word + following))
                dotted = _DOTTED.fullmatch(following)
                if dotted:
                    tokens.add(_encode(word + dotted[1]))
    return " ".join(sorted(tokens))


def _metin_listesi(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [item for item in value if isinstance(item, str)]
    return []


def uretim_notu_mu(sentence):
    """Cümle öz-referanslı bir model üretim notu mu? Desen cümle başına demirlidir."""
    return _GENERATION_NOTE.match(sentence) is not None


def _uretim_notunu_cikar(line):
    """Satırı cümlelere böler, yalnız üretim notu cümlelerini düşürür; doğrusal çalışır."""
    sentences = _SENTENCE_SPLIT.split(line)
    if len(sentences) == 1 and not uretim_notu_mu(line):
        return line
    return " ".join(sentence for sentence in sentences if not uretim_notu_mu(sentence))


def govde_alanlari(body):
    """Gövdeyi indeks için ayırır: üretim notu dışarıda, Limits bölümü ayrı alanda; kayıt değişmez."""
    main, limits, in_limits = [], [], False
    for line in str(body or "").splitlines():
        if _LIMITS_HEADING.match(line):
            in_limits = True
            continue
        if in_limits and _ANY_HEADING.match(line):
            in_limits = False
        (limits if in_limits else main).append(_uretim_notunu_cikar(line))
    return {"body": "\n".join(main), "limits": "\n".join(limits)}


def belge_terimleri(packet):
    """Yalnız rapor içeriğini indeksler; hesap, oy ve model adı sıralamaya girmez.

    ``answers`` (raporun cevapladığı sorular) pakette yoksa alan boş kalır ve etkisizdir.
    """
    parts = govde_alanlari(packet.get("body", ""))
    return {"title": _index_text(packet.get("title", "")),
            "answers": _index_text(" ".join(_metin_listesi(packet.get("answers")))),
            "tags": _index_text(" ".join(_metin_listesi(packet.get("tags", [])))),
            "summary": _index_text(packet.get("summary", "")),
            "body": _index_text(parts["body"]), "limits": _index_text(parts["limits"])}


def sorgu_hazirla(query):
    """Kısmi adayların asgari kapsamını ve korunacak teknik belirteçleri hazırlar.

    Her grup bir sorgu sözcüğüdür: ``groups`` seçeneklerden biri geçince eşleşir; ``parts`` dolu
    olan bileşik sözcük, belgede bütün parçaları ayrı ayrı geçince de eşleşir (birleşik biçim
    zorunlu değildir).
    """
    words = list(dict.fromkeys(belirtecler(metin(query, "Arama", 200))))
    content = [word for word in words if word not in _CONNECTORS]
    # Salt bağlaç/noktalama sorgusu bütün kütüphaneye dönüşmez.
    if not content:
        content = words or (["!empty-query!"] if query.strip() else [])
    if len(content) > GRUP_SINIRI:
        raise ValueError(f"Arama en fazla {GRUP_SINIRI} farklı terim grubu içerebilir.")
    groups, labels, required, parts = [], [], [], []
    for word in content:
        options = _options(word)
        if options in groups:
            continue
        index = len(groups)
        groups.append(options)
        labels.append(word)
        parts.append(tuple(_options(part) for part in _sorgu_parcalari(word)))
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
    terms = dict.fromkeys(term for group in groups for term in group)
    terms.update(dict.fromkeys(term for word_parts in parts for group in word_parts for term in group))
    expression = " OR ".join('"' + term + '"' for term in terms)
    return {"groups": tuple(groups), "labels": tuple(labels), "required": tuple(required),
            "parts": tuple(parts), "minimum": minimum, "match": expression}


def _grup_eslesti(tokens, group, parts):
    """Seçeneklerden biri ya da bileşik sözcüğün bütün parçaları geçiyorsa eşleşir."""
    if tokens.intersection(group):
        return True
    return bool(parts) and all(tokens.intersection(part) for part in parts)


def _grup_dokunuyor(tokens, group, parts, hints):
    """Başlık denetimi için gevşek temas: seçenek ya da herhangi bir parça (rakamlı bileşikte de ipucu)."""
    return bool(tokens.intersection(group)) or any(tokens.intersection(part) for part in (*parts, *hints))


def _plan_parcalari(plan):
    return plan.get("parts") or ((),) * len(plan["groups"])


def _eslesmeler(plan, tokens):
    return [_grup_eslesti(tokens, group, parts) for group, parts in zip(plan["groups"], _plan_parcalari(plan))]


def _alan_belirtecleri(doc_fields, names=None):
    names = names or tuple(doc_fields)
    return set(" ".join(doc_fields.get(name, "") or "" for name in names).split())


def eslesme_bilgisi(plan, doc_fields):
    """Sözcük kapsamını açıklar; tam sözcük eşleşmesi bile yeterli cevap demek değildir."""
    matched_flags = _eslesmeler(plan, _alan_belirtecleri(doc_fields))
    matched, missing = [], []
    for label, flag in zip(plan["labels"], matched_flags):
        (matched if flag else missing).append(label)
    return {"matched_terms": matched, "missing_terms": missing, "all_terms_matched": not missing,
            "answer_sufficiency": "not_assessed"}


def alan_agirligi(plan, doc_fields):
    """Eşleşen her sorgu sözcüğü için en ağır alanın puanını toplar (başlık 6 … Limits 0,5).

    Alanlar belirteç kümesi olarak saklandığından bm25 uzunluk normalizasyonu ağırlıkları bozar;
    bu toplam uzunluktan bağımsızdır. bm25 yalnız eşitlik bozucu olarak kalır.
    """
    field_tokens = {name: set((doc_fields.get(name) or "").split()) for name in INDEX_FIELDS}
    total = 0.0
    for group, parts in zip(plan["groups"], _plan_parcalari(plan)):
        best = 0.0
        for name, tokens in field_tokens.items():
            if _grup_eslesti(tokens, group, parts):
                best = max(best, FIELD_WEIGHTS[name])
        total += best
    return total


def aday_kapsami(plan, doc_fields):
    """Genel gövde sözcüklerinden doğan zayıf kısmi adayı sıralamaya sokmaz."""
    matched = _eslesmeler(plan, _alan_belirtecleri(doc_fields))
    count = sum(matched)
    if count < plan["minimum"] or any(not matched[index] for index in plan["required"]):
        return -1
    if count < len(matched) and not plan["required"]:
        headline = _alan_belirtecleri(doc_fields, HEADLINE_FIELDS)
        hints = [tuple(_options(part) for part in _parcalar(label)) for label in plan["labels"]]
        if not any(_grup_dokunuyor(headline, group, parts, hint)
                   for group, parts, hint in zip(plan["groups"], _plan_parcalari(plan), hints)):
            return -1
    return count
