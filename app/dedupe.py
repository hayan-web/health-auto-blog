import re
import hashlib
from typing import Any, Dict, List, Tuple


def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^0-9a-z가-힣 ]+", "", s)
    return s.strip()


def _title_fingerprint(title: str) -> str:
    """
    제목을 정규화한 뒤 해시로 저장(중복 비교용)
    """
    n = _norm(title)
    return hashlib.sha1(n.encode("utf-8")).hexdigest()


def is_duplicate_title(title: str, history: List[Dict[str, Any]], window: int = 50) -> bool:
    """
    최근 window개 히스토리 안에서 제목 중복 검사
    """
    fp = _title_fingerprint(title)
    recent = history[-window:] if len(history) > window else history
    for h in recent:
        if h.get("title_fp") == fp:
            return True
    return False


def pick_retry_reason(title: str, history: List[Dict[str, Any]]) -> Tuple[bool, str]:
    """
    중복이면 (True, 사유), 아니면 (False, "")
    """
    if is_duplicate_title(title, history):
        return True, "최근 제목과 중복"
    return False, ""
