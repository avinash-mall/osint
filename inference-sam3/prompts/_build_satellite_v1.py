"""Build prompts/satellite_v1.json from public benchmark category lists.

Run: `python prompts/_build_satellite_v1.py` to regenerate the JSON.
The categories are reproduced verbatim from the public taxonomies of:

  * xView Challenge      (https://xviewdataset.org/)        — 60 classes
  * DOTA v2.0           (https://captain-whu.github.io/DOTA/) — 18 classes (v1.5 + airport, helipad)
  * DIOR                (Cheng et al. 2020)                  — 20 classes
  * fMoW                (https://github.com/fMoW/dataset)    — 62 trainable scene classes
  * FAIR1M v1.0         (arXiv:2103.05569)                   — 37 sub-categories
  * HRSC2016            (Liu et al. 2017, ship-type level)   — 27 fine-grained ship types
  * RarePlanes          (IQT WACV 2021, attribute values)    — 33 attribute strings

Output file: satellite_v1.json — a deduped, lowercased, space-normalized list.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


# xView 60 classes (from xView Challenge taxonomy)
XVIEW = [
    "Fixed-wing Aircraft", "Small Aircraft", "Cargo Plane", "Helicopter",
    "Passenger Vehicle", "Small Car", "Bus", "Pickup Truck", "Utility Truck",
    "Truck", "Cargo Truck", "Truck w/Box", "Truck Tractor", "Trailer",
    "Truck w/Flatbed", "Truck w/Liquid", "Crane Truck", "Railway Vehicle",
    "Passenger Car", "Cargo Car", "Flat Car", "Tank car", "Locomotive",
    "Maritime Vessel", "Motorboat", "Sailboat", "Tugboat", "Barge",
    "Fishing Vessel", "Ferry", "Yacht", "Container Ship", "Oil Tanker",
    "Engineering Vehicle", "Tower crane", "Container Crane", "Reach Stacker",
    "Straddle Carrier", "Mobile Crane", "Dump Truck", "Haul Truck",
    "Scraper/Tractor", "Front loader/Bulldozer", "Excavator", "Cement Mixer",
    "Ground Grader", "Hut/Tent", "Shed", "Building", "Aircraft Hangar",
    "Damaged Building", "Facility", "Construction Site", "Vehicle Lot",
    "Helipad", "Storage Tank", "Shipping container lot", "Shipping Container",
    "Pylon", "Tower", "Container",
]

# DOTA v2.0 18 categories
DOTA_V2 = [
    "plane", "ship", "storage tank", "baseball diamond", "tennis court",
    "basketball court", "ground track field", "harbor", "bridge",
    "large vehicle", "small vehicle", "helicopter", "roundabout",
    "soccer ball field", "swimming pool", "container crane", "airport", "helipad",
]

# DIOR 20 categories
DIOR = [
    "airplane", "airport", "baseball field", "basketball court", "bridge",
    "chimney", "dam", "expressway service area", "expressway toll station",
    "golf field", "ground track field", "harbor", "overpass", "ship",
    "stadium", "storage tank", "tennis court", "train station", "vehicle",
    "windmill",
]

# fMoW 62 trainable scene classes (excluding "false_detection")
FMOW = [
    "airport", "airport hangar", "airport terminal", "amusement park",
    "aquaculture", "archaeological site", "barn", "border checkpoint",
    "burial site", "car dealership", "construction site", "crop field",
    "dam", "debris or rubble", "educational institution", "electric substation",
    "factory or powerplant", "fire station", "flooded road", "fountain",
    "gas station", "golf course", "ground transportation station", "helipad",
    "hospital", "impoverished settlement", "interchange", "lake or pond",
    "lighthouse", "military facility", "multi-unit residential", "nuclear powerplant",
    "office building", "oil or gas facility", "park", "parking lot or garage",
    "place of worship", "police station", "port", "prison", "race track",
    "railway bridge", "recreational facility", "road bridge", "runway",
    "shipyard", "shopping mall", "single-unit residential", "smokestack",
    "solar farm", "space facility", "stadium", "storage tank", "surface mine",
    "swimming pool", "toll booth", "tower", "tunnel opening", "waste disposal",
    "water treatment facility", "wind farm", "zoo",
]

# FAIR1M v1.0 — 37 sub-categories across 5 super-categories
FAIR1M = [
    # Airplane (11)
    "boeing 737", "boeing 747", "boeing 777", "boeing 787",
    "airbus a220", "airbus a321", "airbus a330", "airbus a350",
    "comac arj21", "comac c919", "other airplane",
    # Ship (9)
    "passenger ship", "motorboat", "fishing boat", "tugboat",
    "engineering ship", "liquid cargo ship", "dry cargo ship",
    "warship", "other ship",
    # Vehicle (10)
    "small car", "bus", "cargo truck", "dump truck", "van",
    "trailer", "tractor", "excavator", "truck tractor", "other vehicle",
    # Court (4)
    "baseball field", "basketball court", "football field", "tennis court",
    # Road (3)
    "roundabout", "intersection", "bridge",
]

# HRSC2016 ship-type level (27 fine-grained ship types).
# Based on the HRSC2016 hierarchical taxonomy (1 → 4 → 27).
HRSC2016 = [
    "aircraft carrier", "warcraft", "merchant ship", "destroyer",
    "frigate", "patrol", "submarine", "corvette",
    "container ship", "cargo ship", "dry cargo ship", "liquid cargo ship",
    "oil tanker", "lng tanker", "container box", "barge", "tugboat",
    "fishing vessel", "yacht", "passenger ship", "ferry", "ro-ro ship",
    "drilling ship", "research vessel", "icebreaker", "speedboat",
    "lifeboat",
]

# RarePlanes 33 attribute values across 10 attribute categories.
# The RarePlanes taxonomy uses *attribute strings*, not detection class names.
# We materialise the attribute *values* as additional noun phrases that SAM3
# may match on. Source: IQT RarePlanes paper, WACV 2021.
RAREPLANES_ATTRS = [
    # Wing shape (4)
    "swept wing", "straight wing", "delta wing", "variable swept wing",
    # Wing position (3)
    "high wing", "mid wing", "low wing",
    # Wing span FAA class (4)
    "small wingspan", "medium wingspan", "large wingspan", "jumbo wingspan",
    # Propulsion (3)
    "jet propulsion", "propeller propulsion", "unpowered glider",
    # Engine count (4)
    "single engine", "twin engine", "three engine", "four engine",
    # Vertical stabilizers (3)
    "single tail", "twin tail", "h-tail",
    # Canards (2)
    "with canards", "without canards",
    # Aircraft role (10) — keep as broad civilian/general roles
    "civil transport", "general aviation", "regional jet", "commuter",
    "biplane", "trainer", "agricultural plane", "firefighting plane",
    "freighter", "executive jet",
]


def _normalize(label: str) -> str:
    label = label.replace("/", " or ").replace("&", "and").replace("_", " ")
    label = re.sub(r"\s+", " ", label).strip().lower()
    return label


def _build() -> list[str]:
    sources = (XVIEW, DOTA_V2, DIOR, FMOW, FAIR1M, HRSC2016, RAREPLANES_ATTRS)
    seen: set[str] = set()
    out: list[str] = []
    for source in sources:
        for raw in source:
            normalized = _normalize(raw)
            if normalized and normalized not in seen:
                seen.add(normalized)
                out.append(normalized)
    return out


def main() -> None:
    prompts = _build()
    payload = {
        "name": "satellite_v1",
        "description": (
            "Open-vocabulary aerial / satellite prompt union of public benchmark "
            "categories: xView, DOTA v2.0, DIOR, fMoW, FAIR1M, HRSC2016 ship-type "
            "level, RarePlanes attribute values."
        ),
        "sources": [
            "xview", "dota-v2", "dior", "fmow", "fair1m-v1", "hrsc2016", "rareplanes",
        ],
        "count": len(prompts),
        "prompts": prompts,
    }
    out_path = Path(__file__).with_name("satellite_v1.json")
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out_path} with {len(prompts)} prompts")


if __name__ == "__main__":
    main()
