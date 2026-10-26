"""Render-time unit formatting.

Internal computation stays in NHC/engineering units everywhere (kt, nm,
inches, gallons, deg F); conversion happens only here, at render time.
The preference comes from cfg["units"] ("imperial" | "metric"); anything
missing or unrecognized falls back to imperial. Pure formatting, no I/O.
"""

from __future__ import annotations

from .geo import KT_TO_KMH, KT_TO_MPH

NM_TO_KM = 1.852   # 1 nautical mile in km
IN_TO_MM = 25.4    # 1 inch in mm
GAL_TO_L = 3.785   # 1 US gallon in liters


class Units:
    """Render-time unit formatting. Internal units: kt, nm, inches, gallons, deg F."""

    def __init__(self, pref: str):
        self._metric = str(pref or "").strip().lower() in {"metric", "si", "m", "celsius", "km"}

    @property
    def metric(self) -> bool:
        return self._metric

    @property
    def pref(self) -> str:
        return "metric" if self._metric else "imperial"

    def speed(self, kt: float) -> str:
        """'74 mph' | '119 km/h'."""
        if self.metric:
            return f"{int(round(kt * KT_TO_KMH))} km/h"
        return f"{int(round(kt * KT_TO_MPH))} mph"

    def distance(self, nm: float) -> str:
        """'126 nm (233 km)' | '233 km (126 nm)'; under 10 nm show 1 decimal."""
        nm = float(nm)
        km = nm * NM_TO_KM
        if nm < 10:
            if self.metric:
                return f"{km:.1f} km ({nm:.1f} nm)"
            return f"{nm:.1f} nm ({km:.1f} km)"
        if self.metric:
            return f"{km:,.0f} km ({nm:,.0f} nm)"
        return f"{nm:,.0f} nm ({km:,.0f} km)"

    def rain(self, inches: float) -> str:
        """'6 in' | '152 mm'."""
        if self.metric:
            return f"{int(round(inches * IN_TO_MM))} mm"
        return f"{int(round(inches))} in"

    def rain_range(self, lo: float, hi: float) -> str:
        """'6-12 in' | '152-305 mm'."""
        if self.metric:
            return f"{int(round(lo * IN_TO_MM))}-{int(round(hi * IN_TO_MM))} mm"
        return f"{int(round(lo))}-{int(round(hi))} in"

    def temp_f(self, f: float | None) -> str:
        """'85°F' | '29°C'; None -> '?'."""
        if f is None:
            return "?"
        if self.metric:
            return f"{int(round((f - 32) * 5 / 9))}°C"
        return f"{int(round(f))}°F"

    def volume(self, gallons: float) -> str:
        """'9 gal (34 L)' | '34 L'."""
        liters = int(round(gallons * GAL_TO_L))
        if self.metric:
            return f"{liters} L"
        return f"{int(round(gallons))} gal ({liters} L)"


def get_units(cfg: dict) -> Units:
    """Units preference from cfg; missing or bad value -> imperial."""
    return Units((cfg or {}).get("units", "imperial"))
