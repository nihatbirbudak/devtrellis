"""Belge üreticisine göre önceden yazılmış statik belge sözleşmeleri (K0 ikinci dilim).

Her sözleşme yalnız kendi üreticisinin açık işaretiyle seçilir: kök öğe, üreticinin arayüz parçaları
(kalıcı bağlantı, kopyalama düğmesi, son değişiklik tarihi gibi) ve bölüm başlığı bağı adıyla yazılıdır.
Genel çıkarıcının kuralları gevşetilmez: sözleşmede adı geçmeyen etkin içerik, görsel veya yapı yine reddedilir.
Görseller ve gömülü adresler yalnız adresleri (ve alt metni) ile temsil edilir; bayt içerikleri okunmaz.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from bs4 import Tag


class AileHatasi(ValueError):
    """Sözleşme seçimi veya kökü belirsiz; içerik ya da adres hata metnine yazılmaz."""


# DocBook XSL çıktısında bölüm ve bileşen kapsayıcıları. Bu sınıflar tek başına aile seçmez;
# başlık sayfası (titlepage) veya refentry ad bloğu da gerekir.
DOCBOOK_COMPONENTS = frozenset({"set", "book", "part", "reference", "chapter", "appendix", "preface", "article",
                                "refentry", "sect1", "section", "glossary", "bibliography", "index", "colophon"})
DOCBOOK_SECTIONS = DOCBOOK_COMPONENTS | frozenset({"sect2", "sect3", "sect4", "sect5", "simplesect", "refsect1",
                                                    "refsect2", "refsect3", "refnamediv", "refsynopsisdiv"})
SVG_SHAPES = frozenset({"svg", "path", "g", "polygon", "polyline", "circle", "ellipse", "rect", "line", "use",
                        "title", "desc", "defs", "symbol", "clippath", "lineargradient", "radialgradient", "stop"})


def _classes(node):
    return set(node.get("class") or []) if isinstance(node, Tag) else set()


def _generators(soup):
    return [str(node.get("content", "")).strip() for node in soup.select("head meta[name=generator], meta[name=generator]")]


def _one(nodes):
    unique = list({id(node): node for node in nodes}.values())
    return unique[0] if len(unique) == 1 else None


def _heading_anchor(node):
    """Başlığın kendi kimliği; yoksa başlığın ilk öğesi olduğu kapsayıcının kimliği (genel kuralın aynısı)."""
    anchor = node.get("id")
    if not anchor and isinstance(node.parent, Tag):
        first = next((item for item in node.parent.children if isinstance(item, Tag)), None)
        if first is node:
            anchor = node.parent.get("id")
    return anchor if isinstance(anchor, str) and anchor else None


@dataclass(frozen=True)
class Chrome:
    """Kök içinde adıyla bilinen üretici arayüz parçası; yalnız koşul tutarsa çıkarılır."""
    selector: str
    reason: str
    condition: object = None

    def matches(self, node):
        return self.condition is None or self.condition(node)


@dataclass(frozen=True)
class Family:
    name: str
    version: int
    kind: str  # "generator" ya da "site" (siteye özel, ayrıca kayda geçirilmiş deney)
    detect: object  # soup, url -> bool (yalnız açık üretici/site işareti)
    root: object  # soup -> Tag | None (tam bir kök; değilse sözleşme uyuşmazlığı)
    chrome: tuple = ()
    icons: tuple = ()  # aria-hidden olmasa da süs sayılan SVG seçicileri
    volatile_classes: object = None  # derleme başına değişen sınıf adı deseni
    section_heading: object = None  # node, root -> bool; None ise her görünür başlık bölüm başlatır
    anchor: object = None  # node, root -> str | None
    embeds: frozenset = field(default_factory=frozenset)  # adresle temsil edilen gömülü öğe etiketleri

    @property
    def label(self):
        return f"{self.name}-{self.version}"


# --- DocBook XSL (PostgreSQL belgeleri, man sayfaları) -------------------------------------------------

def _docbook_component(node):
    return isinstance(node, Tag) and node.name == "div" and bool(_classes(node) & DOCBOOK_COMPONENTS)


def _docbook_titled(node):
    return any(isinstance(child, Tag) and child.name == "div" and _classes(child) & {"titlepage", "refnamediv"}
               for child in node.children)


def _docbook_roots(soup):
    return [node for node in soup.find_all("div") if _docbook_component(node) and _docbook_titled(node)
            and not any(_docbook_component(parent) for parent in node.parents)]


def _docbook_container(node):
    return next((parent for parent in node.parents
                 if isinstance(parent, Tag) and parent.name == "div" and _classes(parent) & DOCBOOK_SECTIONS), None)


def _docbook_section_heading(node, root):
    """Bölüm başlığı yalnız kapsayıcının başlık sayfasındaki ya da doğrudan çocuğu olan başlıktır.

    Not/İpucu/Uyarı kutularının başlıkları (`div.note > h3.title`) bölüm açmaz; içinde bulundukları bölümün
    içeriğidir. Böylece aynı adlı kutu başlıkları sırayla eşlenmeye çalışılmaz.
    """
    container = _docbook_container(node)
    if container is None:
        return False
    if node.parent is container:
        return True
    titlepage = next((parent for parent in node.parents if parent is container or _classes(parent) & {"titlepage"}), None)
    return titlepage is not None and titlepage is not container and titlepage.parent is container


def _legacy_anchor(node):
    value = node.get("id") or node.get("name")
    return value if node.name == "a" and isinstance(value, str) and value and not node.get_text(strip=True) else None


def _docbook_anchor(node, root):
    """Bağ önce başlığın kendi kimliği, sonra DocBook'un bölüme verdiği kimlik, en son eski `a[name]` işareti.

    Eski DocBook XSL çıktısı (man sayfaları) bölüm kimliğini başlığın hemen önüne ya da içine boş `<a name>`
    olarak yazar. Yalnız başlığa bitişik boş işaretler kabul edilir; araya başka öğe girerse bağ yoktur.
    """
    own = node.get("id")
    if isinstance(own, str) and own:
        return own
    container = _docbook_container(node)
    if container is not None and isinstance(container.get("id"), str) and container.get("id"):
        return container["id"]
    inside = next((value for value in map(_legacy_anchor, node.find_all("a")) if value), None)
    if inside:
        return inside
    for sibling in node.previous_siblings:
        if isinstance(sibling, Tag):
            return _legacy_anchor(sibling)
        if str(sibling).strip():
            return None
    return None


DOCBOOK = Family(
    name="docbook", version=1, kind="generator",
    detect=lambda soup, url: bool(_docbook_roots(soup)),
    root=lambda soup: _one(_docbook_roots(soup)),
    section_heading=_docbook_section_heading, anchor=_docbook_anchor)


# --- Sphinx (Docutils üreteci) ---------------------------------------------------------------------------

def _sphinx_root(soup):
    main = _one(soup.select("[role=main]"))
    if main is None:
        return None
    inner = main.select("[itemprop=articleBody]") or main.select("article.bd-article")
    return _one(inner) if inner else main


SPHINX = Family(
    name="sphinx", version=1, kind="generator",
    detect=lambda soup, url: any(value.startswith("Docutils ") for value in _generators(soup)),
    root=_sphinx_root,
    chrome=(Chrome("a.headerlink", "kalıcı bağlantı işareti"),
            Chrome("button.copybtn", "kopyalama düğmesi")))


# --- MkDocs Material ---------------------------------------------------------------------------------------

def _mkdocs_material(soup):
    return any(re.match(r"mkdocs-\d", value) and "mkdocs-material-" in value for value in _generators(soup))


MKDOCS_MATERIAL = Family(
    name="mkdocs-material", version=1, kind="generator",
    detect=lambda soup, url: _mkdocs_material(soup),
    root=lambda soup: _one(soup.select("article.md-content__inner")),
    chrome=(Chrome("a.headerlink", "kalıcı bağlantı işareti"),
            Chrome("article.md-content__inner > a.md-content__button", "düzenle / kaynağı gör düğmesi"),
            Chrome("aside.md-source-file", "derleme tarihi ve katkı bilgisi"),
            Chrome("button.md-clipboard", "kopyalama düğmesi")),
    icons=("span.twemoji > svg",))


# --- Docusaurus --------------------------------------------------------------------------------------------

DOCUSAURUS = Family(
    name="docusaurus", version=1, kind="generator",
    detect=lambda soup, url: any(re.match(r"Docusaurus v\d", value) for value in _generators(soup)),
    root=lambda soup: _one(soup.select(".theme-doc-markdown")),
    chrome=(Chrome("a.hash-link", "kalıcı bağlantı işareti"),
            Chrome(".theme-code-block button", "kopyalama / satır kaydırma düğmesi")),
    icons=(".theme-admonition [class*=admonitionIcon] svg",),
    # CSS modülü sınıfları (ör. codeBlockContainer_Ckt0) her derlemede değişebilir.
    volatile_classes=re.compile(r"[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9-]{4}\Z"))


# --- Hugo + Docsy ------------------------------------------------------------------------------------------

def _hugo(soup):
    return any(re.match(r"Hugo \d", value) for value in _generators(soup))


HUGO_DOCSY = Family(
    name="hugo-docsy", version=1, kind="generator",
    detect=lambda soup, url: _hugo(soup),
    # Hugo temaları serbesttir; yalnız Docsy'nin tek içerik kökü desteklenir, başka tema sözleşme uyuşmazlığıdır.
    root=lambda soup: _one(soup.select("div.td-content")),
    chrome=(Chrome("a.td-heading-self-link", "kalıcı bağlantı işareti"),
            Chrome("div.td-page-meta__lastmod", "son değişiklik tarihi ve commit bağlantısı")))


# --- Astro Starlight ---------------------------------------------------------------------------------------

def _expressive_loader(node):
    # Kod bloğu bileşeninin dış dosya yükleyicisi: satır içi kod yok, yalnız src/href.
    if node.name == "script":
        return (node.get("type") == "module" and bool(node.get("src")) and not node.get_text(strip=True)
                and set(node.attrs) <= {"type", "src"})
    if node.name == "link":
        return node.get("rel") == ["stylesheet"] and bool(node.get("href")) and set(node.attrs) <= {"rel", "href"}
    return False


STARLIGHT = Family(
    name="starlight", version=1, kind="generator",
    detect=lambda soup, url: any(value.startswith("Starlight v") for value in _generators(soup)),
    root=lambda soup: _one(soup.select(".sl-markdown-content")),
    chrome=(Chrome("div.sl-heading-wrapper > a.sl-anchor-link", "kalıcı bağlantı işareti"),
            Chrome("div.expressive-code > script", "kod bloğu bileşeni yükleyicisi", _expressive_loader),
            Chrome("div.expressive-code > link", "kod bloğu bileşeni stil dosyası", _expressive_loader),
            Chrome("div.expressive-code div.copy", "kopyalama düğmesi")),
    volatile_classes=re.compile(r"astro-[a-z0-9]{8}\Z"))


# --- Siteye özel, ayrıca kayda geçirilmiş deney (E2) -----------------------------------------------------

VUEPRESS1 = Family(
    name="vuepress1", version=1, kind="site",
    detect=lambda soup, url: any(re.match(r"VuePress 1\.", value) for value in _generators(soup)),
    root=lambda soup: _one(soup.select(".theme-default-content.content__default")),
    chrome=(Chrome("a.header-anchor", "kalıcı bağlantı işareti"),))


def _nodered(soup, url):
    parts = urlsplit(url)
    return parts.hostname == "nodered.org" and parts.path.startswith("/docs/") and bool(soup.select("div.docs-content > h1.docs-title"))


NODERED = Family(
    name="nodered-docs", version=1, kind="site",
    detect=_nodered,
    root=lambda soup: _one(soup.select("div.docs-content")),
    embeds=frozenset({"iframe"}))


FAMILIES = (DOCBOOK, SPHINX, MKDOCS_MATERIAL, DOCUSAURUS, HUGO_DOCSY, STARLIGHT, VUEPRESS1, NODERED)


def detect_family(soup, url):
    """Tek bir sözleşme açıkça eşleşirse onu, hiçbiri eşleşmezse None döndürür; birden çoğu belirsizdir."""
    matched = [family for family in FAMILIES if family.detect(soup, url)]
    if len(matched) > 1:
        raise AileHatasi("ambiguous_document_family")
    return matched[0] if matched else None


def family_root(family, soup):
    root = family.root(soup)
    if not isinstance(root, Tag):
        # Üretici işareti var ama sözleşmenin kökü yok ya da birden çok: genel kurala düşülmez.
        raise AileHatasi("family_contract_mismatch")
    return root


def remove_chrome(family, root):
    """Sözleşmede adı geçen arayüz parçalarını çıkarır; kök dışındaki eşleşmelere dokunmaz."""
    removed = 0
    for rule in family.chrome:
        for node in root.select(rule.selector):
            if node is root or not rule.matches(node):
                continue
            node.decompose()
            removed += 1
    return removed


def icon_nodes(family, root):
    return {id(node) for selector in family.icons for node in root.select(selector) if node.name == "svg"}


def decorative_svg(node, declared):
    """Süs SVG'si: aria-hidden="true" ya da sözleşmede adıyla süs; içinde yalnız şekil öğeleri olabilir."""
    if node.name != "svg":
        return False
    if node.get("aria-hidden") != "true" and id(node) not in declared:
        return False
    return all(child.name in SVG_SHAPES for child in node.find_all(True))


def clean_classes(family, value):
    if family is None or family.volatile_classes is None or not isinstance(value, list):
        return value
    return [item for item in value if not family.volatile_classes.match(item)]
