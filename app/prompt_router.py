# app/prompt_router.py
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def _read_prompt_file(filename: str) -> Optional[str]:
    """
    app/prompts/*.txt 를 우선 로딩합니다.
    없으면 None 반환(코드 기본 프롬프트 fallback).
    """
    base = Path(__file__).resolve().parent / "prompts"
    p = base / filename
    if p.exists():
        try:
            return p.read_text(encoding="utf-8").strip()
        except Exception:
            return None
    return None


# 기본 fallback (파일 없을 때만 사용)
_DEFAULT_HEALTH = """당신은 건강 정보 블로그 작성자입니다.
- 과장/낚시 금지, 의학적 단정 금지(개인차/전문의 상담 고지)
- 근거 중심, 안전/주의사항 포함
"""

_DEFAULT_SHOPPING = """당신은 생활/쇼핑(쿠팡) 리뷰 블로그 작성자입니다.
- 과장/낚시 금지, 체험 기반 톤(과도한 홍보/허위 금지)
- 구매 체크포인트/주의사항/실사용 팁 중심
"""

_DEFAULT_ISSUE = """당신은 트렌드/이슈 블로그 작성자입니다.
- 사실 기반, 확인되지 않은 내용은 추정/가능성으로 표현
- 요약→배경→핵심 쟁점→영향→체크포인트 순서
"""


def build_system_prompt(topic: str) -> str:
    topic = (topic or "").lower().strip()

    if topic == "health":
        base = _read_prompt_file("health.txt") or _DEFAULT_HEALTH
    elif topic in ("life", "shopping"):
        base = _read_prompt_file("shopping.txt") or _DEFAULT_SHOPPING
    else:
        base = _read_prompt_file("issue.txt") or _DEFAULT_ISSUE

    # ✅ “코드/HTML/버튼 문법이 본문에 튀는 문제”를 여기서 강제로 차단
    guard = """
[절대 규칙]
- 본문에 HTML 태그(<a>, <div> 등), 코드, 마크다운 코드블록(```), 워드프레스 블록 주석(<!-- wp:... -->)을 절대 넣지 마세요.
- 링크 URL도 본문에 직접 노출하지 마세요. (링크는 발행 파이프라인이 별도로 삽입합니다)
- "명령어/프롬프트/지침" 같은 텍스트가 본문에 그대로 보이면 안 됩니다.
- 출력은 JSON만. 추가 설명/머리말/꼬리말 금지.
""".strip()

    # ✅ 모델 출력 포맷을 고정(formatter가 안정적으로 구조화 가능)
    schema = """
[출력 JSON 스키마]
{
  "title": "제목(한 줄)",
  "summary_bullets": ["요약1","요약2","요약3","요약4"],
  "sections": [
    {"h2":"소제목1","paras":["문단1","문단2"],"bullets":["불릿1","불릿2"]},
    {"h2":"소제목2","paras":["문단1","문단2"],"bullets":["불릿1","불릿2"]},
    {"h2":"소제목3","paras":["문단1","문단2"],"bullets":["불릿1","불릿2"]}
  ],
  "warning_bullets": ["주의1","주의2"],
  "checklist_bullets": ["체크1","체크2","체크3"],
  "outro": "마무리 문단",
  "img_prompt": "이미지 생성용 영어 프롬프트(짧게)",
  "highlight_terms": ["강조단어1","강조단어2","강조단어3"]
}
""".strip()

    return f"{base}\n\n{guard}\n\n{schema}".strip()


def build_user_prompt(topic: str, keyword: str, extra_context: str = "") -> str:
    topic = (topic or "").lower().strip()
    keyword = (keyword or "").strip()

    # 제목 규칙(연령/숫자 금지 등)도 다시 한 번 유저프롬프트에서 못 박기
    title_rules = """
[제목 규칙]
- 연령대/숫자(예: 30~50대, 20대, 3040, 10가지 등) 언급 금지
- 15~32자 내외
- 과장/낚시 금지, 현실적인 톤
""".strip()

    # 섹션 스타일: 관리 잘하는 블로그처럼 보이게(소제목은 명확, 내용은 단락 정리)
    structure_rules = """
[구성 규칙]
- 소제목(h2) 3개는 서로 다른 관점/단어로 만들기(반복 금지)
- 각 소제목마다: 짧은 설명 문단 1~2개 + 불릿 2~4개
- 요약은 4개 불릿(실행/주의/핵심/체크포인트 균형)
- highlight_terms는 본문에서 자주 등장할 “짧은 핵심 단어” 3~6개(2~6글자 위주)
""".strip()

    ctx = ""
    if extra_context:
        ctx = f"\n\n[참고 컨텍스트(사실 기반)]\n{extra_context}\n"

    return f"""
[주제] {topic}
[키워드] {keyword}

{title_rules}

{structure_rules}
{ctx}
""".strip()
