"""Arama sonuçlarını gövdesiz sunar; kaynak, sürüm ve izin doğrulamasını korur."""

from radar.gelistirici_alani import paket_dogrula


def arama_ozeti(result):
    """Kaynak nesneyi değiştirmeden yalnız arama paketlerinin gövdesini çıkarır."""
    if not isinstance(result, dict) or not isinstance(result.get("items"), list):
        raise ValueError("Araştırma arama yanıtı geçersiz.")
    summaries = []
    for item in result["items"]:
        if not isinstance(item, dict) or not isinstance(item.get("packet"), dict):
            raise ValueError("Araştırma arama kaydı geçersiz.")
        packet = {key: value for key, value in item["packet"].items() if key != "body"}
        summaries.append({**item, "packet": packet})
    return {**result, "items": summaries, "view": "summary"}


def paket_ozetini_dogrula(value):
    """Yalnız açık özet yanıtını doğrular; eksik alanlı tam paketi kabul etmez."""
    if not isinstance(value, dict) or "body" in value:
        raise ValueError("Arama özeti gövde içeremez.")
    # Tam paket doğrulaması değişmez. Boş gövde yalnız bu geçici doğrulama
    # kopyasında kullanılır; kaydedilmez, paylaşılmaz ve yanıta eklenmez.
    checked = paket_dogrula({**value, "body": ""})
    return {key: item for key, item in checked.items() if key != "body"}
