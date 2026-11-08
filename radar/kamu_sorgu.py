"""Kamu kaynak adresinde izin verilen sorgu parametreleri: K0 kimliği, yayın kapısı ve K2 bakışı için tek kural.

Yönetici kararı (7 Kasım 2026 22:4x; sözleşme r2 §1 madde 6'nın güncellemesi): K0'ın beş sürüm/dil parametresi izinli,
gerisi ret. Değer kısa ve düz olmalı; tekrar eden anahtar ya da dörtten çok parametre ret. Adres temizlenip kabul edilmez.
"""

import re
from urllib.parse import parse_qsl

IZINLI_ANAHTARLAR = frozenset({"v", "version", "tag", "lang", "language"})
EN_COK_PARAMETRE = 4
_DEGER = re.compile(r"[A-Za-z0-9._-]{1,80}")


def sorgu_izinli(query):
    """Boş olmayan sorgu metni yalnız izinli anahtarlardan ve kısa düz değerlerden oluşuyorsa True."""
    try:
        ciftler = parse_qsl(query, keep_blank_values=True, strict_parsing=True, encoding="utf-8", errors="strict")
    except (ValueError, UnicodeError):
        return False
    return (0 < len(ciftler) <= EN_COK_PARAMETRE and len({anahtar for anahtar, _ in ciftler}) == len(ciftler)
            and all(anahtar in IZINLI_ANAHTARLAR and _DEGER.fullmatch(deger) for anahtar, deger in ciftler))
