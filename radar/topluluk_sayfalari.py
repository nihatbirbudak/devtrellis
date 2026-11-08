"""Yayımlanmış araştırmaları güvenli HTML, Markdown ve keşif akışlarında gösterir."""

import html
import json
import re
import unicodedata
from datetime import datetime, timezone
from urllib.parse import urlsplit


LICENSES = {"CC-BY-4.0": "https://creativecommons.org/licenses/by/4.0/",
            "CC0-1.0": "https://creativecommons.org/publicdomain/zero/1.0/"}


def guvenli_url(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return None
        if any(ord(char) < 32 for char in value):
            return None
    except ValueError:
        return None
    return value


def kisa_ad(title):
    letters = unicodedata.normalize("NFKD", str(title).replace("ı", "i")).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", letters).strip("-")[:72] or "research"


def rapor_yolu(card, *, exact=False):
    if exact:
        return f'/r/{card["id"]}/v/{card["version"]}'
    return f'/r/{card["id"]}/{kisa_ad(card["packet"]["title"])}'


def _inline(value):
    """Yalnız http(s) bağlantısı ve satır içi kodu açar; kalan metni kaçırır."""
    parts = []
    pattern = re.compile(r"(`[^`\n]+`|\[[^\]\n]{1,200}\]\([^\s)]+\))")
    last = 0
    for match in pattern.finditer(value):
        parts.append(html.escape(value[last:match.start()]))
        token = match.group()
        if token.startswith("`"):
            parts.append("<code>" + html.escape(token[1:-1]) + "</code>")
        else:
            label, url = token[1:].split("](", 1)
            url = guvenli_url(url[:-1])
            parts.append(f'<a href="{html.escape(url, quote=True)}" rel="nofollow noopener noreferrer">{html.escape(label)}</a>'
                         if url else html.escape(token))
        last = match.end()
    parts.append(html.escape(value[last:]))
    return "".join(parts)


def guvenli_markdown(value):
    """Başlık, liste, kod ve bağlantı dışındaki Markdown'ı zararsız metin tutar."""
    lines = str(value or "").splitlines()
    out, paragraph, list_type, code = [], [], None, False

    def flush():
        nonlocal paragraph
        if paragraph:
            out.append("<p>" + _inline(" ".join(paragraph)) + "</p>")
            paragraph = []

    def close_list():
        nonlocal list_type
        if list_type:
            out.append(f"</{list_type}>")
            list_type = None

    for line in lines:
        if line.strip().startswith("```"):
            flush(); close_list()
            out.append("</code></pre>" if code else "<pre><code>")
            code = not code
            continue
        if code:
            out.append(html.escape(line) + "\n")
            continue
        if not line.strip():
            flush(); close_list(); continue
        heading = re.fullmatch(r"\s{0,3}(#{1,4})\s+(.+)", line)
        if heading:
            flush(); close_list()
            level = min(len(heading[1]) + 1, 5)
            out.append(f"<h{level}>{_inline(heading[2])}</h{level}>")
            continue
        item = re.fullmatch(r"\s{0,3}([-*]|[0-9]+\.)\s+(.+)", line)
        if item:
            flush()
            kind = "ol" if item[1].endswith(".") else "ul"
            if list_type != kind:
                close_list(); out.append(f"<{kind}>"); list_type = kind
            out.append("<li>" + _inline(item[2]) + "</li>")
            continue
        close_list(); paragraph.append(line.strip())
    flush(); close_list()
    if code:
        out.append("</code></pre>")
    return "\n".join(out)


def _date(value):
    if isinstance(value, str) and re.match(r"^\d{4}-\d{2}-\d{2}", value):
        return value[:10]
    return ""


def _sources(packet):
    seen = set()
    for source in packet.get("sources") or []:
        url = guvenli_url(source.get("url")) if isinstance(source, dict) else None
        if url and url not in seen:
            seen.add(url)
            yield source.get("title") or url, url
    url = guvenli_url(packet.get("url"))
    if url and url not in seen:
        yield "Original" , url


def _provenance(packet, tr=False):
    """HTML ve Markdown için aynı köken beyanını ve içerik sınırını üretir."""
    facts = packet.get("provenance") or {}
    unknown = "Kaydedilmemiş" if tr else "Not recorded"
    status = "unverified" if facts.get("modified") else facts.get("evidence_status", "unknown")
    statuses = ({"verified": "Kaynak eşleşmesi kaydedilmiş", "unverified": "Denetlenmemiş",
                 "insufficient": "Yetersiz dayanak", "unknown": "Bilinmiyor"} if tr else
                {"verified": "Source match recorded", "unverified": "Unverified",
                 "insufficient": "Insufficient evidence", "unknown": "Unknown"})
    rows = [("Kaynak araştırmanın modeli" if tr else "Original research model", facts.get("model") if facts.get("model") not in (None, "", "unknown") else unknown),
            ("Sağlayıcı" if tr else "Provider", facts.get("provider") or unknown),
            ("Araştırmanın üretim tarihi" if tr else "Research generated at", facts.get("generated_at") or unknown),
            ("Kanıt durumu" if tr else "Evidence status", statuses.get(status, statuses["unknown"]))]
    if facts.get("claim_text"):
        rows.append(("İncelenen iddia" if tr else "Claim examined", facts["claim_text"]))
    warnings = [("Model ve köken bilgileri katkı sahibinin beyanıdır; doğruluk garantisi değildir." if tr else
                 "Model and provenance details are contributor statements, not a guarantee of accuracy.")]
    if facts.get("modified"):
        warnings.append("Kart içeriği özgün rapordan sonra değiştirildi; yeniden kanıt denetimi yapılmadı." if tr else
                        "The card was modified after the original report; its evidence has not been checked again.")
    return rows, warnings


def _corrections(card):
    """Düzeltmenin yalnız kamu özetini, kesin hedefini ve geri çekme işaretini taşır."""
    corrections = card.get("public_corrections") or {}
    items = []
    for item in corrections.get("items", []):
        if not isinstance(item, dict):
            continue
        correction = item.get("correction")
        target = (f'/r/{correction["research_id"]}/v/{correction["version"]}' if isinstance(correction, dict)
                  and re.fullmatch(r"[a-f0-9]{32}", str(correction.get("research_id", "")))
                  and type(correction.get("version")) is int and correction["version"] > 0 else None)
        items.append((str(item.get("summary") or ""), target, item.get("withdrawn") is True))
    total = corrections.get("total")
    more = type(total) is int and total > len(items)
    return items, (f'/api/community/objections/corrections/{card["id"]}/{card["version"]}' if more else None)


def sayfa(card, origin, language="en", *, objections_enabled=False):
    packet = card["packet"]
    tr = language == "tr"
    esc = lambda value: html.escape(str(value or ""), quote=True)
    exact = rapor_yolu(card, exact=True)
    current = rapor_yolu(card)
    title = esc(packet["title"])
    description = esc(packet.get("summary", ""))
    license_url = LICENSES.get(packet.get("license"))
    sources = list(_sources(packet))
    citations = [url for _, url in sources]
    facts = packet.get("provenance") or {}
    body_language = facts.get("language") if facts.get("language") in {"tr", "en"} else language
    report = {"@context": "https://schema.org", "@type": "TechArticle", "headline": packet["title"],
              "description": packet.get("summary", ""), "url": origin + exact,
              "datePublished": _date(card.get("updated_at")), "inLanguage": facts.get("language") or language,
              "author": {"@type": "Person", "name": packet.get("author") or "Unknown"},
              "citation": citations, "license": license_url or packet.get("license", "")}
    schema = json.dumps(report, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    source_html = "".join(f'<li><a href="{esc(url)}" rel="nofollow noopener noreferrer">{esc(label)}</a></li>' for label, url in sources)
    correction_items, correction_more = _corrections(card)
    correction_html = ""
    for summary, target, withdrawn in correction_items:
        correction_html += f'<li>{esc(summary)}'
        if target:
            correction_html += f' · <a href="{esc(target)}?lang={language}">{"Düzeltici sürüm" if tr else "Correcting version"}</a>'
        if withdrawn:
            correction_html += ' · ' + ("Özel itiraz geri çekildi; kamu düzeltme izi korunuyor." if tr else "Private objection withdrawn; the public correction trace remains.")
        correction_html += '</li>'
    correction_more_html = (f'<p><a href="{esc(correction_more)}">{"Tüm kamu düzeltmeleri (JSON; sayfalı)" if tr else "All public corrections (JSON; paginated)"}</a></p>' if correction_more else "")
    provenance_rows, warnings = _provenance(packet, tr)
    provenance_html = '<dl>' + ''.join(f'<dt>{esc(label)}</dt><dd>{esc(value)}</dd>' for label, value in provenance_rows) + '</dl>'
    navigation = f'<p><a href="/">{"Araştırmalar" if tr else "Research"}</a> · <a href="/report-guide?lang={language}">{"Rapor nasıl okunur" if tr else "How to read a report"}</a> · <a href="{esc(exact)}?lang={language}">{"Kesin sürüm" if tr else "Exact version"}</a> · <a href="/r/{esc(card["id"])}.md?version={card["version"]}">Markdown</a></p>'
    language_links = f'<p aria-label="Language"><a href="{esc(exact)}?lang=en" lang="en" hreflang="en" data-report-language="en">English</a> · <a href="{esc(exact)}?lang=tr" lang="tr" hreflang="tr" data-report-language="tr">Türkçe</a></p>'
    previous = (f'<p class="notice">{"Eski bir sürümü okuyorsun." if tr else "You are reading an earlier version."} '
                f'<a href="{esc(current)}?lang={language}">{"Güncel sürüm" if tr else "Current version"}</a></p>'
                if card["version"] != card.get("published_version") else "")
    return f'''<!doctype html><html lang="{language}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — DevTrellis</title><meta name="description" content="{description}"><link rel="canonical" href="{esc(origin + exact)}">
<meta property="og:type" content="article"><meta property="og:title" content="{title}"><meta property="og:description" content="{description}"><meta property="og:url" content="{esc(origin + exact)}">
<link rel="stylesheet" href="/static/community-report.css"><script src="/static/report-language.js" defer></script><script type="application/ld+json">{schema}</script></head><body>
<a class="skip-link" href="#report">{"İçeriğe geç" if tr else "Skip to content"}</a><header><a href="/">DevTrellis</a><nav>{navigation}{language_links}</nav></header>
<main id="report"><article><p class="eyebrow">{"Kamu araştırması" if tr else "Public research"} · v{card["version"]}</p><h1>{title}</h1>
<p class="lead">{description}</p><p>{"Yazar beyanı" if tr else "Author as stated"}: {esc(packet.get("author"))} · {"Güncellendi" if tr else "Updated"}: {esc(_date(card.get("updated_at")))} · {"Lisans" if tr else "License"}: {esc(packet.get("license"))}</p>
{previous}{provenance_html}<section aria-label="{'Kanıt ve sınırlar' if tr else 'Evidence and limits'}">{''.join(f'<p class="notice">{esc(item)}</p>' for item in warnings)}<p><a href="/report-guide?lang={language}">{"Kanıt ve sınırları nasıl okumalı?" if tr else "How to read evidence and limits"}</a></p></section>
<section class="report-body" lang="{body_language}">{guvenli_markdown(packet.get("body"))}</section>
<section><h2>{"Kaynaklar" if tr else "Sources"}</h2><ul>{source_html}</ul></section>
<section><h2>{"Kamu düzeltmeleri" if tr else "Public corrections"}</h2>{f'<ul>{correction_html}</ul>' if correction_html else f'<p>{"Bu sürüm için kayıtlı kamu düzeltmesi yok." if tr else "No public correction is recorded for this version."}</p>'}{correction_more_html}</section>
<p><a href="{f'/objections/new/{esc(card["id"])}/{card["version"]}?lang={language}' if objections_enabled else '/support'}">{"İddiaya itiraz et" if tr and objections_enabled else "Dispute a claim" if objections_enabled else "İçerik sorunu bildir" if tr else "Report a content issue"}</a></p></article></main></body></html>'''


def markdown_rapor(card, origin):
    packet = card["packet"]
    rows, warnings = _provenance(packet)
    lines = [f'# {packet["title"]}', "", packet.get("summary", ""), "", f'Version: {card["version"]}',
             f'Date: {_date(card.get("updated_at"))}', f'License: {packet.get("license", "")}',
             f'URL: {origin}{rapor_yolu(card, exact=True)}', "", "## Provenance and limits", ""]
    lines.extend(f'- {label}: {value}' for label, value in rows)
    lines.extend(["", *warnings, ""])
    if card["version"] != card.get("published_version"):
        lines.extend([f'You are reading an earlier version. Current version: {origin}{rapor_yolu(card)}', ""])
    lines.extend([packet.get("body", ""), "", "## Sources", ""])
    lines.extend(f'- [{label}]({url})' for label, url in _sources(packet))
    items, more = _corrections(card)
    lines.extend(["", "## Public corrections", ""])
    if not items:
        lines.append("No public correction is recorded for this version.")
    for summary, target, withdrawn in items:
        lines.append('- ' + summary + (f' · [Correcting version]({origin}{target})' if target else "") +
                     (" · Private objection withdrawn; the public correction trace remains." if withdrawn else ""))
    if more:
        lines.extend(["", f'[All public corrections (JSON; paginated)]({origin}{more})'])
    return "\n".join(lines) + "\n"


def yayindaki_arastirmalar(store):
    offset = 0
    while True:
        page = store.listele(kind="research", offset=offset, limit=100)
        yield from page["items"]
        offset += len(page["items"])
        if offset >= page["total"] or not page["items"]:
            break


def sitemap(cards, origin):
    urls = "".join(f'<url><loc>{html.escape(origin + rapor_yolu(card), quote=True)}</loc><lastmod>{_date(card.get("updated_at"))}</lastmod></url>' for card in cards)
    return '<?xml version="1.0" encoding="utf-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + urls + '</urlset>'


def atom(cards, origin):
    updated = max((_date(card.get("updated_at")) for card in cards), default=datetime.now(timezone.utc).date().isoformat())
    entries = "".join(f'<entry><id>{html.escape(origin + rapor_yolu(card, exact=True))}</id><author><name>{html.escape(card["packet"].get("author") or "Anonymous contributor")}</name></author><title>{html.escape(card["packet"]["title"])}</title><updated>{_date(card.get("updated_at"))}T00:00:00Z</updated><link href="{html.escape(origin + rapor_yolu(card), quote=True)}"/><summary>{html.escape(card["packet"].get("summary", ""))}</summary></entry>' for card in cards)
    return f'<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom"><id>{html.escape(origin)}/</id><title>DevTrellis research</title><author><name>DevTrellis</name></author><link rel="self" type="application/atom+xml" href="{html.escape(origin + "/feed.atom", quote=True)}"/><updated>{updated}T00:00:00Z</updated>{entries}</feed>'
