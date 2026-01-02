from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Dict, Any


KST = timezone(timedelta(hours=9))


@dataclass
class GuardConfig:
    max_posts_per_day: int = 3
    max_usd_per_month: float = 30.0


def _kst_today_key(now: datetime | None = None) -> str:
    if now is None:
        now = datetime.now(tz=timezone.utc)
    return now.astimezone(KST).strftime("%Y-%m-%d")


def _kst_month_key(now: datetime | None = None) -> str:
    if now is None:
        now = datetime.now(tz=timezone.utc)
    return now.astimezone(KST).strftime("%Y-%m")


def check_limits_or_raise(state: Dict[str, Any], cfg: GuardConfig) -> None:
    """
    초과 시 RuntimeError 발생 → main.py에서 잡아서 이번 회차만 스킵
    """
    limits = state.setdefault("limits", {})
    today_key = _kst_today_key()
    month_key = _kst_month_key()

    today_posts = limits.get("posts_by_day", {}).get(today_key, 0)
    month_usd = limits.get("usd_by_month", {}).get(month_key, 0.0)

    if today_posts >= cfg.max_posts_per_day:
        raise RuntimeError(f"일일 발행 제한 초과: {today_posts}/{cfg.max_posts_per_day}")

    if month_usd >= cfg.max_usd_per_month:
        raise RuntimeError(f"월간 비용 제한 초과: ${month_usd:.2f}/${cfg.max_usd_per_month:.2f}")


def increment_post_count(
    state: Dict[str, Any],
    *,
    estimated_usd: float = 0.0,
) -> Dict[str, Any]:
    """
    발행 성공 후 호출
    - 일일 발행 수 +1
    - 월간 비용 누적
    """
    limits = state.setdefault("limits", {})
    by_day = limits.setdefault("posts_by_day", {})
    by_month = limits.setdefault("usd_by_month", {})

    today_key = _kst_today_key()
    month_key = _kst_month_key()

    by_day[today_key] = int(by_day.get(today_key, 0)) + 1
    by_month[month_key] = float(by_month.get(month_key, 0.0)) + float(estimated_usd)

    return state
