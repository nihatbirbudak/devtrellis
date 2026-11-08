"""Bounded optional public hints; a hint never validates a web representation."""
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit
from xml.parsers import expat
from radar.kaynak_kimligi import canonical_source_url
from radar import toplayici


def validate_signal(kind, url, source_url):
    if kind not in {"sitemap", "git_commit"} or not isinstance(url, str):
        raise ValueError("Sinyal tanımı desteklenmiyor.")
    if canonical_source_url(url) != url or urlsplit(url).query or urlsplit(url).fragment:
        raise ValueError("Sinyal sabit kamu adresi olmalı.")
    host, path = urlsplit(url).hostname, urlsplit(url).path
    # The API origin is allowed only for this explicit public commit endpoint;
    # it does not expand the document collector's global host catalog.
    if host not in toplayici.APPROVED_FETCH_HOSTS and not (kind == "git_commit" and host == "api.github.com"):
        raise ValueError("Sinyal alan adı izinli değil.")
    if kind == "sitemap" and (host != urlsplit(source_url).hostname or not path.endswith(".xml")):
        raise ValueError("Site haritası kaynakla aynı kökende XML olmalı.")
    if kind == "git_commit" and (host != "api.github.com" or not re.fullmatch(r"/repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/commits/[A-Za-z0-9_.-]+", path)):
        raise ValueError("Git sinyali açık bir depo commit adresi olmalı.")


def validate_observation(value, kind, now):
    if not isinstance(value, dict) or set(value) != {"kind", "fingerprint"} or value["kind"] != kind:
        raise ValueError("Sinyal gözlemi geçersiz.")
    if not isinstance(value["fingerprint"], str) or not re.fullmatch(r"[a-f0-9]{64}", value["fingerprint"]):
        raise ValueError("Sinyal özeti geçersiz.")
    return {**value, "checked_at": now, "evidence": "scheduling_hint_only"}


def _lastmod(body, source_url):
    if len(body) > 2_000_000:
        raise ValueError("Site haritası sınırı aşıyor.")
    parser = expat.ParserCreate(namespace_separator=" ")
    stack, text, entries, current = [], [], [], {}
    def reject(*args):
        raise ValueError("Site haritasında varlık bildirimi desteklenmiyor.")
    parser.StartDoctypeDeclHandler = reject
    parser.EntityDeclHandler = reject
    parser.ExternalEntityRefHandler = reject
    def start(name, attributes):
        tag = name.split(" ")[-1]
        stack.append(tag)
        if len(stack) > 8:
            reject()
        if tag in {"loc", "lastmod"}:
            text.clear()
    def data(value):
        if stack and stack[-1] in {"loc", "lastmod"}:
            text.append(value)
            if sum(map(len,text)) > 4096:
                reject()
    def end(name):
        tag = name.split(" ")[-1]
        if tag in {"loc", "lastmod"} and stack == ["urlset", "url", tag]:
            if tag in current:
                reject()
            current[tag] = "".join(text).strip()
        if tag == "url" and stack == ["urlset", "url"]:
            if current.get("loc") == source_url:
                entries.append(current.copy())
            current.clear()
        stack.pop()
    parser.StartElementHandler, parser.EndElementHandler, parser.CharacterDataHandler = start, end, data
    parser.Parse(body, True)
    if len(entries) != 1 or not entries[0].get("lastmod"):
        raise ValueError("Site haritasında tek kaynak tarihi bulunamadı.")
    value = entries[0]["lastmod"]
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("Site haritası tarihi geçersiz.")
        parsed = parsed.replace(tzinfo=timezone.utc)
    if parsed.timestamp() > time.time()+300:
        raise ValueError("Site haritası gelecekte bir tarih taşıyor.")
    return parsed.isoformat()


def fetch_signal(source):
    validate_signal(source.signal_kind, source.signal_url, source.url)
    url = source.signal_url
    hosts = {urlsplit(url).hostname}
    deadline = time.monotonic()+toplayici.SOURCE_TIMEOUT
    token = toplayici._REQUEST_FETCH_HOSTS.set(frozenset(hosts))
    try:
        if not toplayici._robots_allowed(url, hosts, deadline):
            raise ValueError("Sinyal robots kuralı okumaya izin vermiyor.")
        response = toplayici._request_once(url, hosts, {"Accept": "application/xml" if source.signal_kind == "sitemap" else "application/vnd.github+json", "Accept-Encoding": "identity"}, deadline, strict_representation=True)
    finally:
        toplayici._REQUEST_FETCH_HOSTS.reset(token)
    headers = {key.lower():value for key,value in response.headers.items()}
    directives = {p.strip().split("=",1)[0].lower() for p in headers.get("cache-control","").split(",")}
    vary = {p.strip().lower() for p in headers.get("vary","").split(",") if p.strip()}
    if (response.status != 200 or response.url != url or "set-cookie" in headers or directives & {"private","no-store"}
            or vary - {"accept", "accept-encoding", "user-agent"}):
        raise ValueError("Sinyal kamu yanıtı uygun değil.")
    if source.signal_kind == "sitemap":
        value = _lastmod(response.body, source.url)
    else:
        item = json.loads(response.body)
        value = item.get("sha") if isinstance(item,dict) else None
        if not isinstance(value,str) or not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}",value):
            raise ValueError("Git nesne sinyali geçersiz.")
    fingerprint = hashlib.sha256((source.signal_kind+"\n"+value).encode()).hexdigest()
    return {"kind":source.signal_kind,"fingerprint":fingerprint},len(response.body)
