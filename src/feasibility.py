"""Feasibility rules: compare measured property against project minimums."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path

from .geometry import PropertyMeasurements


DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _load(name: str) -> dict:
    return json.loads((DATA_DIR / name).read_text())


_PROJECT_SPECS = _load("project_specs.json")


@dataclass
class FeasibilityResult:
    verdict: str           # YES / POSSIBLE / NO
    summary: str
    clearance_checks: list[dict] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    permits: list[str] = field(default_factory=list)


def assess(project_key: str,
           target_size_sqft: float | None,
           measurements: PropertyMeasurements,
           special_notes: str = "") -> FeasibilityResult:
    spec = _PROJECT_SPECS.get(project_key)
    if spec is None:
        return FeasibilityResult(
            verdict="POSSIBLE",
            summary=f"No spec rules loaded for '{project_key}'. Manual review required.",
        )

    checks: list[dict] = []

    # Usable yard: prefer backyard, fall back to full lot - building.
    available_yard = measurements.backyard_sqft if measurements.backyard_sqft > 0 \
        else max(measurements.lot_sqft - measurements.building_sqft, 0)
    needed_yard = max(spec["min_usable_yard_sqft"],
                      (target_size_sqft or 0) * 1.5)  # 1.5x for surround clearance
    checks.append({
        "name": "Usable backyard space",
        "required": f"{needed_yard:,.0f} sqft",
        "available": f"{available_yard:,.0f} sqft" if available_yard else "unknown",
        "passes": available_yard >= needed_yard if available_yard else None,
    })

    # Side clearance: worst of the two sides.
    min_side = min(measurements.left_side_ft, measurements.right_side_ft) \
        if (measurements.left_side_ft or measurements.right_side_ft) else 0
    checks.append({
        "name": "Side-yard clearance",
        "required": f"{spec['min_side_clearance_ft']} ft",
        "available": f"{min_side:.1f} ft" if min_side else "unknown",
        "passes": min_side >= spec["min_side_clearance_ft"] if min_side else None,
    })

    # Rear setback
    checks.append({
        "name": "Rear setback",
        "required": f"{spec['min_rear_setback_ft']} ft",
        "available": f"{measurements.rear_setback_ft:.1f} ft" if measurements.rear_setback_ft else "unknown",
        "passes": measurements.rear_setback_ft >= spec["min_rear_setback_ft"]
                  if measurements.rear_setback_ft else None,
    })

    # Equipment access (assume narrower side gate is the access path)
    checks.append({
        "name": "Equipment access width",
        "required": f"{spec['min_equipment_access_width_ft']} ft",
        "available": f"{min_side:.1f} ft" if min_side else "unknown",
        "passes": min_side >= spec["min_equipment_access_width_ft"] if min_side else None,
        "note": "Assumes narrowest side yard is the equipment access path.",
    })

    # Verdict
    decisive_passes = [c["passes"] for c in checks if c["passes"] is not None]
    if not decisive_passes:
        verdict = "POSSIBLE"
        summary = ("Not enough measurement confidence to give a hard verdict. "
                   "Verify with a site survey.")
    elif all(decisive_passes):
        verdict = "YES"
        summary = "Property meets all baseline clearance and setback minimums."
    elif any(not p for p in decisive_passes if p is False):
        failed = [c["name"] for c in checks if c["passes"] is False]
        if len(failed) == 1:
            verdict = "POSSIBLE"
            summary = f"Falls short on: {failed[0]}. May be workable with design changes."
        else:
            verdict = "NO"
            summary = f"Fails multiple minimums: {', '.join(failed)}."
    else:
        verdict = "POSSIBLE"
        summary = "Some required measurements are unknown — verdict provisional."

    # Risk flags
    risks = list(spec.get("common_risks", []))
    if measurements.lot_sqft and measurements.building_sqft:
        coverage = measurements.building_sqft / measurements.lot_sqft
        if coverage > 0.35:
            risks.append(f"Existing lot coverage ({coverage*100:.0f}%) is high — "
                         "additions may push past local coverage cap.")
    if measurements.confidence in ("Low", "Very Low"):
        risks.append(f"Measurement confidence is {measurements.confidence} — order a survey before permitting.")
    if special_notes.strip():
        risks.append(f"User-noted concerns: {special_notes.strip()}")

    return FeasibilityResult(
        verdict=verdict,
        summary=summary,
        clearance_checks=checks,
        risk_flags=risks,
        permits=list(spec.get("typical_permits", [])),
    )
