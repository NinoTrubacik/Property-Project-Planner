"""Cost model: scale national baseline by size, finish, and regional CCI.

Baselines carry a vintage_year; totals are automatically escalated by
annual_escalation for every year after that, so estimates stay roughly
current without manual data edits.
"""
from __future__ import annotations

import datetime
import json
from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _load(name: str) -> dict:
    return json.loads((DATA_DIR / name).read_text())


_BASELINES = _load("cost_baselines.json")
_CCI = _load("regional_cci.json")


def _escalation() -> tuple[float, str]:
    """Inflation factor from baseline vintage year to the current year."""
    meta = _BASELINES.get("_meta", {})
    vintage = meta.get("vintage_year")
    rate = meta.get("annual_escalation", 0.035)
    if not vintage:
        return 1.0, ""
    years = max(datetime.date.today().year - int(vintage), 0)
    if years == 0:
        return 1.0, f"Baseline prices are current ({vintage})."
    factor = (1 + rate) ** years
    return factor, (f"Prices escalated ×{factor:.3f} "
                    f"({rate:.1%}/yr for {years} yr since {vintage} baseline).")


def list_projects() -> dict[str, str]:
    return {k: v["display_name"] for k, v in _BASELINES.items() if not k.startswith("_")}


def regional_multiplier(state: str, zip_code: str = "") -> tuple[float, str]:
    """Return (multiplier, note). ZIP overrides state when matched."""
    state_mult = _CCI["states"].get(state.upper())
    if zip_code:
        for prefix, mult in _CCI["metro_overrides"].items():
            if zip_code.startswith(prefix):
                return mult, f"Metro override for ZIP prefix {prefix}: ×{mult:.2f}"
    if state_mult is not None:
        return state_mult, f"State-level CCI for {state.upper()}: ×{state_mult:.2f}"
    return 1.00, "No CCI match — using national average ×1.00"


def _scale_for_size(base_total: float, target_sqft: float,
                    ref_sqft: float, exponent: float) -> float:
    if target_sqft <= 0:
        return base_total
    return base_total * (target_sqft / ref_sqft) ** exponent


@dataclass
class CostLine:
    name: str
    subtotal: float
    note: str = ""


@dataclass
class CostEstimate:
    project_key: str
    project_name: str
    target_sqft: float
    finish: str
    region_state: str
    region_zip: str
    region_multiplier: float
    region_note: str
    finish_multiplier: float
    materials_total: float
    labor_total: float
    permits_total: float
    contingency_total: float
    grand_total_mid: float
    grand_total_low: float
    grand_total_high: float
    materials_items: list[CostLine] = field(default_factory=list)
    labor_phases: list[CostLine] = field(default_factory=list)
    confidence: str = "Medium"
    notes: list[str] = field(default_factory=list)


def estimate(project_key: str,
             target_sqft: float | None,
             finish: str,
             state: str,
             zip_code: str = "") -> CostEstimate:
    spec = _BASELINES.get(project_key)
    if spec is None:
        raise ValueError(f"Unknown project_key '{project_key}'")

    target = float(target_sqft) if target_sqft else float(spec["default_size_sqft"])
    finish = finish if finish in spec["finish_multipliers"] else "Mid-Range"
    finish_mult = spec["finish_multipliers"][finish]
    region_mult, region_note = regional_multiplier(state, zip_code)

    esc_factor, esc_note = _escalation()
    scaled_base = _scale_for_size(
        spec["national_avg_total"] * esc_factor, target,
        spec["size_scaling"]["ref_sqft"],
        spec["size_scaling"]["exponent"],
    )
    adjusted_total = scaled_base * finish_mult * region_mult

    materials_total = adjusted_total * spec["materials_share"]
    labor_total = adjusted_total * spec["labor_share"]
    permits_total = adjusted_total * spec["permits_share"]
    contingency_total = adjusted_total * spec["contingency_share"]

    materials_items = [
        CostLine(name=it["item"], subtotal=materials_total * it["share"])
        for it in spec["materials_items"]
    ]
    labor_phases = [
        CostLine(
            name=f"{ph['phase']} — {ph['trade']}",
            subtotal=labor_total * ph["share"],
            note=f"~{ph['days']} day(s)",
        )
        for ph in spec["labor_phases"]
    ]

    low = adjusted_total * 0.85
    high = adjusted_total * 1.20

    notes = [
        f"Baseline national avg for {spec['display_name']} at Mid-Range / 1.00 CCI: "
        f"${spec['national_avg_total']:,} ({_BASELINES.get('_meta', {}).get('vintage', 'unknown vintage')}).",
    ]
    if esc_note:
        notes.append(esc_note)
    notes += [
        f"Size scaling applied: ({target:.0f} / {spec['size_scaling']['ref_sqft']}) ** "
        f"{spec['size_scaling']['exponent']}.",
        f"Finish '{finish}' multiplier ×{finish_mult:.2f}.",
        region_note,
        "Low/High band ≈ −15% / +20% to capture quote variance.",
    ]

    return CostEstimate(
        project_key=project_key,
        project_name=spec["display_name"],
        target_sqft=target,
        finish=finish,
        region_state=state,
        region_zip=zip_code,
        region_multiplier=region_mult,
        region_note=region_note,
        finish_multiplier=finish_mult,
        materials_total=materials_total,
        labor_total=labor_total,
        permits_total=permits_total,
        contingency_total=contingency_total,
        grand_total_mid=adjusted_total,
        grand_total_low=low,
        grand_total_high=high,
        materials_items=materials_items,
        labor_phases=labor_phases,
        confidence="Medium" if region_mult != 1.00 else "Low",
        notes=notes,
    )


def budget_status(estimate_obj: CostEstimate,
                  budget_low: float | None,
                  budget_high: float | None) -> tuple[str, str]:
    if budget_high is None:
        return "No budget given", "Provide a budget range to get a fit assessment."
    mid = estimate_obj.grand_total_mid
    if mid <= budget_high * 0.85:
        return "Within Budget", (
            f"Mid estimate (${mid:,.0f}) sits comfortably under your "
            f"${budget_high:,.0f} ceiling.")
    if mid <= budget_high:
        return "Tight", (
            f"Mid estimate (${mid:,.0f}) is within budget but with little headroom "
            f"for the high band (${estimate_obj.grand_total_high:,.0f}).")
    return "Over Budget", (
        f"Mid estimate (${mid:,.0f}) exceeds your ${budget_high:,.0f} ceiling. "
        f"Consider dropping finish tier, reducing size, or staging the work.")
