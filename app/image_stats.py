from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Dict, Any


KST = timezone(timedelta(hours=9))


def _now_kst_str() -> str:
    return datetime.now(tz=timezone.utc).astimezone(KST).isoformat(timespec="seconds")


def get_image_stats(state: Dict[str, Any]) -> Dict[str, Any]:
    return state.setdefault("image_stats", {})


def record_impression(state: Dict[str, Any], style_id: str) -> Dict[str, Any]:
    stats = get_image_stats(state)
    s = stats.setdefault(style_id, {})
    s.setdefault("impressions", 0)
    s.setdefault("clicks", 0)
    s.setdefault("score", 0.0)

    s["impressions"] += 1
    s["last_update"] = _now_kst_str()
    return state


def record_click(state: Dict[str, Any], style_id: str) -> Dict[str, Any]:
    stats = get_image_stats(state)
    s = stats.setdefault(style_id, {})
    s.setdefault("impressions", 0)
    s.setdefault("clicks", 0)
    s.setdefault("score", 0.0)

    s["clicks"] += 1
    s["last_update"] = _now_kst_str()
    return state


def update_score(state: Dict[str, Any], style_id: str) -> Dict[str, Any]:
    stats = get_image_stats(state)
    s = stats.get(style_id)
    if not s:
        return state

    imp = max(1, int(s.get("impressions", 0)))
    clk = int(s.get("clicks", 0))

    ctr = clk / imp
    s["score"] = round(ctr * 1.5, 4)
    s["last_update"] = _now_kst_str()
    return state
