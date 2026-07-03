"""Fetch & stitch Esri World Imagery tiles (free, no key) around a lat/lon."""
from __future__ import annotations

import io
import math
from dataclasses import dataclass

import requests
from PIL import Image, ImageDraw

TILE_URL = ("https://services.arcgisonline.com/ArcGIS/rest/services/"
            "World_Imagery/MapServer/tile/{z}/{y}/{x}")
TILE_SIZE = 256


@dataclass
class StitchedTile:
    image: Image.Image
    zoom: int
    center_lat: float
    center_lon: float
    # Tile-space top-left (used to convert lat/lon -> pixel in this image)
    origin_tile_x: float
    origin_tile_y: float
    pixel_width: int
    pixel_height: int

    def latlon_to_pixel(self, lat: float, lon: float) -> tuple[float, float]:
        tx, ty = deg2tile(lat, lon, self.zoom)
        px = (tx - self.origin_tile_x) * TILE_SIZE
        py = (ty - self.origin_tile_y) * TILE_SIZE
        return px, py


def deg2tile(lat: float, lon: float, z: int) -> tuple[float, float]:
    lat_rad = math.radians(lat)
    n = 2.0 ** z
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n
    return x, y


def fetch_tiles(lat: float, lon: float, zoom: int = 19,
                grid: int = 4, timeout: int = 15) -> StitchedTile:
    """Fetch a `grid` x `grid` mosaic of Esri imagery tiles centered on lat/lon."""
    cx, cy = deg2tile(lat, lon, zoom)
    half = grid / 2.0
    min_tx = int(math.floor(cx - half))
    min_ty = int(math.floor(cy - half))
    max_tx = min_tx + grid - 1
    max_ty = min_ty + grid - 1

    mosaic = Image.new("RGB", (TILE_SIZE * grid, TILE_SIZE * grid))
    headers = {"User-Agent": "vibe-property-analyzer/0.1"}

    for ix, tx in enumerate(range(min_tx, max_tx + 1)):
        for iy, ty in enumerate(range(min_ty, max_ty + 1)):
            url = TILE_URL.format(z=zoom, x=tx, y=ty)
            r = requests.get(url, timeout=timeout, headers=headers)
            r.raise_for_status()
            tile = Image.open(io.BytesIO(r.content)).convert("RGB")
            mosaic.paste(tile, (ix * TILE_SIZE, iy * TILE_SIZE))

    return StitchedTile(
        image=mosaic,
        zoom=zoom,
        center_lat=lat,
        center_lon=lon,
        origin_tile_x=float(min_tx),
        origin_tile_y=float(min_ty),
        pixel_width=mosaic.width,
        pixel_height=mosaic.height,
    )


def meters_per_pixel(lat: float, zoom: int) -> float:
    """Web Mercator ground resolution at given lat/zoom."""
    return 156543.03392 * math.cos(math.radians(lat)) / (2 ** zoom)


IDENTIFY_URL = ("https://services.arcgisonline.com/ArcGIS/rest/services/"
                "World_Imagery/MapServer/identify")


def fetch_capture_info(lat: float, lon: float, timeout: int = 15) -> dict:
    """Query Esri World Imagery metadata for the capture date of the imagery
    at this location. Returns {"date": "YYYY-MM-DD", "resolution_m": float,
    "source": str} — empty dict when metadata is unavailable.
    """
    params = {
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "sr": "4326",
        "layers": "all",
        "tolerance": "1",
        "mapExtent": f"{lon - 0.005},{lat - 0.005},{lon + 0.005},{lat + 0.005}",
        "imageDisplay": "400,400,96",
        "returnGeometry": "false",
        "f": "json",
    }
    try:
        r = requests.get(IDENTIFY_URL, params=params, timeout=timeout,
                         headers={"User-Agent": "vibe-property-analyzer/0.1"})
        r.raise_for_status()
        results = r.json().get("results", [])
    except Exception:
        return {}
    # The high-res layers carry the DATE attribute; take the first that has one.
    for res in results:
        attrs = res.get("attributes", {})
        raw = attrs.get("DATE (YYYYMMDD)") or ""
        if raw and raw.lower() != "null" and len(raw) == 8 and raw.isdigit():
            info = {"date": f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"}
            try:
                info["resolution_m"] = float(attrs.get("RESOLUTION (M)", 0))
            except (TypeError, ValueError):
                pass
            src = attrs.get("SOURCE") or attrs.get("SOURCE_INFO") or ""
            if src and src.lower() != "null":
                info["source"] = src
            return info
    return {}


RED = (255, 30, 30)


def draw_property_outline(tile: StitchedTile, polygon,
                          color: tuple[int, int, int] = RED,
                          width: int = 5,
                          halo: bool = True) -> None:
    """Draw a closed polygon outline on the tile image (in-place).

    `polygon` is a shapely Polygon with (lon, lat) coords (the format produced
    by src.osm). A black halo is drawn under the colored line for contrast
    against bright/dark imagery.
    """
    coords = list(polygon.exterior.coords)
    pixel_coords = [tile.latlon_to_pixel(lat, lon) for lon, lat in coords]
    if pixel_coords[0] != pixel_coords[-1]:
        pixel_coords.append(pixel_coords[0])

    draw = ImageDraw.Draw(tile.image)
    if halo:
        draw.line(pixel_coords, fill=(0, 0, 0), width=width + 4)
    draw.line(pixel_coords, fill=color, width=width)


def draw_marker(tile: StitchedTile, lat: float, lon: float,
                color: tuple[int, int, int] = RED,
                size: int = 28, width: int = 4) -> None:
    """Draw a target marker (circle + crosshair) at lat/lon — fallback when no
    polygon is available. Black halo applied for contrast.
    """
    px, py = tile.latlon_to_pixel(lat, lon)
    draw = ImageDraw.Draw(tile.image)

    def stroke(geom_fn, w):
        geom_fn((0, 0, 0), w + 3)
        geom_fn(color, w)

    def ellipse(c, w):
        draw.ellipse((px - size, py - size, px + size, py + size), outline=c, width=w)

    def crosshair(c, w):
        draw.line([(px - size * 1.7, py), (px - size * 0.7, py)], fill=c, width=w)
        draw.line([(px + size * 0.7, py), (px + size * 1.7, py)], fill=c, width=w)
        draw.line([(px, py - size * 1.7), (px, py - size * 0.7)], fill=c, width=w)
        draw.line([(px, py + size * 0.7), (px, py + size * 1.7)], fill=c, width=w)

    stroke(ellipse, width)
    stroke(crosshair, width)
