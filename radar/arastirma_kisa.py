"""MCP arama adayını küçük tutar; kaynak, sürüm ve belirsizliği kaybetmez."""

import copy
import json

from radar.arastirma_ozeti import arama_ozeti


BRIEF_TARGET_BYTES = 5120


def kisa_arama_sonucu(result):
    """Doğrulanmış arama kopyasını daraltır; paylaşılabilir paket üretmez."""
    summary = arama_ozeti(result)
    answer = {key: copy.deepcopy(value) for key, value in summary.items() if key != "items"}
    answer.update(view="brief", exportable=False, items=[])
    for item in summary["items"]:
        packet = item["packet"]
        kept = {key: copy.deepcopy(packet[key]) for key in
                ("title", "summary", "sources", "url", "tags", "author", "license",
                 "created_at", "updated_at", "provenance", "lineage") if key in packet}
        provenance = kept.get("provenance")
        if isinstance(provenance, dict):
            if provenance.get("question") == kept.get("title"):
                provenance.pop("question", None)
            if provenance.get("source_url") == "":
                provenance.pop("source_url", None)
        if kept.get("url") == "":
            kept.pop("url", None)
        card = {key: copy.deepcopy(item[key]) for key in
                ("id", "version", "status", "report_url", "updated_at", "body_char_count", "search_match", "public_corrections")
                if key in item}
        card["packet"] = kept
        engagement = item.get("engagement")
        if isinstance(engagement, dict):
            card["engagement"] = {key: engagement[key] for key in ("needs_update", "disputed") if key in engagement}
        relations = item.get("relationships")
        if isinstance(relations, dict):
            card["relationships"] = {key: copy.deepcopy(relations[key]) for key in ("parents", "children_total")
                                     if key in relations}
            card["relationships"]["details_in_full_report"] = True
        answer["items"].append(card)
    # Kaynak, uyarı veya özet cümlesi boyut için sessizce kesilmez.
    size = len(json.dumps(answer, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))
    if size > BRIEF_TARGET_BYTES:
        answer["size_notice"] = {"target_bytes": BRIEF_TARGET_BYTES, "payload_bytes_before_notice": size,
                                 "exceeded": True, "content_truncated": False}
    return answer
