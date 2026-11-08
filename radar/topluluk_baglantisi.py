"""Hazır araştırmaları bulur; yalnız onaylanan anonim önizlemeyi topluluğa gönderir."""

import copy
import hashlib
import json
import re
import secrets
import threading
import time
from urllib.parse import urlencode

import httpx

from radar.arastirma_ozeti import arama_ozeti, paket_ozetini_dogrula
from radar.depo import simdi, tarih
from radar.gelistirici_alani import (
    GelistiriciAlani, KAYIT_ALANLARI, alanlari_dogrula, metin, paket_dogrula, paket_metni, surumu_dogrula,
)
from radar.paylasim_gizliligi import kamu_kopyasi, sorguyu_dogrula
from radar.topluluk_adresi import topluluk_adresi


COMMUNITY_URL = topluluk_adresi()
# K1-V4 sözleşme uçları (r2): yerel uygulama bunları değiştirmeden geçirir; K2 köprüsü yalnız bu üç yolu ister.
SOZLESME_YOLU = re.compile(r"^/api/community/(?:coverage|topics|sources/[0-9a-f]{64})$")
SOZLESME_SEMALARI = {"devtrellis-source-lookup", "devtrellis-coverage", "devtrellis-topics"}
SOZLESME_HATA_KODLARI = {"invalid_request", "unsupported_contract", "rate_limited", "temporarily_unavailable"}
SOZLESME_EN_COK_BAYT = 1024 * 1024
ETAG_BICIMI = re.compile(r'(?:W/)?"[\x21\x23-\x7e]{1,128}"')


def sozlesme_hatasi(kod, retry=None):
    """`devtrellis-contract-error` gövdesi (radar/sema/lookup-coverage.schema.json); sunucudaki `katki_v4.sozlesme_hatasi` ile aynı biçim."""
    return {"schema": "devtrellis-contract-error", "schema_version": 1, "code": kod, "retry_after_seconds": retry}
NOTICE = ("Yalnız aşağıdaki anonim kamu kopyası, kamusal kaynakları ve model bilgisi gönderilir. "
          "Özgün özel soru, iddia, hesap, API anahtarı, sohbet geçmişi ve ham bağlam eklenmez. "
          "Belirgin özel bilgi kalıpları gizlenir; bu işlem bütün adları veya kişisel olguları tanımaz. "
          "Kamu kopyasındaki başlık ve metnin paylaşılabilir olduğunu inceleme ekranında kontrol edin. "
          "Gönderi incelemeden sonra yayımlanır; model bilgisi doğruluk garantisi değildir.")


class ToplulukBaglantiHatasi(ValueError):
    """Uzak hizmet hatasını yanıt metni veya kimlik bilgisi sızdırmadan bildirir.

    `durum` uzak HTTP durum kodudur (yalnız sayı; yanıt metni taşınmaz); ulaşılamama ve geçersiz yanıtta None.
    """

    def __init__(self, message="", durum=None):
        super().__init__(message)
        self.durum = durum


class IsletimSistemiKasasi:
    """Gönderi yönetim anahtarını yalnız işletim sisteminin güvenli kasasında tutar."""

    def backend(self):
        import keyring
        backend = keyring.get_keyring()
        module = type(backend).__module__
        if not module.startswith(("keyring.backends.Windows", "keyring.backends.macOS",
                                  "keyring.backends.SecretService", "keyring.backends.kwallet")):
            raise ValueError("Gönderimi yönetmek için işletim sisteminin güvenli anahtar kasasını etkinleştirin.")
        return backend

    def get(self, name):
        try:
            return self.backend().get_password("devtrellis-community", name)
        except Exception as exc:
            raise ValueError("Gönderi anahtarı için işletim sisteminin güvenli kasasına erişilemiyor.") from exc

    def set(self, name, value):
        try:
            self.backend().set_password("devtrellis-community", name, value)
        except Exception as exc:
            raise ValueError("Gönderi anahtarı güvenli kasaya kaydedilemedi; araştırma gönderilmedi.") from exc


def gizli_kaliplari_temizle(packet):
    """Eski yardımcı adı da aynı sıkı kamu kopyası politikasını uygular."""
    return kamu_kopyasi(packet)


class ToplulukBaglantisi:
    def __init__(self, depo, workflow=None, *, transport=None, vault=None, clock=None):
        self.depo = depo
        self.area = GelistiriciAlani(depo)
        self.workflow = workflow
        self.transport = transport
        self.vault = vault if vault is not None else IsletimSistemiKasasi()
        self.clock = clock or time.time
        self.previews = {}
        self.usage_events = {}
        self.lock = threading.RLock()
        self.namespace = hashlib.sha256(str(depo.path.resolve()).encode()).hexdigest()[:24]
        with depo.baglanti(write=True) as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS community_deliveries (
                local_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL, remote_id TEXT, response TEXT, created_at TEXT NOT NULL
            )""")

    def request(self, method, path, *, payload=None, token=None, params=None, event=False):
        headers = {"Accept": "application/json", "Accept-Encoding": "identity", "User-Agent": "DevTrellis-community-client"}
        if event:
            headers["X-DevTrellis-Client"] = "1"
        if token:
            headers.update({"X-DevTrellis-Inbox": "1", "Authorization": "Bearer " + token})
        try:
            with httpx.Client(timeout=httpx.Timeout(20, connect=8), follow_redirects=False,
                              trust_env=False, transport=self.transport) as client:
                with client.stream(method, COMMUNITY_URL + path, json=payload, params=params, headers=headers) as response:
                    if response.status_code >= 300:
                        messages = {404: "Topluluk kaydı bulunamadı veya artık yayımlanmıyor.",
                                    410: "Topluluk kaydı kaldırıldı veya geri çekildi; eski kopyası kullanılmamalı.",
                                    409: "Gönderi durumu değişti; durumu yenileyip tekrar deneyin.",
                                    429: "Topluluk gönderim sınırına ulaşıldı; daha sonra tekrar deneyin."}
                        raise ToplulukBaglantiHatasi(messages.get(response.status_code,
                            "Topluluk hizmeti isteği tamamlayamadı; yerel araştırmanız korunuyor."), response.status_code)
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise ToplulukBaglantiHatasi("Topluluk yanıtı beklenmeyen sıkıştırma içeriyor.")
                    content = bytearray()
                    for chunk in response.iter_bytes(chunk_size=8192):
                        if len(content) + len(chunk) > 512 * 1024:
                            raise ToplulukBaglantiHatasi("Topluluk yanıtı beklenen sınırı aşıyor.")
                        content.extend(chunk)
                    result = json.loads(content)
                    if not isinstance(result, dict):
                        raise ValueError("object expected")
                    return result
        except ToplulukBaglantiHatasi:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise ToplulukBaglantiHatasi("Topluluk bağlantısı kurulamadı; yerel araştırmanız korunuyor. Yeniden deneyebilirsiniz.") from exc

    def sozlesme_oku(self, path, *, params=None, etag=None):
        """K1-V4 sözleşme uçlarını (bakış, kapsam, konular) değiştirmeden geçirir: (durum, gövde, başlıklar).

        200/404/410 sözleşme gövdeleri ve sözleşme hata gövdeleri aynen döner; 304 gövdesiz, ETag ile. Eski sunucunun
        sözleşmesiz yanıtı `unsupported_contract`, ulaşılamama ve beklenmeyen yanıt `temporarily_unavailable` olur;
        sunucunun kendi hata metni yerel kullanıcıya yansıtılmaz. Hiçbir durumda istisna fırlatmaz (yol dışında).
        """
        if not SOZLESME_YOLU.match(path):
            # İç koruma: yönlendirici yalnız sabit üç yolu verir; kullanıcıya gösterilen bir ileti değildir.
            raise ValueError("Unsupported contract path.")
        headers = {"Accept": "application/json", "Accept-Encoding": "identity", "User-Agent": "DevTrellis-community-client"}
        if isinstance(etag, str) and ETAG_BICIMI.fullmatch(etag):
            headers["If-None-Match"] = etag
        gecici = (503, sozlesme_hatasi("temporarily_unavailable"), {})
        try:
            with httpx.Client(timeout=httpx.Timeout(20, connect=8), follow_redirects=False,
                              trust_env=False, transport=self.transport) as client:
                with client.stream("GET", COMMUNITY_URL + path, params=params or None, headers=headers) as response:
                    status = response.status_code
                    gecen = {name: response.headers[name] for name in ("etag", "cache-control") if name in response.headers}
                    if status == 304:
                        return 304, None, gecen
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        return gecici
                    content = bytearray()
                    for chunk in response.iter_bytes(chunk_size=8192):
                        if len(content) + len(chunk) > SOZLESME_EN_COK_BAYT:
                            return gecici
                        content.extend(chunk)
                    json_mu = response.headers.get("content-type", "").split(";", 1)[0].strip() == "application/json"
        except httpx.HTTPError:
            return gecici
        try:
            govde = json.loads(content) if json_mu else None
        except ValueError:
            govde = None
        if isinstance(govde, dict) and govde.get("schema") == "devtrellis-contract-error":
            kod = govde.get("code") if govde.get("code") in SOZLESME_HATA_KODLARI else "temporarily_unavailable"
            bekleme = govde.get("retry_after_seconds")
            return (status if 400 <= status < 600 else 503,
                    sozlesme_hatasi(kod, bekleme if type(bekleme) is int and bekleme >= 0 else None), gecen)
        if status in (200, 404, 410) and isinstance(govde, dict) and govde.get("schema") in SOZLESME_SEMALARI:
            return status, govde, gecen
        if status in (404, 406):
            return 404, sozlesme_hatasi("unsupported_contract"), {}
        if status == 429:
            return 429, sozlesme_hatasi("rate_limited"), {}
        if status in (400, 422):
            return 400, sozlesme_hatasi("invalid_request"), {}
        return gecici

    @staticmethod
    def identifier(value):
        if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
            raise ValueError("Topluluk kayıt kimliği geçersiz.")
        return value

    def ara(self, payload):
        alanlari_dogrula(payload, {"query", "limit", "view", "search_mode"}, {"query"})
        view = payload.get("view", "full")
        if not isinstance(view, str) or view not in {"full", "summary"}:
            raise ValueError("Arama görünümü full veya summary olmalı.")
        mode = payload.get("search_mode", "all")
        if not isinstance(mode, str) or mode not in {"all", "ranked"}:
            raise ValueError("Arama yöntemi all veya ranked olmalı.")
        query = sorguyu_dogrula(metin(payload["query"], "Arama", 200, required=True))
        limit = payload.get("limit", 10)
        if type(limit) is not int or not 1 <= limit <= 10:
            raise ValueError("Arama sınırı 1–10 arasında olmalı.")
        params = {"query": query, "kind": "research", "limit": limit}
        if view == "summary":
            params["view"] = view
        if mode == "ranked":
            params["search_mode"] = mode
        result = self.request("GET", "/api/community/cards", params=params)
        remote_view = result.get("view", "full")
        if (not isinstance(remote_view, str) or remote_view not in {"full", "summary"}
                or (view == "full" and remote_view != "full")):
            raise ToplulukBaglantiHatasi("Topluluk arama görünümü geçersiz.")
        items, total = result.get("items"), result.get("total")
        if not isinstance(items, list) or len(items) > limit or type(total) is not int or total < len(items):
            raise ToplulukBaglantiHatasi("Topluluk arama yanıtı geçersiz.")
        response = {"items": [self.kart_dogrula(item, summary=remote_view == "summary") for item in items], "total": total,
                    "limit": limit, "offset": 0, "destination": COMMUNITY_URL}
        if mode == "ranked":
            search = result.get("search", {"mode": "all", "fallback": True})
            if (not isinstance(search, dict) or search.get("mode") not in ("all", "ranked")
                    or type(search.get("fallback")) is not bool
                    or search["fallback"] != (search["mode"] == "all")):
                raise ToplulukBaglantiHatasi("Topluluk arama yöntemi yanıtı geçersiz.")
            if search["mode"] == "ranked" and any("search_match" not in item for item in response["items"]):
                raise ToplulukBaglantiHatasi("Topluluk arama kapsamı eksik.")
            response["search"] = {"mode": search["mode"], "fallback": search["fallback"]}
        if "library" in result:
            library = result["library"]
            amount = library.get("published_research_total") if isinstance(library, dict) else None
            if type(amount) is not int or not 0 <= amount <= 9223372036854775807 or amount < total:
                raise ToplulukBaglantiHatasi("Topluluk kütüphane sayımı geçersiz.")
            response["library"] = {"published_research_total": amount}
        # Eski sunucu görünümü yok sayıp tam paket döndürebilir; önce tam
        # doğrulamasını yap, yalnız istemcinin istediği özet yanıtını daralt.
        return arama_ozeti(response) if view == "summary" else response

    def oku(self, entry_id, version=None):
        if version is not None:
            surumu_dogrula(version)
            if version == 0:
                raise ValueError("Araştırma sürümü pozitif olmalı.")
        try:
            result = self.request("GET", "/api/community/cards/" + self.identifier(entry_id),
                                  params=None if version is None else {"version": version})
        except ToplulukBaglantiHatasi as exc:
            # alpha.12 K2: K1 v4 kayıtları (makine kapısından yayımlanan kart, rapor, konu özeti) v1 kart ucunda yoktur
            # (404); kamu kaydı `/api/community/v4/records/...` ucundadır. Eskiden 404 yerel uçta 502 olarak dönüyordu.
            if exc.durum != 404:
                raise
            return self.v4_oku(entry_id, version)
        card = self.kart_dogrula(result)
        if version is not None and card["version"] != version:
            raise ToplulukBaglantiHatasi("İstenen araştırma sürümü alınamadı.")
        return card

    def v4_yolu(self, entry_id, version=None):
        return ("/api/community/v4/records/" + self.identifier(entry_id)
                + ("" if version is None else "/v/" + str(version)))

    def v4_oku(self, entry_id, version=None):
        """K1 v4 kamu kaydını okur; ajana sunucunun ajan görünümüyle (`server` / `declared` / `untrusted`) verilir.

        Görünüm sunucudaki `ajan_gorunumu` ile aynı işlevden üretilir (adresle bulmadaki kartla aynı biçim). Kimlik ve
        sürüm istenenle eşleşmeli; kayıt alanları `v4_alanlarini_dogrula` ile denetlenir.
        """
        from radar.katki_v4 import ajan_gorunumu

        record = self.request("GET", self.v4_yolu(entry_id, version))
        try:
            publication = record["publication"]
            if (record.get("id") != entry_id or type(record.get("version")) is not int or record["version"] < 1
                    or (version is not None and record["version"] != version) or not isinstance(publication, dict)):
                raise ValueError("v4 kayıt kimliği geçersiz.")
            gate = publication.get("gate") if isinstance(publication.get("gate"), dict) else {}
            fields = self.v4_alanlarini_dogrula({**record, "human_reviewed": publication.get("human_reviewed"),
                                                 "publication": {**publication, "gate_version": gate.get("version")}})
            view = ajan_gorunumu(record)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise ToplulukBaglantiHatasi("Topluluk kartı geçersiz.") from exc
        return {"id": entry_id, "version": record["version"], **fields,
                "report_url": f"{COMMUNITY_URL}/api/community/v4/records/{entry_id}/v/{record['version']}",
                "agent_view": view}

    def mcp_oku(self, entry_id, version=None):
        card = self.oku(entry_id, version)
        if card.get("record_type") == "v4":
            # v4 kayıtlarında okuma/kullanım sayacı yok (sunucuda olay ucu v1 kartları içindir).
            card["access_counted"] = False
            return card
        try:
            self.olay_bildir(entry_id, card["version"], "mcp_read", secrets.token_hex(16))
        except (ValueError, ToplulukBaglantiHatasi):
            card["access_counted"] = False
        else:
            card["access_counted"] = True
        return card

    def olay_bildir(self, entry_id, version, kind, event_id):
        result = self.request("POST", "/api/community/cards/" + self.identifier(entry_id) + "/events",
                              payload={"kind": kind, "version": version, "event_id": event_id}, event=True)
        return {"engagement": self.etkilesimi_dogrula(result.get("engagement")),
                "duplicate": result.get("duplicate") is True}

    def kullanimi_bildir(self, entry_id, payload):
        entry_id = self.identifier(entry_id)
        alanlari_dogrula(payload, {"version"}, {"version"})
        surumu_dogrula(payload["version"])
        if payload["version"] == 0:
            raise ValueError("Araştırma sürümü pozitif olmalı.")
        identity = (entry_id, payload["version"])
        with self.lock:
            if identity not in self.usage_events:
                if len(self.usage_events) >= 1000:
                    raise ValueError("Bu oturumun kullanım bildirimi sınırına ulaşıldı.")
                self.usage_events[identity] = secrets.token_hex(16)
            event_id = self.usage_events[identity]
        try:
            return self.olay_bildir(entry_id, payload["version"], "used_in_research", event_id)
        except ToplulukBaglantiHatasi as exc:
            # alpha.12 K2: v4 kaydının olay ucu yok (404). Kayıt yayındaysa bu bir hata değil, sayılmayan kullanımdır.
            if exc.durum != 404:
                raise
            self.request("GET", self.v4_yolu(entry_id, payload["version"]))
            return {"recorded": False, "record_type": "v4", "reason": "usage_not_counted_for_v4_records"}

    @staticmethod
    def etkilesimi_dogrula(value):
        fields = {"web_views", "mcp_reads", "helpful", "used_in_research", "needs_update", "disputed"}
        if not isinstance(value, dict) or set(value) != fields:
            raise ValueError("Araştırma kullanım sayımları geçersiz.")
        if any(type(number) is not int or not 0 <= number <= 9223372036854775807 for number in value.values()):
            raise ValueError("Araştırma kullanım sayımları geçersiz.")
        return dict(value)

    @staticmethod
    def arama_eslesmesini_dogrula(value):
        if not isinstance(value, dict) or set(value) != {"matched_terms", "missing_terms", "all_terms_matched", "answer_sufficiency"}:
            raise ValueError("Arama kapsamı geçersiz.")
        terms = {}
        for field in ("matched_terms", "missing_terms"):
            if not isinstance(value[field], list) or len(value[field]) > 24:
                raise ValueError("Arama terimleri geçersiz.")
            terms[field] = [metin(word, "Arama terimi", 200, required=True) for word in value[field]]
        combined = terms["matched_terms"] + terms["missing_terms"]
        if (not 1 <= len(combined) <= 24 or len(set(combined)) != len(combined)
                or type(value["all_terms_matched"]) is not bool
                or value["all_terms_matched"] != (not terms["missing_terms"])
                or value["answer_sufficiency"] != "not_assessed"):
            raise ValueError("Arama kapsamı tutarsız.")
        return {**terms, "all_terms_matched": value["all_terms_matched"], "answer_sufficiency": "not_assessed"}

    @staticmethod
    def kamu_duzeltmelerini_dogrula(value, research_id, version):
        # Kimlik/sürüm üst karttan gelir; uzak sunucunun ek özel alanı taşınmaz.
        fields = {"total", "offset", "limit", "items"}
        if not isinstance(value, dict) or set(value) != fields:
            raise ValueError("Kamu düzeltme özeti geçersiz.")
        for key, low, high in (("total", 0, 9223372036854775807), ("offset", 0, 0), ("limit", 1, 3)):
            if type(value[key]) is not int or not low <= value[key] <= high:
                raise ValueError("Düzeltme sayfalama bilgisi geçersiz.")
        if not isinstance(value["items"], list) or len(value["items"]) != min(value["limit"], value["total"]):
            raise ValueError("Düzeltme listesi eksik veya sınırı aşıyor.")
        items, identifiers = [], set()
        for item in value["items"]:
            if not isinstance(item, dict) or set(item) != {"id", "summary", "withdrawn", "correction", "reviewed_at"}:
                raise ValueError("Kamu düzeltmesi özel veya beklenmeyen alan içeriyor.")
            identifier = item["id"]
            if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{32}", identifier) or identifier in identifiers:
                raise ValueError("Düzeltme kimliği geçersiz.")
            identifiers.add(identifier)
            correction = item["correction"]
            if (not isinstance(correction, dict) or set(correction) != {"research_id", "version"}
                    or not isinstance(correction["research_id"], str) or not re.fullmatch(r"[a-f0-9]{32}", correction["research_id"])
                    or type(correction["version"]) is not int or not 1 <= correction["version"] <= 9223372036854775807
                    or type(item["withdrawn"]) is not bool):
                raise ValueError("Düzeltmenin tam sürümü geçersiz.")
            stamp = metin(item["reviewed_at"], "İnceleme tarihi", 40, required=True)
            if tarih(stamp) is None:
                raise ValueError("Düzeltme tarihi geçersiz.")
            items.append({"id": identifier, "summary": sorguyu_dogrula(metin(item["summary"], "Kamu düzeltmesi", 600, required=True, multiline=True)),
                          "withdrawn": item["withdrawn"], "correction": dict(correction), "reviewed_at": stamp,
                          "report_url": COMMUNITY_URL + "/?" + urlencode({"card": correction["research_id"], "version": correction["version"]})})
        return {**value, "research_id": research_id, "version": version, "items": items,
                "more_available": value["total"] > len(items)}

    def kart_dogrula(self, result, *, summary=False):
        try:
            if not isinstance(result, dict) or result.get("status") != "published":
                raise ValueError("published card expected")
            packet = paket_ozetini_dogrula(result["packet"]) if summary else paket_dogrula(result["packet"])
            card = {**self.durum_dogrula(result), "packet": packet}
            if "search_match" in result:
                card["search_match"] = self.arama_eslesmesini_dogrula(result["search_match"])
            if "engagement" in result:
                card["engagement"] = self.etkilesimi_dogrula(result["engagement"])
            if "relationships" in result:
                relations = result["relationships"]
                if not isinstance(relations, dict) or not isinstance(relations.get("parents"), list) or not isinstance(relations.get("children"), list):
                    raise ValueError("Araştırma ilişkileri geçersiz.")
                if len(relations["parents"]) > 5 or len(relations["children"]) > 100:
                    raise ValueError("Araştırma ilişkileri sınırı aşıldı.")
                safe = {"parents": [], "children": [], "children_total": relations.get("children_total")}
                if type(safe["children_total"]) is not int or safe["children_total"] < len(relations["children"]):
                    raise ValueError("Araştırma ilişkileri sayısı geçersiz.")
                for group in ("parents", "children"):
                    for item in relations[group]:
                        entry = {"id": self.identifier(item["id"]), "version": item["version"]}
                        surumu_dogrula(entry["version"])
                        if entry["version"] == 0:
                            raise ValueError("Kaynak sürümü geçersiz.")
                        if group == "parents":
                            if type(item.get("available")) is not bool:
                                raise ValueError("Kaynak görünürlüğü geçersiz.")
                            entry["available"] = item["available"]
                        if group == "children" or entry["available"]:
                            entry["title"] = metin(item["title"], "Araştırma başlığı", 160, required=True)
                            for field in ("author", "license"):
                                if field in item:
                                    entry[field] = metin(item[field], field, 100)
                        else:
                            entry["title"] = None
                        safe[group].append(entry)
                card["relationships"] = safe
            if "public_corrections" in result:
                card["public_corrections"] = self.kamu_duzeltmelerini_dogrula(result["public_corrections"], card["id"], card["version"])
            # Uzak adres iddiasını kullanma; bağlantı doğrulanmış kaydın tam sürümünü açar.
            card["report_url"] = COMMUNITY_URL + "/?" + urlencode({"card": card["id"], "version": card["version"]})
            if result.get("record_type") == "v4":
                # K1 v4 kaydı (makine kapısı): tür, yayın yolu ve etiket doğrulanıp taşınır; bağlantı yerelde kurulur.
                card.update(self.v4_alanlarini_dogrula(result))
                card["report_url"] = f"{COMMUNITY_URL}/api/community/v4/records/{card['id']}/v/{card['version']}"
            return card
        except (ValueError, KeyError, TypeError) as exc:
            raise ToplulukBaglantiHatasi("Topluluk kartı geçersiz.") from exc

    def ice_aktar(self, entry_id):
        card = self.oku(entry_id)
        if "packet" not in card:
            raise ValueError("Bu topluluk kaydı (v4) araştırma paketi taşımaz; içe aktarılamaz.")
        return self.area.ice_aktar({"packet": card["packet"]})

    def onizle(self, payload):
        allowed = {"task_id", "entry_id", "packet", "author", "title", "body", "summary", "question", "claim_text"}
        alanlari_dogrula(payload, allowed)
        if sum(key in payload for key in ("task_id", "entry_id", "packet")) != 1:
            raise ValueError("Bir araştırma, yerel kayıt veya paylaşım metni seçin.")
        if "packet" in payload:
            packet = paket_dogrula(payload["packet"])
        else:
            if "task_id" in payload:
                if self.workflow is None:
                    raise ValueError("Araştırma hizmeti hazır değil.")
                task = self.workflow.oku(metin(payload["task_id"], "Araştırma", 120, required=True))
                entry = self.area.arastirmadan_taslak(task)["entry"]
            else:
                entry = self.area.oku(metin(payload["entry_id"], "Kayıt", 120, required=True))
            if entry["origin"] != "local":
                raise ValueError("İçe alınan araştırmanın yazarını değiştiremezsiniz. Kaynağı koruyarak yeni bir araştırma hazırlayın.")
            packet = {"schema": "ufuk-share", "schema_version": 3 if entry.get("lineage") else 2 if entry.get("provenance") else 1,
                      **{key: entry[key] for key in KAYIT_ALANLARI if key in entry},
                      "created_at": entry["created_at"], "updated_at": entry["updated_at"]}
        packet = copy.deepcopy(packet)
        packet["author"] = "Anonymous contributor"
        if "packet" not in payload:
            packet["license"] = "CC-BY-4.0"
        changed = False
        for field, limit in (("title", 160), ("body", 12000), ("summary", 600)):
            if field in payload:
                edited = metin(payload[field], field, limit, required=field == "title", multiline=field != "title")
                changed = changed or edited != packet[field]
                packet[field] = edited
        for field, limit in (("question", 500), ("claim_text", 1000)):
            if field in payload:
                if not packet.get("provenance"):
                    raise ValueError("Bu kayıtta düzenlenecek araştırma sorusu bulunmuyor.")
                edited = metin(payload[field], field, limit, required=field == "question", multiline=field == "claim_text")
                changed = changed or edited != packet["provenance"].get(field, "")
                packet["provenance"][field] = edited
        if changed and packet.get("provenance"):
            packet["provenance"].update(modified=True, evidence_status="unverified")
        packet, redactions = gizli_kaliplari_temizle(packet)
        packet = paket_dogrula(packet)
        with self.lock:
            self.previews = {key: value for key, value in self.previews.items() if value["expires_at"] > self.clock()}
            if len(self.previews) >= 100:
                self.previews.pop(next(iter(self.previews)))
            preview_id = secrets.token_urlsafe(24)
            self.previews[preview_id] = {"expires_at": self.clock() + 1800, "packet": copy.deepcopy(packet),
                                         "approval_hash": None, "result": None, "delivery_attempted": False}
        return {"preview_id": preview_id, "packet": packet, "destination": COMMUNITY_URL,
                "notice": NOTICE, "redactions": redactions}

    def _onizlemeyi_oku(self, preview_id):
        preview_id = metin(preview_id, "Önizleme", 100, required=True)
        preview = self.previews.get(preview_id)
        if not preview or preview["expires_at"] <= self.clock():
            self.previews.pop(preview_id, None)
            raise ValueError("Önizleme geçersiz veya süresi doldu; metni yeniden önizleyin.")
        return preview

    @staticmethod
    def _onay_izi(preview_id, packet, token):
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            raise ValueError("İnceleme ekranından alınan geçerli onay anahtarı gerekli.")
        digest = hashlib.sha256(paket_metni(packet).encode()).hexdigest()
        return hashlib.sha256((preview_id + ":" + digest + ":" + token).encode()).hexdigest()

    def inceleme_ac(self, preview_id):
        with self.lock:
            preview = self._onizlemeyi_oku(preview_id)
            if preview["result"] is not None:
                raise ValueError("Bu önizleme gönderildi; durumu gönderimler listesinden kontrol edin.")
            token = secrets.token_urlsafe(32)
            preview["approval_hash"] = self._onay_izi(preview_id, preview["packet"], token)
            return {"preview_id": preview_id, "packet": copy.deepcopy(preview["packet"]), "destination": COMMUNITY_URL,
                    "notice": NOTICE, "approval_token": token}

    def _onayi_dogrula(self, preview_id, token):
        preview = self._onizlemeyi_oku(preview_id)
        actual = self._onay_izi(preview_id, preview["packet"], token)
        if not preview["approval_hash"] or not secrets.compare_digest(actual, preview["approval_hash"]):
            raise ValueError("Onay bu önizlemeye ait değil veya inceleme yenilenmiş; incelemeyi yeniden açın.")
        return preview

    def vazgec(self, payload):
        alanlari_dogrula(payload, {"preview_id", "approval_token"}, {"preview_id", "approval_token"})
        with self.lock:
            preview = self._onayi_dogrula(payload["preview_id"], payload["approval_token"])
            if preview["delivery_attempted"]:
                raise ValueError("Teslim denenmiş; yerelde kaldığı söylenemez. Önce gönderim sonucunu doğrulayın veya geri çekin.")
            del self.previews[payload["preview_id"]]
            return {"status": "kept_local"}

    def gonder(self, payload):
        fields = {"preview_id", "consent", "reviewed_public", "approval_token"}
        alanlari_dogrula(payload, fields, fields)
        if payload["consent"] is not True or payload["reviewed_public"] is not True:
            raise ValueError("Gösterilen araştırmayı paylaşmak için açık onay gerekli.")
        preview_id = metin(payload["preview_id"], "Önizleme", 100, required=True)
        with self.lock:
            preview = self._onayi_dogrula(preview_id, payload["approval_token"])
            if preview["result"] is not None:
                return {**copy.deepcopy(preview["result"]), "duplicate": True}
            packet = preview["packet"]
            fingerprint = hashlib.sha256(paket_metni(packet).encode()).hexdigest()
            local_id = fingerprint[:32]
            account = self.namespace + ":" + local_id
            # Aynı veri dizinini kullanan farklı MCP süreçleri de tek yönetim anahtarını seçer.
            with self.depo.baglanti(write=True) as connection:
                token = self.vault.get(account)
                if token is None:
                    token = secrets.token_urlsafe(32)
                    self.vault.set(account, token)
                    if self.vault.get(account) != token:
                        raise ValueError("Gönderi anahtarı güvenli kasada doğrulanamadı; araştırma gönderilmedi.")
                connection.execute("INSERT OR IGNORE INTO community_deliveries(local_id,fingerprint,title,created_at) VALUES(?,?,?,?)",
                                   (local_id, fingerprint, packet["title"], simdi()))
            # Ağ beklemesi ortak SQLite yazma kilidini tutmaz.
            preview["delivery_attempted"] = True
            result = self.request("POST", "/api/community/inbox", token=token,
                                  payload={"packet": packet, "consent": True, "idempotency_key": fingerprint})
            saved = self.kaydet(local_id, result)
            preview["result"] = copy.deepcopy(saved)
            return saved

    def kaydet(self, local_id, result):
        submission = result.get("submission", result)
        safe = self.durum_dogrula(submission)
        with self.depo.baglanti(write=True) as connection:
            previous = connection.execute("SELECT remote_id,response FROM community_deliveries WHERE local_id=?",
                                          (local_id,)).fetchone()
            if previous is None or (previous["remote_id"] and previous["remote_id"] != safe["id"]):
                raise ToplulukBaglantiHatasi("Topluluk yanıtı seçilen gönderimle eşleşmiyor.")
            if previous["response"]:
                current = self.durum_dogrula(json.loads(previous["response"]))
                # Farklı oturumlardan gelen gecikmiş yanıtlar yeni geri çekme durumunu geri alamaz.
                if current["version"] > safe["version"]:
                    safe = current
            connection.execute("UPDATE community_deliveries SET remote_id=?,response=? WHERE local_id=?",
                               (safe["id"], paket_metni(safe), local_id))
        return {"local_id": local_id, "submission": safe, "duplicate": result.get("duplicate") is True}

    @staticmethod
    def v4_alanlarini_dogrula(result):
        publication = result.get("publication")
        if (result.get("kind") not in {"source_card", "research_report", "topic_summary"} or type(result.get("human_reviewed")) is not bool
                or not isinstance(publication, dict) or publication.get("path") not in {"machine_gate", "moderator"}
                or publication.get("human_reviewed") != result["human_reviewed"] or not isinstance(publication.get("label"), dict)):
            raise ValueError("v4 kayıt alanları geçersiz.")
        label = {lang: metin(publication["label"].get(lang) or "", "Kanıt etiketi", 1000) for lang in ("tr", "en")}
        if publication["path"] == "machine_gate":
            # Boş etiket kabul edilir ama "insan incelemedi" bilgisi kaybolmaz (alpha.11 yeniden sınama, kalan 1).
            label = {"tr": label["tr"] or "İnsan incelemedi", "en": label["en"] or "Not human-reviewed"}
        return {"record_type": "v4", "kind": result["kind"], "human_reviewed": result["human_reviewed"],
                "publication": {"path": publication["path"], "human_reviewed": result["human_reviewed"], "label": label,
                                "policy_version": metin(publication.get("policy_version") or "", "Yayın politikası", 80),
                                "gate_version": metin(publication.get("gate_version") or "", "Kapı sürümü", 80)}}

    def durum_dogrula(self, submission):
        try:
            if not isinstance(submission, dict):
                raise ValueError("object expected")
            safe = {"id": self.identifier(submission["id"])}
            if submission.get("status") not in {"pending", "published", "rejected", "withdrawn", "hidden"}:
                raise ValueError("status expected")
            safe["status"] = submission["status"]
            for key in ("version", "published_version"):
                value = submission.get(key)
                if key == "published_version" and value is None:
                    safe[key] = None
                elif type(value) is not int or not 1 <= value <= 9223372036854775807:
                    raise ValueError("version expected")
                else:
                    safe[key] = value
            for key in ("created_at", "updated_at"):
                stamp = metin(submission[key], "Tarih", 40, required=True)
                if tarih(stamp) is None:
                    raise ValueError("timestamp expected")
                safe[key] = stamp
            safe["moderation_note"] = metin(submission.get("moderation_note", ""), "İnceleme notu", 2000, multiline=True)
            return safe
        except (ValueError, KeyError, TypeError) as exc:
            raise ToplulukBaglantiHatasi("Topluluk gönderim yanıtı geçersiz.") from exc

    def listele(self, *, offset=0, limit=25):
        if type(offset) is not int or not 0 <= offset <= 9223372036854775807:
            raise ValueError("Sayfa başlangıcı geçersiz.")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Sayfa sınırı 1–100 olmalı.")
        with self.depo.baglanti() as connection:
            connection.execute("BEGIN")
            total = connection.execute("SELECT COUNT(*) FROM community_deliveries").fetchone()[0]
            rows = connection.execute("SELECT * FROM community_deliveries ORDER BY created_at DESC,local_id LIMIT ? OFFSET ?",
                                      (limit, offset)).fetchall()
            return {"items": [{"local_id": row["local_id"], "title": row["title"],
                               "submission": json.loads(row["response"]) if row["response"] else None} for row in rows],
                    "destination": COMMUNITY_URL, "total": total, "offset": offset, "limit": limit}

    def yonet(self, local_id, payload=None):
        local_id = self.identifier(local_id)
        if payload is not None:
            alanlari_dogrula(payload, {"expected_version"}, {"expected_version"})
            surumu_dogrula(payload["expected_version"])
            if payload["expected_version"] == 0:
                raise ValueError("Gönderi sürümü pozitif tam sayı olmalı.")
        with self.depo.baglanti() as connection:
            row = connection.execute("SELECT remote_id FROM community_deliveries WHERE local_id=?", (local_id,)).fetchone()
        if row is None:
            raise ValueError("Yerel gönderim kaydı bulunamadı.")
        if not row["remote_id"] and payload is not None:
            raise ValueError("Gönderimin alındığı henüz doğrulanmadı; önce durumunu yenileyin.")
        token = self.vault.get(self.namespace + ":" + local_id)
        if not token:
            raise ValueError("Bu gönderinin yönetim anahtarı bu bilgisayarın kasasında bulunamadı.")
        if not row["remote_id"]:
            # Başarılı teslimin yanıtı kaybolmuşsa kaydı yalnız mevcut yönetim anahtarıyla bul.
            # Bu salt okuma, oturum kapanmış olsa da raporu tekrar göndermez.
            return self.kaydet(local_id, self.request("GET", "/api/community/inbox/status", token=token))
        path = "/api/community/inbox/" + self.identifier(row["remote_id"])
        if payload is not None:
            result = self.request("POST", path + "/withdraw", token=token, payload=payload)
        else:
            result = self.request("GET", path, token=token)
        return self.kaydet(local_id, result)
