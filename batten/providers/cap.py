"""CAP 1.2 alert provider — OASIS Common Alerting Protocol feeds.

Most national weather services outside the US publish alerts as CAP
(in Atom/RSS feeds or XML endpoints). This provider reads an Atom-style
feed and maps each <entry>'s cap:* extension elements onto Batten's alert
shape. Parsing is namespace-tolerant: tags are matched by local name, so
it works across the namespace variations seen in the wild.

Config:
    {"type": "cap", "url": "https://met.service/alerts.atom",
     "area_filter": "OptionalSubstring"}   # keep alerts whose area mentions it
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from ..errors import FetchError
from . import register, AlertProvider


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(elem: ET.Element, name: str) -> ET.Element | None:
    for c in elem:
        if _local(c.tag) == name:
            return c
    return None


def _text(elem: ET.Element, name: str) -> str:
    c = _child(elem, name)
    if c is None:
        return ""
    return (" ".join(c.itertext())).strip()


@register("cap")
class CAPProvider(AlertProvider):
    name = "cap"

    def fetch(self, cfg: dict) -> list[dict]:
        from .. import noaa  # lazy import: transport is fakeable in tests

        url = self._require("url")
        raw = noaa.http_get_text(url, cfg)
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            raise FetchError("parse", f"CAP feed is not valid XML: {exc}") from exc

        area_filter = str(self.spec.get("area_filter") or "").strip().lower()
        alerts = []
        # Atom wraps items in <entry>; plain RSS uses <item>; bare CAP uses <alert>.
        entries = [e for e in root.iter() if _local(e.tag) in ("entry", "item", "alert")]
        for entry in entries:
            event = _text(entry, "event") or _text(entry, "alertType") or ""
            if not event:
                continue
            area = _text(entry, "areaDesc")
            if not area:
                area_elem = _child(entry, "area")
                if area_elem is not None:
                    area = _text(area_elem, "areaDesc") or (" ".join(area_elem.itertext())).strip()
            if area_filter and area_filter not in area.lower() \
                    and area_filter not in event.lower():
                continue
            sent = (_text(entry, "effective") or _text(entry, "onset")
                    or _text(entry, "published") or _text(entry, "updated"))
            ends = _text(entry, "expires")
            headline = _text(entry, "headline") or _text(entry, "title")
            description = _text(entry, "description") or _text(entry, "summary")
            instruction = _text(entry, "instruction")
            alert_id = _text(entry, "id") or _text(entry, "identifier")
            if not alert_id:
                link = _child(entry, "link")
                if link is not None:
                    alert_id = link.get("href", "") or link.get("href", "")
            alerts.append(self._alert(
                {
                    "id": alert_id,
                    "event": event,
                    "severity": _text(entry, "severity") or "Unknown",
                    "headline": headline,
                    "area": area,
                    "sent": sent,
                    "ends": ends,
                    "description": description,
                    "instruction": instruction,
                },
                headline=headline[:300],
            ))
        return alerts
