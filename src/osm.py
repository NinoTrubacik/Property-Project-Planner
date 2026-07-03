"""OpenStreetMap Overpass queries for buildings and parcels.

Accuracy strategy:
    Building selection cascade (most → least confident):
      1. OSM `addr:housenumber` + `addr:street` tags matching the geocoded address
      2. The building polygon that *contains* the geocoded point
      3. The nearest building within a tight radius (≤25m), skipping ancillary
         structures (garage / shed / roof / carport)
      4. None — caller should not draw an outline at low confidence

    Parcel selection:
      Reject `landuse=residential` polygons because in US OSM data those almost
      always cover an entire neighborhood block, not a single lot. Only accept:
        - boundary=parcel
        - landuse=plot (rare but legitimate per-parcel tag)
        - residential landuse <= 4000 sqm (~1 acre) as a last-resort proxy
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import requests
from pyproj import Transformer
from shapely.geometry import Point, Polygon
from shapely.ops import transform as shp_transform

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
HEADERS = {"User-Agent": "vibe-property-analyzer/0.1"}

# OSM building values that are NOT the main residence on a lot.
_ANCILLARY_BUILDING_TAGS = {
    "garage", "garages", "carport", "shed", "roof", "greenhouse",
    "hut", "kiosk", "service", "boathouse", "shelter",
}

_NEAREST_RADIUS_M = 25
_PARCEL_SEARCH_RADIUS_M = 60
_PARCEL_MAX_SQM = 4000  # ~1 acre


@dataclass
class BuildingMatch:
    polygon: Optional[Polygon]
    confidence: str   # "high" | "medium" | "low" | "none"
    method: str       # human-readable description


@dataclass
class ParcelMatch:
    polygon: Optional[Polygon]
    confidence: str
    method: str
    area_sqm: float = 0.0


# ---------- Overpass helpers ----------
def _query(q: str, timeout: int = 30) -> dict:
    r = requests.post(OVERPASS_URL, data={"data": q}, timeout=timeout, headers=HEADERS)
    r.raise_for_status()
    return r.json()


def _way_to_polygon(elem: dict) -> Optional[Polygon]:
    geom = elem.get("geometry")
    if not geom or len(geom) < 3:
        return None
    coords = [(pt["lon"], pt["lat"]) for pt in geom]
    if coords[0] != coords[-1]:
        coords.append(coords[0])
    try:
        p = Polygon(coords)
        return p if p.is_valid and p.area > 0 else None
    except Exception:
        return None


def _polygon_area_sqm(poly: Polygon) -> float:
    """Area of a (lon, lat) polygon in square meters via local UTM projection."""
    cy = poly.centroid.y
    cx = poly.centroid.x
    import math
    zone = int(math.floor((cx + 180) / 6) + 1)
    epsg = 32600 + zone if cy >= 0 else 32700 + zone
    t = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    return shp_transform(lambda x, y, z=None: t.transform(x, y), poly).area


# ---------- Street-name normalization (loose match) ----------
_SUFFIX_NORMALIZE = {
    "street": "st", "avenue": "ave", "boulevard": "blvd", "road": "rd",
    "drive": "dr", "lane": "ln", "court": "ct", "place": "pl",
    "terrace": "ter", "parkway": "pkwy", "highway": "hwy", "circle": "cir",
    "trail": "trl", "way": "way", "square": "sq", "alley": "aly",
}


def _norm_street(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^\w\s]", " ", s)
    tokens = []
    for tok in s.split():
        tokens.append(_SUFFIX_NORMALIZE.get(tok, tok))
    return " ".join(tokens)


def _streets_match(a: str, b: str) -> bool:
    """Loose comparison: '5TH AVE' ~ '5th Avenue'."""
    if not a or not b:
        return False
    na, nb = _norm_street(a), _norm_street(b)
    if na == nb:
        return True
    # Substring fallback: handles cases like '5th' vs '5th st' where suffix differs.
    return na in nb or nb in na


# ---------- Building lookup ----------
def find_building(lat: float, lon: float,
                  housenumber: str = "",
                  street: str = "",
                  search_radius_m: int = 100) -> BuildingMatch:
    """Return the building polygon for the address with a confidence rating."""

    # Step 1 — Address-tag match (highest confidence)
    if housenumber and street:
        q = f"""
        [out:json][timeout:25];
        (
          way["building"]["addr:housenumber"="{housenumber}"](around:{search_radius_m},{lat},{lon});
        );
        out tags geom;
        """
        try:
            data = _query(q)
            best = None
            for elem in data.get("elements", []):
                tags = elem.get("tags", {})
                osm_street = tags.get("addr:street", "")
                if _streets_match(street, osm_street):
                    poly = _way_to_polygon(elem)
                    if poly is None:
                        continue
                    btype = tags.get("building", "yes")
                    if btype in _ANCILLARY_BUILDING_TAGS:
                        continue
                    # Prefer the largest matching footprint (main house over a shed).
                    if best is None or poly.area > best.area:
                        best = poly
            if best is not None:
                return BuildingMatch(
                    polygon=best, confidence="high",
                    method=f"OSM addr:housenumber={housenumber} + addr:street match",
                )
        except Exception:
            pass  # fall through to spatial methods

    # Step 2 — Containing building (medium-high)
    try:
        q = f"""
        [out:json][timeout:25];
        (
          way["building"](around:{_NEAREST_RADIUS_M},{lat},{lon});
        );
        out tags geom;
        """
        data = _query(q)
    except Exception:
        return BuildingMatch(polygon=None, confidence="none",
                             method="Overpass unreachable")

    pt = Point(lon, lat)
    candidates = []  # (polygon, btype, distance_m)
    for elem in data.get("elements", []):
        poly = _way_to_polygon(elem)
        if poly is None:
            continue
        tags = elem.get("tags", {})
        btype = tags.get("building", "yes")
        if btype in _ANCILLARY_BUILDING_TAGS:
            continue
        # Compute distance in meters via UTM projection (small-area safe).
        d_deg = poly.distance(pt)
        # Cheap meters approx — 1° lat ~ 111km, 1° lon ~ 111km · cos(lat)
        import math
        d_m = d_deg * 111000 * (math.cos(math.radians(lat)) if d_deg else 1.0)
        candidates.append((poly, btype, d_m))
        if poly.contains(pt):
            return BuildingMatch(polygon=poly, confidence="medium",
                                 method="Building polygon contains geocoded point")

    # Step 3 — Nearest within tight radius (lower confidence)
    if candidates:
        candidates.sort(key=lambda c: c[2])
        poly, btype, d_m = candidates[0]
        # Require very close — beyond 12m is likely a neighbor.
        if d_m <= 12:
            return BuildingMatch(
                polygon=poly, confidence="medium",
                method=f"Nearest building ({d_m:.1f}m away)",
            )
        if d_m <= _NEAREST_RADIUS_M:
            return BuildingMatch(
                polygon=poly, confidence="low",
                method=f"Nearest building ({d_m:.1f}m away — could be a neighbor)",
            )

    return BuildingMatch(polygon=None, confidence="none",
                         method="No building polygon found within search radius")


# ---------- Parcel lookup ----------
def find_parcel(lat: float, lon: float) -> ParcelMatch:
    """Return a parcel polygon if one can be confidently identified.

    Never returns neighborhood-block-sized landuse polygons.
    """
    q = f"""
    [out:json][timeout:25];
    (
      way["boundary"="parcel"](around:{_PARCEL_SEARCH_RADIUS_M},{lat},{lon});
      way["landuse"="plot"](around:{_PARCEL_SEARCH_RADIUS_M},{lat},{lon});
      way["landuse"="residential"](around:{_PARCEL_SEARCH_RADIUS_M},{lat},{lon});
    );
    out tags geom;
    """
    try:
        data = _query(q)
    except Exception:
        return ParcelMatch(None, "none", "Overpass unreachable")

    pt = Point(lon, lat)
    best: Optional[tuple[Polygon, str, float]] = None  # (poly, tag, area_sqm)
    for elem in data.get("elements", []):
        poly = _way_to_polygon(elem)
        if poly is None or not poly.contains(pt):
            continue
        tags = elem.get("tags", {})
        area = _polygon_area_sqm(poly)
        is_parcel_tag = tags.get("boundary") == "parcel" or tags.get("landuse") == "plot"
        if is_parcel_tag:
            tag_label = "boundary=parcel" if tags.get("boundary") == "parcel" else "landuse=plot"
            # Always accept these regardless of size.
            if best is None or area < best[2]:
                best = (poly, tag_label, area)
        elif tags.get("landuse") == "residential" and area <= _PARCEL_MAX_SQM:
            if best is None or area < best[2]:
                best = (poly, "landuse=residential (small)", area)

    if best is None:
        return ParcelMatch(
            None, "none",
            "No per-parcel OSM polygon found "
            "(landuse=residential block-level polygons are rejected as inaccurate)",
        )
    poly, tag_label, area = best
    confidence = "high" if "parcel" in tag_label or "plot" in tag_label else "low"
    return ParcelMatch(
        polygon=poly, confidence=confidence,
        method=f"OSM {tag_label}",
        area_sqm=area,
    )


# ---------- Back-compat shims (the old function names) ----------
def nearest_building(lat: float, lon: float, radius_m: int = 60) -> Optional[Polygon]:
    return find_building(lat, lon, search_radius_m=radius_m).polygon


def containing_landuse(lat: float, lon: float, radius_m: int = 80) -> Optional[Polygon]:
    return find_parcel(lat, lon).polygon
