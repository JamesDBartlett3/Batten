"""Pluggable alert providers.

Batten's alert layer is not hard-wired to any one service. A provider turns
some external feed — NWS, a CAP 1.2 Atom feed from a national weather
service, or any JSON endpoint — into Batten's common alert shape:

    {id, event, severity, headline, area, sent, ends, description, instruction}

Users configure providers in config.json:

    "alert_providers": [
      {"type": "nws"},
      {"type": "cap", "url": "https://met-service.example/alerts.atom",
       "area_filter": "Oaxaca"},
      {"type": "generic", "url": "https://city.example/api/alerts.json",
       "headers": {"Authorization": "Bearer ..."},
       "list_path": "alerts",
       "fields": {"event": "type", "area": "neighborhood"}}
    ]

Missing/empty config falls back to a single NWS provider (US + territories).
All providers go through noaa's transport helpers, so the same failure
classification (dns/timeout/http/parse) applies everywhere, and tests can
fake the network in one place.
"""

from __future__ import annotations

from ..errors import FetchError

__all__ = ["AlertProvider", "FetchError", "fetch_all", "get_providers", "register"]

_DESCRIPTION_LIMIT = 1500
_INSTRUCTION_LIMIT = 800

_REGISTRY: dict[str, type] = {}


def register(type_name: str):
    """Class decorator: @register("cap"). Lookup is case-insensitive."""
    def deco(cls):
        _REGISTRY[type_name.lower()] = cls
        return cls
    return deco


def provider_for(spec: dict) -> type:
    t = str((spec or {}).get("type", "")).strip().lower()
    if t not in _REGISTRY:
        known = ", ".join(sorted(_REGISTRY)) or "none registered"
        raise FetchError("parse", f"unknown alert provider type '{t}' (known: {known})")
    return _REGISTRY[t]


class AlertProvider:
    """Base class. Subclasses implement fetch() and test()."""

    name = "provider"

    def __init__(self, spec: dict | None = None):
        self.spec = dict(spec or {})

    def _require(self, key: str) -> str:
        val = str(self.spec.get(key) or "").strip()
        if not val:
            raise FetchError("parse",
                             f"{self.name} provider missing required key '{key}'")
        return val

    def fetch(self, cfg: dict) -> list[dict]:
        raise NotImplementedError

    def test(self, cfg: dict) -> tuple[bool, str]:
        """Cheap check for `batten doctor`: returns (ok, detail)."""
        try:
            alerts = self.fetch(cfg)
            return True, f"reachable, {len(alerts)} active alert(s)"
        except FetchError as exc:
            return False, f"{exc.kind}: {exc.detail}"

    # -- shared helpers -----------------------------------------------------

    @staticmethod
    def _alert(item: dict, **overrides) -> dict:
        """Coerce arbitrary item fields into the common alert shape."""
        def s(key: str) -> str:
            v = item.get(key, "")
            if isinstance(v, (dict, list)):
                import json
                v = json.dumps(v)
            return str(v if v is not None else "")
        out = {
            "id": s("id"),
            "event": s("event"),
            "severity": s("severity") or "Unknown",
            "headline": s("headline"),
            "area": s("area"),
            "sent": s("sent"),
            "ends": s("ends"),
            "description": s("description")[:_DESCRIPTION_LIMIT],
            "instruction": s("instruction")[:_INSTRUCTION_LIMIT],
        }
        out.update({k: v for k, v in overrides.items() if v is not None})
        return out


def get_providers(cfg: dict) -> list[AlertProvider]:
    """Instantiate the configured providers (default: one NWS at cfg's point)."""
    specs = cfg.get("alert_providers")
    if not specs:
        specs = [{"type": "nws"}]
    out = []
    for spec in specs:
        cls = provider_for(spec)
        merged = dict(spec)
        # NWS reads location from the spec, falling back to cfg coordinates.
        if cls.__name__ == "NWSProvider":
            merged.setdefault("lat", cfg.get("lat"))
            merged.setdefault("lon", cfg.get("lon"))
        out.append(cls(merged))
    return out


def fetch_all(cfg: dict) -> tuple[list[dict], list[str]]:
    """Run every configured provider. Never raises.

    Returns (merged_alerts, notes) — each provider failure becomes a note and
    the remaining providers still contribute. Alerts are deduped by id.
    """
    alerts: list[dict] = []
    notes: list[str] = []
    seen: set[str] = set()
    for prov in get_providers(cfg):
        try:
            for a in prov.fetch(cfg):
                aid = a.get("id") or f"{prov.name}:{a.get('event')}:{a.get('area')}"
                if aid in seen:
                    continue
                seen.add(aid)
                alerts.append(a)
        except FetchError as exc:
            notes.append(f"{prov.name} provider: {exc.kind} — {exc.detail}")
    return alerts, notes


# Built-ins register themselves on import. Keep these at the bottom: the
# provider modules import this package's register/AlertProvider.
from . import cap, generic_json, nws  # noqa: E402,F401
