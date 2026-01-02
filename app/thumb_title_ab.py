from __future__ import annotations

import random
import re
from typing import Dict, Any, Optional, Tuple, List

from openai import OpenAI


# 고정 A/B/C 프리셋 (필요하면 2개만 쓰셔도 됩니다)
VARIANTS = [
    "A_short_benefit",   # 짧게 + 혜택/핵심
    "B_numbered",        # 숫자형(3~7 등)
    "C_problem_solution" # 문제→해결 느낌
]


def _clean_line(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"[\r\n]+", " ", s).strip()
    s = re.sub(r"[\"\'\(\)\[\]\{\}]+", "", s).strip()
    # 너무 길면 컷
    if len(s) > 18:
        s = s[:18].strip()
    return s


def _pick_variant(
    state: Dict[str, Any],
    *,
    topic: Optional[str] = None,
    explore_rate: float = 0.12,
) -> str:
    """
    9번: 썸네일 타이틀 variant A/B 선택
    - 초반엔 탐색을 위해 랜덤 비율 유지
    - 학습 점수는 state.thumb_title_stats / state.topic_thumb_title_stats를 참고 (있으면)
    """
    if random.random() < max(0.0, min(1.0, explore_rate)):
        return random.choice(VARIANTS)

    # 점수 기반 선택 (없으면 기본 0.3)
    g = state.get("thumb_title_stats", {}) or {}
    tt = state.get("topic_thumb_title_stats", {}) or {}
    t = (tt.get(topic or "unknown", {}) if topic else {}) or {}

    weights: List[float] = []
    for v in VARIANTS:
        g_score = float((g.get(v, {}) or {}).get("score", 0.3))
        t_score = float((t.get(v, {}) or {}).get("score", 0.3))
        # topic 데이터가 없을 때 global에 더 의존
        blended = 0.55 * g_score + 0.45 * t_score
        weights.append(max(0.10, blended))

    return random.choices(VARIANTS, weights=weights, k=1)[0]


def _prompt_for_variant(variant_id: str, title: str, keyword: str, topic: str) -> str:
    title = (title or "").strip()
    keyword = (keyword or "").strip()
    topic = (topic or "").strip() or "general"

    if variant_id == "A_short_benefit":
        return f"""
다음 글의 썸네일 문구를 만드세요.
- 2~7단어
- 혜택/핵심이 바로 보이게
- 특수문자/괄호/따옴표 금지
- 문구 한 줄만 출력

주제:{topic}
키워드:{keyword}
제목:{title}
""".strip()

    if variant_id == "B_numbered":
        return f"""
다음 글의 썸네일 문구를 만드세요.
- 숫자(3~7 중 하나) 포함
- 2~8단어
- 특수문자/괄호/따옴표 금지
- 문구 한 줄만 출력

주제:{topic}
키워드:{keyword}
제목:{title}
""".strip()

    # C_problem_solution
    return f"""
다음 글의 썸네일 문구를 만드세요.
- 문제→해결 느낌(예: 'OO 해결', 'OO 줄이는 법')
- 2~8단어
- 특수문자/괄호/따옴표 금지
- 문구 한 줄만 출력

주제:{topic}
키워드:{keyword}
제목:{title}
""".strip()


def generate_thumbnail_title_ab(
    client: OpenAI,
    model: str,
    *,
    title: str,
    keyword: str,
    topic: str,
    state: Dict[str, Any],
) -> Tuple[str, str]:
    """
    반환: (thumb_title, variant_id)
    """
    variant_id = _pick_variant(state, topic=topic)

    prompt = _prompt_for_variant(variant_id, title=title, keyword=keyword, topic=topic)

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "Output ONE short line in Korean. No extra text."},
            {"role": "user", "content": prompt},
        ],
    )
    out = _clean_line(resp.choices[0].message.content or "")

    # 안전장치: 너무 짧거나 비어있으면 fallback
    if len(out) < 2:
        out = _clean_line(title)[:18] or "핵심 정리"

    return out, variant_id
