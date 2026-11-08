"""Seçilmiş anonim belgelerin HTTP temsilini ve temkinli yeniden kullanım koşullarını doğrular."""

from __future__ import annotations

import email.utils
import math
import re
import time
from dataclasses import dataclass

from radar.kaynak_kimligi import canonical_source_url
from radar.kaynak_kullanim import parse_content_signal, parse_directives
from radar.toplayici import FetchResult, MAX_BODY_BYTES, USER_AGENT, anonim_belge_oku, robots_content_signals


REPRESENTATION = "public-doc-anonymous-v2"
ACCEPT = "text/html, application/xhtml+xml;q=0.9"
HEADER_NAMES = {"cache-control", "vary", "etag", "last-modified", "date", "age", "expires",
                "content-type", "content-encoding", "content-language"}
VARY_NAMES = {"accept-language", "accept-encoding", "user-agent"}


class HTTPPolicyError(ValueError):
    """Başlık veya belge içeriği taşımayan sabit hata kodu."""


@dataclass(frozen=True)
class DocumentResponse(FetchResult):
    set_cookie_seen: bool = False
    # Kaynak kullanım sinyalleri: robots Content-Signal ve X-Robots-Tag, yorumlanmış (ham değer yok).
    usage: dict | None = None


def request_variant(language):
    if not isinstance(language, str) or not re.fullmatch(r"[a-z]{2}(?:-[A-Z]{2})?", language):
        raise HTTPPolicyError("invalid_language")
    return {"accept": ACCEPT, "accept-language": language, "accept-encoding": "identity", "user-agent": USER_AGENT}


def _source(source):
    if not isinstance(source, dict) or source.get("representation") != REPRESENTATION:
        raise HTTPPolicyError("explicit_v2_policy_required")
    try:
        url = canonical_source_url(source["url"])
    except (KeyError, TypeError, ValueError):
        raise HTTPPolicyError("invalid_source_url") from None
    if url != source["url"]:
        raise HTTPPolicyError("canonical_source_url_required")
    variant = request_variant(source.get("language", "en"))
    if source.get("request_variant", variant) != variant:
        raise HTTPPolicyError("request_variant_mismatch")
    if type(source.get("allow_public_cookie", False)) is not bool:
        raise HTTPPolicyError("invalid_cookie_policy")
    return url, variant


def _date(value):
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            return None
        stamp = parsed.timestamp()
        return stamp if math.isfinite(stamp) and stamp >= 0 else None
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _etag(value):
    if value is None:
        return None
    if (not isinstance(value, str) or len(value) > 512
            or not re.fullmatch(r'(?:W/)?"[\x21\x23-\x7e\x80-\xff]*"', value)):
        raise HTTPPolicyError("invalid_etag")
    return value


def _modified(value):
    if value is not None and (not isinstance(value, str) or len(value) > 80 or _date(value) is None):
        raise HTTPPolicyError("invalid_last_modified")
    return value


def _cache_control(value):
    # Unknown extension values are not retained. Repeated/invalid lifetimes
    # become zero; quoted private/no-cache field names cannot weaken policy.
    directives = {}
    if value.count('"') % 2 or "\\" in value:
        raise HTTPPolicyError("invalid_cache_control")
    for item in re.split(r',(?=(?:[^"]*"[^"]*")*[^"]*$)', value):
        if not item.strip():
            continue
        name, separator, argument = item.strip().partition("=")
        name = name.strip().lower()
        if not re.fullmatch(r"[a-z0-9!#$%&'*+.^_`|~-]+", name):
            raise HTTPPolicyError("invalid_cache_control")
        if name in {"private", "no-store"}:
            raise HTTPPolicyError("private_cache_policy")
        if name in {"no-cache", "public", "must-revalidate", "proxy-revalidate"}:
            if name == "public" and separator:
                raise HTTPPolicyError("invalid_public_cache_policy")
            directives[name] = None
        elif name in {"max-age", "s-maxage"}:
            argument = argument.strip()
            parsed = int(argument.strip('"')) if re.fullmatch(r'[0-9]{1,10}|"[0-9]{1,10}"', argument) else 0
            directives[name] = 0 if name in directives else parsed
    return ", ".join(name if value is None else f"{name}={value}" for name, value in sorted(directives.items()))


def _headers(headers):
    if not isinstance(headers, dict):
        raise HTTPPolicyError("invalid_headers")
    normalized = {}
    cookie = False
    for key, value in headers.items():
        if not isinstance(key, str):
            raise HTTPPolicyError("invalid_headers")
        name = key.lower()
        if name == "set-cookie":
            cookie = True
            continue  # The value never enters a returned object or error.
        if name == "content-range":
            raise HTTPPolicyError("partial_representation")
        if name not in HEADER_NAMES:
            continue
        if not isinstance(value, str) or len(value) > 4096 or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise HTTPPolicyError("invalid_headers")
        if name in normalized:
            if name in {"cache-control", "vary"}:
                normalized[name] += ", " + value
            elif normalized[name] != value:
                raise HTTPPolicyError("conflicting_headers")
        else:
            normalized[name] = value.strip()
    if "cache-control" in normalized:
        normalized["cache-control"] = _cache_control(normalized["cache-control"])
    if "vary" in normalized:
        vary = {item.strip().lower() for item in normalized["vary"].split(",") if item.strip()}
        if not vary <= VARY_NAMES:
            raise HTTPPolicyError("unsupported_vary")
        normalized["vary"] = ", ".join(sorted(vary))
    if "etag" in normalized:
        normalized["etag"] = _etag(normalized["etag"])
    if "last-modified" in normalized:
        normalized["last-modified"] = _modified(normalized["last-modified"])
    for name in ("date", "expires"):
        if name in normalized and _date(normalized[name]) is None:
            normalized[name] = ""  # Invalid clock cannot produce freshness or leak arbitrary text.
    if "age" in normalized and not re.fullmatch(r"[0-9]{1,10}", normalized["age"]):
        normalized["age"] = "invalid"
    if "content-language" in normalized:
        language = normalized["content-language"].lower()
        if not re.fullmatch(r"[a-z]{2}(?:-[a-z]{2})?", language):
            raise HTTPPolicyError("ambiguous_content_language")
        normalized["content-language"] = language
    return normalized, cookie


def check_policy(source, headers, *, set_cookie_seen=False):
    """Birleştirilmiş başlıkları anonim v2 politikasına göre yeniden denetler."""
    _source(source)
    safe, cookie = _headers(headers)
    cookie = cookie or set_cookie_seen
    if cookie and (not source.get("allow_public_cookie", False)
                   or "public" not in {part.strip() for part in safe.get("cache-control", "").split(",")}):
        raise HTTPPolicyError("public_cookie_not_authorized")
    if safe.get("content-encoding", "identity").lower() != "identity":
        raise HTTPPolicyError("unsupported_content_encoding")
    if "content-language" in safe and safe["content-language"] != source.get("language", "en").lower():
        raise HTTPPolicyError("content_language_mismatch")
    if safe.get("content-type", "").split(";", 1)[0].strip().lower() not in {"text/html", "application/xhtml+xml"}:
        raise HTTPPolicyError("unsupported_content_type")
    return safe, bool(cookie)


def fetch_document(source, *, etag=None, modified=None):
    """Sabit anonim başlıklarla güvenli katmanı kullanır; çerez değerlerini döndürmez."""
    url, variant = _source(source)
    etag, modified = _etag(etag), _modified(modified)
    headers = dict(variant)
    if etag is not None:
        headers["if-none-match"] = etag
    if modified is not None:
        headers["if-modified-since"] = modified
    started = time.time()
    result = anonim_belge_oku(url, headers)
    received = time.time()
    safe, cookie = _headers(result.headers)
    tag = result.headers.get("x-robots-tag") if isinstance(result.headers, dict) else None
    usage = {"robots_content_signal": parse_content_signal(robots_content_signals(url)),
             "x_robots_tag": parse_directives([tag] if isinstance(tag, str) else [], scoped=True)}
    response = DocumentResponse(result.status, result.body, safe, result.url, cookie, usage)
    return response, {"sent_etag": etag, "sent_modified": modified, "request_started_at": started,
                      "response_received_at": received, "request_variant": variant}


def _freshness(headers, *, now, started, previous=None, new_date=False):
    directives = dict((item.strip().partition("=")[0], item.strip().partition("=")[2])
                      for item in headers.get("cache-control", "").split(",") if item.strip())
    date = _date(headers.get("date"))
    age = headers.get("age", "0")
    valid_clock = (date is not None and date <= now and isinstance(started, (int, float))
                   and not isinstance(started, bool) and math.isfinite(started) and 0 <= started <= now
                   and re.fullmatch(r"[0-9]{1,10}", age))
    initial_age = max(now - date, int(age) + now - started) if valid_clock else None
    if previous is not None and not new_date and initial_age is not None:
        old_age, old_time = previous.get("initial_age"), previous.get("response_received_at")
        if (not isinstance(old_age, (int, float)) or not isinstance(old_time, (int, float))
                or not math.isfinite(old_age) or not math.isfinite(old_time) or not 0 <= old_time <= now):
            initial_age = None
        else:
            initial_age = max(initial_age, old_age + now - old_time)
    lifetime = 0
    if "s-maxage" in directives or "max-age" in directives:
        lifetime = int(directives.get("s-maxage", directives.get("max-age")))
    elif date is not None and "expires" in headers:
        expires = _date(headers["expires"])
        lifetime = max(0, expires - date) if expires is not None else 0
    remaining = max(0, lifetime - initial_age) if initial_age is not None else 0
    if "no-cache" in directives or directives.get("max-age") == "0":
        remaining = 0
    return {"fresh_until": now + remaining, "revalidate": remaining == 0,
            "initial_age": initial_age, "response_received_at": now}


def prepare_response(source, previous, response, *, now, sent_etag=None, sent_modified=None, request_started_at=None):
    """JSON metaverisini gövdeden ayırır; 304 için gerçek isteği önceki temsile bağlar."""
    url, variant = _source(source)
    if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now) or now < 0:
        raise HTTPPolicyError("invalid_clock")
    if not isinstance(response, FetchResult) or response.url != url or response.status not in (200, 304):
        raise HTTPPolicyError("response_identity_or_status_mismatch")
    if not isinstance(response.body, bytes) or len(response.body) > MAX_BODY_BYTES:
        raise HTTPPolicyError("invalid_body")
    sent_etag, sent_modified = _etag(sent_etag), _modified(sent_modified)
    headers, cookie = _headers(response.headers)
    cookie = cookie or getattr(response, "set_cookie_seen", False)
    identity = {"url": url, "representation": REPRESENTATION, "request_variant": variant,
                "extractor_version": source.get("extractor_version"), "source_key": source.get("source_key")}
    old = None
    kind = "body"
    if response.status == 304:
        if response.body or not isinstance(previous, dict) or any(previous.get(key) != value for key, value in identity.items()):
            raise HTTPPolicyError("previous_representation_required")
        if not (sent_etag or sent_modified):
            raise HTTPPolicyError("sent_validator_required")
        old, previous_cookie = check_policy(source, previous.get("headers", {}), set_cookie_seen=previous.get("set_cookie_seen", False))
        if ((sent_etag is not None and sent_etag != old.get("etag"))
                or (sent_modified is not None and sent_modified != old.get("last-modified"))):
            raise HTTPPolicyError("sent_validator_mismatch")
        if "etag" in headers and (not old.get("etag") or headers["etag"].removeprefix("W/") != old["etag"].removeprefix("W/")):
            raise HTTPPolicyError("response_validator_mismatch")
        if "last-modified" in headers and old.get("last-modified") and headers["last-modified"] != old["last-modified"]:
            raise HTTPPolicyError("response_validator_mismatch")
        cookie = cookie or previous_cookie
        kind = ("weak_etag" if sent_etag.startswith("W/") or headers.get("etag", "").startswith("W/") else "strong_etag") if sent_etag else "last_modified"
    merged, cookie = check_policy(source, {**(old or {}), **headers}, set_cookie_seen=cookie)
    if response.status == 200 and not response.body:
        raise HTTPPolicyError("empty_body")
    fetched = now if response.status == 200 else previous.get("content_fetched_at")
    if not isinstance(fetched, (int, float)) or isinstance(fetched, bool) or not math.isfinite(fetched) or not 0 <= fetched <= now:
        raise HTTPPolicyError("invalid_previous_fetch_time")
    meta = {**identity, "status": response.status, "downloaded_bytes": len(response.body),
            "headers": merged, "set_cookie_seen": cookie, "etag": merged.get("etag"),
            "modified": merged.get("last-modified"), "representation_checked_at": now, "content_fetched_at": fetched,
            "validation_kind": kind, "byte_verified": response.status == 200,
            **_freshness(merged, now=now, started=request_started_at,
                         previous=previous if response.status == 304 else None, new_date="date" in headers)}
    return meta, response.body if response.status == 200 else None
