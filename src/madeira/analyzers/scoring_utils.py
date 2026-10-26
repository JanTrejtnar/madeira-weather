from __future__ import annotations

import itertools
from collections.abc import Sequence


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def pw(x: float, points: Sequence[tuple[float, float]]) -> float:
    """Po částech lineární funkce přes (x, y) body seřazené podle x; mimo rozsah drží krajní hodnotu."""
    if any(a[0] >= b[0] for a, b in itertools.pairwise(points)):
        raise ValueError(f"Body pw() musí mít vzestupné x: {points}")
    if x <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in itertools.pairwise(points):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return points[-1][1]
