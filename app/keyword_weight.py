from __future__ import annotations

import random
from typing import List, Dict, Any


def weighted_choice(
    keywords: List[str],
    state: Dict[str, Any],
) -> str:
    """
    keyword_stats.score 기반 가중 랜덤 선택
    """
    stats = state.get("keyword_stats", {})
    weights = []

    for k in keywords:
        s = stats.get(k, {})
        score = float(s.get("score", 0.3))  # 기본값
        # 최소 가중치 보장
        weights.append(max(0.1, score))

    return random.choices(keywords, weights=weights, k=1)[0]
