# app/prompt_router.py
from __future__ import annotations

from typing import Tuple


HEALTH_KEYS = ("건강", "혈압", "고지혈", "당뇨", "갱년기", "관절", "통증", "수면", "식단", "운동", "스트레스")
IT_KEYS = ("스마트폰", "폰", "PC", "윈도우", "아이폰", "안드로이드", "앱", "로그인", "계정", "보안", "설정", "오류", "인터넷")
LIFE_KEYS = ("생활", "청소", "세탁", "정리", "주방", "욕실", "이사", "요리", "가전", "살림", "다이소")


def route_topic(keyword: str) -> str:
    k = (keyword or "").strip()
    if any(x in k for x in IT_KEYS):
        return "it"
    if any(x in k for x in LIFE_KEYS):
        return "life"
    if any(x in k for x in HEALTH_KEYS):
        return "health"
    # 기본은 health(현재 레포가 health-auto-blog이기도 해서)
    return "health"


def build_extra_prompt(category: str) -> str:
    """
    generate_blog_post에 추가 지시문으로 넣을 수 있는 문장(짧고 강하게)
    """
    if category == "it":
        return (
            "대상: 30~50대. 문제-원인-해결 순서로, 단계별 체크리스트/주의사항 포함. "
            "앱/설정 경로는 구체적으로. 과장 금지."
        )
    if category == "life":
        return (
            "대상: 30~50대. 생활 팁을 '바로 해볼 수 있는 단계'로 쪼개고, "
            "실수하기 쉬운 포인트/대안/비용 팁을 포함. 과장 금지."
        )
    # health
    return (
        "대상: 40~60대. 증상/원인/관리법/주의 신호를 구분해 설명. "
        "의학적 진단 단정 금지, 병원 가야 하는 레드플래그 포함."
    )


def get_generation_context(keyword: str) -> Tuple[str, str]:
    cat = route_topic(keyword)
    extra = build_extra_prompt(cat)
    return cat, extra
