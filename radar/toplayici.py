"""İzinli kamusal kaynakları sınırlı olarak okuyup kanıt ve tarihleri koruyan haber kayıtları üretir."""

from __future__ import annotations

import email.utils
import hashlib
import http.client
import ipaddress
import json
import queue
import re
import socket
import ssl
import threading
import time
import urllib.parse
import urllib.robotparser
from dataclasses import dataclass
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any
from xml.parsers import expat

import certifi
import feedparser
from bs4 import BeautifulSoup

from radar.kaynak_havuzu import izinli_alan_adlari, katalogu_dogrula


USER_AGENT = "UfukRadar/0.1"
MAX_BODY_BYTES = 2_000_000
MAX_ITEMS = 50
MAX_PAGES = 3
MAX_REDIRECTS = 3
SOURCE_TIMEOUT = 35.0
REQUEST_TIMEOUT = 10.0
APPROVED_FETCH_HOSTS = izinli_alan_adlari()
_REQUEST_FETCH_HOSTS = ContextVar("ufuk_request_fetch_hosts", default=frozenset())


def kaynaklari_yetkilendir(sources):
    """Doğrulanmış kaynakların tam alan adlarını okuma listesine ekler."""
    global APPROVED_FETCH_HOSTS
    if not sources:
        return
    validated = katalogu_dogrula(sources)
    hosts = {host for source in validated for field in ("fetch_hosts", "item_hosts") for host in source[field]}
    APPROVED_FETCH_HOSTS = APPROVED_FETCH_HOSTS | frozenset(hosts)


_DNS_SLOTS = threading.BoundedSemaphore(2)
_ROBOTS_CACHE: dict[tuple[str, tuple[str, ...]], tuple[float, Any]] = {}
_ROBOTS_LOCK = threading.Lock()
# Paketle gelen kamusal kökler Windows'ta o ana kadar indirilmiş CA listesine
# bağımlılığı kaldırır; sertifika zinciri ve alan adı doğrulaması açık kalır.
_TLS_CONTEXT = ssl.create_default_context(cafile=certifi.where())


class CollectionError(ValueError):
    """Kaynak adresi veya yanıtı güvenle işlenemediğinde kısa bir açıklama taşır."""


FetchError = CollectionError


@dataclass(frozen=True)
class FetchResult:
    status: int
    body: bytes
    headers: dict[str, str]
    url: str


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise CollectionError("Kaynak için ayrılan okuma süresi doldu.")
    return remaining


def _validate_url(url: str, allowed_hosts: set[str]) -> urllib.parse.SplitResult:
    if not isinstance(url, str) or len(url) > 4096:
        raise CollectionError("Kaynak adresi geçersiz veya çok uzun.")
    if any(ord(char) <= 32 or ord(char) == 127 for char in url) or "\\" in url:
        raise CollectionError("Kaynak adresinde izin verilmeyen karakter var.")
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
        host = (parsed.hostname or "").lower()
    except ValueError as error:
        raise CollectionError("Kaynak adresi ayrıştırılamadı.") from error
    if parsed.scheme != "https" or port not in (None, 443):
        raise CollectionError("Yalnız standart bağlantı noktasındaki HTTPS kaynakları okunabilir.")
    if parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise CollectionError("Kimlik bilgisi veya bölüm işareti içeren kaynak adresi okunamaz.")
    if (host not in APPROVED_FETCH_HOSTS and host not in _REQUEST_FETCH_HOSTS.get()) or host not in allowed_hosts:
        raise CollectionError("Kaynak veya yönlendirme alan adı okuma listesinde yok.")
    return parsed


def _is_public_address(address: str) -> bool:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(parsed, ipaddress.IPv6Address):
        # IPv4 taşıyan geçiş adresleri de yerel ağlara geçiş yolu olmamalıdır.
        if parsed.ipv4_mapped is not None or parsed.sixtofour is not None or parsed.teredo is not None:
            return False
    return bool(parsed.is_global and not parsed.is_multicast and not parsed.is_reserved)


def _resolve_public_host(host: str, deadline: float) -> tuple[int, tuple]:
    """DNS sonucunu sınırlı sürede alır ve yalnız genel adresleri bağlantıya verir."""
    if not _DNS_SLOTS.acquire(timeout=min(5.0, _remaining(deadline))):
        raise CollectionError("DNS çözümleyicisi meşgul; kaynak daha sonra denenmeli.")
    result_queue: queue.Queue = queue.Queue(maxsize=1)

    def resolve() -> None:
        try:
            result_queue.put(socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM))
        except Exception:
            result_queue.put(None)
        finally:
            _DNS_SLOTS.release()

    threading.Thread(target=resolve, daemon=True).start()
    try:
        addresses = result_queue.get(timeout=min(5.0, _remaining(deadline)))
    except queue.Empty as error:
        raise CollectionError("DNS çözümleme süresi doldu.") from error
    if not addresses or any(not _is_public_address(entry[4][0]) for entry in addresses):
        raise CollectionError("Kaynak genel internete ait güvenli bir adrese çözülmedi.")
    # IPv6 bulunup kullanılabilir bağlantı olmaması masaüstünde gereksiz bekleme doğurmasın.
    addresses.sort(key=lambda entry: entry[0] != socket.AF_INET)
    family, _, _, _, address = addresses[0]
    if family not in (socket.AF_INET, socket.AF_INET6):
        raise CollectionError("Kaynağın ağ adresi türü desteklenmiyor.")
    return family, address


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """TLS kimliğini alan adıyla doğrularken yalnız önceden doğrulanan IP'ye bağlanır."""

    def __init__(self, host: str, family: int, address: tuple, timeout: float,
                 deadline: float | None = None):
        super().__init__(host, port=443, timeout=timeout, context=_TLS_CONTEXT)
        self._address_family = family
        self._verified_address = address
        self._deadline = deadline

    def connect(self) -> None:
        raw_socket = socket.socket(self._address_family, socket.SOCK_STREAM)
        try:
            raw_socket.settimeout(self.timeout)
            raw_socket.connect(self._verified_address)
            if self._deadline is not None:
                raw_socket.settimeout(min(REQUEST_TIMEOUT, _remaining(self._deadline)))
            self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)
        except BaseException:
            raw_socket.close()
            raise


def _request_once(url: str, allowed_hosts: set[str], headers: dict[str, str],
                  deadline: float, max_bytes: int = MAX_BODY_BYTES, stop_event=None,
                  strict_validators=False) -> FetchResult:
    parsed = _validate_url(url, allowed_hosts)
    family, address = _resolve_public_host(parsed.hostname, deadline)
    connection = _PinnedHTTPSConnection(parsed.hostname, family, address,
                                        min(REQUEST_TIMEOUT, _remaining(deadline)), deadline=deadline)
    request_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/atom+xml, application/rss+xml, application/json, text/html;q=0.8, */*;q=0.1",
        "Accept-Encoding": "identity",
        "Connection": "close",
        **headers,
    }
    target = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    timeout_timer = None
    try:
        connection.connect()
        response_socket = connection.sock

        def stop_reading() -> None:
            # Soket zaman aşımı yalnız hareketsizliği sınırlar. Parça parça gelen
            # başlıkların da toplam süreyi aşmasını bu kesin kapanış önler.
            try:
                response_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass  # Yanıt aynı anda tamamlanıp soketi kapatmış olabilir.

        timeout_timer = threading.Timer(_remaining(deadline), stop_reading)
        timeout_timer.daemon = True
        timeout_timer.start()
        response_socket.settimeout(min(REQUEST_TIMEOUT, _remaining(deadline)))
        connection.request("GET", target, headers=request_headers)
        response_socket.settimeout(min(REQUEST_TIMEOUT, _remaining(deadline)))
        response = connection.getresponse()
        response_headers = {}
        for key, value in response.getheaders():
            name = key.lower()
            if name in {"cache-control", "vary"} and name in response_headers:
                # Bu alanlar HTTP listeleridir; önceki gizlilik/temsil kuralı kaybolmamalıdır.
                response_headers[name] += ", " + value
            elif (strict_validators and name in {"etag", "last-modified"} and name in response_headers
                    and response_headers[name] != value):
                raise CollectionError("Kaynak yanıtında çelişkili sürüm doğrulayıcıları var.")
            else:
                response_headers[name] = value
        if response_headers.get("content-encoding", "identity").lower() != "identity":
            raise CollectionError("Sıkıştırılmış beklenmeyen yanıt güvenlik sınırı nedeniyle okunmadı.")
        content_length = response_headers.get("content-length")
        expected_size = (int(content_length) if content_length and content_length.isdigit()
                         and response.status not in (204, 304) else None)
        if expected_size is not None and expected_size > max_bytes:
            raise CollectionError("Kaynak yanıtı boyut sınırını aşıyor.")
        pieces: list[bytes] = []
        size = 0
        while True:
            if stop_event is not None and stop_event.is_set():
                raise CollectionError("Kaynak okuması iptal edildi.")
            remaining = _remaining(deadline)
            if response_socket.fileno() != -1:
                response_socket.settimeout(min(REQUEST_TIMEOUT, remaining))
            piece = response.read1(min(65536, max_bytes + 1 - size))
            if not piece:
                break
            size += len(piece)
            if size > max_bytes:
                raise CollectionError("Kaynak yanıtı boyut sınırını aşıyor.")
            pieces.append(piece)
        _remaining(deadline)
        if expected_size is not None and size != expected_size:
            raise CollectionError("Kaynak yanıtı tamamlanmadan bağlantı kapandı; eksik içerik kaydedilmedi.")
        return FetchResult(response.status, b"".join(pieces), response_headers, url)
    except CollectionError:
        raise
    except ssl.SSLCertVerificationError as error:
        raise CollectionError("Kaynağın TLS sertifika zinciri doğrulanamadı; güvenli bağlantı kurulmadı.") from error
    except (OSError, http.client.HTTPException, ValueError) as error:
        raise CollectionError("Kaynak bağlantısı kurulamadı veya güvenli biçimde tamamlanamadı.") from error
    finally:
        if timeout_timer is not None:
            timeout_timer.cancel()
        connection.close()


def _robots_allowed(url: str, allowed_hosts: set[str], deadline: float) -> bool:
    parsed = _validate_url(url, allowed_hosts)
    origin = f"https://{parsed.hostname}"
    key = (origin, tuple(sorted(allowed_hosts)))
    with _ROBOTS_LOCK:
        cached = _ROBOTS_CACHE.get(key)
    if cached and cached[0] > time.monotonic():
        rules = cached[1]
    else:
        result = _fetch(origin + "/robots.txt", allowed_hosts, {}, deadline,
                        check_robots=False, max_bytes=250_000)
        if result.status in (404, 410):
            rules = True
        elif result.status in (401, 403):
            rules = False
        elif result.status == 200:
            rules = urllib.robotparser.RobotFileParser()
            rules.parse(result.body.decode("utf-8", errors="replace").splitlines())
        else:
            raise CollectionError(f"Robots erişim kuralı doğrulanamadı (HTTP {result.status}).")
        with _ROBOTS_LOCK:
            _ROBOTS_CACHE[key] = (time.monotonic() + 3600, rules)
    if isinstance(rules, bool):
        return rules
    crawl_delay = rules.crawl_delay(USER_AGENT) or rules.crawl_delay("*")
    request_rate = rules.request_rate(USER_AGENT) or rules.request_rate("*")
    delay = float(crawl_delay or 0)
    if request_rate and request_rate.requests:
        delay = max(delay, request_rate.seconds / request_rate.requests)
    if delay:
        if delay > 5 or delay >= _remaining(deadline):
            raise CollectionError("Kaynağın robots bekleme şartı pilot sınırını aşıyor; toplama ertelendi.")
        time.sleep(delay)
    return rules.can_fetch(USER_AGENT, url)


def _fetch(url: str, allowed_hosts: set[str], headers: dict[str, str], deadline: float,
           check_robots: bool = True, max_bytes: int = MAX_BODY_BYTES, stop_event=None) -> FetchResult:
    for redirect_number in range(MAX_REDIRECTS + 1):
        if stop_event is not None and stop_event.is_set():
            raise CollectionError("Kaynak okuması iptal edildi.")
        _validate_url(url, allowed_hosts)
        if check_robots and not _robots_allowed(url, allowed_hosts, deadline):
            raise CollectionError("Kaynağın robots erişim kuralı bu okumaya izin vermiyor.")
        if stop_event is not None and stop_event.is_set():
            raise CollectionError("Kaynak okuması iptal edildi.")
        options = {"stop_event": stop_event} if stop_event is not None else {}
        result = _request_once(url, allowed_hosts, headers, deadline, max_bytes, **options)
        if result.status not in (301, 302, 303, 307, 308):
            return result
        if redirect_number == MAX_REDIRECTS:
            raise CollectionError("Kaynak yönlendirme sınırını aştı.")
        location = result.headers.get("location")
        if not location:
            raise CollectionError("Kaynak boş yönlendirme döndürdü.")
        url = urllib.parse.urljoin(url, location)
        # Yönlendirmede koşullu önbellek başlıkları başka kaynağa taşınmaz.
        headers = {}
    raise CollectionError("Kaynak yönlendirmesi tamamlanamadı.")


def _plain(value: Any, limit: int = 700) -> str:
    if not isinstance(value, str):
        return ""
    soup = BeautifulSoup(value[:100_000], "html.parser")
    for element in soup(["script", "style", "noscript", "iframe"]):
        element.decompose()
    return " ".join(soup.get_text(" ", strip=True).split())[:limit]


def _date(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
        except ValueError:
            return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = email.utils.parsedate_to_datetime(value)
        except (ValueError, TypeError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    try:
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, OverflowError):
        return None


def _item_url(value: Any, base: str, allowed_hosts: set[str] | None = None) -> str | None:
    """Yalnız görüntülenecek bağlantıyı temizler; bağlantıyı ayrıca indirmez."""
    if not isinstance(value, str) or not value or len(value) > 4096:
        return None
    if any(ord(char) <= 32 or ord(char) == 127 for char in value) or "\\" in value:
        return None
    try:
        parsed = urllib.parse.urlsplit(urllib.parse.urljoin(base, value))
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return None
        if parsed.username is not None or parsed.password is not None or parsed.port not in (None, 80, 443):
            return None
        if allowed_hosts is not None and parsed.hostname.lower() not in allowed_hosts:
            return None
        # ArXiv metadata'sındaki eski HTTP bağlantısı da aynı resmî HTTPS sayfasına gider.
        if parsed.hostname in ("arxiv.org", "export.arxiv.org"):
            parsed = parsed._replace(scheme="https")
        query = [(key, item) for key, item in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
                 if not key.lower().startswith("utm_") and key.lower() not in ("fbclid", "gclid")]
        return urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query), fragment=""))
    except ValueError:
        return None


def _make_item(source: dict, external_id: str, url: str, title: Any, excerpt: Any,
               published: Any = None, updated: Any = None, evidence: str | None = None) -> dict:
    return {
        "external_id": str(external_id)[:500], "url": url,
        "title": _plain(title, 240), "excerpt": _plain(excerpt),
        "published_at": _date(published), "updated_at": _date(updated), "event_at": None,
        "evidence_type": evidence or source.get("evidence_type", "birincil_duyuru"),
        "language": source.get("language", "en"),
    }


def _guvenli_besleme(body: bytes):
    """Gerçek XML beyanlarını reddeder; CDATA içindeki HTML belge metnini beyan saymaz."""
    def reject_declaration(*args):
        raise CollectionError("Varlık veya belge türü tanımı içeren besleme işlenmedi.")
    parser = expat.ParserCreate()
    parser.StartDoctypeDeclHandler = reject_declaration
    parser.EntityDeclHandler = reject_declaration
    parser.ExternalEntityRefHandler = reject_declaration
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    try:
        parser.Parse(body, True)
    except expat.ExpatError as error:
        raise CollectionError("Kaynağın RSS/Atom XML biçimi doğrulanamadı.") from error
    return feedparser.parse(body)


def _parse_feed(body: bytes, source: dict, base: str) -> list[dict]:
    feed = _guvenli_besleme(body)
    if not feed.get("version"):
        raise CollectionError("Kaynak geçerli bir RSS/Atom beslemesi döndürmedi.")
    items: list[dict] = []
    seen: set[str] = set()
    for entry in feed.entries[:100]:
        url = _item_url(entry.get("link", ""), base, set(source.get("item_hosts", [])) or None)
        if not url or not entry.get("title") or url in seen:
            continue
        identity = str(entry.get("id") or url)
        if source["id"] == "arxiv":
            # Makale sürümü değiştiğinde aynı kayıt yeni revizyonla güncellenir.
            identity = re.sub(r"v\d+$", "", identity)
        # FeedParserDict.get('updated'), alan yokken 'published' değerini takma adla
        # verebilir. Yalnız gerçekten bulunan tarih alanları birbirinden ayrı tutulur.
        items.append(_make_item(source, identity, url, entry.title,
                                entry.get("summary", ""), dict.get(entry, "published"), dict.get(entry, "updated")))
        seen.add(url)
        if len(items) == MAX_ITEMS:
            break
    return items


def _parse_releases(body: bytes, source: dict, base: str) -> list[dict]:
    try:
        releases = json.loads(body)
    except (ValueError, UnicodeDecodeError) as error:
        raise CollectionError("Sürüm kaynağı geçerli JSON döndürmedi.") from error
    if not isinstance(releases, list):
        raise CollectionError("Sürüm kaynağı beklenen kayıt listesini döndürmedi.")
    items: list[dict] = []
    for release in releases[:100]:
        if not isinstance(release, dict) or release.get("draft"):
            continue
        url = _item_url(release.get("html_url"), base, {"github.com"})
        tag = release.get("name") or release.get("tag_name")
        if not url or not tag or not release.get("id"):
            continue
        evidence = "on_surum" if release.get("prerelease") else "resmi_surum"
        items.append(_make_item(source, f"{source['id']}:{release['id']}", url,
                                f"{source['name']} · {tag}", release.get("body", ""),
                                release.get("published_at"), release.get("updated_at"), evidence))
        if len(items) == MAX_ITEMS:
            break
    return items


def _parse_html(body: bytes, source: dict, base: str) -> list[dict]:
    soup = BeautifulSoup(body, "html.parser")
    for element in soup(["script", "style", "noscript", "iframe", "nav", "footer"]):
        element.decompose()
    if source["kind"] == "project":
        heading = soup.find("h1") or soup.find("title")
        description = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
        published = soup.find("meta", attrs={"property": "article:published_time"})
        updated = soup.find("meta", attrs={"property": "article:modified_time"})
        if heading is None or not heading.get_text(strip=True):
            raise CollectionError("Proje sayfasında doğrulanabilir başlık bulunamadı.")
        return [_make_item(source, source["url"], source["url"], heading.get_text(" ", strip=True),
                           description.get("content", "") if description else "",
                           published.get("content") if published else None,
                           updated.get("content") if updated else None)]
    root = soup.find("main") or soup
    allowed_hosts = set(source.get("item_hosts", []))
    patterns = source.get("include_patterns", [])
    exclude_patterns = source.get("exclude_patterns", [])
    items: list[dict] = []
    seen: set[str] = set()
    candidates = []
    container_targets: dict[int, set[str]] = {}
    context_tags = ["article", "li", "div"] if source.get("title_from_context") else ["article", "li"]
    for link in root.select(source.get("selector", "a[href]"))[:2000]:
        url = _item_url(link.get("href"), base, allowed_hosts)
        if not url or url.rstrip("/") == source["url"].rstrip("/"):
            continue
        path = urllib.parse.urlsplit(url).path
        if patterns and not any(re.search(pattern, path, re.IGNORECASE) for pattern in patterns):
            continue
        if any(re.search(pattern, path, re.IGNORECASE) for pattern in exclude_patterns):
            continue
        candidates.append((link, url))
        for container in link.find_parents(context_tags):
            container_targets.setdefault(id(container), set()).add(url)
    for link, url in candidates:
        if url in seen:
            continue
        heading = link.find(["h1", "h2", "h3", "h4"])
        context = link
        if source.get("title_from_context"):
            # Bazı resmî kartlarda bağlantı yalnız "Read story" der. Başlık ancak
            # tek haber URL'si taşıyan en yakın kartın açık başlık öğesinden alınır.
            for container in link.find_parents(context_tags):
                if container_targets.get(id(container)) != {url}:
                    break
                card_heading = container.find(["h1", "h2", "h3", "h4"])
                if card_heading is not None:
                    heading = card_heading
                    context = container
                    break
        title = _plain(heading.get_text(" ", strip=True) if heading else link.get_text(" ", strip=True), 240)
        if len(title) < 12 or title.lower() in {"read more", "read story", "learn more", "devamını oku", "haberin devamı"}:
            continue
        container = link.find_parent(["article", "li"])
        # Bazı sayfalarda article bütün haber listesini sarar. Ortak giriş metni ve
        # liste tarihi tekil habere taşınmaz; kapsayıcı yalnız bu haberle ilişkili olmalı.
        if context is link and container is not None and container_targets.get(id(container)) == {url}:
            context = container
        date_node = context.find("time", attrs={"datetime": True})
        paragraph = context.find("p")
        excerpt = paragraph.get_text(" ", strip=True) if paragraph else ""
        date_only_excerpt = (re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[T ][\d:.+-]+Z?)?", excerpt)
                             and _date(excerpt) is not None)
        if (excerpt.lower() in {"read story", "read more", "learn more"} or excerpt == title
                or re.fullmatch(r"\d+\s*/\s*\d+", excerpt) or date_only_excerpt):
            excerpt = ""
        items.append(_make_item(source, url, url, title, excerpt,
                                date_node.get("datetime") if date_node else None))
        seen.add(url)
        if len(items) == MAX_ITEMS:
            break
    if not items:
        raise CollectionError("Sayfada doğrulanabilir haber bağlantısı bulunamadı; seçici gözden geçirilmeli.")
    return items


def guvenli_sayfa_oku(url: str, source: dict) -> FetchResult:
    """Makale adresini kaynak ve uygulama izin listelerinin kesişiminde aynı ağ sınırlarıyla okur."""
    allowed_hosts = set(source.get("item_hosts", [])) & APPROVED_FETCH_HOSTS
    _validate_url(url, allowed_hosts)
    result = _fetch(url, allowed_hosts, {}, time.monotonic() + SOURCE_TIMEOUT)
    if result.status in (401, 403):
        raise CollectionError(f"Makale erişimi kısıtladı (HTTP {result.status}); engel aşılmadı.")
    if result.status == 429:
        raise CollectionError("Makale kaynağının istek sınırına ulaşıldı (HTTP 429).")
    if result.status != 200:
        raise CollectionError(f"Makale okunamadı (HTTP {result.status}).")
    return result


def kosullu_parametreleri_dogrula(url):
    """Kaynak hafızasında sürüm ve dil parametrelerini korur; özel sorgu parametrelerini reddeder."""
    try:
        pairs = urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query, keep_blank_values=True, strict_parsing=True)
    except (TypeError, ValueError):
        raise CollectionError("Kaynak parametreleri geçersiz.") from None
    if len(pairs) > 4 or len({key for key, _ in pairs}) != len(pairs) or any(
            key not in {"v", "version", "tag", "lang", "language"}
            or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", value) for key, value in pairs):
        raise CollectionError("Kaynak yalnız gözden geçirilmiş sürüm ve dil parametreleri içerebilir.")


def kosullu_sayfa_oku(url: str, source: dict, *, etag=None, modified=None, language="en") -> FetchResult:
    """Seçilmiş tek temsili koşullu okur; yönlendirmeler ve kimlik başlıkları kabul edilmez."""
    allowed_hosts = set(source.get("item_hosts", [])) & APPROVED_FETCH_HOSTS
    _validate_url(url, allowed_hosts)
    kosullu_parametreleri_dogrula(url)
    if not isinstance(language, str) or not re.fullmatch(r"[a-z]{2}(?:-[A-Z]{2})?", language):
        raise CollectionError("Kaynak dili geçersiz.")
    headers = {"Accept-Language": language}
    for name, value in (("If-None-Match", etag), ("If-Modified-Since", modified)):
        if value is not None:
            if (not isinstance(value, str) or not 1 <= len(value) <= 512
                    or any(ord(char) < 32 or ord(char) == 127 or ord(char) > 255 for char in value)):
                raise CollectionError("Kaynak doğrulayıcı başlığı geçersiz.")
            headers[name] = value
    deadline = time.monotonic() + SOURCE_TIMEOUT
    if not _robots_allowed(url, allowed_hosts, deadline):
        raise CollectionError("Kaynağın robots erişim kuralı bu okumaya izin vermiyor.")
    # İlk pilotta temsil ve koşullu başlıkların başka sayfaya taşınması önlenir.
    result = _request_once(url, allowed_hosts, headers, deadline, strict_validators=True)
    if result.status not in (200, 304):
        raise CollectionError("Kaynak koşullu kontrolü başarılı olmadı; erişim engeli veya yönlendirme aşılmadı.")
    if result.status == 304 and not (etag or modified):
        raise CollectionError("Önceki kaynak doğrulayıcısı olmadan 304 kabul edilemez.")
    return result


def kamusal_veri_oku(url, allowed_hosts, *, stop_event=None, deadline=None):
    """Kayıtlı tam alan adlarını aynı TLS, robots, DNS, boyut ve süre sınırlarıyla okur."""
    deadline = min(deadline, time.monotonic() + SOURCE_TIMEOUT) if deadline is not None else time.monotonic() + SOURCE_TIMEOUT
    return _fetch(url, set(allowed_hosts), {}, deadline, stop_event=stop_event)


def kamusal_baglanti_oku(url, *, stop_event=None):
    """Açıkça verilen tek alan adını bu çağrı boyunca okur; genel izin listesini değiştirmez."""
    from radar.kaynak_havuzu import alan_adi_gecerli
    try:
        host = urllib.parse.urlsplit(url).hostname or ""
    except (TypeError, ValueError) as error:
        raise CollectionError("Araştırma bağlantısı geçersiz.") from error
    if not alan_adi_gecerli(host):
        raise CollectionError("Araştırma bağlantısı kamusal bir alan adı olmalı.")
    token = _REQUEST_FETCH_HOSTS.set(frozenset({host}))
    try:
        # Yönlendirmeler aynı tam alan adında kalır. Her istek mevcut DNS/IP,
        # sertifika, robots, süre ve gövde boyutu kontrollerinden geçer.
        return _fetch(url, {host}, {}, time.monotonic() + min(20.0, SOURCE_TIMEOUT),
                      stop_event=stop_event)
    finally:
        _REQUEST_FETCH_HOSTS.reset(token)


def _sayfa_adresi(url, source):
    """Sayfalama bağlantısını aynı HTTPS kökeni ve kaynak yolu içinde tutar."""
    base = urllib.parse.urlsplit(source["fetch_url"])
    joined = urllib.parse.urljoin(source["fetch_url"], url)
    parsed = _validate_url(joined, set(source["fetch_hosts"]))
    base_path = base.path.rstrip("/") or "/"
    path = parsed.path.rstrip("/") or "/"
    repository_id = source.get("repository_id")
    github_canonical = (base.hostname == parsed.hostname == "api.github.com" and source.get("kind") == "github_releases"
                        and type(repository_id) is int and repository_id > 0
                        and path == f"/repositories/{repository_id}/releases")
    if (parsed.netloc != base.netloc or
            not (github_canonical or path == base_path or re.fullmatch(re.escape(base_path.rstrip("/")) + r"/page/[1-9][0-9]{0,3}", path))):
        raise CollectionError("Sayfalama bağlantısı kaynağın izinli yolu dışında.")
    return joined


def _sonraki_sayfa(response, source, count):
    if not count or source.get("kind") == "project":
        return None
    for entry in response.headers.get("link", "").split(","):
        match = re.search(r'<([^>]+)>\s*;\s*rel\s*=\s*[\"\']?next\b', entry, re.I)
        if match:
            return _sayfa_adresi(match.group(1), source)
    if source.get("kind") in {"rss", "atom"}:
        feed = _guvenli_besleme(response.body)
        for link in feed.feed.get("links", []):
            if link.get("rel") == "next" and link.get("href"):
                return _sayfa_adresi(link["href"], source)
    elif source.get("kind") == "html":
        link = BeautifulSoup(response.body, "html.parser").select_one('a[rel~="next"][href], link[rel~="next"][href]')
        if link:
            return _sayfa_adresi(link["href"], source)
    if source.get("pagination") == "wordpress":
        parsed = urllib.parse.urlsplit(response.url)
        query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
        current = query.get("paged", "1")
        if not current.isdigit() or not 1 <= int(current) < 1000:
            raise CollectionError("Besleme sayfa numarası güvenli sınırı aşıyor.")
        query["paged"] = str(int(current) + 1)
        return _sayfa_adresi(urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query))), source)
    return None


def _depo_bakimi(source, deadline, stop_event):
    """Sürüm listesindeki projenin resmî durumunu aynı kaynak bütçesi içinde okur."""
    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    parsed = urllib.parse.urlsplit(source["fetch_url"])
    if parsed.hostname != "api.github.com" or not re.fullmatch(r"/repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/releases", parsed.path):
        return {"status": "error", "error": "Projenin resmî bakım adresi doğrulanamadı.", "checked_at": checked_at}
    url = urllib.parse.urlunsplit(parsed._replace(path=parsed.path[:-len("/releases")], query=""))
    try:
        options = {"stop_event": stop_event} if stop_event is not None else {}
        response = _fetch(url, set(source["fetch_hosts"]), {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}, deadline, **options)
        if response.status != 200:
            raise CollectionError(f"Proje bakım bilgisi okunamadı (HTTP {response.status}).")
        value = json.loads(response.body)
        if not isinstance(value, dict) or type(value.get("id")) is not int or value["id"] <= 0 or any(type(value.get(field)) is not bool for field in ("archived", "disabled")):
            raise CollectionError("Proje bakım yanıtı beklenen alanları taşımıyor.")
        return {"status": "ok", "error": None, "checked_at": checked_at, "repository_id": value["id"],
                "archived": value["archived"], "disabled": value["disabled"], "pushed_at": _date(value.get("pushed_at"))}
    except Exception as exc:
        return {"status": "error", "error": str(exc)[:400] if isinstance(exc, CollectionError) else "Proje bakım bilgisi işlenemedi.", "checked_at": checked_at}


def collect_source(source: dict, etag: str | None = None, modified: str | None = None,
                   *, continuation=None, stop_event=None) -> dict:
    """Güncel ilk sayfayı ve sınırlı devam sayfalarını okur; sonraki tur için ilerleme döndürür."""
    allowed_hosts = set(source.get("fetch_hosts", []))
    headers: dict[str, str] = {}
    for name, value in (("If-None-Match", etag), ("If-Modified-Since", modified)):
        if value and isinstance(value, str) and len(value) <= 1024 and not re.search(r"[\x00-\x1f\x7f]", value):
            headers[name] = value
    if source.get("kind") == "github_releases":
        headers["Accept"] = "application/vnd.github+json"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    deadline = time.monotonic() + SOURCE_TIMEOUT
    maintenance = None
    if source.get("kind") == "github_releases":
        maintenance = _depo_bakimi(source, deadline, stop_event)
        repository_id = maintenance.get("repository_id") or (source.get("maintenance") or {}).get("repository_id")
        source = {**source, "repository_id": repository_id}
    continuation = _sayfa_adresi(continuation, source) if continuation else None
    url, items, seen, bodies = source.get("fetch_url", ""), [], set(), set()
    cache, pages, error, head_unchanged, truncated = {"etag": etag, "modified": modified}, 0, None, False, False
    while url and pages < MAX_PAGES:
        try:
            options = {"stop_event": stop_event} if stop_event is not None else {}
            response = _fetch(url, allowed_hosts, headers if pages == 0 else {}, deadline, **options)
            pages += 1
            if response.status == 304 and pages == 1:
                if not any(name in headers for name in ("If-None-Match", "If-Modified-Since")):
                    raise CollectionError("Kaynak önceki sürüm bilgisi olmadan değişiklik yok yanıtı döndürdü.")
                head_unchanged = True
                cache = {"etag": response.headers.get("etag", etag), "modified": response.headers.get("last-modified", modified)}
                url, continuation = continuation, None
                continue
            if response.status in (401, 403):
                raise CollectionError(f"Kaynak erişimi kısıtladı (HTTP {response.status}); engel aşılmadı.")
            if response.status == 429:
                raise CollectionError("Kaynak istek sınırına ulaştı (HTTP 429); sonraki kontrol ertelenmeli.")
            if response.status != 200:
                raise CollectionError(f"Kaynak okunamadı (HTTP {response.status}).")
            if pages == 1:
                cache = {"etag": response.headers.get("etag"), "modified": response.headers.get("last-modified")}
            digest = hashlib.sha256(response.body).digest()
            if digest in bodies:
                url = None
                break
            bodies.add(digest)
            if source.get("kind") in {"rss", "atom"}:
                parsed = _parse_feed(response.body, source, response.url)
            elif source.get("kind") == "github_releases":
                parsed = _parse_releases(response.body, source, response.url)
            elif source.get("kind") in {"html", "project"}:
                parsed = _parse_html(response.body, source, response.url)
            else:
                raise CollectionError("Kaynağın toplama yöntemi desteklenmiyor.")
            fresh = [item for item in parsed if item["url"] not in seen]
            items.extend(fresh)
            seen.update(item["url"] for item in fresh)
            next_url = _sonraki_sayfa(response, source, len(parsed))
            truncated |= len(parsed) == MAX_ITEMS and not next_url
            if pages == 1 and continuation:
                next_url, continuation = continuation, None
            if not fresh and pages > 1:
                next_url = None
            if next_url == url:
                raise CollectionError("Kaynağın sayfalama bağlantısı kendini yineliyor.")
            url = next_url
        except CollectionError as failure:
            if pages <= 1 and not items:
                raise
            error = str(failure)
            break
    if maintenance is not None:
        maintenance["last_release_at"] = max((item["published_at"] for item in items if item.get("published_at")), default=None)
    return {"items": items, **cache, "not_modified": head_unchanged and not items and not error,
            "pages": pages, "next_url": url, "error": error, "truncated": bool(url) or truncated,
            "maintenance": maintenance,
            "coverage": "Sınırlı sayfalama; bütün geçmişin toplandığı anlamına gelmez."}
