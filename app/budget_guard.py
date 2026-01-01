# app/budget_guard.py
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Tuple


def _today_kst() -> str:
    # runner는 UTC일 수도 있으니, KST 정확히 하려면 Settings에 timezone 넣는 게 베스트
    # 여기서는 단순히 날짜만 사용(UTC여도 "오늘 제한" 정도만)
    return datetime.utcnow().strftime("%Y-%m-%d")


def _month_utc() -> str:
    return datetime.utcnow().strftime("%Y-%m")


def assert_can_run(state: Dict[str, Any], daily_limit: int = 3) -> None:
    """
    - 기존 state 구조를 건드리지 않고 state['budget']만 사용
    - daily_limit만 기본 적용 (비용은 추정치로만 누적)
    """
    budget = state.get("budget") or {}
    today = _today_kst()

    if budget.get("date") != today:
        budget = {"date": today, "posts_today": 0, "month": _month_utc(), "estimated_cost_usd": 0.0}

    posts_today = int(budget.get("posts_today") or 0)
    if posts_today >= int(daily_limit):
        raise RuntimeError(f"오늘 발행 제한 초과: posts_today={posts_today}, limit={daily_limit}")

    state["budget"] = budget


def mark_post_published(state: Dict[str, Any], est_cost_usd: float = 0.0) -> Dict[str, Any]:
    budget = state.get("budget") or {}
    today = _today_kst()

    if budget.get("date") != today:
        budget = {"date": today, "posts_today": 0, "month": _month_utc(), "estimated_cost_usd": 0.0}

    budget["posts_today"] = int(budget.get("posts_today") or 0) + 1
    budget["estimated_cost_usd"] = float(budget.get("estimated_cost_usd") or 0.0) + float(est_cost_usd or 0.0)

    state["budget"] = budget
    return state
