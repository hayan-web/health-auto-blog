from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Dict, Any


KST = timezone(timedelta(hours=9))


def _now_kst() -> datetime:
    return datetime.now(tz=timezone.utc).astimezone(KST)


def _date_str(days: int = 0) -> str:
    return (_now_kst() + timedelta(days=days)).strftime("%Y-%m-%d")


def is_blacklisted(state: Dict[str, Any], keyword: str) -> bool:
    """
    현재 시점 기준 블랙리스트 여부
    """
    bl = state.get("blacklist", {})
    until = bl.get(keyword)
    if not until:
        return False

    today = _date_str(0)
    return today <= until


def add_blacklist(
    state: Dict[str, Any],
    keyword: str,
    *,
    days: int = 3,
    reason: str = "",
) -> Dict[str, Any]:
    """
    키워드를 일정 기간 블랙리스트에 추가
    """
    bl = state.setdefault("blacklist", {})
    bl[keyword] = _date_str(days)

    # 사유 로그(선택)
    logs = state.setdefault("blacklist_log", [])
    logs.append(
        {
            "keyword": keyword,
            "until": bl[keyword],
            "reason": reason,
            "at": _now_kst().isoformat(timespec="seconds"),
        }
    )
    return state
