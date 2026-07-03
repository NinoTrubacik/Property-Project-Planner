"""Per-county ArcGIS parcel lookup.

Queries the configured county's ArcGIS REST service for the parcel polygon
containing the geocoded point. This is the most accurate free source of US
parcel data where covered — direct from the county's authoritative dataset.

Coverage is hand-curated in data/county_parcels.json. Adding a county = adding
one JSON entry (every entry is live-verified against a real point query before
being added).

Accuracy strategy:
  1. Point query first (exact point-in-polygon).
  2. If the point misses (Census geocodes often land in the street, outside
     every parcel), retry with a small envelope (~45 m) around the point.
  3. Among candidates, prefer the parcel whose situs housenumber matches the
     requested address; else the parcel containing the point; else the nearest.
  4. Multiple counties may claim the same ZIP prefix (e.g. St. Louis City vs
     St. Louis County both have 631xx) — each is tried in JSON order until one
     returns a parcel.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests
from pyproj import Transformer
from shapely.geometry import Point, Polygon
from shapely.ops import transform as shp_transform

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "county_parcels.json"
HEADERS = {"User-Agent": "vibe-property-analyzer/0.1"}

_COUNTIES = json.loads(DATA_PATH.read_text())["counties"]

_ENVELOPE_M = 45          # fallback search box half-size around the point
_NEAREST_MAX_M = 30       # beyond this, "nearest parcel" is too risky to use
_LEADING_NUM_RE = re.compile(r"^\s*(\d+)")


@dataclass
class CountyParcelMatch:
    polygon: Optional[Polygon]
    confidence: str         # "high" | "medium" | "low" | "none"
    method: str
    parcel_id: str = ""
    county_name: str = ""
    situs_address: str = ""    # County's address-of-record for this parcel
    address_verified: bool = False  # True if requested housenumber matches situs


def _candidate_counties(state: str, zip_code: str) -> list[dict]:
    """All configured counties matching this state + ZIP, in JSON order."""
    if not state:
        return []
    out = []
    for c in _COUNTIES:
        if c["state"].upper() != state.upper():
            continue
        prefixes = c.get("zip_prefixes") or []
        if not prefixes or any(zip_code.startswith(p) for p in prefixes):
            out.append(c)
    return out


def _rings_to_polygon(rings: list) -> Optional[Polygon]:
    """Convert an ArcGIS 'rings' array (list of [lon, lat] sequences) to a Polygon.
    First ring is exterior; remaining are holes. We only use the exterior."""
    if not rings:
        return None
    try:
        poly = Polygon(rings[0])
        return poly if poly.is_valid and poly.area > 0 else None
    except Exception:
        return None


def _polygon_area_sqm(poly: Polygon) -> float:
    """Compute area of a (lon, lat) polygon in m² via local UTM."""
    cx, cy = poly.centroid.x, poly.centroid.y
    zone = int(math.floor((cx + 180) / 6) + 1)
    epsg = 32600 + zone if cy >= 0 else 32700 + zone
    t = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    return shp_transform(lambda x, y, z=None: t.transform(x, y), poly).area


def _norm_housenumber(v) -> str:
    """'545', 545, 545.0, '0545 ' -> '545'. Empty/zero -> ''."""
    if v is None:
        return ""
    s = str(v).strip()
    if s.endswith(".0"):
        s = s[:-2]
    s = s.lstrip("0").strip()
    return "" if s in ("", "0") else s


def _attr_housenumber(attrs: dict, county: dict) -> str:
    """Situs housenumber from the configured field, falling back to the leading
    number of the situs address string (some counties only publish the latter)."""
    hn = _norm_housenumber(attrs.get(county.get("situs_housenumber_field", "")))
    if hn:
        return hn
    situs = str(attrs.get(county.get("situs_address_field", ""), "") or "")
    m = _LEADING_NUM_RE.match(situs)
    return _norm_housenumber(m.group(1)) if m else ""


def _query(county: dict, lat: float, lon: float,
           envelope_m: float | None, timeout: int) -> list[dict]:
    """Run one ArcGIS spatial query; returns raw feature dicts (may be empty)."""
    if envelope_m:
        dlat = envelope_m / 111000.0
        dlon = envelope_m / (111000.0 * max(math.cos(math.radians(lat)), 0.2))
        geom = json.dumps({"xmin": lon - dlon, "ymin": lat - dlat,
                           "xmax": lon + dlon, "ymax": lat + dlat,
                           "spatialReference": {"wkid": 4326}})
        gtype = "esriGeometryEnvelope"
    else:
        geom = json.dumps({"x": lon, "y": lat, "spatialReference": {"wkid": 4326}})
        gtype = "esriGeometryPoint"
    params = {
        "where": "1=1",
        "geometry": geom,
        "geometryType": gtype,
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "*",
        "returnGeometry": "true",
        "outSR": "4326",
        "f": "json",
    }
    r = requests.get(county["query_url"], params=params,
                     timeout=timeout, headers=HEADERS)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"ArcGIS error {data['error'].get('code')}")
    return data.get("features") or []


def _dist_m(poly: Polygon, pt: Point, lat: float) -> float:
    """Approx distance (meters) from point to polygon at this latitude."""
    d_deg = poly.distance(pt)
    return d_deg * 111000.0 * max(math.cos(math.radians(lat)), 0.2)


def _choose(features: list[dict], lat: float, lon: float,
            requested_housenumber: str, county: dict) -> Optional[CountyParcelMatch]:
    """Pick the best parcel among candidates and grade the match."""
    pt = Point(lon, lat)
    requested = _norm_housenumber(requested_housenumber)

    cands = []  # (poly, attrs, hn, contains, dist_m, area)
    for feat in features:
        poly = _rings_to_polygon((feat.get("geometry") or {}).get("rings") or [])
        if poly is None:
            continue
        attrs = feat.get("attributes") or {}
        cands.append((poly, attrs, _attr_housenumber(attrs, county),
                      poly.contains(pt), _dist_m(poly, pt, lat),
                      _polygon_area_sqm(poly)))
    if not cands:
        return None

    def _result(c, confidence, suffix, verified):
        poly, attrs, _, _, _, _ = c
        parcel_id = str(attrs.get(county.get("id_field", ""), "") or "")
        situs = str(attrs.get(county.get("situs_address_field", ""), "") or "").strip()
        return CountyParcelMatch(
            polygon=poly,
            confidence=confidence,
            method=f"{county['name']} ArcGIS parcel layer{suffix}",
            parcel_id=parcel_id,
            county_name=county["name"],
            situs_address=situs,
            address_verified=verified,
        )

    # 1. Exact situs housenumber match — the right lot even if the geocoded
    #    point drifted into the street or a neighbor.
    if requested:
        exact = [c for c in cands if c[2] == requested]
        if exact:
            # Contained beats near; largest breaks condo/stacked-unit ties.
            exact.sort(key=lambda c: (not c[3], c[4], -c[5]))
            return _result(exact[0], "high", " (situs housenumber verified)", True)

    # 2. Parcel containing the point (largest wins for condo stacks).
    contained = [c for c in cands if c[3]]
    if contained:
        contained.sort(key=lambda c: -c[5])
        best = contained[0]
        if best[2] and requested and best[2] != requested:
            return _result(best, "low",
                           f" (housenumber mismatch: got {best[2]}, "
                           f"wanted {requested})", False)
        return _result(best, "high", " (situs unverified)", False)

    # 3. Nearest parcel, only if very close — otherwise better to return none
    #    than to outline a neighbor's lot.
    cands.sort(key=lambda c: c[4])
    best = cands[0]
    if best[4] <= _NEAREST_MAX_M:
        if best[2] and requested and best[2] != requested:
            return _result(best, "low",
                           f" (nearest parcel {best[4]:.0f}m away; housenumber "
                           f"mismatch: got {best[2]}, wanted {requested})", False)
        return _result(best, "medium",
                       f" (nearest parcel, {best[4]:.0f}m from geocoded point)",
                       False)
    return None


def lookup(state: str, zip_code: str, lat: float, lon: float,
           requested_housenumber: str = "",
           timeout: int = 20) -> CountyParcelMatch:
    """Look up the parcel polygon for (lat, lon) across all matching counties."""
    counties = _candidate_counties(state, zip_code)
    if not counties:
        return CountyParcelMatch(None, "none", "No county GIS configured for this address")

    errors: list[str] = []
    for county in counties:
        try:
            features = _query(county, lat, lon, envelope_m=None, timeout=timeout)
            if not features:
                features = _query(county, lat, lon,
                                  envelope_m=county.get("search_radius_m", _ENVELOPE_M),
                                  timeout=timeout)
        except Exception as e:
            errors.append(f"{county['name']}: {type(e).__name__}")
            continue
        if not features:
            errors.append(f"{county['name']}: no parcel near point")
            continue
        match = _choose(features, lat, lon, requested_housenumber, county)
        if match is not None:
            return match
        errors.append(f"{county['name']}: features had no usable geometry")

    return CountyParcelMatch(
        None, "none",
        "County GIS found no parcel (" + "; ".join(errors) + ")",
    )
