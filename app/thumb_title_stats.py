from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Dict, Any


KST = timezone(timedelta(hours=9))


def _now_kst_str() -> str:
    return datetime.now(tz=timezone.utc).astimezone(KST).isoformat(timespec="seconds")


# -------------------------
# Global title-variant stats
# -------------------------
def _get_global(state: Dict[str, Any]) -> Dict[str, Any]:
    # thumb_title_stats: {variant_id: {impressions, clicks, score, last_update}}
    return state.setdefault("thumb_title_stats", {})


def record_impression(state: Dict[str, Any], variant_id: str) -> Dict[str, Any]:
    g = _get_global(state)
    s = g.setdefault(variant_id, {})
    s.setdefault("impressions", 0)
    s.setdefault("clicks", 0)
    s.setdefault("score", 0.0)

    s["impressions"] += 1
    s["last_update"] = _now_kst_str()
    return state


def record_click(state: Dict[str, Any], variant_id: str) -> Dict[str, Any]:
    g = _get_global(state)
    s = g.setdefault(variant_id, {})
    s.setdefault("impressions", 0)
    s.setdefault("clicks", 0)
    s.setdefault("score", 0.0)

    s["clicks"] += 1
    s["last_update"] = _now_kst_str()
    return state


def update_score(state: Dict[str, Any], variant_id: str) -> Dict[str, Any]:
    g = _get_global(state)
    s = g.get(variant_id)
    if not s:
        return state

    imp = max(1, int(s.get("impressions", 0)))
    clk = int(s.get("clicks", 0))
    ctr = clk / imp
    s["score"] = round(ctr * 1.5, 4)
    s["last_update"] = _now_kst_str()
    return state


# -------------------------
# Topic × title-variant stats
# -------------------------
def _get_topic(state: Dict[str, Any]) -> Dict[str, Any]:
    # topic_thumb_title_stats: {topic: {variant_id: {...}}}
    return state.setdefault("topic_thumb_title_stats", {})


def record_topic_impression(state: Dict[str, Any], topic: str, variant_id: str) -> Dict[str, Any]:
    t = _get_topic(state).setdefault(topic or "unknown", {})
    s = t.setdefault(variant_id, {})
    s.setdefault("impressions", 0)
    s.setdefault("clicks", 0)
    s.setdefault("score", 0.0)

    s["impressions"] += 1
    s["last_update"] = _now_kst_str()
    return state


def record_topic_click(state: Dict[str, Any], topic: str, variant_id: str) -> Dict[str, Any]:
    t = _get_topic(state).setdefault(topic or "unknown", {})
    s = t.setdefault(variant_id, {})
    s.setdefault("impressions", 0)
    s.setdefault("clicks", 0)
    s.setdefault("score", 0.0)

    s["clicks"] += 1
    s["last_update"] = _now_kst_str()
    return state


def update_topic_score(state: Dict[str, Any], topic: str, variant_id: str) -> Dict[str, Any]:
    t = _get_topic(state).get(topic or "unknown")
    if not t:
        return state

    s = t.get(variant_id)
    if not s:
        return state

    imp = max(1, int(s.get("impressions", 0)))
    clk = int(s.get("clicks", 0))
    ctr = clk / imp
    s["score"] = round(ctr * 1.5, 4)
    s["last_update"] = _now_kst_str()
    return state
