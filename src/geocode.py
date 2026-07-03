"""Address -> (lat, lon, normalized address, state) via US Census Geocoder. Free, no key."""
from __future__ import annotations

import re
import requests
from dataclasses import dataclass

CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"

# Approximate bounding box of the contiguous 48 states.
LOWER_48_BBOX = {"min_lat": 24.396308, "max_lat": 49.384358,
                 "min_lon": -125.0, "max_lon": -66.93457}

EXCLUDED_STATES = {"AK", "HI", "PR", "VI", "GU", "AS", "MP"}


@dataclass
class GeocodeResult:
    lat: float
    lon: float
    matched_address: str
    state: str
    zip_code: str
    housenumber: str = ""   # e.g. "350"
    street: str = ""        # e.g. "5TH AVE"

    def in_lower_48(self) -> bool:
        if self.state in EXCLUDED_STATES:
            return False
        b = LOWER_48_BBOX
        return (b["min_lat"] <= self.lat <= b["max_lat"]
                and b["min_lon"] <= self.lon <= b["max_lon"])


_HOUSENUMBER_RE = re.compile(r"^\s*(\d+[A-Za-z\-]?)\s+(.+?)\s*$")


def _parse_housenumber_street(matched_address: str, comp: dict) -> tuple[str, str]:
    """Extract (housenumber, full street name) from Census output.

    Prefers addressComponents (clean, normalized) and falls back to parsing the
    first segment of matchedAddress.
    """
    pre_dir = (comp.get("preDirection") or "").strip()
    pre_type = (comp.get("preType") or "").strip()
    street_name = (comp.get("streetName") or "").strip()
    suf_type = (comp.get("suffixType") or "").strip()
    suf_dir = (comp.get("suffixDirection") or "").strip()
    street_parts = [p for p in (pre_dir, pre_type, street_name, suf_type, suf_dir) if p]

    # Housenumber: Census exposes fromAddress/toAddress as a range. The user's
    # actual housenumber lives in the first token of matchedAddress.
    housenumber = ""
    first_segment = matched_address.split(",", 1)[0]
    m = _HOUSENUMBER_RE.match(first_segment)
    if m:
        housenumber = m.group(1).strip()
        if not street_parts:
            street_parts = [m.group(2).strip()]

    return housenumber, " ".join(street_parts).strip()


def geocode(address: str, timeout: int = 15) -> GeocodeResult | None:
    params = {
        "address": address,
        "benchmark": "Public_AR_Current",
        "format": "json",
    }
    r = requests.get(CENSUS_URL, params=params, timeout=timeout,
                     headers={"User-Agent": "vibe-property-analyzer/0.1"})
    r.raise_for_status()
    matches = r.json().get("result", {}).get("addressMatches", [])
    if not matches:
        return None
    m = matches[0]
    coords = m["coordinates"]
    comp = m.get("addressComponents", {})
    matched = m.get("matchedAddress", address)
    housenumber, street = _parse_housenumber_street(matched, comp)
    return GeocodeResult(
        lat=float(coords["y"]),
        lon=float(coords["x"]),
        matched_address=matched,
        state=comp.get("state", ""),
        zip_code=comp.get("zip", ""),
        housenumber=housenumber,
        street=street,
    )
