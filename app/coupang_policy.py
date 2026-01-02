# app/coupang_policy.py
from __future__ import annotations

import re
import time
from typing import Any, Dict, Tuple


def _kst_ymd() -> str:
    # KST = UTC+9
    t = int(time.time()) + 9 * 3600
    return time.strftime("%Y-%m-%d", time.gmtime(t))


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def _contains_any(text: str, words) -> bool:
    t = _norm(text)
    return any(w.lower() in t for w in words)


def _get_daily_bucket(state: Dict[str, Any]) -> Dict[str, Any]:
    bucket = state.get("coupang_daily")
    if not isinstance(bucket, dict):
        bucket = {"date": _kst_ymd(), "count": 0}
        state["coupang_daily"] = bucket
        return bucket

    today = _kst_ymd()
    if bucket.get("date") != today:
        bucket["date"] = today
        bucket["count"] = 0
    if "count" not in bucket or not isinstance(bucket["count"], int):
        bucket["count"] = 0
    return bucket


def increment_coupang_count(state: Dict[str, Any]) -> Dict[str, Any]:
    bucket = _get_daily_bucket(state)
    bucket["count"] = int(bucket.get("count", 0)) + 1
    return state


def should_inject_coupang(
    state: Dict[str, Any],
    *,
    topic: str,
    keyword: str,
    post: Dict[str, Any] | None = None,
    max_per_day: int = 1,
) -> Tuple[bool, str]:
    """
    쿠팡을 '어떤 글에 넣을지' 정책 결정.
    - topic: "health" | "life" | "it" | 기타
    - max_per_day: 하루 쿠팡 삽입 최대 횟수(추천: 1)
    반환: (허용여부, 사유)
    """

    topic = (topic or "general").strip().lower()
    keyword_n = _norm(keyword)

    # 0) 하루 횟수 제한
    bucket = _get_daily_bucket(state)
    if int(bucket.get("count", 0)) >= int(max_per_day):
        return False, f"daily_limit_reached({bucket.get('count')}/{max_per_day})"

    # 1) 본문이 '순수 지식/의학 설명' 성격이면 쿠팡 넣지 않기 (에드센스 전용으로 둠)
    # - 건강(health)에서는 특히 중요 (품질/리스크)
    # - post 내용이 있으면 함께 판단
    title = _norm((post or {}).get("title", ""))
    joined = " ".join(
        [
            keyword_n,
            title,
            _norm((post or {}).get("intro", "")),
            _norm((post or {}).get("outro", "")),
        ]
    )

    # 건강 정보 글에서 쿠팡 삽입하면 어색/품질하락/규정 리스크가 큰 키워드 패턴
    HEALTH_INFO_ONLY_HINTS = [
        "원인", "증상", "치료", "진단", "부작용", "검사", "약", "처방", "병원",
        "의학", "수치", "질환", "질병", "암", "당뇨", "고혈압", "고지혈증",
        "간수치", "콜레스테롤", "염증", "통증 원인", "수술", "감염"
    ]
    if topic == "health" and _contains_any(joined, HEALTH_INFO_ONLY_HINTS):
        # 단, '기기/도구' 선택 가이드 성격이면 예외 허용
        # 아래에서 다시 판정
        pass

    # 2) 쿠팡에 잘 붙는 “가이드/선택/도구형” 힌트
    GUIDE_HINTS = [
        "추천", "비교", "고르는", "선택", "기준", "체크리스트", "구매", "필수", "좋은", "인기"
    ]

    # 3) 주제별 허용 규칙
    # - life: 생활용품/루틴/정리/가전/주방/욕실 등은 쿠팡 궁합 좋음
    # - it: 주변기기/설정 해결 + 관련 제품(라우터, 케이블, 거치대 등) 궁합 좋음
    # - health: “도구/측정/생활보조” 글일 때만(혈압계/체중계/수면도구/스트레칭도구 등)
    HEALTH_TOOL_HINTS = [
        "혈압계", "혈당계", "체중계", "체지방", "측정", "마사지", "온열", "찜질",
        "무릎", "손목", "보호대", "밴드", "폼롤러", "스트레칭", "요가", "매트",
        "수면", "베개", "안대", "귀마개", "가습", "공기청정", "비타민", "오메가", "유산균"
    ]
    LIFE_GOOD_HINTS = [
        "정리", "청소", "수납", "세탁", "주방", "욕실", "살림", "생활", "가전",
        "선물", "생필품", "냄새", "곰팡이", "습기", "보온", "난방", "여행", "캠핑"
    ]
    IT_GOOD_HINTS = [
        "공유기", "와이파이", "충전기", "케이블", "허브", "거치대", "키보드", "마우스",
        "모니터", "노트북", "보안", "백업", "ssd", "hdd", "웹캠", "마이크"
    ]

    # topic이 명확하지 않으면 보수적으로: life/it 힌트가 있을 때만
    if topic not in ("health", "life", "it"):
        if _contains_any(keyword_n, LIFE_GOOD_HINTS + IT_GOOD_HINTS) or _contains_any(joined, GUIDE_HINTS):
            return True, "general_allowed_by_hints"
        return False, "general_disallowed"

    # life는 비교적 넓게 허용하되, 너무 정보성(뜻/정의)만이면 제외
    if topic == "life":
        if _contains_any(keyword_n, ["뜻", "의미", "정의", "유래"]) and not _contains_any(joined, GUIDE_HINTS):
            return False, "life_info_only"
        return True, "life_allowed"

    # it는 기기/주변기기/구매/가이드 힌트가 있을 때만 허용 (정보성 오류 해결 글은 과도 삽입 금지)
    if topic == "it":
        if _contains_any(joined, IT_GOOD_HINTS) or _contains_any(joined, GUIDE_HINTS):
            return True, "it_allowed"
        return False, "it_disallowed"

    # health는 “도구형/측정형/보조아이템” + 가이드성일 때만
    if topic == "health":
        toolish = _contains_any(joined, HEALTH_TOOL_HINTS)
        guideish = _contains_any(joined, GUIDE_HINTS)

        # 건강 정보-only 힌트가 강하고 도구 힌트가 없으면 금지
        info_only = _contains_any(joined, HEALTH_INFO_ONLY_HINTS)
        if info_only and not toolish:
            return False, "health_info_only"

        if toolish or guideish:
            return True, "health_tool_or_guide_allowed"
        return False, "health_disallowed"

    return False, "fallback_disallowed"
