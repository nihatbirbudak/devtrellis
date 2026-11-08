"""Kayıtlı kamu belgelerinin anlamı korunan adres ve temsil kimliklerini ağ erişimi olmadan üretir."""

import hashlib
import json
import re
from urllib.parse import unquote, urlsplit, urlunsplit

from radar.gelistirici_alani import guvenli_url
from radar.kamu_sorgu import sorgu_izinli
from radar.kaynak_havuzu import alan_adi_gecerli
from radar.paylasim_gizliligi import sorguyu_dogrula


REPRESENTATION = "public-doc-anonymous-v2"
EXTRACTOR_VERSION = "dom-sections-v2"
VARIANT_FIELDS = frozenset({"accept", "accept-language", "accept-encoding", "user-agent"})


class KaynakKimligiHatasi(ValueError):
    """Adres veya kimlik girdisini geri yansıtmayan sabit hata kodudur."""


def canonical_hash(value):
    """Sırası sabit JSON nesnesini UTF-8 ile özetler; metin veya HTML saklamaz."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _remove_dot_segments(path):
    """RFC 3986 5.2.4 nokta parçası temizliği; kökün üstüne çıkan `..` belirsiz sayılır ve reddedilir."""
    segments = path.split("/")
    output = []
    for index, segment in enumerate(segments):
        last = index == len(segments) - 1
        if segment in (".", ".."):
            if segment == "..":
                if len(output) <= 1:
                    raise ValueError
                output.pop()
            if last:
                output.append("")
            continue
        output.append(segment)
    return "/".join(output)


def canonical_source_url(url):
    """HTTPS adresini tanımlar; bu doğrulama bir kaynağa ağ erişimi izni vermez.

    Host/şema ve varsayılan port normalleşir; yoldaki `.` ve `..` parçaları RFC 3986'ya göre
    çözülür (kodlanmış `%2e` nokta parçası belirsizdir, reddedilir). Yol, son eğik çizgi, query sırası
    ve kodlaması korunur. Fragment ayrı bölüm bağının işidir; kaynakta tutulmaz.
    Tam adresin kayıtlı anonim kaynak listesinde bulunması çağıranın koşuludur.
    """
    try:
        if not isinstance(url, str) or not url or len(url) > 2048:
            raise ValueError
        if re.search(r"%(?![A-Fa-f0-9]{2})", url):
            raise ValueError
        checked = guvenli_url(url, required=True)
        parts = urlsplit(checked)
        if (parts.scheme != "https" or parts.port not in (None, 443)
                or not alan_adi_gecerli(parts.hostname or "")):
            raise ValueError
        # Sorgu kuralı yayın kapısıyla ortak tek sabittir (radar.kamu_sorgu).
        if parts.query and not sorgu_izinli(parts.query):
            raise ValueError
        path = _remove_dot_segments(parts.path)
        decoded = path
        for _ in range(4):
            next_value = unquote(decoded, errors="strict")
            if next_value == decoded:
                break
            decoded = next_value
        else:
            raise ValueError
        if any(ord(char) < 32 or ord(char) == 127 for char in decoded) or "\\" in decoded:
            raise ValueError
        if re.search(r"(?:^|/)\.{1,2}(?:/|$)", decoded):
            raise ValueError
        # Kodlamanın varlığı hassas veri değildir; gizlilik kontrolü çözülmüş
        # yola uygulanır, kimlikte orijinal kodlama korunur.
        sorguyu_dogrula(decoded)
        result = urlunsplit(("https", parts.hostname, path, parts.query, ""))
        if "?" in url.split("#", 1)[0] and not parts.query:
            result += "?"
        return result
    except (ValueError, TypeError, UnicodeError):
        raise KaynakKimligiHatasi("invalid_public_source_url") from None


def validate_language(language):
    if not isinstance(language, str) or not re.fullmatch(r"[a-z]{2}(?:-[A-Z]{2})?", language):
        raise KaynakKimligiHatasi("invalid_source_language")
    return language


def address_key(url, language):
    """Adres bulma anahtarı çıkarıcı/temsil sürümünden bağımsızdır."""
    return canonical_hash({"canonical_url": canonical_source_url(url), "language": validate_language(language)})


def source_key(url, language, *, representation, request_variant, extractor_version):
    """Tam temsil kimliğini üretir; sabit başlık değerleri HTTP katmanında denetlenir."""
    if representation != REPRESENTATION:
        raise KaynakKimligiHatasi("unsupported_representation")
    if not isinstance(extractor_version, str) or not re.fullmatch(r"[a-z][a-z0-9.-]{0,79}", extractor_version):
        raise KaynakKimligiHatasi("invalid_extractor_version")
    if not isinstance(request_variant, dict) or set(request_variant) != VARIANT_FIELDS:
        raise KaynakKimligiHatasi("invalid_request_variant")
    if any(not isinstance(value, str) or not 1 <= len(value) <= 256
           or any(ord(char) < 32 or ord(char) > 126 for char in value)
           for value in request_variant.values()):
        raise KaynakKimligiHatasi("invalid_request_variant")
    language = validate_language(language)
    if request_variant["accept-language"] != language:
        raise KaynakKimligiHatasi("request_language_mismatch")
    return canonical_hash({"canonical_url": canonical_source_url(url), "language": language,
                           "representation": representation, "request_variant": dict(request_variant),
                           "extractor_version": extractor_version})
