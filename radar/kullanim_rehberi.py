"""Paketin İngilizce ve Türkçe rehberini dış içerik çalıştırmadan tarayıcıda okunabilir sunar."""

import html
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
TOKEN = re.compile(r"\[([^\]]+)\]\(([^)]+)\)|`([^`]+)`|\*\*([^*]+)\*\*")


def satir(value):
    result, cursor = [], 0
    for match in TOKEN.finditer(value):
        result.append(html.escape(value[cursor:match.start()]))
        label, target, code, strong = match.groups()
        if label is not None:
            if target.startswith("https://"):
                destination = target
            elif target.split("#")[0] in {"README.md", "README.tr.md", "GUIDE.md", "GUIDE.tr.md"}:
                language = "tr" if ".tr.md" in target else "en"
                document = "installation" if target.startswith("README") else "research"
                destination = f"/guide?language={language}&document={document}"
            else:
                destination = None
            result.append(f'<a href="{html.escape(destination, quote=True)}" rel="noreferrer">{html.escape(label)}</a>'
                          if destination else html.escape(label))
        elif code is not None:
            result.append("<code>" + html.escape(code) + "</code>")
        else:
            result.append("<strong>" + html.escape(strong) + "</strong>")
        cursor = match.end()
    result.append(html.escape(value[cursor:]))
    return "".join(result)


def rehber_html(language="en", document="research"):
    paths = {("en", "research"): "GUIDE.md", ("tr", "research"): "GUIDE.tr.md",
             ("en", "installation"): "README-OSS.md", ("tr", "installation"): "README.tr.md"}
    path = ROOT / paths[(language, document)]
    if not path.exists() and language == "en" and document == "installation":
        path = ROOT / "README.md"
    sections, code, paragraph = [], None, []
    def flush():
        if paragraph:
            sections.append("<p>" + satir(" ".join(paragraph)) + "</p>")
            paragraph.clear()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            flush()
            if code is None:
                code = []
            else:
                sections.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
                code = None
        elif code is not None:
            code.append(line.removeprefix("   "))
        elif not stripped:
            flush()
        elif stripped.startswith("#"):
            flush()
            level = min(len(stripped) - len(stripped.lstrip("#")), 6)
            sections.append(f"<h{level}>" + satir(stripped[level:].strip()) + f"</h{level}>")
        elif re.match(r"^(?:\d+\. |[-*] )", stripped):
            flush()
            sections.append("<p>" + satir(stripped) + "</p>")
        else:
            paragraph.append(stripped)
    flush()
    if code is not None:
        sections.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
    back = "Geliştirici alanına dön" if language == "tr" else "Back to developer space"
    return (f'<!doctype html><html lang="{language}"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>DevTrellis — Guide</title><link rel="stylesheet" href="/static/platform.css"></head>'
            f'<body><main class="guide-reader"><nav><a href="/platform">{back}</a></nav>'
            + "".join(sections) + "</main></body></html>")
