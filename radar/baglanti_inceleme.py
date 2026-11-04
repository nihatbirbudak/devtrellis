"""Araştırılan bağlantının erişilen metnini kaydeder; paylaşımı kanıt veya izlenmiş medya saymaz."""

import unicodedata
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup

from radar.arastirma_icerigi import SIR_KALIBI
from radar.gelistirici_alani import guvenli_url
from radar.kaynak_havuzu import alan_adi_gecerli
from radar.okuma import OkumaHatasi, makale_ayristir
from radar.toplayici import CollectionError, MAX_BODY_BYTES, kamusal_baglanti_oku


def baglam_metni(value, maximum):
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(f"İddia veya açıklama en fazla {maximum} karakter olmalı.")
    if (any(unicodedata.category(char) == "Cc" and char not in "\r\n\t" for char in value)
            or any(unicodedata.category(char) == "Cs" for char in value) or SIR_KALIBI.search(value)):
        raise ValueError("İddia ve açıklama geçerli, sır içermeyen düz metin olmalı.")
    return value.strip()


def baglanti_dogrula(value):
    if value == "":
        return ""
    result = guvenli_url(value, required=True)
    parts = urlsplit(result)
    if parts.scheme != "https" or parts.port not in (None, 443) or not alan_adi_gecerli(parts.hostname):
        raise ValueError("Araştırma bağlantısı standart HTTPS adresindeki kamusal bir alan adı olmalı.")
    if SIR_KALIBI.search(result):
        raise ValueError("Araştırma bağlantısı kimlik bilgisi içermemeli.")
    return urlunsplit(parts._replace(fragment=""))


def erisim_baslangici(source_url):
    return {"status": "pending" if source_url else "not_provided", "reason_code": "pending" if source_url else "none",
            "message": "Bağlantının açık sayfa metni henüz okunmadı." if source_url else "Bağlantı verilmedi.",
            "http_status": None, "final_url": "", "title": "", "text": "", "fetched_at": None,
            "media_processed": False, "truncated": False}


def baglantiyi_incele(source_url, *, fetcher=None, stop_event=None, now=None):
    """Tek sınırlı okuma dener; başarısızlığı açık kaydeder ve oturum engelini aşmaz."""
    source_url = baglanti_dogrula(source_url)
    result = erisim_baslangici(source_url)
    result.update(status="unavailable", reason_code="fetch_failed",
                  message="Bağlantı okunamadı; kullanıcı açıklamasıyla bağımsız kaynak araması sürecek.",
                  fetched_at=(now or datetime.now(timezone.utc)).isoformat())
    try:
        response = (fetcher or kamusal_baglanti_oku)(source_url, stop_event=stop_event)
        result["http_status"] = response.status
        final_url = baglanti_dogrula(response.url)
        if urlsplit(final_url).hostname != urlsplit(source_url).hostname:
            raise CollectionError("Yönlendirme alan adı izinli değil.")
        result["final_url"] = final_url
        if len(response.body) > MAX_BODY_BYTES:
            raise CollectionError("Kaynak yanıtı boyut sınırını aşıyor.")
        if response.status != 200:
            result.update(reason_code="access_restricted" if response.status in (401, 403) else "http_error",
                          message=f"Sayfa okunamadı (HTTP {response.status}); oturum veya erişim engeli aşılmadı.")
            return result
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type not in {"", "text/html", "application/xhtml+xml"}:
            result.update(reason_code="unsupported_content",
                          message="Bu içerik HTML metni değil. Video, ses ve PDF işlenmedi; açıklama veya transkript kullanılabilir.")
            return result
        page = BeautifulSoup(response.body, "html.parser")
        if page.select_one('input[type="password"]') or any(
                part in {'login', 'signin', 'sign-in', 'challenge'} for part in urlsplit(final_url).path.lower().split('/')):
            result.update(reason_code="access_restricted",
                          message="Giriş veya doğrulama sayfası içerik sayılmadı; oturum açma engeli aşılmadı.")
            return result
        article = makale_ayristir(response)
        result.update(status="read", reason_code="html_text", title=article["title"][:300], text=article["body"][:8000],
                      truncated=article["truncated"] or len(article["body"]) > 8000,
                      message="Yalnız açık HTML metni okundu. Paylaşım doğrulanmadı; video veya ses işlenmedi.")
    except CollectionError as error:
        result.update(reason_code="fetch_blocked", message=str(error)[:400] + " Bağımsız kaynak araması sürecek.")
    except OkumaHatasi:
        result.update(reason_code="text_unavailable",
                      message="Görünür, tekil sayfa metni çıkarılamadı; giriş ekranı veya boş özet içerik sayılmadı. Açıklama ya da transkript kullanılabilir.")
    except Exception:
        # Üçüncü taraf hata metnini, URL içeriğini veya yanıt gövdesini dışarı sızdırma.
        result.update(reason_code="fetch_failed")
    return result
