"""Topluluk sunucusunun adresi: varsayılan devtrellis.org; yerel pilot için `DEVTRELLIS_COMMUNITY_URL`.

Yerel uygulama, kaynak hafızası istemcisi ve `--standalone` köprüsü aynı değeri kullanır. Değer yalnız şema ve
sunucu adı taşır; düz `http` yalnız loopback için kabul edilir, böylece yanlış bir ayar özel veriyi şifresiz bir
dış adrese göndermez.
"""

import os
from urllib.parse import urlsplit

VARSAYILAN = "https://devtrellis.org"
ORTAM_DEGISKENI = "DEVTRELLIS_COMMUNITY_URL"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def topluluk_adresi(deger=None):
    """Ortam değişkenindeki (ya da verilen) adresi doğrular ve `şema://sunucu[:port]` biçiminde döndürür."""
    deger = (os.environ.get(ORTAM_DEGISKENI, "") if deger is None else deger).strip()
    if not deger:
        return VARSAYILAN
    parts = urlsplit(deger)
    if (parts.scheme not in ("https", "http") or not parts.hostname or parts.path not in ("", "/")
            or parts.query or parts.fragment or parts.username or parts.password):
        raise ValueError(f"{ORTAM_DEGISKENI} yalnız şema ve sunucu adı taşımalı "
                         "(ör. https://devtrellis.org ya da http://127.0.0.1:18941).")
    if parts.scheme == "http" and parts.hostname not in LOOPBACK:
        raise ValueError(f"{ORTAM_DEGISKENI}: düz http yalnız loopback adresi için kabul edilir.")
    return f"{parts.scheme}://{parts.netloc}"
