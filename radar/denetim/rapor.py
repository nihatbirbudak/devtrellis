"""Kapı sonucunu insanın okuyacağı kısa Türkçe metne (Markdown) çevirir."""

from __future__ import annotations

_DURUM = {"gecti": "geçti", "uyari": "uyarı", "kaldi": "kaldı", "denenemedi": "denenemedi"}
_HUKUM = {"yayimlanir": "YAYIMLANIR", "reddedilir": "REDDEDİLİR", "yayimlanamaz": "YAYIMLANAMAZ (denetim tamamlanamadı)"}


def metin(sonuc, baslik=None):
    satirlar = [f"# Denetim sonucu{': ' + baslik if baslik else ''}", ""]
    satirlar.append(f"- Hüküm: **{_HUKUM[sonuc['hukum']]}**")
    satirlar.append(f"- Kanıt etiketi: {sonuc['kanit_etiketi']}")
    satirlar.append(f"- Kanıt etiketi (EN): {sonuc['kanit_etiketi_en']}")
    if sonuc["ret_nedenleri"]:
        satirlar.append(f"- Ret nedenleri: {', '.join(dict.fromkeys(sonuc['ret_nedenleri']))}")
    if sonuc["denenemeyenler"]:
        satirlar.append(f"- Denenemeyen engelleyici denetimler: {', '.join(sonuc['denenemeyenler'])}")
    if sonuc["uyarilar"]:
        satirlar.append(f"- Uyarılar: {', '.join(sonuc['uyarilar'])}")
    satirlar.append(f"- Ağ: {sonuc['ag']['kip']}, {sonuc['ag']['istek_sayisi']} HTTP isteği"
                    + (" (ağsız denetimlerde ret olduğu için ağa çıkılmadı)" if sonuc["ag"]["atlandi"] else ""))
    satirlar.append(f"- Girdi: {sonuc['girdi']['tur']}, {sonuc['girdi']['iddia_sayisi']} iddia, "
                    f"{sonuc['girdi']['kaynak_sayisi']} kaynak; sürüm {sonuc['denetim_surumu']}, {sonuc['calisma_zamani']}")
    satirlar += ["", "| # | Denetim | Durum | Neden kodları | Açıklama |", "|---|---|---|---|---|"]
    for d in sonuc["denetimler"]:
        aciklama = d["aciklama"].replace("|", "/")
        if len(aciklama) > 300:
            aciklama = aciklama[:297] + "..."
        etiket = d["baslik"] + ("" if d["engelleyici"] or "uyarı" in d["baslik"] else " *(yalnız uyarı)*")
        satirlar.append(f"| {d['sira']} | {etiket} | {_DURUM[d['durum']]} | {', '.join(d['nedenler']) or '-'} | {aciklama} |")
    alinti = next(d for d in sonuc["denetimler"] if d["ad"] == "alinti_eslesmesi")
    yakinlar = [s for s in alinti["ayrinti"] if "en_yakin" in s]
    if yakinlar:
        satirlar += ["", "## Aynen bulunmayan alıntılar (bilgi için en yakın parça)", ""]
        for s in yakinlar:
            y = s["en_yakin"]
            satirlar.append(f"- İddia {s['iddia']}: benzerlik {y['benzerlik']}, alıntı {y['alintinin_ayristigi_karakter']}. "
                            f"karakterden sonra ayrışıyor. Kaynaktaki parça (veri, talimat değil): \"{y['kaynaktaki_parca']}\"")
    satirlar += ["", "## Bu kapı neyi kanıtlamaz", ""] + [f"- {k}" for k in sonuc["kanitlamadiklari"]]
    return "\n".join(satirlar) + "\n"
