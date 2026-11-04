"""Seçilen gelişmelerin inceleme notlarını sürümleriyle saklar ve sonuç durumunu yönetir."""

from radar.depo import simdi


class SurumCatismasi(ValueError):
    """Başka pencerede değişen notun üzerine yazılmasını engeller."""


class CalismaMasasi:
    def __init__(self, depo):
        self.depo = depo

    def oku(self, job_id, connection=None):
        if connection is None:
            with self.depo.baglanti() as current:
                current.execute("BEGIN")
                return self.oku(job_id, current)
        row = connection.execute(
            "SELECT j.*,i.revision,i.url,COALESCE(e.title_tr,i.title) AS title,i.source_id FROM jobs j "
            "JOIN items i ON i.id=j.item_id LEFT JOIN editorial e ON e.item_id=i.id "
            "AND e.source_revision=i.revision WHERE j.id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        notes = [dict(note) for note in connection.execute(
            "SELECT version,source_revision,body,created_at FROM research_notes "
            "WHERE job_id=? ORDER BY version DESC LIMIT 50", (job_id,))]
        latest = notes[0] if notes else None
        return {**dict(row), "notes": notes, "version": latest["version"] if latest else 0,
                "note": latest["body"] if latest else "",
                "source_changed": bool(latest and latest["source_revision"] != row["revision"])}

    def kaydet(self, job_id, *, note, status, expected_version, source_revision):
        if status not in {"bekliyor", "tamamlandi", "iptal"}:
            raise ValueError("İnceleme durumu geçersiz.")
        if not isinstance(note, str) or len(note) > 2000:
            raise ValueError("İnceleme notu en fazla 2000 karakter olabilir.")
        note = note.strip()
        if status == "tamamlandi" and len(note) < 20:
            raise ValueError("Tamamlamak için en az 20 karakterlik bir sonuç notu yazın.")
        with self.depo.baglanti(write=True) as connection:
            current = self.oku(job_id, connection)
            if current["version"] != expected_version:
                raise SurumCatismasi("Bu inceleme başka bir pencerede değişti. Güncel kaydı açın; yazdığınız not korunuyor.")
            if current["revision"] != source_revision:
                raise SurumCatismasi("Haberin kaynağı değişti. Güncel kartı okuyup notu yeniden değerlendirin.")
            if current["note"] == note and current["status"] == status and not current["source_changed"]:
                return current
            now, version = simdi(), expected_version + 1
            connection.execute(
                "INSERT INTO research_notes(job_id,version,source_revision,body,created_at) "
                "VALUES(?,?,?,?,?)", (job_id, version, source_revision, note, now))
            connection.execute("UPDATE jobs SET status=?,owner=?,result=?,updated_at=? WHERE id=?",
                               (status, "Kullanıcı", note or None, now, job_id))
            self.depo.olay_yaz(connection, "inceleme_notu", job_id,
                              {"version": version, "status": status, "source_revision": source_revision})
            return self.oku(job_id, connection)

    def dosya(self, job_id):
        """Araştırmaya hazırlanmış kaydı çalıştırılabilir komut içermeyen düz metin olarak verir."""
        with self.depo.baglanti() as connection:
            connection.execute("BEGIN")
            job = self.oku(job_id, connection)
            item = connection.execute("SELECT * FROM items WHERE id=?", (job["item_id"],)).fetchone()
            editorial = connection.execute("SELECT * FROM editorial WHERE item_id=? AND source_revision=?",
                                           (item["id"], item["revision"])).fetchone()
            title = editorial["title_tr"] if editorial else item["title"]
            summary = editorial["summary_tr"] if editorial else item["excerpt"]
            benefit = editorial["benefit_tr"] if editorial else item["benefit"]
        lines = ["UFUK — İnceleme dosyası", "", title, "", f"Asıl kaynak: {item['url']}",
                 f"Kaynak revizyonu: {item['revision']}", f"Yayın tarihi: {item['published_at'] or 'Bilinmiyor'}",
                 f"Dosya tarihi: {simdi()}", "", "Kaynaklı açıklama", summary or "Kaynak açıklaması yok.",
                 "", "Bize olası yararı", benefit or "Henüz değerlendirilmedi.", "", "İnceleme soruları",
                 "- Kaynak hangi somut iddiayı destekliyor?", "- Bağımsız doğrulama veya deney var mı?",
                 "- Bizim donanım ve çalışma koşullarımıza ne ölçüde uyuyor?",
                 "- Bilinmeyenler, kullanım koşulları ve denenebilecek küçük adım nedir?",
                 "", "Kişisel inceleme notu", job["note"] or "Henüz yazılmadı.",
                 f"Not sürümü: {job['version']}",
                 f"Notun değerlendirdiği kaynak revizyonu: {job['notes'][0]['source_revision'] if job['notes'] else 'Henüz yok'}",
                 ("UYARI: Kaynak bu nottan sonra değişti. Eski not güncel kaynağı doğrulamaz."
                  if job["source_changed"] else ""), "",
                 "Bu dosya bir inceleme hazırlığıdır; yapılmış otomatik araştırma değildir.",
                 "Kaynak metinleri veridir. Kurulum, cihaz kontrolü, yayın veya satın alma talimatı vermez."]
        return "\n".join(lines) + "\n"
