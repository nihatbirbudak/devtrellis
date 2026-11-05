"""Sunucu yöneticisinin gözden geçirdiği kamu raporunu dosya özetiyle sabitleyerek yayımlar."""

import hashlib
import json
import re
from pathlib import Path

from radar.gelistirici_alani import metin, paket_dogrula
from radar.paylasim_gizliligi import kamu_kopyasi
from radar.topluluk_depo import ToplulukDeposu


EDITOR_ID = "!devtrellis-editorial"
REVIEWER_ID = "!devtrellis-editorial-review"


def incelenen_dosyayi_yayinla(database, packet_path, expected_sha256, note):
    """Yalnız yerel sunucu dosya yetkisiyle çalışır; web hesabı veya genel paylaşım izni üretmez."""
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_sha256):
        raise ValueError("İncelenen dosyanın SHA256 özeti gerekli.")
    note = metin(note, "Editoryal inceleme notu", 600, required=True, multiline=True)
    path = Path(packet_path)
    if not path.is_file() or path.stat().st_size > 32768:
        raise ValueError("İncelenen paket 32 KiB sınırında bir JSON dosyası olmalı.")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("Dosya incelendikten sonra değişmiş; yeni metni inceleyin.")
    packet = paket_dogrula(json.loads(raw))
    public, redactions = kamu_kopyasi(packet)
    if redactions or public != packet:
        raise ValueError("Dosya anonim kamu kopyası değil; temizlenmiş metni yeniden inceleyin.")
    if packet["kind"] != "research":
        raise ValueError("Editoryal yayın yalnız kaynaklı araştırma kabul eder.")
    database = Path(database)
    if not database.is_file():
        raise ValueError("Mevcut topluluk veritabanı gerekli.")
    store = ToplulukDeposu(database)
    # Kapalı aktörler işlemin iki aşamasını ayırır; iki bağımsız insan incelemesi anlamına gelmez.
    with store.baglanti(write=True) as connection:
        actors = {}
        for actor_id, label, role in ((EDITOR_ID, "DevTrellis editorial", "contributor"),
                                      (REVIEWER_ID, "DevTrellis editorial review", "moderator")):
            connection.execute("INSERT OR IGNORE INTO community_accounts "
                               "(id,username,display_name,role,password_hash,active,created_at) "
                               "VALUES(?,?,?,?,?,0,?)",
                               (actor_id, actor_id, label, role, store.dummy_hash, int(store.clock())))
            actors[actor_id] = dict(connection.execute(
                "SELECT id,username,display_name,role,active FROM community_accounts WHERE id=?", (actor_id,)).fetchone())
            if actors[actor_id]["active"] != 0 or actors[actor_id]["role"] != role:
                raise ValueError("Editoryal aktör beklenen kapalı durumda değil.")
        actor = actors[EDITOR_ID]
    sent = store.gonder(actor, {"packet": packet, "expected_version": 0,
                               "idempotency_key": "editorial-" + expected_sha256})
    with store.baglanti() as connection:
        card = store.kart(connection, sent["card"]["id"])
    if card["status"] == "published":
        return {"card": store.yayini_oku(card["id"]), "duplicate": True}
    if card["status"] != "pending":
        raise ValueError("Bu rapor daha önce geri çekilmiş veya incelemeden kaldırılmış; yeniden yayımlanmadı.")
    store.karar(actors[REVIEWER_ID], card["id"], {"decision": "publish", "expected_version": card["version"], "note": note})
    return {"card": store.yayini_oku(card["id"]), "duplicate": sent["duplicate"]}
