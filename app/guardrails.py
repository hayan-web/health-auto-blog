# app/guardrails.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Tuple


def _today_ymd() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")


def _month_ym() -> str:
    return datetime.utcnow().strftime("%Y-%m")


@dataclass
class GuardConfig:
    max_posts_per_day: int = 3
    # 비용은 "정확 계산" 대신 상한만 두는 방식(추정/기록은 옵션)
    max_usd_per_month: float = 30.0


def load_counters(state: Dict[str, Any]) -> Dict[str, Any]:
    return state.setdefault("counters", {})


def check_limits_or_raise(state: Dict[str, Any], cfg: GuardConfig) -> None:
    counters = load_counters(state)

    day = _today_ymd()
    month = _month_ym()

    day_count = int((counters.get("posts_per_day") or {}).get(day, 0))
    month_cost = float((counters.get("usd_per_month") or {}).get(month, 0.0))

    if day_count >= cfg.max_posts_per_day:
        raise RuntimeError(f"일일 발행 제한 초과: {day_count}/{cfg.max_posts_per_day}")

    if month_cost >= cfg.max_usd_per_month:
        raise RuntimeError(f"월 비용 제한 초과(추정/기록): ${month_cost:.2f}/${cfg.max_usd_per_month:.2f}")


def increment_post_count(state: Dict[str, Any]) -> None:
    counters = load_counters(state)
    day = _today_ymd()

    posts_per_day = counters.setdefault("posts_per_day", {})
    posts_per_day[day] = int(posts_per_day.get(day, 0)) + 1


def add_month_cost(state: Dict[str, Any], usd: float) -> None:
    """
    선택: OpenAI 응답 token 기반 비용 계산이 준비되면 여기로 더하세요.
    지금은 '대략치'를 넣거나 0을 넣어도 됩니다.
    """
    if usd <= 0:
        return
    counters = load_counters(state)
    month = _month_ym()
    usd_per_month = counters.setdefault("usd_per_month", {})
    usd_per_month[month] = float(usd_per_month.get(month, 0.0)) + float(usd)
