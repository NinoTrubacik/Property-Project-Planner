"""Nominatim address search — broader-coverage second pass when Census +
OSM tag matching doesn't pinpoint the building.

Free, no API key. Nominatim's usage policy: max 1 request per second + a
descriptive User-Agent. We enforce both in-process.

Nominatim's `polygon_geojson=1` returns the actual matched OSM polygon when
the address resolves to a building/way — that's the gold-standard outline.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Optional

import requests
from shapely.geometry import Polygon

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
HEADERS = {"User-Agent": "vibe-property-analyzer/0.1 (free non-commercial use)"}

# Single-process rate limiter — Nominatim caps us at 1 req/sec.
_LAST_CALL = 0.0
_LOCK = threading.Lock()


def _throttle(min_interval_s: float = 1.05) -> None:
    global _LAST_CALL
    with _LOCK:
        wait = min_interval_s - (time.time() - _LAST_CALL)
        if wait > 0:
            time.sleep(wait)
        _LAST_CALL = time.time()


@dataclass
class NominatimMatch:
    lat: float
    lon: float
    display_name: str
    osm_type: str = ""        # "way" | "node" | "relation"
    osm_id: int = 0
    place_class: str = ""     # "building" | "place" | "highway" | ...
    place_type: str = ""      # "house" | "residential" | "yes" | ...
    polygon: Optional[Polygon] = None


def _score(result: dict) -> int:
    """Lower is better. Prefer matches that resolved to a building/house."""
    cls = result.get("class", "")
    typ = result.get("type", "")
    if cls == "building":
        return 0
    if typ in ("house", "residential", "apartments", "detached"):
        return 1
    if cls == "place" and typ in ("house", "building"):
        return 2
    if cls == "highway":
        return 90  # street centerline — last resort
    return 50


def _extract_polygon(geo: dict) -> Optional[Polygon]:
    """Extract a Polygon from Nominatim's GeoJSON, if it is one."""
    if not geo or geo.get("type") != "Polygon":
        return None
    try:
        coords = geo["coordinates"][0]  # exterior ring
        poly = Polygon(coords)
        return poly if poly.is_valid and poly.area > 0 else None
    except Exception:
        return None


def lookup(address: str, timeout: int = 15) -> Optional[NominatimMatch]:
    """Search Nominatim for the address and return the best building-ish match."""
    _throttle()
    params = {
        "q": address,
        "format": "jsonv2",
        "addressdetails": 1,
        "polygon_geojson": 1,
        "limit": 5,
        "countrycodes": "us",
    }
    try:
        r = requests.get(NOMINATIM_URL, params=params, timeout=timeout, headers=HEADERS)
        r.raise_for_status()
        results = r.json()
    except Exception:
        return None
    if not results:
        return None

    results.sort(key=_score)
    best = results[0]
    return NominatimMatch(
        lat=float(best["lat"]),
        lon=float(best["lon"]),
        display_name=best.get("display_name", ""),
        osm_type=best.get("osm_type", ""),
        osm_id=int(best.get("osm_id", 0)),
        place_class=best.get("class", ""),
        place_type=best.get("type", ""),
        polygon=_extract_polygon(best.get("geojson", {})),
    )
