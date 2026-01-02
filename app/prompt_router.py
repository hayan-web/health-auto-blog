# app/prompt_router.py
from __future__ import annotations

from typing import Dict, Any


AGE_TITLE_RULE = """
[제목 규칙]
- 제목에 연령대/나이 표현(예: 30대, 40대, 30~40대, 50대, 2030, 3040 등)을 넣지 마세요.
- 숫자 범위(예: 20~30)나 'N대' 표기는 금지합니다.
"""



def guess_topic_from_keyword(keyword: str) -> str:
    k = (keyword or "").lower()

    # health
    health_words = ["건강", "혈압", "고지혈", "중년", "갱년기", "식단", "운동", "수면", "스트레스", "관절", "다이어트", "영양"]
    if any(w in k for w in health_words):
        return "health"

    # it
    it_words = ["아이폰", "안드로이드", "윈도우", "맥", "pc", "노트북", "스마트폰", "와이파이", "유튜브", "앱", "설정", "오류", "로그인", "보안"]
    if any(w in k for w in it_words):
        return "it"

    return "life"


def build_system_prompt(topic: str) -> str:
    """
    generate_blog_post()에서 system prompt로 주입하기 좋게 구성.
    (기존 틀 깨지지 않게: '출력 JSON 스키마 유지'를 강하게 요구)
    """
    topic = (topic or "life").strip().lower()

    base = """
당신은 블로그 글 생성기입니다.
반드시 아래 JSON 스키마로만 출력하세요(추가 텍스트 금지).

{
  "title": "...",
  "img_prompt": "...",
  "summary_bullets": ["...","...","..."],
  "sections": [
    {"h2": "...", "body": "...", "bullets": ["...","...","..."]},
    ...
  ],
  "warning_bullets": ["...","..."],
  "checklist_bullets": ["...","..."],
  "outro": "..."
}

규칙:
- sections는 5개 권장(최소 4개).
- 각 section.body는 140자 이상.
- img_prompt는 single scene, no collage, no text, square 1:1 힌트 포함.
- 과장/허위 금지. 검증 불가 정보는 '일반적으로 알려진 범위'에서만.
""".strip()

    if topic == "health":
        addon = """
추가 규칙(건강):
- 의학적 단정 금지(진단/치료 대신 생활 가이드 중심).
- 위험 신호/주의사항을 warning_bullets에 넣기.
- 전문용어는 쉬운 설명을 곁들이기.
""".strip()
    elif topic == "it":
        addon = """
추가 규칙(IT):
- 단계별 해결/설정 가이드 중심.
- 체크리스트/주의사항을 명확히.
- OS/앱 버전 차이 가능성을 한 줄 언급.
""".strip()
    else:
        addon = """
추가 규칙(생활):
- 바로 실천 가능한 팁 중심.
- 사례/상황 예시 2개 이상 포함.
""".strip()

    return base + "\n\n" + addon


def build_user_prompt(topic: str, keyword: str) -> str:
    """
    user prompt: 키워드만 주고, 톤/구조는 system에서 강제.
    """
    topic = (topic or "life").strip().lower()
    kw = keyword.strip()

    if topic == "health":
        return f"키워드: {kw}\n대상: 30~50대 일반인\n목표: 실천 가능한 건강 습관 가이드"
    if topic == "it":
        return f"키워드: {kw}\n대상: 30~50대\n목표: 오류 해결/설정 튜토리얼"
    return f"키워드: {kw}\n대상: 30~50대\n목표: 생활 문제 해결/꿀팁 가이드"
