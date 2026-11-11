"""K1-V4 sözleşme taslağına karşı sahte yerel uygulama: kapsam, konu listesi, adres bakışı ve arama.

Gerçek sunucu değildir. Sözleşmenin örnek gövdelerini (`k1_v4_fixtures.json`, taslağın bayt bayt kopyası)
ve sürüm kuralını taklit eder; K2 istemci testleri ve ağsız ölçüm için yalnız loopback üzerinde çalışır.
Kartlardaki bölüm/alıntı özetleri sentetiktir; hiçbir kaynak sayfası açılmaz.
"""

import hashlib
import json
import re
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from radar.kaynak_kimligi import EXTRACTOR_VERSION, REPRESENTATION, address_key, canonical_source_url, source_key


FIXTURES = Path(__file__).with_name("k1_v4_fixtures.json")
REQUEST_VARIANT = {"accept": "text/html, application/xhtml+xml;q=0.9", "accept-encoding": "identity",
                   "user-agent": "UfukRadar/0.1"}


def load_fixtures():
    return json.loads(FIXTURES.read_text(encoding="utf-8"))


def _marker(*parts):
    return hashlib.sha256("\n".join(("k2-synthetic",) + parts).encode("utf-8")).hexdigest()


def synthetic_card(url, claims, *, card_id=None, version=1, language="en", not_covered=(), freshness="fresh",
                   checked_on="2026-11-06", unchanged="2026-10-28T09:00:00Z", corrections=0):
    """Sözleşme şemasındaki zorunlu alanlarla sentetik kart; `claims` = (metin, alıntı, ürün, sürüm, şema) listesi."""
    canonical = canonical_source_url(url)
    variant = {**REQUEST_VARIANT, "accept-language": language}
    card_id = card_id or _marker("card", canonical, language)[:32]
    rows = []
    for index, claim in enumerate(claims):
        text, quote, product, versions, scheme = claim[:5]
        extra = claim[5] if len(claim) > 5 else {}
        rows.append({
            "id": _marker("claim", card_id, str(index))[:32], "text": text, "claim_type": "source_statement",
            "applies_to": [{"product": product, "versions": versions, "version_scheme": scheme}],
            "evidence": {"quote": quote, "source_url": canonical, "section_id": _marker("section", canonical, str(index)),
                         "section_hash": _marker("section-hash", canonical, str(index)),
                         "quote_hash": _marker("quote", quote), "supporting_context_hash": _marker("context", quote),
                         "extractor_version": EXTRACTOR_VERSION, "normalization_version": "visible-text-exact-v1",
                         "checked_on": extra.get("checked_on", checked_on)},
            "quote_match": {"state": extra.get("quote_match", "exact"), "quote_verified_at": checked_on + "T12:00:00Z"},
            "claim_review": {"state": extra.get("review", "not_reviewed"), "reviewed_at": None},
            "evidence_state": extra.get("evidence_state", "current")})
    return {
        "schema": "devtrellis-source-card", "schema_version": 4, "id": card_id, "version": version, "language": "en",
        "source": {"canonical_url": canonical, "language": language, "representation": REPRESENTATION,
                   "request_variant": variant, "extractor_version": EXTRACTOR_VERSION,
                   "address_key": address_key(canonical, language),
                   "source_key": source_key(canonical, language, representation=REPRESENTATION,
                                            request_variant=variant, extractor_version=EXTRACTOR_VERSION),
                   "body_hash": _marker("body", canonical)},
        "claims": rows, "not_covered": list(not_covered),
        "source_freshness": {"state": freshness, "validation_kind": "fixture_full_fetch", "byte_verified": True,
                             "signal_checked_at": None, "representation_checked_at": checked_on + "T12:00:00Z",
                             "content_fetched_at": checked_on + "T12:00:00Z", "fresh_until": checked_on + "T12:05:00Z",
                             "unchanged_observed_since": unchanged, "last_attempt_at": checked_on + "T12:00:00Z"},
        "provenance": {"origin": "human", "stages": []},
        "rights": {"own_text_license": "CC0-1.0", "quotes": []},
        "publication": {"state": "published", "path": "machine_source_card", "policy_version": "source-card-gates-draft-v1"},
        "corrections": {"open": corrections, "latest_public_version": None, "public_note": None},
        "link": f"https://devtrellis.example/c/{card_id}/v/{version}"}


def synthetic_agent_view(url, claims, *, record_id=None, version=1, language="en", kind="source_card",
                         checked_on="2026-11-06", content=None, warnings=()):
    """K1-V4 r2 `ajan_gorunumu` biçiminde sentetik kayıt: `server` / `declared` / `untrusted` ayrı nesneler."""
    canonical = canonical_source_url(url)
    key = address_key(canonical, language)
    record_id = record_id or _marker("record", canonical, language)[:32]
    rows = []
    for index, claim in enumerate(claims):
        text, quote, product, versions, scheme = claim[:5]
        rows.append({"id": _marker("claim", record_id, str(index))[:32], "text": text, "quote": quote,
                     "applies_to": [{"product": product, "versions": versions, "version_scheme": scheme}]})
    return {
        "schema": "devtrellis-agent-view", "schema_version": 1, "contract": "k1-v4-r2",
        "server": {"id": record_id, "kind": kind, "version": version, "language": "en",
                   "content_sha256": _marker("content", record_id), "published_at": checked_on + "T13:00:00Z",
                   "path": "machine_gate", "human_reviewed": False, "gate_version": "denetim-kapisi-0.2.1",
                   "gate_ran_at": checked_on + "T12:59:00Z", "checks_passed": ["quote", "privacy", "provenance"],
                   "warnings": list(warnings), "fields_not_tied_to_sources": [],
                   "label": {"tr": "İnsan incelemedi.", "en": "Not reviewed by a person; quotes matched by machine."},
                   "rights": {"own_text_license": "CC0-1.0", "quotes": {"basis": "short_quote", "max_chars": 300}},
                   "corrections": {"open": 0, "latest_public_version": None, "public_note": None},
                   "link": f"https://devtrellis.example/api/community/v4/records/{record_id}/v/{version}",
                   "quote_matches": [{"claim_id": row["id"], "state": "exact", "quote_verified_at": checked_on + "T12:59:00Z",
                                      "quote_hash": _marker("quote", row["quote"]), "address_key": key,
                                      "normalization_version": "gorunur-metin-bosluk-tirnak-tire-v1"} for row in rows]},
        "declared": {"provenance": {"origin": "model_assisted", "stages": []}, "generated_at": checked_on + "T10:00:00Z",
                     "claims": [{"claim_id": row["id"], "checked_on": checked_on, "claim_type": "source_statement",
                                 "applies_to": row["applies_to"]} for row in rows]},
        "untrusted": {"content": dict(content or {"title": "Synthetic record", "not_covered": ["benchmark numbers"]}),
                      "claims": [{"claim_id": row["id"], "text": row["text"], "quote": row["quote"], "source_url": canonical}
                                 for row in rows]}}


def card_key(card):
    if card.get("schema") == "devtrellis-agent-view":
        return card["server"]["quote_matches"][0]["address_key"]
    return card["source"]["address_key"]


def card_applies(card):
    applies = []
    claims = card["declared"]["claims"] if card.get("schema") == "devtrellis-agent-view" else card["claims"]
    for claim in claims:
        for item in claim["applies_to"] or []:   # canlı v4 kaydında null olabilir (gerçek sunucu da `or []` okur)
            if item not in applies:
                applies.append(item)
    return applies


def card_identity(card):
    if card.get("schema") == "devtrellis-agent-view":
        return card["server"]["id"], card["server"]["version"], _marker("source", card["server"]["id"])
    return card["id"], card["version"], card["source"]["source_key"]


def _numbers(text):
    return tuple(int(part) for part in re.findall(r"\d+", text))


def version_match(applies, product, version):
    """Sahte sunucunun sürüm kuralı: exact eşitlik, PEP 440/semver için virgüllü karşılaştırma aralığı."""
    if version is None:
        return "unspecified"
    for item in applies:
        if product is not None and item["product"] != product:
            continue
        spec = item["versions"]
        if item["version_scheme"] == "exact":
            if spec == version:
                return "exact"
            continue
        inside = True
        for part in spec.split(","):
            match = re.fullmatch(r"\s*(>=|<=|>|<|==)\s*([0-9][0-9A-Za-z.]*)\s*", part)
            if not match:
                inside = False
                break
            left, right = _numbers(version), _numbers(match.group(2))
            inside &= {">=": left >= right, "<=": left <= right, ">": left > right, "<": left < right,
                       "==": left == right}[match.group(1)]
        if inside:
            return "in_range"
    return "mismatch"


class FakeLibrary:
    """Sahte kütüphane: kartlar adres anahtarıyla, geri çekilenler mezar taşıyla, konular rapor kimliğiyle."""

    def __init__(self, cards=(), *, tombstones=(), topics=(), revision=10, ttl=300, complete=True,
                 generated=None, search=None):
        self.cards = {card_key(card): card for card in cards}
        self.tombstones = {item["address_key"]: item for item in tombstones}
        self.topic_entries = list(topics)
        self.revision, self.ttl, self.complete = revision, ttl, complete
        self.generated = generated or datetime.now(timezone.utc).replace(microsecond=0)
        self.search = search
        self.calls = []
        self.lock = threading.Lock()
        self.overrides = {}
        self.not_modified = 0
        self.lookup_freshness = {"state": "unknown", "checked_at": None}

    def window(self):
        until = self.generated + timedelta(seconds=self.ttl)
        return {"generated_at": self.generated.isoformat().replace("+00:00", "Z"),
                "valid_until": until.isoformat().replace("+00:00", "Z")}

    def coverage(self):
        addresses = {}
        for key, card in self.cards.items():
            card_id, version, source = card_identity(card)
            addresses[key] = [{"card": {"id": card_id, "version": version}, "source_key": source,
                               "availability": "current"}]
        stones = [{"address_key": key, "card": item["card"], "library_revision": self.revision}
                  for key, item in self.tombstones.items()]
        return {"schema": "devtrellis-coverage", "schema_version": 1, "identity_version": "k0-canonical-json-v1",
                **self.window(), "library_revision": self.revision, "complete": self.complete,
                "addresses": addresses, "tombstones": stones}

    def topics(self):
        return {"schema": "devtrellis-topics", "schema_version": 1, **self.window(),
                "library_revision": self.revision, "complete": self.complete, "entries": self.topic_entries}

    def lookup(self, key, product, version):
        base = {"schema": "devtrellis-source-lookup", "schema_version": 1, "address_key": key,
                "library_revision": self.revision, "cards": [], "candidates": [], "origin_fetches": 0,
                "model_calls": 0, "tombstone": None}
        if key in self.tombstones:
            stone = self.tombstones[key]
            return 410, {**base, "result": "withdrawn", "answer_eligible": False, "version_match": "not_applicable",
                         "tombstone": {"card": stone["card"], "withdrawn_at": stone.get("withdrawn_at", "2026-11-06T12:03:00Z"),
                                       "public_reason": stone.get("public_reason", "withdrawn"),
                                       "library_revision": self.revision}}
        card = self.cards.get(key)
        if card is None:
            return 404, {**base, "result": "not_found", "answer_eligible": False, "version_match": "not_applicable"}
        applies = card_applies(card)
        card_id, card_version, source = card_identity(card)
        match = version_match(applies, product, version)
        if match == "mismatch":
            return 200, {**base, "result": "version_mismatch", "answer_eligible": False, "version_match": "mismatch",
                         "candidates": [{"card": {"id": card_id, "version": card_version}, "applies_to": applies[:3],
                                         "version_match": "mismatch", "source_key": source}]}
        if card.get("schema") == "devtrellis-agent-view":
            # Yönetici kararı (B1): ajan görünümü kart; tazelik kökte, K0 bağlanana dek bilinmiyor;
            # answer_eligible tazelikten bağımsız (sürüm, kapsam, geri çekme).
            return 200, {**base, "result": "found", "answer_eligible": True, "version_match": match, "cards": [card],
                         "freshness": dict(self.lookup_freshness)}
        fresh = card.get("source_freshness", {"state": "fresh"})["state"] == "fresh"
        return 200, {**base, "result": "found", "answer_eligible": fresh, "version_match": match, "cards": [card]}


class FakeHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, status, body, etag=None):
        raw = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        if etag:
            self.send_header("ETag", etag)
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        library = self.server.library
        parts = urlsplit(self.path)
        with library.lock:
            library.calls.append(("GET", parts.path))
        override = library.overrides.get(parts.path) or library.overrides.get("*")
        if override:
            status, body = override
            return self.send(status, body)
        if parts.path == "/api/platform/community/session":
            return self.send(200, {"csrf_token": "a" * 64, "destination": "https://devtrellis.example",
                                   "search_views": ["full", "summary"], "search_modes": ["all", "ranked"]})
        if parts.path in {"/api/platform/community/coverage", "/api/platform/community/topics"}:
            body = library.coverage() if parts.path.endswith("coverage") else library.topics()
            etag = '"' + hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:32] + '"'
            if self.headers.get("If-None-Match") == etag:
                self.send_response(304)
                self.send_header("ETag", etag)
                self.end_headers()
                return None
            return self.send(200, body, etag)
        record = re.fullmatch(r"/api/platform/community/v4-records/([A-Za-z0-9_-]{1,128})", parts.path)
        if record:
            # alpha.14r1: yerel uygulamanın v4 okuma ucu (read_research ve arama kartı); ajan görünümü kütüphanedeki karttan.
            version = parse_qs(parts.query).get("version", [None])[0]
            for card in library.cards.values():
                server = card.get("server") or {}
                if (card.get("schema") == "devtrellis-agent-view" and server.get("id") == record.group(1)
                        and (version is None or str(server.get("version")) == version)):
                    return self.send(200, {"id": server["id"], "version": server["version"], "record_type": "v4",
                                           "kind": server.get("kind"), "human_reviewed": server.get("human_reviewed"),
                                           "publication": {"path": server.get("path")}, "report_url": server.get("link"),
                                           "agent_view": card})
            return self.send(404, {"detail": "Unknown record"})
        match = re.fullmatch(r"/api/platform/community/sources/([0-9a-f]{64})", parts.path)
        if match:
            query = parse_qs(parts.query)
            status, body = library.lookup(match.group(1), query.get("product", [None])[0],
                                          query.get("version", [None])[0])
            etag = '"' + hashlib.sha256(json.dumps([status, body], sort_keys=True).encode()).hexdigest()[:32] + '"'
            if self.headers.get("If-None-Match") == etag:
                with library.lock:
                    library.not_modified += 1
                self.send_response(304)
                self.send_header("ETag", etag)
                self.end_headers()
                return None
            return self.send(status, body, etag)
        return self.send(404, {"detail": "Unknown endpoint"})

    def do_POST(self):
        library = self.server.library
        payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        with library.lock:
            library.calls.append(("POST", self.path))
        if self.path == "/api/platform/community/search" and library.search is not None:
            return self.send(200, library.search(payload))
        return self.send(404, {"detail": "Unknown endpoint"})


def start(library):
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeHandler)
    server.library = library
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}"


def stop(server):
    server.shutdown()
    server.server_close()
