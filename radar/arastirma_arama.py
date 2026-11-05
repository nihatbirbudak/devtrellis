"""Araştırma aramasını küçük bir Türkçe ve İngilizce teknik sözlükle genişletir."""

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


def arama_gruplari(query):
    """Parametre olarak kullanılacak AND gruplarının OR alternatiflerini döndürür.

    Çağıran, her grup için ``(instr(r.search_text,?)>0 OR ...)`` oluşturmalı;
    grupları AND ile birleştirmeli ve değerleri SQL parametresi olarak vermelidir.
    Sorgu içeriği SQL'e veya sütun adına eklenmez. Bilinmeyen terimler atılmaz;
    noktalama ve tanımlayıcılar mevcut aramadaki gibi tam alt dizge olarak kalır.
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
        group = group or (words[position],)
        if group not in groups:
            if len(groups) >= GRUP_SINIRI:
                raise ValueError(f"Arama en fazla {GRUP_SINIRI} farklı terim grubu içerebilir.")
            if len(group) > SECENEK_SINIRI:
                raise ValueError("Arama sözlüğündeki karşılık sayısı sınırı aşıyor.")
            groups.append(group)
        position += consumed
    return tuple(groups)
