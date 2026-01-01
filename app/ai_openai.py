# app/ai_openai.py
from __future__ import annotations
import json
from typing import Any, Dict

from openai import OpenAI


def make_openai_client(api_key: str) -> OpenAI:
    return OpenAI(api_key=api_key)


SYSTEM_PROMPT = """
너는 '블로그 글 작성자'가 아니다.
너의 역할은 '정보형 블로그 콘텐츠를 구성하는 데이터 생성기'다.

❗ 절대 줄글을 쓰지 마라.
❗ 감정 표현, 인사말, 서론 멘트 금지.
❗ 반드시 JSON만 출력한다.

출력 형식은 아래 스키마를 100% 따른다.

{
  "title": string,
  "img_prompt": string,
  "summary_bullets": string[],
  "sections": [
    {
      "title": string,
      "body": string,
      "bullets": string[]
    }
  ],
  "warning_bullets": string[],
  "checklist_bullets": string[],
  "outro": string
}
"""

USER_PROMPT_TEMPLATE = """
주제 키워드: "{keyword}"

요구사항:
- title: 검색 최적화된 자연스러운 제목
- img_prompt: 단일 장면, 콜라주 없음, 텍스트 없음
- summary_bullets: 3~5개
- sections: 5~7개 (각각 title/body/bullets 2~4개)
- warning_bullets: 2~4개
- checklist_bullets: 3~5개
- outro: 2~3문장 요약 정리
"""


def generate_blog_post(
    client: OpenAI,
    model: str,
    keyword: str,
) -> Dict[str, Any]:
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_PROMPT_TEMPLATE.format(keyword=keyword)},
        ],
        temperature=0.6,
        response_format={"type": "json_object"},
    )

    content = resp.choices[0].message.content
    data = json.loads(content)

    # =========================
    # 최소 검증 (안전망)
    # =========================
    required = [
        "title",
        "img_prompt",
        "summary_bullets",
        "sections",
        "warning_bullets",
        "checklist_bullets",
        "outro",
    ]
    for k in required:
        if k not in data:
            raise ValueError(f"OpenAI JSON 누락 필드: {k}")

    if not isinstance(data.get("sections"), list) or len(data["sections"]) < 3:
        raise ValueError("sections 수 부족")

    return data


def generate_thumbnail_title(
    client: OpenAI,
    model: str,
    title: str,
) -> str:
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": "너는 썸네일용 초단문 제목 생성기다. 10자 이내로 핵심만 남겨라.",
            },
            {"role": "user", "content": title},
        ],
        temperature=0.4,
    )

    return resp.choices[0].message.content.strip()
