"""Kapının sınırlarını ve eşiklerini tek yerde tutar; değerler ölçümle değiştirilebilir varsayılanlardır."""

from __future__ import annotations

from dataclasses import dataclass

SURUM = "denetim-kapisi-v0.2.3"

# K1 taslağının sabitlediği istek varyantı (§2). Aynı başlıklar kullanılınca
# kapının gördüğü temsil K0 kaynak izlemenin gördüğüyle aynı olur.
KULLANICI_ARACI = "UfukRadar/0.1"
KABUL = "text/html, application/xhtml+xml;q=0.9"


@dataclass(frozen=True)
class Ayarlar:
    # Kibar getirme
    istek_zaman_asimi: float = 10.0        # tek istek (bağlanma ve okuma adımı)
    kaynak_toplam_sure: float = 30.0       # bir kaynak için robots + yönlendirmeler dahil
    dns_zaman_asimi: float = 5.0
    en_cok_bayt: int = 3_000_000           # kaynak sayfası
    robots_en_cok_bayt: int = 500_000
    kayit_en_cok_bayt: int = 8_000_000     # PyPI/npm JSON; aşılırsa paket "büyük ve eski" sayılır
    en_cok_yonlendirme: int = 3
    site_arasi_bekleme: float = 1.0        # aynı siteye iki istek arası en az saniye
    en_cok_robots_bekleme: float = 10.0    # robots Crawl-delay bundan büyükse kaynak getirilmez
    en_cok_kaynak: int = 30
    en_cok_paket: int = 20
    # Alıntı
    # Kısa alıntı reddedilir (Y1): normalleştirilmiş alıntı bu karakterden kısa ya da harf içeren sözcük sayısı
    # bundan az ise. 40/6, kabul raporunun önerisi; iki yeniden yazılan raporun 24 alıntısının en kısası 68 karakter, 12 sözcük.
    alinti_en_az_karakter: int = 40
    alinti_en_az_sozcuk: int = 6
    yakin_esik: float = 0.85               # bu benzerliğin üstü "yaklaşık" diye adlandırılır (yine kalır)
    bilgi_esik: float = 0.5                # bu benzerliğin altındaki parça gösterilmez
    # Paket
    yeni_paket_gun: int = 30
    desteksiz_ekosistem: str = "denenemedi"  # cargo/go: "denenemedi" (yayını durdurur) ya da "uyari"
    # Yinelenen yayın
    yinelenen_esik: float = 0.5
    # Kip
    reddedilende_ag_atla: bool = True      # ağsız denetimlerde ret varsa ağ isteği atma
