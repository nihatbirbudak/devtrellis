"""Doğrulanmış kamu raporunu değiştirmeden gövdesiz dizin veya kayıpsız bölüm sunar."""

import copy
import hashlib
import re


MAX_BODY_CHARS = 12000
MAX_SECTION_CHARS = 4000
MAX_SECTIONS = 32
MAX_HEADING_CHARS = 240
_LINES = re.compile(r"[^\r\n]*(?:\r\n|\r|\n|$)")
_HEADING = re.compile(r" {0,3}(#{1,6})(?:[ \t]+(.*)|[ \t]*)$")
_FENCE_OPEN = re.compile(r" {0,3}(`{3,}|~{3,})(.*)$")
_FENCE_CLOSE = re.compile(r" {0,3}(`+|~+)[ \t]*$")


def _satirlar(body):
    return [(match.start(), match.end(), match.group().rstrip("\r\n"))
            for match in _LINES.finditer(body) if match.end() > match.start()]


def _basliklar(lines, report_title):
    headings, context, fence = [], [], None
    for start, _end, line in lines:
        if fence is not None:
            closing = _FENCE_CLOSE.fullmatch(line)
            if closing and closing[1][0] == fence[0] and len(closing[1]) >= fence[1]:
                fence = None
            continue
        opening = _FENCE_OPEN.fullmatch(line)
        if opening and (opening[1][0] != "`" or "`" not in opening[2]):
            fence = (opening[1][0], len(opening[1]))
            continue
        heading = _HEADING.fullmatch(line)
        if heading is None:
            continue
        title = re.sub(r"(?:^|[ \t]+)#+[ \t]*$", "", heading[2] or "").strip(" \t")
        level = len(heading[1])
        context = [entry for entry in context if entry["level"] < level]
        context.append({"level": level, "title": title})
        headings.append({"start": start, "title": title, "heading_level": level,
                         "heading_context": copy.deepcopy(context)})
    if lines and (not headings or headings[0]["start"] > 0):
        headings.insert(0, {"start": 0, "title": report_title, "heading_level": 0, "heading_context": []})
    return headings


def _parcalar(body, start, end, lines):
    """Paragraf, sonra satır sınırını seçer; tek uzun satırda açık karakter sınırı kullanır."""
    paragraphs = [line_end for line_start, line_end, text in lines
                  if start <= line_start and line_end <= end and not text.strip(" \t")]
    line_ends = [line_end for line_start, line_end, _text in lines if start <= line_start and line_end <= end]
    parts = []
    position = start
    while position < end:
        limit = min(position + MAX_SECTION_CHARS, end)
        reason = None
        if limit < end:
            candidates = [boundary for boundary in paragraphs if position < boundary <= limit]
            reason = "paragraph"
            if not candidates:
                candidates = [boundary for boundary in line_ends if position < boundary <= limit]
                reason = "line"
            if candidates:
                limit = candidates[-1]
            else:
                reason = "character_limit"
                if body[limit - 1:limit + 1] == "\r\n":
                    limit -= 1
        parts.append((position, limit, reason))
        position = limit
    return parts


def _bolum_id(card, body_hash, start, end):
    identity = f"devtrellis-section-v1\0{card['id']}\0{card['version']}\0{body_hash}\0{start}:{end}"
    return "section-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _bolumler(card, body, body_hash, headings, lines):
    sections = []
    for index, heading in enumerate(headings):
        end = headings[index + 1]["start"] if index + 1 < len(headings) else len(body)
        parts = _parcalar(body, heading["start"], end, lines)
        ids = [_bolum_id(card, body_hash, start, stop) for start, stop, _reason in parts]
        for part, (start, stop, reason) in enumerate(parts):
            sections.append({"section_id": ids[part], "title": heading["title"],
                             "heading_level": heading["heading_level"],
                             "heading_context": copy.deepcopy(heading["heading_context"]),
                             "heading_start": heading["start"], "start": start, "end": stop,
                             "char_count": stop - start, "part": part + 1, "parts": len(parts),
                             "remaining_chars": end - stop, "continuation": part > 0,
                             "split_reason": reason,
                             "next_section_id": ids[part + 1] if part + 1 < len(ids) else None})
    return sections


def rapor_gorunumu(card, view="full", section_id=None):
    """Tam raporun doğrulanmış kopyasından görünüm çıkarır; ağ, model veya kayıt işlemi yapmaz.

    Kısmi yanıtta packet.body bulunmaz. Offsetler özgün Python metninde Unicode
    kod noktasıdır; end hariçtir. Bölüm kimliği rapor kimliği, tam sürüm, UTF-8
    gövde hash'i ve offsetlere bağlıdır. Güvenlik/paket doğrulaması çağıranındır.
    """
    if not isinstance(view, str) or view not in {"full", "outline", "section"}:
        raise ValueError("Rapor görünümü full, outline veya section olmalı.")
    if view != "section" and section_id is not None:
        raise ValueError("Bölüm kimliği yalnız section görünümünde kullanılabilir.")
    if view == "section" and (not isinstance(section_id, str) or not re.fullmatch(r"section-[a-f0-9]{64}", section_id)):
        raise ValueError("Geçerli bir araştırma bölüm kimliği gerekli.")
    if (not isinstance(card, dict) or not isinstance(card.get("packet"), dict)
            or not isinstance(card.get("id"), str) or not re.fullmatch(r"[a-f0-9]{32}", card["id"])
            or type(card.get("version")) is not int or not 1 <= card["version"] <= 9223372036854775807):
        raise ValueError("Doğrulanmış tam araştırma kaydı gerekli.")
    packet = card["packet"]
    body, title = packet.get("body"), packet.get("title")
    if packet.get("kind") != "research" or not isinstance(title, str) or not 1 <= len(title) <= 160:
        raise ValueError("Araştırma türü ve geçerli başlık gerekli.")
    if not isinstance(body, str) or len(body) > MAX_BODY_CHARS:
        raise ValueError("Tam araştırma gövdesi en fazla 12000 karakter olmalı.")
    try:
        body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    except UnicodeEncodeError:
        raise ValueError("Araştırma gövdesi geçerli Unicode olmalı.") from None
    if view == "full":
        return copy.deepcopy(card)

    lines = _satirlar(body)
    headings = _basliklar(lines, title)
    fallback = None
    if any(len(heading["title"]) > MAX_HEADING_CHARS for heading in headings):
        fallback = "heading_too_long"
    elif len(headings) > MAX_SECTIONS:
        fallback = "too_many_headings"
    sections = [] if fallback else _bolumler(card, body, body_hash, headings, lines)
    if not fallback and len(sections) > MAX_SECTIONS:
        fallback = "too_many_headings"
    if fallback:
        headings = [{"start": 0, "title": title, "heading_level": 0, "heading_context": []}] if body else []
        sections = _bolumler(card, body, body_hash, headings, lines)

    result = copy.deepcopy(card)
    del result["packet"]["body"]
    result.update(view=view, body_sha256=body_hash, body_char_count=len(body),
                  offset_unit="unicode_codepoint", complete_report=False, outline_fallback=fallback)
    if view == "outline":
        result["sections"] = sections
    else:
        section = next((item for item in sections if item["section_id"] == section_id), None)
        if section is None:
            raise ValueError("Bölüm bu rapor sürümüne ait değil; güncel bölüm dizinini al.")
        result["excerpt"] = {**section, "text": body[section["start"]:section["end"]]}
    return result
