"""Azimut slunce při východu/západu a bod na horizontu v jeho ose (čistá trigonometrie, bez závislostí).

Azimut východu a západu závisí jen na zeměpisné šířce a na datu (deklinaci slunce), ne na časovém pásmu:
`day` je proto místní datum Madeiry (Atlantic/Madeira) a čas se nikde nepřevádí. Přesnost ≈ 0,5°, což je
při vzdálenosti horizontu 55 km posun o ~0,5 km, tedy hluboko pod rozlišením modelu (~9 km).
"""

from __future__ import annotations

import math
from datetime import date
from typing import Literal

EARTH_RADIUS_KM = 6371.0
SUN_HORIZON_DEG = -0.833  # střed slunce při východu/západu (poloměr kotouče + refrakce)


def declination_deg(day: date) -> float:
    """Deklinace slunce (Spencer 1971), ve stupních."""
    g = 2 * math.pi / 365 * (day.timetuple().tm_yday - 1)
    d = (
        0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g)
        - 0.006758 * math.cos(2 * g) + 0.000907 * math.sin(2 * g)
        - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g)
    )
    return math.degrees(d)


def sun_azimuth_deg(lat: float, day: date, event: Literal["sunrise", "sunset"]) -> float:
    """Azimut slunce (° od severu po směru hodin) při východu (~65–115°) nebo západu (~245–295°)."""
    dec, phi, h = (math.radians(x) for x in (declination_deg(day), lat, SUN_HORIZON_DEG))
    cos_a = (math.sin(dec) - math.sin(h) * math.sin(phi)) / (math.cos(h) * math.cos(phi))
    a = math.degrees(math.acos(max(-1.0, min(1.0, cos_a))))
    return a if event == "sunrise" else 360.0 - a


def destination(lat: float, lon: float, bearing_deg: float, km: float) -> tuple[float, float]:
    """Bod ve vzdálenosti `km` ve směru `bearing_deg` (velká kružnice na kouli)."""
    d, b = km / EARTH_RADIUS_KM, math.radians(bearing_deg)
    p1, l1 = math.radians(lat), math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), (math.degrees(l2) + 540) % 360 - 180


def azimuth_gap(az: float, start: float, end: float) -> float:
    """Úhlová vzdálenost azimutu od sektoru <start, end> (0 uvnitř), s ohledem na přechod přes 0°."""
    if in_azimuth(az, start, end):
        return 0.0
    diff = lambda a, b: abs((a - b + 180) % 360 - 180)
    return min(diff(az, start), diff(az, end))


def in_azimuth(az: float, start: float, end: float) -> bool:
    """Azimut v sektoru <start, end> (včetně hranic, sektor smí přecházet přes 0°)."""
    az, start, end = az % 360, start % 360, end % 360
    return start <= az <= end if start <= end else az >= start or az <= end
