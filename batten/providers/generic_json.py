"""Generic JSON alert provider — point Batten at ANY alert API.

Config:
    {"type": "generic",
     "url": "https://alerts.example.gov/api/current",
     "headers": {"Authorization": "Bearer ..."},     # optional
     "list_path": "alerts",                          # optional: dotted path to the array
     "fields": {                                     # optional: dotted paths per alert key
        "event": "alertType.name",
        "area": "region.description",
        "sent": "issuedAt"}}

Field paths support alternatives separated by "|": "event|type|name" tries
each in order. Sensible defaults cover common key names, so a well-behaved
API often needs nothing but the URL.
"""

from __future__ import annotations

import json

from ..errors import FetchError
from . import register, AlertProvider

DEFAULT_FIELDS = {
    "id": "id|identifier|guid",
    "event": "event|type|alertType|name|title",
    "severity": "severity|level|urgency",
    "headline": "headline|title|summary",
    "area": "area|areaDesc|region|location|areaDescription",
    "sent": "sent|published|effective|time|date|issued",
    "ends": "ends|expires|until|end",
    "description": "description|body|text|message|detail",
    "instruction": "instruction|response|action",
}


def _dig(item, path: str):
    """Navigate a dotted path; returns None when any hop is missing."""
    cur = item
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


_LEAF_KEYS = ("name", "label", "text", "value", "title", "description", "code")


def _pick(item: dict, spec: str):
    for alt in spec.split("|"):
        alt = alt.strip()
        if not alt:
            continue
        val = _dig(item, alt)
        if val is None or val == "":
            continue
        while isinstance(val, dict):          # "type": {"name": ...} shapes
            for leaf in _LEAF_KEYS:
                if val.get(leaf):
                    val = val[leaf]
                    break
            else:
                break
        return val
    return None


def _stringify(val) -> str:
    if val is None:
        return ""
    if isinstance(val, (dict, list)):
        return json.dumps(val)
    return str(val)


@register("generic")
class GenericJSONProvider(AlertProvider):
    name = "generic"

    def _fetch_raw(self, cfg: dict) -> list:
        from .. import noaa  # lazy import: transport is fakeable in tests

        url = self._require("url")
        headers = {str(k): str(v) for k, v in (self.spec.get("headers") or {}).items()}
        data = noaa.http_get_json(url, cfg, headers=headers)
        list_path = str(self.spec.get("list_path") or "").strip()
        items = _dig(data, list_path) if list_path else data
        if not isinstance(items, list):
            raise FetchError(
                "parse",
                f"expected a JSON array at {'root' if not list_path else list_path!r}; "
                f"got {type(items).__name__} — set 'list_path'")
        return items

    def fetch(self, cfg: dict) -> list[dict]:
        fields = dict(DEFAULT_FIELDS)
        for key, spec in (self.spec.get("fields") or {}).items():
            fields[key] = str(spec)
        alerts = []
        for item in self._fetch_raw(cfg):
            if not isinstance(item, dict):
                continue
            row = {key: _stringify(_pick(item, spec)) for key, spec in fields.items()}
            if not row.get("event"):
                continue  # not a recognizable alert
            alerts.append(self._alert(row))
        return alerts
