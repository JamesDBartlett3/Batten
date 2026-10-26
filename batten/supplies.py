"""Household supply readiness tracker.

The default list is derived from ready.gov / FEMA hurricane guidance and
scales with household size. State lives in ~/.batten/supplies.json so the
local model can reason over "what's still missing" with zero network.

The original storm bot tracked supplies as a phase-grouped markdown file;
here the checklist is structured so the chat can answer "what am I missing?"
and the watcher can nag about gaps before a storm arrives.
"""

from __future__ import annotations

import json

from . import config
from .units import Units, GAL_TO_L, get_units

# qty: (base, per_person) — total = base + per_person * household
DEFAULT_SUPPLIES: list[dict] = [
    # Water
    # Water: 1 gal per person per day, minimum 3 days
    {"id": "water", "cat": "Water", "label": "Drinking water stored (1 gal / 4 L per person per day)",
     "qty": (0, 3), "unit": "gallons total — ×7 days for hurricanes if you can", "essential": True},
    {"id": "water_flush", "cat": "Water", "label": "Extra water for flushing/cleaning (tub filled, buckets)",
     "qty": (1, 0), "unit": "check", "essential": False},
    # Food
    {"id": "food", "cat": "Food", "label": "Non-perishable food, 3+ days (cans, dry goods)",
     "qty": (0, 3), "unit": "days covered", "essential": True},
    {"id": "canopener", "cat": "Food", "label": "Manual can opener",
     "qty": (1, 0), "unit": "check", "essential": True},
    # Power & light
    {"id": "flashlight", "cat": "Power & light", "label": "Flashlights/headlamps (one per person)",
     "qty": (1, 1), "unit": "check", "essential": True},
    {"id": "batteries", "cat": "Power & light", "label": "Spare batteries for every light/radio size",
     "qty": (1, 0), "unit": "check", "essential": True},
    {"id": "powerbank", "cat": "Power & light", "label": "Power banks charged (phone = lifeline)",
     "qty": (1, 1), "unit": "check", "essential": True},
    {"id": "carcharger", "cat": "Power & light", "label": "Car charger / 12V adapters",
     "qty": (1, 0), "unit": "check", "essential": False},
    # Comms & info
    {"id": "radio", "cat": "Comms & info", "label": "Battery/crank NOAA weather radio (WEATHER band)",
     "qty": (1, 0), "unit": "check", "essential": True},
    {"id": "whistle", "cat": "Comms & info", "label": "Whistle to signal for help",
     "qty": (0, 1), "unit": "check", "essential": False},
    {"id": "offlinemaps", "cat": "Comms & info", "label": "Offline maps of your area + evacuation route saved",
     "qty": (1, 0), "unit": "check", "essential": True},
    # Medical & sanitation
    {"id": "firstaid", "cat": "Medical & sanitation", "label": "First-aid kit stocked",
     "qty": (1, 0), "unit": "check", "essential": True},
    {"id": "meds", "cat": "Medical & sanitation", "label": "Prescription meds for 7+ days + list of doses",
     "qty": (1, 0), "unit": "check", "essential": True},
    {"id": "sanitizer", "cat": "Medical & sanitation", "label": "Hand sanitizer, soap, wipes",
     "qty": (1, 0), "unit": "check", "essential": False},
    {"id": "bleach", "cat": "Medical & sanitation", "label": "Unscented bleach (water treatment/sanitize)",
     "qty": (1, 0), "unit": "check", "essential": False},
    {"id": "bags", "cat": "Medical & sanitation", "label": "Garbage bags, zip bags (sanitation + waterproofing)",
     "qty": (1, 0), "unit": "check", "essential": False},
    # Documents & money
    {"id": "docs", "cat": "Documents & money", "label": "ID/passport/insurance/deed copies + photos on phone",
     "qty": (1, 0), "unit": "check", "essential": True},
    {"id": "cash", "cat": "Documents & money", "label": "Cash in small bills (ATM/card systems go down)",
     "qty": (1, 0), "unit": "check", "essential": True},
    # Home & hardware
    {"id": "windows", "cat": "Home & hardware", "label": "Window protection ready: shutters, or pre-cut plywood + fasteners",
     "qty": (1, 0), "unit": "check", "essential": True},
    {"id": "tarps", "cat": "Home & hardware", "label": "Tarps + roof tape (post-storm patching)",
     "qty": (1, 0), "unit": "check", "essential": False},
    {"id": "tools", "cat": "Home & hardware", "label": "Tools, work gloves, duct tape, rope",
     "qty": (1, 0), "unit": "check", "essential": True},
    {"id": "sandbags", "cat": "Home & hardware", "label": "Sandbags/flood barriers if flood-prone",
     "qty": (1, 0), "unit": "check", "essential": False},
    {"id": "fuel", "cat": "Home & hardware", "label": "Vehicle fueled + generator fuel stored SAFELY (never indoors)",
     "qty": (1, 0), "unit": "check", "essential": True},
    {"id": "mosquito", "cat": "Home & hardware", "label": "Mosquito repellent (post-storm standing water)",
     "qty": (1, 0), "unit": "check", "essential": False},
    # Special needs
    {"id": "pets", "cat": "Special needs", "label": "Pet food/meds/carriers for 7 days",
     "qty": (1, 0), "unit": "n/a — mark N/A if no pets", "essential": False},
    {"id": "infant", "cat": "Special needs", "label": "Infant/elder/mobility-specific supplies",
     "qty": (1, 0), "unit": "n/a if not applicable", "essential": False},
    {"id": "gobag", "cat": "Special needs", "label": "Go-bag packed (72h) if evacuation is ordered",
     "qty": (1, 0), "unit": "check", "essential": True},
]


def load_supplies() -> dict:
    p = config.supplies_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_supplies(sup: dict) -> None:
    config.home_dir().mkdir(parents=True, exist_ok=True)
    tmp = config.supplies_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(sup, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(config.supplies_path())


def scaled_quantity(item: dict, household: int, units: Units | None = None) -> str:
    u = units or Units("imperial")
    base, per = item["qty"]
    if per:
        total = base + per * max(1, household)
        if item["id"] == "water":
            if u.metric:
                liters = int(round(total * GAL_TO_L))
                return f"{liters} L total (≈1 L/person/day × 3 days)"
            return f"{total} gallons total (1 gal/person/day × 3 days)"
        if item["id"] == "food":
            return f"{total} person-days of food"
        return str(total)
    return str(base)


def render_supplies(household: int, sup: dict, cfg: dict | None = None) -> tuple[str, int, int]:
    """Returns (text, have_count, missing_count)."""
    u = get_units(cfg) if cfg is not None else None
    lines = []
    have = missing = 0
    cat = None
    for item in DEFAULT_SUPPLIES:
        if item["cat"] != cat:
            cat = item["cat"]
            lines.append(f"\n{cat}:")
        entry = sup.get(item["id"], {})
        if entry.get("have"):
            marker, have = "✅", have + 1
            extra = f" — {entry['note']}" if entry.get("note") else ""
            lines.append(f"  {marker} {item['label']}{extra}")
        elif entry.get("na"):
            lines.append(f"  ➖ {item['label']} (N/A)")
        else:
            marker = "❌" if item["essential"] else "▫️"
            missing += 1
            lines.append(
                f"  {marker} {item['label']}  [target: {scaled_quantity(item, household, u)}; "
                f"{item['unit']}]"
            )
    header = f"\nSupply readiness: {have} ready, {missing} to get"
    return header + "\n" + "\n".join(lines), have, missing


def missing_essentials(household: int, sup: dict) -> list[str]:
    out = []
    for item in DEFAULT_SUPPLIES:
        if not item["essential"]:
            continue
        entry = sup.get(item["id"], {})
        if not entry.get("have") and not entry.get("na"):
            out.append(item["label"])
    return out


def set_item(sup: dict, item_id: str, field_name: str, value: bool | str) -> bool:
    """Mark have/na/note on an item. Returns False if id unknown."""
    if not any(i["id"] == item_id for i in DEFAULT_SUPPLIES):
        return False
    entry = sup.setdefault(item_id, {})
    if field_name in ("have", "na"):
        entry[field_name] = bool(value)
        if field_name == "have" and value:
            entry.pop("na", None)
        if field_name == "na" and value:
            entry.pop("have", None)
    else:
        entry[field_name] = str(value)
    return True
