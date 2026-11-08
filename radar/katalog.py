"""Bütün haber havuzunda Türkçe arama yapar ve sonuçları sınırlı sayfalarla döndürür."""

import json

from radar.depo import kart_gorunumu
from radar.konular import KONULAR, konu_kimligi


KISISEL_PUAN_SQL = ("UFUK_PERSONAL_SCORE(i.topic,i.score,COALESCE(e.title_tr,i.title),i.excerpt,"
                    "COALESCE(e.summary_tr,i.summary),COALESCE(e.benefit_tr,i.benefit),"
                    "CASE WHEN e.item_id IS NOT NULL THEN i.title ELSE NULL END)")
KISISEL_SIRA_SQL = (KISISEL_PUAN_SQL + " DESC,i.is_archive ASC,i.published_at DESC,"
                    "i.first_seen_at DESC,i.id ASC")


def arama_metni(value):
    return str(value or "").translate(str.maketrans("Iİ", "ıi")).casefold()


def kisisel_siralama_hazirla(connection):
    """Kaynak ve editör metnini aynı ayarlarla puanlar; üretilmiş özet puanı değiştirmez."""
    from radar.kisisel import kisisel_puan
    settings = json.loads(connection.execute("SELECT settings FROM user_settings WHERE id=1").fetchone()[0])

    def personal_score(topic, score, title, excerpt, summary, benefit, source_title):
        return kisisel_puan({"topic": topic, "score": score, "title": title, "excerpt": excerpt,
                            "summary": summary, "benefit": benefit, "source_title": source_title}, settings)["score"]

    connection.create_function("UFUK_PERSONAL_SCORE", 7, personal_score, deterministic=True)
    return settings


def kartlari_listele(depo, *, query="", topic="all", source="", include_archive=True, limit=100, offset=0,
                    collection_id="", read_status="all", sort="relevance"):
    if (type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int
            or not 0 <= offset <= 1000000 or not isinstance(query, str) or len(query) > 200):
        raise ValueError("Arama veya sayfa sınırı geçersiz.")
    topic = konu_kimligi(topic)
    if topic not in ("all", *KONULAR):
        raise ValueError("Konu filtresi geçersiz.")
    if not isinstance(source, str) or len(source) > 64:
        raise ValueError("Kaynak filtresi geçersiz.")
    if not isinstance(collection_id, str) or len(collection_id) > 128:
        raise ValueError("Koleksiyon filtresi geçersiz.")
    if read_status not in ("all", "read", "unread") or sort not in ("relevance", "newest", "oldest"):
        raise ValueError("Okuma durumu veya sıralama geçersiz.")
    if type(include_archive) is not bool:
        raise ValueError("Arşiv filtresi doğru veya yanlış olmalı.")
    conditions, params = [], []
    if topic != "all":
        conditions.append("i.topic=?")
        params.append(topic)
    if not include_archive:
        conditions.append("i.is_archive=0")
    if source:
        conditions.append("EXISTS(SELECT 1 FROM source_entries se WHERE se.item_id=i.id AND se.source_id=?)")
        params.append(source)
    if collection_id:
        conditions.append("EXISTS(SELECT 1 FROM collection_items ci WHERE ci.item_id=i.id AND ci.collection_id=?)")
        params.append(collection_id)
    if read_status != "all":
        conditions.append("COALESCE(r.is_read,0)=?")
        params.append(int(read_status == "read"))
    if query.strip():
        columns = ["i.title", "i.excerpt", "i.summary", "i.benefit", "e.title_tr", "e.summary_tr",
                   "e.benefit_tr", "a.title_tr", "a.summary_tr", "a.benefit_tr", "w.search_text", "json_extract(s.config,'$.name')"]
        conditions.append("(" + " OR ".join("UFUK_LOWER(" + column + ") LIKE ? ESCAPE '\\'" for column in columns) + ")")
        pattern = "%" + arama_metni(query.strip()).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        params.extend([pattern] * len(columns))
    joins = ("FROM items i JOIN sources s ON s.id=i.source_id "
             "LEFT JOIN editorial e ON e.item_id=i.id AND e.source_revision=i.revision "
             "LEFT JOIN jobs j ON j.item_id=i.id LEFT JOIN item_read_states r ON r.item_id=i.id ")
    if query.strip():
        joins += ("LEFT JOIN current_ai_summaries a ON a.item_id=i.id "
                  "LEFT JOIN current_workflow_text w ON w.item_id=i.id ")
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    with depo.baglanti() as connection:
        connection.create_function("UFUK_LOWER", 1, arama_metni, deterministic=True)
        connection.execute("BEGIN")
        if source and connection.execute("SELECT 1 FROM sources WHERE id=?", (source,)).fetchone() is None:
            raise ValueError("Kaynak bulunamadı.")
        if collection_id and connection.execute("SELECT 1 FROM user_collections WHERE id=?", (collection_id,)).fetchone() is None:
            raise KeyError(collection_id)
        if query.strip():
            from radar.zeka import guncel_ozetler, kaynaklar_guncel
            from radar.proje_profilleri import profil_izi
            from radar.arastirma_talepleri import gorev_profili
            # Geçici tablo yalnız bu bağlantıda yaşar. Aynı revizyon, URL ve gövde
            # doğrulaması hem kart görünümünü hem aranan Türkçe metni belirler.
            summaries = guncel_ozetler(connection)
            connection.execute("CREATE TEMP TABLE current_ai_summaries (item_id TEXT PRIMARY KEY,"
                               "title_tr TEXT,summary_tr TEXT,benefit_tr TEXT)")
            connection.executemany("INSERT INTO current_ai_summaries VALUES(?,?,?,?)",
                                   [(identifier, value["title_tr"], value["summary_tr"], value["benefit_tr"])
                                    for identifier, value in summaries.items()])
            connection.execute("CREATE TEMP TABLE current_workflow_text (item_id TEXT PRIMARY KEY,search_text TEXT)")
            workflow_text = {}
            if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='workflow_tasks'").fetchone():
                reports = connection.execute("SELECT w.* FROM workflow_tasks w JOIN items i ON i.id=w.item_id "
                    "WHERE w.status='ready' AND w.request_id IS NULL "
                    "AND w.source_revision=i.revision AND w.source_url=i.url").fetchall()
                for report in reports:
                    if (report['profile_hash'] != profil_izi(gorev_profili(report, connection))
                            or not kaynaklar_guncel(json.loads(report['sources_json']), connection)):
                        continue
                    value = json.loads(report['result_json'])
                    if value:
                        workflow_text.setdefault(report['item_id'], []).extend(
                            value.get(field, '') for field in ('title_tr', 'summary_tr', 'benefit_tr'))
            # Aynı kartın ön incelemesi ve derin raporu sayfa toplamını çoğaltmaz.
            connection.executemany("INSERT INTO current_workflow_text VALUES(?,?)",
                                   [(identifier, '\n'.join(texts)) for identifier, texts in workflow_text.items()])
        from radar.kisisel import kisisel_puan
        settings = kisisel_siralama_hazirla(connection)
        order = {
            "relevance": KISISEL_SIRA_SQL,
            "newest": "(i.published_at IS NULL) ASC,i.published_at DESC,i.first_seen_at DESC,i.id ASC",
            "oldest": "(i.published_at IS NULL) ASC,i.published_at ASC,i.first_seen_at ASC,i.id ASC",
        }[sort]
        total = connection.execute("SELECT COUNT(*) " + joins + where, params).fetchone()[0]
        rows = connection.execute("SELECT i.*,j.status AS job_status,(j.id IS NOT NULL) AS selected,COALESCE(r.is_read,0) AS is_read " +
                                  joins + where + " ORDER BY " + order + " LIMIT ? OFFSET ?", [*params, limit, offset]).fetchall()
        names = {source["id"]: source["name"] for source in depo.kaynaklar(connection)}
        editorial = {}
        if rows:
            ids = [row["id"] for row in rows]
            editorial = {row["item_id"]: dict(row) for row in connection.execute(
                "SELECT * FROM editorial WHERE item_id IN (" + ",".join("?" for _ in ids) + ")", ids)}
        items = [kart_gorunumu(row, names, editorial) for row in rows]
        from radar.zeka import ozetleri_ekle
        items = ozetleri_ekle(items, connection)
        for item in items:
            item["is_read"] = bool(item["is_read"])
            rank = kisisel_puan(item, settings)
            item.update(personal_score=rank["score"], personal_rank=rank)
    return {"items": items,
            "total": total, "limit": limit, "offset": offset}
