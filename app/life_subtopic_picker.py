# app/life_subtopic_picker.py
from __future__ import annotations

import random
from typing import Dict, List, Tuple


DEFAULT_LIFE_SUBTOPICS: List[str] = [
    "수납·정리",
    "청소·세정",
    "욕실·위생",
    "주방·조리",
    "세탁·의류관리",
    "생활가전·소형가전",
    "홈오피스·데스크",
    "반려동물",
    "육아·키즈",
    "차량·카케어",
]


def _ensure_life_stats(state: Dict) -> Dict:
    state = dict(state or {})
    if "life_subtopic_stats" not in state or not isinstance(state["life_subtopic_stats"], dict):
        state["life_subtopic_stats"] = {}
    return state


def pick_life_subtopic(
    state: Dict,
    *,
    candidates: List[str] | None = None,
    epsilon: float = 0.18,
) -> Tuple[str, Dict]:
    """
    생활 하위주제 선택 (epsilon-greedy)
    - epsilon 확률로 랜덤 탐색
    - 나머지는 score 높은 것(CTR 기반 + smoothing) 선택
    """
    state = _ensure_life_stats(state)
    stats = state["life_subtopic_stats"]
    cands = candidates or DEFAULT_LIFE_SUBTOPICS

    dbg = {"epsilon": epsilon, "mode": "", "scored": []}

    # 탐색
    if random.random() < epsilon:
        dbg["mode"] = "explore"
        return random.choice(cands), dbg

    # 활용(성과 기반)
    best = None
    best_score = -1.0

    for s in cands:
        row = stats.get(s, {})
        imp = int(row.get("impressions", 0))
        clk = int(row.get("clicks", 0))
        # CTR smoothing (imp가 적을수록 과대평가 방지)
        ctr = (clk + 1) / (imp + 50)
        # 약간의 가중: imp가 쌓인 항목을 조금 우대
        score = ctr * (1.0 + min(0.6, imp / 3000.0))
        dbg["scored"].append((s, round(score, 6), imp, clk))

        if score > best_score:
            best_score = score
            best = s

    dbg["mode"] = "exploit"
    dbg["scored"].sort(key=lambda x: x[1], reverse=True)

    return best or random.choice(cands), dbg
