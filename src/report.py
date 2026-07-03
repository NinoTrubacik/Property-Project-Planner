"""End-to-end pipeline: address -> measurements -> feasibility -> cost -> structured report."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from PIL import Image

from . import (costs, county_gis, feasibility, geocode, geometry, imagery,
               nominatim, osm, restricted)


@dataclass
class PropertyReport:
    ok: bool
    error: str = ""
    address: str = ""
    state: str = ""
    zip_code: str = ""
    lat: float = 0.0
    lon: float = 0.0
    satellite: Image.Image | None = None
    measurements: geometry.PropertyMeasurements | None = None
    feasibility: feasibility.FeasibilityResult | None = None
    cost: costs.CostEstimate | None = None
    budget_verdict: str = ""
    budget_note: str = ""
    next_steps: list[str] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    blocked: bool = False
    blocked_reason: str = ""
    # Provenance for the UI's trust indicator
    building_confidence: str = "none"   # high / medium / low / none
    building_method: str = ""
    parcel_confidence: str = "none"
    parcel_method: str = ""
    parcel_situs_address: str = ""   # County's authoritative address for the matched parcel
    imagery_capture_date: str = ""   # e.g. "2026-01-19" — from Esri metadata
    imagery_source: str = ""         # e.g. "Vantor · 0.34 m/px"


def _next_steps(project_key: str, feas: feasibility.FeasibilityResult,
                cost: costs.CostEstimate) -> list[str]:
    steps: list[str] = []
    if feas.verdict == "NO":
        steps.append("Pause: project as-scoped doesn't fit. Reduce target size or pick an alternative project.")
    steps += [
        "Order a stamped survey to confirm exact lot lines and setbacks before applying for permits.",
        "Call 811 to mark underground utilities at the planned work area.",
        "Pull required permits: " + ", ".join(feas.permits) if feas.permits else "Verify local permit requirements with the AHJ.",
        "Request 3 written bids from licensed local contractors for apples-to-apples comparison.",
        "Confirm HOA / deed restrictions and easements with your title report.",
    ]
    if project_key in ("inground_pool", "adu", "addition"):
        steps.append("Get a soils/percolation report — bearing capacity and drainage affect foundation cost.")
    if project_key == "adu":
        steps.append("Confirm sewer lateral & electrical service capacity; service upgrades are a common surprise.")
    return steps


def validate_address(address: str) -> dict:
    """Quick first-pass check used by the landing page.

    Runs geocode + lower-48 + restricted-site gate only — no OSM, imagery,
    or cost work. Returns:
        {"ok": True, "matched": ..., "state": ..., "zip": ..., "lat": ..., "lon": ...}
        {"ok": False, "blocked": True}   # generic refusal, no derived data
        {"ok": False, "error": "..."}    # geocode failure / outside lower 48
    """
    try:
        geo = geocode.geocode(address)
    except Exception as e:
        return {"ok": False, "error": f"Geocoder error: {e}"}
    if geo is None:
        return {"ok": False, "error": "Address not found by US Census geocoder. "
                                       "Check spelling or try a nearby cross-street."}
    if not geo.in_lower_48():
        return {"ok": False,
                "error": f"Address is outside the contiguous 48 states ({geo.state}). "
                         "This app only supports the mainland US."}
    if restricted.check(geo.lat, geo.lon).blocked:
        return {"ok": False, "blocked": True}
    return {
        "ok": True,
        "matched": geo.matched_address,
        "state": geo.state,
        "zip": geo.zip_code,
        "lat": geo.lat,
        "lon": geo.lon,
    }


def run_pipeline(
    address: str,
    project_key: str,
    target_size_sqft: float | None = None,
    lot_sqft_override: float | None = None,
    finish: str = "Mid-Range",
    budget_low: float | None = None,
    budget_high: float | None = None,
    special_notes: str = "",
    fetch_imagery: bool = True,
) -> PropertyReport:
    rep = PropertyReport(ok=False)

    # 1. Geocode
    try:
        geo = geocode.geocode(address)
    except Exception as e:
        rep.error = f"Geocoder error: {e}"
        return rep
    if geo is None:
        rep.error = "Address not found by US Census geocoder. Check spelling or try a nearby cross-street."
        return rep
    if not geo.in_lower_48():
        rep.error = f"Address resolves outside the contiguous 48 states ({geo.state}). This app only supports the mainland US."
        return rep

    # 1b. Restricted-site gate runs BEFORE we surface any geocoded data.
    # If blocked, the report carries only the refusal — no address, no
    # coordinates, no diagnostics about the site.
    restriction = restricted.check(geo.lat, geo.lon)
    if restriction.blocked:
        return PropertyReport(
            ok=False,
            blocked=True,
            blocked_reason=restriction.reason,
            error=("This address falls within a restricted government, military, "
                   "or diplomatic site. This app does not analyze such properties."),
        )

    rep.address = geo.matched_address
    rep.state = geo.state
    rep.zip_code = geo.zip_code
    rep.lat = geo.lat
    rep.lon = geo.lon
    rep.diagnostics.append(f"Geocoded to {geo.lat:.6f}, {geo.lon:.6f} ({geo.state})")
    rep.diagnostics.append("Restriction check: clear")

    # 2. Property data lookups
    # We may refine coords via Nominatim — track the "best known" position
    # so downstream lookups (county GIS, OSM parcel) all benefit.
    best_lat, best_lon = geo.lat, geo.lon

    # ===== BUILDING (footprint) =====
    # Cascade: OSM addr-tag match → Nominatim direct match → OSM nearest (refined coords)
    building_poly = None
    try:
        bm = osm.find_building(geo.lat, geo.lon,
                               housenumber=geo.housenumber, street=geo.street)
        building_poly = bm.polygon
        rep.building_confidence = bm.confidence
        rep.building_method = bm.method
        rep.diagnostics.append(f"Building (OSM): {bm.confidence} — {bm.method}")
    except Exception as e:
        rep.diagnostics.append(f"OSM building lookup failed: {e}")

    # Nominatim fallback when OSM didn't find a confident match.
    if rep.building_confidence in ("low", "none"):
        try:
            nm = nominatim.lookup(address)
        except Exception as e:
            nm = None
            rep.diagnostics.append(f"Nominatim lookup failed: {e}")
        if nm is not None:
            rep.diagnostics.append(
                f"Nominatim: {nm.place_class}/{nm.place_type} @ {nm.lat:.5f},{nm.lon:.5f}"
                + (" + polygon" if nm.polygon is not None else "")
            )
            best_lat, best_lon = nm.lat, nm.lon  # refine for downstream lookups
            if nm.polygon is not None:
                building_poly = nm.polygon
                rep.building_confidence = "high"
                rep.building_method = f"Nominatim direct match (OSM {nm.osm_type} {nm.osm_id})"
            else:
                # Re-run OSM cascade with the refined coords (Nominatim often
                # pinpoints the actual building, fixing Census street-centerline drift).
                try:
                    bm2 = osm.find_building(nm.lat, nm.lon,
                                            housenumber=geo.housenumber,
                                            street=geo.street)
                except Exception:
                    bm2 = None
                if bm2 and bm2.confidence in ("high", "medium"):
                    building_poly = bm2.polygon
                    rep.building_confidence = bm2.confidence
                    rep.building_method = f"OSM via Nominatim-refined coords ({bm2.method})"
                    rep.diagnostics.append(
                        f"Building (refined): {bm2.confidence} — {bm2.method}"
                    )

    # ===== PARCEL (lot polygon) =====
    # County ArcGIS is authoritative where covered. Census coords often land
    # in the street (outside any parcel), so use the building centroid when
    # available — it's guaranteed to be inside the parcel polygon.
    parcel_lat, parcel_lon = best_lat, best_lon
    parcel_coord_src = "refined" if (best_lat, best_lon) != (geo.lat, geo.lon) else "census"
    if building_poly is not None:
        c = building_poly.centroid
        parcel_lat, parcel_lon = c.y, c.x
        parcel_coord_src = "building centroid"

    lot_poly = None
    try:
        cm = county_gis.lookup(geo.state, geo.zip_code, parcel_lat, parcel_lon,
                               requested_housenumber=geo.housenumber)
        rep.diagnostics.append(
            f"Parcel (county GIS, {parcel_coord_src} coords): "
            f"{cm.confidence} — {cm.method}"
        )
        # Low confidence means a known situs mismatch — that polygon is a
        # neighbor's lot. Don't use it for measurements or the outline.
        if cm.polygon is not None and cm.confidence in ("high", "medium"):
            lot_poly = cm.polygon
            rep.parcel_confidence = cm.confidence
            rep.parcel_method = cm.method + (f" · ID {cm.parcel_id}" if cm.parcel_id else "")
            rep.parcel_situs_address = cm.situs_address
        elif cm.polygon is not None:
            rep.parcel_confidence = "low"
            rep.parcel_method = cm.method
    except Exception as e:
        rep.diagnostics.append(f"County GIS lookup failed: {e}")

    # If county GIS still failed and we never tried Nominatim, try it now to
    # refine coords, then retry county GIS.
    if lot_poly is None and parcel_coord_src == "census":
        try:
            nm2 = nominatim.lookup(address)
        except Exception:
            nm2 = None
        if nm2 is not None:
            try:
                cm = county_gis.lookup(geo.state, geo.zip_code, nm2.lat, nm2.lon,
                                       requested_housenumber=geo.housenumber)
                rep.diagnostics.append(
                    f"Parcel (county GIS retry, nominatim coords): "
                    f"{cm.confidence} — {cm.method}"
                )
                if cm.polygon is not None and cm.confidence in ("high", "medium"):
                    lot_poly = cm.polygon
                    rep.parcel_confidence = cm.confidence
                    rep.parcel_method = cm.method + (
                        f" · ID {cm.parcel_id}" if cm.parcel_id else ""
                    )
                    rep.parcel_situs_address = cm.situs_address
            except Exception as e:
                rep.diagnostics.append(f"County GIS retry failed: {e}")

    if lot_poly is None:
        try:
            pm = osm.find_parcel(parcel_lat, parcel_lon)
            if pm.polygon is not None:
                lot_poly = pm.polygon
                rep.parcel_confidence = pm.confidence
                rep.parcel_method = pm.method
            rep.diagnostics.append(f"Parcel (OSM): {pm.confidence} — {pm.method}")
        except Exception as e:
            rep.diagnostics.append(f"OSM parcel lookup failed: {e}")

    # 3. Measurements
    rep.measurements = geometry.measure(
        lot_poly=lot_poly,
        building_poly=building_poly,
        lot_sqft_override=lot_sqft_override,
        center_lat=geo.lat,
        center_lon=geo.lon,
    )

    # 4. Satellite imagery with red property outline (only when we trust the match).
    if fetch_imagery:
        try:
            tile = imagery.fetch_tiles(geo.lat, geo.lon, zoom=19, grid=4)
            outline_src = None
            # Prefer parcel polygon when confident, else building when confident.
            if lot_poly is not None and rep.parcel_confidence in ("high", "medium"):
                imagery.draw_property_outline(tile, lot_poly)
                outline_src = f"parcel polygon ({rep.parcel_method})"
            elif building_poly is not None and rep.building_confidence in ("high", "medium"):
                imagery.draw_property_outline(tile, building_poly)
                outline_src = f"building footprint ({rep.building_method})"
            else:
                imagery.draw_marker(tile, geo.lat, geo.lon)
                if building_poly is not None and rep.building_confidence == "low":
                    outline_src = ("center crosshair (low-confidence building match "
                                   "not drawn to avoid showing a neighbor's house)")
                else:
                    outline_src = "center crosshair (no polygon available)"
            rep.satellite = tile.image
            rep.diagnostics.append(
                f"Esri imagery @ z19, 4×4 mosaic ({tile.pixel_width}×{tile.pixel_height}px); "
                f"overlay = {outline_src}"
            )
            info = imagery.fetch_capture_info(geo.lat, geo.lon)
            if info.get("date"):
                rep.imagery_capture_date = info["date"]
                src_bits = []
                if info.get("source"):
                    src_bits.append(info["source"])
                if info.get("resolution_m"):
                    src_bits.append(f"{info['resolution_m']:.2f} m/px")
                rep.imagery_source = " · ".join(src_bits)
                rep.diagnostics.append(
                    f"Imagery captured {rep.imagery_capture_date}"
                    + (f" ({rep.imagery_source})" if rep.imagery_source else "")
                )
            else:
                rep.diagnostics.append("Imagery capture date unavailable from Esri metadata")
        except Exception as e:
            rep.diagnostics.append(f"Imagery fetch failed: {e}")

    # 5. Feasibility
    rep.feasibility = feasibility.assess(
        project_key=project_key,
        target_size_sqft=target_size_sqft,
        measurements=rep.measurements,
        special_notes=special_notes,
    )

    # 6. Cost estimate
    try:
        rep.cost = costs.estimate(
            project_key=project_key,
            target_sqft=target_size_sqft,
            finish=finish,
            state=geo.state,
            zip_code=geo.zip_code,
        )
    except ValueError as e:
        rep.error = str(e)
        return rep

    # 7. Budget compatibility
    rep.budget_verdict, rep.budget_note = costs.budget_status(rep.cost, budget_low, budget_high)

    # 8. Next steps
    rep.next_steps = _next_steps(project_key, rep.feasibility, rep.cost)

    rep.ok = True
    return rep
