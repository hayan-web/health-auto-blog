# app/quality.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple


@dataclass
class QualityResult:
    ok: bool
    score: int
    reasons: List[str]


def _len_safe(x: Any) -> int:
    try:
        return len(x)
    except Exception:
        return 0


def _is_str_list(x: Any) -> bool:
    return isinstance(x, list) and all(isinstance(i, str) for i in x)


def evaluate_post_quality(post: Dict[str, Any]) -> QualityResult:
    """
    OpenAI가 만든 post(JSON)가 '발행해도 되는 수준인지' 점수화해서 판단합니다.
    - 이미지 생성/업로드 전에 FAIL 시 재생성 → 비용 절약
    """

    score = 100
    reasons: List[str] = []

    title = (post.get("title") or "").strip()
    img_prompt = (post.get("img_prompt") or "").strip()
    summary = post.get("summary_bullets")
    sections = post.get("sections")
    warning = post.get("warning_bullets")
    checklist = post.get("checklist_bullets")
    outro = (post.get("outro") or "").strip()

    # =========================
    # 필수 필드 체크
    # =========================
    required_keys = [
        "title",
        "img_prompt",
        "summary_bullets",
        "sections",
        "warning_bullets",
        "checklist_bullets",
        "outro",
    ]
    for k in required_keys:
        if k not in post:
            score -= 25
            reasons.append(f"필수 필드 누락: {k}")

    # =========================
    # 제목 품질
    # =========================
    if not title:
        score -= 40
        reasons.append("제목이 비어있음")
    else:
        if len(title) < 10:
            score -= 15
            reasons.append("제목이 너무 짧음(10자 미만)")
        if len(title) > 60:
            score -= 10
            reasons.append("제목이 너무 김(60자 초과)")
        if title.count("!") >= 2:
            score -= 8
            reasons.append("제목에 느낌표 과다")
        if any(bad in title.lower() for bad in ["chatgpt", "ai로", "인공지능"]):
            score -= 12
            reasons.append("제목에 AI/ChatGPT 흔적")

    # =========================
    # 이미지 프롬프트
    # =========================
    if not img_prompt:
        score -= 15
        reasons.append("img_prompt가 비어있음")
    else:
        lower = img_prompt.lower()
        # 콜라주/텍스트가 들어갈 가능성 패널티
        if any(w in lower for w in ["collage", "text overlay", "poster", "typography"]):
            score -= 8
            reasons.append("img_prompt에 콜라주/텍스트 유발 단어 포함 가능")
        # 1:1 유도 추천(없어도 치명적이진 않음)
        if "square" not in lower and "1:1" not in lower:
            score -= 3
            reasons.append("img_prompt에 1:1(square) 힌트가 약함")

    # =========================
    # 요약/경고/체크리스트 구조
    # =========================
    if not _is_str_list(summary) or _len_safe(summary) < 3:
        score -= 12
        reasons.append("summary_bullets가 3개 미만이거나 형식이 아님(list[str])")

    if not _is_str_list(warning) or _len_safe(warning) < 2:
        score -= 10
        reasons.append("warning_bullets가 2개 미만이거나 형식이 아님(list[str])")

    if not _is_str_list(checklist) or _len_safe(checklist) < 3:
        score -= 10
        reasons.append("checklist_bullets가 3개 미만이거나 형식이 아님(list[str])")

    # =========================
    # 섹션 품질(핵심)
    # =========================
    if not isinstance(sections, list) or len(sections) < 4:
        score -= 25
        reasons.append("sections가 4개 미만이거나 형식이 아님(list)")
        sections = []
    else:
        if len(sections) > 9:
            score -= 5
            reasons.append("sections가 과도하게 많음(9개 초과)")

        # 각 섹션 검증
        bad_sections = 0
        for idx, s in enumerate(sections):
            if not isinstance(s, dict):
                bad_sections += 1
                continue

            st = (s.get("title") or "").strip()
            sb = (s.get("body") or "").strip()
            bl = s.get("bullets")

            if not st or len(st) < 4:
                score -= 3
                reasons.append(f"섹션{idx+1}: title이 부실함")
                bad_sections += 1

            if len(sb) < 140:
                score -= 4
                reasons.append(f"섹션{idx+1}: body가 너무 짧음(140자 미만)")
                bad_sections += 1

            if not _is_str_list(bl) or _len_safe(bl) < 2:
                score -= 3
                reasons.append(f"섹션{idx+1}: bullets가 2개 미만 또는 형식 오류")
                bad_sections += 1

        if bad_sections >= 3:
            score -= 12
            reasons.append("부실 섹션이 너무 많음(3개 이상)")

    # =========================
    # 마무리 문단
    # =========================
    if len(outro) < 40:
        score -= 8
        reasons.append("outro가 너무 짧음(40자 미만)")

    # =========================
    # 최종 판정
    # =========================
    # 실전 운영 기준: 75점 미만이면 재생성
    ok = score >= 75

    return QualityResult(ok=ok, score=score, reasons=reasons)
