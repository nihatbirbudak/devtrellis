"""Yönerge benzeri içeriğin bekletilmesi (alpha.15 güvenlik, izolasyon incelemesi Y1): hangi kapı uyarılarının bu sınıfa
girdiği tek yerde tanımlıdır.

Sunucu ayarı `hold_instruction_like` (K1 ayar dosyası; varsayılan `false`) açıkken bu uyarılardan birini taşıyan gönderim
otomatik yayımlanmaz; insan kuyruğuna (`pending_review`, `hold_reason: instruction_like_content`) düşer. Ayar açıkken kapı
ayrıca geniş yönerge taramasını çalıştırır (`yonerge.denetle(..., genis=True)`: doğrulama komutu ve kurulum ön koşulu
buyrukları). Kapalıyken kapı sonucu ve yayın kararı önceki sürümün aynısıdır.

Bu bir azaltmadır, kilit değildir: tarama sezgiseldir; başka sözlerle yazılmış bir buyruk ya da 30 günden eski, kayıtta var
olan kötü niyetli bir paket bu sınıfa girmeyebilir.
"""

from __future__ import annotations

# Sınıfa giren uyarı kodları. Ret düzeyindeki yönerge kuralları (talimatı yok say, veri sızdırma, indirip çalıştırma, ajana
# buyruk) zaten yayını durdurur; burada yalnız bugün yayını durdurmayan uyarılar var.
BEKLETILEN_UYARILAR = frozenset({
    "yonerge.rol_taklidi",            # system:, <system>, [INST] ...
    "yonerge.ajana_hitap",            # "AI agents should ...", "dear assistant"
    "yonerge.calistir_buyrugu",       # "run the following", "call the tool"
    "yonerge.kullaniciya_sormadan",   # "without asking the user"
    "yonerge.supheli_komut",          # iex, Invoke-Expression, chmod +x … &&
    "yonerge.gizli_yorum",            # gizli HTML yorumu
    "yonerge.dogrulama_komutu",       # geniş tarama: "to verify, run `…`"
    "yonerge.kurulum_on_kosulu",      # geniş tarama: "first `pip install …` is required"
    "paket.cok_yeni",                 # ilk sürümü 30 günden yeni paket
    "paket.dogrudan_adres",           # kayıttan değil adresten kurulum
    "paket.ozel_dizin",               # özel paket dizini (index/registry)
    "paket.ekosistem_desteklenmiyor",  # denetlenemeyen ekosistem (ayarı "uyari" ise yayına ulaşır)
})
BEKLETME_NEDENI = "instruction_like_content"


def yonerge_benzeri_uyarilar(sonuc):
    """Kapı sonucunun bu sınıfa giren uyarı kodları (sıralı); yoksa boş liste."""
    if not isinstance(sonuc, dict):
        return []
    return sorted({kod for kod in sonuc.get("uyarilar") or [] if isinstance(kod, str) and kod in BEKLETILEN_UYARILAR})


__all__ = ["BEKLETILEN_UYARILAR", "BEKLETME_NEDENI", "yonerge_benzeri_uyarilar"]
