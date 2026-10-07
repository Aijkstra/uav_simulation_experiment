"""Render a fixed 3.5 km Haidian basemap from a local OpenStreetMap PBF."""
import json
import math
import tempfile
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import osmium
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
PBF = Path(tempfile.gettempdir()) / "uav-beijing.osm.pbf"
OUTPUT = ROOT / "frontend" / "public" / "assets" / "haidian-basemap-3_5km.jpg"
METADATA = ROOT / "frontend" / "public" / "assets" / "haidian-basemap.json"

# Haidian Huangzhuang: WGS84 position converted from the published GCJ-02 point.
CENTER_LAT = 39.974702
CENTER_LON = 116.311445
AREA_METERS = 3500.0
HALF = AREA_METERS / 2
IMAGE_SIZE = 1600
M_PER_DEG_LAT = 111_320.0
M_PER_DEG_LON = M_PER_DEG_LAT * math.cos(math.radians(CENTER_LAT))

Point = Tuple[float, float]
Feature = Tuple[List[Point], Dict[str, str]]


def local_xy(lat: float, lon: float) -> Point:
    return ((lon-CENTER_LON)*M_PER_DEG_LON, (lat-CENTER_LAT)*M_PER_DEG_LAT)


def intersects(points: Sequence[Point]) -> bool:
    return bool(points) and not (
        max(p[0] for p in points) < -HALF or min(p[0] for p in points) > HALF
        or max(p[1] for p in points) < -HALF or min(p[1] for p in points) > HALF
    )


class MapHandler(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.roads: List[Feature] = []
        self.buildings: List[Feature] = []
        self.water: List[Feature] = []
        self.green: List[Feature] = []
        self.rail: List[Feature] = []

    def way(self, way):
        tags = {tag.k: tag.v for tag in way.tags}
        if not any(key in tags for key in ("highway", "building", "waterway", "natural", "landuse", "leisure", "railway")):
            return
        try:
            points = [local_xy(node.lat, node.lon) for node in way.nodes if node.location.valid()]
        except osmium.InvalidLocationError:
            return
        if len(points) < 2 or not intersects(points):
            return
        feature = (points, tags)
        if "highway" in tags:
            self.roads.append(feature)
        elif "building" in tags and len(points) >= 3:
            self.buildings.append(feature)
        elif tags.get("natural") == "water" or tags.get("waterway") or tags.get("landuse") in {"reservoir", "basin"}:
            self.water.append(feature)
        elif tags.get("landuse") in {"grass", "forest", "recreation_ground", "cemetery"} or tags.get("leisure") in {"park", "garden", "pitch"} or tags.get("natural") in {"wood", "grassland"}:
            self.green.append(feature)
        elif "railway" in tags:
            self.rail.append(feature)


def render(handler: MapHandler) -> Image.Image:
    image = Image.new("RGB", (IMAGE_SIZE, IMAGE_SIZE), "#e9e6df")
    draw = ImageDraw.Draw(image)
    px_per_meter = IMAGE_SIZE / AREA_METERS
    def project(point: Point):
        return ((point[0]+HALF)*px_per_meter, IMAGE_SIZE-(point[1]+HALF)*px_per_meter)
    def polygon(features: List[Feature], fill: str, outline: str):
        for points, _ in features:
            pixels = [project(p) for p in points]
            draw.polygon(pixels, fill=fill)
            draw.line(pixels, fill=outline, width=1, joint="curve")

    polygon(handler.green, "#cbdcbd", "#b7ccb0")
    polygon(handler.water, "#bad8e5", "#91bdcf")
    polygon(handler.buildings, "#d5d0c8", "#c1bbb2")

    for points, tags in handler.rail:
        pixels=[project(p) for p in points]
        draw.line(pixels, fill="#88847f", width=3, joint="curve")
        draw.line(pixels, fill="#f0ede7", width=1, joint="curve")

    road_width = {
        "motorway": 13, "trunk": 12, "primary": 10, "secondary": 8,
        "tertiary": 7, "residential": 5, "unclassified": 4, "service": 3,
        "living_street": 3, "pedestrian": 3, "footway": 2, "path": 2,
    }
    road_fill = {"motorway":"#f3b67d", "trunk":"#f3c18c", "primary":"#f4d49d", "secondary":"#f7e3b6"}
    ordered=sorted(handler.roads,key=lambda f:road_width.get(f[1].get("highway",""),2))
    for points,tags in ordered:
        kind=tags.get("highway","");width=road_width.get(kind,2);pixels=[project(p) for p in points]
        if width>=4:draw.line(pixels,fill="#b7b3ad",width=width+2,joint="curve")
        draw.line(pixels,fill=road_fill.get(kind,"#faf8f3"),width=width,joint="curve")

    font_path=Path("C:/Windows/Fonts/msyh.ttc")
    font=ImageFont.truetype(str(font_path),18) if font_path.exists() else ImageFont.load_default()
    used=[]
    seen_names=set()
    for points,tags in reversed(ordered):
        name=tags.get("name:zh") or tags.get("name")
        if not name or name in seen_names or tags.get("highway") not in {"motorway","trunk","primary","secondary","tertiary"}:
            continue
        x,y=project(points[len(points)//2]);box=draw.textbbox((x,y),name,font=font,anchor="mm",stroke_width=2)
        if any(not(box[2]<b[0] or box[0]>b[2] or box[3]<b[1] or box[1]>b[3]) for b in used):
            continue
        draw.text((x,y),name,font=font,fill="#575c5e",anchor="mm",stroke_width=3,stroke_fill="#f8f5ef")
        used.append(box)
        seen_names.add(name)
    return image


def main():
    if not PBF.exists():
        raise SystemExit("Missing beijing-260731.osm.pbf")
    handler=MapHandler()
    handler.apply_file(str(PBF), locations=True, idx="flex_mem")
    image=render(handler)
    OUTPUT.parent.mkdir(parents=True,exist_ok=True)
    image.save(OUTPUT,quality=92,optimize=True,progressive=True)
    METADATA.write_text(json.dumps({
        "centerWgs84":{"lat":CENTER_LAT,"lon":CENTER_LON},
        "areaMeters":{"width":AREA_METERS,"height":AREA_METERS},
        "simulationBounds":{"minX":-1200,"maxX":2300,"minY":-1333,"maxY":2167},
        "source":"beijing-260731.osm.pbf","attribution":"© OpenStreetMap contributors",
        "featureCounts":{"roads":len(handler.roads),"buildings":len(handler.buildings),
                         "water":len(handler.water),"green":len(handler.green),"rail":len(handler.rail)},
    },ensure_ascii=False,indent=2),encoding="utf-8")
    print("Rendered",OUTPUT.name,"features",len(handler.roads),len(handler.buildings),len(handler.water),len(handler.green),len(handler.rail))


if __name__=="__main__":
    main()
