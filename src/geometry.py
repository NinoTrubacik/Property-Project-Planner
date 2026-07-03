"""Geometry helpers: project lon/lat polygons to local UTM, measure areas, derive yards."""
from __future__ import annotations

import math
from dataclasses import dataclass

from pyproj import Transformer
from shapely.geometry import Polygon, Point
from shapely.ops import transform as shp_transform

SQM_PER_SQFT = 0.09290304


def utm_epsg(lat: float, lon: float) -> int:
    """Return the EPSG code for the UTM zone containing (lat, lon)."""
    zone = int(math.floor((lon + 180) / 6) + 1)
    return 32600 + zone if lat >= 0 else 32700 + zone


def project_to_meters(poly: Polygon, lat: float, lon: float) -> Polygon:
    epsg = utm_epsg(lat, lon)
    t = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    return shp_transform(lambda x, y, z=None: t.transform(x, y), poly)


def sqm_to_sqft(sqm: float) -> float:
    return sqm / SQM_PER_SQFT


def m_to_ft(m: float) -> float:
    return m * 3.28084


def sqft_to_acres(sqft: float) -> float:
    return sqft / 43560.0


@dataclass
class PropertyMeasurements:
    lot_sqft: float
    lot_acres: float
    building_sqft: float
    backyard_sqft: float
    frontyard_sqft: float
    left_side_ft: float
    right_side_ft: float
    front_setback_ft: float
    rear_setback_ft: float
    confidence: str  # High / Medium / Low
    notes: list[str]


def measure(lot_poly: Polygon | None,
            building_poly: Polygon | None,
            lot_sqft_override: float | None,
            center_lat: float,
            center_lon: float) -> PropertyMeasurements:
    """Compute property measurements. Either `lot_poly` (from OSM) OR
    `lot_sqft_override` (user-supplied) must be provided to get full numbers.
    """
    notes: list[str] = []
    confidence_parts: list[str] = []

    # --- Lot area ---
    if lot_poly is not None:
        lot_m = project_to_meters(lot_poly, center_lat, center_lon)
        lot_sqft = sqm_to_sqft(lot_m.area)
        confidence_parts.append("lot:medium (OSM landuse)")
    elif lot_sqft_override is not None:
        lot_sqft = float(lot_sqft_override)
        lot_m = None
        confidence_parts.append("lot:high (user override)")
    else:
        lot_sqft = 0.0
        lot_m = None
        notes.append("No lot polygon found in OSM and no override given — lot size unknown.")
        confidence_parts.append("lot:none")

    # --- Building footprint ---
    if building_poly is not None:
        bldg_m = project_to_meters(building_poly, center_lat, center_lon)
        building_sqft = sqm_to_sqft(bldg_m.area)
        # Sanity check: residential homes are almost never over 15,000 sqft.
        # Anything larger is most likely a commercial structure picked up by mistake.
        if building_sqft > 15000:
            notes.append(
                f"Building footprint ({building_sqft:,.0f} sqft) is unusually large "
                "for a residential address — OSM may have returned a commercial "
                "neighbor or an entire complex. Verify visually against the outline."
            )
            confidence_parts.append("building:low (sanity check tripped)")
        else:
            confidence_parts.append("building:medium (OSM footprint)")
    else:
        bldg_m = None
        building_sqft = 0.0
        notes.append("No building footprint found in OSM at this location.")
        confidence_parts.append("building:none")

    # --- Yards & setbacks ---
    backyard_sqft = frontyard_sqft = 0.0
    left_side_ft = right_side_ft = 0.0
    front_setback_ft = rear_setback_ft = 0.0

    if lot_m is not None and bldg_m is not None:
        usable_m2 = max(lot_m.area - bldg_m.area, 0.0)
        # Heuristic split: 55% backyard, 25% front, 10% each side. Real split
        # would need parcel-relative orientation; flagged in notes.
        backyard_sqft = sqm_to_sqft(usable_m2 * 0.55)
        frontyard_sqft = sqm_to_sqft(usable_m2 * 0.25)
        side_total_sqft = sqm_to_sqft(usable_m2 * 0.20)

        # Setbacks: bounding-box distances from lot edges to building edges.
        lminx, lminy, lmaxx, lmaxy = lot_m.bounds
        bminx, bminy, bmaxx, bmaxy = bldg_m.bounds
        left_side_ft = m_to_ft(max(bminx - lminx, 0))
        right_side_ft = m_to_ft(max(lmaxx - bmaxx, 0))
        front_setback_ft = m_to_ft(max(bminy - lminy, 0))
        rear_setback_ft = m_to_ft(max(lmaxy - bmaxy, 0))
        notes.append(
            "Yard split (back/front/side 55/25/20) is a heuristic — true split "
            "requires parcel orientation (street frontage edge)."
        )
        notes.append(f"Side-yard distances ({left_side_ft:.0f}ft / {right_side_ft:.0f}ft) "
                     "are bounding-box approximations.")
        _ = side_total_sqft  # accounted into side yard distances above
    elif lot_sqft > 0 and bldg_m is not None:
        usable_sqft = max(lot_sqft - building_sqft, 0.0)
        backyard_sqft = usable_sqft * 0.55
        frontyard_sqft = usable_sqft * 0.25
        notes.append("Lot polygon not available — yard split is a flat heuristic "
                     "from user-supplied lot size minus building footprint.")
    elif lot_sqft > 0:
        backyard_sqft = lot_sqft * 0.55
        frontyard_sqft = lot_sqft * 0.25
        notes.append("Building footprint unknown — yard estimates assume "
                     "an empty lot.")

    # Confidence
    if lot_poly is not None and building_poly is not None:
        confidence = "Medium"
    elif lot_sqft_override is not None and building_poly is not None:
        confidence = "Medium"
    elif lot_sqft > 0 or building_poly is not None:
        confidence = "Low"
    else:
        confidence = "Very Low"

    return PropertyMeasurements(
        lot_sqft=lot_sqft,
        lot_acres=sqft_to_acres(lot_sqft) if lot_sqft else 0.0,
        building_sqft=building_sqft,
        backyard_sqft=backyard_sqft,
        frontyard_sqft=frontyard_sqft,
        left_side_ft=left_side_ft,
        right_side_ft=right_side_ft,
        front_setback_ft=front_setback_ft,
        rear_setback_ft=rear_setback_ft,
        confidence=confidence,
        notes=notes + confidence_parts,
    )
