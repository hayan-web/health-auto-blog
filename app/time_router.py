from __future__ import annotations

from datetime import datetime, timezone, timedelta


KST = timezone(timedelta(hours=9))


def get_kst_hour(now: datetime | None = None) -> int:
    """
    현재 한국 시간(KST) 시(hour) 반환
    """
    if now is None:
        now = datetime.now(tz=timezone.utc)
    return now.astimezone(KST).hour


def topic_by_kst_hour(hour: int) -> str:
    """
    시간대별 고정 주제 분기
    """
    # 🇰🇷 10:00
    if 9 <= hour < 12:
        return "health"

    # 🇰🇷 14:00
    if 13 <= hour < 17:
        return "life"

    # 🇰🇷 19:00
    if 18 <= hour < 22:
        return "trend"

    # 그 외 시간 (수동 실행/예외)
    return "health"
