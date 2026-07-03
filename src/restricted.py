"""Refuse to analyze government / restricted properties.

Two layers:
  1. Hardcoded list of named federal critical sites + 50 state capitols.
  2. OSM Overpass query for military / embassy / capitol / diplomatic tags
     within ~150m of the geocoded point.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import requests

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

_SITES = json.loads((DATA_DIR / "restricted_sites.json").read_text())["sites"]


@dataclass
class RestrictionResult:
    blocked: bool
    reason: str = ""
    source: str = ""  # "hardcoded" | "osm" | ""


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _check_hardcoded(lat: float, lon: float) -> RestrictionResult:
    for s in _SITES:
        d = _haversine_m(lat, lon, s["lat"], s["lon"])
        if d <= s["radius_m"]:
            return RestrictionResult(
                blocked=True,
                reason=f"{s['name']} (within {d:.0f}m of restricted-site centroid).",
                source="hardcoded",
            )
    return RestrictionResult(blocked=False)


# OSM tag patterns that indicate a restricted government / military / diplomatic site.
# Deliberately narrow — broad tags like `office=government` cover post offices and
# city halls, which we do NOT want to block.
_OSM_QUERY_TEMPLATE = """
[out:json][timeout:15];
(
  way["military"](around:{r},{lat},{lon});
  relation["military"](around:{r},{lat},{lon});
  way["landuse"="military"](around:{r},{lat},{lon});
  way["amenity"="embassy"](around:{r},{lat},{lon});
  node["amenity"="embassy"](around:{r},{lat},{lon});
  way["diplomatic"](around:{r},{lat},{lon});
  way["building"="capitol"](around:{r},{lat},{lon});
  way["government"~"parliament|ministry|intelligence|executive"](around:{r},{lat},{lon});
);
out tags 1;
"""


def _check_osm(lat: float, lon: float, radius_m: int = 150,
               timeout: int = 15) -> RestrictionResult:
    q = _OSM_QUERY_TEMPLATE.format(r=radius_m, lat=lat, lon=lon)
    try:
        r = requests.post(OVERPASS_URL, data={"data": q}, timeout=timeout,
                          headers={"User-Agent": "vibe-property-analyzer/0.1"})
        r.raise_for_status()
        data = r.json()
    except Exception:
        # Fail open: if OSM is unreachable, the hardcoded list is still authoritative.
        return RestrictionResult(blocked=False)

    for el in data.get("elements", []):
        tags = el.get("tags", {})
        for key in ("military", "landuse", "amenity", "diplomatic",
                    "building", "government"):
            if key in tags:
                val = tags[key]
                name = tags.get("name", "")
                label = f"{key}={val}" + (f" ({name})" if name else "")
                return RestrictionResult(
                    blocked=True,
                    reason=f"OSM tag indicates restricted site: {label}.",
                    source="osm",
                )
    return RestrictionResult(blocked=False)


def check(lat: float, lon: float) -> RestrictionResult:
    """Return a RestrictionResult. Hardcoded list runs first (offline, fast)."""
    hard = _check_hardcoded(lat, lon)
    if hard.blocked:
        return hard
    return _check_osm(lat, lon)
