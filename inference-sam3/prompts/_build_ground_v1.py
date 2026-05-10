"""Build prompts/ground_v1.json from public ground / oblique imagery benchmarks.

Run: `python prompts/_build_ground_v1.py` to regenerate the JSON.

Sources:
  * COCO 2017            (https://cocodataset.org/) — 80 thing categories
  * Objects365 v2 subset (https://www.objects365.org/) — 365 classes; we ship a
    curated extension over COCO that covers the most common indoor/outdoor
    objects so FMV stays useful without the full LVIS 1203-class load.
  * LVIS v1 hand-picked extras (https://www.lvisdataset.org/) — useful long-tail
    nouns that SAM3 was trained against (the SA-Co dataset includes LVIS).

Output file: ground_v1.json — deduped, lowercased open-vocabulary union.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


# COCO 2017 80 thing classes (canonical order).
COCO80 = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train",
    "truck", "boat", "traffic light", "fire hydrant", "stop sign",
    "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep", "cow",
    "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella", "handbag",
    "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite",
    "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket",
    "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana",
    "apple", "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza",
    "donut", "cake", "chair", "couch", "potted plant", "bed", "dining table",
    "toilet", "tv", "laptop", "mouse", "remote", "keyboard", "cell phone",
    "microwave", "oven", "toaster", "sink", "refrigerator", "book", "clock",
    "vase", "scissors", "teddy bear", "hair drier", "toothbrush",
]

# A curated extension drawn from the Objects365 v2 / LVIS taxonomies that
# (a) is missing from COCO and (b) is plausibly useful for FMV / ground imagery.
# Categories are grouped by theme for readability; the build dedupes them.
OBJECTS365_LVIS_EXT = [
    # People / bodies
    "child", "baby", "soldier", "police officer", "construction worker",
    "pedestrian", "cyclist", "rider", "athlete", "runner",
    # Outdoor structures
    "fence", "gate", "wall", "tower", "pole", "telephone pole",
    "street light", "lamp post", "manhole", "barrier", "guard rail",
    "highway", "road", "sidewalk", "crosswalk", "bridge", "tunnel",
    "stairs", "ladder", "balcony", "rooftop", "chimney",
    # Vehicles (extended)
    "van", "pickup truck", "tractor", "trailer", "semi-trailer", "tank truck",
    "fire engine", "ambulance", "police car", "school bus", "minibus",
    "limousine", "taxi", "convertible", "sports car", "suv", "armored vehicle",
    "tank", "humvee", "bulldozer", "excavator", "forklift", "crane",
    "tractor unit", "tow truck", "garbage truck", "snowplow",
    "rickshaw", "tuktuk", "scooter", "moped", "atv",
    "helicopter", "drone", "ultralight aircraft", "biplane", "jet",
    "yacht", "kayak", "canoe", "rowboat", "lifeboat",
    "train car", "subway", "tram", "monorail",
    # Buildings & rooms
    "house", "skyscraper", "warehouse", "factory", "barn", "shed", "cabin",
    "tent", "hut", "garage", "shop", "office", "school", "church",
    "mosque", "temple", "stadium", "arena", "gym",
    "bedroom", "kitchen", "bathroom", "living room", "office room",
    # Furniture & home
    "armchair", "sofa", "stool", "desk", "shelf", "bookshelf", "wardrobe",
    "cabinet", "dresser", "nightstand", "ottoman", "bench seat",
    "rug", "carpet", "curtain", "blind", "mirror", "picture frame",
    "lamp", "ceiling fan", "fan",
    "pillow", "blanket", "mattress", "towel",
    # Appliances & electronics
    "monitor", "computer", "desktop computer", "tablet", "smart speaker",
    "router", "speaker", "headphones", "camera", "tripod", "projector",
    "microphone", "video game console", "controller",
    "washing machine", "dryer", "dishwasher", "vacuum cleaner", "iron",
    "kettle", "blender", "coffee maker", "toaster oven", "rice cooker",
    "food processor", "electric heater", "air conditioner",
    # Kitchen
    "plate", "saucer", "mug", "tea cup", "wine bottle", "beer bottle",
    "soda can", "milk carton", "salt shaker", "pepper shaker",
    "ladle", "spatula", "rolling pin", "cutting board", "frying pan",
    "saucepan", "pot", "wok", "chopsticks", "tongs", "whisk",
    # Food extended
    "bread", "bagel", "croissant", "muffin", "cookie", "ice cream",
    "candy", "chocolate", "cheese", "egg", "pasta", "rice", "noodles",
    "soup", "salad", "steak", "chicken", "fish", "shrimp", "sushi",
    "burger", "taco", "burrito", "fries", "popcorn",
    "lemon", "grape", "strawberry", "watermelon", "pineapple", "mango",
    "peach", "pear", "kiwi", "cherry", "tomato", "potato", "onion",
    "garlic", "pepper", "cucumber", "lettuce", "spinach", "mushroom",
    "corn", "pumpkin", "squash",
    # Clothing & accessories
    "hat", "cap", "helmet", "scarf", "glove", "sock", "shoe", "boot",
    "sandal", "sneaker", "high heels", "belt", "watch", "necklace",
    "earring", "ring", "bracelet", "sunglasses", "goggles",
    "shirt", "t-shirt", "blouse", "jacket", "coat", "vest", "hoodie",
    "sweater", "dress", "skirt", "pants", "jeans", "shorts", "uniform",
    "apron", "swimsuit", "bikini",
    # Sports & gear
    "soccer ball", "basketball", "football", "volleyball", "rugby ball",
    "golf ball", "golf club", "hockey stick", "puck", "racquet",
    "boxing glove", "punching bag", "weights", "dumbbell", "barbell",
    "treadmill", "exercise bike", "yoga mat",
    "skateboard ramp", "trampoline",
    # Animals (extended)
    "wolf", "fox", "deer", "rabbit", "squirrel", "raccoon",
    "lion", "tiger", "leopard", "cheetah", "panda", "monkey", "ape",
    "kangaroo", "koala", "camel", "donkey", "mule", "goat", "pig", "boar",
    "duck", "goose", "swan", "chicken", "rooster", "turkey", "owl",
    "eagle", "hawk", "parrot", "penguin", "pigeon", "seagull",
    "fish", "shark", "whale", "dolphin", "octopus", "crab", "lobster",
    "snake", "lizard", "turtle", "frog", "alligator", "crocodile",
    "spider", "ant", "bee", "butterfly", "dragonfly", "ladybug",
    # Plants & nature
    "tree", "palm tree", "pine tree", "bush", "flower", "rose", "tulip",
    "sunflower", "leaf", "branch", "log", "mushroom", "moss", "grass",
    "rock", "stone", "boulder", "mountain", "hill", "cliff", "cave",
    "river", "stream", "waterfall", "lake", "pond", "ocean", "wave",
    "beach", "sand", "snow", "ice", "fog", "smoke", "fire", "lightning",
    # Tools & hardware
    "hammer", "screwdriver", "wrench", "pliers", "saw", "drill",
    "chainsaw", "axe", "shovel", "rake", "wheelbarrow", "ladder",
    "rope", "chain", "bucket", "broom", "mop",
    "tape measure", "level", "flashlight", "lantern", "battery",
    # Office & paper goods
    "pen", "pencil", "marker", "ruler", "eraser", "stapler", "tape dispenser",
    "scissors", "envelope", "folder", "notebook", "binder", "calendar",
    "file cabinet", "whiteboard", "blackboard", "easel",
    # Bags & containers
    "duffel bag", "backpack", "messenger bag", "shopping bag",
    "garbage bag", "trash can", "dumpster", "barrel", "drum",
    "crate", "pallet", "shipping container", "suitcase", "briefcase",
    # Misc personal
    "cigarette", "lighter", "ashtray", "candle", "incense",
    "mask", "gas mask", "stethoscope", "syringe", "first aid kit",
    "wheelchair", "walker", "crutch", "stroller", "cradle",
    # Signs & maps
    "billboard", "banner", "flag", "sign", "neon sign", "menu",
    "license plate", "logo", "map", "compass",
    # Toys & musical
    "toy car", "doll", "lego", "puzzle", "board game", "playing card",
    "dice", "chess piece", "kite",
    "guitar", "piano", "drum", "violin", "trumpet", "saxophone",
    "flute", "harmonica", "accordion", "harp", "tambourine",
    # Aerospace / mil ground (kept generic — open vocab)
    "satellite dish", "antenna", "radar dish", "missile launcher",
    "artillery piece", "rocket", "spacecraft", "missile", "drone aircraft",
    # Industrial
    "pipeline", "conveyor belt", "smokestack", "cooling tower",
    "wind turbine", "solar panel", "transformer", "transmission tower",
    "oil rig", "oil pump", "valve", "compressor", "generator",
    "boiler", "tank container", "silo", "grain elevator",
    # Construction equipment
    "cement truck", "concrete mixer", "scaffolding", "construction barrier",
    "construction cone", "traffic cone", "porta potty", "dumpster",
    # Maritime / port
    "container crane", "gantry crane", "buoy", "lighthouse", "pier",
    "dock", "harbor wall",
]


def _normalize(label: str) -> str:
    label = label.replace("/", " or ").replace("&", "and").replace("_", " ")
    label = re.sub(r"\s+", " ", label).strip().lower()
    return label


def _build() -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for source in (COCO80, OBJECTS365_LVIS_EXT):
        for raw in source:
            normalized = _normalize(raw)
            if normalized and normalized not in seen:
                seen.add(normalized)
                out.append(normalized)
    return out


def main() -> None:
    prompts = _build()
    payload = {
        "name": "ground_v1",
        "description": (
            "Open-vocabulary ground / oblique prompt union — COCO 2017 80-class set "
            "plus a curated extension drawn from the Objects365 v2 and LVIS v1 "
            "taxonomies. SAM3 has no inherent prompt-count limit; latency scales "
            "linearly with prompt count (batched in chunks of SAM3_BATCHED_TEXT_CHUNK_SIZE)."
        ),
        "sources": ["coco-2017", "objects365-v2-curated", "lvis-v1-curated"],
        "count": len(prompts),
        "prompts": prompts,
    }
    out_path = Path(__file__).with_name("ground_v1.json")
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out_path} with {len(prompts)} prompts")


if __name__ == "__main__":
    main()
