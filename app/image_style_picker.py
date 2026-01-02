from __future__ import annotations

import random
from typing import Dict, Any, List, Optional


DEFAULT_STYLES = [
    "clean_flat",
    "photo_real",
    "soft_3d",
    "watercolor",
]


def _get_global_score(state: Dict[str, Any], style_id: str) -> float:
    # image_stats: {style_id: {score, impressions, ...}}
    s = (state.get("image_stats", {}) or {}).get(style_id, {}) or {}
    return float(s.get("score", 0.3))


def _get_topic_score(state: Dict[str, Any], topic: str, style_id: str) -> float:
    # topic_style_stats: {topic: {style_id: {score, ...}}}
    ts = (state.get("topic_style_stats", {}) or {}).get(topic or "unknown", {}) or {}
    s = ts.get(style_id, {}) or {}
    return float(s.get("score", 0.3))


def _get_topic_impressions(state: Dict[str, Any], topic: str, style_id: str) -> int:
    ts = (state.get("topic_style_stats", {}) or {}).get(topic or "unknown", {}) or {}
    s = ts.get(style_id, {}) or {}
    return int(s.get("impressions", 0))


def pick_image_style(
    state: Dict[str, Any],
    *,
    topic: Optional[str] = None,
    explore_rate: float = 0.12,
    topic_weight: float = 0.65,
) -> str:
    """
    8번: topic×style 매칭 학습
    - 글로벌(image_stats) 성과 + 토픽(topic_style_stats) 성과를 합쳐 가중치 계산
    - topic 데이터가 거의 없으면(노출 적음) 글로벌 중심으로 선택

    params:
      explore_rate: 가끔 랜덤 탐색(0~1)
      topic_weight: 토픽 성과 반영 비중(0~1)
    """
    styles: List[str] = DEFAULT_STYLES[:]

    # 탐색(Exploration): 일정 확률로 완전 랜덤 선택
    if random.random() < max(0.0, min(1.0, explore_rate)):
        return random.choice(styles)

    weights = []
    for style in styles:
        g = _get_global_score(state, style)  # 글로벌 점수
        if topic:
            t = _get_topic_score(state, topic, style)
            t_imp = _get_topic_impressions(state, topic, style)

            # 토픽 학습량이 적으면 토픽 비중을 줄임(초기 편향 방지)
            # 0~10회: 천천히 토픽 반영 증가
            confidence = min(1.0, t_imp / 10.0)
            w_topic = topic_weight * confidence

            blended = (1.0 - w_topic) * g + w_topic * t
        else:
            blended = g

        # 최소 가중치 보장
        weights.append(max(0.10, float(blended)))

    return random.choices(styles, weights=weights, k=1)[0]
