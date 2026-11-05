"""Hazır araştırmaları bulur; yalnız onaylanan anonim önizlemeyi topluluğa gönderir."""

import copy
import hashlib
import json
import re
import secrets
import threading
import time

import httpx

from radar.depo import simdi, tarih
from radar.gelistirici_alani import (
    GelistiriciAlani, KAYIT_ALANLARI, alanlari_dogrula, metin, paket_dogrula, paket_metni,
)
from radar.paylasim_gizliligi import kamu_kopyasi, sorguyu_dogrula


COMMUNITY_URL = "https://devtrellis.org"
NOTICE = ("Yalnız aşağıdaki anonim kamu kopyası, kamusal kaynakları ve model bilgisi gönderilir. "
          "Özgün özel soru, iddia, hesap, API anahtarı, sohbet geçmişi ve ham bağlam eklenmez. "
          "Belirgin özel bilgi kalıpları gizlenir; bu işlem bütün adları veya kişisel olguları tanımaz. "
          "Kamu kopyasındaki başlık ve metnin paylaşılabilir olduğunu inceleme ekranında kontrol edin. "
          "Gönderi incelemeden sonra yayımlanır; model bilgisi doğruluk garantisi değildir.")


class ToplulukBaglantiHatasi(ValueError):
    """Uzak hizmet hatasını yanıt metni veya kimlik bilgisi sızdırmadan bildirir."""


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
        self.lock = threading.RLock()
        self.namespace = hashlib.sha256(str(depo.path.resolve()).encode()).hexdigest()[:24]
        with depo.baglanti(write=True) as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS community_deliveries (
                local_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL, remote_id TEXT, response TEXT, created_at TEXT NOT NULL
            )""")

    def request(self, method, path, *, payload=None, token=None, params=None):
        headers = {"Accept": "application/json", "Accept-Encoding": "identity", "User-Agent": "DevTrellis-community-client"}
        if token:
            headers.update({"X-DevTrellis-Inbox": "1", "Authorization": "Bearer " + token})
        try:
            with httpx.Client(timeout=httpx.Timeout(20, connect=8), follow_redirects=False,
                              trust_env=False, transport=self.transport) as client:
                with client.stream(method, COMMUNITY_URL + path, json=payload, params=params, headers=headers) as response:
                    if response.status_code >= 300:
                        messages = {404: "Topluluk kaydı bulunamadı veya artık yayımlanmıyor.",
                                    409: "Gönderi durumu değişti; durumu yenileyip tekrar deneyin.",
                                    429: "Topluluk gönderim sınırına ulaşıldı; daha sonra tekrar deneyin."}
                        raise ToplulukBaglantiHatasi(messages.get(response.status_code,
                            "Topluluk hizmeti isteği tamamlayamadı; yerel araştırmanız korunuyor."))
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

    @staticmethod
    def identifier(value):
        if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
            raise ValueError("Topluluk kayıt kimliği geçersiz.")
        return value

    def ara(self, payload):
        alanlari_dogrula(payload, {"query", "limit"}, {"query"})
        query = sorguyu_dogrula(metin(payload["query"], "Arama", 200, required=True))
        limit = payload.get("limit", 10)
        if type(limit) is not int or not 1 <= limit <= 10:
            raise ValueError("Arama sınırı 1–10 arasında olmalı.")
        result = self.request("GET", "/api/community/cards", params={"query": query, "kind": "research", "limit": limit})
        items, total = result.get("items"), result.get("total")
        if not isinstance(items, list) or len(items) > limit or type(total) is not int or total < len(items):
            raise ToplulukBaglantiHatasi("Topluluk arama yanıtı geçersiz.")
        return {"items": [self.kart_dogrula(item) for item in items], "total": total,
                "limit": limit, "offset": 0, "destination": COMMUNITY_URL}

    def oku(self, entry_id):
        result = self.request("GET", "/api/community/cards/" + self.identifier(entry_id))
        return self.kart_dogrula(result)

    def kart_dogrula(self, result):
        try:
            if not isinstance(result, dict) or result.get("status") != "published":
                raise ValueError("published card expected")
            return {**self.durum_dogrula(result), "packet": paket_dogrula(result["packet"])}
        except (ValueError, KeyError, TypeError) as exc:
            raise ToplulukBaglantiHatasi("Topluluk kartı geçersiz.") from exc

    def ice_aktar(self, entry_id):
        return self.area.ice_aktar({"packet": self.oku(entry_id)["packet"]})

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
            packet = {"schema": "ufuk-share", "schema_version": 2 if entry.get("provenance") else 1,
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
            connection.execute("UPDATE community_deliveries SET remote_id=?,response=? WHERE local_id=?",
                               (safe["id"], paket_metni(safe), local_id))
        return {"local_id": local_id, "submission": safe, "duplicate": result.get("duplicate") is True}

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

    def listele(self):
        with self.depo.baglanti() as connection:
            rows = connection.execute("SELECT * FROM community_deliveries ORDER BY created_at DESC LIMIT 100").fetchall()
            return {"items": [{"local_id": row["local_id"], "title": row["title"],
                               "submission": json.loads(row["response"]) if row["response"] else None} for row in rows],
                    "destination": COMMUNITY_URL}

    def yonet(self, local_id, payload=None):
        local_id = self.identifier(local_id)
        with self.depo.baglanti() as connection:
            row = connection.execute("SELECT remote_id FROM community_deliveries WHERE local_id=?", (local_id,)).fetchone()
        if row is None or not row["remote_id"]:
            raise ValueError("Gönderimin alındığı henüz doğrulanmadı; aynı önizlemeden yeniden deneyin.")
        token = self.vault.get(self.namespace + ":" + local_id)
        if not token:
            raise ValueError("Bu gönderinin yönetim anahtarı bu bilgisayarın kasasında bulunamadı.")
        path = "/api/community/inbox/" + self.identifier(row["remote_id"])
        if payload is not None:
            alanlari_dogrula(payload, {"expected_version"}, {"expected_version"})
            result = self.request("POST", path + "/withdraw", token=token, payload=payload)
        else:
            result = self.request("GET", path, token=token)
        return self.kaydet(local_id, result)
