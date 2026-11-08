"""Tek statik belge alanının bölüm ve yapı izlerini metin saklamadan çıkarır ve temkinli karşılaştırır."""

import re
from collections import Counter
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from radar.kaynak_aileleri import (AileHatasi, clean_classes, decorative_svg, detect_family, family_root,
                                   icon_nodes, remove_chrome)
from radar.kaynak_kimligi import (EXTRACTOR_VERSION, REPRESENTATION, canonical_hash,
                                  canonical_source_url)


MAX_BODY_BYTES = 1024 * 1024
MAX_NODES = 20000
MAX_SECTIONS = 256
HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
CONTAINERS = frozenset({"main", "article", "div", "section", "header", "footer", "nav", "aside"})
BLOCKS = CONTAINERS | HEADINGS | frozenset({"p", "pre", "ul", "ol", "li", "blockquote", "table",
                                         "caption", "thead", "tbody", "tfoot", "tr", "dl", "dt", "dd",
                                         "figure", "figcaption", "address", "hr"})
ALLOWED_TAGS = CONTAINERS | HEADINGS | frozenset({
    "p", "a", "span", "em", "strong", "b", "i", "u", "s", "small", "sub", "sup", "mark",
    "abbr", "acronym", "cite", "q", "time", "dfn", "var", "kbd", "samp", "pre", "code", "br", "hr", "wbr",
    "ul", "ol", "li", "blockquote", "table", "caption", "colgroup", "col", "thead", "tbody", "tfoot",
    "tr", "th", "td", "dl", "dt", "dd", "figure", "figcaption", "address", "del", "ins"})
LITERALS = frozenset({"pre", "code", "samp", "kbd"})
ACTIVE_TAGS = frozenset({"script", "iframe", "object", "embed", "form", "input", "button",
                         "textarea", "select", "option", "link", "style", "template", "noscript"})
HEX64 = re.compile(r"[a-f0-9]{64}\Z")
DOCUMENT_FIELDS = frozenset({"policy_version", "extractor_version", "canonical_url", "body_hash", "sections", "ambiguous"})
SECTION_FIELDS = frozenset({"section_id", "heading_hash", "section_hash", "level", "order", "parent_id", "anchor_hash", "duplicate_heading"})


class KaynakBolumHatasi(ValueError):
    """Desteklenmeyen yapının içeriğini veya özel adresini hata metnine eklemez."""


def hidden(node):
    if not isinstance(node, Tag):
        return False
    # aria-hidden yalnız erişilebilirlik ağacını etkiler; görsel metin hash'e katılır.
    if node.has_attr("hidden"):
        return True
    style = str(node.get("style", ""))
    return bool(re.search(r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*(?:hidden|collapse))\s*(?:!important)?\s*(?:;|$)", style, re.I))


def visible(node, stop=None):
    current = node
    while isinstance(current, Tag):
        if hidden(current):
            return False
        if current is stop:
            break
        current = current.parent
    return True


def exact_link(url, value, *, media=False):
    """Aile sözleşmesinde bağlantı hedefi normalleştirilmeden, tam mutlak adresiyle hash'e girer."""
    if not isinstance(value, str) or any(ord(char) < 32 or ord(char) == 127 for char in value.strip()):
        raise KaynakBolumHatasi("unsupported_link_target")
    try:
        address = urljoin(url, value.strip())
        parts = urlsplit(address)
        scheme = parts.scheme.lower()
        if scheme in {"javascript", "vbscript", "data"}:
            raise KaynakBolumHatasi("unsupported_active_content")
        if scheme not in ({"https", "http"} if media else {"https", "http", "mailto"}):
            raise KaynakBolumHatasi("unsupported_link_target")
        if scheme != "mailto" and (not parts.hostname or parts.username is not None or parts.password is not None):
            raise KaynakBolumHatasi("unsupported_link_target")
    except ValueError as error:
        if isinstance(error, KaynakBolumHatasi):
            raise
        raise KaynakBolumHatasi("unsupported_link_target") from None
    return address


def attributes(node, url, family=None):
    result = {}
    for name, value in sorted(node.attrs.items()):
        if name.lower().startswith("on") or name in {"contenteditable", "srcdoc"}:
            raise KaynakBolumHatasi("unsupported_active_content")
        if isinstance(value, list):
            value = list(value)
        elif value is not None and not isinstance(value, str):
            raise KaynakBolumHatasi("unsupported_attribute")
        if name == "href" and family is not None:
            result[name] = exact_link(url, value)
        elif name == "href":
            try:
                address = urljoin(url, value or "")
                parts = urlsplit(address)
                result[name] = canonical_source_url(address) + ("#" + parts.fragment if parts.fragment else "")
            except (ValueError, TypeError):
                raise KaynakBolumHatasi("unsupported_link_target") from None
        elif name == "class":
            result[name] = clean_classes(family, value)
        else:
            result[name] = value
    return result


def media_reference(node, url, family):
    """Görsel ve gömülü öğe yalnız adresi ve metin karşılığıyla temsil edilir; içeriği okunmaz."""
    result = {"src": exact_link(url, node.get("src"), media=True)}
    for name in ("alt", "title"):
        value = node.get(name)
        if value is not None and not isinstance(value, str):
            raise KaynakBolumHatasi("unsupported_attribute")
        result[name] = value
    return result


def text_token(node, literal):
    value = str(node)
    if literal:
        return value
    value = re.sub(r"[\t\n\r\f ]+", " ", value)
    if value == " " and getattr(node.parent, "name", None) in CONTAINERS | {"ul", "ol", "table", "thead", "tbody", "tfoot", "tr", "dl"}:
        def block_boundary(sibling, direction):
            while (isinstance(sibling, Comment) or isinstance(sibling, Tag) and not visible(sibling)
                   or isinstance(sibling, NavigableString) and re.fullmatch(r"[\t\n\r\f ]*", str(sibling))):
                sibling = getattr(sibling, direction)
            if sibling is None:
                return True
            # Bilinen blok sınırında düzen boşluğu yok sayılır. Satır içi komşu,
            # metin veya özel inline stil belirsizse boşluk korunur.
            return isinstance(sibling, Tag) and sibling.name in BLOCKS and not sibling.has_attr("style")
        if block_boundary(node.previous_sibling, "previous_sibling") and block_boundary(node.next_sibling, "next_sibling"):
            return ""
    return value


class Rules:
    """Genel çıkarıcı için family=None; aile sözleşmesinde süs SVG'si, görsel ve gömülü adres kuralları."""

    def __init__(self, family=None, icons=frozenset()):
        self.family, self.icons = family, icons


GENERIC = Rules()


def structure(node, url, literal=False, rules=GENERIC):
    if isinstance(node, Comment):
        return None
    if isinstance(node, NavigableString):
        value = text_token(node, literal)
        return ["text", value] if value else None
    if not isinstance(node, Tag) or not visible(node):
        return None
    if node.name not in ALLOWED_TAGS:
        family = rules.family
        if family is not None:
            if decorative_svg(node, rules.icons):
                # Süs simgesi: geometri hash'e girmez, simgenin varlığı ve sınıfı girer.
                return ["svg", {"aria-hidden": node.get("aria-hidden"), "class": clean_classes(family, list(node.get("class") or []))}, []]
            if node.name == "img":
                return ["img", media_reference(node, url, family), []]
            if node.name in family.embeds:
                return [node.name, media_reference(node, url, family), []]
        raise KaynakBolumHatasi("unsupported_document_structure")
    content = []
    for child in node.children:
        value = structure(child, url, literal or node.name in LITERALS, rules)
        if value is not None:
            if value[0] == "text" and content and content[-1][0] == "text":
                content[-1][1] += value[1]
            else:
                content.append(value)
    return [node.name, attributes(node, url, rules.family), content]


def extract_document(body, url):
    """Sadece statik HTML görünürlüğünü kapsar; dış CSS/JS hesaplaması veya alıntı doğrulaması yapmaz.

    Açık üretici işareti olan belgede önceden yazılmış aile sözleşmesi (kaynak_aileleri) uygulanır; işaret
    yoksa genel kök kuralı aynen çalışır. Belgenin extractor_version alanı hangi sözleşmenin kullanıldığını taşır.
    """
    url = canonical_source_url(url)
    try:
        if isinstance(body, bytes):
            if len(body) > MAX_BODY_BYTES:
                raise ValueError
            body = body.decode("utf-8", errors="strict")
        if not isinstance(body, str) or not body or len(body.encode("utf-8")) > MAX_BODY_BYTES or "\x00" in body:
            raise ValueError
    except (ValueError, UnicodeError):
        raise KaynakBolumHatasi("invalid_document_body") from None
    soup = BeautifulSoup(body, "html.parser")
    nodes = soup.find_all(True)
    if len(nodes) > MAX_NODES:
        raise KaynakBolumHatasi("document_limit_exceeded")
    if soup.find("base"):
        raise KaynakBolumHatasi("unsupported_document_base")
    try:
        family = detect_family(soup, url)
        root = family_root(family, soup) if family else None
    except AileHatasi as error:
        if str(error) == "ambiguous_document_family":
            raise KaynakBolumHatasi("ambiguous_document_family") from None
        raise KaynakBolumHatasi("family_contract_mismatch") from None
    if family is None:
        candidates = [node for node in soup.select("main,article,#docContent") if visible(node)]
        candidates = list({id(node): node for node in candidates}.values())
        # Birden çok adayın iç içe olması, dıştaki metni sessizce düşürme izni değildir.
        if len(candidates) != 1:
            raise KaynakBolumHatasi("ambiguous_document_root")
        root = candidates[0]
        rules = GENERIC
    else:
        if not visible(root):
            raise KaynakBolumHatasi("family_contract_mismatch")
        # Yalnız sözleşmede adı geçen arayüz parçaları çıkar; etkin içerik denetimi bundan sonra tüm kökte çalışır.
        remove_chrome(family, root)
        rules = Rules(family, icon_nodes(family, root))
    version = f"{EXTRACTOR_VERSION}.{family.label if family else 'generic'}"
    for node in [root, *root.find_all(True)]:
        if sum(1 for _ in node.parents) > 80:
            raise KaynakBolumHatasi("document_limit_exceeded")
        # hidden etkin kodu devre dışı bırakmaz; atlanmadan önce reddedilir.
        embedded = family is not None and node.name in family.embeds
        if (node.name in ACTIVE_TAGS and not embedded) or any(name.lower().startswith("on") or name in {"contenteditable", "srcdoc"} for name in node.attrs):
            raise KaynakBolumHatasi("unsupported_active_content")
        if not visible(node, root):
            continue
        if node.name in HEADINGS and node.find_parent(list(HEADINGS | LITERALS | {"table", "dl"})):
            raise KaynakBolumHatasi("unsupported_heading_structure")
        if node.name in {"td", "th"}:
            for attribute in ("rowspan", "colspan"):
                value = node.get(attribute)
                if value is not None and (not re.fullmatch(r"[1-9][0-9]{0,3}", str(value)) or int(value) > 1000):
                    raise KaynakBolumHatasi("unsupported_table_structure")
            if not node.find_parent("table"):
                raise KaynakBolumHatasi("unsupported_table_structure")
    body_structure = structure(root, url, rules=rules)
    if not any(str(text).strip() for text in root.find_all(string=True)
               if not isinstance(text, Comment) and visible(text.parent, root)):
        raise KaynakBolumHatasi("empty_document")
    drafts = [{"level": 0, "heading_hash": canonical_hash(""), "anchor_hash": None, "parts": []}]

    def section_heading(node):
        # Aile sözleşmesi bazı başlıkları (ör. DocBook not kutusu başlığı) bölüm saymaz; onlar içeriktir.
        return (isinstance(node, Tag) and node.name in HEADINGS
                and (family is None or family.section_heading is None or family.section_heading(node, root)))

    def walk(node, context):
        if isinstance(node, Comment) or isinstance(node, Tag) and not visible(node, root):
            return
        if section_heading(node):
            heading_text = re.sub(r"\s+", " ", "".join(str(text) for text in node.find_all(string=True)
                                   if not isinstance(text, Comment) and visible(text.parent, root))).strip()
            if not heading_text or len(heading_text) > 1000:
                raise KaynakBolumHatasi("invalid_heading")
            if family is not None and family.anchor is not None:
                anchor = family.anchor(node, root)
            else:
                anchor = node.get("id")
                if anchor is None and isinstance(node.parent, Tag) and node.parent is not root:
                    first = next((item for item in node.parent.children if isinstance(item, Tag)), None)
                    if first is node:
                        anchor = node.parent.get("id")
            drafts.append({"level": int(node.name[1]), "heading_hash": canonical_hash(heading_text),
                           "anchor_hash": canonical_hash(anchor) if isinstance(anchor, str) and anchor else None,
                           "parts": [[context, structure(node, url, rules=rules)]]})
            if len(drafts) > MAX_SECTIONS + 1:
                raise KaynakBolumHatasi("document_limit_exceeded")
            return
        if isinstance(node, Tag) and any(visible(child, root) and section_heading(child) for child in node.find_all(list(HEADINGS))):
            child_context = context + [[node.name, attributes(node, url, rules.family)]]
            for child in node.children:
                walk(child, child_context)
        else:
            value = structure(node, url, rules=rules)
            if value is not None:
                drafts[-1]["parts"].append([context, value])

    walk(root, [])
    drafts = [draft for draft in drafts if draft["parts"]]
    heading_counts = Counter(draft["heading_hash"] for draft in drafts)
    anchors = Counter(draft["anchor_hash"] for draft in drafts if draft["anchor_hash"])
    sections, parents, occurrence = [], [], Counter()
    for order, draft in enumerate(drafts):
        while parents and parents[-1]["level"] >= draft["level"]:
            parents.pop()
        parent = parents[-1] if parents else None
        parent_path = parent["path"] if parent else []
        occurrence[(parent["section_id"] if parent else None, draft["heading_hash"])] += 1
        index = occurrence[(parent["section_id"] if parent else None, draft["heading_hash"])]
        path = parent_path + [[draft["heading_hash"], index]]
        section = {"section_id": canonical_hash({"extractor_version": version, "path": path}),
                   "heading_hash": draft["heading_hash"], "section_hash": canonical_hash(draft["parts"]),
                   "level": draft["level"], "order": order, "parent_id": parent["section_id"] if parent else None,
                   "anchor_hash": draft["anchor_hash"], "duplicate_heading": heading_counts[draft["heading_hash"]] > 1}
        sections.append(section)
        if draft["level"]:
            parents.append({**section, "path": path})
    ambiguous = any(section["duplicate_heading"] and (section["anchor_hash"] is None or anchors[section["anchor_hash"]] != 1)
                    or section["anchor_hash"] is not None and anchors[section["anchor_hash"]] > 1 for section in sections)
    return {"policy_version": REPRESENTATION, "extractor_version": version,
            "canonical_url": url, "body_hash": canonical_hash(body_structure), "sections": sections, "ambiguous": ambiguous}


def validate_document(value):
    try:
        if not isinstance(value, dict) or set(value) != DOCUMENT_FIELDS or not isinstance(value.get("ambiguous"), bool):
            raise ValueError
        if not HEX64.fullmatch(value.get("body_hash", "")) or not isinstance(value.get("sections"), list) or not 1 <= len(value["sections"]) <= MAX_SECTIONS:
            raise ValueError
        ids = set()
        for order, section in enumerate(value["sections"]):
            if (not isinstance(section, dict) or set(section) != SECTION_FIELDS or type(section.get("level")) is not int or not 0 <= section["level"] <= 6
                    or type(section.get("order")) is not int or section["order"] != order
                    or not isinstance(section.get("duplicate_heading"), bool)):
                raise ValueError
            for field in ("section_id", "section_hash", "heading_hash"):
                if not HEX64.fullmatch(section.get(field, "")):
                    raise ValueError
            if section.get("anchor_hash") is not None and not HEX64.fullmatch(section["anchor_hash"]):
                raise ValueError
            if section.get("parent_id") is not None and section["parent_id"] not in ids:
                raise ValueError
            if section["section_id"] in ids:
                raise ValueError
            ids.add(section["section_id"])
        if canonical_source_url(value["canonical_url"]) != value["canonical_url"]:
            raise ValueError
        for field in ("extractor_version", "policy_version"):
            if not isinstance(value[field], str) or not re.fullmatch(r"[a-z][a-z0-9.-]{0,79}", value[field]):
                raise ValueError
    except (ValueError, TypeError, KeyError):
        raise KaynakBolumHatasi("invalid_document_manifest") from None


def compare_documents(previous, current):
    """Tekil bağlar eşlenir; yapısal belirsizlik veya açıklanamayan gövde farkı unchanged olmaz."""
    validate_document(previous)
    validate_document(current)
    before, after = previous["sections"], current["sections"]
    comparable = all(previous[key] == current[key] for key in ("canonical_url", "policy_version", "extractor_version"))
    if not comparable:
        return {"status": "ambiguous", "added": [row["section_id"] for row in after],
                "removed": [row["section_id"] for row in before], "changed": [], "moved": [], "ambiguous": True}
    left, right = {row["section_id"]: row for row in before}, {row["section_id"]: row for row in after}
    matches = {}
    for field in ("anchor_hash", "section_id", "heading_hash", "section_hash"):
        old_groups, new_groups = {}, {}
        for identifier, row in left.items():
            if row.get(field):
                old_groups.setdefault(row[field], []).append(identifier)
        for identifier, row in right.items():
            if row.get(field):
                new_groups.setdefault(row[field], []).append(identifier)
        for value in old_groups.keys() & new_groups.keys():
            if len(old_groups[value]) == len(new_groups[value]) == 1:
                old_id, new_id = old_groups[value][0], new_groups[value][0]
                matches[old_id] = new_id
                del left[old_id], right[new_id]
    pairs = [{"previous_id": row["section_id"], "current_id": matches[row["section_id"]]}
             for row in before if row["section_id"] in matches]
    old_rows = {row["section_id"]: row for row in before}
    new_rows = {row["section_id"]: row for row in after}
    new_order = {row["section_id"]: index for index, row in enumerate(row for row in after if row["section_id"] in matches.values())}
    changed, moved = [], []
    for index, pair in enumerate(pairs):
        old, new = old_rows[pair["previous_id"]], new_rows[pair["current_id"]]
        if old["section_hash"] != new["section_hash"]:
            changed.append(pair)
        if new_order[new["section_id"]] != index or matches.get(old["parent_id"], old["parent_id"]) != new["parent_id"] or old["level"] != new["level"]:
            moved.append(pair)
    ambiguous = previous["ambiguous"] or current["ambiguous"]
    if previous["body_hash"] != current["body_hash"] and not (left or right or changed or moved):
        ambiguous = True
    # Aynı gövde hash'iyle farklı bölüm bildirimi bozuk veya farklı bir çıkarım kaydıdır.
    if previous["body_hash"] == current["body_hash"] and before != after:
        ambiguous = True
    status = "ambiguous" if ambiguous else "changed" if left or right or changed or moved else "unchanged"
    return {"status": status, "added": list(right), "removed": list(left), "changed": changed, "moved": moved, "ambiguous": ambiguous}
