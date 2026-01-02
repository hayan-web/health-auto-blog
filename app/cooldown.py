# app/cooldown.py
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Tuple


@dataclass
class CooldownRule:
    # 최소 노출(이 이상 모였을 때만 평가)
    min_impressions: int = 120
    # 클릭률 하한(이보다 낮으면 쿨다운)
    ctr_floor: float = 0.0025  # 0.25%
    # 쿨다운 기간(일)
    cooldown_days: int = 3
    # 같은 조합이 반복으로 걸릴 때 가중(일수 추가)
    extra_days_per_strike: int = 1


def _now() -> int:
    return int(time.time())


def _ctr(impressions: int, clicks: int) -> float:
    impressions = max(0, int(impressions))
    clicks = max(0, int(clicks))
    if impressions <= 0:
        return 0.0
    return clicks / impressions


def _ensure_dict(d: Dict[str, Any], *keys: str) -> Dict[str, Any]:
    cur = d
    for k in keys:
        if k not in cur or not isinstance(cur[k], dict):
            cur[k] = {}
        cur = cur[k]
    return cur


def _get_stats_for_image(state: Dict[str, Any], img: str) -> Tuple[int, int]:
    node = state.get("image_stats", {}).get(img, {})
    if not isinstance(node, dict):
        return 0, 0
    return int(node.get("impressions", 0)), int(node.get("clicks", 0))


def _get_stats_for_thumb(state: Dict[str, Any], tv: str) -> Tuple[int, int]:
    node = state.get("thumb_title_stats", {}).get(tv, {})
    if not isinstance(node, dict):
        return 0, 0
    return int(node.get("impressions", 0)), int(node.get("clicks", 0))


def _get_stats_for_topic_style(state: Dict[str, Any], topic: str, img: str) -> Tuple[int, int]:
    node = state.get("topic_style_stats", {}).get(topic, {}).get(img, {})
    if not isinstance(node, dict):
        return 0, 0
    return int(node.get("impressions", 0)), int(node.get("clicks", 0))


def _get_stats_for_topic_thumb(state: Dict[str, Any], topic: str, tv: str) -> Tuple[int, int]:
    node = state.get("topic_thumb_title_stats", {}).get(topic, {}).get(tv, {})
    if not isinstance(node, dict):
        return 0, 0
    return int(node.get("impressions", 0)), int(node.get("clicks", 0))


def is_blocked(state: Dict[str, Any], key: str) -> bool:
    """
    key 예:
    - "img:watercolor"
    - "tv:benefit_short"
    - "ts:health:watercolor"
    - "tt:health:benefit_short"
    """
    cd = state.get("cooldown", {})
    if not isinstance(cd, dict):
        return False
    until = cd.get(key)
    if not until:
        return False
    try:
        return int(until) > _now()
    except Exception:
        return False


def _set_block(state: Dict[str, Any], key: str, days: int) -> Dict[str, Any]:
    cd = _ensure_dict(state, "cooldown")
    strikes = _ensure_dict(state, "cooldown_strikes")

    prev = int(strikes.get(key, 0)) if isinstance(strikes.get(key, 0), int) else 0
    strikes[key] = prev + 1

    # 반복으로 걸리면 기간 늘리기
    extra = int(state.get("cooldown_rule_extra_per_strike", 0))
    if extra <= 0:
        extra = 0

    until = _now() + int((days + extra * (prev)) * 86400)
    cd[key] = until
    return state


def apply_cooldown_rules(state: Dict[str, Any], topic: str, img: str, tv: str, rule: CooldownRule) -> Dict[str, Any]:
    """
    발행 후(노출/클릭 업데이트 이후) 호출:
    - 성과 낮은 조합이면 state에 쿨다운 등록
    """
    topic = topic or "general"

    # 전역 이미지
    imp, clk = _get_stats_for_image(state, img)
    if imp >= rule.min_impressions and _ctr(imp, clk) < rule.ctr_floor:
        state = _set_block(state, f"img:{img}", rule.cooldown_days)

    # 전역 썸네일 variant
    imp, clk = _get_stats_for_thumb(state, tv)
    if imp >= rule.min_impressions and _ctr(imp, clk) < rule.ctr_floor:
        state = _set_block(state, f"tv:{tv}", rule.cooldown_days)

    # topic×style
    imp, clk = _get_stats_for_topic_style(state, topic, img)
    if imp >= rule.min_impressions and _ctr(imp, clk) < rule.ctr_floor:
        state = _set_block(state, f"ts:{topic}:{img}", rule.cooldown_days)

    # topic×thumb
    imp, clk = _get_stats_for_topic_thumb(state, topic, tv)
    if imp >= rule.min_impressions and _ctr(imp, clk) < rule.ctr_floor:
        state = _set_block(state, f"tt:{topic}:{tv}", rule.cooldown_days)

    return state


def choose_with_cooldown_filter(
    state: Dict[str, Any],
    topic: str,
    img: str,
    tv: str,
) -> Tuple[str, str, Dict[str, Any]]:
    """
    11번에서 고른 (img,tv)가 쿨다운이면 자동으로 대체 선택하도록 하기 위한 헬퍼.
    여기서는 "차단 여부만" 판단해 debug 제공.
    """
    topic = topic or "general"

    blocked_reasons = []
    if is_blocked(state, f"img:{img}"):
        blocked_reasons.append(f"img:{img}")
    if is_blocked(state, f"tv:{tv}"):
        blocked_reasons.append(f"tv:{tv}")
    if is_blocked(state, f"ts:{topic}:{img}"):
        blocked_reasons.append(f"ts:{topic}:{img}")
    if is_blocked(state, f"tt:{topic}:{tv}"):
        blocked_reasons.append(f"tt:{topic}:{tv}")

    return img, tv, {"blocked": bool(blocked_reasons), "reasons": blocked_reasons}
