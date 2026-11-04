"""Yerel kaynak kataloğunu doğrular; yayıncıları, dalları ve izinli alan adlarını tutarlı tutar."""

import ipaddress
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from radar.konular import KONULAR

YONTEMLER = {"rss", "atom", "github_releases", "html", "project", "discovery"}
KAYNAK_TURLERI = {"Üretici", "Açık kaynak", "Araştırma", "Kurum", "Teknik yayın"}


def alan_adi_gecerli(host):
    if not isinstance(host, str) or host != host.lower() or len(host) > 253:
        return False
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host) or "." not in host:
        return False
    if any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
           for label in host.split(".")):
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return not host.endswith((".localhost", ".local", ".internal", ".test", ".invalid"))
    return False


def katalogu_dogrula(sources):
    if not isinstance(sources, list) or not 1 <= len(sources) <= 200:
        raise ValueError("Kaynak kataloğu 1–200 kayıt içermeli.")
    ids, feeds = set(), set()
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("Kaynak kaydı nesne olmalı.")
        identifier = source.get("id", "")
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", identifier) or identifier in ids:
            raise ValueError("Kaynak kimliği geçersiz veya yineleniyor.")
        ids.add(identifier)
        if source.get("topic") not in KONULAR or source.get("kind") not in YONTEMLER:
            raise ValueError(f"{identifier}: konu veya toplama yöntemi geçersiz.")
        interval = source.get("interval_hours")
        if isinstance(interval, bool) or not isinstance(interval, (int, float)) or not 1 <= interval <= 720:
            raise ValueError(f"{identifier}: kontrol aralığı geçersiz.")
        if type(source.get("enabled")) is not bool:
            raise ValueError(f"{identifier}: etkinlik alanı doğru/yanlış olmalı.")
        for field in ("name", "benefit", "publisher", "source_type", "language"):
            value = source.get(field)
            if not isinstance(value, str) or not value.strip() or len(value) > 600 or any(ord(char) < 32 for char in value):
                raise ValueError(f"{identifier}: {field} eksik veya geçersiz.")
        if source["source_type"] not in KAYNAK_TURLERI:
            raise ValueError(f"{identifier}: kaynak türü geçersiz.")
        branches = source.get("branches")
        if not isinstance(branches, list) or not 1 <= len(branches) <= 10 or any(
                not isinstance(branch, str) or not 2 <= len(branch.strip()) <= 100 for branch in branches):
            raise ValueError(f"{identifier}: alt dallar eksik veya geçersiz.")
        for field in ("fetch_hosts", "item_hosts"):
            hosts = source.get(field)
            limit = 128 if source.get("kind") == "discovery" and field == "item_hosts" else 12
            if not isinstance(hosts, list) or not 1 <= len(hosts) <= limit or not all(alan_adi_gecerli(host) for host in hosts):
                raise ValueError(f"{identifier}: alan adı listesi geçersiz.")
        if source.get("pagination") not in (None, "wordpress") or (source.get("pagination") and source["kind"] not in {"rss", "atom"}):
            raise ValueError(f"{identifier}: sayfalama yöntemi geçersiz.")
        for field, host_field in (("fetch_url", "fetch_hosts"), ("url", "item_hosts")):
            value = source.get(field, "")
            try:
                parsed = urlsplit(value)
                valid = (isinstance(value, str) and len(value) <= 4096 and parsed.scheme == "https"
                         and parsed.port in (None, 443) and parsed.hostname in source[host_field]
                         and parsed.username is None and parsed.password is None and not parsed.fragment
                         and not any(ord(char) <= 32 for char in value) and "\\" not in value)
            except (AttributeError, TypeError, ValueError):
                valid = False
            if not valid:
                raise ValueError(f"{identifier}: {field} güvenli kaynak alan adlarıyla eşleşmiyor.")
        if source["fetch_url"] in feeds:
            raise ValueError("Aynı toplama adresi iki kez tanımlanamaz.")
        feeds.add(source["fetch_url"])
    return sources


def kaynaklari_oku(path=None):
    path = Path(path) if path else Path(__file__).with_name("kaynaklar.json")
    return katalogu_dogrula(json.loads(path.read_text(encoding="utf-8")))


def izinli_alan_adlari():
    """Yalnız uygulamayla gelen doğrulanmış katalogdaki tam alan adlarını verir."""
    return frozenset(host for source in kaynaklari_oku() for host in source["fetch_hosts"])
