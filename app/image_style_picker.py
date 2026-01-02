from __future__ import annotations

import random
from typing import Dict, Any, List


DEFAULT_STYLES = [
    "clean_flat",
    "photo_real",
    "soft_3d",
    "watercolor",
]


def pick_image_style(state: Dict[str, Any]) -> str:
    stats = state.get("image_stats", {})
    styles: List[str] = DEFAULT_STYLES[:]

    weights = []
    for s in styles:
        score = float(stats.get(s, {}).get("score", 0.3))
        weights.append(max(0.1, score))

    return random.choices(styles, weights=weights, k=1)[0]
