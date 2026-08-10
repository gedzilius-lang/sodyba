"""Geometry helpers.

Two coordinate systems are in play:
  * LKS-94 / EPSG:3346 — metres. Used by Registrų centras and the environment
    agency. Their WKT writes POINT (northing easting), not the usual order.
  * WGS84 — degrees. Used by the protected-areas cadastre, and by anything the
    user types in.

Distances are computed in LKS-94 metres. It is a transverse Mercator projection
with a 0.9998 scale factor on a 24°E meridian; across Lithuania the scale error
stays under ~0.05%, far below the precision that matters for "how far is the
lake". No projection library needed at runtime.
"""
from __future__ import annotations
import math
import re
from typing import Iterable

# EPSG:3346 parameters
_A = 6378137.0                 # GRS80 semi-major axis
_F = 1 / 298.257222101         # GRS80 flattening
_K0 = 0.9998                   # scale factor
_LON0 = math.radians(24.0)     # central meridian
_FE = 500000.0                 # false easting
_FN = 0.0                      # false northing

_E2 = _F * (2 - _F)
_EP2 = _E2 / (1 - _E2)
_N = _F / (2 - _F)


def wgs84_to_lks94(lat: float, lon: float) -> tuple[float, float]:
    """(lat, lon) degrees -> (easting, northing) metres."""
    phi, lam = math.radians(lat), math.radians(lon)
    dl = lam - _LON0
    sp, cp = math.sin(phi), math.cos(phi)
    nu = _A / math.sqrt(1 - _E2 * sp * sp)
    t = math.tan(phi)
    eta2 = _EP2 * cp * cp

    n2, n3, n4 = _N ** 2, _N ** 3, _N ** 4
    b = _A / (1 + _N) * (1 + n2 / 4 + n4 / 64)
    m = b * (phi
             - (3 * _N / 2 - 9 * n3 / 16) * math.sin(2 * phi)
             + (15 * n2 / 16 - 15 * n4 / 32) * math.sin(4 * phi)
             - (35 * n3 / 48) * math.sin(6 * phi)
             + (315 * n4 / 512) * math.sin(8 * phi))

    e = _FE + _K0 * nu * (dl * cp
                          + dl ** 3 * cp ** 3 / 6 * (1 - t * t + eta2)
                          + dl ** 5 * cp ** 5 / 120
                          * (5 - 18 * t ** 2 + t ** 4 + 14 * eta2 - 58 * t ** 2 * eta2))
    n = _FN + _K0 * (m + nu * t * (dl ** 2 * cp ** 2 / 2
                                   + dl ** 4 * cp ** 4 / 24 * (5 - t ** 2 + 9 * eta2 + 4 * eta2 ** 2)
                                   + dl ** 6 * cp ** 6 / 720
                                   * (61 - 58 * t ** 2 + t ** 4 + 270 * eta2 - 330 * t ** 2 * eta2)))
    return e, n


def lks94_to_wgs84(easting: float, northing: float) -> tuple[float, float]:
    """(easting, northing) metres -> (lat, lon) degrees."""
    n2, n3, n4 = _N ** 2, _N ** 3, _N ** 4
    b = _A / (1 + _N) * (1 + n2 / 4 + n4 / 64)
    mu = (northing - _FN) / (_K0 * b)
    phi1 = (mu
            + (3 * _N / 2 - 27 * n3 / 32) * math.sin(2 * mu)
            + (21 * n2 / 16 - 55 * n4 / 32) * math.sin(4 * mu)
            + (151 * n3 / 96) * math.sin(6 * mu)
            + (1097 * n4 / 512) * math.sin(8 * mu))

    sp, cp = math.sin(phi1), math.cos(phi1)
    t = math.tan(phi1)
    eta2 = _EP2 * cp * cp
    nu = _A / math.sqrt(1 - _E2 * sp * sp)
    rho = _A * (1 - _E2) / (1 - _E2 * sp * sp) ** 1.5
    d = (easting - _FE) / (nu * _K0)

    lat = phi1 - (nu * t / rho) * (d ** 2 / 2
                                   - d ** 4 / 24 * (5 + 3 * t ** 2 + eta2 - 9 * eta2 * t ** 2)
                                   + d ** 6 / 720 * (61 + 90 * t ** 2 + 45 * t ** 4))
    lon = _LON0 + (d
                   - d ** 3 / 6 * (1 + 2 * t ** 2 + eta2)
                   + d ** 5 / 120 * (5 + 28 * t ** 2 + 24 * t ** 4)) / cp
    return math.degrees(lat), math.degrees(lon)


def dist_m(e1: float, n1: float, e2: float, n2: float) -> float:
    """Planar distance in LKS-94 metres."""
    return math.hypot(e1 - e2, n1 - n2)


_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def wkt_points(wkt: str, limit: int = 4000) -> list[tuple[float, float]]:
    """Pull raw coordinate pairs out of any WKT geometry, in written order.

    Returns pairs as written. Callers must know their source's axis order:
    the environment agency and Registrų centras write (northing, easting);
    the protected-areas cadastre writes (lat, lon).
    """
    nums = [float(x) for x in _NUM.findall(wkt or "")]
    pairs = list(zip(nums[0::2], nums[1::2]))
    if len(pairs) <= limit:
        return pairs
    step = len(pairs) // limit + 1
    return pairs[::step]


def bbox(points: Iterable[tuple[float, float]]) -> tuple[float, float, float, float] | None:
    """(min_a, min_b, max_a, max_b) over the given pairs."""
    pts = list(points)
    if not pts:
        return None
    a = [p[0] for p in pts]
    b = [p[1] for p in pts]
    return min(a), min(b), max(a), max(b)


def centroid(points: Iterable[tuple[float, float]]) -> tuple[float, float] | None:
    pts = list(points)
    if not pts:
        return None
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


def in_bbox(a: float, b: float, box: tuple[float, float, float, float],
            pad: float = 0.0) -> bool:
    return (box[0] - pad <= a <= box[2] + pad) and (box[1] - pad <= b <= box[3] + pad)
