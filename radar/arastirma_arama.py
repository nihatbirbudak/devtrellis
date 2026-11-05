"""Araştırma aramasını sınırlı dil karşılıkları ve Türkçe ek seçenekleriyle genişletir."""

import re

from radar.gelistirici_alani import arama_metni, metin


GRUP_SINIRI = 24
SECENEK_SINIRI = 8

# Genel dil çözümlemesi veya kök bulma değildir; yalnız bu açık karşılıklar aranır.
# Depodaki metnin biçimi korunur: alternatifler mevcut alt dizge aramasına gider.
_SOZLUK = (
    ("araştırma", "arastirma", "araştırmalar", "araştırması", "araştırmayı", "research", "researching"),
    ("tam metin", "tammetin", "full text", "full-text", "fulltext"),
    ("sıralama", "siralama", "sıralaması", "siralaması", "ranking"),
    ("arama", "araması", "aramayi", "search", "searching"),
    ("kaynak", "kaynaklar", "kaynakları", "source", "sources"),
    ("önbellek", "onbellek", "önbellekleme", "cache", "caching"),
    ("izin", "izinler", "izinleri", "permission", "permissions"),
)
_GRUPLAR = tuple(tuple(dict.fromkeys(arama_metni(value) for value in group)) for group in _SOZLUK)
_KARSILIKLAR = {tuple(value.split()): group for group in _GRUPLAR for value in group}
_EN_UZUN_KARSILIK = max(len(value) for value in _KARSILIKLAR)
_BAGLACLAR = frozenset(("ve", "ile", "için"))
_KESME_EKI = re.compile(r"([a-z0-9][a-z0-9.+_-]{1,63})['’](?:n?[iuü]n|[y]?[iuü]|[y]?[ae]|[dt][ae]n?|s)$")
_COGUL_EKLERI = ("larindan", "lerinden", "larinin", "lerinin", "larini", "lerini", "lardan", "lerden", "larla", "lerle", "larin", "lerin", "lari", "leri")


def _terim_secenekleri(word):
    """Özgün terimi korur; genel kök bulma yapmadan açık ek biçimlerini ekler."""
    options = [word]
    match = _KESME_EKI.fullmatch(word)
    if match:
        options.append(match[1])
    # Salt harfli uzun sözcükte bile yalnız birleşik çoğul eklerini açarız.
    # "compiler" gibi İngilizce adları "ler" keserek yanlış genişletmeyiz.
    if word.isalpha():
        suffixes = _COGUL_EKLERI + (("lar", "ler") if any(c in word for c in "çğöşü") else ())
        for suffix in suffixes:
            if word.endswith(suffix) and len(word) - len(suffix) >= 4:
                options.append(word[:-len(suffix)])
                break
    return tuple(dict.fromkeys(options))


def arama_gruplari(query):
    """Parametre olarak kullanılacak AND gruplarının OR alternatiflerini döndürür.

    Çağıran, her grup için ``(instr(r.search_text,?)>0 OR ...)`` oluşturmalı;
    grupları AND ile birleştirmeli ve değerleri SQL parametresi olarak vermelidir.
    Sorgu içeriği SQL'e veya sütun adına eklenmez. Bilinmeyen içerik terimleri
    atılmaz; özgün terim her zaman seçeneklerdedir. En az iki farklı içerik
    grubu varsa Türkçe bağlaçlar zorunlu eşleşme olmaktan çıkarılır. Tek başına
    bağlaç araması tüm kayıtları döndüren boş sorguya dönüşmez.
    Bu sınırlı sözlük anlamsal arama, sıralama veya çeviri garantisi sağlamaz.
    """
    words = arama_metni(metin(query, "Arama", 200)).split()
    groups = []
    position = 0
    while position < len(words):
        group, consumed = None, 1
        for size in range(min(_EN_UZUN_KARSILIK, len(words) - position), 0, -1):
            matched = _KARSILIKLAR.get(tuple(words[position:position + size]))
            if matched is not None:
                group, consumed = matched, size
                break
        group = group or _terim_secenekleri(words[position])
        if group not in groups:
            if len(groups) >= GRUP_SINIRI:
                raise ValueError(f"Arama en fazla {GRUP_SINIRI} farklı terim grubu içerebilir.")
            if len(group) > SECENEK_SINIRI:
                raise ValueError("Arama sözlüğündeki karşılık sayısı sınırı aşıyor.")
            groups.append(group)
        position += consumed
    content = [group for group in groups if not (len(group) == 1 and group[0] in _BAGLACLAR)]
    return tuple(content if len(content) >= 2 else groups)
