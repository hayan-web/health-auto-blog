from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Dict, Any


KST = timezone(timedelta(hours=9))


def _now_kst_str() -> str:
    return datetime.now(tz=timezone.utc).astimezone(KST).isoformat(timespec="seconds")


def _get_ts(state: Dict[str, Any]) -> Dict[str, Any]:
    # topic_style_stats: { topic: { style_id: {impressions, clicks, score, last_update} } }
    return state.setdefault("topic_style_stats", {})


def record_impression(state: Dict[str, Any], topic: str, style_id: str) -> Dict[str, Any]:
    ts = _get_ts(state)
    t = ts.setdefault(topic or "unknown", {})
    s = t.setdefault(style_id, {})
    s.setdefault("impressions", 0)
    s.setdefault("clicks", 0)
    s.setdefault("score", 0.0)

    s["impressions"] += 1
    s["last_update"] = _now_kst_str()
    return state


def record_click(state: Dict[str, Any], topic: str, style_id: str) -> Dict[str, Any]:
    ts = _get_ts(state)
    t = ts.setdefault(topic or "unknown", {})
    s = t.setdefault(style_id, {})
    s.setdefault("impressions", 0)
    s.setdefault("clicks", 0)
    s.setdefault("score", 0.0)

    s["clicks"] += 1
    s["last_update"] = _now_kst_str()
    return state


def update_score(state: Dict[str, Any], topic: str, style_id: str) -> Dict[str, Any]:
    """
    score = (clicks / impressions) * 1.5  (완만한 스케일)
    impressions는 최소 1로 보정
    """
    ts = _get_ts(state)
    t = ts.get(topic or "unknown")
    if not t:
        return state

    s = t.get(style_id)
    if not s:
        return state

    imp = max(1, int(s.get("impressions", 0)))
    clk = int(s.get("clicks", 0))
    ctr = clk / imp

    s["score"] = round(ctr * 1.5, 4)
    s["last_update"] = _now_kst_str()
    return state
