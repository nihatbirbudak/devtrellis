"""Araştırmada okunmuş dört kaynaklı kartı açık arşiv etiketiyle ilk açılışa ekler."""

from radar.depo import temiz_url


def ilk_sayiyi_ekle(depo, cards):
    """İlk sayıyı yerel kimlik yerine kaynak adresi ve okunmuş içeriğin özetiyle eşleştirir."""
    added, skipped = 0, []
    for card in cards:
        with depo.baglanti() as connection:
            row = connection.execute("SELECT id,revision,content_hash FROM items WHERE url=?",
                                     (temiz_url(card["url"]),)).fetchone()
            existing = connection.execute("SELECT source_revision FROM editorial WHERE item_id=?",
                                          (row["id"],)).fetchone() if row else None
        if row is None:
            skipped.append({"url": card["url"], "reason": "Kaynak henüz havuzda yok."})
            continue
        if not card.get("source_hash") or card["source_hash"] != row["content_hash"]:
            skipped.append({"url": card["url"], "reason": "Okunmuş kaynakla içerik eşleşmiyor; yeniden değerlendirme gerekli."})
            continue
        if existing and existing["source_revision"] == row["revision"]:
            # Aynı notun tarihini yenileyerek seçkide kalmasını sürekli uzatmayız.
            continue
        try:
            depo.editor_karti_yaz({**card, "id": row["id"], "source_revision": row["revision"]})
        except ValueError:
            skipped.append({"url": card["url"], "reason": "Kaynak aktarım sırasında değişti; not uygulanmadı."})
        else:
            added += 1
    return {"added": added, "skipped": skipped}


def kartlari_ekle(depo):
    if depo.gorunum()["stats"]["total"]:
        return
    cards = [
        ("anthropic", {
            "url": "https://www.anthropic.com/news/donating-agent-skills-to-the-agentic-ai-foundation",
            "title": "Agent Skills standardı ortak bir çatıya taşınıyor",
            "excerpt": "Anthropic, Agent Skills standardını Agentic AI Foundation'a devrettiğini 22 Ekim 2026'da duyurdu. Sürümlü yayımlar ve güvenlik rehberi önerilen sonraki çalışmalar arasında.",
            "benefit": "Claude ve Codex'in ortak araştırma usulü için incelenebilir. Ortak haber deposu ve görev kilidinin yerine geçmez.",
            "published_at": "2026-10-22", "evidence_type": "Kurum duyurusu · araştırmada açıldı", "language": "tr"}),
        ("home_assistant", {
            "url": "https://www.home-assistant.io/blog/2026/10/07/release-202610/",
            "title": "Home Assistant tetikleyici seçimini kolaylaştırıyor",
            "excerpt": "2026.10 sürüm yazısı, otomasyon koşullarında tetikleyici seçimini kolaylaştıran arayüzü anlatıyor. İlk duyuru 7 Ekim; 26 Ekim araştırmasında sayfada 24 Ekim tarihli 2026.10.4 yaması da listeleniyordu.",
            "benefit": "Ev ve sera otomasyonlarının bakımı için aday. Canlı kuruluma uyumu bu pilotta denenmedi.",
            "published_at": "2026-10-07", "evidence_type": "Resmî sürüm notu · araştırmada açıldı", "language": "tr"}),
        ("wur", {
            "url": "https://www.wur.nl/en/research/plant/agros-ii-next-steps-towards-autonomous-greenhouse",
            "title": "AGROS II, bitki ve sera verisini birlikte değerlendiriyor",
            "excerpt": "WUR'un 2026–2027 AGROS II projesi, bitki, yetiştirme ortamı ve iklim ölçümleriyle karar desteğini araştırıyor. Kamera temelli ölçüm kapsamda; bu tamamlanmış bir başarı sonucu değil.",
            "benefit": "Kamera ve sensör gözlemini birlikte değerlendirmek için araştırma adayı; saha uyumu ayrıca ölçülmeli.",
            "published_at": None, "evidence_type": "Devam eden proje · yayın tarihi bilinmiyor", "language": "tr"}),
        ("adobe", {
            "url": "https://blog.adobe.com/en/publish/2026/04/15/adobe-extends-leadership-video-unleashing-new-ai-powered-creation-firefly-reinventing-color-editors-in-premiere",
            "title": "Adobe'nin video duyurusu ses ve medya erişimine odaklanıyor",
            "excerpt": "15 Nisan duyurusu Firefly Video Editor ses geliştirmeleri, Premiere beta Color Mode ve Frame.io Drive'ı içeriyor. Bugünkü paket ve erişim koşulları eski yazıdan çıkarılamaz.",
            "benefit": "Konuşma temizleme ve ortak medya erişimi için aday; güncel lisans ve ürün belgesi yeniden incelenmeli.",
            "published_at": "2026-04-15", "evidence_type": "Arşiv · üretici duyurusu", "language": "tr"}),
    ]
    for source_id, card in cards:
        card["external_id"] = card["url"]
        depo.kaydet(source_id, [card], editorial=True)
