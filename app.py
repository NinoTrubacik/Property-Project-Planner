"""Streamlit UI — friendly 3-stage flow: address → project → report.

Mainland US (lower 48) only. Refuses analysis on government / restricted sites.
"""
from __future__ import annotations

import streamlit as st

from src import costs as costs_mod
from src.report import run_pipeline, validate_address

st.set_page_config(page_title="Property Project Planner", layout="wide")

PROJECTS = costs_mod.list_projects()
FINISHES = ["Basic", "Mid-Range", "Premium", "Luxury"]


# ------- session state -------
def _init_state():
    st.session_state.setdefault("stage", "address")
    st.session_state.setdefault("address_typed", "")
    st.session_state.setdefault("geo", None)
    st.session_state.setdefault("report", None)
    st.session_state.setdefault("theme", "light")


def go_to(stage: str):
    st.session_state.stage = stage


def reset_all():
    for k in ("stage", "address_typed", "geo", "report"):
        st.session_state.pop(k, None)
    _init_state()


_init_state()


# ------- theme toggle -------
_DARK_CSS = """
<style>
:root { color-scheme: dark; }
.stApp, [data-testid="stAppViewContainer"], [data-testid="stHeader"] {
    background-color: #0e1117 !important;
}
.stApp, .stApp p, .stApp li, .stApp label, .stApp span,
.stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp h5 {
    color: #e8eaed !important;
}
.stApp [data-testid="stCaptionContainer"],
.stApp [data-testid="stCaptionContainer"] * { color: #9aa0a6 !important; }
.stApp hr { border-color: #2a2e37 !important; }
.stApp input, .stApp textarea,
.stApp [data-baseweb="input"], .stApp [data-baseweb="select"] > div,
.stApp [data-baseweb="base-input"] {
    background-color: #1c1f26 !important;
    color: #e8eaed !important;
    border-color: #3a3f4b !important;
}
.stApp input::placeholder, .stApp textarea::placeholder { color: #6b7280 !important; }
.stApp [data-testid="stExpander"] summary,
.stApp [data-testid="stExpander"] details {
    background-color: #1c1f26 !important;
    border-color: #2a2e37 !important;
}
.stApp button[kind="secondary"], .stApp [data-testid="stBaseButton-secondary"] {
    background-color: #1c1f26 !important;
    color: #e8eaed !important;
    border-color: #3a3f4b !important;
}
.stApp button[kind="secondary"]:hover, .stApp [data-testid="stBaseButton-secondary"]:hover {
    background-color: #262a33 !important;
    border-color: #2c5f8a !important;
    color: #ffffff !important;
}
.stApp button[kind="primary"], .stApp [data-testid="stBaseButton-primary"] {
    color: #ffffff !important;
}
.stApp table { color: #e8eaed !important; }
.stApp thead tr th { background-color: #1c1f26 !important; color: #e8eaed !important; }
.stApp tbody tr td { background-color: #12151c !important; border-color: #2a2e37 !important; }
</style>
"""


def _apply_theme():
    """Small top-right button that flips light/dark; injects dark CSS on demand."""
    spacer, btn = st.columns([11, 1])
    with btn:
        is_dark = st.session_state.theme == "dark"
        label = "Light" if is_dark else "Dark"
        if st.button(label, key="theme_toggle", use_container_width=True,
                     help="Switch between light and dark mode"):
            st.session_state.theme = "light" if is_dark else "dark"
            st.rerun()
    if st.session_state.theme == "dark":
        st.markdown(_DARK_CSS, unsafe_allow_html=True)


_apply_theme()


# ------- formatting helpers -------
def fmt_money(v: float) -> str:
    return f"${v:,.0f}"


def fmt_sqft(v: float) -> str:
    return f"{v:,.0f} sqft" if v else "—"


def fmt_ft(v: float) -> str:
    return f"{v:.1f} ft" if v else "—"


# ========================================
# STAGE 1: Landing — address bar only
# ========================================
def render_landing():
    pad_l, mid, pad_r = st.columns([1, 2, 1])
    with mid:
        st.write("")
        st.write("")
        st.markdown("# Property Project Planner")
        st.markdown(
            "#### Know what fits — and what it costs — before you call a contractor."
        )
        st.markdown(
            "Thinking about a pool, a garage, an ADU, or an addition? Start with your "
            "address. We pull your county's parcel lines and current satellite imagery, "
            "measure the buildable space, and give you an honest cost range you can "
            "actually plan around."
        )
        st.write("")
        st.markdown("**Enter a property address to begin**")
        # A form lets Enter / Return submit the address just like the button.
        with st.form("address_form", border=False):
            address = st.text_input(
                "Property address",
                value=st.session_state.address_typed,
                placeholder="e.g. 123 Main St, City, ST 00000",
                label_visibility="collapsed",
                key="address_bar",
            )
            go = st.form_submit_button("Find property", type="primary",
                                       use_container_width=True)

        if go:
            if not address.strip():
                st.warning("Please type an address first.")
                return
            with st.spinner("Looking up address…"):
                res = validate_address(address.strip())
            if res.get("blocked"):
                st.error(
                    "**Analysis refused — restricted site.** This app does not "
                    "return any data for federal critical infrastructure, state "
                    "capitols, military installations, or diplomatic facilities."
                )
                return
            if not res.get("ok"):
                st.error(res.get("error", "Address could not be validated."))
                return
            st.session_state.geo = res
            st.session_state.address_typed = address.strip()
            go_to("project")
            st.rerun()

        st.write("")
        st.write("")
        st.markdown("---")
        st.markdown("#### How it works")
        s1, s2, s3 = st.columns(3)
        with s1:
            st.markdown("**1 · Locate**")
            st.caption("We match your address to the authoritative county parcel "
                       "record and draw the exact lot lines on live satellite imagery.")
        with s2:
            st.markdown("**2 · Measure**")
            st.caption("Lot size, building footprint, yard space, and setbacks — "
                       "calculated from the real parcel geometry, not a guess.")
        with s3:
            st.markdown("**3 · Estimate**")
            st.caption("Feasibility, permit flags, and a materials-and-labor cost "
                       "range tuned to your region and finish level.")

        st.write("")
        st.markdown("#### Projects it plans")
        p1, p2 = st.columns(2)
        with p1:
            st.markdown(
                "- In-ground & above-ground pools\n"
                "- Detached garages\n"
                "- Accessory dwelling units (ADUs)\n"
            )
        with p2:
            st.markdown(
                "- Home additions\n"
                "- Sport courts\n"
                "- Storage sheds\n"
            )
        st.caption("Contiguous US · Free public data (county assessors, US Census, "
                   "Esri imagery) · Planning estimates only — not a substitute for a "
                   "stamped survey or a contractor's bid.")


# ========================================
# STAGE 2: Project picker
# ========================================
def render_project_picker():
    g = st.session_state.geo
    pad_l, mid, pad_r = st.columns([1, 2, 1])
    with mid:
        st.markdown("# What do you want to build?")
        st.success(f"Located: **{g['matched']}** ({g['state']} {g['zip']})")
        st.write("")

        project_key = st.selectbox(
            "Project type",
            options=list(PROJECTS.keys()),
            format_func=lambda k: PROJECTS[k],
        )

        with st.expander("Project details (optional)", expanded=True):
            c1, c2 = st.columns(2)
            with c1:
                target_size = st.number_input(
                    "Target size (sqft) — 0 = use default",
                    min_value=0, max_value=10000, value=0, step=10,
                    help="Example: 512 for a 16×32 pool.",
                )
                finish = st.selectbox("Finish level", FINISHES, index=1)
            with c2:
                lot_override = st.number_input(
                    "Lot size from your deed (sqft)",
                    min_value=0, value=0, step=100,
                    help="Used when OSM has no parcel polygon for this address.",
                )
                budget_high = st.number_input(
                    "Budget ceiling ($)",
                    min_value=0, value=0, step=1000,
                )

        special_notes = st.text_area(
            "Special notes (optional)",
            placeholder="HOA restrictions, slope, septic, easements, known utilities…",
        )

        st.write("")
        b1, b2 = st.columns([1, 4])
        with b1:
            if st.button("← Back"):
                go_to("address")
                st.rerun()
        with b2:
            run = st.button("Run analysis", type="primary", use_container_width=True)

        if run:
            with st.spinner("Fetching satellite imagery, parcel data, computing estimates…"):
                rep = run_pipeline(
                    address=st.session_state.address_typed,
                    project_key=project_key,
                    target_size_sqft=target_size if target_size > 0 else None,
                    lot_sqft_override=lot_override if lot_override > 0 else None,
                    finish=finish,
                    budget_high=budget_high if budget_high > 0 else None,
                    special_notes=special_notes,
                )
            st.session_state.report = rep
            go_to("results")
            st.rerun()


# ========================================
# STAGE 3: Results
# ========================================
def render_results():
    rep = st.session_state.report

    top_l, top_r = st.columns([5, 1])
    with top_r:
        if st.button("← New search", use_container_width=True):
            reset_all()
            st.rerun()

    if not rep.ok:
        if rep.blocked:
            st.error("Analysis refused — restricted site")
            st.caption("This app does not return any data for federal critical "
                       "infrastructure, state capitols, military installations, "
                       "or diplomatic facilities.")
        else:
            st.error(rep.error or "Pipeline failed.")
            with st.expander("Diagnostics"):
                for d in rep.diagnostics:
                    st.write("•", d)
        return

    # 1. Property Summary
    st.header("1. Property Summary")
    col_l, col_r = st.columns([1.1, 1])
    with col_l:
        if rep.satellite is not None:
            # Pick caption based on what was actually drawn.
            if rep.parcel_confidence in ("high", "medium"):
                cap = (f"Esri World Imagery · red outline = parcel polygon "
                       f"({rep.parcel_confidence} confidence)")
            elif rep.building_confidence in ("high", "medium"):
                cap = (f"Esri World Imagery · red outline = building footprint "
                       f"({rep.building_confidence} confidence)")
            else:
                cap = ("Esri World Imagery · crosshair marks the geocoded point "
                       "(no confident polygon match — outline withheld)")
            if rep.imagery_capture_date:
                cap += f" · photo taken {rep.imagery_capture_date}"
                if rep.imagery_source:
                    cap += f" ({rep.imagery_source})"
            st.image(rep.satellite, caption=cap, use_container_width=True)

            # Trust badge under the image.
            confidence = rep.parcel_confidence if rep.parcel_confidence != "none" \
                else rep.building_confidence
            badge = {"high": "High confidence",
                     "medium": "Medium confidence",
                     "low": "Low confidence",
                     "none": "No polygon match"}[confidence]
            st.markdown(f"**Match quality:** {badge}")
            if rep.building_method:
                st.caption(f"Building: {rep.building_method}")
            if rep.parcel_method:
                st.caption(f"Parcel: {rep.parcel_method}")
            if rep.parcel_situs_address:
                st.caption(f"County address-of-record: **{rep.parcel_situs_address}** — "
                           "verify this matches the property you entered.")
            if confidence in ("low", "none"):
                st.warning(
                    "OSM didn't have a confident match for this address. "
                    "Building footprint and lot size below may be wrong — "
                    "supply the lot size from your deed and verify the structure "
                    "in person before relying on the estimate."
                )
        else:
            st.warning("Satellite imagery could not be loaded (tile server may be rate-limited).")
    with col_r:
        st.markdown(f"**Matched address:** {rep.address}")
        st.markdown(f"**State / ZIP:** {rep.state} / {rep.zip_code}")
        st.markdown(f"**Coordinates:** {rep.lat:.6f}, {rep.lon:.6f}")
        if rep.measurements:
            st.markdown(f"**Lot size:** {fmt_sqft(rep.measurements.lot_sqft)} "
                        f"({rep.measurements.lot_acres:.3f} ac)")
            st.markdown(f"**Building footprint:** {fmt_sqft(rep.measurements.building_sqft)}")
            st.markdown(f"**Measurement confidence:** {rep.measurements.confidence}")

    # 2. Space Breakdown
    st.header("2. Space Breakdown")
    m = rep.measurements
    if m:
        st.table({
            "Zone": ["Backyard (usable)", "Front yard", "Side yard — left", "Side yard — right",
                     "Front setback (bldg → front lot edge)", "Rear setback (bldg → rear lot edge)"],
            "Measurement": [fmt_sqft(m.backyard_sqft), fmt_sqft(m.frontyard_sqft),
                            fmt_ft(m.left_side_ft), fmt_ft(m.right_side_ft),
                            fmt_ft(m.front_setback_ft), fmt_ft(m.rear_setback_ft)],
        })
        if m.notes:
            with st.expander("Measurement notes & confidence detail"):
                for n in m.notes:
                    st.write("•", n)

    # 3. Feasibility Verdict
    st.header("3. Feasibility Verdict")
    f = rep.feasibility
    st.subheader(f.verdict)
    st.write(f.summary)

    # 4. Clearance Analysis
    st.header("4. Clearance Analysis")
    rows = []
    for c in f.clearance_checks:
        status = "Pass" if c["passes"] is True else ("Fail" if c["passes"] is False else "Unknown")
        rows.append([status, c["name"], c["required"], c["available"], c.get("note", "")])
    st.table({
        "Status": [r[0] for r in rows],
        "Check": [r[1] for r in rows],
        "Required": [r[2] for r in rows],
        "Available": [r[3] for r in rows],
        "Note": [r[4] for r in rows],
    })

    # 5. Risk Flags
    st.header("5. Risk Flags")
    if f.risk_flags:
        for r in f.risk_flags:
            st.write("–", r)
    else:
        st.write("No risks flagged from generic rules. Still verify locally.")

    # 6. Materials
    c = rep.cost
    st.header("6. Cost Estimate — Materials")
    st.table({
        "Item": [li.name for li in c.materials_items],
        "Subtotal": [fmt_money(li.subtotal) for li in c.materials_items],
    })
    st.markdown(f"**Materials total:** {fmt_money(c.materials_total)}")

    # 7. Labor
    st.header("7. Cost Estimate — Labor")
    st.table({
        "Phase / Trade": [li.name for li in c.labor_phases],
        "Estimated duration": [li.note for li in c.labor_phases],
        "Subtotal": [fmt_money(li.subtotal) for li in c.labor_phases],
    })
    st.markdown(f"**Labor total:** {fmt_money(c.labor_total)}")
    st.caption(f"Regional adjustment: {c.region_note}")

    # 8. Summary
    st.header("8. Project Cost Summary")

    def band(total, share, lo=0.85, hi=1.20):
        base = total * share
        return base * lo, base, base * hi

    m_lo, m_mid, m_hi = band(c.grand_total_mid, c.materials_total / c.grand_total_mid)
    l_lo, l_mid, l_hi = band(c.grand_total_mid, c.labor_total / c.grand_total_mid)
    p_lo, p_mid, p_hi = band(c.grand_total_mid, c.permits_total / c.grand_total_mid)
    co_lo, co_mid, co_hi = band(c.grand_total_mid, c.contingency_total / c.grand_total_mid)

    st.table({
        "Category": ["Materials", "Labor", "Permits & Fees", "Contingency (10–15%)",
                     "Total Project Cost"],
        "Low": [fmt_money(m_lo), fmt_money(l_lo), fmt_money(p_lo), fmt_money(co_lo),
                fmt_money(c.grand_total_low)],
        "Mid": [fmt_money(m_mid), fmt_money(l_mid), fmt_money(p_mid), fmt_money(co_mid),
                fmt_money(c.grand_total_mid)],
        "High": [fmt_money(m_hi), fmt_money(l_hi), fmt_money(p_hi), fmt_money(co_hi),
                 fmt_money(c.grand_total_high)],
    })

    with st.expander("Cost model notes & assumptions"):
        for n in c.notes:
            st.write("•", n)

    # 9. Record real bids — replace model numbers with actual quotes here.
    st.header("9. Record Real Bids")
    st.markdown(
        "The tables above are **planning estimates**. As real quotes come in, "
        "enter them here to compare against the model band "
        f"({fmt_money(c.grand_total_low)} – {fmt_money(c.grand_total_high)})."
    )
    for slot in (1, 2, 3):
        col_n, col_b = st.columns([2, 1])
        with col_n:
            st.text_input(f"Contractor {slot}", key=f"bid_name_{slot}",
                          placeholder="Company name")
        with col_b:
            st.number_input(f"Bid {slot} ($)", key=f"bid_amt_{slot}",
                            min_value=0, step=500)
    bids = [(st.session_state.get(f"bid_name_{s}") or f"Contractor {s}",
             st.session_state.get(f"bid_amt_{s}") or 0) for s in (1, 2, 3)]
    bids = [(n, a) for n, a in bids if a > 0]
    if bids:
        for n, a in sorted(bids, key=lambda b: b[1]):
            if a < c.grand_total_low:
                note = "below the model's low band — check what's excluded"
            elif a > c.grand_total_high:
                note = "above the model's high band"
            else:
                note = "within the model band"
            st.write(f"**{n}** — {fmt_money(a)} · {note}")
        st.caption("Rule of thumb: get 3 written, itemized bids; suspiciously low "
                   "bids usually grow via change orders.")

    # 10. Budget
    st.header("10. Budget Compatibility")
    st.subheader(rep.budget_verdict)
    st.write(rep.budget_note)

    # 11. Next steps
    st.header("11. Recommended Next Steps")
    for i, step in enumerate(rep.next_steps, 1):
        st.write(f"{i}. {step}")

    with st.expander("Diagnostics / data sources"):
        for d in rep.diagnostics:
            st.write("•", d)
        st.caption("Data sources: US Census Geocoder · OpenStreetMap (ODbL) · "
                   "Esri World Imagery · internal cost baselines (2024–2025).")


# ------- router -------
if st.session_state.stage == "address":
    render_landing()
elif st.session_state.stage == "project":
    render_project_picker()
elif st.session_state.stage == "results":
    render_results()
else:
    reset_all()
    st.rerun()
