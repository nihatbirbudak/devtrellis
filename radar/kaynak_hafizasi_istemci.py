"""Ortak kaynak hafızasını sabit hizmetten dar kimliklerle okur ve açık istekle kontrol eder."""

import hashlib
import json
import math
import re
from http.cookies import CookieError, SimpleCookie
from urllib.parse import urlsplit

import httpx

from radar.kaynak_hafizasi import KaynakTanimi, kamu_metni, ozet_dogrula
from radar.toplayici import APPROVED_FETCH_HOSTS
from radar.topluluk_adresi import topluluk_adresi


COMMUNITY_URL = topluluk_adresi()
PREFIX = "/api/community/source-memory"
COOKIE = "devtrellis_session"
MAX_RESPONSE_BYTES = 128 * 1024
INVALID = "Kaynak hafızası geçerli ve sınırlı bir yanıt döndürmedi."
INPUT_ERROR = "Kaynak isteği geçersiz; yalnız kaynak kimliği ve sınırlı kontrol seçenekleri kullanılabilir."
SUMMARY_FIELDS = {"summary", "findings", "scope", "limitations", "sections", "model", "generated_at", "license"}
SOURCE_FIELDS = {"id", "url", "language", "representation", "extractor_version"}
RECORD_FIELDS = {"source_id", "key", "url", "language", "representation", "extractor_version", "state", "fresh",
                 "source_revision", "summary_status", "summary_reusable", "summary", "claims_verified", "model_calls",
                 "content_hash", "title", "text_length", "fetched_at", "checked_at", "attempted_at", "last_error",
                 "method", "checks", "downloaded_bytes"}
CHECK_FIELDS = {"check_started", "check_succeeded", "ownership_lost", "budget_exhausted", "retry_after_seconds"}


class KaynakHafizasiIstemciHatasi(ValueError):
    """Uzak gövde, çerez veya özel bağlamı yansıtmayan kaynak bağlantısı hatası."""


def _require(condition, message=INVALID):
    if not condition:
        raise KaynakHafizasiIstemciHatasi(message)


def _identifier(value):
    _require(isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", value), INPUT_ERROR)
    return value


def _integer(value, minimum=0, maximum=9223372036854775807):
    _require(type(value) is int and minimum <= value <= maximum)
    return value


def _boolean(value):
    _require(type(value) is bool)
    return value


def _unique(pairs):
    value = {}
    for key, item in pairs:
        _require(key not in value)
        value[key] = item
    return value


def _constant(value):
    raise KaynakHafizasiIstemciHatasi(INVALID)


def _source(value):
    _require(isinstance(value, dict) and set(value) == SOURCE_FIELDS)
    try:
        source = KaynakTanimi(**value)
        _require(urlsplit(source.url).hostname in APPROVED_FETCH_HOSTS)
    except (ValueError, TypeError, KeyError):
        raise KaynakHafizasiIstemciHatasi(INVALID) from None
    return source


def _source_public(source):
    return {"id": source.id, "url": source.url, "language": source.language,
            "representation": source.representation, "extractor_version": source.extractor_version}


def _record(value, source_id, *, checking=False):
    _require(isinstance(value, dict) and RECORD_FIELDS <= value.keys()
             and not (value.keys() - RECORD_FIELDS - (CHECK_FIELDS if checking else set())))
    _require(value["source_id"] == source_id)
    source = _source({"id": source_id, **{key: value[key] for key in SOURCE_FIELDS - {"id"}}})
    _require(value["key"] == source.key)
    _require(isinstance(value["state"], str) and value["state"] in {"missing", "checking", "fresh", "stale", "error"})
    fresh, reusable = _boolean(value["fresh"]), _boolean(value["summary_reusable"])
    revision = _integer(value["source_revision"])
    _require(isinstance(value["summary_status"], str) and value["summary_status"] in {"missing", "current", "stale"})
    _require(value["claims_verified"] is False and type(value["model_calls"]) is int and value["model_calls"] == 0)
    _require(fresh == (value["state"] == "fresh"))
    result = {key: value[key] for key in RECORD_FIELDS}
    for field in ("fetched_at", "checked_at", "attempted_at"):
        stamp = value[field]
        _require(stamp is None or (type(stamp) in (int, float) and 0 <= stamp <= 253402300799 and math.isfinite(stamp)))
    _integer(value["checks"])
    _integer(value["downloaded_bytes"])
    if revision:
        _require(isinstance(value["content_hash"], str) and re.fullmatch(r"[a-f0-9]{64}", value["content_hash"]))
        _integer(value["text_length"], 1, 40000)
        _require(value["fetched_at"] is not None and value["checked_at"] is not None
                 and isinstance(value["method"], str) and value["method"] in {"content_hash", "conditional_304"} and value["checks"] >= revision)
        try:
            result["title"] = kamu_metni(value["title"], "Kaynak başlığı", 500)
        except (ValueError, TypeError):
            raise KaynakHafizasiIstemciHatasi(INVALID) from None
    else:
        _require(all(value[field] is None for field in ("content_hash", "title", "text_length", "fetched_at", "checked_at", "method")))
        _require(not fresh and not reusable and value["summary_status"] == "missing")
    error = value["last_error"]
    _require(error is None or isinstance(error, str) and 0 < len(error) <= 600)
    _require(not fresh or error is None)
    _require(value["state"] != "error" or error is not None)
    result["last_error"] = "Kaynak kontrolü tamamlanamadı; önceki başarılı kontrol yenilenmedi." if error else None
    _require(value["summary"] is None or isinstance(value["summary"], dict))
    result["summary"] = None
    if reusable:
        _require(fresh and revision > 0 and value["summary_status"] == "current")
        summary = value["summary"]
        _require(isinstance(summary, dict) and set(summary) == SUMMARY_FIELDS | {"version", "source_revision", "content_hash"})
        _integer(summary["version"], 1)
        _integer(summary["source_revision"], 1)
        _require(summary["source_revision"] == revision and summary["content_hash"] == value["content_hash"])
        try:
            public = ozet_dogrula({key: summary[key] for key in SUMMARY_FIELDS})
        except (ValueError, TypeError, KeyError):
            raise KaynakHafizasiIstemciHatasi(INVALID) from None
        result["summary"] = {**public, **{key: summary[key] for key in ("version", "source_revision", "content_hash")}}
    # Çekirdek hata/eski kontrol durumunda eski özeti döndürebilir. İstemci yalnız
    # summary_reusable olan sürümü taşır; alanın varlığı tek başına izin değildir.
    for field in CHECK_FIELDS & value.keys():
        result[field] = _integer(value[field], 0, 86400) if field == "retry_after_seconds" else _boolean(value[field])
    if checking:
        _require("check_started" in value)
        if value.get("check_succeeded") is True:
            _require(value["check_started"] is True and not value.get("ownership_lost", False) and error is None)
    return result


class KaynakHafizasiIstemcisi:
    """Anahtarsız sabit kamu hizmeti istemcisi; kurucu ağ veya veritabanı açmaz."""

    def __init__(self, *, transport=None):
        self.transport = transport

    def _client(self, *, checking=False):
        return httpx.Client(timeout=httpx.Timeout(45 if checking else 20, connect=8), follow_redirects=False, trust_env=False,
                            transport=self.transport, headers={"Accept": "application/json", "Accept-Encoding": "identity",
                            "User-Agent": "DevTrellis-source-memory-client"})

    @staticmethod
    def _request(client, method, path, *, payload=None, csrf=None, session=False):
        headers = {"Origin": COMMUNITY_URL} if method == "POST" else {}
        if csrf is not None:
            headers["X-Ufuk-CSRF"] = csrf
        try:
            with client.stream(method, COMMUNITY_URL + path, json=payload, headers=headers) as response:
                if response.status_code != 200:
                    messages = {404: "Kaynak hafızası kapalı veya seçilen kaynak bulunamadı.",
                                429: "Kaynak kontrol sınırına ulaşıldı; daha sonra tekrar deneyin.",
                                401: "Kaynak kontrol oturumu doğrulanamadı.", 403: "Kaynak kontrol oturumu doğrulanamadı."}
                    raise KaynakHafizasiIstemciHatasi(messages.get(response.status_code,
                        "Kaynak hafızası isteği tamamlanamadı; otomatik tekrar yapılmadı."))
                _require(response.headers.get("content-type", "").split(";", 1)[0].strip().lower() == "application/json")
                _require(response.headers.get("content-encoding", "identity").lower() == "identity")
                cookies = response.headers.get_list("set-cookie")
                _require(session or not cookies)
                length = response.headers.get("content-length")
                if length is not None:
                    _require(length.isascii() and length.isdecimal() and int(length) <= MAX_RESPONSE_BYTES)
                raw = bytearray()
                for chunk in response.iter_bytes(chunk_size=8192):
                    _require(len(raw) + len(chunk) <= MAX_RESPONSE_BYTES)
                    raw.extend(chunk)
                value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_constant)
                _require(isinstance(value, dict))
                return value, cookies
        except KaynakHafizasiIstemciHatasi:
            raise
        except (httpx.HTTPError, OSError, ValueError, TypeError, RecursionError, OverflowError):
            raise KaynakHafizasiIstemciHatasi(INVALID) from None

    @staticmethod
    def _session(client):
        value, cookies = KaynakHafizasiIstemcisi._request(client, "POST", "/api/community/session", payload={}, session=True)
        _require(set(value) == {"authenticated", "user", "csrf_token"} and value["authenticated"] is False and value["user"] is None)
        token = value["csrf_token"]
        _require(isinstance(token, str) and re.fullmatch(r"[a-f0-9]{64}", token))
        _require(len(cookies) == 1 and len(cookies[0]) <= 1024
                 and not any(ord(char) < 32 or ord(char) == 127 for char in cookies[0]))
        parsed = SimpleCookie()
        try:
            parsed.load(cookies[0])
        except CookieError:
            raise KaynakHafizasiIstemciHatasi(INVALID) from None
        _require(set(parsed) == {COOKIE})
        cookie = parsed[COOKIE]
        _require(re.fullmatch(r"[A-Za-z0-9_-]{32,128}", cookie.value) and cookie["path"] == "/"
                 and not cookie["domain"] and cookie["secure"] and cookie["httponly"]
                 and cookie["samesite"].lower() == "strict")
        _require(hashlib.sha256(("devtrellis-csrf:" + cookie.value).encode()).hexdigest() == token)
        # Yalnız bu çağrı için yeni anonim oturum çerezi; tarayıcı/hesap çerezi devralınmaz.
        client.cookies.clear()
        client.cookies.set(COOKIE, cookie.value, domain=urlsplit(COMMUNITY_URL).hostname, path="/")
        return token

    def katalog(self):
        with self._client() as client:
            value, _ = self._request(client, "GET", PREFIX)
        _require(set(value) == {"sources", "model_calls"} and type(value["model_calls"]) is int and value["model_calls"] == 0)
        _require(isinstance(value["sources"], list) and len(value["sources"]) <= 20)
        sources = [_source(item) for item in value["sources"]]
        _require(len({item.id for item in sources}) == len(sources) and len({item.key for item in sources}) == len(sources))
        return {"sources": [_source_public(item) for item in sources], "model_calls": 0}

    def oku(self, source_id):
        source_id = _identifier(source_id)
        with self._client() as client:
            value, _ = self._request(client, "GET", PREFIX + "/" + source_id)
        return _record(value, source_id)

    def kontrol_et(self, source_id, max_age_seconds=3600, freshness_required=True):
        source_id = _identifier(source_id)
        _require(type(max_age_seconds) is int and 60 <= max_age_seconds <= 86400
                 and type(freshness_required) is bool, INPUT_ERROR)
        with self._client(checking=True) as client:
            csrf = self._session(client)
            value, _ = self._request(client, "POST", PREFIX + "/" + source_id + "/check", csrf=csrf,
                                    payload={"max_age_seconds": max_age_seconds, "freshness_required": freshness_required})
        return _record(value, source_id, checking=True)
