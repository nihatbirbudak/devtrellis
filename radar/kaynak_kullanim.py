"""Kaynağın kullanım sinyallerini (robots Content-Signal, X-Robots-Tag, meta robots) metin saklamadan yorumlar.

K0 kontrol yolu zaten model çağırmaz ve ham sayfa saklamaz; bu modül kaynağın isteğini kayda geçirir ki sonraki
katmanlar (K1 kart/özet) uygulayabilsin: `model_input` kısıtlıysa kaynak yalnız hash kontrolünde kalır, kaynak
metninden model özeti üretilmez. `ai-input=no`, `noarchive`, `nocache`, `none` ve `noai` bu kısıtı koyar.
Tanınmayan değer "ambiguous" (belirsiz) sayılır; hiçbir zaman izin sayılmaz.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

SCHEMA = "usage-signals-v1"
AGENT = "ufukradar"
CONTENT_KEYS = {"search": "search", "ai-input": "ai_input", "ai-train": "ai_train"}
FLAGS = frozenset({"all", "index", "follow", "noindex", "nofollow", "none", "noarchive", "nocache", "nosnippet",
                   "notranslate", "noimageindex", "noai", "noimageai", "indexifembedded"})
VALUED = frozenset({"max-snippet", "max-image-preview", "max-video-preview", "unavailable_after"})
MAX_VALUE = 1024


def _state(restricted, unknown):
    return "restricted" if restricted else "ambiguous" if unknown else "not_restricted"


def parse_content_signal(values):
    """robots.txt Content-Signal değerleri: `search=yes, ai-input=no, ai-train=no` biçimi.

    None: robots kaydı yok (denetlenmedi). Boş liste: robots var, sinyal yok. Anahtar başına "yes", "no",
    tanınmayan değerde "unknown"; yazılmamış anahtar None. Tanınmayan anahtar `unrecognized` yapar.
    Çelişen iki değer "unknown" olur.
    """
    if values is None:
        return None
    result = {name: None for name in CONTENT_KEYS.values()}
    unrecognized = False
    for value in values:
        if not isinstance(value, str):
            unrecognized = True
            continue
        for item in value[:MAX_VALUE].split(","):
            if not item.strip():
                continue
            key, separator, answer = (part.strip().lower() for part in item.partition("="))
            name = CONTENT_KEYS.get(key)
            if name is None or not separator:
                unrecognized = True
                continue
            answer = answer if answer in {"yes", "no"} else "unknown"
            result[name] = answer if result[name] in (None, answer) else "unknown"
    return {**result, "present": bool(values), "unrecognized": unrecognized}


def parse_directives(values, *, scoped):
    """X-Robots-Tag ya da meta robots değerleri. Başka ajana yazılmış (`googlebot: noindex`) yönergeler atlanır.

    Dönen: tanınan yönergelerin sıralı listesi ve tanınmayan değer olup olmadığı. Değer metni saklanmaz;
    `max-snippet` gibi sayısal yönergeden yalnız sıfır olup olmadığı tutulur.
    """
    if values is None:
        return None
    directives, unrecognized = set(), False
    for value in values:
        if not isinstance(value, str):
            unrecognized = True
            continue
        scope = None
        for item in value[:MAX_VALUE].split(","):
            token = item.strip().lower()
            if not token:
                continue
            prefix = re.fullmatch(r"([a-z0-9_.-]+)\s*:\s*(.*)", token)
            if scoped and prefix and prefix.group(1) not in VALUED:
                scope, token = prefix.group(1), prefix.group(2).strip()
                if not token:
                    continue
            if scope not in (None, AGENT):
                continue
            name, _, argument = (part.strip() for part in token.partition(":"))
            if name in FLAGS and not argument:
                directives.add(name)
            elif name in VALUED and argument:
                if name == "max-snippet" and argument == "0":
                    directives.add("max-snippet:0")
                elif name == "max-image-preview" and argument == "none":
                    directives.add("max-image-preview:none")
            else:
                unrecognized = True
    return {"directives": sorted(directives), "present": bool(values), "unrecognized": unrecognized}


def meta_robots(body):
    """Sayfanın `<meta name="robots">` ve bu ajana özel `<meta name="ufukradar">` değerleri (yalnız head içi)."""
    if not isinstance(body, (bytes, str)) or not body:
        return None
    soup = BeautifulSoup(body, "html.parser")
    head = soup.head or soup
    values = [str(node.get("content", "")) for node in head.find_all("meta")
              if str(node.get("name", "")).strip().lower() in {"robots", AGENT}]
    return parse_directives(values, scoped=False)


def summarize(content_signal, robots_tag, meta, *, checked_at):
    """Üç kaynağı tek kayda çevirir. Kısıt bir kaynakta bile varsa geçerlidir; tanınmayan değer belirsizdir."""
    parts = [part for part in (robots_tag, meta) if part]
    flags = {name for part in parts for name in part["directives"]}
    unknown = any(part["unrecognized"] for part in parts) or bool(content_signal and content_signal["unrecognized"])
    signal = content_signal or {}

    def answer(name):
        return signal.get(name)

    # nocache (bazı arama motorlarının noarchive karşılığı) noarchive gibi model girdisini kısıtlar.
    model = _state(answer("ai_input") == "no" or bool(flags & {"noarchive", "nocache", "none", "noai"}),
                   unknown or answer("ai_input") == "unknown")
    training = _state(answer("ai_train") == "no" or bool(flags & {"noai", "none"}), unknown or answer("ai_train") == "unknown")
    quote = _state(bool(flags & {"nosnippet", "none", "max-snippet:0"}), unknown)
    index = _state(answer("search") == "no" or bool(flags & {"noindex", "none"}), unknown or answer("search") == "unknown")
    checked = {"robots_content_signal": content_signal is not None, "x_robots_tag": robots_tag is not None,
               "meta_robots": meta is not None}
    if not any(checked.values()):
        # Hiçbir kaynak denetlenmediyse (ör. eski kayıt) izin varsayılmaz.
        model = training = quote = index = "not_checked"
    return {"schema": SCHEMA, "checked_at": checked_at, "checked": checked,
            "robots_content_signal": content_signal, "x_robots_tag": robots_tag, "meta_robots": meta,
            "model_input": model, "model_training": training, "quotation": quote, "search_index": index}
