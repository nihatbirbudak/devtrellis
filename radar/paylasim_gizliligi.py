"""Topluluğa ayrılan kamu kopyasını anonimleştirir; belirgin özel veriyi ve hassas sorguları engeller."""

import copy
import html
import ipaddress
import re
import unicodedata
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit

from radar.gelistirici_alani import guvenli_url, paket_dogrula


URL_HATASI = "Paylaşım bağlantısı özel bilgi veya desteklenmeyen adres/parametre içeriyor; kamusal HTTPS kaynak adresini kullanın."
SORGU_HATASI = "Arama özel bilgi kalıbı içeriyor; kişisel ayrıntı, kimlik ve bağlantı parametrelerini çıkarın."
PRIVATE_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home", ".home.arpa", ".corp", ".intranet", ".localdomain")
TRACKING_KEYS = {"fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid", "igshid", "si", "ref", "referrer"}
URL_PATTERN = re.compile(r"(?:https?://|file://)[^\s<>\"']+", re.I)
IPV4_PATTERN = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
IPV6_PATTERN = re.compile(r"(?<![\w:])(?:[0-9a-f]{0,4}:){2,}[0-9a-f]{0,4}(?:%[\w.-]+)?(?![\w:])", re.I)
PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"\b[\w.!#$%&'*+/=?^`{|}~-]+@[\w.-]+\.[\w-]{2,}\b", re.I),
    re.compile(r"\b(?:sk-|nvapi-|gh[pousr]_|github_pat_)[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{6,}", re.I),
    re.compile(r"\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|token|password|passwd|secret|authorization|auth|session|key)[\"']?\s*[:=]\s*(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s\"'&,;]+)", re.I),
    re.compile(r"(?<!\w)--(?:api[_-]?key|access[_-]?token|token|password|passwd|secret)\s+(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s]+)", re.I),
    re.compile(r"\b[A-Za-z]:[\\/][^\s\"<>]+"),
    re.compile(r"\\\\[^\s\\]+\\[^\s\"<>]+"),
    re.compile(r"(?:~[\\/]|\$HOME/|%USERPROFILE%[\\/]|/(?:home|Users|root|etc|var|tmp|mnt|opt|private|run|Volumes)/)[^\s\"<>]+", re.I),
    re.compile(r"(?<![\w.-])(?:[\w-]+\.)*(?:localhost|[\w-]+\.(?:local|internal|lan|home|corp|intranet|localdomain|home\.arpa))(?![\w.-])", re.I),
    re.compile(r"(?<!\w)\+?\d{10,15}(?!\w)"),
)
PHONE_PATTERN = re.compile(r"(?<!\w)(?:\+\d{1,3}[\s().-]*)?(?:\(?\d{2,4}\)?[\s.-]+){2,4}\d{2,4}(?!\w)")


def _coz(value):
    value = html.unescape(value)
    for _ in range(3):
        decoded = unquote(value)
        if decoded == value:
            return value
        value = decoded
    if unquote(value) != value:
        raise ValueError(URL_HATASI)
    return value


def _ip(value):
    try:
        ipaddress.ip_address(value.split("%", 1)[0])
        return True
    except ValueError:
        return False


def _kalip_temizle(value):
    count = 0
    for pattern in PATTERNS:
        value, changed = pattern.subn("[redacted]", value)
        count += changed
    def replace_phone(match):
        nonlocal count
        if 10 <= sum(char.isdigit() for char in match.group()) <= 15:
            count += 1
            return "[redacted]"
        return match.group()
    value = PHONE_PATTERN.sub(replace_phone, value)
    for pattern in (IPV4_PATTERN, IPV6_PATTERN):
        def replace(match):
            nonlocal count
            if _ip(match.group()):
                count += 1
                return "[redacted]"
            return match.group()
        value = pattern.sub(replace, value)
    return value, count


def kamusal_url(value):
    """URL'yi ziyaret etmez; yalnız dar kamusal HTTPS biçimini kabul eder."""
    if value == "":
        return value
    try:
        checked = guvenli_url(value, required=True)
        decoded = _coz(checked)
        parts = urlsplit(checked)
        host = (parts.hostname or "").lower().rstrip(".")
        if parts.scheme != "https" or _ip(host) or host.endswith(PRIVATE_SUFFIXES):
            raise ValueError(URL_HATASI)
        if any(unicodedata.category(char).startswith("C") for char in decoded):
            raise ValueError(URL_HATASI)
        if _kalip_temizle(decoded)[1]:
            raise ValueError(URL_HATASI)
        pairs = parse_qsl(urlsplit(decoded).query, keep_blank_values=True, strict_parsing=True)
        kept = [(key, item) for key, item in pairs if not key.lower().startswith("utm_") and key.lower() not in TRACKING_KEYS]
        # Yalnız YouTube'un kamusal video kimliği için dar, tek parametreli istisna.
        query = ""
        if kept:
            if (host not in {"youtube.com", "www.youtube.com", "m.youtube.com"} or parts.path != "/watch"
                    or len(kept) != 1 or kept[0][0] != "v" or not re.fullmatch(r"[A-Za-z0-9_-]{11}", kept[0][1])):
                raise ValueError(URL_HATASI)
            query = "v=" + kept[0][1]
        # Sayfa içi konumlar paylaşım için gerekli değil; kimlik taşıyan fragmentler üstte reddedilir.
        return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))
    except (ValueError, TypeError, UnicodeError):
        raise ValueError(URL_HATASI) from None


def _metni_temizle(value):
    original = value
    try:
        value = _coz(value)
    except ValueError:
        return "[redacted]", 1
    value = "".join(char for char in value if unicodedata.category(char) != "Cf")
    count = int(value != original)

    def replace_url(match):
        nonlocal count
        address = match.group()
        # Metnin URL'den sonraki noktalamasını adresin parçası sayma.
        bare = address.rstrip(".,;!?)\u3002")
        suffix = address[len(bare):]
        try:
            clean = kamusal_url(bare)
        except ValueError:
            clean = "[redacted]"
        count += int(clean != bare)
        return clean + suffix

    value = URL_PATTERN.sub(replace_url, value)
    value, removed = _kalip_temizle(value)
    return value, count + removed


def sorguyu_dogrula(query):
    """Belirgin hassas kalıp bulunan sorguyu dışarı göndermeden reddeder."""
    if not isinstance(query, str) or _metni_temizle(query)[1]:
        raise ValueError(SORGU_HATASI)
    return query


def kamu_kopyasi(packet):
    """Şeması doğrulanmış, sabit anonim kamu kopyası döndürür; adları anlamsal olarak tanıdığı iddiası yoktur."""
    try:
        result = copy.deepcopy(paket_dogrula(packet))
        redactions = int(result["author"] != "Anonymous contributor")
        result["author"] = "Anonymous contributor"
        for key in ("title", "summary", "body"):
            result[key], count = _metni_temizle(result[key])
            redactions += count
        tags = []
        for tag in result["tags"]:
            cleaned, count = _metni_temizle(tag)
            tags.append(cleaned)
            redactions += count
        result["tags"] = tags
        for source in result["sources"]:
            source["title"], count = _metni_temizle(source["title"])
            redactions += count
            cleaned = kamusal_url(source["url"])
            redactions += int(cleaned != source["url"])
            source["url"] = cleaned
        cleaned = kamusal_url(result["url"])
        redactions += int(cleaned != result["url"])
        result["url"] = cleaned
        if result.get("provenance"):
            provenance = result["provenance"]
            redactions += int(provenance["question"] != result["title"]) + int("claim_text" in provenance)
            provenance["question"] = result["title"]
            provenance.pop("claim_text", None)
            provenance["model"], count = _metni_temizle(provenance["model"])
            redactions += count
            cleaned = kamusal_url(provenance["source_url"])
            redactions += int(cleaned != provenance["source_url"])
            provenance["source_url"] = cleaned
            if redactions:
                provenance.update(modified=True, evidence_status="unverified")
        return paket_dogrula(result), redactions
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise ValueError("Paylaşım güvenli kamu kopyasına dönüştürülemedi; metni ve kamusal HTTPS kaynak adreslerini gözden geçirin.") from None
